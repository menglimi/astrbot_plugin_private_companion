# -*- coding: utf-8 -*-
"""debug_version 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（3 个方法 / 167 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import importlib
import re
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.star_handler import star_handlers_registry
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginDebugVersionMixin:
    """debug_version 域（从 PrivateCompanionPlugin 拆出）。"""

    def _log_registered_command_handlers(self) -> None:
        expected = {
            "companion_command": "/陪伴(alias: /私聊陪伴, /主动陪伴)",
            "group_companion_command": "/陪伴群(alias: /群陪伴, /群聊陪伴)",
        }
        found: set[str] = set()
        try:
            for handler in star_handlers_registry:
                callback = getattr(handler, "handler", None) or getattr(handler, "func", None)
                handler_name = (
                    getattr(handler, "handler_name", "")
                    or getattr(handler, "name", "")
                    or getattr(callback, "__name__", "")
                )
                if handler_name in expected:
                    found.add(handler_name)
        except Exception as exc:
            logger.debug("指令注册诊断失败: %s", _single_line(exc, 120))
            return
        registered = [expected[name] for name in expected if name in found]
        missing = [expected[name] for name in expected if name not in found]
        if registered:
            logger.info("AstrBot 指令已注册: %s", "；".join(registered))
        if missing:
            logger.warning("AstrBot 指令注册诊断未找到: %s", "；".join(missing))

    def _detect_astrbot_version(self) -> str:
        candidates: list[Any] = []
        for obj in (
            getattr(self, "context", None),
            getattr(getattr(self, "context", None), "core_lifecycle", None),
            getattr(getattr(self, "context", None), "metadata", None),
        ):
            if obj is None:
                continue
            for attr in ("version", "astrbot_version", "__version__", "VERSION"):
                try:
                    candidates.append(getattr(obj, attr, ""))
                except Exception:
                    pass
        for module_name in ("astrbot", "astrbot.core", "astrbot.api"):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in ("__version__", "VERSION", "version"):
                try:
                    candidates.append(getattr(module, attr, ""))
                except Exception:
                    pass
        for candidate in candidates:
            text = _single_line(candidate, 40)
            if re.search(r"\d+\.\d+(?:\.\d+)?", text):
                return text
        return ""

    async def _debug_prompt_text(self, kind: str, user: dict[str, Any], event: AstrMessageEvent | None = None) -> str:
        normalized = str(kind or "").strip().lower()
        await self._ensure_weather_context()
        if normalized in {"日程", "plan", "daily_plan"}:
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
            return self._build_daily_plan_prompt(
                self._environment_now().strftime("%Y-%m-%d %H:%M"),
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"细化", "detail", "enhancement"}:
            plan = dict(self.data.get("daily_plan", {}))
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.get("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
            segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
            if not segment:
                current_item = self._get_current_plan_item(plan)
                if not isinstance(current_item, dict):
                    return "当前没有可用于细化的日程段。先生成日程,并等到某个时间段临近,或让当天有当前日程项。"
                start = self._parse_hhmm_to_minutes(current_item.get("time")) or self._environment_now_minutes()
                segment = {
                    "start": start,
                    "end": min(24 * 60, start + 120),
                    "item": current_item,
                }
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(
                    kind="detail",
                    segment=segment,
                    plan=plan,
                    state=state,
                    max_chars=1100,
                )
            return self._build_detail_enhancement_prompt(
                segment,
                plan,
                state,
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"主动", "proactive"}:
            name = str(user.get("nickname") or runtime_persona_setting(self, 'default_nickname', '你'))
            planned_reason = str(user.get("planned_proactive_reason") or "")
            planned_action = str(user.get("planned_proactive_action") or "message")
            planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
            reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason, user) else ""
            if not reason:
                reason, _ = self._choose_proactive_message(user, name, planned_reason)
                planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_topic = _single_line(user.get("planned_proactive_topic"), 48)
            framework_prompt = await self._build_framework_proactive_prompt(
                user=user,
                name=name,
                reason=reason,
                action=planned_action,
                action_context="（调试预览：这里会放工具结果或观察结果）",
                motive=planned_motive,
            )
            sections = [
                prompt_section(
                    key="debug.proactive.description",
                    title="说明",
                    source="main",
                    content=(
                        "当前主动消息已改为走 AstrBot 框架唤醒链。\n"
                        "人格、历史对话和会话上下文不再在这里手工重复拼接,而是由框架根据当前 conversation 自动注入。"
                    ),
                )
            ]
            if planned_topic:
                sections.append(
                    prompt_section(
                        key="debug.proactive.topic",
                        title="内部话题钩子",
                        source="main",
                        content=planned_topic,
                    )
                )
            sections.append(
                prompt_section(
                    key="debug.proactive.framework_prompt",
                    title="送入框架的任务提示",
                    source="main",
                    content=framework_prompt,
                )
            )
            return render_prompt_sections(
                sections,
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        if normalized in {"回复注入", "reply", "injection"}:
            await self._refresh_default_persona_prompt(getattr(event, "unified_msg_origin", "") if event is not None else "")
            state = await self._ensure_daily_state()
            parts = [self._format_state_injection(state)]
            life_context = self._format_life_context_injection()
            if life_context:
                parts.append(life_context)
            important_dates = self._format_important_dates_injection()
            if important_dates:
                parts.append(important_dates)
            memo_notes = self._format_memo_notes_injection()
            if memo_notes:
                parts.append(memo_notes)
            detail_injection = self._format_detail_injection()
            if detail_injection:
                parts.append(detail_injection)
            return "\n\n".join(parts)
        return "可查看的提示词类型：日程 / 细化 / 主动 / 回复注入"
