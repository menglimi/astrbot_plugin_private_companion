# -*- coding: utf-8 -*-
"""ProactiveEngineCandidatePlannedDeliveryDeferMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 298 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineCandidateMixin）。
"""
from __future__ import annotations
from .proactive_engine_candidate_shared import Any
from .proactive_engine_candidate_shared import _engine_host
from .proactive_engine_candidate_shared import _safe_float
from .proactive_engine_candidate_shared import _safe_int
from .proactive_engine_candidate_shared import _single_line
from .proactive_engine_candidate_shared import hashlib



class ProactiveEngineCandidatePlannedDeliveryDeferMixin:
    """ProactiveEngineCandidatePlannedDeliveryDeferMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _planned_proactive_timeliness_level(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return "routine"
        return self._proactive_timeliness_level(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
        )

    def _planned_proactive_delivery_key(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return ""
        parts = (
            _single_line(user.get("planned_candidate_id"), 40),
            _single_line(user.get("planned_proactive_impulse_id"), 40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            _single_line(user.get("planned_proactive_topic"), 120),
            _single_line(user.get("planned_proactive_motive"), 220),
        )
        if not any(parts):
            return ""
        return hashlib.sha1("\n".join(parts).encode("utf-8", errors="ignore")).hexdigest()

    def _planned_proactive_freshness_class(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return "contextual"
        delivery_key = self._planned_proactive_delivery_key(user)
        if delivery_key and _single_line(user.get("planned_proactive_origin_key"), 80) == delivery_key:
            stored = _single_line(user.get("planned_proactive_freshness"), 24)
            if stored in {"immediate", "contextual", "durable"}:
                return stored
        return self._proactive_item_freshness_class(
            action=str(user.get("planned_proactive_action") or "message"),
            reason=str(user.get("planned_proactive_reason") or ""),
            source=str(user.get("planned_proactive_source") or ""),
            semantic_kind=str(user.get("planned_proactive_semantic_kind") or ""),
        )

    def _ensure_planned_proactive_delivery_state(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        check_now = _engine_host._now_ts() if now is None else now
        delivery_key = self._planned_proactive_delivery_key(user)
        if not delivery_key:
            return {}
        origin_key = _single_line(user.get("planned_proactive_origin_key"), 80)
        origin_at = _safe_float(user.get("planned_proactive_origin_at"), 0)
        freshness = self._planned_proactive_freshness_class(user)
        if origin_key != delivery_key or origin_at <= 0:
            origin_at = _safe_float(user.get("planned_proactive_window_start_at"), 0) or _safe_float(user.get("next_proactive_at"), 0) or check_now
            user["planned_proactive_origin_at"] = origin_at
            user["planned_proactive_origin_key"] = delivery_key
            user["planned_proactive_freshness"] = freshness
            user["planned_proactive_delivery_state"] = "fresh"
        elif _single_line(user.get("planned_proactive_freshness"), 24) not in {"immediate", "contextual", "durable"}:
            user["planned_proactive_freshness"] = freshness
        return {
            "key": delivery_key,
            "origin_at": origin_at,
            "best_until_at": _safe_float(user.get("planned_proactive_best_until_at"), 0),
            "expire_at": _safe_float(user.get("planned_proactive_expire_at"), 0),
            "freshness": _single_line(user.get("planned_proactive_freshness"), 24) or freshness,
            "delivery_state": _single_line(user.get("planned_proactive_delivery_state"), 24) or "fresh",
        }

    def _planned_proactive_send_freshness_reason(
        self,
        user: dict[str, Any],
        snapshot: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> str:
        if not isinstance(snapshot, dict) or not snapshot:
            return ""
        current = self._ensure_planned_proactive_delivery_state(user, now=now)
        if not current:
            return "主动候选已被清理或替换"
        if _single_line(current.get("key"), 80) != _single_line(snapshot.get("key"), 80):
            return "主动候选在生成期间已变化"
        check_now = _engine_host._now_ts() if now is None else now
        expire_at = _safe_float(current.get("expire_at"), 0)
        if expire_at > 0 and check_now > expire_at and self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) != "timer":
            return "主动候选在生成期间已过期"
        if _single_line(current.get("freshness"), 24) == "immediate":
            best_until = _safe_float(current.get("best_until_at"), 0)
            if best_until > 0 and check_now > best_until:
                return "即时主动已越过自然表达窗口"
        return ""

    def _is_immediate_life_share_impulse(self, impulse: dict[str, Any]) -> bool:
        if not isinstance(impulse, dict) or not self._action_has_photo_text(str(impulse.get("action") or "")):
            return False
        return self._proactive_item_freshness_class(
            action=str(impulse.get("action") or ""),
            reason=str(impulse.get("reason") or ""),
            source=str(impulse.get("source") or ""),
            semantic_kind=str(impulse.get("semantic_kind") or ""),
        ) == "immediate"

    def _defer_or_replace_planned_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
        delay_minutes: tuple[float, float] = (30.0, 90.0),
        block_current: bool = False,
    ) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        impulse = self._planned_proactive_impulse(user)
        current_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        delivery = self._ensure_planned_proactive_delivery_state(user, now=check_now)
        freshness = _single_line(delivery.get("freshness"), 24) or "contextual"
        best_until = _safe_float(delivery.get("best_until_at"), 0)
        source = _single_line(user.get("planned_proactive_source"), 40)
        hard_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0) if source == "body_monitor" else 0
        if hard_expire_at > 0 and not block_current:
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = check_now + delay
            if next_window >= hard_expire_at:
                self._mark_planned_candidate_status(user, "blocked", "身体状态事件有效期已结束")
                if isinstance(impulse, dict):
                    impulse["state"] = "blocked"
                    impulse["last_note"] = "身体状态事件有效期已结束"
                    impulse["updated_ts"] = check_now
                self._clear_pending_proactive_plan(user)
                return False
            self._mark_planned_candidate_status(user, "deferred", note)
            if isinstance(impulse, dict):
                impulse["state"] = "deferred"
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = next_window
                impulse["best_until_at"] = min(_safe_float(impulse.get("best_until_at"), hard_expire_at), hard_expire_at)
                impulse["expire_at"] = hard_expire_at
                impulse["updated_ts"] = check_now
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            user["planned_proactive_best_until_at"] = min(_safe_float(user.get("planned_proactive_best_until_at"), hard_expire_at), hard_expire_at)
            user["planned_proactive_expire_at"] = hard_expire_at
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        is_immediate = freshness == "immediate"
        if is_immediate and not block_current and best_until > 0 and check_now >= best_until:
            expired_note = _single_line(note, 120) or "即时主动已过自然窗口"
            expired_note = f"{expired_note}；原候选已作废并重新安排"
            self._mark_planned_candidate_status(user, "blocked", expired_note)
            if isinstance(impulse, dict):
                impulse["updated_ts"] = check_now
                impulse["last_note"] = expired_note
                impulse["state"] = "blocked"
            self._clear_pending_proactive_plan(user)
            if not self._materialize_best_proactive_impulse(user, now=check_now):
                return False
            return bool(_single_line(user.get("planned_proactive_impulse_id"), 20) != current_id)
        if is_immediate and not block_current:
            self._mark_planned_candidate_status(user, "deferred", note)
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = min(check_now + delay, best_until) if best_until > 0 else check_now + delay
            capped_expire_at = best_until + 8 * 60 if best_until > 0 else 0
            if isinstance(impulse, dict):
                impulse["updated_ts"] = check_now
                impulse["last_note"] = _single_line(note, 160)
                impulse["state"] = "deferred"
                impulse["hesitation_count"] = _safe_int(impulse.get("hesitation_count"), 0, 0, 8) + 1
                impulse["hesitation_at"] = check_now
                impulse["hesitation_note"] = _single_line(note, 160)
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_window)
                if capped_expire_at > 0:
                    old_expire_at = _safe_float(impulse.get("expire_at"), 0)
                    impulse["expire_at"] = min(old_expire_at if old_expire_at > 0 else capped_expire_at, capped_expire_at)
                self._remember_proactive_hesitation(user, impulse, note=note, now=check_now)
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            if capped_expire_at > 0:
                old_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
                user["planned_proactive_expire_at"] = min(old_expire_at if old_expire_at > 0 else capped_expire_at, capped_expire_at)
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        self._mark_planned_candidate_status(user, "blocked" if block_current else "deferred", note)
        if isinstance(impulse, dict):
            impulse["updated_ts"] = check_now
            impulse["last_note"] = _single_line(note, 160)
            if block_current:
                impulse["state"] = "blocked"
            else:
                hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8) + 1
                impulse["hesitation_count"] = hesitation_count
                impulse["hesitation_at"] = check_now
                impulse["hesitation_note"] = _single_line(note, 160)
                self._remember_proactive_hesitation(user, impulse, note=note, now=check_now)
                delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
                next_window = check_now + delay
                impulse["state"] = "deferred"
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_window)
                impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_window + 25 * 60)
                impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), next_window + 90 * 60)
        elif not block_current:
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = check_now + delay
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            user["planned_proactive_best_until_at"] = next_window + 25 * 60
            user["planned_proactive_expire_at"] = next_window + 90 * 60
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        self._clear_pending_proactive_plan(user)
        if not self._materialize_best_proactive_impulse(user, now=check_now):
            return False
        return bool(_single_line(user.get("planned_proactive_impulse_id"), 20) != current_id)

    def _defer_planned_proactive_to_quiet_end(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[bool, str]:
        check_now = _engine_host._now_ts() if now is None else now
        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        quiet_end = _safe_float(quiet_end_getter(check_now), 0.0) if callable(quiet_end_getter) else 0.0
        if quiet_end <= check_now:
            return False, "当前不在免打扰时段"
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        if source in {"timer", "troubleshooting", "simulation"}:
            return False, "来源不参与免打扰改期"
        target = quiet_end + _engine_host.random.uniform(2 * 60, 8 * 60)
        delivery = self._ensure_planned_proactive_delivery_state(user, now=check_now)
        freshness = _single_line(delivery.get("freshness"), 24) or self._planned_proactive_freshness_class(user)
        expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        impulse = self._planned_proactive_impulse(user)
        if expire_at > 0 and target >= expire_at and freshness != "durable":
            self._mark_planned_candidate_status(user, "blocked", "免打扰覆盖整个有效窗口")
            if isinstance(impulse, dict):
                impulse["state"] = "blocked"
                impulse["last_status"] = "blocked"
                impulse["last_note"] = "免打扰覆盖整个有效窗口"
                impulse["updated_ts"] = check_now
            self._clear_pending_proactive_plan(user)
            # Do not let the generic scheduler put a weather event immediately
            # back into the same window.  Respect the quiet-hours boundary,
            # the user's normal proactive interval, and the weather cooldown.
            floor = quiet_end + 2 * 60
            last_sent = max(
                _safe_float(user.get("last_proactive_sent_at"), 0),
                _safe_float(user.get("last_proactive_message_at"), 0),
            )
            if last_sent > check_now:
                last_sent = 0.0
            min_interval_getter = getattr(self, "_effective_min_interval_seconds", None)
            if callable(min_interval_getter):
                try:
                    floor = max(floor, last_sent + max(0, int(min_interval_getter(user))))
                except Exception:
                    pass
            if source in {"weather_alert", "weather_context", "environment_change"}:
                floor = max(floor, _safe_float(user.get("weather_proactive_last_at"), 0) + 6 * 3600)
            delay_hours = max(0.08, (floor - check_now) / 3600.0)
            self._schedule_next_proactive(user, now=check_now, delay_hours=(delay_hours, delay_hours))
            if _safe_float(user.get("next_proactive_at"), 0) < floor:
                user["next_proactive_at"] = floor
            return True, "有效窗口被免打扰覆盖，已跳过并在免打扰结束后重排"

        old_start = _safe_float(user.get("planned_proactive_window_start_at"), check_now)
        old_best = _safe_float(user.get("planned_proactive_best_until_at"), old_start)
        if expire_at > 0 and target >= expire_at:
            shift = target - old_start
            new_best = max(old_best + shift, target + 20 * 60)
            new_expire = max(expire_at + shift, new_best + 20 * 60)
        else:
            new_best = max(old_best, min(expire_at, target + 20 * 60) if expire_at > 0 else target + 20 * 60)
            new_expire = expire_at if expire_at > 0 else new_best + 40 * 60
        user["next_proactive_at"] = target
        user["planned_proactive_window_start_at"] = target
        user["planned_proactive_best_until_at"] = new_best
        user["planned_proactive_expire_at"] = new_expire
        user["planned_proactive_delivery_state"] = "deferred"
        if isinstance(impulse, dict):
            impulse["state"] = "deferred"
            impulse["window_start_at"] = target
            impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), target)
            impulse["best_until_at"] = new_best
            impulse["expire_at"] = new_expire
            impulse["updated_ts"] = check_now
            impulse["last_status"] = "deferred"
            impulse["last_note"] = "免打扰时段，已直接移到结束后"
        candidate_id = _single_line(user.get("planned_candidate_id"), 40)
        if candidate_id:
            for item in self._cleanup_proactive_candidate_pool(now=check_now):
                if _single_line(item.get("id"), 40) != candidate_id:
                    continue
                item["status"] = "deferred"
                item["note"] = "免打扰时段，已直接移到结束后"
                item["scheduled_ts"] = target
                item["window_start_at"] = target
                item["best_until_at"] = new_best
                item["expire_at"] = new_expire
                item["updated_ts"] = check_now
                break
        return True, "已直接调度到免打扰结束后"
