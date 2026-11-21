"""Read bounded token summaries from the V2 services and public memory bridge."""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Any


SOURCE_SPECS = {
    "memory": {
        "plugins": {"astrbot_plugin_remember_you", "astrbot_plugin_memory_companion"},
        "getters": ("get_memory_companion_bridge", "get_active_bridge"),
        "display_name": "记忆插件",
    },
    "together": {
        "display_name": "一起房间",
    },
    "reality": {
        "display_name": "现实观察",
    },
    "live": {
        "display_name": "直播与表演",
    },
    "game": {
        "display_name": "共同游玩",
    },
}


def _integer(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def _text(value: Any, limit: int = 160) -> str:
    if type(value) not in (str, int, float):
        return ""
    return " ".join(str(value).split())[:limit]


def _bucket(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    calls = _integer(row.get("calls"))
    token_value = row.get("total_tokens", row.get("tokens"))
    tokens = _integer(token_value) if token_value is not None else None
    elapsed = _integer(row.get("elapsed_ms"))
    latency = row.get("avg_latency_ms")
    try:
        latency = round(float(latency)) if latency is not None and math.isfinite(float(latency)) else None
    except (TypeError, ValueError, OverflowError):
        latency = None
    if latency is None and calls:
        latency = round(elapsed / calls) if elapsed else None
    reported = row.get("reported_tokens")
    estimated = _integer(row.get("estimated_tokens"))
    if "estimated_tokens" not in row and row.get("estimated") is True:
        estimated = tokens or 0
    if "reported_tokens" not in row:
        reported = max(0, tokens - estimated) if tokens is not None else None

    input_value = row.get("prompt_tokens", row.get("input_tokens"))
    input_present = input_value is not None
    input_tokens = _integer(input_value) if input_present else None
    cache_read_key = next((key for key in ("cache_read_tokens", "cached_tokens", "cached_input_tokens")
                           if row.get(key) is not None), None)
    cache_write_key = next((key for key in ("cache_write_tokens", "cache_creation_tokens")
                            if row.get(key) is not None), None)
    cache_other_present = row.get("cache_other_input_tokens") is not None
    explicit_cache_input = row.get("cache_input_tokens")
    explicit_reported_calls = row.get("cache_reported_calls")
    explicit_telemetry_calls = row.get("cache_telemetry_calls")
    explicit_hit = row.get("cache_hit_reported")
    if estimated and explicit_hit is None and explicit_telemetry_calls is None:
        # Old estimates contain synthesized zero cache fields; they are no receipt.
        cache_read_key = cache_write_key = None
        explicit_hit = False
        explicit_reported_calls = explicit_telemetry_calls = 0
    cache_read = _integer(row.get(cache_read_key)) if cache_read_key else 0
    cache_write = _integer(row.get(cache_write_key)) if cache_write_key else 0
    cache_other = _integer(row.get("cache_other_input_tokens"))
    has_cache_receipt = (cache_read_key is not None or cache_write_key is not None or cache_other_present
                         or explicit_cache_input is not None or _integer(explicit_reported_calls) > 0
                         or _integer(explicit_telemetry_calls) > 0 or explicit_hit is True)
    cache_hit_reported = explicit_hit if type(explicit_hit) is bool else bool(input_present and cache_read_key is not None)
    cache_input_tokens = _integer(explicit_cache_input) if explicit_cache_input is not None else (
        input_tokens if input_present and cache_hit_reported else 0)
    cache_reported_calls = _integer(explicit_reported_calls) if explicit_reported_calls is not None else (
        calls if cache_hit_reported else 0)
    cache_telemetry_calls = _integer(explicit_telemetry_calls) if explicit_telemetry_calls is not None else (
        calls if has_cache_receipt else 0)
    return {
        "calls": calls,
        "tokens": tokens,
        "input_tokens": input_tokens,
        "output_tokens": (_integer(row.get("completion_tokens", row.get("output_tokens")))
                          if row.get("completion_tokens", row.get("output_tokens")) is not None else None),
        "failures": _integer(row.get("errors", row.get("failures"))),
        "reported_tokens": _integer(reported) if reported is not None else None,
        "estimated_tokens": estimated,
        "unconfirmed_calls": _integer(row.get("unconfirmed_calls")),
        "usage_reported_calls": _integer(row.get("usage_reported_calls")),
        "cache_reported_calls": cache_reported_calls,
        "cache_telemetry_calls": cache_telemetry_calls,
        "cache_input_tokens": cache_input_tokens,
        "cached_input_tokens": cache_read if cache_read_key else None,
        "cache_creation_tokens": cache_write if cache_write_key else None,
        "cache_other_input_tokens": cache_other,
        "cache_hit_reported": cache_hit_reported,
        "avg_latency_ms": latency,
        "cost_microusd": (_integer(row.get("cost_microusd"))
                          if row.get("cost_microusd") is not None else None),
    }


def _rows(value: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        result = [(str(key), item) for key, item in value.items() if isinstance(item, Mapping)]
    elif isinstance(value, list):
        result = [(str(item.get("key") or item.get("provider") or item.get("task") or "未知"), item)
                  for item in value if isinstance(item, Mapping)]
    else:
        result = []
    return result


def _ranked(value: Any, *, limit: int = 32) -> list[dict[str, Any]]:
    rows = [{"key": key, **_bucket(bucket)} for key, bucket in _rows(value)]
    rows.sort(key=lambda row: (row["tokens"] or 0, row["calls"], row["key"]), reverse=True)
    return rows[:limit]


def _series(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows = [{"key": key, **_bucket(bucket)} for key, bucket in _rows(value)]
    rows.sort(key=lambda row: row["key"])
    return rows[-limit:]


def _recent(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[-80:]:
        if not isinstance(item, Mapping):
            continue
        success = bool(item.get("success", True))
        timestamp = item.get("time")
        if not timestamp and item.get("ts") is not None:
            try:
                timestamp = datetime.fromtimestamp(float(item["ts"])).isoformat(timespec="seconds")
            except (TypeError, ValueError, OverflowError, OSError):
                timestamp = ""
        bucket = _bucket({**item, "calls": 1})
        hit_reported = bucket["cache_hit_reported"]
        result.append({
            "at": _text(timestamp, 32),
            "kind": _text(item.get("task"), 80) or "外部模型任务",
            "provider": _text(item.get("provider"), 100) or "未知 Provider",
            "state": "generated" if success else "discarded",
            "tokens": bucket["tokens"],
            "input_tokens": bucket["input_tokens"],
            "output_tokens": bucket["output_tokens"],
            "cached_input_tokens": bucket["cached_input_tokens"],
            "cache_creation_tokens": bucket["cache_creation_tokens"],
            "cache_hit_rate": (round(bucket["cached_input_tokens"] * 100 / bucket["input_tokens"], 1)
                               if hit_reported and bucket["input_tokens"] and bucket["cached_input_tokens"] is not None else None),
            "cache_hit_reported": hit_reported,
            "latency_ms": bucket["avg_latency_ms"],
            "failure": "provider_request_failed" if not success else None,
            "estimated_tokens": bucket["estimated_tokens"],
            "reported_tokens": bucket["reported_tokens"],
            "usage_availability": _text(item.get("usage_availability"), 32) or (
                "estimated" if bucket["estimated_tokens"] else "reported" if bucket["tokens"] is not None else "unavailable"),
        })
    return result[::-1]


def _summary(raw: Any, source: str, *, installed: bool) -> dict[str, Any]:
    spec = SOURCE_SPECS[source]
    if not isinstance(raw, Mapping):
        raw = {}
    available = bool(raw.get("available", True))
    by_day_raw = raw.get("by_day")
    by_provider_raw = raw.get("by_day_provider") if isinstance(raw.get("by_day_provider"), Mapping) else {}
    by_task_raw = raw.get("by_day_task") if isinstance(raw.get("by_day_task"), Mapping) else {}
    daily = _series(by_day_raw, limit=366)
    for row in daily:
        row["day"] = row["key"]
        row["providers"] = _ranked(by_provider_raw.get(row["key"], {}), limit=8)
        row["tasks"] = _ranked(by_task_raw.get(row["key"], {}), limit=10)
    hourly = _series(raw.get("by_hour"), limit=72)
    for row in hourly:
        key = row["key"].replace(" ", "T")
        row["day"] = key[:10]
        row["hour"] = key[11:13] if len(key) >= 13 else "00"
    return {
        "available": available,
        "installed": bool(raw.get("installed", installed)),
        "display_name": _text(raw.get("display_name") or spec["display_name"], 80),
        "reason": _text(raw.get("reason"), 180),
        "note": _text(raw.get("note"), 300),
        "updated_at": _text(raw.get("updated_at"), 32),
        "counted_in_private_companion_budget": bool(raw.get("counted_in_private_companion_budget", False)),
        "totals": _bucket(raw.get("totals")),
        "by_provider": _ranked(raw.get("by_provider")),
        "by_task": _ranked(raw.get("by_task")),
        "daily": daily,
        "history": daily,
        "hourly": hourly,
        "recent": _recent(raw.get("recent")),
    }


def _loaded_summary(source: str) -> tuple[dict[str, Any] | None, bool]:
    spec = SOURCE_SPECS[source]
    installed = False
    seen: set[int] = set()
    for loaded_name, module in tuple(sys.modules.items()):
        if module is None or id(module) in seen:
            continue
        seen.add(id(module))
        try:
            namespace = object.__getattribute__(module, "__dict__")
        except (AttributeError, TypeError):
            continue
        if not isinstance(namespace, dict):
            continue
        module_name = str(namespace.get("__name__", loaded_name))
        plugin_name = str(namespace.get("PLUGIN_NAME", ""))
        if plugin_name not in spec["plugins"] and not any(module_name.endswith("." + item) for item in spec["plugins"]):
            continue
        installed = True
        for getter_name in spec["getters"]:
            getter = namespace.get(getter_name)
            if not callable(getter):
                continue
            try:
                bridge = getter()
                summary_getter = getattr(bridge, "get_token_usage_summary", None) if bridge is not None else None
                raw = summary_getter() if callable(summary_getter) else None
            except Exception:
                return None, installed
            if isinstance(raw, Mapping):
                return dict(raw), installed
    return None, installed


def _native_summary(controller, source) -> tuple[dict[str, Any] | None, bool]:
    registry = getattr(controller, "native_extensions", None)
    lookup = getattr(registry, "service", None)
    service = lookup(source) if callable(lookup) else None
    if service is None:
        return None, False
    tracker = getattr(service, "token_usage", None)
    summary = getattr(tracker, "summary", None)
    try:
        raw = summary() if callable(summary) else None
    except Exception:
        return None, True
    return (dict(raw) if isinstance(raw, Mapping) else None), True


def read_token_sources(controller=None) -> dict[str, dict[str, Any]]:
    """Internal services count their own calls; ordinary core replies stay separate."""
    result = {}
    for source in SOURCE_SPECS:
        try:
            raw, installed = (_loaded_summary(source) if source == "memory"
                              else _native_summary(controller, source))
            if raw is None:
                raw = {"available": False, "installed": installed,
                       "reason": (SOURCE_SPECS[source]["display_name"] + "内部统计暂不可用" if source != "memory"
                                  else "插件未运行或没有可用的 Token 统计桥")}
            result[source] = _summary(raw, source, installed=installed)
        except Exception:
            spec = SOURCE_SPECS[source]
            result[source] = _summary({"available": False, "installed": False,
                "reason": "统计桥暂时不可用"}, source, installed=False)
    return result
