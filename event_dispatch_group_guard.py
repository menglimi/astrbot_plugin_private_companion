# -*- coding: utf-8 -*-
"""EventDispatchGroupGuardMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 755 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .event_dispatch_shared import _persona_feature_enabled, _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from datetime import datetime
from typing import Any



class EventDispatchGroupGuardMixin:
    """EventDispatchGroupGuardMixin（从 EventDispatchMixin 拆出）。"""


    def _group_active_conversation(self, group: dict[str, Any]) -> dict[str, Any]:
        active = group.setdefault("active_bot_conversation", {})
        if not isinstance(active, dict):
            active = {}
            group["active_bot_conversation"] = active
        return active

    def _group_air_guard_trim_bot_replies(self, group: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        current = _now_ts() if now is None else float(now)
        window = max(30, _safe_int(_persona_value(self, "group_air_guard_window_seconds", 180), 180, 30, 1800))
        raw = group.get("recent_bot_replies") if isinstance(group.get("recent_bot_replies"), list) else []
        return [
            item for item in raw
            if isinstance(item, dict) and current - _safe_float(item.get("ts"), 0) <= window
        ]

    def _group_air_guard_is_polite_terminal(self, text: Any) -> bool:
        cleaned = _single_line(text, 80).lower()
        if not cleaned:
            return False
        compact = re.sub(r"[\s，,。.!！?？~～…、（）()\[\]【】<>《》'\"“”‘’]+", "", cleaned)
        if not compact:
            return False
        polite_words = (
            "晚安", "安安", "睡了", "睡觉", "早点睡", "早安", "午安", "拜拜", "再见",
            "886", "88", "谢谢", "感谢", "辛苦了", "好梦", "做个好梦", "goodnight", "gn",
        )
        if any(word in compact for word in polite_words):
            return len(compact) <= 24
        return False

    def _group_air_guard_has_new_work_signal(self, text: Any) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        task_markers = (
            "帮我", "能不能", "可以", "怎么", "为什么", "咋", "如何", "看看", "查一下", "解释", "总结",
            "翻译", "写", "改", "发", "生成", "搜索", "谁", "哪里", "什么时候", "吗", "？", "?",
        )
        repair_markers = ("不对", "错了", "不是", "我说的是", "刚才", "你说", "你漏", "重新")
        return any(marker in compact for marker in task_markers) or any(marker in compact for marker in repair_markers)

    def _group_air_reply_guard_prompt_section(
        self,
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any],
        recent_bot_count: int,
        recent_bot_lines: str,
        recent_flow: str,
    ) -> PromptSection:
        return prompt_section(
            key="background.group_air_reply_guard",
            title="群聊继续回复判断",
            source="event_dispatch",
            template=(
                "判断群聊里 Bot 现在是否应该继续回复。只回答 REPLY 或 SILENCE，不要解释。\n\n"
                "优先 SILENCE 的情况：\n"
                "- 群里多个机器人/账号正在互相 @、互相引用或礼貌收尾，继续回只会循环。\n"
                "- 当前只是“晚安/早安/谢谢/拜拜/辛苦了”等收尾寒暄，而且 Bot 近期已经回过类似内容。\n"
                "- 话题已经自然结束、没有新的问题、任务或需要 Bot 承接的信息。\n\n"
                "可以 REPLY 的情况：\n"
                "- 当前明确提出新问题、给出新任务、纠正 Bot 事实错误，或需要 Bot 做具体处理。\n\n"
                "当前发言者：{sender}\n"
                "当前消息：{message}\n"
                "场景：trigger={trigger} reason={reason}\n"
                "窗口内 Bot 已回复次数：{recent_bot_count}\n"
                "Bot 近期回复：\n{recent_bot_lines}\n\n"
                "最近群聊：\n{recent_flow}"
            ),
            variables={
                "sender": self._group_member_identity_label(sender_id, sender_name, limit=24),
                "message": _single_line(text, 180),
                "trigger": _single_line(scene.get("trigger"), 40),
                "reason": _single_line(scene.get("reason"), 80),
                "recent_bot_count": str(recent_bot_count),
                "recent_bot_lines": recent_bot_lines,
                "recent_flow": recent_flow or "（无）",
            },
        )

    async def _group_air_reply_guard_decision(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any],
    ) -> dict[str, Any]:
        if not bool(_persona_value(self, "enable_group_air_reply_guard", True)):
            return {"block": False, "reason": "disabled"}
        if str(scene.get("talking_to") or "") != "bot":
            return {"block": False, "reason": "not_to_bot"}
        now = _now_ts()
        recent_bot = self._group_air_guard_trim_bot_replies(group, now=now)
        max_replies = max(1, _safe_int(_persona_value(self, "group_air_guard_max_bot_replies", 3), 3, 1, 20))
        has_new_work = self._group_air_guard_has_new_work_signal(text)
        if len(recent_bot) >= max_replies and not has_new_work:
            return {"block": True, "reason": "hard_limit", "recent_bot_replies": len(recent_bot)}

        current_polite = self._group_air_guard_is_polite_terminal(text)
        if current_polite:
            polite_limit = max(1, _safe_int(_persona_value(self, "group_air_guard_polite_loop_limit", 2), 2, 1, 10))
            polite_replies = [item for item in recent_bot if self._group_air_guard_is_polite_terminal(item.get("text"))]
            if len(polite_replies) >= polite_limit:
                return {"block": True, "reason": "polite_loop", "recent_polite_replies": len(polite_replies)}

        should_judge = current_polite or len(recent_bot) >= max(1, max_replies - 1)
        if not should_judge:
            return {"block": False, "reason": "low_risk"}
        provider_id = self._task_provider(
            _persona_value(self, "group_followup_judge_provider_id", ""),
            _persona_value(self, "response_review_provider_id", ""),
            _persona_value(self, "mai_style_provider_id", ""),
        )
        if not provider_id:
            return {"block": False, "reason": "no_provider"}
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = flow_formatter(group, sender_id=sender_id, text=text, max_lines=12, max_chars=1200) if callable(flow_formatter) else ""
        recent_bot_lines = "\n".join(
            f"- {self._format_ts_for_display(_safe_float(item.get('ts'), 0)) if hasattr(self, '_format_ts_for_display') else int(_safe_float(item.get('ts'), 0))}: {_single_line(item.get('text'), 120)}"
            for item in recent_bot[-6:]
        ) or "（无）"
        prompt = render_prompt_sections(
            [self._group_air_reply_guard_prompt_section(
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                scene=scene,
                recent_bot_count=len(recent_bot),
                recent_bot_lines=recent_bot_lines,
                recent_flow=recent_flow,
            )],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            raw = await self._llm_call(prompt, max_tokens=8, provider_id=provider_id, task="group_air_reply_guard")
        except Exception as exc:
            logger.debug("群聊读空气判断失败: %s", _single_line(exc, 120))
            return {"block": False, "reason": "judge_failed"}
        answer = str(raw or "").strip().upper()
        if answer.startswith("SILENCE") or answer.startswith("NO") or answer.startswith("否") or answer.startswith("沉默"):
            return {"block": True, "reason": "air_judge", "answer": answer[:40]}
        return {"block": False, "reason": "air_judge_reply", "answer": answer[:40]}

    async def _group_followup_llm_judge(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        active: dict[str, Any],
        scene: dict[str, Any],
    ) -> bool | None:
        provider_id = self._task_provider(_persona_value(self, "group_followup_judge_provider_id", ""))
        if not provider_id:
            return None
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=text, max_lines=10, max_chars=1200)
            if callable(flow_formatter)
            else ""
        )
        prompt = render_prompt_sections(
            [self._group_followup_judge_prompt_section(
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                active=active,
                scene=scene,
                recent_flow=recent_flow,
            )],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(
            prompt,
            max_tokens=8,
            provider_id=provider_id,
            task="group_followup_judge",
        )
        answer = str(raw or "").strip().upper()
        if answer.startswith("YES") or answer.startswith("是"):
            return True
        if answer.startswith("NO") or answer.startswith("否"):
            return False
        return None

    def _group_followup_judge_prompt_section(
        self,
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        active: dict[str, Any],
        scene: dict[str, Any],
        recent_flow: str,
    ) -> PromptSection:
        return prompt_section(
            key="background.group_followup_judge",
            title="群聊连续对话判断",
            source="event_dispatch",
            template=(
                "判断群聊里当前这句话是否仍然是在和 Bot 对话。\n\n"
                "只回答 YES 或 NO，不要解释。\n\n"
                "已知：\n"
                "- 上一次明确和 Bot 对话的人：{previous_sender}\n"
                "- 上一次明确对 Bot 说的话：{previous_message}\n"
                "- Bot 上一次回复：{previous_reply}\n"
                "- 当前发言者：{current_sender}\n"
                "- 当前发言者身份锚点：{identity_anchor}\n"
                "- 当前消息：{current_message}\n"
                "- 规则初判：trigger={trigger} talking_to={talking_to}\n\n"
                "真实最近群聊（按时间顺序，包含当前句；判断时必须参考整段聊天流，不要只看上一条唤醒消息）：\n"
                "{recent_flow}\n\n"
                "判断标准：\n"
                "- 如果当前消息是在承接 Bot 的回答、追问 Bot、纠正 Bot、继续问 Bot，回答 YES。\n"
                "- 如果当前消息明显转向群友、全群、第三人、另一个话题，回答 NO。\n"
                "- 如果中间有人插话，但当前消息仍明确指向 Bot，可以回答 YES。\n"
                "- 不要因为同一用户还在窗口内就直接 YES。\n"
                "- 方括号里的 QQ 是内部身份锚点；不同 QQ 即使外号相似也不是同一人。"
            ),
            variables={
                "previous_sender": self._group_member_identity_label(
                    str(active.get("sender_id") or sender_id),
                    active.get("sender_name"),
                    limit=24,
                ),
                "previous_message": _single_line(active.get("last_text"), 120),
                "previous_reply": _single_line(active.get("last_bot_reply"), 180) or "（未记录）",
                "current_sender": self._group_member_identity_label(sender_id, sender_name, limit=24),
                "identity_anchor": self._group_member_identity_anchor_note(
                    sender_id,
                    sender_name,
                    limit=120,
                ) or "无显示名冲突",
                "current_message": _single_line(text, 180),
                "trigger": _single_line(scene.get("trigger"), 40),
                "talking_to": _single_line(scene.get("talking_to"), 40),
                "recent_flow": recent_flow or "（无）",
            },
        )

    async def _group_message_is_bot_continuation(
        self,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        scene: dict[str, Any],
        text: str,
        *,
        allow_llm: bool = True,
    ) -> bool | None:
        if (
            not _persona_value(self, "enable_group_scene_awareness", False)
            or not _persona_value(self, "enable_group_conversation_followup", False)
            or _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0) <= 0
            or _safe_int(_persona_value(self, "group_conversation_followup_max_turns", 3), 3, 0) <= 0
        ):
            return False
        active = self._group_active_conversation(group)
        if str(active.get("sender_id") or "") != str(sender_id or ""):
            return False
        now = _now_ts()
        if _safe_float(active.get("expires_at"), 0) <= now:
            return False
        if str(scene.get("talking_to") or "") not in {"group", "bot"}:
            return False
        if str(scene.get("trigger") or "") in {"at_other", "reply_other", "at_all"}:
            return False
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        max_followups = _safe_int(_persona_value(self, "group_conversation_followup_max_turns", 3), 3, 0)
        followup_seconds = _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0)
        if _safe_int(active.get("contextual_followups"), 0, 0) >= max_followups:
            return False

        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        active_ts = _safe_float(active.get("last_ts"), 0)
        after_active = [
            item for item in recent[-12:]
            if isinstance(item, dict) and _safe_float(item.get("ts"), 0) >= active_ts
        ]
        short_interjection_checker = getattr(self, "_group_scene_short_interjection", None)
        other_after_active = [
            item for item in after_active
            if str(item.get("sender_id") or "") and str(item.get("sender_id") or "") != str(sender_id or "")
            and not (callable(short_interjection_checker) and short_interjection_checker(item.get("text")))
        ]
        seconds_since = now - active_ts if active_ts > 0 else 9999
        direct_markers = (
            "你", "妳", _persona_value(self, "bot_name", ""), "bot", "Bot", "刚才你", "你刚才", "你说", "你觉得", "你看",
            "那你", "问你", "回你", "跟你说", "不是说你", "不是问你", "你来", "按你说",
        )
        continuation_markers = (
            "所以", "那", "那我", "那你", "还有", "然后", "不过", "但是", "刚刚", "刚才",
            "这个", "这样", "怎么", "为什么", "可以吗", "行吗", "是不是", "对吗", "对吧",
            "是吧", "然后呢", "后来呢", "接着呢", "你呢", "所以呢", "你觉得", "？", "?",
        )
        redirect_markers = ("你们", "大家", "群里", "有人", "谁", "他", "她", "它", "他们", "她们")
        has_direct_cue = any(marker and marker in cleaned for marker in direct_markers)
        has_continuation_cue = any(marker in cleaned for marker in continuation_markers)
        looks_redirected_to_group = any(marker in cleaned for marker in redirect_markers) and not has_direct_cue

        if looks_redirected_to_group:
            return False
        if has_direct_cue:
            return True
        score_getter = getattr(self, "_group_implicit_reply_score", None)
        implicit_score = score_getter(cleaned) if callable(score_getter) else 0
        if seconds_since <= 45 and implicit_score >= 40:
            return True
        if other_after_active:
            if not allow_llm:
                return None
            judged = await self._group_followup_llm_judge(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                active=active,
                scene=scene,
            )
            if judged is not None:
                return judged
            return False
        if seconds_since <= 25 and has_continuation_cue:
            return True
        if seconds_since <= max(45, followup_seconds) and has_continuation_cue:
            if not allow_llm:
                return None
            judged = await self._group_followup_llm_judge(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                active=active,
                scene=scene,
            )
            return bool(judged) if judged is not None else False
        return False

    def _mark_group_bot_conversation(
        self,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        *,
        active: bool,
        text: str = "",
        contextual_followup: bool = False,
    ) -> None:
        store = self._group_active_conversation(group)
        followup_enabled = bool(_persona_value(self, "enable_group_conversation_followup", False))
        followup_seconds = _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0)
        max_followups = _safe_int(_persona_value(self, "group_conversation_followup_max_turns", 3), 3, 0)
        if not active or not followup_enabled or followup_seconds <= 0:
            if str(store.get("sender_id") or "") == str(sender_id or ""):
                store.clear()
            return
        now = _now_ts()
        previous_turns = _safe_int(store.get("contextual_followups"), 0, 0) if str(store.get("sender_id") or "") == str(sender_id or "") else 0
        contextual_turns = previous_turns + 1 if contextual_followup else 0
        if contextual_followup and contextual_turns >= max_followups:
            store.clear()
            return
        store.update(
            {
                "sender_id": str(sender_id or ""),
                "sender_name": _single_line(sender_name, 40),
                "last_ts": now,
                "last_text": _single_line(text, 120),
                "contextual_followups": contextual_turns,
                "message_count": _safe_int(group.get("message_count"), 0, 0),
                "expires_at": now + max(5, followup_seconds),
            }
        )

    def _refresh_group_bot_conversation_after_reply(
        self,
        group: dict[str, Any],
        sender_id: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Start the follow-up window from the confirmed Bot reply time."""
        if (
            not _persona_value(self, "enable_group_conversation_followup", False)
            or _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0) <= 0
            or _safe_int(_persona_value(self, "group_conversation_followup_max_turns", 3), 3, 0) <= 0
        ):
            return False
        store = self._group_active_conversation(group)
        if not store or str(store.get("sender_id") or "") != str(sender_id or ""):
            return False
        reply_ts = _now_ts() if now is None else float(now)
        store["last_ts"] = reply_ts
        store["last_bot_reply_ts"] = reply_ts
        store["expires_at"] = reply_ts + max(5, _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0))
        store["message_count"] = _safe_int(group.get("message_count"), 0, 0)
        return True

    async def _refresh_group_conversation_after_confirmed_send(self, event: AstrMessageEvent) -> None:
        if not bool(getattr(event, "_has_send_oper", False)):
            return
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("talking_to") or "") != "bot":
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return
        refreshed = False
        expires_in = 0.0
        async with self._data_lock:
            group = self._get_group(group_id)
            refreshed = self._refresh_group_bot_conversation_after_reply(group, sender_id)
            if refreshed:
                active = self._group_active_conversation(group)
                expires_in = max(0.0, _safe_float(active.get("expires_at"), 0) - _now_ts())
                self._save_data_sync(sections={"groups"})
        if refreshed:
            logger.info(
                "Bot 回复已确认发送，群聊续接窗口从实际回复时间重新计时: group=%s sender=%s window=%.1fs",
                group_id,
                sender_id,
                expires_in,
            )

    async def _mark_group_conversation_from_llm_request(self, event: AstrMessageEvent) -> None:
        group_enabled = _persona_feature_enabled(self, "enable_group_companion")
        if not group_enabled:
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return
        sender_name = _single_line(
            getattr(event, "private_companion_group_sender_name", "") or self._sender_display_name(event),
            40,
        )
        text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        scene = getattr(event, "private_companion_group_scene", None)
        async with self._data_lock:
            group = self._get_group(group_id)
            if not isinstance(scene, dict):
                scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            if str(scene.get("talking_to") or "") != "bot":
                return
            self._mark_group_bot_conversation(
                group,
                sender_id,
                sender_name,
                active=True,
                text=text,
                contextual_followup=bool(getattr(event, "private_companion_group_contextual_followup", False)),
            )
            self._save_data_sync(sections={"groups"})

    def _extract_group_id_from_event(self, event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return ""
        except Exception:
            pass
        if ":friendmessage:" in umo.casefold():
            return ""

        normalizer = getattr(self, "_normalize_group_identity_id", None)

        def normalize(value: Any) -> str:
            if callable(normalizer):
                return normalizer(value)
            if isinstance(value, (dict, list, tuple, set)):
                return ""
            text = _single_line(value, 160)
            if ":GroupMessage:" in text:
                text = _single_line(text.rsplit(":GroupMessage:", 1)[-1], 160)
            return text if text and ":" not in text else ""

        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                value = normalize(getter())
                if value:
                    return value
            except Exception:
                pass

        raw = self._event_raw_payload(event)
        for key in ("group_openid", "group_id", "group", "group_no", "group_uin"):
            value = normalize(raw.get(key))
            if value:
                return value
        if ":groupmessage:" in umo.casefold():
            value = normalize(umo)
            if value:
                return value
        message_obj = getattr(event, "message_obj", None)
        for attr in ("group_openid", "group_id", "group", "group_no", "group_uin"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            value = normalize(value)
            if value:
                return value
        message_type = str(raw.get("message_type") or raw.get("detail_type") or "").lower()
        event_message_type = getattr(event, "message_type", None)
        event_message_type_text = str(getattr(event_message_type, "name", event_message_type) or "").lower()
        is_group_hint = (
            message_type == "group"
            or event_message_type_text in {"group", "group_message", "messagetype.group"}
            or ":groupmessage:" in umo.casefold()
        )
        session_id = normalize(getattr(event, "session_id", ""))
        try:
            sender_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            sender_id = _single_line(raw.get("user_id") or raw.get("openid"), 160)
        if is_group_hint and session_id and session_id != sender_id:
            return session_id
        return ""

    def _sender_qq_nickname(self, event: AstrMessageEvent) -> str:
        """Read the account nickname without treating a group card as global identity."""
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj is not None else None
        raw_message = getattr(message_obj, "raw_message", None) if message_obj is not None else None
        sources = [sender]
        if isinstance(raw_message, dict):
            sources.append(raw_message.get("sender"))
        event_raw = getattr(event, "raw_message", None)
        if isinstance(event_raw, dict):
            sources.append(event_raw.get("sender"))
        for source in sources:
            if isinstance(source, dict):
                value = source.get("nickname")
            else:
                value = getattr(source, "nickname", None) if source is not None else None
            value = _single_line(value, 30)
            if value:
                return value
        getter = getattr(event, "get_sender_nickname", None)
        if callable(getter):
            try:
                return _single_line(getter(), 30)
            except Exception:
                pass
        return ""

    def _sender_display_name(self, event: AstrMessageEvent) -> str:
        for name in ("get_sender_name", "get_sender_nickname"):
            func = getattr(event, name, None)
            if callable(func):
                try:
                    value = _single_line(func(), 30)
                    if value:
                        return value
                except Exception:
                    pass
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj is not None else None
        for attr in ("nickname", "card", "name", "user_id"):
            value = getattr(sender, attr, None) if sender is not None else None
            if value:
                return _single_line(value, 30)
        try:
            return str(event.get_sender_id())
        except Exception:
            return "群友"

    def _event_components(self, event: AstrMessageEvent) -> list[Any]:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                value = getter()
                return list(value) if isinstance(value, (list, tuple)) else []
            except Exception:
                return []
        message_obj = getattr(event, "message_obj", None)
        value = getattr(message_obj, "message", None) if message_obj is not None else None
        return list(value) if isinstance(value, (list, tuple)) else []

    def _event_scene_signals(self, event: AstrMessageEvent) -> dict[str, Any]:
        self_id = self._event_self_id(event)
        at_targets: list[dict[str, str]] = []
        at_all = False
        reply_to_id = ""

        def _component_attr(comp: Any, names: tuple[str, ...]) -> str:
            for name in names:
                try:
                    value = getattr(comp, name, None)
                except Exception:
                    value = None
                if value is None:
                    continue
                if isinstance(value, dict):
                    for key in ("user_id", "qq", "id", "target", "name", "nickname", "card"):
                        nested = value.get(key)
                        if nested:
                            return str(nested).strip()
                    continue
                text = str(value or "").strip()
                if text:
                    return text
            return ""

        def _is_bot_at(user_id: str, name: str) -> bool:
            setting_getter = getattr(self, "persona_setting", None)
            bot_name = str(setting_getter("bot_name", "") if callable(setting_getter) else _persona_value(self, 'bot_name', "") or "").strip()
            clean_name = str(name or "").strip().lstrip("@")
            if self_id and user_id and user_id == self_id:
                return True
            if bot_name and clean_name and (clean_name == bot_name or bot_name in clean_name):
                return True
            return False

        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if class_name == "at" or class_name.endswith("at"):
                qq = _component_attr(comp, ("qq", "target", "user_id", "uin", "id", "at", "at_user", "target_id"))
                name = _single_line(
                    _component_attr(comp, ("name", "display_name", "nickname", "card", "text")) or qq,
                    40,
                )
                if qq.lower() == "all":
                    at_all = True
                    continue
                if qq or _is_bot_at(qq, name):
                    target_id = qq or self_id or "bot"
                    at_targets.append({"user_id": target_id, "name": name or target_id, "is_bot": _is_bot_at(target_id, name)})
            elif class_name == "atall":
                at_all = True
            elif class_name == "reply":
                value = _component_attr(comp, ("sender_id", "sender", "user_id", "target_id", "reply_to", "sender_uin"))
                if value:
                    reply_to_id = str(value).strip()
        return {"self_id": self_id, "at_targets": at_targets, "at_all": at_all, "reply_to_id": reply_to_id}

    def _event_at_user_ids(self, event: AstrMessageEvent) -> set[str]:
        ids: set[str] = set()
        for item in self._event_scene_signals(event).get("at_targets", []):
            if not isinstance(item, dict) or item.get("is_bot"):
                continue
            user_id = re.sub(r"\D+", "", str(item.get("user_id") or ""))
            if user_id:
                ids.add(user_id)
        raw_parts = [str(getattr(event, "message_str", "") or "")]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            raw_parts.append(str(getattr(message_obj, "raw_message", "") or ""))
        raw = "\n".join(raw_parts)
        for match in re.finditer(r"\[At:(\d+)\]|@(\d{5,})", raw):
            ids.add(match.group(1) or match.group(2))
        return ids

    def _group_resting_mention_notice(
        self,
        event: AstrMessageEvent,
        group: dict[str, Any],
        *,
        sender_id: str,
        now: float | None = None,
    ) -> tuple[str, str]:
        check_now = _now_ts() if now is None else now
        self_id = self._event_self_id(event)
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return "", ""
        for target_id in sorted(self._event_at_user_ids(event)):
            if not target_id or target_id == sender_id or target_id == self_id:
                continue
            user = users.get(target_id)
            if not isinstance(user, dict):
                continue
            if self._user_rest_kind(user) not in self._ACTIVE_REST_KINDS:
                continue
            rest_until = self._user_rest_silence_until(user, now=check_now)
            if rest_until <= check_now:
                continue
            log = group.setdefault("resting_at_notice_log", [])
            if not isinstance(log, list):
                log = []
                group["resting_at_notice_log"] = log
            kept = [
                item for item in log
                if isinstance(item, dict) and check_now - _safe_float(item.get("ts"), 0) <= 3600
            ]
            signature = f"{sender_id}:{target_id}"
            if any(
                str(item.get("signature") or "") == signature
                and check_now - _safe_float(item.get("ts"), 0) <= 10 * 60
                for item in kept
            ):
                group["resting_at_notice_log"] = kept
                return "", ""
            kept.append({"ts": check_now, "signature": signature, "sender_id": sender_id, "target_id": target_id})
            group["resting_at_notice_log"] = kept[-50:]
            target_name = _single_line(
                user.get("nickname")
                or user.get("last_display_name")
                or user.get("display_name")
                or target_id,
                24,
            )
            logger.info(
                "群聊 @ 休息用户提醒: group=%s sender=%s target=%s until=%s",
                self._extract_group_id_from_event(event),
                sender_id,
                target_id,
                datetime.fromtimestamp(rest_until).strftime("%m-%d %H:%M"),
            )
            return target_id, f"{target_name}现在在休息，晚点再叫他吧。"
        return "", ""

    def _event_priority(self, event: dict[str, Any]) -> tuple[int, float]:
        reason = str(event.get("reason") or "")
        action = str(event.get("action") or "")
        topic = _single_line(event.get("topic"), 40)
        window = str(event.get("window") or "")
        start, _ = self._parse_window_minutes(window)
        start_minutes = start if start is not None else 24 * 60
        priority = 0
        if reason == "morning_greeting":
            priority += 42 if self._is_sticky_greeting_event(event) else 10
        elif reason == "important_date_share":
            priority += 20
        elif reason == "noon_greeting":
            priority += 38 if self._is_sticky_greeting_event(event) else 10
        elif reason == "evening_greeting":
            priority += 28 if self._is_sticky_greeting_event(event) else 6
        elif reason in {"quiet_care", "state_share"}:
            priority += 12
        if action in {"poke", "voice"}:
            priority += 2
        if any(token in topic for token in ("早安", "起床", "赖床", "闹钟")):
            priority += 8
        return (-priority, start_minutes)

    def _chain_has_media_component(self, chain: list[Any]) -> bool:
        media_types = {"image", "record", "video", "file", "node", "forward"}
        for item in chain if isinstance(chain, list) else []:
            try:
                type_name = self._component_type_name(item)
            except Exception:
                type_name = str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()
            type_name = str(type_name or "").strip().lower()
            # AstrBot component enums stringify as ``ComponentType.File``.
            # Normalize them before deciding whether a result must retain its
            # complete MessageChain; otherwise file attachments degrade into a
            # text-only result on current framework versions.
            if "." in type_name:
                type_name = type_name.rsplit(".", 1)[-1]
            if type_name in media_types:
                return True
        return False
