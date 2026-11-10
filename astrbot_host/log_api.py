"""Dashboard route for this plugin's bounded in-process log buffer."""

from __future__ import annotations

from astrbot.api.web import request

from companion_v2.execution.models import TaskError
from companion_v2.execution.models import stamp
from companion_v2.foundation.outcome import Outcome

from .log_buffer import snapshot


PREFIX = "/astrbot_plugin_private_companion"


class LogWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return ((PREFIX + "/logs", self.read_logs, ["GET"],
                 "Read the current plugin process's bounded recent log records"),)

    async def read_logs(self):
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")
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
        data["generated_at"] = stamp(self.controller.now())
        return data


__all__ = ["LogWebAPI", "PREFIX"]
