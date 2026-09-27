# -*- coding: utf-8 -*-
"""ProactiveEngineGatePart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_gate.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 210 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineGateMixin）。
"""
from __future__ import annotations

from .proactive_engine_gate_shared import logger
from .proactive_engine_gate_shared import Any
from .proactive_engine_gate_shared import _path_text
from .proactive_engine_gate_shared import _safe_float
from .proactive_engine_gate_shared import _single_line
from .proactive_engine_gate_shared import os
from .proactive_engine_gate_shared import runtime_persona_setting



class ProactiveEngineGatePart03Mixin:
    """ProactiveEngineGatePart03Mixin（从 ProactiveEngineGateMixin 拆出）。"""


    async def _render_message(self, user: dict[str, Any]) -> tuple[str, str, str, list[Any], str, str]:
        name = str(user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"))
        user["planned_opener_mode"] = ""
        user.pop("_proactive_photo_subject_owner", None)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        planned_action = str(user.get("planned_proactive_action") or "message")
        planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
        due_timer_active = self._has_due_llm_timer(user)
        troubleshooting_active = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "troubleshooting"
        reason = planned_reason if planned_reason and (troubleshooting_active or due_timer_active or self._is_reason_allowed_now(planned_reason, user)) else ""
        if not reason:
            reason, _ = self._choose_proactive_message(user, name, planned_reason)
            planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_action = self._choose_action_for_reason(reason, user, motive=planned_motive)
        if self._should_use_name_only_opener(
            user,
            reason=reason,
            action=planned_action,
            motive=planned_motive,
        ):
            user["planned_opener_mode"] = "name_only"
            return reason, self._build_name_only_opener(name), "", [], "先轻轻叫了你一声", "message"
        budget_remaining = getattr(self, "_llm_daily_budget_remaining", None)
        if callable(budget_remaining) and budget_remaining() == 0:
            user["_proactive_render_failure_stage"] = "今日 Token 硬限额已耗尽，未执行主动动作"
            return reason, "", "", [], "Token 硬限额已耗尽", planned_action
        deferred_poke = planned_action == "poke"
        action_payload = (
            {
                "success": True,
                "context": "poke：待主动正文确认可发送后再执行；本阶段尚未产生实际戳一戳",
                "extra_components": [],
                "summary": "准备戳一下",
                "effective_action": "poke",
            }
            if deferred_poke
            else await self._execute_proactive_action(planned_action, user, name, reason)
        )
        effective_action = _single_line(action_payload.get("effective_action") or planned_action, 60) or "message"
        raw_action_context = str(action_payload.get("context") or "")
        if reason == "group_share":
            share_context = self._format_group_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, share_context) if part).strip()
        if reason == "bili_video_share":
            video_context = self._format_bilibili_video_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, video_context) if part).strip()
        if reason == "news_share":
            news_context = self._format_news_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, news_context) if part).strip()
        if reason == "web_exploration_share":
            exploration_context = self._format_web_exploration_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, exploration_context) if part).strip()
        if reason == "creative_share":
            creative_context = self._format_creative_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, creative_context) if part).strip()
        if reason == "memory_echo":
            echo = user.get("memory_echo_context") if isinstance(user.get("memory_echo_context"), dict) else {}
            echo_summary = _single_line(echo.get("summary"), 180)
            echo_residue = _single_line(echo.get("residue"), 140)
            echo_correction = _single_line(echo.get("correction"), 180)
            echo_source_date = _single_line(echo.get("source_date"), 20) or "昨日"
            echo_context = (
                f"记忆回响证据（{echo_source_date}，摘要而非逐字原话）：概括={echo_summary}；残留={echo_residue}。"
                "只可把它当作轻微承接背景，不得添加摘要中没有的事实，不得使用引号伪装成用户原话，"
                "不要说‘系统记录/记忆库显示’，也不要要求用户必须回应。"
                + (
                    f"这是一次纠正后的记忆：{echo_correction}。必须沿用修正版，不要再次复述或维护原来的错误；"
                    "可以自然承认自己之前记岔过，但不要把故意出错写成表演。"
                    if echo_correction
                    else "若细节置信不足，使用‘我是不是记得……’这类留有余地的表达，允许用户自然纠正。"
                )
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, echo_context) if part).strip()
        if reason == "mood_checkin":
            mood = user.get("mood_checkin_context") if isinstance(user.get("mood_checkin_context"), dict) else {}
            mood_context = (
                f"隔日情绪回访证据（摘要而非逐字原话）：{_single_line(mood.get('residue'), 160)}。"
                "只围绕这项已知状态轻声问一句今天是否好一点；不得诊断，不得扩大严重程度，"
                "不得声称用户现在仍处于昨天的状态，也不要连续追问。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, mood_context) if part).strip()
        if reason == "absence_miss":
            absence = user.get("absence_miss_context") if isinstance(user.get("absence_miss_context"), dict) else {}
            absence_context = (
                f"自然停聊时长约 {_safe_float(absence.get('absent_days'), 0):.1f} 天。"
                "可以直接、简短地表达一点想念，但不得写成控诉、查岗、索取安抚或催促回复；"
                "不要虚构这几天用户的经历。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, absence_context) if part).strip()
        if reason == "game_invite":
            game = user.get("game_invite_context") if isinstance(user.get("game_invite_context"), dict) else {}
            game_context = (
                f"游戏邀约证据：游戏={_single_line(game.get('game_label'), 40) or '上次那款游戏'}；"
                f"余韵={_single_line(game.get('reflection'), 160)}；语气={_single_line(game.get('tone'), 120)}。"
                "只发一次轻量、可拒绝的邀约；不要声称已经开房、已开始对局或现在轮到用户操作，"
                "也不要暴露 invite_interest 等内部评分。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, game_context) if part).strip()
        extra_components = list(action_payload.get("extra_components") or [])
        action_summary = _single_line(action_payload.get("summary") or planned_action, 80)
        if not bool(action_payload.get("success", True)):
            if "photo_text" in {planned_action, effective_action}:
                logger.info(
                    "主动图片动作未产出,降级为纯文字分享: user=%s reason=%s topic=%s",
                    _single_line(user.get("user_id"), 40),
                    reason,
                    _single_line(user.get("planned_proactive_topic"), 80),
                )
                planned_action = "message"
                effective_action = "message"
                extra_components = []
                raw_action_context = "message：图片动作本轮未产出；只按原话题自然分享，不得声称已拍照、已生成或已发送图片"
                action_summary = "图片未产出，已降级为文字"
            else:
                user["_proactive_render_failure_stage"] = f"主动动作执行失败：{effective_action or planned_action or 'unknown'}"
                return reason, "", "", [], action_summary, effective_action
        image_path = self._extract_action_image_path(raw_action_context)
        photo_caption = self._extract_action_photo_caption(raw_action_context)
        photo_subject_owner = self._extract_action_photo_subject_owner(raw_action_context)
        if image_path:
            user["_proactive_photo_subject_owner"] = photo_subject_owner or "unknown"
        if image_path and photo_caption:
            action_summary = f"发图：{photo_caption}"
        action_context = await self._narrate_action_context(effective_action, raw_action_context)
        if image_path:
            action_context = f"{action_context}\n真实图片文件：{image_path}".strip()
        text = await self._generate_proactive_message_with_llm(
            user, name, reason, action_context, action=effective_action, motive=planned_motive
        )
        captured_text, captured_image_path, captured_extra_components = self._pop_framework_captured_send_payload(
            str(user.get("umo") or "")
        )
        deferred_photo = self._pop_framework_deferred_photo_payload(
            str(user.get("umo") or "")
        )
        deferred_photo_path = _path_text(deferred_photo.get("path"), 1000)
        if deferred_photo_path and os.path.exists(deferred_photo_path):
            deferred_caption = _single_line(deferred_photo.get("caption"), 500)
            text = deferred_caption
            image_path = deferred_photo_path
            extra_components = []
            effective_action = "photo_text"
            action_summary = f"发图：{deferred_caption}" if deferred_caption else "发送了一张图片"
            deferred_intent_kind = _single_line(deferred_photo.get("intent_kind"), 40)
            user["_proactive_photo_subject_owner"] = (
                "bot"
                if deferred_intent_kind in {"selfie", "sticker"}
                else "scene"
                if deferred_intent_kind == "text2img"
                else "unknown"
            )
            logger.info(
                "主动消息采用 pc_generate_photo 成图并进入统一发送链: user=%s kind=%s",
                _single_line(user.get("user_id"), 40),
                deferred_intent_kind or "unknown",
            )
        if not deferred_photo_path and (
            "photo_text" in effective_action or planned_action == "photo_text"
        ):
            if captured_text:
                text = captured_text
            if captured_image_path:
                image_path = captured_image_path
            if self._contains_inline_image_tag(text):
                image_path = ""
                extra_components = []
        if captured_extra_components and not deferred_photo_path:
            extra_components = list(captured_extra_components)
        if "photo_text" in planned_action and self._contains_inline_image_tag(text):
            image_path = ""
            extra_components = []
        if not image_path and not extra_components:
            text = self._remove_unbacked_media_claims(text)
        text = self._visible_text_without_tts_reading(text, limit=1000)
        text = self._normalize_proactive_sentence_flow(text)
        if reason == "group_share":
            recency_repair = getattr(self, "_repair_group_share_recency_text", None)
            if callable(recency_repair):
                text = recency_repair(user, text)
        sticker_pending_getter = getattr(self, "_proactive_sticker_only_pending", None)
        try:
            sticker_only_pending = bool(sticker_pending_getter(user.get("umo"))) if callable(sticker_pending_getter) else False
        except Exception:
            sticker_only_pending = False
        if not text and not image_path and not extra_components and not sticker_only_pending:
            return reason, "", "", [], action_summary, effective_action
        if deferred_poke:
            poke_payload = await self._execute_proactive_action("poke", user, name, reason)
            if not bool(poke_payload.get("success", False)):
                user["_proactive_render_failure_stage"] = _single_line(
                    poke_payload.get("context"), 160
                ) or "主动戳一戳执行失败"
                return reason, "", "", [], "戳一戳未执行", "poke"
            action_summary = _single_line(poke_payload.get("summary"), 80) or "戳了你一下"
        if sticker_only_pending:
            pre_poke_count, pre_poke_context = 0, ""
        else:
            pre_poke_count, pre_poke_context = await self._maybe_run_pre_message_poke(
                user,
                name,
                reason,
                action=effective_action,
                motive=planned_motive,
            )
        if pre_poke_context and not pre_poke_context.startswith("poke：已"):
            logger.info("消息前置戳一戳失败,跳过本次前置戳: %s", _single_line(pre_poke_context, 120))
        if pre_poke_count > 0:
            action_summary = f"先戳了 {pre_poke_count} 下 + {action_summary}"
            effective_action = f"poke+{effective_action}" if effective_action != "poke" else "poke"
        return reason, text, image_path, extra_components, action_summary, effective_action
