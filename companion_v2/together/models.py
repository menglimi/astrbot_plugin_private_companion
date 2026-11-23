# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from companion_v2.sources.privacy import redact_text


VALID_ROOM_MODES = {"call", "watch", "work", "novel"}
_DATA_URL_PATTERN = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+", re.IGNORECASE)
_VISUAL_DIGEST_SPACE = re.compile(r"\s+")


def normalize_room_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_ROOM_MODES else "call"


@dataclass(slots=True)
class NativeRoomBinding:
    scope_key: str
    bot_id: str
    account_id: str
    persona_id: str | None
    prompt: str
    model_provider_id: str = ""
    tts_provider_id: str = ""
    tts_language: str = "zh"
    tts_timeout_seconds: int = 30
    platform: str = ""
    session_id: str = ""
    scope_kind: str = "private"


@dataclass(slots=True)
class RoomTicket:
    token: str
    mode: str
    user_id: str
    created_at: float
    expires_at: float
    native_v2: NativeRoomBinding | None = None
    room_id: str = ""
    invitation_id: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class RoomTicketStore:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = max(60, min(int(ttl_seconds or 600), 86400))
        self._tickets: dict[str, RoomTicket] = {}

    def issue(self, *, mode: str, user_id: str = "",
              native_v2: NativeRoomBinding | None = None,
              room_id: str = "", invitation_id: str = "") -> RoomTicket:
        self.prune()
        now = time.time()
        ticket = RoomTicket(
            token=secrets.token_urlsafe(32),
            mode=normalize_room_mode(mode),
            user_id=str(user_id or "").strip()[:80],
            created_at=now,
            expires_at=now + self.ttl_seconds,
            native_v2=native_v2,
            room_id=str(room_id or "").strip()[:160],
            invitation_id=str(invitation_id or "").strip()[:160],
        )
        self._tickets[ticket.token] = ticket
        return ticket

    def get(self, token: str) -> RoomTicket | None:
        self.prune()
        ticket = self._tickets.get(str(token or ""))
        if ticket is None or ticket.expired:
            return None
        return ticket

    def consume(self, token: str) -> RoomTicket | None:
        """Return and revoke a ticket in one event-loop-safe operation."""
        self.prune()
        ticket = self._tickets.pop(str(token or ""), None)
        if ticket is None or ticket.expired:
            return None
        return ticket

    def revoke(self, token: str) -> None:
        self._tickets.pop(str(token or ""), None)

    def revoke_room(self, room_id: str) -> int:
        target = str(room_id or "")
        tokens = [token for token, ticket in self._tickets.items() if ticket.room_id == target]
        for token in tokens:
            self._tickets.pop(token, None)
        return len(tokens)

    def prune(self) -> int:
        expired = [token for token, ticket in self._tickets.items() if ticket.expired]
        for token in expired:
            self._tickets.pop(token, None)
        return len(expired)


@dataclass(slots=True)
class RoomMember:
    user_id: str
    role: str
    websocket: Any = None
    resume_token: str = ""
    display_name: str = ""
    scope_key: str = ""
    call_active: bool = False
    call_camera_frame: str = ""
    call_camera_updated_at: float = 0.0
    joined_at: float = field(default_factory=time.time)


