# -*- coding: utf-8 -*-
"""EventDispatchPromptInjectionMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（13 个方法 + 3 个模块级名字 + 0 个类级赋值 / 518 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from .conversation_prompt_section import PromptRenderMode, PromptSection, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from collections.abc import Mapping
from copy import deepcopy
from typing import Any



_PROMPT_MODULE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "state.session_update": ("模拟状态更新", "增量注入 Bot 自身模拟状态变化，并标明不是用户事实或长期记忆。"),
    "state.lightweight": ("轻量模拟状态", "短句/轻量被动回复复用 Bot 自身模拟状态，只提供身体状态和表达节奏。"),
    "state.full": ("完整模拟状态", "注入 Bot 自身模拟状态，只提供身体状态、情绪和表达节奏，不混入用户事实。"),
    "life.context": ("模拟生活背景", "单独注入 Bot 拟人化日程和生活线索，便于排查日程污染。"),
    "important.dates": ("重要日期", "单独注入近期重要日期，只在用户提到相关计划或纪念时自然承接。"),
    "worldview.adaptation": ("世界观适配", "补充当前人格/世界观的表达边界，避免回复和设定脱节。"),
    "identity.anchor": ("身份锚点", "固定私聊对象身份和称呼，降低昵称变化、群名片或历史记忆导致的认错。"),
    "turn.continuation": ("连续补话", "把用户短时间内连续补充的内容视作同一轮输入，避免逐条误回。"),
    "recall.query": ("历史召回查询", "当用户询问此前聊过什么时，提供近期可自然引用的消息。"),
    "image.direct": ("图片直挂", "说明图片已交给视觉主模型，要求同时理解画面和用户借图表达。"),
    "image.vision": ("图片视觉摘要", "当前主模型不能可靠直接看图时，注入视觉模型摘要和回复目标。"),
    "image.fallback": ("图片兜底", "图片存在但没有可用视觉摘要时，防止模型编造画面内容。"),
    "image.only.vision": ("单图视觉摘要", "用户只发图没有文字时，注入该图摘要并要求自然接住图片表达。"),
    "image.only.fallback": ("单图兜底", "用户只发图但识图失败时，要求不要沉默也不要编造。"),
    "image.reply.vision": ("引用图片摘要", "用户引用/回复某张图时，把被引用图片作为本轮主要依据。"),
    "image.reply.fallback": ("引用图片兜底", "引用图片无法识别时，避免把旧图或历史内容当成当前引用目标。"),
    "creative.hidden": ("创作上下文", "在用户触发创作相关话题时，补充必要的创作状态和边界。"),
    "bookshelf.secret": ("资料柜隐藏线索", "在合适场景提供资料柜相关的隐藏上下文，不主动暴露机制。"),
    "bookshelf.reading": ("阅读上下文", "补充近期阅读/资料柜内容对本轮回复的影响。"),
    "news.recent": ("近期新闻", "用户聊到新闻/时事时，提供近期阅读过的新闻上下文。"),
    "web_exploration.recent": ("主动搜索近况", "用户询问最近搜索/网页探索时，提供真实搜索词、动机、笔记和来源。"),
    "skill.growth": ("能力成长", "注入角色近期能力变化，帮助回复体现可成长性。"),
    "skill.growth.match": ("本轮相关技能", "用户提到已追踪技能时，只注入命中的能力边界。"),
    "companion.planner": ("陪伴规划", "整合关系画像、互动节奏和回复策略，控制陪伴感与边界。"),
    "proactive.reply_context": ("悬着话头", "只用于明确被挂起的半句主动，不再按旧主动消息纠偏普通被动回复。"),
    "detail.injection": ("日程细节", "补充当前生活片段和可用碎片，让回复有具体落点。"),
    "timer.scheduling": ("主动预约", "允许模型在合适时隐藏预约下一次主动开口。"),
    "environment.lightweight": ("轻量环境", "短句被动回复使用的时间和平台边界，避免完全丢失当前语境。"),
    "environment.perception": ("环境感知", "完整被动回复使用的时间、日期、平台和模型环境边界。"),
    "environment.request": ("请求级环境感知", "状态总注入关闭时仍可单独补充的时间、日期、平台和消息媒介边界。"),
    "tts.rule": ("TTS 基础规则", "告诉模型本轮是否可以使用插件私有语音标签、目标语种、双语展示和格式示例。"),
    "tts.frequency": ("TTS 频率控制", "自动语音概率未命中时的软约束；没有用户明确请求时要求本轮必须纯文字，不主动使用任何语音标签。"),
    "tts.block": ("TTS 强约束禁用", "强约束模式下概率未命中或会话冷却时的反向规则，要求本轮禁止任何语音内容。"),
    "tts.force": ("TTS 主用户倾向", "主用户或明确 @ 主用户时的语音倾向提示，仍由模型按语境判断。"),
    "tts.user_request": ("用户语音请求", "用户明确想听语音/声音时的顺应规则，不受自动语音概率限制。"),
    "tts.functional_reply": ("TTS 功能性回复取舍", "主回复链处理指令或功能操作时，优先保留便于查看、复制和操作的文字结果。"),
    "capability.boundary": ("能力边界", "群聊中防止模型承诺现实操作、网络操作或无法执行的代办。"),
    "tools.atrelay": ("跨群转述工具", "用户可能要转告、私聊、@ 群友时注入的工具使用说明。"),
    "tools.qzone": ("QQ 空间工具", "用户提到空间、说说、动态等场景时注入的工具使用说明。"),
    "group.persona_denoise": ("群聊人格降噪", "群聊回复时降低私聊腔、过度亲密和状态外露。"),
    "group.context": ("群聊上下文", "群聊回复时补充群氛围、当前发言者、最近话题和连续补充内容。"),
    "identity.non_target": ("非目标私聊防串", "私聊对象不是主陪伴用户时防止套用专属关系和记忆。"),
    "forward.message": ("合并转发上下文", "合并转发、聊天记录或引用卡片进入回复时注入的阅读内容或转述。"),
    "reply.chain": ("引用链上下文", "用户引用的消息本身继续引用更早消息时，按层级提供原始被引用内容。"),
}

_PROMPT_MODULE_PREFIX_DESCRIPTIONS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("state.", ("状态片段", "提供当前拟人身体状态、情绪底色和表达节奏。")),
    ("image.", ("图片片段", "帮助模型理解当前图片、引用图片或识图失败时的回复边界。")),
    ("bookshelf.", ("资料柜片段", "提供资料柜/阅读相关上下文。")),
    ("proactive.", ("主动相关片段", "处理主动消息节奏、边界和明确挂起的话头。")),
    ("timer.", ("预约片段", "处理模型可见的主动预约规则。")),
    ("creative.", ("创作片段", "提供创作状态或创作相关边界。")),
    ("environment.", ("环境片段", "提供当前时间、日期、平台、模型或消息媒介边界。")),
)

_PROMPT_SECTION_DESCRIPTIONS: dict[str, str] = {
    "内容选择菜单": "限制本次主动消息可选内容类型，避免把多个动机拼成一条。",
    "主动能力检索": "提示模型可使用的主动能力和素材来源。",
    "禁止事项": "列出主动消息不能触碰的回复式承接、幻觉和污染项。",
    "最近主动行为闭环": "提供最近主动行为后的反馈，用于降低打扰和重复。",
    "主动意图具体化": "要求主动消息围绕一个具体由头，减少泛泛关心。",
    "语言风格疲劳": "提示模型避开最近重复的开头、口癖和句式。",
    "主动承接边界": "说明本轮是独立主动还是续接来源，防止把历史写成当前对话。",
    "媒体真实性边界": "当本轮不会发图/媒体时，禁止正文假装发了照片或图片。",
    "新闻阅读上下文": "提供新闻阅读或分享相关背景。",
    "人格": "当前会话人格和表达站位。",
    "对象": "收信人身份、称呼或关系背景。",
    "收信人": "主动消息目标用户和关系上下文。",
    "主动原因": "本次主动触发原因和动机。",
    "当前状态": "Bot 自身模拟状态和情绪底色。",
    "当前拟人状态": "Bot 自身身体状态和表达节奏。",
    "当前日程背景": "当前日程素材和时段背景。",
    "当前会话 TTS 规则": "当前会话语音/TTS 的格式和使用规则。",
    "必须满足的格式重点": "语音或特殊输出格式的硬性要求。",
    "当前版本": "当前输出格式或功能版本说明。",
    "这次想分享的画面钩子": "图片/画面类主动消息的素材落点。",
    "Bot 关系网": "Bot 熟悉角色卡，供主动拍照/生图选择可入镜的关系人物。",
    "生图风格": "图片生成或图片分享相关风格要求。",
}


class EventDispatchPromptInjectionMixin:
    """EventDispatchPromptInjectionMixin（从 EventDispatchMixin 拆出）。"""


    def _prompt_module_info(self, key: str, fallback_title: str = "") -> tuple[str, str]:
        normalized_key = _single_line(key, 80)
        if normalized_key in _PROMPT_MODULE_DESCRIPTIONS:
            return _PROMPT_MODULE_DESCRIPTIONS[normalized_key]
        for prefix, info in _PROMPT_MODULE_PREFIX_DESCRIPTIONS:
            if normalized_key.startswith(prefix):
                return info
        title = _single_line(fallback_title, 60)
        if title in _PROMPT_SECTION_DESCRIPTIONS:
            return title, _PROMPT_SECTION_DESCRIPTIONS[title]
        if normalized_key.startswith("section."):
            return title or "提示词段落", _PROMPT_SECTION_DESCRIPTIONS.get(title, "按标题从完整 prompt 中拆出的段落，用于定位主动主链提示词来源。")
        return title or normalized_key or "提示词片段", "提示词组装中的一个片段；用于排查它对本轮模型输入的影响。"

    def _split_prompt_modules_by_heading(self, content: str) -> list[dict[str, Any]]:
        """Recover modules from persisted pre-manifest prompt traces.

        New traces must supply a section manifest instead of inferring semantic
        boundaries from rendered text.  This parser remains only so the
        diagnostics page can display trace records written by older releases.
        """

        text = str(content or "").strip()
        if not text:
            return []
        matches = list(re.finditer(r"(?m)^【([^】\n]{1,40})】\s*$", text))
        modules: list[dict[str, Any]] = []
        if not matches:
            title, description = self._prompt_module_info("prompt.full", "完整提示词")
            return [
                {
                    "key": "prompt.full",
                    "source": "merged_prompt",
                    "priority": 100,
                    "title": title,
                    "description": description,
                    "content": text,
                    "chars": len(text),
                }
            ]
        if matches[0].start() > 0:
            intro = text[: matches[0].start()].strip()
            if intro:
                title, description = self._prompt_module_info("section.0.intro", "开场说明")
                modules.append(
                    {
                        "key": "section.0.intro",
                        "source": "prompt_heading_split",
                        "priority": 0,
                        "title": title,
                        "description": description,
                        "content": intro,
                        "chars": len(intro),
                    }
                )
        for index, match in enumerate(matches):
            heading = _single_line(match.group(1), 60) or f"段落 {index + 1}"
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            part = text[start:end].strip()
            title, description = self._prompt_module_info(f"section.{index + 1}.{heading}", heading)
            modules.append(
                {
                    "key": f"section.{index + 1}.{heading}",
                    "source": "prompt_heading_split",
                    "priority": index + 1,
                    "title": title,
                    "description": description,
                    "content": part,
                    "chars": len(part),
                }
            )
        return modules

    def _normalize_prompt_injection_modules(
        self,
        content: str,
        modules: Any = None,
        *,
        legacy_heading_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        """Normalize a typed section manifest for prompt diagnostics.

        ``legacy_heading_fallback`` is intentionally enabled for callers that
        read old persisted traces.  Live trace writers disable it, so headings
        inside rendered or user-supplied text cannot become fake modules.
        """

        if isinstance(modules, (list, tuple)):
            raw_modules = list(modules)
        elif legacy_heading_fallback:
            raw_modules = self._split_prompt_modules_by_heading(content)
        else:
            text = str(content or "").strip()
            raw_modules = (
                [
                    {
                        "key": "prompt.full",
                        "source": "merged_prompt",
                        "priority": 100,
                        "title": "完整提示词",
                        "content": text,
                        "chars": len(text),
                    }
                ]
                if text
                else []
            )
        result: list[dict[str, Any]] = []
        max_modules = 28
        max_content = 6000
        for index, raw in enumerate(raw_modules[:max_modules]):
            if isinstance(raw, PromptSection):
                section = raw
                module_content = render_prompt_sections(
                    [section],
                    mode=PromptRenderMode.BODY_ONLY,
                ).strip()
                key = _single_line(section.key, 100) or f"module.{index + 1}"
                source = _single_line(section.source, 80)
                raw_title = _single_line(section.title, 80)
                priority = index
                raw_description = ""
                raw_chars = len(module_content)
                raw_metadata = dict(section.metadata)
            elif isinstance(raw, Mapping):
                raw_content = raw.get("content")
                module_content = raw_content.strip() if isinstance(raw_content, str) else ""
                raw_chars = _safe_int(raw.get("chars"), len(module_content), 0)
                key = _single_line(raw.get("key"), 100) or f"module.{index + 1}"
                source = _single_line(raw.get("source"), 80)
                raw_title = _single_line(raw.get("title"), 80)
                priority = _safe_int(raw.get("priority"), index, 0)
                raw_description = raw.get("description")
                raw_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
            else:
                continue
            if not module_content:
                continue
            inferred_title, description = self._prompt_module_info(key, raw_title)
            title = raw_title or inferred_title
            if raw_description:
                description = _single_line(raw_description, 220) or description
            truncated = len(module_content) > max_content
            if truncated:
                module_content = module_content[:max_content] + "\n...[模块内容已截断]"
            item = {
                "key": key,
                "source": source,
                "priority": priority,
                "title": title,
                "description": description,
                "chars": raw_chars,
                "truncated": truncated,
                "preview": _single_line(module_content, 180),
                "content": module_content,
            }
            if raw_metadata:
                item["metadata"] = {
                    _single_line(metadata_key, 40): _single_line(metadata_value, 220)
                    for metadata_key, metadata_value in raw_metadata.items()
                    if _single_line(metadata_key, 40)
                    and _single_line(metadata_value, 220)
                }
            result.append(item)
        return result

    def _legacy_proactive_prompt_trace_text(self, item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        parts = [
            str(item.get("content") or ""),
            str(item.get("preview") or ""),
            str(item.get("title") or ""),
        ]
        modules = item.get("modules")
        if isinstance(modules, list):
            for module in modules:
                if isinstance(module, dict):
                    parts.extend(
                        [
                            str(module.get("key") or ""),
                            str(module.get("title") or ""),
                            str(module.get("content") or ""),
                            str(module.get("preview") or ""),
                        ]
                    )
        return "\n".join(part for part in parts if part)

    def _is_legacy_proactive_prompt_trace(self, item: Any) -> bool:
        text = self._legacy_proactive_prompt_trace_text(item)
        if not text:
            return False
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and meta_leak_checker(text):
            return True
        markers = (
            "【怎么写这条消息】",
            "【禁止事项】",
            "【状态表现层】",
            "【主动意图具体化】",
            "【语言风格疲劳】",
            "你正在为 Private Companion 生成一条主动私聊消息。下面这段规则是稳定规则前缀",
            "日程主语归属必须稳定",
        )
        return any(marker in text for marker in markers)

    def _cleanup_legacy_proactive_prompt_traces(self) -> bool:
        root = self.data.get("recent_prompt_injections") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(root, dict):
            return False
        changed = False
        for key in ("proactive",):
            items = root.get(key)
            if not isinstance(items, list):
                continue
            kept = [item for item in items if not self._is_legacy_proactive_prompt_trace(item)]
            if len(kept) != len(items):
                root[key] = kept[:5]
                changed = True
        return changed

    def _prompt_injection_trace_id_for_event(self, event: AstrMessageEvent) -> str:
        cached = _single_line(getattr(event, "private_companion_prompt_trace_id", ""), 80)
        if cached:
            return cached
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event)
        sender_id = self._event_sender_id(event)
        message_id = self._event_message_id(event)
        inbound_ts = self._event_inbound_activity_ts(event)
        text = self._event_text_for_recall_cache(event, limit=280)
        seed = "|".join(
            part
            for part in (
                session,
                sender_id,
                message_id,
                f"{inbound_ts:.3f}" if inbound_ts > 0 else "",
                text,
            )
            if part
        )
        trace_id = f"evt-{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:16]}" if seed else f"evt-{uuid.uuid4().hex[:16]}"
        try:
            setattr(event, "private_companion_prompt_trace_id", trace_id)
        except Exception:
            pass
        return trace_id

    def _prompt_injection_message_preview_for_event(self, event: AstrMessageEvent) -> str:
        cached = _single_line(getattr(event, "private_companion_prompt_trace_preview", ""), 220)
        if cached:
            return cached
        text = self._event_text_for_recall_cache(event, limit=280)
        if not text:
            text = _single_line(getattr(event, "message_str", ""), 280)
        if not text:
            text = self._event_existing_reply_result_preview(event)
        preview = _single_line(text, 220)
        try:
            setattr(event, "private_companion_prompt_trace_preview", preview)
        except Exception:
            pass
        return preview

    def _prompt_injection_sender_label_for_event(self, event: AstrMessageEvent) -> str:
        sender_id = _single_line(self._event_sender_id(event), 80)
        display_name = _single_line(self._sender_display_name(event), 40)
        if display_name and sender_id and display_name != sender_id:
            return f"{display_name}/{sender_id}"
        return display_name or sender_id

    @staticmethod
    def _prompt_injection_preview_is_internal_prompt(text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        internal_markers = (
            "【语音消息规则】",
            "<pc_tts>",
            "</pc_tts>",
            "语音消息规则",
            "提示词片段",
            "请求级环境感知注入",
            "被动回复注入",
            "当前语音正文目标语种",
            "自然聊天时用中文文字推进对话",
            "不要写“中文含义”",
        )
        if any(marker in cleaned for marker in internal_markers):
            return True
        if cleaned.startswith("【") and "规则" in cleaned[:40]:
            return True
        return False

    def _safe_prompt_injection_message_preview(self, value: Any, *, limit: int = 220) -> str:
        preview = _single_line(value, limit)
        if self._prompt_injection_preview_is_internal_prompt(preview):
            return ""
        return preview

    def _upsert_recent_prompt_injection_event(
        self,
        *,
        trace_id: str,
        item: dict[str, Any],
        message_preview: str = "",
        sender_label: str = "",
    ) -> None:
        trace = _single_line(trace_id, 80) or f"trace-{uuid.uuid4().hex[:16]}"
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        preview = (
            self._safe_prompt_injection_message_preview(message_preview)
            or self._safe_prompt_injection_message_preview(metadata.get("触发消息"))
        )
        sender = _single_line(sender_label, 80)
        events = self.data.setdefault("recent_prompt_injection_events", [])
        if not isinstance(events, list):
            events = []
            self.data["recent_prompt_injection_events"] = events
        target: dict[str, Any] | None = None
        for entry in events:
            if isinstance(entry, dict) and _single_line(entry.get("trace_id"), 80) == trace:
                target = entry
                break
        ts = _safe_float(item.get("ts"), _now_ts(), 0.0)
        copied_item = deepcopy(item)
        if target is None:
            target = {
                "trace_id": trace,
                "session": _single_line(item.get("session"), 160) or "unknown",
                "sender_label": sender,
                "message_preview": preview,
                "first_ts": ts,
                "last_ts": ts,
                "items": [],
            }
            events.append(target)
        else:
            if not _single_line(target.get("session"), 160):
                target["session"] = _single_line(item.get("session"), 160) or "unknown"
            if preview:
                target["message_preview"] = preview
            if sender:
                target["sender_label"] = sender
            target["first_ts"] = min(_safe_float(target.get("first_ts"), ts, 0.0), ts)
            target["last_ts"] = max(_safe_float(target.get("last_ts"), ts, 0.0), ts)
        event_items = target.get("items")
        if not isinstance(event_items, list):
            event_items = []
            target["items"] = event_items
        copied_item["trace_seq"] = len(event_items)
        event_items.append(copied_item)
        del event_items[32:]
        events.sort(key=lambda entry: _safe_float(entry.get("last_ts"), 0.0, 0.0), reverse=True)
        del events[10:]

    async def _record_prompt_injection_snapshot(
        self,
        *,
        kind: str,
        session: str,
        title: str,
        text: str,
        mode: str = "",
        metadata: dict[str, Any] | None = None,
        modules: list[dict[str, Any]] | None = None,
        section_manifest: list[Any] | tuple[Any, ...] | None = None,
        trace_id: str = "",
        message_preview: str = "",
        sender_label: str = "",
    ) -> None:
        content = str(text or "").strip()
        kind = _single_line(kind, 20) or "unknown"
        if kind not in {"passive", "proactive", "request"} or not content:
            return
        now = _now_ts()
        max_content = 12000
        truncated = len(content) > max_content
        if truncated:
            content = content[:max_content] + "\n...[已截断]"
        selected_manifest = (
            section_manifest
            if isinstance(section_manifest, (list, tuple))
            else modules
        )
        normalized_modules = self._normalize_prompt_injection_modules(
            str(text or ""),
            selected_manifest,
            legacy_heading_fallback=False,
        )
        if not normalized_modules:
            normalized_modules = self._normalize_prompt_injection_modules(
                str(text or ""),
                None,
                legacy_heading_fallback=False,
            )
        item = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now) if hasattr(self, "_format_timestamp_elapsed") else "",
            "kind": kind,
            "session": _single_line(session, 160) or "unknown",
            "title": _single_line(title, 80),
            "mode": _single_line(mode, 40),
            "chars": len(str(text or "")),
            "truncated": truncated,
            "preview": _single_line(content, 220),
            "content": content,
            "modules": normalized_modules,
            "metadata": {
                _single_line(key, 40): _single_line(value, 220)
                for key, value in (metadata or {}).items()
                if _single_line(key, 40) and _single_line(value, 220)
            },
        }
        if kind == "proactive" and self._is_legacy_proactive_prompt_trace(item):
            return
        async with self._data_lock:
            root = self.data.setdefault("recent_prompt_injections", {})
            if not isinstance(root, dict):
                root = {}
                self.data["recent_prompt_injections"] = root
            items = root.setdefault(kind, [])
            if not isinstance(items, list):
                items = []
                root[kind] = items
            items.insert(0, item)
            del items[5:]
            if kind == "request" and any(
                isinstance(module, dict) and _single_line(module.get("key"), 100).startswith("tts.")
                for module in item.get("modules", [])
            ):
                tts_items = root.setdefault("tts", [])
                if not isinstance(tts_items, list):
                    tts_items = []
                    root["tts"] = tts_items
                tts_items.insert(0, item)
                del tts_items[8:]
            self._upsert_recent_prompt_injection_event(
                trace_id=trace_id,
                item=item,
                message_preview=message_preview,
                sender_label=sender_label,
            )
        try:
            self._schedule_data_save(
                sections={"recent_prompt_injections", "recent_prompt_injection_events"},
                delay=2.0,
            )
        except Exception:
            pass
