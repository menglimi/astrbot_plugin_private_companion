# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import uuid
import asyncio
from typing import Any

from astrbot.api import logger

from .helpers import _single_line


class RememberYouAdapterMixin:
    """Optional bridge helpers for astrbot_plugin_remember_you."""

    def _remember_you_bridge(self) -> Any | None:
        for module_name in (
            "data.plugins.astrbot_plugin_remember_you.main",
            "astrbot_plugin_remember_you.main",
        ):
            module = sys.modules.get(module_name)
            getter = getattr(module, "get_active_bridge", None) if module is not None else None
            if not callable(getter):
                continue
            try:
                bridge = getter()
            except Exception:
                bridge = None
            if bridge is not None:
                return bridge
        return None

    def _remember_you_coordination_status(self) -> dict[str, Any]:
        bridge = self._remember_you_bridge()
        if bridge is None:
            return {"available": False}
        getter = getattr(bridge, "coordination_status", None)
        if not callable(getter):
            return {"available": True}
        try:
            status = getter()
        except Exception as exc:
            logger.debug("[PrivateCompanion] RememberYou 协同状态读取失败: %s", _single_line(exc, 120))
            return {"available": True, "error": _single_line(exc, 120)}
        return status if isinstance(status, dict) else {"available": True}

    def _remember_you_mark_deferred_section(
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
                existing = getattr(target, "remember_you_companion_deferred_sections", None)
                if isinstance(existing, set):
                    sections = set(existing)
                elif isinstance(existing, (list, tuple)):
                    sections = {_single_line(item, 80) for item in existing if _single_line(item, 80)}
                elif isinstance(existing, str):
                    sections = {_single_line(item, 80) for item in existing.split(",") if _single_line(item, 80)}
                else:
                    sections = set()
                sections.add(normalized)
                setattr(target, "remember_you_companion_deferred_sections", sections)
            except Exception:
                pass

    def _remember_you_should_defer_prompt_section(
        self,
        section: str,
        event: Any | None = None,
        req: Any | None = None,
    ) -> bool:
        bridge = self._remember_you_bridge()
        checker = getattr(bridge, "should_defer_private_companion_section", None) if bridge is not None else None
        if not callable(checker):
            return False
        try:
            should_defer = bool(checker(section))
        except Exception as exc:
            logger.debug("[PrivateCompanion] RememberYou 协同状态读取失败: %s", _single_line(exc, 120))
            return False
        if should_defer:
            self._remember_you_mark_deferred_section(section, event, req)
        return should_defer

    def _remember_you_build_private_context(
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
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _remember_you_schedule_owner_context(self) -> tuple[str, dict[str, Any]]:
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

    def _remember_you_schedule_session_context(self, *, message_text: str = "") -> dict[str, Any]:
        user_id, user = self._remember_you_schedule_owner_context()
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

    async def _remember_you_compose_schedule_context(
        self,
        *,
        kind: str = "daily_plan",
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        max_chars: int = 1200,
    ) -> str:
        bridge = self._remember_you_bridge()
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
            text = await asyncio.wait_for(
                composer(
                    query=query,
                    session_context=self._remember_you_schedule_session_context(message_text=query),
                    top_k=6 if kind == "daily_plan" else 5,
                    max_chars=max(500, min(1800, int(max_chars or 1200))),
                ),
                timeout=4.0,
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] RememberYou 日程上下文读取失败: %s", _single_line(exc, 120))
            return ""
        text = str(text or "").strip()
        if not text:
            return ""
        if "没有检索到足够相关的长期记忆" in text and text.count("\n- ") <= 1:
            return ""
        return text[: max(300, int(max_chars or 1200))]

    async def _remember_you_record_daily_plan(self, plan: dict[str, Any]) -> None:
        if not isinstance(plan, dict):
            return
        bridge = self._remember_you_bridge()
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
            logger.debug("[PrivateCompanion] RememberYou 日程写入失败: %s", _single_line(exc, 120))

    async def _remember_you_record_detail_enhancement(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        if not isinstance(segment, dict) or not isinstance(detail, dict):
            return
        bridge = self._remember_you_bridge()
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
            logger.debug("[PrivateCompanion] RememberYou 细化写入失败: %s", _single_line(exc, 120))

    def _remember_you_build_group_context(
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
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _remember_you_attach_context(self, event: Any | None, payload: dict[str, Any]) -> None:
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
            logger.debug("[PrivateCompanion] RememberYou 上下文线索挂载失败: %s", _single_line(exc, 120))

    def _remember_you_attach_private_context(
        self,
        event: Any | None,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
    ) -> None:
        payload = self._remember_you_build_private_context(user_id=user_id, user=user, text=text, event=event)
        self._remember_you_attach_context(event, payload)

    def _remember_you_attach_group_context(
        self,
        event: Any | None,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        payload = self._remember_you_build_group_context(
            group_id=group_id,
            group=group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            event=event,
        )
        self._remember_you_attach_context(event, payload)

    async def _remember_you_record_proactive_message(
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
        bridge = self._remember_you_bridge()
        recorder = getattr(bridge, "record_proactive_message", None) if bridge is not None else None
        if not callable(recorder):
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
            await recorder(
                content=f"Bot 主动向 {name or user_id} 发送：{content}",
                scope="private",
                session_id=umo,
                platform=platform,
                message_id=f"private_companion_proactive_{uuid.uuid4().hex}",
                subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                object={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                metadata=metadata,
                source_plugin="private_companion",
                confidence=0.92,
                importance=0.58,
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] RememberYou 主动消息桥接写入失败: %s", _single_line(exc, 120))

    async def _remember_you_record_image_observation(
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
        bridge = self._remember_you_bridge()
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
            logger.debug("[PrivateCompanion] RememberYou 图片观察写入失败: %s", _single_line(exc, 120))