@dataclass
class RoomSession:
    room_id: str
    ticket_token: str
    mode: str
    user_id: str
    websocket: Any
    native_v2: NativeRoomBinding | None = None
    # Audience revision: moves once per admitted member, so output already in
    # flight can be re-validated against the room's current participants.
    member_revision: int = 1
    created_at: float = field(default_factory=time.time)
    history: list[dict[str, str]] = field(default_factory=list)
    media_state: dict[str, Any] = field(default_factory=dict)
    media_token: str = ""
    media_resolution_task: asyncio.Task | None = None
    watch_events: list[dict[str, Any]] = field(default_factory=list)
    watch_event_sequence: int = 0
    watch_knowledge: str = ""
    watch_knowledge_task: asyncio.Task | None = None
    watch_memory: str = ""
    watch_memory_cursor: int = 0
    watch_memory_media_time: float = 0.0
    watch_memory_refreshing: bool = False
    watch_memory_task: asyncio.Task | None = None
    last_watch_spoken_media_time: float = -1.0
    vision_error_notified: bool = False
    watch_epoch: int = 0
    watch_ending_epoch: int = -1
    watch_tts_enabled: bool = True
    call_tts_enabled: bool = True
    shared_experience_task: asyncio.Task | None = None
    shared_experience_finalized: bool = False
    shared_experience_last_fingerprint: str = ""
    shared_experience_revision: int = 0
    shared_experience_epoch: int = 0
    shared_experience_history_offset: int = 0
    shared_experience_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    integration_closed: bool = False
    generation_task: asyncio.Task | None = None
    active_utterance_id: str = ""
    resume_token: str = ""
    detach_close_task: asyncio.Task | None = None
    last_activity_notify: float = 0.0
    call_active: bool = False
    call_last_user_activity: float = 0.0
    call_last_proactive_at: float = 0.0
    call_idle_check_count: int = 0
    call_camera_frame: str = ""
    call_camera_updated_at: float = 0.0
    call_visual_events: list[str] = field(default_factory=list)
    call_visual_last_at: float = 0.0
    call_visual_last_signature: str = ""
    visual_digest_ring: list[dict[str, Any]] = field(default_factory=list)
    last_visual_digest: str = ""
    last_visual_digest_at: float = 0.0
    last_visual_digest_signature: str = ""
    call_action_token: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    client_local_time: str = ""
    client_timezone: str = ""
    client_time_updated_at: float = 0.0
    work_context: dict[str, Any] = field(default_factory=dict)
    work_context_updated_at: float = 0.0
    # 工作协同的执行状态由模型在每轮回复中维护，避免只依赖瞬时屏幕摘要。
    work_state: dict[str, Any] = field(default_factory=dict)
    work_state_updated_at: float = 0.0
    work_context_signature: str = ""
    work_last_progress_check_at: float = 0.0
    novel_state: dict[str, Any] = field(default_factory=dict)
    novel_context: str = ""
    novel_last_progress_at: float = 0.0
    novel_progress_sequence: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    conversation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    astrbot_unified_msg_origin: str = ""
    astrbot_conversation_id: str = ""
    members: dict[str, RoomMember] = field(default_factory=dict)
    imported_history_count: int = 0

    def append_turn(self, role: str, content: str, *, history_turns: int) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self.history.append({"role": role, "content": text})
        max_messages = max(4, int(history_turns or 12) * 2)
        if len(self.history) > max_messages:
            overflow = len(self.history) - max_messages
            self.history = self.history[overflow:]
            self.imported_history_count = max(0, self.imported_history_count - overflow)
            self.shared_experience_history_offset = max(0, self.shared_experience_history_offset - overflow)

    def cancel_generation(self) -> bool:
        task = self.generation_task
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def cancel_media_resolution(self) -> bool:
        task = self.media_resolution_task
        self.media_resolution_task = None
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def update_call_camera(self, frame: str) -> None:
        self.call_camera_frame = str(frame or "")
        self.call_camera_updated_at = time.monotonic() if self.call_camera_frame else 0.0

    def recent_call_camera_frame(self, *, max_age_seconds: float = 25.0) -> str:
        if not self.call_active or self.mode != "call" or not self.call_camera_frame:
            return ""
        age = time.monotonic() - float(self.call_camera_updated_at or 0.0)
        return self.call_camera_frame if age <= max(1.0, float(max_age_seconds)) else ""

    def append_call_visual_event(self, user_text: str, bot_text: str) -> None:
        """Retain one semantic trace of a user-requested camera-assisted turn."""
        user = " ".join(str(user_text or "").split())[:320]
        bot = " ".join(str(bot_text or "").split())[:600]
        if not user or not bot:
            return
        signature = f"{user}\n{bot}"
        now = time.monotonic()
        if signature == self.call_visual_last_signature and now - self.call_visual_last_at < 90:
            return
        self.call_visual_events.append(
            f"用户在通话中结合主动开启的镜头问：{user}\nBot 基于本轮实时画面回应：{bot}"
        )
        self.call_visual_events = self.call_visual_events[-8:]
        self.call_visual_last_signature = signature
        self.call_visual_last_at = now

    @staticmethod
    def _sanitize_visual_digest(text: Any, *, max_chars: int = 80) -> str:
        raw = str(text or "")
        if not raw.strip():
            return ""
        cleaned = _DATA_URL_PATTERN.sub("", raw)
        cleaned = redact_text(cleaned)
        cleaned = _VISUAL_DIGEST_SPACE.sub(" ", cleaned).strip()
        if not cleaned:
            return ""
        limit = max(16, int(max_chars or 80))
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1].rstrip() + "…"

    @staticmethod
    def _visual_signature(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return "".join(ch for ch in normalized if ch.isalnum())

    def record_visual_digest(
        self,
        digest: Any,
        *,
        mode: str = "",
        source: str = "",
        max_chars: int = 80,
        max_items: int = 16,
        min_interval_seconds: float = 12.0,
    ) -> str:
        """Store one concise, redacted visual digest in the bounded ring (never raw images)."""
        summary = self._sanitize_visual_digest(digest, max_chars=max_chars)
        if not summary:
            return ""
        now_mono = time.monotonic()
        prev_digest_at = float(self.last_visual_digest_at or 0.0)
        signature = self._visual_signature(summary)
        self.last_visual_digest = summary
        self.last_visual_digest_at = now_mono
        duplicate = (
            bool(signature)
            and signature == self.last_visual_digest_signature
            and prev_digest_at > 0.0
            and (now_mono - prev_digest_at <= max(0.0, float(min_interval_seconds)))
        )
        if duplicate and self.visual_digest_ring:
            return summary
        if self.visual_digest_ring:
            prev_summary = str(self.visual_digest_ring[-1].get("summary") or "")
            if signature and signature == self._visual_signature(prev_summary):
                self.last_visual_digest_signature = signature
                return summary
            prev_at = float(self.visual_digest_ring[-1].get("mono_at") or 0.0)
            if (min_interval_seconds > 0 and prev_at > 0
                    and now_mono - prev_at < float(min_interval_seconds)
                    and str(self.visual_digest_ring[-1].get("source") or "") == str(source or "")[:40]
                    and source == "watch_observation"):
                # Rate-limit bursty passive watch observations while keeping latest summary.
                self.visual_digest_ring[-1]["summary"] = summary
                self.visual_digest_ring[-1]["at"] = time.time()
                self.last_visual_digest_signature = signature
                return summary
        self.visual_digest_ring.append({
            "at": time.time(),
            "mono_at": now_mono,
            "mode": normalize_room_mode(mode or self.mode),
            "source": str(source or "frame")[:40],
            "summary": summary,
        })
        if len(self.visual_digest_ring) > max(4, int(max_items or 16)):
            self.visual_digest_ring = self.visual_digest_ring[-max(4, int(max_items or 16)):]
        self.last_visual_digest_signature = signature
        return summary

    def recent_visual_digest(self, *, max_age_seconds: float = 120.0) -> str:
        if not self.last_visual_digest or not self.last_visual_digest_at:
            return ""
        if time.monotonic() - float(self.last_visual_digest_at) > max(1.0, float(max_age_seconds)):
            return ""
        return self.last_visual_digest

    def append_watch_event(
        self,
        kind: str,
        text: str,
        *,
        media_time: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        content = str(text or "").strip()
        if not content:
            return None
        self.watch_event_sequence += 1
        event = {
            "seq": self.watch_event_sequence,
            "kind": str(kind or "event")[:40],
            "text": content[:600],
            "media_time": max(0.0, float(media_time or 0.0)),
            "created_at": time.time(),
            "metadata": dict(metadata or {}),
        }
        self.watch_events.append(event)
        if len(self.watch_events) > 140:
            self.watch_events = self.watch_events[-140:]
        return event

    def reset_watch(self, *, media_token: str = "") -> None:
        self.cancel_watch_knowledge()
        self.cancel_watch_memory()
        self.media_token = str(media_token or "")
        self.watch_events.clear()
        self.watch_event_sequence = 0
        self.watch_knowledge = ""
        self.watch_memory = ""
        self.watch_memory_cursor = 0
        self.watch_memory_media_time = 0.0
        self.last_watch_spoken_media_time = -1.0
        self.vision_error_notified = False
        self.visual_digest_ring.clear()
        self.last_visual_digest = ""
        self.last_visual_digest_at = 0.0
        self.last_visual_digest_signature = ""
        self.watch_epoch += 1
        self.shared_experience_finalized = False
        self.shared_experience_last_fingerprint = ""
        self.shared_experience_revision = 0
        self.shared_experience_epoch = self.watch_epoch
        self.shared_experience_history_offset = len(self.history)

    def cancel_watch_knowledge(self) -> bool:
        task = self.watch_knowledge_task
        self.watch_knowledge_task = None
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def cancel_watch_memory(self) -> bool:
        task = self.watch_memory_task
        self.watch_memory_task = None
        self.watch_memory_refreshing = False
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True
