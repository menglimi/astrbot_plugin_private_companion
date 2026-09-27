# -*- coding: utf-8 -*-
"""ProactivePart08Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 236 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import _proactive_setting_value, logger
from .proactive_core_shared import Any
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import _today_key
from .proactive_core_shared import random



class ProactivePart08Mixin:
    """ProactivePart08Mixin（从 ProactiveMixin 拆出）。"""


    def _pick_mobile_anonymous_area_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn a completed anonymous-area visit into a delayed, low-pressure thought."""
        if not isinstance(user, dict) or self._private_user_role(user) == "friend":
            return None
        check_now = _now_ts() if now is None else float(now)
        if not self._mobile_location_humanization_budget_available(user, now=check_now):
            return None
        pending = user.get("mobile_anonymous_area_pending")
        if not isinstance(pending, dict) or _safe_float(pending.get("expires_at"), 0.0) <= check_now:
            if isinstance(pending, dict) and pending:
                user["mobile_anonymous_area_pending"] = {}
            return None
        candidate_at = _safe_float(pending.get("candidate_at"), 0.0)
        if candidate_at > 0 and check_now - candidate_at < 24 * 3600:
            return None
        familiar = bool(pending.get("familiar"))
        dwell_minutes = _safe_int(pending.get("dwell_minutes"), 0, 0, 24 * 60)
        if familiar:
            return {
                "date": _today_key(),
                "window": self._window_from_delay_minutes(20, width_minutes=70),
                "reason": "anonymous_area_familiarity",
                "action": "message",
                "why": "用户最近几次在相似的未命名区域停留，离开后自然产生一点熟悉感",
                "topic": "最近好像有个常去的地方",
                "motive": "不是想查问位置，只是最近几次都想起对方似乎有个常去的地方，想轻轻聊起",
                "scene": "用户离开一个最近重复到访的匿名区域后",
                "tone": "像聊天时忽然注意到，不追问地点名称",
                "impulse": "先分享一点模糊的熟悉感，把命名权留给用户",
                "_scheduled_ts": check_now + random.uniform(12 * 60, 42 * 60),
                "context_key": "anonymous_area_context",
                "context": {"visit_count": _safe_int(pending.get("visit_count"), 3, 1), "after_departure": True},
                "origin_event_id": f"anonymous-area-familiarity:{pending.get('token')}:{int(pending.get('left_at') or check_now)}",
                "followup_kind": "anonymous_area_familiarity",
            }
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(20, width_minutes=70),
            "reason": "anonymous_area_dwell",
            "action": "message",
            "why": "用户在未命名区域稳定停留了一段时间，离开后想用不打扰的方式关心一下",
            "topic": "刚才在外面待了挺久",
            "motive": "刚才好像在外面待了挺久，离开一会儿后想轻轻问问今天还顺不顺",
            "scene": "用户离开一个未命名区域后的回程余韵",
            "tone": "轻一点，不暴露位置感知，不追问具体地点",
            "impulse": "先关心感受，不把位置本身说成话题",
            "_scheduled_ts": check_now + random.uniform(12 * 60, 42 * 60),
            "context_key": "anonymous_area_context",
            "context": {"dwell_minutes": dwell_minutes, "after_departure": True},
            "origin_event_id": f"anonymous-area-dwell:{pending.get('token')}:{int(pending.get('left_at') or check_now)}",
            "followup_kind": "anonymous_area_dwell",
        }

    async def _mobile_location_watch_once(
        self,
        *,
        now: float | None = None,
        user_ids: set[str] | None = None,
    ) -> bool:
        api_getter = getattr(self, "_reality_companion_api", None)
        if callable(api_getter) and api_getter() is None:
            return False
        scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        user_getter = getattr(self, "_get_user", None)
        if not callable(scene_getter):
            return False
        check_now = _now_ts() if now is None else now
        triggered = False
        changed = False
        watched_user_ids = self._mobile_location_watch_user_ids()
        if user_ids is not None:
            watched_user_ids = [user_id for user_id in watched_user_ids if user_id in user_ids]
        for user_id in watched_user_ids:
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict) and callable(user_getter):
                try:
                    user = user_getter(user_id)
                except Exception:
                    user = None
            if not isinstance(user, dict):
                continue
            try:
                scene = scene_getter(user, now=check_now)
            except TypeError:
                scene = scene_getter(user)
            except Exception:
                continue
            anonymous_changed = self._observe_mobile_anonymous_area(user, scene, now=check_now)
            changed = changed or anonymous_changed
            transition_key = _single_line(scene.get("transition_key"), 80) if isinstance(scene, dict) else ""
            if not transition_key:
                user["mobile_location_watch_initialized"] = True
                user["mobile_location_watch_pending_key"] = ""
                user["mobile_location_watch_pending_count"] = 0
                continue
            previous_key = _single_line(user.get("mobile_location_watch_transition_key"), 80)
            user["mobile_location_watch_transition_key"] = transition_key
            changed = changed or previous_key != transition_key
            if not bool(user.get("mobile_location_watch_initialized")):
                user["mobile_location_watch_initialized"] = True
                user["mobile_location_watch_pending_key"] = transition_key
                user["mobile_location_watch_pending_count"] = 0
                user["mobile_location_watch_triggered_key"] = transition_key
                continue
            pending_key = _single_line(user.get("mobile_location_watch_pending_key"), 80)
            pending_count = _safe_int(user.get("mobile_location_watch_pending_count"), 0, 0)
            if pending_key != transition_key:
                pending_key = transition_key
                pending_count = 1
            else:
                pending_count += 1
            user["mobile_location_watch_pending_key"] = pending_key
            user["mobile_location_watch_pending_count"] = pending_count
            already_triggered = _single_line(user.get("mobile_location_watch_triggered_key"), 80) == transition_key
            # The Android client already requires consecutive stable fixes before
            # reporting a confirmed place. One new semantic transition is therefore
            # enough to wake planning; waiting for a duplicate upload can lose the
            # event when foreground sharing is closed shortly after arrival.
            if pending_count >= 1 and not already_triggered and bool(scene.get("recent_transition")):
                user["mobile_location_priority_key"] = transition_key
                user["mobile_location_priority_until"] = check_now + 180
                user["mobile_location_watch_triggered_key"] = transition_key
                triggered = True
        if changed:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"users"}, delay=0.5)
        if triggered:
            kicker = getattr(self, "_kick_proactive_loop_once", None)
            if callable(kicker):
                await kicker()
        return triggered

    async def _handle_mobile_location_update(self, user_id: Any) -> dict[str, Any]:
        """Process one gateway location event without trusting it as a send command."""
        normalized = _single_line(user_id, 120)
        if not normalized:
            return {"handled": False, "reason": "user_missing"}
        try:
            triggered = await self._mobile_location_watch_once(
                now=_now_ts(),
                user_ids={normalized},
            )
        except Exception as exc:
            logger.debug("手机位置事件处理暂时失败: %s", _single_line(exc, 160))
            return {"handled": False, "reason": "watch_failed"}
        return {"handled": True, "triggered": bool(triggered)}

    async def _kick_proactive_loop_once(self) -> None:
        try:
            await self._run_scheduler_cycle(immediate=True)
        except Exception as e:
            logger.warning(f"主动链即时唤醒失败: {e}", exc_info=True)

    def _next_scheduler_timeout(self) -> float:
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        persona_getter = getattr(self, "_scheduler_persona_ids", None)
        persona_ids = list(persona_getter() if callable(persona_getter) else [""])
        timeout_getter = getattr(self, "_next_scheduler_timeout_for_active_persona", None)

        def next_for_active_persona() -> float:
            if callable(timeout_getter):
                return float(timeout_getter())
            return float(ProactivePart08Mixin._next_scheduler_timeout_for_active_persona(self))

        if active or persona_ids == [""]:
            return next_for_active_persona()
        timeouts: list[float] = []
        activator = getattr(self, "_activate_persona_id", None)
        deactivator = getattr(self, "_deactivate_persona_for_event", None)
        for persona_id in persona_ids:
            token = activator(persona_id) if callable(activator) else None
            try:
                timeouts.append(next_for_active_persona())
            finally:
                if token is not None and callable(deactivator):
                    deactivator(token)
        interval = _safe_float(_proactive_setting_value(self, "check_interval_seconds", 60), 60, 1.0)
        return min(timeouts) if timeouts else max(30.0, interval)

    def _next_scheduler_timeout_for_active_persona(self) -> float:
        base = max(30.0, _safe_float(_proactive_setting_value(self, "check_interval_seconds", 60), 60, 1.0))
        now = _now_ts()
        nearest_due_in: float | None = None
        users = self.data.get("users", {})
        if isinstance(users, dict):
            for raw_user in users.values():
                if not isinstance(raw_user, dict):
                    continue
                if not raw_user.get("umo"):
                    continue
                next_at = _safe_float(raw_user.get("next_proactive_at"), 0)
                due_times = [next_at]
                if bool(_proactive_setting_value(self, "enable_goodnight_screen_check", False)):
                    due_times.append(_safe_float(raw_user.get("goodnight_screen_check_due_at"), 0))
                for due_at in due_times:
                    if due_at <= 0:
                        continue
                    due_in = max(0.0, due_at - now)
                    if nearest_due_in is None or due_in < nearest_due_in:
                        nearest_due_in = due_in

        if nearest_due_in is None:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None:
                nearest_due_in = detail_due_in

        memo_due_getter = getattr(self, "_next_memo_due_in_seconds", None)
        memo_due_in = memo_due_getter(now) if callable(memo_due_getter) else None
        if memo_due_in is not None and (nearest_due_in is None or memo_due_in < nearest_due_in):
            nearest_due_in = memo_due_in
        elif bool(_proactive_setting_value(self, "enable_detail_enhancement", True)):
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None and detail_due_in < nearest_due_in:
                nearest_due_in = detail_due_in

        diary_due_getter = getattr(self, "_next_daily_diary_due_in_seconds", None)
        diary_due_in = diary_due_getter(now) if callable(diary_due_getter) else None
        if diary_due_in is not None and (nearest_due_in is None or diary_due_in < nearest_due_in):
            nearest_due_in = diary_due_in

        review_due_getter = getattr(self, "_next_daily_review_due_in_seconds", None)
        review_due_in = review_due_getter(now) if callable(review_due_getter) else None
        if review_due_in is not None and (nearest_due_in is None or review_due_in < nearest_due_in):
            nearest_due_in = review_due_in

        if nearest_due_in is None:
            return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
        if nearest_due_in <= 20:
            return max(3.0, nearest_due_in + random.uniform(0.8, 3.2))
        if nearest_due_in <= 90:
            return max(8.0, nearest_due_in * random.uniform(0.35, 0.7))
        if nearest_due_in <= 6 * 60:
            return max(20.0, min(base * 0.5, nearest_due_in * random.uniform(0.18, 0.42)))
        return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
