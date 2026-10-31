"""Dashboard controls for host-wide voice, poke and forward-intake settings."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
import hmac
import json
from math import isfinite

from astrbot.api.web import request

from companion_v2.conversation.forwards.models import (DEFAULT_MAX_NODES, ForwardIntakePolicy, MAX_DEPTH,
                                                        MAX_FORWARDS, MAX_NODES)
from companion_v2.conversation.pokes import PokePolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome

from .management_identity import dashboard_operator
from .service_balance import (NEWAPI_DEFAULT_DIVISOR, configured_balance_port,
                              configured_balance_source, display_amount)
from .weather_settings import WeatherHostSettings


PREFIX = "/astrbot_plugin_private_companion/host-options"
VOICE_FIELDS = frozenset(("enabled", "probability", "private_probability", "group_probability"))
POKE_FIELDS = frozenset(("enabled", "trigger_probability", "replies", "back_probability", "back_times",
                         "model_reply_enabled", "model_reply_probability",
                         "context_reply_enabled", "context_delay_seconds"))
FORWARD_FIELDS = frozenset(("max_forwards", "max_nodes", "max_depth", "parse_nested"))


def _document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
    if type(value) is not dict or set(value) != {"edit_token", "changes"}:
        raise ValueError("unexpected fields")
    if (type(value["edit_token"]) is not str or len(value["edit_token"]) != 64
            or type(value["changes"]) is not dict or not value["changes"]):
        raise ValueError("invalid edit token or changes")
    return value


class HostOptionsWebAPI:
    """Keep this narrow write surface separate from arbitrary plugin config edits."""

    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read host-wide settings"),
                (PREFIX + "/voice", self.voice, ["POST"], "Save host voice defaults"),
                (PREFIX + "/poke", self.poke, ["POST"], "Save received-poke replies"),
                (PREFIX + "/forward_intake", self.forward_intake, ["POST"], "Save forward intake limits"),
                (PREFIX + "/balance", self.balance_status, ["GET"], "Read service balance source status"),
                (PREFIX + "/balance", self.balance, ["POST"], "Save service balance source"),
                (PREFIX + "/balance/test", self.balance_test, ["POST"], "Test a service balance source"),
                (PREFIX + "/weather", self.weather_status, ["GET"], "Read host weather source settings"),
                (PREFIX + "/weather", self.weather, ["POST"], "Save host weather source settings")]

    def _operator(self):
        username = request.username
        if (type(username) is not str or not username.strip() or username.startswith("api_key:")):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if request.query:
            raise TaskError("host_options_request_invalid", "host settings do not accept query overrides")
        controller = self.controller
        if getattr(controller, "_terminated", False):
            raise TaskError("staging_host_terminated", "plugin is stopped")
        if controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        return dashboard_operator(controller.runtime, username, at=controller.now())

    @staticmethod
    def _config_group(config, name):
        value = config.get(name, {})
        if type(value) is not dict:
            raise TaskError("host_options_invalid", "a host settings section has an invalid shape")
        return value

    def _view(self):
        config = self.controller.config
        if not isinstance(config, dict):
            raise TaskError("host_options_unavailable", "the plugin settings cannot be read here")
        voice_config = self._config_group(config, "voice_reply")
        voice_defaults = asdict(VoiceReplyPolicy())
        voice_values = {key: voice_config.get(key, default) for key, default in voice_defaults.items()}
        try:
            policy = VoiceReplyPolicy(**voice_values)
            voice_enabled = voice_config.get("enabled", False)
            if type(voice_enabled) is not bool:
                raise ValueError("voice switch is invalid")
            poke_policy = PokePolicy.parse(config.get("poke"))
            runtime_config = self._config_group(config, "runtime")
            defaults = ForwardIntakePolicy()
            forward_policy = ForwardIntakePolicy(
                max_forwards=runtime_config.get("forward_max_forwards", defaults.max_forwards),
                max_nodes=runtime_config.get("forward_max_nodes", defaults.max_nodes),
                max_depth=runtime_config.get("forward_max_depth", defaults.max_depth),
                parse_nested=runtime_config.get("forward_parse_nested", defaults.parse_nested),
            )
        except (TaskError, TypeError, ValueError) as error:
            raise TaskError("host_options_invalid", "check the host voice, received-poke and forward settings") from error

        voice = {"enabled": voice_enabled, "probability": policy.probability,
                 "private_probability": policy.private_probability,
                 "group_probability": policy.group_probability}
        poke = {"enabled": poke_policy.enabled, "trigger_probability": poke_policy.trigger_probability,
                "replies": list(poke_policy.replies), "back_probability": poke_policy.back_probability,
                "back_times": poke_policy.back_times, "model_reply_enabled": poke_policy.model_reply_enabled,
                "model_reply_probability": poke_policy.model_reply_probability,
                "context_reply_enabled": poke_policy.context_reply_enabled,
                "context_delay_seconds": poke_policy.context_delay_seconds}
        forward_intake = asdict(forward_policy)
        installation_id = self.controller.runtime.installation_id
        balance_config = self._config_group(config, "service_balance")
        balance_key = balance_config.get("api_key", "")
        balance_url = balance_config.get("url", "")
        balance_user_id = balance_config.get("user_id", "")
        balance_divisor = balance_config.get("value_divisor", NEWAPI_DEFAULT_DIVISOR)
        if (type(balance_key) is not str or type(balance_url) is not str
                or type(balance_user_id) is not str or type(balance_divisor) not in (str, int, float)):
            raise TaskError("host_options_invalid", "the service balance settings have an invalid shape")
        try:
            port = configured_balance_port(config)
            source = configured_balance_source(config)
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            port = None
            source = configured_balance_source(config)
            balance_state = "invalid"
        else:
            balance_state = "configured" if port is not None else "not_configured"
        plugin_source = bool(source and source["source"] == "plugin")
        balance = {"source": source["source"] if source else "none",
                   "source_state": balance_state,
                   "provider": "newapi" if plugin_source else getattr(port, "provider", "newapi"),
                   "url": balance_url if plugin_source else "",
                   "user_id": balance_user_id if plugin_source else "",
                   "value_divisor": str(balance_divisor) if plugin_source else NEWAPI_DEFAULT_DIVISOR,
                   "has_api_key": bool(balance_key),
                   "edit_token": digest([installation_id, "service_balance", balance_url, balance_key,
                                         balance_user_id, str(balance_divisor)])}
        return {"voice_reply": voice, "poke": poke, "forward_intake": forward_intake,
                "service_balance": balance,
                "forward_bounds": {"max_forwards": MAX_FORWARDS, "max_nodes": MAX_NODES, "max_depth": MAX_DEPTH},
                "edit_tokens": {"voice": digest([installation_id, "voice_reply", voice]),
                                "poke": digest([installation_id, "poke", poke]),
                                "forward_intake": digest([installation_id, "forward_intake", forward_intake]),
                                "balance": balance["edit_token"]},
                "apply_modes": {"voice": "restart_host", "poke": "immediate",
                                "forward_intake": "immediate_next_input", "balance": "immediate"}}

    async def _run(self, group):
        from .web_api import outcome_response

        try:
            async with self.controller._control_lock:
                operator = self._operator()
                if group == "weather_status":
                    result = Outcome.success(WeatherHostSettings(self.controller).view())
                elif group in {"status", "balance_status"}:
                    result = Outcome.success(self._view())
                else:
                    raw = await request.body()
                    if len(raw) > 8192:
                        raise ValueError("request is too large")
                    data = _document(raw)
                    result = Outcome.success(WeatherHostSettings(self.controller).configure(data)
                        if group == "weather" else self._configure(operator, group, data))
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("host_options_request_invalid", "check the host settings and retry"))
        except OSError:
            return outcome_response(Outcome.failed("host_options_save_failed", "the host settings could not be saved"))

    def _configure(self, operator, group, data):
        if group not in {"voice", "poke", "forward_intake", "balance"}:
            raise TaskError("host_options_request_invalid", "unknown host settings group")
        current = self._view()
        token = current["edit_tokens"][group]
        if data["edit_token"] != token:
            raise TaskError("host_options_conflict", "settings changed in another editor; reload before saving")
        changes = data["changes"]
        if group == "balance":
            return self._configure_balance(changes)
        allowed = {"voice": VOICE_FIELDS, "poke": POKE_FIELDS,
                   "forward_intake": FORWARD_FIELDS}[group]
        if set(changes) - allowed:
            raise TaskError("host_options_request_invalid", "only the listed host settings can be changed here")

        candidate = deepcopy(dict(self.controller.config))
        if group == "voice":
            section = candidate.setdefault("voice_reply", {})
            if type(section) is not dict:
                raise TaskError("host_options_invalid", "a host settings section has an invalid shape")
            section.update(changes)
            if "enabled" in changes and type(changes["enabled"]) is not bool:
                raise TaskError("host_options_invalid", "the voice switch must be boolean")
            for key in ("probability", "private_probability", "group_probability"):
                if key not in changes:
                    continue
                value = changes[key]
                minimum = -1 if key != "probability" else 0
                if (type(value) not in (int, float) or not isfinite(value)
                        or not minimum <= value <= 1):
                    raise TaskError("host_options_invalid", "voice probabilities must be 0–1, or -1 to inherit")
            defaults = asdict(VoiceReplyPolicy())
            values = {key: section.get(key, default) for key, default in defaults.items()}
            try:
                VoiceReplyPolicy(**values)
            except (TypeError, ValueError) as error:
                raise TaskError("host_options_invalid", "check the host voice settings") from error
            apply_mode = "restart_host"
        else:
            if group == "poke":
                section = candidate.setdefault("poke", {})
                if type(section) is not dict:
                    raise TaskError("host_options_invalid", "a host settings section has an invalid shape")
                section.update(changes)
            else:
                section = candidate.setdefault("runtime", {})
                if type(section) is not dict:
                    raise TaskError("host_options_invalid", "the runtime settings section has an invalid shape")
                names = {"max_forwards": "forward_max_forwards", "max_nodes": "forward_max_nodes",
                         "max_depth": "forward_max_depth", "parse_nested": "forward_parse_nested"}
                section.update({names[key]: value for key, value in changes.items()})
                try:
                    policy = ForwardIntakePolicy(
                        max_forwards=section.get("forward_max_forwards", MAX_FORWARDS),
                        max_nodes=section.get("forward_max_nodes", DEFAULT_MAX_NODES),
                        max_depth=section.get("forward_max_depth", MAX_DEPTH),
                        parse_nested=section.get("forward_parse_nested", True),
                    )
                except (TaskError, TypeError, ValueError) as error:
                    raise TaskError("host_options_invalid", "forward limits must be integers within the structural ceilings") from error
                try:
                    self._validate_host_settings(candidate)
                except (TaskError, TypeError, ValueError) as error:
                    raise TaskError("host_options_invalid", "forward limits cannot be applied with the current host settings") from error
                apply_mode = "immediate_next_input"
            if group == "poke":
                try:
                    policy = PokePolicy.parse(section)
                except (TypeError, ValueError) as error:
                    raise TaskError("host_options_invalid", "check the received-poke replies, probabilities and back count") from error
                apply_mode = "immediate"

        save = getattr(self.controller, "save_plugin_config", None)
        if not callable(save):
            raise TaskError("host_options_save_unavailable", "restart with Dashboard configuration support")
        save(candidate)
        if group == "voice":
            refresh = getattr(self.controller, "refresh_settings", None)
            if callable(refresh):
                refresh()
            settings_error = getattr(self.controller, "settings_error", None)
            if settings_error is not None:
                raise TaskError(settings_error.code, str(settings_error))
        else:
            if group == "poke":
                self.controller.poke_policy = policy
            else:
                refresh = getattr(self.controller, "refresh_settings", None)
                if callable(refresh):
                    refresh()
                settings_error = getattr(self.controller, "settings_error", None)
                if settings_error is not None:
                    raise TaskError(settings_error.code, str(settings_error))
                settings = self.controller.settings
                policy = settings.forward_intake if hasattr(settings, "forward_intake") else settings.common.forward_intake
                for name in ("session", "last_session"):
                    session = getattr(self.controller, name, None)
                    bridge = getattr(session, "bridge", None)
                    if bridge is not None:
                        bridge.forward_intake = policy
        return {"saved": True, "apply_mode": apply_mode, **self._view()}

    def _configure_balance(self, changes):
        allowed = {"url", "api_key", "user_id", "value_divisor", "clear_configuration"}
        if set(changes) - allowed:
            raise TaskError("host_options_request_invalid", "only the service balance fields can be changed here")
        clear = changes.get("clear_configuration", False)
        if type(clear) is not bool:
            raise TaskError("host_options_invalid", "the service balance clear option must be boolean")
        candidate = deepcopy(dict(self.controller.config))
        if clear:
            if set(changes) != {"clear_configuration"}:
                raise TaskError("host_options_request_invalid", "clear the service balance source separately")
            candidate.pop("service_balance", None)
            try:
                balance_port = configured_balance_port(candidate)
            except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                balance_port = None
        else:
            url = changes.get("url")
            api_key = changes.get("api_key", "")
            if (type(url) is not str or len(url) > 2048 or type(api_key) is not str
                    or len(api_key) > 4096 or any(ord(char) < 32 for char in api_key)):
                raise TaskError("host_options_invalid", "check the service balance URL and token")
            prior = self._config_group(candidate, "service_balance")
            saved_key = api_key if api_key else prior.get("api_key", "")
            user_id = changes.get("user_id", prior.get("user_id", ""))
            divisor = changes.get("value_divisor", prior.get("value_divisor", NEWAPI_DEFAULT_DIVISOR))
            if (type(user_id) is not str or len(user_id) > 19
                    or user_id and not user_id.isdecimal()
                    or type(divisor) is not str or len(divisor) > 64 or not divisor.strip()):
                raise TaskError("host_options_invalid", "check the NewAPI user ID and quota divisor")
            candidate["service_balance"] = {"url": url.strip(), "api_key": saved_key,
                "user_id": user_id, "value_divisor": divisor.strip()}
            try:
                balance_port = configured_balance_port(candidate)
            except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as error:
                raise TaskError("host_options_invalid", "check the HTTPS URL and NewAPI dashboard token") from error

        save = getattr(self.controller, "save_plugin_config", None)
        if not callable(save):
            raise TaskError("host_options_save_unavailable", "restart with Dashboard configuration support")
        save(candidate)
        session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
        application = getattr(session, "application", None)
        schedule = getattr(application, "contact_schedule", None)
        balance_events = getattr(schedule, "balance_events", None)
        if balance_events is not None:
            update_port = getattr(balance_events, "update_port", None)
            if callable(update_port):
                update_port(balance_port)
            else:
                balance_events.port = balance_port
        return {"saved": True, "apply_mode": "immediate", **self._view()}

    def _balance_test_port(self, data):
        token = self._view()["edit_tokens"]["balance"]
        if not hmac.compare_digest(data["edit_token"], token):
            raise TaskError("host_options_conflict", "settings changed in another editor; reload before testing")
        changes = data["changes"]
        if changes.get("use_current") is True:
            if set(changes) != {"use_current"}:
                raise TaskError("host_options_request_invalid", "test the current source separately")
            port = configured_balance_port(self.controller.config)
            if port is None:
                raise TaskError("host_options_invalid", "there is no configured balance source to test")
            return port

        allowed = {"url", "api_key", "user_id", "value_divisor"}
        if set(changes) - allowed:
            raise TaskError("host_options_request_invalid", "only balance source fields can be tested")
        candidate = deepcopy(dict(self.controller.config))
        url, api_key = changes.get("url"), changes.get("api_key", "")
        if (type(url) is not str or len(url) > 2048 or type(api_key) is not str
                or len(api_key) > 4096 or any(ord(char) < 32 for char in api_key)):
            raise TaskError("host_options_invalid", "check the service balance URL and token")
        prior = self._config_group(candidate, "service_balance")
        saved_key = api_key if api_key else prior.get("api_key", "")
        user_id = changes.get("user_id", prior.get("user_id", ""))
        divisor = changes.get("value_divisor", prior.get("value_divisor", NEWAPI_DEFAULT_DIVISOR))
        if (type(user_id) is not str or len(user_id) > 19
                or user_id and not user_id.isdecimal()
                or type(divisor) is not str or len(divisor) > 64 or not divisor.strip()):
            raise TaskError("host_options_invalid", "check the NewAPI user ID and quota divisor")
        candidate["service_balance"] = {"url": url.strip(), "api_key": saved_key,
            "user_id": user_id, "value_divisor": divisor.strip()}
        try:
            return configured_balance_port(candidate)
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as error:
            raise TaskError("host_options_invalid", "check the HTTPS URL and NewAPI profile:read token") from error

    async def balance_test(self):
        from .web_api import outcome_response

        try:
            raw = await request.body()
            if len(raw) > 8192:
                raise ValueError("request is too large")
            data = _document(raw)
            async with self.controller._control_lock:
                self._operator()
                port = self._balance_test_port(data)
            try:
                observation = await asyncio.wait_for(port.fetch(), timeout=12)
            except asyncio.TimeoutError as error:
                raise TaskError("balance_test_timeout", "NewAPI 查询超时，请核对地址和服务状态。") from error
            except Exception as error:
                raise TaskError("balance_test_failed", "NewAPI 未返回有效余额；请检查令牌权限、用户 ID 和地址。") from error
            return outcome_response(Outcome.success({
                "tested": True, "available": True, "provider": port.provider,
                "provider_label": port.provider_label, "currency_label": port.currency_label,
                "amount": display_amount(observation.balance),
                "observed_at": observation.observed_at.isoformat(),
            }))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("host_options_request_invalid", "check the balance test request"))
        except OSError:
            return outcome_response(Outcome.failed("balance_test_failed", "NewAPI could not be reached."))

    @staticmethod
    def _validate_host_settings(candidate):
        from .multi_settings import MultiHostSettings
        from .settings import HostSettings

        multi = candidate.get("multi_host", {})
        if isinstance(multi, dict) and multi.get("enabled", False):
            MultiHostSettings.parse(candidate)
        else:
            HostSettings.parse(candidate)

    async def status(self):
        return await self._run("status")

    async def voice(self):
        return await self._run("voice")

    async def poke(self):
        return await self._run("poke")

    async def forward_intake(self):
        return await self._run("forward_intake")

    async def balance(self):
        return await self._run("balance")

    async def test_balance(self):
        return await self.balance_test()

    async def balance_status(self):
        return await self._run("balance_status")

    async def weather_status(self):
        return await self._run("weather_status")

    async def weather(self):
        return await self._run("weather")
