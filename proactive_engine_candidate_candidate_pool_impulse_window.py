# -*- coding: utf-8 -*-
"""ProactiveEngineCandidateCandidatePoolImpulseWindowMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 673 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineCandidateMixin）。
"""
from __future__ import annotations

from .proactive_engine_candidate_shared import logger
from .proactive_engine_candidate_shared import Any
from .proactive_engine_candidate_shared import _engine_host
from .proactive_engine_candidate_shared import _engine_proactive_window_timezone
from .proactive_engine_candidate_shared import _safe_float
from .proactive_engine_candidate_shared import _safe_int
from .proactive_engine_candidate_shared import _single_line
from .proactive_engine_candidate_shared import datetime
from .proactive_engine_candidate_shared import hashlib
from .proactive_engine_candidate_shared import re
from .proactive_engine_candidate_shared import timedelta
from .proactive_engine_candidate_shared import uuid



class ProactiveEngineCandidateCandidatePoolImpulseWindowMixin:
    """ProactiveEngineCandidateCandidatePoolImpulseWindowMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _proactive_candidate_pool(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("proactive_candidate_pool", [])
        if not isinstance(raw, list):
            raw = []
            self.data["proactive_candidate_pool"] = raw
        return raw

    def _pending_proactive_candidate_limit(self, user: dict[str, Any] | None = None) -> int:
        if not isinstance(user, dict):
            return 200
        override = _safe_int(user.get("pending_proactive_candidate_limit"), -1, -1)
        return override if override > 0 else 200

    def _candidate_user_id(self, item: dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        return _single_line(item.get("user_id") or item.get("target_user_id") or item.get("id"), 40)

    @staticmethod
    def _pending_candidate_status(status: str) -> bool:
        normalized = _single_line(status, 24).lower()
        return normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}

    @staticmethod
    def _candidate_repeat_count_limit(status: str = "") -> int:
        normalized = _single_line(status, 24).lower()
        if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
            return 12
        if normalized == "sent":
            return 8
        return 6

    def _normalize_candidate_repeat_count(self, item: dict[str, Any]) -> int:
        if not isinstance(item, dict):
            return 1
        limit = self._candidate_repeat_count_limit(str(item.get("status") or ""))
        count = _safe_int(item.get("repeat_count"), 1, 1)
        normalized = max(1, min(limit, count))
        if count != normalized:
            item["repeat_count"] = normalized
            item["repeat_count_capped"] = True
        return normalized

    def _planned_candidate_ids_by_user(self) -> dict[str, str]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned: dict[str, str] = {}
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            candidate_id = _single_line(user.get("planned_candidate_id"), 40)
            if candidate_id:
                planned[str(user_id)] = candidate_id
        return planned

    def _trim_proactive_candidate_total(self, items: list[dict[str, Any]], *, limit: int = 600) -> list[dict[str, Any]]:
        if len(items) <= limit:
            return items
        planned_ids = set(self._planned_candidate_ids_by_user().values())
        protected = [
            item for item in items
            if _single_line(item.get("id"), 40) in planned_ids
        ]
        protected_ids = {_single_line(item.get("id"), 40) for item in protected}
        remaining = [
            item for item in items
            if _single_line(item.get("id"), 40) not in protected_ids
        ]
        keep_count = max(0, limit - len(protected))
        trimmed = remaining[-keep_count:] if keep_count else []
        result = protected + trimmed
        result.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return result[-limit:]

    def _candidate_trim_priority(self, item: dict[str, Any], *, planned_candidate_id: str = "") -> tuple[int, int, int, float]:
        status = _single_line(item.get("status"), 24).lower()
        note = _single_line(item.get("note"), 160)
        item_id = _single_line(item.get("id"), 40)
        updated = _safe_float(item.get("updated_ts"), 0)
        created = _safe_float(item.get("created_ts"), 0)
        scheduled = _safe_float(item.get("scheduled_ts"), 0)
        last_seen = _safe_float(item.get("last_seen_ts"), 0)
        repeat_count = _safe_int(item.get("repeat_count"), 1, 1)
        protected = item_id and planned_candidate_id and item_id == planned_candidate_id
        status_rank = {
            "failed": 0,
            "cancelled": 1,
            "dropped": 2,
            "blocked": 3,
            "deferred": 4,
            "accepted": 6,
        }.get(status, 5)
        note_penalty = 0 if note else 1
        freshness = max(updated, scheduled, last_seen, created)
        return (1 if protected else 0, status_rank, repeat_count + note_penalty, freshness)

    def _apply_per_user_pending_candidate_cap(
        self,
        items: list[dict[str, Any]],
        *,
        pending_cap: int | None = None,
        target_user_id: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned_ids = self._planned_candidate_ids_by_user()
        grouped: dict[str, list[dict[str, Any]]] = {}
        passthrough: list[dict[str, Any]] = []
        removed = 0
        target = str(target_user_id or "").strip()
        for item in items:
            if not isinstance(item, dict):
                continue
            user_id = self._candidate_user_id(item)
            if not user_id:
                passthrough.append(item)
                continue
            if target and user_id != target:
                passthrough.append(item)
                continue
            grouped.setdefault(user_id, []).append(item)
        kept: list[dict[str, Any]] = list(passthrough)
        for user_id, user_items in grouped.items():
            user = users.get(user_id) if isinstance(users, dict) else None
            limit = pending_cap if pending_cap is not None else self._pending_proactive_candidate_limit(user if isinstance(user, dict) else None)
            if limit <= 0:
                kept.extend(user_items)
                continue
            pending_items = [item for item in user_items if self._pending_candidate_status(str(item.get("status") or ""))]
            sent_items = [item for item in user_items if not self._pending_candidate_status(str(item.get("status") or ""))]
            if len(pending_items) > limit:
                planned_candidate_id = planned_ids.get(user_id, "")
                pending_items.sort(
                    key=lambda item: self._candidate_trim_priority(item, planned_candidate_id=planned_candidate_id),
                    reverse=True,
                )
                trimmed_pending = pending_items[:limit]
                removed += max(0, len(pending_items) - len(trimmed_pending))
                pending_items = sorted(
                    trimmed_pending,
                    key=lambda item: max(
                        _safe_float(item.get("updated_ts"), 0),
                        _safe_float(item.get("created_ts"), 0),
                        _safe_float(item.get("scheduled_ts"), 0),
                    ),
                )
            kept.extend(sent_items)
            kept.extend(pending_items)
        kept.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return kept, removed

    def _shrink_user_proactive_candidates(
        self,
        user_id: str,
        *,
        pending_cap: int | None = None,
        note: str = "",
    ) -> int:
        target_user_id = str(user_id or "").strip()
        if not target_user_id:
            return 0
        current = [item for item in self._proactive_candidate_pool() if isinstance(item, dict)]
        kept, removed = self._apply_per_user_pending_candidate_cap(
            current,
            pending_cap=pending_cap,
            target_user_id=target_user_id,
        )
        if removed > 0:
            self.data["proactive_candidate_pool"] = kept
            logger.info(
                "主动候选自动收缩: user=%s removed=%s cap=%s note=%s",
                target_user_id,
                removed,
                pending_cap or "default",
                _single_line(note, 120),
            )
        return removed

    def _cleanup_proactive_candidate_pool(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _engine_host._now_ts()
        kept: list[dict[str, Any]] = []
        for item in self._proactive_candidate_pool():
            if not isinstance(item, dict):
                continue
            self._normalize_candidate_repeat_count(item)
            created = _safe_float(item.get("created_ts"), 0)
            scheduled = _safe_float(item.get("scheduled_ts"), 0)
            status = str(item.get("status") or "")
            short_lived = self._proactive_candidate_is_short_lived(item)
            ttl = (
                6 * 3600
                if short_lived and status in {"accepted", "sent"}
                else 3 * 3600
                if short_lived
                else 36 * 3600
                if status in {"accepted", "sent"}
                else 18 * 3600
            )
            expire_at = _safe_float(item.get("expire_at"), 0)
            if short_lived and expire_at > 0 and now > expire_at + 2 * 3600:
                continue
            anchor = max(created, scheduled)
            if anchor > 0 and now - anchor <= ttl:
                kept.append(item)
        kept, _ = self._apply_per_user_pending_candidate_cap(kept)
        self.data["proactive_candidate_pool"] = self._trim_proactive_candidate_total(kept, limit=600)
        return self.data["proactive_candidate_pool"]

    @staticmethod
    def _proactive_candidate_is_short_lived(item: dict[str, Any]) -> bool:
        """Weather and environment transitions must not survive into another day."""
        if not isinstance(item, dict):
            return False
        values = {
            _single_line(item.get("source"), 40).strip().lower(),
            _single_line(item.get("reason"), 40).strip().lower(),
            _single_line(item.get("planned_proactive_source"), 40).strip().lower(),
            _single_line(item.get("planned_proactive_reason"), 40).strip().lower(),
        }
        return bool(values & {"weather_alert", "environment_change"})

    def _proactive_impulse_pool(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        raw = user.get("proactive_impulses")
        if not isinstance(raw, list):
            raw = []
            user["proactive_impulses"] = raw
        return raw

    @staticmethod
    def _scrub_body_monitor_impulse_context(item: dict[str, Any]) -> None:
        if _single_line(item.get("source"), 40) != "body_monitor":
            return
        item.pop("context", None)
        item["context_key"] = ""

    def _cleanup_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        check_now = _engine_host._now_ts() if now is None else now
        kept: list[dict[str, Any]] = []
        for item in self._proactive_impulse_pool(user):
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            updated = _safe_float(item.get("updated_ts"), created)
            state = str(item.get("state") or "queued").strip().lower()
            window_start_at = _safe_float(item.get("window_start_at"), 0)
            preferred_ts = _safe_float(item.get("preferred_ts"), window_start_at)
            best_until_at = _safe_float(item.get("best_until_at"), preferred_ts)
            expire_at = _safe_float(item.get("expire_at"), 0)
            if state in {"sent", "blocked", "cancelled", "dropped"}:
                self._scrub_body_monitor_impulse_context(item)
                if max(created, updated, expire_at) > 0 and check_now - max(created, updated, expire_at) <= 12 * 3600:
                    kept.append(item)
                continue
            if expire_at > 0 and check_now > expire_at:
                item["state"] = "blocked"
                item["last_status"] = "blocked"
                item["last_note"] = "潜在念头窗口已过期"
                item["updated_ts"] = check_now
                self._scrub_body_monitor_impulse_context(item)
                kept.append(item)
                continue
            if not (
                window_start_at > 0
                and window_start_at <= preferred_ts <= best_until_at <= expire_at
            ):
                item["state"] = "blocked"
                item["last_status"] = "blocked"
                item["last_note"] = "潜在念头时间窗口无效"
                item["updated_ts"] = check_now
                self._scrub_body_monitor_impulse_context(item)
                kept.append(item)
                continue
            if expire_at > 0 and check_now - expire_at > 2 * 3600:
                continue
            if created > 0 and check_now - created > 48 * 3600:
                continue
            kept.append(item)
        user["proactive_impulses"] = kept[-16:]
        return user["proactive_impulses"]

    def _proactive_impulse_signature(self, item: dict[str, Any]) -> str:
        route_key = _single_line(item.get("route_dedupe_key"), 160)
        if route_key:
            return route_key
        # motive 是模板化动机文本，不参与主题相似判定，避免不同内容被误判重复。
        return self._proactive_topic_signature(
            item.get("reason"),
            item.get("source"),
            item.get("topic"),
        )

    def _proactive_impulse_default_window_seconds(self, reason: str, *, source: str = "") -> tuple[float, float]:
        route = self._proactive_route_for(reason=reason, source=source)
        return float(route.active_window_seconds), float(route.grace_window_seconds)

    def _event_time_window_bounds(
        self,
        event: dict[str, Any],
        *,
        reason: str,
        source: str = "",
        now: float | None = None,
    ) -> tuple[float, float, float, float]:
        check_now = _engine_host._now_ts() if now is None else now
        preferred_ts = _safe_float(event.get("_scheduled_ts"), 0)
        start_ts = preferred_ts
        end_ts = 0.0
        window = str(event.get("window") or "").strip()
        if window:
            start_minute, end_minute = self._parse_window_minutes(window)
            if start_minute is not None and end_minute is not None:
                when = self._environment_fromtimestamp(check_now)
                date_text = str(event.get("date") or "").strip()
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                        base_date = datetime.strptime(date_text, "%Y-%m-%d").date()
                    else:
                        base_date = when.date()
                except Exception:
                    base_date = when.date()
                tzinfo = when.tzinfo
                start_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=start_minute)
                end_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=end_minute)
                start_ts = start_dt.timestamp()
                end_ts = end_dt.timestamp()
        if preferred_ts <= 0:
            preferred_ts = start_ts if start_ts > 0 else check_now + 60
        if start_ts <= 0:
            start_ts = preferred_ts
        route = self._proactive_route_for(
            reason=reason,
            source=source or event.get("source"),
            semantic_kind=event.get("semantic_kind"),
            kind=event.get("kind"),
        )
        active_span = float(route.active_window_seconds)
        grace_span = float(route.grace_window_seconds)
        if end_ts <= 0:
            end_ts = max(start_ts + 60.0, preferred_ts + active_span)
        expire_at = max(end_ts + grace_span, preferred_ts + 5 * 60.0)
        return start_ts, preferred_ts, end_ts, expire_at

    def _proactive_origin_event_id(self, candidate: dict[str, Any], *, source: str = "") -> str:
        explicit = _single_line(
            candidate.get("origin_event_id")
            or candidate.get("event_id")
            or candidate.get("source_event_id")
            or candidate.get("key")
            or candidate.get("id"),
            80,
        )
        if explicit:
            return explicit
        context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
        context_id = _single_line(
            context.get("id")
            or context.get("memo_id")
            or context.get("goal_id")
            or context.get("event_id"),
            80,
        )
        scheduled_ts = _safe_float(
            candidate.get("_scheduled_ts")
            or candidate.get("scheduled_ts")
            or candidate.get("window_start_at")
            or candidate.get("preferred_ts"),
            0,
        )
        window = _single_line(candidate.get("window"), 40)
        date_text = _single_line(candidate.get("date"), 20)
        if not date_text and scheduled_ts > 0:
            try:
                date_text = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d")
            except Exception:
                date_text = datetime.fromtimestamp(scheduled_ts).strftime("%Y-%m-%d")
        # 有明确日期/时段的来源事件，其随机落点分钟不是事件身份的一部分。
        # 否则同一饭点、问候或日程事件每次重选随机分钟都会得到新 ID。
        scheduled_anchor = "" if window else str(int(scheduled_ts // 60))
        raw = "|".join(
            (
                _single_line(source or candidate.get("source"), 40),
                _single_line(candidate.get("reason"), 40),
                date_text,
                window,
                scheduled_anchor,
                context_id,
                _single_line(candidate.get("topic"), 80),
            )
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _prepare_proactive_candidate_window(
        self,
        candidate: dict[str, Any],
        *,
        reason: str,
        source: str,
        now: float,
    ) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(candidate, dict):
            return None, "主动来源无效"
        prepared = dict(candidate)
        current_timezone = _engine_proactive_window_timezone(self)
        candidate_timezone = _single_line(candidate.get("window_timezone"), 64)
        time_exempt = source in {"timer", "troubleshooting", "simulation"}
        if (
            candidate_timezone
            and candidate_timezone != current_timezone
            and not time_exempt
        ):
            candidate["lifecycle_status"] = "skipped"
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件生成时区已变化"
            return None, "来源事件生成时区已变化"
        prepared["window_timezone"] = candidate_timezone or current_timezone
        candidate.setdefault("window_timezone", prepared["window_timezone"])
        origin_event_id = self._proactive_origin_event_id(candidate, source=source)
        prepared["origin_event_id"] = origin_event_id
        if origin_event_id and not _single_line(candidate.get("origin_event_id"), 80):
            candidate["origin_event_id"] = origin_event_id
        effective_timezone = _single_line(
            getattr(self, "environment_perception_timezone", ""), 64
        ) or _single_line(getattr(self, "environment_perception_timezone_setting", ""), 64)
        stored_timezone = _single_line(
            prepared.get("window_timezone") or candidate.get("window_timezone"), 64
        )
        weather_window = reason == "weather_alert" or source in {
            "weather_alert",
            "environment_change",
        }
        # Window timestamps are derived from local calendar boundaries. When the
        # effective timezone changes, discard only the derived weather window and
        # rebuild it from the stable source event instead of mixing two calendars.
        if weather_window and stored_timezone and effective_timezone and stored_timezone != effective_timezone:
            for key in (
                "window_start_at",
                "preferred_ts",
                "best_until_at",
                "expire_at",
                "scheduled_ts",
                "_scheduled_ts",
            ):
                prepared.pop(key, None)
            prepared["timezone_rebased_from"] = stored_timezone
        if effective_timezone and not time_exempt:
            prepared["window_timezone"] = effective_timezone
        window_start_at = _safe_float(prepared.get("window_start_at"), 0)
        preferred_ts = _safe_float(prepared.get("preferred_ts"), 0)
        best_until_at = _safe_float(prepared.get("best_until_at"), 0)
        expire_at = _safe_float(prepared.get("expire_at"), 0)
        if any(value <= 0 for value in (window_start_at, preferred_ts, best_until_at, expire_at)):
            window_start_at, preferred_ts, best_until_at, expire_at = self._event_time_window_bounds(
                prepared,
                reason=reason,
                source=source,
                now=now,
            )
        midnight_ritual = bool(prepared.get("_midnight_ritual")) and reason in {
            "birthday_celebration",
            "special_day_greeting",
        }
        quiet_hours_exempt = reason == "insomnia_night" or midnight_ritual
        if (
            reason == "morning_greeting"
            and source in {"daily_greeting", "story", "daily_story", "state"}
            and _single_line(prepared.get("window"), 40)
        ):
            current = self._environment_fromtimestamp(now)
            morning_start, morning_end = self._morning_greeting_window()
            day_start = datetime.combine(current.date(), datetime.min.time(), tzinfo=current.tzinfo)
            canonical_start = (day_start + timedelta(minutes=morning_start)).timestamp()
            canonical_end = (day_start + timedelta(minutes=morning_end)).timestamp()
            if best_until_at < canonical_start or window_start_at > canonical_end:
                window_start_at = canonical_start
                preferred_ts = min(max(preferred_ts, canonical_start), canonical_end)
                best_until_at = canonical_end
            else:
                window_start_at = max(window_start_at, canonical_start)
                preferred_ts = min(max(preferred_ts, window_start_at), canonical_end)
                best_until_at = min(max(best_until_at, preferred_ts), canonical_end)
            expire_at = min(
                max(expire_at, best_until_at + 5 * 60),
                canonical_end + 35 * 60,
            )
        if not time_exempt and expire_at <= now:
            candidate["lifecycle_status"] = "expired"
            candidate["expired_at"] = now
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件有效窗口已过期"
            return None, "来源事件有效窗口已过期"
        if not (
            window_start_at > 0
            and window_start_at <= preferred_ts <= best_until_at <= expire_at
        ):
            candidate["lifecycle_status"] = "skipped"
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件时间窗口无效"
            return None, "来源事件时间窗口无效"

        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        quiet_end = 0.0
        if not time_exempt and not quiet_hours_exempt and callable(quiet_end_getter):
            try:
                quiet_end = _safe_float(quiet_end_getter(max(window_start_at, preferred_ts)), 0.0)
            except Exception:
                quiet_end = 0.0
        if quiet_end > max(window_start_at, preferred_ts):
            target = quiet_end + 2 * 60
            freshness = self._proactive_item_freshness_class(
                action=str(prepared.get("action") or "message"),
                reason=reason,
                source=source,
                semantic_kind=str(prepared.get("semantic_kind") or ""),
            )
            if expire_at <= target and freshness != "durable":
                candidate["lifecycle_status"] = "skipped"
                candidate["expired_at"] = now
                candidate["lifecycle_updated_at"] = now
                candidate["lifecycle_note"] = "免打扰覆盖整个有效窗口"
                return None, "免打扰覆盖整个有效窗口"
            if expire_at <= target:
                shift = target - window_start_at
                window_start_at += shift
                preferred_ts = max(preferred_ts + shift, window_start_at)
                best_until_at = max(best_until_at + shift, preferred_ts + 20 * 60)
                expire_at = max(expire_at + shift, best_until_at + 20 * 60)
            else:
                window_start_at = max(window_start_at, target)
                preferred_ts = max(preferred_ts, target)
                best_until_at = max(best_until_at, min(expire_at, target + 20 * 60))
            prepared["quiet_hours_adjusted"] = True
            prepared["quiet_hours_until"] = quiet_end

        prepared["window_start_at"] = window_start_at
        prepared["preferred_ts"] = preferred_ts
        prepared["best_until_at"] = best_until_at
        prepared["expire_at"] = expire_at
        prepared["scheduled_ts"] = max(
            _safe_float(prepared.get("scheduled_ts") or prepared.get("_scheduled_ts"), window_start_at),
            window_start_at,
        )
        return prepared, ""

    def _build_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str,
        source: str,
        window_start_at: float,
        preferred_ts: float,
        best_until_at: float,
        expire_at: float,
        window_timezone: str = "",
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        trigger_ts: float = 0,
        quota_exempt: bool = False,
        context_key: str = "",
        context: Any = None,
        opener_mode: str = "",
        followup_kind: str = "",
        origin_event_id: str = "",
        conversation_posture: str = "",
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        impulse_reason = _single_line(reason, 40) or "check_in"
        impulse_action = _single_line(action, 40) or "message"
        impulse_topic = _single_line(topic, 80)
        impulse_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        posture = _single_line(conversation_posture, 24).lower()
        if posture not in {"closing", "open", "neutral"}:
            posture = ""
        salience = 0.54
        warmth = 0.46
        urgency = 0.38
        if source in {"followup", "timer", "pending_followup"}:
            salience += 0.2
            urgency += 0.12
        elif source in {"story", "event"}:
            salience += 0.12
        elif source == "random":
            warmth += 0.06
        if impulse_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "special_day_greeting"}:
            warmth += 0.1
            urgency += 0.08
        if impulse_reason in {"quiet_care", "important_date_share", "insomnia_night"}:
            warmth += 0.16
        if role == "friend":
            warmth = max(0.18, warmth - 0.08)
            urgency = max(0.16, urgency - 0.04)
        decay_per_hour = 0.06 if source in {"followup", "timer"} else 0.1
        persona_alignment = self._proactive_persona_alignment(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
        )
        semantics = self._proactive_candidate_semantics(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
            context=context,
            chain=chain,
            trigger_message_id=trigger_message_id,
            trigger_ts=trigger_ts,
        )
        proactive_kind = self._proactive_message_kind(
            reason=impulse_reason,
            source=source,
            semantic_kind=semantics.get("kind"),
        )
        kind_policy = self._proactive_kind_policy(proactive_kind)
        quota_policy = self._proactive_quota_policy(user)
        return {
            "id": uuid.uuid4().hex[:12],
            "created_ts": _engine_host._now_ts(),
            "updated_ts": _engine_host._now_ts(),
            "state": "queued",
            "source": _single_line(source, 40) or "random",
            "kind": proactive_kind,
            "kind_label": _single_line(kind_policy.get("label"), 40),
            "response_expectation": _single_line(kind_policy.get("response_expectation"), 24),
            "quota_tier": _safe_int(quota_policy.get("tier"), 0, 0, 5),
            "quota_tier_label": _single_line(quota_policy.get("label"), 40),
            "reason": impulse_reason,
            "action": impulse_action,
            "topic": impulse_topic,
            "motive": impulse_motive,
            "conversation_posture": posture,
            "window_start_at": max(0.0, float(window_start_at or preferred_ts or _engine_host._now_ts())),
            "preferred_ts": max(0.0, float(preferred_ts or window_start_at or _engine_host._now_ts())),
            "best_until_at": max(float(best_until_at or preferred_ts or _engine_host._now_ts()), float(window_start_at or 0.0)),
            "expire_at": max(float(expire_at or best_until_at or preferred_ts or _engine_host._now_ts()), float(best_until_at or 0.0)),
            "window_timezone": _single_line(window_timezone, 64)
            or _engine_proactive_window_timezone(self),
            "salience": max(0.0, min(1.0, salience)),
            "warmth": max(0.0, min(1.0, warmth)),
            "urgency": max(0.0, min(1.0, urgency)),
            "decay_per_hour": max(0.01, min(0.5, decay_per_hour)),
            "persona_fit": max(0.0, min(1.0, _safe_float(persona_alignment.get("score"), 0.5))),
            "persona_fit_note": _single_line(persona_alignment.get("note"), 160),
            "persona_fit_blocker": bool(persona_alignment.get("blocker")),
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))),
            "semantic_anchor_score": max(0.0, min(1.0, _safe_float(semantics.get("anchor_score"), 0.5))),
            "semantic_pressure": max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.4))),
            "semantic_risk": max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))),
            "semantic_note": _single_line(semantics.get("note"), 180),
            "semantic_need_layer": _single_line(semantics.get("need_layer"), 40),
            "semantic_need_drive": _single_line(semantics.get("need_drive"), 80),
            "semantic_need_note": _single_line(semantics.get("need_note"), 120),
            "semantic_need_score_bias": _safe_float(semantics.get("need_score_bias"), 0.0),
            "semantic_need_pressure_bias": _safe_float(semantics.get("need_pressure_bias"), 0.0),
            "semantic_blocker": bool(semantics.get("blocker")),
            "signature": self._proactive_topic_signature(impulse_reason, source, impulse_topic, impulse_motive),
            "chain": [] if role == "friend" else [dict(item) for item in (chain or []) if isinstance(item, dict)],
            "trigger_message_id": _single_line(trigger_message_id, 120),
            "trigger_umo": _single_line(trigger_umo, 160),
            "trigger_ts": _safe_float(trigger_ts, 0),
            "quota_exempt": bool(quota_exempt),
            "context_key": _single_line(context_key, 60),
            "context": dict(context) if isinstance(context, dict) else context,
            "opener_mode": _single_line(opener_mode, 24),
            "followup_kind": _single_line(followup_kind, 32),
            "origin_event_id": _single_line(origin_event_id, 80),
        }
