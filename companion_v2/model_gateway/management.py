"""Dashboard model task settings for one authorized private role context."""

from dataclasses import asdict, replace
from datetime import date
from collections import deque
import json

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, EffectiveValue
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.role_work.life.management import RoleLifeManagement
from .budget import effective_soft
from .cache_diagnostics import checked_signature, event_severity, signature_changes, signature_snapshot
from .core_observations import availability_summary, observations_for_role, request_rows
from .models import MAX_MODEL_OUTPUT_TOKENS
from .reply_efficiency import reply_efficiency_report
from .refusal import canonical_refusal_terms
from .replacement_rules import checked_rules, validate_targets
from .usage import MAIN_CHAIN_KIND, ModelUsageReport, usage_source


FIELDS = ("provider", "fallback", "prompt", "max_output_tokens", "replacement_rules", "fallback_on_refusal",
          "refusal_terms", "timeout_seconds", "replacement_scope")
OWNER = "configuration"
PROMPT_LAYERS = (
    {"key": "role_instruction", "label": "角色指令", "editable": False,
     "description": "当前角色的人格与身份设定，作为独立系统消息发送。"},
    {"key": "request_instruction", "label": "业务任务指令", "editable": False,
     "description": "由业务流程按任务和本轮场景生成；此处不读取或展示单次请求正文。"},
    {"key": "task_prompt", "label": "模型任务提示", "editable": True,
     "description": "本页编辑的提示字段，运行时接在业务任务指令之后；自定义值替换该字段的内置默认值。"},
    {"key": "output_contract", "label": "输出合同", "editable": False,
     "description": "按任务类型加入自然语言、JSON 字段及证据等格式约束。"},
    {"key": "capability_instruction", "label": "能力指令", "editable": False,
     "description": "需要工具、图片等模型能力时，由运行端追加对应协议。"},
    {"key": "terminal_prompt_guard", "label": "请求末尾约束", "editable": False,
     "description": "每轮请求最后追加的简短安全边界；不插入材料前缀，保留服务商可复用的稳定提示前缀。"},
)


def _target(context, layer):
    scope = context.scope
    if layer is ConfigLayer.INSTALLATION:
        return ConfigTarget(scope.installation_id, layer)
    return ConfigTarget(scope.installation_id, layer, context.role_binding.role,
                        scope if layer is ConfigLayer.WINDOW else None, bot_id=scope.bot_id)


def _field(value, inherited):
    return {"value": value.value, "inherited_value": inherited.value,
            "source": value.source.layer.value if value.source else "default",
            "inherited_source": inherited.source.layer.value if inherited.source else "default",
            "explicit": value.explicit}


def _unavailable_core_costs(value):
    """Unknown host prices stay unknown in every pure-core report bucket."""
    if isinstance(value, dict):
        if "cost_microusd" in value:
            value["cost_microusd"] = None
            value["cost_availability"] = "unavailable"
        for child in value.values():
            _unavailable_core_costs(child)
    elif isinstance(value, list):
        for child in value:
            _unavailable_core_costs(child)


def _legacy_cache_rows(runtime, role_accounts, batch_days, origin_key):
    account_keys = tuple(role_accounts[period] for period in batch_days)
    placeholders = ",".join("?" for _ in account_keys)
    return runtime.read_all(f"""SELECT c.call_id, c.position, c.input_tokens,
            c.cached_input_tokens, c.finished_at, c.started_at, r.created_at, t.kind,
            t.origin_key, c.provider_json, s.signature_json,
            CASE WHEN json_valid(t.record_json)
                THEN json_extract(t.record_json, '$.spec.context.audience.scope') ELSE NULL END AS audience_scope_json
        FROM model_calls c JOIN model_call_cache_signatures s ON s.call_id = c.call_id
        JOIN model_requests r ON r.task_id = c.task_id JOIN execution_tasks t ON t.task_id = c.task_id
        WHERE (EXISTS (SELECT 1 FROM model_call_accounts ca
                       WHERE ca.call_id = c.call_id AND ca.account_key IN ({placeholders}))
               OR (t.kind = ? AND t.origin_key = ?))
        ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at), c.position""",
        account_keys + (MAIN_CHAIN_KIND, origin_key))


