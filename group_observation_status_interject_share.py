# -*- coding: utf-8 -*-
"""GroupObservationStatusInterjectShareMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 572 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import random
import re
import uuid
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .group_observation_shared import _persona_value
from .helpers import _group_link_message_context, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from typing import Any



class GroupObservationStatusInterjectShareMixin:
    """GroupObservationStatusInterjectShareMixin（从 GroupObservationMixin 拆出）。"""


    def _format_group_status(self, group: dict[str, Any]) -> str:
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        top_terms = []
        for item in slang[:12]:
            if (
                isinstance(item, dict)
                and item.get("term")
                and self._group_slang_term_is_promoted(group, item)
            ):
                top_terms.append(f"{item.get('term')}({item.get('count', 0)})")
        active_members = sorted(
            [(user_id, item) for user_id, item in members.items() if isinstance(item, dict)],
            key=lambda pair: _safe_int(pair[1].get("count"), 0, 0),
            reverse=True,
        )[:8]
        member_text = "、".join(
            f"{self._group_member_identity_name(str(item.get('user_id') or item.get('sender_id') or user_id), item.get('identity_name') or item.get('name'), limit=16)}({item.get('count', 0)})"
            for user_id, item in active_members
        )
        group_id = _single_line(group.get("group_id"), 80)
        llm_blocked = bool(group_id and self._group_llm_reply_blocked(group_id))
        global_enabled = bool(_persona_value(self, "enable_group_companion", False))
        group_enabled = bool(group.get("enabled", True))
        allowed_by_mode = bool(group_id and self._group_allowed_by_access_mode(group_id))
        effective_enabled = global_enabled and group_enabled and allowed_by_mode
        if not global_enabled:
            effective_reason = "群聊陪伴总开关关闭"
        elif not group_enabled:
            effective_reason = "本群单独停用；可在群聊面板启用本群"
        elif not allowed_by_mode:
            effective_reason = "当前群未被名单模式放行"
        else:
            effective_reason = "总开关、本群开关和名单均已生效"
        return (
            f"群聊陪伴最终状态：{'开启' if effective_enabled else '关闭'}\n"
            f"群聊陪伴总开关：{'开启' if global_enabled else '关闭'}\n"
            f"本群单独开关：{'开启' if group_enabled else '关闭'}\n"
            f"名单放行：{'是' if allowed_by_mode else '否'}\n"
            f"状态说明：{effective_reason}\n"
            f"本群 LLM 回复：{'关闭' if llm_blocked else '开启'}\n"
            f"访问模式：{'黑名单' if _persona_value(self, 'group_access_mode', 'whitelist') == 'blacklist' else '白名单'}\n"
            f"群号：{group_id}\n"
            f"累计观察：{group.get('message_count', 0)} 条\n"
            f"气氛：{atmosphere.get('pace', '未知')}｜{atmosphere.get('mood', '平稳')}\n"
            f"常见词/梗：{'、'.join(top_terms) if top_terms else '暂无'}\n"
            f"活跃群友：{member_text or '暂无'}\n"
            f"当前话题：{_single_line(self._format_group_topic_threads_for_prompt(group), 180) or '暂无'}\n"
            f"群友互动图：{_single_line(self._format_group_relationship_graph_for_prompt(group), 180) or '暂无'}\n"
            f"插话反馈：{self._format_group_interjection_feedback(group)}"
        )

    def _format_group_interjection_feedback(self, group: dict[str, Any]) -> str:
        feedback = group.get("interjection_feedback")
        if not isinstance(feedback, dict) or not feedback:
            return "暂无"
        return (
            f"后续回复 {feedback.get('replies_after', 0)}｜"
            f"正向 {feedback.get('positive', 0)}｜负向 {feedback.get('negative', 0)}"
        )

    def _clean_group_interjection_reply(self, value: Any) -> str:
        text = _single_line(value, 80)
        text = re.sub(r"^```(?:text)?|```$", "", text).strip()
        text = text.strip("\"'“”‘’` ")
        compact = re.sub(r"\s+", "", text)
        unwrapped = re.sub(
            r"^[\s\"'“”‘’`(\（\[\【<《「『]+|[\s\"'“”‘’`)\）\]\】>》」』。.!！?？~～…、，,;；:：]+$",
            "",
            compact,
        )
        silent_markers = {
            "空",
            "空字符串",
            "空内容",
            "留空",
            "无",
            "没有",
            "null",
            "none",
            "nil",
            "n/a",
            "不适合说话",
            "不说",
            "不回复",
            "无需回复",
            "不用回复",
            "不要回复",
            "别回复",
            "静默",
            "忽略",
        }
        if not text or compact in silent_markers or unwrapped in silent_markers:
            return ""
        if "空字符串" in unwrapped and len(unwrapped) <= 12:
            return ""
        if re.fullmatch(r"[.。…~～\s\"'“”‘’`-]{1,12}", text):
            return ""
        return text

    def _parse_group_interjection_decision(self, raw: Any) -> tuple[bool, str, str]:
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return (False, "", "invalid_json")
        raw_decision = str(
            payload.get("decision")
            or payload.get("action")
            or payload.get("status")
            or ""
        ).strip().lower()
        raw_should_reply = payload.get("should_reply", payload.get("reply", payload.get("speak")))
        if isinstance(raw_should_reply, str):
            should_text = raw_should_reply.strip().lower()
            should_reply = should_text in {"true", "1", "yes", "y", "reply", "speak", "send", "说", "回复", "发言", "接话"}
            explicit_no_reply = should_text in {"false", "0", "no", "n", "silent", "skip", "drop", "none", "不说", "不回复", "静默"}
        elif raw_should_reply is None:
            should_reply = raw_decision in {"reply", "speak", "send", "say", "接话", "回复", "发言"}
            explicit_no_reply = raw_decision in {"silent", "skip", "drop", "none", "no_reply", "no-reply", "不说", "不回复", "静默"}
        else:
            should_reply = bool(raw_should_reply)
            explicit_no_reply = not should_reply
        if raw_decision in {"silent", "skip", "drop", "none", "no_reply", "no-reply", "不说", "不回复", "静默"}:
            explicit_no_reply = True
        if explicit_no_reply:
            return (False, "", _single_line(payload.get("reason"), 80) or "model_skip")
        reply = self._clean_group_interjection_reply(
            payload.get("text")
            or payload.get("reply_text")
            or payload.get("message")
            or payload.get("content")
            or ""
        )
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if reply and callable(meta_leak_checker) and meta_leak_checker(reply):
            return (False, "", "review_meta_leak")
        return (bool(should_reply and reply), reply if should_reply else "", _single_line(payload.get("reason"), 80))

    def _group_interjection_allowed(self, group: dict[str, Any], text: str) -> tuple[bool, str]:
        if not _persona_value(self, "enable_group_interjection", False):
            return False, "群聊主动插话未开启"
        _, has_link_payload = _group_link_message_context(text)
        if has_link_payload:
            return False, "链接或分享内容不触发主动插话"
        max_daily_getter = getattr(self, "_effective_group_interject_max_daily", None)
        max_daily = max_daily_getter() if callable(max_daily_getter) else _safe_int(_persona_value(self, "group_interject_max_daily", 0), 0, 0)
        min_interval_getter = getattr(self, "_effective_group_interject_min_interval_minutes", None)
        min_interval = min_interval_getter() if callable(min_interval_getter) else _safe_float(_persona_value(self, "group_interject_min_interval_minutes", 0), 0, 0)
        if max_daily <= 0:
            return False, "群聊主动插话上限为 0"
        today = _today_key()
        if group.get("interject_day") != today:
            group["interject_day"] = today
            group["interject_today"] = 0
        limit_unlimited = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        if (
            not (callable(limit_unlimited) and limit_unlimited(max_daily))
            and _safe_int(group.get("interject_today"), 0, 0) >= max_daily
        ):
            return False, "今日群聊插话已达上限"
        if _now_ts() - _safe_float(group.get("last_interject_at"), 0) < min_interval * 60:
            return False, "群聊插话间隔太近"
        recent = self._filtered_group_recent_messages(group)
        current = recent[-1] if recent and isinstance(recent[-1], dict) else {}
        talking_to = str(current.get("talking_to") or "group") if isinstance(current, dict) else "group"
        if talking_to not in {"", "group", "bot"}:
            return False, "当前更像群友之间的一对一对话"
        if re.search(r"^\s*(?:@|回复|引用)", text):
            return False, "当前消息有明确对话对象"
        if re.search(r"(别插|别接|别吵|别回|闭嘴|别打断)", text):
            return False, "群友表达了不希望被打断"
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        mood = str(atmosphere.get("mood") or "")
        pace = str(atmosphere.get("pace") or "")
        if pace == "热闹" and mood not in {"玩笑", "求助"}:
            return False, "群聊太热闹,不抢话"
        probability_getter = getattr(self, "_cycle_group_interject_probability", None)

        def adjusted_probability(value: float) -> float:
            return probability_getter(value) if callable(probability_getter) else value

        if re.search(r"(有没有人|谁懂|救命|怎么回事|咋办)", text):
            return random.random() < adjusted_probability(0.055), "有开放式接话口"
        if re.search(r"(笑死|绷不住|太离谱)", text):
            return random.random() < adjusted_probability(0.018 if mood == "玩笑" else 0.008), "玩笑反应口"
        if mood == "玩笑":
            return random.random() < adjusted_probability(0.015), "玩笑气氛"
        if mood == "求助":
            return random.random() < adjusted_probability(0.035), "求助气氛"
        return False, "没有自然插话口"

    def _group_repeat_signature(self, text: str) -> str:
        cleaned = self._compact_repeat_text(text)
        # Platform adapters often collapse every image-only message to the
        # same visible placeholder. It is not a meaningful repeat signal:
        # distinct images must not accumulate under one signature.
        if cleaned in {
            "[图片]",
            "【图片】",
            "图片",
            "[语音]",
            "【语音】",
            "语音",
            "[视频]",
            "【视频】",
            "视频",
            "[文件]",
            "【文件】",
            "文件",
        }:
            return ""
        cleaned = re.sub(r"[!！?？。.,，~～…]+$", "", cleaned).strip()
        return cleaned

    def _format_group_share_action_context(self, user: dict[str, Any]) -> str:
        share = user.get("group_share_context")
        if not isinstance(share, dict):
            return ""
        age_seconds = self._group_share_age_seconds(share)
        if age_seconds > 3 * 3600:
            return ""
        recency_text = self._group_share_recency_label(share)
        group_id = _single_line(share.get("group_id"), 24)
        group_name = _single_line(share.get("group_name"), 80)
        speaker = _single_line(share.get("speaker"), 64) or "群友"
        text = _single_line(share.get("text"), 120)
        summary = _single_line(share.get("summary"), 220)
        topic_summary = _single_line(share.get("topic_summary"), 260)
        topic = _single_line(share.get("topic"), 60)
        participants = share.get("participants") if isinstance(share.get("participants"), list) else []
        participant_text = "、".join(_single_line(item, 64) for item in participants[:6] if _single_line(item, 64))
        window_minutes = _safe_int(share.get("window_minutes"), 0, 0)
        source_target = _single_line(share.get("source_talking_to_name"), 80)
        source_talking_to = _single_line(share.get("source_talking_to"), 40)
        if "addressed_to_bot" not in share:
            direction_text = "消息指向：旧候选没有保存可靠指向证据；不得据此声称有人艾特、寻找或评价 Bot。"
        elif bool(share.get("addressed_to_bot")):
            direction_text = "消息指向：结构化场景确认该消息对 Bot 说话。"
        elif source_talking_to and source_talking_to != "group":
            direction_text = f"消息指向：明确对群友 {source_target or source_talking_to}说话，不是对 Bot。"
        else:
            direction_text = "消息指向：面向整个群，没有证据表明在艾特、寻找或评价 Bot。"
        parts = [
            f"群聊分享线索：{group_name}（群号 {group_id}）" if group_name and group_id else (f"群聊分享线索：群 {group_id}" if group_id else "群聊分享线索"),
            f"发生时间：{recency_text}的一段群聊；超过 30 分钟不要写成刚刚/刚才",
            f"时间窗：约 {window_minutes} 分钟的一段群聊" if window_minutes else "",
            f"参与者：{participant_text}" if participant_text else "",
            "身份锚点：[QQ:...] 只用于内部区分群友,不要写进最终私聊消息。" if participant_text or speaker else "",
            f"这段话题发生了什么：{topic_summary}" if topic_summary else "",
            f"代表性片段：{speaker}: {text}" if text else "",
            f"话题推进样本：{summary}" if summary else "",
            f"话题钩子：{topic}" if topic else "",
            direction_text,
            "事实边界：昵称、群名、头像文字、表情符号和被艾特对象的名字只是身份信息，不能改写成群友对 Bot 的评价；只转述来源中能逐字或直接推出的事实。",
        ]
        return "\n".join(part for part in parts if part)

    def _remember_recent_group_share_snapshot(
        self,
        user: dict[str, Any],
        *,
        share_context: dict[str, Any] | None,
        shared_text: str,
        sent_at: float | None = None,
        delivery_umo: str = "",
    ) -> None:
        if not isinstance(user, dict) or not isinstance(share_context, dict):
            return
        delivered_at = _now_ts() if sent_at is None else sent_at
        user["last_group_share_snapshot"] = {
            "schema_version": 1,
            "group_id": _single_line(share_context.get("group_id"), 40),
            "group_name": _single_line(share_context.get("group_name"), 80),
            "kind": _single_line(share_context.get("kind"), 32),
            "topic": _single_line(share_context.get("topic"), 100),
            "speaker_id": _single_line(share_context.get("speaker_id"), 40),
            "speaker": _single_line(share_context.get("speaker"), 64),
            "source_text": _single_line(share_context.get("text"), 240),
            "summary": _single_line(share_context.get("summary"), 420),
            "topic_summary": _single_line(share_context.get("topic_summary"), 420),
            "addressed_to_bot": bool(share_context.get("addressed_to_bot")),
            "has_address_evidence": "addressed_to_bot" in share_context,
            "source_talking_to": _single_line(share_context.get("source_talking_to"), 40),
            "source_talking_to_name": _single_line(share_context.get("source_talking_to_name"), 80),
            "source_trigger": _single_line(share_context.get("source_trigger"), 40),
            "shared_text": _single_line(shared_text, 500),
            "delivery_umo": _single_line(delivery_umo, 180),
            "event_ts": _safe_float(share_context.get("event_ts"), 0),
            "sent_at": delivered_at,
            "expires_at": delivered_at + 12 * 3600,
        }

    @staticmethod
    def _group_share_followup_needs_source(inbound_text: str) -> bool:
        text = _single_line(inbound_text, 220)
        if not text:
            return False
        return bool(re.search(
            r"(哪个群|哪一个群|什么群|群里|群名|群号|具体|谁|哪位|哪个人|原话|说了什么|怎么说|聊天记录|翻.{0,4}记录|艾特|@|找你|找我|说你|说我|外星人)",
            text,
            flags=re.I,
        ))

    def _format_recent_group_share_snapshot_for_reply_prompt_section(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
        *,
        event_umo: str = "",
        now: float | None = None,
    ) -> PromptSection | None:
        if not isinstance(user, dict) or not self._group_share_followup_needs_source(inbound_text):
            return None
        check_now = _now_ts() if now is None else now
        delivery_umo = _single_line(event_umo, 180)
        snapshot = user.get("last_group_share_snapshot")
        if isinstance(snapshot, dict):
            expires_at = _safe_float(snapshot.get("expires_at"), 0)
            snapshot_umo = _single_line(snapshot.get("delivery_umo"), 180)
            if expires_at > check_now and not (snapshot_umo and delivery_umo and snapshot_umo != delivery_umo):
                speaker = re.sub(r"\s*\[QQ:[^\]]+\]\s*", "", _single_line(snapshot.get("speaker"), 64)).strip()
                source_target = re.sub(r"\s*\[QQ:[^\]]+\]\s*", "", _single_line(snapshot.get("source_talking_to_name"), 80)).strip()
                if not bool(snapshot.get("has_address_evidence")):
                    direction = "来源未保存可靠的消息指向，不能声称群友在艾特、寻找或评价 Bot。"
                elif bool(snapshot.get("addressed_to_bot")):
                    direction = "结构化场景确认该消息是对 Bot 说的。"
                elif _single_line(snapshot.get("source_talking_to"), 40) not in {"", "group"}:
                    direction = f"该消息明确对群友 {source_target or '另一名群友'}说，不是对 Bot。"
                else:
                    direction = "该消息面向整个群，没有证据表明在艾特、寻找或评价 Bot。"
                group_id = _single_line(snapshot.get("group_id"), 40)
                group_name = _single_line(snapshot.get("group_name"), 80)
                title = "最近一次群聊主动消息的事实来源"
                body = "\n".join(part for part in (
                    "用户正在追问你刚才主动提到的群聊。以下是成功发送前保存的来源快照；优先直接回答用户问的具体点，不要用含糊撒娇回避。",
                    f"你实际主动发送的正文：{_single_line(snapshot.get('shared_text'), 500)}" if snapshot.get("shared_text") else "",
                    f"来源群：{group_name}（群号 {group_id}）" if group_name and group_id else (f"来源群号：{group_id}" if group_id else "来源群名和群号均未可靠保存"),
                    f"来源成员：{speaker}" if speaker else "来源成员未可靠保存",
                    f"来源原文：{_single_line(snapshot.get('source_text'), 240)}" if snapshot.get("source_text") else "来源原文未可靠保存",
                    f"上下文摘要：{_single_line(snapshot.get('summary') or snapshot.get('topic_summary'), 420)}" if snapshot.get("summary") or snapshot.get("topic_summary") else "",
                    f"消息指向证据：{direction}",
                    "回答边界：只说快照能证明的群、成员、原话和指向关系。昵称、群名、头像文字、表情符号不等于别人对 Bot 的评价；若用户要求快照中没有的细节，优先调用可用的群聊查询工具，否则坦白说没有记清，绝不能补出人物、说法或事件。",
                ) if part)
                section = prompt_section(
                    key="group_share.reply_source",
                    title=title,
                    source="group_observation",
                    content=body,
                )
                return section

        last_reason = _single_line(user.get("last_proactive_reason"), 40)
        last_sent_at = _safe_float(user.get("last_proactive_sent_at"), 0)
        last_umo = _single_line(user.get("last_proactive_delivery_umo"), 180)
        max_age = min(max(1, _safe_int(_persona_value(self, "proactive_reply_context_hours", 12), 12, 1, 72)), 12) * 3600
        if (
            last_reason == "group_share"
            and last_sent_at > 0
            and 0 <= check_now - last_sent_at <= max_age
            and not (last_umo and delivery_umo and last_umo != delivery_umo)
        ):
            title = "群聊主动消息追问的事实边界"
            body = (
                "用户正在追问你前面主动提到的群聊，但这条旧消息没有保存可核验的群号、成员和原文快照。"
                "不要根据自己上一条说法继续补全，也不要猜‘哪个群、谁、艾特了谁、说了什么’；优先调用可用的群聊查询工具，"
                "仍查不到时就如实说明没有记清。"
            )
            section = prompt_section(
                key="group_share.reply_boundary",
                title=title,
                source="group_observation",
                content=body,
            )
            return section
        return None

    def _format_recent_group_share_snapshot_for_reply(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
        *,
        event_umo: str = "",
        now: float | None = None,
    ) -> str:
        section = self._format_recent_group_share_snapshot_for_reply_prompt_section(
            user,
            inbound_text,
            event_umo=event_umo,
            now=now,
        )
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _group_share_send_block_reason(self, user_id: str, user: dict[str, Any], *, now: float | None = None) -> str:
        if str(user.get("planned_proactive_reason") or "") != "group_share":
            return ""
        share = user.get("group_share_context")
        if not isinstance(share, dict):
            return "群聊分享上下文已失效"
        check_now = _now_ts() if now is None else now
        event_ts = self._group_share_event_ts(share)
        if event_ts <= 0 or check_now - event_ts > 3 * 3600:
            return "群聊分享候选已过期"
        group_id = _single_line(share.get("group_id"), 40)
        if not group_id:
            return "群聊分享缺少群号"
        groups = self.data.get("groups")
        group = groups.get(group_id) if isinstance(groups, dict) else None
        if not isinstance(group, dict):
            return "群聊记录不存在"
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        member = members.get(str(user_id)) if isinstance(members, dict) else None
        member_last_seen = _safe_float((member or {}).get("last_seen"), 0) if isinstance(member, dict) else 0
        if member_last_seen > event_ts:
            return f"用户已在群 {group_id} 重新发言（{self._format_elapsed(check_now - member_last_seen)}前）"
        if member_last_seen > 0 and check_now - member_last_seen < 8 * 3600:
            return f"用户距上次群发言不足 8 小时（{self._format_elapsed(check_now - member_last_seen)}前）"
        return ""

    def _format_group_wakeup_humanized_prompt(
        self,
        effect: dict[str, Any] | None,
        state: dict[str, Any] | None = None,
    ) -> str:
        section = self._format_group_wakeup_humanized_prompt_section(effect, state)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_group_wakeup_humanized_prompt_section(
        self,
        effect: dict[str, Any] | None,
        state: dict[str, Any] | None = None,
    ) -> PromptSection:
        body = ""
        if isinstance(effect, dict) and effect:
            state = state if isinstance(state, dict) else self.data.get("daily_state", {})
            energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
            mood = _single_line(state.get("mood_bias"), 40) if isinstance(state, dict) else "平稳"
            runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
            phase = _single_line(runtime.get("label") or runtime.get("phase"), 40)
            updates = effect.get("updates") if isinstance(effect.get("updates"), list) else []
            update_text = "；".join(_single_line(item, 60) for item in updates if _single_line(item, 60))
            strength_label = _single_line(effect.get("strength_label"), 24)
            fatigue = effect.get("fatigue") if isinstance(effect.get("fatigue"), dict) else {}
            fatigue_label = _single_line(fatigue.get("label"), 20)
            fatigue_line = f"唤醒疲劳：{fatigue_label}（{_safe_float(fatigue.get('value'), 0.0, 0.0):.1f}/{_safe_int(fatigue.get('limit'), 0, 0)}）\n" if fatigue_label else ""
            body = (
                f"当前状态：能量 {energy}/100｜情绪底色 {mood}" + (f"｜睡眠阶段 {phase}" if phase else "") + "\n"
                + (f"唤醒强度：{strength_label}\n" if strength_label else "")
                + fatigue_line
                + f"唤醒影响：{_single_line(effect.get('note'), 220)}\n"
                + (f"状态变化：{update_text}\n" if update_text else "")
                + "回复要求：把这当作真实状态反应来写,不要说关键词、概率、插件、触发规则；在群聊里短一点,像被自然叫到或被话题吸引。"
            )
        return prompt_section(
            key="group.wakeup_state",
            title="群聊唤醒与当前状态",
            source="group_observation",
            content=body,
        )

    def _maybe_add_worldbook_pending_observation(
        self,
        *,
        sender_id: str,
        sender_name: str,
        group_id: str,
        text: str,
        now: float | None = None,
    ) -> None:
        # Retired compatibility entrypoint: observed group messages update the
        # unified archive upstream, never a second Worldbook person profile.
        return
        user_id = str(sender_id or "").strip()
        if not user_id:
            return
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return
        profile = profiles.get(user_id)
        if not isinstance(profile, dict) or profile.get("enabled", True) is False:
            return
        signal = self._worldbook_pending_observation_signal(text)
        if not signal:
            return
        cleaned = signal["evidence"]
        now = now or _now_ts()
        last_at = _safe_float(profile.get("last_pending_observation_at"), 0)
        if last_at and now - last_at < 12 * 3600:
            return
        pending = profile.setdefault("pending_observations", [])
        if not isinstance(pending, list):
            pending = []
            profile["pending_observations"] = pending
        evidence = cleaned
        evidence_key = self._worldbook_pending_observation_key(evidence)
        for item in pending:
            if not isinstance(item, dict):
                continue
            existing_key = self._worldbook_pending_observation_key(item.get("evidence") or item.get("content"))
            if existing_key and (existing_key == evidence_key or existing_key in evidence_key or evidence_key in existing_key):
                item["count"] = _safe_int(item.get("count"), 1, 1) + 1
                item["updated_at"] = now
                profile["last_pending_observation_at"] = now
                return
        identity_name = _single_line(profile.get("name") or sender_name or user_id, 40)
        pending.insert(
            0,
            {
                "id": uuid.uuid4().hex[:12],
                "title": signal["title"],
                "content": f"{identity_name} 在群聊中提到或表现出：{evidence}",
                "evidence": evidence,
                "group_id": _single_line(group_id, 40),
                "source": "group_observation",
                "weight": signal["weight"],
                "count": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        del pending[24:]
        profile["last_pending_observation_at"] = now

    def _worldbook_pending_observation_signal(self, text: str) -> dict[str, Any] | None:
        cleaned = _single_line(text, 140)
        if not (6 <= len(cleaned) <= 100):
            return None
        if cleaned.startswith(("/", "!", "！", "#")) or re.fullmatch(r"[\W_]+", cleaned):
            return None
        if re.search(r"(https?://|www\.|BV[0-9A-Za-z]{8,}|av\d{4,}|\[图片\]|\[语音\]|\[转发消息\])", cleaned, re.I):
            return None
        if re.search(r"(我是|你可以叫我|我是你|你爹|你爸|我是.*主人)", cleaned):
            return None
        if re.search(r"(?<!不要)(?<!别)(?<!不准)叫我", cleaned):
            return None
        if re.search(r"(胖次|内裤|脱下来|给你看|生理需求|起飞|开导|涩涩|色色)", cleaned):
            return None
        if re.fullmatch(r"(今天的?|明天的?|昨天的?|解决了|怎么做呢|好+|嗯+|啊+|草+|笑死|笨蛋|入土|入机)", cleaned):
            return None
        if re.search(r"[?？]$", cleaned) and re.search(r"(你|他|她|它|大家|有人|谁|什么|怎么|为啥|为什么)", cleaned):
            return None

        strong_patterns: tuple[tuple[str, int, str], ...] = (
            ("偏好/厌恶", 50, r"(喜欢|爱吃|爱看|爱玩|推|厨|不喜欢|讨厌|反感|雷|雷点|受不了|不能接受|不吃|过敏)"),
            ("互动边界", 55, r"(不要叫|别叫|不要提|别提|不想聊|不接受|介意|边界|底线|触雷|会破防)"),
            ("长期习惯", 45, r"(习惯|总是|经常|一直|长期|每天|常常|固定|作息|失眠|熬夜|早睡|晚睡)"),
            ("近期计划", 42, r"(最近在|正在|准备|打算|计划|以后想|想要|要开始|在学|学.*中|练.*中|项目|稿子|作业|考试|上课|上班|下班)"),
            ("重要状态", 45, r"(压力很大|压力大|焦虑|难过|生气|开心|累死|很累|困死|生病|发烧|住院|搬家|入职|离职|毕业)"),
        )
        for title, weight, pattern in strong_patterns:
            if re.search(pattern, cleaned):
                if re.search(r"^(今天|明天|昨天)[，,。 ]*(还行|一般|没啥|没事|解决了)?$", cleaned):
                    return None
                return {"title": title, "weight": weight, "evidence": cleaned}
        return None

    @staticmethod
    def _worldbook_pending_observation_key(value: Any) -> str:
        text = _single_line(value, 120).lower()
        text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
        return text[:80]

    def _looks_like_group_member_name(
        self,
        group: dict[str, Any],
        token: str,
        *,
        name_tokens: set[str] | None = None,
    ) -> bool:
        token = _single_line(token, 40)
        if not token:
            return False
        normalized = re.sub(r"\s+", "", token)
        resolved_name_tokens = name_tokens if isinstance(name_tokens, set) else self._group_member_name_tokens(group)
        if token in resolved_name_tokens or normalized in resolved_name_tokens:
            return True
        if len(normalized) >= 3:
            for name in resolved_name_tokens:
                compact_name = re.sub(r"\s+", "", name)
                if compact_name and (normalized == compact_name or normalized in compact_name or compact_name in normalized):
                    return True
        return False
