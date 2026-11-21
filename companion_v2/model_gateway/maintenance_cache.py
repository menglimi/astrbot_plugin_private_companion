"""Thread-safe watermark snapshot and immutable historical-day caches for maintenance."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from weakref import WeakKeyDictionary

from companion_v2.execution.models import digest
from .budget import RECLAIM_AUDIT_KEY, RELEASE_RECEIPT_PREFIX, effective_soft
from .cache_diagnostics import (
    checked_signature,
    checked_signature_record,
    event_severity,
    signature_changes,
    signature_snapshot,
)
from .core_observations import (
    _parse_utc_timestamp,
    available as core_observations_available,
    observations_with_scope_for_role,
    request_rows,
    role_key as core_role_key,
)
from .usage import CORE_CHAIN_KIND, MAIN_CHAIN_KIND, ModelUsageReport, usage_source


SNAPSHOT_CACHE_MAX_ENTRIES = 16
DAY_CACHE_MAX_ENTRIES = 16
LEGACY_CACHE_MAX_ENTRIES = 16

CACHE_DIAGNOSTIC_KEYS = ("all", "conversation", "plugin", "astrbot_core", "legacy_conversation")
CORE_USAGE_KEYS = ("all", "conversation", "astrbot_core")
LEGACY_USAGE_KEYS = ("all", "conversation", "plugin", "legacy_conversation")
CHANGE_FIELDS = {
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
SCOPE_FIELDS = (
    "installation_id",
    "platform",
    "kind",
    "native_scope_id",
    "bot_id",
    "account_id",
    "conversation_id",
    "session_id",
)
_IMMUTABLES = frozenset({str, int, float, bool, type(None), bytes})
_RECENT_DROP_KEYS = frozenset({"signature", "signature_json", "audience_scope_json", "aggregate"})
_AVAILABILITY_DEFAULTS = {
    "cost_reported_requests": 0,
    "cost_availability": "unavailable",
    "request_count_basis": "observed_provider_attempts_or_explicit_last_response_fallback",
    "platform_delivery_confirmation": "unavailable",
}


class _PreparedPayload:
    """Immutable private snapshot indexed by flat leaf containers for sub-3ms deep cloning."""

    __slots__ = ("root", "leaf_dicts", "leaf_dict_lists", "scalar_lists")

    def __init__(self, payload: dict) -> None:
        leaf_dicts: set[int] = set()
        leaf_dict_lists: set[int] = set()
        scalar_lists: set[int] = set()
        self.root = self._prepare(payload, leaf_dicts, leaf_dict_lists, scalar_lists)
        self.leaf_dicts = frozenset(leaf_dicts)
        self.leaf_dict_lists = frozenset(leaf_dict_lists)
        self.scalar_lists = frozenset(scalar_lists)

    @classmethod
    def _prepare(cls, value, leaf_dicts: set[int], leaf_dict_lists: set[int], scalar_lists: set[int]):
        value_type = type(value)
        if value_type is dict:
            out = value.copy()
            is_leaf = True
            for key, item in out.items():
                if type(item) not in _IMMUTABLES:
                    is_leaf = False
                    out[key] = cls._prepare(item, leaf_dicts, leaf_dict_lists, scalar_lists)
            if is_leaf:
                leaf_dicts.add(id(out))
            return out
        if value_type is list:
            out = [
                item if type(item) in _IMMUTABLES else cls._prepare(item, leaf_dicts, leaf_dict_lists, scalar_lists)
                for item in value
            ]
            if all(type(item) in _IMMUTABLES for item in out):
                scalar_lists.add(id(out))
            elif all(type(item) is dict and id(item) in leaf_dicts for item in out):
                leaf_dict_lists.add(id(out))
            return out
        if value_type is tuple:
            return tuple(
                item if type(item) in _IMMUTABLES else cls._prepare(item, leaf_dicts, leaf_dict_lists, scalar_lists)
                for item in value
            )
        if value_type in _IMMUTABLES:
            return value
        return deepcopy(value)

    def clone(self) -> dict:
        return self._clone(self.root, self.leaf_dicts, self.leaf_dict_lists, self.scalar_lists)

    @classmethod
    def _clone(cls, value, leaf_dicts: frozenset[int], leaf_dict_lists: frozenset[int], scalar_lists: frozenset[int]):
        value_type = type(value)
        if value_type is dict:
            if id(value) in leaf_dicts:
                return value.copy()
            out = value.copy()
            for key, item in out.items():
                if type(item) not in _IMMUTABLES:
                    out[key] = cls._clone(item, leaf_dicts, leaf_dict_lists, scalar_lists)
            return out
        if value_type is list:
            vid = id(value)
            if vid in scalar_lists:
                return value.copy()
            if vid in leaf_dict_lists:
                return [item.copy() for item in value]
            return [
                item if type(item) in _IMMUTABLES else cls._clone(item, leaf_dicts, leaf_dict_lists, scalar_lists)
                for item in value
            ]
        if value_type is tuple:
            return tuple(
                item if type(item) in _IMMUTABLES else cls._clone(item, leaf_dicts, leaf_dict_lists, scalar_lists)
                for item in value
            )
        if value_type in _IMMUTABLES:
            return value
        return deepcopy(value)


def clone_maintenance_payload(value):
    """Return a deep isolated copy of a plain JSON-compatible maintenance payload."""
    if isinstance(value, _PreparedPayload):
        return value.clone()
    value_type = type(value)
    if value_type is dict:
        out = value.copy()
        for key, item in out.items():
            if type(item) not in _IMMUTABLES:
                out[key] = clone_maintenance_payload(item)
        return out
    if value_type is list:
        return [item if type(item) in _IMMUTABLES else clone_maintenance_payload(item) for item in value]
    if value_type is tuple:
        return tuple(item if type(item) in _IMMUTABLES else clone_maintenance_payload(item) for item in value)
    if value_type in _IMMUTABLES:
        return value
    return deepcopy(value)


def _empty_cache_day(day: str) -> dict:
    result = {
        "day": day, "signature_calls": 0, "compared_calls": 0, "baseline_calls": 0,
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
        "tool_bytes_total": 0, "images_total": 0, "audio_total": 0, "video_total": 0,
    }
    for dimension in ("route", "prefix", "system", "tools", "materials", "availability"):
        result.update({
            f"{dimension}_compared_calls": 0,
            f"{dimension}_stable_calls": 0,
            f"{dimension}_cache_calls": 0,
            f"{dimension}_cache_input_tokens_total": 0,
            f"{dimension}_cached_input_tokens_total": 0,
        })
    return result


class CacheDiagnosticAccumulator:
    """Incremental cache-signature diagnostics accumulator shared by cold and cached paths."""

    __slots__ = (
        "daily",
        "events",
        "previous_by_scope",
        "scope_fingerprints",
        "max_sort_key",
        "delta_traces",
        "saw_core_kind",
    )

    def __init__(self) -> None:
        self.daily = {key: {} for key in CACHE_DIAGNOSTIC_KEYS}
        self.events = {key: deque(maxlen=80) for key in CACHE_DIAGNOSTIC_KEYS}
        self.previous_by_scope = {}
        self.scope_fingerprints = {}
        self.max_sort_key = None
        self.delta_traces = {key: {} for key in CACHE_DIAGNOSTIC_KEYS}
        self.saw_core_kind = False

    def clone(self) -> "CacheDiagnosticAccumulator":
        copied = CacheDiagnosticAccumulator()
        copied.daily = {
            key: {day: item.copy() for day, item in days.items()}
            for key, days in self.daily.items()
        }
        copied.events = {key: deque(items, maxlen=80) for key, items in self.events.items()}
        copied.previous_by_scope = dict(self.previous_by_scope)
        copied.scope_fingerprints = dict(self.scope_fingerprints)
        copied.max_sort_key = self.max_sort_key
        copied.delta_traces = {
            key: {day: list(deltas) for day, deltas in days.items()}
            for key, days in self.delta_traces.items()
        }
        copied.saw_core_kind = self.saw_core_kind
        return copied

    def add_row(self, row, *, allowed_days=None, track_sort_key: bool = False) -> None:
        raw_signature = row.get("signature") if isinstance(row, dict) else None
        signature = (
            checked_signature_record(raw_signature)
            if raw_signature is not None
            else checked_signature(row["signature_json"])
        )
        if signature is None:
            return
        at = row["finished_at"] or row["started_at"] or row["created_at"]
        day = at[:10] if type(at) is str else ""
        if allowed_days is not None and day not in allowed_days:
            return
        if track_sort_key:
            sort_key = (at, row["position"])
            if self.max_sort_key is None or sort_key > self.max_sort_key:
                self.max_sort_key = sort_key
        kind = row["kind"]
        if kind == CORE_CHAIN_KIND:
            self.saw_core_kind = True
        source = usage_source(kind)
        path = (
            "astrbot_core"
            if kind != MAIN_CHAIN_KIND and source == "conversation"
            else "legacy_conversation"
            if source == "conversation"
            else "plugin"
        )
        keys = tuple(dict.fromkeys(("all", source, path)))
        counts = [self.daily[key].setdefault(day, _empty_cache_day(day)) for key in keys]
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
            if (
                signature.context_observed
                and signature.estimates_observed
                and signature.message_estimates_observed
                and signature.request_estimate_observed
            ):
                item["request_profile_calls"] += 1
                item["profile_context_est_tokens_total"] += signature.context_est_tokens
                item["profile_message_est_tokens_total"] += signature.message_est_tokens
                item["profile_system_est_tokens_total"] += signature.system_est_tokens
                item["profile_tool_est_tokens_total"] += signature.tool_est_tokens
                item["profile_request_est_tokens_total"] += signature.request_est_tokens
        if isinstance(row, dict) and "provider" in row:
            provider_id = row["provider"] if type(row["provider"]) is str else ""
        else:
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
        if type(scope) is dict and all(type(scope.get(name)) is str and scope[name] for name in SCOPE_FIELDS):
            scope_tuple = tuple(scope[name] for name in SCOPE_FIELDS)
            scope_fingerprint = self.scope_fingerprints.get(scope_tuple)
            if scope_fingerprint is None:
                scope_fingerprint = digest({name: scope[name] for name in SCOPE_FIELDS})
                self.scope_fingerprints[scope_tuple] = scope_fingerprint
        else:
            scope_fingerprint = None
        try:
            call_id = row["call_id"]
        except (KeyError, IndexError, TypeError):
            call_id = digest([row["origin_key"], kind, at, row["position"]])
        partition = (
            digest(row["origin_key"]),
            kind,
            scope_fingerprint,
            None if scope_fingerprint else call_id,
        )
        previous_entry = self.previous_by_scope.get(partition)
        previous = previous_entry[0] if previous_entry else None
        input_tokens, cached_tokens = row["input_tokens"], row["cached_input_tokens"]
        hit_rate = (
            round(cached_tokens * 100 / input_tokens, 1)
            if input_tokens and cached_tokens is not None
            else None
        )
        previous_provider = previous_entry[2] if previous_entry else None
        same_cache_route = (
            previous_entry is not None
            and previous_provider == provider_id
            and previous.model_hash == signature.model_hash
        )
        previous_hit_rate = previous_entry[1] if same_cache_route else None
        self.previous_by_scope[partition] = (signature, hit_rate, provider_id)
        if previous is None:
            for item in counts:
                item["baseline_calls"] += 1
            return
        for item in counts:
            item["compared_calls"] += 1
        stable_dimensions = {
            "route": (
                previous_provider == provider_id and previous.model_hash == signature.model_hash
                if previous_provider and provider_id
                else None
            ),
            "system": previous.system_hash == signature.system_hash,
            "tools": previous.tools_hash == signature.tools_hash,
            "materials": (
                previous.context_sequence_hash == signature.context_sequence_hash
                if previous.context_observed and signature.context_observed
                else None
            ),
            "availability": (
                previous.context_availability_hash == signature.context_availability_hash
                if previous.context_availability_observed and signature.context_availability_observed
                else None
            ),
        }
        stable_dimensions["prefix"] = (
            all(value is True for value in stable_dimensions.values())
            if all(value is not None for value in stable_dimensions.values())
            else None
        )
        comparable_route = stable_dimensions["route"] is True
        for dimension, stable in stable_dimensions.items():
            if stable is None:
                continue
            for item in counts:
                item[f"{dimension}_compared_calls"] += 1
                if stable:
                    item[f"{dimension}_stable_calls"] += 1
                    if (
                        comparable_route
                        and type(input_tokens) is int
                        and input_tokens > 0
                        and type(cached_tokens) is int
                    ):
                        item[f"{dimension}_cache_calls"] += 1
                        item[f"{dimension}_cache_input_tokens_total"] += input_tokens
                        item[f"{dimension}_cached_input_tokens_total"] += cached_tokens
        changes = list(signature_changes(signature, previous))
        if previous_provider != provider_id:
            changes.insert(0, "provider_change")
        changes = tuple(changes)
        if not changes:
            return
        severity = event_severity(changes)
        delta = (
            round(hit_rate - previous_hit_rate, 1)
            if hit_rate is not None and previous_hit_rate is not None
            else None
        )
        for key, item in zip(keys, counts):
            item["changed_calls"] += 1
            item[f"{severity}_severity_events"] += 1
            for change in changes:
                item[CHANGE_FIELDS[change]] += 1
            if delta is not None:
                item["cache_hit_comparable_changes"] += 1
                item["cache_hit_delta_points_total"] += delta
                self.delta_traces[key].setdefault(day, []).append(delta)
                if delta < 0:
                    item["cache_hit_drop_after_change_calls"] += 1
        session_ref = (
            digest(["cache-diagnostics-session", scope_fingerprint])[:8]
            if scope_fingerprint
            else None
        )
        event = {
            "day": day, "at": at, "kind": kind, "provider": provider_id,
            "execution_path": (
                "astrbot_core"
                if path == "astrbot_core"
                else "legacy_v2_conversation"
                if path == "legacy_conversation"
                else "v2_background"
            ),
            "measurement_basis": (
                row.get("signature_scope", "ledger_provider_payload")
                if isinstance(row, dict)
                else "ledger_provider_payload"
            ),
            "previous_provider": previous_provider,
            "session_ref": session_ref, "severity": severity,
            "changes": list(changes), "input_tokens": input_tokens, "cache_hit_rate": hit_rate,
            "previous_cache_hit_rate": previous_hit_rate,
            "cache_hit_delta": delta,
            "before": signature_snapshot(previous), "after": signature_snapshot(signature),
        }
        for key in keys:
            self.events[key].append(event)

    def project(self) -> dict:
        return {
            key: {
                "daily": [days[day] for day in sorted(days)],
                "recent_events": list(reversed(self.events[key])),
                "event_sample_limit": 80,
                "scope": "current_role",
                "signature_storage": "hashes_sizes_estimates_no_prompt_text",
            }
            for key, days in self.daily.items()
        }


def merge_cache_diagnostics(
    legacy_acc: CacheDiagnosticAccumulator,
    core_acc: CacheDiagnosticAccumulator,
) -> dict:
    """Combine disjoint legacy and core cache-diagnostic accumulators with exact parity."""
    result = {}
    for key in CACHE_DIAGNOSTIC_KEYS:
        merged_days = {day: item.copy() for day, item in legacy_acc.daily[key].items()}
        for day, core_item in core_acc.daily[key].items():
            existing = merged_days.get(day)
            if existing is None:
                merged_days[day] = core_item.copy()
                continue
            had_legacy_delta = existing["cache_hit_comparable_changes"] > 0
            for field, val in core_item.items():
                if field == "day":
                    continue
                if field == "cache_hit_delta_points_total":
                    if had_legacy_delta:
                        for delta in core_acc.delta_traces[key].get(day, ()):
                            existing[field] += delta
                    else:
                        existing[field] = val
                else:
                    existing[field] += val
        merged_events = deque(legacy_acc.events[key], maxlen=80)
        merged_events.extend(core_acc.events[key])
        result[key] = {
            "daily": [merged_days[day] for day in sorted(merged_days)],
            "recent_events": list(reversed(merged_events)),
            "event_sample_limit": 80,
            "scope": "current_role",
            "signature_storage": "hashes_sizes_estimates_no_prompt_text",
        }
    return result


def _strip_recent_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in _RECENT_DROP_KEYS}


def _strip_private_observation(obs: dict) -> dict:
    """Retain only fields needed by reply_efficiency._core_sample (no large signatures)."""
    stripped = {
        key: value
        for key, value in obs.items()
        if key not in ("request_hook_signature", "requests", "context_json")
    }
    hook_sig = obs.get("request_hook_signature")
    if isinstance(hook_sig, dict) and "tool_count" in hook_sig:
        stripped["request_hook_signature"] = {"tool_count": hook_sig["tool_count"]}
    else:
        stripped["request_hook_signature"] = None
    requests = obs.get("requests")
    if isinstance(requests, list):
        stripped["requests"] = [
            {key: value for key, value in item.items() if key != "signature"}
            if isinstance(item, dict)
            else item
            for item in requests
        ]
    else:
        stripped["requests"] = []
    return stripped


def _recent_sort_key(row) -> tuple:
    return (
        row["finished_at"] or row["started_at"] or row["created_at"] or "",
        row["position"] or 0,
    )


def _clone_usage_report(report: ModelUsageReport) -> ModelUsageReport:
    copied = ModelUsageReport()
    copied.by_day = {k: v.copy() for k, v in report.by_day.items()}
    copied.by_provider = {k: v.copy() for k, v in report.by_provider.items()}
    copied.by_task = {k: v.copy() for k, v in report.by_task.items()}
    copied.day_provider = {k: v.copy() for k, v in report.day_provider.items()}
    copied.day_task = {k: v.copy() for k, v in report.day_task.items()}
    copied.hourly = {k: v.copy() for k, v in report.hourly.items()}
    copied.recent_rows = list(report.recent_rows)
    return copied


def merge_usage_report(target: ModelUsageReport, source: ModelUsageReport) -> None:
    """Add all aggregate buckets and recent rows from *source* into *target*."""
    for attr in ("by_day", "by_provider", "by_task", "day_provider", "day_task", "hourly"):
        dst_map = getattr(target, attr)
        for key, src_vals in getattr(source, attr).items():
            dst_vals = dst_map.get(key)
            if dst_vals is None:
                dst_map[key] = src_vals.copy()
            else:
                for field, val in src_vals.items():
                    dst_vals[field] = dst_vals.get(field, 0) + val
    target.recent_rows.extend(source.recent_rows)


def _availability_counts_for_observations(observations) -> tuple[int, ...]:
    turns = len(observations)
    observed_requests = 0
    fallback_usage = 0
    req_avail_turns = 0
    req_complete_turns = 0
    req_partial_turns = 0
    usage_reported = 0
    cache_reported = 0
    for item in observations:
        if item.get("request_observation_available") is True:
            req_avail_turns += 1
        if item.get("request_observation_complete") is True:
            req_complete_turns += 1
        if item.get("tool_requery_observed") is True:
            req_partial_turns += 1
        for req in item.get("requests", ()):
            if req.get("measurement_basis") == "provider_response":
                observed_requests += 1
            else:
                fallback_usage += 1
            usage = req.get("usage") or {}
            if type(usage.get("tokens")) is int:
                usage_reported += 1
            if type(usage.get("cached_input_tokens")) is int:
                cache_reported += 1
    return (
        turns,
        observed_requests,
        fallback_usage,
        req_avail_turns,
        req_complete_turns,
        req_partial_turns,
        usage_reported,
        cache_reported,
    )


def _add_availability_counts(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right))


def availability_summary_from_counts(counts: tuple[int, ...]) -> dict:
    return {
        "turns": counts[0],
        "observed_requests": counts[1],
        "fallback_usage_observations": counts[2],
        "request_observation_turns": counts[3],
        "complete_request_observation_turns": counts[4],
        "partial_request_observation_turns": counts[5],
        "usage_reported_requests": counts[6],
        "cache_reported_requests": counts[7],
        **_AVAILABILITY_DEFAULTS,
    }


def _file_stat_token(path: Path) -> tuple[int, int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size)


def pricing_fingerprint(runtime, pricing_revision=None) -> tuple:
    """Return a hashable fingerprint invalidating immediately on any pricing change."""
    if pricing_revision is None:
        explicit = None
    elif isinstance(pricing_revision, (str, int, float, bool)):
        explicit = (type(pricing_revision).__name__, pricing_revision)
    else:
        explicit = ("digest", digest(pricing_revision))
    data_root = getattr(runtime, "data_root", None)
    if data_root is None:
        return (explicit, None, None)
    pricing_dir = Path(data_root) / "pricing"
    return (
        explicit,
        _file_stat_token(pricing_dir / "settings.json"),
        _file_stat_token(pricing_dir / "catalog.json"),
    )


def _single_store_active_write(runtime) -> bool:
    return int(getattr(runtime, "_write_depth", 0) or 0) > 0


def has_active_write_transaction(runtime) -> bool:
    """Return True if either the main store or split config store has an active write tx."""
    if _single_store_active_write(runtime):
        return True
    config_store = getattr(runtime, "configuration_store", None)
    if config_store is not None and config_store is not runtime:
        return _single_store_active_write(config_store)
    return False


def has_active_read_snapshot(runtime) -> bool:
    """Return True when the current thread holds an open SQLite MVCC read snapshot on *runtime*."""
    if has_active_write_transaction(runtime):
        return False
    local_conns = getattr(runtime, "_read_connections", None)
    conn = getattr(local_conns, "connection", None) if local_conns is not None else None
    if conn is None:
        return False
    try:
        return bool(conn.in_transaction)
    except Exception:
        return False


@dataclass(frozen=True)
class _StoreWriteToken:
    generation: tuple[int, int]
    local_changes: tuple[int, int, int]


@dataclass(frozen=True)
class _DatabaseWriteToken:
    main: _StoreWriteToken
    configuration: _StoreWriteToken | None

    @property
    def generation(self):
        return self.main.generation, self.configuration.generation if self.configuration else None


def _compatible_read_snapshot(before: _DatabaseWriteToken, after: _DatabaseWriteToken | None) -> bool:
    return (after is not None
            and before.main.generation == after.main.generation
            and before.configuration == after.configuration)


def _single_runtime_shared_token(runtime) -> _StoreWriteToken | None:
    if _single_store_active_write(runtime):
        return None
    lock = getattr(runtime, "_lock", None)
    if lock is None or not lock.acquire(timeout=0.01):
        return None
    try:
        connection = getattr(runtime, "_connection", None)
        if connection is None or _single_store_active_write(runtime):
            return None
        # The writer sees every external commit and its own commits leave this value unchanged.
        # SQLite's value is connection-local, so always sample the same writer connection.
        generation = (id(connection), int(connection.execute("PRAGMA data_version").fetchone()[0]))
        local_changes = (connection.total_changes, runtime._savepoint_sequence,
                         runtime._transaction_rollback_epoch)
        return _StoreWriteToken(generation, local_changes)
    except sqlite3.Error:
        return None
    finally:
        lock.release()


def read_shared_db_token(runtime) -> _DatabaseWriteToken | None:
    """Thread-independent O(1) write token across main and optional split config stores."""
    main_token = _single_runtime_shared_token(runtime)
    if main_token is None:
        return None
    config_store = getattr(runtime, "configuration_store", None)
    if config_store is not None and config_store is not runtime:
        config_token = _single_runtime_shared_token(config_store)
        if config_token is None:
            return None
        return _DatabaseWriteToken(main_token, config_token)
    return _DatabaseWriteToken(main_token, None)


def read_fast_table_watermark(runtime, *, role_key_value: str, core_available: bool) -> tuple:
    """Fast O(1)/covering-index watermark across the four primary maintenance tables."""
    if core_available:
        core_row = runtime.read_one(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MAX(received_at), '') "
            "FROM astrbot_core_reply_observations WHERE role_key = ?",
            (role_key_value,),
        )
        core_mark = (int(core_row[0]), int(core_row[1]), core_row[2]) if core_row else (0, 0, "")
    else:
        core_mark = None
    calls_row = runtime.read_one("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM model_calls", ())
    budget_row = runtime.read_one("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM model_budget_accounts", ())
    no_reply_row = runtime.read_one("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM inbound_no_replies", ())
    return (
        core_mark,
        (int(calls_row[0]), int(calls_row[1])) if calls_row else (0, 0),
        (int(budget_row[0]), int(budget_row[1])) if budget_row else (0, 0),
        (int(no_reply_row[0]), int(no_reply_row[1])) if no_reply_row else (0, 0),
    )


def read_core_history_watermark(
    runtime, *, role_key_value: str, history_cutoff: str, core_available: bool
) -> tuple | None:
    """Detect any insert, delete, backfill, or revision update before *history_cutoff*."""
    if not core_available:
        return None
    row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(revision), 0), "
        "COALESCE(MAX(observed_at), ''), COALESCE(MAX(received_at), '') "
        "FROM astrbot_core_reply_observations WHERE role_key = ? AND received_at < ?",
        (role_key_value, history_cutoff),
    )
    return (int(row[0]), int(row[1]), int(row[2]), row[3], row[4]) if row else (0, 0, 0, "", "")


def read_core_today_watermark(
    runtime, *, role_key_value: str, history_cutoff: str, core_available: bool
) -> tuple | None:
    """Detect any insert, delete, revision update, or payload edit on/after *history_cutoff*."""
    if not core_available:
        return None
    row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(revision), 0), "
        "COALESCE(MAX(observed_at), ''), COALESCE(MAX(received_at), ''), "
        "COALESCE(SUM(length(record_json)), 0) "
        "FROM astrbot_core_reply_observations WHERE role_key = ? AND received_at >= ?",
        (role_key_value, history_cutoff),
    )
    return (int(row[0]), int(row[1]), int(row[2]), row[3], row[4], int(row[5])) if row else (0, 0, 0, "", "", 0)


def _rows_sha256(rows) -> str:
    """Order-preserving collision-free SHA-256 digest of SQLite row tuples."""
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update(b"\x1e")
        for value in row:
            hasher.update(b"\x1f")
            if value is None:
                hasher.update(b"N")
            else:
                hasher.update(b"V")
                hasher.update(str(value).encode("utf-8", errors="surrogatepass"))
    return hasher.hexdigest()


def _chunked_rows_sha256(runtime, query_template: str, ids: tuple[str, ...]) -> str:
    """Hash rows fetched across chunked IN (...) predicates with deterministic ordering."""
    if not ids:
        return ""
    hasher = hashlib.sha256()
    for offset in range(0, len(ids), 400):
        group = ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in group)
        rows = runtime.read_all(query_template.format(placeholders=placeholders), group)
        for row in rows:
            hasher.update(b"\x1e")
            for value in row:
                hasher.update(b"\x1f")
                if value is None:
                    hasher.update(b"N")
                else:
                    hasher.update(b"V")
                    hasher.update(str(value).encode("utf-8", errors="surrogatepass"))
    return hasher.hexdigest()


def read_legacy_watermark(runtime) -> tuple:
    """Detect any insert, delete, or in-place update across legacy model-ledger tables."""
    calls_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(c.rowid), 0),
                COALESCE(SUM(COALESCE(c.actual_tokens, -1)), 0),
                COALESCE(SUM(COALESCE(c.actual_cost, -1)), 0),
                COALESCE(SUM(COALESCE(c.input_tokens, -1)), 0),
                COALESCE(SUM(COALESCE(c.cached_input_tokens, -1)), 0),
                COALESCE(SUM(COALESCE(c.cache_creation_tokens, -1)), 0),
                COALESCE(SUM(COALESCE(c.output_tokens, -1)), 0),
                COALESCE(SUM(COALESCE(c.reserved_tokens, 0)), 0),
                COALESCE(SUM(COALESCE(c.reserved_cost, 0)), 0),
                COALESCE(SUM(COALESCE(c.latency_ms, -1)), 0),
                COUNT(c.started_at), COUNT(c.finished_at), COUNT(c.receipt_ref),
                COALESCE(MIN(c.finished_at), ''), COALESCE(MAX(c.finished_at), ''),
                COALESCE(MIN(c.started_at), ''), COALESCE(MAX(c.started_at), ''),
                COALESCE(MIN(c.receipt_ref), ''), COALESCE(MAX(c.receipt_ref), ''),
                COALESCE(SUM(length(COALESCE(c.receipt_ref, ''))), 0),
                COALESCE(SUM(CASE WHEN COALESCE(c.receipt_ref, '') LIKE ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE c.state
                    WHEN 'generated' THEN 1 WHEN 'dispatched' THEN 3 WHEN 'reserved' THEN 7
                    WHEN 'unknown' THEN 13 WHEN 'discarded' THEN 29 WHEN 'not_started' THEN 53
                    ELSE 97 END), 0),
                COALESCE(SUM(length(c.provider_json)), 0),
                COALESCE(MAX(c.provider_json), ''), COALESCE(MIN(c.provider_json), ''),
                COALESCE(SUM(CASE WHEN t.kind = ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(length(t.kind)), 0),
                COALESCE(MAX(t.kind), ''), COALESCE(MIN(t.kind), ''),
                COALESCE(SUM(length(t.origin_key)), 0),
                COALESCE(MAX(t.origin_key), ''), COALESCE(MIN(t.origin_key), ''),
                COALESCE(SUM(length(r.plan_json)), 0),
                COALESCE(MAX(r.created_at), ''), COALESCE(MIN(r.created_at), '')
            FROM model_calls c
            JOIN execution_tasks t ON t.task_id = c.task_id
            JOIN model_requests r ON r.task_id = c.task_id""",
        (RELEASE_RECEIPT_PREFIX + "%", MAIN_CHAIN_KIND),
    )
    recent_calls_digest = _rows_sha256(
        runtime.read_all(
            """SELECT c.call_id, c.state, c.reserved_tokens, c.reserved_cost,
                    c.actual_tokens, c.actual_cost, c.input_tokens, c.cached_input_tokens,
                    c.cache_creation_tokens, c.output_tokens, c.latency_ms,
                    c.started_at, c.finished_at, c.receipt_ref, c.provider_json,
                    t.kind, t.origin_key
                FROM model_calls c
                JOIN execution_tasks t ON t.task_id = c.task_id
                JOIN model_requests r ON r.task_id = c.task_id
                ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at) DESC, c.position DESC
                LIMIT 200""",
            (),
        )
    )
    accounts_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MAX(account_key), ''), COALESCE(MIN(account_key), '') "
        "FROM model_call_accounts",
        (),
    )
    failures_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MIN(observed_at), ''), COALESCE(MAX(observed_at), ''), "
        "COALESCE(MIN(code), ''), COALESCE(MAX(code), ''), COALESCE(SUM(length(code)), 0) "
        "FROM model_call_failures",
        (),
    )
    failures_digest = _rows_sha256(
        runtime.read_all("SELECT call_id, code, observed_at FROM model_call_failures ORDER BY rowid", ())
    )
    diag_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MIN(observed_at), ''), COALESCE(MAX(observed_at), ''), "
        "COALESCE(SUM(length(summary_json)), 0), COALESCE(MIN(summary_json), ''), COALESCE(MAX(summary_json), '') "
        "FROM model_call_diagnostics",
        (),
    )
    diag_recent_digest = _rows_sha256(
        runtime.read_all(
            "SELECT call_id, observed_at, summary_json FROM model_call_diagnostics ORDER BY rowid DESC LIMIT 256",
            (),
        )
    )
    sig_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(length(signature_json)), 0), "
        "COALESCE(MIN(signature_json), ''), COALESCE(MAX(signature_json), '') "
        "FROM model_call_cache_signatures",
        (),
    )
    return (
        tuple(calls_row) if calls_row else (),
        recent_calls_digest,
        tuple(accounts_row) if accounts_row else (),
        tuple(failures_row) if failures_row else (),
        failures_digest,
        tuple(diag_row) if diag_row else (),
        diag_recent_digest,
        tuple(sig_row) if sig_row else (),
    )


