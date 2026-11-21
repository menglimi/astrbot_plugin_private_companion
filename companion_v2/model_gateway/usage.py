"""Role ledger projections split dialogue generation from companion tasks."""

import json

from .models import is_released
from .response_diagnostics import checked_response_diagnostics
from datetime import timedelta


USAGE_FIELDS = (
    "calls", "tokens", "cost_microusd", "unconfirmed_calls", "failures", "unknown_calls",
    "active_calls", "cache_reported_calls", "cache_telemetry_calls", "cache_input_tokens",
    "cached_input_tokens", "cache_creation_tokens", "cache_other_input_tokens", "input_tokens",
    "output_tokens", "cache_low_calls", "latency_total_ms", "latency_samples",
)
_COST_META_FIELDS = (
    "recorded_cost_calls", "estimated_cost_calls", "recorded_cost_microusd",
    "estimated_cost_microusd", "unpriced_token_calls",
)

# The foreground reply task is the product's main chain.  It is reported beside
# plugin work, but its model calls must not consume plugin budget accounts.
MAIN_CHAIN_KIND = "conversation.reply"
CORE_CHAIN_KIND = "conversation.astrbot_core"


def usage_source(kind):
    return "conversation" if kind in (MAIN_CHAIN_KIND, CORE_CHAIN_KIND) else "plugin"


def _empty():
    data = dict.fromkeys(USAGE_FIELDS, 0)
    data.update(dict.fromkeys(_COST_META_FIELDS, 0))
    return data


def _add(target, row):
    for name in USAGE_FIELDS:
        target[name] += row.get(name, 0) or 0
    for name in _COST_META_FIELDS:
        target[name] += row.get(name, 0) or 0


def _cost_basis(recorded_calls, estimated_calls):
    if recorded_calls <= 0 and estimated_calls <= 0:
        return "unconfigured"
    if estimated_calls <= 0:
        return "recorded"
    if recorded_calls <= 0:
        return "estimated_local"
    return "mixed"


def _project(row, key=None):
    projected = {name: int(row.get(name) or 0) for name in USAGE_FIELDS
                 if name not in ("latency_total_ms", "latency_samples", "cost_microusd")}
    rec_calls = int(row.get("recorded_cost_calls") or 0)
    est_calls = int(row.get("estimated_cost_calls") or 0)
    if rec_calls + est_calls > 0:
        projected["cost_microusd"] = int(row.get("cost_microusd") or 0)
        projected["estimated_cost_microusd"] = (int(row.get("estimated_cost_microusd") or 0)
                                                if est_calls > 0 else None)
    else:
        projected["cost_microusd"] = None
        projected["estimated_cost_microusd"] = None
    projected["cost_basis"] = _cost_basis(rec_calls, est_calls)
    latency_count = int(row.get("latency_samples") or 0)
    projected["avg_latency_ms"] = (round(row["latency_total_ms"] / latency_count)
                                   if latency_count else None)
    if key is not None:
        projected["key"] = key
    return projected


def _dimensions(source, *, day=None, limit=8):
    entries = [_project(values, key[1] if day is not None else key)
               for key, values in source.items() if day is None or key[0] == day]
    return sorted(entries, key=lambda item: (-item["tokens"], item["key"]))[:limit]


