"""Host-owned weather source settings, credentials and observation reuse."""

import asyncio
from copy import deepcopy
import hmac
from time import monotonic
from urllib.parse import urlsplit

from companion_v2.execution.models import TaskError, digest

from .qweather_credentials import HOST_PATTERN, credentials_from_environment, read_credentials
from .qweather_weather import configured_qweather_weather_port
from .qweather_alerts import configured_qweather_alert_port


PROVIDERS = ("auto", "qweather", "open-meteo")


def weather_settings(config):
    values = config.get("weather", {}) if isinstance(config, dict) else {}
    if type(values) is not dict:
        raise ValueError("invalid weather settings")
    provider, interval = values.get("provider", "auto"), values.get("refresh_minutes", 5)
    if provider not in PROVIDERS or type(interval) is not int or not 1 <= interval <= 120:
        raise ValueError("invalid weather source or refresh interval")
    return {"provider": provider, "refresh_minutes": interval,
            "api_host": values.get("api_host", ""), "credential": values.get("credential", ""),
            "jwt_credential_id": values.get("jwt_credential_id", ""),
            "jwt_project_id": values.get("jwt_project_id", ""),
            "jwt_developer_id": values.get("jwt_developer_id", ""),
            "has_jwt_private_key": bool(values.get("jwt_private_key"))}


def normalize_host(value):
    if type(value) is not str or len(value) > 253:
        raise ValueError("invalid QWeather API Host")
    value = value.strip().lower()
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.port:
            raise ValueError("use the QWeather API Host without an endpoint")
        value = parsed.hostname or ""
    if value and not HOST_PATTERN.fullmatch(value):
        raise ValueError("use the assigned QWeather API Host")
    return value


class CachedWeatherPort:
    """Reuse a source observation across page, planning and contact reads."""

    def __init__(self, port, refresh_minutes, *, clock=monotonic):
        self.port, self.refresh_minutes, self.clock = port, refresh_minutes, clock
        self.provider = getattr(port, "provider", "")
        self.cache, self.pending = {}, {}

    async def fetch(self, location):
        saved = self.cache.get(location)
        if saved is not None and 0 <= self.clock() - saved[0] < self.refresh_minutes * 60:
            return saved[1]
        pending = self.pending.get(location)
        if pending is None:
            pending = asyncio.create_task(self._fetch(location))
            pending.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            self.pending[location] = pending
        return await asyncio.shield(pending)

    async def refresh(self, location):
        self.cache.pop(location, None)
        return await self.fetch(location)

    async def _fetch(self, location):
        try:
            snapshot = await self.port.fetch(location)
            if len(self.cache) >= 64 and location not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            self.cache[location] = (self.clock(), snapshot)
            return snapshot
        finally:
            self.pending.pop(location, None)


def configured_weather_ports(config):
    settings = weather_settings(config)
    return (CachedWeatherPort(configured_qweather_weather_port(config), settings["refresh_minutes"]),
            configured_qweather_alert_port(config))


