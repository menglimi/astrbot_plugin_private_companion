# -*- coding: utf-8 -*-
"""DailyReviewPart01Mixin。

由 tools/split_mixin_domain.py 从 daily_review.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 475 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyReviewMixin）。
"""
from __future__ import annotations

from .daily_review_shared import logger
from .daily_review_shared import Any
from .daily_review_shared import Path
from .daily_review_shared import _redact_outbound_secrets
from .daily_review_shared import _safe_float
from .daily_review_shared import _safe_int
from .daily_review_shared import _single_line
from .daily_review_shared import asyncio
from .daily_review_shared import datetime
from .daily_review_shared import hashlib
from .daily_review_shared import json
from .daily_review_shared import re
from .daily_review_shared import time
from .daily_review_shared import timedelta
from .daily_review_shared import uuid
from .daily_review_shared import zoneinfo

# 拆分前本模块代码位于插件根 daily_review.py 中，__file__ 指向插件根；
# 单独成文件后 __file__ 会变成 part 文件路径，故按插件根位置修正以保持行为不变。
_PLUGIN_DIR = Path(__file__).resolve().parent


class DailyReviewPart01Mixin:
    """DailyReviewPart01Mixin（从 DailyReviewMixin 拆出）。"""


    def _daily_review_setting(self, key: str, default: Any = None) -> Any:
        """Read daily-review settings through the active persona when available."""
        getter = getattr(self, "persona_setting", None)
        if callable(getter):
            try:
                return getter(key, default)
            except TypeError:
                try:
                    return getter(key, default=default)
                except Exception:
                    pass
            except Exception:
                pass
        return getattr(self, key, default)

    def _daily_review_config_schema_index(self) -> dict[str, dict[str, str]]:
        cached = getattr(self, "_daily_review_schema_index_cache", None)
        if isinstance(cached, dict):
            return cached
        index: dict[str, dict[str, str]] = {}
        try:
            schema_path = _PLUGIN_DIR / "_conf_schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

            def visit(node: Any, group: str = "") -> None:
                if not isinstance(node, dict):
                    return
                items = node.get("items")
                if not isinstance(items, dict):
                    return
                current_group = _single_line(node.get("description"), 80) or group
                for key, item in items.items():
                    if not isinstance(item, dict) or bool(item.get("invisible")):
                        continue
                    if isinstance(item.get("items"), dict):
                        visit(item, current_group)
                        continue
                    normalized_key = _single_line(key, 100)
                    if not normalized_key:
                        continue
                    index[normalized_key] = {
                        "label": _single_line(item.get("description"), 100) or normalized_key,
                        "group": current_group or "插件配置",
                        "type": _single_line(item.get("type"), 24) or "unknown",
                        "hint": _single_line(item.get("hint"), 220),
                    }

            for group_node in schema.values() if isinstance(schema, dict) else []:
                visit(group_node)
        except Exception as exc:
            logger.warning(
                "每日巡视读取配置 Schema 失败: %s",
                _single_line(exc, 180),
            )
        self._daily_review_schema_index_cache = index
        return index

    def _daily_review_config_catalog(self, snapshot: dict[str, Any]) -> list[tuple[str, str]]:
        index = self._daily_review_config_schema_index()
        if not index:
            return []
        evidence = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).lower()
        families = {
            "tts": ("tts", "voice", "audio", "语音"),
            "proactive": ("proactive", "主动"),
            "group": ("group", "member_safety", "群聊", "风控"),
            "reply": ("reply", "passive", "silence", "debounce", "回复"),
            "model": ("model", "provider", "timeout", "token", "模型", "超时"),
            "schedule": ("schedule", "daily", "diary", "日程", "日记"),
            "storage": ("storage", "sqlite", "retention", "保存", "存储"),
        }
        active_families = {
            family
            for family, terms in families.items()
            if any(term in evidence for term in terms)
        }
        always = {
            "enable_daily_review",
            "daily_review_time",
            "daily_review_auto_apply_guidance",
            "enable_daily_case_review_experiment",
            "daily_review_retention_days",
        }
        ranked: list[tuple[int, str, str]] = []
        for key, meta in index.items():
            haystack = " ".join((key, meta.get("label", ""), meta.get("group", ""), meta.get("hint", ""))).lower()
            score = 100 if key in always else 0
            for family in active_families:
                if any(term in haystack for term in families[family]):
                    score += 10
            if score:
                ranked.append((score, key, meta.get("label", key)))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [(key, label) for _, key, label in ranked[:80]]

    def _daily_review_config_suggestion(self, raw: dict[str, Any]) -> dict[str, Any]:
        key = _single_line(raw.get("key"), 100)
        meta = self._daily_review_config_schema_index().get(key)
        valid = isinstance(meta, dict)
        return {
            "key": key,
            "label": _single_line((meta or {}).get("label"), 100) or key or "未知配置项",
            "group": _single_line((meta or {}).get("group"), 80),
            "type": _single_line((meta or {}).get("type"), 24),
            "valid": valid,
            "invalid_reason": "" if valid else "配置项不存在或已被移除",
            "suggestion": _single_line(raw.get("suggestion"), 280),
            "reason": _single_line(raw.get("reason"), 220),
            "risk": "high" if _single_line(raw.get("risk"), 16).lower() == "high" else "medium",
            "requires_confirmation": True,
        }

    def _daily_review_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_daily_review_generation_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._daily_review_generation_lock = lock
        return lock

    def _daily_review_failure_attempt(
        self,
        date_key: str,
        error: str,
        *,
        previous: Any = None,
        attempted_at: float | None = None,
    ) -> dict[str, Any]:
        """Build a persisted retry state for an automatic review failure."""
        now = float(attempted_at if attempted_at is not None else time.time())
        prior = previous if isinstance(previous, dict) else self.data.get("daily_review_last_attempt")
        prior_status = _single_line((prior or {}).get("status"), 16).lower()
        prior_at = _safe_float((prior or {}).get("attempted_at"), 0.0, 0.0)
        prior_count = _safe_int((prior or {}).get("failure_count"), 0, 0, 1000)
        same_failure_window = prior_status in {"failed", "paused"} and prior_at > 0 and now - prior_at <= self._DAILY_REVIEW_FAILURE_CIRCUIT_SECONDS
        failure_count = prior_count + 1 if same_failure_window else 1
        paused = failure_count >= self._DAILY_REVIEW_MAX_CONSECUTIVE_FAILURES
        if paused:
            retry_after = now + self._DAILY_REVIEW_FAILURE_CIRCUIT_SECONDS
        else:
            backoff = self._DAILY_REVIEW_FAILURE_COOLDOWN_SECONDS * (
                2 ** min(max(0, failure_count - 1), 8)
            )
            retry_after = now + min(backoff, self._DAILY_REVIEW_FAILURE_MAX_BACKOFF_SECONDS)
        return {
            "date": _single_line(date_key, 16),
            "status": "paused" if paused else "failed",
            "attempted_at": now,
            "retry_after": retry_after,
            "failure_count": failure_count,
            "error": self._daily_review_safe_text(error, 180),
        }

    def _daily_review_retry_delay_seconds(
        self,
        attempt: Any = None,
        *,
        now: float | None = None,
    ) -> float:
        state = attempt if isinstance(attempt, dict) else self.data.get("daily_review_last_attempt")
        if not isinstance(state, dict):
            return 0.0
        status = _single_line(state.get("status"), 16).lower()
        if status not in {"failed", "paused"}:
            return 0.0
        current = time.time() if now is None else float(now)
        retry_after = _safe_float(state.get("retry_after"), 0.0, 0.0)
        if retry_after > 0:
            return max(0.0, retry_after - current)
        attempted_at = _safe_float(state.get("attempted_at"), 0.0, 0.0)
        if attempted_at <= 0:
            return 0.0
        return max(
            0.0,
            self._DAILY_REVIEW_FAILURE_COOLDOWN_SECONDS - (current - attempted_at),
        )

    def _daily_review_now(self, ts: float | None = None) -> datetime:
        timezone_name = _single_line(
            getattr(self, "environment_perception_timezone", "Asia/Shanghai"),
            80,
        ) or "Asia/Shanghai"
        try:
            timezone = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            timezone = zoneinfo.ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(time.time() if ts is None else float(ts), timezone)

    @staticmethod
    def _daily_review_minutes(value: Any, default: int = 4 * 60) -> int:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return default
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return default
        return hour * 60 + minute

    def _daily_review_target_date(self, *, now: datetime | None = None) -> str:
        current = now or self._daily_review_now()
        configured = self._daily_review_minutes(self._daily_review_setting("daily_review_time", "04:00"))
        current_minutes = current.hour * 60 + current.minute
        due_date = current.date() if current_minutes >= configured else current.date() - timedelta(days=1)
        target = due_date - timedelta(days=1)
        return target.isoformat()

    def _daily_review_reports(self) -> list[dict[str, Any]]:
        reports = self.data.setdefault("daily_review_reports", [])
        if not isinstance(reports, list):
            reports = []
            self.data["daily_review_reports"] = reports
        return reports

    def _daily_review_safe_text(self, value: Any, limit: int) -> str:
        return _single_line(_redact_outbound_secrets(value, self), limit)

    def _daily_review_case_text(self, value: Any, limit: int = 260) -> str:
        text = self._daily_review_safe_text(value, max(32, limit * 2))
        text = re.sub(r"(?<!\d)\d{6,20}(?!\d)", "[数字标识已隐藏]", text)
        return _single_line(text, limit)

    def _daily_review_case_signals(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        protected = {"user_id", "group_id", "session", "message_id", "audio_path", "file_path", "umo"}
        credentials = {"api_key", "apikey", "authorization", "password", "passwd", "secret", "token"}

        def safe_signal_text(raw: Any, limit: int) -> str:
            text = self._daily_review_case_text(raw, max(32, limit * 2))
            text = re.sub(
                r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization)"
                r"\b\s*[:=]\s*(?:[\"'][^\"']*[\"']|[^\s,;|]+)",
                r"\1=[redacted]",
                text,
            )
            text = re.sub(r"(?i)\bbearer\s+[^\s,;|]+", "Bearer [redacted]", text)
            return _single_line(text, limit)

        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:24]:
            key = _single_line(raw_key, 48).lower()
            if (
                not key
                or any(token in key for token in protected)
                or any(token in key for token in credentials)
            ):
                continue
            if isinstance(raw_value, bool):
                result[key] = raw_value
            elif isinstance(raw_value, (int, float)):
                result[key] = raw_value
            elif isinstance(raw_value, (list, tuple)):
                result[key] = [safe_signal_text(item, 120) for item in raw_value[:8]]
            elif raw_value is not None:
                result[key] = safe_signal_text(raw_value, 220)
        return result

    def _daily_review_user_role(self, user_id: Any) -> str:
        value = _single_line(user_id, 128)
        if not value:
            return "unknown"
        owner_checker = getattr(self, "_is_private_companion_owner_user_id", None)
        if callable(owner_checker):
            try:
                return "owner" if bool(owner_checker(value)) else "other"
            except Exception:
                pass
        role_getter = getattr(self, "_private_user_role", None)
        user_getter = getattr(self, "_get_user", None)
        if callable(role_getter) and callable(user_getter):
            try:
                return "owner" if role_getter(user_getter(value), value) == "owner" else "other"
            except Exception:
                pass
        return "unknown"

    def _daily_review_event_role(self, event: Any) -> str:
        sender_id = ""
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                sender_id = getter()
            except Exception:
                sender_id = ""
        if not sender_id:
            sender_id = getattr(event, "sender_id", "")
        return self._daily_review_user_role(sender_id)

    def _daily_review_case_audit(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("daily_review_case_audit", [])
        if not isinstance(raw, list):
            raw = []
            self.data["daily_review_case_audit"] = raw
        if not bool(self._daily_review_setting("enable_daily_case_review_experiment", False)):
            raw.clear()
            return raw
        cutoff = time.time() - 4 * 86400
        raw[:] = [
            item for item in raw
            if isinstance(item, dict) and _safe_float(item.get("ts"), 0.0) >= cutoff
        ][-160:]
        return raw

    def _append_daily_review_case(
        self,
        *,
        kind: str,
        scene: str,
        inbound: Any = "",
        output: Any = "",
        outcome: str = "",
        role: str = "unknown",
        components: list[str] | None = None,
        signals: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> str:
        if not bool(self._daily_review_setting("enable_daily_case_review_experiment", False)):
            return ""
        now = float(ts or time.time())
        item = {
            "id": uuid.uuid4().hex[:12],
            "ts": now,
            "kind": _single_line(kind, 32) or "reply",
            "scene": _single_line(scene, 24) or "unknown",
            "role": role if role in {"owner", "other", "unknown"} else "unknown",
            "inbound": self._daily_review_case_text(inbound, 260),
            "output": self._daily_review_case_text(output, 360),
            "outcome": _single_line(outcome, 48) or "observed",
            "components": [_single_line(value, 32) for value in (components or []) if _single_line(value, 32)][:8],
            "signals": self._daily_review_case_signals(signals),
        }
        audit = self._daily_review_case_audit()
        audit.append(item)
        cutoff = now - 4 * 86400
        audit[:] = [entry for entry in audit if isinstance(entry, dict) and _safe_float(entry.get("ts"), 0.0) >= cutoff][-160:]
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            scheduler(sections={"daily_review_case_audit"})
        return str(item["id"])

    def _update_daily_review_case(self, case_id: str, **changes: Any) -> None:
        if not case_id:
            return
        for item in reversed(self._daily_review_case_audit()):
            if not isinstance(item, dict) or str(item.get("id") or "") != str(case_id):
                continue
            for key, value in changes.items():
                if key in {"output", "inbound"}:
                    item[key] = self._daily_review_case_text(value, 360 if key == "output" else 260)
                elif key == "append_output":
                    addition = self._daily_review_case_text(value, 220)
                    current = self._daily_review_case_text(item.get("output"), 360)
                    if addition and addition not in current:
                        item["output"] = self._daily_review_case_text(f"{current} {addition}".strip(), 360)
                elif key == "signals" and isinstance(value, dict):
                    current = item.setdefault("signals", {})
                    if not isinstance(current, dict):
                        current = {}
                        item["signals"] = current
                    current.update(self._daily_review_case_signals(value))
                elif key in {"outcome", "kind", "scene"}:
                    item[key] = _single_line(value, 48)
            item["updated_ts"] = time.time()
            scheduler = getattr(self, "_schedule_data_save", None)
            if callable(scheduler):
                scheduler(sections={"daily_review_case_audit"})
            return

    def _record_daily_review_outbound_case(self, event: Any, chain: list[Any]) -> str:
        if not bool(self._daily_review_setting("enable_daily_case_review_experiment", False)):
            return ""
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return ""
        existing = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        if existing:
            return existing
        inbound = self._daily_review_case_text(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or getattr(getattr(event, "message_obj", None), "message_str", ""),
            260,
        )
        if not inbound:
            return ""
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        scene = "private" if is_private else "group"
        component_types: list[str] = []
        visible_parts: list[str] = []
        spoken_parts: list[str] = []
        source_parts: list[str] = []
        for component in chain:
            name = component.__class__.__name__.strip().lower() or "unknown"
            if name not in component_types:
                component_types.append(name)
            text = self._daily_review_case_text(getattr(component, "text", ""), 280)
            if text:
                visible_parts.append(text)
            spoken = self._daily_review_case_text(getattr(component, "_private_companion_tts_spoken_text", ""), 280)
            source = self._daily_review_case_text(getattr(component, "_private_companion_tts_source_text", ""), 320)
            if spoken:
                spoken_parts.append(spoken)
            if source:
                source_parts.append(source)
        has_voice = any(name in {"record", "voice", "audio"} for name in component_types)
        expected_text = " ".join(source_parts)
        visible_text = " ".join(visible_parts)
        spoken_text = " ".join(spoken_parts)
        output = visible_text or (f"[语音] {spoken_text}" if spoken_text else "[媒体回复]")
        case_id = self._append_daily_review_case(
            kind="tts" if has_voice else "reply",
            scene=scene,
            role=self._daily_review_event_role(event),
            inbound=inbound,
            output=output,
            outcome="delivery_pending" if has_voice else "prepared",
            components=component_types,
            signals={
                "has_voice": has_voice,
                "spoken_preview": spoken_text,
                "expected_text_preview": expected_text,
                "visible_text_preview": visible_text,
                "visible_text_complete": bool(visible_text) if has_voice else True,
            },
        )
        if case_id:
            try:
                setattr(event, "_private_companion_daily_review_case_id", case_id)
            except Exception:
                pass
        return case_id

    @staticmethod
    def _daily_review_case_is_anomaly(item: dict[str, Any]) -> bool:
        outcome = str(item.get("outcome") or "").lower()
        if outcome in {
            "blocked", "delivery_failed", "dropped", "error", "failed", "incomplete",
            "reversed", "strike", "suppressed", "cancelled",
        }:
            return True
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        return bool(
            signals.get("interrupted_by_new_message")
            or signals.get("manually_reversed")
            or signals.get("visible_text_complete") is False
        )

    def _daily_review_case_timeline(self, item: dict[str, Any]) -> list[dict[str, str]]:
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        timeline: list[dict[str, str]] = []
        if item.get("inbound"):
            timeline.append({"stage": "received", "status": "ok", "detail": "已观察到输入"})
        if item.get("output") or item.get("components"):
            timeline.append({"stage": "prepared", "status": "ok", "detail": "已形成回复组件"})
        if bool(signals.get("has_voice")) or any(
            str(value).lower() in {"record", "voice", "audio"} for value in item.get("components", [])
        ):
            timeline.append({"stage": "voice", "status": "ok", "detail": "已生成语音组件"})
        expected = _safe_int(signals.get("segments_expected"), 0, 0, 100)
        sent = _safe_int(signals.get("segments_sent"), 0, 0, 100)
        if expected:
            status = "ok" if sent >= expected else "incomplete"
            timeline.append({"stage": "segments", "status": status, "detail": f"计划 {expected} 段，实际 {sent} 段"})
        stop_reason = _single_line(signals.get("stop_reason"), 80)
        if stop_reason or signals.get("interrupted_by_new_message"):
            timeline.append({
                "stage": "interrupted",
                "status": "attention",
                "detail": stop_reason or "收到新消息后中止",
            })
        outcome = _single_line(item.get("outcome"), 48) or "observed"
        timeline.append({
            "stage": "result",
            "status": "attention" if self._daily_review_case_is_anomaly(item) else "ok",
            "detail": outcome,
        })
        return timeline[:8]

    def _daily_review_case_cluster_key(self, item: dict[str, Any]) -> str:
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        control_content = ""
        if not self._daily_review_case_is_anomaly(item):
            content = f"{_single_line(item.get('inbound'), 120)}|{_single_line(item.get('output'), 160)}"
            control_content = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:10]
        parts = [
            _single_line(item.get("kind"), 32).lower(),
            _single_line(item.get("scene"), 24).lower(),
            _single_line(item.get("role"), 16).lower(),
            _single_line(item.get("outcome"), 48).lower(),
            _single_line(signals.get("category"), 40).lower(),
            _single_line(signals.get("reason") or signals.get("stop_reason"), 100).lower(),
            "voice" if bool(signals.get("has_voice")) else "plain",
            "incomplete" if signals.get("visible_text_complete") is False else "complete",
            control_content,
        ]
        return "|".join(parts)
