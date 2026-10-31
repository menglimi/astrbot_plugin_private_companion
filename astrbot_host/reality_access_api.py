"""Bind native Reality capability consent to host-resolved private targets and roles."""

import json

from astrbot.api.web import request
from companion_v2.capabilities.reality_access import ACTION, RealitySnapshotAccess
from companion_v2.capabilities.reality_camera import ACTION as CAMERA_ACTION, RealityCameraAccess
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome

from .capability_role_selection import (
    set_role_selection,
    validate_role_selection_command,
)
from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/reality-access"

CAPABILITY_PATHS = {
    ACTION: "",
    CAMERA_ACTION: "/camera",
    "reality.device.list": "/device-list",
    "reality.device.status": "/device-status",
    "reality.device.control": "/device-control",
    "reality.device.action": "/device-action",
    "reality.operation.lookup": "/operation-lookup",
    "reality.operation.cancel": "/operation-cancel",
}


class RealityAccessWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        routes = []
        for action, suffix in CAPABILITY_PATHS.items():
            status, configure = self._handlers(action)
            route = PREFIX + suffix
            label = action.removeprefix("reality.").replace(".", " ")
            routes.extend(((route, status, ["GET"], f"Read scoped Reality {label} access"),
                           (route + "/configure", configure, ["POST"], f"Set scoped Reality {label} consent")))
        routes.append((PREFIX + "/role-selection", self.role_selection, ["POST"],
                       "Enable or disable Reality for one Bot role"))
        return routes

    def _handlers(self, action):
        async def status():
            return await self._run(action=action)

        async def configure():
            return await self._configure(action)

        return status, configure

    async def _run(self, command=None, *, action=ACTION):
        from .web_api import outcome_response
        try:
            if request.query:
                raise TaskError("reality_request_invalid", "target identity overrides are not accepted")
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
                                if item.descriptor.tool.action == action) if registry else ()
                if len(entries) != 1 or registry.current_declaration(entries[0].host_tool_id) != entries[0]:
                    raise TaskError("reality_provider_unavailable", "one active native Reality provider is required")
                descriptor = entries[0].descriptor
                if descriptor.binding.provider != "reality.native":
                    raise TaskError("reality_provider_changed", "the native Reality provider changed")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                access_type = RealityCameraAccess if action == CAMERA_ACTION else RealitySnapshotAccess
                access = (access_type(session.application, session.profile.target, descriptor.package)
                          if action == CAMERA_ACTION else
                          access_type(session.application, session.profile.target, descriptor.package, capability=action))
                at = controller.now()
                if command is None:
                    result = access.status(operator, at)
                elif "expected_revision" in command:
                    validate_role_selection_command(command, error_code="reality_request_invalid")
                    selection = set_role_selection(
                        controller, operator, "reality", session.profile.target, command, at)
                    result = {"replayed": selection.replayed,
                              "status": access.status(operator, at)}
                else:
                    result = access.configure(operator, command, at)
                if command is not None:
                    await registry.reconcile_service("reality")
                result["runtime"] = registry.status("reality")
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("reality_request_invalid", "invalid Reality access request"))

    async def status(self):
        return await self._run()

    async def camera_status(self):
        return await self._run(action=CAMERA_ACTION)

    async def configure(self):
        return await self._configure(ACTION)

    async def configure_camera(self):
        return await self._configure(CAMERA_ACTION)

    async def role_selection(self):
        from .web_api import outcome_response
        try:
            return await self._run(await self._command_body(), action=ACTION)
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed(
                "reality_request_invalid", "invalid Reality role selection command"))

    async def _configure(self, action):
        from .web_api import outcome_response
        try:
            return await self._run(await self._command_body(), action=action)
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed("reality_request_invalid", "invalid Reality access command"))

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
