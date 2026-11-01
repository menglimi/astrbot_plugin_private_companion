"""Dashboard-only reply preferences for one configured group, role and native session."""

import json

from astrbot.api.web import request
from companion_v2.configuration.contract import contract_of, layers_of, provenance_of
from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.conversation.participation import CONFIG_GROUP as PARTICIPATION_GROUP
from companion_v2.conversation.participation import CONFIG_OWNER as PARTICIPATION_OWNER
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.execution.models import TaskContext, TaskError, digest
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, RoleOwnerRef, ScopeKind, ScopeRef, VersionRef

from .group_settings import GroupTarget, group_session_matches
from .management_identity import dashboard_operator
from .web_api import outcome_response


INTERJECTION_FIELDS = (
    "enable_group_interjection",
    "group_interject_min_interval_minutes",
    "group_interject_max_daily",
    "enable_group_interjection_feedback",
    "group_interject_feedback_window_seconds",
)

# A configured group that has never bound a role, or that has no original V2
# conversation, is an expected empty state rather than a read failure.
# Installation, authorization and identity failures are not empty groups, so
# only these codes degrade into an empty page.
DEGRADED_STYLE_REASONS = ("role_binding_missing", "expression_group_context_required")


class GroupReplyWebAPI:
    def __init__(self, controller, target_id, role_id):
        self.controller, self.target_id, self.role_id = controller, target_id, role_id

    def routes(self, prefix):
        return [(prefix, self.status, ["GET"], "Read this group's reply preferences"),
                (prefix + "/preview", self.preview, ["POST"], "Preview this group's reply without model access"),
                (prefix + "/configure", self.configure, ["POST"], "Save this group's reply preferences"),
                (prefix + "/expression", self.expression, ["GET"], "Read this group's expression-learning bounds"),
                (prefix + "/expression/configure", self.expression_configure, ["POST"],
                 "Save this group's expression-learning bounds"),
                (prefix + "/participation", self.participation, ["GET"],
                 "Read this group's active interjection settings"),
                (prefix + "/participation/configure", self.participation_configure, ["POST"],
                 "Save this group's active interjection settings"),
                (prefix + "/style", self.style, ["GET"], "Review this group's learned expression signals"),
                (prefix + "/style/samples", self.style_samples, ["GET"], "Review this group's writing samples"),
                (prefix + "/style/revise", self.style_revise, ["POST"], "Edit a group expression rule"),
                (prefix + "/style/samples/command", self.style_sample_command, ["POST"], "Decide a group writing sample"),
                (prefix + "/style/learn", self.style_learn, ["POST"], "Learn from eligible group samples"),
                (prefix + "/style/export", self.style_export, ["POST"], "Export this group's adopted rules"),
                (prefix + "/style/import/preview", self.style_import_preview, ["POST"], "Preview group rule import"),
                (prefix + "/style/import/apply", self.style_import_apply, ["POST"], "Import group rules for review"),
                (prefix + "/style/command", self.style_command, ["POST"],
                 "Decide a group expression signal")]

    @staticmethod
    def _session(group_id, *, allowed=()):
        query = request.query
        if set(query.keys()) - {"session_id", *allowed} or any(
                len(query.getlist(key)) != 1 for key in query.keys()):
            raise ValueError("unexpected group reply query")
        session_id = query.get("session_id", group_id)
        if not group_session_matches(session_id, group_id):
            raise TaskError("group_reply_session_invalid", "select this group's shared or per-member native session")
        return session_id

    @staticmethod
    async def _body(action):
        raw = await request.body()
        if len(raw) > (300000 if action == "preview" else 8192):
            raise ValueError("group reply request is too large")

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
        optional = ({"image_count"} if action == "preview" else
                    {"reset_to_inherit"} if action in ("configure", "participation_configure") else set())
        if type(data) is not dict or not expected <= set(data) or set(data) - expected - optional:
            raise ValueError("unexpected or missing group reply fields")
        return data

    async def _run(self, action, data=None):
        async with self.controller._control_lock:
            try:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = request.username
                if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                selected = controller.settings.target(self.target_id)
                if not isinstance(selected, GroupTarget) or not any(
                        p.bot_id == selected.bot_id and p.role_id == self.role_id for p in controller.settings.personas):
                    raise TaskError("group_reply_target_required", "the configured group/role is unavailable")
                session_id = self._session(selected.group_id)
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                bridge = session.bridge.groups
                account = bridge.accounts[(selected.platform_id, selected.account_id)]
                conversation_id, role, _ = await bridge._role(account, session_id)
                if role.subject_id != self.role_id or role.installation_id != controller.runtime.installation_id:
                    raise TaskError("group_reply_role_changed", "the native group session selected another role")
                scope = ScopeRef(controller.runtime.installation_id, selected.platform_id, ScopeKind.GROUP,
                                 selected.group_id, selected.bot_id, selected.account_id, conversation_id, session_id)
                if bridge.target_for_scope(scope) != selected:
                    raise TaskError("group_reply_target_required", "the native group target changed")
                target = ConfigTarget(scope.installation_id, ConfigLayer.WINDOW, role, scope)
                manager = ReplyManagement(session.application, target=target)
                if action == "status":
                    result = manager.status(operator)
                elif action == "preview":
                    result = manager.preview(operator, data["settings"], data["text"], data.get("image_count", 0))
                elif action == "expression":
                    result = manager.expression_status(operator)
                elif action == "expression_configure":
                    result = manager.expression_configure(operator, data)
                elif action == "participation":
                    result = self._participation_status(session.application, operator, target, controller.now())
                    if result.is_success:
                        result.value.update(target_id=self.target_id, role_id=self.role_id, session_id=session_id,
                                            scope_kind="group")
                elif action == "participation_configure":
                    return outcome_response(self._participation_configure(
                        session.application, operator, target, data, controller.now(),
                        target_id=self.target_id, role_id=self.role_id, session_id=session_id))
                else:
                    result = manager.configure(operator, data)
                if result.is_success and action != "preview":
                    status = result.value if action in ("status", "expression", "participation") else result.value["status"]
                    status.update(target_id=self.target_id, role_id=self.role_id, session_id=session_id,
                                  scope_kind="group")
                return outcome_response(result)
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except ConfigurationError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
                return outcome_response(Outcome.failed("group_reply_request_invalid", "invalid group reply settings request"))

    @staticmethod
    def _participation_status(application, operator, target, observed_at):
        read = application.configuration.read(operator, target=target, group_id=PARTICIPATION_GROUP,
                                              observed_at=observed_at)
        if read.failure is not None:
            return Outcome.failed(read.failure.code, read.failure.reason)
        snapshot = read.unwrap()
        prefix = PARTICIPATION_GROUP + "."
        fields = contract_of(application.configuration.registry, PARTICIPATION_GROUP)
        selected_fields = {}
        settings = {}
        for name in INTERJECTION_FIELDS:
            entry = dict(fields[name])
            entry.update(write_layer=ConfigLayer.WINDOW.value, editable_layers=[ConfigLayer.WINDOW.value])
            selected_fields[name] = entry
            settings[name] = snapshot.fields[prefix + name].value
        provenance = provenance_of(snapshot, PARTICIPATION_GROUP)
        return Outcome.success({
            "settings": settings,
            "edit_token": snapshot.edit_token,
            "revision": snapshot.revision,
            "configuration_token": snapshot.resolution_token,
            "group_id": PARTICIPATION_GROUP,
            "scope": ConfigLayer.WINDOW.value,
            "apply_mode": "immediate",
            "layers": layers_of(target),
            "provenance": {name: provenance[name] for name in INTERJECTION_FIELDS},
            "fields": selected_fields,
        })

    @classmethod
    def _participation_configure(cls, application, operator, target, data, observed_at,
                                 *, target_id, role_id, session_id):
        changes = data["changes"]
        resets = data.get("reset_to_inherit", [])
        if (type(data["operation_key"]) is not str or not data["operation_key"].strip()
                or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                or len(data["edit_token"]) != 64 or type(changes) is not dict
                or (not changes and not resets)
                or type(resets) is not list or any(type(name) is not str for name in resets)
                or any(type(name) is not str for name in changes)
                or set(changes).union(resets) - set(INTERJECTION_FIELDS)
                or set(changes).intersection(resets) or len(set(resets)) != len(resets)):
            return Outcome.failed("group_participation_configuration_invalid",
                                  "provide valid interjection fields for this group")
        operation_id = "group-participation-config:" + digest(
            [operator.subject, target.key, data["operation_key"]])
        result = application.configuration.patch(
            operator, target=target, group_id=PARTICIPATION_GROUP,
            operation=OperationRef(operation_id, PARTICIPATION_OWNER, target.authorization_scope,
                                   VersionRef(operation_id, 1), operation_id),
            expected_edit_token=data["edit_token"],
            changes={PARTICIPATION_GROUP + "." + name: value for name, value in changes.items()},
            reset_to_inherit=tuple(PARTICIPATION_GROUP + "." + name for name in resets),
            observed_at=observed_at)
        if result.failure is not None:
            return Outcome.failed(result.failure.code, result.failure.reason)
        receipt = result.unwrap()
        application.notify()
        payload = {"result": {"operation_id": receipt.operation_id, "replayed": receipt.replayed}}
        status = cls._participation_status(application, operator, target, observed_at)
        if status.is_success:
            status.value.update(target_id=target_id, role_id=role_id, session_id=session_id, scope_kind="group")
            payload["status"] = status.unwrap()
        return Outcome.success(payload)

    async def status(self):
        return await self._run("status")

    async def preview(self):
        try:
            data = await self._body("preview")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("group_reply_request_invalid", "invalid UTF-8 group reply preview request"))
        return await self._run("preview", data)

    async def configure(self):
        try:
            data = await self._body("configure")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("group_reply_request_invalid", "invalid UTF-8 group reply settings request"))
        return await self._run("configure", data)

    async def expression(self):
        return await self._run("expression")

    async def expression_configure(self):
        try:
            data = await self._body("expression_configure")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("group_reply_request_invalid", "invalid UTF-8 expression settings request"))
        return await self._run("expression_configure", data)

    async def participation(self):
        return await self._run("participation")

    async def participation_configure(self):
        try:
            data = await self._body("participation_configure")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("group_reply_request_invalid",
                                                   "invalid UTF-8 group participation settings request"))
        return await self._run("participation_configure", data)

    @staticmethod
    def _empty_style_page(reason, *, samples=False):
        """A group that has no readable expression scope is an empty page.

        The page shows one source per configured group, so failing the whole
        request turns an expected "this group has not produced a V2 turn yet"
        state into a page-level alert. Row-level unreadability is already
        expressed with ``unavailable_reason``; the group-level empty state uses
        the same field. The samples reader additionally aggregates one
        ``runtime`` block per source, so that page keeps the zeroed shape of a
        readable source instead of leaving the block undefined. Commands still
        require a real context.
        """
        page = {"items": [], "counts": {}, "next_cursor": None, "unavailable_reason": reason}
        if samples:
            # Stable defaults for a source whose session cannot be read yet.
            # The field names align with the runtime contract the samples page
            # reads: it aggregates every source the same way and already
            # tolerates both the old and the new shape of the hosted
            # ``status()`` payload.
            page.update(sample_review_enabled=False, runtime={
                "last_result": None, "running": False, "learning_enabled": False,
                "application_enabled": False, "minimum_samples": 0, "eligible_samples": 0,
                "new_messages": None, "required_new_messages": None,
                "group_batches_today": 0, "group_daily_batch_limit": 0})
        return page

    def _degraded_style_page(self, controller, scope, reason, *, samples, role_id):
        """Empty page for an expected "no readable scope" state.

        Only ``list`` reports the legacy-scope diagnostic; the samples page has
        no equivalent column.
        """
        page = self._empty_style_page(reason, samples=samples)
        if not samples:
            page["legacy_orphan_count"] = self._legacy_expression_count(controller, scope, role_id)
        return page

    @staticmethod
    def _legacy_expression_count(controller, scope, role_id):
        """Count old group rules stored in the pre-fix synthetic scope.

        Old group imports used ``conversation_id = session_id = group_id``.
        Those rows are neither listed nor injected at the runtime scope, so the
        page would silently show no rules. The count is read-only diagnostic
        data: rows are not moved here, because run/signal identities derive
        from the scope and a careless migration would duplicate evidence and
        rotation history.
        """
        legacy = ScopeRef(scope.installation_id, scope.platform, ScopeKind.GROUP,
                          scope.native_scope_id, scope.bot_id, scope.account_id,
                          scope.native_scope_id, scope.native_scope_id)
        if digest(legacy) == digest(scope):
            return 0
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role_id)
        row = controller.runtime.read_one("""SELECT COUNT(*) AS count FROM expression_style_signals
            WHERE owner_key = ? AND scope_key = ?""", (digest(owner), digest(legacy)))
        return int(row["count"]) if row is not None else 0

    def _style_context(self, controller, session_id, binding, scope):
        from companion_v2.conversation.native_turns import latest_scope
        native = latest_scope(controller.runtime, scope)
        if native is not None:
            try:
                context = TaskContext.restore(json.loads(native["context_json"]))
            except (TypeError, ValueError, KeyError):
                context = None
            if (context is not None and context.key == native["context_key"]
                    and context.scope == scope and context.role_binding == binding):
                return context
        rows = controller.runtime.read_all("""SELECT origin_key, context_json FROM conversation_batches b
            WHERE json_extract(b.context_json, '$.role_binding.scope.installation_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.platform') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.native_scope_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.bot_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.account_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.conversation_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.session_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.kind') = ?
              AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
              AND json_extract(b.context_json, '$.role_binding.revision') = ?
            ORDER BY b.rowid DESC LIMIT 100""",
            (scope.installation_id, scope.platform, scope.native_scope_id, scope.bot_id, scope.account_id,
             scope.conversation_id, session_id, scope.kind.value, binding.role.subject_id, binding.revision))
        for row in rows:
            try:
                context = TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
            if context.key == row["origin_key"] and context.scope == scope and context.role_binding == binding:
                return context
        raise TaskError("expression_group_context_required", "this group session has no original conversation yet")

    async def _style(self, *, command=False, method=None):
        try:
            method = method or ("command" if command else "list")
            query = request.query
            allowed = {"session_id"} if command else {"session_id", "state", "dimension", "limit", "after_id"}
            if method == "list_samples":
                allowed.discard("dimension")
            if set(query.keys()) - allowed or any(len(query.getlist(key)) != 1 for key in query.keys()):
                raise ValueError("unexpected or repeated style query fields")
            data = None
            if command:
                raw = await request.body()
                if len(raw) > (300000 if method in ("preview_import", "import_pack") else 100000):
                    raise ValueError("command too large")

                def unique(pairs):
                    values = {}
                    for key, value in pairs:
                        if key in values:
                            raise ValueError("duplicate field")
                        values[key] = value
                    return values

                def finite(_value):
                    raise ValueError("nonfinite value")

                data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
                fields = {
                    "command": {"signal_id", "action", "view_token", "operation_key"},
                    "sample_command": {"handle", "action", "view_token", "operation_key"},
                    "revise": {"signal_id", "view_token", "operation_key", "changes"},
                    "learn": set(), "export_pack": {"signal_ids"},
                    "preview_import": {"package_json"},
                    "import_pack": {"package_json", "preview_token", "operation_key"},
                }[method]
                if type(data) is not dict or (set(data) != fields and not (method == "export_pack" and not data)):
                    raise ValueError("unexpected style decision fields")
                if method in ("preview_import", "import_pack"):
                    package = data.pop("package_json")
                    if type(package) is not str or len(package.encode("utf-8")) > 250000:
                        raise ValueError("invalid expression pack")
                    data["raw_pack"] = json.loads(package, object_pairs_hook=unique, parse_constant=finite)
            elif not command:
                int(query.get("limit", "20"))

            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = request.username
                if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                selected = controller.settings.target(self.target_id)
                if not isinstance(selected, GroupTarget) or not any(
                        p.bot_id == selected.bot_id and p.role_id == self.role_id for p in controller.settings.personas):
                    raise TaskError("group_reply_target_required", "the configured group/role is unavailable")
                session_id = self._session(selected.group_id, allowed=allowed - {"session_id"})
                bridge = session.bridge.groups
                account = bridge.accounts[(selected.platform_id, selected.account_id)]
                conversation_id, role, _ = await bridge._role(account, session_id)
                if role.subject_id != self.role_id or role.installation_id != controller.runtime.installation_id:
                    raise TaskError("group_reply_role_changed", "the native group session selected another role")
                scope = ScopeRef(controller.runtime.installation_id, selected.platform_id, ScopeKind.GROUP,
                                 selected.group_id, selected.bot_id, selected.account_id, conversation_id, session_id)
                if bridge.target_for_scope(scope) != selected:
                    raise TaskError("group_reply_target_required", "the native group target changed")
                current = session.application.tasks.identity.resolve_role(scope, observed_at=controller.now())
                if current.failure:
                    if method in ("list", "list_samples") and current.failure.code in DEGRADED_STYLE_REASONS:
                        page = self._degraded_style_page(controller, scope, current.failure.code,
                                                         samples=method == "list_samples", role_id=self.role_id)
                        return outcome_response(Outcome.success(page))
                    if method in ("list", "list_samples"):
                        # Installation, authorization and identity failures are
                        # not an empty group and must stay visible.
                        raise TaskError(current.failure.code, current.failure.reason)
                    # Decisions still need a real conversation context.
                    raise TaskError("expression_group_context_required", "this group session has no original conversation yet")
                binding = current.unwrap()
                if binding.role != role:
                    raise TaskError("group_reply_role_changed", "the native group session selected another role")
                try:
                    context = self._style_context(controller, session_id, binding, scope)
                except TaskError as error:
                    if method in ("list", "list_samples") and error.code in DEGRADED_STYLE_REASONS:
                        page = self._degraded_style_page(controller, scope, error.code,
                                                         samples=method == "list_samples",
                                                         role_id=binding.role.subject_id)
                        return outcome_response(Outcome.success(page))
                    raise
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = ExpressionStyleManagement(session.application)
                if command:
                    result = getattr(manager, method)(operator=operator, context=context, **data)
                else:
                    try:
                        limit = int(query.get("limit", "20"))
                    except (TypeError, ValueError):
                        raise ValueError("invalid page size") from None
                    arguments = {"state": query.get("state") or ("all" if method == "list_samples" else "pending"),
                                 "limit": limit, "after_id": query.get("after_id") or None}
                    if method == "list":
                        arguments["dimension"] = query.get("dimension") or None
                    result = getattr(manager, method)(operator=operator, context=context, **arguments)
                    if method == "list" and result.is_success:
                        page = result.unwrap()
                        page["legacy_orphan_count"] = self._legacy_expression_count(
                            controller, scope, binding.role.subject_id)
                        result = Outcome.success(page)
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("expression_query_invalid", "invalid group style request"))

    async def style(self):
        return await self._style()

    async def style_command(self):
        return await self._style(command=True)

    async def style_samples(self):
        return await self._style(method="list_samples")

    async def style_sample_command(self):
        return await self._style(command=True, method="sample_command")

    async def style_learn(self):
        return await self._style(command=True, method="learn")

    async def style_revise(self):
        return await self._style(command=True, method="revise")

    async def style_export(self):
        return await self._style(command=True, method="export_pack")

    async def style_import_preview(self):
        return await self._style(command=True, method="preview_import")

    async def style_import_apply(self):
        return await self._style(command=True, method="import_pack")
