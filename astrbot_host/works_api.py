"""Read-only catalogue endpoint for role-authored Content works.

The Content extension owns its database.  This endpoint only exposes the
bounded, path-free catalogue (title, type, revision, creation time) to the
dashboard; it never returns manuscript text and never writes.
"""

from __future__ import annotations

import asyncio

from astrbot.api.web import request

from companion_v2.content.catalogue import content_database, read_works
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome

PREFIX = "/astrbot_plugin_private_companion/works"


class WorksWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self, prefix=PREFIX):
        return [(prefix, self.list_works, ["GET"], "List this role's authored works")]

    def _selected(self):
        query = request.query
        allowed = {"target_id", "role_id"}
        if (set(query.keys()) - allowed
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            raise TaskError("works_request_invalid", "unexpected works query")
        target_id = query.get("target_id", "")
        role_id = query.get("role_id", "")
        if not target_id or not role_id:
            raise TaskError("works_request_invalid", "select a target and role")
        settings = getattr(self.controller, "settings", None)
        if settings is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        try:
            target = settings.target(target_id)
        except KeyError:
            raise TaskError("works_target_unavailable", "the selected target is unavailable") from None
        if target is None:
            raise TaskError("works_target_unavailable", "the selected target is unavailable")
        from .group_settings import GroupTarget
        if isinstance(target, GroupTarget):
            raise TaskError("works_scope_unavailable", "Content works belong to a private role")
        if not any(persona.bot_id == target.bot_id and persona.role_id == role_id
                   for persona in settings.personas):
            raise TaskError("works_target_unavailable", "the role is not assigned to this bot")
        return target, role_id

    async def list_works(self):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            async with self.controller._control_lock:
                if self.controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                runtime = self.controller.runtime
                if runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                target, role_id = self._selected()
            path = content_database(runtime.data_root)
            result = await asyncio.to_thread(read_works, path,
                installation_id=runtime.installation_id, bot_id=target.bot_id, role_id=role_id)
            return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (OSError, ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed(
                "works_unavailable", "the works catalogue is unavailable"))
