"""Authenticated Dashboard routes for private writing and role routines."""

import asyncio
import json

from astrbot.api.web import request

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.life.management import RoleLifeManagement
from companion_v2.storage.runtime import StorageOpenError

from .management_identity import dashboard_operator

PREFIX = "/astrbot_plugin_private_companion/life"


class LifeWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read private journals and role routines"),
                (PREFIX + "/command", self.command, ["POST"], "Manage private journals and role routines")]

    def _snapshot(self, username=None):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "the plugin has stopped")
        if username is None:
            username = request.username
        if type(username) is not str or not username.strip():
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        session = controller.session or controller.last_session
        runtime = controller.runtime
        if session is None or runtime is None:
            raise TaskError("staging_not_initialized", "load an initialized V2 profile first")
        return (
            session,
            runtime,
            controller.now(),
            getattr(controller.settings, "profile_id", None),
            getattr(controller, "target_id", None),
            username,
        )

    @staticmethod
    def _build_manager(session, runtime, username, observed_at):
        operator = dashboard_operator(runtime, username, at=observed_at)
        return RoleLifeManagement(session.application, session.profile.target), operator

    def manager(self, username=None):
        session, runtime, observed_at, _profile_id, _target_id, resolved_username = self._snapshot(username)
        return self._build_manager(session, runtime, resolved_username, observed_at)

    async def status(self):
        from .web_api import outcome_response
        try:
            query = request.query
            if set(query.keys()) - {"before", "after_activity", "after_reading", "after_sleep"} or any(len(query.getlist(k)) != 1 for k in query.keys()):
                raise ValueError("invalid query")
            username = request.username
            before = query.get("before") or None
            after_activity = query.get("after_activity") or None
            after_reading = query.get("after_reading") or None
            after_sleep = query.get("after_sleep") or None
            async with self.controller._control_lock:
                session, runtime, observed_at, profile_id, target_id, resolved_username = self._snapshot(username)

            def load():
                manager, operator = self._build_manager(session, runtime, resolved_username, observed_at)
                return manager.status(operator, before=before, after_activity=after_activity,
                                      after_reading=after_reading, after_sleep=after_sleep)

            result = await asyncio.to_thread(load)
            result["profile_id"] = profile_id
            if target_id is not None:
                result["target_id"] = target_id
            return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("life_storage_unavailable", "the life store is not available"))
        except (ValueError, TypeError):
            return outcome_response(Outcome.failed("life_query_invalid", "invalid archive query"))

    async def command(self):
        from .web_api import outcome_response
        try:
            if request.query:
                raise ValueError("commands do not accept query fields")
            username = request.username
            raw = await request.body()
            if len(raw) > 1200000:
                raise ValueError("body too large")
            def unique(pairs):
                data = {}
                for key, value in pairs:
                    if key in data:
                        raise ValueError("duplicate field")
                    data[key] = value
                return data
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            common = {"action", "operation_key", "view_token", "expected_revision"}
            fields = {
                "configure": {"changes", "edit_token"}, "add_text": {"title", "text"}, "sleep": {"minutes"},
                "read": {"activity_id"}, "wake": {"activity_id"}, "cancel": {"activity_id"}, "withdraw": {"activity_id"},
                "remove": {"activity_id"},
                "diary": {"local_date", "retry_of"}, "dream": {"activity_id", "retry_of"},
                "edit": {"kind", "local_date", "body", "afterglow", "timezone"},
                "delete": {"kind", "local_date", "timezone"},
                "restore": {"kind", "local_date", "body", "afterglow", "recall", "timezone"},
                "derive": {"kind", "local_date", "timezone"},
            }
            if type(data) is not dict or data.get("action") not in fields or set(data) - common - fields[data["action"]]:
                raise ValueError("unknown command fields")
            async with self.controller._control_lock:
                session, runtime, observed_at, _profile_id, _target_id, resolved_username = self._snapshot(username)

            def prepare():
                manager, operator = self._build_manager(session, runtime, resolved_username, observed_at)
                manager.authorize(operator)
                return manager, operator

            manager, operator = await asyncio.to_thread(prepare)
            # Model work uses its own task lifecycle; leave stop/withdraw routes responsive.
            return outcome_response(await manager.command(operator, data))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("life_storage_unavailable", "the life store is not available"))
        except (ValueError, TypeError, RecursionError):
            return outcome_response(Outcome.failed("life_command_invalid", "invalid UTF-8 life command"))
