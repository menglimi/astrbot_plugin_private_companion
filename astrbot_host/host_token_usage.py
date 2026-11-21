"""Read AstrBot's persisted internal-agent usage, independently of V2 budgets."""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Lock

from companion_v2.model_gateway.maintenance_cache import _PreparedPayload

_HOST_USAGE_CACHE_LOCK = Lock()
_HOST_USAGE_CACHE: OrderedDict[tuple, tuple[datetime, datetime | None, _PreparedPayload]] = OrderedDict()
_HOST_USAGE_CACHE_MAX = 4


def _file_stat_token(path: Path) -> tuple[int, int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size)


def _resolve_sqlite_file(db_helper) -> Path | None:
    raw = getattr(db_helper, "db_path", None)
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw).strip()
    if not text or text == ":memory:" or text.startswith("file:"):
        return None
    path = Path(text)
    if not path.is_file():
        return None
    return path


def _db_file_fingerprint(path: Path) -> tuple | None:
    db_stat = _file_stat_token(path)
    if db_stat is None:
        return None
    wal_stat = _file_stat_token(Path(str(path) + "-wal"))
    return (db_stat, wal_stat)


def _unavailable(reason):
    return {"available": False, "installed": True, "display_name": "AstrBot 全部会话",
            "reason": reason, "counted_in_private_companion_budget": False}


def _bucket(row):
    values = dict(row)
    calls = int(values.get("calls") or 0)
    unknown = int(values.get("unconfirmed_calls") or 0)
    measured = calls == 0 or unknown < calls
    other = int(values.get("input_other") or 0)
    cached = int(values.get("input_cached") or 0)
    output = int(values.get("output_tokens") or 0)
    samples = int(values.get("latency_samples") or 0)
    return {
        "calls": calls, "tokens": other + cached + output if measured else None,
        "input_tokens": other + cached if measured else None,
        "output_tokens": output if measured else None,
        "reported_tokens": other + cached + output if measured else None,
        "estimated_tokens": 0, "unconfirmed_calls": unknown,
        "usage_reported_calls": calls - unknown, "failures": int(values.get("failures") or 0),
        "cached_input_tokens": cached if cached > 0 else None,
        "cache_creation_tokens": None, "cache_input_tokens": 0,
        "cache_reported_calls": 0, "cache_telemetry_calls": int(values.get("cache_samples") or 0),
        # ProviderStat does not retain whether a zero cache count was reported.
        "cache_hit_reported": False, "cost_microusd": None,
        "latency_samples": samples,
        "avg_latency_ms": round(float(values.get("elapsed_ms") or 0) / samples) if samples else None,
    }


