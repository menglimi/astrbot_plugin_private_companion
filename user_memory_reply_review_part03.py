# -*- coding: utf-8 -*-
"""UserMemoryReplyReviewPart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_reply_review.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 478 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryReplyReviewMixin）。
"""
from __future__ import annotations

from .user_memory_reply_review_shared import (
    _CLAUSE_BOUNDARY,
    _IMPLICIT_LATE,
    _LATE_CLAIM_GAP,
    _LATE_CLOCK,
    _LATE_CLOCK_INTRO,
    _SLEEP_CUE,
)
from .user_memory_reply_review_shared import Any
from .user_memory_reply_review_shared import AstrMessageEvent
from .user_memory_reply_review_shared import PromptSection
from .user_memory_reply_review_shared import _normalize_photo_subject_owner
from .user_memory_reply_review_shared import _now_ts
from .user_memory_reply_review_shared import _photo_subject_owner_prompt_label
from .user_memory_reply_review_shared import _render_conversation_section_labeled
from .user_memory_reply_review_shared import _safe_float
from .user_memory_reply_review_shared import _single_line
from .user_memory_reply_review_shared import _strip_internal_message_blocks
from .user_memory_reply_review_shared import prompt_section
from .user_memory_reply_review_shared import re
from .user_memory_reply_review_shared import runtime_persona_setting



