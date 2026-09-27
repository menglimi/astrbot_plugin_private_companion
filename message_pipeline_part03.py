# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from .helpers import (
    _group_link_message_context,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
)
from .message_pipeline_part01 import (
    _coalesce_event_data_saves,
    _persona_feature_enabled,
    _persona_value,
)
from .message_pipeline_shared import logger


@_coalesce_event_data_saves
async def handle_group_message(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
    """观察群聊消息，维护群上下文并判断是否自然唤醒 Bot。"""
    if self is None:
        return
    if self._is_onebot_poke_notice_event(event):
        # 同样避免群聊观察链将戳一戳误作空消息或普通上下文。
        logger.debug("群聊戳一戳 notice 已放行给专用插件")
        return
    self._qzone_note_event_bot(event)
    if not _persona_feature_enabled(self, "enable_group_companion"):
        return
    group_id = self._extract_group_id_from_event(event)
    if not group_id or not self._group_enabled_for_event(group_id):
        return
    try:
        sender_id = str(event.get_sender_id())
    except Exception:
        sender_id = ""
    self_id = self._event_self_id(event)
    if sender_id and self_id and sender_id == self_id:
        logger.info(
            "已终止 Bot 自己的群聊回流事件: group=%s self=%s text=%s",
            group_id,
            self_id,
            _single_line(getattr(event, "message_str", ""), 80),
        )
        event.stop_event()
        return
    received_ts = _now_ts()
    text = self._group_observation_event_text(event)
    if not text:
        return
    if text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
        return
    if self._message_debounce_command_text(event, text):
        return
    sender_name = self._sender_display_name(event)
    reaction_expression_feedback = {}
    reaction_feedback_lock = getattr(self, "_data_lock", None)
    if sender_id and reaction_feedback_lock is not None:
        # The image tool stores its group target in the group reaction
        # state and scopes it by the exact group UMO. Apply feedback before any
        # early-return branch so existing reply handlers cannot swallow it.
        async with reaction_feedback_lock:
            group_user = self._reaction_expression_feedback_user(
                sender_id,
                text,
                create_for_opt_out=True,
                event=event,
            )
            if isinstance(group_user, dict):
                reaction_expression_feedback = self._apply_reaction_expression_feedback(
                    group_user,
                    text,
                    scope_key=self._reaction_expression_scope_key(event, sender_id),
                )
                if reaction_expression_feedback:
                    self._persist_reaction_expression_state(
                        sections={"reaction_expression_group_states"}
                    )
    if reaction_expression_feedback:
        self._log_reaction_expression_event(
            event,
            stage="feedback",
            decision="recorded",
            reason="feedback_group",
            scope="group",
            image_id=reaction_expression_feedback.get("image_id"),
            feedback_signal=reaction_expression_feedback.get("signal"),
            feedback_score=reaction_expression_feedback.get("score"),
        )
    await self._capture_group_observation_event(
        event,
        group_id=group_id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
    )
    self._start_group_image_understanding(
        event,
        group_id=group_id,
        sender_id=sender_id,
        text=text,
    )
    existing_reply_preview = self._event_existing_reply_result_preview(event)
    if self._proactive_only_blocks_passive_event(event, "group_event_pipeline"):
        logger.debug("主动消息专用模式已保留群聊观察,跳过回复增强")
        return
    if existing_reply_preview:
        async with self._data_lock:
            if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
                self._save_data_sync(sections={"inbound_debounce_stats"})
                return
            group = self._get_group(group_id)
            group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            self._capture_group_observation_once(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
                scene=scene,
                message_id=self._event_message_id(event),
                event=event,
            )
            self._save_data_sync(sections={"groups"})
            group_snapshot = deepcopy(group)
        logger.info(
            "已有其他链路回复,仅记录群聊观察: group=%s sender=%s text=%s result=%s",
            group_id,
            sender_id,
            _single_line(text, 80),
            _single_line(existing_reply_preview, 120),
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_episode(group_id, group_snapshot),
            label="refresh_group_episode_existing_reply",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_slang_meanings(group_id, group_snapshot),
            label="refresh_group_slang_existing_reply",
        )
        return
    if self._group_llm_reply_blocked(group_id):
        logger.debug(
            "本群 LLM 回复已关闭,已保留观察并跳过回复增强: group=%s text=%s",
            group_id,
            _single_line(text, 80),
        )
        return
    try:
        signals = self._event_scene_signals(event)
    except Exception:
        signals = {}
    at_bot = any(
        isinstance(item, dict) and bool(item.get("is_bot"))
        for item in (signals.get("at_targets") if isinstance(signals, dict) else []) or []
    )
    reply_to_bot = bool(
        isinstance(signals, dict)
        and _single_line(signals.get("self_id"), 80)
        and _single_line(signals.get("reply_to_id"), 80) == _single_line(signals.get("self_id"), 80)
    )
    quoted_link_payload = False
    if not at_bot and not reply_to_bot:
        try:
            quoted_link_payload = await self._event_reply_contains_link_payload(event)
        except Exception as exc:
            logger.debug("群聊引用链接守卫读取失败: %s", _single_line(exc, 120))
        if quoted_link_payload:
            setattr(event, "private_companion_group_quoted_link_payload", True)
    current_link_payload = _group_link_message_context(text, limit=260)[1]
    if (current_link_payload or quoted_link_payload) and not at_bot and not reply_to_bot:
        # Some adapters mark any reply/share card as a wake event before
        # plugins inspect its real target.  Clear that provisional state;
        # an actual Bot-name/custom-word/continuation match below can set
        # it again deliberately.
        try:
            setattr(event, "is_at_or_wake_command", False)
            setattr(event, "is_wake", False)
        except Exception:
            pass
    if (at_bot or reply_to_bot) and await self._maybe_handle_natural_language_photo_request(event, sender_id, text, directed=True):
        return
    image_wakeup: dict[str, Any] = {}
    image_wakeup_getter = getattr(self, "_maybe_group_image_wakeup", None)
    if callable(image_wakeup_getter):
        image_wakeup = await image_wakeup_getter(event, sender_id=sender_id)
    registration_payload = None
    continuation: bool | None = False
    resting_mention_notice = ""
    scene: dict[str, Any] = {}
    wakeup_state_effect: dict[str, Any] = {}
    group_for_judge: dict[str, Any] = {}
    active_for_judge: dict[str, Any] = {}
    high_intensity_state: dict[str, Any] = {}
    group_snapshot_high_intensity: dict[str, Any] = {}
    async with self._data_lock:
        if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
            self._save_data_sync(sections={"inbound_debounce_stats"})
            event.stop_event()
            return
        group = self._get_group(group_id)
        if sender_id:
            users = self.data.get("users", {})
            resolver = getattr(self, "_private_user_id_for_event", None)
            scoped_sender_id = (
                resolver(event, sender_id)
                if callable(resolver)
                else self._canonical_private_user_id(sender_id)
            )
            current_sender = users.get(scoped_sender_id) if isinstance(users, dict) else None
            boundary_profile_known = bool(
                isinstance(current_sender, dict)
                and any(
                    key in current_sender
                    for key in ("relationship_role", "manual_enabled", "enabled", "umo", "relationship_score")
                )
            )
            if (at_bot or reply_to_bot) and boundary_profile_known and bool(
                _persona_value(self, 'enable_relationship_boundary_feedback', True)
            ):
                current_sender.setdefault("user_id", scoped_sender_id)
                group_boundary_intent = self._analyze_inbound_intent(text)
                group_boundary_intent["boundary_scope"] = "group"
                group_boundary_intent["boundary_group_id"] = group_id
                boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
                if callable(boundary_enricher):
                    group_boundary_intent = boundary_enricher(current_sender, group_boundary_intent)
                violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
                if callable(violation_settler):
                    violation_settler(
                        current_sender,
                        group_boundary_intent,
                        event_id=self._event_message_id(event),
                        now=received_ts,
                    )
                if self._should_use_llm_emotion_judgement(text, group_boundary_intent):
                    review_id = uuid.uuid4().hex
                    current_sender["pending_emotion_judgement"] = {
                        "review_id": review_id,
                        "message_event_id": self._event_message_id(event),
                        "text": _single_line(text, 240),
                        "created_at": _now_ts(),
                        "local": deepcopy(group_boundary_intent),
                        "scope": "group",
                        "group_id": group_id,
                    }
                    self._create_lifecycle_background_task(
                        self._refine_inbound_emotion_with_model(
                            scoped_sender_id,
                            text,
                            deepcopy(group_boundary_intent),
                            review_id=review_id,
                        ),
                        label="group_boundary_emotion_refine",
                    )
            if scoped_sender_id in set(self._configured_target_ids()) or (
                isinstance(current_sender, dict) and bool(current_sender.get("manual_enabled"))
            ):
                target_user = self._get_user(scoped_sender_id)
                target_user["last_activity_at"] = received_ts
                self._mark_greetings_satisfied_by_recent_activity(target_user, activity_ts=received_ts)
                if self._cancel_inbound_conflicting_greeting(
                    target_user,
                    now=received_ts,
                    user_id=scoped_sender_id,
                    trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                ):
                    logger.info("目标用户已在群内交流,已请求取消冲突问候候选: group=%s user=%s", group_id, scoped_sender_id)
                    if not self._simulation_active(target_user) and _safe_float(target_user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(target_user, now=received_ts)
                self._maybe_schedule_post_goodnight_group_activity(
                    group_id,
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    now=received_ts,
                )
                self._maybe_schedule_group_ignore_complaint(
                    group_id,
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    now=received_ts,
                )
        group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        _, resting_mention_notice = self._group_resting_mention_notice(
            event,
            group,
            sender_id=sender_id,
            now=received_ts,
        )
        if resting_mention_notice:
            self._save_data_sync(sections={"groups"})
        scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
        if quoted_link_payload:
            scene["quoted_link_payload"] = True
        if resting_mention_notice:
            continuation = True
            scene.update(
                {
                    "trigger": "group_wakeup_resting_mention",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "mentioned_resting_user",
                    "wakeup_word": "@休息用户",
                    "wakeup_strength": "strong",
                    "wakeup_strength_label": "明确需要你接话",
                    "wakeup_instruction": (
                        f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                        "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                    ),
                }
            )
        high_intensity_state = self._group_high_intensity_state(group)
        if not resting_mention_notice:
            async with self._temporarily_release_data_lock():
                continuation = await self._group_message_is_bot_continuation(
                    group,
                    sender_id,
                    sender_name,
                    scene,
                    text,
                    allow_llm=False,
                )
        if continuation is None:
            if high_intensity_state.get("active"):
                continuation = False
            else:
                group_for_judge = deepcopy(group)
            active_for_judge = deepcopy(self._group_active_conversation(group))

    if continuation is None:
        judged = await self._group_followup_llm_judge(
            group_for_judge,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            active=active_for_judge,
            scene=scene,
        )
        continuation = bool(judged) if judged is not None else False

    async with self._data_lock:
        group = self._get_group(group_id)
        scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
        if quoted_link_payload:
            scene["quoted_link_payload"] = True
        if resting_mention_notice:
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            scene.update(
                {
                    "trigger": "group_wakeup_resting_mention",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "mentioned_resting_user",
                    "wakeup_word": "@休息用户",
                    "wakeup_strength": "strong",
                    "wakeup_strength_label": "明确需要你接话",
                    "wakeup_fatigue": {},
                    "wakeup_instruction": (
                        f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                        "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                    ),
                }
            )
        elif continuation:
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            scene.update({"trigger": "bot_conversation_followup", "talking_to": "bot", "talking_to_name": "你", "reason": "contextual_followup_after_bot_wake"})
        elif _persona_value(self, "enable_group_wakeup_enhancement", False) and str(scene.get("trigger") or "") == "mention_bot_name":
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.update(
                {
                    "trigger": "group_wakeup_direct_word",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "direct_wakeup_word",
                    "wakeup_word": _single_line(_persona_value(self, "bot_name", ""), 60),
                    "wakeup_strength": strength,
                    "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                    "wakeup_fatigue": dict(fatigue),
                    "wakeup_note": "群友提到了 Bot 名字。",
                }
            )
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": _single_line(_persona_value(self, "bot_name", ""), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": "direct_wakeup_word",
                "reason_label": self._group_wakeup_reason_label("direct_word", "direct_wakeup_word"),
                "reason_detail": "提到 Bot 名字或强唤醒词",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
            }
            wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note=_single_line(scene.get("wakeup_note"), 180),
            )
        elif (
            image_wakeup
            and str(scene.get("trigger") or "") not in {"at_other", "reply_other", "at_all"}
            and not bool(scene.get("quoted_link_payload"))
        ):
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.update(
                {
                    "trigger": "group_wakeup_image_word",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": _single_line(image_wakeup.get("reason"), 60) or "image_direct_wakeup_word",
                    "wakeup_word": _single_line(image_wakeup.get("word"), 60),
                    "wakeup_strength": strength,
                    "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                    "wakeup_fatigue": dict(fatigue),
                    "wakeup_note": _single_line(image_wakeup.get("note"), 180),
                }
            )
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": _single_line(image_wakeup.get("word"), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": _single_line(image_wakeup.get("reason"), 80) or "image_direct_wakeup_word",
                "reason_label": self._group_wakeup_reason_label("direct_word", str(image_wakeup.get("reason") or "")),
                "reason_detail": "图片视觉摘要命中强唤醒词",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
                "source": "image_vision",
            }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note=_single_line(image_wakeup.get("note"), 180),
            )
            logger.info(
                "群聊图片内容命中唤醒词: group=%s sender=%s word=%s strength=%s",
                group_id,
                sender_id,
                image_wakeup.get("word"),
                strength,
            )
        else:
            wakeup = self._evaluate_group_wakeup(
                group,
                event=event,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
            )
            if wakeup:
                setattr(event, "is_at_or_wake_command", True)
                setattr(event, "is_wake", True)
                strength = _single_line(wakeup.get("strength"), 24) or self._group_wakeup_strength(str(wakeup.get("type") or ""), group, scene)
                fatigue = self._bump_group_wakeup_fatigue(group, str(wakeup.get("type") or ""))
                scene.update(
                    {
                        "trigger": f"group_wakeup_{wakeup.get('type')}",
                        "talking_to": "bot",
                        "talking_to_name": "你",
                        "reason": _single_line(wakeup.get("reason"), 60),
                        "wakeup_word": _single_line(wakeup.get("word"), 60),
                        "wakeup_strength": strength,
                        "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                        "wakeup_fatigue": dict(fatigue),
                        "wakeup_note": _single_line(wakeup.get("note"), 180),
                        "wakeup_topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                    }
                )
                group["last_group_wakeup_at"] = _now_ts()
                group["last_group_wakeup"] = {
                    "ts": _now_ts(),
                    "type": _single_line(wakeup.get("type"), 40),
                    "word": _single_line(wakeup.get("word"), 60),
                    "strength": strength,
                    "strength_label": self._group_wakeup_strength_label(strength),
                    "fatigue": dict(fatigue),
                    "probability": wakeup.get("probability"),
                    "score": wakeup.get("score"),
                    "threshold": wakeup.get("threshold"),
                    "intensity": wakeup.get("intensity"),
                    "help_type": wakeup.get("help_type"),
                    "reason": _single_line(wakeup.get("reason"), 80),
                    "reason_label": _single_line(wakeup.get("reason_label"), 80) or self._group_wakeup_reason_label(str(wakeup.get("type") or ""), str(wakeup.get("reason") or "")),
                    "reason_detail": _single_line(wakeup.get("reason_detail"), 180) or self._group_wakeup_reason_detail(wakeup),
                    "topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                }
                wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    wakeup=group["last_group_wakeup"],
                    result="woke",
                    strength=strength,
                    fatigue=fatigue,
                    note=_single_line(wakeup.get("note"), 180),
                )
                logger.info(
                    "群聊增强唤醒命中: group=%s sender=%s type=%s word=%s strength=%s fatigue=%s reason=%s detail=%s",
                    group_id,
                    sender_id,
                    wakeup.get("type"),
                    wakeup.get("word"),
                    strength,
                    fatigue.get("label"),
                    group["last_group_wakeup"].get("reason_label"),
                    group["last_group_wakeup"].get("reason_detail"),
                )
        talking_to_bot = str(scene.get("talking_to") or "") == "bot"
        if (
            not talking_to_bot
            and
            str(scene.get("talking_to") or "") not in {"group", ""}
            and str(self._group_active_conversation(group).get("sender_id") or "") != str(sender_id or "")
        ):
            self._mark_group_bot_conversation(group, sender_id, sender_name, active=False)
        scene_trigger = str(scene.get("trigger") or "")
        if talking_to_bot and scene_trigger in {"at_bot", "reply_bot"}:
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.setdefault("wakeup_strength", strength)
            scene.setdefault("wakeup_strength_label", self._group_wakeup_strength_label(strength))
            scene["wakeup_fatigue"] = dict(fatigue)
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": "@" if scene_trigger == "at_bot" else "reply",
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": "explicit_at_or_reply",
                "reason_label": "明确 @ 或引用 Bot",
                "reason_detail": "群友明确 @ 或引用了 Bot",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
            }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note="群友明确 @ 或引用了 Bot。",
            )
        high_intensity_state = self._group_high_intensity_state(group)
        if high_intensity_state.get("active"):
            setattr(event, "private_companion_group_high_intensity", dict(high_intensity_state))
        high_intensity_merge_active = bool(high_intensity_state.get("merge_active"))
        setattr(event, "private_companion_group_scene", dict(scene))
        setattr(event, "private_companion_group_sender_name", sender_name)
        setattr(event, "private_companion_group_text", text)
        setattr(event, "private_companion_group_contextual_followup", bool(continuation))
        read_view_getter = getattr(self, "_req041_relationship_read_view", None)
        private_users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        canonical_sender = self._canonical_private_user_id(sender_id)
        relationship_user = private_users.get(canonical_sender) if isinstance(private_users, dict) else None
        if callable(read_view_getter) and isinstance(relationship_user, dict):
            read_view_getter(
                event, relationship_user, kind="group_member", group_id=group_id,
            )
        group_read_view = group
        scoped_group_getter = getattr(self, "_req041_scoped_group_read_view", None)
        if callable(scoped_group_getter):
            group_read_view = scoped_group_getter(
                event, group_id=group_id, group=group, sender_id=sender_id,
                relationship_user=relationship_user if isinstance(relationship_user, dict) else None,
            )
        self._memory_companion_attach_group_context(
            event,
            group_id=group_id,
            group=group_read_view,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
        )
        if wakeup_state_effect:
            setattr(event, "private_companion_group_wakeup_state_effect", dict(wakeup_state_effect))
        group_reference_media_with_text = False
        if talking_to_bot and text:
            async with self._temporarily_release_data_lock():
                group_reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
            if group_reference_media_with_text:
                logger.info(
                    "群聊引用媒体/合并消息附带文字,跳过群聊收口等待: group=%s sender=%s text=%s",
                    group_id,
                    sender_id,
                    _single_line(text, 80),
                )
        if high_intensity_merge_active and talking_to_bot and not group_reference_media_with_text:
            high_key = self._group_high_intensity_buffer_key(group_id, sender_id)
            if self._note_semantic_message_buffer(
                high_key,
                text,
                sender_name=sender_name,
                wait_seconds=self._group_high_intensity_merge_wait_seconds(),
                force=True,
                kind="group_high_intensity",
            ):
                self._capture_group_observation_once(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                    scene=scene,
                    message_id=self._event_message_id(event),
                    event=event,
                )
                self._save_data_sync(sections={"groups"})
                logger.info(
                    "群聊高强度消息已合并等待: group=%s sender=%s scope=%s recent_wakeups=%s floor=%s reason=%s wait=%ss text=%s",
                    group_id,
                    sender_id,
                    _persona_value(self, "group_high_intensity_merge_scope", "group"),
                    high_intensity_state.get("recent_wakeups"),
                    high_intensity_state.get("merge_recent_floor"),
                    high_intensity_state.get("reason"),
                    self._group_high_intensity_merge_wait_seconds(),
                    _single_line(text, 80),
                )
                event.stop_event()
                return
        if talking_to_bot and not high_intensity_merge_active and not group_reference_media_with_text:
            async with self._temporarily_release_data_lock():
                air_guard = await self._group_air_reply_guard_decision(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    scene=scene,
                )
            if isinstance(air_guard, dict) and air_guard.get("block"):
                self._capture_group_observation_once(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                    scene=scene,
                    message_id=self._event_message_id(event),
                    event=event,
                )
                group["last_air_guard_block"] = {
                    "ts": _now_ts(),
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                    "reason": _single_line(air_guard.get("reason"), 60),
                    "answer": _single_line(air_guard.get("answer"), 60),
                    "recent_bot_replies": _safe_int(air_guard.get("recent_bot_replies"), 0, 0, 99),
                    "recent_polite_replies": _safe_int(air_guard.get("recent_polite_replies"), 0, 0, 99),
                }
                self._save_data_sync(sections={"groups"})
                logger.info(
                    "群聊读空气拦截回复: group=%s sender=%s reason=%s text=%s",
                    group_id,
                    sender_id,
                    air_guard.get("reason"),
                    _single_line(text, 80),
                )
                event.stop_event()
                return
        group_smart_wait = 0.0
        group_buffer_key = self._semantic_buffer_key(f"group:{group_id}", sender_id)
        pending_debounce_merge = False
        pending_absorber = getattr(self, "_message_debounce_absorb_pending_message", None)
        if (
            talking_to_bot
            and not high_intensity_merge_active
            and not group_reference_media_with_text
            and callable(pending_absorber)
        ):
            pending_debounce_merge = bool(pending_absorber(event, text))
        if talking_to_bot and not high_intensity_merge_active and not group_reference_media_with_text:
            if not pending_debounce_merge:
                self._maybe_record_smart_message_debounce_followup(
                    scope=f"group:{group_id}",
                    sender_id=sender_id,
                    text=text,
                    now=_now_ts(),
                )
                async with self._temporarily_release_data_lock():
                    group_smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                        event,
                        key=group_buffer_key,
                        text=text,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        private_chat=False,
                    )
        group_smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
        group_smart_decision = str(group_smart_result.get("decision") or "") if isinstance(group_smart_result, dict) else ""
        group_smart_handled = group_smart_decision in {"complete", "incomplete"}
        short_wait = 0.0
        if not high_intensity_merge_active:
            short_wait = self._group_short_wakeup_wait_seconds(event, text, smart_result=group_smart_result)
        if short_wait > 0:
            group_smart_wait = max(group_smart_wait, short_wait)
            group_smart_decision = "incomplete"
            group_smart_handled = True
        if (
            talking_to_bot
            and not high_intensity_merge_active
            and not group_reference_media_with_text
            and group_smart_decision == "incomplete"
            and self._note_semantic_message_buffer(
                group_buffer_key,
                text,
                sender_name=sender_name,
                wait_seconds=group_smart_wait,
                smart_debounce={"enabled": group_smart_handled, "decision": group_smart_decision or "fixed"},
                kind="group_short_wakeup" if short_wait > 0 else "group_text",
            )
        ):
            self._save_data_sync(sections={"smart_message_debounce"})
            event.stop_event()
            return
        self._capture_group_observation_once(
            group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            group_id=group_id,
            scene=scene,
            message_id=self._event_message_id(event),
            event=event,
        )
        registration_payload = self._maybe_worldbook_self_register_from_group_message(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            group=group,
        )
        affinity_preparer = getattr(self, "_req041_prepare_group_affinity_candidate", None)
        if (
            callable(affinity_preparer)
            and scene_trigger in {"at_bot", "reply_bot"}
            and not group_reference_media_with_text
            and not (
                isinstance(registration_payload, dict)
                and bool(registration_payload.get("blocked_reply"))
            )
        ):
            affinity_preparer(
                event,
                group_id=group_id,
                relationship_user=(
                    relationship_user if isinstance(relationship_user, dict) else None
                ),
                scene_trigger=scene_trigger,
                forwarded=group_reference_media_with_text,
            )
        share_scheduled = self._maybe_schedule_group_private_share(group_id, group, trigger_sender_id=sender_id)
        save_sections = {"groups", "users", "proactive_candidate_pool"}
        if self._expression_group_learning_source_enabled(group.get("group_id") or group_id):
            save_sections.add("expression_voice_profile")
        if isinstance(registration_payload, dict):
            if registration_payload.get("updated_observation_profile"):
                save_sections.add("worldbook_member_profiles")
            if registration_payload.get("user_id"):
                save_sections.update(
                    {
                        "worldbook_member_profiles",
                        "worldbook_deleted_member_ids",
                    }
                )
        self._save_data_sync(sections=save_sections)
        group_snapshot = deepcopy(group)
        group_snapshot_high_intensity = dict(high_intensity_state)
    await self._dispatch_due_atrelay_tasks(event, group_id, sender_id)
    if isinstance(registration_payload, dict) and registration_payload.get("blocked_reply"):
        await self._reply(event, str(registration_payload.get("blocked_reply") or "这个称呼我不记。"))
        event.stop_event()
        return
    if isinstance(registration_payload, dict) and registration_payload.get("confirm_reply"):
        await self._reply(event, str(registration_payload.get("confirm_reply") or ""))
        if not registration_payload.get("user_id"):
            event.stop_event()
            return
    if registration_payload and registration_payload.get("user_id"):
        self._create_lifecycle_background_task(
            self._refresh_worldbook_self_registration_impression(registration_payload),
            label="refresh_worldbook_self_registration_impression",
        )
    if share_scheduled:
        self._create_lifecycle_background_task(
            self._kick_proactive_loop_once(),
            label="kick_proactive_loop_group",
        )
    if not group_snapshot_high_intensity.get("active"):
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_episode(group_id, group_snapshot),
            label="refresh_group_episode",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_slang_meanings(group_id, group_snapshot),
            label="refresh_group_slang",
        )
    else:
        logger.info(
            "群聊高强度收口生效: group=%s recent_wakeups=%s threshold=%s merge_active=%s floor=%s reason=%s merge_scope=%s merge_wait=%ss skip=followup-refresh/general-interject repeat=enabled",
            group_id,
            group_snapshot_high_intensity.get("recent_wakeups"),
            group_snapshot_high_intensity.get("threshold"),
            group_snapshot_high_intensity.get("merge_active"),
            group_snapshot_high_intensity.get("merge_recent_floor"),
            group_snapshot_high_intensity.get("reason"),
            _persona_value(self, "group_high_intensity_merge_scope", "group"),
            self._group_high_intensity_merge_wait_seconds(),
        )
    await self._maybe_group_interject(
        event,
        group_snapshot,
        text,
        allow_interjection=(
            not bool(group_snapshot_high_intensity.get("active"))
            and self._group_wakeup_allows_general_interjection(scene)
        ),
    )
    original_interject_at = _safe_float(group.get("last_interject_at"), 0) if isinstance(group, dict) else 0
    repeat_state_changed = group_snapshot.get("repeat_follow_state") != (
        group.get("repeat_follow_state") if isinstance(group, dict) else {}
    )
    if _safe_float(group_snapshot.get("last_interject_at"), 0) > original_interject_at or repeat_state_changed:
        async with self._data_lock:
            current = self._get_group(group_id)
            current["last_interject_at"] = group_snapshot.get("last_interject_at", current.get("last_interject_at", 0))
            current["interject_day"] = group_snapshot.get("interject_day", current.get("interject_day", ""))
            current["interject_today"] = group_snapshot.get("interject_today", current.get("interject_today", 0))
            current["last_bot_interjection"] = group_snapshot.get("last_bot_interjection", current.get("last_bot_interjection", {}))
            current["repeat_follow_state"] = group_snapshot.get("repeat_follow_state", current.get("repeat_follow_state", {}))
            current["recent_bot_replies"] = deepcopy(
                group_snapshot.get("recent_bot_replies", current.get("recent_bot_replies", []))
            )
            self._save_data_sync(sections={"groups"})
