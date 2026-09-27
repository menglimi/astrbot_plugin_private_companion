# -*- coding: utf-8 -*-
"""画像与情绪关系面板域。

由 tools/split_mixin_domain.py 从 page_api_summary_panel.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 601 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiSummaryPanelMixin）。
"""
from __future__ import annotations

import time
from .companion_interaction_expression import current_interaction_projection
from .relationship_ledger import normalize_relationship_mode, relationship_ledger_summary
from typing import Any



class PrivateCompanionPageApiSummaryPanelProfileMixin:
    """画像与情绪关系面板域（从 PrivateCompanionPageApiSummaryPanelMixin 拆出）。"""


    def _relationship_panel(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        relationship_stage: str,
    ) -> dict[str, Any]:
        """Return a user-scoped display DTO without authority or private content."""
        del user_id
        role = self.plugin._private_user_role(user, str(user.get("user_id") or "")) if hasattr(self.plugin, "_private_user_role") else str(user.get("relationship_role") or "friend")
        mode = normalize_relationship_mode(user.get("relationship_mode"), role)
        intimacy = self._relationship_intimacy_projection(self._int(user.get("relationship_score")))
        changes = relationship_ledger_summary(user)
        intimacy["trend"] = changes.get("trend", "steady")
        intimacy["recent_delta"] = changes.get("recent_delta", 0)
        interaction = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
            now=time.time(),
        )
        expression: dict[str, Any] = {
            "contract": "companion_interaction_expression.v2",
            "status": "configured_projection",
            "expression_band": interaction.get("expression_band") or "relaxed",
            "tone": "steady",
            "response_length": "balanced",
            "initiative": "passive_only",
            "pacing": "steady",
            "directness": "natural",
            "validation_style": "none",
            "self_disclosure": "none",
            "humor_mode": "off",
            "topic_initiative": "reply_only",
            "safety_mode": "live_event_not_evaluated",
            "blocker": "",
            "reason_codes": [],
        }
        builder = getattr(self.plugin, "_build_expression_decision_for_user", None)
        if callable(builder):
            try:
                raw = builder(user, passive_reengagement=True)
                raw_projection = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw or {})
                if isinstance(raw_projection, dict):
                    expression.update(raw_projection)
            except Exception:
                pass
        dimension_defaults = {
            "pacing": "steady",
            "directness": "natural",
            "validation_style": "none",
            "self_disclosure": "none",
            "humor_mode": "off",
            "topic_initiative": "reply_only",
        }
        dimension_values = {
            "pacing": {"slow", "steady", "bright"},
            "directness": {"indirect", "natural", "direct"},
            "validation_style": {"none", "acknowledge", "support_first"},
            "self_disclosure": {"none", "light", "allowed"},
            "humor_mode": {"off", "light", "playful"},
            "topic_initiative": {"reply_only", "followup", "shared_topic"},
        }
        expression["contract"] = "companion_interaction_expression.v2"
        for key, allowed in dimension_values.items():
            value = self._single_line(expression.get(key), 20)
            expression[key] = value if value in allowed else dimension_defaults[key]
        inbound = max(0, self._int(user.get("inbound_count")))
        proactive = max(0, self._int(user.get("proactive_sent_count")))
        replies = max(0, self._int(user.get("reply_count")))
        if not proactive:
            reply_band = "no_proactive_sample"
        elif replies / proactive >= 0.65:
            reply_band = "steady"
        elif replies / proactive >= 0.30:
            reply_band = "some"
        else:
            reply_band = "low"

        score = self._int(user.get("relationship_score"))
        basis = "close" if score >= 55 else "familiar" if score >= 3 else "initial"
        memory_phase = {"status": "unavailable", "phase": "unknown", "momentum_band": "unknown"}
        getter = getattr(self.plugin, "_memory_companion_peek_relationship_phase", None)
        session_id = self._single_line(user.get("umo"), 200)
        if session_id and callable(getter):
            try:
                raw = getter(session_id=session_id)
            except Exception:
                raw = {}
            if isinstance(raw, dict):
                observed = raw.get("observed") is True and raw.get("status") == "observed"
                phase = self._single_line(raw.get("phase"), 32)
                memory_phase = {
                    "status": "observed" if observed and phase else "not_observed",
                    "phase": phase if observed and phase else "unknown",
                    "momentum_band": self._single_line(raw.get("momentum_band"), 20) if observed else "unknown",
                }

        return {
            "relationship_mode": mode,
            "relationship_intimacy": intimacy,
            "relationship_changes": changes,
            "current_interaction": interaction,
            "expression_decision": expression,
            "relationship_positive_stage_cap_key": getattr(self.plugin, "relationship_positive_stage_cap_key", "close"),
            "normal_interaction_band_cap": getattr(self.plugin, "normal_interaction_band_cap", "warm"),
            "relationship_basis": {"band": basis},
            "relationship_stage": self._single_line(relationship_stage, 24) or "unclassified",
            "interaction": {"inbound_count": inbound, "reply_count": replies, "reply_band": reply_band},
            "memory_phase": memory_phase,
            "network": {"status": "group_local_only", "pending_observation_count": 0},
            "reply_temperature": {"status": "live_chat_only"},
        }

    def _user_summary(self, user_id: str, user: dict[str, Any]) -> dict[str, Any]:
        last_seen = user.get("last_seen", 0)
        last_sent = user.get("last_sent", 0)
        user_id_text = str(user_id)
        is_qq_user = user_id_text.isdigit()
        umo = str(
            user.get("umo")
            or user.get("bound_delivery_umo")
            or user.get("preferred_delivery_umo")
            or user.get("last_inbound_umo", "")
            or ""
        )
        source = self._single_line(umo.split(":", 1)[0], 40) if ":" in umo else ""
        platform_profile_getter = getattr(self.plugin, "_platform_profile", None)
        platform_profile = platform_profile_getter(umo=umo) if callable(platform_profile_getter) else {}
        platform_kind = self._single_line((platform_profile or {}).get("kind"), 40) or ("onebot" if is_qq_user else "generic")
        nickname = self._single_line(user.get("nickname"), 40)
        generic_names = {"用户", "主人", "主要用户", "默认用户", "临时会话"}
        profile_origin = self._single_line(user.get("profile_origin"), 40)
        capabilities = user.get("unified_profile_capabilities") if isinstance(user.get("unified_profile_capabilities"), dict) else {}
        grant_source = self._single_line(capabilities.get("grant_source"), 60)
        group_observation_identity = bool(
            profile_origin == "group_observation"
            or user.get("observation_only")
            or (
                grant_source == "legacy_effective_migration"
                and not umo
                and not self._float(last_seen)
            )
        )
        directory_scope = "group" if group_observation_identity else "private"
        if is_qq_user:
            display_name = nickname if nickname and nickname not in generic_names else user_id_text
            if group_observation_identity:
                resolver = getattr(self.plugin, "_group_member_identity_name", None)
                if callable(resolver):
                    try:
                        resolved = self._single_line(resolver(user_id_text, nickname, limit=40), 40)
                        if resolved and resolved != user_id_text:
                            display_name = resolved
                    except Exception:
                        pass
        elif platform_kind == "qq_official":
            display_name = nickname if nickname and nickname not in generic_names else f"QQ 官方 · {user_id_text[:8]}"
        else:
            display_name = f"临时会话 · {user_id_text[:8]}"
        relationship_stage = ""
        profile_getter = getattr(self.plugin, "_relationship_profile", None)
        if not relationship_stage and callable(profile_getter):
            try:
                profile = profile_getter(user)
                if isinstance(profile, dict):
                    relationship_stage = self._single_line(profile.get("level"), 12)
            except Exception:
                relationship_stage = ""
        if relationship_stage not in {"亲近", "熟悉", "陌生"}:
            score = self._int(user.get("relationship_score"))
            inbound_count = self._int(user.get("inbound_count"))
            proactive_count = self._int(user.get("proactive_sent_count"))
            reply_count = self._int(user.get("reply_count"))
            reply_rate = reply_count / proactive_count if proactive_count > 0 else 0.0
            if score >= 16 and reply_rate >= 0.35:
                relationship_stage = "亲近"
            elif score >= 3 or inbound_count >= 1 or reply_rate >= 0.2:
                relationship_stage = "熟悉"
            else:
                relationship_stage = "陌生"
        role = self.plugin._private_user_role(user, user_id_text) if hasattr(self.plugin, "_private_user_role") else ""
        role_labeler = getattr(self.plugin, "_private_user_role_label", None)
        role_label = role_labeler(role) if callable(role_labeler) else ("主要用户" if role == "owner" else "次要用户")
        relationship_panel = self._relationship_panel(
            user_id_text,
            user,
            relationship_stage=relationship_stage,
        )
        relationship_mode = relationship_panel["relationship_mode"]
        relationship_intimacy = relationship_panel["relationship_intimacy"]
        current_interaction = relationship_panel["current_interaction"]
        if relationship_mode == "owner_exclusive":
            relationship_stage = self._single_line(getattr(self.plugin, "owner_exclusive_label", "专属联结"), 20) or "专属联结"
            relationship_intimacy["owner_exclusive"] = {
                "label": relationship_stage,
                "tone": self._single_line(getattr(self.plugin, "owner_exclusive_tone", "温暖、亲近、稳定"), 120),
                "fixed": True,
            }
        else:
            relationship_stage = self._single_line((relationship_intimacy.get("phase") or {}).get("label"), 20) or relationship_stage
        exclusive_prompt_status_getter = getattr(
            self.plugin,
            "_owner_exclusive_relationship_prompt_status",
            None,
        )
        owner_exclusive_relationship_prompt = (
            exclusive_prompt_status_getter(user, stable_user_id=user_id_text)
            if callable(exclusive_prompt_status_getter)
            else {
                "persona_id": "",
                "persona_label": "当前人格",
                "stable_user_id": user_id_text,
                "text": "",
                "configured": False,
                "eligible": role == "owner",
                "active": False,
                "relationship_mode": relationship_mode,
                "max_chars": 2400,
            }
        )
        slowdown_count_getter = getattr(self.plugin, "_unanswered_slowdown_count", None)
        multiplier_getter = getattr(self.plugin, "_unanswered_interval_multiplier", None)
        unanswered_slowdown_count = 0
        unanswered_interval_multiplier = 1.0
        if callable(slowdown_count_getter):
            try:
                unanswered_slowdown_count = max(0, self._int(slowdown_count_getter(user)))
            except Exception:
                unanswered_slowdown_count = 0
        if callable(multiplier_getter):
            try:
                unanswered_interval_multiplier = max(1.0, float(multiplier_getter(user)))
            except Exception:
                unanswered_interval_multiplier = 1.0
        unanswered_slowdown_text = (
            f"连续未回应 {self._int(user.get('ignored_streak'))} 次，最小主动间隔 ×{unanswered_interval_multiplier:.2f}"
            if unanswered_slowdown_count > 0
            else "未触发"
        )
        soft_daily_target = 0.0
        soft_target_getter = getattr(self.plugin, "_soft_daily_target", None)
        if callable(soft_target_getter):
            try:
                soft_daily_target = max(0.0, float(soft_target_getter(user)))
            except Exception:
                soft_daily_target = 0.0
        quota_policy_getter = getattr(self.plugin, "_proactive_quota_policy", None)
        quota_policy = quota_policy_getter(user) if callable(quota_policy_getter) else {}
        pending_emotion_judgement = self._emotion_pending_judgement_summary(user.get("pending_emotion_judgement"))
        last_emotion_judgement = self._emotion_last_judgement_summary(user.get("last_emotion_judgement"))
        last_emotion_judgement_error = self._emotion_judgement_error_summary(user.get("last_emotion_judgement_error"))
        capability_getter = getattr(self.plugin, "_req036_capability_summary_for_user", None)
        capability_summary = capability_getter(user) if callable(capability_getter) else {
            "private_companion_enabled": True,
            "proactive_private_enabled": False,
            "effective_proactive_private_enabled": False,
            "portrait_mode": "disabled",
            "portrait_learning_enabled": False,
            "portrait_usage_enabled": False,
            "grant_source": "legacy",
            "blocked_reasons": [],
        }
        return {
            "user_id": user_id_text,
            "display_name": display_name,
            "directory_scope": directory_scope,
            "observation_only": group_observation_identity,
            "is_qq_user": is_qq_user,
            "platform_kind": platform_kind,
            "platform_label": self._single_line((platform_profile or {}).get("label"), 60),
            "identity_label": self._single_line((platform_profile or {}).get("identity_label"), 60),
            "stable_platform_identity": bool(platform_kind == "qq_official" or is_qq_user),
            "source": source,
            "enabled": True,
            "private_companion_enabled": True,
            "proactive_private_enabled": bool(capability_summary.get("proactive_private_enabled")),
            "portrait_mode": self._single_line(capability_summary.get("portrait_mode"), 40) or "disabled",
            "portrait_learning_enabled": bool(capability_summary.get("portrait_learning_enabled")),
            "portrait_usage_enabled": bool(capability_summary.get("portrait_usage_enabled")),
            "portrait_mode_override": self._single_line(
                (user.get("unified_profile_capabilities") if isinstance(user.get("unified_profile_capabilities"), dict) else {}).get("portrait_mode_override"),
                40,
            ) or "follow_global",
            "capability_summary": capability_summary,
            "unified_person_id": self._single_line(user.get("unified_person_id"), 80),
            "proactive_contact_enabled": bool(capability_summary.get("effective_proactive_private_enabled")),
            "relationship_role": role,
            "relationship_role_label": role_label,
            "nickname": user.get("nickname", ""),
            "style": user.get("style", ""),
            "umo": user.get("umo", ""),
            "delivery_bound": bool(self._single_line(user.get("bound_delivery_umo"), 240)),
            "bound_delivery_umo": self._single_line(user.get("bound_delivery_umo"), 240),
            "last_seen_ts": last_seen,
            "last_seen": self.plugin._format_timestamp_elapsed(last_seen),
            "last_sent_ts": last_sent,
            "last_sent": self.plugin._format_timestamp_elapsed(last_sent),
            "sent_today": user.get("sent_today", 0),
            "last_proactive_skip_ts": self._float(user.get("last_proactive_skip_at")),
            "last_proactive_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
            "last_proactive_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
            "last_proactive_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
            "effective_daily_limit": (
                self.plugin._effective_user_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_daily_limit")
                else getattr(self.plugin, "max_daily_messages", 0)
            ),
            "effective_daily_limit_text": (
                self.plugin._format_proactive_daily_limit(self.plugin._effective_user_daily_limit(user))
                if hasattr(self.plugin, "_format_proactive_daily_limit") and hasattr(self.plugin, "_effective_user_daily_limit")
                else str(getattr(self.plugin, "max_daily_messages", 0))
            ),
            "effective_daily_limit_unlimited": (
                self.plugin._proactive_daily_limit_is_unlimited(self.plugin._effective_user_daily_limit(user))
                if hasattr(self.plugin, "_proactive_daily_limit_is_unlimited") and hasattr(self.plugin, "_effective_user_daily_limit")
                else False
            ),
            "soft_daily_target": round(soft_daily_target, 2),
            "proactive_quota_tier": self._int(quota_policy.get("tier")),
            "proactive_quota_tier_label": self._single_line(quota_policy.get("label"), 40),
            "unanswered_slowdown_count": unanswered_slowdown_count,
            "unanswered_interval_multiplier": unanswered_interval_multiplier,
            "unanswered_slowdown_text": unanswered_slowdown_text,
            "effective_idle_minutes": (
                self.plugin._effective_user_idle_minutes(user)
                if hasattr(self.plugin, "_effective_user_idle_minutes")
                else getattr(self.plugin, "idle_minutes", 0)
            ),
            "effective_min_interval_minutes": (
                self.plugin._effective_user_min_interval_minutes(user)
                if hasattr(self.plugin, "_effective_user_min_interval_minutes")
                else getattr(self.plugin, "min_interval_minutes", 0)
            ),
            "effective_screen_peek_daily_limit": (
                self.plugin._effective_user_screen_peek_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_screen_peek_daily_limit")
                else getattr(self.plugin, "screen_peek_max_daily", 0)
            ),
            "effective_photo_daily_limit": (
                self.plugin._effective_user_photo_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_photo_daily_limit")
                else getattr(self.plugin, "photo_action_max_daily", 0)
            ),
            "proactive_daily_limit": user.get("proactive_daily_limit", -1),
            "proactive_idle_minutes": user.get("proactive_idle_minutes", -1),
            "proactive_min_interval_minutes": user.get("proactive_min_interval_minutes", -1),
            "photo_daily_limit": user.get("photo_daily_limit", -1),
            "screen_peek_daily_limit": user.get("screen_peek_daily_limit", -1),
            "poke_daily_limit": user.get("poke_daily_limit", -1),
            "proactive_boundary_note": user.get("proactive_boundary_note", ""),
            "inbound_count": user.get("inbound_count", 0),
            "reply_count": user.get("reply_count", 0),
            "proactive_sent_count": user.get("proactive_sent_count", 0),
            "relationship_score": user.get("relationship_score", 0),
            "relationship_stage": relationship_stage,
            "relationship_mode": relationship_mode,
            "relationship_mode_label": "专属关系" if relationship_mode == "owner_exclusive" else "普通阶段",
            "owner_exclusive_relationship_prompt": owner_exclusive_relationship_prompt,
            "relationship_intimacy": relationship_intimacy,
            "relationship_ledger": relationship_panel["relationship_changes"],
            "current_interaction": current_interaction,
            "expression_decision": relationship_panel["expression_decision"],
            "pending_emotion_judgement": pending_emotion_judgement,
            "last_emotion_judgement": last_emotion_judgement,
            "last_emotion_judgement_error": last_emotion_judgement_error,
            "planned_reason": user.get("planned_proactive_reason", ""),
            "planned_action": user.get("planned_proactive_action", ""),
            "next_proactive_ts": user.get("next_proactive_at", 0),
            "next_proactive": self.plugin._format_next_proactive(user),
            "memory_items": self._memory_item_count(user.get("companion_memory")),
            "dialogue_episode_count": len(user.get("dialogue_episodes") or []),
            "open_loop_count": len(user.get("open_loops") or []),
            "habit_count": len(self._behavior_habit_summary(user).get("items", [])),
            "alias_user_ids": [
                self._single_line(item, 80)
                for item in (user.get("alias_user_ids") if isinstance(user.get("alias_user_ids"), list) else [])
                if self._single_line(item, 80)
            ],
        }

    def _emotion_relationship_state_summary(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        scalar_limits = {
            "mode": 24,
            "stage": 24,
            "last_intent": 40,
            "last_emotion": 40,
            "last_emotion_event": 40,
            "last_emotion_reason": 120,
            "last_emotion_target": 40,
            "last_emotion_rule": 60,
            "last_hurt_reason": 120,
            "last_hurt_text": 180,
            "updated_at": 40,
        }
        numeric_keys = {
            "mood_score",
            "mood_updated_ts",
            "hurt_until",
            "emotion_min_until",
            "silence_turns",
            "last_pressure",
            "last_emotion_intensity",
            "last_intent_confidence",
            "last_emotion_confidence",
        }
        summary: dict[str, Any] = {}
        for key, limit in scalar_limits.items():
            if key in state:
                summary[key] = self._single_line(state.get(key), limit)
        for key in numeric_keys:
            if key in state:
                summary[key] = state.get(key)
        dims = state.get("emotion_dimensions")
        if isinstance(dims, dict):
            summary["emotion_dimensions"] = {
                key: dims.get(key)
                for key in ("pleasantness", "tension", "arousal", "certainty")
                if key in dims
            }
        plutchik_emotions = state.get("plutchik_emotions")
        if isinstance(plutchik_emotions, dict):
            summary["plutchik_emotions"] = {
                key: plutchik_emotions.get(key)
                for key in ("joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation")
                if key in plutchik_emotions
            }
        plutchik = state.get("plutchik_profile")
        if isinstance(plutchik, dict):
            profile = {
                key: plutchik.get(key)
                for key in (
                    "dominant",
                    "dominant_label",
                    "dominant_value",
                    "blend_key",
                    "blend_label",
                    "blend_value",
                    "updated_at",
                )
                if key in plutchik
            }
            active = plutchik.get("active")
            if isinstance(active, list):
                profile["active"] = [dict(item) for item in active[:8] if isinstance(item, dict)]
            summary["plutchik_profile"] = profile
        regulation = state.get("emotion_regulation")
        if isinstance(regulation, dict):
            reg = {
                key: regulation.get(key)
                for key in ("strategy", "strategy_label", "intensity", "reason", "updated_at")
                if key in regulation
            }
            stack = regulation.get("strategy_stack")
            if isinstance(stack, list):
                reg["strategy_stack"] = [dict(item) for item in stack[:5] if isinstance(item, dict)]
            summary["emotion_regulation"] = reg
        return summary

    def _emotion_pending_judgement_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        text = self._single_line(value.get("text"), 180)
        created_at = self._float(value.get("created_at"))
        local = value.get("local") if isinstance(value.get("local"), dict) else {}
        result: dict[str, Any] = {
            "text": text,
            "created_at": created_at,
            "created_at_text": self.plugin._format_timestamp_elapsed(created_at) if created_at else "",
        }
        if local:
            result["local"] = {
                "emotion_event": self._single_line(local.get("emotion_event"), 40),
                "emotion_target": self._single_line(local.get("emotion_target"), 40),
                "emotion_intensity": local.get("emotion_intensity"),
                "emotion_confidence": local.get("emotion_confidence"),
                "emotion_reason": self._single_line(local.get("emotion_reason"), 100),
            }
        return result if text or created_at or local else {}

    def _emotion_last_judgement_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        status = self._single_line(value.get("status"), 24)
        if status not in {"applied", "kept_local", "failed"}:
            return {}
        return {
            "status": status,
            "outcome": self._single_line(value.get("outcome"), 32),
            "event": self._single_line(value.get("event"), 32),
            "target": self._single_line(value.get("target"), 24),
            "intensity": self._int(value.get("intensity")),
            "confidence": round(max(0.0, min(1.0, self._float(value.get("confidence")))), 2),
            "reason": self._single_line(value.get("reason"), 100),
            "reviewed_at": self._single_line(value.get("reviewed_at"), 32),
        }

    def _emotion_judgement_error_summary(self, value: Any) -> str:
        text = self._single_line(value, 160)
        if not text:
            return ""
        if text.startswith("{") or '"event"' in text or '"confidence"' in text:
            return ""
        labels = {
            "request_failed": "模型请求失败",
            "empty_response": "模型返回为空",
            "invalid_response": "模型返回格式无效",
            "empty_or_invalid": "模型返回为空或格式无效",
        }
        return labels.get(text, "模型请求或返回格式异常")

    def _behavior_habit_summary(self, user: dict[str, Any]) -> dict[str, Any]:
        formatter = getattr(self.plugin, "_qualified_user_behavior_habits", None)
        if callable(formatter):
            try:
                items = formatter(user)
            except Exception:
                items = []
        else:
            raw = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
            patterns = raw.get("patterns") if isinstance(raw.get("patterns"), list) else []
            items = [item for item in patterns if isinstance(item, dict)]
        normalized = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "bucket": self._single_line(item.get("bucket"), 12),
                    "category": self._single_line(item.get("category"), 20),
                    "topic": self._single_line(item.get("topic"), 80),
                    "count": self._int(item.get("count")),
                    "avg_time": self.plugin._format_user_habit_time(item.get("avg_minute")) if hasattr(self.plugin, "_format_user_habit_time") else "",
                    "last_seen": self.plugin._format_timestamp_elapsed(item.get("last_seen_ts", 0)),
                    "last_seen_text": self._single_line(item.get("last_seen_text"), 100),
                }
            )
        raw_habits = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_user_habit_learning", False)),
            "updated_at": self._single_line(raw_habits.get("updated_at"), 30) if isinstance(raw_habits, dict) else "",
            "items": normalized,
        }

    def _skill_growth_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("skill_growth") if isinstance(data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        items: list[dict[str, Any]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            level = self._int(raw.get("level")) or 1
            exp = self._float(raw.get("exp"))
            next_exp = self.plugin._skill_next_exp(level) if hasattr(self.plugin, "_skill_next_exp") else None
            prev_exp = {1: 0, 2: 100, 3: 260, 4: 520, 5: 900, 6: 1400}.get(level, 0)
            if next_exp:
                progress = max(0, min(100, int(((exp - prev_exp) / max(1, next_exp - prev_exp)) * 100)))
            else:
                progress = 100
            logs = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
            keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 32),
                    "name": self._single_line(raw.get("name"), 32),
                    "category": self._single_line(raw.get("category"), 24),
                    "keywords": [self._single_line(item, 24) for item in keywords if self._single_line(item, 24)][:16],
                    "aliases": [self._single_line(item, 24) for item in aliases if self._single_line(item, 24)][:12],
                    "hidden": bool(raw.get("hidden")),
                    "frozen": bool(raw.get("frozen")),
                    "level": level,
                    "level_title": self.plugin._skill_level_title(level) if hasattr(self.plugin, "_skill_level_title") else self._single_line(raw.get("level_title"), 24),
                    "description": self.plugin._skill_level_description(level) if hasattr(self.plugin, "_skill_level_description") else "",
                    "exp": round(exp, 2),
                    "next_exp": next_exp,
                    "progress": progress,
                    "training_count": self._int(raw.get("training_count")),
                    "last_trained": self.plugin._format_timestamp_elapsed(raw.get("last_trained_ts", 0)),
                    "recent_logs": [
                        {
                            "activity": self._single_line(log.get("activity"), 80),
                            "exp": self._float(log.get("exp")),
                            "time": self.plugin._format_timestamp_elapsed(log.get("ts", 0)),
                            "level_up": bool(log.get("level_up")),
                        }
                        for log in logs[-4:]
                        if isinstance(log, dict)
                    ],
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), -item["level"], -item["exp"], -item["training_count"], item.get("category") or "", item.get("name") or ""))
        return {
            "enabled": bool(getattr(self.plugin, "enable_skill_growth_simulation", False)),
            "rate": float(getattr(self.plugin, "skill_growth_rate", 1.0) or 1.0),
            "passive_injection": bool(getattr(self.plugin, "enable_skill_growth_passive_injection", False)),
            "schedule_influence": bool(getattr(self.plugin, "enable_skill_growth_schedule_influence", False)),
            "schedule_influence_strength": float(getattr(self.plugin, "skill_growth_schedule_influence_strength", 0.35) or 0.0),
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
            "skill_count": len(items),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "frozen_count": sum(1 for item in items if item.get("frozen")),
            "items": items[:120],
        }