def _recent(row):
    try:
        provider_record = json.loads(row["provider_json"])
    except (TypeError, ValueError):
        provider_record = None
    provider_id = provider_record.get("provider", "") if isinstance(provider_record, dict) else ""
    if type(provider_id) is not str:
        provider_id = ""
    input_tokens, cached_tokens = row["input_tokens"], row["cached_input_tokens"]
    cache_rate = (round(cached_tokens * 100 / input_tokens, 1)
                  if cached_tokens is not None and input_tokens is not None and input_tokens > 0 else None)
    result = {"kind": row["kind"], "provider": provider_id, "state": row["state"],
        "tokens": row["actual_tokens"], "cost_microusd": row["actual_cost"],
        "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
        "cache_creation_tokens": row["cache_creation_tokens"], "output_tokens": row["output_tokens"],
        "cache_hit_rate": cache_rate, "latency_ms": row["latency_ms"], "failure": row["failure_code"],
        "response_diagnostics": checked_response_diagnostics(
            row["response_summary_json"] if "response_summary_json" in row.keys() else None),
        "at": row["finished_at"] or row["started_at"] or row["created_at"]}
    if "reserved_tokens" in row.keys():
        released = is_released(row["receipt_ref"]) if "receipt_ref" in row.keys() else False
        if released:
            usage_state = "released"
        elif row["actual_tokens"] is not None:
            usage_state = "settled"
        else:
            usage_state = "awaiting_receipt"
        result["reserved_tokens"] = row["reserved_tokens"]
        result["held_tokens"] = 0 if released or row["actual_tokens"] is not None else row["reserved_tokens"]
        result["usage_state"] = usage_state
    for name in ("execution_path", "usage_scope", "token_availability", "cost_availability", "cost_basis",
                 "estimated_cost_microusd", "cache_availability", "signature_scope"):
        if name in row.keys():
            result[name] = row[name]
    if "cost_basis" not in result:
        result["cost_basis"] = "recorded" if result["cost_microusd"] is not None else "unconfigured"
    if "execution_path" not in result:
        result["execution_path"] = "legacy_v2_conversation" if row["kind"] == MAIN_CHAIN_KIND else "v2_background"
    return result


