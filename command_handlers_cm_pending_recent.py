# -*- coding: utf-8 -*-
"""待确认与最近上下域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 414 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import uuid
from .command_handlers_shared import logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from typing import Any



class CommandHandlersCmPendingRecentMixin:
    """待确认与最近上下域（从 CommandHandlersMixin 拆出）。"""


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
                self._save_data_sync(sections={"manual_diagnosis_pending_config"})
            return ""
        token = uuid.uuid4().hex[:6]
        store[key] = {
            "token": token,
            "ts": _now_ts(),
            "question": _single_line(question, 260),
            "changes": proposals,
        }
        self._save_data_sync(sections={"manual_diagnosis_pending_config"})
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
                logger.debug("答疑携带图片提取失败: %s", _single_line(exc, 120))
        reply_cache_getter = getattr(self, "_photo_reference_sources_from_reply_cache", None)
        if callable(reply_cache_getter):
            try:
                for source in reply_cache_getter(event):
                    add(source, "引用撤回/缓存图片")
            except Exception as exc:
                logger.debug("答疑引用缓存图片提取失败: %s", _single_line(exc, 120))
        reply_getter = getattr(self, "_photo_reference_sources_from_reply_event", None)
        if callable(reply_getter):
            try:
                for source in await reply_getter(event):
                    add(source, "引用消息图片")
            except Exception as exc:
                logger.debug("答疑引用图片提取失败: %s", _single_line(exc, 120))
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
                logger.info("答疑图片视觉摘要失败: %s", _single_line(exc, 120))
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
            self._schedule_data_save(sections={"manual_diagnosis_recent_context"})
        except Exception:
            try:
                self._save_data_sync(sections={"manual_diagnosis_recent_context"})
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
