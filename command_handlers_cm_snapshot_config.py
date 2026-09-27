# -*- coding: utf-8 -*-
"""运行快照与配置规格域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 848 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from typing import Any



class CommandHandlersCmSnapshotConfigMixin:
    """运行快照与配置规格域（从 CommandHandlersMixin 拆出）。"""


    def _companion_manual_clean_multiline(self, value: Any, limit: int = 1800) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"```(?:json|text|markdown)?\s*", "", text, flags=re.I)
        text = text.replace("```", "")
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit].strip()

    def _companion_manual_clean_question_text(self, value: Any, limit: int = 260) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\[CQ:image,[^\]]+\]", " ", text, flags=re.I)
        text = re.sub(r"\[(?:图片|image|Image|IMAGE)\]", " ", text)
        text = re.sub(r"【(?:图片|image)】", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text)
        return _single_line(text, limit)

    def _companion_manual_current_group_note(self, event: AstrMessageEvent | None = None) -> str:
        group_id = ""
        if event is not None:
            try:
                group_id = self._extract_group_id_from_event(event)
            except Exception:
                group_id = ""
        if not group_id:
            return ""
        allowed = False
        try:
            allowed = bool(self._group_enabled_for_event(group_id))
        except Exception:
            allowed = False
        mode = _single_line(runtime_persona_setting(self, 'group_access_mode', ""), 20) or "unknown"
        return f"当前群：{group_id}｜群聊陪伴：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_companion', False))}｜名单模式：{mode}｜本群可用：{self._feature_on_text(allowed)}"

    def _companion_manual_setting_snapshot(self) -> list[str]:
        rest_probability = _safe_float(runtime_persona_setting(self, 'rest_reply_probability', 0.0), 0.0, 0.0)
        if rest_probability <= 1:
            rest_probability_text = f"{rest_probability * 100:.0f}%"
        else:
            rest_probability_text = f"{rest_probability:.0f}%"
        silence_confidence = _safe_float(runtime_persona_setting(self, 'smart_silence_min_confidence', 0.0), 0.0, 0.0)
        if silence_confidence <= 1:
            silence_confidence_text = f"{silence_confidence * 100:.0f}%"
        else:
            silence_confidence_text = f"{silence_confidence:.0f}%"
        reply_style = str(runtime_persona_setting(self, 'reply_style_prompt', "") or "").strip()
        command_photo_limit = self._command_photo_generation_daily_limit()
        if command_photo_limit < 0:
            command_photo_limit_text = "不限量（-1）"
        elif command_photo_limit == 0:
            command_photo_limit_text = "不允许（0）"
        else:
            command_photo_limit_text = f"{command_photo_limit} 次"
        return [
            f"群聊连续对话：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_conversation_followup', False))}，窗口 {runtime_persona_setting(self, 'group_conversation_followup_seconds', 0)} 秒，最多 {runtime_persona_setting(self, 'group_conversation_followup_max_turns', 0)} 轮",
            f"高强度收口：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_high_intensity_mode', False))}，{runtime_persona_setting(self, 'group_high_intensity_wakeup_window_seconds', 0)} 秒内 {runtime_persona_setting(self, 'group_high_intensity_wakeup_threshold', 0)} 次唤醒后持续 {runtime_persona_setting(self, 'group_high_intensity_cooldown_seconds', 0)} 秒",
            f"高强度合并：等待 {runtime_persona_setting(self, 'group_high_intensity_merge_seconds', 0)} 秒，范围 {runtime_persona_setting(self, 'group_high_intensity_merge_scope', 'group')}，最多 {runtime_persona_setting(self, 'group_high_intensity_max_merge_messages', 0)} 条",
            f"消息收口：{self._feature_on_text(runtime_persona_setting(self, 'enable_message_debounce', False))}，智能文本收口 {self._feature_on_text(runtime_persona_setting(self, 'enable_smart_message_debounce', False))}，文本最长等待 {runtime_persona_setting(self, 'text_message_debounce_max_wait_seconds', 0)} 秒",
            f"群聊唤醒增强：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_wakeup_enhancement', False))}，全局强唤醒词 {len(runtime_persona_setting(self, 'group_wakeup_direct_words', []) or [])} 个，主要用户专属强唤醒词 {len(runtime_persona_setting(self, 'group_wakeup_owner_direct_words', []) or [])} 个，短唤醒补话等待 {runtime_persona_setting(self, 'group_wakeup_short_text_wait_seconds', 0)} 秒",
            f"休息回复闸门：{self._feature_on_text(runtime_persona_setting(self, 'enable_rest_reply_simulation', False))}，模式 {runtime_persona_setting(self, 'rest_reply_mode', 'probability')}，概率 {rest_probability_text}，模型阈值 {runtime_persona_setting(self, 'rest_reply_llm_threshold', 0)}，清醒宽限 {runtime_persona_setting(self, 'rest_reply_awake_grace_minutes', 0)} 分钟",
            f"繁忙回复闸门：{self._feature_on_text(runtime_persona_setting(self, 'enable_busy_reply_gate', False))}，私聊延迟 {runtime_persona_setting(self, 'busy_reply_min_delay_seconds', 60)}-{runtime_persona_setting(self, 'busy_reply_max_delay_seconds', 300)} 秒，群聊上限 12 秒，忙完后主动缓冲 {runtime_persona_setting(self, 'busy_reply_proactive_resume_buffer_minutes', 10)} 分钟",
            f"智能沉默：{self._feature_on_text(runtime_persona_setting(self, 'enable_smart_silence', True))}，模式 {runtime_persona_setting(self, 'smart_silence_judge_mode', 'boundary_only')}，置信度 {silence_confidence_text}，超时 {runtime_persona_setting(self, 'smart_silence_model_timeout_seconds', 0)} 秒",
            f"被动回复复核：{self._feature_on_text(runtime_persona_setting(self, 'enable_passive_response_review', runtime_persona_setting(self, 'enable_response_self_review', True)))}，模式 {runtime_persona_setting(self, 'passive_review_mode', runtime_persona_setting(self, 'response_review_mode', 'severe_only'))}，强度 {runtime_persona_setting(self, 'passive_review_strength', 'lenient')}，长度阈值 {runtime_persona_setting(self, 'response_review_max_chars', 260)} 字；框架异常文本外发拦截：{self._feature_on_text(runtime_persona_setting(self, 'enable_framework_error_leak_guard', True))}",
            f"主动消息终审：{self._feature_on_text(runtime_persona_setting(self, 'enable_proactive_message_review', True))}，模式 {runtime_persona_setting(self, 'proactive_review_mode', 'full')}，强度 {runtime_persona_setting(self, 'proactive_review_strength', 'lenient')}",
            f"用户请求生图：{self._feature_on_text(runtime_persona_setting(self, 'enable_user_requested_photo_generation', True))}，每日上限 {command_photo_limit_text}；非指令生图：{_single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', 'tool_first'), 24) or 'tool_first'}，规则快判{self._feature_on_text(runtime_persona_setting(self, 'enable_natural_language_photo_generation', False))}，每日上限 {runtime_persona_setting(self, 'natural_language_photo_generation_max_daily', 0)}",
            f"拟人状态：健康 {self._feature_on_text(runtime_persona_setting(self, 'enable_health_state', True))}，饥饿 {self._feature_on_text(runtime_persona_setting(self, 'enable_hunger_state', True))}，生理期 {self._feature_on_text(runtime_persona_setting(self, 'enable_cycle_state', True))}，强度 {runtime_persona_setting(self, 'humanized_state_intensity', 0)}",
            self._cycle_status_text(),
            f"回复风格：{'已配置' if reply_style else '未配置'}，长度 {len(reply_style)} 字",
        ]

    def _cycle_status_text(self) -> str:
        """Build the six-phase cycle position line for the status overview."""
        if not self._advanced_cycle_enabled():
            return "六阶段周期：未开启"
        try:
            runtime = self._advanced_cycle_runtime()
        except Exception:
            runtime = {}
        if not runtime or not runtime.get("phase_name"):
            return "六阶段周期：已开启（尚未进入首个周期）"
        parts = [
            f"{runtime.get('phase_name')} 第{runtime.get('day_in_phase', 1)}/{runtime.get('phase_days', 1)}天",
            f"周期第{runtime.get('cycle_day', 0)}/{runtime.get('cycle_days', 0)}天",
        ]
        if runtime.get("next_phase_name"):
            parts.append(f"下一阶段{runtime.get('next_phase_name')}")
        try:
            discomfort = self._active_cycle_discomfort_conditions()
        except Exception:
            discomfort = []
        if isinstance(discomfort, list) and discomfort:
            labels = "、".join(
                _single_line(item.get("label") or item.get("type"), 40)
                for item in discomfort[:3]
                if isinstance(item, dict)
            )
            if labels:
                parts.append(f"当前不适：{labels}")
        return "六阶段周期：" + "，".join(parts)

    def _companion_manual_runtime_snapshot(self, event: AstrMessageEvent | None = None) -> str:
        lines: list[str] = []
        group_id = ""
        sender_id = ""
        if event is not None:
            try:
                group_id = self._extract_group_id_from_event(event)
            except Exception:
                group_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        try:
            sleep_delay = self._sleep_delay_override_state(clear_expired=True)
        except Exception:
            sleep_delay = {}
        if isinstance(sleep_delay, dict) and sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24) or "-"
            lines.append(
                "临时晚睡覆盖："
                f"到 {until_text}"
                f"｜来源={_single_line(sleep_delay.get('user_text'), 60) or '用户今晚陪聊约定'}"
                "｜过期后自动恢复日程休息"
            )
        if group_id:
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            group = groups.get(group_id) if isinstance(groups, dict) else None
            if isinstance(group, dict):
                try:
                    intensity = self._group_high_intensity_state(group, mutate=False)
                except Exception:
                    intensity = {}
                if isinstance(intensity, dict):
                    lines.append(
                        "当前群高强度："
                        f"{self._feature_on_text(intensity.get('active'))}"
                        f"｜原因={_single_line(intensity.get('reason'), 40) or '-'}"
                        f"｜近窗唤醒={_safe_int(intensity.get('recent_wakeups'), 0)}"
                        f"/{_safe_int(intensity.get('threshold'), 0)}"
                        f"｜剩余={_safe_float(intensity.get('remaining_seconds'), 0):.1f}s"
                    )
                active = group.get("active_bot_conversation") if isinstance(group.get("active_bot_conversation"), dict) else {}
                if active:
                    lines.append(
                        "当前群连续对话锚点："
                        f"sender={_single_line(active.get('sender_id'), 40) or '-'}"
                        f"｜turns={_safe_int(active.get('contextual_followups'), 0)}"
                        f"｜expires_in={max(0.0, _safe_float(active.get('expires_at'), 0) - _now_ts()):.1f}s"
                        f"｜last={_single_line(active.get('last_text'), 80) or '-'}"
                    )
                last_wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
                if last_wakeup:
                    lines.append(
                        "最近群唤醒："
                        f"{_single_line(last_wakeup.get('type'), 30) or '-'}"
                        f"｜{_single_line(last_wakeup.get('reason_label'), 60) or _single_line(last_wakeup.get('reason'), 60) or '-'}"
                        f"｜sender={_single_line(last_wakeup.get('sender_id'), 40) or '-'}"
                        f"｜text={_single_line(last_wakeup.get('text'), 80) or '-'}"
                    )
                recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
                recent_lines = []
                for item in recent[-5:]:
                    if not isinstance(item, dict):
                        continue
                    who = _single_line(item.get("identity_name") or item.get("name") or item.get("sender_id"), 24)
                    msg = _single_line(item.get("text"), 80)
                    if msg:
                        recent_lines.append(f"{who or '?'}: {msg}")
                if recent_lines:
                    lines.append("最近群消息：" + " / ".join(recent_lines))
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        if isinstance(user, dict):
            lines.append(
                "当前用户状态："
                f"enabled={self._feature_on_text(user.get('enabled', True))}"
                f"｜role={_single_line(user.get('role'), 30) or '-'}"
                f"｜ignored={_safe_int(user.get('ignored_streak'), 0)}"
                f"｜last_seen={self._format_timestamp_elapsed(user.get('last_seen')) if callable(getattr(self, '_format_timestamp_elapsed', None)) else _single_line(user.get('last_seen'), 30)}"
            )
        debounce = data.get("smart_message_debounce") if isinstance(data.get("smart_message_debounce"), dict) else {}
        logs = debounce.get("recent_logs") if isinstance(debounce.get("recent_logs"), list) else []
        if logs:
            compact_logs = []
            for item in logs[-4:]:
                if not isinstance(item, dict):
                    continue
                compact_logs.append(
                    f"{_single_line(item.get('chat'), 10) or '-'}:{_single_line(item.get('decision'), 20) or '-'}"
                    f"/{_single_line(item.get('outcome'), 24) or '-'}"
                    f"({_single_line(item.get('reason'), 40) or '-'})"
                )
            if compact_logs:
                lines.append("最近智能收口：" + " / ".join(compact_logs))
        passive = data.get("passive_no_reply_records") if isinstance(data.get("passive_no_reply_records"), dict) else {}
        if passive:
            reasons = []
            passive_items = passive.get("items") if isinstance(passive.get("items"), list) else []
            for item in passive_items[:5]:
                if isinstance(item, dict):
                    reasons.append(f"{_single_line(item.get('reason'), 40)}×{_safe_int(item.get('count'), 1)}")
            if reasons:
                lines.append("最近被动未回复：" + " / ".join(reasons))
        if isinstance(user, dict):
            backlog = user.get("rest_reply_backlog")
            if isinstance(backlog, list) and backlog:
                rest_items = []
                for item in backlog[-3:]:
                    if not isinstance(item, dict):
                        continue
                    text = _single_line(item.get("text"), 50) or "非文本消息"
                    reason = _single_line(item.get("reason"), 32) or "-"
                    rest_items.append(f"{text}({reason})")
                if rest_items:
                    lines.append("休息待补看私聊：" + " / ".join(rest_items))
        tests = data.get("troubleshooting_test_results") if isinstance(data.get("troubleshooting_test_results"), dict) else {}
        if tests:
            test_lines = []
            for key, item in list(tests.items())[-4:]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title") or key, 24)
                if bool(item.get("pending")):
                    status = "pending"
                else:
                    status = "ok" if bool(item.get("ok")) else "fail"
                detail = _single_line(item.get("error") or item.get("detail"), 48)
                test_lines.append(f"{title}:{status}{('/' + detail) if detail else ''}")
            if test_lines:
                lines.append("最近排障测试：" + " / ".join(test_lines))
        review_summary_getter = getattr(self, "_proactive_review_audit_summary", None)
        if callable(review_summary_getter):
            try:
                review_summary = review_summary_getter(window_days=7)
            except Exception:
                review_summary = {}
            if isinstance(review_summary, dict) and review_summary.get("total", 0):
                counts = review_summary.get("decision_counts") if isinstance(review_summary.get("decision_counts"), dict) else {}
                count_text = "/".join(
                    f"{_single_line(key, 16)}={_safe_int(value, 0)}"
                    for key, value in sorted(counts.items())
                )
                lines.append(f"近7天主动复核：{count_text or '-'}")
                outcomes = review_summary.get("reply_outcomes") if isinstance(review_summary.get("reply_outcomes"), dict) else {}
                if outcomes:
                    reply_rate = review_summary.get("reply_rate_24h")
                    rate_text = f"{reply_rate}%" if isinstance(reply_rate, (int, float)) else "待积累"
                    lines.append(
                        "主动回应（需回应路线）："
                        f"24h内回应={_safe_int(outcomes.get('replied_24h'), 0)}/"
                        f"未回应={_safe_int(outcomes.get('no_reply_24h'), 0)}/"
                        f"待观察={_safe_int(outcomes.get('pending'), 0)}，回应率={rate_text}"
                    )
                fallback_count = _safe_int(review_summary.get("consecutive_fallback_releases"), 0)
                if fallback_count >= 10:
                    lines.append(f"主动复核告警：模型已连续放行 {fallback_count} 条原文，请检查 RESPONSE_REVIEW_PROVIDER_ID")
        recent_photos = data.get("recent_photo_generations") if isinstance(data.get("recent_photo_generations"), list) else []
        photo_lines = []
        for item in recent_photos[:2]:
            if not isinstance(item, dict):
                continue
            kind = _single_line(item.get("kind"), 24) or "-"
            backend = _single_line(item.get("backend"), 36) or "-"
            status = "ok" if bool(item.get("ok")) else "fail"
            note = _single_line(item.get("note"), 48)
            reference = "ref" if bool(item.get("reference")) else "no-ref"
            prompt = _single_line(item.get("prompt"), 60)
            photo_lines.append(f"{kind}/{backend}/{status}/{reference}{('/' + note) if note else ''}{('｜' + prompt) if prompt else ''}")
        if photo_lines:
            lines.append("最近生图：" + " / ".join(photo_lines))
        return "\n".join(lines)

    def _companion_manual_config_specs(self) -> dict[str, dict[str, Any]]:
        return {
            "enable_group_companion": {"type": "bool", "label": "群聊陪伴总开关"},
            "enable_group_conversation_followup": {"type": "bool", "label": "群聊连续对话保持"},
            "group_conversation_followup_seconds": {"type": "int", "min": 0, "max": 600, "label": "群聊续接窗口秒数"},
            "group_conversation_followup_max_turns": {"type": "int", "min": 0, "max": 10, "label": "群聊连续续接上限"},
            "enable_group_high_intensity_mode": {"type": "bool", "label": "群聊高强度收口"},
            "group_high_intensity_wakeup_window_seconds": {"type": "int", "min": 15, "max": 600, "label": "高强度统计窗口秒数"},
            "group_high_intensity_wakeup_threshold": {"type": "int", "min": 2, "max": 20, "label": "高强度唤醒阈值"},
            "group_high_intensity_cooldown_seconds": {"type": "int", "min": 30, "max": 1800, "label": "高强度收口持续秒数"},
            "group_high_intensity_merge_seconds": {"type": "int", "min": 1, "max": 30, "label": "高强度合并等待秒数"},
            "group_high_intensity_max_merge_messages": {"type": "int", "min": 0, "max": 50, "label": "高强度最大合并消息数"},
            "group_high_intensity_merge_scope": {
                "type": "select",
                "choices": {"group", "same_user"},
                "aliases": {
                    "sender": "same_user",
                    "same_sender": "same_user",
                    "user": "same_user",
                    "同一用户": "same_user",
                    "同一发送者": "same_user",
                    "全群": "group",
                },
                "label": "高强度合并范围",
            },
            "enable_message_debounce": {"type": "bool", "label": "消息收口"},
            "enable_smart_message_debounce": {"type": "bool", "label": "智能文本收口"},
            "smart_message_debounce_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "智能收口等待秒数"},
            "text_message_debounce_seconds": {"type": "float", "min": 0.0, "max": 15.0, "label": "文本补话等待秒数"},
            "text_message_debounce_max_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "文本最长等待秒数"},
            "message_debounce_max_merge_messages": {"type": "int", "min": 0, "max": 30, "label": "最大合并消息数"},
            "enable_smart_silence": {"type": "bool", "label": "智能沉默"},
            "smart_silence_judge_mode": {
                "type": "select",
                "choices": {"boundary_only", "contextual"},
                "aliases": {
                    "边界": "boundary_only",
                    "明确边界": "boundary_only",
                    "保守": "boundary_only",
                    "上下文": "contextual",
                    "模型判断": "contextual",
                    "更智能": "contextual",
                    "智能": "contextual",
                },
                "label": "智能沉默判断模式",
            },
            "smart_silence_min_confidence": {"type": "percent", "min": 0.0, "max": 1.0, "label": "智能沉默最低置信度"},
            "smart_silence_model_timeout_seconds": {"type": "float", "min": 0.2, "max": 5.0, "label": "智能沉默模型超时秒数"},
            "enable_passive_response_review": {"type": "bool", "label": "被动回复复核"},
            "enable_framework_error_leak_guard": {"type": "bool", "label": "框架异常文本外发拦截"},
            "enable_proactive_message_review": {"type": "bool", "label": "主动消息终审"},
            "enable_proactive_burst": {"type": "bool", "label": "主动消息爆发式发送"},
            "proactive_burst_max_messages": {"type": "int", "min": 2, "max": 3, "label": "爆发式发送最多消息数"},
            "proactive_burst_gap_min_seconds": {"type": "int", "min": 10, "max": 600, "label": "爆发消息最小间隔秒数"},
            "proactive_burst_gap_max_seconds": {"type": "int", "min": 20, "max": 900, "label": "爆发消息最大间隔秒数"},
            "proactive_hour_activity_curve": {"type": "string", "max_len": 240, "label": "主动小时活跃曲线"},
            "passive_review_mode": {
                "type": "select",
                "choices": {"local_only", "severe_only", "full"},
                "aliases": {
                    "本地": "local_only",
                    "本地复核": "local_only",
                    "仅本地": "local_only",
                    "严重": "severe_only",
                    "严重问题": "severe_only",
                    "默认": "severe_only",
                    "完整": "full",
                    "全量": "full",
                    "积极": "full",
                },
                "label": "被动回复复核模式",
            },
            "passive_review_strength": {"type": "select", "choices": {"lenient", "balanced", "strict"}, "label": "被动回复复核强度"},
            "proactive_review_mode": {"type": "select", "choices": {"local_only", "severe_only", "full"}, "label": "主动消息终审模式"},
            "proactive_review_strength": {"type": "select", "choices": {"lenient", "balanced", "strict"}, "label": "主动消息终审强度"},
            "response_review_max_chars": {"type": "int", "min": 80, "max": 900, "label": "被动复核长度阈值"},
            "reply_style_prompt": {"type": "string", "max_len": 1200, "label": "回复风格约束"},
            "enable_group_wakeup_question": {"type": "bool", "label": "群聊解惑唤醒"},
            "group_wakeup_question_threshold": {"type": "int", "min": 0, "max": 100, "label": "解惑强度阈值"},
            "group_wakeup_short_text_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "短唤醒补话等待秒数"},
            "group_wakeup_cooldown_seconds": {"type": "int", "min": 0, "max": 3600, "label": "群聊唤醒冷却秒数"},
            "enable_rest_reply_simulation": {"type": "bool", "label": "休息回复闸门"},
            "rest_reply_mode": {
                "type": "select",
                "choices": {"probability", "llm"},
                "aliases": {
                    "概率": "probability",
                    "仅概率": "probability",
                    "概率模式": "probability",
                    "模型": "llm",
                    "模型判断": "llm",
                    "llm_judge": "llm",
                    "model": "llm",
                },
                "label": "休息回复闸门模式",
            },
            "rest_reply_probability": {"type": "percent", "min": 0.0, "max": 1.0, "label": "休息闸门概率"},
            "rest_reply_llm_threshold": {"type": "int", "min": 0, "max": 100, "label": "休息醒来模型阈值"},
            "rest_reply_awake_grace_minutes": {"type": "int", "min": 0, "max": 240, "label": "休息清醒宽限分钟"},
            "enable_rest_backlog_reply": {"type": "bool", "label": "醒后补看私聊"},
            "rest_backlog_max_messages": {"type": "int", "min": 1, "max": 12, "label": "醒后最多补看条数"},
            "enable_busy_reply_gate": {"type": "bool", "label": "繁忙回复闸门"},
            "busy_reply_min_delay_seconds": {"type": "int", "min": 0, "max": 900, "label": "繁忙回复最短延迟秒数"},
            "busy_reply_max_delay_seconds": {"type": "int", "min": 0, "max": 900, "label": "繁忙回复最长延迟秒数"},
            "busy_reply_proactive_resume_buffer_minutes": {"type": "int", "min": 0, "max": 120, "label": "忙完后主动缓冲分钟数"},
            "enable_health_state": {"type": "bool", "label": "健康/不适状态"},
            "enable_hunger_state": {"type": "bool", "label": "饥饿/胃口状态"},
            "enable_cycle_state": {"type": "bool", "label": "生理期模拟"},
            "humanized_state_intensity": {"type": "int", "min": 0, "max": 100, "label": "拟人状态强度"},
            "natural_language_photo_generation_mode": {
                "type": "select",
                "choices": {"tool_first", "rule_fast", "off"},
                "aliases": {
                    "工具": "tool_first",
                    "工具优先": "tool_first",
                    "tool": "tool_first",
                    "规则": "rule_fast",
                    "规则快判": "rule_fast",
                    "快判": "rule_fast",
                    "关闭": "off",
                    "关": "off",
                },
                "label": "非指令生图处理方式",
            },
            "enable_natural_language_photo_generation": {"type": "bool", "label": "允许规则快判生图/改图"},
            "enable_user_requested_photo_generation": {"type": "bool", "label": "允许用户请求生图"},
            "photo_generation_private_owner_max_daily": {"type": "int", "min": -1, "max": 100, "label": "主要用户私聊生图每日上限"},
            "photo_generation_private_friend_max_daily": {"type": "int", "min": -1, "max": 100, "label": "其他陪伴用户私聊生图每日上限"},
            "photo_generation_group_max_daily": {"type": "int", "min": -1, "max": 100, "label": "群聊生图每日上限"},
            "photo_generation_proactive_max_daily": {"type": "int", "min": -1, "max": 100, "label": "Bot 主动生图每日上限"},
            "command_photo_generation_max_daily": {"type": "int", "min": -1, "max": 100, "label": "用户请求生图每日上限"},
            "photo_generation_trace_max_size_kb": {"type": "int", "min": 0, "max": 102400, "label": "生图日志单文件大小（KB）"},
            "photo_generation_trace_backup_count": {"type": "int", "min": 0, "max": 20, "label": "生图日志轮转备份数"},
            "natural_language_photo_generation_max_daily": {"type": "int", "min": 0, "max": 100, "label": "规则快判生图每日上限"},
            "natural_language_photo_extra_prompt": {"type": "string", "max_len": 5000, "label": "规则快判生图附加提示词"},
            "enable_backup_external_image_api": {"type": "bool", "label": "启用备选在线图片 API"},
            "external_image_download_proxy": {"type": "string", "max_len": 500, "label": "在线图片结果下载代理"},
            "external_image_download_use_environment_proxy": {"type": "bool", "label": "在线图片下载继承环境代理"},
            "enable_photo_reference_image": {"type": "bool", "label": "启用人设/穿搭参考图一致性"},
            "external_image_api_platform": {
                "type": "select",
                "choices": {"auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "bailian", "modelscope", "doubao", "gemini"},
                "aliases": {
                    "openrouter": "openrouter",
                    "open-router": "openrouter",
                    "open_router": "openrouter",
                    "openrouter.ai": "openrouter",
                    "agnes-ai": "agnes",
                    "sapiens": "agnes",
                    "Agnes": "agnes",
                    "日日新": "sensenova",
                    "商汤日日新": "sensenova",
                    "百炼": "bailian",
                    "阿里云百炼": "bailian",
                    "魔搭": "modelscope",
                    "魔搭社区": "modelscope",
                    "豆包": "doubao",
                    "火山": "doubao",
                    "火山引擎": "doubao",
                    "seedream": "doubao",
                    "google": "gemini",
                    "谷歌": "gemini",
                    "openai兼容": "openai",
                    "minimaxi": "minimax",
                    "minimax-ai": "minimax",
                    "海螺": "minimax",
                    "海螺ai": "minimax",
                },
                "label": "在线生图平台",
            },
            "backup_external_image_api_platform": {
                "type": "select",
                "choices": {"auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "bailian", "modelscope", "doubao", "gemini"},
                "aliases": {
                    "openrouter": "openrouter",
                    "open-router": "openrouter",
                    "open_router": "openrouter",
                    "openrouter.ai": "openrouter",
                    "agnes-ai": "agnes",
                    "sapiens": "agnes",
                    "Agnes": "agnes",
                    "日日新": "sensenova",
                    "商汤日日新": "sensenova",
                    "百炼": "bailian",
                    "阿里云百炼": "bailian",
                    "魔搭": "modelscope",
                    "魔搭社区": "modelscope",
                    "豆包": "doubao",
                    "火山": "doubao",
                    "火山引擎": "doubao",
                    "seedream": "doubao",
                    "google": "gemini",
                    "谷歌": "gemini",
                    "openai兼容": "openai",
                    "minimaxi": "minimax",
                    "minimax-ai": "minimax",
                    "海螺": "minimax",
                    "海螺ai": "minimax",
                },
                "label": "备选在线生图平台",
            },
            "backup_external_image_api_timeout_seconds": {"type": "int", "min": 20, "max": 600, "label": "备选在线生图超时秒数"},
            "enable_qzone_comment_inbox": {"type": "bool", "label": "QQ 空间评论收件箱"},
            "qzone_comment_inbox_interval_minutes": {"type": "int", "min": 5, "max": 1440, "label": "空间评论检查间隔"},
            "qzone_comment_inbox_recent_posts": {"type": "int", "min": 1, "max": 20, "label": "空间评论扫描说说数"},
            "qzone_comment_inbox_max_replies_per_tick": {"type": "int", "min": 1, "max": 5, "label": "空间评论每轮最多回复"},
        }

    def _companion_manual_config_label(self, key: str) -> str:
        spec = self._companion_manual_config_specs().get(str(key or ""))
        if isinstance(spec, dict):
            return str(spec.get("label") or key)
        meta = self._companion_manual_config_display_meta().get(str(key or ""))
        return str(meta.get("label") or key) if isinstance(meta, dict) else str(key or "")

    def _companion_manual_config_display_meta(self) -> dict[str, dict[str, str]]:
        return {
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": {"label": "群聊连续对话判断模型", "location": "拓展页 -> 模型/Provider -> GROUP_FOLLOWUP_JUDGE_PROVIDER_ID"},
            "FAST_RESPONSE_PROVIDER_ID": {"label": "快速响应模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 快速响应模型"},
            "COMPLEX_REASONING_PROVIDER_ID": {"label": "复杂推理模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 复杂推理模型"},
            "CREATIVE_MODEL_PROVIDER_ID": {"label": "创作模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 创作模型"},
            "LLM_PROVIDER_ID": {"label": "插件主模型 Provider", "location": "拓展页 -> 模型/Provider -> LLM_PROVIDER_ID"},
            "MAI_STYLE_PROVIDER_ID": {"label": "风格/轻量任务模型", "location": "拓展页 -> 模型/Provider -> MAI_STYLE_PROVIDER_ID"},
            "PHOTO_MODEL_PROVIDER_ID": {"label": "生图模型感知 Provider", "location": "拓展页 -> 模型/Provider -> PHOTO_MODEL_PROVIDER_ID"},
            "PHOTO_PROMPT_PROVIDER_ID": {"label": "生图提示词模型", "location": "拓展页 -> 模型/Provider -> PHOTO_PROMPT_PROVIDER_ID"},
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": {"label": "主动人格判定模型", "location": "拓展页 -> 模型/Provider -> PROACTIVE_PERSONA_JUDGE_PROVIDER_ID"},
            "RESPONSE_REVIEW_PROVIDER_ID": {"label": "回复复核模型", "location": "拓展页 -> 模型/Provider -> RESPONSE_REVIEW_PROVIDER_ID"},
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": {"label": "智能收口小模型", "location": "拓展页 -> 模型/Provider -> SMART_MESSAGE_DEBOUNCE_PROVIDER_ID；也可在 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口 查看"},
            "SMART_SILENCE_PROVIDER_ID": {"label": "智能沉默模型", "location": "拓展页 -> 模型/Provider -> SMART_SILENCE_PROVIDER_ID；也可在 功能开关 -> 通用能力 -> 智能沉默 查看"},
            "TROUBLESHOOTING_PROVIDER_ID": {"label": "插件答疑/排障模型", "location": "拓展页 -> 模型/Provider -> TROUBLESHOOTING_PROVIDER_ID"},
            "enable_group_wakeup_enhancement": {"label": "群聊唤醒增强", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强"},
            "group_access_mode": {"label": "群聊访问模式", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊启用范围"},
            "group_wakeup_context_words": {"label": "群聊弱相关唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_direct_words": {"label": "群聊强唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_owner_direct_words": {"label": "主要用户专属强唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_interest_keywords": {"label": "群聊兴趣唤醒关键词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 兴趣唤醒"},
            "reply_style_prompt": {"label": "回复风格约束", "location": "拓展页 -> 世界知识/角色与表达 -> 回复风格约束；也可在配置页搜索 reply_style_prompt"},
            "enable_smart_silence": {"label": "智能沉默", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_judge_mode": {"label": "智能沉默判断模式", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_min_confidence": {"label": "智能沉默最低置信度", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_model_timeout_seconds": {"label": "智能沉默模型超时秒数", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "enable_passive_response_review": {"label": "被动回复复核", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核"},
            "enable_framework_error_leak_guard": {"label": "框架异常文本外发拦截", "location": "拓展页 -> 功能开关 -> 通用能力 -> 框架异常文本外发拦截"},
            "passive_review_mode": {"label": "被动回复复核模式", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "passive_review_strength": {"label": "被动回复复核强度", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "enable_proactive_message_review": {"label": "主动消息终审", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审"},
            "proactive_review_mode": {"label": "主动消息终审模式", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情"},
            "proactive_review_strength": {"label": "主动消息终审强度", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情"},
            "response_review_max_chars": {"label": "被动复核长度阈值", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "enable_rest_reply_simulation": {"label": "休息回复闸门", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_mode": {"label": "休息回复闸门模式", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_probability": {"label": "休息闸门概率", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_llm_threshold": {"label": "休息醒来模型阈值", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_awake_grace_minutes": {"label": "休息清醒宽限分钟", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_rest_backlog_reply": {"label": "醒后补看私聊", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_backlog_max_messages": {"label": "醒后最多补看条数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_busy_reply_gate": {"label": "繁忙回复闸门", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_min_delay_seconds": {"label": "繁忙回复最短延迟秒数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_max_delay_seconds": {"label": "繁忙回复最长延迟秒数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_proactive_resume_buffer_minutes": {"label": "忙完后主动缓冲分钟数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "enable_health_state": {"label": "健康/不适状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_hunger_state": {"label": "饥饿/胃口状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_cycle_state": {"label": "生理期模拟", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟"},
            "humanized_state_intensity": {"label": "拟人状态强度", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度"},
            "enable_photo_text_action": {"label": "生图/拍照能力", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力"},
            "enable_photo_reference_image": {"label": "参考图一致性", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 参考图一致性"},
            "photo_generation_backend": {"label": "生图后端", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 后端选择"},
            "external_image_api_platform": {"label": "在线生图平台", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "EXTERNAL_IMAGE_API_BASE_URL": {"label": "在线图片 API 地址", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "EXTERNAL_IMAGE_API_MODEL": {"label": "在线图片模型", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "external_image_download_proxy": {"label": "在线图片结果下载代理", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "external_image_download_use_environment_proxy": {"label": "在线图片下载继承环境代理", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "enable_backup_external_image_api": {"label": "启用备选在线图片 API", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_platform": {"label": "备选在线生图平台", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": {"label": "备选在线 API 地址", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": {"label": "备选在线图片模型", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_size": {"label": "备选在线生图尺寸", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_timeout_seconds": {"label": "备选在线生图超时秒数", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "photo_reference_catalog": {"label": "规范参考图目录", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 参考图库；也可用命令 陪伴 参考图 / 参考图库"},
            "enable_wardrobe": {"label": "启用角色衣柜", "location": "角色页 -> 角色衣柜；也可用命令 陪伴 衣柜"},
            "wardrobe_tendency": {"label": "整体服饰倾向", "location": "角色页 -> 角色衣柜 -> 整体服饰倾向"},
            "wardrobe_items": {"label": "具体衣物条目", "location": "角色页 -> 角色衣柜 -> 具体衣物；也可用命令 陪伴 衣柜 添加 / 添加图片"},
            "enable_wardrobe_prompt": {"label": "衣柜写入角色提示词", "location": "角色页 -> 角色衣柜"},
            "wardrobe_image_max_count": {"label": "衣柜单次识图上限", "location": "角色页 -> 角色衣柜 -> 识图设置"},
            "wardrobe_image_prompt": {"label": "衣柜识图描述提示词", "location": "角色页 -> 角色衣柜 -> 识图设置 -> 衣物描述提示词"},
            "WARDROBE_VISION_PROVIDER_ID": {"label": "衣柜识图模型", "location": "角色页 -> 角色衣柜 -> 识图设置；也可在 模型配置 -> 视觉与外界信息 里选择"},
            "natural_language_photo_generation_mode": {"label": "非指令生图处理方式", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图"},
            "natural_language_photo_extra_prompt": {"label": "规则快判生图附加提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图"},
            "photo_generation_prompt_format": {"label": "生图提示词表达方式", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_scene_presets": {"label": "生图场景预设", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_fixed_prompt": {"label": "全局固定生图提示词（兼容）", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_text2img_fixed_prompt": {"label": "文生图固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_selfie_fixed_prompt": {"label": "自拍/人像固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_edit_fixed_prompt": {"label": "改图固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "enable_qzone_integration": {"label": "QQ 空间联动", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动"},
            "enable_qzone_life_publish": {"label": "QQ 空间生活说说", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_max_daily": {"label": "每日自动说说上限", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_window_mode": {"label": "生活说说发布时段模式", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_windows": {"label": "生活说说自定义窗口", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_allow_insomnia_night": {"label": "失眠时允许凌晨说说", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_intra_day_gap_minutes": {"label": "同日说说最小间隔", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_similarity_threshold": {"label": "生活说说去重相似阈值", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "WEB_EXPLORATION_API_BASE_URL": {"label": "主动搜索接口地址", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_KEY": {"label": "主动搜索接口 API Key", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_MODEL": {"label": "主动搜索接口模型", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "max_daily_messages": {"label": "主动消息每日上限", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "min_interval_minutes": {"label": "主动消息最小间隔", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "enable_proactive_burst": {"label": "主动消息爆发式发送", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_max_messages": {"label": "爆发式发送最多消息数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_gap_min_seconds": {"label": "爆发消息最小间隔秒数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_gap_max_seconds": {"label": "爆发消息最大间隔秒数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_hour_activity_curve": {"label": "主动小时活跃曲线", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "quiet_hours": {"label": "主动免打扰时间", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "target_user_ids": {"label": "目标用户 ID 列表", "location": "拓展页 -> 模块 -> 快速启动 -> 部署与目标 -> 私聊服务对象 ID"},
            "REST_WAKEUP_PROVIDER_ID": {"label": "休息醒来判断模型", "location": "拓展页 -> 模型/Provider -> REST_WAKEUP_PROVIDER_ID"},
            "enable_companion_memory": {"label": "本地陪伴画像", "location": "拓展页 -> 功能开关 -> 本地画像、通用表达与习惯 -> 本地陪伴画像"},
            "enable_group_episode_memory": {"label": "群聊片段记忆", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊片段记忆"},
            "enable_group_privacy_guard": {"label": "群聊隐私保护", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊隐私保护"},
        }

    def _companion_manual_config_location(self, key: str) -> str:
        key = str(key or "").strip()
        locations = {
            "enable_group_companion": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关",
            "enable_group_conversation_followup": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "group_conversation_followup_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "group_conversation_followup_max_turns": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "enable_group_high_intensity_mode": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口",
            "group_high_intensity_wakeup_window_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_wakeup_threshold": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_cooldown_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_merge_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_max_merge_messages": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_merge_scope": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "enable_message_debounce": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖",
            "enable_smart_message_debounce": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口",
            "smart_message_debounce_wait_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口",
            "text_message_debounce_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "text_message_debounce_max_wait_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "message_debounce_max_merge_messages": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "enable_smart_silence": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_judge_mode": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_min_confidence": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_model_timeout_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "enable_passive_response_review": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核",
            "enable_framework_error_leak_guard": "拓展页 -> 功能开关 -> 通用能力 -> 框架异常文本外发拦截",
            "passive_review_mode": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "passive_review_strength": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "enable_proactive_message_review": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审",
            "proactive_review_mode": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情",
            "proactive_review_strength": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情",
            "response_review_max_chars": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "reply_style_prompt": "拓展页 -> 世界知识/角色与表达 -> 回复风格约束；也可在配置页搜索 reply_style_prompt",
            "enable_group_wakeup_question": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 解惑与冷群",
            "group_wakeup_question_threshold": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 解惑与冷群",
            "group_wakeup_short_text_wait_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 节流与拟人感",
            "group_wakeup_cooldown_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 节流与拟人感",
            "enable_rest_reply_simulation": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_mode": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_probability": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_llm_threshold": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_awake_grace_minutes": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "enable_rest_backlog_reply": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_backlog_max_messages": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "enable_busy_reply_gate": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_min_delay_seconds": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_max_delay_seconds": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_proactive_resume_buffer_minutes": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "enable_health_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_hunger_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_cycle_state": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟",
            "humanized_state_intensity": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度",
            "natural_language_photo_generation_mode": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "enable_natural_language_photo_generation": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "enable_user_requested_photo_generation": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "photo_generation_private_owner_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "photo_generation_private_friend_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "photo_generation_group_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "photo_generation_proactive_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "command_photo_generation_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图数量限制",
            "photo_generation_trace_max_size_kb": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图可观测日志",
            "photo_generation_trace_backup_count": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图可观测日志",
            "natural_language_photo_generation_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "natural_language_photo_extra_prompt": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "enable_qzone_comment_inbox": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_interval_minutes": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_recent_posts": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_max_replies_per_tick": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
        }
        meta = self._companion_manual_config_display_meta().get(key)
        if isinstance(meta, dict) and meta.get("location"):
            return str(meta.get("location"))
        return locations.get(key, "拓展页 -> 功能开关，搜索参数名或中文名")

    def _companion_manual_config_ref(self, key: str, *, include_location: bool = True) -> str:
        key = str(key or "").strip()
        if not key:
            return ""
        label = self._companion_manual_config_label(key)
        text = f"{label}（{key}）" if label and label != key else key
        if include_location:
            text = f"{text}｜位置：{self._companion_manual_config_location(key)}"
        return text

    def _companion_manual_mentioned_config_keys(self, text: str) -> list[str]:
        source = str(text or "")
        if not source:
            return []
        found: list[str] = []
        keys = set(self._companion_manual_config_specs()) | set(self._companion_manual_config_display_meta())
        for key in sorted(keys, key=len, reverse=True):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", source):
                found.append(key)
        labels: list[tuple[str, str]] = []
        for key in keys:
            label = self._companion_manual_config_label(key)
            if label and label != key:
                labels.append((key, label))
        for key, label in sorted(labels, key=lambda item: len(item[1]), reverse=True):
            if key not in found and label in source:
                found.append(key)
        return found

    def _companion_manual_config_aliases(self) -> dict[str, str]:
        aliases = {
            "群聊陪伴": "enable_group_companion",
            "连续对话保持": "enable_group_conversation_followup",
            "续接窗口": "group_conversation_followup_seconds",
            "连续对话窗口": "group_conversation_followup_seconds",
            "群聊续接窗口": "group_conversation_followup_seconds",
            "续接轮数": "group_conversation_followup_max_turns",
            "连续对话轮数": "group_conversation_followup_max_turns",
            "续接上限": "group_conversation_followup_max_turns",
            "高强度收口": "enable_group_high_intensity_mode",
            "高强度阈值": "group_high_intensity_wakeup_threshold",
            "高强度唤醒阈值": "group_high_intensity_wakeup_threshold",
            "高强度持续": "group_high_intensity_cooldown_seconds",
            "收口持续": "group_high_intensity_cooldown_seconds",
            "高强度合并等待": "group_high_intensity_merge_seconds",
            "合并等待": "group_high_intensity_merge_seconds",
            "高强度合并范围": "group_high_intensity_merge_scope",
            "合并范围": "group_high_intensity_merge_scope",
            "文本等待": "text_message_debounce_seconds",
            "文本补话等待": "text_message_debounce_seconds",
            "智能等待": "smart_message_debounce_wait_seconds",
            "智能收口等待": "smart_message_debounce_wait_seconds",
            "文本最长等待": "text_message_debounce_max_wait_seconds",
            "最大合并数": "message_debounce_max_merge_messages",
            "智能沉默": "enable_smart_silence",
            "智能静默": "enable_smart_silence",
            "智能沉默模式": "smart_silence_judge_mode",
            "沉默判断模式": "smart_silence_judge_mode",
            "沉默模型判断": "smart_silence_judge_mode",
            "沉默置信度": "smart_silence_min_confidence",
            "智能沉默置信度": "smart_silence_min_confidence",
            "沉默模型超时": "smart_silence_model_timeout_seconds",
            "智能沉默超时": "smart_silence_model_timeout_seconds",
            "回复复核": "enable_passive_response_review",
            "被动复核": "enable_passive_response_review",
            "框架异常拦截": "enable_framework_error_leak_guard",
            "异常文本外发拦截": "enable_framework_error_leak_guard",
            "主动复核": "enable_proactive_message_review",
            "复核模式": "passive_review_mode",
            "回复复核模式": "passive_review_mode",
            "主动复核模式": "proactive_review_mode",
            "被动复核阈值": "response_review_max_chars",
            "复核长度阈值": "response_review_max_chars",
            "回复风格": "reply_style_prompt",
            "回复风格约束": "reply_style_prompt",
            "表达风格": "reply_style_prompt",
            "简洁回复要求": "reply_style_prompt",
            "求助阈值": "group_wakeup_question_threshold",
            "解惑阈值": "group_wakeup_question_threshold",
            "短唤醒等待": "group_wakeup_short_text_wait_seconds",
            "休息闸门": "enable_rest_reply_simulation",
            "休息回复闸门": "enable_rest_reply_simulation",
            "睡眠闸门": "enable_rest_reply_simulation",
            "晚安不回": "enable_rest_reply_simulation",
            "休息模式": "rest_reply_mode",
            "休息闸门模式": "rest_reply_mode",
            "休息概率": "rest_reply_probability",
            "休息回复概率": "rest_reply_probability",
            "休息模型阈值": "rest_reply_llm_threshold",
            "醒来阈值": "rest_reply_llm_threshold",
            "清醒宽限": "rest_reply_awake_grace_minutes",
            "休息清醒宽限": "rest_reply_awake_grace_minutes",
            "醒后补看": "enable_rest_backlog_reply",
            "醒后补看条数": "rest_backlog_max_messages",
            "繁忙闸门": "enable_busy_reply_gate",
            "繁忙回复闸门": "enable_busy_reply_gate",
            "忙碌回复闸门": "enable_busy_reply_gate",
            "繁忙最短延迟": "busy_reply_min_delay_seconds",
            "繁忙最长延迟": "busy_reply_max_delay_seconds",
            "忙完主动缓冲": "busy_reply_proactive_resume_buffer_minutes",
            "繁忙主动缓冲": "busy_reply_proactive_resume_buffer_minutes",
            "健康状态": "enable_health_state",
            "不适状态": "enable_health_state",
            "饥饿状态": "enable_hunger_state",
            "饥饿模拟": "enable_hunger_state",
            "胃口状态": "enable_hunger_state",
            "生理期": "enable_cycle_state",
            "生理期模拟": "enable_cycle_state",
            "拟人状态强度": "humanized_state_intensity",
            "身体状态强度": "humanized_state_intensity",
            "非指令生图": "natural_language_photo_generation_mode",
            "自然语言生图": "natural_language_photo_generation_mode",
            "自然语言改图": "natural_language_photo_generation_mode",
            "规则快判生图": "enable_natural_language_photo_generation",
            "规则快判改图": "enable_natural_language_photo_generation",
            "主要用户私聊生图上限": "photo_generation_private_owner_max_daily",
            "主用户私聊生图上限": "photo_generation_private_owner_max_daily",
            "其他陪伴用户私聊生图上限": "photo_generation_private_friend_max_daily",
            "其他用户私聊生图上限": "photo_generation_private_friend_max_daily",
            "群聊生图上限": "photo_generation_group_max_daily",
            "群聊生图每日上限": "photo_generation_group_max_daily",
            "Bot主动生图上限": "photo_generation_proactive_max_daily",
            "Bot 主动生图上限": "photo_generation_proactive_max_daily",
            "用户生图上限": "command_photo_generation_max_daily",
            "用户请求生图上限": "command_photo_generation_max_daily",
            "指令生图上限": "command_photo_generation_max_daily",
            "陪伴生图上限": "command_photo_generation_max_daily",
            "允许用户请求生图": "enable_user_requested_photo_generation",
            "用户请求生图开关": "enable_user_requested_photo_generation",
            "生图日志大小": "photo_generation_trace_max_size_kb",
            "生图日志轮转数": "photo_generation_trace_backup_count",
            "生图日志备份数": "photo_generation_trace_backup_count",
            "自然生图上限": "natural_language_photo_generation_max_daily",
            "自然语言生图上限": "natural_language_photo_generation_max_daily",
            "规则快判生图上限": "natural_language_photo_generation_max_daily",
            "自然生图附加提示词": "natural_language_photo_extra_prompt",
            "自然语言生图附加提示词": "natural_language_photo_extra_prompt",
            "规则快判生图附加提示词": "natural_language_photo_extra_prompt",
            "文生图固定提示词": "photo_generation_text2img_fixed_prompt",
            "自拍固定提示词": "photo_generation_selfie_fixed_prompt",
            "人像固定提示词": "photo_generation_selfie_fixed_prompt",
            "改图固定提示词": "photo_generation_edit_fixed_prompt",
            "全局固定生图提示词": "photo_generation_fixed_prompt",
            "备选生图api": "enable_backup_external_image_api",
            "备选生图API": "enable_backup_external_image_api",
            "备选在线api": "enable_backup_external_image_api",
            "备选在线API": "enable_backup_external_image_api",
            "生图下载代理": "external_image_download_proxy",
            "图片下载代理": "external_image_download_proxy",
            "生图使用系统代理": "external_image_download_use_environment_proxy",
            "图片下载使用系统代理": "external_image_download_use_environment_proxy",
            "备选生图平台": "backup_external_image_api_platform",
            "生图平台": "external_image_api_platform",
            "备选生图超时": "backup_external_image_api_timeout_seconds",
            "空间评论收件箱": "enable_qzone_comment_inbox",
            "空间评论间隔": "qzone_comment_inbox_interval_minutes",
            "空间评论扫描数": "qzone_comment_inbox_recent_posts",
            "空间每轮回复数": "qzone_comment_inbox_max_replies_per_tick",
        }
        for key in self._companion_manual_config_specs():
            aliases[key] = key
            label = self._companion_manual_config_label(key)
            if label:
                aliases[label] = key
        return aliases

    def _companion_manual_config_key_from_alias(self, value: Any) -> str:
        text = str(value or "").strip()
        if text in self._companion_manual_config_specs():
            return text
        compact = re.sub(r"\s+", "", text).lower()
        for alias, key in self._companion_manual_config_aliases().items():
            if re.sub(r"\s+", "", str(alias or "")).lower() == compact:
                return key
        return ""

    def _companion_manual_config_keys_from_alias_text(self, value: Any, *, limit: int = 6) -> list[str]:
        compact = re.sub(r"\s+", "", str(value or "")).lower()
        if not compact:
            return []
        found: list[str] = []
        for alias, key in sorted(self._companion_manual_config_aliases().items(), key=lambda item: len(str(item[0])), reverse=True):
            alias_compact = re.sub(r"\s+", "", str(alias or "")).lower()
            if not alias_compact or len(alias_compact) < 2:
                continue
            if alias_compact in compact and key not in found:
                found.append(key)
                if len(found) >= limit:
                    break
        return found
