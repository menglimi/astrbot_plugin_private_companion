"""Dashboard model task settings for one authorized private role context."""

from dataclasses import asdict, replace
from datetime import date, timedelta, timezone
import json

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, EffectiveValue
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.role_work.life.management import RoleLifeManagement
from .budget import effective_soft
from .core_observations import (_parse_utc_timestamp, availability_summary,
                                observations_with_scope_for_role, request_rows)
from .models import MAX_MODEL_OUTPUT_TOKENS, provider_supports_task
from .pricing import (
    PricingSettingsStore,
    effective_usd_cny_rate,
    evaluate_call_cost_microusd,
    lookup_pricing_schedule,
    pricing_summary,
    provider_needs_per_call_repricing,
    resolve_effective_pricing,
    resolve_pricing_rule,
)
from .pricing_catalog import PriceCatalogStore
from .reply_efficiency import reply_efficiency_report
from .refusal import canonical_refusal_terms
from .replacement_rules import checked_rules, output_capabilities, validate_targets
from .usage import MAIN_CHAIN_KIND, ModelUsageReport, usage_source
from .maintenance_cache import (
    CORE_USAGE_KEYS,
    CacheDiagnosticAccumulator,
    build_legacy_entry,
    build_snapshot_entry,
    clone_legacy_reports,
    has_active_read_snapshot,
    has_active_write_transaction,
    merge_cache_diagnostics,
    merge_usage_report,
    pricing_fingerprint,
    read_legacy_watermark,
    read_shared_db_token,
    runtime_maintenance_cache,
    save_disk_core_history,
)
from .core_observations import available as _core_observations_available, role_key as _core_role_key


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


def _resolve_provider_pricing_meta(router_providers, provider_metadata):
    meta_map = provider_metadata if isinstance(provider_metadata, dict) else {}
    provider_models = {item.binding.provider: item.model for item in router_providers}
    provider_clusters = {}
    provider_own_sources = {}
    for pid_key, info in meta_map.items():
        if type(pid_key) is not str or not isinstance(info, dict):
            continue
        if not provider_models.get(pid_key) and type(info.get("model")) is str and info["model"]:
            provider_models[pid_key] = info["model"]
        if type(info.get("cluster_id")) is str and info["cluster_id"]:
            provider_clusters[pid_key] = info["cluster_id"]
        if isinstance(info.get("own_source_ids"), (set, frozenset, list, tuple)):
            provider_own_sources[pid_key] = frozenset(str(x) for x in info["own_source_ids"] if x)
    return provider_models, provider_clusters, provider_own_sources


def _make_pricing_resolver(
    pricing_settings,
    pricing_catalog,
    provider_models,
    provider_clusters,
    provider_own_sources,
):
    usd_cny_rate = effective_usd_cny_rate(pricing_settings)
    raw_base_pair_cache = {}
    has_schedule_pair_cache = {}
    static_eff_pair_cache = {}

    def model_for(provider_id, call_model=""):
        if type(call_model) is str and call_model.strip():
            return call_model.strip()
        return provider_models.get(provider_id, "")

    def _raw_base_for_pair(pid, cmodel):
        pair = (pid, cmodel)
        if pair not in raw_base_pair_cache:
            raw_base_pair_cache[pair] = resolve_pricing_rule(
                pid,
                cmodel,
                pricing_settings,
                catalog=pricing_catalog,
                own_source_ids=provider_own_sources.get(pid, frozenset()),
            )
        return raw_base_pair_cache[pair]

    def resolve_for_call(provider_id, cmodel, call_ts=None):
        pid = provider_id if type(provider_id) is str else ""
        pair = (pid, cmodel)
        has_sched = has_schedule_pair_cache.get(pair)
        if has_sched is None:
            has_sched = lookup_pricing_schedule(pid, cmodel, pricing_settings) is not None
            has_schedule_pair_cache[pair] = has_sched
        if not has_sched or call_ts is None:
            if pair not in static_eff_pair_cache:
                static_eff_pair_cache[pair] = resolve_effective_pricing(
                    pid,
                    cmodel,
                    pricing_settings,
                    catalog=pricing_catalog,
                    cluster_id=provider_clusters.get(pid),
                    own_source_ids=provider_own_sources.get(pid, frozenset()),
                    precomputed_base=_raw_base_for_pair(pid, cmodel),
                )
            return static_eff_pair_cache[pair]
        return resolve_effective_pricing(
            pid,
            cmodel,
            pricing_settings,
            catalog=pricing_catalog,
            cluster_id=provider_clusters.get(pid),
            timestamp=call_ts,
            own_source_ids=provider_own_sources.get(pid, frozenset()),
            precomputed_base=_raw_base_for_pair(pid, cmodel),
        )

    def enrich_core_rows(rows):
        seen_core_turns = set()
        for row in rows:
            pid = row["provider"] if type(row["provider"]) is str else ""
            cmodel = model_for(pid)
            call_ts = row.get("finished_at") or row.get("started_at") or row.get("created_at")
            eff = resolve_for_call(pid, cmodel, call_ts)
            measurable = (row["actual_tokens"] is not None
                          or row["input_tokens"] is not None
                          or row["output_tokens"] is not None)
            row_calls = max(1, int(row.get("_rollup_calls") or 1))
            if "_rollup_requests" in row:
                row_requests = max(0, int(row["_rollup_requests"] or 0))
            else:
                turn_id = str(row.get("call_id") or "").rsplit(":", 1)[0]
                turn_key = (turn_id, pid)
                is_new_turn = turn_key not in seen_core_turns
                if measurable:
                    seen_core_turns.add(turn_key)
                row_requests = 1 if is_new_turn else 0
            est = (
                evaluate_call_cost_microusd(
                    eff,
                    usd_cny_rate=usd_cny_rate,
                    input_tokens=row["input_tokens"],
                    cached_input_tokens=row["cached_input_tokens"],
                    cache_creation_tokens=row["cache_creation_tokens"],
                    output_tokens=row["output_tokens"],
                    total_tokens=row["actual_tokens"],
                    model=cmodel,
                    calls=row_calls,
                    requests=row_requests,
                    strict_expr=False,
                )
                if (eff is not None and measurable)
                else None
            )
            if est is not None:
                cost_meta = {
                    "cost_microusd": est, "recorded_cost_calls": 0, "recorded_cost_microusd": 0,
                    "estimated_cost_calls": row_calls, "estimated_cost_microusd": est, "unpriced_token_calls": 0,
                }
                enriched_core_recent = {
                    **row, "actual_cost": est, "estimated_cost_microusd": est,
                    "cost_basis": "estimated_local", "cost_availability": "estimated_local",
                }
            else:
                cost_meta = {
                    "cost_microusd": 0, "recorded_cost_calls": 0, "recorded_cost_microusd": 0,
                    "estimated_cost_calls": 0, "estimated_cost_microusd": 0,
                    "unpriced_token_calls": row_calls if measurable else 0,
                }
                enriched_core_recent = {
                    **row, "actual_cost": None, "cost_basis": "unconfigured",
                    "cost_availability": "unavailable",
                }
            yield row, cost_meta, enriched_core_recent

    return usd_cny_rate, model_for, resolve_for_call, enrich_core_rows


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


