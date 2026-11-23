"""Dashboard route for this plugin's bounded in-process log buffer."""

from __future__ import annotations

from datetime import datetime, timezone

from astrbot.api.web import request

from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.outcome import Outcome

from .log_buffer import snapshot
from .management_identity import current_request_username, require_controller_management_operator


PREFIX = "/astrbot_plugin_private_companion"


class LogWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return ((PREFIX + "/logs", self.read_logs, ["GET"],
                 "Read the current plugin process's bounded recent log records"),)

    async def read_logs(self):
        from .web_api import outcome_response

        try:
            username = current_request_username(request)
            lock = getattr(self.controller, "_control_lock", None)
            if lock is not None:
                async with lock:
                    require_controller_management_operator(self.controller, username)
            else:
                require_controller_management_operator(self.controller, username)
            allowed = {"level", "q", "limit", "before"}
            if set(request.query.keys()) - allowed:
                raise TaskError("logs_query_invalid", "unsupported log query fields")
            if any(len(request.query.getlist(key)) != 1 for key in request.query.keys()):
                raise TaskError("logs_query_invalid", "each log query field must appear once")
            level = request.query.get("level", "")
            query = request.query.get("q", "")
            try:
                limit = int(request.query.get("limit", "200"))
                before = int(request.query.get("before", "0"))
            except (TypeError, ValueError):
                raise TaskError("logs_query_invalid", "limit and before must be integers")
            data = snapshot(level=level, query=query, limit=limit, before=before)
            now = getattr(self.controller, "now", None)
            at = now() if callable(now) else datetime.now(timezone.utc)
            data["generated_at"] = stamp(at)
            return outcome_response(Outcome.success(data))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, RuntimeError):
            return outcome_response(Outcome.failed("logs_unavailable", "the recent log records could not be read"))


__all__ = ["LogWebAPI", "PREFIX"]