async def read_astrbot_token_usage(controller, *, now=None):
    """Use the host DB API; select only counters, never conversation content."""
    context = getattr(controller, "context", None)
    getter = getattr(context, "get_db", None)
    if not callable(getter):
        return _unavailable("当前宿主未提供全局用量数据库接口。")
    try:
        from sqlalchemy import case, func, select
        from astrbot.core.db.po import ProviderStat

        explicit_now = now is not None
        now = now or datetime.now().astimezone()
        if now.tzinfo is None:
            now = now.astimezone()
        db_helper = getter()
        tz_offset_seconds = int(now.utcoffset().total_seconds())
        db_file = None if explicit_now else _resolve_sqlite_file(db_helper)
        file_fp_before = _db_file_fingerprint(db_file) if db_file is not None else None
        cache_key = (
            (str(db_file), now.date().isoformat(), tz_offset_seconds, file_fp_before)
            if (db_file is not None and file_fp_before is not None)
            else None
        )
        end = now.astimezone(timezone.utc).replace(tzinfo=None)
        if cache_key is not None:
            with _HOST_USAGE_CACHE_LOCK:
                cached = _HOST_USAGE_CACHE.get(cache_key)
                if cached is not None:
                    cached_at, next_stat_at, prepared = cached
                    if cached_at <= end and (next_stat_at is None or end < next_stat_at):
                        _HOST_USAGE_CACHE.move_to_end(cache_key)
                        cloned = prepared.clone()
                        cloned["updated_at"] = now.isoformat()
                        return cloned
        start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                 - timedelta(days=365)).astimezone(timezone.utc).replace(tzinfo=None)
        offset = f"{tz_offset_seconds} seconds"
        day = func.strftime("%Y-%m-%d", ProviderStat.created_at, offset)
        hour = func.strftime("%Y-%m-%dT%H", ProviderStat.created_at, offset)
        token_count = (ProviderStat.token_input_other + ProviderStat.token_input_cached
                       + ProviderStat.token_output)
        duration_known = ((ProviderStat.start_time > 0)
                          & (ProviderStat.end_time >= ProviderStat.start_time))
        counters = [func.count().label("calls"),
            func.sum(ProviderStat.token_input_other).label("input_other"),
            func.sum(ProviderStat.token_input_cached).label("input_cached"),
            func.sum(ProviderStat.token_output).label("output_tokens"),
            func.sum(case((token_count == 0, 1), else_=0)).label("unconfirmed_calls"),
            func.sum(case((ProviderStat.status == "error", 1), else_=0)).label("failures"),
            func.sum(case((ProviderStat.token_input_cached > 0, 1), else_=0)).label("cache_samples"),
            func.sum(case((duration_known, 1), else_=0)).label("latency_samples"),
            func.sum(case((duration_known, (ProviderStat.end_time - ProviderStat.start_time) * 1000),
                          else_=0)).label("elapsed_ms")]
        internal_stats = ProviderStat.agent_type == "internal"
        conditions = (internal_stats, ProviderStat.created_at <= end)
        next_stat_at = None
        async with db_helper.get_db() as session:
            totals = _bucket((await session.execute(select(*counters).where(*conditions))).mappings().one())
            providers = (await session.execute(select(ProviderStat.provider_id.label("key"), *counters)
                .where(*conditions).group_by(ProviderStat.provider_id)
                .order_by(func.sum(token_count).desc(), ProviderStat.provider_id).limit(32))).mappings().all()
            days = (await session.execute(select(day.label("key"), *counters)
                .where(*conditions, ProviderStat.created_at >= start)
                .group_by(day).order_by(day))).mappings().all()
            day_providers = (await session.execute(select(day.label("day"),
                ProviderStat.provider_id.label("key"), *counters)
                .where(*conditions, ProviderStat.created_at >= start)
                .group_by(day, ProviderStat.provider_id)
                .order_by(day, func.sum(token_count).desc()))).mappings().all()
            hours = (await session.execute(select(hour.label("key"), *counters)
                .where(*conditions, ProviderStat.created_at >= start)
                .group_by(hour).order_by(hour.desc()).limit(72))).mappings().all()
            recent = (await session.execute(select(ProviderStat.id, ProviderStat.created_at,
                ProviderStat.provider_id, ProviderStat.provider_model, ProviderStat.status,
                ProviderStat.token_input_other, ProviderStat.token_input_cached, ProviderStat.token_output,
                ProviderStat.start_time, ProviderStat.end_time)
                .where(*conditions).order_by(ProviderStat.created_at.desc(), ProviderStat.id.desc())
                .limit(80))).mappings().all()
            if cache_key is not None:
                next_stat_at = (await session.execute(select(func.min(ProviderStat.created_at))
                    .where(internal_stats, ProviderStat.created_at > end))).scalar_one()

        by_day = {}
        for item in days:
            bucket = _bucket(item)
            by_day[item["key"]] = {"key": item["key"], "day": item["key"], **bucket,
                "providers": [], "tasks": [{"key": "conversation.reply", **bucket}]}
        for item in day_providers:
            target = by_day[item["day"]]["providers"]
            if len(target) < 32:
                target.append({"key": item["key"] or "未知 Provider", **_bucket(item)})
        recent_rows = []
        for item in recent:
            created = item["created_at"]
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            bucket = _bucket({"calls": 1,
                "input_other": item["token_input_other"], "input_cached": item["token_input_cached"],
                "output_tokens": item["token_output"],
                "unconfirmed_calls": int(not (item["token_input_other"] + item["token_input_cached"]
                                              + item["token_output"])),
                "latency_samples": int(item["start_time"] > 0 and item["end_time"] >= item["start_time"]),
                "elapsed_ms": max(0, item["end_time"] - item["start_time"]) * 1000})
            recent_rows.append({**bucket, "at": created.astimezone(now.tzinfo).isoformat(timespec="seconds"),
                "kind": "conversation.reply", "provider": item["provider_id"] or "未知 Provider",
                "model": item["provider_model"] or "", "latency_ms": bucket["avg_latency_ms"],
                "state": {"error": "discarded", "aborted": "unknown"}.get(item["status"], "generated"),
                "failure": "provider_request_failed" if item["status"] == "error" else None,
                "cache_hit_rate": None,
                "usage_availability": "reported" if bucket["tokens"] is not None else "unavailable"})
        daily = list(by_day.values())
        result = {"available": True, "installed": True, "display_name": "AstrBot 全部会话",
            "source": "astrbot.provider_stats", "counted_in_private_companion_budget": False,
            "updated_at": now.isoformat(), "totals": totals, "daily": daily, "history": daily,
            "by_provider": [{"key": item["key"] or "未知 Provider", **_bucket(item)} for item in providers],
            "by_task": [{"key": "conversation.reply", **totals}], "recent": recent_rows,
            "hourly": [{"key": item["key"], "day": item["key"][:10], "hour": item["key"][11:13],
                        **_bucket(item)} for item in reversed(hours)],
            "note": "按核心回复轮次汇总，包含工具续轮；缓存命中率与费用未提供。"}
        if cache_key is not None and _db_file_fingerprint(db_file) == file_fp_before:
            prepared = _PreparedPayload(result)
            with _HOST_USAGE_CACHE_LOCK:
                if cache_key in _HOST_USAGE_CACHE:
                    _HOST_USAGE_CACHE.move_to_end(cache_key)
                _HOST_USAGE_CACHE[cache_key] = (end, next_stat_at, prepared)
                while len(_HOST_USAGE_CACHE) > _HOST_USAGE_CACHE_MAX:
                    _HOST_USAGE_CACHE.popitem(last=False)
        return result
    except (ImportError, AttributeError):
        return _unavailable("当前 AstrBot 版本未提供兼容的核心回复用量记录。")
    except Exception:
        return _unavailable("AstrBot 全局用量读取失败，请稍后重新读取。")
