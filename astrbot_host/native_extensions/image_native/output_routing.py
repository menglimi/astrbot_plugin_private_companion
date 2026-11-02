"""Native image routing through V2-only API queues and local backend connections."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from dataclasses import replace
from pathlib import Path
from urllib.request import getproxies, proxy_bypass
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .backends import NativeTransportBackend
from .output_configuration import accepted_request_fields
from .owner import checked
from .types import MAX_REFERENCE_IMAGES, BackendResult, NativeImageError, fingerprint

OPENAI_COMPATIBLE_PLATFORMS = frozenset({"openai", "openrouter", "agnes", "sensenova", "doubao"})
OPENAI_IMAGE_SUFFIXES = ("/images/generations", "/images/edits")
MINIMAX_IMAGE_SUFFIX = "/image_generation"

# The dispatch fence is crossed before the POST is attempted, so ``dispatching``
# only proves that a request *may* have left the process.  A 4xx response is a
# definitive refusal: the provider answered and did not execute the generation,
# so the owner may record ``failed`` (retryable) instead of ``unknown``.
#
# What differs between 4xx responses is the *scope* of the refusal:
#   account : 401 credentials / 402 billing.  A retry against another endpoint
#             reuses the same broken credential or unpaid account in the normal
#             single-account deployment and cannot help; it can also trip
#             credential-abuse lockouts.  Stop and let the operator fix it.
#   endpoint: 400 request shape, 403 model/key entitlement, 404/405 path/method,
#             413 payload limit and 422 schema validation.  Each endpoint owns
#             its own ``base_url``/``api_key``/``model``/limits (see
#             ``output_configuration.py``), so the same body may be accepted by
#             another endpoint.  No request executed, so switching cannot
#             double-bill.
#   rate    : 429 admission refusal; one alternative endpoint is tried.
#   unknown : 408/409/5xx/transport.  The provider may already have executed the
#             request; never retried.
#
# A switchable status can still be global when the provider's own detail names a
# content-policy/moderation refusal (the same prompt would be rejected
# everywhere; retrying is policy shopping) or a credential/account condition
# reported with the wrong status code.  ``failure_scope()`` applies those
# overrides from the bounded, redacted error detail.
STOP_FAILURE_STATUSES = frozenset({401, 402})
SWITCH_FAILURE_STATUSES = frozenset({400, 403, 404, 405, 413, 422})
RATE_LIMIT_STATUSES = frozenset({429})
DEFINITIVE_FAILURE_STATUSES = STOP_FAILURE_STATUSES | SWITCH_FAILURE_STATUSES | RATE_LIMIT_STATUSES
RATE_LIMIT_ATTEMPTS = 2

# Scopes of a definitive refusal; carried in every diagnostic so an operator can
# see why a 403 stopped or switched without re-deriving it from the status.
SCOPE_ACCOUNT = "account"
SCOPE_CONTENT = "content"
SCOPE_ENDPOINT = "endpoint"
SCOPE_RATE = "rate"
SCOPE_UNCONFIRMED = "unconfirmed"

# ``failure_scope()`` reads only the bounded, redacted detail produced by
# ``_error_detail``.  Markers are deliberately specific: "the api key has not
# enabled this model" is an endpoint entitlement (switch), while "incorrect API
# key" is a credential failure (stop).  The Chinese markers cover the providers
# this build ships (MiniMax / Bailian / Doubao / ModelScope).
CONTENT_REFUSAL_MARKERS = (
    "content_policy", "content policy", "contentpolicy",
    "moderation", "moderated", "safety system", "safety_violation",
    "safety check", "safety_checker", "blocked by safety",
    "nsfw", "prohibited content", "policy violation",
    "violates our", "violates the", "content filter", "content_filter",
    "内容审核", "内容违规", "违规内容", "敏感内容",
)
CREDENTIAL_REFUSAL_MARKERS = (
    "invalid_api_key", "invalid api key", "incorrect api key",
    "api key not valid", "api key is invalid", "missing api key",
    "no api key", "invalid token", "invalid_token",
    "authentication failed", "authentication_error", "unauthenticated",
    "invalid credentials", "credentials are invalid",
    "鉴权失败", "认证失败", "密钥无效", "无效的api", "无效api", "无效密钥",
)
ACCOUNT_REFUSAL_MARKERS = (
    "insufficient_quota", "insufficient quota", "quota exceeded", "quota_exceeded",
    "billing", "payment required", "payment_required", "credit balance",
    "insufficient balance", "no credit", "account suspended",
    "account is not active", "account deactivated",
    "organization must be verified", "verify your organization",
    "余额不足", "欠费", "账户已被", "账户被停", "未实名", "实名认证",
)

# Rejection kinds carried by ``Rejected``; the router maps them to a terminal
# code without re-deriving anything from strings.
REJECTED_STOP = "stop"
REJECTED_UNAVAILABLE = "unavailable"
REJECTED_ENDPOINT = "endpoint"
REJECTED_RATE = "rate_limited"
REJECTED_REQUEST = "request_invalid"

# Local, pre-execution codes that must abort routing instead of being read as
# "this endpoint is unavailable".  Anything else raised before the fence is
# endpoint-local configuration and may fall through to the next route.
ABORTING_CODES = frozenset({"image_authority_revoked", "image_provider_stopped"})

MAX_ERROR_BODY_BYTES = 4096
MAX_ERROR_DETAIL_CHARS = 160
FAILURE_REASONS = {
    400: "bad_request", 401: "unauthorized", 402: "payment_required", 403: "forbidden",
    404: "endpoint_not_found", 405: "method_not_allowed", 408: "request_timeout",
    409: "conflict", 413: "payload_too_large", 422: "unprocessable", 429: "rate_limited",
}
_CONTROL_CHARS = {code: " " for code in (*range(0, 32), *range(127, 160))}
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/?#\s@]+@")
_RETRY_AFTER = re.compile(r"[0-9]{1,6}")


def _flatten(value: str) -> str:
    """One line of printable text: no newlines, tabs, NULs or control bytes."""
    return " ".join(str(value).translate(_CONTROL_CHARS).split())


def failure_scope(status: int, detail: str) -> str:
    """Return the scope of a definitive refusal from its status and detail.

    ``detail`` is the already bounded and redacted output of ``_error_detail``:
    classification can neither leak a prompt/key nor read past the diagnostic
    budget.  A 4xx is always definitive (the provider answered and did not
    execute); only its scope is being derived here.
    """
    if status in STOP_FAILURE_STATUSES:
        return SCOPE_ACCOUNT
    if status in RATE_LIMIT_STATUSES:
        return SCOPE_RATE
    if status not in SWITCH_FAILURE_STATUSES:
        return SCOPE_UNCONFIRMED
    text = str(detail).lower()
    if any(marker in text for marker in CONTENT_REFUSAL_MARKERS):
        return SCOPE_CONTENT
    if any(marker in text for marker in CREDENTIAL_REFUSAL_MARKERS):
        return SCOPE_ACCOUNT
    if any(marker in text for marker in ACCOUNT_REFUSAL_MARKERS):
        return SCOPE_ACCOUNT
    return SCOPE_ENDPOINT


def _redact(value: str, secrets) -> str:
    """Remove every configured secret, bearer token and URL userinfo."""
    result = value
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4 and secret in result:
            result = result.replace(secret, "***")
    result = _USERINFO.sub(r"\1***@", result)
    return _BEARER.sub("Bearer ***", result)


def _secrets(config, prompt: str = "", negative_prompt: str = ""):
    values = [config.get("api_key")]
    headers = config.get("custom_headers")
    if isinstance(headers, dict):
        values.extend(headers.values())
    values.extend((prompt, negative_prompt))
    return tuple(value for value in values if isinstance(value, str) and len(value) >= 4)


def _error_fields(raw: bytes) -> dict:
    """Whitelist the diagnostic fields of an error body, never the whole body."""
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    source = payload.get("error")
    if not isinstance(source, dict):
        base_resp = payload.get("base_resp")
        source = base_resp if isinstance(base_resp, dict) else payload
    names = {"code": ("code", "status_code"), "type": ("type",),
             "param": ("param",), "message": ("message", "msg", "status_msg", "error")}
    fields = {}
    for name, keys in names.items():
        for key in keys:
            value = source.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                fields[name] = str(value).strip()
                break
    return fields


def _error_detail(raw: bytes, secrets=()) -> str:
    """A single bounded, redacted line derived from at most one error body."""
    fields = _error_fields(raw)
    parts = [f"{name}={_flatten(fields[name])}" for name in ("code", "type", "param", "message")
             if fields.get(name)]
    if not parts:
        parts = [_flatten(raw.decode("utf-8", "replace"))]
    return _redact(" ".join(part for part in parts if part), secrets)[:MAX_ERROR_DETAIL_CHARS]


async def _error_body(response) -> bytes:
    """Read at most ``MAX_ERROR_BODY_BYTES`` of a rejection body."""
    raw = bytearray()
    try:
        async for chunk in response.content.iter_chunked(1024):
            raw.extend(chunk)
            if len(raw) >= MAX_ERROR_BODY_BYTES:
                break
    except Exception:  # a broken body must never replace the status diagnosis
        return bytes(raw[:MAX_ERROR_BODY_BYTES])
    return bytes(raw[:MAX_ERROR_BODY_BYTES])


def _retry_after(response) -> str:
    headers = getattr(response, "headers", None)
    value = headers.get("Retry-After", "") if headers is not None else ""
    value = str(value).strip()
    return value if _RETRY_AFTER.fullmatch(value) else ""


def _diagnostic(config, status: int, reason: str, detail: str, retry_after: str = "",
                scope: str = SCOPE_UNCONFIRMED) -> dict:
    return {"endpoint_id": str(config.get("id", "")), "status": status,
            "reason": reason, "detail": detail, "retry_after": retry_after,
            "scope": scope}


def _backend_config(backend) -> dict:
    config = getattr(backend, "config", None)
    return config if isinstance(config, dict) else {}


class Rejected(NativeImageError):
    """A definitive refusal that the provider did not execute (so nothing billed).

    ``switchable`` says whether another configured endpoint may still accept the
    same request.  ``scope`` records *why*: an account/content refusal is
    global, while an endpoint-scoped refusal is local to this endpoint's key,
    model entitlement, payload limit or request schema.
    """

    def __init__(self, code: str = "backend_rejected", *, endpoint_id: str = "", status: int = 0,
                 reason: str = "", detail: str = "", retry_after: str = "", switchable: bool = True,
                 kind: str = REJECTED_UNAVAILABLE, scope: str = SCOPE_ENDPOINT):
        self.endpoint_id = endpoint_id
        self.status = status
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after
        self.switchable = bool(switchable)
        self.kind = kind
        self.scope = scope
        diagnostic = {"endpoint_id": endpoint_id, "status": status, "reason": reason,
                      "detail": detail, "retry_after": retry_after, "scope": scope}
        super().__init__(code, diagnostics=(diagnostic,), unconfirmed=False)


def operation_url(base: str, operation: str, platform: str) -> str:
    """Return the final request URL without nesting a second operation path.

    An admin may paste a root address, a complete operation URL or a URL that
    carries provider query parameters (for example ``?api-version=``).  Only the
    path is rewritten, so a query is preserved and an address belonging to the
    other operation is replaced instead of appended.
    """
    parsed = urlsplit(base)
    path = parsed.path.rstrip("/")
    if platform == "minimax":
        suffix = MINIMAX_IMAGE_SUFFIX
        known = (MINIMAX_IMAGE_SUFFIX,)
    else:
        suffix = "/images/edits" if operation == "edit" else "/images/generations"
        known = OPENAI_IMAGE_SUFFIXES
    for candidate in known:
        if path.endswith(candidate):
            path = path[:-len(candidate)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path + suffix, parsed.query, ""))


def resolve_platform(config):
    platform = config.get("platform", "auto")
    if platform != "auto":
        return platform
    base = config.get("base_url", "").lower()
    model = config.get("model", "").lower()
    host = urlsplit(base).hostname or ""
    if "minimax" in host or model in {"image-01", "image-01-live"}:
        return "minimax"
    if "openrouter.ai" in host:
        return "openrouter"
    if "agnes-ai.com" in host or model.startswith("agnes-image-"):
        return "agnes"
    if "sensenova.cn" in host or model.startswith("senova-"):
        return "sensenova"
    if "volces.com" in host or "volcengine" in host:
        return "doubao"
    if host == "dashscope.aliyuncs.com" or host.endswith(".dashscope.aliyuncs.com"):
        return "bailian"
    if host == "modelscope.cn" or host.endswith(".modelscope.cn"):
        return "modelscope"
    if host == "generativelanguage.googleapis.com":
        return "gemini"
    return "openai"


class OnlineImageBackend:
    def __init__(self, config, network=None):
        self.config = config
        self.network = network if isinstance(network, dict) else {}

    def _request_options(self, url):
        proxy = str(self.network.get("download_proxy", "") or "").strip()
        if proxy:
            return {"proxy": proxy}
        if self.network.get("use_environment_proxy"):
            host = urlsplit(url).hostname or ""
            if host and not proxy_bypass(host):
                proxies = getproxies()
                proxy = proxies.get(urlsplit(url).scheme) or proxies.get("all") or ""
                if proxy:
                    return {"proxy": proxy}
        return {}

    @property
    def operations(self):
        config = self.config
        base = config.get("base_url", "").rstrip("/")
        parsed = urlsplit(base)
        if (config.get("enabled") is not True or not config.get("model")
                or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment):
            return ()
        platform = resolve_platform(config)
        if platform == "minimax":
            return ("generate",)
        if platform in OPENAI_COMPATIBLE_PLATFORMS:
            return (("generate", "edit") if config.get("max_reference_images", MAX_REFERENCE_IMAGES) > 0
                    else ("generate",))
        return ()

    async def execute(self, invocation, references, before_dispatch):
        config = self.config
        base = config.get("base_url", "").rstrip("/")
        if not base or not config.get("model"):
            raise NativeImageError("native_endpoint_unconfigured", unconfirmed=False)
        platform = resolve_platform(config)
        if invocation.operation == "mask_edit" or (platform == "minimax" and invocation.operation != "generate"):
            raise NativeImageError("unsupported_image_operation", unconfirmed=False)
        if platform != "minimax" and platform not in OPENAI_COMPATIBLE_PLATFORMS:
            raise NativeImageError("unsupported_image_platform", unconfirmed=False)
        url = operation_url(base, invocation.operation, platform)
        size = f"{config['width']}x{config['height']}"
        if platform == "agnes" and config.get("size_label") in {"1K", "2K", "3K", "4K"}:
            size = config["size_label"]
        # The optional request fields follow the model's protocol.  The official
        # GPT Image family accepts no ``response_format`` and only
        # auto/low/medium/high quality; 7.0.7's stored ``standard`` is a legacy
        # default there, while a dall-e endpoint keeps an explicit standard/hd.
        accepted = accepted_request_fields(platform, config.get("model", ""),
                                           quality=config.get("quality", ""),
                                           response_format=config.get("response_format", ""))
        body = {"model": config["model"], "prompt": invocation.prompt, "n": 1, "size": size}
        if accepted["response_format"]:
            body["response_format"] = accepted["response_format"]
        if platform == "minimax":
            divisor = math.gcd(config["width"], config["height"])
            ratio = config.get("ratio") or f"{config['width'] // divisor}:{config['height'] // divisor}"
            body = {"model": config["model"], "prompt": invocation.prompt,
                    "n": 1, "aspect_ratio": ratio,
                    "response_format": "base64"}
        elif accepted["quality"]:
            body["quality"] = accepted["quality"]
        if invocation.operation == "edit":
            max_reference_images = config.get("max_reference_images", MAX_REFERENCE_IMAGES)
            if (type(max_reference_images) is not int or max_reference_images < 1
                    or len(references) > max_reference_images):
                # Per-endpoint configuration, rejected before any I/O: nothing was
                # sent, but another endpoint may accept this many references.
                raise Rejected("backend_request_invalid", endpoint_id=str(config.get("id", "")),
                               reason="reference_limit",
                               detail=f"max={max_reference_images} provided={len(references)}",
                               switchable=True, kind=REJECTED_REQUEST, scope=SCOPE_ENDPOINT)
            form = aiohttp.FormData()
            for key, value in body.items():
                form.add_field(key, str(value))
            field_name = "image" if len(references) == 1 else "image[]"
            for index, reference in enumerate(references, start=1):
                form.add_field(field_name, reference.data, filename=f"reference-{index}.png",
                               content_type="image/png")
            kwargs = {"data":form}
        else:
            kwargs = {"json":body}
        headers = ({"Authorization": "Bearer " + config["api_key"]}
                   if config.get("api_key") else {})
        custom_headers = config.get("custom_headers")
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)
        secrets = _secrets(config, invocation.prompt, invocation.negative_prompt)
        timeout = aiohttp.ClientTimeout(total=config.get("timeout_seconds", 180))
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            await checked(before_dispatch)
            try:
                async with session.post(url, headers=headers, allow_redirects=False, **kwargs) as response:
                    if response.status >= 300:
                        raise await self._failure(response, secrets)
                    raw = await self._read(response)
            except NativeImageError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # The fence is already crossed: the provider may have accepted the
                # request even though no response reached us.  Never retried.
                raise NativeImageError(
                    "backend_result_unconfirmed", unconfirmed=True,
                    diagnostics=(_diagnostic(config, 0, "transport_error", type(exc).__name__),)) from exc
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise NativeImageError("backend_result_unconfirmed") from exc
            entries = payload.get("data", [])
            if isinstance(entries, dict):
                entries = [{"b64_json":item} for item in entries.get("image_base64", [])] or [
                    {"url":item} for item in entries.get("image_urls", [])]
            if not isinstance(entries, list) or not entries:
                raise NativeImageError("backend_result_unconfirmed")
            entry = entries[0]
            if entry.get("b64_json"):
                try:
                    image = base64.b64decode(entry["b64_json"], validate=True)
                except ValueError as exc:
                    raise NativeImageError("backend_result_unconfirmed") from exc
            elif entry.get("url"):
                remote = urlsplit(entry["url"])
                if remote.scheme not in {"http", "https"} or not remote.hostname or remote.username or remote.password:
                    raise NativeImageError("backend_image_url_invalid")
                await checked(getattr(before_dispatch, "read", before_dispatch))
                async with session.get(entry["url"], allow_redirects=False,
                                       **self._request_options(entry["url"])) as response:
                    if response.status != 200:
                        raise NativeImageError("backend_result_unconfirmed")
                    image = await self._read(response)
            else:
                raise NativeImageError("backend_result_unconfirmed")
            return BackendResult((image,), receipt={"backend":"online_api", "endpoint_id":config["id"],
                                                   "model":config["model"], "response_received":True})

    async def _failure(self, response, secrets):
        """Classify one response into a definitive refusal or an unconfirmed one."""
        config = self.config
        status = int(response.status)
        reason = FAILURE_REASONS.get(status, "server_error" if status >= 500 else "unexpected_status")
        detail = _error_detail(await _error_body(response), secrets)
        retry_after = _retry_after(response)
        endpoint_id = str(config.get("id", ""))
        scope = failure_scope(status, detail)
        if scope == SCOPE_RATE:
            return Rejected("backend_rate_limited", endpoint_id=endpoint_id, status=status,
                            reason=reason, detail=detail, retry_after=retry_after,
                            switchable=True, kind=REJECTED_RATE, scope=SCOPE_RATE)
        if scope in {SCOPE_ACCOUNT, SCOPE_CONTENT}:
            # A credential/account/content refusal is global to this deployment
            # even when a provider reported it as 400/403/422; repeating the same
            # body or the same key against every endpoint cannot help.
            return Rejected("native_endpoint_rejected", endpoint_id=endpoint_id, status=status,
                            reason=reason, detail=detail, retry_after=retry_after,
                            switchable=False, kind=REJECTED_STOP, scope=scope)
        if scope == SCOPE_ENDPOINT:
            # Endpoint-scoped refusal: another endpoint owns a different key,
            # model entitlement, payload limit and request schema.  No request
            # executed, so trying the next endpoint cannot double-bill.
            kind = REJECTED_UNAVAILABLE if status in {404, 405} else REJECTED_ENDPOINT
            return Rejected("native_endpoint_rejected", endpoint_id=endpoint_id, status=status,
                            reason=reason, detail=detail, retry_after=retry_after,
                            switchable=True, kind=kind, scope=SCOPE_ENDPOINT)
        # 408/409/5xx and anything unexpected may already have been executed.
        return NativeImageError(
            "backend_result_unconfirmed", unconfirmed=True,
            diagnostics=(_diagnostic(config, status, reason, detail, retry_after),))

    @staticmethod
    async def _read(response):
        raw = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > 32 * 1024 * 1024:
                raise NativeImageError("backend_response_too_large")
        return bytes(raw)


class OutputRoutingBackend:
    def __init__(self, settings, directory):
        self.config = json.loads(json.dumps(settings))
        self.backend = settings["backend"]
        self.directory = Path(directory)
        self.revision = fingerprint(settings)
        self._closed = False

    @property
    def operations(self):
        if self.config.get("enabled") is not True:
            return ()
        available = {operation for operation in ("generate", "edit", "mask_edit")
                     for backend in self._routes(operation)
                     if operation in getattr(backend, "operations", ())}
        return tuple(operation for operation in ("generate", "edit", "mask_edit") if operation in available)

    def _routes(self, operation=None):
        route_operation = "generate" if operation == "generate" else "edit"
        operation_routes = self.config.get("operation_routes")
        if operation is not None and isinstance(operation_routes, dict):
            endpoints = {item.get("id"): item for item in self.config.get("endpoints", [])
                         if isinstance(item, dict) and isinstance(item.get("id"), str)}
            routes = operation_routes.get(route_operation, ())
            if not isinstance(routes, (list, tuple)):
                return
            for route in routes:
                if not isinstance(route, str):
                    continue
                if route.startswith("online:"):
                    endpoint = endpoints.get(route[7:])
                    if endpoint and endpoint.get("enabled"):
                        yield OnlineImageBackend(endpoint, self.config.get("network", {}))
                    continue
                if route in {"comfyui", "sd_webui"}:
                    try:
                        yield NativeTransportBackend(route, self.config.get(route, {}), self.directory / route)
                    except NativeImageError:
                        continue
            return

        selected = self.backend
        if selected in {"auto", "online_api"}:
            for endpoint in self.config.get("endpoints", []):
                if endpoint.get("enabled"):
                    yield OnlineImageBackend(endpoint)
        for name in ("comfyui", "sd_webui"):
            if selected in {"auto", name}:
                try:
                    yield NativeTransportBackend(name, self.config.get(name, {}), self.directory / name)
                except NativeImageError:
                    continue

    async def execute(self, invocation, references, before_dispatch):
        if self._closed or not self.config["enabled"]:
            raise NativeImageError("image_provider_stopped")
        prompt = invocation.envelope.get("image_prompt", {})
        if prompt:
            invocation = replace(invocation, prompt=prompt["positive"], negative_prompt=prompt["negative"])
        routed = rejected = request_invalid = rate_limited = False
        diagnostics: list[dict] = []
        rate_budget = RATE_LIMIT_ATTEMPTS
        for backend in self._routes(invocation.operation):
            routed = True
            if invocation.operation not in backend.operations:
                # A route that cannot serve this operation is a configuration gap
                # for this endpoint only; it must not be reported as "no route".
                continue
            dispatched = False

            async def dispatch():
                nonlocal dispatched
                await checked(before_dispatch)
                dispatched = True

            dispatch.read = getattr(before_dispatch, "read", before_dispatch)
            try:
                return await backend.execute(invocation, references, dispatch)
            except Rejected as failure:
                # Every ``Rejected`` is definitive: the provider's own answer or a
                # local check proves this attempt was not billed.
                diagnostics.append(dict(failure.diagnostics[0]))
                if failure.kind == REJECTED_STOP:
                    raise NativeImageError("native_endpoint_rejected", diagnostics=diagnostics,
                                           unconfirmed=False)
                if not failure.switchable:
                    raise NativeImageError(failure.code, diagnostics=diagnostics, unconfirmed=False)
                if failure.kind == REJECTED_REQUEST:
                    request_invalid = True
                    continue
                if failure.kind == REJECTED_RATE:
                    rate_limited = True
                    rate_budget -= 1
                    if rate_budget <= 0:
                        break
                    continue
                rejected = True
                continue
            except Exception as exc:
                if dispatched:
                    # A transport failure after the fence is never retried: the
                    # provider may already have executed and billed the request.
                    raise
                if isinstance(exc, NativeImageError) and exc.code in ABORTING_CODES:
                    raise
                # A pre-fence, endpoint-local failure never reached the provider;
                # it may fall through, but it is still recorded so that the final
                # code cannot claim "no backend is configured".
                diagnostics.append(_diagnostic(_backend_config(backend), 0, "endpoint_unavailable",
                                               _flatten(exc.code if isinstance(exc, NativeImageError)
                                                        else type(exc).__name__)))
                rejected = True
        if request_invalid:
            raise NativeImageError("backend_request_invalid", diagnostics=diagnostics,
                                   unconfirmed=False)
        if rate_limited:
            raise NativeImageError("backend_rate_limited", diagnostics=diagnostics,
                                   unconfirmed=False)
        if rejected or diagnostics:
            raise NativeImageError("native_endpoints_rejected", diagnostics=diagnostics,
                                   unconfirmed=False)
        if not routed:
            raise NativeImageError("native_backend_unconfigured", unconfirmed=False)
        raise NativeImageError("unsupported_image_operation", unconfirmed=False)

    async def recover(self, locator, authorize):
        name = locator.get("backend")
        if name not in {"comfyui", "sd_webui"}:
            raise NativeImageError("native_backend_changed")
        backend = NativeTransportBackend(name, self.config.get(name, {}), self.directory / name)
        return await backend.recover(locator, authorize)

    async def close(self):
        self._closed = True
