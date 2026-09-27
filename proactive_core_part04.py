# -*- coding: utf-8 -*-
"""ProactivePart04Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 560 行）。
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
from .proactive_core_shared import _unanswered_proactive_count
from .proactive_core_shared import datetime
from .proactive_core_shared import re
from .proactive_core_shared import req036_capability_summary
from .proactive_core_shared import req036_update_capabilities
from .proactive_core_shared import timedelta



class ProactivePart04Mixin:
    """ProactivePart04Mixin（从 ProactiveMixin 拆出）。"""


    def _friend_unanswered_silence_reason(self, user: dict[str, Any] | None, *, now: float | None = None) -> str:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return ""
        # 未回应只降低频率并把内容收敛为低压文字，不再把已授权用户永久停发。
        # 明确拒绝、休息和关系边界仍由统一互动/休息闸门处理。
        ignored = _unanswered_proactive_count(user)
        check_now = _now_ts() if now is None else now
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        unanswered_hours = (check_now - awaiting_since) / 3600.0 if awaiting_since > 0 else 0.0
        if ignored >= 2 or unanswered_hours >= 24:
            user["friend_unanswered_silence_note"] = (
                f"次要用户连续 {ignored} 次未回应"
                + (f"，已等待约 {unanswered_hours:.1f} 小时" if unanswered_hours > 0 else "")
                + "；继续使用低压文字并渐进延长间隔，不自动停发"
            )
        else:
            user["friend_unanswered_silence_note"] = ""
        user["friend_unanswered_silenced_since"] = 0
        return ""

    def _proactive_unanswered_pause_after(self) -> int:
        configured = _safe_int(
            _proactive_setting_value(self, "proactive_unanswered_pause_after", 0),
            0,
            0,
            50,
        )
        return self._effective_proactive_int(
            "pause_after",
            configured,
            minimum=0,
            maximum=50,
        )

    def _proactive_unanswered_pause_reason(self, user: dict[str, Any] | None) -> str:
        if not isinstance(user, dict):
            return ""
        pause_after = self._proactive_unanswered_pause_after()
        count = _unanswered_proactive_count(user)
        if pause_after <= 0 or count < pause_after:
            return ""
        return (
            f"连续 {count} 次主动消息未收到回复，已达到配置的暂停阈值 {pause_after}；"
            "收到用户新消息后恢复普通主动"
        )

    def _block_friend_unanswered_pending_proactive(
        self,
        user: dict[str, Any],
        *,
        note: str,
        now: float | None = None,
    ) -> None:
        if not isinstance(user, dict) or not note:
            return
        check_now = _now_ts() if now is None else now
        safe_note = _single_line(note, 160)
        impulse_cleaner = getattr(self, "_cleanup_proactive_impulses", None)
        if callable(impulse_cleaner):
            try:
                for impulse in impulse_cleaner(user, now=check_now):
                    if not isinstance(impulse, dict):
                        continue
                    state = _single_line(impulse.get("state") or "queued", 24).lower()
                    source = _single_line(impulse.get("source"), 40)
                    if state not in {"queued", "deferred", "pending", ""} or source in {"timer", "troubleshooting", "simulation"}:
                        continue
                    impulse["state"] = "blocked"
                    impulse["last_status"] = "blocked"
                    impulse["last_note"] = safe_note
                    impulse["updated_ts"] = check_now
            except Exception as exc:
                logger.debug("清理次要用户未回应主动念头失败: %s", _single_line(exc, 120))
        pool_cleaner = getattr(self, "_cleanup_proactive_candidate_pool", None)
        pending_checker = getattr(self, "_pending_candidate_status", None)
        candidate_user_getter = getattr(self, "_candidate_user_id", None)
        user_id = _single_line(user.get("user_id") or user.get("id"), 40)
        if callable(pool_cleaner) and callable(pending_checker) and callable(candidate_user_getter) and user_id:
            try:
                for candidate in pool_cleaner(now=check_now):
                    if not isinstance(candidate, dict):
                        continue
                    if candidate_user_getter(candidate) != user_id:
                        continue
                    source = _single_line(candidate.get("source"), 40)
                    if source in {"timer", "troubleshooting", "simulation"}:
                        continue
                    if not pending_checker(_single_line(candidate.get("status"), 24).lower()):
                        continue
                    candidate["status"] = "blocked"
                    candidate["note"] = safe_note
                    candidate["updated_ts"] = check_now
            except Exception as exc:
                logger.debug("清理次要用户未回应主动候选失败: %s", _single_line(exc, 120))

    @staticmethod
    def _friend_unanswered_should_remove_action(action: str) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek"})

    def _friend_unanswered_plan_patch(
        self,
        user: dict[str, Any],
        *,
        level: int,
        reason: str,
        action: str,
        topic: str,
        motive: str,
    ) -> dict[str, str]:
        if level <= 0:
            return {}
        high_pressure_reasons = {
            "check_in",
            "quiet_care",
            "state_share",
            "activity_share",
            "background_schedule",
            "diary_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "habit_awareness",
        }
        normalized_reason = str(reason or "check_in")
        normalized_action = "message" if self._friend_unanswered_should_remove_action(action) else (str(action or "message") or "message")
        if level == 1:
            if normalized_reason in high_pressure_reasons:
                normalized_reason = "quiet_care" if normalized_reason in {"check_in", "state_share", "habit_awareness"} else normalized_reason
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": _single_line(topic, 80) or "轻一点的近况",
                "motive": "对方还没接话，放轻；不催不追问",
            }
        if level == 2:
            return {
                "reason": "quiet_care",
                "action": "message",
                "topic": "低压近况",
                "motive": "对方有一阵没回应了，低压；不连问",
            }
        return {
            "reason": "quiet_care",
            "action": "message",
            "topic": "留出空间",
            "motive": "连续没回应，退一步；留空间",
        }

    @staticmethod
    def _friend_plan_has_private_interaction_text(text: Any) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        patterns = (
            r"给.{0,16}(?:回了?消息|发了?消息|回信|回复了?|发私聊)",
            r"(?:回了?消息|发了?消息|回信|发私聊|私聊|聊天|互相吐槽|互相安慰)",
            r"(?:约饭|夜宵|见面|出门|一起(?:做|看|聊|吃|去|玩|散步|上课|写|打))",
            r"(?:朋友用户|朋友边界|朋友那边|朋友私聊|次要用户|次要用户边界|次要用户那边|次要用户私聊)",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    def _sync_configured_targets(self):
        for user_id in self._configured_target_ids():
            user = self._get_user(user_id)
            capabilities = user.get("unified_profile_capabilities")
            needs_initial_route = not isinstance(capabilities, dict)
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, user)) if callable(migrator) else False
            capabilities = user.get("unified_profile_capabilities")
            if not isinstance(capabilities, dict) and not migrated:
                # ``target_user_ids`` is an administrator-managed legacy
                # permission source, not an inbound-DM signal.  Convert it
                # once when materializing a target record; future syncs only
                # read the frozen capability state and cannot reopen a user.
                req036_update_capabilities(
                    user,
                    {
                        "private_companion_enabled": True,
                        "proactive_private_enabled": _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0,
                    },
                    actor_authorized=True,
                    grant_source="legacy_configured_target_migration",
                    actor_id="startup_migration",
                    target_identity=user_id,
                    reason_code="legacy_configured_target_migration",
                )
            capability = req036_capability_summary(user)
            user["enabled"] = True
            user["target_user"] = True
            user.setdefault("nickname", _proactive_setting_value(self, "default_nickname", "小星"))
            if needs_initial_route or migrated or capability.get("proactive_private_enabled"):
                self._ensure_private_user_umo(user_id, user)
            if self._user_enabled_for_proactive(user_id, user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=_now_ts())

    def _prime_enabled_user_schedules(self) -> bool:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return False
        changed = False
        now = _now_ts()
        for raw_user in users.values():
            if not isinstance(raw_user, dict):
                continue
            raw_user_id = str(raw_user.get("user_id") or "")
            if not self._user_enabled_for_proactive(raw_user_id, raw_user):
                raw_user["enabled"] = True
                self._clear_pending_proactive_plan(raw_user)
                changed = True
                continue
            if self._ensure_private_user_umo(raw_user_id, raw_user):
                changed = True
            if not raw_user.get("umo"):
                continue
            if _safe_float(raw_user.get("next_proactive_at"), 0) > 0:
                if self._promote_earlier_daily_greeting_event(raw_user, now=now):
                    changed = True
                continue
            self._schedule_next_proactive(raw_user, now=now)
            changed = True
        return changed

    def _quiet_hours_end_timestamp(self, at_ts: float | None = None) -> float:
        quiet_hours = _proactive_setting_value(self, "quiet_hours", "23:00-08:30")
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(quiet_hours or ""))
        if not match:
            return 0.0
        sh, sm, eh, em = [int(part) for part in match.groups()]
        if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
            return 0.0
        start = sh * 60 + sm
        end = eh * 60 + em
        check_ts = _now_ts() if at_ts is None else float(at_ts)
        converter = getattr(self, "_environment_fromtimestamp", None)
        now = converter(check_ts) if callable(converter) else datetime.fromtimestamp(check_ts)
        current = now.hour * 60 + now.minute
        if start == end:
            return (now + timedelta(days=1)).replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if start < end:
            if not (start <= current < end):
                return 0.0
            return now.replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if current >= start:
            return (now + timedelta(days=1)).replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if current < end:
            return now.replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        return 0.0

    def _is_quiet_time(self) -> bool:
        return self._quiet_hours_end_timestamp() > _now_ts()

    def _reset_daily_counter_if_needed(self, user: dict[str, Any]):
        today = _today_key()
        if user.get("sent_day") != today:
            user["sent_day"] = today
            user["sent_today"] = 0
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}
        if user.get("photo_generated_day") != today:
            user["photo_generated_day"] = today
            user["photo_generated_today"] = 0
        if user.get("screen_peek_day") != today:
            user["screen_peek_day"] = today
            user["screen_peek_today"] = 0
            user["screen_peek_last_at"] = 0
        if user.get("greeting_sent_day") != today:
            user["greeting_sent_day"] = today
            user["greetings_sent"] = []
            user["greetings_suppressed_by_inbound"] = []
            user["morning_greeting_sent_at"] = 0
            user["morning_greeting_reply_at"] = 0
        if user.get("proactive_daypart_day") != today:
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}

    def _note_morning_greeting_reply(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        """Record the first inbound message after today's morning greeting."""
        self._reset_daily_counter_if_needed(user)
        reply_at = _now_ts() if now is None else now
        sent_at = _safe_float(user.get("morning_greeting_sent_at"), 0)
        previous_reply_at = _safe_float(user.get("morning_greeting_reply_at"), 0)
        if sent_at <= 0 or reply_at < sent_at or previous_reply_at >= sent_at:
            return False
        user["morning_greeting_reply_at"] = reply_at
        return True

    def _unanswered_slowdown_count(self, user: dict[str, Any]) -> int:
        ignored_streak = _unanswered_proactive_count(user)
        start = self._effective_proactive_int(
            "unanswered_slowdown_start",
            _safe_int(_proactive_setting_value(self, "proactive_unanswered_slowdown_start", 1), 1, 1, 10),
            minimum=1,
            maximum=10,
        )
        return max(0, ignored_streak - start + 1)

    def _unanswered_interval_multiplier(self, user: dict[str, Any]) -> float:
        active_count = self._unanswered_slowdown_count(user)
        max_multiplier = self._effective_proactive_float(
            "unanswered_max_interval_multiplier",
            max(1.0, _safe_float(_proactive_setting_value(self, "proactive_unanswered_max_interval_multiplier", 2.2), 2.2, 1.0)),
            minimum=1.0,
            maximum=8.0,
        )
        raw_multiplier = min(max_multiplier, 1.0 + active_count * 0.35)
        weight = _safe_float(
            self._proactive_quota_policy(user).get("unanswered_interval_weight"),
            1.0,
            0.0,
        )
        return 1.0 + max(0.0, raw_multiplier - 1.0) * min(1.0, weight)

    def _effective_min_interval_seconds(self, user: dict[str, Any], *, kind: str = "") -> int:
        route_kind = kind or self._planned_proactive_kind(user)
        route_policy = self._proactive_kind_policy(route_kind)
        multiplier = (
            self._unanswered_interval_multiplier(user)
            * self._cycle_proactive_frequency_profile()["private_interval_multiplier"]
            * _safe_float(route_policy.get("interval_multiplier"), 1.0, 0.05)
        )
        return int(self._effective_user_min_interval_minutes(user) * 60 * multiplier)

    def _bot_proactive_drive(self, user: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 120)
        conditions = state.get("conditions")
        score = 0.55 + (energy - 55) / 220.0
        reasons: list[str] = [f"energy={energy}"]
        if mood in {"轻快", "兴奋", "松弛", "明亮", "活跃"}:
            score += 0.08
            reasons.append(f"心情{mood}")
        elif mood in {"安静", "疲惫", "低落", "收声", "困倦"}:
            score -= 0.09
            reasons.append(f"心情{mood}")
        if any(token in note for token in ("疲惫", "困", "低电量", "收声", "慢一点")):
            score -= 0.08
            reasons.append("状态偏收")
        if any(token in note for token in ("轻快", "有精神", "想说话", "灵感", "开心")):
            score += 0.07
            reasons.append("状态偏开")
        if isinstance(conditions, list):
            for cond in conditions[:4]:
                text = _single_line(cond.get("label") or cond.get("text") or cond.get("kind"), 40) if isinstance(cond, dict) else _single_line(cond, 40)
                if any(token in text for token in ("疲惫", "困", "安静", "低落", "身体不舒服")):
                    score -= 0.04
                elif any(token in text for token in ("兴奋", "开心", "灵感", "想分享")):
                    score += 0.04
        score = max(0.12, min(1.0, score))
        if score >= 0.72:
            label = "想开口"
        elif score <= 0.42:
            label = "想收着"
        else:
            label = "平稳"
        return {
            "score": score,
            "label": label,
            "detail": "；".join(reasons[:4]),
            "energy": energy,
            "mood": mood,
        }

    def _proactive_response_readiness(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        score = 0.54
        reasons: list[str] = []
        ignored_streak = _unanswered_proactive_count(user)
        if ignored_streak:
            score -= min(0.32, ignored_streak * 0.08)
            reasons.append(f"未回应{ignored_streak}")
        last_reply_at = _safe_float(user.get("last_reply_at"), 0)
        if last_reply_at > 0:
            hours = (check_now - last_reply_at) / 3600.0
            if hours <= 6:
                score += 0.12
                reasons.append("刚有回应")
            elif hours <= 24:
                score += 0.06
                reasons.append("近一天回应过")
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        if awaiting_since > 0 and check_now - awaiting_since > 4 * 3600:
            score -= 0.08
            reasons.append("上一轮还悬着")
        score = max(0.05, min(1.0, score))
        if score >= 0.7:
            label = "温热"
        elif score <= 0.38:
            label = "偏冷"
        else:
            label = "普通"
        return {
            "score": score,
            "label": label,
            "detail": "；".join(reasons[:5]) or "回应节奏平稳",
        }

    def _relationship_proactive_temperature(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        drive: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility projection derived from the unified expression decision."""
        check_now = _now_ts() if now is None else now
        drive = drive if isinstance(drive, dict) else self._bot_proactive_drive(user, now=check_now)
        response = self._proactive_response_readiness(user, now=check_now)
        readiness_score = (
            _safe_float(drive.get("score"), 0.55) * 0.48
            + _safe_float(response.get("score"), 0.55) * 0.52
        )
        quiet_hours_active = False
        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        if callable(quiet_end_getter):
            try:
                quiet_hours_active = _safe_float(quiet_end_getter(check_now), 0) > check_now
            except Exception:
                quiet_hours_active = False
        expression_decision: dict[str, Any] = {}
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    proactive_candidate={
                        "eligible": True,
                        "dynamic_allowance": self._effective_user_daily_limit(user),
                        "readiness_score": int(max(0.0, min(1.0, readiness_score)) * 100),
                        "current_ts": check_now,
                    },
                    bot_state={"energy": drive.get("energy"), "mood": drive.get("mood")},
                    schedule={"quiet_hours": quiet_hours_active},
                    message_intent={"requested_content_tier": "normal"},
                    now=check_now,
                )
                expression_decision = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression_decision = {}
        expression_warmth = _safe_float(expression_decision.get("warmth"), 55, 0, 100) / 100.0
        score = response.get("score", 0.55) * 0.4 + expression_warmth * 0.6
        if expression_decision and _safe_int(expression_decision.get("proactive_budget"), 0, 0) <= 0:
            score = min(score, 0.2)
        score = max(0.05, min(1.0, score))
        label = "温热" if score >= 0.7 else "偏冷" if score <= 0.38 else "普通"
        reason_codes = expression_decision.get("reason_codes") if isinstance(expression_decision.get("reason_codes"), (list, tuple)) else []
        detail = "；".join(_single_line(item, 48) for item in reason_codes[:4]) or str(response.get("detail") or "统一表达平稳")
        return {
            "score": score,
            "label": label,
            "detail": detail,
            "expression_band": _single_line(expression_decision.get("expression_band"), 24) or "relaxed",
            "expression_decision": expression_decision,
            "response_readiness": response,
        }

    def _proactive_inner_readiness(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        drive = self._bot_proactive_drive(user, now=now)
        temperature = self._relationship_proactive_temperature(user, now=check_now, drive=drive)
        score = _safe_float(drive.get("score"), 0.55) * 0.48 + _safe_float(temperature.get("score"), 0.55) * 0.52
        expression_decision = temperature.get("expression_decision") if isinstance(temperature.get("expression_decision"), dict) else {}
        if expression_decision and _safe_int(expression_decision.get("proactive_budget"), 0, 0) <= 0:
            score = min(score, 0.2)
        motivation: dict[str, Any] = {}
        if bool(_proactive_setting_value(self, "enable_experimental_motivation_model", False)):
            motivation = self._experimental_proactive_motivation(user, now=now, drive=drive, temperature=temperature)
            modifier = (_safe_float(motivation.get("score"), 0.5) - 0.5) * 0.16
            score += modifier
        score = max(0.05, min(1.0, score))
        result = {
            "score": score,
            "label": f"{drive.get('label')}/{temperature.get('label')}",
            "detail": f"状态:{drive.get('detail')}; 关系:{temperature.get('detail')}",
            "drive": drive,
            "temperature": temperature,
        }
        if expression_decision:
            result["expression_decision"] = expression_decision
        if motivation:
            result["motivation"] = motivation
            result["detail"] = f"{result['detail']}; 动机:{motivation.get('label')} {motivation.get('detail')}"
        return result

    def _experimental_proactive_incentive(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        topic = _single_line(user.get("planned_proactive_topic"), 100)
        motive = _single_line(user.get("planned_proactive_motive"), 160)
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception:
                semantics = {}
        score = 0.5
        reasons: list[str] = []
        if reason in {"timer", "reminder", "pending_followup", "followup"} or source == "timer":
            score += 0.18
            reasons.append("任务/约定诱因")
        if reason in {"creative_share", "diary_share", "dream_share", "activity_share", "news_share", "web_exploration_share", "qzone_life_publish"}:
            score += 0.10
            reasons.append("有内容可分享")
        if reason in {"group_share", "atrelay_followup"}:
            score += 0.12
            reasons.append("外部互动线索")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            sent_today = _safe_int(user.get("sent_today"), 0, 0, 100)
            last_reply_at = _safe_float(user.get("last_reply_at"), 0)
            check_now = _now_ts() if now is None else now
            if sent_today > 0 or (last_reply_at > 0 and check_now - last_reply_at <= 3 * 3600):
                score -= 0.16
                reasons.append("问候诱因已释放")
            else:
                score += 0.04
                reasons.append("时段问候")
        if reason in {"check_in", "quiet_care", ""}:
            score -= 0.03
            reasons.append("泛关心诱因较弱")
        if action in {"photo_text", "voice", "poke"}:
            score += 0.04
            reasons.append(f"{action}动作诱因")
        if self._private_user_role(user) == "friend" and action in {"photo_text", "screen_peek"}:
            score -= 0.18
            reasons.append("次要用户能力边界")
        concrete_text = f"{topic} {motive}"
        if len(re.sub(r"\s+", "", concrete_text)) >= 18 and not any(token in concrete_text for token in ("问一句近况", "打个招呼", "在不在", "忙不忙")):
            score += 0.07
            reasons.append("切口具体")
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        score += (semantic_score - 0.5) * 0.10
        score -= max(0.0, semantic_pressure - 0.55) * 0.16
        ignored = _unanswered_proactive_count(user)
        if ignored:
            score -= min(0.22, ignored * 0.055)
            reasons.append(f"未回应{ignored}")
        score = max(0.05, min(1.0, score))
        label = "诱因强" if score >= 0.68 else "诱因弱" if score <= 0.38 else "诱因普通"
        return {"score": score, "label": label, "detail": "；".join(reasons[:5]) or "无明显外部诱因"}

    def _experimental_proactive_arousal(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 160)
        arousal = 0.38 + energy / 180.0
        reasons = [f"energy={energy}"]
        if mood in {"兴奋", "轻快", "活跃", "明亮"}:
            arousal += 0.12
            reasons.append(f"心情{mood}")
        elif mood in {"疲惫", "困倦", "低落", "收声", "安静"}:
            arousal -= 0.12
            reasons.append(f"心情{mood}")
        if any(token in note for token in ("高压", "赶", "急", "兴奋", "停不下来")):
            arousal += 0.08
            reasons.append("状态偏高")
        if any(token in note for token in ("困", "疲惫", "低电量", "慢一点", "收声")):
            arousal -= 0.08
            reasons.append("状态偏低")
        ignored = _unanswered_proactive_count(user)
        if ignored >= 2:
            arousal -= 0.06
            reasons.append("未回应降唤醒")
        arousal = max(0.0, min(1.0, arousal))
        fit = max(0.0, 1.0 - abs(arousal - 0.55) * 1.45)
        if arousal >= 0.78:
            label = "唤醒偏高"
        elif arousal <= 0.30:
            label = "唤醒偏低"
        else:
            label = "唤醒适中"
        return {"score": fit, "level": arousal, "label": label, "detail": "；".join(reasons[:4])}
