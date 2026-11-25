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


def propagate_balance_port(controller, balance_port):
    """Synchronize an updated balance port across single-role and multi-role host sessions."""
    if controller is None:
        return
    base = getattr(controller, "controller", controller)
    owners = [controller]
    if base is not controller:
        owners.append(base)

    applications = []
    seen_ids = set()

    def _collect(app):
        if app is not None and id(app) not in seen_ids:
            seen_ids.add(id(app))
            applications.append(app)

    for owner in owners:
        for attr in ("session", "last_session"):
            sess = getattr(owner, attr, None)
            _collect(getattr(sess, "application", None))
        for attr in ("sessions", "_sessions"):
            group = getattr(owner, attr, None)
            if isinstance(group, dict):
                for sess in group.values():
                    _collect(getattr(sess, "application", None))
            elif isinstance(group, (list, tuple, set)):
                for sess in group:
                    _collect(getattr(sess, "application", None))

    for application in applications:
        application.balance_port = balance_port
        materials = getattr(application, "keyword_materials", None)
        invalidate = getattr(materials, "invalidate", None)
        if callable(invalidate):
            invalidate("service.balance")
        schedule = getattr(application, "contact_schedule", None)
        balance_events = getattr(schedule, "balance_events", None)
        if balance_events is not None:
            update_port = getattr(balance_events, "update_port", None)
            if callable(update_port):
                update_port(balance_port)
            else:
                balance_events.port = balance_port


