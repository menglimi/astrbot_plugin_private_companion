"""Authorized reply editor and bounded diagnostics; never invokes a model or sender."""

import json

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.configuration.contract import contract_of, layers_of, provenance_of
from companion_v2.configuration.models import ConfigurationError
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.delivery.components import build_component_plan
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.role_work.life.management import RoleLifeManagement
from companion_v2.model_gateway.response_diagnostics import checked_response_diagnostics

from .media.models import MAX_INPUT_IMAGES
from .media.understanding import SCOPE_PERMISSION_FIELDS
from .reply_settings import (DIAGNOSTIC_DEFAULTS, GROUP, OWNER, ROLE_SHARED_FIELDS,
    GROUP_WAKEUP_GROUP_FIELDS, group_wakeup_settings, target_for, validate, voice_policy)
from .outbound_plan import component_order
from .segmentation import minimum_part_gap_seconds, part_gap_seconds
from .segmentation import DEFAULT_PROMPT, LLM_SEGMENT_MARKER
from .reply_preparation import prepare_reply
from .text_processing import text_policy


class ReplyManagement:
    def __init__(self, app, context=None, *, target=None, layer=ConfigLayer.WINDOW):
        if (context is None) == (target is None):
            raise ValueError("select one private context or group configuration target")
        if target is not None and (type(target) is not ConfigTarget or target.layer is not ConfigLayer.WINDOW
                                   or target.scope is None or target.scope.kind not in
                                   (ScopeKind.PRIVATE, ScopeKind.GROUP)):
            raise ValueError("object reply configuration requires an exact private or group window")
        if target is None and layer not in (ConfigLayer.ROLE, ConfigLayer.WINDOW):
            raise ValueError("reply configuration supports role defaults or one private window")
        self.app, self.context = app, context
        self.target = target if target is not None else target_for(context, layer)
        self.scope = self.target.scope if self.target.scope is not None else context.scope
        self.access = RoleLifeManagement(app, context) if context is not None else ObjectAccessPolicy(app.runtime)

    def _snapshot(self, operator):
        if self.context is not None:
            self.access.authorize(operator)
        else:
            require(self.access.scope_access(operator, scope=self.target.scope,
                action="configuration.read", observed_at=self.app.now(), core_only=True))
        return require(self.app.configuration.read(operator, target=self.target, group_id=GROUP,
                                                   observed_at=self.app.now()))

    @outcome
    def status(self, operator):
        with self.app.runtime.read_snapshot():
            snapshot = self._snapshot(operator)
            settings = {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}
            recent_limit = settings.get("reply_diagnostic_limit", DIAGNOSTIC_DEFAULTS["reply_diagnostic_limit"])
            participation = {}
            if self.scope.kind is ScopeKind.GROUP:
                limit = group_wakeup_settings(settings)["group_wakeup_log_limit"]
                participation = {"participation_recent": self.app.tools.group_participation.diagnostics(
                    self.scope, self.target.role, limit), "participation_limit": limit,
                    "timing_apply_mode": "next_accepted_input"}
            batch_select = """SELECT b.batch_id, b.state, b.created_at, b.context_json, b.contributions_json,
                b.generation_task_id, b.part_count, f.state AS flow_state, f.reason,
                (SELECT COUNT(*) FROM conversation_inputs i WHERE i.batch_id = b.batch_id AND i.withdrawn = 0)
                    AS current_inputs
                FROM conversation_batches b LEFT JOIN conversation_flows f ON f.batch_id = b.batch_id WHERE """
            if self.context is not None:
                batches = self.app.runtime.read_all(batch_select + """b.origin_key = ?
                    ORDER BY b.created_at DESC, b.rowid DESC LIMIT ?""", (self.context.key, recent_limit))
            elif self.scope.kind is ScopeKind.GROUP:
                scope = self.scope
                batches = self.app.runtime.read_all(batch_select + """
                    json_extract(b.context_json, '$.role_binding.scope.installation_id') = ?
                    AND json_extract(b.context_json, '$.role_binding.scope.platform') = ?
                    AND json_extract(b.context_json, '$.role_binding.scope.native_scope_id') = ?
                    AND json_extract(b.context_json, '$.role_binding.scope.bot_id') = ?
                    AND json_extract(b.context_json, '$.role_binding.scope.account_id') = ?
                    AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
                    ORDER BY b.created_at DESC, b.rowid DESC LIMIT ?""",
                    (scope.installation_id, scope.platform, scope.native_scope_id, scope.bot_id,
                     scope.account_id, self.target.role.subject_id, recent_limit))
            else:
                batches = ()
            recent = []
            for batch in batches:
                contributions = json.loads(batch["contributions_json"])
                parts = self.app.runtime.read_all("""SELECT p.sequence, p.state, LENGTH(p.text) AS chars,
                    d.candidate_reason, d.render_state, d.component_order_json, d.text_hidden,
                    LENGTH(v.speech) AS speech_chars
                    FROM conversation_parts p
                    LEFT JOIN conversation_voice_diagnostics d ON d.part_id = p.part_id
                    LEFT JOIN conversation_voice_parts v ON v.part_id = p.part_id
                    WHERE p.batch_id = ? ORDER BY p.sequence""", (batch["batch_id"],))
                calls = self.app.runtime.read_all("""SELECT c.position, c.state, c.actual_tokens, c.latency_ms,
                        f.code AS failure_code, d.summary_json AS response_summary_json
                    FROM model_calls c LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                    LEFT JOIN model_call_diagnostics d ON d.call_id = c.call_id
                    WHERE c.task_id = ? ORDER BY c.position LIMIT 18""", (batch["generation_task_id"],))
                request = self.app.runtime.read_one("SELECT plan_json FROM model_requests WHERE task_id = ?",
                                                    (batch["generation_task_id"],))
                plan = json.loads(request["plan_json"]) if request else {}
                rounds = self.app.runtime.read_all(
                    "SELECT round_no, state, tool_calls_json FROM model_rounds WHERE task_id = ? ORDER BY round_no",
                    (batch["generation_task_id"],))
                round_details = [{"round_no": item["round_no"], "state": item["state"],
                    "tool_actions": [call.get("action", "") for call in json.loads(item["tool_calls_json"])]}
                    for item in rounds]
                tools = self.app.runtime.read_all("""SELECT action, state, effect FROM conversation_tool_calls
                    WHERE batch_id = ? ORDER BY sequence LIMIT 32""", (batch["batch_id"],))
                materials = contributions.get("materials", []) if isinstance(contributions, dict) else []
                material_summary = [{"owner": item.get("owner", ""), "purpose": item.get("purpose", ""),
                                     "state": item.get("state", "")}
                                    for item in materials if isinstance(item, dict)]
                group_snapshot = contributions.get("group_context", {}) if isinstance(contributions, dict) else {}
                response_type = ("no_reply" if not batch["part_count"] else
                    "image_only" if parts and all(item["chars"] == 0 for item in parts) else "text_or_mixed")
                recent.append({"batch_id": batch["batch_id"], "created_at": batch["created_at"],
                    "state": batch["flow_state"] or batch["state"], "reason": batch["reason"],
                    "turn_type": plan.get("profile", {}).get("kind", "conversation.reply"),
                    "current_inputs": batch["current_inputs"],
                    "parts": [{**dict(p),
                        "component_order": json.loads(p["component_order_json"])
                            if p["component_order_json"] is not None else None}
                        for p in parts], "calls": [{key: call[key] for key in
                            ("position", "state", "actual_tokens", "latency_ms", "failure_code")} | {
                            "response_diagnostics": checked_response_diagnostics(call["response_summary_json"])}
                            for call in calls],
                    "response_type": response_type,
                    "injected_materials": material_summary,
                    "group_history_items": len(group_snapshot.get("references", []))
                        if isinstance(group_snapshot, dict) else 0,
                    "offered_tools": plan.get("tool_actions", []),
                    "filtered_tools": plan.get("tool_filters", []),
                    "rounds": round_details,
                    "tool_calls": [dict(call) for call in tools]})
            from .native_reply_diagnostics import recent as native_recent
            recent.extend(native_recent(self.app.runtime, self.scope, self.target.role, limit=recent_limit))
            recent.sort(key=lambda item: (item["created_at"], item["batch_id"]), reverse=True)
            recent = recent[:recent_limit]
            fields = contract_of(self.app.configuration.registry, GROUP)
            for name in fields:
                if name in ROLE_SHARED_FIELDS:
                    fields[name]["editable_layers"] = ["role", "window"]
                    fields[name]["write_layer"] = self.target.layer.value
                elif (self.target.layer is ConfigLayer.WINDOW and self.scope.kind is ScopeKind.GROUP
                      and name in GROUP_WAKEUP_GROUP_FIELDS):
                    fields[name]["editable_layers"] = ["window"]
                    fields[name]["write_layer"] = "window"
                else:
                    fields[name]["editable_layers"] = []
                    fields[name]["write_layer"] = None
            return {"settings": settings, "edit_token": snapshot.edit_token, "revision": snapshot.revision,
                "configuration_token": snapshot.resolution_token, "apply_mode": "next_sealed_reply",
                "host_state": self.app.state, "recent": recent, "recent_limit": recent_limit, "marker": LLM_SEGMENT_MARKER,
                "default_prompt": DEFAULT_PROMPT,
                # The same three extras every config section publishes, so the shared
                # workbench can draw its layer rail without a page-local copy of
                # GROUP limits. Role defaults are inherited into the exact private
                # or group window that owns this reply editor.
                "scope": self.target.layer.value, "layers": layers_of(self.target),
                "provenance": provenance_of(snapshot, GROUP),
                "fields": fields, **participation}

    @outcome
    def preview(self, operator, settings, text, image_count=0):
        self._snapshot(operator)
        try:
            policy = validate(settings)
        except ConfigurationError as error:
            raise TaskError(error.code, str(error)) from error
        try:
            if (type(text) is not str or not text.strip() or len(text) > 65536
                    or type(image_count) is not int or not 0 <= image_count <= MAX_INPUT_IMAGES):
                raise ValueError("invalid preview input")
            voice = voice_policy(settings) if "voice_enabled" in settings else None
            prepared = prepare_reply(text, policy, text_policy(settings), voice)
            parts = prepared.parts
            speech = tuple(item[0] for item in prepared.speech(voice)) if voice else ("",) * len(parts)
        except (TypeError, ValueError) as error:
            raise TaskError("reply_preview_invalid", "provide nonblank bounded reply text") from error
        components = []
        image_position = settings.get("image_position", "after_text")
        order = component_order(settings)
        for index, (part, spoken) in enumerate(zip(parts, speech)):
            images = tuple(range(image_count)) if index == len(parts) - 1 else ()
            fallback = build_component_plan(part, images, image_position=image_position, order=order)
            # This sentinel models successful synthesis without calling a provider.
            success = build_component_plan(part, images,
                {"type": "record", "data": {"file": "offline-preview"}} if spoken else None,
                position=voice.position if voice else "after_media", image_position=image_position,
                voice_only=voice is not None and voice.delivery_mode == "voice_only", speech=spoken,
                order=order)
            components.append({"speech": spoken, "success_order": success.kinds,
                               "fallback_order": fallback.kinds, "text_hidden": success.text_hidden})
        part_spacing = [round(part_gap_seconds(settings, part, f"preview:{index}") * 1000)
                        for index, part in enumerate(parts[:-1])]
        minimum_spacing = sum(round(minimum_part_gap_seconds(settings, part) * 1000)
                              for part in parts[:-1])
        return {"parts": parts, "count": len(parts), "characters": sum(map(len, parts)),
                "minimum_spacing_ms": minimum_spacing, "planned_spacing_ms": sum(part_spacing),
                "part_spacing_ms": part_spacing,
                "image_position": image_position, "image_count": image_count, "components": components,
                "component_order": list(order),
                "quote": {"enabled": bool(settings.get("reply_quote_enabled", False)),
                          "group_enabled": bool(settings.get("reply_quote_group_enabled", True)),
                          "first_part_only": bool(settings.get("reply_quote_first_part_only", True)),
                          "skip_chars": settings.get("reply_quote_skip_chars", 0),
                          "mention_speaker": bool(settings.get("reply_mention_speaker_enabled", False))},
                "voice": {"enabled": voice is not None, "provider_id": voice.provider_for_language() if voice else "",
                          "instruction": voice.instruction if voice else ""},
                "instruction": "\n".join(value for value in (policy.instruction, text_policy(settings).instruction) if value),
                "mode": "offline_preview"}

    @outcome
    def expression_status(self, operator):
        """Read only the local learning switch for this relationship or group."""
        from companion_v2.expression.configuration import (DEFAULTS, GROUP as EXPRESSION_GROUP,
            GROUP_DEFAULTS, PRIVATE_DEFAULTS, local_configuration, target_for as expression_target_for)
        with self.app.runtime.read_snapshot():
            self._snapshot(operator)
            target, definitions, group, _ = local_configuration(self.context, target=self.target)
            snapshot = require(self.app.configuration.read(operator, target=target,
                group_id=group, observed_at=self.app.now()))
            installation_id = self.scope.installation_id
            shared = require(self.app.configuration.read(operator,
                target=expression_target_for(self.context, installation_id=installation_id),
                group_id=EXPRESSION_GROUP,
                observed_at=self.app.now()))
            fields = contract_of(self.app.configuration.registry, group)
        defaults = PRIVATE_DEFAULTS if group.endswith(".private") else GROUP_DEFAULTS
        settings = {key.removeprefix(group + "."): value for key, value in snapshot.values.items()}
        resolved_defaults = dict(defaults)
        for name in ("private_learning_enabled", "group_learning_enabled"):
            local_key = group + "." + name
            role_key = EXPRESSION_GROUP + "." + name
            if local_key in snapshot.fields and not snapshot.fields[local_key].explicit:
                settings[name] = shared.values.get(role_key, DEFAULTS[name])
                resolved_defaults[name] = settings[name]
        learning = {}
        service = getattr(self.app, "expression_style", None)
        if service is not None and self.context is not None:
            try:
                learning = service.status(self.context)
            except (TaskError, ConfigurationError):
                learning = {}
        return {"settings": settings,
                "defaults": resolved_defaults, "edit_token": snapshot.edit_token,
                "revision": snapshot.revision, "configuration_token": snapshot.resolution_token,
                "apply_mode": "immediate", "group_id": group,
                "scope": target.layer.value, "layers": layers_of(target),
                "provenance": provenance_of(snapshot, group), "fields": fields,
                "learning": learning}

    @outcome
    def expression_configure(self, operator, data):
        from companion_v2.expression.configuration import local_configuration
        if (type(data) is not dict or set(data) != {"operation_key", "edit_token", "changes"}
                or type(data["operation_key"]) is not str or not data["operation_key"].strip()
                or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                or len(data["edit_token"]) != 64 or type(data["changes"]) is not dict or not data["changes"]
                or any(type(key) is not str for key in data["changes"])):
            raise TaskError("expression_configuration_invalid", "provide explicit learning bound changes")
        with self.app.runtime.transaction():
            self._snapshot(operator)
            target, definitions, group, owner = local_configuration(self.context, target=self.target)
            if self.context is None:
                require(self.access.scope_access(operator, scope=self.target.scope,
                    action="configuration.write", observed_at=self.app.now(), core_only=True))
            op_id = "expression-config:" + digest([operator.subject,
                target.key, data["operation_key"]])
            receipt = require(self.app.configuration.patch(operator, target=target,
                group_id=group,
                operation=OperationRef(op_id, owner.owner_id, target.authorization_scope,
                                       VersionRef(op_id, 1), op_id),
                expected_edit_token=data["edit_token"],
                changes={group + "." + name: value for name, value in data["changes"].items()},
                observed_at=self.app.now()))
            return {"replayed": receipt.replayed, "operation_id": op_id,
                    "status": require(self.expression_status(operator))}

    @outcome
    def configure(self, operator, data):
        if (type(data) is not dict or not {"operation_key", "edit_token", "changes"} <= set(data)
                or set(data) - {"operation_key", "edit_token", "changes", "reset_to_inherit"}):
            raise TaskError("reply_configuration_invalid", "provide explicit setting changes")
        key, token, changes = data["operation_key"], data["edit_token"], data["changes"]
        resets = data.get("reset_to_inherit", [])
        if type(key) is not str or not key.strip() or len(key) > 128 or type(token) is not str or len(token) != 64:
            raise TaskError("reply_configuration_invalid", "provide a stable operation key and edit token")
        if (type(changes) is not dict or type(resets) is not list
                or any(type(name) is not str for name in resets)
                or not changes and not resets or set(changes).intersection(resets)
                or len(set(resets)) != len(resets)):
            raise TaskError("reply_configuration_invalid", "provide explicit setting changes")
        with self.app.runtime.transaction():
            self._snapshot(operator)
            op_id = "reply-settings:" + digest([operator.subject, self.target.key, key])
            if self.context is None:
                require(self.access.scope_access(operator, scope=self.scope,
                    action="configuration.write", observed_at=self.app.now(), core_only=True))
            receipt = require(self.app.configuration.patch(operator, target=self.target, group_id=GROUP,
                operation=OperationRef(op_id, OWNER, self.target.authorization_scope, VersionRef(op_id, 1), op_id),
                expected_edit_token=token, changes={GROUP + "." + name: value for name, value in changes.items()},
                reset_to_inherit=tuple(GROUP + "." + name for name in resets),
                observed_at=self.app.now()))
            return {"replayed": receipt.replayed, "operation_id": op_id, "status": require(self.status(operator))}
