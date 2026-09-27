# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPrivatePassivePromptPart03Mixin。

由 tools/split_mixin_domain.py 从 main_private_passive_prompt.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 288 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPrivatePassivePromptMixin）。
"""
from __future__ import annotations

from .main_private_passive_prompt_shared import logger
from .main_private_passive_prompt_shared import Any
from .main_private_passive_prompt_shared import AstrMessageEvent
from .main_private_passive_prompt_shared import PromptRenderMode
from .main_private_passive_prompt_shared import PromptSurface
from .main_private_passive_prompt_shared import ProviderRequest
from .main_private_passive_prompt_shared import _now_ts
from .main_private_passive_prompt_shared import _safe_float
from .main_private_passive_prompt_shared import _safe_int
from .main_private_passive_prompt_shared import _single_line
from .main_private_passive_prompt_shared import hashlib
from .main_private_passive_prompt_shared import re
from .main_private_passive_prompt_shared import render_prompt_sections



class PrivateCompanionPluginPrivatePassivePromptPart03Mixin:
    """PrivateCompanionPluginPrivatePassivePromptPart03Mixin（从 PrivateCompanionPluginPrivatePassivePromptMixin 拆出）。"""


    def _private_passive_state_update_for_prompt(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
    ) -> tuple[str, bool, str]:
        sections, state_changed, reason = self._private_passive_state_update_prompt_sections(
            session=session,
            state=state,
            current_user=current_user,
            inbound_text=inbound_text,
            lightweight=lightweight,
        )
        return (
            "\n".join(
                render_prompt_sections(
                    [section],
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
                for section in sections
            ),
            state_changed,
            reason,
        )

    def _add_private_active_period_boundary_to_surface(
        self,
        prompt_surface: PromptSurface,
        state: dict[str, Any],
    ) -> str:
        boundary_section = self._format_active_period_boundary_prompt_section(
            state,
            public=False,
        )
        boundary = render_prompt_sections(
            [boundary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if boundary:
            prompt_surface.add(
                boundary_section,
                priority=89,
            )
        return boundary

    def _request_context_text_size(self, value: Any, *, depth: int = 0) -> int:
        if depth > 8 or value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (int, float, bool)):
            return len(str(value))
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if str(key) in {"tool_calls", "extra_content", "metadata"}:
                    continue
                total += self._request_context_text_size(item, depth=depth + 1)
            return total
        if isinstance(value, (list, tuple)):
            return sum(self._request_context_text_size(item, depth=depth + 1) for item in value)
        return len(str(value))

    def _plain_context_content_for_fast_reply(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        parts.append(text)
                    continue
                item_type = str(item.get("type") or "").lower()
                if item_type in {"text", "input_text"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif "image" in item_type:
                    parts.append("[图片]")
                elif "audio" in item_type or "voice" in item_type:
                    parts.append("[语音]")
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                if key in content:
                    return self._plain_context_content_for_fast_reply(content.get(key))
        return str(content or "").strip()

    def _trim_passive_request_context_if_needed(self, event: AstrMessageEvent, req: ProviderRequest, *, is_private_chat: bool) -> None:
        if not is_private_chat:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or len(contexts) <= 24:
            return
        approx_tokens = max(0, self._request_context_text_size(contexts) // 4)
        if approx_tokens < 50000 and len(contexts) < 120:
            return
        trimmed: list[Any] = []
        for item in contexts[-36:]:
            if not isinstance(item, dict):
                text = self._plain_context_content_for_fast_reply(item)
                if text:
                    trimmed.append({"role": "user", "content": _single_line(text, 1200)})
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant"}:
                continue
            text = self._plain_context_content_for_fast_reply(item.get("content"))
            if not text:
                continue
            trimmed.append({"role": role, "content": _single_line(text, 1200)})
        trimmed = trimmed[-24:]
        if not trimmed:
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "私聊超长上下文已启用轻量护栏: session=%s contexts=%s->%s approx_tokens=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            approx_tokens,
        )

    def _context_text_is_new_conversation_boundary(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"\s+", "", raw).lower()
        if compact in {"/new", "／new"}:
            return True
        if "switchedtonewconversation" in compact:
            return True
        if re.search(r"(已|成功)?(切换|开启|创建|新建).{0,8}(新)?会话", raw, flags=re.IGNORECASE):
            return True
        return False

    def _group_llm_reply_block_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return {}
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return {}
        item = self._group_llm_reply_block_item(group_id)
        if not bool(item.get("enabled")):
            return {}
        return item

    def _passive_no_reply_event_text(self, event: AstrMessageEvent | None, *, limit: int = 180) -> str:
        if event is None:
            return ""
        candidates = [
            getattr(event, "private_companion_group_text", ""),
            getattr(event, "message_str", ""),
        ]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            candidates.append(getattr(message_obj, "message_str", ""))
        for value in candidates:
            text = _single_line(value, limit)
            if text:
                return text
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                name = _single_line(self._component_type_name(item), 32)
                if name and name not in component_types:
                    component_types.append(name)
        except Exception:
            component_types = []
        return ",".join(component_types[:6])

    def _record_passive_no_reply(
        self,
        event: AstrMessageEvent | None,
        *,
        source: str,
        reason: str,
        detail: str = "",
        level: str = "info",
        action: str = "",
        reply_preview: str = "",
    ) -> None:
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        source_text = _single_line(source, 40) or "被动未回复"
        reason_text = _single_line(reason, 120) or "未说明原因"
        level_text = _single_line(level, 12)
        if level_text not in {"error", "warn", "info"}:
            level_text = "info"
        now = _now_ts()
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) if event is not None else ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80) if event is not None else ""
        except Exception:
            sender_id = ""
        inbound = self._passive_no_reply_event_text(event)
        detail_text = _single_line(detail, 220)
        reply_text = _single_line(reply_preview, 180)
        key = hashlib.sha1(f"{source_text}|{reason_text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        root = self.data.setdefault("passive_no_reply_records", {})
        if not isinstance(root, dict):
            root = {}
            self.data["passive_no_reply_records"] = root
        items = root.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            root["items"] = items
        target: dict[str, Any] | None = None
        for item in items:
            if isinstance(item, dict) and item.get("key") == key:
                target = item
                break
        if target is None:
            target = {
                "key": key,
                "source": source_text,
                "reason": reason_text,
                "level": level_text,
                "count": 0,
                "first_ts": now,
                "last_ts": 0,
                "samples": [],
            }
            items.append(target)
        target["source"] = source_text
        target["reason"] = reason_text
        target["level"] = level_text
        target["count"] = _safe_int(target.get("count"), 0, 0) + 1
        target["last_ts"] = now
        target["last_session"] = session
        target["last_sender_id"] = sender_id
        target["last_inbound"] = inbound
        target["last_detail"] = detail_text
        target["last_action"] = _single_line(action, 120)
        target["last_reply_preview"] = reply_text
        sample = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now),
            "session": session,
            "sender_id": sender_id,
            "inbound": inbound,
            "detail": detail_text,
            "reply_preview": reply_text,
        }
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        samples.insert(0, sample)
        del samples[5:]
        root["total"] = _safe_int(root.get("total"), 0, 0) + 1
        root["last_ts"] = now
        items.sort(key=lambda item: _safe_float(item.get("last_ts"), 0) if isinstance(item, dict) else 0, reverse=True)
        del items[80:]
        if event is not None:
            try:
                setattr(event, "_private_companion_passive_no_reply_recorded", True)
            except Exception:
                pass
        logger.info(
            "已记录被动未回复: source=%s reason=%s count=%s session=%s inbound=%s",
            source_text,
            reason_text,
            target.get("count"),
            session or "-",
            _single_line(inbound, 120),
        )
        try:
            self._schedule_data_save(sections={"passive_no_reply_records"})
        except Exception:
            pass
        self._schedule_reply_interception_forward(
            "plugin_block",
            source=source_text,
            reason=reason_text,
            source_session=session,
            inbound=inbound,
            after=reply_text,
            detail=detail_text,
        )