def _legacy_matched_calls_cte(placeholders):
    return (
        f"matched AS (SELECT call_id FROM model_call_accounts WHERE account_key IN ({placeholders}) "
        "UNION SELECT c.call_id FROM execution_tasks t JOIN model_calls c ON c.task_id = t.task_id "
        "WHERE t.kind = ? AND t.origin_key = ?)"
    )


def _legacy_cache_rows(runtime, role_accounts, batch_days, origin_key):
    account_keys = tuple(role_accounts[period] for period in batch_days)
    placeholders = ",".join("?" for _ in account_keys)
    matched_cte = _legacy_matched_calls_cte(placeholders)
    return runtime.read_all(f"""WITH {matched_cte}
        SELECT c.call_id, c.position, c.input_tokens,
            c.cached_input_tokens, c.finished_at, c.started_at, r.created_at, t.kind,
            t.origin_key, c.provider_json, s.signature_json,
            CASE WHEN json_valid(t.record_json)
                THEN json_extract(t.record_json, '$.spec.context.audience.scope') ELSE NULL END AS audience_scope_json
        FROM matched m
        JOIN model_calls c ON c.call_id = m.call_id
        JOIN model_call_cache_signatures s ON s.call_id = c.call_id
        JOIN model_requests r ON r.task_id = c.task_id JOIN execution_tasks t ON t.task_id = c.task_id
        ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at), c.position""",
        account_keys + (MAIN_CHAIN_KIND, origin_key))


def _legacy_cache_diagnostic_accumulator(runtime, role_accounts, all_days, origin_key):
    accumulator = CacheDiagnosticAccumulator()
    ordered_days = sorted(all_days)
    for offset in range(0, len(ordered_days), 30):
        batch_days = ordered_days[offset:offset + 30]
        if not batch_days:
            continue
        for row in _legacy_cache_rows(runtime, role_accounts, batch_days, origin_key):
            accumulator.add_row(row, allowed_days=all_days)
    return accumulator


