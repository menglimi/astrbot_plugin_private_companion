# -*- coding: utf-8 -*-
"""ProactiveEngineEventPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_event.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 468 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineEventMixin）。
"""
from __future__ import annotations

from .proactive_engine_event_shared import logger
from .proactive_engine_event_shared import Any
from .proactive_engine_event_shared import _engine_host
from .proactive_engine_event_shared import _safe_float
from .proactive_engine_event_shared import _safe_int
from .proactive_engine_event_shared import _single_line
from .proactive_engine_event_shared import _today_key
from .proactive_engine_event_shared import datetime
from .proactive_engine_event_shared import hashlib
from .proactive_engine_event_shared import re
from .proactive_engine_event_shared import runtime_persona_setting



class ProactiveEngineEventPart01Mixin:
    """ProactiveEngineEventPart01Mixin（从 ProactiveEngineEventMixin 拆出）。"""


    def _user_activity_question_targets_someone_else(self, text: str) -> bool:
        raw = _single_line(text, 180)
        if not raw:
            return False
        compact = re.sub(r"[\s,，。.!！?？~～…·、；;：:（）()【】\[\]\"'“”‘’]+", "", raw)
        if not compact:
            return False
        # “你觉得春希现在在干什么”虽然以“你”开头，询问对象仍是春希。
        # 这类认知/转述问句不能触发 Bot 自身状态、近期活动或状态记忆注入。
        return bool(
            re.search(
                r"(?:你|bot|机器人)(?:觉得|猜|知道|认为|看看|看|问).{0,24}"
                r"(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
                compact,
                flags=re.I,
            )
        )

    def _user_asks_bot_current_state_or_activity(self, text: str) -> bool:
        raw = _single_line(text, 120)
        if not raw:
            return False
        if raw.lstrip().startswith(("/", "／", "!", "！", "#", "＃")):
            return False
        compact = re.sub(r"[\s,，。.!！?？~～…·、；;：:（）()【】\[\]\"'“”‘’]+", "", raw)
        if not compact or len(compact) > 80:
            return False
        if re.search(r"(?:我|俺|咱|我们)(?:现在|这会儿|刚刚|刚才)?在?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)", compact):
            return False
        tech_status_words = ("插件", "系统", "接口", "API", "api", "配置", "页面", "排障", "日志", "服务", "连接", "模型", "任务", "进程")
        if "状态" in compact and any(word in raw for word in tech_status_words):
            return False
        direct_patterns = (
            r"(?:你|bot|机器人)?(?:现在|这会儿|这时候|刚才|今天)?在?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
            r"(?:你|bot|机器人)?(?:现在|这会儿|今天)?在(?:上课|上班|睡觉|休息|吃饭|忙|摸鱼|干活|写作业|看书)(?:吗|嘛|没|呢)?$",
            r"(?:你|bot|机器人)(?:现在|这会儿|今天)?(?:状态|情况)?(?:怎么样|咋样|如何|还好吗|还好不|累不累|困不困|忙不忙|饿不饿)$",
            r"(?:你|bot|机器人)(?:现在|这会儿)?(?:什么状态|啥状态)$",
        )
        if any(re.fullmatch(pattern, compact, flags=re.I) for pattern in direct_patterns):
            return True
        # Do not treat a question addressed to the Bot *about somebody else*
        # as a request for the Bot's own state.  The permissive colloquial
        # fallback below intentionally accepts leading observations, so
        # cognition/reporting verbs need an explicit boundary first.
        if self._user_activity_question_targets_someone_else(compact):
            return False
        # 私聊里常见的口语问法会带承接词或观察性前缀，例如
        # “那你现在在干啥呢”“好像你在忙的样子，忙啥呢”。
        return bool(
            re.search(
                r"(?:你|bot|机器人).{0,16}(?:在)?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
                compact,
                flags=re.I,
            )
        )

    def _proactive_item_is_state_share_for_current_status_question(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        reason = self._normalize_legacy_proactive_text(item.get("reason") or item.get("planned_proactive_reason"), limit=40)
        source = self._normalize_legacy_proactive_text(item.get("source") or item.get("planned_proactive_source"), limit=40)
        if source in {"timer", "troubleshooting", "simulation"}:
            return False
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share", "creative_share", "important_date_share"}:
            return False
        if reason in {"state_share", "activity_share", "background_schedule", "diary_share"}:
            return True
        text = " ".join(
            _single_line(item.get(key), 120)
            for key in (
                "topic",
                "planned_proactive_topic",
                "motive",
                "planned_proactive_motive",
                "why",
                "scene",
                "impulse",
            )
            if _single_line(item.get(key), 120)
        )
        if not text:
            return False
        state_tokens = (
            "当前日程", "现在日程", "当前细化", "正在", "刚好在",
            "上课", "上班", "摸鱼", "休息", "吃饭", "路上", "通勤", "回家", "小日常",
            "今天的小事", "刚看到", "刚听到", "刚经历",
        )
        return reason in {"check_in", "quiet_care"} and any(token in text for token in state_tokens)

    def _clear_state_share_proactive_after_user_status_question(
        self,
        user: dict[str, Any],
        *,
        user_id: str = "",
        text: str = "",
        now: float | None = None,
    ) -> bool:
        if not isinstance(user, dict) or not self._user_asks_bot_current_state_or_activity(text):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        note = "用户已询问当前状态，状态分享念头已由被动回复承接"
        changed = False
        planned_item = {
            "reason": user.get("planned_proactive_reason"),
            "action": user.get("planned_proactive_action"),
            "source": user.get("planned_proactive_source"),
            "topic": user.get("planned_proactive_topic"),
            "motive": user.get("planned_proactive_motive"),
        }
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and self._proactive_item_is_state_share_for_current_status_question(planned_item):
            self._mark_planned_candidate_status(user, "blocked", note)
            self._clear_pending_proactive_plan(user)
            changed = True
        for impulse in self._cleanup_proactive_impulses(user, now=check_now):
            if not isinstance(impulse, dict):
                continue
            state = _single_line(impulse.get("state") or "queued", 24).lower()
            if state not in {"queued", "deferred", "pending", ""}:
                continue
            if not self._proactive_item_is_state_share_for_current_status_question(impulse):
                continue
            impulse["state"] = "blocked"
            impulse["last_status"] = "blocked"
            impulse["last_note"] = note
            impulse["updated_ts"] = check_now
            changed = True
        target_user_id = _single_line(user_id or user.get("user_id") or user.get("id"), 40)
        if target_user_id:
            for candidate in self._cleanup_proactive_candidate_pool(now=check_now):
                if not isinstance(candidate, dict):
                    continue
                if self._candidate_user_id(candidate) != target_user_id:
                    continue
                status = _single_line(candidate.get("status"), 24).lower()
                if not self._pending_candidate_status(status):
                    continue
                if not self._proactive_item_is_state_share_for_current_status_question(candidate):
                    continue
                candidate["status"] = "blocked"
                candidate["note"] = note
                candidate["updated_ts"] = check_now
                changed = True
        if changed:
            logger.info(
                "用户已询问当前状态,已清理状态分享主动念头: user=%s text=%s",
                target_user_id or "unknown",
                _single_line(text, 80),
            )
        return changed

    def _friend_proactive_candidate_leaks_owner_environment(self, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend" or not isinstance(candidate, dict):
            return False
        reason = self._normalize_legacy_proactive_text(candidate.get("reason"), limit=40)
        if reason not in {"activity_share", "diary_share", "background_schedule", "state_share", "check_in", "quiet_care"}:
            return False
        text = " ".join(
            _single_line(candidate.get(key), 180)
            for key in ("topic", "motive", "why", "scene", "impulse", "status")
            if _single_line(candidate.get(key), 180)
        )
        if not text:
            return False
        weather_tokens = (
            "天气", "气温", "温度", "降雨", "下雨", "阵雨", "小雨", "中雨", "大雨",
            "暴雨", "雷雨", "雷暴", "晴", "阳光", "多云", "阴天", "晚霞", "风",
            "外面在下雨", "天色",
        )
        location_tokens = (
            "当前位置", "当前地点", "所在城市", "住处", "住址", "地址", "小区", "街道",
            "校区", "宿舍", "家里", "学校", "工作地点", "路上", "通勤",
        )
        return any(token in text for token in weather_tokens) or any(token in text for token in location_tokens)

    def _pick_mobile_location_arrival_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Offer one gentle anchor when consented location confirms a place transition."""
        scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        if not callable(scene_getter):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        budget_available = getattr(self, "_mobile_location_humanization_budget_available", None)
        if callable(budget_available) and not budget_available(user, now=check_now):
            return None
        try:
            scene = scene_getter(user, now=check_now)
        except TypeError:
            try:
                scene = scene_getter(user)
            except Exception:
                return None
        except Exception:
            return None
        if not isinstance(scene, dict) or not scene.get("recent_transition"):
            return None
        transition_key = _single_line(scene.get("transition_key"), 80)
        place_name = _single_line(scene.get("place_name"), 40)
        place_kind = _single_line(scene.get("place_kind"), 24)
        transition_kind = _single_line(scene.get("transition_kind"), 24)
        if not transition_key or not place_name:
            return None
        if _single_line(user.get("last_mobile_location_arrival_key"), 80) == transition_key:
            return None
        weather = _single_line(self._weather_summary_text(self.data.get("daily_weather", {})), 120)
        risk_getter = getattr(self, "_mobile_location_weather_is_safety_relevant", None)
        weather_risk = bool(risk_getter(weather) if callable(risk_getter) else any(
            token in weather for token in ("暴雨", "雷雨", "雷暴", "台风", "大风", "强风")
        ))
        if transition_kind == "departure" and place_kind == "home":
            topic = "风雨天刚离开家后的路上" if weather_risk else "刚离开家后的路上"
            motive = "外面风雨明显，刚出门，想提醒你路上留意一点" if weather_risk else "刚出门，想顺手跟你说一声"
            scene_hint = "用户刚离开已标记的家"
        elif transition_kind == "departure" and place_kind == "work":
            topic = "风雨天刚离开公司后的这一段" if weather_risk else "刚离开公司后的这一段"
            motive = "外面风雨明显，刚离开公司，想提醒你路上留意一点" if weather_risk else "刚离开公司，想顺手问问接下来怎么走"
            scene_hint = "用户刚离开已标记的工作地点"
        elif transition_kind == "departure":
            topic = f"刚离开{place_name}后的这一段"
            motive = f"刚离开{place_name}，想顺手跟你说一声"
            scene_hint = f"用户刚离开已标记地点{place_name}"
        elif place_kind == "home":
            topic = "刚到家后的这一小段"
            motive = "刚到家，想顺手跟你说一声"
            scene_hint = "用户刚进入已标记的家"
        elif place_kind == "work":
            topic = "到公司后的这会儿"
            motive = "到公司后缓下来一点，想顺手跟你说一声"
            scene_hint = "用户刚进入已标记的工作地点"
        else:
            topic = f"到{place_name}后的这会儿"
            motive = f"刚到{place_name}，想顺手跟你说一声"
            scene_hint = f"用户刚进入已标记地点{place_name}"
        priority_key = _single_line(user.get("mobile_location_priority_key"), 80)
        priority_until = _safe_float(user.get("mobile_location_priority_until"), 0)
        is_priority_arrival = priority_key and priority_key == transition_key and priority_until > check_now
        delay_seconds = _engine_host.random.uniform(5, 20) if is_priority_arrival else _engine_host.random.uniform(45, 240)
        battery = scene.get("battery_percent")
        low_battery = isinstance(battery, int) and battery <= 15 and not bool(scene.get("charging"))
        tone = "轻一点，短一点，不邀请长通话" if low_battery else "轻一点"
        return {
            "event_id": f"mobile-place-{transition_kind or 'arrival'}:{place_kind}:{transition_key}",
            "reason": "check_in",
            "action": "message",
            "topic": topic,
            "motive": motive,
            "scene": scene_hint,
            "tone": tone,
            "impulse": motive,
            "_scheduled_ts": check_now + delay_seconds,
            "_mobile_location_transition_key": transition_key,
            "_mobile_location_priority": bool(is_priority_arrival),
            "mobile_location_event_type": (
                "home_arrival" if transition_kind != "departure" and place_kind == "home" else "place_transition"
            ),
            "weather_linked": bool(weather_risk and transition_kind == "departure"),
        }

    def _pick_game_invite_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn a strong, recent game afterglow into one optional rematch invite."""
        check_now = _engine_host._now_ts() if now is None else now
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        state_getter = getattr(self, "_game_afterglow_for_user", None)
        view_getter = getattr(self, "_game_afterglow_public_view", None)
        if not callable(state_getter) or not callable(view_getter):
            return None
        try:
            view = view_getter(state_getter(user), now=check_now)
        except Exception:
            return None
        interest = _safe_int(view.get("invite_interest"), 0, 0, 100)
        last_event_at = _safe_float(view.get("last_event_at"), 0)
        if not view.get("active") or interest < 70 or last_event_at <= 0:
            return None
        event_age = check_now - last_event_at
        if event_age < 30 * 60 or event_age > 5 * 24 * 3600:
            return None
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent > 0 and check_now - last_sent < 10 * 3600:
            return None
        game = _single_line(view.get("game"), 40)
        game_label = _single_line(view.get("game_label"), 40) or game or "上次那局游戏"
        invite_key = hashlib.sha1(f"{game}|{int(last_event_at)}".encode("utf-8")).hexdigest()[:20]
        if invite_key in {
            _single_line(user.get("last_game_invite_key"), 40),
            _single_line(user.get("game_invite_checked_key"), 40),
        }:
            return None
        user["game_invite_checked_key"] = invite_key
        invite_probability = min(0.78, 0.28 + max(0, interest - 70) / 100)
        if _engine_host.random.random() > invite_probability:
            return None
        scheduled = self._move_timestamp_into_reason_window(
            check_now + _engine_host.random.randint(30, 120) * 60,
            "game_invite",
            user,
        )
        context = {
            "invite_key": invite_key,
            "game": game,
            "game_label": game_label,
            "last_event_at": last_event_at,
            "invite_interest": interest,
            "tone": _single_line(view.get("tone"), 120),
            "reflection": _single_line(view.get("reflection"), 160),
        }
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - check_now) / 60)), width_minutes=60),
            "reason": "game_invite",
            "action": "message",
            "why": "最近一局游戏留下了明确的再玩意愿，想发一次可拒绝、无催促的邀约",
            "topic": f"再玩一局{game_label}",
            "motive": f"想起上次的{game_label}，有点想再约一局",
            "_scheduled_ts": scheduled,
            "context_key": "game_invite_context",
            "context": context,
            "origin_event_id": f"game_invite:{invite_key}",
        }

    def _pick_state_need_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return None
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text in {"", "饥饿感平稳", "该人格不适用饥饿状态"}:
            return None
        if self._food_prompt_cooldown_remaining(user, now=now) > 0:
            return None
        if _safe_float(user.get("last_food_feedback_at"), 0) + 2 * 3600 > now:
            return None
        active_hunger = None
        for cond in self._get_active_conditions():
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger":
                active_hunger = cond
                break
        if not isinstance(active_hunger, dict):
            return None
        started = _safe_float(active_hunger.get("start_ts"), now)
        if now - started < 25 * 60:
            return None
        when = self._environment_fromtimestamp(now)
        minute = when.hour * 60 + when.minute
        if not (10 * 60 + 30 <= minute <= 21 * 60 + 40):
            return None
        intensity = max(
            0.0,
            min(1.0, runtime_persona_setting(self, "humanized_state_intensity", 50) / 100),
        )
        chance = 0.18 + 0.32 * intensity
        if _engine_host.random.random() > chance:
            return None
        delay_minutes = _engine_host.random.randint(4, 12) if now - started >= 55 * 60 else _engine_host.random.randint(12, 32)
        scheduled = now + delay_minutes * 60
        phase = _single_line(active_hunger.get("phase"), 24)
        topic = "吃点什么"
        if phase == "afternoon":
            topic = _engine_host.random.choice(["下午想吃点甜的", "下午想吃点咸的", "下午想吃点热的", "下午想吃点凉的"])
        elif phase == "late_snack":
            topic = "夜里要不要吃点东西"
        elif phase in {"lunch", "dinner"}:
            topic = "这一顿吃什么"
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "state_share",
            "action": "message",
            "why": "有些饿了",
            "topic": topic,
            "motive": self._normalize_internal_motive_text(
                "有些饿了，想问问用户吃什么"
            ),
            "scene": "饭点或嘴馋的小空档",
            "tone": "自然",
            "impulse": "想问问用户吃什么比较好",
            "_scheduled_ts": scheduled,
            "_state_need": "hunger",
        }

    @staticmethod
    def _is_sticky_greeting_event(event: dict[str, Any]) -> bool:
        reason = str(event.get("reason") or "")
        return (
            bool(event.get("_daily_greeting"))
            and reason in {"morning_greeting", "noon_greeting", "evening_greeting"}
        ) or bool(event.get("_daily_meal_care"))

    def _pick_open_loop_followup_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn an unresolved conversation thread into a normal, expiring impulse."""
        if not bool(runtime_persona_setting(self, "enable_open_loop_tracking", True)):
            return None
        if self._private_user_role(user) == "friend":
            return None
        check_now = _engine_host._now_ts() if now is None else now
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            return None
        loops = user.get("open_loops")
        if not isinstance(loops, list):
            return None
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for item in loops:
            if not isinstance(item, dict) or str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            text = _single_line(item.get("text"), 120)
            created_at = _safe_float(item.get("created_ts"), 0)
            if not text or created_at <= 0:
                continue
            age = check_now - created_at
            if age < 4 * 3600 or age > 14 * 86400:
                continue
            last_candidate_at = _safe_float(item.get("proactive_candidate_at"), 0)
            if last_candidate_at > 0 and check_now - last_candidate_at < 36 * 3600:
                continue
            score_getter = getattr(self, "_open_loop_relevance_score", None)
            score = _safe_float(score_getter(item) if callable(score_getter) else 0.5, 0.5)
            candidates.append((score, created_at, item))
        if not candidates:
            return None
        _, created_at, selected = max(candidates, key=lambda value: (value[0], value[1]))
        text = _single_line(selected.get("text"), 120)
        sampler = getattr(self, "_sample_proactive_timestamp", None)
        scheduled = (
            sampler(user, now=check_now, delay_hours=(0.25, 2.0), reason="open_loop_followup")
            if callable(sampler)
            else check_now + _engine_host.random.uniform(15 * 60, 2 * 3600)
        )
        selected["proactive_candidate_at"] = check_now
        anonymous_pending = user.get("mobile_anonymous_area_pending")
        anonymous_linked = (
            isinstance(anonymous_pending, dict)
            and _safe_float(anonymous_pending.get("expires_at"), 0.0) > check_now
            and _safe_float(anonymous_pending.get("candidate_at"), 0.0) <= 0
        )
        if anonymous_linked:
            anonymous_pending["candidate_at"] = check_now
        open_loop_motive_prefix = "刚离开外面后，" if anonymous_linked else ""
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(max(15, int((scheduled - check_now) / 60)), width_minutes=75),
            "reason": "open_loop_followup",
            "action": "message",
            "why": "用户之前提过一件还没有下文的事，隔了一段时间后自然想起",
            "topic": text,
            "motive": self._normalize_internal_motive_text(f"{open_loop_motive_prefix}想自然问问之前提到的这件事后来怎么样了：{text}"),
            "scene": "离开外出区域后的聊天间隙，忽然想起对方之前说过的事" if anonymous_linked else "日常聊天间隙忽然想起对方之前说过的事",
            "tone": "像朋友随口问起，不像提醒或查岗",
            "impulse": "想知道那件事后来有没有新进展",
            "_scheduled_ts": scheduled,
            "origin_event_id": "open-loop:" + hashlib.sha1(f"{created_at}:{text}".encode("utf-8")).hexdigest()[:16],
            "context_key": "open_loop_followup_context",
            "context": {
                "text": text,
                "created_ts": created_at,
                "created_at": datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S"),
                "source": selected.get("source"),
                "after_anonymous_area_departure": anonymous_linked,
            },
            "followup_kind": "open_loop",
        }
