# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import base64
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .constants import DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
from .helpers import _flat_get, _missing_optional_model_dependency, _now_ts, _safe_float, _safe_int, _set_into_config, _single_line, _today_key


_PHOTO_REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class CommandHandlersMixin:
    """Implementation bodies for command handlers registered in main.py."""

    def _feature_on_text(self, value: Any) -> str:
        return "开启" if bool(value) else "关闭"

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
        mode = _single_line(getattr(self, "group_access_mode", ""), 20) or "unknown"
        return f"当前群：{group_id}｜群聊陪伴：{self._feature_on_text(getattr(self, 'enable_group_companion', False))}｜名单模式：{mode}｜本群可用：{self._feature_on_text(allowed)}"

    def _companion_manual_setting_snapshot(self) -> list[str]:
        rest_probability = _safe_float(getattr(self, "rest_reply_probability", 0.0), 0.0, 0.0)
        if rest_probability <= 1:
            rest_probability_text = f"{rest_probability * 100:.0f}%"
        else:
            rest_probability_text = f"{rest_probability:.0f}%"
        silence_confidence = _safe_float(getattr(self, "smart_silence_min_confidence", 0.0), 0.0, 0.0)
        if silence_confidence <= 1:
            silence_confidence_text = f"{silence_confidence * 100:.0f}%"
        else:
            silence_confidence_text = f"{silence_confidence:.0f}%"
        reply_style = str(getattr(self, "reply_style_prompt", "") or "").strip()
        return [
            f"群聊连续对话：{self._feature_on_text(getattr(self, 'enable_group_conversation_followup', False))}，窗口 {getattr(self, 'group_conversation_followup_seconds', 0)} 秒，最多 {getattr(self, 'group_conversation_followup_max_turns', 0)} 轮",
            f"高强度收口：{self._feature_on_text(getattr(self, 'enable_group_high_intensity_mode', False))}，{getattr(self, 'group_high_intensity_wakeup_window_seconds', 0)} 秒内 {getattr(self, 'group_high_intensity_wakeup_threshold', 0)} 次唤醒后持续 {getattr(self, 'group_high_intensity_cooldown_seconds', 0)} 秒",
            f"高强度合并：等待 {getattr(self, 'group_high_intensity_merge_seconds', 0)} 秒，范围 {getattr(self, 'group_high_intensity_merge_scope', 'group')}，最多 {getattr(self, 'group_high_intensity_max_merge_messages', 0)} 条",
            f"消息收口：{self._feature_on_text(getattr(self, 'enable_message_debounce', False))}，智能文本收口 {self._feature_on_text(getattr(self, 'enable_smart_message_debounce', False))}，文本最长等待 {getattr(self, 'text_message_debounce_max_wait_seconds', 0)} 秒",
            f"群聊唤醒增强：{self._feature_on_text(getattr(self, 'enable_group_wakeup_enhancement', False))}，短唤醒补话等待 {getattr(self, 'group_wakeup_short_text_wait_seconds', 0)} 秒",
            f"休息回复闸门：{self._feature_on_text(getattr(self, 'enable_rest_reply_simulation', False))}，模式 {getattr(self, 'rest_reply_mode', 'probability')}，概率 {rest_probability_text}，模型阈值 {getattr(self, 'rest_reply_llm_threshold', 0)}，清醒宽限 {getattr(self, 'rest_reply_awake_grace_minutes', 0)} 分钟",
            f"智能沉默：{self._feature_on_text(getattr(self, 'enable_smart_silence', True))}，置信度 {silence_confidence_text}，超时 {getattr(self, 'smart_silence_model_timeout_seconds', 0)} 秒",
            f"回复复核：{self._feature_on_text(getattr(self, 'enable_response_self_review', True))}，模式 {getattr(self, 'response_review_mode', 'severe_only')}，被动长度阈值 {getattr(self, 'response_review_max_chars', 260)} 字",
            f"自然语言生图：{self._feature_on_text(getattr(self, 'enable_natural_language_photo_generation', False))}，每日上限 {getattr(self, 'natural_language_photo_generation_max_daily', 0)}",
            f"拟人状态：健康 {self._feature_on_text(getattr(self, 'enable_health_state', True))}，饥饿 {self._feature_on_text(getattr(self, 'enable_hunger_state', True))}，生理期 {self._feature_on_text(getattr(self, 'enable_cycle_state', True))}，强度 {getattr(self, 'humanized_state_intensity', 0)}",
            f"回复风格：{'已配置' if reply_style else '未配置'}，长度 {len(reply_style)} 字",
        ]

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
            "smart_silence_min_confidence": {"type": "percent", "min": 0.0, "max": 1.0, "label": "智能沉默最低置信度"},
            "smart_silence_model_timeout_seconds": {"type": "float", "min": 0.2, "max": 5.0, "label": "智能沉默模型超时秒数"},
            "enable_response_self_review": {"type": "bool", "label": "回复/主动复核"},
            "response_review_mode": {
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
                "label": "回复/主动复核模式",
            },
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
            "enable_health_state": {"type": "bool", "label": "健康/不适状态"},
            "enable_hunger_state": {"type": "bool", "label": "饥饿/胃口状态"},
            "enable_cycle_state": {"type": "bool", "label": "生理期模拟"},
            "humanized_state_intensity": {"type": "int", "min": 0, "max": 100, "label": "拟人状态强度"},
            "enable_natural_language_photo_generation": {"type": "bool", "label": "自然语言生图/改图"},
            "natural_language_photo_generation_max_daily": {"type": "int", "min": 0, "max": 100, "label": "自然语言生图每日上限"},
            "natural_language_photo_extra_prompt": {"type": "string", "max_len": 5000, "label": "自然语言生图附加提示词"},
            "enable_backup_external_image_api": {"type": "bool", "label": "启用备选在线图片 API"},
            "backup_external_image_api_platform": {
                "type": "select",
                "choices": {"auto", "openai", "bailian"},
                "aliases": {"百炼": "bailian", "阿里云百炼": "bailian", "openai兼容": "openai"},
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
            "TROUBLESHOOTING_PROVIDER_ID": {"label": "排障/答疑模型", "location": "拓展页 -> 模型/Provider -> TROUBLESHOOTING_PROVIDER_ID"},
            "enable_group_wakeup_enhancement": {"label": "群聊唤醒增强", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强"},
            "group_access_mode": {"label": "群聊访问模式", "location": "拓展页 -> 用户与群聊 -> 群聊名单/访问模式"},
            "group_wakeup_context_words": {"label": "群聊弱相关唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_direct_words": {"label": "群聊强唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_interest_keywords": {"label": "群聊兴趣唤醒关键词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 兴趣唤醒"},
            "reply_style_prompt": {"label": "回复风格约束", "location": "拓展页 -> 世界知识/角色与表达 -> 回复风格约束；也可在配置页搜索 reply_style_prompt"},
            "enable_smart_silence": {"label": "智能沉默", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_min_confidence": {"label": "智能沉默最低置信度", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_model_timeout_seconds": {"label": "智能沉默模型超时秒数", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "enable_response_self_review": {"label": "回复/主动复核", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情"},
            "response_review_mode": {"label": "回复/主动复核模式", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情"},
            "response_review_max_chars": {"label": "被动复核长度阈值", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情"},
            "enable_rest_reply_simulation": {"label": "休息回复闸门", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_mode": {"label": "休息回复闸门模式", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_probability": {"label": "休息闸门概率", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_llm_threshold": {"label": "休息醒来模型阈值", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_awake_grace_minutes": {"label": "休息清醒宽限分钟", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_rest_backlog_reply": {"label": "醒后补看私聊", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_backlog_max_messages": {"label": "醒后最多补看条数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_health_state": {"label": "健康/不适状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_hunger_state": {"label": "饥饿/胃口状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_cycle_state": {"label": "生理期模拟", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟"},
            "humanized_state_intensity": {"label": "拟人状态强度", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度"},
            "enable_photo_text_action": {"label": "生图/拍照能力", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力"},
            "photo_generation_backend": {"label": "生图后端", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 后端选择"},
            "external_image_api_platform": {"label": "在线生图平台", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "EXTERNAL_IMAGE_API_BASE_URL": {"label": "在线图片 API 地址", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "EXTERNAL_IMAGE_API_MODEL": {"label": "在线图片模型", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "enable_backup_external_image_api": {"label": "启用备选在线图片 API", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "backup_external_image_api_platform": {"label": "备选在线生图平台", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": {"label": "备选在线 API 地址", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": {"label": "备选在线图片模型", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "backup_external_image_api_size": {"label": "备选在线生图尺寸", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "backup_external_image_api_timeout_seconds": {"label": "备选在线生图超时秒数", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 备选在线图片 API"},
            "photo_persona_reference_image_path": {"label": "人设参考图路径", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 本地 ComfyUI；也可用命令 陪伴 参考图 设置"},
            "natural_language_photo_extra_prompt": {"label": "自然语言生图附加提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 自然语言生图/改图"},
            "photo_generation_scene_presets": {"label": "生图场景预设", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_fixed_prompt": {"label": "全局固定生图提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "enable_qzone_integration": {"label": "QQ 空间联动", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动"},
            "enable_qzone_life_publish": {"label": "QQ 空间生活说说", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "WEB_EXPLORATION_API_BASE_URL": {"label": "主动搜索接口地址", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_KEY": {"label": "主动搜索接口 API Key", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_MODEL": {"label": "主动搜索接口模型", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "max_daily_messages": {"label": "主动消息每日上限", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "min_interval_minutes": {"label": "主动消息最小间隔", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "proactive_review_strength": {"label": "主动发送前复核强度", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情"},
            "quiet_hours": {"label": "主动免打扰时间", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "target_user_ids": {"label": "目标用户 QQ 列表", "location": "拓展页 -> 用户与群聊 -> 私聊对象/目标用户"},
            "REST_WAKEUP_PROVIDER_ID": {"label": "休息醒来判断模型", "location": "拓展页 -> 模型/Provider -> REST_WAKEUP_PROVIDER_ID"},
            "enable_companion_memory": {"label": "用户陪伴记忆", "location": "拓展页 -> 功能开关 -> 记忆、表达与习惯 -> 用户陪伴记忆"},
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
            "smart_silence_min_confidence": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_model_timeout_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "enable_response_self_review": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情",
            "response_review_mode": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情",
            "response_review_max_chars": "拓展页 -> 功能开关 -> 私聊陪伴 -> 回复/主动复核详情",
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
            "enable_health_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_hunger_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_cycle_state": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟",
            "humanized_state_intensity": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度",
            "enable_natural_language_photo_generation": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 自然语言生图/改图",
            "natural_language_photo_generation_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 自然语言生图/改图",
            "natural_language_photo_extra_prompt": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 自然语言生图/改图",
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
            "沉默置信度": "smart_silence_min_confidence",
            "智能沉默置信度": "smart_silence_min_confidence",
            "沉默模型超时": "smart_silence_model_timeout_seconds",
            "智能沉默超时": "smart_silence_model_timeout_seconds",
            "回复复核": "enable_response_self_review",
            "主动复核": "enable_response_self_review",
            "复核模式": "response_review_mode",
            "回复复核模式": "response_review_mode",
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
            "健康状态": "enable_health_state",
            "不适状态": "enable_health_state",
            "饥饿状态": "enable_hunger_state",
            "饥饿模拟": "enable_hunger_state",
            "胃口状态": "enable_hunger_state",
            "生理期": "enable_cycle_state",
            "生理期模拟": "enable_cycle_state",
            "拟人状态强度": "humanized_state_intensity",
            "身体状态强度": "humanized_state_intensity",
            "自然语言生图": "enable_natural_language_photo_generation",
            "自然语言改图": "enable_natural_language_photo_generation",
            "自然生图上限": "natural_language_photo_generation_max_daily",
            "自然语言生图上限": "natural_language_photo_generation_max_daily",
            "自然生图附加提示词": "natural_language_photo_extra_prompt",
            "自然语言生图附加提示词": "natural_language_photo_extra_prompt",
            "备选生图api": "enable_backup_external_image_api",
            "备选生图API": "enable_backup_external_image_api",
            "备选在线api": "enable_backup_external_image_api",
            "备选在线API": "enable_backup_external_image_api",
            "备选生图平台": "backup_external_image_api_platform",
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

    def _companion_manual_issue_tags(self, query: str) -> set[str]:
        compact = re.sub(r"\s+", "", str(query or "")).lower()
        tags: set[str] = set()
        if not compact:
            return tags
        if any(word in compact for word in ("刚才", "这次", "刚刚", "上一条", "为什么没回", "为什么不回", "没有回复", "不回复", "没回复")):
            tags.add("recent")
        if any(word in compact for word in ("群聊", "群里", "群内", "群消息", "没@", "没at", "连续对话", "高强度", "收口", "唤醒", "插话", "碰瓷")):
            tags.add("group")
        if any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")):
            tags.add("followup")
        if any(word in compact for word in ("高强度", "收口", "合并", "压制")):
            tags.add("high_intensity")
        if any(word in compact for word in ("防抖", "智能收口", "补话", "等补充", "合并消息")):
            tags.add("debounce")
        if any(word in compact for word in ("休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "醒后补看")):
            tags.add("rest")
        if any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊")):
            tags.add("silence")
        if any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截")):
            tags.add("review")
        if any(word in compact for word in ("生图", "画图", "改图", "自拍", "参考图", "穿搭图", "出图", "提示词", "图片生成", "自然语言生图")):
            tags.add("photo")
        if any(word in compact for word in ("qq空间", "空间", "说说", "评论", "点赞", "cookie", "onebot", "登录空间")):
            tags.add("qzone")
        if any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "拟人状态")):
            tags.add("state")
        if any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格")):
            tags.add("style")
        if any(word in compact for word in ("模型", "provider", "llm", "超时", "timeout", "降级", "无有效json", "无效json")):
            tags.add("model")
        if any(word in compact for word in ("rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "知识图谱", "专属记忆", "未安装")):
            tags.add("memory")
        if any(word in compact for word in ("在哪", "哪里", "位置", "设置", "配置项", "怎么改", "如何改", "调参")):
            tags.add("location")
        return tags

    def _companion_manual_entry_tags(self, entry: dict[str, Any]) -> set[str]:
        title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
        if "被动消息为什么没回" in title or "被动未回复" in title:
            return {"recent"}
        if "连续对话" in title:
            return {"group", "followup"}
        if "高强度" in title:
            return {"group", "high_intensity"}
        if "收口" in title or "防抖" in title:
            return {"debounce"}
        if "唤醒" in title or "答疑误触" in title:
            return {"group"}
        if "休息" in title or "晚安" in title:
            return {"rest"}
        if "智能沉默" in title:
            return {"silence"}
        if "回复复核" in title or "去重" in title or "复读" in title:
            return {"review"}
        if "回复太长" in title or "字数限制" in title:
            return {"style"}
        if "模型" in title or "provider" in title:
            return {"model"}
        if "rememberyou" in title or "联动" in title:
            return {"memory"}
        if "主动消息" in title:
            return {"proactive"}
        if "拟人身体" in title or "饥饿" in title:
            return {"state"}
        if "生图" in title or "自拍" in title:
            return {"photo"}
        if "qq空间" in title or "空间" in title:
            return {"qzone"}
        if "群聊老是" in title:
            return {"group"}
        return set()

    def _companion_manual_current_config_value(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return _flat_get(getattr(self, "config", None), key, None)

    def _companion_manual_parse_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否"}:
            return False
        return None

    def _companion_manual_normalize_config_value(self, key: str, value: Any) -> tuple[bool, Any, str]:
        spec = self._companion_manual_config_specs().get(str(key or ""))
        if not isinstance(spec, dict):
            return False, None, f"不允许通过答疑命令修改配置项：{key}"
        kind = str(spec.get("type") or "string")
        try:
            if kind == "bool":
                parsed = self._companion_manual_parse_bool(value)
                if parsed is None:
                    return False, None, "布尔值请使用 开启/关闭、true/false、1/0。"
                return True, parsed, ""
            if kind == "int":
                parsed = int(float(str(value).strip()))
                parsed = max(int(spec.get("min", 0)), min(int(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "float":
                parsed = float(str(value).strip())
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "percent":
                text = str(value or "").strip().replace("%", "")
                parsed = float(text)
                if parsed > 1:
                    parsed = parsed / 100.0
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", 1.0)), parsed))
                return True, parsed, ""
            if kind == "select":
                text = str(value or "").strip().lower()
                aliases = spec.get("aliases") if isinstance(spec.get("aliases"), dict) else {}
                text = str(aliases.get(text, text))
                choices = spec.get("choices") if isinstance(spec.get("choices"), set) else set()
                if text not in choices:
                    return False, None, f"可选值只有：{', '.join(sorted(str(item) for item in choices))}"
                return True, text, ""
            if kind == "string":
                text = str(value or "").strip()
                max_len = _safe_int(spec.get("max_len"), 1200, 1)
                if len(text) > max_len:
                    text = text[:max_len].strip()
                return True, text, ""
        except (TypeError, ValueError):
            return False, None, f"{self._companion_manual_config_label(key)} 的值格式不对。"
        return False, None, f"不支持的配置类型：{kind}"

    def _companion_manual_values_equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return bool(left) == bool(right)
        try:
            return abs(float(left) - float(right)) < 0.0001
        except (TypeError, ValueError):
            return str(left) == str(right)

    def _companion_manual_format_config_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "开启" if value else "关闭"
        if isinstance(value, str) and len(value) > 120:
            return _single_line(value, 120)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _companion_manual_format_config_item_value(self, key: str, value: Any) -> str:
        if str(key or "") in {"rest_reply_probability", "smart_silence_min_confidence"}:
            try:
                number = float(value)
                percent = number * 100 if 0 <= number <= 1 else number
                return f"{percent:.0f}%"
            except (TypeError, ValueError):
                return self._companion_manual_format_config_value(value)
        return self._companion_manual_format_config_value(value)

    def _companion_manual_confidence_label(self, confidence: Any) -> str:
        score = _safe_float(confidence, 0.0, 0.0)
        if score >= 0.78:
            return "高"
        if score >= 0.55:
            return "中"
        return "低"

    def _companion_manual_add_proposal(
        self,
        proposals: list[dict[str, Any]],
        key: str,
        value: Any,
        reason: str,
        *,
        evidence: list[str] | None = None,
        strength: str = "可尝试",
        confidence: float = 0.62,
    ) -> None:
        if any(item.get("key") == key for item in proposals):
            return
        ok, normalized, error = self._companion_manual_normalize_config_value(key, value)
        if not ok:
            logger.debug("[PrivateCompanion] 答疑可执行建议被跳过: key=%s error=%s", key, _single_line(error, 120))
            return
        old = self._companion_manual_current_config_value(key)
        if self._companion_manual_values_equal(old, normalized):
            return
        evidence_lines = [
            _single_line(item, 150)
            for item in (evidence or [])
            if _single_line(item, 150)
        ]
        current_evidence = f"当前 {key}={self._companion_manual_format_config_item_value(key, old)}"
        if not any(str(item).startswith(f"当前 {key}=") for item in evidence_lines):
            evidence_lines.insert(0, current_evidence)
        proposals.append(
            {
                "key": key,
                "label": self._companion_manual_config_label(key),
                "old": old,
                "value": normalized,
                "reason": _single_line(reason, 160),
                "evidence": evidence_lines[:4],
                "strength": _single_line(strength, 20) or "可尝试",
                "confidence": max(0.0, min(1.0, _safe_float(confidence, 0.0, 0.0))),
            }
        )

    def _companion_manual_build_config_proposals(
        self,
        question: str,
        selected: list[dict[str, Any]],
        event: AstrMessageEvent | None = None,
    ) -> list[dict[str, Any]]:
        query = str(question or "")
        compact = re.sub(r"\s+", "", query).lower()
        titles = " ".join(str(item.get("title") or "") for item in selected)
        titles_compact = re.sub(r"\s+", "", titles).lower()
        primary_title = re.sub(r"\s+", "", str(selected[0].get("title") if selected else "")).lower()
        proposals: list[dict[str, Any]] = []
        runtime = self._companion_manual_runtime_snapshot(event) if event is not None else ""

        def runtime_evidence(*patterns: str) -> list[str]:
            lines = []
            for line in runtime.splitlines():
                if not line:
                    continue
                if any(pattern and pattern in line for pattern in patterns):
                    lines.append(line)
            return lines[:2]

        def current_number(key: str, default: float = 0.0) -> float:
            return _safe_float(self._companion_manual_current_config_value(key), default, 0.0)

        def current_int(key: str, default: int = 0) -> int:
            return _safe_int(self._companion_manual_current_config_value(key), default, 0)

        def current_bool(key: str, default: bool = False) -> bool:
            value = self._companion_manual_current_config_value(key)
            parsed = self._companion_manual_parse_bool(value)
            return bool(default) if parsed is None else parsed

        issue_tags = self._companion_manual_issue_tags(query)
        recent_question = "recent" in issue_tags or any(word in compact for word in ("刚才", "刚刚", "这次", "上一条", "为什么没回", "为什么不回", "没回复", "没发出来"))
        recent_no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=4) if event is not None and recent_question else []
        recent_no_reply_compact = re.sub(r"\s+", "", " ".join(recent_no_reply)).lower()

        group_issue_words = ("群聊不回复", "群里不回复", "群内不回复", "群聊没回复", "群里没回复", "群聊回复慢", "群里回复慢", "好久才回复", "老是不回复")
        group_slow = any(word in compact for word in group_issue_words) or primary_title.startswith("群聊老是不回复")
        followup = any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")) or primary_title.startswith("群聊连续对话")
        high_intensity = any(word in compact for word in ("高强度", "收口", "合并", "压制")) or primary_title.startswith("群聊高强度")
        debounce = any(word in compact for word in ("防抖", "智能收口", "补话", "等待", "合并消息")) or primary_title.startswith("消息收口")
        wakeup_mistouch = any(word in compact for word in ("误触", "碰瓷", "插话", "乱回复", "抢话"))
        wakeup_mistouch = wakeup_mistouch or any(word in compact for word in ("太敏感", "过于敏感", "容易触发", "乱触发"))
        photo_mistouch = any(word in compact for word in ("生图误触", "画图误触", "改图误触", "自然语言生图怎么关闭")) or (
            "生图" in compact and any(word in compact for word in ("误触", "太敏感", "过于敏感", "容易触发", "乱触发"))
        )
        qzone_repeat = any(word in compact for word in ("空间重复", "一直回复", "重复回复", "评论重复")) or ("qq空间" in titles_compact and "重复" in compact)
        rest_gate = any(word in compact for word in ("休息闸门", "睡眠闸门", "休息回复", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "不回复消息", "闸门"))
        smart_silence = any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话"))
        response_review_issue = any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截"))
        verbose_reply = any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "回复风格", "口语化"))
        body_state = any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "来月经", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "状态太低", "拟人状态"))
        photo_behavior = any(word in compact for word in ("生图没反应", "生图没有反应", "出图后", "好了", "自然语言生图", "自拍", "参考图", "穿搭图", "提示词"))
        qzone_setup = any(word in compact for word in ("空间首次", "第一次使用", "登录空间", "先登录空间", "点赞失效", "空间点赞", "cookie", "onebot")) or ("qq空间" in titles_compact and not qzone_repeat)
        if recent_no_reply_compact:
            focused_tags = issue_tags - {"recent", "location"}

            def recent_focus_allows(tag: str) -> bool:
                return not focused_tags or tag in focused_tags

            if recent_focus_allows("rest") and ("休息闸门" in recent_no_reply_compact or "休息静默" in recent_no_reply_compact):
                rest_gate = True
            if recent_focus_allows("silence") and "智能沉默" in recent_no_reply_compact:
                smart_silence = True
            if recent_focus_allows("group") and "群聊答疑复核" in recent_no_reply_compact:
                wakeup_mistouch = True
            if recent_focus_allows("review") and ("回复复核去重" in recent_no_reply_compact or "发送前去重" in recent_no_reply_compact):
                response_review_issue = True
            if recent_focus_allows("debounce") and ("智能收口" in recent_no_reply_compact or "消息收口" in recent_no_reply_compact):
                debounce = True

        def propose(
            key: str,
            value: Any,
            reason: str,
            scene: str,
            *,
            condition: str = "",
            strength: str = "可尝试",
            confidence: float = 0.62,
            runtime_patterns: tuple[str, ...] = (),
        ) -> None:
            evidence = [f"命中场景：{scene}"]
            if condition:
                evidence.append(condition)
            evidence.extend(runtime_evidence(*runtime_patterns))
            self._companion_manual_add_proposal(
                proposals,
                key,
                value,
                reason,
                evidence=evidence,
                strength=strength,
                confidence=confidence,
            )

        if group_slow or followup:
            if not current_bool("enable_group_conversation_followup", True):
                propose(
                    "enable_group_conversation_followup",
                    True,
                    "开启后，明确叫过 Bot 的同一用户在短窗口内不用每句都 @。",
                    "群聊不回复/连续对话续接",
                    condition="连续对话当前关闭，未 @ 的后续消息更容易断开。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前群连续对话锚点", "最近群唤醒"),
                )
            seconds = current_int("group_conversation_followup_seconds", 120)
            if seconds < 90 or seconds > 240:
                propose(
                    "group_conversation_followup_seconds",
                    120,
                    "把续接窗口收在 120 秒左右，既不太迟钝，也不容易很久后误认。",
                    "群聊不回复/连续对话续接",
                    condition=f"当前续接窗口 {seconds} 秒不在推荐观察区间 90-240 秒。",
                    strength="强建议" if seconds <= 0 or seconds > 360 else "可尝试",
                    confidence=0.76 if seconds <= 0 or seconds > 360 else 0.66,
                    runtime_patterns=("当前群连续对话锚点", "最近群消息"),
                )
            turns = current_int("group_conversation_followup_max_turns", 1)
            if turns < 1:
                propose(
                    "group_conversation_followup_max_turns",
                    1,
                    "至少允许无 @ 续接一轮，能改善“叫过之后马上不回”的体感。",
                    "群聊不回复/连续对话续接",
                    condition="当前无 @ 续接轮数为 0，明确叫过 Bot 后也不会自然续接。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("当前群连续对话锚点",),
                )
            elif group_slow and turns == 1 and "更容易" in compact:
                propose(
                    "group_conversation_followup_max_turns",
                    2,
                    "如果目标是更容易接住同一人的后续补话，可以临时放到 2 轮观察。",
                    "用户明确希望更容易接话",
                    condition="当前最多续接 1 轮，调到 2 会增加对同一用户补话的承接。",
                    strength="可尝试",
                    confidence=0.58,
                    runtime_patterns=("当前群连续对话锚点",),
                )

        if group_slow or high_intensity:
            if not current_bool("enable_group_high_intensity_mode", True):
                propose(
                    "enable_group_high_intensity_mode",
                    True,
                    "开启后连续叫 Bot 会先合并，避免多次 LLM 并发挤爆主链。",
                    "群聊高频唤醒/回复慢",
                    condition="高强度收口当前关闭，连续 @ 时更容易形成多轮并发。",
                    strength="可尝试",
                    confidence=0.6,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_wakeup_threshold", 3) < 4:
                propose(
                    "group_high_intensity_wakeup_threshold",
                    4,
                    "阈值从 3 提到 4，可以减少普通连续对话过早进入高强度压制。",
                    "高强度收口过早/回复慢",
                    condition="当前阈值低于 4，普通连续互动也可能较早进入收口。",
                    strength="可尝试",
                    confidence=0.66,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_cooldown_seconds", 150) > 90:
                propose(
                    "group_high_intensity_cooldown_seconds",
                    90,
                    "收口持续时间缩短到 90 秒，能让群聊更快回到正常续接判断。",
                    "高强度收口持续过久",
                    condition="当前持续时间超过 90 秒，容易让一段时间内的续接判断偏保守。",
                    strength="可尝试",
                    confidence=0.65,
                    runtime_patterns=("当前群高强度",),
                )
            if current_int("group_high_intensity_merge_seconds", 8) > 5:
                propose(
                    "group_high_intensity_merge_seconds",
                    5,
                    "高强度合并等待降到 5 秒，能少一点“好久才回”的体感。",
                    "高强度合并等待偏长",
                    condition="当前合并等待超过 5 秒，会直接增加高强度期间首条回复等待。",
                    strength="可尝试",
                    confidence=0.7,
                    runtime_patterns=("当前群高强度",),
                )
            if str(self._companion_manual_current_config_value("group_high_intensity_merge_scope") or "group") == "group":
                propose(
                    "group_high_intensity_merge_scope",
                    "same_user",
                    "只合并同一发送者的补话，避免别人接话时被全群收口卷进去。",
                    "高强度合并范围过宽",
                    condition="当前按全群合并，其他人接话也可能被并入同一轮。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近群消息", "当前群高强度"),
                )

        if group_slow or debounce:
            if current_number("text_message_debounce_seconds", 0.0) > 2:
                propose(
                    "text_message_debounce_seconds",
                    2,
                    "普通文本固定等待降到 2 秒，能减少完整发言后的无谓等待。",
                    "消息收口导致回复慢",
                    condition="当前普通文本固定等待超过 2 秒。",
                    strength="强建议",
                    confidence=0.78,
                    runtime_patterns=("最近智能收口",),
                )
            if current_bool("enable_smart_message_debounce", False) and current_number("smart_message_debounce_wait_seconds", 3.0) > 2:
                propose(
                    "smart_message_debounce_wait_seconds",
                    2,
                    "智能收口的总等待预算降到 2 秒，保留补话感但不拖太久。",
                    "智能收口等待偏长",
                    condition="智能收口已开启，且等待预算超过 2 秒。",
                    strength="可尝试",
                    confidence=0.69,
                    runtime_patterns=("最近智能收口",),
                )
            if current_number("text_message_debounce_max_wait_seconds", 12.0) > 10:
                propose(
                    "text_message_debounce_max_wait_seconds",
                    10,
                    "滑动收口最长等待压到 10 秒，避免用户连续补话时一直拖住回复。",
                    "收口最长等待偏长",
                    condition="当前文本最长等待超过 10 秒。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近智能收口",),
                )

        if wakeup_mistouch and not photo_mistouch and not smart_silence:
            if current_bool("enable_group_wakeup_question", True) and current_int("group_wakeup_question_threshold", 65) < 75:
                propose(
                    "group_wakeup_question_threshold",
                    75,
                    "提高公共求助阈值，能减少普通闲聊被当成“需要 Bot 答疑”。",
                    "群聊答疑/解惑误触",
                    condition="用户问题包含误触/碰瓷/乱插话意图，且当前求助阈值低于 75。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近群唤醒", "最近被动未回复"),
                )
            if current_number("group_wakeup_short_text_wait_seconds", 15.0) < 5:
                propose(
                    "group_wakeup_short_text_wait_seconds",
                    5,
                    "短唤醒多等几秒补话，能减少一两个字就触发回复。",
                    "短文本唤醒误触",
                    condition="短唤醒补话等待低于 5 秒，碎片消息更容易提前触发。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近群唤醒",),
                )

        if rest_gate:
            if current_bool("enable_rest_reply_simulation", False) and any(word in compact for word in ("误触", "太敏感", "容易", "晚安", "说句晚安", "不回")):
                propose(
                    "enable_rest_reply_simulation",
                    False,
                    "先关闭休息回复闸门，可以避免一句晚安后整段被动消息都被睡眠状态挡掉。",
                    "休息回复闸门误触/晚安后不回",
                    condition="用户问题命中休息/晚安不回，且休息回复闸门当前开启。",
                    strength="强建议",
                    confidence=0.86,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )
            elif current_bool("enable_rest_reply_simulation", False) and str(self._companion_manual_current_config_value("rest_reply_mode") or "") == "probability":
                propose(
                    "rest_reply_mode",
                    "llm",
                    "把休息闸门从纯概率切到模型判断，能减少普通晚安被机械挡住。",
                    "休息回复闸门误触",
                    condition="当前是 probability 模式，容易给人随机不回的体感。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_rest_reply_simulation", False) and current_int("rest_reply_awake_grace_minutes", 30) < 45:
                propose(
                    "rest_reply_awake_grace_minutes",
                    60,
                    "清醒宽限调到 60 分钟，刚被叫醒后的一小段对话不容易再次被当作睡眠中。",
                    "休息回复闸门反复拦截",
                    condition="当前清醒宽限低于 45 分钟。",
                    strength="可尝试",
                    confidence=0.63,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )

        if smart_silence:
            if current_bool("enable_smart_silence", True) and any(word in compact for word in ("误触", "太敏感", "不该沉默", "没回", "不回")):
                threshold = current_number("smart_silence_min_confidence", 0.66)
                if threshold < 0.76:
                    propose(
                        "smart_silence_min_confidence",
                        0.78,
                        "提高沉默置信度，只有更确定是用户想结束话题时才取消回复。",
                        "智能沉默误触",
                        condition=f"当前智能沉默置信度 {threshold:.2f} 低于 0.76。",
                        strength="强建议",
                        confidence=0.78,
                        runtime_patterns=("最近被动未回复",),
                    )
                else:
                    propose(
                        "enable_smart_silence",
                        False,
                        "先关掉智能沉默止血，确认误触样本后再重新打开调阈值。",
                        "智能沉默误触",
                        condition="用户明确反馈沉默误触，且阈值已经不低。",
                        strength="可尝试",
                        confidence=0.61,
                        runtime_patterns=("最近被动未回复",),
                    )

        if response_review_issue:
            if current_bool("enable_response_self_review", True) and any(word in compact for word in ("关闭", "关掉", "不要", "先关")):
                propose(
                    "enable_response_self_review",
                    False,
                    "先关闭回复/主动复核可以止血，但会少一层重复、串台和工具回执外发保护，建议只在定位问题时临时使用。",
                    "用户明确要求关闭复核",
                    condition="问题里明确出现关闭/不要复核，且复核当前开启。",
                    strength="可尝试",
                    confidence=0.58,
                    runtime_patterns=("最近被动未回复",),
                )
            mode = str(self._companion_manual_current_config_value("response_review_mode") or "severe_only").strip()
            if current_bool("enable_response_self_review", True) and mode == "full":
                propose(
                    "response_review_mode",
                    "severe_only",
                    "从 full 调回 severe_only，可以保留严重问题保护，同时减少普通被动回复被过度改写或误拦截。",
                    "回复复核过强/误杀",
                    condition="当前复核模式是 full，普通短回复也更容易进入模型复核。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_response_self_review", True) and current_int("response_review_max_chars", 260) < 220:
                propose(
                    "response_review_max_chars",
                    260,
                    "把被动复核长度阈值调回 260 字附近，避免很短的正常闲聊频繁进入复核链。",
                    "被动复核长度阈值偏低",
                    condition="当前被动复核长度阈值低于 220 字。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近被动未回复",),
                )

        if verbose_reply:
            style_text = str(self._companion_manual_current_config_value("reply_style_prompt") or "").strip()
            concise_style = (
                "每次回复至多三句话；简单回答尽量保持在 1-2 句，口语化、简洁，跟随当前对话节奏；"
                "必须使用简体中文，符合社交媒体聊天习惯。需要排障、教程、复杂说明或用户明确要求详细解释时，可以优先保证信息完整。"
            )
            if "至多三句" not in style_text and "至多三句话" not in style_text and "1-2" not in style_text:
                propose(
                    "reply_style_prompt",
                    concise_style,
                    "把简洁、口语化和复杂问题例外写进统一回复风格，能压住高强度群聊里动态提示词带来的话痨倾向。",
                    "回复太长/回复风格不生效",
                    condition="当前回复风格没有检测到明确的句数/简洁约束。",
                    strength="强建议",
                    confidence=0.79,
                )

        if photo_mistouch:
            if current_bool("enable_natural_language_photo_generation", False):
                propose(
                    "enable_natural_language_photo_generation",
                    False,
                    "自然语言生图关闭后，只保留主动拍照/自拍链路，能避免和普通聊天或其他生图插件抢触发。",
                    "自然语言生图误触",
                    condition="用户问题明确提到生图/改图误触，且自然语言生图入口当前开启。",
                    strength="强建议",
                    confidence=0.84,
                )

        if photo_behavior:
            if "上限" in compact and current_int("natural_language_photo_generation_max_daily", 2) < 100:
                propose(
                    "natural_language_photo_generation_max_daily",
                    100,
                    "把自然语言生图每日上限放宽到 100，适合测试期观察触发和出图链路。",
                    "自然语言生图上限",
                    condition="用户问题提到自然语言生图上限，且当前上限低于 100。",
                    strength="可尝试",
                    confidence=0.74,
                )
            if any(word in compact for word in ("呆", "好了", "没反应", "没有反应")) and not current_bool("enable_natural_language_photo_generation", False):
                propose(
                    "enable_natural_language_photo_generation",
                    True,
                    "如果就是想让普通自然语言触发生图，需要先打开自然语言生图入口。",
                    "自然语言生图没有反应",
                    condition="当前自然语言生图入口关闭。",
                    strength="可尝试",
                    confidence=0.66,
                )

        if qzone_repeat:
            if current_bool("enable_qzone_comment_inbox", False):
                propose(
                    "enable_qzone_comment_inbox",
                    False,
                    "先暂停评论收件箱，避免排障前继续对同一条评论公开回复。",
                    "QQ 空间评论重复回复",
                    condition="用户问题明确提到空间评论重复/一直回复，先关入口可止血。",
                    strength="强建议",
                    confidence=0.83,
                )
            if current_int("qzone_comment_inbox_interval_minutes", 60) < 60:
                propose(
                    "qzone_comment_inbox_interval_minutes",
                    60,
                    "评论检查间隔至少 60 分钟，降低重复扫描带来的二次回复风险。",
                    "QQ 空间评论重复回复",
                    condition="当前评论检查间隔小于 60 分钟，重复扫描频率偏高。",
                    strength="可尝试",
                    confidence=0.62,
                )
            if current_int("qzone_comment_inbox_max_replies_per_tick", 1) > 1:
                propose(
                    "qzone_comment_inbox_max_replies_per_tick",
                    1,
                    "每轮最多回复 1 条，排障时更容易定位是哪条评论触发。",
                    "QQ 空间评论重复回复",
                    condition="当前每轮可回复多条，排障时不容易定位触发源。",
                    strength="可尝试",
                    confidence=0.6,
                )

        if qzone_setup and current_bool("enable_qzone_comment_inbox", False) and current_int("qzone_comment_inbox_interval_minutes", 60) < 30:
            propose(
                "qzone_comment_inbox_interval_minutes",
                30,
                "首次接入空间时先把评论轮询间隔放到 30 分钟以上，更容易观察 Cookie 和去重是否稳定。",
                "QQ 空间首次使用/点赞评论排障",
                condition="评论收件箱已开启，且当前轮询间隔偏短。",
                strength="可尝试",
                confidence=0.58,
                runtime_patterns=("最近排障测试",),
            )

        if body_state:
            if any(word in compact for word in ("一天都是饥饿", "总是饥饿", "一直饿", "老是饿", "一直饥饿")) and current_bool("enable_hunger_state", True):
                propose(
                    "enable_hunger_state",
                    False,
                    "先关闭饥饿状态止血，避免状态机持续把吃饭/胃口写进回复。",
                    "饥饿状态长期不退",
                    condition="用户反馈一天都是饥饿状态，且饥饿状态当前开启。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前用户状态",),
                )
            if any(word in compact for word in ("生理期", "来月经", "姨妈")) and any(word in compact for word in ("不要", "关闭", "不想", "误触", "没设置", "奇怪")) and current_bool("enable_cycle_state", True):
                propose(
                    "enable_cycle_state",
                    False,
                    "关闭后会清理生理期相关状态，避免未确认用户接受时继续注入。",
                    "生理期模拟不想启用",
                    condition="用户问题包含生理期模拟负反馈，且开关当前开启。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("最近被动未回复",),
                )
            if any(word in compact for word in ("情绪太低", "情绪过低", "状态太低", "太容易过低")) and current_int("humanized_state_intensity", 50) > 35:
                propose(
                    "humanized_state_intensity",
                    35,
                    "降低拟人状态强度，能让健康、饥饿、情绪余波这类状态少一点压过人格。",
                    "拟人状态过强",
                    condition="用户反馈情绪/状态过低，且当前状态强度高于 35。",
                    strength="可尝试",
                    confidence=0.69,
                )

        primary_tags = issue_tags

        def proposal_rank(item: dict[str, Any]) -> float:
            key = str(item.get("key") or "")
            score = _safe_float(item.get("confidence"), 0.0, 0.0) * 100
            if str(item.get("strength") or "") == "强建议":
                score += 18
            if recent_no_reply_compact and any(pattern in recent_no_reply_compact for pattern in ("休息闸门", "智能沉默", "回复复核", "群聊答疑复核")):
                if (
                    ("休息闸门" in recent_no_reply_compact and (key.startswith("rest_") or key == "enable_rest_reply_simulation"))
                    or ("智能沉默" in recent_no_reply_compact and (key.startswith("smart_silence") or key == "enable_smart_silence"))
                    or ("回复复核" in recent_no_reply_compact and key in {"enable_response_self_review", "response_review_mode", "response_review_max_chars"})
                    or ("群聊答疑复核" in recent_no_reply_compact and key in {"group_wakeup_question_threshold", "enable_group_wakeup_question"})
                ):
                    score += 22
            if "rest" in primary_tags and (key.startswith("rest_") or key in {"enable_rest_reply_simulation", "enable_rest_backlog_reply"}):
                score += 16
            if "silence" in primary_tags and (key.startswith("smart_silence") or key == "enable_smart_silence"):
                score += 16
            if "review" in primary_tags and key in {"enable_response_self_review", "response_review_mode", "response_review_max_chars"}:
                score += 16
            if "photo" in primary_tags and ("photo" in key or "image" in key):
                score += 16
            if "state" in primary_tags and key in {"enable_health_state", "enable_hunger_state", "enable_cycle_state", "humanized_state_intensity"}:
                score += 16
            if "style" in primary_tags and key == "reply_style_prompt":
                score += 16
            if "qzone" in primary_tags and (key.startswith("qzone_") or key == "enable_qzone_comment_inbox"):
                score += 16
            return score

        proposals.sort(key=proposal_rank, reverse=True)
        return proposals[:6]

    def _companion_manual_pending_key(self, event: AstrMessageEvent) -> str:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        group_id = ""
        try:
            group_id = self._extract_group_id_from_event(event)
        except Exception:
            group_id = ""
        return f"group:{group_id}:{sender_id}" if group_id else f"private:{sender_id}"

    def _companion_manual_pending_store(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        store = data.setdefault("manual_diagnosis_pending_config", {})
        if not isinstance(store, dict):
            store = {}
            data["manual_diagnosis_pending_config"] = store
        return store

    def _companion_manual_prune_pending_store(self, store: dict[str, Any]) -> None:
        if not isinstance(store, dict):
            return
        now = _now_ts()
        for key, item in list(store.items()):
            ts = _safe_float(item.get("ts") if isinstance(item, dict) else 0.0, 0.0, 0.0)
            if ts <= 0 or now - ts > 1800:
                store.pop(key, None)
        if len(store) <= 80:
            return
        ranked = sorted(
            store.items(),
            key=lambda pair: _safe_float(pair[1].get("ts") if isinstance(pair[1], dict) else 0.0, 0.0, 0.0),
            reverse=True,
        )
        keep = {key for key, _ in ranked[:80]}
        for key in list(store.keys()):
            if key not in keep:
                store.pop(key, None)

    def _companion_manual_store_pending_config(
        self,
        event: AstrMessageEvent,
        question: str,
        proposals: list[dict[str, Any]],
    ) -> str:
        store = self._companion_manual_pending_store()
        self._companion_manual_prune_pending_store(store)
        key = self._companion_manual_pending_key(event)
        if not proposals:
            if key in store:
                store.pop(key, None)
                self._save_data_sync()
            return ""
        token = uuid.uuid4().hex[:6]
        store[key] = {
            "token": token,
            "ts": _now_ts(),
            "question": _single_line(question, 260),
            "changes": proposals,
        }
        self._save_data_sync()
        return token

    def _companion_manual_recent_context_store(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        store = data.setdefault("manual_diagnosis_recent_context", {})
        if not isinstance(store, dict):
            store = {}
            data["manual_diagnosis_recent_context"] = store
        now = _now_ts()
        for key, item in list(store.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0.0, 0.0) > 20 * 60:
                store.pop(key, None)
        if len(store) > 80:
            ranked = sorted(
                store.items(),
                key=lambda pair: _safe_float(pair[1].get("ts") if isinstance(pair[1], dict) else 0.0, 0.0, 0.0),
                reverse=True,
            )
            keep = {key for key, _ in ranked[:80]}
            for key in list(store.keys()):
                if key not in keep:
                    store.pop(key, None)
        return store

    def _companion_manual_recent_context_text(self, event: AstrMessageEvent) -> str:
        store = self._companion_manual_recent_context_store()
        item = store.get(self._companion_manual_pending_key(event))
        if not isinstance(item, dict):
            return ""
        question = _single_line(item.get("question"), 180)
        answer = _single_line(item.get("answer"), 360)
        configs = item.get("configs") if isinstance(item.get("configs"), list) else []
        config_text = "；".join(_single_line(value, 120) for value in configs[:4] if _single_line(value, 120))
        parts = []
        if question:
            parts.append(f"上一轮问题：{question}")
        if answer:
            parts.append(f"上一轮答复摘要：{answer}")
        if config_text:
            parts.append(f"上一轮涉及配置：{config_text}")
        return "\n".join(parts)

    async def _companion_manual_media_context(self, event: AstrMessageEvent, question: str) -> str:
        sources: list[tuple[str, str]] = []

        def add(source: Any, label: str) -> None:
            text = str(source or "").strip()
            if not text:
                return
            if any(existing == text for existing, _ in sources):
                return
            sources.append((text, label))

        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        current_getter = getattr(self, "_photo_reference_sources_from_current_event", None)
        if callable(current_getter):
            try:
                for source in await current_getter(event, sender_id):
                    add(source, "随消息携带图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑携带图片提取失败: %s", _single_line(exc, 120))
        reply_cache_getter = getattr(self, "_photo_reference_sources_from_reply_cache", None)
        if callable(reply_cache_getter):
            try:
                for source in reply_cache_getter(event):
                    add(source, "引用撤回/缓存图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑引用缓存图片提取失败: %s", _single_line(exc, 120))
        reply_getter = getattr(self, "_photo_reference_sources_from_reply_event", None)
        if callable(reply_getter):
            try:
                for source in await reply_getter(event):
                    add(source, "引用消息图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑引用图片提取失败: %s", _single_line(exc, 120))
        if not sources:
            return ""

        limited_sources = sources[:5]
        source_values = [source for source, _label in limited_sources]
        labels: list[str] = []
        for _source, label in limited_sources:
            if label not in labels:
                labels.append(label)
        vision_text = ""
        transcriber = getattr(self, "_transcribe_private_inbound_images", None)
        if callable(transcriber):
            try:
                raw_vision = await transcriber(
                    source_values,
                    umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    user_text=question or "陪伴答疑图片排障",
                    force_contextual=True,
                )
                limit_getter = getattr(self, "_private_image_vision_text_limit", None)
                limit = limit_getter(len(source_values)) if callable(limit_getter) else 1200
                vision_text = _single_line(raw_vision, _safe_int(limit, 1200, 240, 2400))
            except Exception as exc:
                logger.info("[PrivateCompanion] 答疑图片视觉摘要失败: %s", _single_line(exc, 120))
                vision_text = ""

        lines = [
            "本轮答疑附带图片上下文：",
            f"图片来源：{'、'.join(labels)}；数量={len(source_values)}",
        ]
        if vision_text:
            lines.append("图片视觉摘要：" + vision_text)
        else:
            lines.append("已检测到图片，但当前没有拿到可靠视觉摘要；如果用户问截图内容，只能说明需要更清晰图片或日志，不要编造画面。")
        return "\n".join(lines)

    def _companion_manual_store_recent_context(
        self,
        event: AstrMessageEvent,
        *,
        question: str,
        answer: str,
        proposals: list[dict[str, Any]],
    ) -> None:
        store = self._companion_manual_recent_context_store()
        key = self._companion_manual_pending_key(event)
        configs = [
            self._companion_manual_config_ref(_single_line(item.get("key"), 80), include_location=False)
            for item in proposals[:6]
            if isinstance(item, dict) and _single_line(item.get("key"), 80)
        ]
        store[key] = {
            "ts": _now_ts(),
            "question": _single_line(question, 260),
            "answer": _single_line(answer, 600),
            "configs": configs,
        }
        self._companion_manual_recent_context_store()
        try:
            self._schedule_data_save()
        except Exception:
            try:
                self._save_data_sync()
            except Exception:
                pass

    def _companion_manual_format_config_proposals(self, token: str, proposals: list[dict[str, Any]]) -> str:
        if not proposals:
            return ""
        lines = ["可执行建议（现在还没改配置）："]
        for idx, item in enumerate(proposals, start=1):
            confidence = _safe_float(item.get("confidence"), 0.0, 0.0)
            key = _single_line(item.get("key"), 80)
            evidence = [
                _single_line(part, 120)
                for part in (item.get("evidence") if isinstance(item.get("evidence"), list) else [])
                if _single_line(part, 120)
            ]
            lines.append(
                f"{idx}. {self._companion_manual_config_ref(key)}："
                f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}；"
                f"{item.get('strength') or '可尝试'}｜置信度{self._companion_manual_confidence_label(confidence)}；"
                f"{item.get('reason')}"
            )
            if evidence:
                lines.append("   依据：" + "；".join(evidence[:3]))
        lines.append("")
        lines.append("确认执行：陪伴 答疑确认")
        lines.append("取消建议：陪伴 答疑取消")
        lines.append("手动改一项：陪伴 答疑设置 <配置项> <值>")
        if token:
            lines.append(f"本次建议编号：{token}")
        return "\n".join(lines)

    def _companion_manual_format_config_proposals_brief(self, token: str, proposals: list[dict[str, Any]]) -> str:
        if not proposals:
            return ""
        lines = ["可直接调整的项（还没改）："]
        for idx, item in enumerate(proposals[:3], start=1):
            key = _single_line(item.get("key"), 80)
            if not key:
                continue
            lines.append(
                f"{idx}. {self._companion_manual_config_ref(key)}："
                f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}"
            )
        if not lines[1:]:
            return ""
        lines.append("要我直接应用的话发：陪伴 答疑确认；不想改就发：陪伴 答疑取消。")
        if token:
            lines.append(f"建议编号：{token}")
        return "\n".join(lines)

    def _companion_manual_fallback_answer(
        self,
        event: AstrMessageEvent,
        question: str,
        selected: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
        media_context: str = "",
    ) -> str:
        query = _single_line(question, 180)
        if not selected:
            if media_context:
                return (
                    "我这轮已经检测到你带了图片或引用了图片，但答疑模型没有给出稳定诊断。"
                    "如果图片摘要没生成，就需要再看清晰截图或对应日志；如果摘要已生成，可以继续追问“这张图里哪里不对”。"
                )
            return (
                "这句我还没抓准你想查哪块功能。你可以直接说具体一点，比如“刚才为什么没回复”、"
                "“为什么等了几秒”、或“某个配置在哪里改”，我就能按当前会话状态接着查。"
            )
        primary = selected[0] if isinstance(selected[0], dict) else {}
        title = _single_line(primary.get("title"), 60) or "相关功能"
        summary = _single_line(primary.get("summary"), 220) or "这类情况需要结合当前运行状态判断。"
        group_note = self._companion_manual_current_group_note(event)
        checks = [str(item) for item in primary.get("checks", []) if str(item or "").strip()]
        suggestions = [str(item) for item in primary.get("suggestions", []) if str(item or "").strip()]
        no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=2)
        tests = self._companion_manual_recent_test_evidence(limit=2)
        lines = []
        lines.append(f"我先按“{title}”看，{summary}")
        if group_note:
            lines.append(_single_line(group_note, 180))
        if no_reply and any(word in str(question or "") for word in ("刚才", "刚刚", "没回", "不回", "没回复", "为什么")):
            lines.append("最近未回复记录：" + "；".join(no_reply))
        elif tests and any(word in str(question or "") for word in ("测试", "排障", "空间", "生图", "tts", "窥屏")):
            lines.append("最近排障测试：" + "；".join(tests))
        if checks:
            lines.append("最先看这一点：" + _single_line(checks[0], 180))
        if suggestions:
            lines.append("可以先试：" + _single_line(suggestions[0], 180))
        if proposals:
            item = proposals[0]
            key = _single_line(item.get("key"), 80)
            if key:
                lines.append(
                    f"如果要调配置，优先看 {self._companion_manual_config_ref(key, include_location=False)}，"
                    f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                    f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}。"
                )
        return "\n".join(line for line in lines if line)


    def _companion_manual_format_diagnostic_evidence(
        self,
        event: AstrMessageEvent,
        selected: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        titles = [_single_line(item.get("title"), 50) for item in selected if isinstance(item, dict)]
        titles = [item for item in titles if item]
        if titles:
            lines.append("匹配说明书：" + " / ".join(titles[:3]))
        runtime = self._companion_manual_runtime_snapshot(event)
        runtime_lines = [
            _single_line(line, 180)
            for line in runtime.splitlines()
            if _single_line(line, 180)
        ]
        if runtime_lines:
            lines.extend(runtime_lines[:5])
        if proposals:
            config_lines = []
            for item in proposals[:6]:
                if not isinstance(item, dict):
                    continue
                key = _single_line(item.get("key"), 80)
                if key:
                    config_lines.append(
                        f"{self._companion_manual_config_ref(key, include_location=False)}="
                        f"{self._companion_manual_format_config_item_value(key, item.get('old'))}"
                    )
            if config_lines:
                lines.append("涉及可改配置：" + "、".join(config_lines))
        if not lines:
            return ""
        return "诊断依据：\n" + "\n".join(f"- {line}" for line in lines[:8])

    def _companion_manual_recent_no_reply_evidence(self, event: AstrMessageEvent | None = None, *, limit: int = 3) -> list[str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        passive = data.get("passive_no_reply_records") if isinstance(data.get("passive_no_reply_records"), dict) else {}
        items = passive.get("items") if isinstance(passive.get("items"), list) else []
        session = ""
        sender_id = ""
        if event is not None:
            session = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            try:
                sender_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                sender_id = ""
        ranked: list[tuple[float, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            score = _safe_float(item.get("last_ts"), 0.0, 0.0)
            if session and _single_line(item.get("last_session"), 160) == session:
                score += 10_000_000
            elif sender_id and _single_line(item.get("last_sender_id"), 80) == sender_id:
                score += 1_000_000
            reason = _single_line(item.get("reason"), 80) or "未说明原因"
            source = _single_line(item.get("source"), 30) or "被动未回复"
            inbound = _single_line(item.get("last_inbound"), 80)
            count = _safe_int(item.get("count"), 1, 1)
            text = f"{source}：{reason}×{count}"
            if inbound:
                text += f"｜最近消息：{inbound}"
            ranked.append((score, text))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _score, text in ranked[: max(1, limit)]]

    def _companion_manual_recent_test_evidence(self, *, limit: int = 3) -> list[str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        tests = data.get("troubleshooting_test_results") if isinstance(data.get("troubleshooting_test_results"), dict) else {}
        ranked: list[tuple[float, str]] = []
        for key, item in tests.items():
            if not isinstance(item, dict):
                continue
            ts = _safe_float(item.get("ran_at"), 0.0, 0.0)
            title = _single_line(item.get("title") or key, 30)
            status = "进行中" if bool(item.get("pending")) else ("通过" if bool(item.get("ok")) else "失败")
            detail = _single_line(item.get("error") or item.get("detail"), 80)
            ranked.append((ts, f"{title}：{status}{('｜' + detail) if detail else ''}"))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _ts, text in ranked[: max(1, limit)]]

    def _companion_manual_relevant_setting_snapshot(self, selected: list[dict[str, Any]], query: str = "") -> list[str]:
        issue_tags = self._companion_manual_issue_tags(query)
        settings: list[str] = []
        for entry in selected[:2]:
            if not isinstance(entry, dict):
                continue
            for key in entry.get("settings", [])[:8]:
                key_text = str(key or "").strip()
                if not key_text or key_text in settings:
                    continue
                if key_text in self._companion_manual_config_specs():
                    current = self._companion_manual_current_config_value(key_text)
                    settings.append(
                        f"{self._companion_manual_config_label(key_text)}={self._companion_manual_format_config_item_value(key_text, current)}"
                    )
                elif key_text in self._companion_manual_config_display_meta():
                    current = self._companion_manual_current_config_value(key_text)
                    if current not in (None, ""):
                        settings.append(
                            f"{self._companion_manual_config_label(key_text)}={self._companion_manual_format_config_item_value(key_text, current)}"
                        )
        if settings:
            return settings[:8]
        fallback = []
        for item in self._companion_manual_setting_snapshot():
            if (
                ("group" in issue_tags and any(token in item for token in ("群聊", "高强度", "消息收口", "唤醒")))
                or ("rest" in issue_tags and "休息" in item)
                or ("silence" in issue_tags and "智能沉默" in item)
                or ("review" in issue_tags and "回复复核" in item)
                or ("photo" in issue_tags and "自然语言生图" in item)
                or ("state" in issue_tags and "拟人状态" in item)
                or ("style" in issue_tags and "回复风格" in item)
            ):
                fallback.append(item)
        return fallback[:5]

    def _companion_manual_can_apply_config(self, event: AstrMessageEvent) -> bool:
        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        return self._can_manage_private_companion(event) if is_private else self._can_manage_group_companion(event)

    def _companion_manual_get_pending_config(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        store = self._companion_manual_pending_store()
        key = self._companion_manual_pending_key(event)
        pending = store.get(key)
        if not isinstance(pending, dict):
            return None
        if _now_ts() - _safe_float(pending.get("ts"), 0.0, 0.0) > 1800:
            store.pop(key, None)
            self._save_data_sync()
            return None
        return pending

    def _companion_manual_apply_config_value(self, key: str, value: Any) -> tuple[bool, str, Any, Any]:
        ok, normalized, error = self._companion_manual_normalize_config_value(key, value)
        if not ok:
            return False, error, None, None
        old = self._companion_manual_current_config_value(key)
        setattr(self, key, normalized)
        extra_config_updates: dict[str, Any] = {}
        if key == "enable_message_debounce":
            self.enable_semantic_message_debounce = bool(normalized)
            extra_config_updates["enable_semantic_message_debounce"] = bool(normalized)
        if key == "text_message_debounce_seconds":
            self.semantic_message_debounce_seconds = normalized
            extra_config_updates["semantic_message_debounce_seconds"] = normalized
        config_value = normalized
        if key == "rest_reply_probability":
            config_value = max(0, min(100, int(round(_safe_float(normalized, 0.0, 0.0) * 100))))
        saved = False
        config = getattr(self, "config", None)
        if config is not None:
            try:
                saved = _set_into_config(config, key, config_value, allow_flat_fallback=False)
            except TypeError:
                saved = _set_into_config(config, key, config_value)
            if not saved:
                saved = _set_into_config(config, key, config_value)
            for extra_key, extra_value in extra_config_updates.items():
                try:
                    _set_into_config(config, extra_key, extra_value, allow_flat_fallback=False)
                except TypeError:
                    _set_into_config(config, extra_key, extra_value)
            self._save_config_if_possible()
        if not saved:
            logger.debug("[PrivateCompanion] 答疑设置只更新运行态,未找到可写配置项: key=%s", key)
        return True, "", old, normalized

    def _companion_manual_apply_pending_config(self, event: AstrMessageEvent) -> str:
        if not self._companion_manual_can_apply_config(event):
            return self._management_denied_text()
        pending = self._companion_manual_get_pending_config(event)
        if not pending:
            return "没有待确认的答疑配置建议。先用：陪伴 答疑 <问题>"
        changes = pending.get("changes") if isinstance(pending.get("changes"), list) else []
        if not changes:
            return "这次答疑没有可执行配置建议。"
        lines = ["已按刚才的答疑建议修改配置："]
        applied = 0
        for item in changes:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            ok, error, old, new = self._companion_manual_apply_config_value(key, item.get("value"))
            if not ok:
                lines.append(f"- {key}：跳过，{error}")
                continue
            applied += 1
            lines.append(
                f"- {key}（{self._companion_manual_config_label(key)}）："
                f"由 {self._companion_manual_format_config_item_value(key, old)} 改为 {self._companion_manual_format_config_item_value(key, new)}"
                f"；{_single_line(item.get('reason'), 120) or '按答疑建议调整'}"
            )
        self._companion_manual_pending_store().pop(self._companion_manual_pending_key(event), None)
        self._save_data_sync()
        if applied <= 0:
            return "没有成功应用的配置项。"
        lines.append("已保存到插件配置；如果 AstrBot 配置对象不支持同步保存，日志里会提示。")
        return "\n".join(lines)

    def _companion_manual_cancel_pending_config(self, event: AstrMessageEvent) -> str:
        store = self._companion_manual_pending_store()
        key = self._companion_manual_pending_key(event)
        existed = key in store
        store.pop(key, None)
        self._save_data_sync()
        return "已取消刚才的答疑配置建议。" if existed else "当前没有待确认的答疑配置建议。"

    def _companion_manual_parse_setting_text(self, text: str) -> tuple[str, str]:
        raw = re.sub(r"^(?:把|将)\s*", "", str(text or "").strip())
        if not raw:
            return "", ""
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:|：|设为|设置为|改成|调到)\s*(.+)", raw)
        if match:
            key = self._companion_manual_config_key_from_alias(match.group(1).strip())
            return key, match.group(2).strip()
        for alias, key in sorted(self._companion_manual_config_aliases().items(), key=lambda item: len(str(item[0])), reverse=True):
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            delimiter_pattern = rf"^{re.escape(alias_text)}\s*(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*(.+)$"
            delimiter_match = re.match(delimiter_pattern, raw, flags=re.I | re.S)
            if delimiter_match:
                return key, delimiter_match.group(1).strip()
            if raw.lower().startswith(alias_text.lower()):
                tail = raw[len(alias_text):].strip()
                tail = re.sub(r"^(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*", "", tail).strip()
                if tail:
                    return key, tail
        parts = raw.split(maxsplit=1)
        if len(parts) >= 2:
            key = self._companion_manual_config_key_from_alias(parts[0].strip())
            return key, parts[1].strip()
        return "", ""

    def _companion_manual_apply_setting_command(self, event: AstrMessageEvent, text: str) -> str:
        if not self._companion_manual_can_apply_config(event):
            return self._management_denied_text()
        key, value = self._companion_manual_parse_setting_text(text)
        if not key or not value:
            allowed = "、".join(sorted(self._companion_manual_config_specs().keys())[:12])
            return (
                "请这样写：陪伴 答疑设置 <配置项> <值>\n"
                "例如：陪伴 答疑设置 group_high_intensity_wakeup_threshold 5\n"
                "也可以：陪伴 答疑设置 高强度阈值 5\n"
                f"可改配置很多，前几个是：{allowed} ..."
            )
        ok, error, old, new = self._companion_manual_apply_config_value(key, value)
        if not ok:
            return error
        self._companion_manual_pending_store().pop(self._companion_manual_pending_key(event), None)
        self._save_data_sync()
        if self._companion_manual_values_equal(old, new):
            return (
                "配置没有变化：\n"
                f"{key}（{self._companion_manual_config_label(key)}）本来就是 "
                f"{self._companion_manual_format_config_item_value(key, new)}"
            )
        return (
            "已修改并保存配置：\n"
            f"{key}（{self._companion_manual_config_label(key)}）："
            f"由 {self._companion_manual_format_config_item_value(key, old)} 改为 {self._companion_manual_format_config_item_value(key, new)}"
        )

    def _companion_manual_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "刚才被动消息为什么没回",
                "keywords": ["刚才没回", "刚才没回复", "刚刚没回", "为什么没回", "为什么不回", "没有回复", "被动未回复", "空结果", "跳过发送", "消息链全为空", "没发出来"],
                "summary": "这类问题先看最近被动未回复记录，而不是先猜人格。常见来源包括休息回复闸门、智能沉默、回复复核拦截、群聊答疑碰瓷复核、空结果兜底、自然语言生图或其他命令接管。",
                "checks": [
                    "排障页和答疑运行态都会合并最近被动未回复原因，重复原因不会刷屏。",
                    "如果来源是休息回复闸门，重点看 enable_rest_reply_simulation、rest_reply_mode 和 rest_reply_awake_grace_minutes。",
                    "如果来源是智能沉默，重点看 enable_smart_silence 和 smart_silence_min_confidence。",
                    "如果来源是群聊答疑复核，说明发送前判断这次回复像碰瓷插话。",
                    "如果来源是空结果/消息链全为空，要继续查哪个插件或工具阶段清空了结果。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_awake_grace_minutes",
                    "enable_smart_silence",
                    "smart_silence_min_confidence",
                    "enable_group_wakeup_question",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "先问“陪伴 答疑 刚才为什么没回复”，让它带出最近未回复记录。",
                    "如果最近记录为空，再看 AstrBot 日志里 respond.stage、on_decorating_result 和插件命令是否提前 stop_event。",
                ],
            },
            {
                "title": "群聊老是不回复或好久才回复",
                "keywords": ["群聊", "群内", "群里", "不活跃", "活跃", "不回复", "没回复", "好久", "回复慢", "没反应", "不理", "卡住", "延迟"],
                "summary": "群聊回复不是所有消息都接管，通常要被 @、引用、命中唤醒、或处在连续对话窗口内。慢回复多半来自收口等待、高强度合并、模型超时或主链排队。",
                "checks": [
                    "先确认目标群启用了群聊陪伴，并且白名单/黑名单没有挡住。",
                    "如果没有 @/引用 Bot，只有“群聊连续对话保持”窗口内的同一用户后续发言才可能续接。",
                    "如果短时间连续叫 Bot，高强度收口会合并多条消息后再回复，看起来会慢几秒。",
                    "如果开启智能文本收口，短引子、逗号结尾、疑似没说完的话会先等补话。",
                    "如果日志里有主链会话锁、Provider timeout、休息回复闸门或智能沉默，回复也可能被延后或取消。",
                ],
                "settings": [
                    "enable_group_companion",
                    "group_access_mode",
                    "enable_group_conversation_followup",
                    "enable_group_high_intensity_mode",
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "想更容易接话：打开群聊连续对话，并把窗口设为 90-180 秒。",
                    "想少等：降低文本/短唤醒等待，或关闭智能文本收口。",
                    "高强度群里想减少误压制：阈值调到 4-5，持续时间调到 60-90 秒，合并范围改 same_user。",
                ],
            },
            {
                "title": "群聊连续对话在哪里设置",
                "keywords": ["连续对话", "续接", "上下文续接", "followup", "接话", "没at", "没@", "设置在哪"],
                "summary": "配置项是 enable_group_conversation_followup。开启后，群里同一用户明确 @/引用 Bot 之后，短时间内没继续 @ 的后续消息也会判断是否仍在和 Bot 对话。",
                "checks": [
                    "设置入口：配置页搜索“群聊连续对话保持”或 enable_group_conversation_followup。",
                    "窗口：group_conversation_followup_seconds，决定多久内还能续接。",
                    "轮数：group_conversation_followup_max_turns，决定不继续 @ 时最多自动续几轮。",
                    "模型：GROUP_FOLLOWUP_JUDGE_PROVIDER_ID 只在规则不确定时使用；留空时会先跟随快速响应模型，快速响应模型也留空时只走规则判断。",
                ],
                "settings": [
                    "enable_group_conversation_followup",
                    "group_conversation_followup_seconds",
                    "group_conversation_followup_max_turns",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "推荐：窗口 120 秒，最多 1-2 轮，小模型可填低延迟分类模型。",
                    "如果经常碰瓷回复，把最大轮数设为 1，并填写续接判断模型。",
                ],
            },
            {
                "title": "群聊高强度收口和连续对话的关系",
                "keywords": ["高强度", "收口", "合并", "冲突", "连续对话", "压制", "高强度收口"],
                "summary": "两者不是硬冲突，但高强度收口优先级更高。近期连续叫到 Bot 时会合并明确消息，并暂停不确定的续接模型判断；冷却残留只降载，不再延迟单条明确 @。",
                "checks": [
                    "触发条件：group_high_intensity_wakeup_window_seconds 内唤醒次数达到 group_high_intensity_wakeup_threshold；唤醒疲劳只会在近期已有连续唤醒时辅助触发。",
                    "持续时间：group_high_intensity_cooldown_seconds。",
                    "合并范围：group 表示全群叫 Bot 合并；same_user 表示只合并同一用户补话。",
                    "高强度冷却期间明确 @/引用仍会处理；只有近期仍在连续叫 Bot 时才进入合并等待。",
                ],
                "settings": [
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_wakeup_window_seconds",
                    "group_high_intensity_wakeup_threshold",
                    "group_high_intensity_cooldown_seconds",
                    "group_high_intensity_merge_seconds",
                    "group_high_intensity_merge_scope",
                ],
                "suggestions": [
                    "想保留对话感：阈值 4-5、持续 60-90 秒、合并范围 same_user。",
                    "想极限省 token：保持默认 group 合并，并接受高强度期间续接变保守。",
                ],
            },
            {
                "title": "消息收口/智能防抖是什么",
                "keywords": ["防抖", "收口", "智能收口", "补话", "等补充", "等待", "合并消息"],
                "summary": "消息收口会给用户留一点补充时间，把连续几句话合并成同一轮；智能收口会先判断这句话是不是完整，只有像“问你个事/你猜/等等/逗号结尾”才等待。",
                "checks": [
                    "总开关：enable_message_debounce。",
                    "智能文本收口：enable_smart_message_debounce。",
                    "固定文本等待：text_message_debounce_seconds；智能收口开启时主要看 smart_message_debounce_wait_seconds。",
                    "最长等待：text_message_debounce_max_wait_seconds，避免一直补话拖住。",
                    "最大合并：message_debounce_max_merge_messages，达到后立刻进入回复链。",
                ],
                "settings": [
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "text_message_debounce_seconds",
                    "smart_message_debounce_wait_seconds",
                    "text_message_debounce_max_wait_seconds",
                    "message_debounce_max_merge_messages",
                ],
                "suggestions": [
                    "觉得慢：文本等待设 0-2 秒，智能等待设 1-2 秒。",
                    "用户常先发图/转发再补字：图片/转发等待可以保留 5-8 秒。",
                ],
            },
            {
                "title": "群聊唤醒、@ 和答疑误触",
                "keywords": ["唤醒", "@", "at", "艾特", "答疑", "误触", "碰瓷", "为什么插话"],
                "summary": "群聊默认不会每句话都回复。明确 @/引用最强；名字、弱唤醒词、兴趣词、公共求助问题会按规则和概率进入回复链。答疑类回复发送前还有碰瓷复核。",
                "checks": [
                    "强触发：@ Bot、引用 Bot、直接叫 Bot 名字。",
                    "弱触发：group_wakeup_context_words、group_wakeup_interest_keywords、公共求助问题。",
                    "公共求助由 enable_group_wakeup_question 和 group_wakeup_question_threshold 控制。",
                    "答疑误触可看日志里的“群聊答疑回复发送前复核”。",
                ],
                "settings": [
                    "enable_group_wakeup_enhancement",
                    "group_wakeup_direct_words",
                    "group_wakeup_context_words",
                    "group_wakeup_interest_keywords",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：提高求助阈值，删掉泛化弱唤醒词，保留 Bot 名字和明确 @。",
                    "不回复多：检查是否被冷却/疲劳/高强度压制。",
                ],
            },
            {
                "title": "休息回复闸门和晚安后不回",
                "keywords": ["休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "说句晚安", "醒后补看"],
                "summary": "休息回复闸门只在 Bot 当前日程像睡眠/午休且处于配置窗口时生效。它会在回复链前判断要不要醒来；被拦截的私聊会记录到被动未回复，并可在醒后补看。",
                "checks": [
                    "总开关：enable_rest_reply_simulation；关闭后不会因为睡眠状态拦截被动回复。",
                    "模式：rest_reply_mode。probability 是概率醒来，llm 是小模型判断是否需要醒来。",
                    "模型阈值：rest_reply_llm_threshold，越高越不容易醒来。",
                    "清醒宽限：rest_reply_awake_grace_minutes，被叫醒后这段时间内不容易再次被当成休息中。",
                    "醒后补看：enable_rest_backlog_reply 和 rest_backlog_max_messages 控制被挡住的私聊是否下次注入。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_probability",
                    "rest_reply_llm_threshold",
                    "rest_reply_awake_grace_minutes",
                    "enable_rest_backlog_reply",
                    "rest_backlog_max_messages",
                    "REST_WAKEUP_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：先关闭 enable_rest_reply_simulation 止血，或改成 llm 模式并提高醒来判断质量。",
                    "只是晚安后不想整段断联：把清醒宽限调到 45-60 分钟。",
                    "想保留拟人睡眠：打开醒后补看，避免 token 已省但用户消息彻底丢上下文。",
                ],
            },
            {
                "title": "智能沉默和结束话题",
                "keywords": ["智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊", "停止回复"],
                "summary": "智能沉默是在回复发送前工作的。主模型先生成回复，小模型再看用户是否明确表达“别回/别继续这个话题”，如果判定应沉默，会把待发送结果清空并记录被动未回复。",
                "checks": [
                    "总开关：enable_smart_silence。",
                    "阈值：smart_silence_min_confidence，越高越不容易沉默。",
                    "模型：SMART_SILENCE_PROVIDER_ID；留空时会走插件模型回退。",
                    "群聊场景会参考最近真实群聊上下文，不只看唤醒消息。",
                    "如果已经发出去了，就不是智能沉默拦截；智能沉默的发送前兜底只会取消待发送回复。",
                ],
                "settings": [
                    "enable_smart_silence",
                    "smart_silence_min_confidence",
                    "smart_silence_model_timeout_seconds",
                    "SMART_SILENCE_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：把置信度调到 0.76-0.82，仍误触再临时关闭。",
                    "希望更准确：给 SMART_SILENCE_PROVIDER_ID 填低延迟但能稳定输出 JSON 的小模型。",
                ],
            },
            {
                "title": "回复复核/去重为什么会拦截",
                "keywords": ["回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截", "为什么被拦截"],
                "summary": "回复复核主要防止复读、串台、工具回执和异常文本外发。它在发送前工作；如果判定要丢弃，会清空待发送结果并写入被动未回复记录。",
                "checks": [
                    "先看最近被动未回复记录里 source 是“回复复核去重”“发送前拦截”还是“群聊答疑复核”。",
                    "response_review_mode=full 时，普通被动回复也更容易进入模型改写或复核；severe_only 更像默认保护层。",
                    "response_review_max_chars 太低时，短闲聊也可能被当成需要复核的长回复。",
                    "群聊答疑碰瓷复核不等同于普通去重，它只处理公共求助/答疑唤醒产生的可疑插话。",
                    "如果拦的是“消息已发送/发送成功”这类回执，通常是工具链回执被保护性拦截，不该关掉复核。",
                ],
                "settings": [
                    "enable_response_self_review",
                    "response_review_mode",
                    "response_review_max_chars",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                ],
                "suggestions": [
                    "误杀普通回复：优先把 response_review_mode 从 full 改回 severe_only，或把 response_review_max_chars 调回 260 左右。",
                    "只是群里答疑碰瓷被拦：优先调群聊解惑阈值，不要直接关整个复核。",
                ],
            },
            {
                "title": "回复太长或不听人格字数限制",
                "keywords": ["话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格", "人设限制", "人格限制"],
                "summary": "人格里的字数限制仍然有效，但群聊高强度、关系网、动态状态、记忆、图片和转发上下文注入太多时，模型可能更想解释完整而变长。回复风格约束会作为更靠近当前请求的表达节奏提示注入。",
                "checks": [
                    "回复风格配置：reply_style_prompt，会进入普通聊天和主动消息的请求动态块。",
                    "它不是只作用于私聊；群聊主链、主动和被动都会尽量遵守，但复杂排障/教程可例外。",
                    "如果群聊只想极短，可以把句数、语言、口语化、复杂说明例外写清楚。",
                    "如果仍然超长，要看是否有其他插件或主人格在系统提示词里要求详细解释。",
                ],
                "settings": [
                    "reply_style_prompt",
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_max_merge_messages",
                    "enable_message_debounce",
                ],
                "suggestions": [
                    "推荐写法：每次回复至多三句话；简单回答尽量 1-2 句；口语化、简洁；复杂问题或用户要求详细时例外。",
                    "群聊动态注入过多时，也可以降低高强度合并条数，减少一次性喂给模型的信息量。",
                ],
            },
            {
                "title": "模型和 Provider 配置",
                "keywords": ["llm", "LLM", "模型", "provider", "Provider", "配置模型", "没配置", "子模型", "小模型", "默认模型", "超时", "timeout", "降级", "无有效json"],
                "summary": "插件多数能力默认跟随 AstrBot 当前会话模型；部分功能可以单独指定 Provider。未单独配置时通常不是没模型，而是会回退到主模型或相关默认模型；模型超时或 JSON 无效时，部分判定会降级本地规则。",
                "checks": [
                    "主聊天回复通常使用 AstrBot 当前会话选择的人格和 Provider。",
                    "可以先用快速配置只填 4 类：快速响应模型、复杂推理模型、创作模型、插件视觉模型；高级单项留空时会自动套用这些快速配置。",
                    "陪伴答疑优先使用 TROUBLESHOOTING_PROVIDER_ID，未填时依次回退到 RESPONSE_REVIEW_PROVIDER_ID、MAI_STYLE_PROVIDER_ID、LLM_PROVIDER_ID。",
                    "智能收口使用 SMART_MESSAGE_DEBOUNCE_PROVIDER_ID；留空时跟随插件主模型。",
                    "生图模型不等于聊天模型，需要在生图平台/后端配置里单独确认。",
                    "日志出现 Request timed out、无有效 JSON、降级本地判定时，说明这次不是人格问题，而是某个小模型/主模型没在预算内稳定返回。",
                    "如果提示 Provider 不可用、模型超时或空回复，再看对应功能的 Provider ID 和 Token 页错误。",
                ],
                "settings": [
                    "LLM_PROVIDER_ID",
                    "FAST_RESPONSE_PROVIDER_ID",
                    "COMPLEX_REASONING_PROVIDER_ID",
                    "CREATIVE_MODEL_PROVIDER_ID",
                    "TROUBLESHOOTING_PROVIDER_ID",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "MAI_STYLE_PROVIDER_ID",
                    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
                    "SMART_SILENCE_PROVIDER_ID",
                    "PHOTO_MODEL_PROVIDER_ID",
                    "PHOTO_PROMPT_PROVIDER_ID",
                ],
                "suggestions": [
                    "只想先跑通：保留子模型为空，让它跟随主模型。",
                    "想降低延迟：答疑、智能收口、复核类功能可填低延迟小模型。",
                    "生图失败时优先检查生图平台和图片模型，不要填普通聊天模型。",
                ],
            },
            {
                "title": "“我会牢牢记住你”联动和未安装兼容",
                "keywords": ["rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "专属记忆", "知识图谱", "未安装", "联动", "记忆查询"],
                "summary": "“我会牢牢记住你”是可选深度联动，不是陪伴插件硬依赖。安装后，陪伴插件会把日程、穿搭、创作、主动消息、用户习惯等结构化反馈给记忆插件；未安装时，本地关系网、状态和短期上下文仍照常工作。",
                "checks": [
                    "未安装“我会牢牢记住你”时，答疑、群聊、主动、状态模拟不会因此直接失效，只是少了长期结构化检索和图谱补充。",
                    "安装后，答疑会尝试读取近期排障/配置记忆，但本地运行状态、截图和日志仍优先。",
                    "私聊和群聊记忆边界仍由陪伴插件控制，不会把私聊隐私直接带进群聊。",
                    "如果联动导致超时，应该看到“我会牢牢记住你”读取失败或超时日志；陪伴插件会回退本地上下文。",
                ],
                "settings": [
                    "enable_companion_memory",
                    "enable_group_episode_memory",
                    "enable_group_privacy_guard",
                ],
                "suggestions": [
                    "想验证联动：先问穿搭、进食、最近主动消息这类明确事实，再看“我会牢牢记住你”是否有对应个人记忆。",
                    "担心 CPU 或超时：保持“我会牢牢记住你”为软依赖，不要把它放进每轮必须成功的阻塞链路。",
                ],
            },
            {
                "title": "主动消息不发或很少发",
                "keywords": ["主动", "不主动", "不发消息", "很久没发", "主动消息", "私聊主动"],
                "summary": "主动消息会受目标用户、每日上限、最小间隔、免打扰、休息状态、用户很久没回、主动价值复核和发送失败重试影响。",
                "checks": [
                    "确认用户在 target_user_ids 或私聊页已启用。",
                    "检查 max_daily_messages、min_interval_minutes、quiet_hours。",
                    "如果用户长期不回，主动会变短、变少，甚至延后。",
                    "如果开启主动消息价值复核，低价值或像打扰的消息会被改写/拦截。",
                ],
                "settings": [
                    "target_user_ids",
                    "max_daily_messages",
                    "min_interval_minutes",
                    "quiet_hours",
                    "proactive_review_strength",
                    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "先用“陪伴 查看主动判定”和扩展页排障看最近一次跳过原因。",
                    "调试期把每日上限设 2-5，间隔不要太短，更容易观察真实行为。",
                ],
            },
            {
                "title": "拟人身体状态、饥饿和生理期",
                "keywords": ["饥饿", "一直饿", "一天都是饥饿", "胃口", "健康状态", "不适状态", "生理期", "姨妈", "来月经", "情绪太低", "情绪过低", "拟人状态"],
                "summary": "拟人身体状态来自日程、时间段、状态强度和条件状态。当前逻辑里，生理期模拟开关开启就视为适用；关闭后会清理旧的生理期条件。饥饿状态如果长时间不退，多半是强度偏高或进食记录没有被日程/记忆及时更新。",
                "checks": [
                    "健康状态：enable_health_state。",
                    "饥饿/胃口：enable_hunger_state。",
                    "生理期：enable_cycle_state，开启就认为适用模拟。",
                    "状态强度：humanized_state_intensity，越高越容易把状态写进提示词和主动候选。",
                    "如果接入“我会牢牢记住你”，进食、穿搭、习惯等会尽量以可检索的个人记忆补充，但未安装时插件仍能用本地状态运行。",
                ],
                "settings": [
                    "enable_health_state",
                    "enable_hunger_state",
                    "enable_cycle_state",
                    "humanized_state_intensity",
                ],
                "suggestions": [
                    "一天都饿：先关 enable_hunger_state 或把 humanized_state_intensity 降到 30-40。",
                    "不想要生理期模拟：直接关闭 enable_cycle_state。",
                    "情绪/身体状态压过人格：降低状态强度，不要只改人格。"
                ],
            },
            {
                "title": "生图/自拍/参考图问题",
                "keywords": ["生图", "自拍", "参考图", "图片", "画图", "改图", "不出图", "脸", "分辨率", "没反应", "好了", "穿搭图", "提示词"],
                "summary": "生图链路会优先使用配置的在线 API，失败后按配置回退；参考图支持本地路径或 URL。自然语言生图默认关闭，避免和其他生图插件冲突；自拍会尽量结合今天穿搭和当前日程地点。",
                "checks": [
                    "确认 enable_photo_text_action 和生图后端配置可用。",
                    "自然语言生图/改图要单独打开 enable_natural_language_photo_generation。",
                    "参考图命令：陪伴 参考图 <本地图片路径|图片URL|清空>，也可带图或回复图片。",
                    "自然语言生图上限：natural_language_photo_generation_max_daily。",
                    "固定补充提示词：natural_language_photo_extra_prompt，可以配置不想每次重复写的画面要求。",
                    "排障页可看最近生图提示词、参考图数量、后端错误和任务状态。",
                ],
                "settings": [
                    "enable_photo_text_action",
                    "enable_natural_language_photo_generation",
                    "natural_language_photo_generation_max_daily",
                    "natural_language_photo_extra_prompt",
                    "photo_persona_reference_image_path",
                    "photo_generation_backend",
                    "photo_generation_fixed_prompt",
                    "photo_generation_scene_presets",
                ],
                "suggestions": [
                    "如果误触多，关闭自然语言生图，只保留主动拍照/自拍。",
                    "如果没反应，先确认自然语言生图开关、每日上限和是否被其他插件/主链工具抢走。",
                    "如果出图后只回“好了”，重点看图片任务回调和结果说明模板，而不是聊天主模型。",
                    "在线 API 报模型错误时，确认图片模型不是普通聊天模型。",
                ],
            },
            {
                "title": "QQ 空间评论或说说链路",
                "keywords": ["qq空间", "空间", "说说", "评论", "回复评论", "一直回复", "onebot", "cookie", "点赞", "首次使用", "第一次使用", "登录空间"],
                "summary": "QQ 空间功能依赖 OneBot/Cookie 能力，评论收件箱默认应谨慎开启，并记录已见评论 ID 防止重复回复。",
                "checks": [
                    "确认 enable_qzone_integration 和对应子功能开启。",
                    "第一次使用时，先在 OneBot/适配器所在账号完成 QQ 空间登录；排障提示“请先登录空间”通常表示 Cookie 不可用或已失效。",
                    "如果日志提示没有可用 OneBot 连接，通常是当前适配器没有暴露可取 Cookie 的连接。",
                    "点赞失效时优先跑排障页 QQ 空间测试，看 Cookie、g_tk、说说列表和点赞接口哪一步失败。",
                    "重复回复同一评论时，重点看 comment_inbox_seen_ids / replied_ids 是否保存。",
                    "可在排障页跑 QQ 空间测试。",
                ],
                "settings": [
                    "enable_qzone_integration",
                    "enable_qzone_life_publish",
                    "enable_qzone_comment_inbox",
                    "qzone_comment_inbox_interval_minutes",
                    "qzone_comment_inbox_recent_posts",
                    "qzone_comment_inbox_max_replies_per_tick",
                ],
                "suggestions": [
                    "评论回复建议低概率、长间隔、默认关闭，先用测试链路确认不会重复回复。",
                ],
            },
        ]

    def _companion_manual_select_entries(self, query: str) -> list[dict[str, Any]]:
        compact = re.sub(r"\s+", "", query).lower()
        issue_tags = self._companion_manual_issue_tags(query)
        entries = self._companion_manual_entries()
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in entries:
            score = 0
            entry_tags = self._companion_manual_entry_tags(entry)
            for keyword in entry.get("keywords", []):
                key = re.sub(r"\s+", "", str(keyword or "")).lower()
                if key and key in compact:
                    score += max(2, min(8, len(key)))
            title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
            if title and any(part and part in compact for part in re.split(r"[、/ ]+", title)):
                score += 2
            overlap = issue_tags & entry_tags
            if overlap:
                score += 6 * len(overlap)
            if "recent" in issue_tags and entry_tags & {"rest", "silence", "debounce", "group", "photo"}:
                score += 2
            if "location" in issue_tags and any(str(item or "").strip() for item in entry.get("settings", [])):
                score += 1
            if issue_tags and entry_tags and not overlap:
                score -= 3
            if "photo" in issue_tags and entry_tags & {"group", "debounce"} and "group" not in issue_tags:
                score -= 8
            if "silence" in issue_tags and entry_tags == {"group"}:
                score -= 8
            if "rest" in issue_tags and entry_tags & {"group", "photo"}:
                score -= 6
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:3]]

    def _companion_manual_context_text(self, selected: list[dict[str, Any]] | None = None) -> str:
        entries = self._companion_manual_entries()
        selected_titles = {
            _single_line(item.get("title"), 80)
            for item in (selected or [])
            if isinstance(item, dict) and _single_line(item.get("title"), 80)
        }
        ordered: list[dict[str, Any]] = []
        if selected:
            ordered.extend(item for item in selected if isinstance(item, dict))
        ordered.extend(
            item
            for item in entries
            if isinstance(item, dict) and _single_line(item.get("title"), 80) not in selected_titles
        )
        blocks: list[str] = []
        for entry in ordered:
            title = _single_line(entry.get("title"), 80)
            if not title:
                continue
            checks = "；".join(str(item) for item in entry.get("checks", [])[:6] if str(item or "").strip())
            suggestions = "；".join(str(item) for item in entry.get("suggestions", [])[:4] if str(item or "").strip())
            settings = "；".join(
                self._companion_manual_config_ref(str(item), include_location=True)
                for item in entry.get("settings", [])[:12]
                if str(item or "").strip()
            )
            blocks.append(
                "\n".join(
                    [
                        f"【{title}】",
                        f"逻辑：{entry.get('summary') or ''}",
                        f"检查：{checks or '无'}",
                        f"建议：{suggestions or '无'}",
                        f"配置键：{settings or '无'}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _companion_manual_local_answer(self, event: AstrMessageEvent, question: str) -> tuple[str, list[dict[str, Any]]]:
        query = _single_line(question, 260)
        if not query:
            return (
                "可以这样问：\n"
                "陪伴 答疑 群聊里面老是不回复或者好久才回复是什么情况\n"
                "陪伴 答疑 群连续对话在哪里进行设置\n"
                "陪伴 答疑 高强度收口和连续对话会不会冲突\n"
                "陪伴 答疑 智能收口为什么会等几秒\n"
                "陪伴 答疑 说句晚安后为什么不回消息了\n"
                "陪伴 答疑 智能沉默会不会在发送前拦截\n"
                "陪伴 答疑 自然语言生图怎么关闭"
            ), []
        selected = self._companion_manual_select_entries(query)
        if not selected:
            return (
                "这句我不太确定你想查哪一块功能。\n"
                "你可以直接问具体场景，比如：群聊不回复、连续对话、高强度收口、智能收口、休息闸门、智能沉默、主动消息、生图、模型配置、QQ 空间。\n"
                "如果是在查刚刚那次异常，问“为什么刚才没回复/为什么等了几秒/为什么没生图”会更准。"
            ), []
        issue_tags = self._companion_manual_issue_tags(query)
        mentioned_keys = self._companion_manual_mentioned_config_keys(query)
        for key in self._companion_manual_config_keys_from_alias_text(query, limit=4):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        lines = []
        group_note = self._companion_manual_current_group_note(event)
        if group_note:
            lines.append(group_note)
        primary = selected[0]
        if "location" in issue_tags and mentioned_keys:
            config_lines = []
            for key in mentioned_keys[:4]:
                current = self._companion_manual_current_config_value(key)
                config_lines.append(
                    f"{self._companion_manual_config_ref(key)}｜当前值：{self._companion_manual_format_config_item_value(key, current)}"
                )
            lines.append("你要找的大概是：" + "；".join(config_lines))
        lines.append(f"我更倾向先看“{primary.get('title')}”。{primary.get('summary')}")
        if len(selected) > 1:
            other_titles = [
                _single_line(item.get("title"), 40)
                for item in selected[1:3]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            ]
            if other_titles:
                lines.append("也可能牵到：" + "、".join(other_titles))
        no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=2)
        if no_reply and ("recent" in issue_tags or any(word in query for word in ("刚才", "刚刚", "没回", "不回", "没回复", "为什么"))):
            lines.append("最近未回复记录：" + "；".join(no_reply))
        tests = self._companion_manual_recent_test_evidence(limit=2)
        if tests and (issue_tags & {"photo", "qzone"} or any(word in query for word in ("测试", "排障"))):
            lines.append("最近排障测试：" + "；".join(tests))
        checks = [str(item) for item in primary.get("checks", []) if str(item or "").strip()]
        if checks:
            lines.append("优先看：" + "；".join(_single_line(item, 130) for item in checks[:3]))
        suggestions = [str(item) for item in primary.get("suggestions", []) if str(item or "").strip()]
        if suggestions:
            lines.append("建议先试：" + "；".join(_single_line(item, 130) for item in suggestions[:2]))
        settings = [str(item) for item in primary.get("settings", []) if str(item or "").strip()]
        if settings:
            lines.append("相关配置：" + "；".join(self._companion_manual_config_ref(item) for item in settings[:5]))
        snapshot = self._companion_manual_relevant_setting_snapshot(selected, query)
        if snapshot:
            lines.append("当前值：" + "；".join(snapshot[:3]))
        return "\n".join(lines), selected

    def _companion_manual_local_hint_text(self, event: AstrMessageEvent, selected: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        group_note = self._companion_manual_current_group_note(event)
        if group_note:
            lines.append(group_note)
        if selected:
            titles = [
                _single_line(item.get("title"), 50)
                for item in selected[:3]
                if isinstance(item, dict) and _single_line(item.get("title"), 50)
            ]
            if titles:
                lines.append("本地初筛：" + " / ".join(titles))
            primary = selected[0] if isinstance(selected[0], dict) else {}
            checks_source = primary.get("checks") if isinstance(primary.get("checks"), list) else []
            suggestions_source = primary.get("suggestions") if isinstance(primary.get("suggestions"), list) else []
            checks = [
                _single_line(item, 120)
                for item in checks_source[:3]
                if _single_line(item, 120)
            ]
            suggestions = [
                _single_line(item, 120)
                for item in suggestions_source[:2]
                if _single_line(item, 120)
            ]
            if checks:
                lines.append("优先核对：" + "；".join(checks))
            if suggestions:
                lines.append("可参考建议：" + "；".join(suggestions))
        else:
            lines.append("本地关键词没有稳定定位，需根据完整说明书和运行状态自行判断。")
        snapshot = [
            _single_line(item, 120)
            for item in self._companion_manual_relevant_setting_snapshot(selected, "")
            if _single_line(item, 120)
        ]
        if snapshot:
            lines.append("关键配置概览：" + "；".join(snapshot))
        return "\n".join(line for line in lines if line)

    async def _companion_manual_model_answer(
        self,
        event: AstrMessageEvent,
        question: str,
        local_answer: str,
        selected: list[dict[str, Any]],
        media_context: str = "",
    ) -> str:
        caller = getattr(self, "_llm_call", None)
        if not callable(caller):
            return ""
        provider_selector = getattr(self, "_task_provider", None)
        if callable(provider_selector):
            provider_id = provider_selector(
                getattr(self, "troubleshooting_provider_id", ""),
                getattr(self, "response_review_provider_id", ""),
                getattr(self, "mai_style_provider_id", ""),
                getattr(self, "llm_provider_id", ""),
            )
        else:
            provider_id = str(
                getattr(self, "troubleshooting_provider_id", "")
                or getattr(self, "response_review_provider_id", "")
                or getattr(self, "mai_style_provider_id", "")
                or getattr(self, "llm_provider_id", "")
                or ""
            )
        if not provider_id:
            return ""
        manual_context = self._companion_manual_context_text(selected)
        local_hint = self._companion_manual_local_hint_text(event, selected) or self._companion_manual_clean_multiline(local_answer, limit=900)
        selected_hint = (
            "关键词初筛命中：" + " / ".join(_single_line(item.get("title"), 60) for item in selected if isinstance(item, dict))
            if selected
            else "关键词初筛未命中；请直接阅读完整说明书判断，不要把“未命中”当成答案。"
        )
        mentioned_keys = self._companion_manual_mentioned_config_keys(question)
        for key in self._companion_manual_config_keys_from_alias_text(question, limit=6):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        mentioned_config_text = (
            "\n".join(f"- {self._companion_manual_config_ref(key)}" for key in mentioned_keys)
            if mentioned_keys
            else "无"
        )
        recent_context = self._companion_manual_recent_context_text(event) or "没有同一会话内的上一轮答疑上下文。"
        persona_text = ""
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                await asyncio.wait_for(refresher(str(getattr(event, "unified_msg_origin", "") or "")), timeout=1.5)
            except Exception:
                pass
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                persona_text = _single_line(getter(), 700)
            except Exception:
                persona_text = ""
        runtime = self._companion_manual_runtime_snapshot(event)
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="companion_manual_diagnosis",
                    query=(
                        f"陪伴插件答疑排障：{question}；"
                        "最近配置变动、失败日志、主动消息、群聊回复、自然语言生图、QQ空间、用户反馈、排障上下文"
                    ),
                    event=event,
                    top_k=5,
                    max_chars=900,
                    timeout_seconds=2.0,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
你是 AstrBot 陪伴插件当前人格下的答疑助手。用户不是在闲聊,是在问插件功能为什么这样运行。

要求：
- 根据“完整功能说明书”和“当前运行状态”判断最可能原因,不要泛泛复述所有可能性。
- 如果“本轮图片/引用图片上下文”有内容,要把它当作用户给的截图/报错/UI 线索一起判断。
- 如果证据不足,明确说“更像是/需要看日志确认”,不要装作确定。
- 回复要像当前人格在群里解释,不是后台报告；保留人格语气,但不要编造事实、不要撒娇过头影响清晰度。
- 默认 4-8 行内说清楚：先一句结论,再说明关键原因,最后给 1-2 条最有用建议。
- 不要输出“问题/大概结论/优先检查/相关配置/当前关键配置/诊断依据”这种报告标题。
- 不要把完整配置快照逐条贴给用户；只有真正要调的配置才提。
- 只能使用“完整功能说明书”里出现过的配置项；不要编造不存在的配置项。
- 提到配置时必须同时写中文名和参数名,格式类似“高强度唤醒阈值（group_high_intensity_wakeup_threshold）”。
- 如果“用户明确提到的配置项”不是“无”,回答里要告诉用户它在拓展页配置页的具体位置。
- 涉及调参时不要只说“改成/设为 X”；必须尽量写成“由 当前值 改为 目标值”。当前值不知道时,写“由当前值确认后改为 X”。
- 可执行改配置由本地白名单规则另行生成；你只负责解释和建议,不要声称已经修改配置。
- 语气口语化,像插件作者在排障,不要写客服套话,不要输出表格。
- 不要说“内置说明书没匹配到”“关键词没命中”“去扩展页排障中心”这类暴露实现的话；如果不确定,就自然说明需要更具体的现象或日志。
- 不要要求用户复制文件；用户和你在同一机器上。

【用户问题】
{_single_line(question, 260)}

【当前人格/说话风格参考】
{persona_text or '未读取到人格；保持简洁、自然、温和。'}

【同一会话上一轮答疑上下文】
{recent_context}

【本轮图片/引用图片上下文】
{media_context or '本轮没有检测到随消息携带或引用的图片。'}

【我会牢牢记住你 最近排障/配置记忆】
{memory_context or '暂无可用的近期记忆。'}
使用方式：只辅助理解这台实例最近发生过什么；本地运行状态、截图和日志证据优先。不要说“我查记忆发现”。

【检索提示】
{selected_hint}

【完整功能说明书】
{manual_context}

【用户明确提到的配置项】
{mentioned_config_text}

【当前运行状态快照】
{runtime or '没有拿到当前会话专项状态,只能按配置和说明书判断。'}

【本地规则初判】
{local_hint}

请输出：
一段自然答复。可以有很短的分行,但不要写成长报告。
""".strip()
        try:
            raw = await asyncio.wait_for(
                caller(
                    prompt,
                    max_tokens=700,
                    provider_id=provider_id,
                    task="companion_manual_diagnosis",
                ),
                timeout=6.0,
            )
        except asyncio.TimeoutError:
            logger.info("[PrivateCompanion] 陪伴答疑模型诊断超时,回退本地说明: question=%s", _single_line(question, 120))
            return ""
        except Exception as exc:
            logger.info("[PrivateCompanion] 陪伴答疑模型诊断失败,回退本地说明: %s", _single_line(exc, 120))
            return ""
        return self._companion_manual_clean_multiline(raw, limit=1800)

    async def _companion_manual_answer(self, event: AstrMessageEvent, question: str) -> str:
        query = self._companion_manual_clean_question_text(question, 260)
        media_context = await self._companion_manual_media_context(event, query)
        if not query and media_context:
            query = "根据本轮携带或引用的图片做插件答疑/排障"
        local_answer, selected = self._companion_manual_local_answer(event, query)
        if not query:
            self._companion_manual_store_pending_config(event, query, [])
            return local_answer
        proposals = self._companion_manual_build_config_proposals(query, selected, event)
        token = self._companion_manual_store_pending_config(event, query, proposals)
        proposal_text = self._companion_manual_format_config_proposals_brief(token, proposals)
        model_answer = await self._companion_manual_model_answer(event, query, local_answer, selected, media_context=media_context)
        if model_answer:
            answer = model_answer
        else:
            answer = self._companion_manual_fallback_answer(event, query, selected, proposals, media_context=media_context)
        if proposal_text:
            answer = f"{answer}\n\n{proposal_text}"
        self._companion_manual_store_recent_context(event, question=query, answer=answer, proposals=proposals)
        return answer

    def _daily_outfit_command_payload(self) -> tuple[str, str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        today = _today_key()
        if not item:
            if not bool(getattr(self, "enable_daily_outfit_photo", False)):
                return (
                    "今天还没有每日穿搭图；每日穿搭照片当前没有开启。\n"
                    "需要的话，管理员可以在配置页开启“每日穿搭照片”，或手动用：陪伴 生成穿搭。",
                    "",
                )
            return "今天还没有生成每日穿搭图。管理员可以手动用：陪伴 生成穿搭。", ""
        date_key = _single_line(item.get("date"), 20)
        error = _single_line(item.get("error"), 180)
        note = _single_line(item.get("note"), 160)
        if date_key and date_key != today:
            suffix = f"\n上一次记录是 {date_key}。"
            if error:
                suffix += f"\n上次失败原因：{error}"
            return "今天还没有新的每日穿搭图。" + suffix + "\n管理员可以手动用：陪伴 生成穿搭。", ""
        path_text = _single_line(item.get("path"), 500).strip().strip('"').strip("'")
        if not path_text:
            reason = error or note or "没有可用图片路径"
            retry_count = int(item.get("retry_count", 0) or 0)
            retry_max = 5
            if retry_count > 0 and retry_count < retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n正在自动重试（第{retry_count}/{retry_max}次），稍后再来看看，或管理员手动用：陪伴 生成穿搭。", ""
            elif retry_count >= retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n已重试{retry_max}次仍未成功，管理员可以手动用：陪伴 生成穿搭。", ""
            return f"今天的每日穿搭图还没生成成功：{reason}\n管理员可以手动用：陪伴 生成穿搭。", ""
        try:
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / path
            path = path.resolve()
        except Exception:
            path = Path(path_text)
        try:
            exists = path.exists() and path.is_file()
        except (OSError, ValueError):
            exists = False
        if not exists:
            return "今天的每日穿搭图记录存在，但图片文件已经找不到了。\n管理员可以手动用：陪伴 生成穿搭。", ""
        if path.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
            return "今天的每日穿搭图记录存在，但图片格式不支持发送。管理员可以重新生成一次。", ""
        meta_parts = []
        generated_at = _safe_float(item.get("generated_at"), 0.0, 0.0)
        formatter = getattr(self, "_format_timestamp_elapsed", None)
        if generated_at > 0 and callable(formatter):
            meta_parts.append(f"生成：{formatter(generated_at)}")
        backend = _single_line(item.get("backend"), 40)
        if backend:
            meta_parts.append(f"后端：{backend}")
        caption = "今天的穿搭图在这里。"
        if meta_parts:
            caption += "\n" + "｜".join(meta_parts)
        return caption, str(path)

    def _photo_reference_image_dir(self) -> Path:
        target_dir = Path(self.data_dir) / "photo_reference_images"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _photo_reference_stem(self, stem: str = "reference") -> str:
        clean = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(stem or "reference")).strip("._")
        if not clean:
            clean = "reference"
        return f"{clean}_{int(_now_ts() * 1000)}_{uuid.uuid4().hex[:8]}"

    def _photo_reference_copy_local_file(self, source_path: Path, *, stem: str = "reference") -> str:
        try:
            resolved = source_path.resolve()
        except Exception:
            resolved = source_path
        if not resolved.exists() or not resolved.is_file():
            return ""
        suffix = resolved.suffix.lower()
        if suffix not in _PHOTO_REFERENCE_SUFFIXES:
            return ""
        target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
        shutil.copy2(resolved, target)
        return str(target.resolve())

    def _photo_reference_write_data_image(self, source: str, *, stem: str = "reference") -> str:
        text = str(source or "").strip()
        try:
            if text.startswith("base64://"):
                raw = base64.b64decode(text[len("base64://"):], validate=False)
                suffix = ".jpg"
            elif text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                if ";base64" not in meta.lower():
                    return ""
                raw = base64.b64decode(payload, validate=False)
                lowered = meta.lower()
                suffix = ".png" if "png" in lowered else ".webp" if "webp" in lowered else ".jpg"
            else:
                return ""
            if not raw:
                return ""
            target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
            target.write_bytes(raw)
            return str(target.resolve())
        except Exception:
            return ""

    async def _photo_reference_source_to_stable_path(self, source: str, *, stem: str = "reference", event: AstrMessageEvent | None = None) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        data_path = self._photo_reference_write_data_image(text, stem=stem)
        if data_path:
            return data_path
        if re.match(r"^https?://", text, flags=re.I):
            downloader = getattr(self, "_persist_private_remote_image_source", None)
            if callable(downloader):
                try:
                    downloaded = await downloader(text, self._photo_reference_image_dir(), self._photo_reference_stem(f"{stem}_remote"))
                except Exception:
                    downloaded = ""
                if downloaded:
                    return self._photo_reference_copy_local_file(Path(downloaded), stem=stem) or downloaded
            return ""
        local_text = text[len("file://"):] if text.startswith("file://") else text
        try:
            copied = self._photo_reference_copy_local_file(Path(local_text), stem=stem)
            if copied:
                return copied
        except (OSError, ValueError):
            pass
        resolver = getattr(self, "_qzone_resolve_onebot_image_source", None)
        if callable(resolver) and event is not None:
            try:
                resolved = await resolver(event, text)
            except Exception:
                resolved = ""
            if resolved and resolved != text:
                return await self._photo_reference_source_to_stable_path(resolved, stem=stem, event=event)
        return ""

    async def _photo_reference_sources_from_current_event(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        persister = getattr(self, "_persist_private_inbound_images", None)
        if callable(persister):
            try:
                for source in await persister(event, user_id):
                    add(source)
            except Exception:
                pass
        raw_extractor = getattr(self, "_raw_private_image_sources", None)
        if callable(raw_extractor):
            try:
                for source in raw_extractor(event):
                    add(source)
            except Exception:
                pass
        return sources

    def _photo_reference_sources_from_reply_cache(self, event: AstrMessageEvent) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        cleanup = getattr(self, "_cleanup_recall_message_cache", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            return sources
        id_getter = getattr(self, "_event_reply_message_ids", None)
        message_ids = id_getter(event) if callable(id_getter) else []
        scope_getter = getattr(self, "_event_scope_key", None)
        current_scope = _single_line(scope_getter(event), 160) if callable(scope_getter) else ""
        item_getter = getattr(self, "_recall_image_items_from_snapshot", None)
        for message_id in message_ids:
            snapshot = cache.get(message_id)
            if not isinstance(snapshot, dict):
                continue
            snapshot_scope = _single_line(snapshot.get("scope"), 160)
            if current_scope and snapshot_scope and snapshot_scope != current_scope:
                continue
            if callable(item_getter):
                try:
                    items = item_getter(snapshot)
                except Exception:
                    items = []
            else:
                raw_items = snapshot.get("image_items") if isinstance(snapshot.get("image_items"), list) else []
                items = [item for item in raw_items if isinstance(item, dict)]
            for item in items:
                if not isinstance(item, dict):
                    continue
                tier = _single_line(item.get("tier"), 40)
                source = str(item.get("source") or "").strip()
                if not source or tier in {"placeholder", "platform_file"}:
                    continue
                add(source)
            for source in snapshot.get("images") if isinstance(snapshot.get("images"), list) else []:
                add(source)
        return sources

    async def _photo_reference_sources_from_reply_event(self, event: AstrMessageEvent) -> list[str]:
        cached = getattr(event, "_private_companion_photo_reply_sources", None)
        if isinstance(cached, list):
            return [str(item).strip() for item in cached if str(item or "").strip()]
        sources: list[str] = []
        finder = getattr(self, "_find_reply_image_sources_for_event", None)
        if callable(finder):
            try:
                for source in await finder(event):
                    text = str(source or "").strip()
                    if text and text not in sources:
                        sources.append(text)
            except Exception:
                sources = []
        try:
            setattr(event, "_private_companion_photo_reply_sources", list(sources))
        except Exception:
            pass
        return sources

    async def _photo_reference_image_from_command_context(
        self,
        event: AstrMessageEvent,
        user_id: str,
    ) -> tuple[str, str, bool]:
        saw_image = False
        for source in await self._photo_reference_sources_from_current_event(event, user_id):
            saw_image = True
            path = await self._photo_reference_source_to_stable_path(source, stem="message", event=event)
            if path:
                return path, "随消息发送的图片", True
        for source in self._photo_reference_sources_from_reply_cache(event):
            saw_image = True
            path = await self._photo_reference_source_to_stable_path(source, stem="reply", event=event)
            if path:
                return path, "引用消息里的图片", True
        for source in await self._photo_reference_sources_from_reply_event(event):
            saw_image = True
            path = await self._photo_reference_source_to_stable_path(source, stem="reply", event=event)
            if path:
                return path, "引用消息里的图片", True
        return "", "", saw_image

    def _resolve_photo_reference_command_path(self, value: str) -> tuple[str, str]:
        raw = _single_line(value, 1000).strip().strip('"').strip("'")
        if not raw:
            return "", "请这样设置：陪伴 参考图 <本地图片路径或图片URL>"
        if re.match(r"^https?://", raw, flags=re.I):
            return raw, ""
        expanded = os.path.expandvars(os.path.expanduser(raw))
        candidates = [Path(expanded)]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / expanded)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if not resolved.exists() or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
                return "", "参考图只支持 png、jpg、jpeg、webp。"
            return str(resolved), ""
        return "", "没有找到这张本地图片。请确认路径存在，并且 Bot 所在机器能访问；也可以直接填写 http(s) 图片 URL。"

    def _set_photo_reference_config_path(self, path: str) -> bool:
        clean = _single_line(path, 260)
        self.photo_persona_reference_image_path = clean
        try:
            saved = _set_into_config(self.config, "photo_persona_reference_image_path", clean)
            if saved:
                self._save_config_if_possible()
            return bool(saved)
        except Exception:
            return False

    async def _photo_reference_command_text(self, event: AstrMessageEvent, user_id: str, value: str = "") -> str:
        action = _single_line(value, 1000)
        if action in {"清空", "删除", "移除", "clear", "none", "空"}:
            saved = self._set_photo_reference_config_path("")
            return "已清空主动自拍人设参考图。" + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
        force_image = action in {"图片", "这张", "这张图", "引用", "引用图", "引用图片", "设置", "更换", "更新", "添加", "上传", "用这张", "使用这张"}
        if action in {"查看", "状态", "当前", "current", "show"}:
            force_image = False
        if not action or force_image:
            image_path, image_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            if image_path:
                saved = self._set_photo_reference_config_path(image_path)
                return (
                    f"已把{image_label}设为主动自拍人设参考图：\n"
                    f"{image_path}\n"
                    "只会在 selfie/人像类主动生图里使用；ComfyUI 需要支持 images=1 的自拍工作流。"
                    + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
                )
            if force_image:
                if saw_image:
                    return "找到了图片，但没能保存成参考图。参考图只支持 png、jpg、jpeg、webp；也可能是平台只给了图片 file id，拿不到原图。"
                return "没有在这条消息或引用消息里找到图片。可以发送图片并附上“陪伴 参考图”，或回复一条近期图片消息发送“陪伴 参考图”。"
        if not action or action in {"查看", "状态", "当前", "current", "show"}:
            configured = _single_line(getattr(self, "photo_persona_reference_image_path", ""), 260)
            resolved = self._photo_persona_reference_image_path() if callable(getattr(self, "_photo_persona_reference_image_path", None)) else ""
            if not configured:
                return "当前没有设置主动自拍人设参考图。\n设置方式：陪伴 参考图 <本地图片路径或图片URL>；也可以发送图片并附上“陪伴 参考图”。"
            status = "可用" if resolved else "URL 待首次使用时下载" if re.match(r"^https?://", configured.strip(), flags=re.I) else "路径不可用或格式不支持"
            return (
                "当前主动自拍人设参考图：\n"
                f"{configured}\n"
                f"状态：{status}"
            )
        path, error = self._resolve_photo_reference_command_path(action)
        if error:
            return error
        stable_path = await self._photo_reference_source_to_stable_path(path, stem="manual") or path
        saved = self._set_photo_reference_config_path(stable_path)
        return (
            "已设置主动自拍人设参考图：\n"
            f"{stable_path}\n"
            "只会在 selfie/人像类主动生图里使用；ComfyUI 需要支持 images=1 的自拍工作流。"
            + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
        )

    def _natural_language_photo_explicit_plugin_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        plugin_hit = any(
            token in compact
            for token in (
                "插件能力",
                "插件生图",
                "插件画图",
                "用插件",
                "走插件",
                "陪伴能力",
                "陪伴插件",
                "本插件",
            )
        )
        if not plugin_hit:
            return False
        return any(
            token in compact
            for token in (
                "画",
                "绘图",
                "生图",
                "出图",
                "生成图",
                "生成图片",
                "图片",
                "照片",
                "改图",
                "修图",
                "重绘",
            )
        )

    def _natural_language_photo_disabled_text(self, reason: str = "natural_off") -> str:
        if reason == "photo_off":
            return (
                "插件的主动拍照/生图总开关现在没开，所以不能走插件生图链路。\n"
                "位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图。"
            )
        return (
            "插件的自然语言生图/改图入口现在没开，所以这句不会被插件接管。\n"
            "位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图详情 -> 自然语言生图/改图。"
        )

    def _natural_language_photo_intent(
        self,
        text: str,
        *,
        has_reference: bool = False,
        directed: bool = False,
    ) -> dict[str, Any]:
        raw = re.sub(r"\[CQ:image,[^\]]+\]", "", str(text or ""))
        raw = re.sub(r"\[CQ:at,[^\]]+\]", "", raw)
        raw = re.sub(r"\[(?:At|@):[^\]]+\]", "", raw, flags=re.I)
        raw = re.sub(r"\[(?:引用消息|回复消息|reply)\]", "", raw, flags=re.I)
        raw = _single_line(raw, 800)
        if not raw:
            return {}
        compact = re.sub(r"\s+", "", raw)
        selfie_markers = ("自拍", "拍照", "拍张照", "拍一张照", "拍一张照片", "拍张照片", "来张自拍", "发张自拍", "发一张自拍")
        selfie_hit = any(marker in compact for marker in selfie_markers)
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(raw)
        draw_visual_targets = ("图片", "照片", "插画", "头像", "壁纸", "表情包", "自拍", "拍照", "画卷", "图")
        edit_visual_targets = draw_visual_targets + ("这张", "这个图", "引用图")
        edit_strong_markers = ("改图", "修图", "重绘", "p图", "P图", "p一下", "P一下")
        edit_operation_markers = ("改成", "改为", "改一下", "p成", "P成", "换成", "变成", "加上", "加个", "去掉", "去除")
        draw_patterns = (
            r"(?:帮我|给我|替我|请你|麻烦你)?(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?.{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画一张|画个|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画|生成|做|出).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成一张|生一张|做一张|做个|出一张).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:来|整)(?:一张|张|个).{0,40}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
        )
        draw_hit = any(re.search(pattern, raw, flags=re.I) for pattern in draw_patterns)
        if draw_hit and not any(token in compact for token in draw_visual_targets):
            draw_hit = False
        if selfie_hit and directed:
            draw_hit = True
        if not draw_hit and explicit_plugin_request:
            draw_hit = True
        if not draw_hit and directed:
            bare_draw_patterns = (
                r"^(?:帮我|给我|替我|请你|请|麻烦你)?(?:画一张|画个|画一下|画一个|画|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个)\S{1,120}",
                r"^(?:帮我|给我|替我|请你|请|麻烦你)(?:画|生成|做|出|整)\S{1,120}",
            )
            draw_hit = any(re.search(pattern, compact, flags=re.I) for pattern in bare_draw_patterns)
            if draw_hit and re.search(r"(?:画个饼|画饼|规划|画重点|画大饼|画风|图个|图啥|图什么)", compact, flags=re.I):
                draw_hit = False
        edit_hit = False
        if has_reference:
            explicit_visual_target = any(token in compact for token in edit_visual_targets)
            strong_edit = any(marker in compact for marker in edit_strong_markers)
            operation_edit = any(marker in compact for marker in edit_operation_markers)
            leading_operation = any(compact.startswith(marker) for marker in edit_operation_markers)
            implicit_directed_edit = bool(
                directed
                and not re.search(r"(?:什么|怎么|为啥|为什么|吗|呢|？|\?)", compact)
                and any(
                    marker in compact
                    for marker in (
                        "红色",
                        "蓝色",
                        "绿色",
                        "黑色",
                        "白色",
                        "粉色",
                        "紫色",
                        "黄色",
                        "基调",
                        "色调",
                        "风格",
                        "背景",
                        "滤镜",
                        "清晰",
                        "高清",
                        "二次元",
                        "写实",
                        "赛博",
                    )
                )
            )
            edit_hit = bool(strong_edit or (operation_edit and (explicit_visual_target or leading_operation)) or implicit_directed_edit)
        if not draw_hit and not edit_hit:
            return {}
        prompt = raw
        cleanup_patterns = [
            r"^(?:麻烦|可以|能不能|能|帮我|给我|替我|请你|请)?",
            r"^(?:拍一张|拍张|拍个|拍一下|发一张|发张|来一张|来张)(?:自拍|照片|照|图片|图)?",
            r"^(?:用|走)?(?:这个|你|本)?(?:插件能力|插件|陪伴能力|陪伴插件)(?:来|去)?",
            r"^(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:画一张|画个|画一下|画一个|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|图)?",
            r"^(?:把)?(?:这张图|这个图|这张|引用图|图片)?(?:帮我)?(?:改成|改为|改一下|改图|修图|重绘|p成|P成|换成|变成)",
        ]
        for pattern in cleanup_patterns:
            prompt = re.sub(pattern, "", prompt, count=1, flags=re.I).strip()
        prompt = prompt.strip(" ，,。.!！?？:：；;")
        if selfie_hit and prompt in {"", "看看", "看一下", "看看吧", "看看嘛"}:
            prompt = "拍一张自拍"
        if not prompt or prompt in {"图", "图片", "一张图", "这张", "这张图"}:
            return {
                "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
                "prompt": "",
                "needs_prompt": True,
            }
        return {
            "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
            "prompt": _single_line(prompt, 700),
            "raw": raw,
        }

    def _natural_language_photo_quota_left(self, user: dict[str, Any]) -> int:
        limit = max(0, _safe_int(getattr(self, "natural_language_photo_generation_max_daily", 0), 0))
        if limit <= 0:
            return 0
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        used = _safe_int(user.get("natural_photo_generated_today"), 0)
        if str(user.get("natural_photo_generated_day") or "") != today:
            used = 0
        return max(0, limit - used)

    def _note_natural_language_photo_generation_attempt(self, user: dict[str, Any], image_path: str = "") -> None:
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        if user.get("natural_photo_generated_day") != today:
            user["natural_photo_generated_day"] = today
            user["natural_photo_generated_today"] = 0
        user["natural_photo_generated_today"] = _safe_int(user.get("natural_photo_generated_today"), 0) + 1
        user["last_natural_photo_path"] = _single_line(image_path, 260)
        user["last_natural_photo_at"] = _now_ts()

    def _build_natural_language_photo_prompt(
        self,
        *,
        prompt: str,
        kind: str,
        has_reference: bool,
        memory_context: str = "",
    ) -> str:
        style_name, style_instruction = self._get_photo_style_instruction() if callable(getattr(self, "_get_photo_style_instruction", None)) else ("默认", "")
        extra_prompt = str(
            getattr(self, "natural_language_photo_extra_prompt", DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT)
            or ""
        ).strip()
        if kind == "edit" and has_reference:
            base = (
                "基于用户提供或引用的参考图进行改图。"
                f"用户要求：{prompt}。"
                "尽量保留用户未要求修改的主体、构图和重要细节，只改变明确要求的部分。"
            )
        elif kind == "selfie":
            base = (
                "根据用户自然语言请求生成角色自拍。"
                f"用户要求：{prompt or '拍一张自拍'}。"
                "角色本人必须露脸，脸、发型、表情和上半身/穿搭要清楚；优先保持今天穿搭、人设参考图和角色外观一致。"
            )
        else:
            base = f"根据用户自然语言请求生成图片。用户要求：{prompt}。"
        return _single_line(
            " ".join(
                part
                for part in [
                    base,
                    f"我会牢牢记住你 相关记忆：{_single_line(memory_context, 760)}；使用方式：优先保持今日穿搭、当前地点、最近自拍和用户偏好的一致性。" if memory_context else "",
                    extra_prompt,
                    f"风格：{style_name}；{style_instruction}",
                ]
                if str(part or "").strip()
            ),
            6500,
        )

    def _natural_language_photo_ack_text(self, *, kind: str, has_reference: bool) -> str:
        return "等我一下。"

    def _natural_language_photo_done_text(self, *, kind: str, reference_label: str = "") -> str:
        return "好了，你看。"

    def _natural_language_photo_ack_reference(self, *, kind: str, has_reference: bool) -> str:
        if kind == "edit" or has_reference:
            return "参考意图：已经拿到参考图，开始按用户要求改图；让用户稍等，不要描述工具执行过程。"
        if kind == "selfie":
            return "参考意图：用户要角色自拍，先轻轻回应会去准备；让用户稍等，不要承诺额外内容。"
        return "参考意图：用户要生成图片，先轻轻回应会去画；让用户稍等，不要描述工具执行过程。"

    def _natural_language_photo_done_reference(self, *, kind: str, reference_label: str = "") -> str:
        if kind == "edit":
            label = _single_line(reference_label, 24) or "这张图"
            return f"参考意图：图片已按{label}改好，提醒用户看图；语气自然短一点。"
        if kind == "selfie":
            return "参考意图：自拍图片已经完成，提醒用户看图；语气自然短一点。"
        return "参考意图：图片已经完成，提醒用户看图；语气自然短一点。"

    async def _natural_language_photo_ack_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        has_reference: bool,
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_ack_reference(kind=kind, has_reference=has_reference),
                scene="自然语言生图/改图已接单，生成前短回执",
                user=user,
                event=event,
                fallback_text="等我一下。",
                task="natural_photo_ack_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "等我一下。"

    async def _natural_language_photo_done_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        reference_label: str = "",
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_done_reference(kind=kind, reference_label=reference_label),
                scene="自然语言生图/改图已完成，随图短标题",
                user=user,
                event=event,
                fallback_text="好了，你看。",
                task="natural_photo_done_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "好了，你看。"

    async def _maybe_handle_natural_language_photo_request(
        self,
        event: AstrMessageEvent,
        user_id: str,
        text: str,
        *,
        directed: bool = False,
    ) -> bool:
        text = _single_line(text, 800)
        if not text or text.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴")):
            return False
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(text)
        if not getattr(self, "enable_photo_text_action", False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
                event.stop_event()
                return True
            return False
        if not getattr(self, "enable_natural_language_photo_generation", False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("natural_off"))
                event.stop_event()
                return True
            return False
        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_intent"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 自然语言生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False
        intent = self._natural_language_photo_intent(text, has_reference=has_reference, directed=directed)
        if not intent:
            if directed:
                logger.info(
                    "[PrivateCompanion] 定向自然语言生图未命中意图: user=%s has_reference=%s text=%s",
                    _single_line(user_id, 40),
                    has_reference,
                    _single_line(text, 180),
                )
            return False
        logger.info(
            "[PrivateCompanion] 自然语言生图命中: user=%s kind=%s has_reference=%s prompt=%s raw=%s",
            _single_line(user_id, 40),
            _single_line(intent.get("kind"), 30),
            has_reference,
            _single_line(intent.get("prompt"), 180),
            _single_line(text, 180),
        )
        if intent.get("needs_prompt"):
            await self._reply(event, "要画成什么样？给我一句具体点的描述就行。")
            event.stop_event()
            return True
        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._is_target_private_user(user_id, user) or not bool(user.get("enabled", True)):
                if directed:
                    logger.info(
                        "[PrivateCompanion] 定向自然语言生图已命中但用户无权限: user=%s enabled=%s text=%s",
                        _single_line(user_id, 40),
                        bool(user.get("enabled", True)) if isinstance(user, dict) else False,
                        _single_line(text, 160),
                    )
                return False
            if self._private_user_role(user, user_id) == "friend":
                await self._reply(event, "这个自然语言生图/改图入口只给主人开放。")
                event.stop_event()
                return True
            if self._natural_language_photo_quota_left(user) <= 0:
                await self._reply(event, "今天自然语言生图/改图额度用完了。")
                event.stop_event()
                return True
        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True
        reference_path = ""
        reference_label = ""
        if intent.get("kind") == "edit":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 自然语言改图参考图解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                await self._reply(event, f"改图参考图解析缺少可选依赖 {missing}，这次先不改图。")
                event.stop_event()
                return True
            logger.info(
                "[PrivateCompanion] 自然语言改图参考图解析: user=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path:
                await self._reply(
                    event,
                    "我没拿到要改的图。可以把图片和要求一起发，或者引用一张近期图片再说“改成……”。"
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时改不了。",
                )
                event.stop_event()
                return True
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="natural_photo",
                    query=(
                        f"自然语言生图 {intent.get('kind') or ''} {intent.get('prompt') or ''} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""
        prompt_text = self._build_natural_language_photo_prompt(
            prompt=str(intent.get("prompt") or ""),
            kind=str(intent.get("kind") or "text2img"),
            has_reference=bool(reference_path),
            memory_context=memory_context,
        )
        intent_kind = str(intent.get("kind") or "text2img")
        workflow_kind = "selfie" if reference_path or intent_kind == "selfie" else "text2img"
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user,
            kind=intent_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                session_key=f"natural_photo_{user_id}",
                reference_image_path=reference_path,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 自然语言生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "[PrivateCompanion] 自然语言生图结果: user=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_natural_language_photo_generation_attempt(user, image_path=image_path)
                self._save_data_sync()
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入自然语言生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user,
            kind=intent_kind,
            reference_label=reference_label,
        )
        chain = self._build_outbound_chain(caption, image_path)
        try:
            await event.send(self._build_result_from_chain(chain))
        except Exception:
            await event.send(event.chain_result(chain))
        event.stop_event()
        return True

    async def _handle_companion_photo_command(
        self,
        event: AstrMessageEvent,
        user_id: str,
        action: str,
        value: str,
    ) -> bool:
        """Run the plugin image backend from an explicit /陪伴 command."""
        action_text = _single_line(action, 24)
        prompt = _single_line(value, 800).strip()
        action_kind_map = {
            "自拍": "selfie",
            "拍照": "selfie",
            "拍一张": "selfie",
            "改图": "edit",
            "修图": "edit",
            "重绘": "edit",
            "P图": "edit",
            "p图": "edit",
        }
        forced_kind = action_kind_map.get(action_text, "text2img")
        if not getattr(self, "enable_photo_text_action", False):
            await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
            event.stop_event()
            return True

        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_quota"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 指令生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False

        compact = re.sub(r"\s+", "", prompt)
        if forced_kind == "text2img":
            if any(marker in compact for marker in ("自拍", "拍照", "拍张照", "拍一张照", "来张自拍", "发张自拍")):
                forced_kind = "selfie"
            elif has_reference and any(marker in compact for marker in ("改图", "修图", "重绘", "p图", "P图", "改成", "改为", "换成", "变成", "加上", "去掉", "去除")):
                forced_kind = "edit"

        if forced_kind == "selfie" and not prompt:
            prompt = "拍一张自拍"
        if not prompt:
            usage = (
                "请这样使用：\n"
                "陪伴 生图 <画面描述>\n"
                "陪伴 自拍 [画面要求]\n"
                "陪伴 改图 <修改要求>（需要带图或回复图片）"
            )
            await self._reply(event, usage)
            event.stop_event()
            return True

        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._is_target_private_user(user_id, user) or not bool(user.get("enabled", True)):
                await self._reply(event, "这个生图入口只对已启用的陪伴对象开放。")
                event.stop_event()
                return True
            if self._private_user_role(user, user_id) == "friend":
                await self._reply(event, "这个指令生图/改图入口只给主人开放。")
                event.stop_event()
                return True
            if self._natural_language_photo_quota_left(user) <= 0:
                await self._reply(event, "今天指令生图/改图额度用完了。")
                event.stop_event()
                return True

        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True

        reference_path = ""
        reference_label = ""
        if forced_kind == "edit":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 指令改图参考图解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                await self._reply(event, f"改图参考图解析缺少可选依赖 {missing}，这次先不改图。")
                event.stop_event()
                return True
            logger.info(
                "[PrivateCompanion] 指令改图参考图解析: user=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path:
                await self._reply(
                    event,
                    "我没拿到要改的图。可以把图片和“陪伴 改图 <要求>”一起发，或者引用近期图片再用这个指令。"
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时改不了。",
                )
                event.stop_event()
                return True

        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="command_photo",
                    query=(
                        f"指令生图 {forced_kind} {prompt} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""

        prompt_text = self._build_natural_language_photo_prompt(
            prompt=prompt,
            kind=forced_kind,
            has_reference=bool(reference_path),
            memory_context=memory_context,
        )
        workflow_kind = "selfie" if reference_path or forced_kind == "selfie" else "text2img"
        async with self._data_lock:
            user = self._get_user(user_id)
            user_snapshot = dict(user)
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                session_key=f"command_photo_{user_id}",
                reference_image_path=reference_path,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 指令生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "[PrivateCompanion] 指令生图结果: user=%s action=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            action_text,
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_natural_language_photo_generation_attempt(user, image_path=image_path)
                self._save_data_sync()
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入今日指令生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            reference_label=reference_label,
        )
        chain = self._build_outbound_chain(caption, image_path)
        try:
            await event.send(self._build_result_from_chain(chain))
        except Exception:
            await event.send(event.chain_result(chain))
        event.stop_event()
        return True

    async def _group_companion_command_impl(self, event: AstrMessageEvent):
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("这条命令需要在群聊里使用。")
            return
        if not self.enable_group_companion or not self._group_allowed_by_access_mode(group_id):
            if self.group_access_mode == "blacklist" and group_id in self._configured_group_blacklist_ids():
                yield event.plain_result("这个群在群聊陪伴黑名单中，暂时不启用。")
            elif self.group_access_mode == "whitelist":
                yield event.plain_result("这个群还没有加入群聊陪伴白名单，暂时不启用。")
            else:
                yield event.plain_result("这个群暂时不启用群聊陪伴。")
            return
        message = str(event.message_str or "").strip()
        action = ""
        response_chain = None
        parts = message.split(maxsplit=2)
        if len(parts) >= 2:
            action = parts[1].strip()
        if action in {"开启", "启用", "打开", "关闭", "停用", "关掉", "撤回消息", "防撤回", "转述撤回", "撤回转述"} and not self._can_manage_group_companion(event):
            yield event.plain_result(self._management_denied_text())
            return
        async with self._data_lock:
            group = self._get_group(group_id)
            if action in {"开启", "启用", "打开"}:
                group["enabled"] = True
                self._save_data_sync()
                response = "群聊陪伴观察已开启。"
            elif action in {"关闭", "停用", "关掉"}:
                group["enabled"] = False
                self._save_data_sync()
                response = "群聊陪伴观察已关闭。"
            elif action in {"黑话", "梗", "词"}:
                slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
                meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
                if slang:
                    lines = ["当前群内常见词/梗："]
                    for item in slang[:20]:
                        if not isinstance(item, dict):
                            continue
                        term = _single_line(item.get("term"), 20)
                        if not term:
                            continue
                        meaning = ""
                        if isinstance(meanings.get(term), dict):
                            meaning_item = meanings[term]
                            confidence = min(1.0, _safe_float(meaning_item.get("confidence"), 1.0, 0.0))
                            raw_meaning = _single_line(meaning_item.get("meaning"), 60)
                            raw_usage = _single_line(meaning_item.get("usage"), 60)
                            if confidence >= 0.55 and not self._is_uncertain_group_slang_meaning(raw_meaning, raw_usage):
                                meaning = raw_meaning
                        lines.append(f"- {term}｜出现 {item.get('count', 0)} 次" + (f"｜{meaning}" if meaning else ""))
                    response = "\n".join(lines)
                else:
                    response = "还没有学到稳定的群内常见词。"
            elif action in {"群友", "成员", "画像"}:
                members = group.get("members") if isinstance(group.get("members"), dict) else {}
                ranked = sorted(
                    [item for item in members.values() if isinstance(item, dict)],
                    key=lambda item: _safe_int(item.get("count"), 0, 0),
                    reverse=True,
                )[:12]
                if ranked:
                    response = "当前群内成员观察：\n" + "\n".join(
                        f"- {_single_line(item.get('name'), 18) or '群友'}"
                        + (
                            "｜" + " / ".join(
                                _single_line(x, 18)
                                for x in (item.get('recent_phrases') or [])[:3]
                                if _single_line(x, 18)
                            )
                            if item.get("recent_phrases")
                            else ""
                        )
                        for item in ranked
                    )
                else:
                    response = "还没有群友样本。"
            elif action in {"话题", "线程"}:
                response = "当前群聊话题线程：\n" + (self._format_group_topic_threads_for_prompt(group) or "暂无。")
            elif action in {"片段", "群聊片段", "记忆"}:
                response = "近期群聊片段记忆：\n" + (self._format_group_episodes_for_prompt(group) or "暂无。")
            elif action in {"插话判定", "插话反馈", "反馈"}:
                response = "群聊插话反馈：" + self._format_group_interjection_feedback(group)
            elif action in {"关系网", "关系网络", "互动关系"}:
                response = "群友互动图：\n" + (self._format_group_relationship_graph_for_prompt(group) or "暂无。")
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not self.enable_recall_enhancement or not self.enable_recall_transcribe_command:
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    extra_components = self._recalled_message_media_components_for_event(event, limit=5)
                    if extra_components:
                        response_chain = self._build_outbound_chain(response, extra_components=extra_components)
            elif action in {"状态", "气氛", ""}:
                response = self._format_group_status(group)
            else:
                response = (
                    "群聊陪伴命令：\n"
                    "陪伴群 状态\n"
                    "陪伴群 黑话\n"
                    "陪伴群 群友\n"
                    "陪伴群 话题\n"
                    "陪伴群 片段\n"
                    "陪伴群 插话反馈\n"
                    "陪伴群 关系网\n"
                    "陪伴群 撤回消息\n"
                    "陪伴群 开启\n"
                    "陪伴群 关闭"
                )
        if response_chain:
            yield event.chain_result(response_chain)
        else:
            yield event.plain_result(response)
        event.stop_event()
