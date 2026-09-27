# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterComposeContextMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 372 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import asyncio
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _single_address, _single_line
from .memory_companion_adapter_shared import _memory_companion_safe_float, logger
from datetime import datetime
from typing import Any



class MemoryCompanionAdapterComposeContextMixin:
    """MemoryCompanionAdapterComposeContextMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


    async def _memory_companion_compose_schedule_context(
        self,
        *,
        kind: str = "daily_plan",
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        max_chars: int = 1200,
    ) -> str:
        bridge = self._memory_companion_bridge()
        composer = getattr(bridge, "compose_context", None) if bridge is not None else None
        if not callable(composer):
            return ""
        now_text = ""
        try:
            now_text = self._environment_now().strftime("%Y-%m-%d %H:%M")
        except Exception:
            now_text = ""
        query_parts = [
            "Private Companion 日程连续性",
            "Bot 自我时间线",
            "最近主动消息",
            "最近阅读 创作 搜索 生图 QQ空间 说说 行动",
            "最近吃了什么 今日穿搭 梦境碎片 主动私聊",
            "刚刚发布的 QQ 空间说说 最近已发说说 公开动态余味 不要重复已发说说",
            "主要用户明确偏好 约定 边界",
            "避免把次要用户互动写进 Bot 日程",
        ]
        if now_text:
            query_parts.append(f"当前时间 {now_text}")
        if isinstance(segment, dict):
            item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            if isinstance(item, dict):
                query_parts.extend(
                    [
                        _single_line(item.get("time"), 40),
                        _single_line(item.get("activity"), 180),
                        _single_line(item.get("message_seed"), 120),
                    ]
                )
        if isinstance(plan, dict):
            query_parts.append(_single_line(plan.get("date"), 40))
        if isinstance(state, dict):
            query_parts.append(_single_line(state.get("summary") or state.get("mood") or state.get("emotion"), 160))
        query = _single_line(" ".join(part for part in query_parts if _single_line(part, 240)), 1400)
        if not query:
            return ""
        try:
            bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
            timeout = max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)))
            compose_kwargs = {
                "query": query,
                "session_context": self._memory_companion_schedule_session_context(message_text=query),
                "top_k": 6 if kind == "daily_plan" else 5,
                "max_chars": max(500, min(1800, int(max_chars or 1200))),
                "companion_bot_mood": bot_mood,
                "companion_bot_energy": bot_energy,
            }
            compose_kwargs.update(self._memory_companion_p5_gate_kwargs(event=None, sink="bridge_serialization"))
            if self._memory_companion_coordination_status().get("schedule_fast_context") is True:
                compose_kwargs["retrieval_profile"] = "schedule_fast"
            text = await asyncio.wait_for(
                composer(**compose_kwargs),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MemoryCompanion 日程上下文读取超时,已跳过: kind=%s timeout=%.2fs",
                _single_line(kind, 60),
                max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2))),
            )
            return ""
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="compose_schedule_context"):
                return ""
            logger.debug("MemoryCompanion 日程上下文读取失败: %s", _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        text = self._memory_companion_filter_internal_error_context(text)
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)
        if callable(relationship_sanitizer):
            try:
                text = relationship_sanitizer(
                    text,
                    source=f"memory_companion.schedule.{kind}",
                )
            except Exception:
                pass
        return text[: max(300, int(max_chars or 1200))] if text else ""

    async def _memory_companion_compose_feature_context(
        self,
        *,
        kind: str,
        query: str,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        event: Any | None = None,
        top_k: int = 5,
        max_chars: int = 900,
        timeout_seconds: float = 4.0,
        strict_session_only: bool = False,
    ) -> str:
        if not getattr(self, "enable_memory_companion_feature_context", True):
            return ""
        bridge = self._memory_companion_bridge()
        composer = getattr(bridge, "compose_context", None) if bridge is not None else None
        if not callable(composer):
            return ""
        # Apply configured defaults if caller didn't override
        configured_top_k = getattr(self, "memory_companion_context_top_k", 5)
        configured_max_chars = getattr(self, "memory_companion_context_max_chars", 900)
        if top_k == 5:
            top_k = configured_top_k
        if max_chars == 900:
            max_chars = configured_max_chars
        clean_query = _single_line(query, 1200)
        if not clean_query:
            return ""
        if (
            kind in {"daily_outfit_photo", "daily_diary"}
            and event is None
            and not user_id
            and not isinstance(user, dict)
        ):
            owner_getter = getattr(self, "_memory_companion_schedule_owner_context", None)
            if callable(owner_getter):
                try:
                    owner_id, owner = owner_getter()
                    if owner_id and isinstance(owner, dict):
                        user_id = _single_line(owner_id, 80)
                        user = owner
                except Exception:
                    pass
        session_context: dict[str, Any]
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180)
            scope = "unknown"
            try:
                scope = "private" if bool(getattr(event, "is_private_chat", lambda: False)()) else "group"
            except Exception:
                scope = "unknown"
            if not user_id:
                try:
                    user_id = _single_line(event.get_sender_id(), 80)
                except Exception:
                    user_id = ""
            user_name = ""
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
            preferred_address = _single_address(user.get("nickname"), 24) if isinstance(user, dict) else ""
            session_context = {
                "session_id": session_id,
                "scope": scope,
                "platform": session_id.split(":", 1)[0] if ":" in session_id else "",
                "user_id": user_id,
                "user_name": user_name,
                "preferred_address": preferred_address,
                "preferred_address_locked": bool(preferred_address),
                "bot_id": self._memory_companion_bridge_bot_id(event),
                "message_text": clean_query,
                "strict_session_only": bool(strict_session_only),
                "topic_fit_policy": "旧话题和未完成话头只作可选参考；和当前问题不贴时先放着，不必为了兑现它改变本轮话题。",
            }
        elif isinstance(user, dict):
            umo = _single_line(user.get("umo"), 200)
            preferred_address = _single_address(user.get("nickname"), 24)
            if not user_id and not umo:
                # 无用户标识：无法在 memory 插件侧隔离会话作用域，宁可召回为空也不跨用户串线。
                return ""
            session_context = {
                "session_id": umo or f"private_companion:{kind}:{user_id or 'unknown'}",
                "scope": "private" if user_id else "unknown",
                "platform": umo.split(":", 1)[0] if ":" in umo else "",
                "user_id": user_id,
                "user_name": _single_line(user.get("nickname") or user.get("display_name") or user_id, 80),
                "preferred_address": preferred_address,
                "preferred_address_locked": bool(preferred_address),
                "bot_id": self._memory_companion_bridge_bot_id(),
                "message_text": clean_query,
                "strict_session_only": bool(strict_session_only),
                "topic_fit_policy": "旧话题和未完成话头只作可选参考；和当前问题不贴时先放着，不必为了兑现它改变本轮话题。",
            }
        else:
            # 无 event 且无 user：无法确定当前对话用户，fail-closed，宁可召回为空也不跨用户串线。
            return ""
        try:
            bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
            configured_timeout = max(0.2, min(6.0, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)))
            compose_kwargs = {
                "query": clean_query,
                "session_context": session_context,
                "top_k": max(1, min(10, int(top_k or 5))),
                "max_chars": max(240, min(1800, int(max_chars or 900))),
                "companion_bot_mood": bot_mood,
                "companion_bot_energy": bot_energy,
            }
            compose_kwargs.update(self._memory_companion_p5_gate_kwargs(event=event, sink="bridge_serialization"))
            if (
                kind == "daily_outfit_photo"
                and self._memory_companion_coordination_status().get("outfit_fast_context") is True
            ):
                compose_kwargs["retrieval_profile"] = "outfit_fast"
            text = await asyncio.wait_for(
                composer(**compose_kwargs),
                timeout=max(0.2, min(6.0, min(configured_timeout, _memory_companion_safe_float(timeout_seconds, configured_timeout, 0.2)))),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MemoryCompanion 功能上下文读取超时,已跳过: kind=%s timeout=%.2fs",
                _single_line(kind, 60),
                max(0.2, min(6.0, min(_memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2), _memory_companion_safe_float(timeout_seconds, 1.2, 0.2)))),
            )
            return ""
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where=f"compose_feature_context:{kind}"):
                return ""
            logger.debug("MemoryCompanion 功能上下文读取失败: kind=%s err=%s", _single_line(kind, 60), _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        text = self._memory_companion_filter_internal_error_context(text)
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        generation_kinds = {
            "current_state_reply",
            "daily_diary",
            "daily_outfit_photo",
            "natural_photo",
            "command_photo",
        }
        should_filter_relationships = (
            kind in generation_kinds
            or kind.startswith("proactive_")
            or kind.startswith("qzone_")
            or kind.startswith("creative_")
        )
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)
        if should_filter_relationships and callable(relationship_sanitizer):
            try:
                text = relationship_sanitizer(
                    text,
                    source=f"memory_companion.feature.{kind}",
                )
            except Exception:
                pass
        return text[: max(240, min(1800, int(max_chars or 900)))]

    @staticmethod
    def _memory_companion_private_recall_needed(text: Any) -> bool:
        cleaned = _single_line(text, 280)
        if len(cleaned) < 2:
            return False
        cues = (
            "还记得",
            "记得我",
            "以前",
            "之前",
            "上次",
            "前阵子",
            "说过",
            "提过",
            "答应",
            "约定",
            "承诺",
            "习惯",
            "偏好",
            "喜欢",
            "讨厌",
            "雷点",
            "别叫",
            "怎么称呼",
            "我叫什么",
            "我生日",
        )
        return any(cue in cleaned for cue in cues)

    async def _memory_companion_compose_private_recall(
        self,
        *,
        event: Any,
        user: dict[str, Any],
        user_id: str,
        text: str,
    ) -> PromptSection:
        """Return a small, current-session-only memory supplement for private replies."""
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="memory.private_recall",
                title="当前私聊长期记忆补充",
                source="memory_companion",
                content=content,
            )

        if not getattr(self, "enable_memory_companion_private_recall", True):
            return build_section()
        if not self._memory_companion_private_recall_needed(text):
            return build_section()
        query = _single_line(
            "当前私聊用户正在说："
            f"{_single_line(text, 260)}。"
            "只检索当前私聊会话中与本轮直接相关的明确约定、称呼、边界或稳定偏好；"
            "最多保留 3 条，没有可靠依据则返回空。"
            "禁止引用其他私聊、群聊、公开动态或其他人的信息。",
            700,
        )
        try:
            recalled = await self._memory_companion_compose_feature_context(
                kind="private_turn_recall",
                query=query,
                user=user,
                user_id=user_id,
                event=event,
                top_k=3,
                max_chars=min(620, max(240, int(getattr(self, "memory_companion_context_max_chars", 900) or 900))),
                timeout_seconds=min(1.2, _memory_companion_safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2)),
                strict_session_only=True,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="compose_private_recall"):
                return build_section()
            logger.debug("MemoryCompanion 私聊选择性召回失败: %s", _single_line(exc, 120))
            return build_section()
        recalled = _single_line(recalled, 620)
        if not recalled:
            return build_section()
        body = (
            f"{recalled}\n"
            "只在与本轮直接相关时自然接住；不要主动列举记忆、不要提及检索过程，也不要把它当作其他用户的信息。"
        )
        return build_section(body)

    def _memory_companion_agenda_memory_write_entries(
        self,
        *,
        date_text: str = "",
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return only entries admitted by the canonical memory-write view."""

        getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(getter):
            return []
        current = now
        if current is None:
            try:
                current = self._environment_now()
            except Exception:
                current = datetime.now().astimezone()
        try:
            try:
                view = getter(
                    "memory_write",
                    now=current,
                    max_entries=256,
                    date_key=_single_line(date_text, 20),
                )
            except TypeError:
                view = getter("memory_write", now=current, max_entries=256)
        except Exception:
            return []
        entries = getattr(view, "entries", None)
        if entries is None and isinstance(view, dict):
            entries = view.get("entries")
        return [dict(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
