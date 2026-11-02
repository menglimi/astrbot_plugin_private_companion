"""Per-target private-text debounce with latest-event ownership.

Each eligible event is registered synchronously at intake. The latest event
owns the eventual core reply, so its native event ID and authorization remain
aligned with AstrBot's current event sequence. This module has no SDK calls or
storage writes; the caller decides whether a message is eligible.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Any

from companion_v2.conversation import debounce_log
from companion_v2.conversation.private_debounce import Policy, merge_texts, quiet_seconds


@dataclass
class Pending:
    key: str
    owner: Any
    entries: list[tuple[Any, str, Any]]
    tickets: list[int]
    owner_ticket: int
    created: float
    latest_received: float
    policy: Policy | None = None
    state: str = "collecting"
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    version: int = 0
    wake_logged: bool = False
    followups_logged: int = 0

    @property
    def latest_text(self):
        return self.entries[-1][1] if self.entries else ""

    @property
    def latest_ticket(self):
        return max(self.tickets, default=-1)


@dataclass(frozen=True)
class Reservation:
    pending: Pending
    ticket: int

    @property
    def key(self):
        return self.pending.key


class PrivateDebounce:
    """Shared per-target debounce state for one running plugin instance."""

    def __init__(self, policy=None, *, clock=time.monotonic):
        self.policy = policy
        self._clock = clock
        self._pending: dict[str, Pending] = {}
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._closed = False

    @property
    def closed(self):
        return self._closed

    def _timing(self, pending):
        policy = pending.policy
        if policy is None:
            return ""
        quiet = quiet_seconds(pending.latest_text, policy) * 1000
        return debounce_log.timing(
            quiet_ms=quiet,
            min_ms=policy.min_seconds * 1000,
            max_ms=policy.max_seconds * 1000,
            total_ms=policy.total_seconds * 1000,
        )

    def _ensure_wake_logged(self, pending):
        if pending.wake_logged or not pending.entries:
            return
        first_text = pending.entries[0][1]
        pending.wake_logged = True
        debounce_log.wake(
            "private", "private_message", first_text,
            target=pending.key, timing_text=self._timing(pending))
        self._log_followups(pending)

    def _log_followups(self, pending):
        if not pending.wake_logged:
            return
        while pending.followups_logged < len(pending.entries) - 1:
            index = pending.followups_logged + 1
            text = pending.entries[index][1]
            pending.followups_logged = index
            debounce_log.followup(
                "private", text, target=pending.key, count=index + 1,
                elapsed_ms=max(0.0, (self._clock() - pending.created) * 1000),
                timing_text=self._timing(pending),
            )

    def reserve(self, key, event, text, *, received_at=None):
        """Register an event before the handler's first await.

        AstrBot's event loop runs this synchronous method atomically, preserving
        intake order even if later context capture has different latency.
        """
        if self._closed or not isinstance(key, str) or not key:
            return None
        if self.policy is not None and not self.policy.enabled:
            return None
        self._sequence += 1
        ticket = self._sequence
        received = self._clock() if received_at is None else received_at
        pending = self._pending.get(key)
        created_at = getattr(event, "created_at", None)
        if pending is None or pending.state in {"finished", "aborted", "closed", "bypass"}:
            policy = self.policy
            pending = Pending(
                key=key,
                owner=event,
                entries=[(event, text, created_at)],
                tickets=[ticket],
                owner_ticket=ticket,
                created=received,
                latest_received=received,
                policy=policy,
                state="active" if policy is not None else "collecting",
            )
            self._pending[key] = pending
            return Reservation(pending, ticket)

        pending.entries.append((event, text, created_at))
        pending.tickets.append(ticket)
        pending.created = min(pending.created, received)
        pending.version += 1
        pending.owner = event
        pending.owner_ticket = ticket
        pending.latest_received = received
        pending.wake.set()
        if pending.state == "active":
            self._log_followups(pending)
        return Reservation(pending, ticket)

    @staticmethod
    def private_plain_text(event):
        """Return text only when the complete native private message is Plain."""
        message_obj = getattr(event, "message_obj", None)
        kind = getattr(getattr(message_obj, "type", None), "value",
                       getattr(message_obj, "type", None))
        if kind != "FriendMessage":
            return None
        try:
            parts = tuple(message_obj.message)
        except (AttributeError, TypeError):
            return None
        if not parts:
            return None
        texts = []
        for part in parts:
            kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            text = getattr(part, "text", None)
            if kind != "Plain" or not isinstance(text, str):
                return None
            texts.append(text)
        text = "".join(texts)
        return text if text.strip() else None

    @staticmethod
    def replace_private_plain_text(event, text):
        """Replace only the body while retaining the latest native event identity."""
        message_obj = getattr(event, "message_obj", None)
        try:
            parts = tuple(message_obj.message)
        except (AttributeError, TypeError):
            return False
        if not parts or any(
                getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) != "Plain"
                or not isinstance(getattr(part, "text", None), str) for part in parts):
            return False
        try:
            parts[0].text = text
            message_obj.message = [parts[0]]
            message_obj.message_str = text
            event.message_str = text
        except (AttributeError, TypeError):
            return False
        return True

    async def join(self, key, event, text, policy=None, *, received_at=None):
        """Compatibility wrapper for direct users of the state machine."""
        if policy is not None and self.policy is None:
            self.policy = policy
        return self.reserve(key, event, text, received_at=received_at)

    async def configure(self, reservation, event, policy):
        """Install the first available policy, or bypass an unconfigured turn."""
        pending = reservation.pending
        async with self._lock:
            if self._closed or pending.state == "closed":
                return "closed"
            if pending.owner_ticket != reservation.ticket or pending.owner is not event:
                return "configuring" if pending.state == "collecting" else "superseded"
            if pending.state == "aborted":
                return "aborted"
            if pending.state == "bypass":
                return "bypass"
            if pending.state == "finished" or self._pending.get(pending.key) is not pending:
                return "finished"
            if policy is None or not policy.enabled:
                # Once one message has entered an active burst, retain its
                # window policy so already-suppressed inputs still get a reply.
                if pending.state == "active":
                    return "active"
                pending.state = "bypass"
                self._pending.pop(pending.key, None)
                pending.wake.set()
                return "bypass"
            if pending.state == "active":
                if pending.policy != policy:
                    pending.policy = policy
                    pending.version += 1
                    pending.wake.set()
                self._ensure_wake_logged(pending)
                return "active"
            pending.policy = policy
            pending.state = "active"
            pending.version += 1
            pending.wake.set()
            self._ensure_wake_logged(pending)
            return "active"

    async def wait_for_resolution(self, reservation, event):
        """Wait until the latest event installs a policy or releases the burst."""
        pending = reservation.pending
        while True:
            async with self._lock:
                if self._closed or pending.state == "closed":
                    return "closed"
                if pending.state == "collecting":
                    version = pending.version
                    pending.wake.clear()
                    if pending.version != version:
                        continue
                elif pending.state == "active":
                    return "active" if (pending.owner_ticket == reservation.ticket
                                         and pending.owner is event) else "superseded"
                else:
                    return pending.state
            try:
                await pending.wake.wait()
            except asyncio.CancelledError:
                raise

    async def wait_for_quiet(self, reservation, event):
        """Wait until this event is still latest after its quiet window."""
        pending = reservation.pending
        while True:
            async with self._lock:
                if self._closed or pending.state == "closed":
                    return "closed"
                if pending.state != "active":
                    return pending.state
                if self._pending.get(pending.key) is not pending:
                    return pending.state
                if pending.owner_ticket != reservation.ticket or pending.owner is not event:
                    return "superseded"
                version = pending.version
                policy = pending.policy
                if policy is None:
                    return "bypass"
                quiet_deadline = pending.latest_received + quiet_seconds(pending.latest_text, policy)
                total_deadline = pending.created + policy.total_seconds
                timeout = min(quiet_deadline, total_deadline) - self._clock()
                if timeout <= 0:
                    return "ready"
                pending.wake.clear()
                if pending.version != version:
                    continue
            try:
                await asyncio.wait_for(pending.wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

    async def finish(self, reservation, event):
        """Atomically close the turn only for its latest event."""
        pending = reservation.pending
        key = reservation.key
        async with self._lock:
            if (self._pending.get(key) is not pending or pending.state != "active"
                    or pending.owner_ticket != reservation.ticket
                    or pending.owner is not event or self._closed):
                return None
            self._pending.pop(key, None)
            pending.state = "finished"
            ordered = sorted(zip(pending.tickets, pending.entries), key=lambda item: item[0])
            entries = tuple(entry for _, entry in ordered)
            if pending.wake_logged:
                debounce_log.settled(
                    "private", target=pending.key, inputs=len(entries),
                    followups=max(0, len(entries) - 1),
                    waited_ms=max(0.0, (self._clock() - pending.created) * 1000),
                    timing_text=self._timing(pending),
                )
            return entries

    async def abort(self, reservation, *, event):
        pending = reservation.pending
        key = reservation.key
        async with self._lock:
            if (self._pending.get(key) is not pending
                    or pending.owner_ticket != reservation.ticket or pending.owner is not event):
                return False
            self._pending.pop(key, None)
            if pending.state not in {"finished", "closed"}:
                pending.state = "aborted"
                if pending.wake_logged:
                    debounce_log.cancelled("private", target=pending.key, reason="aborted")
            pending.wake.set()
            return True

    @staticmethod
    def superseded_by_same_event(reservation, event):
        """Whether a stale handler aliases the event object owned by a newer ticket."""
        pending = reservation.pending
        return pending.owner is event and pending.owner_ticket != reservation.ticket

    @staticmethod
    def combined(reservation, entries=None):
        pending = reservation.pending
        items = pending.entries if entries is None else entries
        return merge_texts(item[1] for item in items)

    async def close(self):
        async with self._lock:
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            for item in pending:
                item.state = "closed"
                item.wake.set()


class PrivateDeliveryDedup:
    """Suppress a repeated delivery of the same native message id.

    AstrBot can emit one platform message more than once (adapter reconnect,
    duplicate acknowledgement, or a file upload event arriving beside the
    message event).  The private text debounce is not a substitute: media and
    file messages do not enter it, and the duplicate can arrive while the first
    reply is still running.  Keys are scoped per target and expire quickly, so
    a deliberate re-send later still reaches the model.
    """

    def __init__(self, ttl_seconds=120.0, limit=512, *, clock=time.monotonic):
        if type(ttl_seconds) not in (int, float) or ttl_seconds <= 0:
            raise ValueError("dedup window must be positive")
        if type(limit) is not int or limit <= 0:
            raise ValueError("dedup limit must be a positive integer")
        self.ttl_seconds = float(ttl_seconds)
        self.limit = limit
        self._clock = clock
        self._seen: dict[tuple[str, str], float] = {}

    def duplicate(self, key: tuple[str, str]) -> bool:
        """Record one delivery and report whether it is already in the window."""
        now = self._clock()
        previous = self._seen.get(key)
        if previous is not None and now - previous < self.ttl_seconds:
            return True
        if len(self._seen) >= self.limit:
            self._prune(now)
            if len(self._seen) >= self.limit:
                oldest = min(self._seen, key=self._seen.get)
                self._seen.pop(oldest, None)
        self._seen[key] = now
        return False

    def _prune(self, now: float) -> None:
        expired = [key for key, at in self._seen.items() if now - at >= self.ttl_seconds]
        for key in expired:
            del self._seen[key]
