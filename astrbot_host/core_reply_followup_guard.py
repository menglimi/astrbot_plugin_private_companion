"""Serialize media follow-ups and preserve per-sender order before AstrBot core.

AstrBot's ``follow_up.py`` captures any inbound event from the same sender on
an active ``unified_msg_origin`` before ``InternalAgentSubStage`` builds the
multimodal request. On hosts that reduce captured events to plain text
placeholders (for example ``[图片]``), a media message sent while the previous
turn is still running loses its attachment and never enters the vision/media
pipeline.

This guard runs at the plugin handoff right before an admitted turn enters
AstrBot's core pipeline:

* Pure-text follow-ups with no waiting/unstarted media turn ahead of them pass
  through immediately, keeping AstrBot's native in-run text follow-up intact.
* Media-bearing follow-ups (``Image``, ``Video``, ``Record``, ``File``,
  ``Forward``/``Node``/``Nodes``, media-bearing or unresolved ``Reply``, and
  pre-attached multimodal ``provider_request``) wait until active core turns
  for the same ``(umo, sender_id)`` finish and any live host runner clears, so
  the media event enters AstrBot cleanly as a full multimodal turn.
* Subsequent plain-text turns from the same sender queue behind a waiting or
  not-yet-started media turn in strict arrival order, and release once that
  media turn reaches ``on_agent_begin`` (holding AstrBot's session lock with
  its runner registered) or finishes. Different chats and different group
  senders are never blocked.

Because each AstrBot event runs in its own ``asyncio.Task``, waiting here never
blocks ``EventBus.dispatch`` or other conversations, and coexists without extra
latency when an upstream AstrBot build also skips media in ``follow_up.py``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
import time
from typing import Any


logger = logging.getLogger(__name__)

MEDIA_COMPONENT_KINDS = frozenset({
    "image",
    "video",
    "record",
    "audio",
    "file",
    "forward",
    "node",
    "nodes",
})
REPLY_COMPONENT_KINDS = frozenset({"reply"})
MEDIA_PART_TYPES = frozenset({"image_url", "audio_url", "input_audio"})
DEFAULT_WAIT_TIMEOUT_SECONDS = 180.0
DEFAULT_HOST_POLL_INTERVAL_SECONDS = 0.05
_MAX_COMPONENT_DEPTH = 6


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _component_kind(part: Any) -> str:
    kind = _attr_or_key(part, "type")
    value = getattr(kind, "value", getattr(kind, "name", kind))
    if isinstance(value, str) and value.strip():
        return value.strip().casefold()
    name = type(part).__name__
    return name.strip().casefold() if isinstance(name, str) else ""


def _nonempty_identifier(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    text = str(value).strip()
    return bool(text) and text != "0"


def event_umo(event: Any) -> str:
    """Resolve the session origin key AstrBot's follow-up registry indexes by."""
    if event is None:
        return ""
    origin = getattr(event, "unified_msg_origin", None)
    if isinstance(origin, str) and origin.strip():
        return origin.strip()
    getter = getattr(event, "get_session_id", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = getattr(event, "session_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def event_sender_id(event: Any) -> str:
    """Resolve the sender identifier AstrBot's follow-up matcher compares."""
    if event is None:
        return ""
    getter = getattr(event, "get_sender_id", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if value is not None and str(value).strip():
            return str(value).strip()
    sender = getattr(getattr(event, "message_obj", None), "sender", None)
    user_id = getattr(sender, "user_id", None)
    if user_id is not None and str(user_id).strip():
        return str(user_id).strip()
    return ""


def _event_messages(event: Any) -> tuple[Any, ...]:
    getter = getattr(event, "get_messages", None)
    if callable(getter):
        try:
            messages = getter()
            if isinstance(messages, (list, tuple)):
                return tuple(messages)
        except Exception:
            pass
    raw = getattr(getattr(event, "message_obj", None), "message", None)
    return tuple(raw) if isinstance(raw, (list, tuple)) else ()


def _provider_request_has_media(request: Any) -> bool:
    if request is None:
        return False
    if _attr_or_key(request, "image_urls") or _attr_or_key(request, "audio_urls"):
        return True
    parts = _attr_or_key(request, "extra_user_content_parts")
    if isinstance(parts, (list, tuple)):
        for part in parts:
            part_type = _attr_or_key(part, "type")
            if isinstance(part_type, str) and part_type.strip().casefold() in MEDIA_PART_TYPES:
                return True
    return False


def event_media_kinds(event: Any) -> tuple[str, ...]:
    """Return normalized media kinds that must not be collapsed into text follow-up."""
    found: list[str] = []
    seen: set[int] = set()

    def visit(items: Any, *, quoted: bool, depth: int) -> None:
        if depth > _MAX_COMPONENT_DEPTH or not isinstance(items, (list, tuple)):
            return
        for part in items:
            marker = id(part)
            if marker in seen:
                continue
            seen.add(marker)
            kind = _component_kind(part)
            if kind in MEDIA_COMPONENT_KINDS:
                normalized = "record" if kind == "audio" else kind
                found.append(f"reply_{normalized}" if quoted else normalized)
                continue
            if kind in REPLY_COMPONENT_KINDS:
                chain = _attr_or_key(part, "chain")
                if isinstance(chain, (list, tuple)) and chain:
                    visit(chain, quoted=True, depth=depth + 1)
                elif _nonempty_identifier(_attr_or_key(part, "id")):
                    # A reply-id-only payload can still carry quoted images or
                    # files resolved later by AstrBot's build_main_agent.
                    found.append("reply_unresolved")

    visit(_event_messages(event), quoted=False, depth=0)

    getter = getattr(event, "get_extra", None)
    if callable(getter):
        try:
            provider_request = getter("provider_request")
        except Exception:
            provider_request = None
        if _provider_request_has_media(provider_request):
            found.append("provider_request_media")

    return tuple(dict.fromkeys(found))


def event_requires_media_serialization(event: Any) -> bool:
    """Whether this event carries media that text-only follow-up would lose."""
    return bool(event_media_kinds(event))


def _event_stopped_or_suppressed(event: Any) -> bool:
    if event is None:
        return True
    if getattr(event, "call_llm", False):
        return True
    checker = getattr(event, "is_stopped", None)
    if callable(checker):
        try:
            if checker():
                return True
        except Exception:
            pass
    return False


def _default_active_runners() -> Mapping[str, Any] | None:
    try:
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
    except Exception:
        return None
    return _ACTIVE_AGENT_RUNNERS if isinstance(_ACTIVE_AGENT_RUNNERS, Mapping) else None


def _default_host_serializes_media() -> bool:
    """Whether AstrBot core natively defers media follow-ups in arrival order."""
    try:
        from astrbot.core.pipeline.process_stage import follow_up
    except Exception:
        return False
    return (
        callable(getattr(follow_up, "extract_plain_steering_text", None))
        and hasattr(follow_up, "_FOLLOW_UP_ORDER_STATE")
    )


def runner_can_capture_followup(
    runner: Any,
    sender_id: str,
    *,
    exclude_event: Any = None,
) -> bool:
    """Mirror AstrBot ``try_capture_follow_up`` eligibility without mutating state."""
    if runner is None or not sender_id:
        return False
    done_check = getattr(runner, "done", None)
    if callable(done_check):
        try:
            if done_check():
                return False
        except Exception:
            pass
    stop_check = getattr(runner, "_is_stop_requested", None)
    if callable(stop_check):
        try:
            if stop_check():
                return False
        except Exception:
            pass
    run_context = getattr(runner, "run_context", None)
    context = getattr(run_context, "context", None)
    runner_event = getattr(context, "event", None)
    if runner_event is None or runner_event is exclude_event:
        return False
    active_sender_id = event_sender_id(runner_event)
    if not active_sender_id or active_sender_id != sender_id:
        return False
    getter = getattr(runner_event, "get_extra", None)
    if callable(getter):
        try:
            if getter("agent_stop_requested"):
                return False
        except Exception:
            pass
    return True


@dataclass
class _ActiveTurn:
    key: tuple[str, str]
    event: Any
    owner_task: asyncio.Task[Any] | None
    is_media: bool
    started: bool = False
    finished: bool = False
    on_turn_done: Callable[[Any, asyncio.Task[Any]], Any] | None = None
    _done_callback: Callable[[asyncio.Task[Any]], Any] | None = None


@dataclass
class _QueuedWaiter:
    seq: int
    event: Any
    owner_task: asyncio.Task[Any] | None
    is_media: bool
    media_kinds: tuple[str, ...]


@dataclass
class _KeyState:
    active_turns: list[_ActiveTurn] = field(default_factory=list)
    waiters: deque[_QueuedWaiter] = field(default_factory=deque)
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class CoreReplyFollowUpGuard:
    """In-memory per-``(umo, sender_id)`` media barrier and arrival-order guard."""

    def __init__(
        self,
        *,
        wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
        host_poll_interval_seconds: float = DEFAULT_HOST_POLL_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        active_runners_getter: Callable[[], Mapping[str, Any] | None] = _default_active_runners,
        host_serializes_media: bool | Callable[[], bool] | None = None,
    ) -> None:
        if type(wait_timeout_seconds) not in (int, float) or wait_timeout_seconds <= 0:
            raise ValueError("follow-up media wait timeout must be positive")
        if type(host_poll_interval_seconds) not in (int, float) or host_poll_interval_seconds <= 0:
            raise ValueError("follow-up host poll interval must be positive")
        self._wait_timeout_seconds = float(wait_timeout_seconds)
        self._host_poll_interval_seconds = float(host_poll_interval_seconds)
        self._clock = clock
        self._active_runners_getter = active_runners_getter
        if host_serializes_media is None:
            self._host_serializes_media_getter = (
                _default_host_serializes_media
                if active_runners_getter is _default_active_runners
                else (lambda: False)
            )
        elif isinstance(host_serializes_media, bool):
            fixed = host_serializes_media
            self._host_serializes_media_getter = lambda _v=fixed: _v
        elif callable(host_serializes_media):
            self._host_serializes_media_getter = host_serializes_media
        else:
            raise TypeError("host_serializes_media must be a bool, callable, or None")
        self._keys: dict[tuple[str, str], _KeyState] = {}
        self._seq = 0
        self._closed = False

    def _host_serializes_media(self) -> bool:
        try:
            return bool(self._host_serializes_media_getter())
        except Exception:
            return False

    @staticmethod
    def _attach_done_callback(
        event: Any,
        owner_task: asyncio.Task[Any] | None,
        on_turn_done: Callable[[Any, asyncio.Task[Any]], Any] | None,
    ) -> None:
        if owner_task is None or not callable(on_turn_done):
            return

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            try:
                on_turn_done(event, done_task)
            except Exception:
                logger.warning(
                    "[CompanionV2] 核心轮次收尾回调异常",
                    exc_info=True,
                )

        owner_task.add_done_callback(_on_done)

    @property
    def closed(self) -> bool:
        return self._closed

    @staticmethod
    def _current_task() -> asyncio.Task[Any] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    @staticmethod
    def _notify(state: _KeyState) -> None:
        previous = state.wake
        state.wake = asyncio.Event()
        previous.set()

    def _get_or_create_state(self, key: tuple[str, str]) -> _KeyState:
        state = self._keys.get(key)
        if state is None:
            state = _KeyState()
            self._keys[key] = state
        return state

    def _maybe_drop_state(self, key: tuple[str, str]) -> None:
        state = self._keys.get(key)
        if state is not None and not state.active_turns and not state.waiters:
            self._keys.pop(key, None)

    def _prune_finished(self, key: tuple[str, str], state: _KeyState | None) -> bool:
        if state is None or not state.active_turns:
            return False
        kept: list[_ActiveTurn] = []
        changed = False
        for turn in state.active_turns:
            if turn.finished or (turn.owner_task is not None and turn.owner_task.done()):
                turn.finished = True
                turn.started = True
                changed = True
                continue
            kept.append(turn)
        if changed:
            state.active_turns[:] = kept
            self._notify(state)
            self._maybe_drop_state(key)
        return changed

    @staticmethod
    def _other_active_turns(
        state: _KeyState | None,
        *,
        event: Any,
        current_task: asyncio.Task[Any] | None,
    ) -> list[_ActiveTurn]:
        if state is None or not state.active_turns:
            return []
        return [
            turn
            for turn in state.active_turns
            if not turn.finished
            and turn.event is not event
            and (current_task is None or turn.owner_task is not current_task)
        ]

    def _has_live_host_runner(
        self,
        umo: str,
        sender_id: str,
        *,
        exclude_event: Any = None,
    ) -> bool:
        try:
            runners = self._active_runners_getter()
        except Exception:
            return False
        if not isinstance(runners, Mapping):
            return False
        return runner_can_capture_followup(
            runners.get(umo),
            sender_id,
            exclude_event=exclude_event,
        )

    def has_active_turn(self, event: Any) -> bool:
        """Whether another core turn is currently active for this ``(umo, sender_id)``."""
        if self._closed:
            return False
        umo = event_umo(event)
        sender_id = event_sender_id(event)
        if not umo or not sender_id:
            return False
        key = (umo, sender_id)
        state = self._keys.get(key)
        self._prune_finished(key, state)
        state = self._keys.get(key)
        other_turns = self._other_active_turns(
            state,
            event=event,
            current_task=self._current_task(),
        )
        return bool(other_turns) or self._has_live_host_runner(
            umo,
            sender_id,
            exclude_event=event,
        )

    def has_pending_media_barrier(
        self,
        event: Any,
        *,
        owner_event: Any = None,
    ) -> bool:
        """Whether a queued waiter or unstarted media turn sits ahead of ``event``."""
        if self._closed:
            return False
        umo = event_umo(event)
        sender_id = event_sender_id(event)
        if not umo or not sender_id:
            return False
        key = (umo, sender_id)
        state = self._keys.get(key)
        self._prune_finished(key, state)
        state = self._keys.get(key)
        if state is None:
            return False
        if state.waiters:
            return True
        current_task = self._current_task()
        return any(
            turn.is_media
            and not turn.started
            and not turn.finished
            and turn.event is not owner_event
            for turn in self._other_active_turns(
                state,
                event=event,
                current_task=current_task,
            )
        )

    def _register_active_turn(
        self,
        key: tuple[str, str],
        event: Any,
        owner_task: asyncio.Task[Any] | None,
        *,
        is_media: bool,
        on_turn_done: Callable[[Any, asyncio.Task[Any]], Any] | None = None,
    ) -> _ActiveTurn:
        state = self._get_or_create_state(key)
        turn = _ActiveTurn(
            key=key,
            event=event,
            owner_task=owner_task,
            is_media=is_media,
            on_turn_done=on_turn_done,
        )
        state.active_turns.append(turn)
        if owner_task is not None:
            def _on_task_done(done_task: asyncio.Task[Any]) -> None:
                self._complete_turn(turn)
                callback = turn.on_turn_done
                if callable(callback):
                    try:
                        callback(event, done_task)
                    except Exception:
                        logger.warning(
                            "[CompanionV2] 核心轮次收尾回调异常",
                            exc_info=True,
                        )

            turn._done_callback = _on_task_done
            owner_task.add_done_callback(_on_task_done)
        return turn

    def _complete_turn(self, turn: _ActiveTurn) -> bool:
        if turn.finished:
            return False
        turn.finished = True
        turn.started = True
        state = self._keys.get(turn.key)
        if state is not None:
            try:
                state.active_turns.remove(turn)
            except ValueError:
                pass
            self._notify(state)
            self._maybe_drop_state(turn.key)
        return True

    def mark_agent_begin(self, event: Any) -> bool:
        """Mark an active turn as having entered AstrBot's agent runner.

        ``on_agent_begin`` fires after ``InternalAgentSubStage`` has acquired
        ``session_lock`` and registered the active runner, so queued plain-text
        follow-ups behind a media turn can safely release without overtaking it.
        """
        if self._closed or event is None:
            return False
        umo = event_umo(event)
        sender_id = event_sender_id(event)
        current_task = self._current_task()
        candidate_keys: list[tuple[str, str]] = []
        if umo and sender_id and (umo, sender_id) in self._keys:
            candidate_keys.append((umo, sender_id))
        else:
            candidate_keys.extend(tuple(self._keys))

        marked = False
        for key in candidate_keys:
            state = self._keys.get(key)
            if state is None:
                continue
            key_changed = False
            for turn in state.active_turns:
                if turn.finished or turn.started:
                    continue
                if turn.event is event or (
                    current_task is not None and turn.owner_task is current_task
                ):
                    turn.started = True
                    key_changed = True
                    marked = True
            if key_changed:
                self._notify(state)
        return marked

    def finish_turn(self, event: Any) -> bool:
        """Explicitly release an active turn slot once the core turn ends."""
        if event is None:
            return False
        removed = False
        for key in tuple(self._keys):
            state = self._keys.get(key)
            if state is None:
                continue
            for turn in tuple(state.active_turns):
                if turn.event is event:
                    if (
                        not callable(turn.on_turn_done)
                        and turn.owner_task is not None
                        and turn._done_callback is not None
                    ):
                        try:
                            turn.owner_task.remove_done_callback(turn._done_callback)
                        except Exception:
                            pass
                    if self._complete_turn(turn):
                        removed = True
        return removed

    async def enter(
        self,
        event: Any,
        *,
        event_ref: str = "",
        channel: str = "",
        on_turn_done: Callable[[Any, asyncio.Task[Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """Serialize media follow-ups behind active turns and keep arrival order."""
        if self._closed:
            return {
                "waited": False,
                "waited_ms": 0,
                "reason": "closed",
                "media_kinds": (),
                "had_active_turn": False,
            }
        umo = event_umo(event)
        sender_id = event_sender_id(event)
        media_kinds = event_media_kinds(event)
        is_media = bool(media_kinds)
        if not umo or not sender_id:
            return {
                "waited": False,
                "waited_ms": 0,
                "reason": "unkeyed",
                "media_kinds": media_kinds,
                "had_active_turn": False,
            }

        current_task = self._current_task()
        if self._host_serializes_media():
            had_active = self._has_live_host_runner(umo, sender_id, exclude_event=event)
            self._attach_done_callback(event, current_task, on_turn_done)
            return {
                "waited": False,
                "waited_ms": 0,
                "reason": "host_serialized",
                "media_kinds": media_kinds,
                "had_active_turn": had_active,
            }

        key = (umo, sender_id)
        state = self._keys.get(key)
        self._prune_finished(key, state)
        state = self._keys.get(key)

        has_queued = bool(state is not None and state.waiters)
        other_turns = self._other_active_turns(state, event=event, current_task=current_task)
        host_live = self._has_live_host_runner(umo, sender_id, exclude_event=event)
        has_unstarted_media = any(
            turn.is_media and not turn.started and not turn.finished
            for turn in other_turns
        )
        should_wait = (
            (has_queued or bool(other_turns) or host_live)
            if is_media
            else (has_queued or has_unstarted_media)
        )

        if not should_wait:
            had_active = bool(other_turns) or host_live
            self._register_active_turn(
                key,
                event,
                current_task,
                is_media=is_media,
                on_turn_done=on_turn_done,
            )
            return {
                "waited": False,
                "waited_ms": 0,
                "reason": "immediate",
                "media_kinds": media_kinds,
                "had_active_turn": had_active,
            }

        state = self._get_or_create_state(key)
        queued_ahead = len(state.waiters)
        self._seq += 1
        waiter = _QueuedWaiter(
            seq=self._seq,
            event=event,
            owner_task=current_task,
            is_media=is_media,
            media_kinds=media_kinds,
        )
        state.waiters.append(waiter)
        if is_media:
            logger.info(
                "[CompanionV2] 媒体追发等待前序核心回复结束 event_ref=%s channel=%s "
                "media_kinds=%s active_turns=%d host_runner=%s queued_ahead=%d",
                event_ref or "unknown",
                channel or "unknown",
                ",".join(media_kinds),
                len(other_turns),
                host_live,
                queued_ahead,
            )
        else:
            logger.info(
                "[CompanionV2] 追发文本按顺序等待前序媒体轮次启动 event_ref=%s channel=%s "
                "queued_ahead=%d",
                event_ref or "unknown",
                channel or "unknown",
                queued_ahead,
            )

        started_at = self._clock()
        deadline = started_at + self._wait_timeout_seconds
        reason = "serialized"
        stopped = False
        registered = False

        try:
            while True:
                if self._closed:
                    reason = "closed"
                    break
                if _event_stopped_or_suppressed(event):
                    reason = "stopped"
                    stopped = True
                    break
                self._prune_finished(key, state)
                is_head = bool(state.waiters) and state.waiters[0] is waiter
                other_turns = self._other_active_turns(
                    state,
                    event=event,
                    current_task=current_task,
                )
                host_live = False
                if is_head:
                    if is_media:
                        host_live = self._has_live_host_runner(
                            umo,
                            sender_id,
                            exclude_event=event,
                        )
                        if not other_turns and not host_live:
                            reason = "prior_turn_finished"
                            break
                    else:
                        has_unstarted_media = any(
                            turn.is_media and not turn.started and not turn.finished
                            for turn in other_turns
                        )
                        if not has_unstarted_media:
                            reason = "prior_media_started"
                            break

                remaining = deadline - self._clock()
                if remaining <= 0:
                    reason = "timeout"
                    break

                if is_media and is_head and not other_turns and host_live:
                    step_timeout = min(remaining, self._host_poll_interval_seconds)
                else:
                    step_timeout = remaining

                wake = state.wake
                try:
                    await asyncio.wait_for(wake.wait(), timeout=step_timeout)
                except asyncio.TimeoutError:
                    continue

            if not self._closed and not stopped:
                if reason == "timeout":
                    # Do not register a timed-out turn into active_turns, so a stalled
                    # prior turn cannot cascade into repeated wait timeouts for later events.
                    self._attach_done_callback(event, current_task, on_turn_done)
                else:
                    self._register_active_turn(
                        key,
                        event,
                        current_task,
                        is_media=is_media,
                        on_turn_done=on_turn_done,
                    )
                    registered = True
        finally:
            try:
                state.waiters.remove(waiter)
            except ValueError:
                pass
            self._notify(state)
            self._maybe_drop_state(key)

        waited_ms = round(max(0.0, self._clock() - started_at) * 1000)
        if reason == "timeout":
            logger.warning(
                "[CompanionV2] 媒体追发等待前序核心回复超时，安全放行继续处理 "
                "event_ref=%s channel=%s media_kinds=%s waited_ms=%d",
                event_ref or "unknown",
                channel or "unknown",
                ",".join(media_kinds) or "text",
                waited_ms,
            )
        elif registered:
            logger.info(
                "[CompanionV2] 追发消息结束等待并进入核心管线 event_ref=%s channel=%s "
                "reason=%s media_kinds=%s waited_ms=%d",
                event_ref or "unknown",
                channel or "unknown",
                reason,
                ",".join(media_kinds) or "text",
                waited_ms,
            )

        current_state = self._keys.get(key)
        other_after = self._other_active_turns(
            current_state,
            event=event,
            current_task=current_task,
        )
        had_active_after = bool(other_after) or self._has_live_host_runner(
            umo,
            sender_id,
            exclude_event=event,
        )
        return {
            "waited": True,
            "waited_ms": waited_ms,
            "reason": reason,
            "media_kinds": media_kinds,
            "had_active_turn": had_active_after,
        }

    def close(self) -> None:
        """Unblock all waiters immediately during plugin shutdown or reload."""
        self._closed = True
        states = tuple(self._keys.values())
        self._keys.clear()
        for state in states:
            for turn in tuple(state.active_turns):
                turn.finished = True
                turn.started = True
                if turn.owner_task is not None and turn._done_callback is not None:
                    try:
                        turn.owner_task.remove_done_callback(turn._done_callback)
                    except Exception:
                        pass
            state.active_turns.clear()
            state.waiters.clear()
            self._notify(state)