class ModelUsageReport:
    def __init__(self):
        self.by_day, self.by_provider, self.by_task = {}, {}, {}
        self.day_provider, self.day_task, self.hourly = {}, {}, {}
        self.recent_rows = []

    def add_group(self, row):
        day = row["day"]
        provider = row["provider"] if type(row["provider"]) is str and row["provider"] else "未知 Provider"
        kind = row["kind"] if type(row["kind"]) is str and row["kind"] else "unknown"
        keys = set(row.keys())
        values = {name: row[name] or 0 for name in USAGE_FIELDS}
        if any(name in keys for name in _COST_META_FIELDS):
            rec_calls = int(row["recorded_cost_calls"] or 0) if "recorded_cost_calls" in keys else 0
            est_calls = int(row["estimated_cost_calls"] or 0) if "estimated_cost_calls" in keys else 0
            rec_cost = int(row["recorded_cost_microusd"] or 0) if "recorded_cost_microusd" in keys else 0
            est_cost = int(row["estimated_cost_microusd"] or 0) if "estimated_cost_microusd" in keys else 0
            unpriced = int(row["unpriced_token_calls"] or 0) if "unpriced_token_calls" in keys else 0
            values["cost_microusd"] = rec_cost + est_cost
            values.update(recorded_cost_calls=rec_calls, estimated_cost_calls=est_calls,
                          recorded_cost_microusd=rec_cost, estimated_cost_microusd=est_cost,
                          unpriced_token_calls=unpriced)
        else:
            raw_cost = row["cost_microusd"] if "cost_microusd" in keys else None
            if raw_cost is not None:
                rec_calls = int(row["calls"] or 0) or 1
                rec_cost = int(raw_cost)
            else:
                rec_calls = 0
                rec_cost = 0
            values["cost_microusd"] = rec_cost
            values.update(recorded_cost_calls=rec_calls, estimated_cost_calls=0,
                          recorded_cost_microusd=rec_cost, estimated_cost_microusd=0,
                          unpriced_token_calls=0)
        for source, key in ((self.by_day, day), (self.by_provider, provider), (self.by_task, kind),
                            (self.day_provider, (day, provider)), (self.day_task, (day, kind))):
            _add(source.setdefault(key, _empty()), values)

    def add_hour(self, row):
        keys = set(row.keys())
        values = self.hourly.setdefault((row["day"], row["hour"]),
                                       {"calls": 0, "tokens": 0, "cost_microusd": 0,
                                        "recorded_cost_calls": 0, "estimated_cost_calls": 0})
        values["calls"] += int(row["calls"] or 0)
        values["tokens"] += int(row["tokens"] or 0)
        if "recorded_cost_calls" in keys or "estimated_cost_calls" in keys:
            rec_calls = int(row["recorded_cost_calls"] or 0) if "recorded_cost_calls" in keys else 0
            est_calls = int(row["estimated_cost_calls"] or 0) if "estimated_cost_calls" in keys else 0
            values["recorded_cost_calls"] += rec_calls
            values["estimated_cost_calls"] += est_calls
            values["cost_microusd"] += int(row["cost_microusd"] or 0) if (rec_calls + est_calls) > 0 else 0
        else:
            raw_cost = row["cost_microusd"] if "cost_microusd" in keys else None
            if raw_cost is not None:
                values["recorded_cost_calls"] += int(row["calls"] or 0) or 1
                values["cost_microusd"] += int(raw_cost)

    def add_recent(self, row):
        self.recent_rows.append(row)

    def _cost_totals(self):
        return {
            name: sum(row[name] for row in self.by_day.values())
            for name in _COST_META_FIELDS
        }

    def project(self, now):
        today = now.date().isoformat()
        periods = [(now.date() - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
        windows = {}
        for label, length in (("7d", 7), ("30d", 30)):
            start = (now.date() - timedelta(days=length - 1)).isoformat()
            totals, providers, tasks = _empty(), {}, {}
            for period, values in self.by_day.items():
                if start <= period <= today:
                    _add(totals, values)
            for (period, provider), values in self.day_provider.items():
                if start <= period <= today:
                    _add(providers.setdefault(provider, _empty()), values)
            for (period, kind), values in self.day_task.items():
                if start <= period <= today:
                    _add(tasks.setdefault(kind, _empty()), values)
            windows[label] = {"start_day": start, "end_day": today, "totals": _project(totals),
                             "providers": _dimensions(providers, limit=100), "tasks": _dimensions(tasks, limit=100)}

        history = []
        for period in sorted(self.by_day):
            entry = _project(self.by_day[period], period)
            entry["providers"] = _dimensions(self.day_provider, day=period)
            entry["tasks"] = _dimensions(self.day_task, day=period)
            history.append(entry)
        history_by_day = {row["key"]: row for row in history}
        daily = [history_by_day.get(period, _project(_empty(), period)) for period in periods]
        all_fields = USAGE_FIELDS + _COST_META_FIELDS
        all_time = _project({name: sum(row[name] for row in self.by_day.values()) for name in all_fields})
        all_time.update(providers=_dimensions(self.by_provider, limit=12),
                        tasks=_dimensions(self.by_task, limit=16), days=len(history))

        recent_rows = sorted(self.recent_rows,
            key=lambda row: (row["finished_at"] or row["started_at"] or row["created_at"] or "",
                             row["position"] or 0), reverse=True)[:80]
        summary = _project({name: sum(self.by_day.get(period, _empty())[name] for period in periods)
                            for name in all_fields})
        known_calls = summary["calls"] - summary["unconfirmed_calls"]
        summary.update(known_calls=known_calls,
            cache_coverage_pct=round(summary["cache_reported_calls"] * 100 / known_calls, 1) if known_calls else None,
            cache_hit_rate=round(summary["cached_input_tokens"] * 100 / summary["cache_input_tokens"], 1)
                if summary["cache_input_tokens"] else None)
        hourly = []
        for (day, hour), values in sorted(self.hourly.items()):
            rec_calls = values["recorded_cost_calls"]
            est_calls = values["estimated_cost_calls"]
            hourly.append({
                "day": day, "hour": hour,
                "calls": values["calls"], "tokens": values["tokens"],
                "cost_microusd": values["cost_microusd"] if (rec_calls + est_calls) > 0 else None,
                "cost_basis": _cost_basis(rec_calls, est_calls),
            })
        return {"summary": summary, "daily": daily, "history": history, "totals": all_time, "windows": windows,
                "by_provider": _dimensions(self.by_provider, limit=100), "by_task": _dimensions(self.by_task, limit=100),
                "hourly": hourly,
                "recent": [_recent(row) for row in recent_rows]}
