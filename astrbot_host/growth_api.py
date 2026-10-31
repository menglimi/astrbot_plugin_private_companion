"""Authenticated commands for the pinned profile's role growth records."""

import json
from datetime import datetime

from astrbot.api.web import request

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeRef, SourceRef, VersionRef
from companion_v2.role_work.growth.management import RoleGrowthManagement
from companion_v2.role_work.growth.access_management import RoleGrowthAccessManagement
from companion_v2.role_work.growth.progress_management import GrowthProgressManagement
from companion_v2.role_work.journal.self_review_management import SelfReviewManagement

from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/growth"


def _source(value):
    fields = {"source_id", "owner", "scope", "version", "issued_at", "expires_at"}
    if type(value) is not dict or set(value) != fields or type(value["scope"]) is not dict:
        raise ValueError("use a complete original source reference")
    if type(value["version"]) is not dict or set(value["version"]) != {"lineage_id", "revision"}:
        raise ValueError("use the original source version")
    scope = value["scope"]
    scope_fields = {"installation_id", "platform", "kind", "native_scope_id", "bot_id",
                    "account_id", "conversation_id", "session_id"}
    if set(scope) != scope_fields or any(type(part) is not str for part in scope.values()):
        raise ValueError("use the complete original private scope")
    if (any(type(value[name]) is not str for name in ("source_id", "owner", "issued_at"))
            or type(value["version"]["lineage_id"]) is not str
            or type(value["version"]["revision"]) is not int
            or value["expires_at"] is not None and type(value["expires_at"]) is not str):
        raise ValueError("invalid source identity")
    return SourceRef(value["source_id"], value["owner"], ScopeRef.restore(value["scope"]),
        VersionRef(**value["version"]), datetime.fromisoformat(value["issued_at"]),
        datetime.fromisoformat(value["expires_at"]) if value["expires_at"] is not None else None)


class GrowthWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.list, ["GET"], "Read role growth in the pinned private profile"),
                (PREFIX + "/evidence", self.evidence, ["GET"], "Read current growth source evidence"),
                (PREFIX + "/command", self.command, ["POST"], "Explicitly manage role growth"),
                (PREFIX + "/progress", self.progress, ["GET"], "Read pending growth progress reviews"),
                (PREFIX + "/progress/command", self.progress_command, ["POST"], "Decide a growth progress review"),
                (PREFIX + "/self-review", self.self_review, ["GET"], "Read role self-review candidates"),
                (PREFIX + "/self-review/command", self.self_review_command, ["POST"], "Decide a role self-review candidate")]

    async def _run(self, arguments, *, method):
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
                self_review_request = method.startswith("self_review_")
                manager = RoleGrowthManagement(session.application.role_growth)
                access = RoleGrowthAccessManagement(session.application)
                if method == "configure":
                    manager = access
                elif method.startswith("progress_"):
                    manager = GrowthProgressManagement(session.application)
                    method = method.removeprefix("progress_")
                elif self_review_request:
                    manager = SelfReviewManagement(session.application)
                    method = method.removeprefix("self_review_")
                result = getattr(manager, method)(operator=operator, context=session.profile.target, **arguments)
                if result.is_success and method == "list" and not self_review_request:
                    settings = access.read(operator=operator, context=session.profile.target)
                    if not settings.is_success:
                        return outcome_response(settings)
                    result.unwrap()["access"] = settings.unwrap()
                if result.is_success and method == "list" and self_review_request:
                    service = getattr(session.application, "self_review", None)
                    if service is not None:
                        try:
                            result.unwrap()["status"] = service.status(session.profile.target)
                        except (TaskError, ValueError, TypeError):
                            pass
                response = outcome_response(result)
                if result.is_success and method == "list" and not self_review_request:
                    response["data"].update(operator_name=username,
                        profile_id=getattr(controller, "target_id", None) or controller.settings.profile_id)
                return response
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))

    async def list(self):
        from .web_api import outcome_response
        query = request.query
        if set(query.keys()) - {"state", "kind", "limit", "after_id"} or any(
                len(query.getlist(key)) != 1 for key in query.keys()):
            return outcome_response(Outcome.failed("growth_query_invalid", "unknown or repeated query fields"))
        try:
            limit = int(query.get("limit", "20"))
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("growth_query_invalid", "invalid page size"))
        return await self._run(dict(state=query.get("state") or None, kind=query.get("kind") or None,
                                    limit=limit, after_id=query.get("after_id") or None), method="list")

    async def evidence(self):
        from .web_api import outcome_response
        query = request.query
        if set(query.keys()) != {"growth_id"} or len(query.getlist("growth_id")) != 1:
            return outcome_response(Outcome.failed("growth_query_invalid", "select one original growth record"))
        return await self._run({"growth_id": query["growth_id"]}, method="evidence")

    async def command(self):
        from .web_api import outcome_response
        try:
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
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(data) is not dict or type(data.get("action")) is not str:
                raise ValueError("explicit command required")
            action = data["action"]
            common = {"action", "operation_key", "view_token"}
            if action == "configure":
                required = allowed = {"action", "operation_key", "edit_token", "changes"}
            elif action == "propose":
                required = common | {"kind", "title", "sources"}
                allowed = required | {"description", "progress_note"}
            elif action == "revise":
                required = common | {"growth_id", "expected_revision", "title", "description", "progress_note"}
                allowed = required | {"sources"}
            elif action in RoleGrowthManagement.ALLOWED_STATES:
                required = allowed = common | {"growth_id", "expected_revision"}
            else:
                raise ValueError("unknown command")
            if not required.issubset(data) or set(data) - allowed:
                raise ValueError("unexpected or missing fields")
            if "sources" in data:
                if type(data["sources"]) is not list or not 1 <= len(data["sources"]) <= 8:
                    raise ValueError("provide one to eight original source references")
                data["sources"] = tuple(_source(value) for value in data["sources"])
            if action in ("propose", "configure"):
                data.pop("action")
            method = "create" if action == "propose" else "configure" if action == "configure" else "command"
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("growth_command_invalid", "invalid UTF-8 growth command"))
        return await self._run(data, method=method)

    async def progress(self):
        from .web_api import outcome_response
        query = request.query
        if set(query.keys()) - {"limit", "after_id"} or any(len(query.getlist(key)) != 1 for key in query.keys()):
            return outcome_response(Outcome.failed("growth_progress_query_invalid", "unknown or repeated query fields"))
        try:
            limit = int(query.get("limit", "20"))
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("growth_progress_query_invalid", "invalid page size"))
        return await self._run(dict(limit=limit, after_id=query.get("after_id") or None), method="progress_list")

    async def progress_command(self):
        from .web_api import outcome_response
        try:
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
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            fields = {"review_id", "action", "expected_revision", "view_token", "operation_key"}
            if type(data) is not dict or set(data) != fields:
                raise ValueError("unexpected review fields")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("growth_progress_command_invalid", "invalid UTF-8 review command"))
        return await self._run(data, method="progress_command")

    async def self_review(self):
        from .web_api import outcome_response
        query = request.query
        if set(query.keys()) - {"state", "limit", "after_id"} or any(
                len(query.getlist(key)) != 1 for key in query.keys()):
            return outcome_response(Outcome.failed("self_review_query_invalid", "unknown or repeated query fields"))
        try:
            limit = int(query.get("limit", "20"))
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("self_review_query_invalid", "invalid page size"))
        return await self._run(dict(state=query.get("state") or "pending", limit=limit,
                                    after_id=query.get("after_id") or None), method="self_review_list")

    async def self_review_command(self):
        from .web_api import outcome_response
        try:
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
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            fields = {"item_id", "action", "view_token", "operation_key"}
            if type(data) is not dict or set(data) != fields:
                raise ValueError("unexpected review fields")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("self_review_command_invalid", "invalid UTF-8 review command"))
        return await self._run(data, method="self_review_command")
