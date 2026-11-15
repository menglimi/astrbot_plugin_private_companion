"""Dashboard access to role-authored Content works and candidate versions.

The Content extension owns its database.  This endpoint only exposes the
legacy catalogue plus scoped previews and version adoption to authenticated
Dashboard operators.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from astrbot.api.web import request

from companion_v2.content.catalogue import content_database, read_works
from companion_v2.content.models import ContentError
from companion_v2.conversation.content.access import ContentAccess
from companion_v2.conversation.content.library import catalogue, manuscript
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.life.management import RoleLifeManagement

from .management_identity import dashboard_operator

PREFIX = "/astrbot_plugin_private_companion/works"


class WorksWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self, prefix=PREFIX):
        return [(prefix, self.list_works, ["GET"], "List this role's authored works"),
                (prefix + "/library", self.library, ["GET"], "List works in the selected private relationship"),
                (prefix + "/preview", self.preview, ["GET"], "Read a fixed work version"),
                (prefix + "/adopt", self.adopt, ["POST"], "Adopt a candidate at its original revision")]

    def _selected(self, *, fields=(), target_values=None):
        query = request.query
        allowed = {"target_id", "role_id", *fields}
        if (set(query.keys()) - allowed
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            raise TaskError("works_request_invalid", "unexpected works query")
        selection = query if target_values is None else target_values
        target_id = selection.get("target_id", "")
        role_id = selection.get("role_id", "")
        if not all(isinstance(value, str) and 0 < len(value) <= 512 for value in (target_id, role_id)):
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

    def _private_context(self, *, write=False, fields=(), target_values=None):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        target, role_id = self._selected(fields=fields, target_values=target_values)
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        link = controller.settings.window_link_for_target(target.target_id)
        target_id = link.primary_target_id if link is not None else target.target_id
        context = controller.authority.context(target_id, role_id)
        app = session.application
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        RoleLifeManagement(app, context).authorize(operator)
        if write:
            app.require_running()
        return app, context, operator

    async def library(self):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                app, context, _ = self._private_context()
                path = content_database(app.runtime.data_root)
                works = await asyncio.to_thread(catalogue, path, ContentAccess(app), context) if path.is_file() else []
                return outcome_response(Outcome.success({"works": works}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed("works_unavailable", "the works catalogue is unavailable"))

    async def preview(self):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                app, context, _ = self._private_context(fields=("work_id", "version_id"))
                work_id, version_id = request.query["work_id"], request.query["version_id"]
                result = await asyncio.to_thread(manuscript, content_database(app.runtime.data_root),
                                                 ContentAccess(app), context, work_id, version_id)
                return outcome_response(Outcome.success({"chapters": result["chapters"]}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed("works_unavailable", "the work version is unavailable"))

    async def adopt(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 8192:
                raise ValueError("request too large")
            data = json.loads(raw.decode("utf-8"))
            if (request.query or type(data) is not dict
                    or set(data) != {"target_id", "role_id", "work_id", "version_id", "expected_revision"}
                    or type(data["expected_revision"]) is not int or data["expected_revision"] < 1
                    or not all(isinstance(data[key], str) and 0 < len(data[key]) <= 512
                               for key in ("work_id", "version_id"))):
                raise ValueError("unexpected adoption request")
            target = {key: data.pop(key) for key in ("target_id", "role_id")}
            async with self.controller._control_lock:
                app, context, operator = self._private_context(write=True, target_values=target)
                if app.scheduled_authoring is None:
                    raise TaskError("content_provider_unavailable", "the native Content service is not running")
                manager = RoleLifeManagement(app, context)
                path = content_database(app.runtime.data_root)
                work = next((item for item in await asyncio.to_thread(catalogue, path, ContentAccess(app), context)
                             if item["work_id"] == data["work_id"]), None)
                already_adopted = (work is not None and work["head_version"] == data["version_id"]
                                   and work["revision"] == data["expected_revision"] + 1)
                if already_adopted:
                    return outcome_response(Outcome.success({"work_id": data["work_id"],
                                                             "version_id": data["version_id"]}))
                if (work is None or work["revision"] != data["expected_revision"]
                        or work["candidate_version"] != data["version_id"]):
                    raise TaskError("stale_candidate", "this candidate is no longer current")
                if work["candidate_append_only"]:
                    raise TaskError("content_append_only", "this legacy candidate contains only an appended fragment")
                key = "dashboard-adopt:" + digest([operator.subject, context.key, data])
                receipt = await asyncio.to_thread(app.scheduled_authoring.operation, context,
                    "adopt", key, {"operator": operator.subject.subject_id},
                    lambda: manager.authorize(operator), administrator=True, **data)
                app.notify()
                return outcome_response(Outcome.success({"work_id": receipt.work_id, "version_id": receipt.version_id}))
        except (TaskError, ContentError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed("works_request_invalid", "invalid adoption request"))

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
