from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # Standalone unit tests do not load the AstrBot runtime.
    logger = logging.getLogger(__name__)

SUPPORTED_API_VERSION = 1
BODY_MONITOR_MODULE = "data.plugins.astrbot_plugin_body_monitor.main"
BODY_MONITOR_MODULES = (
    BODY_MONITOR_MODULE,
    "astrbot_plugin_body_monitor.main",
)
STATE_KEY = "body_monitor_integration"
CONTEXT_KEY = "body_monitor_health_context"


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = _text(value, 80)
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        return max(0.0, datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _cursor(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _identifier(value: Any, limit: int = 64) -> str:
    text = _text(value, limit)
    return text if re.fullmatch(r"[A-Za-z0-9_.-]+", text) else ""


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return None


def _unit(value: Any) -> str:
    normalized = "".join(str(value or "").split()).lower()[:24]
    aliases = {
        "bpm": "bpm",
        "%": "%",
        "percent": "%",
        "percentage": "%",
        "c": "°C",
        "°c": "°C",
        "celsius": "°C",
        "f": "°F",
        "°f": "°F",
        "fahrenheit": "°F",
        "h": "h",
        "hr": "h",
        "hour": "h",
        "hours": "h",
        "min": "min",
        "minute": "min",
        "minutes": "min",
        "steps": "steps",
        "step": "steps",
        "kg": "kg",
        "g": "g",
        "cm": "cm",
        "mmhg": "mmHg",
        "mmol/l": "mmol/L",
        "mg/dl": "mg/dL",
        "score": "score",
        "次/分": "次/分",
        "次/分钟": "次/分钟",
        "小时": "小时",
        "分钟": "分钟",
        "步": "步",
    }
    return aliases.get(normalized, "")


def _error_text(value: Any, limit: int = 180) -> str:
    text = _text(value, max(limit * 2, 240))
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password)\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[:limit]


class BodyMonitorIntegration:
    """Own Body Monitor discovery, cursor semantics and candidate projection."""

    def __init__(self, host: Any) -> None:
        self._host = host
        self._poll_lock = asyncio.Lock()

    def _state(self) -> dict[str, Any]:
        data = getattr(self._host, "data", None)
        if not isinstance(data, dict):
            data = {}
            self._host.data = data
        state = data.setdefault(STATE_KEY, {})
        if not isinstance(state, dict):
            state = {}
            data[STATE_KEY] = state
        state.setdefault("enabled_last", False)
        state.setdefault("initialized", False)
        state.setdefault("stream_id", "")
        state.setdefault("cursor", None)
        state.setdefault("status", "disabled")
        state.setdefault("api_version", 0)
        state.setdefault("last_pull_at", 0.0)
        state.setdefault("last_success_at", 0.0)
        state.setdefault("last_error", "")
        state.setdefault("has_more", False)
        state.setdefault("last_batch", {})
        state.setdefault("generation", 0)
        return state

    def _save(self, *, include_candidates: bool = False) -> None:
        save = getattr(self._host, "_save_data_sync", None)
        if callable(save):
            sections = {STATE_KEY}
            if include_candidates:
                # Offering a candidate mutates both the owner profile and the
                # shared review pool in addition to this integration cursor.
                sections.update({"users", "proactive_candidate_pool"})
            save(sections=sections)

    @asynccontextmanager
    async def _host_data_locked(self):
        lock = getattr(self._host, "_data_lock", None)
        if hasattr(lock, "__aenter__") and hasattr(lock, "__aexit__"):
            async with lock:
                yield
            return
        yield

    @staticmethod
    def _reset_cursor(state: dict[str, Any]) -> None:
        state["initialized"] = False
        state["stream_id"] = ""
        state["cursor"] = None
        state["has_more"] = False

    def _clear_persisted_health_contexts(self) -> None:
        users = getattr(self._host, "data", {}).get("users", {})
        if not isinstance(users, dict):
            return
        clearer = getattr(self._host, "_clear_pending_proactive_plan", None)
        for user in users.values():
            if not isinstance(user, dict):
                continue
            if _text(user.get("planned_proactive_source"), 40) == "body_monitor" and callable(clearer):
                clearer(user)
            user.pop(CONTEXT_KEY, None)
            impulses = user.get("proactive_impulses")
            if not isinstance(impulses, list):
                continue
            for impulse in impulses:
                if not isinstance(impulse, dict) or _text(impulse.get("source"), 40) != "body_monitor":
                    continue
                impulse.pop("context", None)
                impulse["context_key"] = ""
                if _text(impulse.get("state"), 24) in {"", "queued", "deferred"}:
                    impulse["state"] = "blocked"
                    impulse["last_note"] = "Body Monitor 联动已关闭"

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        enabled = bool(enabled)
        setattr(self._host, "enable_body_monitor_integration", enabled)
        lock = getattr(self._host, "_data_lock", None)
        if isinstance(lock, asyncio.Lock):
            async with lock:
                state = self._state()
                previous = bool(state.get("enabled_last"))
                changed = previous != enabled
                if changed:
                    self._reset_cursor(state)
                    state["generation"] = int(state.get("generation") or 0) + 1
                    if not enabled:
                        self._clear_persisted_health_contexts()
                state["enabled_last"] = enabled
                if changed or not enabled or not state.get("initialized"):
                    state["status"] = "initializing" if enabled else "disabled"
                    state["last_error"] = ""
                if changed:
                    self._save(include_candidates=not enabled)
        else:
            state = self._state()
            previous = bool(state.get("enabled_last"))
            changed = previous != enabled
            if changed:
                self._reset_cursor(state)
                state["generation"] = int(state.get("generation") or 0) + 1
                if not enabled:
                    self._clear_persisted_health_contexts()
            state["enabled_last"] = enabled
            if changed or not enabled or not state.get("initialized"):
                state["status"] = "initializing" if enabled else "disabled"
                state["last_error"] = ""
            if changed:
                self._save(include_candidates=not enabled)
        return self.status_view()

    async def poll(self) -> dict[str, Any]:
        async with self._poll_lock:
            if not bool(getattr(self._host, "enable_body_monitor_integration", False)):
                state = self._state()
                if state.get("enabled_last"):
                    await self.set_enabled(False)
                    state = self._state()
                state["status"] = "disabled"
                return self.status_view()

            state = self._state()
            if not state.get("enabled_last"):
                await self.set_enabled(True)
                state = self._state()

            started_at = time.time()
            state["last_pull_at"] = started_at
            state["status"] = "initializing" if not state.get("initialized") else "connected"
            state["last_error"] = ""
            request_generation = int(state.get("generation") or 0)

            try:
                module = self._load_body_monitor_module()
            except Exception as exc:
                return self._record_error(state, "error", exc)
            if module is None:
                state["status"] = "not_installed"
                state["last_error"] = "Body Monitor 未安装或尚未加载"
                self._save()
                return self.status_view()

            getter = getattr(module, "get_body_monitor_api", None)
            if not callable(getter):
                state["status"] = "not_installed"
                state["last_error"] = "Body Monitor 未提供联动接口"
                self._save()
                return self.status_view()
            try:
                api = getter()
            except Exception as exc:
                return self._record_error(state, "error", exc)
            if api is None:
                state["status"] = "not_installed"
                state["last_error"] = "Body Monitor 尚未初始化"
                self._save()
                return self.status_view()

            api_version = _cursor(getattr(api, "proactive_event_api_version", None)) or 0
            state["api_version"] = api_version
            if api_version != SUPPORTED_API_VERSION:
                state["status"] = "incompatible"
                state["last_error"] = f"接口版本不兼容: {api_version or 'unknown'}"
                self._save()
                return self.status_view()

            after_cursor = state.get("cursor") if state.get("initialized") else None
            try:
                feed = await self._read_feed(api, after_cursor=after_cursor)
                if state.get("initialized") and _text(feed.get("stream_id"), 80) != _text(state.get("stream_id"), 80):
                    feed = await self._read_feed(api, after_cursor=None)
                    after_cursor = None
                elif after_cursor is not None and feed["next_cursor"] < after_cursor:
                    raise RuntimeError("Body Monitor 返回的事件游标发生倒退")
                elif after_cursor is not None and feed.get("has_more") and feed["next_cursor"] == after_cursor:
                    raise RuntimeError("Body Monitor 事件批次未推进游标")
            except Exception as exc:
                if int(self._state().get("generation") or 0) != request_generation:
                    return self.status_view()
                return self._record_error(state, "error", exc)

            if after_cursor is None:
                async with self._host_data_locked():
                    state = self._state()
                    if (
                        int(state.get("generation") or 0) != request_generation
                        or not bool(getattr(self._host, "enable_body_monitor_integration", False))
                        or not bool(state.get("enabled_last"))
                    ):
                        return self.status_view()
                    self._apply_initialization(state, feed, started_at)
                    self._save()
                return self.status_view()

            old_cursor = state.get("cursor")
            try:
                async with self._host_data_locked():
                    state = self._state()
                    if (
                        int(state.get("generation") or 0) != request_generation
                        or not bool(getattr(self._host, "enable_body_monitor_integration", False))
                        or not bool(state.get("enabled_last"))
                    ):
                        return self.status_view()
                    old_cursor = state.get("cursor")
                    batch = self._process_events(feed)
                    state["stream_id"] = feed["stream_id"]
                    state["cursor"] = feed["next_cursor"]
                    state["initialized"] = True
                    state["status"] = "connected"
                    state["last_success_at"] = time.time()
                    state["last_error"] = ""
                    state["has_more"] = bool(feed.get("has_more"))
                    state["last_batch"] = batch
                    self._save(include_candidates=True)
            except Exception as exc:
                if int(self._state().get("generation") or 0) != request_generation:
                    return self.status_view()
                state["cursor"] = old_cursor
                return self._record_error(state, "error", exc)
            return self.status_view()

    @staticmethod
    def _load_body_monitor_module() -> Any | None:
        for module_name in BODY_MONITOR_MODULES:
            try:
                return importlib.import_module(module_name)
            except ModuleNotFoundError as exc:
                missing = str(getattr(exc, "name", "") or "")
                if missing and (module_name == missing or module_name.startswith(f"{missing}.")):
                    continue
                raise
        return None

    async def _read_feed(self, api: Any, *, after_cursor: int | None) -> dict[str, Any]:
        reader = getattr(api, "read_proactive_events", None)
        if not callable(reader):
            raise RuntimeError("Body Monitor 联动接口缺少 read_proactive_events")
        payload = reader(after_cursor=after_cursor, limit=32)
        if inspect.isawaitable(payload):
            payload = await payload
        if not isinstance(payload, dict):
            raise RuntimeError("Body Monitor 返回了无效事件批次")
        version = _cursor(payload.get("version"))
        if version != SUPPORTED_API_VERSION:
            raise RuntimeError(f"Body Monitor 事件批次版本不兼容: {version or 'unknown'}")
        stream_id = " ".join(str(payload.get("stream_id") or "").split())
        next_cursor = _cursor(payload.get("next_cursor"))
        latest_cursor = _cursor(payload.get("latest_cursor"))
        events = payload.get("events")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", stream_id)
            or next_cursor is None
            or latest_cursor is None
            or not isinstance(events, list)
        ):
            raise RuntimeError("Body Monitor 返回的事件批次字段不完整")
        if latest_cursor < next_cursor:
            raise RuntimeError("Body Monitor 返回的事件游标顺序无效")
        if after_cursor is None and events:
            events = []
        return {
            "version": version,
            "stream_id": stream_id,
            "next_cursor": next_cursor,
            "latest_cursor": latest_cursor,
            "has_more": bool(payload.get("has_more")),
            "events": events,
        }

    @staticmethod
    def _apply_initialization(state: dict[str, Any], feed: dict[str, Any], now: float) -> None:
        state["stream_id"] = feed["stream_id"]
        state["cursor"] = feed["latest_cursor"]
        state["initialized"] = True
        state["status"] = "connected"
        state["last_success_at"] = now
        state["last_error"] = ""
        state["has_more"] = False
        state["last_batch"] = {
            "received": 0,
            "offered": 0,
            "accepted": 0,
            "skipped": 0,
            "duplicate": 0,
            "expired": 0,
            "initialized": True,
        }

    def _process_events(self, feed: dict[str, Any]) -> dict[str, int | bool]:
        batch: dict[str, int | bool] = {
            "received": len(feed["events"]),
            "offered": 0,
            "accepted": 0,
            "skipped": 0,
            "duplicate": 0,
            "expired": 0,
            "initialized": False,
        }
        now = time.time()
        for raw in feed["events"]:
            event = self._normalize_event(raw)
            if event is None:
                batch["skipped"] += 1
                continue
            if event["expires_at"] <= now:
                batch["expired"] += 1
                batch["skipped"] += 1
                continue
            targets = event["targets"]
            matched = self._matched_users(targets)
            if not matched:
                batch["skipped"] += 1
                continue
            for user_id, user, target_umo in matched:
                candidate = self._candidate(feed["stream_id"], event, target_umo, now=now)
                offer = getattr(self._host, "_offer_proactive_candidate", None)
                if not callable(offer):
                    raise RuntimeError("Private Companion 缺少主动候选入口")
                accepted = bool(offer(user_id, user, candidate))
                if accepted:
                    batch["offered"] += 1
                    batch["accepted"] += 1
                else:
                    batch["duplicate"] += 1
                    batch["skipped"] += 1
        return batch

    @staticmethod
    def _normalize_event(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or _text(raw.get("type"), 40) != "health_alert":
            return None
        event_id = _cursor(raw.get("id"))
        event_key = _text(raw.get("event_key"), 120)
        occurred_at = _timestamp(raw.get("occurred_at"))
        expires_at = _timestamp(raw.get("expires_at"))
        targets_raw = raw.get("targets")
        if (
            event_id is None
            or event_id > 9223372036854775807
            or not event_key
            or occurred_at <= 0
            or expires_at <= occurred_at
            or not isinstance(targets_raw, list)
        ):
            return None
        targets: list[str] = []
        for item in targets_raw:
            if isinstance(item, dict):
                item = item.get("umo") or item.get("target") or item.get("session_id")
            target = _text(item, 240)
            if ":FriendMessage:" in target and target not in targets:
                targets.append(target)
        if not targets:
            return None
        context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
        metric = _identifier(context.get("metric"))
        value = _number(context.get("value"))
        baseline = context.get("baseline")
        baseline_mean = _number(baseline.get("mean")) if isinstance(baseline, dict) else None
        if not metric or value is None or baseline_mean is None:
            return None
        clean_context: dict[str, Any] = {
            "metric": metric,
            "value": value,
            "baseline": {"mean": baseline_mean},
            "occurred_at": occurred_at,
        }
        unit = _unit(context.get("unit"))
        if unit:
            clean_context["unit"] = unit
        today = context.get("today")
        if isinstance(today, dict):
            limited_today = {
                key: number
                for key in ("steps", "sleep_score", "spo2", "weight_change")
                if (number := _number(today.get(key))) is not None
            }
            if limited_today:
                clean_context["today"] = limited_today
        return {
            "id": str(event_id),
            "event_key": event_key,
            "occurred_at": occurred_at,
            "expires_at": expires_at,
            "severity": _text(raw.get("severity"), 24),
            "topic": _identifier(raw.get("topic"), 80),
            "targets": targets,
            "context": clean_context,
        }

    def _matched_users(self, targets: list[str]) -> list[tuple[str, dict[str, Any], str]]:
        users = getattr(self._host, "data", {}).get("users", {})
        if not isinstance(users, dict):
            return []
        enabled = getattr(self._host, "_user_enabled_for_proactive", None)
        verified = getattr(self._host, "_private_delivery_umo_is_verified", None)
        matches = getattr(self._host, "_private_umo_matches_user_id", None)
        result: list[tuple[str, dict[str, Any], str]] = []
        seen: set[tuple[str, str]] = set()
        resolver = getattr(self._host, "_proactive_chat_bridge_user", None)
        if callable(resolver):
            for target in targets:
                try:
                    user_id, user = resolver(target)
                except Exception:
                    user_id, user = "", None
                if not user_id or not isinstance(user, dict):
                    continue
                if not callable(enabled) or not enabled(user_id, user):
                    continue
                if not callable(verified) or not verified(user_id, user, target):
                    continue
                key = (user_id, target)
                if key not in seen:
                    result.append((user_id, user, target))
                    seen.add(key)
        delivery_id_for = getattr(self._host, "_private_delivery_user_id_for", None)
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict):
                continue
            if not callable(enabled) or not enabled(user_id, user):
                continue
            delivery_id = delivery_id_for(user_id) if callable(delivery_id_for) else user_id
            for target in targets:
                if not callable(matches) or not (
                    matches(target, user_id)
                    or (delivery_id and matches(target, str(delivery_id)))
                ):
                    continue
                if not callable(verified) or not verified(user_id, user, target):
                    continue
                key = (user_id, target)
                if key not in seen:
                    result.append((user_id, user, target))
                    seen.add(key)
        return result

    @staticmethod
    def _candidate(stream_id: str, event: dict[str, Any], target_umo: str, *, now: float) -> dict[str, Any]:
        target_hash = hashlib.sha256(target_umo.encode("utf-8", errors="ignore")).hexdigest()[:12]
        stream_token = stream_id
        event_token = str(event["id"])
        context = dict(event["context"])
        metric = _text(context.get("metric") or event.get("topic"), 80) or "身体状态"
        occurred_at = max(0.0, float(event["occurred_at"]))
        expires_at = float(event["expires_at"])
        return {
            "source": "body_monitor",
            "reason": "health_alert",
            "action": "message",
            "scheduled_ts": now,
            "window_start_at": now,
            "preferred_ts": now,
            "best_until_at": expires_at,
            "expire_at": expires_at,
            "topic": f"温和关心对方近期的{metric}",
            "motive": "身体记录出现了一项值得温和关心的变化，想自然问候一句并给对方保留不回复的空间",
            "context_key": CONTEXT_KEY,
            "context": context,
            "trigger_ts": occurred_at,
            "origin_event_id": f"body:{stream_token}:{event_token}:{target_hash}",
        }

    def format_health_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        if reason != "health_alert" or not isinstance(user, dict):
            return ""
        context = user.get(CONTEXT_KEY)
        if not isinstance(context, dict):
            return ""
        metric = self._metric_label(context.get("metric") or context.get("topic"))
        unit = _unit(context.get("unit"))
        current = self._display_measure(context.get("value"), unit)
        baseline_context = context.get("baseline")
        baseline = self._display_measure(
            baseline_context.get("mean") if isinstance(baseline_context, dict) else None,
            unit,
        )
        occurred = self._display_time(context.get("occurred_at"))
        facts = []
        if metric:
            facts.append(f"指标：{metric}")
        if current:
            facts.append(f"当前记录：{current}")
        if baseline:
            facts.append(f"个人平时参考：{baseline}")
        if occurred:
            facts.append(f"记录时间：{occurred}")
        if not facts:
            return ""
        return (
            "【身体状态关心线索】\n"
            f"- {'；'.join(facts)}。\n"
            "- 只把它作为一次温和问候的由头，不下结论，不夸大风险，也不要求对方立即解释或回复。\n"
            "- 可以自然问问现在感觉如何、是否需要休息；如对方明显不舒服，可建议联系可信赖的人或专业人员。\n"
            "- 不复述后台字段、统计方法、内部来源或系统术语。"
        )

    @staticmethod
    def _metric_label(value: Any) -> str:
        text = _text(value, 80)
        labels = {
            "heart_rate": "心率",
            "resting_heart_rate": "静息心率",
            "blood_oxygen": "血氧",
            "spo2": "血氧",
            "sleep": "睡眠",
            "sleep_duration": "睡眠时长",
            "temperature": "体温",
            "body_temperature": "体温",
            "steps": "步数",
            "stress": "压力状态",
        }
        return labels.get(text.lower(), re.sub(r"[_-]+", " ", text))

    @staticmethod
    def _display_value(value: Any) -> str:
        if value in (None, "") or isinstance(value, (dict, list, tuple, set)):
            return ""
        return _text(value, 60)

    @classmethod
    def _display_measure(cls, value: Any, unit: str = "") -> str:
        displayed = cls._display_value(value)
        if not displayed or not unit:
            return displayed
        separator = (
            ""
            if unit in {"%", "°C", "°F", "次/分", "次/分钟", "小时", "分钟", "步"}
            else " "
        )
        return f"{displayed}{separator}{unit}"

    @staticmethod
    def _display_time(value: Any) -> str:
        ts = _timestamp(value)
        if ts <= 0:
            return ""
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return ""

    def _record_error(self, state: dict[str, Any], status: str, exc: Exception) -> dict[str, Any]:
        state["status"] = status
        state["last_error"] = _error_text(exc, 180) or exc.__class__.__name__
        logger.warning("[身体监测联动] Body Monitor 联动失败: %s", state["last_error"])
        self._save()
        return self.status_view()

    def status_view(self) -> dict[str, Any]:
        state = self._state()
        status = _text(state.get("status"), 24) or "disabled"
        batch = state.get("last_batch") if isinstance(state.get("last_batch"), dict) else {}
        return {
            "enabled": bool(getattr(self._host, "enable_body_monitor_integration", False)),
            "status": status,
            "state": status,
            "api_version": _cursor(state.get("api_version")) or 0,
            "supported_api_version": SUPPORTED_API_VERSION,
            "initialized": bool(state.get("initialized")),
            "stream_id": _text(state.get("stream_id"), 80),
            "cursor": _cursor(state.get("cursor")),
            "has_more": bool(state.get("has_more")),
            "last_pull_at": float(state.get("last_pull_at") or 0.0),
            "last_success_at": float(state.get("last_success_at") or 0.0),
            "last_error": _text(state.get("last_error"), 180),
            "error": _text(state.get("last_error"), 180),
            "last_batch": {
                "received": int(batch.get("received") or 0),
                "offered": int(batch.get("offered") or 0),
                "accepted": int(batch.get("accepted") or batch.get("offered") or 0),
                "skipped": int(batch.get("skipped") or 0),
                "duplicate": int(batch.get("duplicate") or 0),
                "expired": int(batch.get("expired") or 0),
                "initialized": bool(batch.get("initialized")),
            },
        }
