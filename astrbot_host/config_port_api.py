"""Export/import of portable configuration values for the selected role.

The document contains settings only (the same short field names the pages edit).
It never contains role profiles, relationship records, memories, journals,
images or any other stored data.  Import applies values through the regular
configuration editors, so every field keeps its layer, validation and edit-token
rules; one failing section does not discard the others.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from astrbot.api.web import request

from companion_v2.configuration.portability import EXCLUDED_SECTIONS, SCHEMA, changes_for, portable
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome

from .configuration_api import SECTIONS
from .management_identity import dashboard_operator
from .settings import HostControlError

PREFIX = "/astrbot_plugin_private_companion/config/port"
SELECTION_FIELDS = {"target_id", "role_id", "bot_id", "window_selected"}


class ConfigPortWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX + "/export", self.export, ["GET"], "Export portable configuration values"),
                (PREFIX + "/import", self.import_values, ["POST"], "Import portable configuration values")]

    def _prepare(self, selection):
        controller = self.controller
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        if (any(type(selection.get(name)) is not str or not selection[name].strip()
                for name in ("target_id", "role_id", "bot_id"))
                or type(selection.get("window_selected")) is not bool):
            raise TaskError("configuration_port_selection_required", "select a Bot account and role")
        try:
            target = controller.settings.target(selection["target_id"])
        except HostControlError:
            raise TaskError("configuration_target_unavailable", "the selected target is unavailable") from None
        if target.bot_id != selection["bot_id"] or not any(
                item.bot_id == target.bot_id and item.role_id == selection["role_id"]
                for item in controller.settings.personas):
            raise TaskError("configuration_target_changed", "the selected Bot or role binding changed")
        from .group_settings import GroupTarget
        if isinstance(target, GroupTarget):
            raise TaskError("configuration_port_group_unavailable",
                            "configuration import and export require a resolved private role context")
        from .multi_api import TargetControllerView
        selected = TargetControllerView(controller, target.target_id, selection["role_id"]).session
        if (selected.profile.target.scope.bot_id != selection["bot_id"]
                or selected.profile.target.role_binding.role.subject_id != selection["role_id"]):
            raise TaskError("configuration_target_changed", "the selected Bot or role context changed")
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        return selected.application, selected.profile.target, operator

    @staticmethod
    def _requires_window(status, settings):
        if status.get("scope") not in ("installation", "role"):
            return True
        fields = status.get("fields") or {}
        return any((fields.get(name) or {}).get("write_layer", status["scope"])
                   not in ("installation", "role") for name in settings)

    def _export(self, application, context, operator, *, window_selected):
        sections, unavailable = {}, {}
        for name, editor_class in SECTIONS.items():
            if not portable(name, SECTIONS):
                continue
            try:
                result = editor_class(application, context).status(operator)
            except Exception:
                unavailable[name] = "configuration_port_unavailable"
                continue
            if not result.is_success:
                unavailable[name] = result.failure.code
                continue
            current = result.unwrap()
            settings = current.get("settings") or {}
            if not window_selected and self._requires_window(current, settings):
                unavailable[name] = "configuration_port_window_required"
                continue
            if settings:
                sections[name] = {"settings": settings}
        return {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).isoformat(),
                "excluded": sorted(EXCLUDED_SECTIONS), "sections": sections,
                "unavailable": unavailable}

    def _import(self, application, context, operator, document, *, window_selected):
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise TaskError("configuration_port_invalid", "not a portable configuration document")
        sections = document.get("sections")
        if not isinstance(sections, dict) or len(sections) > len(SECTIONS):
            raise TaskError("configuration_port_invalid", "portable configuration sections are invalid")
        applied, failed = {}, {}
        for name, payload in sections.items():
            if not portable(name, SECTIONS):
                failed[str(name)] = "configuration_port_unsupported"
                continue
            settings = payload.get("settings") if isinstance(payload, dict) else None
            try:
                editor = SECTIONS[name](application, context)
                status = editor.status(operator)
            except Exception:
                failed[name] = "configuration_port_unavailable"
                continue
            if not status.is_success:
                failed[name] = status.failure.code
                continue
            current = status.unwrap()
            changes = changes_for(settings, set(current.get("settings") or {}))
            if not changes:
                continue
            if not window_selected and self._requires_window(current, changes):
                failed[name] = "configuration_port_window_required"
                continue
            try:
                result = editor.configure(operator, {
                    "operation_key": "config-import-%s-%s" % (name.replace(".", "-"), uuid4().hex),
                    "edit_token": current["edit_token"], "changes": changes})
            except Exception:
                failed[name] = "configuration_port_unavailable"
                continue
            if result.is_success:
                applied[name] = sorted(changes)
            else:
                failed[name] = result.failure.code
        return {"applied": applied, "failed": failed}

    async def export(self):
        from .web_api import outcome_response
        try:
            query = request.query
            names = set(query.keys())
            if (names != SELECTION_FIELDS or any(len(query.getlist(name)) != 1 for name in names)
                    or query.get("window_selected") not in ("true", "false")):
                raise TaskError("configuration_port_selection_required", "select a Bot account and role")
            selection = {name: query[name] for name in SELECTION_FIELDS}
            selection["window_selected"] = query["window_selected"] == "true"
            async with self.controller._control_lock:
                application, context, operator = self._prepare(selection)
            document = await asyncio.to_thread(self._export, application, context, operator,
                                              window_selected=selection["window_selected"])
            return outcome_response(Outcome.success(document))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (OSError, ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed(
                "configuration_port_unavailable", "portable configuration is unavailable"))

    async def import_values(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 262144 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if not isinstance(body, dict) or set(body) != {"document", *SELECTION_FIELDS}:
                raise ValueError()
            document = body["document"]
            async with self.controller._control_lock:
                application, context, operator = self._prepare(body)
            result = await asyncio.to_thread(self._import, application, context, operator, document,
                                            window_selected=body["window_selected"])
            return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed(
                "configuration_port_invalid", "invalid portable configuration request"))
