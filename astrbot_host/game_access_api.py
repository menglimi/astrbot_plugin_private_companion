"""Dashboard game permissions resolved from the selected private target."""

import json

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.games.native.access import NativeGameAccess

from .management_identity import dashboard_operator

PREFIX = "/astrbot_plugin_private_companion/game-access"


class GameAccessWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read current private game permissions"),
                (PREFIX + "/configure", self.configure, ["POST"], "Set current private game permissions")]

    async def _run(self, command=None):
        from .web_api import outcome_response
        try:
            if request.query:
                raise TaskError("game_access_request_invalid", "target overrides are not accepted")
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            controller = self.controller
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load a V2 profile first")
                registry = controller.native_extensions
                package = registry.current_package("game") if registry else None
                if package is None:
                    raise TaskError("game_provider_unavailable", "the native game provider is unavailable")
                at = controller.now()
                operator = dashboard_operator(controller.runtime, username, at=at)
                access = NativeGameAccess(session.application, session.profile.target, package)
                result = access.status(operator, at) if command is None else access.configure(operator, command, at)
                if command is not None:
                    await registry.reconcile_service("game")
                    result["status"] = access.status(operator, at)
                result["runtime"] = registry.status("game")
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("game_access_request_invalid", "invalid game permission request"))

    async def status(self):
        return await self._run()

    async def configure(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("bounded request required")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate field")
                    result[key] = value
                return result

            command = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(command) is not dict:
                raise ValueError("an object is required")
            return await self._run(command)
        except (ValueError, TypeError, UnicodeError):
            return outcome_response(Outcome.failed("game_access_request_invalid", "invalid game permission command"))
