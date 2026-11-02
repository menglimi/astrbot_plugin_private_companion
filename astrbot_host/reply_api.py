"""Dashboard-only reply preferences, offline preview and sanitized diagnostics."""

import json

from astrbot.api.web import request
from companion_v2.configuration.models import ConfigLayer
from companion_v2.configuration.models import ConfigurationError
from companion_v2.conversation.recall_management import RecallManagement
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.execution.models import TaskError
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.outcome import Outcome
from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/reply"


class ReplyWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read reply settings and recent diagnostics"),
                (PREFIX + "/preview", self.preview, ["POST"], "Preview reply segmentation without model access"),
                (PREFIX + "/configure", self.configure, ["POST"], "Save reply settings for subsequent turns"),
                (PREFIX + "/expression", self.expression, ["GET"], "Read the expression-learning bounds"),
                (PREFIX + "/expression/configure", self.expression_configure, ["POST"],
                 "Save the expression-learning bounds"),
                (PREFIX + "/style", self.style, ["GET"], "Review learned expression signals"),
                (PREFIX + "/style/revise", self.style_revise, ["POST"], "Edit a learned expression rule"),
                (PREFIX + "/style/samples", self.style_samples, ["GET"], "Review original expression samples"),
                (PREFIX + "/style/samples/command", self.style_sample_command, ["POST"], "Decide an expression sample"),
                (PREFIX + "/style/learn", self.style_learn, ["POST"], "Learn from this window's eligible samples"),
                (PREFIX + "/style/export", self.style_export, ["POST"], "Export adopted expression rules"),
                (PREFIX + "/style/import/preview", self.style_import_preview, ["POST"],
                 "Preview an expression rule-pack import"),
                (PREFIX + "/style/import/apply", self.style_import_apply, ["POST"],
                 "Import expression rules into the review queue"),
                (PREFIX + "/style/command", self.style_command, ["POST"],
                 "Decide a learned expression signal"),
                (PREFIX + "/recalls", self.recalls, ["GET"],
                 "Read this window's withdrawn messages within the configured bound"),
                (PREFIX + "/harassment", self.harassment, ["GET"],
                 "Read anti-harassment settings, directory and audit"),
                (PREFIX + "/harassment/settings", self.harassment_settings, ["POST"],
                 "Save anti-harassment judgment and disposal settings"),
                (PREFIX + "/harassment/block", self.harassment_block, ["POST"],
                 "Move one private user into the blacklist"),
                (PREFIX + "/harassment/unblock", self.harassment_unblock, ["POST"],
                 "Remove one private user from the blacklist"),
                (PREFIX + "/harassment/extend", self.harassment_extend, ["POST"],
                 "Change one blacklist entry's expiry"),
                (PREFIX + "/harassment/false-positive", self.harassment_false_positive, ["POST"],
                 "Release a false positive and keep its evidence in the audit"),
                (PREFIX + "/harassment/export", self.harassment_export, ["POST"],
                 "Export anti-harassment settings, directory and audit"),
                (PREFIX + "/harassment/import", self.harassment_import, ["POST"],
                 "Import anti-harassment settings and directory")]

    def _tts_catalog(self):
        from .voice import tts_provider_catalog
        return tts_provider_catalog(self.controller.context)

    async def _run(self, action):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("reply_request_invalid", "reply routes do not accept target overrides")
            data = None
            if action not in ("status", "expression"):
                raw = await request.body()
                if len(raw) > 300000:
                    raise ValueError("reply request is too large")
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate field")
                        result[key] = value
                    return result
                def finite(value):
                    raise ValueError("nonfinite value")
                data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
                expected = {"settings", "text"} if action == "preview" else {"operation_key", "edit_token", "changes"}
                optional = {"image_count"} if action == "preview" else {"reset_to_inherit"} if action == "configure" else set()
                if (type(data) is not dict or not expected <= set(data)
                        or set(data) - expected - optional):
                    raise ValueError("unexpected or missing reply fields")
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = ReplyManagement(session.application, session.profile.target, layer=ConfigLayer.ROLE)
                if action == "status":
                    result = manager.status(operator)
                elif action == "preview":
                    result = manager.preview(operator, data["settings"], data["text"], data.get("image_count", 0))
                elif action == "expression":
                    result = manager.expression_status(operator)
                elif action == "expression_configure":
                    result = manager.expression_configure(operator, data)
                else:
                    result = manager.configure(operator, data)
                if result.is_success and action in ("status", "configure"):
                    status = result.value if action == "status" else result.value["status"]
                    status["tts"] = self._tts_catalog()
                    # 附件解析器是否装好，直接随设置一起下发，理解页不用另开接口。
                    from .files.parsing import parser_availability
                    status["documents"] = parser_availability()
                    try:
                        from companion_v2.conversation.web_understanding import WebUnderstandingSettings
                        status["web"] = WebUnderstandingSettings(
                            session.application.configuration).read(session.profile.target, controller.now())
                    except Exception:
                        status["web"] = {}
                return outcome_response(result)
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("reply_request_invalid", "invalid UTF-8 reply settings request"))

    async def status(self):
        return await self._run("status")

    async def preview(self):
        return await self._run("preview")

    async def configure(self):
        return await self._run("configure")

    async def expression(self):
        return await self._run("expression")

    async def expression_configure(self):
        return await self._run("expression_configure")

    async def _style(self, arguments, method):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load an initialized V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = ExpressionStyleManagement(session.application)
                result = getattr(manager, method)(operator=operator, context=session.profile.target, **arguments)
                return outcome_response(result)
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def style(self):
        from .web_api import outcome_response
        query = request.query
        if set(query.keys()) - {"state", "dimension", "limit", "after_id"} or any(
                len(query.getlist(key)) != 1 for key in query.keys()):
            return outcome_response(Outcome.failed("expression_query_invalid", "unknown or repeated query fields"))
        try:
            limit = int(query.get("limit", "20"))
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("expression_query_invalid", "invalid page size"))
        return await self._style(dict(state=query.get("state") or "pending",
            dimension=query.get("dimension") or None, limit=limit,
            after_id=query.get("after_id") or None), "list")

    async def style_command(self):
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
            fields = {"signal_id", "action", "view_token", "operation_key"}
            if type(data) is not dict or set(data) != fields:
                raise ValueError("unexpected style decision fields")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("expression_command_invalid", "invalid UTF-8 style decision"))
        return await self._style(data, "command")

    async def style_samples(self):
        from .web_api import outcome_response
        query = request.query
        try:
            if set(query.keys()) - {"state", "limit", "after_id"} or any(
                    len(query.getlist(key)) != 1 for key in query.keys()):
                raise ValueError
            return await self._style({"state": query.get("state") or "all",
                "limit": int(query.get("limit", "24")), "after_id": query.get("after_id") or None}, "list_samples")
        except (ValueError, TypeError):
            return outcome_response(Outcome.failed("expression_query_invalid", "invalid sample query"))

    async def _sample_action(self, fields, method, *, max_bytes=8192):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > max_bytes or request.query:
                raise ValueError
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=self._unique_json_pairs,
                              parse_constant=self._reject_nonfinite_json)
            if type(data) is not dict or set(data) != fields:
                raise ValueError
            return await self._style(data, method)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("expression_command_invalid", "invalid sample action"))

    async def style_sample_command(self):
        return await self._sample_action({"handle", "action", "view_token", "operation_key"}, "sample_command")

    async def style_learn(self):
        return await self._sample_action(set(), "learn")

    async def style_revise(self):
        return await self._sample_action({"signal_id", "view_token", "operation_key", "changes"}, "revise",
                                         max_bytes=32768)

    @staticmethod
    def _unique_json_pairs(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate JSON field")
            values[key] = value
        return values

    @staticmethod
    def _reject_nonfinite_json(value):
        raise ValueError("nonfinite JSON value")

    async def _expression_pack_body(self, expected_fields):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 300000:
                raise ValueError("share pack is too large")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=self._unique_json_pairs,
                              parse_constant=self._reject_nonfinite_json)
            if type(data) is not dict or set(data) != expected_fields:
                raise ValueError("unexpected share pack fields")
            package_text = data.get("package_json")
            if type(package_text) is not str or len(package_text.encode("utf-8")) > 250000:
                raise ValueError("share pack must be UTF-8 JSON under 250 KB")
            package = json.loads(package_text, object_pairs_hook=self._unique_json_pairs,
                                 parse_constant=self._reject_nonfinite_json)
            if type(package) is not dict:
                raise ValueError("share pack must be a JSON object")
            return data, package, None
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return None, None, outcome_response(Outcome.failed(
                "expression_pack_invalid", "invalid or unsupported UTF-8 expression share pack"))

    async def style_export(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 50000:
                raise ValueError("export request too large")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=self._unique_json_pairs,
                              parse_constant=self._reject_nonfinite_json)
            if type(data) is not dict or set(data) not in (set(), {"signal_ids"}):
                raise ValueError("unexpected export fields")
            signal_ids = data.get("signal_ids")
            return await self._style({"signal_ids": signal_ids}, "export_pack")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("expression_export_invalid", "invalid expression export request"))

    async def style_import_preview(self):
        data, package, error = await self._expression_pack_body({"package_json"})
        if error is not None:
            return error
        return await self._style({"raw_pack": package}, "preview_import")

    async def style_import_apply(self):
        data, package, error = await self._expression_pack_body(
            {"package_json", "preview_token", "operation_key"})
        if error is not None:
            return error
        return await self._style({"raw_pack": package, "preview_token": data["preview_token"],
                                  "operation_key": data["operation_key"]}, "import_pack")

    async def recalls(self):
        from .web_api import outcome_response
        try:
            query = request.query
            if set(query.keys()) - {"limit"} or any(len(query.getlist(key)) != 1 for key in query.keys()):
                raise ValueError("unexpected recall query fields")
            limit = int(query.get("limit", "5"))
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                return outcome_response(RecallManagement(session.application, session.profile.target)
                                        .transcribe(operator, limit=limit))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError):
            return outcome_response(Outcome.failed("recall_request_invalid", "invalid recall query"))



    # ---- 防骚扰：判断、处置与黑名单目录 ----

    def _harassment_runtime(self):
        controller = self.controller
        if getattr(controller, "_terminated", False):
            raise TaskError("staging_host_terminated", "plugin is stopped")
        runtime = getattr(controller, "runtime", None)
        if runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        return runtime

    @staticmethod
    async def _harassment_body(fields):
        raw = await request.body()
        if len(raw) > 200000:
            raise ValueError("harassment request is too large")

        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError("duplicate field")
                values[key] = value
            return values

        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(data) is not dict or set(data) != set(fields):
            raise ValueError("unexpected or missing harassment fields")
        return data

    def _harassment_snapshot(self, runtime, policy):
        return {"settings": policy.read_settings(runtime),
                "entries": policy.entries(runtime),
                "audit": policy.audit(runtime),
                "reasons": policy.REASONS,
                "reason_group": policy.REASON_GROUP}

    async def harassment(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def harassment_settings(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"changes"})
            if type(data["changes"]) is not dict:
                raise ValueError("harassment changes must be an object")
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                policy.save_settings(runtime, data["changes"])
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_config_invalid", "防骚扰设置无效"))

    async def harassment_block(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"scope", "user_id", "hours", "note"})
            scope, user_id = data["scope"], data["user_id"]
            if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
                raise ValueError("block needs scope and user")
            hours = data["hours"] if type(data["hours"]) is int and 0 <= data["hours"] <= 8760 else 0
            note = data["note"] if type(data["note"]) is str else ""
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                policy.add_entry(runtime, scope=scope, user_id=user_id, kind="block",
                                 reasons=("manual",), evidence=note[:200], hours=hours)
                policy.record_audit(runtime, scope=scope, user_id=user_id, action="manual_block",
                                    reasons=("manual",), evidence=note[:200])
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_entry_invalid", "黑名单条目无效"))

    async def harassment_unblock(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"scope", "user_id"})
            scope, user_id = data["scope"], data["user_id"]
            if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
                raise ValueError("unblock needs scope and user")
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                policy.remove_entry(runtime, scope=scope, user_id=user_id)
                policy.record_audit(runtime, scope=scope, user_id=user_id, action="unblock",
                                    reasons=())
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_entry_invalid", "黑名单条目无效"))

    async def harassment_extend(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"scope", "user_id", "hours"})
            scope, user_id, hours = data["scope"], data["user_id"], data["hours"]
            if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
                raise ValueError("extend needs scope and user")
            if type(hours) is not int or not 0 <= hours <= 8760:
                raise ValueError("extend hours is invalid")
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                policy.extend_entry(runtime, scope=scope, user_id=user_id, hours=hours)
                policy.record_audit(runtime, scope=scope, user_id=user_id, action="extend",
                                    reasons=(), evidence=f"{hours} 小时" if hours else "永久")
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_entry_invalid", "黑名单条目无效"))

    async def harassment_false_positive(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"scope", "user_id", "note"})
            scope, user_id = data["scope"], data["user_id"]
            if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
                raise ValueError("false positive needs scope and user")
            note = data["note"] if type(data["note"]) is str else ""
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                entry = policy.entry_for(runtime, scope, user_id)
                reasons = tuple(entry.get("reasons") or ()) if entry else ()
                policy.remove_entry(runtime, scope=scope, user_id=user_id)
                policy.record_audit(runtime, scope=scope, user_id=user_id, action="false_positive",
                                    reasons=reasons, evidence=note[:200])
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_entry_invalid", "黑名单条目无效"))

    async def harassment_export(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            await self._harassment_body({"scope"})
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                return outcome_response(Outcome.success({
                    "settings": policy.read_settings(runtime),
                    "entries": policy.entries(runtime),
                    "audit": policy.audit(runtime),
                }))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_export_invalid", "导出请求无效"))

    async def harassment_import(self):
        from .web_api import outcome_response
        from companion_v2.relationships import harassment as policy
        try:
            data = await self._harassment_body({"document"})
            document = data["document"]
            if type(document) is not dict:
                raise ValueError("import document must be an object")
            settings = document.get("settings")
            entries = document.get("entries")
            if settings is not None and type(settings) is not dict:
                raise ValueError("import settings must be an object")
            if entries is not None and type(entries) is not list:
                raise ValueError("import entries must be a list")
            async with self.controller._control_lock:
                runtime = self._harassment_runtime()
                if settings:
                    policy.save_settings(runtime, {key: settings[key] for key in settings
                                                   if key in policy.DEFAULTS})
                for item in (entries or [])[:policy.MAX_ENTRIES]:
                    if type(item) is not dict or type(item.get("scope")) is not str                             or type(item.get("user_id")) is not str:
                        continue
                    policy.add_entry(runtime, scope=item["scope"], user_id=item["user_id"],
                                     kind="block" if item.get("kind") != "cooldown" else "cooldown",
                                     reasons=tuple(item.get("reasons") or ()),
                                     evidence=str(item.get("evidence") or "")[:300],
                                     strike=int(item.get("strike") or 1),
                                     hours=0 if not item.get("until") else max(
                                         1, int((item["until"] - policy.now_value()) / 3600)))
                policy.record_audit(runtime, scope="(import)", user_id="(import)", action="import",
                                    reasons=(), evidence=f"{len(entries or [])} 条")
                return outcome_response(Outcome.success(self._harassment_snapshot(runtime, policy)))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("harassment_import_invalid", "导入文件无效"))
