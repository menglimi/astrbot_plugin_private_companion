# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterRecordObservationsMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 586 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import re
import uuid
from .helpers import _path_text, _safe_int, _single_line
from .memory_companion_adapter_shared import logger
from typing import Any



class MemoryCompanionAdapterRecordObservationsMixin:
    """MemoryCompanionAdapterRecordObservationsMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


    async def _memory_companion_record_proactive_message(
        self,
        *,
        user: dict[str, Any],
        user_id: str,
        text: str,
        umo: str = "",
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
        umo = _single_line(umo or user.get("umo"), 200)
        if not umo:
            return
        platform = umo.split(":", 1)[0] if ":" in umo else ""
        name = _single_line(user.get("nickname") or user.get("display_name") or user_id, 80)
        metadata = {
            "reason": _single_line(reason, 80),
            "action": _single_line(action, 80),
            "motive": _single_line(motive, 180),
            "action_summary": _single_line(action_summary, 240),
            "image_path": _path_text(image_path, 1000),
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
            if callable(proactive_recorder):
                await proactive_recorder(
                    content=f"Bot 主动向 {name or user_id} 发送：{content}",
                    scope="private",
                    session_id=umo,
                    platform=platform,
                    message_id=message_id,
                    subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                    object={"kind": "user", "id": str(user_id or ""), "name": name, "role": "private_companion_target"},
                    metadata={
                        **metadata,
                        "date": self._memory_companion_now_iso()[:10],
                        "event_type": "proactive_message",
                        "action_label": "主动私聊",
                        "query_anchors": ["主动消息", "主动私聊", "刚才主动说了什么", "最近主动联系", "发送给用户"],
                    },
                    source_plugin="private_companion",
                    confidence=0.92,
                    importance=0.58,
                    tags=["proactive", "proactive_message", "bot_action", "private_companion"],
                    occurred_at=self._memory_companion_now_iso(),
                )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_proactive_message"):
                return
            logger.debug("MemoryCompanion 主动消息桥接写入失败: %s", _single_line(exc, 120))

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
            logger.debug("MemoryCompanion 图片观察写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_photo_generation(
        self,
        event: Any | None,
        *,
        prompt: str,
        kind: str = "",
        intent_kind: str = "",
        backend: str = "",
        image_path: str = "",
        note: str = "",
        sent: bool = False,
        trigger: str = "",
        scene_preset: str = "",
        reference_image_path: str = "",
        reference_used: bool | None = None,
    ) -> None:
        prompt_text = _single_line(prompt, 900)
        if not prompt_text and not image_path:
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_event", None) if bridge is not None else None
        if not callable(recorder):
            recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
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
        user_id = ""
        user_name = ""
        if event is not None:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
            try:
                user_name = _single_line(self._sender_display_name(event), 80)
            except Exception:
                user_name = ""
        kind_text = _single_line(intent_kind or kind, 40) or "图片"
        backend_text = _single_line(backend, 80)
        scene_text = _single_line(scene_preset, 80)
        ref_text = _path_text(reference_image_path, 1000)
        legacy_reference_used = bool(
            ref_text
            and re.search(
                r"(?:已使用|已提交|成功提交|已带入)[^；。]{0,16}参考图"
                r"|参考图[^；。]{0,16}(?:已使用|已提交|成功提交|已带入)",
                str(note or ""),
                flags=re.I,
            )
        )
        effective_reference_used = (
            bool(reference_used)
            if reference_used is not None
            else legacy_reference_used
        )
        status = "生成并发送" if sent else "生成"
        content = (
            f"Bot 通过生图能力{status}了一张{kind_text}。"
            f"画面要求：{prompt_text or '未记录'}。"
            f"{' 场景预设：' + scene_text + '。' if scene_text else ''}"
            f"{' 后端：' + backend_text + '。' if backend_text else ''}"
            f"{' 使用了参考图。' if effective_reference_used else ''}"
            f"{' 图片路径：' + _path_text(image_path, 1000) + '。' if image_path else ''}"
        )
        memory_key = uuid.uuid4().hex[:12]
        try:
            await recorder(
                content=content,
                memory_type="photo_generation",
                scope=scope,
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_photo_{memory_key}",
                memory_id=f"private_companion_photo_{memory_key}",
                subject={"kind": "bot", "id": "self", "name": "Bot", "role": "bot_self"},
                object={"kind": "user", "id": user_id, "name": user_name, "role": "conversation_partner"},
                visibility="private_pair" if scope == "private" else "group_public",
                sayability="direct",
                reality_level="bot_action",
                lifecycle="recent",
                confidence=0.86,
                importance=0.52,
                review_status="auto",
                tags=[
                    "photo_generation",
                    "image",
                    "bot_action",
                    "private_companion",
                    _single_line(kind_text, 40),
                    _single_line(trigger, 40),
                ],
                metadata={
                    "date": self._memory_companion_now_iso()[:10],
                    "event_type": "photo_generation",
                    "action_label": "生图/拍照",
                    "trigger": _single_line(trigger, 40),
                    "kind": _single_line(kind, 40),
                    "intent_kind": _single_line(intent_kind, 40),
                    "backend": backend_text,
                    "prompt": prompt_text,
                    "image_path": _path_text(image_path, 1000),
                    "note": _single_line(note, 220),
                    "sent": bool(sent),
                    "scene_preset": scene_text,
                    "reference_image_path": ref_text,
                    "used_reference": effective_reference_used,
                    "query_anchors": [
                        "刚才生成了什么图",
                        "刚才发了什么图",
                        "刚才画了什么",
                        "表情包",
                        "自拍",
                        "生图",
                        "图片生成",
                    ],
                },
                source_plugin="private_companion",
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_photo_generation"):
                return
            logger.debug("MemoryCompanion 生图记录写入失败: %s", _single_line(exc, 120))

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
            logger.debug("MemoryCompanion 用户习惯写入失败: %s", _single_line(exc, 120))

    async def _memory_companion_record_daily_outfit(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not _path_text(item.get("path"), 1000):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(item.get("date"), 20)
        prompt = _single_line(item.get("prompt"), 600)
        note = _single_line(item.get("note"), 160)
        path = _path_text(item.get("path"), 1000)
        schedule_hint = ""
        try:
            schedule_hint = _single_line(self._daily_outfit_schedule_text(), 280)
        except Exception:
            schedule_hint = ""
        content = (
            f"{date_text or '今天'}的 Bot 每日穿搭图已生成。"
            f"这条记忆只记录生成当天的基础/默认穿搭，可用于回答当天穿什么等问题；它不是后续剧情中的永久当前状态。"
            f"如果近期对话已经明确换装，应优先承接那次换装，而不是恢复这张图里的衣服。"
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
                    "query_anchors": ["今日穿搭", "当天基础穿搭", "每日穿搭", "衣服颜色", "当天穿什么"],
                },
                source_plugin="private_companion",
                confidence=0.76,
                importance=0.62,
                tags=["daily_outfit", "outfit", "clothing", "daily_baseline", "persona_life", "衣服颜色", "今日穿搭"],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_daily_outfit"):
                return
            logger.debug("MemoryCompanion 每日穿搭写入失败: %s", _single_line(exc, 120))

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
            logger.debug("MemoryCompanion 创作进展写入失败: %s", _single_line(exc, 120))

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
        recorder = getattr(bridge, "record_qzone_action", None) if bridge is not None else None
        if not callable(recorder):
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
        date_text = ""
        try:
            date_text = self._environment_now().date().isoformat()
        except Exception:
            date_text = self._memory_companion_now_iso()[:10]
        try:
            await recorder(
                content=memory_content,
                scope="unknown",
                session_id=session_id,
                platform=platform,
                message_id=f"private_companion_qzone_{memory_key}",
                memory_id=f"private_companion_qzone_{memory_key}",
                memory_type="qzone_action",
                reality_level="bot_action",
                sayability="direct",
                metadata={
                    "date": date_text,
                    "event_type": "qzone_publish",
                    "action_label": "QQ 空间说说",
                    "reason": reason_text,
                    "tid": _single_line(tid, 80),
                    "text": content,
                    "clean_visible_text": content,
                    "image_count": safe_image_count,
                    "verified": bool(verified) if verified is not None else None,
                    "query_anchors": [
                        "QQ空间",
                        "说说",
                        "QQ 空间说说",
                        "刚才发了什么",
                        "刚刚发了什么",
                        "发了什么说说",
                        "最近已发说说",
                        "空间动态",
                        "公开动态余味",
                    ],
                },
                source_plugin="private_companion",
                confidence=0.84,
                importance=0.58,
                tags=["qzone", "qzone_publish", "bot_action", "persona_life", "说说", "QQ空间", reason_text],
                occurred_at=self._memory_companion_now_iso(),
            )
        except Exception as exc:
            if self._memory_companion_optional_dependency_failed(exc, where="record_qzone_publish"):
                return
            logger.debug("MemoryCompanion QQ 空间发布写入失败: %s", _single_line(exc, 120))
