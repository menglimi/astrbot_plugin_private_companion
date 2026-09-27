# -*- coding: utf-8 -*-
"""ProactivePart03Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（27 个方法 + 0 个模块级名字 + 0 个类级赋值 / 558 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import _proactive_setting_value
from .proactive_core_shared import Any
from .proactive_core_shared import PromptRenderMode
from .proactive_core_shared import PromptSection
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import _unanswered_proactive_count
from .proactive_core_shared import current_interaction_projection
from .proactive_core_shared import deepcopy
from .proactive_core_shared import prompt_section
from .proactive_core_shared import relationship_stage_for_score
from .proactive_core_shared import render_prompt_sections



class ProactivePart03Mixin:
    """ProactivePart03Mixin（从 ProactiveMixin 拆出）。"""


    def _format_private_delivery_binding_status(self, user_id: str, user: dict[str, Any] | None) -> str:
        route = self._private_delivery_route_status(user_id, user)
        selected = _single_line(route.get("umo"), 240) or "尚未形成可投递会话"
        bound = _single_line(route.get("bound_umo"), 240)
        binding_label = "自动选择"
        if bound:
            binding_label = "已绑定当前私聊" if route.get("source") == "bound" else "已绑定，但当前会话不可用"
        lines = [
            f"绑定状态：{binding_label}",
            f"当前会话：{selected}",
            f"路线来源：{_single_line(route.get('source_label'), 80) or '平台兜底'}",
        ]
        recent_error = _single_line(route.get("recent_error"), 200)
        if recent_error:
            recovered = "（已恢复）" if route.get("recent_error_recovered") else ""
            lines.append(f"最近错误{recovered}：{recent_error}")
        return "\n".join(lines)

    def _private_umo_matches_user_id(self, umo: str, user_id: str) -> bool:
        clean_umo = _single_line(umo, 180)
        clean_user_id = str(user_id or "").strip()
        if not clean_umo or not clean_user_id:
            return False
        if f":FriendMessage:{clean_user_id}" not in clean_umo:
            return False
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                return parser(clean_umo) is not None
            except Exception:
                return False
        return True

    def _note_private_user_umo(self, user_id: str, user: dict[str, Any] | None, umo: str) -> None:
        if not isinstance(user, dict):
            return
        clean_umo = _single_line(umo, 180)
        if not clean_umo:
            return
        user_id = self._canonical_private_user_id(str(user_id or user.get("user_id") or "").strip())
        user["last_inbound_umo"] = clean_umo
        self._remember_private_delivery_route(user, clean_umo, outcome="observed")
        delivery_id = self._private_delivery_user_id_for(user_id)
        inbound_session_id = self._private_umo_session_id(clean_umo)
        if delivery_id and delivery_id != user_id:
            if inbound_session_id == delivery_id:
                user["umo"] = clean_umo
                return
            delivery_umo = self._private_delivery_umo_for_user_id(user_id)
            if delivery_umo:
                user["umo"] = delivery_umo
            return
        if self._private_umo_matches_user_id(clean_umo, user_id):
            user["umo"] = clean_umo

    def _note_private_delivery_success(self, user_id: str, user: dict[str, Any] | None, umo: str) -> None:
        if not isinstance(user, dict):
            return
        self._remember_private_delivery_route(user, umo, outcome="success")
        delivery_id = self._private_delivery_user_id_for(user_id)
        if delivery_id and self._private_umo_matches_user_id(umo, delivery_id):
            user["umo"] = _single_line(umo, 240)

    def _note_private_delivery_failure(
        self,
        user_id: str,
        user: dict[str, Any] | None,
        umo: str,
        error: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        self._remember_private_delivery_route(user, umo, outcome="failure", error=error)
        preferred = self._private_delivery_umo_for_user_id(user_id)
        if preferred and preferred != _single_line(umo, 240) and self._private_delivery_umo_is_verified(user_id, user, preferred):
            user["umo"] = preferred

    def _ensure_private_user_umo(self, user_id: str, user: dict[str, Any] | None) -> bool:
        if not isinstance(user, dict):
            return False
        user_id = str(user_id or user.get("user_id") or "").strip()
        fallback = self._private_delivery_umo_for_user_id(user_id)
        if not fallback:
            return False
        current = _single_line(user.get("umo"), 180)
        delivery_id = self._private_delivery_user_id_for(user_id)
        canonical_id = self._canonical_private_user_id(user_id)
        if (
            current
            and fallback != current
            and self._private_delivery_umo_is_verified(canonical_id, user, fallback)
        ):
            user["umo"] = fallback
            return True
        if delivery_id and delivery_id != canonical_id:
            expected_suffix = f":FriendMessage:{delivery_id}"
            if not current.endswith(expected_suffix):
                user["umo"] = fallback
                return True
        else:
            last_inbound_umo = _single_line(user.get("last_inbound_umo"), 180)
            if (
                last_inbound_umo
                and last_inbound_umo != current
                and self._private_umo_matches_user_id(last_inbound_umo, canonical_id)
            ):
                user["umo"] = last_inbound_umo
                return True
        if not current:
            user["umo"] = fallback
            return True
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                if parser(current) is None:
                    user["umo"] = fallback
                    return True
            except Exception:
                user["umo"] = fallback
                return True
        return False

    def _private_user_role(self, user: dict[str, Any] | None, user_id: str = "") -> str:
        if not isinstance(user, dict):
            return "friend"
        role_getter = getattr(self, "_ensure_private_user_role", None)
        if callable(role_getter):
            try:
                return role_getter(str(user_id or user.get("user_id") or ""), user)
            except Exception:
                pass
        normalizer = getattr(self, "_normalize_private_user_role", None)
        role = normalizer(user.get("relationship_role")) if callable(normalizer) else str(user.get("relationship_role") or "")
        return role if role in {"owner", "friend"} else "friend"

    def _user_profile_override_int(self, user: dict[str, Any], key: str) -> int | None:
        if not isinstance(user, dict):
            return None
        raw = user.get(key)
        if raw in (None, ""):
            return None
        value = _safe_int(raw, -1, -1)
        return value if value >= 0 else None

    def _effective_user_daily_limit(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_daily_limit")
        max_daily_messages = self._runtime_max_daily_messages()
        if max_daily_messages <= 0 or override == 0:
            return 0
        user_limit = (
            min(self._PROACTIVE_DAILY_QUOTA_MAX, max_daily_messages)
            if override is None
            else min(self._PROACTIVE_USER_DAILY_QUOTA_MAX, max(0, override))
        )
        if not bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False)):
            return max(0, user_limit)
        view_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        relationship_user = (
            view_getter(user, source="proactive_daily_limit") if callable(view_getter) else user
        )
        role = self._private_user_role(relationship_user)
        mode = str(relationship_user.get("relationship_mode") or "normal")
        violation = user.get("relationship_violation")
        recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
        if isinstance(violation, dict) and callable(recovery_settler):
            recovery_settler(user, now=_now_ts())
            violation = user.get("relationship_violation")
        if str(role).strip().lower() != "owner" and isinstance(violation, dict) and _safe_int(violation.get("unrecovered_points"), 0, 0, 12) > 0:
            return 0
        relationship_is_distant = False
        if not (role == "owner" and mode == "owner_exclusive"):
            policy = (
                _proactive_setting_value(self, "relationship_stage_policy", None)
                if bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False))
                else None
            )
            stage = relationship_stage_for_score(relationship_user.get("relationship_score", 0), policy).get("phase", {})
            relationship_is_distant = _safe_int(relationship_user.get("relationship_score"), 0, -1200, 1200) < 0 or str(
                stage.get("key") or ""
            ) in {"deeply_distant", "strongly_distant", "distant"}
        if relationship_is_distant:
            return 0
        interaction = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=mode,
            relationship_score=relationship_user.get("relationship_score"),
            normal_interaction_band_cap=_proactive_setting_value(self, "normal_interaction_band_cap", "warm"),
            now=_now_ts(),
        )
        if str(interaction.get("expression_band") or "relaxed") in {"avoidant", "hurt"}:
            dynamic_limit = 0
        else:
            # 未回应只逐步放大主动间隔；不要把软降频误当成每日硬额度。
            dynamic_limit = user_limit
        return max(0, min(user_limit, dynamic_limit))

    def _relationship_proactive_soft_target(self, user: dict[str, Any]) -> int:
        if not bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False)):
            return max(1, _safe_int(_proactive_setting_value(self, "max_daily_messages", 1), 1, 0, 30))
        view_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        relationship_user = (
            view_getter(user, source="proactive_soft_target") if callable(view_getter) else user
        )
        role = self._private_user_role(relationship_user)
        mode = str(relationship_user.get("relationship_mode") or "normal")
        if role == "owner" and mode == "owner_exclusive":
            return max(1, _safe_int(_proactive_setting_value(self, "owner_exclusive_proactive_limit", 6), 6, 0, 30))
        violation = user.get("relationship_violation")
        recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
        if isinstance(violation, dict) and callable(recovery_settler):
            recovery_settler(user, now=_now_ts())
            violation = user.get("relationship_violation")
        if isinstance(violation, dict) and _safe_int(violation.get("unrecovered_points"), 0, 0, 12) > 0:
            return 0
        policy = (
            _proactive_setting_value(self, "relationship_stage_policy", None)
            if bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False))
            else None
        )
        stage = relationship_stage_for_score(relationship_user.get("relationship_score", 0), policy).get("phase", {})
        if _safe_int(relationship_user.get("relationship_score"), 0, -1200, 1200) < 0 or str(stage.get("key") or "") in {
            "deeply_distant",
            "strongly_distant",
            "distant",
        }:
            return 0
        return max(1, _safe_int(stage.get("proactive_care_limit"), 1, 0, 30))

    def _runtime_max_daily_messages(self) -> int:
        runtime_value = _safe_int(
            _proactive_setting_value(self, "max_daily_messages", 8),
            8,
            0,
            self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        # Legacy lightweight integrations may update only ``config``.  Keep
        # that live read when no persona resolver exists, without writing back
        # to the shared runtime attribute.
        if not callable(getattr(self, "persona_setting", None)):
            config = getattr(self, "config", None)
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    runtime_value = _safe_int(
                        getter("max_daily_messages", runtime_value),
                        runtime_value,
                        0,
                        self._PROACTIVE_DAILY_QUOTA_MAX,
                    )
                except Exception:
                    pass
        if runtime_value <= 0:
            return 0
        effective_value = self._effective_proactive_int(
            "max_daily_messages",
            runtime_value,
            minimum=0,
            maximum=self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        if effective_value <= 0:
            return 0
        return min(self._PROACTIVE_DAILY_QUOTA_MAX, effective_value)

    def _proactive_generation_disabled(self, user: dict[str, Any] | None = None) -> bool:
        if self._runtime_max_daily_messages() <= 0:
            return True
        if isinstance(user, dict) and self._user_profile_override_int(user, "proactive_daily_limit") == 0:
            return True
        return False

    def _suspend_user_proactive_generation(self, user: dict[str, Any]) -> bool:
        if not isinstance(user, dict):
            return False
        tracked = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_candidate_id",
            "proactive_impulses",
            "pending_followup_event",
            "suspended_proactive",
            "pending_proactive_send_retry",
            "proactive_sending",
        )
        before = {key: deepcopy(user.get(key)) for key in tracked}
        self._clear_pending_proactive_plan(user)
        user["proactive_impulses"] = []
        user["pending_followup_event"] = {}
        user["suspended_proactive"] = {}
        user["pending_proactive_send_retry"] = {}
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0
        return any(before.get(key) != user.get(key) for key in tracked)

    def _format_daily_limit_disabled_reason(self, user: dict[str, Any]) -> str:
        override = user.get("proactive_daily_limit", -1) if isinstance(user, dict) else -1
        runtime_value = _safe_int(
            _proactive_setting_value(self, "max_daily_messages", 0),
            0,
            0,
            self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        config_value = runtime_value
        if not callable(getattr(self, "persona_setting", None)):
            config = getattr(self, "config", None)
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    config_value = _safe_int(getter("max_daily_messages", runtime_value), runtime_value, 0, self._PROACTIVE_DAILY_QUOTA_MAX)
                except Exception:
                    config_value = runtime_value
        return f"每日上限为 0（用户覆盖={override}，运行中全局={runtime_value}，配置全局={config_value}）"

    def _effective_user_idle_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_idle_minutes")
        if override is not None:
            return override
        base_idle = self._effective_proactive_int(
            "idle_minutes",
            _safe_int(_proactive_setting_value(self, "idle_minutes", 60), 60, 0, 1440),
            minimum=0,
            maximum=1440,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_idle_floor_minutes",
                0,
                minimum=0,
                maximum=1440,
            )
            base_idle = max(base_idle, friend_floor)
        tier_cap = _safe_int(self._proactive_quota_policy(user).get("idle_cap_minutes"), base_idle, 0, 1440)
        return max(0, min(base_idle, tier_cap)) if tier_cap > 0 else max(0, base_idle)

    def _effective_user_greeting_idle_minutes(self, user: dict[str, Any]) -> int:
        greeting_idle = _safe_int(
            _proactive_setting_value(self, "greeting_idle_minutes", 30),
            30,
            0,
            240,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_idle_floor_minutes",
                0,
                minimum=0,
                maximum=1440,
            )
            return max(greeting_idle, min(60, friend_floor))
        return max(0, greeting_idle)

    def _effective_user_min_interval_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_min_interval_minutes")
        if override is not None:
            return override
        base_interval = self._effective_proactive_int(
            "min_interval_minutes",
            _safe_int(_proactive_setting_value(self, "min_interval_minutes", 120), 120, 0, 2880),
            minimum=0,
            maximum=2880,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_min_interval_floor_minutes",
                0,
                minimum=0,
                maximum=2880,
            )
            base_interval = max(base_interval, friend_floor)
        tier_cap = _safe_int(
            self._proactive_quota_policy(user).get("interval_cap_minutes"),
            base_interval,
            0,
            2880,
        )
        return max(0, min(base_interval, tier_cap)) if tier_cap > 0 else max(0, base_interval)

    def _effective_user_photo_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "photo_daily_limit")
            if override is not None:
                return override
        return max(0, _safe_int(_proactive_setting_value(self, "photo_action_max_daily", 0), 0, 0))

    def _effective_user_screen_peek_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "screen_peek_daily_limit")
            if override is not None:
                return override
        return max(0, _safe_int(_proactive_setting_value(self, "screen_peek_max_daily", 0), 0, 0))

    def _effective_user_poke_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            override = self._user_profile_override_int(user, "poke_daily_limit")
            if override is not None:
                return override
            if self._private_user_role(user) == "friend":
                return 0
        return max(0, _safe_int(_proactive_setting_value(self, "poke_action_max_times", 0), 0, 0))

    def _format_private_user_boundary_prompt_section(
        self,
        user: dict[str, Any],
    ) -> PromptSection:
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        note = _single_line(user.get("proactive_boundary_note"), 180)
        if role == "owner":
            body = (
                f"- 当前用户角色：{label}。\n"
                "- 可以延续人格中对主要用户的亲近、依赖和日常陪伴动机，但仍要尊重用户休息、忙碌和拒绝信号。"
            )
        else:
            body = (
                f"- 当前用户角色：{label}。\n"
                "- 对方不是主要用户/恋人/专属陪伴目标。主动联系应像普通朋友：少量、具体、不过度亲密，不使用主要用户专属称呼、占有欲、撒娇索取或暧昧承诺。\n"
                "- 动机应以礼貌关心、共同话题、必要转告、轻分享为主；不要因为想贴近、想被哄、想确认对方在不在而频繁打扰。\n"
                "- 不给次要用户使用窥屏或主动生图能力；不要把主要用户或其他私聊对象的图片、生活碎片复用给次要用户。"
                "- 不对次要用户发起资料/资料归档推荐、资料归档分享、屏幕观察、群聊私下转述、私下创作分享或其他涉及隐私来源的主动。"
            )
        if note:
            body += f"\n- 用户级边界备注：{note}"
        return prompt_section(
            key="proactive.private_user_role",
            title="当前私聊关系角色",
            source="proactive",
            content=body,
        )

    def _format_private_user_boundary_hint(self, user: dict[str, Any]) -> str:
        return render_prompt_sections(
            [self._format_private_user_boundary_prompt_section(user)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _friend_sensitive_proactive_reason(self, reason: Any) -> bool:
        normalized = str(reason or "").strip()
        return normalized in {
            "group_share",
            "creative_share",
            "weather_alert",
        }

    def _friend_sensitive_proactive_action(self, action: Any) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"screen_peek", "photo_text"})

    def _friend_can_receive_proactive_reason(self, user: dict[str, Any] | None, reason: Any, action: Any = "") -> bool:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return True
        return not (self._friend_sensitive_proactive_reason(reason) or self._friend_sensitive_proactive_action(action))

    def _sanitize_friend_proactive_plan_fields(
        self,
        user: dict[str, Any] | None,
        *,
        reason: str = "",
        action: str = "message",
        topic: str = "",
        motive: str = "",
    ) -> dict[str, str]:
        normalized_action = str(action or "message").strip() or "message"
        normalized_topic = _single_line(topic, 80)
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return {
                "reason": str(reason or "check_in"),
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        normalized_reason = str(reason or "check_in")
        unanswered_level = self._friend_unanswered_downgrade_level(user)
        if unanswered_level >= 1 and self._friend_unanswered_should_remove_action(normalized_action):
            normalized_action = "message"
        if self._friend_sensitive_proactive_action(normalized_action):
            normalized_action = "message"
        sensitive_markers = (
            "screen_peek", "窥屏", "屏幕", "识屏", "偷看", "偷偷看", "瞄一眼", "看一眼",
            "观察你", "看你在忙", "看看你在干嘛", "看你在干嘛",
        )
        combined = f"{normalized_topic} {normalized_motive}"
        has_sensitive_action_text = any(token in combined for token in sensitive_markers)
        has_friend_interaction_text = self._friend_plan_has_private_interaction_text(combined)
        unanswered_patch = self._friend_unanswered_plan_patch(
            user,
            level=unanswered_level,
            reason=normalized_reason,
            action=normalized_action,
            topic=normalized_topic,
            motive=normalized_motive,
        )
        if unanswered_patch:
            normalized_reason = unanswered_patch["reason"]
            normalized_action = unanswered_patch["action"]
            normalized_topic = unanswered_patch["topic"]
            normalized_motive = unanswered_patch["motive"]
            combined = f"{normalized_topic} {normalized_motive}"
            has_sensitive_action_text = any(token in combined for token in sensitive_markers)
            has_friend_interaction_text = self._friend_plan_has_private_interaction_text(combined)
        if not has_sensitive_action_text and not has_friend_interaction_text:
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        if has_friend_interaction_text:
            if not normalized_topic or self._friend_plan_has_private_interaction_text(normalized_topic):
                normalized_topic = "顺手分享一点日常近况"
            normalized_motive = (
                "作为普通朋友轻轻分享一个不指向第三方私聊互动的小片段,不要求立刻回复"
                if str(reason or "") in {"", "check_in", "quiet_care", "state_share", "activity_share"}
                else "按次要用户关系做一次克制的普通文字分享,不写成和次要用户聊天或约见"
            )
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        topic_replacements = {
            "空档偷看一眼": "空档问一句",
            "偷看一眼": "问一句近况",
            "你这会儿在干嘛": "问一句近况",
        }
        for old, new in topic_replacements.items():
            normalized_topic = normalized_topic.replace(old, new)
        if not normalized_topic or any(token in normalized_topic for token in sensitive_markers):
            normalized_topic = "问一句近况"
        normalized_motive = (
            "作为次要用户关系想起对方可能正忙,只轻轻问一句,不要求立刻回复"
            if normalized_reason in {"", "check_in", "quiet_care", "state_share"}
            else "按次要用户关系顺手补一句,只做普通文字关心,不涉及屏幕观察"
        )
        return {
            "reason": normalized_reason,
            "action": normalized_action,
            "topic": normalized_topic,
            "motive": normalized_motive,
        }

    def _friend_unanswered_downgrade_level(self, user: dict[str, Any] | None, *, now: float | None = None) -> int:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return 0
        level = 0
        ignored = _unanswered_proactive_count(user)
        if ignored >= 3:
            level = 3
        elif ignored >= 2:
            level = 2
        elif ignored >= 1:
            level = 1
        check_now = _now_ts() if now is None else now
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        if awaiting_since > 0:
            hours = max(0.0, (check_now - awaiting_since) / 3600.0)
            if hours >= 24:
                level = max(level, 3)
            elif hours >= 10:
                level = max(level, 2)
            elif hours >= 4:
                level = max(level, 1)
        return level