class UserMemoryReplyReviewPart03Mixin:
    """UserMemoryReplyReviewPart03Mixin（从 UserMemoryReplyReviewMixin 拆出）。"""


    def _response_review_severe_flags(self, flags: list[str]) -> list[str]:
        severe = {
            "meta_or_assistant",
            "leaks_internal",
            "repeats_last_bot_message",
            "casual_overexplained",
            "weather_overexplained",
            "invalid_current_time_anchor",
            "false_no_reply_claim",
            "fact_attribution_after_correction",
            "unverified_fact_attribution",
            "proactive_media_ownership_reversal",
            "denies_existing_creative_work",
            "content_tier_review_candidate",
        }
        if self._expression_style_review_enabled():
            severe.update({"unnatural_punctuation", "expression_overfit", "copied_user_expression_sample"})
        return [flag for flag in flags if flag in severe]

    def _casual_reply_review_limit(self, inbound_text: str) -> int:
        inbound_compact = self._compact_repeat_text(inbound_text)
        if len(inbound_compact) <= 12:
            return min(140, max(90, runtime_persona_setting(self, "response_review_max_chars", 260) // 2))
        if len(inbound_compact) <= 28:
            return min(180, max(120, int(runtime_persona_setting(self, "response_review_max_chars", 260) * 0.65)))
        return runtime_persona_setting(self, "response_review_max_chars", 260)

    def _is_short_casual_inbound_for_review(self, inbound_text: str, user: dict[str, Any]) -> bool:
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if len(self._compact_repeat_text(inbound)) > 32:
            return False
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        if str(intent_profile.get("intent") or "") in {"help", "task", "code", "search"}:
            return False
        if re.search(r"(怎么|如何|为什么|啥原因|帮我|检查|分析|整理|写|生成|修|改|步骤|教程|配置|报错)", inbound):
            return False
        return True

    def _fallback_overlong_casual_reply(self, inbound_text: str, response_text: str) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).strip()
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?…])\s*|\n+", cleaned) if part.strip()]
        for part in parts:
            if len(part) <= 90 and not re.search(r"(首先|其次|最后|建议你|你可以.*也可以|总结一下|以下是)", part):
                return part
        if parts:
            return _single_line(parts[0], 70)
        return ""

    def _response_has_invalid_current_time_anchor(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        now = self._environment_now()
        current_minutes = now.hour * 60 + now.minute
        # 钟点必须与睡意线索同句才算深夜宣言；白天的 11 点只是普通时间点。
        late_clock = r"(?:" + _LATE_CLOCK_INTRO + r"\s*)?(?:晚上)?" + _LATE_CLOCK
        explicit_late_anchor = bool(
            re.search(late_clock + _LATE_CLAIM_GAP + _SLEEP_CUE, cleaned)
            or re.search(_SLEEP_CUE + _LATE_CLAIM_GAP + late_clock, cleaned)
        )
        implicit_late_anchor = bool(re.search(_IMPLICIT_LATE, cleaned))
        late_night = 22 * 60 <= current_minutes or current_minutes <= 90
        if (explicit_late_anchor or implicit_late_anchor) and not late_night:
            return True
        return False

    def _has_open_proactive_awaiting_reply(self, user: dict[str, Any]) -> bool:
        if not isinstance(user, dict):
            return False
        now = _now_ts()
        afterglow = user.get("proactive_afterglow")
        if isinstance(afterglow, dict) and afterglow.get("status") == "awaiting_reply":
            ts = _safe_float(afterglow.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict) or item.get("status") != "awaiting_reply":
                continue
            ts = _safe_float(item.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        return False

    def _response_has_false_no_reply_claim(self, text: str, inbound_text: str, user: dict[str, Any]) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if not re.search(r"(看你|见你|以为你|还以为你|你).{0,8}(没回|不回|没理|不理|没搭理)|等你回|等你回复|等你消息", cleaned):
            return False
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if re.search(r"(之前|前面|上一条|上次|刚才那条|我那条)", cleaned) and self._has_open_proactive_awaiting_reply(user):
            return False
        return True

    def _fallback_temporal_or_continuity_confused_reply(
        self,
        inbound_text: str,
        response_text: str,
        *,
        flags: list[str] | None = None,
        user: dict[str, Any] | None = None,
    ) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).strip()
        if not cleaned:
            return ""
        active_flags = set(flags or [])
        user = user if isinstance(user, dict) else {}
        if "invalid_current_time_anchor" not in active_flags and self._response_has_invalid_current_time_anchor(cleaned):
            active_flags.add("invalid_current_time_anchor")
        if "false_no_reply_claim" not in active_flags and self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            active_flags.add("false_no_reply_claim")
        if not active_flags.intersection({"invalid_current_time_anchor", "false_no_reply_claim"}):
            return ""
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message"), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 260)
        if (
            "false_no_reply_claim" in active_flags
            and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}
            and last_message
            and self._response_has_invalid_current_time_anchor(last_message)
        ):
            return "啊，刚才那句时间感说偏了，是我没接稳你前一句。"
        if "false_no_reply_claim" in active_flags and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}:
            return "啊，我刚才那句是顺口接你问的“有意思的什么”，不是说你没回。"
        # 只删完整的深夜宣言（可带泛称称呼、可带睡意线索），删到该小句结束；
        # 白天的普通时间点不匹配，也不会被截成半截，更不会从句中切走。
        cleaned = re.sub(
            _CLAUSE_BOUNDARY + r"[，,；;、\s]*"
            r"(?:(?:[\u4e00-\u9fffA-Za-z0-9_\-]{1,12})[，,、:：]|(?:主人|宝贝|亲爱的|宝宝|老师))?\s*"
            + _LATE_CLOCK_INTRO + r"?\s*(?:晚上)?" + _LATE_CLOCK + r"(?:了|啦|咯|吧)?"
            + _LATE_CLAIM_GAP + _SLEEP_CUE + r"[^，,。！？!?\n]{0,8}[，,。！？!?]?[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        # 「时间不早了」这类隐含深夜说法：后面跟着睡意线索时连小句一起删，
        # 否则只删宣言本身，不牵连后面那句话。
        cleaned = re.sub(
            _CLAUSE_BOUNDARY + r"[，,；;、\s]*(?:那[^，,。！？!?；;]{0,12})?" + _IMPLICIT_LATE + r"(?:了|啦|咯)?"
            r"(?:"
            + _LATE_CLAIM_GAP + _SLEEP_CUE + r"[^，,。！？!?\n]{0,8}[，,。！？!?]?"
            r"|(?=[，,。！？!?]|$)[，,。！？!?]?"
            r")"
            r"[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(
            r"[，,。！？!?；;、\s]*(?:看你|见你|以为你|还以为你|你).{0,8}(?:没回|不回|没理|不理|没搭理).{0,16}?(?:嘛|啦|了|而已|就)?[，,。！？!?~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(r"[，,；;、\s]+$", "", cleaned).strip()
        if cleaned:
            return cleaned
        inbound = str(inbound_text or "").strip()
        if inbound in {"？", "?"}:
            return "啊，我刚才那句没说清楚，是在接你问“有意思的什么”。"
        return "刚才那句我说偏了，重新接你这句。"

    def _simulation_active(self, user: dict[str, Any]) -> bool:
        raw = user.get("simulation_mode")
        return isinstance(raw, dict) and bool(raw.get("active"))

    def _cancel_inbound_conflicting_greeting(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        user_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        now = now or _now_ts()
        changed = False
        planned_reason = str(user.get("planned_proactive_reason") or "")
        planned_topic = _single_line(user.get("planned_proactive_topic"), 80)
        planned_is_greeting_habit = (
            planned_reason == "habit_awareness"
            and self._habit_topic_is_greeting_like(planned_topic)
            and self._recent_activity_suppresses_habit_greeting(user, now=now, topic=planned_topic)
        )
        if (
            self._inbound_satisfies_greeting(planned_reason, now=now, user=user)
            or planned_is_greeting_habit
        ):
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at > 0:
                if self._inbound_satisfies_greeting(planned_reason, now=now):
                    changed = self._mark_greeting_satisfied_by_inbound(user, planned_reason) or changed
                self._clear_pending_proactive_plan(user)
                changed = True
        raw_followup = user.get("pending_followup_event")
        if isinstance(raw_followup, dict):
            if raw_followup.get("_cancel_on_inbound") or raw_followup.get("_chain_followup") or raw_followup.get("_opener_followup"):
                user["pending_followup_event"] = {}
                changed = True
            else:
                follow_reason = str(raw_followup.get("reason") or "")
                if self._inbound_satisfies_greeting(follow_reason, now=now, user=user):
                    changed = self._mark_greeting_satisfied_by_inbound(user, follow_reason) or changed
                    user["pending_followup_event"] = {}
                    changed = True
        raw_timer = user.get("llm_timer_event")
        if isinstance(raw_timer, dict):
            timer_reason = str(raw_timer.get("reason") or "")
            if self._inbound_satisfies_greeting(timer_reason, now=now, user=user):
                if _single_line(raw_timer.get("backend"), 40) == "astrbot_cron":
                    queue_cancel = getattr(self, "_queue_official_llm_timer_cancel", None)
                    queued = bool(
                        callable(queue_cancel)
                        and queue_cancel(
                            _single_line(user_id or user.get("user_id"), 120),
                            raw_timer,
                            source_text="用户已在问候时段自然出现",
                            source_origin="inbound_satisfied_greeting",
                            trigger_umo=trigger_umo,
                        )
                    )
                    if queued:
                        changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                        changed = True
                else:
                    changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                    user["llm_timer_event"] = {}
                    changed = True
        return changed

    async def _format_proactive_reply_prompt_sections(
        self,
        event: AstrMessageEvent,
    ) -> list[PromptSection]:
        try:
            user_id = str(event.get_sender_id())
            event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 180)
        except Exception:
            return []
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            user_id = resolver(event, user_id)
        consume_suspended = False
        recent_delivery_sections: list[PromptSection] = []
        async with self._data_lock:
            user = dict(self._get_user(user_id))
            raw_suspended = user.get("suspended_proactive")
            if isinstance(raw_suspended, dict) and raw_suspended.get("active") and raw_suspended.get("resume_ready"):
                consume_suspended = True
                current = self._get_user(user_id)
                current["suspended_proactive"] = {}
                self._save_data_sync(sections={"users"})

            last_proactive_text = _single_line(user.get("last_proactive_message"), 500)
            last_proactive_at = _safe_float(user.get("last_proactive_sent_at"), 0)
            last_proactive_action = _single_line(user.get("last_proactive_action"), 80).lower()
            last_proactive_summary = _single_line(user.get("last_proactive_behavior_summary"), 300)
            delivery_umo = _single_line(user.get("last_proactive_delivery_umo") or user.get("umo"), 180)
            consumed_for = _safe_float(user.get("last_proactive_reply_context_consumed_for"), 0)
            max_age = min(
                max(1, runtime_persona_setting(self, "proactive_reply_context_hours", 12)) * 3600,
                30 * 60,
            )
            same_delivery = last_proactive_at > 0 and abs(consumed_for - last_proactive_at) > 0.001
            if (
                last_proactive_text
                and event_umo
                and delivery_umo == event_umo
                and same_delivery
                and 0 <= _now_ts() - last_proactive_at <= max_age
            ):
                recent_delivery_body = (
                    f"你刚才在当前会话主动发了：{last_proactive_text}\n"
                    "这是你自己已经说过并成功外发的内容。用户当前消息很可能在回应它；"
                    "必须直接承认并顺着这条消息接话，不得声称不知道自己发了什么、没看到这条消息或把它当成别人发的。"
                    "如果其中的标题、平台或链接确实有误，简短承认并依据上面的实际原文纠正，不要继续编造来源。"
                )
                recent_delivery_sections.append(
                    prompt_section(
                        key="proactive.recent_delivery",
                        title="刚才你主动发出的消息",
                        source="proactive",
                        content=recent_delivery_body,
                    )
                )
                if "photo_text" in last_proactive_action:
                    image_scene = ""
                    subject_owner = "unknown"
                    snapshot = user.get("last_photo_share_snapshot")
                    if isinstance(snapshot, dict):
                        image_scene = _single_line(snapshot.get("caption"), 260)
                        subject_owner = _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown"
                    if not image_scene and last_proactive_summary:
                        image_scene = _single_line(re.split(r"[:：]", last_proactive_summary, maxsplit=1)[-1], 260)
                    image_subject_body = (
                        (f"图片画面：{image_scene}\n" if image_scene else "")
                        + f"图片发送者：Bot/当前人格；画面主体：{_photo_subject_owner_prompt_label(subject_owner)}\n"
                        + "用户接下来的短句默认是在评价这张图，不是在说用户自己做了图中的事。"
                        "严格按上面的结构化归属理解代词和动作，不要仅凭‘她’猜主体。"
                        "除非用户明确说‘我做了/我弄洒了’，否则不得责怪或安慰用户仿佛事故发生在用户身上。"
                    )
                    recent_delivery_sections.append(
                        prompt_section(
                            key="proactive.recent_media_subject",
                            title="刚才主动图片的主客体",
                            source="proactive",
                            content=image_subject_body,
                        )
                    )
                current = self._get_user(user_id)
                current["last_proactive_reply_context_consumed_for"] = last_proactive_at
                self._save_data_sync(sections={"users"})

        suspended = user.get("suspended_proactive")
        if isinstance(suspended, dict) and suspended.get("active") and (
            suspended.get("resume_ready") or consume_suspended
        ):
            opener = _single_line(suspended.get("opener_text"), 60) or f"{runtime_persona_setting(self, 'default_nickname', '你')}……"
            hidden_reason = _single_line(suspended.get("reason"), 40)
            hidden_action = _single_line(suspended.get("action"), 32)
            hidden_motive = _single_line(suspended.get("motive"), 120)
            hidden_summary = _single_line(suspended.get("summary"), 60)
            schedule_context = self._format_schedule_context_for_prompt()
            body = (
                f"你刚才主动私聊时,只先发了一句：{opener}\n"
                "你真正想说的后半句还没发出去,现在用户回头了。\n"
                f"当时主动原因：{hidden_reason or 'check_in'}\n"
                f"当时原本想用的主动行为：{hidden_action or 'message'}"
                + (f"（{hidden_summary}）\n" if hidden_summary else "\n")
                + (f"当时心里那点念头：{hidden_motive}\n" if hidden_motive else "")
                + "请像终于等到对方抬头一样,自然把后半句接上。不要解释“我刚才故意只叫你一声”,也不要突然像全新开场。\n"
                + "如果用户现在只是“怎么了”“？”“在吗”这类短句,就把它理解成他终于回头了,顺着那一下接话。\n"
                + "可以参考当前状态和今天的生活背景,但只体现在语气和接话方式里；别把日期、状态或日程当汇报念出来。\n"
                + f"当前/附近日程参考：{schedule_context or '无当前日程'}\n"
                + f"今天预设的生活线索：{self._format_story_plan_for_prompt()}"
            )
            section = prompt_section(
                key="proactive.suspended_opener",
                title="刚才悬着的话头",
                source="proactive",
                content=body,
            )
            return [section]

        return recent_delivery_sections

    async def _format_proactive_reply_context(
        self,
        event: AstrMessageEvent,
    ) -> str:
        sections = await self._format_proactive_reply_prompt_sections(event)
        return "\n".join(
            _render_conversation_section_labeled(section)
            for section in sections
        )

    def _response_review_drop_marker(self) -> str:
        return "__PRIVATE_COMPANION_DROP_DUPLICATE__"

    def _is_response_review_drop_marker(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw == self._response_review_drop_marker():
            return True
        compact = re.sub(r"[\s<>\[\]{}_'\"`“”‘’：:。.!！?？-]+", "", raw).upper()
        return compact in {"PRIVATECOMPANIONDROPDUPLICATE", "DROPDUPLICATE", "丢弃重复", "取消重复"}

    def _text_is_near_duplicate_reply(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current == recent:
            return True
        short, long = (current, recent) if len(current) <= len(recent) else (recent, current)
        return len(short) >= 12 and short in long and len(short) / max(1, len(long)) >= 0.82

    def _should_drop_duplicate_reply_text(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
    ) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, ""
        if self._inbound_explicitly_requests_repeat(inbound_text):
            return False, ""
        visible = _single_line(_strip_internal_message_blocks(response_text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 500)
        last_message = _single_line(user.get("last_companion_message"), 500)
        if not visible or not last_message:
            return False, ""
        last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if last_at > 0 and _now_ts() - last_at > 30 * 60:
            return False, ""
        if self._text_is_near_duplicate_reply(visible, last_message):
            return True, "最终回复与上一条 Bot 消息几乎相同"
        return False, ""

    def _response_review_flags(self, text: str, user: dict[str, Any], *, inbound_text: str = "") -> list[str]:
        cleaned = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", str(text or "")).strip()
        flags: list[str] = []
        if not cleaned:
            return flags
        if "```" in cleaned:
            return flags
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        is_help = str(intent_profile.get("intent") or "") == "help"
        length_limit = runtime_persona_setting(self, "response_review_max_chars", 260) * (2 if is_help else 1)
        if len(cleaned) > length_limit:
            flags.append("too_long")
        if not is_help and self._is_short_casual_inbound_for_review(inbound_text, user):
            casual_limit = self._casual_reply_review_limit(inbound_text)
            sentence_count = len(re.findall(r"[。！？!?…]+", cleaned))
            paragraph_count = len([part for part in re.split(r"\n+", cleaned) if part.strip()])
            advice_count = len(re.findall(r"(记得|别忘|注意|小心|可以|要不要|最好|建议|带伞|喝点|早点|路上)", cleaned))
            if len(cleaned) > casual_limit or sentence_count >= 4 or paragraph_count >= 2:
                flags.append("casual_overexplained")
            inbound_weather = re.search(r"(雨|下雨|变天|天气|降温|冷|热|风)", inbound_text)
            reply_weather = re.search(r"(雨|天气|伞|降温|冷|热|风|外面|出门)", cleaned)
            if inbound_weather and reply_weather and (len(cleaned) > min(casual_limit, 130) or advice_count >= 2):
                flags.append("weather_overexplained")
        if re.search(r"^(好的|当然|没问题|我理解|总结一下|以下是|首先|其次|最后)[，,：:]", cleaned):
            flags.append("assistant_tone")
        if re.search(r"(作为.*助手|AI|模型|系统|提示词|插件|后台|根据.*信息|我会从.*角度)", cleaned, re.IGNORECASE):
            flags.append("meta_or_assistant")
        if not is_help and re.search(r"^\s*(?:[-*]|\d+[.、])\s+", cleaned, re.MULTILINE) and len(cleaned) < 900:
            flags.append("over_structured")
        if re.search(r"(能量\s*\d+|关系站位|状态机|内部规划|用户意图|表达学习|陪伴记忆|本地陪伴画像)", cleaned):
            flags.append("leaks_internal")
        if self._response_has_invalid_current_time_anchor(cleaned):
            flags.append("invalid_current_time_anchor")
        if self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            flags.append("false_no_reply_claim")
        correction = self._active_private_fact_correction(user, inbound_text)
        if self._looks_like_private_fact_correction(inbound_text):
            flags.append("fact_attribution_after_correction")
        claims_user_prior_action = self._response_claims_user_prior_action(cleaned, user)
        inbound_claims_ownership = bool(
            re.search(r"(?:我|你|他|她|它|谁)[^。！？!?\n]{0,18}(?:上次|之前|先|说|提|想|拿|问|做|告诉|推荐|诱惑)", inbound_text)
        )
        if claims_user_prior_action and correction:
            flags.append("fact_attribution_after_correction")
        elif claims_user_prior_action and not inbound_claims_ownership and len(self._compact_repeat_text(inbound_text)) <= 32:
            flags.append("unverified_fact_attribution")
        if self._response_reverses_recent_proactive_media_ownership(cleaned, user, inbound_text):
            flags.append("proactive_media_ownership_reversal")
        if self._expression_style_review_enabled():
            flags.extend(self._expression_review_flags(cleaned, user))
        signature = self._proactive_topic_signature(cleaned)
        if runtime_persona_setting(self, "enable_passive_topic_suppression", True):
            for item in self._cleanup_recent_passive_topics(user):
                if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                    flags.append("repeated_topic")
                    break
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_sent = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if (
            last_message
            and not self._inbound_explicitly_requests_repeat(inbound_text)
            and self._text_repeats_recent_message(cleaned, last_message)
        ):
            if not last_sent or _now_ts() - last_sent <= runtime_persona_setting(
                self,
                "proactive_reply_context_hours",
                12,
            ) * 3600:
                flags.append("repeats_last_bot_message")
        return list(dict.fromkeys(flags))

    def _expression_review_flags(self, cleaned: str, user: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if re.search(r"[，,]\s*[。！？!?…~～]|[。！？!?]\s*[，,]|[，,]{2,}|[。！？!?]{3,}", cleaned):
            flags.append("unnatural_punctuation")
        if re.search(r"\b[A-Za-z]{2,}\b\s*[。！？!?]\s*\b[A-Za-z]{1,4}\b\s*[。！？!?]", cleaned):
            flags.append("unnatural_punctuation")
        if len(cleaned) <= 260:
            punct_count = len(re.findall(r"[，,。！？!?…~～]", cleaned))
            if punct_count >= max(7, len(cleaned) // 10):
                flags.append("expression_overfit")
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        phrases = self._expression_profile_phrases(profile, limit=8)
        compact_reply = self._compact_repeat_text(cleaned)
        copied = 0
        for phrase in phrases:
            compact_phrase = self._compact_repeat_text(phrase)
            if len(compact_phrase) >= 8 and compact_phrase in compact_reply:
                copied += 1
        if copied >= 2 or (copied >= 1 and self._expression_learning_mode() == "aggressive"):
            flags.append("copied_user_expression_sample")
        if re.search(r"(学你|像你说话|模仿你|你的口癖|你的语气)", cleaned):
            flags.append("leaks_internal")
        return flags

    @staticmethod
    def _compact_repeat_text(text: str) -> str:
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()
