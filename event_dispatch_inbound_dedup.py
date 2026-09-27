# -*- coding: utf-8 -*-
"""EventDispatchInboundDedupMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（20 个方法 + 2 个模块级名字 + 0 个类级赋值 / 575 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from collections.abc import Mapping
from typing import Any



_FINGERPRINT_FILLER_PATTERN = re.compile(
    r"[的了吧吗呢啊呀哦噢哈呵嘿诶嗯嘛喽唷哟哇咯嚯]"
    r"|(?<![A-Za-z])[啊哦噢嗯哈嘿诶](?![A-Za-z])"
    r"|(?<![A-Za-z0-9])[了][的]?(?![A-Za-z0-9])"
    r"|[。，、！？…：；\"'【】《》（）\-–—~\s]+"
)

def _normalised_text_fingerprint(text: str, scope: str, route: str) -> str:
    """Build a fuzzy fingerprint for duplicate detection.

    Punctuation, whitespace, and common Chinese filler words are stripped
    before hashing, so ``"今天天气真好呀！"`` and ``"今天天气真好"`` produce
    the same fingerprint.
    """
    cleaned = _FINGERPRINT_FILLER_PATTERN.sub("", text).strip().lower()
    if not cleaned:
        return ""
    return hashlib.sha1(
        f"{scope}\n{route}\n{cleaned}".encode("utf-8", errors="ignore")
    ).hexdigest()


class EventDispatchInboundDedupMixin:
    """EventDispatchInboundDedupMixin（从 EventDispatchMixin 拆出）。"""


    def _is_onebot_poke_notice_event(self, event: AstrMessageEvent) -> bool:
        """Return whether *event* is an inbound OneBot poke notification.

        OneBot exposes a poke as a ``notice/notify/poke`` event while AstrBot
        also maps it to a private or group message event.  It is therefore not
        an empty chat message: dedicated poke plugins must receive it before
        companion message guards make any decision.
        """
        raw = self._event_raw_payload(event)
        return (
            str(raw.get("post_type") or "").strip().lower() == "notice"
            and str(raw.get("notice_type") or "").strip().lower() == "notify"
            and str(raw.get("sub_type") or "").strip().lower() == "poke"
        )

    def _event_is_inbound_chat_message(self, event: AstrMessageEvent) -> bool:
        """Return whether *event* represents a real inbound chat message.

        Some adapters expose ``notice``, ``request`` and outgoing
        ``message_sent`` payloads as private/group message events.  Those
        payloads must remain available to their dedicated handlers without
        entering companion authorization, profile creation or reply paths.
        """
        raw = self._event_raw_payload(event)
        message_obj = getattr(event, "message_obj", None)

        echo_checker = getattr(self, "_event_is_recent_req036_denial_echo", None)
        if callable(echo_checker):
            try:
                if echo_checker(event):
                    return False
            except Exception:
                pass

        def field(owner: Any, name: str) -> Any:
            if owner is None:
                return None
            if isinstance(owner, Mapping):
                return owner.get(name)
            try:
                value = getattr(owner, name, None)
            except Exception:
                return None
            if callable(value):
                try:
                    value = value()
                except Exception:
                    return None
            return value

        def marker_enabled(value: Any) -> bool:
            if value is True:
                return True
            if type(value) in {int, float}:
                return value == 1
            if isinstance(value, str):
                return value.strip().casefold() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "self",
                    "outbound",
                    "outgoing",
                    "sent",
                }
            return False

        # Some adapters map an outgoing delivery back to a normal ``message``
        # event and even retain the recipient as sender.  Explicit direction
        # markers are therefore authoritative and must be checked before
        # ``post_type=message`` is accepted.
        owners = (raw, event, message_obj)
        for owner in owners:
            if any(
                marker_enabled(field(owner, name))
                for name in ("is_self", "from_self", "is_outbound", "outbound", "is_sent")
            ):
                return False
            for name in ("direction", "message_direction", "event_direction", "flow"):
                direction = str(field(owner, name) or "").strip().casefold()
                if direction in {"outbound", "outgoing", "send", "sent", "sending", "egress", "output"}:
                    return False
            for name in ("status", "message_status", "delivery_status"):
                status = str(field(owner, name) or "").strip().casefold()
                if status in {"outbound", "outgoing", "send", "sent", "sending", "delivered"}:
                    return False

        def identity_text(*values: Any) -> str:
            for value in values:
                if value is None:
                    continue
                try:
                    text = str(value).strip()
                except Exception:
                    continue
                if text:
                    return text
            return ""

        sender_payload = raw.get("sender") if isinstance(raw.get("sender"), Mapping) else {}
        message_sender = field(message_obj, "sender")
        sender_id = identity_text(
            raw.get("user_id"),
            raw.get("sender_id"),
            sender_payload.get("user_id"),
            sender_payload.get("id"),
            field(message_sender, "user_id"),
            field(message_sender, "id"),
        )
        if not sender_id:
            getter = getattr(event, "get_sender_id", None)
            if callable(getter):
                try:
                    sender_id = identity_text(getter())
                except Exception:
                    sender_id = ""
        self_id = identity_text(raw.get("self_id"), field(message_obj, "self_id"))
        if not self_id:
            getter = getattr(event, "get_self_id", None)
            if callable(getter):
                try:
                    self_id = identity_text(getter())
                except Exception:
                    self_id = ""
        if sender_id and self_id and sender_id == self_id:
            return False

        post_type = str(raw.get("post_type") or "").strip().lower()
        if post_type == "message":
            return True
        if post_type in {"notice", "request", "meta_event", "message_sent", "outbound", "send", "sent"}:
            return False
        if raw:
            message_type = str(raw.get("message_type") or "").strip().lower()
            if message_type in {"private", "group"}:
                return True
            if any(key in raw for key in ("notice_type", "request_type", "meta_event_type")):
                return False
        # Non-OneBot adapters do not necessarily expose a raw post_type.  The
        # framework has already classified this event as a message, so retain
        # that classification, including image/file-only messages with no text.
        return True

    def _event_message_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            if value is not None and str(value).strip():
                return _single_line(value, 120)
        raw = self._event_raw_payload(event)
        for key in ("message_id", "id", "msg_id", "seq", "message_seq", "real_id"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                return _single_line(value, 120)
        return ""

    def _event_message_id_candidates(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        raw = self._event_raw_payload(event)
        for key in ("message_id", "msg_id", "id", "seq", "message_seq", "real_id"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                ids.append(_single_line(value, 120))
        message_obj = getattr(event, "message_obj", None)
        for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            if value is not None and str(value).strip():
                ids.append(_single_line(value, 120))
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        return unique

    def _event_is_platform_message_event(self, event: AstrMessageEvent) -> bool:
        """Return True only for events whose current id can be queried by get_msg."""
        raw = self._event_raw_payload(event)
        post_type = str(raw.get("post_type") or "").strip().lower()
        if post_type:
            return post_type in {"message", "message_sent"}
        if raw:
            message_type = str(raw.get("message_type") or "").strip().lower()
            if message_type in {"private", "group"}:
                return True
            if any(key in raw for key in ("raw_message", "message", "message_id", "msg_id")):
                return True
            return False
        components = self._event_components(event)
        if components:
            labels: list[str] = []
            for item in components:
                label = item.__class__.__name__.lower()
                if isinstance(item, dict):
                    label = str(item.get("type") or item.get("post_type") or "").strip().lower()
                labels.append(label)
            if labels and all(("poke" in label or "notice" in label) for label in labels):
                return False
            return True
        return bool(str(getattr(event, "message_str", "") or "").strip())

    def _event_scope_key(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        umo_kind = umo.casefold()
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        if ":friendmessage:" in umo_kind:
            is_private = True
        elif ":groupmessage:" in umo_kind:
            is_private = False
        if is_private:
            try:
                user_id = _single_line(event.get_sender_id(), 160)
            except Exception:
                user_id = _single_line(raw.get("user_id") or raw.get("openid"), 160)
            resolver = getattr(self, "_private_user_id_for_event", None)
            if user_id and callable(resolver):
                try:
                    user_id = _single_line(resolver(event, user_id), 160) or user_id
                except Exception:
                    pass
            return f"private:{user_id}" if user_id else (umo or "unknown")

        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return f"group:{group_id}"

        normalizer = getattr(self, "_normalize_group_identity_id", None)
        if callable(normalizer):
            for key in ("group_openid", "group_id", "group", "group_no", "group_uin"):
                group_id = normalizer(raw.get(key))
                if group_id:
                    return f"group:{group_id}"
        return umo or "unknown"

    def _event_sender_id(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        try:
            sender_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            sender_id = ""
        return sender_id or _single_line(raw.get("user_id") or raw.get("openid"), 160)

    def _event_existing_reply_result_preview(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_result", None)
        if not callable(getter):
            return ""
        try:
            result = getter()
        except Exception:
            return ""
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return ""
        parts: list[str] = []
        for comp in chain[:4]:
            text = _single_line(
                getattr(comp, "text", "")
                or getattr(comp, "message", "")
                or getattr(comp, "content", ""),
                80,
            )
            if text:
                parts.append(text)
                continue
            if comp.__class__.__name__.lower() != "plain":
                parts.append(comp.__class__.__name__)
        return " / ".join(part for part in parts if part)

    def _event_has_existing_reply_result(self, event: AstrMessageEvent) -> bool:
        return bool(self._event_existing_reply_result_preview(event))

    def _outbound_text_duplicate_candidate(self, event: AstrMessageEvent) -> dict[str, str]:
        """Build a stable key for a plain-text outbound result.

        Media-bearing chains are deliberately excluded: identical captions can
        legitimately accompany different images, records, or files.
        """
        getter = getattr(event, "get_result", None)
        if not callable(getter):
            return {}
        try:
            result = getter()
        except Exception:
            return {}
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or self._chain_has_media_component(chain):
            return {}
        component_types: list[str] = []
        route_parts: list[str] = []
        for comp in chain:
            try:
                type_name = self._component_type_name(comp)
            except Exception:
                type_name = comp.__class__.__name__.strip().lower()
            type_name = str(type_name or "").strip().lower()
            if "." in type_name:
                type_name = type_name.rsplit(".", 1)[-1]
            component_types.append(type_name)
            if type_name == "at":
                target = _single_line(
                    getattr(comp, "qq", "")
                    or getattr(comp, "user_id", "")
                    or getattr(comp, "target", ""),
                    80,
                )
                if target:
                    route_parts.append(f"at:{target}")
            elif type_name == "reply":
                target = _single_line(
                    getattr(comp, "id", "")
                    or getattr(comp, "message_id", "")
                    or getattr(comp, "reply_id", ""),
                    120,
                )
                if target:
                    route_parts.append(f"reply:{target}")
        if any(item not in {"plain", "at", "reply"} for item in component_types):
            return {}
        try:
            text = str(result.get_plain_text() or "")
        except Exception:
            text = " ".join(
                str(getattr(comp, "text", "") or "")
                for comp, type_name in zip(chain, component_types)
                if type_name == "plain"
            )
        text = re.sub(r"\s+", " ", _strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)))).strip()
        if not text:
            return {}
        scope = _single_line(self._event_scope_key(event), 160) or "unknown"
        route = "|".join(route_parts)
        # Exact SHA1 signature — catches byte-identical text.
        signature = hashlib.sha1(f"{scope}\n{route}\n{text}".encode("utf-8", errors="ignore")).hexdigest()
        # Normalised fingerprint — catches semantically-similar text after
        # stripping punctuation, whitespace, and common filler words.
        fingerprint = _normalised_text_fingerprint(text, scope, route)
        return {
            "signature": signature,
            "fingerprint": fingerprint,
            "scope": scope,
            "route": route,
            "text": _single_line(text, 500),
            "sender_id": _single_line(self._event_sender_id(event), 80),
            "self_id": _single_line(self._event_self_id(event), 80),
            "message_id": _single_line(self._event_message_id(event), 120),
        }

    @staticmethod
    def _outbound_duplicate_sources_match(candidate: dict[str, str], previous: dict[str, Any]) -> bool:
        """Trust distinct inbound IDs and use sender matching only as a fallback."""
        sender_id = _single_line(candidate.get("sender_id"), 80)
        self_id = _single_line(candidate.get("self_id"), 80)
        previous_sender = _single_line(previous.get("sender_id"), 80)
        message_id = _single_line(candidate.get("message_id"), 120)
        previous_message_id = _single_line(previous.get("message_id"), 120)
        from_self = bool(sender_id and self_id and sender_id == self_id)
        same_sender = bool(sender_id and previous_sender and sender_id == previous_sender)
        same_message = bool(message_id and previous_message_id and message_id == previous_message_id)
        if from_self or same_message:
            return True
        if message_id and previous_message_id:
            return False
        return same_sender

    @staticmethod
    def _outbound_text_guard_key(candidate: dict[str, str]) -> str:
        """Keep independent inbound turns without exposing their raw IDs in cache keys."""
        signature = _single_line(candidate.get("signature"), 80)
        message_id = _single_line(candidate.get("message_id"), 120)
        if not signature or not message_id:
            return signature
        message_digest = hashlib.sha1(
            message_id.encode("utf-8", errors="ignore")
        ).hexdigest()
        return f"{signature}:message:{message_digest}"

    def _reserve_outbound_text_candidate(self, candidate: dict[str, str], *, now: float | None = None) -> str:
        """Reserve one inbound turn and return the duplicate state when blocked."""
        signature = _single_line(candidate.get("signature"), 80)
        fingerprint = _single_line(candidate.get("fingerprint"), 80)
        if not signature:
            return ""
        now = _now_ts() if now is None else now
        cache = getattr(self, "_recent_outbound_text_guard", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_outbound_text_guard = cache
        # Prune stale entries — 300-second window (up from 120 s) to catch
        # repeated questions that arrive a few minutes apart.
        for key, value in list(cache.items()):
            ts = _safe_float(value.get("ts"), 0) if isinstance(value, dict) else 0
            if ts <= 0 or now - ts > 300.0:
                cache.pop(key, None)
        # Cap total entries so a high-frequency conversation cannot accumulate
        # an unbounded number of signatures inside the sliding window.
        max_items = 1024
        if len(cache) > max_items:
            for key in sorted(cache, key=lambda k: _safe_float(cache.get(k, {}).get("ts"), 0))[
                : len(cache) - max_items
            ]:
                cache.pop(key, None)

        # Check every exact-text entry because independent inbound message IDs
        # have separate idempotency slots under the same reply signature.
        for previous in cache.values():
            if not isinstance(previous, dict):
                continue
            if _single_line(previous.get("signature"), 80) != signature:
                continue
            if not self._outbound_duplicate_sources_match(candidate, previous):
                continue
            age = max(0.0, now - _safe_float(previous.get("ts"), 0))
            state = _single_line(previous.get("state"), 20) or "pending"
            message_id = _single_line(candidate.get("message_id"), 120)
            previous_message_id = _single_line(previous.get("message_id"), 120)
            same_inbound_message = bool(
                message_id
                and previous_message_id
                and message_id == previous_message_id
            )
            window = 120.0 if same_inbound_message else (5.0 if state == "sent" else 2.0)
            if age <= window:
                return state
        # Fall back to fuzzy fingerprint match for semantically-similar text
        # while preserving the same inbound-message ownership rules.
        if fingerprint:
            for previous in cache.values():
                if not isinstance(previous, dict):
                    continue
                if _single_line(previous.get("fingerprint"), 80) != fingerprint:
                    continue
                if not self._outbound_duplicate_sources_match(candidate, previous):
                    continue
                age = max(0.0, now - _safe_float(previous.get("ts"), 0))
                state = _single_line(previous.get("state"), 20) or "pending"
                message_id = _single_line(candidate.get("message_id"), 120)
                previous_message_id = _single_line(previous.get("message_id"), 120)
                same_inbound_message = bool(
                    message_id
                    and previous_message_id
                    and message_id == previous_message_id
                )
                window = 120.0 if same_inbound_message else (5.0 if state == "sent" else 2.0)
                if age <= window:
                    return state
        cache[self._outbound_text_guard_key(candidate)] = {
            **candidate,
            "ts": now,
            "state": "pending",
        }
        return ""

    def _confirm_outbound_text_candidate(self, candidate: dict[str, str], *, now: float | None = None) -> None:
        signature = _single_line(candidate.get("signature"), 80)
        if not signature:
            return
        cache = getattr(self, "_recent_outbound_text_guard", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_outbound_text_guard = cache
        cache[self._outbound_text_guard_key(candidate)] = {
            **candidate,
            "ts": _now_ts() if now is None else now,
            "state": "sent",
        }

    def _event_self_id(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        self_id = _single_line(raw.get("self_id"), 80)
        if self_id:
            return self_id
        try:
            return _single_line(event.get_self_id(), 80)
        except Exception:
            message_obj = getattr(event, "message_obj", None)
            return _single_line(getattr(message_obj, "self_id", ""), 80)

    def _note_inbound_activity_for_scope(self, event: AstrMessageEvent) -> None:
        raw = self._event_raw_payload(event)
        post_type = str(raw.get("post_type") or "").strip().lower()
        # ``message_sent`` is an outbound delivery acknowledgement on several
        # adapters. It must not advance the inbound activity watermark while
        # delayed reply segments are waiting to be delivered.
        if post_type in {"notice", "message_sent", "outbound", "send"}:
            return
        message_obj = getattr(event, "message_obj", None)
        for owner in (event, message_obj):
            if owner is None:
                continue
            for attr in ("is_self", "from_self", "is_outbound", "outbound", "is_sent"):
                marker = getattr(owner, attr, None)
                if isinstance(marker, bool) and marker:
                    return
        scope = self._event_scope_key(event)
        if not scope or scope == "unknown":
            return
        sender_id = self._event_sender_id(event)
        self_id = self._event_self_id(event)
        if sender_id and self_id and sender_id == self_id:
            return
        noted_at = _now_ts()
        try:
            setattr(event, "_private_companion_inbound_ts", noted_at)
        except Exception:
            pass
        generation_map = getattr(self, "_reply_turn_generation_by_scope", None)
        if not isinstance(generation_map, dict):
            generation_map = {}
            self._reply_turn_generation_by_scope = generation_map
        generation = _safe_int(generation_map.get(scope), 0, 0) + 1
        generation_map[scope] = generation
        try:
            setattr(event, "_private_companion_reply_turn_generation", generation)
        except Exception:
            pass
        activity = getattr(self, "_recent_inbound_activity_by_scope", None)
        if not isinstance(activity, dict):
            activity = {}
            self._recent_inbound_activity_by_scope = activity
        activity[scope] = {
            "ts": noted_at,
            "message_id": self._event_message_id(event),
            "sender_id": sender_id,
            "from_self": bool(sender_id and self_id and sender_id == self_id),
        }
        if len(activity) > 500:
            stale = sorted(
                activity.items(),
                key=lambda kv: _safe_float(kv[1].get("ts") if isinstance(kv[1], dict) else 0, 0),
            )
            for key, _ in stale[: len(activity) - 500]:
                activity.pop(key, None)

    def _reply_turn_generation(self, scope: str) -> int:
        """Return the latest inbound turn number for a conversation scope."""
        value = getattr(self, "_reply_turn_generation_by_scope", None)
        if not isinstance(value, dict):
            return 0
        return _safe_int(value.get(_single_line(scope, 160)), 0, 0)

    def _reply_turn_is_current(self, scope: str, generation: Any) -> bool:
        expected = _safe_int(generation, 0, 0)
        if expected <= 0:
            return True
        return self._reply_turn_generation(scope) == expected

    def _scope_has_new_inbound_activity(self, scope: str, since_ts: float, *, ignore_self: bool = True) -> bool:
        activity = getattr(self, "_recent_inbound_activity_by_scope", None)
        if not isinstance(activity, dict):
            return False
        item = activity.get(_single_line(scope, 160))
        if not isinstance(item, dict):
            return False
        if _safe_float(item.get("ts"), 0.0, 0.0) <= since_ts:
            return False
        if ignore_self and bool(item.get("from_self")):
            return False
        return True

    def _event_inbound_activity_ts(self, event: AstrMessageEvent) -> float:
        ts = _safe_float(getattr(event, "_private_companion_inbound_ts", 0), 0.0, 0.0)
        if ts > 0:
            return ts
        raw = self._event_raw_payload(event)
        raw_ts = _safe_float(raw.get("time") or raw.get("timestamp"), 0.0, 0.0)
        if raw_ts > 0:
            return raw_ts
        return _now_ts()
