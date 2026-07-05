# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import uuid
import asyncio
import time
from typing import Any

from astrbot.api import logger

from .helpers import _missing_optional_model_dependency, _safe_int, _single_line


class MemoryCompanionAdapterMixin:
    """Optional bridge helpers for astrbot_plugin_memory_companion."""

    _bridge_cache: Any | None = None
    _bridge_cache_ts: float = 0.0
    _BRIDGE_CACHE_TTL: float = 30.0
    _bridge_dependency_failure_until: float = 0.0
    _bridge_dependency_failure_module: str = ""

    def _memory_companion_optional_dependency_failed(self, exc: BaseException, *, where: str = "") -> bool:
        module = _missing_optional_model_dependency(exc)
        if not module:
            return False
        self._bridge_cache = None
        self._bridge_cache_ts = 0.0
        self._bridge_dependency_failure_until = time.monotonic() + 300.0
        self._bridge_dependency_failure_module = module
        logger.warning(
            "[PrivateCompanion] 记忆插件可选模型依赖缺失，已临时降级 MemoryCompanion 桥接: module=%s where=%s err=%s",
            module,
            _single_line(where, 80) or "-",
            _single_line(exc, 160),
        )
        return True

    def _memory_companion_bridge(self) -> Any | None:
        now = time.monotonic()
        if now < self._bridge_dependency_failure_until:
            return None
        if self._bridge_cache is not None and (now - self._bridge_cache_ts) < self._BRIDGE_CACHE_TTL:
            return self._bridge_cache
        bridge = self._memory_companion_bridge_uncached()
        self._bridge_cache = bridge
        self._bridge_cache_ts = now
        return bridge

    def _memory_companion_bridge_uncached(self) -> Any | None:
        for module_name in (
            "data.plugins.astrbot_plugin_remember_you.main",
            "astrbot_plugin_remember_you.main",
            "data.plugins.astrbot_plugin_memory_companion.main",
            "astrbot_plugin_memory_companion.main",
        ):
            module = sys.modules.get(module_name)
            bridge = self._memory_companion_bridge_from_module(module)
            if bridge is not None:
                return bridge
        for module in list(sys.modules.values()):
            module_vars = getattr(module, "__dict__", {}) if module is not None else {}
            if not isinstance(module_vars, dict):
                continue
            if module_vars.get("PLUGIN_NAME", "") not in {"astrbot_plugin_memory_companion", "astrbot_plugin_remember_you", "我会牢牢记住你", "RememberYou"}:
                continue
            bridge = self._memory_companion_bridge_from_module(module)
            if bridge is not None:
                return bridge
        return None

    def _memory_companion_bridge_from_module(self, module: Any | None) -> Any | None:
        module_vars = getattr(module, "__dict__", {}) if module is not None else {}
        getter = module_vars.get("get_active_bridge") if isinstance(module_vars, dict) else None
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="get_active_bridge")
            return None

    def _memory_companion_coordination_status(self) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"available": False}
        getter = getattr(bridge, "coordination_status", None)
        if not callable(getter):
            return {"available": True}
        try:
            status = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="coordination_status"):
                return {"available": False, "error": f"missing optional dependency: {self._bridge_dependency_failure_module}"}
            logger.debug("[PrivateCompanion] MemoryCompanion 协同状态读取失败: %s", _single_line(exc, 120))
            return {"available": True, "error": _single_line(exc, 120)}
        return status if isinstance(status, dict) else {"available": True}

    def _memory_companion_token_usage_summary(self) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "未检测到运行中的记忆插件"}
        getter = getattr(bridge, "get_token_usage_summary", None)
        if not callable(getter):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "当前记忆插件版本暂未暴露 Token 统计"}
        try:
            usage = getter()
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="token_usage"):
                return {"available": False, "display_name": "我会牢牢记住你", "reason": f"缺少可选依赖 {self._bridge_dependency_failure_module}"}
            logger.debug("[PrivateCompanion] 记忆插件 Token 统计读取失败: %s", _single_line(exc, 120))
            return {"available": False, "display_name": "我会牢牢记住你", "reason": _single_line(exc, 120)}
        if not isinstance(usage, dict):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "记忆插件返回的 Token 统计格式无效"}
        usage.setdefault("available", True)
        usage.setdefault("display_name", "我会牢牢记住你")
        usage.setdefault("counted_in_private_companion_budget", False)
        return usage

    def _memory_companion_mark_deferred_section(
        self,
        section: str,
        event: Any | None = None,
        req: Any | None = None,
    ) -> None:
        normalized = _single_line(section, 80)
        if not normalized:
            return
        for target in (event, req):
            if target is None:
                continue
            try:
                existing = getattr(target, "memory_companion_companion_deferred_sections", None)
                if isinstance(existing, set):
                    sections = set(existing)
                elif isinstance(existing, (list, tuple)):
                    sections = {_single_line(item, 80) for item in existing if _single_line(item, 80)}
                elif isinstance(existing, str):
                    sections = {_single_line(item, 80) for item in existing.split(",") if _single_line(item, 80)}
                else:
                    sections = set()
                sections.add(normalized)
                setattr(target, "memory_companion_companion_deferred_sections", sections)
            except Exception:
                pass

    def _memory_companion_should_defer_prompt_section(
        self,
        section: str,
        event: Any | None = None,
        req: Any | None = None,
    ) -> bool:
        bridge = self._memory_companion_bridge()
        checker = getattr(bridge, "should_defer_private_companion_section", None) if bridge is not None else None
        if not callable(checker):
            return False
        try:
            should_defer = bool(checker(section))
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="should_defer"):
                return False
            logger.debug("[PrivateCompanion] MemoryCompanion 协同状态读取失败: %s", _single_line(exc, 120))
            return False
        if should_defer:
            self._memory_companion_mark_deferred_section(section, event, req)
            logger.info("[PrivateCompanion] MemoryCompanion 已接管提示词片段，跳过本地注入: section=%s", _single_line(section, 80))
        return should_defer

    def _memory_companion_bot_emotional_state(self) -> tuple[str, float]:
        """Extract bot's current mood and energy from daily_state for memory context sharing."""
        try:
            state = self.data.get("daily_state", {})
            if not isinstance(state, dict):
                return "", 0.0
            mood = _single_line(state.get("mood_bias"), 40)
            try:
                energy = float(state.get("energy", 0) or 0)
            except Exception:
                energy = 0.0
            return mood, energy
        except Exception:
            return "", 0.0

    def _memory_companion_build_private_context(
        self,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
        event: Any | None = None,
    ) -> dict[str, Any]:
        role = ""
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user, user_id), 40)
            except TypeError:
                try:
                    role = _single_line(role_getter(user), 40)
                except Exception:
                    role = ""
            except Exception:
                role = ""
        current_item = None
        try:
            current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        except Exception:
            current_item = None
        schedule_text = ""
        if isinstance(current_item, dict):
            try:
                schedule_text = _single_line(self._format_plan_item_for_prompt(current_item), 180)
            except Exception:
                schedule_text = _single_line(current_item.get("activity") or current_item.get("text"), 180)
        relationship = ""
        try:
            relationship = _single_line(self._format_relationship_summary(user), 220)
        except Exception:
            relationship = ""
        entities = [
            _single_line(user.get("nickname") or user.get("display_name") or user_id, 80),
            _single_line(user_id, 80),
        ]
        worldbook_mentions = ""
        formatter = getattr(self, "_format_worldbook_private_mentions_for_prompt", None)
        if callable(formatter) and text:
            try:
                worldbook_mentions = _single_line(formatter(text, limit=4), 240)
            except Exception:
                worldbook_mentions = ""
        facts = [
            f"当前私聊用户角色：{role}" if role else "",
            f"关系摘要：{relationship}" if relationship else "",
            f"最近主动消息：{_single_line(user.get('last_proactive_message'), 180)}" if user.get("last_proactive_message") else "",
            f"关系网命中：{worldbook_mentions}" if worldbook_mentions else "",
        ]
        keywords = [
            _single_line(user.get("planned_proactive_topic"), 80),
            _single_line(user.get("planned_proactive_reason"), 80),
            _single_line(user.get("last_proactive_reason"), 80),
        ]
        payload = {
            "source": "private_companion",
            "scope": "private",
            "topic": _single_line(user.get("planned_proactive_topic") or user.get("last_proactive_reason") or text, 120),
            "intent": _single_line(user.get("planned_proactive_semantic_kind") or user.get("last_proactive_action") or "private_reply", 80),
            "entities": [item for item in entities if item],
            "facts": [item for item in facts if item],
            "keywords": [item for item in keywords if item],
            "motive": _single_line(user.get("planned_proactive_motive") or user.get("last_proactive_motive"), 160),
            "schedule": schedule_text,
            "private_user_role": role,
            "user_id": _single_line(user_id, 80),
            "session_id": _single_line(getattr(event, "unified_msg_origin", "") if event is not None else user.get("umo"), 180),
        }
        # Attach bot emotional state for memory plugin to calibrate injection tone
        bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
        if bot_mood:
            payload["mood_bias"] = bot_mood
        if bot_energy > 0:
            payload["energy"] = bot_energy
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _memory_companion_schedule_owner_context(self) -> tuple[str, dict[str, Any]]:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return "", {}
        fallback: tuple[str, dict[str, Any]] = ("", {})
        for raw_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            user_id = str(raw_id or "").strip()
            if not user_id:
                continue
            if not bool(raw_user.get("enabled", True)):
                continue
            if not fallback[0]:
                fallback = (user_id, raw_user)
            try:
                if self._private_user_role(raw_user, user_id) == "owner":
                    return user_id, raw_user
            except Exception:
                continue
        return fallback

    def _memory_companion_schedule_session_context(self, *, message_text: str = "") -> dict[str, Any]:
        user_id, user = self._memory_companion_schedule_owner_context()
        umo = _single_line(user.get("umo"), 200) if isinstance(user, dict) else ""
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        user_name = _single_line(
            (user.get("nickname") or user.get("display_name") or user_id) if isinstance(user, dict) else user_id,
            80,
        )
        return {
            "session_id": umo or f"private_companion:schedule:{user_id or 'bot_self'}",
            "scope": "private" if user_id else "unknown",
            "platform": platform,
            "user_id": user_id,
            "user_name": user_name,
            "message_text": _single_line(message_text, 1200),
        }

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
            "主人明确偏好 约定 边界",
            "避免把朋友用户互动写进 Bot 日程",
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
            text = await asyncio.wait_for(
                composer(
                    query=query,
                    session_context=self._memory_companion_schedule_session_context(message_text=query),
                    top_k=6 if kind == "daily_plan" else 5,
                    max_chars=max(500, min(1800, int(max_chars or 1200))),
                    companion_bot_mood=bot_mood,
                    companion_bot_energy=bot_energy,
                ),
                timeout=4.0,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="compose_schedule_context"):
                return ""
            logger.debug("[PrivateCompanion] MemoryCompanion 日程上下文读取失败: %s", _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        return text[: max(300, int(max_chars or 1200))]

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
    ) -> str:
        bridge = self._memory_companion_bridge()
        composer = getattr(bridge, "compose_context", None) if bridge is not None else None
        if not callable(composer):
            return ""
        clean_query = _single_line(query, 1200)
        if not clean_query:
            return ""
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
            session_context = {
                "session_id": session_id,
                "scope": scope,
                "platform": session_id.split(":", 1)[0] if ":" in session_id else "",
                "user_id": user_id,
                "user_name": user_name,
                "message_text": clean_query,
            }
        elif isinstance(user, dict):
            umo = _single_line(user.get("umo"), 200)
            session_context = {
                "session_id": umo or f"private_companion:{kind}:{user_id or 'unknown'}",
                "scope": "private" if user_id else "unknown",
                "platform": umo.split(":", 1)[0] if ":" in umo else "",
                "user_id": user_id,
                "user_name": _single_line(user.get("nickname") or user.get("display_name") or user_id, 80),
                "message_text": clean_query,
            }
        else:
            session_context = {
                "session_id": f"private_companion:{kind}",
                "scope": "unknown",
                "message_text": clean_query,
            }
        try:
            bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
            text = await asyncio.wait_for(
                composer(
                    query=clean_query,
                    session_context=session_context,
                    top_k=max(1, min(10, int(top_k or 5))),
                    max_chars=max(240, min(1800, int(max_chars or 900))),
                    companion_bot_mood=bot_mood,
                    companion_bot_energy=bot_energy,
                ),
                timeout=max(0.5, min(6.0, float(timeout_seconds or 4.0))),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where=f"compose_feature_context:{kind}"):
                return ""
            logger.debug("[PrivateCompanion] MemoryCompanion 功能上下文读取失败: kind=%s err=%s", _single_line(kind, 60), _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        return text[: max(240, min(1800, int(max_chars or 900)))]

    async def _memory_companion_record_daily_plan(self, plan: dict[str, Any]) -> None:
        if not isinstance(plan, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_schedule_fragment", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(plan.get("date"), 40)
        items = plan.get("items")
        if not date_text or not isinstance(items, list) or not items:
            return
        lines: list[str] = []
        for item in items[:16]:
            if not isinstance(item, dict):
                continue
            line = _single_line(
                " ".join(
                    part
                    for part in [
                        _single_line(item.get("time"), 12),
                        _single_line(item.get("activity"), 180),
                        f"情绪:{_single_line(item.get('mood'), 40)}" if _single_line(item.get("mood"), 40) else "",
                        f"可分享:{_single_line(item.get('message_seed'), 80)}" if _single_line(item.get("message_seed"), 80) else "",
                    ]
                    if part
                ),
                260,
            )
            if line:
                lines.append(line)
        if not lines:
            return
        content = f"{date_text} 的 Bot 当日生活日程已生成：\n" + "\n".join(f"- {line}" for line in lines)
        try:
            await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:schedule",
                message_id=f"private_companion_daily_plan_{date_text}",
                memory_id=f"private_companion_daily_plan_{date_text}",
                metadata={
                    "date": date_text,
                    "source": _single_line(plan.get("source"), 40),
                    "item_count": len(lines),
                    "provider_id": _single_line(plan.get("provider_id"), 120),
                },
                source_plugin="private_companion",
                confidence=0.86,
                importance=0.5,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_daily_plan"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 日程写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_detail_enhancement(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        if not isinstance(segment, dict) or not isinstance(detail, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_schedule_fragment", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(plan.get("date") if isinstance(plan, dict) else "", 40)
        start = 0
        end = 0
        try:
            start = int(segment.get("start") or 0)
            end = int(segment.get("end") or 0)
        except Exception:
            start, end = 0, 0
        if not date_text or start < 0:
            return
        try:
            start_text = self._minutes_to_hhmm(start)
            end_text = self._minutes_to_hhmm(end)
        except Exception:
            start_text = str(start)
            end_text = str(end or "")
        summary = _single_line(detail.get("summary"), 180)
        events = []
        for item in detail.get("today_events") if isinstance(detail.get("today_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(item.get("event"), 160)
                if text:
                    events.append(text)
        proactive = []
        for item in detail.get("proactive_events") if isinstance(detail.get("proactive_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(
                    item.get("topic") or item.get("motive") or item.get("why") or item.get("reason"),
                    100,
                )
                if text:
                    proactive.append(text)
        if not summary and not events and not proactive:
            return
        parts = [f"{date_text} {start_text}-{end_text} 的 Bot 日程细化："]
        if summary:
            parts.append(summary)
        if events:
            parts.append("生活片段：" + "；".join(events[:4]))
        if proactive:
            parts.append("可能主动念头：" + "；".join(proactive[:3]))
        try:
            await recorder(
                content="\n".join(parts),
                scope="unknown",
                session_id="private_companion:schedule",
                message_id=f"private_companion_detail_{date_text}_{start}_{end}",
                memory_id=f"private_companion_detail_{date_text}_{start}_{end}",
                metadata={
                    "date": date_text,
                    "start": start_text,
                    "end": end_text,
                    "summary": summary,
                    "event_count": len(events),
                    "proactive_count": len(proactive),
                },
                source_plugin="private_companion",
                confidence=0.84,
                importance=0.42,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_detail_enhancement"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 细化写入失败: %s", _single_line(exc, 120))

    def _memory_companion_build_group_context(
        self,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        event: Any | None = None,
    ) -> dict[str, Any]:
        current_item = None
        try:
            current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        except Exception:
            current_item = None
        schedule_text = ""
        if isinstance(current_item, dict):
            try:
                schedule_text = _single_line(self._format_plan_item_for_prompt(current_item), 160)
            except Exception:
                schedule_text = _single_line(current_item.get("activity") or current_item.get("text"), 160)
        group_context = ""
        formatter = getattr(self, "_format_group_context_for_prompt", None)
        if callable(formatter):
            try:
                group_context = _single_line(formatter(group, sender_id, text), 260)
            except Exception:
                group_context = ""
        relationship_text = ""
        relation_formatter = getattr(self, "_format_group_relationship_graph_for_prompt", None)
        if callable(relation_formatter):
            try:
                relationship_text = _single_line(relation_formatter(group, sender_id, text), 220)
            except Exception:
                relationship_text = ""
        payload = {
            "source": "private_companion",
            "scope": "group",
            "topic": _single_line(text or group.get("last_topic") or group.get("name"), 120),
            "intent": "group_reply",
            "entities": [
                _single_line(sender_name or sender_id, 80),
                _single_line(sender_id, 80),
                _single_line(group_id, 80),
            ],
            "facts": [
                f"当前群：{_single_line(group.get('name') or group_id, 80)}",
                f"当前发言者：{_single_line(sender_name or sender_id, 80)}({sender_id})",
                f"群聊摘要：{group_context}" if group_context else "",
                f"群友互动：{relationship_text}" if relationship_text else "",
            ],
            "keywords": [
                _single_line(group.get("last_topic"), 80),
                _single_line(group.get("last_wakeup_type"), 80),
            ],
            "schedule": schedule_text,
            "group_id": _single_line(group_id, 80),
            "sender_id": _single_line(sender_id, 80),
            "session_id": _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180),
        }
        # Attach bot emotional state for memory plugin to calibrate injection tone
        bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
        if bot_mood:
            payload["mood_bias"] = bot_mood
        if bot_energy > 0:
            payload["energy"] = bot_energy
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _memory_companion_attach_context(self, event: Any | None, payload: dict[str, Any]) -> None:
        if event is None or not isinstance(payload, dict) or not payload:
            return
        try:
            existing = getattr(event, "private_companion_context", None)
            if isinstance(existing, dict):
                merged = dict(existing)
                for key, value in payload.items():
                    if key in {"entities", "facts", "keywords"}:
                        old = merged.get(key)
                        old_items = old if isinstance(old, list) else ([old] if old else [])
                        new_items = value if isinstance(value, list) else ([value] if value else [])
                        merged[key] = list(dict.fromkeys(_single_line(item, 120) for item in [*old_items, *new_items] if _single_line(item, 120)))
                    elif value not in ("", [], {}, None):
                        merged[key] = value
                payload = merged
            setattr(event, "private_companion_context", payload)
        except Exception as exc:
            logger.debug("[PrivateCompanion] MemoryCompanion 上下文线索挂载失败: %s", _single_line(exc, 120))

    def _memory_companion_attach_private_context(
        self,
        event: Any | None,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
    ) -> None:
        payload = self._memory_companion_build_private_context(user_id=user_id, user=user, text=text, event=event)
        self._memory_companion_attach_context(event, payload)

    def _memory_companion_attach_group_context(
        self,
        event: Any | None,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        payload = self._memory_companion_build_group_context(
            group_id=group_id,
            group=group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            event=event,
        )
        self._memory_companion_attach_context(event, payload)

    async def _memory_companion_record_proactive_message(
        self,
        *,
        user: dict[str, Any],
        user_id: str,
        text: str,
        reason: str = "",
        action: str = "message",
        motive: str = "",
        action_summary: str = "",
        image_path: str = "",
        extra_count: int = 0,
    ) -> None:
        content = _single_line(text, 1000)
        if not content:
            return
        bridge = self._memory_companion_bridge()
        visible_turn_recorder = getattr(bridge, "record_visible_turn", None) if bridge is not None else None
        proactive_recorder = getattr(bridge, "record_proactive_message", None) if bridge is not None else None
        if not callable(visible_turn_recorder) and not callable(proactive_recorder):
            return
        umo = _single_line(user.get("umo"), 200)
        if not umo:
            return
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        name = _single_line(user.get("nickname") or user.get("display_name") or user_id, 80)
        metadata = {
            "reason": _single_line(reason, 80),
            "action": _single_line(action, 80),
            "motive": _single_line(motive, 180),
            "action_summary": _single_line(action_summary, 240),
            "image_path": _single_line(image_path, 300),
            "extra_count": int(extra_count or 0),
            "clean_visible_text": content,
        }
        try:
            message_id = f"private_companion_proactive_{uuid.uuid4().hex}"
            if callable(visible_turn_recorder):
                await visible_turn_recorder(
                    role="assistant",
                    content=content,
                    scope="private",
                    session_id=umo,
                    platform=platform,
                    user_id=str(user_id or ""),
                    user_name=name,
                    message_id=message_id,
                    source="private_companion_proactive",
                    metadata=metadata,
                )
                return
            await proactive_recorder(
                content=f"Bot 主动向 {name or user_id} 发送：{content}",
                scope="private",
                session_id=umo,
                platform=platform,
                message_id=message_id,
                subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                object={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                metadata=metadata,
                source_plugin="private_companion",
                confidence=0.92,
                importance=0.58,
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_proactive_message"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 主动消息桥接写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_image_observation(
        self,
        event: Any | None,
        *,
        content: str,
        image_count: int = 1,
        source: str = "private_image",
        user_id: str = "",
        user_name: str = "",
    ) -> None:
        text = _single_line(content, 1200)
        if not text:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            return
        is_private = False
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        scope = "private" if is_private else "group"
        session_id = _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        if not user_id and event is not None:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
        if not user_name and event is not None:
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
        visibility = "private_pair" if scope == "private" else "group_public"
        content_text = f"用户本轮图片视觉摘要：{text}"
        try:
            await recorder(
                content=content_text,
                memory_type="image_observation",
                scope=scope,
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_image_{uuid.uuid4().hex}",
                subject={"kind": "user", "id": user_id, "name": user_name, "role": "conversation_partner"},
                object={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                visibility=visibility,
                sayability="indirect",
                reality_level="observed_context",
                lifecycle="current_window",
                confidence=0.72,
                importance=0.42,
                review_status="auto",
                tags=["image", "vision", "current_context", _single_line(source, 40)],
                metadata={
                    "source": _single_line(source, 40),
                    "image_count": max(1, int(image_count or 1)),
                    "summary": text,
                },
                source_plugin="private_companion",
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_image_observation"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 图片观察写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_user_habit(
        self,
        *,
        user: dict[str, Any],
        user_id: str,
        habit: dict[str, Any],
    ) -> None:
        if not isinstance(user, dict) or not isinstance(habit, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            return
        topic = _single_line(habit.get("topic"), 120)
        category = _single_line(habit.get("category"), 40)
        intent = _single_line(habit.get("intent"), 60)
        if not topic or not category:
            return
        count = 0
        try:
            count = int(habit.get("count") or 0)
        except Exception:
            count = 0
        bucket = _single_line(habit.get("bucket"), 20)
        avg_time = ""
        formatter = getattr(self, "_format_user_habit_time", None)
        if callable(formatter):
            try:
                avg_time = _single_line(formatter(habit.get("avg_minute")), 20)
            except Exception:
                avg_time = ""
        name = _single_line(user.get("nickname") or user.get("display_name") or user_id, 80)
        umo = _single_line(user.get("umo"), 200)
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        query_anchors = habit.get("query_anchors")
        if not isinstance(query_anchors, list):
            query_anchors = []
        query_anchors = [_single_line(item, 40) for item in query_anchors if _single_line(item, 40)][:12]
        answer_hints = habit.get("answer_hints")
        if not isinstance(answer_hints, list):
            answer_hints = []
        answer_hints = [_single_line(item, 80) for item in answer_hints if _single_line(item, 80)][:8]
        examples = habit.get("examples")
        if not isinstance(examples, list):
            examples = []
        examples = [_single_line(item, 90) for item in examples if _single_line(item, 90)][:5]
        content_parts = [
            f"{name or user_id} 常在{bucket or '相近时段'}问：{topic}",
            f"类型：{category}",
            f"出现约 {count} 次" if count > 0 else "",
            f"平均时间：{avg_time}" if avg_time else "",
            "回答时优先检索：" + "、".join(query_anchors) if query_anchors else "",
            "回答倾向：" + "；".join(answer_hints) if answer_hints else "",
        ]
        content = "；".join(part for part in content_parts if part)
        if not content:
            return
        memory_key = _single_line(habit.get("memory_key") or habit.get("key"), 120)
        if not memory_key:
            memory_key = f"{user_id}:{category}:{topic}"
        try:
            await recorder(
                content=content,
                memory_type="user_habit",
                scope="private",
                session_id=umo,
                platform=platform,
                message_id=f"private_companion_user_habit_{memory_key}",
                memory_id=f"private_companion_user_habit_{memory_key}",
                subject={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                object={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                visibility="private_pair",
                sayability="direct",
                reality_level="real_user_fact",
                lifecycle="stable_memory",
                confidence=0.82,
                importance=0.66,
                review_status="auto",
                tags=["user_habit", "private_user", category, intent, *query_anchors[:6]],
                metadata={
                    "category": category,
                    "intent": intent,
                    "topic": topic,
                    "bucket": bucket,
                    "avg_time": avg_time,
                    "count": count,
                    "query_anchors": query_anchors,
                    "answer_hints": answer_hints,
                    "examples": examples,
                    "source": "private_companion_behavior_habits",
                },
                source_plugin="private_companion",
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_user_habit"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 用户习惯写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_daily_outfit(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not _single_line(item.get("path"), 300):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(item.get("date"), 20)
        prompt = _single_line(item.get("prompt"), 600)
        note = _single_line(item.get("note"), 160)
        path = _single_line(item.get("path"), 300)
        schedule_hint = ""
        try:
            schedule_hint = _single_line(self._daily_outfit_schedule_text(), 280)
        except Exception:
            schedule_hint = ""
        content = (
            f"{date_text or '今天'}的 Bot 每日穿搭图已生成。"
            f"这条记忆用于回答当前穿搭、衣服颜色、今天穿什么等问题。"
        )
        if schedule_hint:
            content += f" 穿搭依据：{schedule_hint}。"
        if prompt:
            content += f" 穿搭提示摘要：{prompt[:360]}"
        try:
            await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:daily_outfit",
                message_id=f"private_companion_daily_outfit_{date_text or 'today'}",
                memory_id=f"private_companion_daily_outfit_{date_text or 'today'}",
                metadata={
                    "date": date_text,
                    "image_path": path,
                    "backend": _single_line(item.get("backend"), 80),
                    "note": note,
                    "prompt_preview": prompt,
                    "query_anchors": ["今日穿搭", "每日穿搭", "衣服颜色", "穿什么", "穿了什么", "当前穿着"],
                },
                source_plugin="private_companion",
                confidence=0.76,
                importance=0.62,
                tags=["daily_outfit", "outfit", "clothing", "current_state", "persona_life", "衣服颜色", "今日穿搭"],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_daily_outfit"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 每日穿搭写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_creative_progress(
        self,
        *,
        project: dict[str, Any],
        chunk: str = "",
        extract: dict[str, Any] | None = None,
        event: Any | None = None,
    ) -> None:
        if not isinstance(project, dict):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_creative_work", None) if bridge is not None else None
        if not callable(recorder):
            return
        project_id = _single_line(project.get("id"), 60)
        title = _single_line(project.get("title"), 60) or "未命名作品"
        work_type = _single_line(project.get("work_type"), 40) or "创作"
        premise = _single_line(project.get("premise"), 180)
        chunk_text = _single_line(chunk, 360)
        extract = extract if isinstance(extract, dict) else {}
        next_direction = _single_line(extract.get("next_direction") or project.get("next_hint"), 160)
        important = extract.get("important_facts") if isinstance(extract.get("important_facts"), list) else []
        threads = extract.get("new_threads") if isinstance(extract.get("new_threads"), list) else []
        important_text = "；".join(_single_line(item, 80) for item in important[:3] if _single_line(item, 80))
        thread_text = "；".join(_single_line(item, 80) for item in threads[:3] if _single_line(item, 80))
        content_parts = [
            f"Bot 私下创作项目《{title}》（{work_type}）有新进展。",
            f"核心设定：{premise}" if premise else "",
            f"最新片段：{chunk_text}" if chunk_text else "",
            f"新增线索：{thread_text}" if thread_text else "",
            f"必须记住：{important_text}" if important_text else "",
            f"下一步：{next_direction}" if next_direction else "",
        ]
        content = " ".join(part for part in content_parts if part)
        if not content.strip():
            return
        session_id = "private_companion:creative"
        group_id = ""
        platform = ""
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180) or session_id
            platform = session_id.split(":", 1)[0] if ":" in session_id else ""
            try:
                if not bool(getattr(event, "is_private_chat", lambda: False)()):
                    group_id = _single_line(getattr(event, "get_group_id", lambda: "")(), 80)
            except Exception:
                group_id = ""
        try:
            await recorder(
                content=content,
                scope="unknown",
                session_id=session_id,
                platform=platform,
                group_id=group_id,
                message_id=f"private_companion_creative_{project_id}_{_single_line(project.get('current_chars'), 20)}",
                memory_id=f"private_companion_creative_{project_id}_{_single_line(project.get('current_chars'), 20)}",
                metadata={
                    "project_id": project_id,
                    "title": title,
                    "work_type": work_type,
                    "status": _single_line(project.get("status"), 30),
                    "current_chars": project.get("current_chars"),
                    "target_chars": project.get("target_chars"),
                    "next_direction": next_direction,
                    "important_facts": important[:5],
                    "new_threads": threads[:5],
                    "query_anchors": [title, work_type, "私下创作", "创作项目", "上次写到哪", "小说片段", "人工修订"],
                },
                source_plugin="private_companion",
                confidence=0.8,
                importance=0.72,
                tags=["creative_work", "private_companion", "creative_project", work_type, title],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_creative_progress"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion 创作进展写入失败: %s", _single_line(exc, 120))

    def _memory_companion_now_iso(self) -> str:
        try:
            return self._environment_now().isoformat(timespec="seconds")
        except Exception:
            try:
                from datetime import datetime

                return datetime.now().isoformat(timespec="seconds")
            except Exception:
                return ""

    async def _memory_companion_record_qzone_publish(
        self,
        *,
        text: str,
        reason: str = "",
        tid: str = "",
        image_count: int = 0,
        verified: bool | None = None,
        event: Any | None = None,
    ) -> None:
        content = _single_line(text, 800)
        if not content:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        reason_text = _single_line(reason, 40) or "qzone_publish"
        session_id = "private_companion:qzone"
        platform = ""
        if event is not None:
            session_id = _single_line(getattr(event, "unified_msg_origin", ""), 180) or session_id
            platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        safe_image_count = _safe_int(image_count, 0, 0, 99)
        image_part = f"；配图 {safe_image_count} 张" if safe_image_count > 0 else ""
        verify_part = "；已反查确认" if verified else ""
        memory_content = f"Bot 刚刚发布了一条 QQ 空间说说：{content}{image_part}{verify_part}。"
        memory_key = _single_line(tid, 40) or uuid.uuid4().hex[:12]
        try:
            await recorder(
                content=memory_content,
                scope="unknown",
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_qzone_{memory_key}",
                memory_id=f"private_companion_qzone_{memory_key}",
                metadata={
                    "reason": reason_text,
                    "tid": _single_line(tid, 80),
                    "text": content,
                    "image_count": safe_image_count,
                    "verified": bool(verified) if verified is not None else None,
                    "query_anchors": ["QQ空间", "说说", "刚才发了什么", "刚刚发了什么", "发了什么说说", "空间动态"],
                },
                source_plugin="private_companion",
                confidence=0.84,
                importance=0.58,
                tags=["qzone", "qzone_publish", "persona_life", "说说", "QQ空间", reason_text],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_qzone_publish"):
                return
            logger.debug("[PrivateCompanion] MemoryCompanion QQ 空间发布写入失败: %s", _single_line(exc, 120))

    def _memory_companion_apply_emotional_drift(self, *, session_id: str = "") -> None:
        """Pull pending emotional drift events from the memory plugin and apply to daily_state.

        This now includes cross-window emotional continuity: if the bot recently
        touched emotional memories in other sessions, a dampened residue is also
        applied to the current daily_state, creating a sense of emotional carryover.
        """
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return
        getter = getattr(bridge, "get_emotional_events", None)
        if not callable(getter):
            return
        try:
            events = getter(session_id=session_id, limit=3)
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="get_emotional_events"):
                return
            logger.debug("[PrivateCompanion] 情绪漂移拉取失败: %s", _single_line(exc, 120))
            return
        # Cross-window emotional residue: check if there are recent emotional events
        # from OTHER sessions that should subtly influence the current state
        cross_window_delta = 0.0
        cross_window_hints: list[str] = []
        cross_state_getter = getattr(bridge, "get_recent_emotional_state", None)
        if callable(cross_state_getter):
            try:
                cross_state = cross_state_getter()
                if isinstance(cross_state, dict) and cross_state.get("total", 0) > 0:
                    # Apply a dampened cross-window effect (30% strength)
                    scar_count = cross_state.get("scar_count", 0)
                    warm_count = cross_state.get("warm_count", 0)
                    if scar_count > 0:
                        cross_window_delta = -min(2.0, scar_count * 0.8)
                        cross_window_hints.append("低落")
                    if warm_count > 0:
                        cross_window_delta += min(1.5, warm_count * 0.5)
                        cross_window_hints.append("微暖")
            except Exception as exc:
                self._memory_companion_optional_dependency_failed(exc, where="get_recent_emotional_state")
        if not events and not cross_window_hints:
            return
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        state = data.get("daily_state")
        if not isinstance(state, dict):
            state = {}
            data["daily_state"] = state
        try:
            current_energy = float(state.get("energy") or 0.0)
        except Exception:
            current_energy = 0.0
        total_delta = 0.0
        mood_hints: list[str] = []
        for event in events:
            delta = float(event.get("energy_delta") or 0.0)
            total_delta += delta
            hint = _single_line(event.get("mood_hint"), 40)
            if hint:
                mood_hints.append(hint)
        # Add cross-window residue (already dampened)
        total_delta += cross_window_delta
        mood_hints.extend(cross_window_hints)
        # Safety valve: clamp total drift per cycle
        total_delta = max(-10.0, min(6.0, total_delta))
        new_energy = max(0.0, min(100.0, current_energy + total_delta))
        state["energy"] = round(new_energy, 1)
        # Apply mood drift with safety valve: only shift if hint is significant
        if mood_hints:
            current_mood = _single_line(state.get("mood_bias"), 80)
            dominant_hint = mood_hints[0]
            if current_mood and dominant_hint not in current_mood:
                state["mood_bias"] = _single_line(f"{current_mood}，偏{dominant_hint}", 80)
            elif not current_mood:
                state["mood_bias"] = dominant_hint
        drift_log = state.get("mood_drift_log")
        if not isinstance(drift_log, list):
            drift_log = []
            state["mood_drift_log"] = drift_log
        drift_log.append({
            "ts": self._memory_companion_now_iso(),
            "events": [{"type": e.get("event_type"), "delta": e.get("energy_delta"), "hint": _single_line(e.get("mood_hint"), 40)} for e in events],
            "cross_window_delta": round(cross_window_delta, 2),
            "total_delta": round(total_delta, 2),
        })
        if len(drift_log) > 20:
            drift_log[:] = drift_log[-20:]
        logger.debug(
            "[PrivateCompanion] 情绪漂移已应用: energy=%.1f->%.1f delta=%.1f cross_delta=%.1f hints=%s",
            current_energy, new_energy, total_delta, cross_window_delta, mood_hints,
        )

    async def _memory_companion_search_open_loops(self, *, session_id: str = "", limit: int = 3) -> list[dict[str, Any]]:
        """Search for unresolved open-loop / promise memories for proactive companionship."""
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return []
        searcher = getattr(bridge, "search_open_loops", None)
        if not callable(searcher):
            return []
        try:
            return await searcher(session_id=session_id, limit=limit)
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="search_open_loops"):
                return []
            logger.debug("[PrivateCompanion] open-loop 搜索失败: %s", _single_line(exc, 120))
            return []

    async def _memory_companion_record_dream_fragment(
        self,
        *,
        content: str = "",
        mood: str = "",
        dream_type: str = "",
        user_id: str = "",
    ) -> None:
        """Record a dream fragment into the memory plugin for cross-session continuity."""
        dream_text = _single_line(content, 800)
        if not dream_text:
            return
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return
        recorder = getattr(bridge, "record_persona_life", None)
        if not callable(recorder):
            return
        parts = [f"Bot 梦境碎片：{dream_text}"]
        if mood:
            parts.append(f"梦醒情绪：{_single_line(mood, 60)}")
        if dream_type:
            parts.append(f"梦境类型：{_single_line(dream_type, 40)}")
        full_content = " ".join(parts)
        try:
            await recorder(
                content=full_content,
                scope="private",
                session_id=f"private_companion:dream",
                memory_id=f"private_companion_dream_{uuid.uuid4().hex[:12]}",
                metadata={
                    "dream_type": _single_line(dream_type, 40),
                    "dream_mood": _single_line(mood, 60),
                    "query_anchors": ["梦境", "梦到", "做梦", "梦里的", "梦见"],
                },
                source_plugin="private_companion",
                importance=0.48,
                tags=["dream", "dream_fragment", "persona_life", "梦境碎片"],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_dream_fragment"):
                return
            logger.debug("[PrivateCompanion] 梦境碎片写入失败: %s", _single_line(exc, 120))

    def _memory_companion_get_relationship_phase(self, *, session_id: str = "") -> dict[str, Any]:
        """Get current relationship phase from the memory plugin."""
        bridge = self._memory_companion_bridge()
        if bridge is None:
            return {"phase": "unknown", "momentum": 0.0}
        getter = getattr(bridge, "get_relationship_phase", None)
        if not callable(getter):
            return {"phase": "unknown", "momentum": 0.0}
        try:
            return getter(session_id=session_id, scope="private")
        except Exception as exc:
            self._memory_companion_optional_dependency_failed(exc, where="get_relationship_phase")
            return {"phase": "unknown", "momentum": 0.0}
