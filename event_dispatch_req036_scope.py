# -*- coding: utf-8 -*-
"""EventDispatchReq036ScopeMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 359 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import re
import time
from .event_dispatch_shared import logger
from .helpers import _safe_float, _single_line
from astrbot.api.event import AstrMessageEvent
from collections.abc import Mapping
from typing import Any



class EventDispatchReq036ScopeMixin:
    """EventDispatchReq036ScopeMixin（从 EventDispatchMixin 拆出）。"""


    def _event_raw_payload(self, event: AstrMessageEvent) -> dict[str, Any]:
        message_obj = getattr(event, "message_obj", None)
        for owner in (message_obj, event):
            if owner is None:
                continue
            raw = owner.get("raw_message") if isinstance(owner, Mapping) else getattr(owner, "raw_message", None)
            if isinstance(raw, Mapping):
                return dict(raw)
        # A few adapters expose the protocol payload as the message object
        # itself instead of nesting it under ``raw_message``.
        for owner in (message_obj, event):
            if isinstance(owner, Mapping) and any(
                key in owner
                for key in (
                    "post_type",
                    "message_type",
                    "notice_type",
                    "request_type",
                    "meta_event_type",
                    "is_self",
                    "from_self",
                    "is_outbound",
                    "outbound",
                    "is_sent",
                    "direction",
                    "status",
                )
            ):
                return dict(owner)
        return {}

    def _event_req036_visible_text(self, event: AstrMessageEvent) -> str:
        """Extract only visible text for the narrow unauthorized-reply echo guard."""
        raw = self._event_raw_payload(event)
        message_obj = getattr(event, "message_obj", None)
        direct = getattr(event, "message_str", "")
        if isinstance(direct, str) and direct.strip():
            return re.sub(r"\s+", " ", direct).strip()[:480]

        parts: list[str] = []

        def append_component(item: Any) -> None:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item)
                return
            if isinstance(item, Mapping):
                kind = str(item.get("type") or item.get("name") or "").strip().lower()
                data = item.get("data")
                if kind in {"text", "plain"}:
                    if isinstance(data, Mapping):
                        value = data.get("text") or data.get("content") or data.get("message")
                    else:
                        value = data
                    if value is not None and str(value).strip():
                        parts.append(str(value))
                return
            kind = str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()
            if kind in {"text", "plain"}:
                value = getattr(item, "text", "") or getattr(item, "content", "")
                if str(value).strip():
                    parts.append(str(value))

        for owner in (raw, message_obj):
            if isinstance(owner, Mapping):
                payload = owner.get("message")
                if payload in (None, "", []):
                    payload = owner.get("raw_message")
            else:
                payload = getattr(owner, "message", None) if owner is not None else None
            if isinstance(payload, (list, tuple)):
                for item in payload:
                    append_component(item)
            elif isinstance(payload, str):
                append_component(payload)
        return re.sub(r"\s+", " ", "".join(parts)).strip()[:480]

    def _event_req036_scope(self, event: AstrMessageEvent) -> str:
        base_scope = ""
        getter = getattr(self, "_event_scope_key", None)
        if callable(getter):
            try:
                base_scope = _single_line(getter(event), 200)
            except Exception:
                base_scope = ""
            if base_scope == "unknown":
                base_scope = ""
        raw = self._event_raw_payload(event)
        try:
            sender_id = _single_line(event.get_sender_id(), 120)
        except Exception:
            sender_id = _single_line(raw.get("user_id") or raw.get("openid"), 120)
        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if not base_scope:
            base_scope = origin or (f"private:{sender_id}" if sender_id else "unknown")

        identity: dict[str, str] = {}
        identity_getter = getattr(self, "_private_event_identity_context", None)
        if callable(identity_getter):
            try:
                value = identity_getter(event, sender_id)
                if isinstance(value, dict):
                    identity = {
                        key: _single_line(value.get(key), limit)
                        for key, limit in (("platform", 40), ("adapter", 120), ("bot_id", 120))
                    }
            except Exception:
                identity = {}
        platform = identity.get("platform", "")
        if not platform:
            platform_getter = getattr(self, "_platform_kind_for_event", None)
            if callable(platform_getter):
                try:
                    platform = _single_line(platform_getter(event), 40)
                except Exception:
                    platform = ""
        message_obj = getattr(event, "message_obj", None)
        adapter = identity.get("adapter", "") or _single_line(
            getattr(event, "adapter_instance_id", "")
            or getattr(message_obj, "adapter_instance_id", "")
            or raw.get("adapter_instance_id"),
            120,
        )
        if not adapter and origin:
            adapter = _single_line(origin.split(":", 1)[0], 80)
        bot_id = identity.get("bot_id", "")
        if not bot_id:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    bot_id = _single_line(self_getter(event), 120)
                except Exception:
                    bot_id = ""
        persona_id = _single_line(getattr(event, "private_companion_persona_id", ""), 96)
        if not persona_id:
            persona_getter = getattr(self, "_effective_plugin_persona_id", None)
            if callable(persona_getter):
                try:
                    persona_id = _single_line(persona_getter(), 96)
                except Exception:
                    persona_id = ""
        return "|".join(
            (
                base_scope,
                f"persona={persona_id or '-'}",
                f"platform={platform or '-'}",
                f"adapter={adapter or '-'}",
                f"bot={bot_id or '-'}",
            )
        )

    def _event_req036_has_media(self, event: AstrMessageEvent) -> bool:
        raw = self._event_raw_payload(event)
        message_obj = getattr(event, "message_obj", None)
        for owner in (raw, message_obj):
            payload = owner.get("message") if isinstance(owner, Mapping) else getattr(owner, "message", None)
            if not isinstance(payload, (list, tuple)):
                continue
            for item in payload:
                if isinstance(item, Mapping):
                    kind = str(item.get("type") or "").strip().lower()
                else:
                    kind = str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()
                if kind in {"image", "file", "video", "record", "audio", "face", "forward", "node", "nodes"}:
                    return True
        return False

    def _event_req036_component_shape(self, event: AstrMessageEvent) -> tuple[bool, bool]:
        """Return ``(has_non_text, explicitly_empty)`` for the protocol chain."""
        raw = self._event_raw_payload(event)
        message_obj = getattr(event, "message_obj", None)
        payloads: list[Any] = []
        payload_present = False

        def append_payload(owner: Any) -> None:
            nonlocal payload_present
            if isinstance(owner, Mapping):
                if "message" in owner:
                    payload_present = True
                    payloads.append(owner.get("message"))
                raw_message = owner.get("raw_message")
                if isinstance(raw_message, str):
                    payload_present = True
                    payloads.append(raw_message)
                return
            if owner is None:
                return
            missing = object()
            try:
                value = getattr(owner, "message", missing)
            except Exception:
                return
            if value is not missing:
                payload_present = True
                payloads.append(value)

        append_payload(raw)
        append_payload(message_obj)
        has_non_text = False
        has_visible_text = False
        for payload in payloads:
            if isinstance(payload, str):
                has_visible_text = has_visible_text or bool(payload.strip())
                continue
            if payload is None:
                continue
            if not isinstance(payload, (list, tuple)):
                has_non_text = True
                continue
            for item in payload:
                if isinstance(item, str):
                    has_visible_text = has_visible_text or bool(item.strip())
                    continue
                if isinstance(item, Mapping):
                    kind = str(item.get("type") or item.get("name") or "").strip().lower()
                    data = item.get("data")
                    if kind in {"text", "plain"}:
                        value = (
                            data.get("text") or data.get("content") or data.get("message")
                            if isinstance(data, Mapping)
                            else data
                        )
                        has_visible_text = has_visible_text or bool(str(value or "").strip())
                    else:
                        has_non_text = True
                    continue
                kind = str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()
                if kind in {"text", "plain"}:
                    value = getattr(item, "text", "") or getattr(item, "content", "")
                    has_visible_text = has_visible_text or bool(str(value or "").strip())
                else:
                    has_non_text = True
        return has_non_text, bool(payload_present and not has_non_text and not has_visible_text)

    def _event_req036_has_non_text_components(self, event: AstrMessageEvent) -> bool:
        return self._event_req036_component_shape(event)[0]

    def _event_req036_is_explicitly_empty(self, event: AstrMessageEvent) -> bool:
        return self._event_req036_component_shape(event)[1]

    def _remember_req036_denial_echo(self, event: AstrMessageEvent, reply: Any) -> dict[str, Any] | None:
        text = re.sub(r"\s+", " ", str(reply or "")).strip()[:480]
        if not text:
            return None
        now = time.monotonic()
        scope = self._event_req036_scope(event)
        cache = getattr(self, "_req036_denial_echo_cache", None)
        if not isinstance(cache, list):
            cache = []
            self._req036_denial_echo_cache = cache
        cache[:] = [
            item
            for item in cache
            if (
                isinstance(item, dict)
                and 0 <= now - _safe_float(item.get("ts"), 0.0) <= 10.0
            )
        ][-32:]
        entry = {"scope": scope, "text": text, "ts": now, "state": "pending", "matches": 0}
        cache.append(entry)
        return entry

    @staticmethod
    def _confirm_req036_denial_echo(entry: Any) -> None:
        if isinstance(entry, dict):
            entry["state"] = "sent"
            entry["confirmed_at"] = time.monotonic()

    def _forget_req036_denial_echo(self, entry: Any) -> None:
        cache = getattr(self, "_req036_denial_echo_cache", None)
        if isinstance(cache, list) and isinstance(entry, dict):
            try:
                cache.remove(entry)
            except ValueError:
                pass

    def _event_is_recent_req036_denial_echo(self, event: AstrMessageEvent) -> bool:
        if bool(getattr(event, "_private_companion_req036_echo", False)):
            return True
        cache = getattr(self, "_req036_denial_echo_cache", None)
        if not isinstance(cache, list) or not cache:
            return False
        now = time.monotonic()
        cache[:] = [
            item
            for item in cache
            if (
                isinstance(item, dict)
                and 0 <= now - _safe_float(item.get("ts"), 0.0) <= 10.0
                and (
                    _safe_float(item.get("matched_at"), 0.0) <= 0
                    or now - _safe_float(item.get("matched_at"), 0.0) <= 1.5
                )
            )
        ][-32:]
        if not cache:
            return False
        scope = self._event_req036_scope(event)
        text = self._event_req036_visible_text(event)
        shape_getter = getattr(self, "_event_req036_component_shape", None)
        if callable(shape_getter):
            try:
                has_non_text, explicitly_empty = shape_getter(event)
            except Exception:
                has_non_text, explicitly_empty = self._event_req036_has_media(event), False
        else:
            has_non_text, explicitly_empty = self._event_req036_has_media(event), False
        message_id = ""
        message_id_getter = getattr(self, "_event_message_id", None)
        if callable(message_id_getter):
            try:
                message_id = _single_line(message_id_getter(event), 120)
            except Exception:
                message_id = ""
        if not message_id:
            message_id = _single_line(raw_id, 120) if (raw_id := (
                self._event_raw_payload(event).get("message_id")
                or self._event_raw_payload(event).get("msg_id")
            )) else ""
        for entry in reversed(cache):
            if not isinstance(entry, dict) or entry.get("scope") != scope:
                continue
            match_mode = ""
            if text and text == entry.get("text") and not has_non_text:
                match_mode = "text"
            # A small number of adapters drop the text while echoing a plain
            # outgoing message. Only consume an explicitly empty chain in the
            # same scope during the pending reply window; every real component
            # (including cards, shares and market faces) remains inbound.
            raw = self._event_raw_payload(event)
            post_type = str(raw.get("post_type") or "").strip().lower()
            if (
                not match_mode
                and not text
                and post_type in {"", "message"}
                and explicitly_empty
                and now - _safe_float(entry.get("ts"), 0.0) <= 3.0
            ):
                match_mode = "empty"
            if not match_mode:
                continue
            matched_at = _safe_float(entry.get("matched_at"), 0.0)
            prior_message_id = _single_line(entry.get("echo_message_id"), 120)
            prior_matches = int(_safe_float(entry.get("matches"), 0.0))
            if matched_at > 0:
                same_delivery = bool(message_id and prior_message_id and message_id == prior_message_id)
                immediate_duplicate = bool(
                    (not message_id or not prior_message_id)
                    and now - matched_at <= 1.5
                    and prior_matches < 2
                )
                if not same_delivery and not immediate_duplicate:
                    continue
            entry["matched_at"] = matched_at or now
            entry["matches"] = prior_matches + 1
            if message_id and not prior_message_id:
                entry["echo_message_id"] = message_id
            logger.info(
                "已拦截未授权私聊拒绝回流: scope=%s mode=%s duplicate=%s",
                _single_line(scope, 240),
                match_mode,
                bool(matched_at),
            )
            try:
                setattr(event, "_private_companion_req036_echo", True)
            except Exception:
                pass
            return True
        return False
