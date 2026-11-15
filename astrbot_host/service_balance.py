"""Bounded HTTPS JSON balance adapter for host settings and environment fallback."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
from urllib.parse import urlsplit

import aiohttp

from companion_v2.proactive.balance_events import BalanceObservation, display_amount
from companion_v2.execution.models import digest


PREFIX = "COMPANION_V2_BALANCE_"
MAX_RESPONSE_BYTES = 65536
NEWAPI_DEFAULT_DIVISOR = "500000"
HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}\Z")


def _path(value):
    if type(value) is not str or not value or len(value) > 128:
        raise ValueError("balance JSON path must be a short dotted path")
    parts = value.split(".")
    if any(not part or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*|[0-9]+", part) for part in parts):
        raise ValueError("balance JSON path has an invalid component")
    return tuple(parts)


def _read_path(payload, path):
    value = payload
    for part in path:
        if type(value) is dict and part in value:
            value = value[part]
        elif type(value) is list and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError("balance JSON path is missing")
    return value


def _newapi_user_url(value):
    if type(value) is not str or len(value) > 2048:
        raise ValueError("NewAPI URL is invalid")
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        raise ValueError("NewAPI URL must not include a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/user/self"):
        pass
    elif path.endswith("/api"):
        path += "/user/self"
    else:
        path += "/api/user/self"
    return parsed._replace(path=path).geturl()


def _newapi_compatible_urls(value):
    """Return the configured NewAPI endpoint followed by its known host alias."""
    endpoint = _newapi_user_url(value)
    parsed = urlsplit(endpoint)
    hostname = (parsed.hostname or "").lower()
    aliases = {"api.syuan.org", "ai.syuan.org"}
    if hostname not in aliases:
        return (endpoint,)
    alternate = "ai.syuan.org" if hostname == "api.syuan.org" else "api.syuan.org"
    if parsed.port is not None:
        alternate = f"{alternate}:{parsed.port}"
    return (endpoint, parsed._replace(netloc=alternate).geturl())


def _amount(value, divisor):
    if type(value) is bool or type(value) not in (str, int, float, Decimal):
        raise ValueError("balance JSON value is not numeric")
    try:
        amount = Decimal(str(value)) / divisor
    except (InvalidOperation, ZeroDivisionError) as error:
        raise ValueError("balance JSON value is invalid") from error
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000000000"):
        raise ValueError("balance JSON value is outside the accepted range")
    return amount


class ServiceBalancePort:
    def __init__(self, *, url, api_key="", auth_header="Authorization", auth_scheme="Bearer",
                 headers=None, balance_path="balance", total_path="", used_path="",
                 divisor="1", timeout_seconds=6, now=None, provider="json", currency_label="额度"):
        headers = {} if headers is None else headers
        if type(url) is not str or len(url) > 2048:
            raise ValueError("service balance URL is invalid")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or any(ord(char) < 33 for char in url)):
            raise ValueError("service balance URL must be an absolute HTTPS URL without embedded credentials")
        if (type(api_key) is not str or any(ord(char) < 32 for char in api_key)
                or type(auth_header) is not str or not HEADER_NAME.fullmatch(auth_header)
                or type(auth_scheme) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{0,32}", auth_scheme)):
            raise ValueError("service balance authorization settings are invalid")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 10:
            raise ValueError("service balance timeout must be 1-10 seconds")
        if type(headers) is not dict or len(headers) > 8:
            raise ValueError("service balance custom headers must be a small JSON object")
        for name, value in headers.items():
            if (type(name) is not str or not HEADER_NAME.fullmatch(name) or type(value) is not str
                    or len(value) > 2048 or any(ord(char) < 32 for char in value)):
                raise ValueError("service balance custom header is invalid")
        if api_key and auth_header.lower() in {name.lower() for name in headers}:
            raise ValueError("service balance authorization header is duplicated")
        try:
            divisor_value = Decimal(str(divisor))
        except InvalidOperation as error:
            raise ValueError("service balance divisor is invalid") from error
        if not divisor_value.is_finite() or divisor_value <= 0:
            raise ValueError("service balance divisor must be positive")
        if (provider not in {"json", "newapi"} or type(currency_label) is not str
                or not currency_label or len(currency_label) > 24
                or any(ord(char) < 32 for char in currency_label)):
            raise ValueError("service balance display settings are invalid")
        self.url, self.timeout_seconds = url, timeout_seconds
        self.provider = provider
        self.provider_label = "NewAPI" if provider == "newapi" else "服务余额"
        self.currency_label = currency_label
        self.headers = dict(headers)
        if api_key:
            self.headers[auth_header] = (auth_scheme + " " if auth_scheme else "") + api_key
        self.balance_path = _path(balance_path) if balance_path else None
        self.total_path = _path(total_path) if total_path else None
        self.used_path = _path(used_path) if used_path else None
        if self.balance_path is None and (self.total_path is None or self.used_path is None):
            raise ValueError("service balance requires a balance path or total and used paths")
        self.divisor, self.now = divisor_value, now or (lambda: datetime.now(timezone.utc))
        self.request_urls = (_newapi_compatible_urls(url) if provider == "newapi" else (url,))
        self.source_fingerprint = digest([self.provider, self.url, self.headers, self.balance_path,
            self.total_path, self.used_path, str(self.divisor), self.currency_label, self.request_urls])

    def parse(self, payload, observed_at):
        if self.provider == "newapi" and isinstance(payload, dict) and payload.get("success") is False:
            raise ValueError("NewAPI balance response was unsuccessful")
        total = _amount(_read_path(payload, self.total_path), self.divisor) if self.total_path else None
        if self.balance_path:
            balance = _amount(_read_path(payload, self.balance_path), self.divisor)
        else:
            used = _amount(_read_path(payload, self.used_path), self.divisor)
            balance = max(Decimal(0), total - used)
        return BalanceObservation(balance, total, observed_at)

    async def fetch(self):
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            last_error = None
            for url in self.request_urls:
                try:
                    async with session.get(url, headers=self.headers, allow_redirects=False) as response:
                        if (response.status != 200 or response.content_length is not None
                                and response.content_length > MAX_RESPONSE_BYTES):
                            raise ValueError("service balance response is unavailable")
                        raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError("service balance response exceeds the limit")
                    return self.parse(json.loads(raw, parse_float=Decimal), self.now())
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError, UnicodeError, ValueError) as error:
                    last_error = error
            if last_error is not None:
                raise last_error
            raise ValueError("service balance response is unavailable")


def configured_balance_source(config=None):
    settings = config.get("service_balance", {}) if isinstance(config, dict) else {}
    if type(settings) is not dict:
        raise ValueError("service balance settings have an invalid shape")
    configured_url = settings.get("url", "")
    configured_key = settings.get("api_key", "")
    if settings.get("advanced_enabled", False) or configured_url or configured_key:
        return {"source": "plugin", "settings": settings}

    names = ("PROVIDER", "URL", "API_KEY", "AUTH_HEADER", "AUTH_SCHEME", "CUSTOM_HEADERS",
             "JSON_PATH", "TOTAL_JSON_PATH", "USED_JSON_PATH", "VALUE_DIVISOR", "CURRENCY_LABEL",
             "TIMEOUT_SECONDS")
    values = {name: os.environ.get(PREFIX + name, "") for name in names}
    return {"source": "environment", "settings": values} if any(values.values()) else None


def configured_balance_port(config=None, *, context=None):
    source = configured_balance_source(config)
    if source is None:
        return None
    settings = source["settings"]
    if source["source"] == "plugin":
        if type(settings.get("advanced_enabled", False)) is not bool:
            raise ValueError("advanced balance switch must be boolean")
        if settings.get("advanced_enabled", False):
            from .service_balance_script import ScriptServiceBalancePort
            binding = settings.get("advanced_from_astrbot", False)
            if type(binding) is not bool:
                raise ValueError("AstrBot balance binding must be boolean")
            if binding:
                from .service_balance_provider import AstrBotBalancePort
                return AstrBotBalancePort(settings, context)
            return ScriptServiceBalancePort(settings)
        configured_url = settings.get("url", "")
        configured_key = settings.get("api_key", "")
        if type(configured_url) is not str or not configured_url.strip():
            raise ValueError("NewAPI balance URL is required")
        if type(configured_key) is not str or not configured_key.strip():
            raise ValueError("NewAPI balance requires a dashboard access token")
        user_id = settings.get("user_id", "")
        if type(user_id) is not str or user_id and not re.fullmatch(r"[1-9][0-9]{0,18}", user_id):
            raise ValueError("NewAPI user ID must be a positive account ID")
        return ServiceBalancePort(url=_newapi_user_url(configured_url.strip()), api_key=configured_key,
            headers={"New-Api-User": user_id} if user_id else {},
            balance_path="data.quota", divisor=settings.get("value_divisor", NEWAPI_DEFAULT_DIVISOR),
            provider="newapi", currency_label=settings.get("currency_label", "额度"))

    values = settings
    if not values["URL"]:
        raise ValueError("service balance URL is required when balance environment settings are present")
    provider = values["PROVIDER"].strip().lower() or "json"
    if provider not in {"json", "newapi"}:
        raise ValueError("service balance provider must be json or newapi")
    try:
        custom_headers = json.loads(values["CUSTOM_HEADERS"] or "{}")
        timeout = int(values["TIMEOUT_SECONDS"] or "6")
    except (ValueError, TypeError) as error:
        raise ValueError("service balance environment settings are invalid") from error
    if provider == "newapi":
        if not values["API_KEY"]:
            raise ValueError("NewAPI balance requires a dashboard access token")
        return ServiceBalancePort(url=_newapi_user_url(values["URL"]), api_key=values["API_KEY"],
            headers=custom_headers, balance_path="data.quota", divisor=values["VALUE_DIVISOR"] or NEWAPI_DEFAULT_DIVISOR,
            timeout_seconds=timeout, provider=provider, currency_label=values["CURRENCY_LABEL"] or "额度")
    return ServiceBalancePort(url=values["URL"], api_key=values["API_KEY"],
        auth_header=values["AUTH_HEADER"] or "Authorization",
        auth_scheme=values["AUTH_SCHEME"] or "Bearer", headers=custom_headers,
        balance_path=values["JSON_PATH"] or ("" if values["TOTAL_JSON_PATH"] and values["USED_JSON_PATH"] else "balance"),
        total_path=values["TOTAL_JSON_PATH"], used_path=values["USED_JSON_PATH"],
        divisor=values["VALUE_DIVISOR"] or "1", timeout_seconds=timeout,
        currency_label=values["CURRENCY_LABEL"] or "额度")


BALANCE_KEYWORDS = ("余额", "额度", "欠费", "还剩多少")
BALANCE_MATERIAL_INSTRUCTION = (
    "这是 Bot 所用服务在观测时间的经费余额，不是用户的欠款、付款义务或对方账户余额。"
    "被问到余额、额度时用它回答，并带上观测时间；state=unavailable 时说明这次没取到或还没接余额接口，"
    "不要猜金额，也不要把这段材料说成对方的钱包。")


def balance_keyword_material(application):
    """登记「问余额时才去查一次」的材料。

    映射面刻意很窄：只有主要陪伴对象本人的私聊、只有在余额源已配置时才登记；
    关键词只认这四种说法，其余消息一次请求都不会多发。
    """
    from companion_v2.conversation.keyword_materials import KeywordMaterialSpec
    from companion_v2.expression.configuration import source_subject_allowed_for_context
    from companion_v2.foundation.refs import ScopeKind

    async def prepare(context, text, at):
        port = getattr(application, "balance_port", None)
        if port is None:
            return {"state": "unavailable", "code": "balance_source_unavailable"}
        observation = await asyncio.wait_for(port.fetch(), timeout=8)
        if getattr(application, "balance_port", None) is not port:
            return {"state": "unavailable", "code": "balance_source_changed"}
        total = observation.total
        try:
            percent = round(float(observation.balance) * 100 / float(total), 1) if total else None
        except (ArithmeticError, ValueError):
            percent = None
        return {"state": "ok",
                "currency": getattr(observation, "unit", None) or getattr(port, "currency_label", "额度") or "额度",
                "balance": display_amount(observation.balance),
                "total": display_amount(total) if total is not None else None,
                "percent": percent,
                "observed_at": observation.observed_at.isoformat()}

    last_source = [None]

    def available(context):
        port = getattr(application, "balance_port", None)
        fingerprint = getattr(port, "source_fingerprint", None)
        if last_source[0] != fingerprint:
            last_source[0] = fingerprint
            materials = getattr(application, "keyword_materials", None)
            if callable(getattr(materials, "invalidate", None)):
                materials.invalidate("service.balance")
        if context.scope.kind is not ScopeKind.PRIVATE:
            return False
        if getattr(application, "balance_port", None) is None:
            return False
        try:
            return bool(source_subject_allowed_for_context(application.runtime, context))
        except Exception:
            return False

    return KeywordMaterialSpec(owner="service.balance", title="服务余额",
        instruction=BALANCE_MATERIAL_INSTRUCTION, keywords=BALANCE_KEYWORDS,
        prepare=prepare, available=available, ttl_seconds=300)


def balance_observation_data(port, observation):
    """Keep the existing amount contract and add optional management metadata."""
    data = {"currency_label": getattr(observation, "unit", None) or port.currency_label,
            "amount": display_amount(observation.balance),
            "observed_at": observation.observed_at.isoformat()}
    for name in ("total", "used"):
        value = getattr(observation, name, None)
        if value is not None:
            data[name] = display_amount(value)
    for name in ("plan_name", "extra"):
        value = getattr(observation, name, None)
        if value:
            data[name] = value
    return data


def balance_failure_data(error):
    if getattr(error, "safe_balance_error", False):
        return {"state": error.state, "message": str(error)}
    return {"state": "unavailable"}