class WeatherHostSettings:
    def __init__(self, controller):
        self.controller = controller

    def view(self):
        config = self.controller.config
        try:
            settings = weather_settings(config)
            values = read_credentials(config)
        except (ValueError, TypeError, KeyError):
            raise TaskError("weather_source_invalid", "check the host weather settings") from None
        plugin = bool(settings["api_host"] or settings["credential"]
                      or settings["jwt_credential_id"] or settings["jwt_project_id"]
                      or settings["jwt_developer_id"] or settings["has_jwt_private_key"])
        source = "plugin" if plugin else "environment" if values else "none"
        host = values["API_HOST"] if values else ""
        signed = bool(values and (values.get("PRIVATE_KEY_PEM") or values.get("PRIVATE_KEY_FILE")))
        credential_kind = ("signed_jwt" if signed else
                           "api_key" if values and values["API_KEY"] else
                           "jwt" if values and values.get("JWT_TOKEN") else "none")
        # 页面用 has_credential 决定是否显示「已配置」。半组（只填了 API Key、只填了
        # 开发者 ID 等）无法认证任何请求，若仍按「有字段即已配置」就会和 source_state
        # 的「配置待完善」同时出现。这里复用适配器的真实可用性判定，避免两处规则漂移。
        try:
            has_credential = credentials_from_environment(label="host", config=config) is not None
        except (ValueError, TypeError, KeyError, OSError):
            has_credential = False
        try:
            weather, alerts = configured_weather_ports(config)
        except (ValueError, TypeError, KeyError, OSError):
            weather, alerts, state = None, None, "invalid"
        else:
            state = "configured"
        return {"settings": {"provider": settings["provider"], "api_host": host,
                             "refresh_minutes": settings["refresh_minutes"],
                             "jwt_credential_id": settings["jwt_credential_id"],
                             "jwt_project_id": settings["jwt_project_id"],
                             "jwt_developer_id": settings["jwt_developer_id"],
                             "has_jwt_private_key": settings["has_jwt_private_key"]},
                "credential_source": source, "credential_kind": credential_kind,
                "has_credential": has_credential, "source_state": state,
                "actual_provider": getattr(weather, "provider", ""), "alerts_ready": alerts is not None,
                "edit_token": digest([self.controller.runtime.installation_id, "weather", settings, values]),
                "apply_mode": "immediate"}

    def configure(self, data):
        current = self.view()
        if not hmac.compare_digest(data["edit_token"], current["edit_token"]):
            raise TaskError("host_options_conflict", "weather settings changed; reload before saving")
        changes = data["changes"]
        signed_names = ("jwt_credential_id", "jwt_project_id", "jwt_developer_id", "jwt_private_key")
        if set(changes) - {"provider", "api_host", "credential", "refresh_minutes", "clear_credentials",
                           *signed_names}:
            raise TaskError("host_options_request_invalid", "unknown weather setting")
        candidate = deepcopy(dict(self.controller.config))
        section = candidate.setdefault("weather", {})
        clear = changes.get("clear_credentials", False)
        if type(clear) is not bool:
            raise TaskError("weather_source_invalid", "invalid clear option")
        if clear:
            if set(changes) != {"clear_credentials"}:
                raise TaskError("host_options_request_invalid", "clear credentials separately")
            section.update(api_host="", credential="", jwt_credential_id="", jwt_project_id="",
                           jwt_developer_id="", jwt_private_key="")
            if section.get("provider") == "qweather":
                section["provider"] = "auto"
        else:
            for name in ("provider", "refresh_minutes"):
                if name in changes:
                    section[name] = changes[name]
            credential = changes.get("credential", "")
            if type(credential) is not str or len(credential) > 4096:
                raise TaskError("weather_source_invalid", "check the QWeather credential")
            credential = credential.strip()
            signed_changes = {name: changes[name] for name in signed_names if name in changes}
            for name, value in signed_changes.items():
                if type(value) is not str or len(value) > 16384:
                    raise TaskError("weather_source_invalid", "check the QWeather JWT settings")
                signed_changes[name] = value.strip()
            if credential and signed_changes:
                raise TaskError("weather_source_invalid",
                                "use either a JWT token/API Key or private-key JWT fields")
            if credential:
                section["credential"] = credential
                section.update(jwt_credential_id="", jwt_project_id="",
                               jwt_developer_id="", jwt_private_key="")
            elif signed_changes:
                section["credential"] = ""
                section.update(signed_changes)
            if "api_host" in changes:
                try:
                    section["api_host"] = normalize_host(changes["api_host"])
                except ValueError:
                    raise TaskError("weather_source_invalid", "check the assigned QWeather API Host") from None
            # A provider-only save continues to use environment credentials. Editing
            # their host or credential deliberately creates a complete host-owned group.
            # 只有环境组完整可用时才需要这个保护；半配置的环境组本来就没有可用的
            # 凭据可替换，用户补 Host 时交给下面的端口校验给出通用的配置无效提示，
            # 而不是误报「替换环境 Host 必须填写凭据」。
            if (current["credential_source"] == "environment" and current["has_credential"]
                    and (credential or signed_changes or "api_host" in changes)):
                if not section.get("api_host"):
                    section["api_host"] = current["settings"]["api_host"]
                if not credential and not signed_changes:
                    raise TaskError("weather_source_credential_required",
                                    "enter a credential when replacing the environment host")
        try:
            weather, alerts = configured_weather_ports(candidate)
        except (ValueError, TypeError, KeyError, OSError):
            raise TaskError("weather_source_invalid", "check the weather source, API Host and JWT or API Key") from None
        save = getattr(self.controller, "save_plugin_config", None)
        if not callable(save):
            raise TaskError("host_options_save_unavailable", "host configuration saving is unavailable")
        save(candidate)
        applications = []
        for name in ("session", "last_session"):
            app = getattr(getattr(self.controller, name, None), "application", None)
            if app is not None and all(app is not prior for prior in applications):
                applications.append(app)
        for app in applications:
            planning = getattr(app, "calendar_planning", None)
            if planning is not None:
                planning.weather_port = weather
            schedule = getattr(app, "contact_schedule", None)
            for name in ("weather_events", "weather_change_events", "official_alert_events"):
                events = getattr(schedule, name, None)
                if events is not None:
                    events.port = alerts if name == "official_alert_events" else weather
                    retry_at = getattr(events, "retry_at", None)
                    if retry_at is not None:
                        retry_at.clear()
            # 模型侧工具在装配时就把端口冻进 ``LocalRoleTools.weather.port``
            # （见 conversation.tool_loop、agenda.weather_tools），保存后不重绑会让
            # ``weather.current`` 一直走旧数据源，并继续持有已轮换或已清除的凭据。
            # 新端口自带空缓存，旧端口的缓存也没有消费者了，顺手清掉避免被仍持有
            # 旧对象的路径读回过期观测。
            tools = getattr(app, "tools", None)
            tool = getattr(tools, "weather", None) if tools is not None else None
            if tool is not None:
                previous = getattr(tool, "port", None)
                tool.port = weather
                stale_cache = getattr(previous, "cache", None)
                if previous is not weather and stale_cache is not None:
                    stale_cache.clear()
        return {"saved": True, **self.view()}