def read_aux_maintenance_watermark(
    runtime, context, *, context_key: str, soft_fallback, eff_now: datetime | None = None
) -> tuple:
    """Detect changes to budget, runtime diagnostics, and reply-efficiency tables."""
    budget_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(token_limit), 0), "
        "COALESCE(SUM(held_tokens), 0), COALESCE(SUM(spent_tokens), 0) "
        "FROM model_budget_accounts",
        (),
    )
    no_reply_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MAX(observed_at), '') "
        "FROM inbound_no_replies WHERE origin_key = ? OR origin_key IS NULL",
        (context_key,),
    )
    no_reply_digest = _rows_sha256(
        runtime.read_all(
            "SELECT event_ref, channel, reason_code, code, observed_at, origin_key "
            "FROM inbound_no_replies WHERE origin_key = ? OR origin_key IS NULL "
            "ORDER BY observed_at DESC, rowid DESC LIMIT 24",
            (context_key,),
        )
    )
    batches_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(rowid), 0),
                COALESCE(MIN(created_at), ''), COALESCE(MAX(created_at), ''),
                COALESCE(SUM(revision), 0), COALESCE(SUM(part_count), 0),
                COUNT(generation_task_id),
                COALESCE(MIN(generation_task_id), ''), COALESCE(MAX(generation_task_id), ''),
                COALESCE(SUM(CASE state
                    WHEN 'open' THEN 1 WHEN 'sealed' THEN 3 WHEN 'ready' THEN 7
                    WHEN 'cancelled' THEN 13 WHEN 'invalidated' THEN 29 ELSE 53 END), 0),
                COALESCE(SUM(length(contributions_json) + length(context_json)), 0)
            FROM conversation_batches""",
        (),
    )
    flows_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(rowid), 0),
                COALESCE(SUM(CASE state
                    WHEN 'collecting' THEN 1 WHEN 'generating' THEN 3 WHEN 'delivering' THEN 7
                    WHEN 'complete' THEN 13 WHEN 'blocked' THEN 29 WHEN 'closed' THEN 53
                    ELSE 97 END), 0),
                COUNT(due_at), COALESCE(MIN(due_at), ''), COALESCE(MAX(due_at), ''),
                COUNT(merge_until), COALESCE(MIN(merge_until), ''), COALESCE(MAX(merge_until), ''),
                COUNT(deadline_at), COALESCE(MIN(deadline_at), ''), COALESCE(MAX(deadline_at), ''),
                COUNT(reason), COALESCE(MIN(reason), ''), COALESCE(MAX(reason), ''),
                COALESCE(SUM(length(COALESCE(reason, ''))), 0),
                COUNT(policy_json), COALESCE(MIN(policy_json), ''), COALESCE(MAX(policy_json), ''),
                COALESCE(SUM(length(COALESCE(policy_json, ''))), 0)
            FROM conversation_flows""",
        (),
    )
    inputs_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(length(event_json)), 0), "
        "COALESCE(MIN(CASE WHEN json_valid(event_json) THEN json_extract(event_json, '$.received_at') END), ''), "
        "COALESCE(MAX(CASE WHEN json_valid(event_json) THEN json_extract(event_json, '$.received_at') END), '') "
        "FROM conversation_inputs",
        (),
    )
    parts_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(rowid), 0),
                COALESCE(SUM(CASE state
                    WHEN 'pending' THEN 1 WHEN 'dispatched' THEN 3 WHEN 'accepted' THEN 7
                    WHEN 'delivered' THEN 13 WHEN 'failed' THEN 29 WHEN 'unknown' THEN 53
                    WHEN 'cancelled' THEN 97 ELSE 193 END), 0),
                COUNT(dispatched_at), COALESCE(MIN(dispatched_at), ''), COALESCE(MAX(dispatched_at), ''),
                COUNT(delivered_at), COALESCE(MIN(delivered_at), ''), COALESCE(MAX(delivered_at), '')
            FROM conversation_parts""",
        (),
    )
    tools_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(rowid), 0),
                COALESCE(SUM(CASE state
                    WHEN 'started' THEN 1 WHEN 'succeeded' THEN 3 WHEN 'failed' THEN 7
                    WHEN 'unknown' THEN 13 ELSE 29 END), 0),
                COUNT(finished_at), COALESCE(MIN(finished_at), ''), COALESCE(MAX(finished_at), ''),
                COALESCE(MIN(created_at), ''), COALESCE(MAX(created_at), '')
            FROM conversation_tool_calls""",
        (),
    )
    ext_row = runtime.read_one(
        """SELECT COUNT(*), COALESCE(MAX(rowid), 0),
                COALESCE(SUM(CASE state
                    WHEN 'queued' THEN 1 WHEN 'running' THEN 3 WHEN 'succeeded' THEN 7
                    WHEN 'failed' THEN 13 WHEN 'unknown' THEN 29 ELSE 53 END), 0),
                COUNT(finished_at), COALESCE(MIN(finished_at), ''), COALESCE(MAX(finished_at), ''),
                COALESCE(MIN(created_at), ''), COALESCE(MAX(created_at), '')
            FROM extension_tool_invocations""",
        (),
    )
    events_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MIN(observed_at), ''), COALESCE(MAX(observed_at), '') "
        "FROM execution_events WHERE state = 'running'",
        (),
    )
    laya_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MIN(observed_at), ''), COALESCE(MAX(observed_at), ''), "
        "COALESCE(SUM(COALESCE(elapsed_ms, -1)), 0) FROM laya_decision_events",
        (),
    )
    proactive_row = runtime.read_one(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MAX(created_at), '') "
        "FROM proactive_contacts WHERE origin_key = ?",
        (context_key,),
    )

    # Collision-free content digests for all recent/7d batches surfaced in diagnostics & reply_efficiency.
    if eff_now is not None:
        window_start = (
            eff_now.astimezone(timezone.utc) - timedelta(days=8)
        ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        recent_batch_rows = runtime.read_all(
            """SELECT batch_id, generation_task_id FROM (
                    SELECT batch_id, generation_task_id FROM conversation_batches
                    WHERE origin_key = ? ORDER BY created_at DESC, rowid DESC LIMIT 24
                )
                UNION
                SELECT batch_id, generation_task_id FROM conversation_batches
                WHERE created_at >= ?
                ORDER BY batch_id""",
            (context_key, window_start),
        )
    else:
        recent_batch_rows = runtime.read_all(
            """SELECT batch_id, generation_task_id FROM (
                    SELECT batch_id, generation_task_id FROM conversation_batches
                    WHERE origin_key = ? ORDER BY created_at DESC, rowid DESC LIMIT 24
                )
                UNION
                SELECT batch_id, generation_task_id FROM (
                    SELECT batch_id, generation_task_id FROM conversation_batches
                    ORDER BY created_at DESC, rowid DESC LIMIT 64
                )
                ORDER BY batch_id""",
            (context_key,),
        )
    recent_batch_ids = tuple(dict.fromkeys(row["batch_id"] for row in recent_batch_rows if row["batch_id"]))
    recent_task_ids = tuple(
        dict.fromkeys(row["generation_task_id"] for row in recent_batch_rows if row["generation_task_id"])
    )
    recent_window_digests = (
        _chunked_rows_sha256(
            runtime,
            "SELECT b.batch_id, b.origin_key, b.created_at, b.state, b.revision, b.part_count, "
            "b.generation_task_id, b.contributions_json, b.context_json, r.plan_json "
            "FROM conversation_batches b LEFT JOIN model_requests r ON r.task_id = b.generation_task_id "
            "WHERE b.batch_id IN ({placeholders}) ORDER BY b.batch_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, state, reason, due_at, merge_until, deadline_at, policy_json "
            "FROM conversation_flows WHERE batch_id IN ({placeholders}) ORDER BY batch_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, event_key, event_json "
            "FROM conversation_inputs WHERE batch_id IN ({placeholders}) ORDER BY batch_id, position, event_key",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, part_id, state, dispatched_at, delivered_at "
            "FROM conversation_parts WHERE batch_id IN ({placeholders}) ORDER BY batch_id, sequence, part_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, task_id, tool_call_id, sequence, action, state, created_at, finished_at "
            "FROM conversation_tool_calls WHERE batch_id IN ({placeholders}) ORDER BY batch_id, sequence, tool_call_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, invocation_id, parent_task_id, tool_call_id, state, created_at, finished_at "
            "FROM extension_tool_invocations WHERE batch_id IN ({placeholders}) ORDER BY batch_id, invocation_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT batch_id, event_id, source, status, mode, reason_code, elapsed_ms, decision_json, observed_at "
            "FROM laya_decision_events WHERE source = 'conversation' AND batch_id IN ({placeholders}) "
            "ORDER BY batch_id, observed_at, event_id",
            recent_batch_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT task_id, revision, observed_at FROM execution_events "
            "WHERE state = 'running' AND task_id IN ({placeholders}) ORDER BY task_id, revision",
            recent_task_ids,
        ),
        _chunked_rows_sha256(
            runtime,
            "SELECT c.task_id, c.call_id, c.position, c.state, c.started_at, c.finished_at, c.latency_ms, "
            "c.provider_json, c.actual_tokens, c.input_tokens, c.output_tokens, f.code, d.summary_json "
            "FROM model_calls c LEFT JOIN model_call_failures f ON f.call_id = c.call_id "
            "LEFT JOIN model_call_diagnostics d ON d.call_id = c.call_id "
            "WHERE c.task_id IN ({placeholders}) ORDER BY c.task_id, c.position, c.call_id",
            recent_task_ids,
        ),
    )

    reclaim_row = runtime.read_one(
        "SELECT value FROM runtime_metadata WHERE key = ?",
        (RECLAIM_AUDIT_KEY,),
    )
    soft = effective_soft(runtime, context, soft_fallback)
    soft_mark = (bool(soft and soft.enabled), soft.tokens if soft else 0)
    return (
        tuple(budget_row) if budget_row else (),
        tuple(no_reply_row) if no_reply_row else (),
        tuple(batches_row) if batches_row else (),
        tuple(flows_row) if flows_row else (),
        tuple(inputs_row) if inputs_row else (),
        tuple(parts_row) if parts_row else (),
        tuple(tools_row) if tools_row else (),
        tuple(ext_row) if ext_row else (),
        tuple(events_row) if events_row else (),
        tuple(laya_row) if laya_row else (),
        tuple(proactive_row) if proactive_row else (),
        no_reply_digest,
        recent_window_digests,
        reclaim_row["value"] if reclaim_row is not None else None,
        soft_mark,
    )


def _read_storage_pragmas(runtime) -> dict:
    pragma = lambda name: runtime.read_one("PRAGMA " + name, ())[0]
    return {
        "journal_mode": str(pragma("journal_mode")).lower(),
        "synchronous": int(pragma("synchronous")),
        "busy_timeout_ms": int(pragma("busy_timeout")),
        "foreign_keys": bool(pragma("foreign_keys")),
        "page_count": int(pragma("page_count")),
        "freelist_count": int(pragma("freelist_count")),
        "schema_version": int(pragma("user_version")),
    }


def _query_core_partition_rows(
    runtime,
    *,
    role_key_value: str,
    received_ge: str | None = None,
    received_lt: str | None = None,
    end_dt: datetime,
) -> tuple[list[tuple[dict, str, datetime]], bool, datetime | None]:
    """Query one half-open SQL partition [received_ge, received_lt) via astrbot_core_reply_role_idx."""
    where = ["role_key = ?"]
    values: list[object] = [role_key_value]
    if received_ge is not None:
        where.append("received_at >= ?")
        values.append(received_ge)
    if received_lt is not None:
        where.append("received_at < ?")
        values.append(received_lt)
    rows = runtime.read_all(
        "SELECT scope_kind, context_key, context_json, received_at, record_json "
        "FROM astrbot_core_reply_observations WHERE " + " AND ".join(where)
        + " ORDER BY received_at DESC, turn_id DESC",
        tuple(values),
    )
    result: list[tuple[dict, str, datetime]] = []
    has_future = False
    max_dt: datetime | None = None
    for row in rows:
        row_dt = _parse_utc_timestamp(row["received_at"])
        if row_dt is None:
            continue
        if row_dt > end_dt:
            has_future = True
            continue
        if max_dt is None or row_dt > max_dt:
            max_dt = row_dt
        try:
            record = json.loads(row["record_json"])
            if type(record) is dict and record.get("version") == 1:
                obs = {**record, "context_key": row["context_key"], "context_json": row["context_json"]}
                result.append((obs, row["scope_kind"], row_dt))
        except (TypeError, ValueError):
            continue
    return result, has_future, max_dt


def _has_future_conversation_batches(runtime, *, now_iso: str, batch_count: int | None = None) -> bool:
    if batch_count == 0:
        return False
    row = runtime.read_one(
        "SELECT 1 FROM conversation_batches WHERE julianday(created_at) > julianday(?) LIMIT 1",
        (now_iso,),
    )
    return row is not None


@dataclass
class _CoreHistoryEntry:
    hist_watermark: tuple | None
    hist_max_utc_dt: datetime | None
    core_days: frozenset[str]
    usage_report: ModelUsageReport
    availability_counts: tuple[int, ...]
    cache_diag: CacheDiagnosticAccumulator
    private_7d_records: tuple[tuple[dict, datetime], ...]


@dataclass
class _LegacyCacheEntry:
    legacy_watermark: tuple
    all_days: frozenset[str]
    candidate_days: frozenset[str]
    reports: dict[str, ModelUsageReport]
    cache_diag: CacheDiagnosticAccumulator


@dataclass
class _SnapshotCacheEntry:
    db_write_token: _DatabaseWriteToken | None
    fast_watermark: tuple
    rich_watermark: tuple
    runtime_state_key: tuple
    snapshot_now_utc: datetime
    has_future_rows: bool
    payload: _PreparedPayload
    storage_pragmas_override: dict | None = None


@dataclass
class CorePartitionState:
    """Merged historical + today core-observation projections ready for maintenance()."""

    core_days: frozenset[str]
    usage_report: ModelUsageReport
    availability: dict
    has_observations: bool
    cache_diag: CacheDiagnosticAccumulator
    private_7d: list[dict]
    has_future_obs: bool
    raw_core_rows: list[dict] | None = None


def _fold_core_observations(
    base: _CoreHistoryEntry | None,
    obs_with_scope: list[tuple[dict, str, datetime]],
    *,
    enrich_core_rows,
    eff_start: datetime,
    eff_now: datetime,
    hist_watermark: tuple | None,
    hist_max_utc_dt: datetime | None,
) -> tuple[_CoreHistoryEntry | None, bool]:
    """Fold *obs_with_scope* onto *base*, returning ``(entry, order_ok)``."""
    obs_list = [obs for obs, _scope_kind, _row_dt in obs_with_scope]
    rows = request_rows(obs_list, include_signature_json=False)
    sorted_rows = sorted(
        rows,
        key=lambda row: (row["finished_at"] or row["started_at"] or row["created_at"], row["position"]),
    )
    if base is not None and base.cache_diag.max_sort_key is not None and sorted_rows:
        first = sorted_rows[0]
        first_key = (first["finished_at"] or first["started_at"] or first["created_at"], first["position"])
        if first_key <= base.cache_diag.max_sort_key:
            return None, False

    if base is None:
        usage_report = ModelUsageReport()
        cache_diag = CacheDiagnosticAccumulator()
        core_days = set()
        avail_counts = (0, 0, 0, 0, 0, 0, 0, 0)
        private_records: list[tuple[dict, datetime]] = []
        merged_max_dt = hist_max_utc_dt
    else:
        usage_report = _clone_usage_report(base.usage_report)
        cache_diag = base.cache_diag.clone()
        core_days = set(base.core_days)
        avail_counts = base.availability_counts
        private_records = [
            (rec, dt) for rec, dt in base.private_7d_records if eff_start <= dt <= eff_now
        ]
        if base.hist_max_utc_dt is None:
            merged_max_dt = hist_max_utc_dt
        elif hist_max_utc_dt is None:
            merged_max_dt = base.hist_max_utc_dt
        else:
            merged_max_dt = max(base.hist_max_utc_dt, hist_max_utc_dt)

    if rows:
        core_days.update(row["day"] for row in rows)
        new_recent = []
        for row, cost_meta, recent in enrich_core_rows(rows):
            usage_report.add_group({**row, **row["aggregate"], **cost_meta})
            usage_report.add_hour({**row, **cost_meta})
            new_recent.append(_strip_recent_row(recent))
        # Preserve Timsort stability: newer observations appear before older cached ones.
        combined_recent = new_recent + usage_report.recent_rows
        usage_report.recent_rows = sorted(combined_recent, key=_recent_sort_key, reverse=True)[:80]

    for row in sorted_rows:
        cache_diag.add_row(row, track_sort_key=True)

    avail_counts = _add_availability_counts(avail_counts, _availability_counts_for_observations(obs_list))
    new_private = [
        (_strip_private_observation(obs), row_dt)
        for obs, scope_kind, row_dt in obs_with_scope
        if scope_kind == "private" and eff_start <= row_dt <= eff_now
    ]
    private_records = new_private + private_records

    return (
        _CoreHistoryEntry(
            hist_watermark=hist_watermark,
            hist_max_utc_dt=merged_max_dt,
            core_days=frozenset(core_days),
            usage_report=usage_report,
            availability_counts=avail_counts,
            cache_diag=cache_diag,
            private_7d_records=tuple(private_records),
        ),
        True,
    )


def _lru_put(cache: OrderedDict, key, value, max_entries: int) -> None:
    if key in cache:
        cache.move_to_end(key)
    cache[key] = value
    while len(cache) > max_entries:
        cache.popitem(last=False)


class _RuntimeMaintenanceCache:
    """Per-StorageRuntime thread-safe bounded LRU caches for maintenance projections."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshots: OrderedDict[tuple, _SnapshotCacheEntry] = OrderedDict()
        self._days: OrderedDict[tuple, _CoreHistoryEntry] = OrderedDict()
        self._legacy: OrderedDict[tuple, _LegacyCacheEntry] = OrderedDict()
        self._stats = {
            "snapshot_hits": 0,
            "snapshot_misses": 0,
            "day_cache_hits": 0,
            "day_cache_advances": 0,
            "day_cache_misses": 0,
            "legacy_cache_hits": 0,
            "legacy_cache_misses": 0,
        }

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                **self._stats,
                "snapshot_entries": len(self._snapshots),
                "day_entries": len(self._days),
                "legacy_entries": len(self._legacy),
            }

    def clear(self) -> None:
        with self._lock:
            self._snapshots.clear()
            self._days.clear()
            self._legacy.clear()
            for key in self._stats:
                self._stats[key] = 0

    def lookup_snapshot(
        self,
        runtime,
        context,
        *,
        scope_key: tuple,
        runtime_state_key: tuple,
        now: datetime,
        eff_now: datetime,
        role_key_value: str,
        context_key: str,
        history_cutoff: str,
        core_available: bool,
        soft_fallback,
        shared_token_before: _DatabaseWriteToken,
    ) -> tuple[dict | None, dict]:
        """Try to serve a fresh copy of the full maintenance snapshot in O(1)."""
        db_write_token = shared_token_before if read_shared_db_token(runtime) == shared_token_before else None
        fast_watermark = read_fast_table_watermark(
            runtime, role_key_value=role_key_value, core_available=core_available
        )
        now_utc = eff_now.astimezone(timezone.utc)
        precomputed: dict[str, object] = {
            "can_cache": True,
            "shared_token_before": shared_token_before,
            "db_write_token": db_write_token,
            "fast_watermark": fast_watermark,
        }

        with self._lock:
            entry = self._snapshots.get(scope_key)
            if entry is None:
                self._stats["snapshot_misses"] += 1
                return None, precomputed
            clock_ok = (
                now_utc == entry.snapshot_now_utc
                or (now_utc > entry.snapshot_now_utc and not entry.has_future_rows)
            )
            if (
                clock_ok
                and entry.runtime_state_key == runtime_state_key
                and entry.fast_watermark == fast_watermark
                and db_write_token is not None
                and entry.db_write_token == db_write_token
            ):
                self._snapshots.move_to_end(scope_key)
                self._stats["snapshot_hits"] += 1
                payload = entry.payload.clone()
                if entry.storage_pragmas_override is not None and isinstance(payload.get("runtime"), dict):
                    payload["runtime"]["storage"] = entry.storage_pragmas_override.copy()
                payload["generated_at"] = now.isoformat()
                if isinstance(payload.get("reply_efficiency"), dict):
                    payload["reply_efficiency"]["generated_at"] = eff_now.isoformat()
                return payload, precomputed
            candidate_rich = entry.rich_watermark if (clock_ok and entry.runtime_state_key == runtime_state_key) else None

        if candidate_rich is None:
            with self._lock:
                self._stats["snapshot_misses"] += 1
            return None, precomputed

        # A DB write occurred somewhere; check whether any maintenance table actually changed.
        hist_wm = read_core_history_watermark(
            runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
        )
        today_wm = read_core_today_watermark(
            runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
        )
        legacy_wm = read_legacy_watermark(runtime)
        aux_wm = read_aux_maintenance_watermark(
            runtime, context, context_key=context_key, soft_fallback=soft_fallback, eff_now=eff_now
        )
        rich_watermark = (hist_wm, today_wm, legacy_wm, aux_wm)
        precomputed.update(
            hist_watermark=hist_wm,
            today_watermark=today_wm,
            legacy_watermark=legacy_wm,
            aux_watermark=aux_wm,
            rich_watermark=rich_watermark,
        )

        if rich_watermark == candidate_rich:
            storage_pragmas = _read_storage_pragmas(runtime)
            stable_token = (
                db_write_token is not None
                and shared_token_before is not None
                and read_shared_db_token(runtime) == shared_token_before
            )
            with self._lock:
                entry = self._snapshots.get(scope_key)
                if entry is not None and entry.rich_watermark == rich_watermark:
                    if stable_token:
                        entry.db_write_token = db_write_token
                        entry.fast_watermark = fast_watermark
                        entry.storage_pragmas_override = storage_pragmas.copy()
                    self._snapshots.move_to_end(scope_key)
                    self._stats["snapshot_hits"] += 1
                    payload = entry.payload.clone()
                    if isinstance(payload.get("runtime"), dict):
                        payload["runtime"]["storage"] = storage_pragmas.copy()
                    payload["generated_at"] = now.isoformat()
                    if isinstance(payload.get("reply_efficiency"), dict):
                        payload["reply_efficiency"]["generated_at"] = eff_now.isoformat()
                    return payload, precomputed

        with self._lock:
            self._stats["snapshot_misses"] += 1
        return None, precomputed

    def store_snapshot(self, scope_key: tuple, entry: _SnapshotCacheEntry) -> None:
        with self._lock:
            existing = self._snapshots.get(scope_key)
            if existing is not None:
                if existing.snapshot_now_utc > entry.snapshot_now_utc:
                    return
                if (
                    existing.snapshot_now_utc == entry.snapshot_now_utc
                    and existing.db_write_token is not None
                    and entry.db_write_token is None
                ):
                    return
            _lru_put(self._snapshots, scope_key, entry, SNAPSHOT_CACHE_MAX_ENTRIES)

    def promote_snapshot_token(
        self,
        runtime,
        context,
        *,
        scope_key: tuple,
        entry: _SnapshotCacheEntry,
        role_key_value: str,
        context_key: str,
        history_cutoff: str,
        core_available: bool,
        soft_fallback,
        eff_now: datetime,
    ) -> bool:
        """Verify an MVCC-built snapshot against the latest DB state and attach its O(1) write token.

        When a slow cold build overlaps with a periodic write to an unrelated table (such as
        scheduler diagnostics), the build still caches an MVCC-consistent snapshot with
        ``db_write_token=None``. Calling this immediately after the cold ``read_snapshot`` closes
        promotes the entry to O(1) hits if no maintenance table actually changed.
        """
        if has_active_write_transaction(runtime):
            return False
        fresh_shared = read_shared_db_token(runtime)
        if fresh_shared is None:
            return False
        read_snapshot = getattr(runtime, "read_snapshot", None)
        if not callable(read_snapshot):
            return False
        with read_snapshot():
            if read_shared_db_token(runtime) != fresh_shared:
                return False
            fresh_fast_wm = read_fast_table_watermark(
                runtime, role_key_value=role_key_value, core_available=core_available
            )
            hist_wm = read_core_history_watermark(
                runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
            )
            today_wm = read_core_today_watermark(
                runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
            )
            legacy_wm = read_legacy_watermark(runtime)
            aux_wm = read_aux_maintenance_watermark(
                runtime, context, context_key=context_key, soft_fallback=soft_fallback, eff_now=eff_now
            )
            fresh_rich = (hist_wm, today_wm, legacy_wm, aux_wm)
            if fresh_rich != entry.rich_watermark:
                return False
            storage_pragmas = _read_storage_pragmas(runtime)
        if read_shared_db_token(runtime) != fresh_shared:
            return False
        with self._lock:
            current = self._snapshots.get(scope_key)
            if current is entry:
                entry.db_write_token = fresh_shared
                entry.fast_watermark = fresh_fast_wm
                entry.storage_pragmas_override = storage_pragmas.copy()
                return True
        return False

    def load_partitioned_core(
        self,
        runtime,
        context,
        *,
        role_key_value: str,
        history_cutoff: str,
        tz_offset_seconds: int,
        cache_version: tuple,
        enrich_core_rows,
        eff_start: datetime,
        eff_now: datetime,
        core_available: bool,
        hist_watermark: tuple | None = None,
        can_cache: bool = True,
        shared_token_before: _DatabaseWriteToken | None = None,
    ) -> CorePartitionState:
        """Load historical core aggregates from LRU day cache and query only today's rows."""
        if not core_available:
            return CorePartitionState(
                core_days=frozenset(),
                usage_report=ModelUsageReport(),
                availability=availability_summary_from_counts((0, 0, 0, 0, 0, 0, 0, 0)),
                has_observations=False,
                cache_diag=CacheDiagnosticAccumulator(),
                private_7d=[],
                has_future_obs=False,
            )

        now_utc = eff_now.astimezone(timezone.utc)
        if hist_watermark is None:
            hist_watermark = read_core_history_watermark(
                runtime,
                role_key_value=role_key_value,
                history_cutoff=history_cutoff,
                core_available=True,
            )
        day_key = (role_key_value, history_cutoff, tz_offset_seconds, cache_version)

        hist_entry: _CoreHistoryEntry | None = None
        prior_candidates: list[tuple[str, _CoreHistoryEntry]] = []
        with self._lock:
            cached = self._days.get(day_key)
            if (
                cached is not None
                and cached.hist_watermark == hist_watermark
                and (cached.hist_max_utc_dt is None or now_utc >= cached.hist_max_utc_dt)
            ):
                self._days.move_to_end(day_key)
                self._stats["day_cache_hits"] += 1
                hist_entry = cached
            else:
                for existing_key, existing_entry in self._days.items():
                    if (
                        existing_key[0] == role_key_value
                        and existing_key[1] < history_cutoff
                        and existing_key[2] == tz_offset_seconds
                        and existing_key[3] == cache_version
                        and (existing_entry.hist_max_utc_dt is None or now_utc >= existing_entry.hist_max_utc_dt)
                    ):
                        prior_candidates.append((existing_key[1], existing_entry))

        # Midnight rollover fast path: advance the latest valid earlier day prefix across the gap.
        if hist_entry is None and prior_candidates:
            prior_candidates.sort(key=lambda item: item[0], reverse=True)
            prev_cutoff, prev_entry = prior_candidates[0]
            prev_wm = read_core_history_watermark(
                runtime,
                role_key_value=role_key_value,
                history_cutoff=prev_cutoff,
                core_available=True,
            )
            if prev_wm == prev_entry.hist_watermark:
                gap_obs, gap_future, gap_max_dt = _query_core_partition_rows(
                    runtime,
                    role_key_value=role_key_value,
                    received_ge=prev_cutoff,
                    received_lt=history_cutoff,
                    end_dt=now_utc,
                )
                if not gap_future:
                    advanced, order_ok = _fold_core_observations(
                        prev_entry,
                        gap_obs,
                        enrich_core_rows=enrich_core_rows,
                        eff_start=eff_start,
                        eff_now=eff_now,
                        hist_watermark=hist_watermark,
                        hist_max_utc_dt=gap_max_dt,
                    )
                    if order_ok and advanced is not None:
                        hist_entry = advanced
                        store_ok = (
                            can_cache
                            and not has_active_write_transaction(runtime)
                            and (
                                shared_token_before is None
                                or read_shared_db_token(runtime) == shared_token_before
                                or has_active_read_snapshot(runtime)
                            )
                        )
                        with self._lock:
                            self._stats["day_cache_advances"] += 1
                            if store_ok:
                                _lru_put(self._days, day_key, hist_entry, DAY_CACHE_MAX_ENTRIES)

        hist_has_future = False
        if hist_entry is None:
            hist_obs, hist_has_future, hist_max_dt = _query_core_partition_rows(
                runtime,
                role_key_value=role_key_value,
                received_lt=history_cutoff,
                end_dt=now_utc,
            )
            built, _ = _fold_core_observations(
                None,
                hist_obs,
                enrich_core_rows=enrich_core_rows,
                eff_start=eff_start,
                eff_now=eff_now,
                hist_watermark=hist_watermark,
                hist_max_utc_dt=hist_max_dt,
            )
            hist_entry = built
            store_ok = (
                can_cache
                and not hist_has_future
                and hist_entry is not None
                and not has_active_write_transaction(runtime)
                and (
                    shared_token_before is None
                    or read_shared_db_token(runtime) == shared_token_before
                    or has_active_read_snapshot(runtime)
                )
            )
            with self._lock:
                self._stats["day_cache_misses"] += 1
                if store_ok:
                    _lru_put(self._days, day_key, hist_entry, DAY_CACHE_MAX_ENTRIES)

        today_obs, today_has_future, today_max_dt = _query_core_partition_rows(
            runtime,
            role_key_value=role_key_value,
            received_ge=history_cutoff,
            end_dt=now_utc,
        )
        merged, order_ok = _fold_core_observations(
            hist_entry,
            today_obs,
            enrich_core_rows=enrich_core_rows,
            eff_start=eff_start,
            eff_now=eff_now,
            hist_watermark=hist_watermark,
            hist_max_utc_dt=today_max_dt,
        )
        if not order_ok or merged is None:
            # Rare midnight-straddling turn finished after a today turn: full unpartitioned fallback.
            full_obs = observations_with_scope_for_role(runtime, context, end=eff_now.isoformat())
            raw_obs = [obs for obs, _scope_kind, _row_dt in full_obs]
            raw_rows = request_rows(raw_obs, include_signature_json=False)
            full_entry, _ = _fold_core_observations(
                None,
                [
                    (obs, scope_kind, row_dt if row_dt is not None else _parse_utc_timestamp(obs.get("received_at")))
                    for obs, scope_kind, row_dt in full_obs
                    if (row_dt if row_dt is not None else _parse_utc_timestamp(obs.get("received_at"))) is not None
                ],
                enrich_core_rows=enrich_core_rows,
                eff_start=eff_start,
                eff_now=eff_now,
                hist_watermark=hist_watermark,
                hist_max_utc_dt=None,
            )
            assert full_entry is not None
            return CorePartitionState(
                core_days=full_entry.core_days,
                usage_report=full_entry.usage_report,
                availability=availability_summary_from_counts(full_entry.availability_counts),
                has_observations=full_entry.availability_counts[0] > 0,
                cache_diag=full_entry.cache_diag,
                private_7d=[rec for rec, _dt in full_entry.private_7d_records],
                has_future_obs=hist_has_future or today_has_future,
                raw_core_rows=raw_rows,
            )

        return CorePartitionState(
            core_days=merged.core_days,
            usage_report=merged.usage_report,
            availability=availability_summary_from_counts(merged.availability_counts),
            has_observations=merged.availability_counts[0] > 0,
            cache_diag=merged.cache_diag,
            private_7d=[rec for rec, _dt in merged.private_7d_records],
            has_future_obs=hist_has_future or today_has_future,
        )

    def lookup_legacy(
        self,
        legacy_key: tuple,
        *,
        legacy_watermark: tuple,
        core_days: frozenset[str],
        today: str,
    ) -> _LegacyCacheEntry | None:
        with self._lock:
            entry = self._legacy.get(legacy_key)
            if entry is None or entry.legacy_watermark != legacy_watermark:
                self._stats["legacy_cache_misses"] += 1
                return None
            expected_days = entry.candidate_days | core_days | {today}
            if entry.all_days != expected_days:
                self._stats["legacy_cache_misses"] += 1
                return None
            self._legacy.move_to_end(legacy_key)
            self._stats["legacy_cache_hits"] += 1
            return entry

    def store_legacy(self, legacy_key: tuple, entry: _LegacyCacheEntry) -> None:
        with self._lock:
            _lru_put(self._legacy, legacy_key, entry, LEGACY_CACHE_MAX_ENTRIES)


_RUNTIME_CACHES_LOCK = Lock()
_RUNTIME_CACHES: WeakKeyDictionary[object, _RuntimeMaintenanceCache] = WeakKeyDictionary()


def runtime_maintenance_cache(runtime) -> _RuntimeMaintenanceCache | None:
    """Return the per-runtime cache container, or None for non-weakref mock runtimes."""
    with _RUNTIME_CACHES_LOCK:
        cache = _RUNTIME_CACHES.get(runtime)
        if cache is not None:
            return cache
        try:
            cache = _RuntimeMaintenanceCache()
            _RUNTIME_CACHES[runtime] = cache
            return cache
        except TypeError:
            return None


def maintenance_cache_stats(runtime) -> dict[str, int]:
    """Read hit/miss and entry-count diagnostics for one runtime's maintenance caches."""
    cache = runtime_maintenance_cache(runtime)
    return cache.stats() if cache is not None else {}


def clear_maintenance_cache(runtime) -> None:
    """Clear all cached maintenance snapshots and day partitions for one runtime."""
    cache = runtime_maintenance_cache(runtime)
    if cache is not None:
        cache.clear()


def build_snapshot_entry(
    runtime,
    context,
    *,
    precomputed: dict,
    runtime_state_key: tuple,
    role_key_value: str,
    context_key: str,
    history_cutoff: str,
    core_available: bool,
    soft_fallback,
    eff_now: datetime,
    has_future_obs: bool,
    payload: dict,
) -> _SnapshotCacheEntry | None:
    """Assemble a validated full-snapshot cache entry reusing any already-read watermarks."""
    if not precomputed.get("can_cache", True) or has_active_write_transaction(runtime):
        return None
    shared_token_before = precomputed.get("shared_token_before")
    in_mvcc_snapshot = has_active_read_snapshot(runtime)
    shared_token_mid = read_shared_db_token(runtime) if shared_token_before is not None else None
    token_changed = shared_token_before is not None and shared_token_mid != shared_token_before
    if token_changed:
        if not in_mvcc_snapshot or not _compatible_read_snapshot(shared_token_before, shared_token_mid):
            return None
    db_write_token = None if token_changed else precomputed.get("db_write_token")
    fast_watermark = precomputed.get("fast_watermark")
    if fast_watermark is None:
        fast_watermark = read_fast_table_watermark(
            runtime, role_key_value=role_key_value, core_available=core_available
        )
    rich_watermark = precomputed.get("rich_watermark")
    if rich_watermark is None:
        hist_wm = precomputed.get("hist_watermark")
        if hist_wm is None:
            hist_wm = read_core_history_watermark(
                runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
            )
        today_wm = precomputed.get("today_watermark")
        if today_wm is None:
            today_wm = read_core_today_watermark(
                runtime, role_key_value=role_key_value, history_cutoff=history_cutoff, core_available=core_available
            )
        legacy_wm = precomputed.get("legacy_watermark")
        if legacy_wm is None:
            legacy_wm = read_legacy_watermark(runtime)
        aux_wm = precomputed.get("aux_watermark")
        if aux_wm is None:
            aux_wm = read_aux_maintenance_watermark(
                runtime, context, context_key=context_key, soft_fallback=soft_fallback, eff_now=eff_now
            )
        rich_watermark = (hist_wm, today_wm, legacy_wm, aux_wm)
    batches_meta = rich_watermark[3][2] if len(rich_watermark) >= 4 and len(rich_watermark[3]) >= 3 else ()
    batch_count = int(batches_meta[0]) if batches_meta else None
    has_future_batches = _has_future_conversation_batches(
        runtime, now_iso=eff_now.isoformat(), batch_count=batch_count
    )
    if shared_token_before is not None:
        shared_token_final = read_shared_db_token(runtime)
        if shared_token_final != shared_token_before:
            if not (in_mvcc_snapshot and has_active_read_snapshot(runtime)
                    and _compatible_read_snapshot(shared_token_before, shared_token_final)):
                return None
            db_write_token = None
    return _SnapshotCacheEntry(
        db_write_token=db_write_token,
        fast_watermark=fast_watermark,
        rich_watermark=rich_watermark,
        runtime_state_key=runtime_state_key,
        snapshot_now_utc=eff_now.astimezone(timezone.utc),
        has_future_rows=has_future_obs or has_future_batches,
        payload=_PreparedPayload(payload),
    )


def build_legacy_entry(
    *,
    legacy_watermark: tuple,
    all_days: frozenset[str],
    candidate_days: frozenset[str],
    reports: dict[str, ModelUsageReport],
    cache_diag: CacheDiagnosticAccumulator,
) -> _LegacyCacheEntry:
    return _LegacyCacheEntry(
        legacy_watermark=legacy_watermark,
        all_days=all_days,
        candidate_days=candidate_days,
        reports={key: _clone_usage_report(reports[key]) for key in LEGACY_USAGE_KEYS},
        cache_diag=cache_diag.clone(),
    )


def clone_legacy_reports(entry: _LegacyCacheEntry) -> dict[str, ModelUsageReport]:
    reports = {key: _clone_usage_report(entry.reports[key]) for key in LEGACY_USAGE_KEYS}
    reports["astrbot_core"] = ModelUsageReport()
    return reports