def _cache_diagnostic_summary(runtime, role_accounts, all_days, origin_key, *, core_rows=()):
    accumulator = _legacy_cache_diagnostic_accumulator(runtime, role_accounts, all_days, origin_key)
    for row in sorted(core_rows, key=lambda item: (item["finished_at"] or item["started_at"] or item["created_at"], item["position"])):
        accumulator.add_row(row, allowed_days=all_days)
    return accumulator.project()


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

    def _route_after_save(self, profile, saved, inherited_saved, changes, reset):
        """The provider and fallback dispatch would use for the saved config."""
        group = profile.group
        fields = dict(saved.fields)
        for name, value in changes.items():
            key = group + "." + name
            if key in fields:
                fields[key] = replace(fields[key], value=value,
                                      source=saved.target, explicit=True)
        for name in reset:
            key = group + "." + name
            parent = inherited_saved.fields[key]
            fields[key] = replace(fields[key], value=parent.value,
                                  source=parent.source, explicit=False)
        snapshot = replace(saved, fields=fields)
        try:
            default = self.app.router.provider_default(self.context, profile, snapshot)
            routed = self.app.router.apply_provider_default(profile, snapshot, default)
        except TaskError:
            routed = snapshot
        return (routed.fields[group + ".provider"].value,
                routed.fields[group + ".fallback"].value)

    def _snapshots(self, operator, profile, layer=ConfigLayer.ROLE):
        now = self.app.now()
        parent = ConfigLayer.INSTALLATION if layer is ConfigLayer.ROLE else ConfigLayer.ROLE
        current = require(self.app.configuration.read(operator, target=_target(self.context, layer),
                                                      group_id=profile.group, observed_at=now))
        inherited = require(self.app.configuration.read(operator, target=_target(self.context, parent),
                                                        group_id=profile.group, observed_at=now))
        # Kept untouched so the editor can tell which stored values recovery
        # stopped using; the routed copies below are for display and dispatch.
        saved = current
        inherited_saved = inherited
        # Both views degrade to the saved values when the host cannot answer:
        # the page still opens and shows what is stored, while dispatch keeps
        # raising the same failure instead of silently running another model.
        try:
            provider_default = self.app.router.provider_default(self.context, profile, current)
            current = self.app.router.apply_provider_default(profile, current, provider_default)
        except TaskError:
            provider_default = None
        try:
            # The parent view answers a different question: what runs when this
            # layer's override is removed. Its own route decides that, not the
            # current layer's, which may be suppressed here but absent there.
            inherited_default = self.app.router.provider_default(self.context, profile, inherited)
            inherited = self.app.router.apply_provider_default(
                profile, inherited, inherited_default)
        except TaskError:
            inherited_default = None
        return current, inherited, provider_default, inherited_default, saved, inherited_saved

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

    def _effective_output_ceiling(self, fields):
        """The strictest output capability of every provider the route can reach.

        Routing may send one request to the primary provider, to the fallback,
        or to a replacement-rule target, so the ceiling shown here is computed
        with the same helper dispatch uses. The editor therefore displays the
        exact value a registry default will be clamped to at send time.
        """
        values = {name: fields[name]["value"] for name in FIELDS}
        return min(output_capabilities(values, self.app.router.providers),
                   default=MAX_MODEL_OUTPUT_TOKENS)

    def _project(self, operator, profile, layer=ConfigLayer.ROLE):
        current, inherited, provider_default, inherited_default, saved, inherited_saved = (
            self._snapshots(operator, profile, layer))
        fields = {name: _field(current.fields[profile.group + "." + name],
                               inherited.fields[profile.group + "." + name]) for name in FIELDS}
        # A registry default may exceed a provider's declared capability; routing
        # clamps it at dispatch time. Show the value that will actually be sent so
        # the editor and the maintenance check agree with the dispatched envelope.
        ceiling = self._effective_output_ceiling(fields)
        limit = fields["max_output_tokens"]
        if limit["source"] == "default":
            limit["value"] = min(limit["value"], ceiling)
        if limit["inherited_source"] == "default":
            limit["inherited_value"] = min(limit["inherited_value"], ceiling)
        # Each view names the host route only when its own layer would use it.
        # The parent view may resolve the host model while the current layer's
        # explicit route suppresses it, so the two are labelled separately.
        for name in ("provider", "fallback"):
            if name == "fallback" and profile.default_fallback:
                continue
            # A cleared field keeps "default": recovery may empty a fallback that
            # collided with the primary, and an empty fallback is not a route.
            if provider_default is not None and fields[name]["value"] and fields[name]["source"] == "default":
                fields[name]["source"] = "host_conversation"
            if (inherited_default is not None and fields[name]["inherited_value"]
                    and fields[name]["inherited_source"] == "default"):
                fields[name]["inherited_source"] = "host_conversation"
        # Recovery can stop using a value *this layer* stores (a fallback that
        # collided with the recovered primary). Only an explicit row qualifies:
        # a value inherited from the installation layer is recovered in place and
        # still follows the parent, so marking it would make the editor treat the
        # recovered host route as a local choice and write it back on save.
        # The parent view applies recovery for the parent's own route. When this
        # layer resets its fallback, the save inherits the parent's stored row
        # instead, so the editor needs that value to match what will run.
        parent_fallback = inherited_saved.fields[profile.group + ".fallback"]
        # Only a real row is a stored value; without one the field carries the
        # registry default, while dispatch resolves the host route instead.
        stored_provider = (self.app.router.providers.get(parent_fallback.value)
                           if parent_fallback.source is not None and parent_fallback.value
                           else None)
        fields["fallback"]["parent_stored"] = (
            parent_fallback.value if parent_fallback.source is not None else "")
        fields["fallback"]["parent_stored_usable"] = bool(
            stored_provider is not None
            and set(profile.capabilities).issubset(stored_provider.capabilities))
        for name in ("fallback",):
            raw = saved.fields[profile.group + "." + name]
            routed = current.fields[profile.group + "." + name]
            # Only an emptied field is "switched off": recovery may instead
            # substitute a different, usable fallback (the profile's declared
            # default), which is still in force and must not read as disabled.
            if raw.explicit and routed.source is None and not routed.value and raw.value:
                fields[name]["cleared"] = True
            # A substituted value hides the stored row just as well: name it so
            # the operator can see what is saved and remove it.
            if raw.explicit and routed.source is None and routed.value and raw.value != routed.value:
                fields[name]["replaced"] = True
                fields[name]["stored_value"] = raw.value
            # An emptied field has no route, whoever saved the row. The editor
            # needs that even when the row is inherited: otherwise its fallback
            # chain falls through to the primary and reports a fallback that
            # dispatch will never try.
            if routed.source is None and not routed.value and raw.value:
                fields[name]["emptied"] = True
                fields[name]["stored_value"] = raw.value
                fields[name]["recovered_value"] = (
                    profile.default_fallback or provider_default or profile.default_provider)
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
                                   "supports_video_relay": provider.supports_video_relay,
                                   "model": provider.model}
                                   for provider in self.app.router.providers.values()],
                    "host_state": self.app.state}

    @outcome
    def maintenance(self, operator, *, provider_metadata=None, pricing_revision=None):
        """Read role-scoped model usage and diagnostics without exposing prompt material."""
        runtime = self.app.runtime
        active_write = has_active_write_transaction(runtime)
        shared_token_before = None if active_write else read_shared_db_token(runtime)
        promote_ctx = None
        disk_save_ctx = None
        pre_cache = None
        preloaded_hist = None
        pre_pricing_file_key = None
        pre_pricing_settings = None
        pre_pricing_catalog = None
        pre_pricing_key = None
        if not active_write and shared_token_before is not None:
            pre_cache = runtime_maintenance_cache(runtime)
            pre_gen = shared_token_before.generation
            if pre_cache is not None:
                pre_cache.sync_generation(runtime, pre_gen)
            if pre_cache is not None and _core_observations_available(runtime):
                pre_now = self.app.now()
                pre_eff_now = pre_now.replace(tzinfo=timezone.utc) if pre_now.tzinfo is None else pre_now
                pre_eff_start = (pre_eff_now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
                pre_today = pre_now.date().isoformat()
                pre_tz = int((pre_eff_now.utcoffset() or timedelta(0)).total_seconds())
                pre_role_key = _core_role_key(self.context)
                pre_pricing_file_key = pricing_fingerprint(runtime, pricing_revision)
                pre_pricing_settings = PricingSettingsStore(runtime.data_root).read_safe()
                pre_pricing = pricing_summary(pre_pricing_settings, now=pre_now)
                pre_models, pre_clusters, pre_own_sources = _resolve_provider_pricing_meta(
                    self.app.router.providers.values(), provider_metadata
                )
                pre_pricing_key = (
                    pre_pricing_file_key, pre_pricing["revision"], tuple(sorted(pre_models.items())),
                    tuple(sorted(pre_clusters.items())), tuple(sorted(pre_own_sources.items())),
                )
                pre_cache_ver = (pre_pricing_key, pre_gen)
                if pre_cache.needs_core_history_prewarm(
                    role_key_value=pre_role_key,
                    history_cutoff=pre_today,
                    tz_offset_seconds=pre_tz,
                    cache_version=pre_cache_ver,
                    raw_cache_version=pre_gen,
                ):
                    self._authorize(operator)
                    pre_pricing_catalog = PriceCatalogStore(runtime.data_root).read_safe(copy=False)
                    _, _, _, pre_enrich_core_rows = _make_pricing_resolver(
                        pre_pricing_settings,
                        pre_pricing_catalog,
                        pre_models,
                        pre_clusters,
                        pre_own_sources,
                    )
                    preloaded_hist = pre_cache.prewarm_core_history(
                        runtime,
                        role_key_value=pre_role_key,
                        history_cutoff=pre_today,
                        tz_offset_seconds=pre_tz,
                        pricing_key=pre_pricing_key,
                        enrich_core_rows=pre_enrich_core_rows,
                        eff_start=pre_eff_start,
                        eff_now=pre_eff_now,
                        core_available=True,
                        expected_generation=pre_gen,
                    )
                    shared_token_before = read_shared_db_token(runtime)
                    if shared_token_before is None or shared_token_before.generation != pre_gen:
                        if shared_token_before is not None:
                            pre_cache.sync_generation(runtime, shared_token_before.generation)
                        preloaded_hist = None
                    elif preloaded_hist is not None and pricing_fingerprint(runtime, pricing_revision) != pre_pricing_file_key:
                        preloaded_hist = None
        with runtime.read_snapshot():
            self._authorize(operator)
            now = self.app.now()
            eff_now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
            eff_start = (eff_now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            today = now.date().isoformat()
            tz_offset_seconds = int((eff_now.utcoffset() or timedelta(0)).total_seconds())
            policy = self.app.gateway.ledger.policy
            soft_fallback = self.app.gateway.ledger.soft
            providers = [{"id": item.binding.provider, "model": item.model}
                         for item in self.app.router.providers.values()]
            runtime_state_key = (
                policy.enabled,
                policy.role.tokens,
                getattr(policy.role, "cost_microusd", None),
                soft_fallback,
                tuple((item["id"], item["model"]) for item in providers),
            )
            role_key_value = _core_role_key(self.context)
            core_available = _core_observations_available(runtime)
            file_key = pricing_fingerprint(runtime, pricing_revision)
            if pre_pricing_settings is not None and file_key == pre_pricing_file_key:
                pricing_settings = pre_pricing_settings
            else:
                pricing_settings = PricingSettingsStore(runtime.data_root).read_safe()
                preloaded_hist = None
                pre_pricing_catalog = None
            pricing = pricing_summary(pricing_settings, now=now)
            provider_models, provider_clusters, provider_own_sources = _resolve_provider_pricing_meta(
                self.app.router.providers.values(), provider_metadata
            )

            pricing_key = (
                file_key, pricing["revision"], tuple(sorted(provider_models.items())),
                tuple(sorted(provider_clusters.items())), tuple(sorted(provider_own_sources.items())),
            )
            cache = None
            disk_clear_epoch = None
            snapshot_token = read_shared_db_token(runtime) if shared_token_before is not None else None
            if snapshot_token is not None and shared_token_before is not None and snapshot_token.generation != shared_token_before.generation:
                if pre_cache is not None:
                    pre_cache.sync_generation(runtime, snapshot_token.generation)
                preloaded_hist = None
            elif (snapshot_token is not None and shared_token_before is not None
                    and snapshot_token.generation == shared_token_before.generation
                    and pricing_fingerprint(runtime, pricing_revision) == file_key):
                cache = runtime_maintenance_cache(runtime)
                if cache is not None:
                    disk_clear_epoch = cache.clear_epoch()
            precomputed = {}
            if cache is not None:
                cache_version = (pricing_key, shared_token_before.generation)
                scope_key = (role_key_value, self.context.key, today, tz_offset_seconds, cache_version)
                cached_payload, precomputed = cache.lookup_snapshot(
                    runtime,
                    self.context,
                    scope_key=scope_key,
                    runtime_state_key=runtime_state_key,
                    now=now,
                    eff_now=eff_now,
                    role_key_value=role_key_value,
                    context_key=self.context.key,
                    history_cutoff=today,
                    core_available=core_available,
                    soft_fallback=soft_fallback,
                    shared_token_before=shared_token_before,
                )
                if cached_payload is not None:
                    cached_payload["pricing"] = pricing
                    return cached_payload

            runtime_diagnostics = self._runtime_diagnostics()
            scope = self.context.scope
            role_address = [scope.installation_id, scope.bot_id, asdict(self.context.role_binding.role)]
            role_address_key = digest(role_address)
            pricing_catalog = (
                pre_pricing_catalog
                if pre_pricing_catalog is not None
                else PriceCatalogStore(runtime.data_root).read_safe(copy=False)
            )
            if cache is not None and pricing_fingerprint(runtime, pricing_revision) != file_key:
                cache = None
            usd_cny_rate, model_for, resolve_for_call, enrich_core_rows = _make_pricing_resolver(
                pricing_settings,
                pricing_catalog,
                provider_models,
                provider_clusters,
                provider_own_sources,
            )

            base_rule_cache = {}
            complex_cache = {}

            def base_rule_for(provider_id):
                pid = provider_id if type(provider_id) is str else ""
                if pid not in base_rule_cache:
                    base_rule_cache[pid] = resolve_for_call(pid, model_for(pid), None)
                return base_rule_cache[pid]

            def is_complex_provider(provider_id):
                pid = provider_id if type(provider_id) is str else ""
                if pid not in complex_cache:
                    complex_cache[pid] = provider_needs_per_call_repricing(
                        pid,
                        model_for(pid),
                        pricing_settings,
                        catalog=pricing_catalog,
                        cluster_id=provider_clusters.get(pid),
                        own_source_ids=provider_own_sources.get(pid, frozenset()),
                    )
                return complex_cache[pid]

            def enrich_aggregate(row, per_call_override=None):
                data = {key: row[key] for key in row.keys()}
                rec_calls = int(data.get("recorded_cost_calls") or 0)
                rec_cost = int(data.get("recorded_cost_microusd") or 0)
                unpriced = int(data.get("unpriced_calls") or 0)
                est_calls = 0
                est_cost = 0
                remaining_unpriced = 0
                if unpriced > 0:
                    if per_call_override is not None:
                        est_calls, est_cost, remaining_unpriced = per_call_override
                    else:
                        pid = data.get("provider") if type(data.get("provider")) is str else ""
                        eff = base_rule_for(pid)
                        if eff is not None:
                            if eff.mode == "per_turn":
                                computed = evaluate_call_cost_microusd(
                                    eff, usd_cny_rate=usd_cny_rate, calls=unpriced,
                                )
                                if computed is not None:
                                    est_cost = computed
                                    est_calls = unpriced
                                else:
                                    remaining_unpriced = unpriced
                            elif eff.mode == "per_request":
                                req_count = int(data.get("unpriced_requests") or 0)
                                computed = evaluate_call_cost_microusd(
                                    eff, usd_cny_rate=usd_cny_rate, requests=req_count,
                                )
                                if computed is not None:
                                    est_cost = computed
                                    est_calls = unpriced
                                else:
                                    remaining_unpriced = unpriced
                            else:
                                u_in = int(data.get("unpriced_input_tokens") or 0)
                                u_cached = int(data.get("unpriced_cached_input_tokens") or 0)
                                u_create = int(data.get("unpriced_cache_creation_tokens") or 0)
                                u_out = int(data.get("unpriced_output_tokens") or 0)
                                u_flat = int(data.get("unpriced_flat_tokens") or 0)
                                split_cost = (
                                    evaluate_call_cost_microusd(
                                        eff,
                                        usd_cny_rate=usd_cny_rate,
                                        input_tokens=u_in,
                                        cached_input_tokens=u_cached,
                                        cache_creation_tokens=u_create,
                                        output_tokens=u_out,
                                    ) or 0
                                ) if (u_in or u_out) else 0
                                flat_cost = (
                                    evaluate_call_cost_microusd(
                                        eff,
                                        usd_cny_rate=usd_cny_rate,
                                        total_tokens=u_flat,
                                    ) or 0
                                ) if u_flat else 0
                                est_cost = split_cost + flat_cost
                                est_calls = unpriced
                        else:
                            remaining_unpriced = unpriced
                data["recorded_cost_calls"] = rec_calls
                data["recorded_cost_microusd"] = rec_cost
                data["estimated_cost_calls"] = est_calls
                data["estimated_cost_microusd"] = est_cost
                data["unpriced_token_calls"] = remaining_unpriced
                data["cost_microusd"] = rec_cost + est_cost
                return data

            def enrich_recent_call(row):
                data = {key: row[key] for key in row.keys()}
                try:
                    provider_record = json.loads(data["provider_json"])
                except (TypeError, ValueError):
                    provider_record = None
                provider_id = provider_record.get("provider", "") if isinstance(provider_record, dict) else ""
                call_model = provider_record.get("model", "") if isinstance(provider_record, dict) else ""
                if type(provider_id) is not str:
                    provider_id = ""
                if type(call_model) is not str:
                    call_model = ""
                actual_cost = data.get("actual_cost")
                if actual_cost is not None and actual_cost > 0:
                    data["cost_basis"] = "recorded"
                    data["cost_availability"] = "recorded"
                    return data
                if data.get("actual_tokens") is not None:
                    call_ts = data.get("finished_at") or data.get("started_at") or data.get("created_at")
                    cmodel = model_for(provider_id, call_model)
                    eff = resolve_for_call(provider_id, cmodel, call_ts)
                    if eff is not None:
                        est = evaluate_call_cost_microusd(
                            eff,
                            usd_cny_rate=usd_cny_rate,
                            input_tokens=data.get("input_tokens"),
                            cached_input_tokens=data.get("cached_input_tokens"),
                            cache_creation_tokens=data.get("cache_creation_tokens"),
                            output_tokens=data.get("output_tokens"),
                            total_tokens=data.get("actual_tokens"),
                            model=cmodel,
                            calls=1,
                            requests=1,
                            strict_expr=False,
                        )
                        if est is not None:
                            data["actual_cost"] = est
                            data["estimated_cost_microusd"] = est
                            data["cost_basis"] = "estimated_local"
                            data["cost_availability"] = "estimated_local"
                            return data
                data["actual_cost"] = None
                data["cost_basis"] = "unconfigured"
                data["cost_availability"] = "unconfigured"
                return data

            if cache is not None:
                core_state = cache.load_partitioned_core(
                    runtime,
                    self.context,
                    role_key_value=role_key_value,
                    history_cutoff=today,
                    tz_offset_seconds=tz_offset_seconds,
                    cache_version=cache_version,
                    raw_cache_version=shared_token_before.generation,
                    enrich_core_rows=enrich_core_rows,
                    eff_start=eff_start,
                    eff_now=eff_now,
                    core_available=core_available,
                    hist_watermark=precomputed.get("hist_watermark"),
                    can_cache=precomputed.get("can_cache", True),
                    shared_token_before=shared_token_before,
                    preloaded_hist=preloaded_hist if pre_pricing_key == pricing_key else None,
                )
                if core_state.disk_hist_entry is not None and not active_write:
                    disk_save_ctx = (
                        role_key_value,
                        today,
                        tz_offset_seconds,
                        pricing_key,
                        eff_start,
                        core_state.disk_hist_entry,
                        shared_token_before.generation,
                        disk_clear_epoch,
                    )
            else:
                core_state = None

            legacy_entry = None
            legacy_watermark = None
            if cache is not None and core_state is not None:
                legacy_key = (role_address_key, self.context.key, cache_version)
                legacy_token = precomputed.get("db_write_token") or shared_token_before
                legacy_entry = cache.lookup_legacy_fast(
                    legacy_key,
                    db_write_token=legacy_token,
                    core_days=core_state.core_days,
                    today=today,
                )
                if legacy_entry is not None:
                    legacy_watermark = legacy_entry.legacy_watermark
                    precomputed["legacy_watermark"] = legacy_watermark
                else:
                    legacy_watermark = precomputed.get("legacy_watermark")
                    if legacy_watermark is None:
                        legacy_watermark = read_legacy_watermark(runtime)
                        precomputed["legacy_watermark"] = legacy_watermark
                    legacy_entry = cache.lookup_legacy(
                        legacy_key,
                        legacy_watermark=legacy_watermark,
                        core_days=core_state.core_days,
                        today=today,
                        db_write_token=legacy_token,
                    )

            if legacy_entry is not None and core_state is not None:
                all_days = set(legacy_entry.all_days)
                role_accounts = {period: digest(["role", role_address, period]) for period in all_days}
                reports = clone_legacy_reports(legacy_entry)
                legacy_cache_diag = legacy_entry.cache_diag
                core_observations = None
                core_rows = None
            else:
                candidate_days = runtime.read_all("""SELECT DISTINCT
                        substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day
                    FROM model_calls c JOIN model_requests r ON r.task_id = c.task_id
                    ORDER BY day""", ())
                valid_candidate_days = set()
                for item in candidate_days:
                    value = item["day"]
                    try:
                        parsed = date.fromisoformat(value)
                    except (TypeError, ValueError):
                        continue
                    if parsed.isoformat() == value:
                        valid_candidate_days.add(value)
                all_days = {today} | valid_candidate_days
                if core_state is not None:
                    all_days.update(core_state.core_days)
                    core_observations = None
                    core_rows = None
                else:
                    obs_with_scope = observations_with_scope_for_role(runtime, self.context, end=now.isoformat())
                    core_observations = [obs for obs, _scope_kind, _row_dt in obs_with_scope]
                    core_rows = request_rows(core_observations, include_signature_json=False)
                    all_days.update(row["day"] for row in core_rows)
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
                    matched_cte = _legacy_matched_calls_cte(placeholders)
                    grouped_rows = runtime.read_all(f"""WITH {matched_cte}
                        SELECT
                            substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day,
                            CASE WHEN json_valid(c.provider_json)
                                THEN COALESCE(json_extract(c.provider_json, '$.provider'), '') ELSE '' END AS provider,
                            t.kind AS kind,
                            COUNT(*) AS calls,
                            SUM(COALESCE(c.actual_tokens, 0)) AS tokens,
                            SUM(COALESCE(c.actual_cost, 0)) AS cost_microusd,
                            SUM(CASE WHEN c.actual_cost IS NOT NULL AND c.actual_cost > 0 THEN c.actual_cost ELSE 0 END)
                                AS recorded_cost_microusd,
                            SUM(CASE WHEN c.actual_cost IS NOT NULL AND c.actual_cost > 0 THEN 1 ELSE 0 END)
                                AS recorded_cost_calls,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL THEN 1 ELSE 0 END)
                                AS unpriced_calls,
                            COUNT(DISTINCT CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN c.task_id END) AS unpriced_requests,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.input_tokens, 0) ELSE 0 END) AS unpriced_input_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.cached_input_tokens, 0) ELSE 0 END) AS unpriced_cached_input_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.cache_creation_tokens, 0) ELSE 0 END) AS unpriced_cache_creation_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.output_tokens, 0) ELSE 0 END) AS unpriced_output_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                AND c.input_tokens IS NULL AND c.output_tokens IS NULL THEN c.actual_tokens ELSE 0 END)
                                AS unpriced_flat_tokens,
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
                        FROM matched m
                        JOIN model_calls c ON c.call_id = m.call_id
                        JOIN model_requests r ON r.task_id = c.task_id
                        JOIN execution_tasks t ON t.task_id = c.task_id
                        LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                        GROUP BY day, provider, kind""", account_keys + (MAIN_CHAIN_KIND, self.context.key))

                    # Switch to per-call timestamp-aware repricing ONLY for providers with
                    # per_tier, tiered_expr, or active pricing_schedules!
                    complex_providers = sorted({
                        str(row["provider"] or "")
                        for row in grouped_rows
                        if int(row["unpriced_calls"] or 0) > 0 and is_complex_provider(row["provider"])
                    })
                    per_call_group = {}
                    per_call_hour = {}
                    if complex_providers:
                        cp_placeholders = ",".join("?" for _ in complex_providers)
                        complex_rows = runtime.read_all(f"""SELECT
                                substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day,
                                substr(COALESCE(c.finished_at, c.started_at, r.created_at), 12, 2) AS hour,
                                COALESCE(c.finished_at, c.started_at, r.created_at) AS at_ts,
                                CASE WHEN json_valid(c.provider_json)
                                    THEN COALESCE(json_extract(c.provider_json, '$.provider'), '') ELSE '' END AS provider,
                                CASE WHEN json_valid(c.provider_json)
                                    THEN COALESCE(json_extract(c.provider_json, '$.model'), '') ELSE '' END AS call_model,
                                t.kind AS kind,
                                c.task_id AS task_id,
                                c.actual_tokens, c.input_tokens, c.cached_input_tokens,
                                c.cache_creation_tokens, c.output_tokens
                            FROM model_calls c
                            JOIN model_requests r ON r.task_id = c.task_id
                            JOIN execution_tasks t ON t.task_id = c.task_id
                            WHERE (EXISTS (SELECT 1 FROM model_call_accounts ca
                                           WHERE ca.call_id = c.call_id AND ca.account_key IN ({placeholders}))
                                   OR (t.kind = ? AND t.origin_key = ?))
                              AND (c.actual_cost IS NULL OR c.actual_cost = 0)
                              AND c.actual_tokens IS NOT NULL
                              AND (CASE WHEN json_valid(c.provider_json)
                                   THEN COALESCE(json_extract(c.provider_json, '$.provider'), '') ELSE '' END)
                                  IN ({cp_placeholders})""",
                            account_keys + (MAIN_CHAIN_KIND, self.context.key) + tuple(complex_providers))
                        seen_group_tasks = set()
                        seen_hour_tasks = set()
                        for crow in complex_rows:
                            pid = str(crow["provider"] or "")
                            cmodel = model_for(pid, crow["call_model"])
                            eff = resolve_for_call(pid, cmodel, crow["at_ts"])
                            gkey = (crow["day"], pid, crow["kind"])
                            hkey = (crow["day"], crow["hour"], pid, crow["kind"])
                            g_task_key = (gkey, crow["task_id"])
                            h_task_key = (hkey, crow["task_id"])
                            g_reqs = 0 if g_task_key in seen_group_tasks else 1
                            h_reqs = 0 if h_task_key in seen_hour_tasks else 1
                            seen_group_tasks.add(g_task_key)
                            seen_hour_tasks.add(h_task_key)
                            g_cost = evaluate_call_cost_microusd(
                                eff,
                                usd_cny_rate=usd_cny_rate,
                                input_tokens=crow["input_tokens"],
                                cached_input_tokens=crow["cached_input_tokens"],
                                cache_creation_tokens=crow["cache_creation_tokens"],
                                output_tokens=crow["output_tokens"],
                                total_tokens=crow["actual_tokens"],
                                model=cmodel,
                                calls=1,
                                requests=g_reqs,
                                strict_expr=False,
                            )
                            h_cost = (
                                g_cost
                                if g_reqs == h_reqs
                                else evaluate_call_cost_microusd(
                                    eff,
                                    usd_cny_rate=usd_cny_rate,
                                    input_tokens=crow["input_tokens"],
                                    cached_input_tokens=crow["cached_input_tokens"],
                                    cache_creation_tokens=crow["cache_creation_tokens"],
                                    output_tokens=crow["output_tokens"],
                                    total_tokens=crow["actual_tokens"],
                                    model=cmodel,
                                    calls=1,
                                    requests=h_reqs,
                                    strict_expr=False,
                                )
                            )
                            ge, gc, gu = per_call_group.get(gkey, (0, 0, 0))
                            if g_cost is not None:
                                per_call_group[gkey] = (ge + 1, gc + g_cost, gu)
                            else:
                                per_call_group[gkey] = (ge, gc, gu + 1)
                            he, hc, hu = per_call_hour.get(hkey, (0, 0, 0))
                            if h_cost is not None:
                                per_call_hour[hkey] = (he + 1, hc + h_cost, hu)
                            else:
                                per_call_hour[hkey] = (he, hc, hu + 1)

                    for row in grouped_rows:
                        gkey = (row["day"], row["provider"], row["kind"])
                        override = per_call_group.get(gkey) if is_complex_provider(row["provider"]) else None
                        enriched = enrich_aggregate(row, per_call_override=override)
                        for key in report_keys(row["kind"]):
                            reports[key].add_group(enriched)
                    hour_rows = runtime.read_all(f"""WITH {matched_cte}
                        SELECT
                            substr(COALESCE(c.finished_at, c.started_at, r.created_at), 1, 10) AS day,
                            substr(COALESCE(c.finished_at, c.started_at, r.created_at), 12, 2) AS hour,
                            CASE WHEN json_valid(c.provider_json)
                                THEN COALESCE(json_extract(c.provider_json, '$.provider'), '') ELSE '' END AS provider,
                            t.kind AS kind,
                            COUNT(*) AS calls, SUM(COALESCE(c.actual_tokens, 0)) AS tokens,
                            SUM(COALESCE(c.actual_cost, 0)) AS cost_microusd,
                            SUM(CASE WHEN c.actual_cost IS NOT NULL AND c.actual_cost > 0 THEN c.actual_cost ELSE 0 END)
                                AS recorded_cost_microusd,
                            SUM(CASE WHEN c.actual_cost IS NOT NULL AND c.actual_cost > 0 THEN 1 ELSE 0 END)
                                AS recorded_cost_calls,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL THEN 1 ELSE 0 END)
                                AS unpriced_calls,
                            COUNT(DISTINCT CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN c.task_id END) AS unpriced_requests,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.input_tokens, 0) ELSE 0 END) AS unpriced_input_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.cached_input_tokens, 0) ELSE 0 END) AS unpriced_cached_input_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.cache_creation_tokens, 0) ELSE 0 END) AS unpriced_cache_creation_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                THEN COALESCE(c.output_tokens, 0) ELSE 0 END) AS unpriced_output_tokens,
                            SUM(CASE WHEN (c.actual_cost IS NULL OR c.actual_cost = 0) AND c.actual_tokens IS NOT NULL
                                AND c.input_tokens IS NULL AND c.output_tokens IS NULL THEN c.actual_tokens ELSE 0 END)
                                AS unpriced_flat_tokens
                        FROM matched m
                        JOIN model_calls c ON c.call_id = m.call_id
                        JOIN model_requests r ON r.task_id = c.task_id
                        JOIN execution_tasks t ON t.task_id = c.task_id
                        GROUP BY day, hour, provider, kind ORDER BY day, hour""",
                        account_keys + (MAIN_CHAIN_KIND, self.context.key))
                    for row in hour_rows:
                        hkey = (row["day"], row["hour"], row["provider"], row["kind"])
                        override = per_call_hour.get(hkey) if is_complex_provider(row["provider"]) else None
                        enriched_hour = enrich_aggregate(row, per_call_override=override)
                        for key in report_keys(row["kind"]):
                            reports[key].add_hour(enriched_hour)
                    recent_rows = runtime.read_all(f"""WITH {matched_cte}, samples AS (
                        SELECT c.state, c.position, c.actual_tokens, c.reserved_tokens,
                            c.receipt_ref,
                            c.actual_cost, c.input_tokens, c.cached_input_tokens, c.cache_creation_tokens, c.output_tokens,
                            c.latency_ms, c.finished_at, c.started_at, r.created_at, t.kind, c.provider_json,
                            f.code AS failure_code, d.summary_json AS response_summary_json,
                            ROW_NUMBER() OVER (PARTITION BY CASE WHEN t.kind = 'conversation.reply'
                                THEN 'conversation' ELSE 'plugin' END
                                ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at) DESC, c.position DESC)
                                AS source_position
                        FROM matched m
                        JOIN model_calls c ON c.call_id = m.call_id
                        JOIN model_requests r ON r.task_id = c.task_id
                        JOIN execution_tasks t ON t.task_id = c.task_id
                        LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                        LEFT JOIN model_call_diagnostics d ON d.call_id = c.call_id)
                         SELECT * FROM samples WHERE source_position <= 80""",
                        account_keys + (MAIN_CHAIN_KIND, self.context.key))
                    for row in recent_rows:
                        enriched_recent = enrich_recent_call(row)
                        for key in report_keys(row["kind"]):
                            reports[key].add_recent(enriched_recent)
                legacy_cache_diag = _legacy_cache_diagnostic_accumulator(
                    runtime, role_accounts, all_days, self.context.key
                )
                if (
                    cache is not None
                    and legacy_watermark is not None
                    and precomputed.get("can_cache", True)
                    and not has_active_write_transaction(runtime)
                    and (
                        shared_token_before is None
                        or read_shared_db_token(runtime) == shared_token_before
                        or has_active_read_snapshot(runtime)
                    )
                ):
                    stable_legacy_token = (
                        shared_token_before
                        if (shared_token_before is not None and read_shared_db_token(runtime) == shared_token_before)
                        else None
                    )
                    cache.store_legacy(
                        legacy_key,
                        build_legacy_entry(
                            legacy_watermark=legacy_watermark,
                            all_days=frozenset(all_days),
                            candidate_days=frozenset(valid_candidate_days),
                            reports=reports,
                            cache_diag=legacy_cache_diag,
                            db_write_token=stable_legacy_token,
                        ),
                    )

            if core_state is not None:
                for key in CORE_USAGE_KEYS:
                    merge_usage_report(reports[key], core_state.usage_report)
                if not legacy_cache_diag.saw_core_kind:
                    cache_reports = merge_cache_diagnostics(legacy_cache_diag, core_state.cache_diag)
                else:
                    fallback_rows = core_state.raw_core_rows
                    if fallback_rows is None:
                        fallback_obs = [
                            obs for obs, _scope_kind, _row_dt in
                            observations_with_scope_for_role(runtime, self.context, end=now.isoformat())
                        ]
                        fallback_rows = request_rows(fallback_obs, include_signature_json=False)
                    cache_reports = _cache_diagnostic_summary(
                        runtime, role_accounts, all_days, self.context.key, core_rows=fallback_rows
                    )
                core_coverage = core_state.availability
                has_core_observations = core_state.has_observations
                core_7d = core_state.private_7d
            else:
                for row, cost_meta, recent in enrich_core_rows(core_rows):
                    for key in CORE_USAGE_KEYS:
                        reports[key].add_group({**row, **row["aggregate"], **cost_meta})
                        reports[key].add_hour({**row, **cost_meta})
                        reports[key].add_recent(recent)
                cache_reports = _cache_diagnostic_summary(
                    runtime, role_accounts, all_days, self.context.key, core_rows=core_rows
                )
                core_coverage = availability_summary(core_observations)
                has_core_observations = bool(core_observations)
                core_7d = [
                    obs for obs, scope_kind, row_dt in obs_with_scope
                    if scope_kind == "private"
                    and (parsed_dt := (row_dt if row_dt is not None else _parse_utc_timestamp(obs.get("received_at")))) is not None
                    and eff_start <= parsed_dt <= eff_now
                ]

            usage_reports = {key: {**report.project(now), "cache_diagnostics": cache_reports[key]}
                             for key, report in reports.items()}
            core_meta = reports["astrbot_core"]._cost_totals()
            core_priced = core_meta["recorded_cost_calls"] + core_meta["estimated_cost_calls"]
            core_unpriced = core_meta["unpriced_token_calls"]
            core_cost_avail = ("unavailable" if core_priced == 0
                               else "partial_estimated" if core_unpriced > 0
                               else "estimated_local")
            usage_reports["astrbot_core"].update(execution_path="astrbot_core",
                coverage=core_coverage,
                measurement_basis={"usage": "observed_provider_responses_with_explicit_fallback_scope",
                                   "cache": "reported_fields_only", "cost": core_cost_avail,
                                   "signature": "runner_pre_provider_or_provider_request_hook"})
            if core_cost_avail == "unavailable":
                _unavailable_core_costs(usage_reports["astrbot_core"])
            usage_reports["astrbot_core"]["cost_availability"] = core_cost_avail
            for key in ("all", "conversation", "plugin", "legacy_conversation"):
                meta = reports[key]._cost_totals()
                priced = meta["recorded_cost_calls"] + meta["estimated_cost_calls"]
                unpriced = meta["unpriced_token_calls"]
                if key in ("all", "conversation") and has_core_observations and core_cost_avail == "unavailable":
                    avail = "partial"
                elif priced == 0:
                    avail = "unconfigured"
                elif unpriced > 0:
                    avail = "partial_estimated" if meta["estimated_cost_calls"] > 0 else "partial"
                elif meta["estimated_cost_calls"] > 0:
                    avail = "estimated_local"
                else:
                    avail = "local_priced_subtotal"
                usage_reports[key]["cost_availability"] = avail
                if key in ("all", "conversation"):
                    usage_reports[key]["core_cost_availability"] = core_cost_avail
            usage_reports["legacy_conversation"]["execution_path"] = "legacy_v2_conversation"
            usage_reports["plugin"]["execution_path"] = "v2_background"
            today_account = role_accounts[today]
            budget_row = runtime.read_one(
                "SELECT token_limit, held_tokens, spent_tokens FROM model_budget_accounts WHERE account_key = ?",
                (today_account,))
            plugin_usage = self.app.gateway.ledger.plugin_account_usage(today_account)
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
            soft = effective_soft(runtime, self.context, soft_fallback)
            budget.update({"soft_limit_enabled": bool(soft and soft.enabled),
                           "soft_limit_tokens": soft.tokens if soft else 0})
            payload = {"generated_at": now.isoformat(), **usage_reports["all"],
                       "usage_sources": {key: usage_reports[key] for key in ("conversation", "plugin", "astrbot_core", "legacy_conversation")},
                       "reply_efficiency": reply_efficiency_report(runtime, self.context, now, core_observations=core_7d),
                       "budget": budget, "providers": providers, "window_days": 30,
                       "pricing": pricing, "ledger_scope": "current_role", "runtime": runtime_diagnostics}
            if cache is not None and pricing_fingerprint(runtime, pricing_revision) == file_key:
                snapshot_entry = build_snapshot_entry(
                    runtime,
                    self.context,
                    precomputed=precomputed,
                    runtime_state_key=runtime_state_key,
                    role_key_value=role_key_value,
                    context_key=self.context.key,
                    history_cutoff=today,
                    core_available=core_available,
                    soft_fallback=soft_fallback,
                    eff_now=eff_now,
                    has_future_obs=core_state.has_future_obs if core_state is not None else False,
                    payload=payload,
                )
                if snapshot_entry is not None:
                    cache.store_snapshot(scope_key, snapshot_entry)
                    if snapshot_entry.db_write_token is None and not active_write:
                        promote_ctx = (
                            scope_key,
                            snapshot_entry,
                            role_key_value,
                            self.context.key,
                            today,
                            core_available,
                            soft_fallback,
                            eff_now,
                        )
        if disk_save_ctx is not None and not has_active_write_transaction(runtime):
            save_disk_core_history(
                runtime,
                role_key_value=disk_save_ctx[0],
                history_cutoff=disk_save_ctx[1],
                tz_offset_seconds=disk_save_ctx[2],
                pricing_key=disk_save_ctx[3],
                eff_start=disk_save_ctx[4],
                entry=disk_save_ctx[5],
                expected_generation=disk_save_ctx[6],
                expected_clear_epoch=disk_save_ctx[7],
            )
        if promote_ctx is not None and not has_active_write_transaction(runtime):
            cache.promote_snapshot_token(
                runtime,
                self.context,
                scope_key=promote_ctx[0],
                entry=promote_ctx[1],
                role_key_value=promote_ctx[2],
                context_key=promote_ctx[3],
                history_cutoff=promote_ctx[4],
                core_available=promote_ctx[5],
                soft_fallback=promote_ctx[6],
                eff_now=promote_ctx[7],
            )
        return payload

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

    def _changes(self, profile, data, current, inherited, saved=None, inherited_saved=None):
        saved = saved if saved is not None else current
        inherited_saved = inherited_saved if inherited_saved is not None else inherited
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
            # Validation uses _route_after_save; this value only feeds the type
            # check below, so it stays the parent's projection.
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
        # Routing treats a value saved on *any* configuration layer as the user's
        # contract (``source is not None``); ``explicit`` only marks the layer being
        # edited. Use the same basis here, including the value a reset inherits, so
        # an unrelated edit cannot save a task that dispatch would refuse later.
        limit_field = current.fields[profile.group + ".max_output_tokens"]
        if "max_output_tokens" in changes:
            limit_explicit = True
        elif "max_output_tokens" in reset:
            limit_explicit = inherited.fields[profile.group + ".max_output_tokens"].source is not None
        else:
            limit_explicit = limit_field.source is not None
        # Compare the route this save actually produces. Recovery, the profile's
        # declared fallback, host routes and stale layers all feed into it, so
        # simulate it instead of approximating those rules here.
        routed_provider, routed_fallback = self._route_after_save(
            profile, saved, inherited_saved, changes, reset)
        fallback_explicit = ("fallback" in changes or
                             ("fallback" not in reset
                              and saved.fields[profile.group + ".fallback"].explicit))
        # A provider reset writes no row of its own, so its inherited value is a
        # route, not this save's content. That is only safe while the reset also
        # removes any collision dispatch would see: at the window layer the
        # inherited primary is exactly what dispatch reads, so it stays checked.
        checking_provider = ("provider" not in reset or "fallback" in changes
                             or current.target.layer is ConfigLayer.WINDOW
                             # A reset only drops this layer's row. When the row
                             # it inherits is a real value, dispatch will use it,
                             # so a collision with the saved fallback is real.
                             or current.fields[profile.group + ".provider"].source is not None)
        if (checking_provider and fallback_explicit and routed_fallback
                and routed_fallback == routed_provider):
            raise TaskError("model_fallback_duplicate", "fallback must differ from the primary provider")
        # Check the route this save leaves behind, including a recovered row that
        # becomes live again: its capabilities and ceiling were never checked by
        # the projection, which had replaced or emptied it.
        selected = [routed_provider] + ([routed_fallback] if routed_fallback else [])
        for provider_id in selected:
            provider = self.app.router.provider_for(provider_id)
            if provider is None:
                raise TaskError("model_provider_unavailable", "the provider has no registered V2 adapter")
            if not provider_supports_task(profile.kind, profile.capabilities, provider):
                raise TaskError("model_capability_mismatch", "the provider lacks task capabilities")
            if limit_explicit and values["max_output_tokens"] > provider.maximum_output_tokens:
                # Only a provider ceiling can refuse a task limit, so the message names
                # the field, the provider and that ceiling; the rejected value stays out
                # of the text, and an undeclared provider never produces this error.
                # An inherited registry default is not refused here: routing clamps it
                # to the provider capability, so unrelated edits stay saveable.
                raise TaskError("model_output_limit",
                                f"max_output_tokens is limited to {provider.maximum_output_tokens}"
                                " by provider " + provider_id)
        try:
            rules = checked_rules(values["replacement_rules"])
            for rule in rules:
                self.app.router.provider_for(rule.provider_id)
            validate_targets(rules, self.app.router.providers, capabilities=profile.capabilities,
                             max_output_tokens=values["max_output_tokens"] if limit_explicit else None,
                             task_kind=profile.kind)
        except TaskError:
            raise
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
            current, inherited, _, _, saved, inherited_saved = self._snapshots(
                operator, profile, selected_layer)
            changes, reset = self._changes(profile, data, current, inherited, saved,
                                           inherited_saved)
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
