"""Authenticated state and calendar API for the host's original pinned target."""

import asyncio
import json
from datetime import timedelta

from astrbot.api.web import request
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.state.management import StateManagement
from companion_v2.agenda.planning_config import configured_weather_location, configured_weather_max_age
from companion_v2.agenda.weather_reading import WeatherReading
from companion_v2.proactive.weather_alerts import active_official_alerts
from companion_v2.role_work.state.configuration import GROUP, target_for
from .growth_api import _source
from .management_identity import dashboard_operator

PREFIX = "/astrbot_plugin_private_companion/state"


class StateWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self._weather_application = None
        self._weather_reading = None
        self._alert_cache = None

    async def _alerts(self, app, location, now):
        """Current official alerts for the role location, cached for five minutes."""
        cached = self._alert_cache
        if (cached is not None and cached[0] is app and cached[1] == location
                and timedelta(0) <= now - cached[2] < timedelta(minutes=5)):
            return cached[3]
        events = getattr(getattr(app, "contact_schedule", None), "official_alert_events", None)
        port = getattr(events, "port", None)
        if port is None:
            return []
        try:
            observation = await asyncio.wait_for(port.fetch(location), timeout=6)
            active = active_official_alerts(observation, location, now)
            alerts = [{
                "title": item.headline,
                "event_name": item.event_name,
                "severity": item.severity,
                "sender_name": item.sender_name,
                "issued_at": item.issued_at.isoformat(),
                "ends_at": item.expires_at.isoformat(),
                "description": item.description,
            } for item in active]
        except Exception:
            return []
        self._alert_cache = (app, location, now, alerts)
        return alerts

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read role state and calendar"),
                (PREFIX + "/weather", self.weather, ["GET"], "Read configured role weather"),
                (PREFIX + "/command", self.command, ["POST"], "Manage role state and calendar")]

    async def _run(self, data=None, **query):
        from .web_api import outcome_response
        controller = self.controller
        async with controller._control_lock:
            try:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the plugin has stopped")
                username = request.username
                if type(username) is not str or not username.strip():
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load an initialized V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = StateManagement(session.application, session.profile.target)
                return outcome_response(manager.command(operator, data) if data is not None else manager.status(operator, **query))
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (ValueError, TypeError, KeyError, OverflowError):
                return outcome_response(Outcome.failed("state_command_invalid", "invalid state or calendar fields"))

    async def status(self):
        from .web_api import outcome_response
        query = request.query
        try:
            if set(query.keys()) - {"after_id", "limit"} or any(len(query.getlist(k)) != 1 for k in query.keys()):
                raise ValueError("unknown or repeated query fields")
            limit = int(query.get("limit", "30"))
            if not 1 <= limit <= 100:
                raise ValueError("invalid page size")
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("state_query_invalid", "invalid state query"))
        return await self._run(after_id=query.get("after_id", ""), limit=limit)

    def _weather_context(self):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "the plugin has stopped")
        username = request.username
        if type(username) is not str or not username.strip():
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            raise TaskError("staging_not_initialized", "load an initialized V2 profile first")
        app, context = session.application, session.profile.target
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        with app.runtime.read_snapshot():
            StateManagement(app, context).life.authorize(operator)
            snapshot = require(app.configuration.read(operator, target=target_for(context),
                group_id=GROUP, observed_at=app.now()))
            settings = {name: snapshot.values[GROUP + "." + name] for name in (
                "planning_weather_enabled", "planning_weather_location", "planning_weather_max_age_minutes",
                "planning_timezone")}
        return app, context, settings

    async def weather(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            query = request.query
            if (set(query.keys()) - {"refresh"}
                    or any(len(query.getlist(key)) != 1 for key in query.keys())
                    or query.get("refresh", "0") not in ("0", "1")):
                raise ValueError("weather route has invalid query fields")
            force = query.get("refresh", "0") == "1"
            async with controller._control_lock:
                app, context, settings = self._weather_context()
                location = configured_weather_location(settings["planning_weather_location"])
                timezone = settings["planning_timezone"]
                if settings["planning_weather_enabled"] is not True:
                    return outcome_response(Outcome.success({"state":"disabled", "location":location.name if location else None,
                        "timezone":timezone, "server_time":app.now().isoformat()}))
                if location is None:
                    raise TaskError("planning_weather_location_required", "set the role location before reading current weather")
                port = app.calendar_planning.weather_port
                if self._weather_application is not app:
                    self._weather_application, self._weather_reading = app, WeatherReading()
                reading = self._weather_reading

            result = await reading.read(port, location, max_age=configured_weather_max_age(settings),
                now=app.now, force=force)
            alerts = await self._alerts(app, location, app.now())

            async with controller._control_lock:
                current_app, current_context, current_settings = self._weather_context()
                if current_app is not app or current_context != context:
                    raise TaskError("staging_context_changed", "the active role changed during the weather read")
                current_location = configured_weather_location(current_settings["planning_weather_location"])
                if (current_settings["planning_weather_enabled"] is not True or current_location != location
                        or configured_weather_max_age(current_settings) != configured_weather_max_age(settings)
                        or app.calendar_planning.weather_port is not port):
                    raise TaskError("weather_settings_changed", "weather settings changed during the read")
                return outcome_response(Outcome.success({**result, "alerts": alerts,
                    "timezone": current_settings["planning_timezone"],
                    "server_time": app.now().isoformat()}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, OverflowError):
            return outcome_response(Outcome.failed("state_query_invalid", "invalid weather request"))

    async def command(self):
        from .web_api import outcome_response
        try:
            if request.query:
                raise ValueError("command route does not accept query fields")
            raw = await request.body()
            if len(raw) > 100000:
                raise ValueError("command too large")
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("duplicate field")
                    values[key] = value
                return values
            def finite(value):
                raise ValueError("nonfinite value")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
            if type(data) is not dict or type(data.get("action")) is not str:
                raise ValueError("provide an explicit action")
            common = {"action", "operation_key", "view_token", "expected_revision"}
            action = data["action"]
            if action == "configure":
                required = allowed = {"action", "operation_key", "edit_token", "changes"}
                allowed = allowed | {"role_edit_token"}
            elif action == "configure_role":
                required = allowed = {"action", "operation_key", "role_edit_token", "changes"}
            elif action == "configure_window":
                required = allowed = {"action", "operation_key", "edit_token", "changes"}
            elif action == "state.write":
                required, allowed = common | {"data"}, common | {"data", "sources"}
            elif action == "cycle.align":
                required, allowed = common | {"data"}, common | {"data", "settings_token"}
            elif action == "maintain":
                required = allowed = common
            elif action in ("calendar.create", "calendar.revise"):
                required, allowed = common | {"id", "data"}, common | {"id", "data", "sources"}
            elif action == "calendar.presence":
                required = allowed = common | {"id", "data"}
            elif action in ("calendar.accept", "calendar.cancel"):
                required = allowed = common | {"id"}
            elif action == "calendar.exception":
                required = allowed = common | {"id", "data"}
            elif action == "calendar.remind":
                required = allowed = common | {"id", "sequence"}
            else:
                raise ValueError("unknown action")
            if not required.issubset(data) or set(data) - allowed:
                raise ValueError("unexpected or missing fields")
            if "sources" in data:
                if type(data["sources"]) is not list or len(data["sources"]) > 8:
                    raise ValueError("provide at most eight sources")
                data["sources"] = tuple(_source(value) for value in data["sources"])
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("state_command_invalid", "invalid UTF-8 state command"))
        return await self._run(data)
