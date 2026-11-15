"""Administrator-configured, bounded request/extractor balance queries.

Only request strings cross into the host HTTP client. No Python callbacks, I/O,
module loader or credentials are exposed to the JavaScript runtime.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
import re
from urllib.parse import unquote, urlsplit

import aiohttp

from companion_v2.execution.models import digest
from companion_v2.proactive.balance_events import BalanceObservation
from .service_balance import MAX_RESPONSE_BYTES, ServiceBalancePort, _amount

MAX_SCRIPT_BYTES = 16384
MAX_REQUEST_BYTES = 16384
SCRIPT_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="balance-script")
RESULT_FIELDS = ("isValid", "invalidMessage", "remaining", "unit", "planName", "total", "used", "extra")


class BalanceQueryError(ValueError):
    """A bounded, credential-redacted explanation safe for the management page."""

    safe_balance_error = True

    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


def _interpolate(script, variables):
    """Expand literal variables before string operations, without JS injection.

    Encoding every UTF-16 code unit as a JS Unicode escape works inside both
    quote styles and untagged template strings. Quotes, backslashes and `${...}`
    in credential values cannot terminate the enclosing literal or become code.
    """
    values = {"baseUrl": "", "apiKey": "", "accessToken": "", "userId": "", **(variables or {})}
    def replace(match):
        name = match.group(1)
        if name not in values:
            raise ValueError("unknown balance template variable")
        encoded = values[name].encode("utf-16-be")
        return "".join("\\u%04x" % int.from_bytes(encoded[i:i + 2], "big")
                       for i in range(0, len(encoded), 2))
    return re.sub(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}", replace, script)


def _execute(script, response=None, variables=None):
    # Create, use and destroy every context on the same worker thread. The two
    # phases deliberately evaluate the expression independently, as CC Switch does.
    try:
        import quickjs
        context = quickjs.Context()
        context.set_memory_limit(16 * 1024 * 1024)
        context.set_max_stack_size(256 * 1024)
        context.set_time_limit(0.5)
        script = _interpolate(script, variables)
        operation = "config.request" if response is None else "config.extractor(JSON.parse(" + json.dumps(response) + "))"
        code = """(() => {
          const stringify = JSON.stringify;
          const config = (
SCRIPT
          );
          if (!config || typeof config !== 'object' || Array.isArray(config)
              || typeof config.extractor !== 'function') throw Error('invalid configuration');
          const result = OPERATION;
          if (!result || typeof result !== 'object' || Array.isArray(result)
              || typeof result.then === 'function') throw Error('expected one synchronous object');
          for (const name of ['remaining','total','used']) {
            if (result[name] != null && (typeof result[name] !== 'number'
                || !Number.isFinite(result[name]))) throw Error('invalid amount');
          }
          return stringify(result);
        })()""".replace("OPERATION", operation, 1).replace("SCRIPT", script, 1)
        encoded = context.eval(code)
        if type(encoded) is not str or len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("oversized script result")
        return json.loads(encoded)
    except Exception:
        # Never expose JS exception text: it may contain the script or payload.
        raise BalanceQueryError("script_error", "余额脚本执行失败，请检查语法、返回格式或运行限制。") from None


def _proxy(value):
    if type(value) is not str or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("invalid balance proxy")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "socks5"} or not parsed.hostname or not parsed.port
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("invalid balance proxy")
    except ValueError:
        raise ValueError("balance proxy must be an HTTP or SOCKS5 URL with a port") from None
    return value


class ScriptServiceBalancePort:
    provider = "script"
    provider_label = "高级余额查询"
    currency_label = "额度"

    def __init__(self, settings, *, now=None):
        self.script = settings.get("advanced_script", "")
        self.base_url = settings.get("advanced_base_url", "")
        self.api_key = settings.get("advanced_api_key", "")
        self.access_token = settings.get("advanced_access_token", "")
        self.user_id = settings.get("advanced_user_id", "")
        if (type(self.script) is not str or not self.script.strip()
                or len(self.script.encode("utf-8")) > MAX_SCRIPT_BYTES):
            raise ValueError("balance script must be nonempty and at most 16 KiB")
        if type(self.base_url) is not str or type(self.api_key) is not str or len(self.api_key) > 4096:
            raise ValueError("invalid advanced balance credentials")
        if self.base_url:
            ServiceBalancePort(url=self.base_url)
        if (type(self.access_token) is not str or len(self.access_token) > 4096
                or type(self.user_id) is not str or len(self.user_id) > 19
                or self.user_id and not re.fullmatch(r"[0-9]+", self.user_id)):
            raise ValueError("invalid advanced balance access token or user ID")
        if any(ord(c) < 32 for c in self.api_key + self.access_token):
            raise ValueError("invalid advanced balance token")
        self.proxy_url = _proxy(settings.get("advanced_proxy_url", ""))
        if importlib.util.find_spec("quickjs") is None:
            raise ValueError("advanced balance queries require quickjs-ng")
        if self.proxy_url and importlib.util.find_spec("aiohttp_socks") is None:
            raise ValueError("balance proxy requires aiohttp-socks")
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.variables = {"baseUrl": self.base_url, "apiKey": self.api_key,
                          "accessToken": self.access_token, "userId": self.user_id}
        self.source_fingerprint = digest([self.provider, self.script, self.variables, self.proxy_url])

    def _safe_text(self, value):
        if type(value) is not str:
            raise ValueError("invalid balance result text")
        # Protect both the raw URL and decoded proxy credentials in explanations.
        parsed = urlsplit(self.proxy_url)
        secrets = (self.api_key, self.access_token, self.proxy_url, parsed.username or "", parsed.password or "")
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[redacted]").replace(unquote(secret), "[redacted]")
        return "".join(c for c in value if ord(c) >= 32)[:256]

    def _request(self, config):
        if type(config) is not dict or set(config) - {"url", "method", "headers", "body"}:
            raise ValueError("invalid balance request fields")
        def substitute(value):
            if type(value) is not str:
                raise ValueError("balance request values must be strings")
            return value
        url = substitute(config.get("url", ""))
        headers = config.get("headers", {})
        if type(headers) is not dict:
            raise ValueError("invalid balance request headers")
        headers = {name: substitute(value) for name, value in headers.items()}
        # Share the existing HTTPS, credential, header count and size validation.
        ServiceBalancePort(url=url, headers=headers)
        method = config.get("method", "GET")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            raise ValueError("invalid balance request method")
        body = config.get("body")
        if body is not None:
            body = substitute(body)
            if len(body.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValueError("balance request body exceeds 16 KiB")
        return url, method, headers, body

    def parse(self, result, observed_at):
        if type(result) is not dict:
            raise ValueError("balance extractor must return one object")
        validity = result.get("isValid")
        if validity is not None and type(validity) is not bool:
            raise ValueError("invalid balance validity")
        text = {name: self._safe_text(result[name]) if result.get(name) is not None else None
                for name in ("invalidMessage", "unit", "planName", "extra")}
        if validity is False:
            raise BalanceQueryError("account_invalid", text["invalidMessage"] or "服务账户不可用。")
        for name in ("remaining", "total", "used"):
            if result.get(name) is not None and type(result[name]) not in (int, float):
                raise ValueError("balance extractor amounts must be numbers")
        total = _amount(result["total"], Decimal(1)) if result.get("total") is not None else None
        used = _amount(result["used"], Decimal(1)) if result.get("used") is not None else None
        if result.get("remaining") is not None:
            balance = _amount(result["remaining"], Decimal(1))
        elif total is not None and used is not None:
            balance = max(Decimal(0), total - used)
        else:
            raise BalanceQueryError("amount_missing", "提取结果没有可用余额，请返回 remaining 或 total 与 used。")
        unit = text["unit"]
        if unit and len(unit) > 24:
            raise ValueError("balance unit must be at most 24 characters")
        return BalanceObservation(balance, total, observed_at, unit=unit or None, used=used,
                                  plan_name=text["planName"], extra=text["extra"])

    async def _run(self, response=None):
        loop = asyncio.get_running_loop()
        # Bound outstanding work as well as worker count; don't accumulate jobs
        # behind an overloaded default executor. The gate belongs to this loop.
        gate = getattr(loop, "_companion_balance_script_gate", None)
        if gate is None:
            gate = asyncio.Semaphore(2)
            setattr(loop, "_companion_balance_script_gate", gate)
        async with gate:
            return await loop.run_in_executor(SCRIPT_WORKERS, _execute, self.script, response, self.variables)

    async def fetch(self):
        try:
            url, method, headers, body = self._request(await self._run())
            connector = None
            if self.proxy_url:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False, connector=connector) as session:
                async with session.request(method, url, headers=headers, data=body,
                                           allow_redirects=False) as response:
                    if not 200 <= response.status < 300:
                        raise BalanceQueryError("http_error", f"余额服务返回 HTTP {response.status}。")
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise ValueError("oversized balance response")
                    parts, length = [], 0
                    async for chunk in response.content.iter_chunked(8192):
                        length += len(chunk)
                        if length > MAX_RESPONSE_BYTES:
                            raise ValueError("oversized balance response")
                        parts.append(chunk)
            raw = b"".join(parts).decode("utf-8")
            json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            return self.parse(await self._run(raw), self.now())
        except BalanceQueryError:
            raise
        except Exception:
            raise BalanceQueryError("unavailable", "余额查询失败，请检查高级请求、提取结果或代理设置。") from None