def _cache_diagnostic_summary(runtime, role_accounts, all_days, origin_key, *, core_rows=()):
    def empty_day(day):
        result = {"day": day, "signature_calls": 0, "compared_calls": 0, "baseline_calls": 0,
                "changed_calls": 0, "high_severity_events": 0, "medium_severity_events": 0,
                "low_severity_events": 0, "system_prompt_changes": 0, "system_order_drifts": 0,
                "tool_definition_changes": 0, "tool_order_drifts": 0, "model_changes": 0,
                "provider_changes": 0,
                "context_resets": 0, "context_order_drifts": 0, "context_prefix_changes": 0,
                "context_tail_trims": 0, "context_availability_changes": 0,
                "cache_hit_comparable_changes": 0, "cache_hit_drop_after_change_calls": 0,
                "cache_hit_delta_points_total": 0.0,
                "context_stats_calls": 0, "context_text_stats_calls": 0,
                "context_availability_calls": 0,
                "request_stats_calls": 0, "context_estimate_calls": 0, "system_estimate_calls": 0,
                "tool_estimate_calls": 0, "message_estimate_calls": 0, "request_estimate_calls": 0,
                "context_est_tokens_total": 0, "system_est_tokens_total": 0,
                "tool_est_tokens_total": 0, "message_est_tokens_total": 0, "request_est_tokens_total": 0,
                "request_profile_calls": 0, "profile_context_est_tokens_total": 0,
                "profile_message_est_tokens_total": 0, "profile_system_est_tokens_total": 0,
                "profile_tool_est_tokens_total": 0, "profile_request_est_tokens_total": 0,
                "context_materials_total": 0, "context_chars_total": 0,
                "context_bytes_total": 0, "message_chars_total": 0, "message_bytes_total": 0,
                "deferred_materials_total": 0, "unavailable_materials_total": 0,
                "messages_total": 0, "system_messages_total": 0,
                "system_chars_total": 0, "system_bytes_total": 0, "tools_total": 0,
                "tool_bytes_total": 0, "images_total": 0, "audio_total": 0, "video_total": 0}
        for dimension in ("route", "prefix", "system", "tools", "materials", "availability"):
            result.update({f"{dimension}_compared_calls": 0, f"{dimension}_stable_calls": 0,
                           f"{dimension}_cache_calls": 0,
                           f"{dimension}_cache_input_tokens_total": 0,
                           f"{dimension}_cached_input_tokens_total": 0})
        return result

    daily = {key: {} for key in ("all", "conversation", "plugin", "astrbot_core", "legacy_conversation")}
    events = {key: deque(maxlen=80) for key in daily}
    previous_by_scope = {}
    change_fields = {
        "provider_change": "provider_changes",
        "model_change": "model_changes",
        "system_prompt_change": "system_prompt_changes",
        "system_order_drift": "system_order_drifts",
        "tool_definition_change": "tool_definition_changes",
        "tool_order_drift": "tool_order_drifts",
        "context_reset": "context_resets",
        "context_order_drift": "context_order_drifts",
        "context_prefix_change": "context_prefix_changes",
        "context_tail_trim": "context_tail_trims",
        "context_availability_change": "context_availability_changes",
    }
    ordered_days = sorted(all_days)
    for offset in range(0, len(ordered_days) + 30, 30):
        batch_days = ordered_days[offset:offset + 30]
        if not batch_days:
            rows = sorted(core_rows, key=lambda row: (row["finished_at"] or row["started_at"] or row["created_at"], row["position"]))
        else:
            rows = _legacy_cache_rows(runtime, role_accounts, batch_days, origin_key)
        for row in rows:
            signature = checked_signature(row["signature_json"])
            if signature is None:
                continue
            at = row["finished_at"] or row["started_at"] or row["created_at"]
            day = at[:10] if type(at) is str else ""
            if day not in all_days:
                continue
            source = usage_source(row["kind"])
            path = "astrbot_core" if row["kind"] != MAIN_CHAIN_KIND and source == "conversation" else "legacy_conversation" if source == "conversation" else "plugin"
            keys = tuple(dict.fromkeys(("all", source, path)))
            counts = [daily[key].setdefault(day, empty_day(day)) for key in keys]
            for item in counts:
                item["signature_calls"] += 1
                if signature.context_observed:
                    item["context_stats_calls"] += 1
                    item["context_materials_total"] += signature.context_material_count
                    item["context_bytes_total"] += signature.context_bytes
                    if signature.request_stats_observed:
                        item["context_text_stats_calls"] += 1
                        item["context_chars_total"] += signature.context_chars
                    if signature.estimates_observed and signature.request_estimate_observed:
                        item["context_estimate_calls"] += 1
                        item["context_est_tokens_total"] += signature.context_est_tokens
                if signature.context_availability_observed:
                    item["context_availability_calls"] += 1
                    item["deferred_materials_total"] += signature.deferred_material_count
                    item["unavailable_materials_total"] += signature.unavailable_material_count
                if signature.request_stats_observed:
                    item["request_stats_calls"] += 1
                    item["messages_total"] += signature.message_count
                    item["system_messages_total"] += signature.system_message_count
                    item["system_chars_total"] += signature.system_chars
                    item["system_bytes_total"] += signature.system_bytes
                    item["tools_total"] += signature.tool_count
                    item["tool_bytes_total"] += signature.tool_bytes
                    item["images_total"] += signature.image_count
                    item["audio_total"] += signature.audio_count
                    item["video_total"] += signature.video_count
                    if signature.estimates_observed and signature.request_estimate_observed:
                        item["system_estimate_calls"] += 1
                        item["tool_estimate_calls"] += 1
                        item["system_est_tokens_total"] += signature.system_est_tokens
                        item["tool_est_tokens_total"] += signature.tool_est_tokens
                if signature.message_estimates_observed:
                    item["message_estimate_calls"] += 1
                    item["message_est_tokens_total"] += signature.message_est_tokens
                    item["message_chars_total"] += signature.message_chars
                    item["message_bytes_total"] += signature.message_bytes
                if signature.request_estimate_observed:
                    item["request_estimate_calls"] += 1
                    item["request_est_tokens_total"] += signature.request_est_tokens
                if (signature.context_observed and signature.estimates_observed
                        and signature.message_estimates_observed and signature.request_estimate_observed):
                    item["request_profile_calls"] += 1
                    item["profile_context_est_tokens_total"] += signature.context_est_tokens
                    item["profile_message_est_tokens_total"] += signature.message_est_tokens
                    item["profile_system_est_tokens_total"] += signature.system_est_tokens
                    item["profile_tool_est_tokens_total"] += signature.tool_est_tokens
                    item["profile_request_est_tokens_total"] += signature.request_est_tokens
            try:
                provider_record = json.loads(row["provider_json"])
            except (TypeError, ValueError):
                provider_record = None
            provider_id = provider_record.get("provider", "") if isinstance(provider_record, dict) else ""
            if type(provider_id) is not str:
                provider_id = ""
            try:
                raw_scope = row["audience_scope_json"]
                scope = json.loads(raw_scope) if type(raw_scope) is str else raw_scope
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                scope = None
            scope_fields = ("installation_id", "platform", "kind", "native_scope_id", "bot_id",
                            "account_id", "conversation_id", "session_id")
            scope = ({name: scope[name] for name in scope_fields}
                     if type(scope) is dict and all(type(scope.get(name)) is str and scope[name]
                                                    for name in scope_fields)
                     else None)
            scope_fingerprint = digest(scope) if scope is not None else None
            try:
                call_id = row["call_id"]
            except (KeyError, IndexError, TypeError):
                call_id = digest([row["origin_key"], row["kind"], at, row["position"]])
            partition = (digest(row["origin_key"]), row["kind"], scope_fingerprint,
                         None if scope_fingerprint else call_id)
            previous_entry = previous_by_scope.get(partition)
            previous = previous_entry[0] if previous_entry else None
            input_tokens, cached_tokens = row["input_tokens"], row["cached_input_tokens"]
            hit_rate = (round(cached_tokens * 100 / input_tokens, 1)
                        if input_tokens and cached_tokens is not None else None)
            previous_provider = previous_entry[2] if previous_entry else None
            same_cache_route = (previous_entry is not None and previous_provider == provider_id
                                and previous.model_hash == signature.model_hash)
            previous_hit_rate = previous_entry[1] if same_cache_route else None
            previous_by_scope[partition] = (signature, hit_rate, provider_id)
            if previous is None:
                for item in counts:
                    item["baseline_calls"] += 1
                continue
            for item in counts:
                item["compared_calls"] += 1
            stable_dimensions = {
                "route": (previous_provider == provider_id and previous.model_hash == signature.model_hash
                    if previous_provider and provider_id else None),
                "system": previous.system_hash == signature.system_hash,
                "tools": previous.tools_hash == signature.tools_hash,
                "materials": (previous.context_sequence_hash == signature.context_sequence_hash
                    if previous.context_observed and signature.context_observed else None),
                "availability": (previous.context_availability_hash == signature.context_availability_hash
                    if previous.context_availability_observed and signature.context_availability_observed else None),
            }
            stable_dimensions["prefix"] = (all(value is True for value in stable_dimensions.values())
                                            if all(value is not None for value in stable_dimensions.values())
                                            else None)
            comparable_route = stable_dimensions["route"] is True
            for dimension, stable in stable_dimensions.items():
                if stable is None:
                    continue
                for item in counts:
                    item[f"{dimension}_compared_calls"] += 1
                    if stable:
                        item[f"{dimension}_stable_calls"] += 1
                        if (comparable_route and type(input_tokens) is int and input_tokens > 0
                                and type(cached_tokens) is int):
                            item[f"{dimension}_cache_calls"] += 1
                            item[f"{dimension}_cache_input_tokens_total"] += input_tokens
                            item[f"{dimension}_cached_input_tokens_total"] += cached_tokens
            changes = list(signature_changes(signature, previous))
            if previous_provider != provider_id:
                changes.insert(0, "provider_change")
            changes = tuple(changes)
            if not changes:
                continue
            severity = event_severity(changes)
            for item in counts:
                item["changed_calls"] += 1
                item[f"{severity}_severity_events"] += 1
                for change in changes:
                    item[change_fields[change]] += 1
                if hit_rate is not None and previous_hit_rate is not None:
                    delta = round(hit_rate - previous_hit_rate, 1)
                    item["cache_hit_comparable_changes"] += 1
                    item["cache_hit_delta_points_total"] += delta
                    if delta < 0:
                        item["cache_hit_drop_after_change_calls"] += 1
            session_ref = digest(["cache-diagnostics-session", scope_fingerprint])[:8] \
                if scope_fingerprint else None
            event = {"day": day, "at": at, "kind": row["kind"], "provider": provider_id,
                     "execution_path": "astrbot_core" if path == "astrbot_core" else "legacy_v2_conversation" if path == "legacy_conversation" else "v2_background",
                     "measurement_basis": row.get("signature_scope", "ledger_provider_payload") if isinstance(row, dict) else "ledger_provider_payload",
                     "previous_provider": previous_provider,
                     "session_ref": session_ref, "severity": severity,
                     "changes": list(changes), "input_tokens": input_tokens, "cache_hit_rate": hit_rate,
                     "previous_cache_hit_rate": previous_hit_rate,
                     "cache_hit_delta": (round(hit_rate - previous_hit_rate, 1)
                                         if hit_rate is not None and previous_hit_rate is not None else None),
                     "before": signature_snapshot(previous), "after": signature_snapshot(signature)}
            for key in keys:
                events[key].append(event)

    return {key: {"daily": [days[day] for day in sorted(days)], "recent_events": list(reversed(events[key])),
                  "event_sample_limit": 80, "scope": "current_role",
                  "signature_storage": "hashes_sizes_estimates_no_prompt_text"}
            for key, days in daily.items()}