class MultiStationBalancePort:
    """Bridge multi-station quota snapshots into companion_v2 BalanceObservation contract.

    Supports three awareness aggregation policies:
    - ``primary``: track the selected primary station (or first enabled participating station)
    - ``aggregate_sum``: convert participating fiat stations via live FX and sum them
    - ``any_low``: evaluate all participating stations and report the most urgent one
    """

    provider = "multi_station"
    provider_label = "多站点额度看板"

    def __init__(self, runtime_root, controller, *, now=None):
        from pathlib import Path

        self.runtime_root = Path(runtime_root).resolve()
        self.controller = controller
        self._now = now or getattr(controller, "now", None) or (lambda: datetime.now(timezone.utc))
        self._last_selected: tuple[str, str, str] | None = None

    def _service(self):
        from .quota_service import get_quota_watch_service

        return get_quota_watch_service(self.controller, self.runtime_root)

    def _participating(self):
        service = self._service()
        stations, awareness, _token, _legacy = service._effective_stations()
        enabled = [st for st in stations if st.enabled]
        if not enabled:
            return service, [], awareness
        participating = [st for st in enabled if st.participate_in_awareness]
        if awareness.mode == "primary":
            if awareness.primary_station_id:
                chosen = next((st for st in enabled if st.id == awareness.primary_station_id), None)
                if chosen is not None:
                    return service, [chosen], awareness
            if participating:
                return service, [participating[0]], awareness
            return service, [], awareness
        return service, participating, awareness

    @property
    def currency_label(self):
        service, participating, awareness = self._participating()
        if awareness.target_currency in ("CNY", "USD"):
            return awareness.target_currency
        if awareness.mode == "primary" and participating and participating[0].display_type:
            return participating[0].display_type
        display_currency, _rate = service._pricing_fx()
        return display_currency or "额度"

    @property
    def source_fingerprint(self):
        service, participating, awareness = self._participating()
        display_currency, _rate = service._pricing_fx()
        return digest([
            "multi_station",
            awareness.to_dict(),
            display_currency,
            [st.secret_free_fingerprint() for st in participating],
        ])

    @staticmethod
    def _dec(value):
        if value is None:
            return None
        d = Decimal(str(value))
        return max(Decimal(0), d) if d.is_finite() else None

    async def fetch(self):
        from companion_v2.model_gateway.quota import (
            convert_currency_amount,
            evaluate_station_health,
        )

        service, participating, awareness = self._participating()
        if not participating:
            raise ValueError("尚未指定参与余额感知的站点")

        snapshots = await service.refresh_for_awareness(participating)
        display_currency, usd_cny_rate = service._pricing_fx()
        target_currency = awareness.target_currency or display_currency or "CNY"
        now_dt = self._now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        if awareness.mode == "primary":
            spec = participating[0]
            snap = snapshots.get(spec.id)
            self._last_selected = (spec.id, spec.name, (snap.symbol if snap else "") or "")
            if snap is None or not snap.ok or snap.remaining is None:
                raise ValueError(f"主站点「{spec.name}」余额暂不可用")
            observed = datetime.fromisoformat(snap.checked_at) if snap.checked_at else now_dt
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            return BalanceObservation(
                self._dec(snap.remaining),
                self._dec(snap.total),
                observed,
                unit=snap.display_type or "额度",
                used=self._dec(snap.used),
                plan_name=spec.name[:120],
                extra=snap.extra[:200] if snap.extra else None,
            )

        if awareness.mode == "aggregate_sum":
            self._last_selected = ("", "多站点汇总", "")
            rem_sum = Decimal(0)
            total_sum = Decimal(0)
            used_sum = Decimal(0)
            has_total = True
            has_used = False
            ok_names = []
            latest_obs = None

            for spec in participating:
                snap = snapshots.get(spec.id)
                if snap is None or not snap.ok or snap.remaining is None:
                    continue
                conv_rem = convert_currency_amount(snap.remaining, snap.display_type, target_currency, usd_cny_rate)
                if conv_rem is None:
                    continue
                rem_sum += self._dec(conv_rem)
                if snap.total is not None:
                    conv_tot = convert_currency_amount(snap.total, snap.display_type, target_currency, usd_cny_rate)
                    if conv_tot is not None:
                        total_sum += self._dec(conv_tot)
                    else:
                        has_total = False
                else:
                    has_total = False
                if snap.used is not None:
                    conv_used = convert_currency_amount(snap.used, snap.display_type, target_currency, usd_cny_rate)
                    if conv_used is not None:
                        used_sum += self._dec(conv_used)
                        has_used = True
                ok_names.append(spec.name)
                if snap.checked_at:
                    dt = datetime.fromisoformat(snap.checked_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if latest_obs is None or dt > latest_obs:
                        latest_obs = dt

            if not ok_names:
                raise ValueError("参与余额感知的站点均无可用的 CNY/USD 余额数据")
            extra_text = f"已汇总 {len(ok_names)} 个站点（{target_currency}）"
            return BalanceObservation(
                rem_sum,
                total_sum if has_total and total_sum > 0 else None,
                latest_obs or now_dt,
                unit=target_currency,
                used=used_sum if has_used else None,
                plan_name="多站点汇总",
                extra=extra_text,
            )

        # mode == "any_low": choose the most urgent healthy station (critical > low > lowest pct / converted remaining)
        severity_rank = {"critical": 0, "low": 1, "normal": 2}
        candidates = []
        for spec in participating:
            snap = snapshots.get(spec.id)
            if snap is None or not snap.ok or snap.remaining is None:
                continue
            health = evaluate_station_health(spec, snap)
            rank = severity_rank.get(health, 2)
            conv_rem = convert_currency_amount(snap.remaining, snap.display_type, target_currency, usd_cny_rate)
            pct = snap.remaining_percent if snap.remaining_percent is not None else 101.0
            sort_key = (rank, pct, conv_rem if conv_rem is not None else snap.remaining)
            candidates.append((sort_key, spec, snap))

        if not candidates:
            raise ValueError("参与余额感知的站点暂均不可用")
        candidates.sort(key=lambda item: item[0])
        _, chosen_spec, chosen_snap = candidates[0]
        self._last_selected = (chosen_spec.id, chosen_spec.name, chosen_snap.symbol or "")
        observed = datetime.fromisoformat(chosen_snap.checked_at) if chosen_snap.checked_at else now_dt
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return BalanceObservation(
            self._dec(chosen_snap.remaining),
            self._dec(chosen_snap.total),
            observed,
            unit=chosen_snap.display_type or "额度",
            used=self._dec(chosen_snap.used),
            plan_name=chosen_spec.name[:120],
            extra=f"站点 {chosen_spec.name}（共监测 {len(candidates)} 站）",
        )

    def balance_metadata(self, observation=None):
        """Return explicit station & awareness-mode metadata for both success and error states."""
        from companion_v2.model_gateway.quota import normalize_currency_symbol

        service, participating, awareness = self._participating()
        stations, _, _, _ = service._effective_stations()
        enabled = [st for st in stations if st.enabled]
        snaps, _ = service.snapshot_cache.all()

        participating_enabled = [st for st in enabled if st.participate_in_awareness]
        is_primary_explicit = bool(
            awareness.mode == "primary"
            and awareness.primary_station_id
            and any(st.id == awareness.primary_station_id for st in enabled)
        )
        participating_count = len(participating_enabled)
        if participating_count == 0 and participating:
            participating_count = len(participating)

        obs_unit = str(getattr(observation, "unit", "") or "").strip().upper()
        station_id = ""
        station_name = ""
        currency_symbol = ""

        if awareness.mode == "primary":
            if participating:
                spec = participating[0]
                snap = snaps.get(spec.id)
                station_id = spec.id
                station_name = spec.name
                currency_symbol = (
                    (snap.symbol if snap and snap.symbol else "")
                    or normalize_currency_symbol(obs_unit or (snap.display_type if snap else "") or spec.display_type)
                )
        elif awareness.mode == "aggregate_sum":
            station_name = "多站点汇总"
            target_cur = obs_unit or awareness.target_currency or service._pricing_fx()[0] or "CNY"
            currency_symbol = normalize_currency_symbol(target_cur)
        else:
            if self._last_selected is not None and self._last_selected[1]:
                station_id, station_name, currency_symbol = self._last_selected
            elif participating:
                spec = participating[0]
                snap = snaps.get(spec.id)
                station_id = spec.id
                station_name = spec.name
                currency_symbol = snap.symbol if snap else ""
            if not currency_symbol:
                currency_symbol = normalize_currency_symbol(obs_unit)

        return {
            "source_kind": "quota_dashboard",
            "awareness_mode": awareness.mode,
            "station_id": station_id,
            "station_name": station_name,
            "is_primary_explicit": is_primary_explicit,
            "participating_count": participating_count,
            "currency_symbol": currency_symbol,
        }


def configured_balance_port(config=None, *, context=None, runtime_root=None, controller=None):
    if runtime_root is None and controller is not None:
        base_ctrl = getattr(controller, "controller", controller)
        runtime_obj = getattr(base_ctrl, "runtime", None)
        runtime_root = getattr(runtime_obj, "data_root", None) or getattr(base_ctrl, "data_root", None)
    if runtime_root is not None and controller is not None:
        from pathlib import Path
        from companion_v2.model_gateway.quota import QuotaStationStore

        store = QuotaStationStore(Path(runtime_root))
        if store.exists():
            stations, awareness, _token = store.read_safe()
            enabled = [st for st in stations if st.enabled]
            has_primary = bool(
                awareness.mode == "primary"
                and awareness.primary_station_id
                and any(st.id == awareness.primary_station_id for st in enabled)
            )
            has_participating = any(st.participate_in_awareness for st in enabled)
            if has_primary or has_participating:
                return MultiStationBalancePort(runtime_root, controller)

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
