"""Dashboard routes for Screen's per-private-window consent."""

import json

from astrbot.api.web import request

from companion_v2.capabilities.screen_access import ACTION, PROVIDER, ScreenAccess
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome

from .capability_role_selection import (
    set_role_selection,
    validate_role_selection_command,
)
from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/screen-access"


class ScreenAccessWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self, prefix=PREFIX):
        from .screen_observation_api import ScreenObservationWebAPI
        return [(prefix, self.status, ["GET"], "Read scoped native Screen access"),
                (prefix + "/configure", self.configure, ["POST"], "Set scoped native Screen consent"),
                (prefix + "/role-selection", self.role_selection, ["POST"],
                 "Enable or disable native Screen for one Bot role"),
                *ScreenObservationWebAPI(self.controller).routes(prefix)]

    async def _run(self, command=None):
        from .web_api import outcome_response
        try:
            if request.query:
                raise TaskError("screen_access_request_invalid", "target identity overrides are not accepted")
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            controller = self.controller
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
                registry = controller.native_extensions
                entries = tuple(item for item in registry.declarations()
                    if item.descriptor.tool.action == ACTION) if registry else ()
                if len(entries) != 1 or registry.current_declaration(entries[0].host_tool_id) != entries[0]:
                    raise TaskError("screen_provider_unavailable", "one active native Screen provider is required")
                descriptor = entries[0].descriptor
                if descriptor.binding.provider != PROVIDER:
                    raise TaskError("screen_provider_changed", "the native Screen provider changed")
                at = controller.now()
                operator = dashboard_operator(controller.runtime, username, at=at)
                service = ScreenAccess(session.application, session.profile.target, descriptor)
                if command is None:
                    result = service.status(operator, at)
                elif "expected_revision" in command:
                    validate_role_selection_command(command, error_code="screen_access_request_invalid")
                    selection = set_role_selection(
                        controller, operator, "companion.screen.native", session.profile.target, command, at)
                    result = {"replayed": selection.replayed,
                              "status": service.status(operator, at)}
                else:
                    result = service.configure(operator, command, at)
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("screen_access_request_invalid", "invalid Screen access request"))

    async def status(self):
        return await self._run()

    async def role_selection(self):
        from .web_api import outcome_response
        try:
            return await self._run(await self._command_body())
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed(
                "screen_access_request_invalid", "invalid Screen role selection command"))

    async def configure(self):
        from .web_api import outcome_response
        try:
            return await self._run(await self._command_body())
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed("screen_access_request_invalid", "invalid Screen access command"))

    async def _command_body(self):
        raw = await request.body()
        if len(raw) > 4096:
            raise ValueError("request is too large")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result
        command = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if type(command) is not dict:
            raise ValueError("command must be an object")
        return command


__all__ = ["PREFIX", "ScreenAccessWebAPI"]
