# -*- coding: utf-8 -*-
"""ProactiveEngineCandidateScoreSemanticsMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 484 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineCandidateMixin）。
"""
from __future__ import annotations
from .proactive_engine_candidate_shared import Any
from .proactive_engine_candidate_shared import _engine_host
from .proactive_engine_candidate_shared import _safe_float
from .proactive_engine_candidate_shared import _safe_int
from .proactive_engine_candidate_shared import _single_line
from .proactive_engine_candidate_shared import runtime_persona_setting



class ProactiveEngineCandidateScoreSemanticsMixin:
    """ProactiveEngineCandidateScoreSemanticsMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _score_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        check_now = _engine_host._now_ts() if now is None else now
        proactive_kind = _single_line(impulse.get("kind"), 40) or self._proactive_message_kind(
            reason=impulse.get("reason"),
            source=impulse.get("source"),
            semantic_kind=impulse.get("semantic_kind"),
        )
        impulse["kind"] = proactive_kind
        kind_policy = self._proactive_kind_policy(proactive_kind)
        quota_policy = self._proactive_quota_policy(user)
        created = _safe_float(impulse.get("created_ts"), check_now)
        preferred_ts = _safe_float(impulse.get("preferred_ts"), _safe_float(impulse.get("window_start_at"), check_now))
        best_until_at = _safe_float(impulse.get("best_until_at"), preferred_ts)
        age_hours = max(0.0, check_now - created) / 3600.0
        score = (
            _safe_float(impulse.get("salience"), 0.5)
            + _safe_float(impulse.get("urgency"), 0.3) * 0.9
            + _safe_float(impulse.get("warmth"), 0.3) * 0.7
        )
        score += _safe_float(kind_policy.get("score_bias"), 0.0)
        score += _safe_float(quota_policy.get("candidate_score_bias"), 0.0)
        source_feedback = self._proactive_source_feedback_modifier(
            user,
            _single_line(impulse.get("source"), 40),
        )
        score += source_feedback
        quota_tier = _safe_int(quota_policy.get("tier"), 0, 0, 5)
        if quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}:
            score += 0.05
        score -= age_hours * _safe_float(impulse.get("decay_per_hour"), 0.08)
        if preferred_ts > 0:
            score -= min(0.55, abs(check_now - preferred_ts) / 3600.0 * 0.14)
        if best_until_at > 0 and check_now > best_until_at:
            score -= min(0.7, (check_now - best_until_at) / 3600.0 * 0.25)
        if str(impulse.get("source") or "") in {"pending_followup", "followup"} or str(impulse.get("reason") or "") == "quiet_care":
            score += 0.05
        if (
            str(impulse.get("reason") or "") == "morning_greeting"
            and preferred_ts > 0
            and check_now <= max(preferred_ts, best_until_at)
        ):
            score += 0.10
        persona_fit = _safe_float(impulse.get("persona_fit"), -1.0)
        if persona_fit < 0:
            persona_alignment = self._proactive_persona_alignment(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 40) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 80),
                source=_single_line(impulse.get("source"), 40),
                now=check_now,
            )
            persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
            impulse["persona_fit"] = persona_fit
            impulse["persona_fit_note"] = _single_line(persona_alignment.get("note"), 160)
            impulse["persona_fit_blocker"] = bool(persona_alignment.get("blocker"))
        score += (persona_fit - 0.6) * 0.36
        if impulse.get("persona_fit_blocker"):
            score -= 0.45
        semantic_score = _safe_float(impulse.get("semantic_score"), -1.0)
        if semantic_score < 0:
            semantics = self._proactive_candidate_semantics(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 60) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 100),
                source=_single_line(impulse.get("source"), 40),
                context=impulse.get("context"),
                chain=impulse.get("chain") if isinstance(impulse.get("chain"), list) else [],
                trigger_message_id=_single_line(impulse.get("trigger_message_id"), 120),
                trigger_ts=_safe_float(impulse.get("trigger_ts"), 0),
            )
            semantic_score = _safe_float(semantics.get("score"), 0.5)
            impulse["semantic_kind"] = _single_line(semantics.get("kind"), 40)
            impulse["semantic_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            impulse["semantic_score"] = semantic_score
            impulse["semantic_anchor_score"] = _safe_float(semantics.get("anchor_score"), 0.5)
            impulse["semantic_pressure"] = _safe_float(semantics.get("pressure"), 0.4)
            impulse["semantic_risk"] = _safe_float(semantics.get("risk"), 0.0)
            impulse["semantic_note"] = _single_line(semantics.get("note"), 180)
            impulse["semantic_need_layer"] = _single_line(semantics.get("need_layer"), 40)
            impulse["semantic_need_drive"] = _single_line(semantics.get("need_drive"), 80)
            impulse["semantic_need_note"] = _single_line(semantics.get("need_note"), 120)
            impulse["semantic_need_score_bias"] = _safe_float(semantics.get("need_score_bias"), 0.0)
            impulse["semantic_need_pressure_bias"] = _safe_float(semantics.get("need_pressure_bias"), 0.0)
            impulse["semantic_blocker"] = bool(semantics.get("blocker"))
        score += (semantic_score - 0.5) * 0.42
        score -= max(0.0, _safe_float(impulse.get("semantic_pressure"), 0.4) - 0.55) * 0.22
        score -= _safe_float(impulse.get("semantic_risk"), 0.0) * 0.42
        if impulse.get("semantic_blocker"):
            score -= 0.5
        readiness = self._proactive_inner_readiness(user, now=check_now)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        score += (_safe_float(readiness.get("score"), 0.55) - 0.55) * 0.38
        score += (_safe_float(temperature.get("score"), 0.55) - 0.55) * 0.22
        hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        if hesitation_count > 0:
            score += min(0.12, hesitation_count * 0.035)
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 2:
            unanswered_penalty = _safe_float(kind_policy.get("unanswered_score_penalty"), 0.08, 0.0)
            if quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}:
                unanswered_penalty = 0.0
            score -= unanswered_penalty
        return score

    def _proactive_candidate_semantics(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        context: Any = None,
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_ts: float = 0,
    ) -> dict[str, Any]:
        normalized_reason = _single_line(reason, 40) or "check_in"
        normalized_action = _single_line(action, 60) or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 100)
        normalized_source = _single_line(source, 40)
        context_text = self._proactive_semantic_evidence_text(context)
        chain_text = self._proactive_semantic_chain_text(chain)
        has_context = bool(context_text)
        has_chain = bool(chain_text)
        has_trigger = bool(_single_line(trigger_message_id, 120))
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        evidence_text = f"{text} {context_text} {chain_text}"
        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        kind = "check_in"
        if normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            kind = "greeting"
        elif normalized_reason in {"meal_care", "meal_care_followup"}:
            kind = "care"
        elif normalized_reason in {
            "quiet_care", "state_share", "post_goodnight_group_activity",
            "memory_echo", "mood_checkin", "absence_miss",
            "anonymous_area_dwell", "anonymous_area_familiarity",
        }:
            kind = "care"
        elif normalized_reason in {"activity_share", "diary_share", "background_schedule", "creative_share", "personal_goal_progress"}:
            kind = "self_share"
        elif normalized_reason in {"important_date_share", "memo_note_reminder", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow"}:
            kind = "reminder"
        elif normalized_reason in {"environment_change", "weather_alert"}:
            kind = "observation"
        elif normalized_reason in {"group_share", "bili_video_share", "news_share", "web_exploration_share", "game_invite"}:
            kind = "external_share"
        elif normalized_source in {"pending_followup", "followup"}:
            kind = "continuation"
        elif action_parts & {"screen_peek"}:
            kind = "observation"
        elif action_parts & {"poke"}:
            kind = "light_touch"

        anchor_type = "vague"
        anchor_score = 0.28
        if normalized_reason == "memory_echo":
            anchor_type, anchor_score = "cross_day_memory", 0.76
        elif normalized_reason in {"mood_checkin", "absence_miss"}:
            anchor_type, anchor_score = "recent_context", 0.76
        elif normalized_source in {"pending_followup", "followup"} or has_trigger or has_chain or "前面提过" in evidence_text:
            anchor_type, anchor_score = "recent_context", 0.78
        elif normalized_reason in {"group_share", "post_goodnight_group_activity"} or "群" in evidence_text:
            anchor_type, anchor_score = "group_context", 0.72
        elif normalized_reason in {"diary_share", "creative_share"} or any(token in evidence_text for token in ("日记", "写到", "作品", "片段")):
            anchor_type, anchor_score = "inner_life", 0.68
        elif normalized_reason in {"meal_care", "meal_care_followup"} or any(token in evidence_text for token in ("早餐", "早饭", "午饭", "午餐", "晚饭", "晚餐", "吃了吗", "吃了没")):
            anchor_type, anchor_score = "meal_time", 0.74
        elif normalized_reason in {"background_schedule"} or any(token in evidence_text for token in ("手上", "忙到", "日程", "计划", "刚好停")):
            anchor_type, anchor_score = "current_activity", 0.62
        elif normalized_reason in {"important_date_share", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow"} or any(token in evidence_text for token in ("生日", "纪念", "日期", "考试", "提醒")):
            anchor_type, anchor_score = "important_date", 0.78
        elif normalized_reason in {"environment_change", "weather_alert"}:
            anchor_type, anchor_score = "environment", 0.82
        elif normalized_reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}:
            anchor_type, anchor_score = "environment", 0.68
        elif normalized_reason in {"news_share", "web_exploration_share", "bili_video_share"}:
            anchor_type, anchor_score = "external_info", 0.66
        elif normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            anchor_type, anchor_score = "time_ritual", 0.55
        elif any(token in evidence_text for token in ("天气", "雨", "阳光", "晚霞", "天色", "风", "窗")):
            anchor_type, anchor_score = "environment", 0.58
        elif has_context:
            anchor_type, anchor_score = "topic_hint", 0.56
        elif normalized_topic:
            anchor_type, anchor_score = "topic_hint", 0.48

        pressure = 0.34
        if kind in {"greeting", "self_share", "reminder"}:
            pressure -= 0.04
        if kind in {"check_in", "care", "observation", "light_touch"}:
            pressure += 0.08
        if action_parts & {"screen_peek", "poke", "voice"}:
            pressure += 0.16
        if has_context or has_trigger:
            pressure -= 0.05
        if has_chain and normalized_source in {"pending_followup", "followup", "daily_greeting"}:
            pressure -= 0.03
        if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            pressure += 0.18
        if self._private_user_role(user) == "friend":
            pressure += 0.08
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            pressure += min(0.22, _safe_int(user.get("ignored_streak"), 0, 0) * 0.06)

        risk = 0.0
        blocker = False
        notes: list[str] = []

        def note(value: str) -> None:
            clean = _single_line(value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        if anchor_score >= 0.65:
            note(f"由头明确:{anchor_type}")
        elif anchor_score <= 0.35:
            note("由头偏虚")
        if pressure >= 0.62:
            note("打扰压力偏高")
        if self._unverified_social_relay_plan_reason(
            {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
                "scene": context_text,
            },
            source=normalized_source,
            has_trigger=has_trigger,
        ):
            risk += 0.35
            blocker = True
            note("疑似无来源转述")
        if any(token in evidence_text for token in ("模型", "插件", "接口", "后台", "提示词", "系统调度", "action")):
            risk += 0.42
            blocker = True
            note("内部机制泄露")
        if self._friend_can_receive_proactive_reason(user, normalized_reason, normalized_action) is False:
            risk += 0.35
            blocker = True
            note("次要用户关系语义越界")
        if self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            risk += 0.18
            if self._private_user_role(user) == "friend":
                risk += 0.18
                note("次要用户关系亲密过量")
        score = 0.52 + (anchor_score - 0.5) * 0.42 - max(0.0, pressure - 0.45) * 0.36 - risk * 0.5
        if kind in {"continuation", "reminder"}:
            score += 0.08
        if kind == "self_share" and anchor_score >= 0.48:
            score += 0.05
        if has_context and anchor_score >= 0.5:
            score += 0.04
        if kind == "check_in" and anchor_score < 0.45:
            score -= 0.08
        need_profile: dict[str, Any] = {}
        if bool(runtime_persona_setting(self, "enable_maslow_motivation_experiment", False)):
            need_profile = self._maslow_motivation_profile(
                user,
                reason=normalized_reason,
                action=normalized_action,
                motive=normalized_motive,
                topic=normalized_topic,
                source=normalized_source,
                semantic_kind=kind,
                anchor_type=anchor_type,
                anchor_score=anchor_score,
                evidence_text=evidence_text,
            )
            strength = max(
                0.0,
                min(
                    1.0,
                    _safe_float(
                        runtime_persona_setting(self, "maslow_motivation_strength", 35),
                        35,
                        0.0,
                    )
                    / 100.0,
                ),
            )
            score += _safe_float(need_profile.get("score_bias"), 0.0) * strength
            pressure += _safe_float(need_profile.get("pressure_bias"), 0.0) * strength
            need_note = _single_line(need_profile.get("note"), 60)
            if need_note:
                note(f"实验动机:{need_note}")
        score = max(0.0, min(1.0, score))
        if not notes:
            note(f"{kind}/{anchor_type}")
        result = {
            "kind": kind,
            "anchor_type": anchor_type,
            "anchor_score": anchor_score,
            "pressure": max(0.0, min(1.0, pressure)),
            "risk": max(0.0, min(1.0, risk)),
            "score": score,
            "note": "；".join(notes[:4]),
            "blocker": blocker,
        }
        if need_profile:
            result.update(
                {
                    "need_layer": _single_line(need_profile.get("layer"), 40),
                    "need_drive": _single_line(need_profile.get("drive"), 80),
                    "need_note": _single_line(need_profile.get("note"), 120),
                    "need_score_bias": _safe_float(need_profile.get("score_bias"), 0.0),
                    "need_pressure_bias": _safe_float(need_profile.get("pressure_bias"), 0.0),
                }
            )
        return result

    def _planned_proactive_semantics(self, user: dict[str, Any]) -> dict[str, Any]:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return {
                "kind": _single_line(impulse.get("semantic_kind"), 40),
                "anchor_type": _single_line(impulse.get("semantic_anchor_type"), 40),
                "score": _safe_float(impulse.get("semantic_score"), 0.5),
                "anchor_score": _safe_float(impulse.get("semantic_anchor_score"), 0.5),
                "pressure": _safe_float(impulse.get("semantic_pressure"), 0.4),
                "risk": _safe_float(impulse.get("semantic_risk"), 0.0),
                "note": _single_line(impulse.get("semantic_note"), 180),
                "need_layer": _single_line(impulse.get("semantic_need_layer"), 40),
                "need_drive": _single_line(impulse.get("semantic_need_drive"), 80),
                "need_note": _single_line(impulse.get("semantic_need_note"), 120),
                "need_score_bias": _safe_float(impulse.get("semantic_need_score_bias"), 0.0),
                "need_pressure_bias": _safe_float(impulse.get("semantic_need_pressure_bias"), 0.0),
                "blocker": bool(impulse.get("semantic_blocker")),
            }
        return self._proactive_candidate_semantics(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=60) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 100),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            chain=user.get("planned_event_chain") if isinstance(user.get("planned_event_chain"), list) else [],
            trigger_message_id=_single_line(user.get("planned_proactive_trigger_message_id"), 120),
            trigger_ts=_safe_float(user.get("planned_proactive_trigger_ts"), 0),
        )

    def _planned_proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        return self._proactive_persona_alignment(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 80),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            now=now,
        )

    def _planned_proactive_impulse(self, user: dict[str, Any]) -> dict[str, Any] | None:
        impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        if not impulse_id:
            return None
        for item in self._cleanup_proactive_impulses(user):
            if isinstance(item, dict) and _single_line(item.get("id"), 20) == impulse_id:
                return item
        return None

    def _planned_impulse_value(self, user: dict[str, Any], *, now: float | None = None) -> float:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return self._score_proactive_impulse(user, impulse, now=now)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        value = 0.62
        if source in {"timer", "troubleshooting", "simulation"}:
            value += 0.35
        if source in {"pending_followup", "daily_greeting", "story", "state"}:
            value += 0.08
        if reason in {"important_date_share", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow", "quiet_care", "group_share", "news_share", "creative_share"}:
            value += 0.08
        if self._private_user_role(user) == "friend":
            value -= 0.06
        return value

    def _planned_impulse_window_phase(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[str, str]:
        check_now = _engine_host._now_ts() if now is None else now
        start_at = _safe_float(user.get("planned_proactive_window_start_at"), 0)
        best_until = _safe_float(user.get("planned_proactive_best_until_at"), 0)
        expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if expire_at > 0 and check_now > expire_at:
            return "expired", "念头窗口已过期"
        if start_at > 0 and check_now < start_at:
            return "before", f"距窗口开始还有 {self._format_elapsed(start_at - check_now)}"
        if best_until > 0 and check_now <= best_until:
            return "best", f"正处于最佳表达窗口,剩余 {self._format_elapsed(best_until - check_now)}"
        if expire_at > 0:
            return "tail", f"已过最佳窗口,距过期 {self._format_elapsed(max(0, expire_at - check_now))}"
        return "unknown", "未记录念头窗口"

    def _proactive_item_freshness_class(
        self,
        *,
        action: str,
        reason: str,
        source: str,
        semantic_kind: str = "",
    ) -> str:
        normalized_reason = self._normalize_legacy_proactive_text(reason, limit=40)
        normalized_source = self._normalize_legacy_proactive_text(source, limit=40)
        normalized_kind = self._normalize_legacy_proactive_text(semantic_kind, limit=40)
        if normalized_reason in {"environment_change", "weather_alert", "health_alert", "memo_note_reminder"} or normalized_source in {
            "environment_change",
            "weather_alert",
            "body_monitor",
            "memo_note",
        }:
            return "immediate"
        if normalized_source == "timer" or normalized_reason in {
            "birthday_eve_hint",
            "birthday_celebration",
            "birthday_makeup",
            "birthday_afterglow",
            "important_date_share",
            "special_day_greeting",
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
        }:
            return "durable"
        action_parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        if {"photo_text", "screen_peek"} & action_parts:
            return "immediate"
        if normalized_kind in {"self_share", "observation"} and normalized_source in {
            "story",
            "daily_story",
            "state",
            "event",
            "simulation",
        }:
            return "immediate"
        return "contextual"

    def _proactive_timeliness_level(
        self,
        *,
        reason: Any = "",
        source: Any = "",
    ) -> str:
        """Classify only events whose value materially decays within minutes."""

        normalized_reason = self._normalize_legacy_proactive_text(reason, limit=40)
        normalized_source = self._normalize_legacy_proactive_text(source, limit=40)
        if normalized_reason in {"weather_alert", "health_alert"} or normalized_source in {
            "weather_alert",
            "body_monitor",
        }:
            return "urgent"
        if normalized_reason in {
            "environment_change",
            "memo_note_reminder",
            "birthday_celebration",
            "special_day_greeting",
            "insomnia_night",
        } or normalized_source in {
            "environment_change",
            "memo_note",
            "special_day_ritual",
            "night_care",
        }:
            return "timely"
        return "routine"

    @staticmethod
    def _proactive_timeliness_rank(level: Any) -> int:
        return {"routine": 0, "timely": 1, "urgent": 2}.get(str(level or "routine"), 0)