class ModelManagement:
    def __init__(self, app, context):
        self.app, self.context = app, context
        self.access = (RoleLifeManagement(app, context)
                       if context.scope.kind is ScopeKind.PRIVATE else None)
        self.scope_access = ObjectAccessPolicy(app.runtime)

    def _authorize(self, operator, *, write=False):
        if self.access is not None:
            return self.access.authorize(operator)
        return require(self.scope_access.scope_access(operator, scope=self.context.scope,
            action="configuration.write" if write else "configuration.read",
            observed_at=self.app.now(), core_only=True))

    @outcome
    def authorize(self, operator, *, write=False):
        """Validate Dashboard access without building the full task projection."""
        return self._authorize(operator, write=write)

    def _profile(self, kind):
        if type(kind) is not str or kind not in self.app.router.profiles:
            raise TaskError("model_task_unavailable", "select a registered model task")
        return self.app.router.profiles[kind]

    def _snapshots(self, operator, profile, layer=ConfigLayer.ROLE):
        now = self.app.now()
        parent = ConfigLayer.INSTALLATION if layer is ConfigLayer.ROLE else ConfigLayer.ROLE
        current = require(self.app.configuration.read(operator, target=_target(self.context, layer),
                                                      group_id=profile.group, observed_at=now))
        inherited = require(self.app.configuration.read(operator, target=_target(self.context, parent),
                                                        group_id=profile.group, observed_at=now))
        provider_default = self.app.router.provider_default(self.context, profile, current)
        return (self.app.router.apply_provider_default(profile, current, provider_default),
                self.app.router.apply_provider_default(profile, inherited, provider_default), provider_default)

    def _section_snapshots(self, operator, section, layer=ConfigLayer.ROLE):
        definitions = self.app.configuration.registry.group(section.config_group)
        if layer not in (ConfigLayer.ROLE, ConfigLayer.WINDOW) or layer not in definitions[0].layers:
            raise TaskError("model_configuration_invalid", "the prompt section does not support this configuration scope")
        if (ConfigLayer.RELATIONSHIP in definitions[0].layers
                and self.context.scope.kind is ScopeKind.PRIVATE):
            scope = self.context.scope
            target = ConfigTarget(scope.installation_id, ConfigLayer.WINDOW,
                self.context.role_binding.role, scope, bot_id=scope.bot_id,
                subject=self.context.actor.subject)
        else:
            target = _target(self.context, layer)
        inherited_target = next((parent for parent in reversed(target.chain()[:-1])
                                 if parent.layer in definitions[0].layers), None)
        now = self.app.now()
        current = require(self.app.configuration.read(operator, target=target,
            group_id=section.config_group, observed_at=now))
        inherited = (require(self.app.configuration.read(operator, target=inherited_target,
            group_id=section.config_group, observed_at=now)) if inherited_target is not None else
            replace(current, fields={field.field_id: EffectiveValue(field.default, None, False, 0)
                                     for field in definitions}))
        return current, inherited

    def _prompt_sections(self, operator, profile, layer=ConfigLayer.ROLE):
        snapshots, result = {}, []
        for section in profile.prompt_sections:
            item = {"key": section.key, "label": section.label, "condition": section.condition,
                    "default_prompt": section.default_prompt, "editable": section.editable,
                    "variables": list(section.variables)}
            if section.editable:
                definitions = self.app.configuration.registry.group(section.config_group)
                section_layer = layer
                group_window_inherits = (layer is ConfigLayer.WINDOW
                    and self.context.scope.kind is ScopeKind.GROUP
                    and ConfigLayer.RELATIONSHIP in definitions[0].layers)
                if group_window_inherits:
                    section_layer = ConfigLayer.ROLE
                    item["editable"] = False
                snapshot_key = section.config_group, section_layer
                if snapshot_key not in snapshots:
                    snapshots[snapshot_key] = self._section_snapshots(operator, section, section_layer)
                current, inherited = snapshots[snapshot_key]
                field_id = section.config_group + "." + section.field
                field = _field(current.fields[field_id], inherited.fields[field_id])
                item.update(field=field, edit_token=current.edit_token, config_field=field_id,
                            configuration_layer=current.target.layer.value,
                            prompt=field["value"], inheritance_label="上级继承值")
            else:
                item["prompt"] = section.default_prompt
            result.append(item)
        return result

    def _project(self, operator, profile, layer=ConfigLayer.ROLE):
        current, inherited, provider_default = self._snapshots(operator, profile, layer)
        fields = {name: _field(current.fields[profile.group + "." + name],
                               inherited.fields[profile.group + "." + name]) for name in FIELDS}
        if provider_default is not None:
            for name in ("provider", "fallback"):
                if name == "fallback" and profile.default_fallback:
                    continue
                if fields[name]["source"] == "default":
                    fields[name]["source"] = "host_conversation"
                if fields[name]["inherited_source"] == "default":
                    fields[name]["inherited_source"] = "host_conversation"
        application = require(self.app.configuration.application_status(operator,
            target=_target(self.context, layer), group_id=profile.group, observed_at=self.app.now()))
        sections = self._prompt_sections(operator, profile, layer)
        layers = [dict(layer) for layer in PROMPT_LAYERS]
        if any(section["editable"] for section in sections):
            layers[1].update(editable=True, description="完整业务模板在下方按场景查看；表达模板可编辑，事实与协议规则固定保留。")
        return {"kind": profile.kind, "configuration_layer": layer.value,
                "lane": profile.lane.value, "capabilities": list(profile.capabilities),
                "default_prompt": profile.default_prompt, "prompt_version": profile.version,
                "prompt_layers": layers, "prompt_sections": sections,
                "fields": fields, "edit_token": current.edit_token, "application_state": application.state,
                "apply_mode": current.apply_mode.value, "revision": current.revision}

    def _runtime_diagnostics(self):
        runtime = self.app.runtime
        context_key = self.context.key
        identity_scope = {
            "dashboard_operator": "verified",
            "role_access": "verified",
            "token_ledger": "current_role",
        }
        pragma = lambda name: runtime.read_one("PRAGMA " + name, ())[0]
        storage = {
            "journal_mode": str(pragma("journal_mode")).lower(),
            "synchronous": int(pragma("synchronous")),
            "busy_timeout_ms": int(pragma("busy_timeout")),
            "foreign_keys": bool(pragma("foreign_keys")),
            "page_count": int(pragma("page_count")),
            "freelist_count": int(pragma("freelist_count")),
            "schema_version": int(pragma("user_version")),
        }
        batches = runtime.read_all("""SELECT b.batch_id, b.created_at, b.state AS batch_state,
                b.part_count, f.state AS flow_state, f.reason,
                COUNT(DISTINCT i.event_key) AS input_count,
                COUNT(DISTINCT CASE WHEN p.state = 'delivered' THEN p.part_id END) AS delivered_parts,
                COUNT(DISTINCT CASE WHEN p.state = 'failed' THEN p.part_id END) AS failed_parts,
                COUNT(DISTINCT CASE WHEN p.state = 'unknown' THEN p.part_id END) AS unknown_parts,
                COUNT(DISTINCT CASE WHEN p.state IN ('pending', 'dispatched') THEN p.part_id END) AS pending_parts
            FROM conversation_batches b
            LEFT JOIN conversation_flows f ON f.batch_id = b.batch_id
            LEFT JOIN conversation_inputs i ON i.batch_id = b.batch_id
            LEFT JOIN conversation_parts p ON p.batch_id = b.batch_id
            WHERE b.origin_key = ?
            GROUP BY b.batch_id
            ORDER BY b.created_at DESC LIMIT 12""", (context_key,))
        conversation = []
        for row in batches:
            flow_state = row["flow_state"] or "not_started"
            part_count = row["part_count"] or 0
            delivered = row["delivered_parts"] or 0
            failed = row["failed_parts"] or 0
            unknown = row["unknown_parts"] or 0
            pending = row["pending_parts"] or 0
            no_reply = (not delivered and not unknown and not pending and
                        (failed > 0 or flow_state in ("blocked", "closed") or
                         (flow_state == "complete" and part_count == 0)))
            reply_outcome = ("partial_reply" if delivered and (failed or unknown) else
                             "replied" if delivered else "unknown" if unknown else
                             "pending" if pending or flow_state in ("collecting", "generating", "delivering") else
                             "no_reply" if no_reply else "unresolved")
            conversation.append({
                "created_at": row["created_at"], "batch_state": row["batch_state"],
                "flow_state": flow_state, "flow_reason": row["reason"] or "",
                "input_count": row["input_count"] or 0, "part_count": part_count,
                "delivered_parts": delivered, "failed_parts": failed,
                "unknown_parts": unknown, "pending_parts": pending,
                "reply_outcome": reply_outcome, "no_reply": bool(no_reply),
            })
        flow_rows = runtime.read_all("""SELECT b.created_at, b.state AS batch_state, b.part_count,
                f.state AS flow_state, f.due_at, f.merge_until, f.deadline_at, f.policy_json, f.reason,
                COUNT(DISTINCT i.event_key) AS input_count
            FROM conversation_batches b
            JOIN conversation_flows f ON f.batch_id = b.batch_id
            LEFT JOIN conversation_inputs i ON i.batch_id = b.batch_id
            WHERE b.origin_key = ?
            GROUP BY b.batch_id
            ORDER BY b.created_at DESC LIMIT 8""", (context_key,))
        debounce_trace = []
        for row in flow_rows:
            try:
                policy = json.loads(row["policy_json"])
            except (TypeError, ValueError):
                policy = {}
            if not isinstance(policy, dict):
                policy = {}

            def milliseconds(name):
                value = policy.get(name)
                if type(value) not in (int, float) or not 0 <= value <= 3600:
                    return None
                return round(value * 1000)

            debounce_trace.append({
                "at": row["created_at"], "batch_state": row["batch_state"],
                "flow_state": row["flow_state"], "reason": row["reason"],
                "input_count": row["input_count"] or 0, "part_count": row["part_count"] or 0,
                "due_at": row["due_at"], "merge_until": row["merge_until"],
                "deadline_at": row["deadline_at"],
                "quiet_window_ms": milliseconds("quiet_seconds"),
                "merge_window_ms": milliseconds("maximum_wait_seconds"),
                "turn_timeout_ms": milliseconds("turn_timeout_seconds"),
            })
        material_rows = runtime.read_all("""SELECT
                COALESCE(json_extract(m.value, '$.owner'), 'unknown') AS owner,
                COALESCE(json_extract(m.value, '$.purpose'), 'unknown') AS purpose,
                COALESCE(json_extract(m.value, '$.state'), 'unknown') AS state,
                COUNT(*) AS uses
            FROM (SELECT contributions_json FROM conversation_batches
                  WHERE origin_key = ? ORDER BY created_at DESC LIMIT 20) AS recent
            JOIN json_each(recent.contributions_json, '$.materials') AS m
            GROUP BY owner, purpose, state ORDER BY uses DESC, owner, purpose LIMIT 24""", (context_key,))
        materials = [{"owner": row["owner"], "purpose": row["purpose"],
                      "state": row["state"], "uses": row["uses"]} for row in material_rows]
        proactive = runtime.read_one("""SELECT COUNT(*) AS calls, MAX(created_at) AS latest_at
            FROM proactive_contacts WHERE origin_key = ?""", (context_key,))
        no_reply_rows = runtime.read_all("""SELECT event_ref, channel, reason_code, code, observed_at,
                CASE WHEN origin_key IS NULL THEN 'unmatched' ELSE 'current_role' END AS scope
            FROM inbound_no_replies
            WHERE origin_key = ? OR origin_key IS NULL
            ORDER BY observed_at DESC, rowid DESC LIMIT 24""", (context_key,))
        inbound_no_replies = [{
            "event_ref": row["event_ref"], "channel": row["channel"],
            "reason_code": row["reason_code"], "code": row["code"], "observed_at": row["observed_at"],
            "scope": row["scope"],
        } for row in no_reply_rows]
        return {"storage": storage, "conversation": conversation, "prompt_materials": materials,
                "identity_scope": identity_scope, "debounce_trace": debounce_trace,
                "inbound_no_replies": inbound_no_replies,
                "proactive": {"calls": proactive["calls"] if proactive else 0,
                              "latest_at": proactive["latest_at"] if proactive else None}}

    @outcome
    def status(self, operator, *, layer="role"):
        if layer not in (ConfigLayer.ROLE.value, ConfigLayer.WINDOW.value):
            raise TaskError("model_configuration_invalid", "select role defaults or this conversation window")
        selected_layer = ConfigLayer(layer)
        with self.app.runtime.read_snapshot():
            self._authorize(operator)
            return {"configuration_layer": selected_layer.value,
                    "tasks": [self._project(operator, profile, selected_layer)
                              for profile in self.app.router.profiles.values()],
                    "providers": [{"id": provider.binding.provider, "capabilities": list(provider.capabilities),
                                   "max_output_tokens": provider.maximum_output_tokens,
                                   "supports_model_override": provider.supports_model_override,
                                   "model": provider.model}
                                   for provider in self.app.router.providers.values()],
                    "host_state": self.app.state}

    @outcome
    def maintenance(self, operator):
        """Read role-scoped model usage and diagnostics without exposing prompt material."""
        with self.app.runtime.read_snapshot():
            self._authorize(operator)
            now = self.app.now()
            runtime_diagnostics = self._runtime_diagnostics()
            scope = self.context.scope
            role_address = [scope.installation_id, scope.bot_id, asdict(self.context.role_binding.role)]
            today = now.date().isoformat()
            candidate_days = self.app.runtime.read_all("""SELECT DISTINCT
                    substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day
                FROM model_calls c JOIN model_requests r ON r.task_id = c.task_id
                ORDER BY day""", ())
            all_days = {today}
            core_observations = observations_for_role(self.app.runtime, self.context, end=now.isoformat())
            core_rows = request_rows(core_observations)
            all_days.update(row["day"] for row in core_rows)
            for item in candidate_days:
                value = item["day"]
                try:
                    parsed = date.fromisoformat(value)
                except (TypeError, ValueError):
                    continue
                if parsed.isoformat() == value:
                    all_days.add(value)
            role_accounts = {period: digest(["role", role_address, period]) for period in all_days}
            reports = {key: ModelUsageReport() for key in ("all", "conversation", "plugin", "astrbot_core", "legacy_conversation")}
            def report_keys(kind):
                source = usage_source(kind)
                return ("all", "conversation", "legacy_conversation") if source == "conversation" else ("all", "plugin")
            ordered_days = sorted(all_days)
            batch_size = 300
            for offset in range(0, len(ordered_days), batch_size):
                batch_days = ordered_days[offset:offset + batch_size]
                account_keys = tuple(role_accounts[period] for period in batch_days)
                placeholders = ",".join("?" for _ in account_keys)
                grouped_rows = self.app.runtime.read_all(f"""SELECT
                        substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day,
                        CASE WHEN json_valid(c.provider_json)
                            THEN COALESCE(json_extract(c.provider_json, '$.provider'), '') ELSE '' END AS provider,
                        t.kind AS kind,
                        COUNT(*) AS calls,
                        SUM(COALESCE(c.actual_tokens, 0)) AS tokens,
                        SUM(COALESCE(c.actual_cost, 0)) AS cost_microusd,
                        SUM(CASE WHEN c.actual_tokens IS NULL THEN 1 ELSE 0 END) AS unconfirmed_calls,
                        SUM(CASE WHEN f.call_id IS NULL THEN 0 ELSE 1 END) AS failures,
                        SUM(CASE WHEN c.state = 'unknown' THEN 1 ELSE 0 END) AS unknown_calls,
                        SUM(CASE WHEN c.state IN ('reserved', 'dispatched') THEN 1 ELSE 0 END) AS active_calls,
                        SUM(CASE WHEN c.input_tokens IS NOT NULL AND c.cached_input_tokens IS NOT NULL THEN 1 ELSE 0 END)
                            AS cache_reported_calls,
                        SUM(CASE WHEN c.input_tokens IS NOT NULL AND c.cached_input_tokens IS NOT NULL THEN c.input_tokens ELSE 0 END)
                            AS cache_input_tokens,
                        SUM(CASE WHEN c.input_tokens IS NOT NULL AND c.cached_input_tokens IS NOT NULL THEN c.cached_input_tokens ELSE 0 END)
                            AS cached_input_tokens,
                        SUM(COALESCE(c.cache_creation_tokens, 0)) AS cache_creation_tokens,
                        SUM(CASE WHEN c.input_tokens IS NOT NULL AND c.cached_input_tokens IS NOT NULL THEN
                            MAX(0, c.input_tokens - c.cached_input_tokens - COALESCE(c.cache_creation_tokens, 0))
                            ELSE 0 END) AS cache_other_input_tokens,
                        SUM(CASE WHEN c.cached_input_tokens IS NOT NULL OR c.cache_creation_tokens IS NOT NULL THEN 1 ELSE 0 END)
                            AS cache_telemetry_calls,
                        SUM(CASE WHEN c.input_tokens >= 1000 AND c.cached_input_tokens IS NOT NULL
                            AND c.cached_input_tokens * 5 < c.input_tokens
                            THEN 1 ELSE 0 END) AS cache_low_calls,
                        SUM(COALESCE(c.input_tokens, 0)) AS input_tokens,
                        SUM(COALESCE(c.output_tokens, 0)) AS output_tokens,
                        SUM(COALESCE(c.latency_ms, 0)) AS latency_total_ms,
                        SUM(CASE WHEN c.latency_ms IS NULL THEN 0 ELSE 1 END) AS latency_samples
                    FROM model_calls c
                    JOIN model_requests r ON r.task_id = c.task_id
                    JOIN execution_tasks t ON t.task_id = c.task_id
                    LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                    WHERE (EXISTS (SELECT 1 FROM model_call_accounts ca
                                   WHERE ca.call_id = c.call_id AND ca.account_key IN ({placeholders}))
                           OR (t.kind = ? AND t.origin_key = ?))
                     GROUP BY day, provider, kind""", account_keys + (MAIN_CHAIN_KIND, self.context.key))

                for row in grouped_rows:
                    for key in report_keys(row["kind"]):
                        reports[key].add_group(row)
                hour_rows = self.app.runtime.read_all(f"""SELECT
                        substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day,
                        substr(COALESCE(c.finished_at, c.started_at, r.created_at), 12, 2) AS hour,
                        t.kind AS kind,
                        COUNT(*) AS calls, SUM(COALESCE(c.actual_tokens, 0)) AS tokens,
                        SUM(COALESCE(c.actual_cost, 0)) AS cost_microusd
                    FROM model_calls c
                    JOIN model_requests r ON r.task_id = c.task_id
                    JOIN execution_tasks t ON t.task_id = c.task_id
                    WHERE (EXISTS (SELECT 1 FROM model_call_accounts ca
                                   WHERE ca.call_id = c.call_id AND ca.account_key IN ({placeholders}))
                           OR (t.kind = ? AND t.origin_key = ?))
                    GROUP BY day, hour, kind ORDER BY day, hour""",
                    account_keys + (MAIN_CHAIN_KIND, self.context.key))
                for row in hour_rows:
                    for key in report_keys(row["kind"]):
                        reports[key].add_hour(row)
                recent_rows = self.app.runtime.read_all(f"""WITH samples AS (
                    SELECT c.state, c.position, c.actual_tokens, c.reserved_tokens,
                        c.receipt_ref,
                        c.actual_cost, c.input_tokens, c.cached_input_tokens, c.cache_creation_tokens, c.output_tokens,
                        c.latency_ms, c.finished_at, c.started_at, r.created_at, t.kind, c.provider_json,
                        f.code AS failure_code, d.summary_json AS response_summary_json,
                        ROW_NUMBER() OVER (PARTITION BY CASE WHEN t.kind = 'conversation.reply'
                            THEN 'conversation' ELSE 'plugin' END
                            ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at) DESC, c.position DESC)
                            AS source_position
                    FROM model_calls c
                    JOIN model_requests r ON r.task_id = c.task_id
                    JOIN execution_tasks t ON t.task_id = c.task_id
                    LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                    LEFT JOIN model_call_diagnostics d ON d.call_id = c.call_id
                    WHERE (EXISTS (SELECT 1 FROM model_call_accounts ca
                                   WHERE ca.call_id = c.call_id AND ca.account_key IN ({placeholders}))
                           OR (t.kind = ? AND t.origin_key = ?)))
                     SELECT * FROM samples WHERE source_position <= 80""",
                    account_keys + (MAIN_CHAIN_KIND, self.context.key))
                for row in recent_rows:
                    for key in report_keys(row["kind"]):
                        reports[key].add_recent(row)
            for row in core_rows:
                for key in ("all", "conversation", "astrbot_core"):
                    reports[key].add_group({**row, **row["aggregate"]})
                    reports[key].add_hour(row)
                    reports[key].add_recent(row)
            cache_reports = _cache_diagnostic_summary(self.app.runtime, role_accounts, all_days, self.context.key,
                                                     core_rows=core_rows)
            usage_reports = {key: {**report.project(now), "cache_diagnostics": cache_reports[key]}
                             for key, report in reports.items()}
            usage_reports["astrbot_core"].update(execution_path="astrbot_core",
                coverage=availability_summary(core_observations),
                measurement_basis={"usage": "observed_provider_responses_with_explicit_fallback_scope",
                                   "cache": "reported_fields_only", "cost": "unavailable",
                                   "signature": "runner_pre_provider_or_provider_request_hook"})
            _unavailable_core_costs(usage_reports["astrbot_core"])
            usage_reports["astrbot_core"]["cost_availability"] = "unavailable"
            for key in ("all", "conversation"):
                usage_reports[key]["cost_availability"] = "partial" if core_observations else "local_priced_subtotal"
                usage_reports[key]["core_cost_availability"] = "unavailable"
            usage_reports["legacy_conversation"]["execution_path"] = "legacy_v2_conversation"
            usage_reports["plugin"]["execution_path"] = "v2_background"
            today_account = role_accounts[now.date().isoformat()]
            budget_row = self.app.runtime.read_one(
                "SELECT token_limit, held_tokens, spent_tokens FROM model_budget_accounts WHERE account_key = ?",
                (today_account,))
            plugin_usage = self.app.gateway.ledger.plugin_account_usage(today_account)
            policy = self.app.gateway.ledger.policy
            budget = {"hard_limit_enabled": policy.enabled,
                      "limit_tokens": budget_row["token_limit"] if budget_row else policy.role.tokens,
                      "spent_tokens": plugin_usage["spent_tokens"],
                      "held_tokens": plugin_usage["held_tokens"]}
            reservations = self.app.gateway.ledger.reservation_sources(today_account)
            released = self.app.gateway.ledger.released_usage(today_account)
            pending = [item for item in reservations if item["state"] not in ("reserved", "dispatched")]
            budget.update(reservation_sources=reservations,
                          pending_usage_calls=sum(item["calls"] for item in pending),
                          pending_usage_tokens=sum(item["held_tokens"] for item in pending),
                          released_calls=released["calls"],
                          released_tokens=released["tokens"],
                          released_cost_microusd=released["cost_microusd"],
                          reclaim=self.app.gateway.ledger.reclaim_audit(),
                          remaining_tokens=(max(0, budget["limit_tokens"] - budget["spent_tokens"] - budget["held_tokens"])
                                            if policy.enabled else None))
            soft = effective_soft(self.app.runtime, self.context, self.app.gateway.ledger.soft)
            budget.update({"soft_limit_enabled": bool(soft and soft.enabled),
                           "soft_limit_tokens": soft.tokens if soft else 0})
            providers = [{"id": item.binding.provider, "model": item.model}
                         for item in self.app.router.providers.values()]
            return {"generated_at": now.isoformat(), **usage_reports["all"],
                    "usage_sources": {key: usage_reports[key] for key in ("conversation", "plugin", "astrbot_core", "legacy_conversation")},
                    "reply_efficiency": reply_efficiency_report(self.app.runtime, self.context, now),
                    "budget": budget, "providers": providers, "window_days": 30,
                    "ledger_scope": "current_role", "runtime": runtime_diagnostics}

    @outcome
    def check_storage(self, operator):
        """Run explicit read-only SQLite integrity and foreign-key checks."""
        with self.app.runtime.read_only_snapshot() as connection:
            self._authorize(operator)
            integrity = [row[0] for row in connection.execute("PRAGMA quick_check")]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchmany(20)
            return {"status": "ok" if integrity == ["ok"] and not foreign_keys else "failed",
                    "integrity": integrity[:8], "foreign_key_issues": len(foreign_keys),
                    "foreign_key_sample": [list(row) for row in foreign_keys[:5]]}

    def _changes(self, profile, data, current, inherited):
        changes, reset = data["changes"], data["reset"]
        if (type(changes) is not dict or type(reset) is not list or not changes and not reset
                or any(type(name) is not str or name not in FIELDS for name in changes)
                or any(type(name) is not str or name not in FIELDS for name in reset)
                or len(reset) != len(set(reset)) or set(changes).intersection(reset)):
            raise TaskError("model_configuration_invalid", "select distinct task fields to save or inherit")
        independent = set(changes).union(reset)
        if independent and independent <= {"prompt", "fallback_on_refusal", "refusal_terms"}:
            # These fields do not need a working provider route to be edited.
            if "prompt" in changes and (type(changes["prompt"]) is not str or len(changes["prompt"]) > 8000):
                raise TaskError("model_configuration_invalid", "task prompt must be bounded text")
            if "fallback_on_refusal" in changes and type(changes["fallback_on_refusal"]) is not bool:
                raise TaskError("model_configuration_invalid", "refusal replacement must be a boolean")
            if "refusal_terms" in changes:
                try:
                    changes = {**changes, "refusal_terms": canonical_refusal_terms(changes["refusal_terms"])}
                except ValueError as error:
                    raise TaskError("model_configuration_invalid", "check the refusal match terms") from error
            return {profile.group + "." + name: value for name, value in changes.items()}, tuple(
                profile.group + "." + name for name in reset)
        values = {name: current.fields[profile.group + "." + name].value for name in FIELDS}
        for name in reset:
            values[name] = inherited.fields[profile.group + "." + name].value
        values.update(changes)
        try:
            values["refusal_terms"] = canonical_refusal_terms(values["refusal_terms"])
        except ValueError as error:
            raise TaskError("model_configuration_invalid", "check the refusal match terms") from error
        if "refusal_terms" in changes:
            changes = {**changes, "refusal_terms": values["refusal_terms"]}
        if (type(values["provider"]) is not str or type(values["fallback"]) is not str
                or type(values["prompt"]) is not str
                or len(values["prompt"]) > 8000 or type(values["max_output_tokens"]) is not int
                or type(values["fallback_on_refusal"]) is not bool
                or not 1 <= values["max_output_tokens"] <= MAX_MODEL_OUTPUT_TOKENS
                or type(values["timeout_seconds"]) is not int or not 1 <= values["timeout_seconds"] <= 600
                or values["replacement_scope"] not in ("all", "plugin", "conversation")):
            raise TaskError("model_configuration_invalid", "check task prompt and output token limit")
        selected = [values["provider"]] + ([values["fallback"]] if values["fallback"] else [])
        fallback_explicit = ("fallback" in changes or
                             ("fallback" not in reset and current.fields[profile.group + ".fallback"].explicit))
        if fallback_explicit and len(set(selected)) != len(selected):
            raise TaskError("model_fallback_duplicate", "fallback must differ from the primary provider")
        for provider_id in selected:
            provider = self.app.router.providers.get(provider_id)
            if provider is None:
                raise TaskError("model_provider_unavailable", "the provider has no registered V2 adapter")
            if not set(profile.capabilities).issubset(provider.capabilities):
                raise TaskError("model_capability_mismatch", "the provider lacks task capabilities")
            if values["max_output_tokens"] > provider.maximum_output_tokens:
                # Only a provider ceiling can refuse a task limit, so the message names
                # the field, the provider and that ceiling; the rejected value stays out
                # of the text, and an undeclared provider never produces this error.
                raise TaskError("model_output_limit",
                                f"max_output_tokens is limited to {provider.maximum_output_tokens}"
                                " by provider " + provider_id)
        try:
            validate_targets(checked_rules(values["replacement_rules"]), self.app.router.providers,
                             capabilities=profile.capabilities, max_output_tokens=values["max_output_tokens"])
        except ValueError as error:
            raise TaskError("model_route_invalid", str(error)) from error
        return {profile.group + "." + name: value for name, value in changes.items()}, tuple(
            profile.group + "." + name for name in reset)

    @outcome
    def configure(self, operator, data):
        keys = {"kind", "operation_key", "edit_token", "changes", "reset"}
        if type(data) is not dict or set(data) not in (keys, keys | {"section"}, keys | {"layer"},
                                                       keys | {"section", "layer"}):
            raise TaskError("model_configuration_invalid", "provide one complete model task patch")
        kind, key, token = data["kind"], data["operation_key"], data["edit_token"]
        layer = data.get("layer", ConfigLayer.WINDOW.value)
        if (type(key) is not str or not key.strip() or len(key) > 128
                or type(token) is not str or len(token) != 64
                or layer not in (ConfigLayer.ROLE.value, ConfigLayer.WINDOW.value)):
            raise TaskError("model_configuration_invalid", "provide a stable operation key and edit token")
        selected_layer = ConfigLayer(layer)
        with self.app.runtime.transaction():
            self._authorize(operator, write=True)
            profile = self._profile(kind)
            if "section" in data:
                return self._configure_section(operator, profile, data, selected_layer)
            current, inherited, _ = self._snapshots(operator, profile, selected_layer)
            changes, reset = self._changes(profile, data, current, inherited)
            target = _target(self.context, selected_layer)
            op_id = "model-settings:" + digest([operator.subject, target.key, kind, key])
            receipt = require(self.app.configuration.patch(operator,
                target=target, group_id=profile.group,
                operation=OperationRef(op_id, OWNER, target.authorization_scope, VersionRef(op_id, 1), op_id),
                expected_edit_token=token, changes=changes, reset_to_inherit=reset,
                observed_at=self.app.now()))
            return {"replayed": receipt.replayed, "operation_id": op_id,
                    "task": self._project(operator, profile, selected_layer)}

    def _configure_section(self, operator, profile, data, layer):
        section = next((item for item in profile.prompt_sections if item.key == data["section"]), None)
        if section is None or not section.editable:
            raise TaskError("model_prompt_section_readonly", "select a registered editable expression template")
        changes, reset = data["changes"], data["reset"]
        if not (type(changes) is dict and type(reset) is list
                and ((set(changes) == {"prompt"} and not reset and type(changes["prompt"]) is str
                      and len(changes["prompt"]) <= 8000) or (not changes and reset == ["prompt"]))):
            raise TaskError("model_configuration_invalid", "save one expression template or restore its inherited value")
        current, _ = self._section_snapshots(operator, section, layer)
        field_id = section.config_group + "." + section.field
        owner = self.app.configuration.registry.group(section.config_group)[0].owner_id
        op_id = "model-prompt:" + digest([operator.subject, current.target.key, profile.kind,
                                          section.key, data["operation_key"]])
        receipt = require(self.app.configuration.patch(operator,
            target=current.target, group_id=section.config_group,
            operation=OperationRef(op_id, owner, current.target.authorization_scope, VersionRef(op_id, 1), op_id),
            expected_edit_token=data["edit_token"], changes={field_id: changes["prompt"]} if changes else {},
            reset_to_inherit=(field_id,) if reset else (), observed_at=self.app.now()))
        return {"replayed": receipt.replayed, "operation_id": op_id,
                "task": self._project(operator, profile, layer)}
