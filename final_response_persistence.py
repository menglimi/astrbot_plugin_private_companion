from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import uuid
from dataclasses import dataclass, field, is_dataclass, replace
from functools import wraps
from typing import Any, Awaitable, Callable

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Record
from astrbot.core.agent.message import AssistantMessageSegment, TextPart
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from .helpers import (
    _format_history_media_marker,
    _has_history_media_marker,
    _now_ts,
    _safe_float,
    _single_line,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
)
from .llm_tool_actions import PHOTO_TOOL_SILENT_SENTINEL
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


_DELIVERY_TASK_LABELS = frozenset({"segmented_llm_remainder"})


@dataclass(slots=True)
class ConfirmedDelivery:
    """One platform-confirmed send and its optional logical segment mapping."""

    chain: list[Any]
    sent_at: float
    logical_segment_ids: tuple[int, ...] = ()
    # Positions in the planned chunk list make a single combined-forward
    # confirmation unambiguous even when several chunks share one logical ID.
    logical_segment_indices: tuple[int, ...] = ()


@dataclass(slots=True)
class DeliveryLedger:
    """One logical outbound turn, independent of how many sends it uses."""

    umo: str
    event: AstrMessageEvent | None = None
    passive: bool = False
    confirmed_chains: list[list[Any]] = field(default_factory=list)
    confirmed_deliveries: list[ConfirmedDelivery] = field(default_factory=list)
    candidate_chain: list[Any] = field(default_factory=list)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    original_send: Any = None
    original_send_streaming: Any = None
    streaming: bool = False
    context_token: contextvars.Token | None = None
    fallback_task: asyncio.Task | None = None
    final_chain_start: int | None = None
    finalized: bool = False
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    logical_plan_cursor: int = 0

    @property
    def delivered_chain(self) -> list[Any]:
        return [component for chain in self.confirmed_chains for component in chain]


_CURRENT_DELIVERY: contextvars.ContextVar[DeliveryLedger | None] = (
    contextvars.ContextVar("private_companion_final_delivery", default=None)
)


class FinalResponsePersistenceCoordinator:
    """Collect platform-confirmed content and commit it to optional sinks."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    @staticmethod
    def _event_ledger(event: AstrMessageEvent | None) -> DeliveryLedger | None:
        if event is None:
            return None
        ledger = getattr(event, "_private_companion_delivery_ledger", None)
        return ledger if isinstance(ledger, DeliveryLedger) else None

    def begin_passive(self, event: AstrMessageEvent) -> DeliveryLedger:
        ledger = self._event_ledger(event)
        if ledger is None:
            ledger = DeliveryLedger(
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                event=event,
                passive=True,
            )
            setattr(event, "_private_companion_delivery_ledger", ledger)
        if ledger.context_token is None:
            ledger.context_token = _CURRENT_DELIVERY.set(ledger)
        setattr(event, "_private_companion_persistence_managed", True)
        return ledger

    def begin_proactive(self, umo: str) -> DeliveryLedger:
        ledger = DeliveryLedger(umo=str(umo or "").strip())
        ledger.context_token = _CURRENT_DELIVERY.set(ledger)
        return ledger

    @staticmethod
    def _reset_context(ledger: DeliveryLedger) -> None:
        token = ledger.context_token
        ledger.context_token = None
        if token is None:
            return
        try:
            _CURRENT_DELIVERY.reset(token)
        except (LookupError, ValueError):
            pass

    def finish_proactive(self, ledger: DeliveryLedger, outcome: Any) -> Any:
        self._reset_context(ledger)
        if not is_dataclass(outcome):
            return outcome
        fields = getattr(outcome, "__dataclass_fields__", {})
        updates: dict[str, Any] = {}
        if "delivery_umo" in fields:
            updates["delivery_umo"] = ledger.umo
        if "delivered_chain" in fields:
            updates["delivered_chain"] = tuple(ledger.delivered_chain)
        if "delivered_text" in fields and ledger.confirmed_chains:
            delivered_text = self._delivered_text(ledger.confirmed_chains)
            if delivered_text:
                updates["delivered_text"] = delivered_text
        return replace(outcome, **updates) if updates else outcome

    def confirm(
        self,
        umo: str,
        chain: list[Any] | tuple[Any, ...],
        *,
        logical_segment_ids: tuple[int, ...] | list[int] | None = None,
    ) -> None:
        components = list(chain or [])
        if not components:
            return
        ledger = _CURRENT_DELIVERY.get()
        if ledger is None:
            return
        if ledger.umo and umo and str(umo) != ledger.umo:
            return
        self._append_confirmation(
            ledger,
            components,
            logical_segment_ids=logical_segment_ids,
        )

    def _append_confirmation(
        self,
        ledger: DeliveryLedger,
        components: list[Any],
        *,
        logical_segment_ids: tuple[int, ...] | list[int] | None = None,
    ) -> None:
        ledger.confirmed_chains.append(components)
        resolved_ids, resolved_indices = self._resolve_logical_segment_metadata(
            ledger,
            components,
            logical_segment_ids=logical_segment_ids,
        )
        ledger.confirmed_deliveries.append(
            ConfirmedDelivery(
                chain=components,
                sent_at=_now_ts(),
                logical_segment_ids=resolved_ids,
                logical_segment_indices=resolved_indices,
            )
        )
        event = ledger.event
        # A normal passive turn is finalized by the after-send hook.  Only
        # schedule the delayed fallback when propagation has already stopped;
        # otherwise every segmented chunk leaves an unnecessary task behind
        # that can outlive the turn's event loop.
        stopped = False
        if ledger.passive and event is not None:
            try:
                stopped = bool(event.is_stopped())
            except Exception:
                stopped = False
        if stopped and ledger.fallback_task is None:
            try:
                ledger.fallback_task = asyncio.create_task(
                    self._finalize_stopped_event_after_yield(ledger)
                )
            except RuntimeError:
                ledger.fallback_task = None

    async def _finalize_stopped_event_after_yield(self, ledger: DeliveryLedger) -> None:
        await asyncio.sleep(0.05)
        event = ledger.event
        if event is None or ledger.finalized:
            return
        try:
            stopped = bool(event.is_stopped())
        except Exception:
            stopped = False
        if stopped:
            await self.finalize_passive(event)

    def track_background_task(self, task: asyncio.Task | None, label: str) -> None:
        if task is None or _single_line(label, 100) not in _DELIVERY_TASK_LABELS:
            return
        ledger = _CURRENT_DELIVERY.get()
        if ledger is None or not ledger.passive:
            return
        ledger.background_tasks.add(task)
        task.add_done_callback(ledger.background_tasks.discard)

    def mark_final_response_ready(self, event: AstrMessageEvent) -> None:
        """Separate tool-step deliveries from the final assistant reply."""
        ledger = self._event_ledger(event)
        if ledger is None or ledger.final_chain_start is not None:
            return
        if (
            getattr(event, "_private_companion_official_assistant_message", None)
            is None
        ):
            return
        ledger.final_chain_start = len(ledger.confirmed_chains)

    def install_send_tracking(self, event: AstrMessageEvent) -> None:
        if not bool(getattr(event, "_private_companion_persistence_managed", False)):
            logger.info(
                "[SendTracking] _private_companion_persistence_managed not set, calling begin_passive: event=%s",
                id(event),
            )
            self.begin_passive(event)
        ledger = self._event_ledger(event) or self.begin_passive(event)
        try:
            result = event.get_result()
        except Exception:
            result = None
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if chain:
            ledger.candidate_chain = chain
            setattr(event, "_private_companion_final_outbound_chain", tuple(chain))
        if not callable(ledger.original_send):
            original_send = getattr(event, "send", None)
            if callable(original_send):
                async def tracked_send(message: Any, *args: Any, **kwargs: Any):
                    # Strip [[PC_PHOTO_SENT_NO_FOLLOWUP]] from Plain/TextPart
                    # components or from bare string messages BEFORE sending
                    # to the adapter.  In streaming mode the on_llm_response
                    # handler runs after the stream has already been dispatched,
                    # so the marker must be removed here to prevent it from
                    # reaching the chat client.
                    sent_chain = getattr(message, "chain", None)
                    if isinstance(sent_chain, (list, tuple)) and sent_chain:
                        for component in sent_chain:
                            if isinstance(component, (Plain, TextPart)):
                                text = str(getattr(component, "text", "") or "")
                                if PHOTO_TOOL_SILENT_SENTINEL in text:
                                    logger.info(
                                        "[SendTracking] tracked_send STRIPPING chain: event=%s text_len=%d",
                                        id(event),
                                        len(text),
                                    )
                                    try:
                                        component.text = text.replace(PHOTO_TOOL_SILENT_SENTINEL, "")
                                    except Exception:
                                        pass
                    elif isinstance(message, str) and PHOTO_TOOL_SILENT_SENTINEL in message:
                        logger.info(
                            "[SendTracking] tracked_send STRIPPING str: event=%s text_len=%d",
                            id(event),
                            len(message),
                        )
                        message = message.replace(PHOTO_TOOL_SILENT_SENTINEL, "")
                    send_result = await original_send(message, *args, **kwargs)
                    if send_result is not False and isinstance(sent_chain, (list, tuple)) and sent_chain:
                        self._append_confirmation(ledger, list(sent_chain))
                    return send_result

                ledger.original_send = original_send
                setattr(event, "_private_companion_original_send", original_send)
                event.send = tracked_send
                logger.info(
                    "[SendTracking] send wrapper installed: event=%s",
                    id(event),
                )

        if not callable(ledger.original_send_streaming):
            original_streaming = getattr(event, "send_streaming", None)
            if callable(original_streaming):
                async def tracked_send_streaming(
                    generator: Any,
                    *args: Any,
                    **kwargs: Any,
                ) -> Any:
                    captured: list[list[Any]] = []

                    async def capture_generator():
                        async for message in generator:
                            sent_chain = getattr(message, "chain", None)
                            if isinstance(sent_chain, (list, tuple)) and sent_chain:
                                # Strip [[PC_PHOTO_SENT_NO_FOLLOWUP]] from streamed
                                # Plain/TextPart components before they reach the
                                # adapter.  In streaming mode, on_llm_response
                                # handlers run after the stream has already been
                                # sent, so the safety net in
                                # normalize_tts_enhancement_response cannot
                                # intercept the marker.  Stripping here covers
                                # all streaming paths unconditionally.
                                for component in sent_chain:
                                    if isinstance(component, (Plain, TextPart)):
                                        text = str(getattr(component, "text", "") or "")
                                        if PHOTO_TOOL_SILENT_SENTINEL in text:
                                            logger.info(
                                                "[SendTracking] tracked_send_streaming STRIPPING chain: event=%s text_len=%d",
                                                id(event),
                                                len(text),
                                            )
                                            try:
                                                component.text = text.replace(PHOTO_TOOL_SILENT_SENTINEL, "")
                                            except Exception:
                                                pass
                                captured.append(list(sent_chain))
                            else:
                                # Also check AssistantMessageSegment.content
                                # for sentinel text (no chain attribute).
                                content = str(getattr(message, "content", "") or "")
                                if PHOTO_TOOL_SILENT_SENTINEL in content:
                                    logger.info(
                                        "[SendTracking] tracked_send_streaming STRIPPING content: event=%s text_len=%d",
                                        id(event),
                                        len(content),
                                    )
                                    try:
                                        stripped = content.replace(PHOTO_TOOL_SILENT_SENTINEL, "")
                                        message.content = stripped
                                    except Exception:
                                        pass
                                    if stripped.strip():
                                        captured.append([Plain(stripped)])
                            yield message

                    send_result = await original_streaming(
                        capture_generator(),
                        *args,
                        **kwargs,
                    )
                    if send_result is not False and captured:
                        ledger.streaming = True
                        for sent_chain in captured:
                            self._append_confirmation(ledger, sent_chain)
                    return send_result

                ledger.original_send_streaming = original_streaming
                event.send_streaming = tracked_send_streaming
                logger.info(
                    "[SendTracking] send_streaming wrapper installed: event=%s",
                    id(event),
                )

        setattr(
            event,
            "_private_companion_confirmed_send_chains",
            ledger.confirmed_chains,
        )
        setattr(event, "_private_companion_send_tracking_installed", True)

    async def finalize_passive(self, event: AstrMessageEvent) -> bool:
        ledger = self._event_ledger(event)
        if ledger is None:
            return False
        async with ledger.finalize_lock:
            if ledger.finalized:
                return True
            # A tool-calling turn streams intermediate assistant text before
            # the agent finishes. That intermediate send must not be treated
            # as the final reply: on_agent_done has not run yet, so there is
            # no official assistant message to stage, and finalising here
            # would lock the ledger before the real reply arrives. The real
            # reply's _no_save flag would then never be cleared and the core
            # would drop it from history. Wait for the final reply's own
            # after_message_sent instead. A stopped event is the exception:
            # no final reply is coming, so this send IS the reply (the
            # direct-send-and-stop path).
            if ledger.final_chain_start is None:
                try:
                    stopped = bool(event.is_stopped())
                except Exception:
                    stopped = False
                if not stopped:
                    return False
            current = asyncio.current_task()
            pending = [
                task
                for task in list(ledger.background_tasks)
                if task is not current and not task.done()
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if callable(ledger.original_send):
                event.send = ledger.original_send
            if callable(ledger.original_send_streaming):
                event.send_streaming = ledger.original_send_streaming
            setattr(event, "_private_companion_send_tracking_installed", False)
            self._reset_context(ledger)

            if (
                not callable(ledger.original_send)
                and not callable(ledger.original_send_streaming)
                and bool(getattr(event, "_has_send_oper", False))
                and ledger.candidate_chain
            ):
                if not any(
                    sent_chain == ledger.candidate_chain
                    for sent_chain in ledger.confirmed_chains
                ):
                    candidate_ids, candidate_indices = (
                        self._resolve_logical_segment_metadata(
                            ledger,
                            list(ledger.candidate_chain),
                        )
                    )
                    ledger.confirmed_chains.insert(0, list(ledger.candidate_chain))
                    ledger.confirmed_deliveries.insert(
                        0,
                        ConfirmedDelivery(
                            chain=list(ledger.candidate_chain),
                            sent_at=_now_ts(),
                            logical_segment_ids=candidate_ids,
                            logical_segment_indices=candidate_indices,
                        ),
                    )
            confirmed_chains = ledger.confirmed_chains
            if ledger.final_chain_start is not None:
                confirmed_chains = confirmed_chains[ledger.final_chain_start :]
            if not confirmed_chains:
                return False
            delivered_text = self._delivered_text(
                confirmed_chains,
                separator="" if ledger.streaming else "\n",
            )
            llm_segments = self._confirmed_llm_history_segments(
                event,
                confirmed_chains,
                confirmed_deliveries=(
                    ledger.confirmed_deliveries[ledger.final_chain_start :]
                    if ledger.final_chain_start is not None
                    else ledger.confirmed_deliveries
                ),
            )
            written = await self.owner._finalize_passive_delivered_response(
                event,
                chain=[
                    component
                    for sent_chain in confirmed_chains
                    for component in sent_chain
                ],
                fallback_text=delivered_text,
                llm_segments=llm_segments,
                force=True,
            )
            ledger.finalized = True
            return bool(written)

    def _delivered_text(
        self,
        chains: list[list[Any]],
        *,
        separator: str = "\n",
    ) -> str:
        extractor = getattr(self.owner, "_actual_text_from_delivered_chain", None)
        if not callable(extractor):
            return ""
        return separator.join(
            text
            for chain in chains
            for text in [extractor(chain)]
            if text
        ).strip()

    def _planned_segment_metadata(
        self,
        ledger: DeliveryLedger,
    ) -> tuple[tuple[str, ...], tuple[int, ...]]:
        event = ledger.event
        if event is None:
            return (), ()
        planned = getattr(event, "_private_companion_llm_planned_chunk_texts", ())
        segment_ids = getattr(event, "_private_companion_llm_planned_segment_ids", ())
        if not (
            isinstance(planned, tuple)
            and isinstance(segment_ids, tuple)
            and len(planned) == len(segment_ids)
            and len(planned) >= 2
        ):
            return (), ()
        normalized = tuple(
            sanitize_llm_segment_control_tokens(str(item or "")).strip()
            for item in planned
        )
        try:
            normalized_ids = tuple(int(item) for item in segment_ids)
        except (TypeError, ValueError):
            return (), ()
        if any(not text for text in normalized):
            return (), ()
        return normalized, normalized_ids

    def _resolve_logical_segment_metadata(
        self,
        ledger: DeliveryLedger,
        components: list[Any],
        *,
        logical_segment_ids: tuple[int, ...] | list[int] | None = None,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Associate one confirmed chain with contiguous planned chunks.

        The sender can confirm an individual chunk, several chunks in one
        merged-forward chain, or a partial prefix after a later send fails.
        Matching the actual text against the plan preserves that distinction
        without requiring adapter-specific send metadata.
        """
        explicit: tuple[int, ...] = ()
        if logical_segment_ids is not None:
            try:
                explicit = tuple(int(item) for item in logical_segment_ids)
            except (TypeError, ValueError):
                explicit = ()
        extractor = getattr(self.owner, "_actual_text_from_delivered_chain", None)
        if not callable(extractor):
            return (), ()
        try:
            actual = sanitize_llm_segment_control_tokens(extractor(components)).strip()
        except Exception:
            actual = ""
        if not actual:
            return (), ()
        planned, ids = self._planned_segment_metadata(ledger)
        if not planned:
            return (), ()

        # Normal segmented sends and merged-forward sends both appear as one
        # contiguous slice of the plan. Start at the cursor first to prevent a
        # repeated text chunk from being attributed to an earlier chunk.
        cursor = min(max(0, ledger.logical_plan_cursor), len(planned))
        starts = list(range(cursor, len(planned)))
        for start in starts:
            combined = ""
            for end in range(start, len(planned)):
                combined += planned[end]
                if combined == actual:
                    ledger.logical_plan_cursor = max(ledger.logical_plan_cursor, end + 1)
                    matched_indices = tuple(range(start, end + 1))
                    matched_ids = (
                        explicit
                        if explicit and len(explicit) == len(matched_indices)
                        else ids[start : end + 1]
                    )
                    return matched_ids, matched_indices
                if len(combined) >= len(actual):
                    break
        return explicit, ()

    def _confirmed_llm_history_segments(
        self,
        event: AstrMessageEvent,
        confirmed_chains: list[list[Any]],
        *,
        confirmed_deliveries: list[ConfirmedDelivery] | None = None,
    ) -> tuple[str, ...]:
        """Return LLM logical segments only after the full send plan is confirmed."""
        streaming_checker = getattr(self.owner, "_event_uses_streaming_result", None)
        if callable(streaming_checker) and streaming_checker(event):
            return ()
        planned = getattr(event, "_private_companion_llm_planned_chunk_texts", ())
        segment_ids = getattr(
            event,
            "_private_companion_llm_planned_segment_ids",
            (),
        )
        if not (
            isinstance(planned, tuple)
            and isinstance(segment_ids, tuple)
            and len(planned) >= 2
            and len(segment_ids) == len(planned)
            and len(set(segment_ids)) >= 2
        ):
            return ()
        extractor = getattr(self.owner, "_actual_text_from_delivered_chain", None)
        if not callable(extractor):
            return ()

        # Prefer the delivery metadata collected at confirmation time. This
        # handles merged-forward sends and partial delivery while retaining a
        # single assistant history turn. A metadata record without plan
        # positions is deliberately rejected here; the exact-text fallback
        # below is safer than guessing how a combined chain was split.
        if confirmed_deliveries:
            grouped: dict[int, list[str]] = {}
            order: list[int] = []
            metadata_valid = True
            for delivery in confirmed_deliveries:
                ids = tuple(delivery.logical_segment_ids or ())
                indices = tuple(delivery.logical_segment_indices or ())
                if not ids or len(ids) != len(indices):
                    metadata_valid = False
                    break
                delivered_text = sanitize_llm_segment_control_tokens(
                    extractor(delivery.chain)
                ).strip()
                if not delivered_text:
                    metadata_valid = False
                    break
                expected_parts = [
                    sanitize_llm_segment_control_tokens(str(planned[index] or "")).strip()
                    for index in indices
                    if 0 <= index < len(planned)
                ]
                if len(expected_parts) != len(indices) or "".join(expected_parts) != delivered_text:
                    metadata_valid = False
                    break
                for segment_id, text in zip(ids, expected_parts):
                    normalized_id = int(segment_id)
                    if normalized_id not in grouped:
                        grouped[normalized_id] = []
                        order.append(normalized_id)
                    grouped[normalized_id].append(text)
            if metadata_valid:
                cleaned = tuple(
                    segment
                    for segment_id in order
                    if (
                        segment := sanitize_llm_segment_control_tokens(
                            "".join(grouped[segment_id])
                        ).strip()
                    )
                )
                return cleaned if len(cleaned) >= 2 else ()

        delivered = tuple(
            sanitize_llm_segment_control_tokens(extractor(chain)).strip()
            for chain in confirmed_chains
        )
        expected = tuple(
            sanitize_llm_segment_control_tokens(item).strip()
            for item in planned
        )
        if delivered != expected:
            return ()
        grouped: dict[int, list[str]] = {}
        order: list[int] = []
        for segment_id, text in zip(segment_ids, delivered):
            try:
                normalized_id = int(segment_id)
            except (TypeError, ValueError):
                return ()
            if normalized_id not in grouped:
                grouped[normalized_id] = []
                order.append(normalized_id)
            grouped[normalized_id].append(text)
        cleaned = tuple(
            segment
            for segment_id in order
            if (
                segment := sanitize_llm_segment_control_tokens(
                    "".join(grouped[segment_id])
                ).strip()
            )
        )
        return cleaned if len(cleaned) >= 2 else ()


def collect_proactive_delivery(
    function: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Attach the chains confirmed by the common proactive send primitive."""

    @wraps(function)
    async def wrapped(self: Any, umo: str, *args: Any, **kwargs: Any) -> Any:
        coordinator = self._final_response_persistence_coordinator()
        ledger = coordinator.begin_proactive(umo)
        try:
            outcome = await function(self, umo, *args, **kwargs)
        except BaseException:
            coordinator._reset_context(ledger)
            raise
        return coordinator.finish_proactive(ledger, outcome)

    return wrapped


class FinalResponsePersistenceMixin:
    """Stable integration surface used by PrivateCompanion's thin hooks."""

    def _final_response_persistence_coordinator(
        self,
    ) -> FinalResponsePersistenceCoordinator:
        coordinator = getattr(self, "_final_response_persistence", None)
        if not isinstance(coordinator, FinalResponsePersistenceCoordinator):
            coordinator = FinalResponsePersistenceCoordinator(self)
            self._final_response_persistence = coordinator
        return coordinator

    def _begin_final_response_persistence(self, event: AstrMessageEvent) -> None:
        self._final_response_persistence_coordinator().begin_passive(event)
        self._capture_final_outbound_delivery(event)
        self._defer_livingmemory_response_capture(event)

    async def _prepare_final_response_after_agent(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        if not bool(getattr(event, "_private_companion_persistence_managed", False)):
            return
        try:
            self._prepare_final_response_persistence(event, run_context, response)
            self._final_response_persistence_coordinator().mark_final_response_ready(
                event
            )
        finally:
            self._restore_livingmemory_response_capture(event)

    def _capture_final_outbound_delivery(self, event: AstrMessageEvent) -> None:
        self._final_response_persistence_coordinator().install_send_tracking(event)

    async def _persist_final_outbound_delivery(self, event: AstrMessageEvent) -> bool:
        return await self._final_response_persistence_coordinator().finalize_passive(
            event
        )

    def _track_final_response_background_task(
        self,
        task: asyncio.Task | None,
        label: str,
    ) -> None:
        self._final_response_persistence_coordinator().track_background_task(
            task,
            label,
        )

    def _confirm_outbound_delivery(
        self,
        umo: str,
        chain: list[Any] | tuple[Any, ...],
    ) -> None:
        self._final_response_persistence_coordinator().confirm(umo, chain)

    @staticmethod
    def _actual_text_from_delivered_chain(
        chain: list[Any] | tuple[Any, ...],
    ) -> str:
        text_parts: list[str] = []
        voice_parts: list[str] = []
        for component in list(chain or []):
            if isinstance(component, Plain):
                text = str(getattr(component, "text", "") or "")
                if text.strip():
                    text_parts.append(text)
                continue
            if isinstance(component, Record):
                source_text = str(
                    getattr(component, "_private_companion_tts_source_text", "")
                    or getattr(component, "_private_companion_tts_spoken_text", "")
                    or ""
                ).strip()
                if source_text:
                    voice_parts.append(source_text)
        return "".join(text_parts or voice_parts).strip()

    @staticmethod
    def _is_livingmemory_handler_module(module_path: Any) -> bool:
        normalized = str(module_path or "").strip().lower().replace("-", "_")
        return "astrbot_plugin_livingmemory" in normalized

    @staticmethod
    def _is_memory_companion_handler_module(module_path: Any) -> bool:
        normalized = str(module_path or "").strip().lower().replace("-", "_")
        return any(
            plugin_id in normalized
            for plugin_id in (
                "astrbot_plugin_memory_companion",
                "astrbot_plugin_remember_you",
            )
        )

    def _livingmemory_response_handlers(
        self,
        *,
        plugins_name: list[str] | None = None,
    ) -> list[Any]:
        if not bool(getattr(self, "enable_livingmemory_integration", False)):
            return []
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMResponseEvent,
                plugins_name=plugins_name,
            )
        except Exception:
            return []
        return [
            handler
            for handler in handlers
            if self._is_livingmemory_handler_module(
                getattr(handler, "handler_module_path", "")
            )
        ]

    def _memory_companion_response_handlers(
        self,
        *,
        plugins_name: list[str] | None = None,
    ) -> list[Any]:
        if not bool(getattr(self, "enable_livingmemory_integration", False)):
            return []
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        try:
            bridge = bridge_getter() if callable(bridge_getter) else None
        except Exception:
            return []
        if not callable(getattr(bridge, "record_visible_turn", None)):
            return []
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMResponseEvent,
                plugins_name=plugins_name,
            )
        except Exception:
            return []
        return [
            handler
            for handler in handlers
            if self._is_memory_companion_handler_module(
                getattr(handler, "handler_module_path", "")
            )
        ]

    @staticmethod
    def _handler_plugin_name(handler: Any) -> str:
        plugin = star_map.get(str(getattr(handler, "handler_module_path", "") or ""))
        return str(getattr(plugin, "name", "") or "").strip()

    def _defer_livingmemory_response_capture(self, event: AstrMessageEvent) -> bool:
        """Keep recall enabled while postponing raw assistant writes."""
        if event is None or bool(
            getattr(event, "_private_companion_final_memory_dispatch", False)
        ):
            return False
        if bool(getattr(event, "_private_companion_livingmemory_deferred", False)):
            return True

        original_plugins = getattr(event, "plugins_name", None)
        livingmemory_handlers = self._livingmemory_response_handlers(
            plugins_name=original_plugins
        )
        memory_companion_handlers = self._memory_companion_response_handlers(
            plugins_name=original_plugins
        )
        livingmemory_names = {
            name
            for name in (
                self._handler_plugin_name(handler)
                for handler in livingmemory_handlers
            )
            if name
        }
        memory_companion_names = {
            name
            for name in (
                self._handler_plugin_name(handler)
                for handler in memory_companion_handlers
            )
            if name
        }
        managed_names = livingmemory_names | memory_companion_names
        if not managed_names:
            return False

        if original_plugins is None or original_plugins == ["*"]:
            allowed_plugins = sorted(
                {
                    str(getattr(plugin, "name", "") or "").strip()
                    for plugin in star_map.values()
                    if bool(getattr(plugin, "activated", False))
                    and str(getattr(plugin, "name", "") or "").strip()
                    not in managed_names
                }
            )
        else:
            allowed_plugins = [
                str(name)
                for name in list(original_plugins or [])
                if str(name) not in managed_names
            ]

        setattr(event, "_private_companion_original_plugins_name", original_plugins)
        setattr(
            event,
            "_private_companion_livingmemory_plugin_names",
            tuple(sorted(livingmemory_names)),
        )
        setattr(
            event,
            "_private_companion_memory_companion_plugin_names",
            tuple(sorted(memory_companion_names)),
        )
        setattr(event, "_private_companion_livingmemory_deferred", True)
        event.plugins_name = allowed_plugins
        return True

    @staticmethod
    def _restore_livingmemory_response_capture(event: AstrMessageEvent) -> None:
        if event is None or not bool(
            getattr(event, "_private_companion_livingmemory_deferred", False)
        ):
            return
        event.plugins_name = getattr(
            event,
            "_private_companion_original_plugins_name",
            None,
        )
        setattr(event, "_private_companion_livingmemory_deferred", False)

    @staticmethod
    def _message_content_text(message: Any) -> str:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        return "".join(
            str(getattr(part, "text", "") or "")
            for part in content
            if isinstance(part, TextPart)
        ).strip()

    @staticmethod
    def _event_uses_streaming_result(event: AstrMessageEvent) -> bool:
        try:
            result = event.get_result()
        except Exception:
            return False
        content_type = getattr(result, "result_content_type", None)
        label = str(getattr(content_type, "name", "") or content_type or "").upper()
        return "STREAMING" in label

    def _last_assistant_text(self, run_context: Any) -> str:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return ""
        for message in reversed(messages):
            if str(getattr(message, "role", "") or "") == "assistant":
                return self._message_content_text(message)
        return ""

    def _prepare_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        if event is None or not bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            return
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return
        for message in reversed(messages):
            if str(getattr(message, "role", "") or "") != "assistant":
                continue
            setattr(
                event,
                "_private_companion_raw_assistant_text",
                self._message_content_text(message),
            )
            setattr(event, "_private_companion_official_assistant_message", message)
            try:
                message._no_save = True
            except Exception:
                pass
            break

        setattr(
            event,
            "_private_companion_reviewed_assistant_text",
            str(getattr(response, "completion_text", "") or "").strip(),
        )
        try:
            request = event.get_extra("provider_request")
            conversation = getattr(request, "conversation", None)
            conversation_id = str(getattr(conversation, "cid", "") or "").strip()
            if conversation_id:
                setattr(
                    event,
                    "_private_companion_response_conversation_id",
                    conversation_id,
                )
        except Exception:
            pass

    def _delivered_assistant_text_from_chain(
        self,
        chain: list[Any] | tuple[Any, ...],
        *,
        fallback_text: str = "",
    ) -> str:
        components = list(chain or [])
        text = str(fallback_text or "").strip()
        if not text:
            text = self._actual_text_from_delivered_chain(components)
        image_count = sum(isinstance(component, Image) for component in components)
        record_count = sum(isinstance(component, Record) for component in components)
        media_marker = _format_history_media_marker(
            images=image_count,
            records=record_count,
        )
        if media_marker:
            text = f"{text}\n{media_marker}" if text else media_marker
        return text.strip()

    def _stage_delivered_assistant_for_official_history(
        self,
        *,
        event: AstrMessageEvent,
        assistant_response: str,
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        # Media markers are useful to the companion's private continuity state,
        # but AstrBot's official conversation history is rendered directly by
        # chat clients. Keep internal metadata out of that user-visible field.
        visible_response_text = _strip_outbound_control_blocks(response_text)
        message = getattr(event, "_private_companion_official_assistant_message", None)
        if (
            not visible_response_text
            and _has_history_media_marker(response_text)
            and message is not None
            and str(getattr(message, "role", "") or "") == "assistant"
            and self._message_content_text(message)
        ):
            try:
                # A late decorator may replace the visible model text with a
                # pure media chain. Preserve the original assistant text in
                # AstrBot history instead of leaking the internal media marker
                # or leaving the takeover flag stuck on the message.
                message._no_save = False
            except Exception as exc:
                logger.warning(
                    "纯媒体回复恢复 AstrBot 核心保存失败: session=%s error=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 140),
                    _single_line(exc, 160),
                )
                return False
            logger.info(
                "纯媒体回复已保留转码前正文供 AstrBot 核心保存: %s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140),
            )
            return True
        if (
            not visible_response_text
            or message is None
            or str(getattr(message, "role", "") or "") != "assistant"
        ):
            return False
        try:
            message.content = [TextPart(text=visible_response_text)]
            if hasattr(message, "tool_calls"):
                message.tool_calls = None
            if hasattr(message, "tool_call_id"):
                message.tool_call_id = None
            message._no_save = False
        except Exception as exc:
            logger.warning(
                "实际回复暂存到 AstrBot 会话上下文失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140),
                _single_line(exc, 160),
            )
            return False
        logger.info(
            "已将实际发送回复交给 AstrBot 核心保存: %s",
            _single_line(getattr(event, "unified_msg_origin", ""), 140),
        )
        return True

    async def _append_delivered_assistant_to_conversation(
        self,
        *,
        event: AstrMessageEvent,
        assistant_response: str,
    ) -> bool:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        visible_response_text = _strip_outbound_control_blocks(response_text)
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if not umo or not visible_response_text or conv_mgr is None:
            return False
        requested_cid = str(
            getattr(event, "_private_companion_response_conversation_id", "") or ""
        ).strip()

        async def write() -> bool:
            conv_id = requested_cid or str(
                await conv_mgr.get_curr_conversation_id(umo) or ""
            ).strip()
            if not conv_id:
                return False
            conversation = await conv_mgr.get_conversation(umo, conv_id)
            if conversation is None:
                return False
            raw_history = getattr(conversation, "history", "[]")
            history = (
                json.loads(raw_history or "[]")
                if isinstance(raw_history, str)
                else list(raw_history)
                if isinstance(raw_history, list)
                else []
            )
            history.append(AssistantMessageSegment(content=visible_response_text).model_dump())
            await conv_mgr.update_conversation(umo, conv_id, history=history)
            return True

        try:
            written = bool(
                await self._conversation_db_operation(
                    "append_delivered_assistant",
                    write,
                )
            )
        except Exception as exc:
            logger.warning(
                "实际回复写入 AstrBot 会话历史失败: session=%s error=%s",
                _single_line(umo, 140),
                _single_line(exc, 160),
            )
            return False
        if written:
            logger.info(
                "已将实际发送回复写入 AstrBot 会话历史: %s",
                _single_line(umo, 140),
            )
        return written

    async def _record_final_assistant_in_livingmemory(
        self,
        *,
        umo: str,
        assistant_response: str,
        delivery_id: str,
        event: AstrMessageEvent | None = None,
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        umo = str(umo or "").strip()
        if not umo or not response_text:
            return False

        handlers = self._livingmemory_response_handlers()
        if event is not None and bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            selected_names = set(
                getattr(event, "_private_companion_livingmemory_plugin_names", ()) or ()
            )
            if not selected_names:
                return False
            handlers = [
                handler
                for handler in handlers
                if self._handler_plugin_name(handler) in selected_names
            ]
        if not handlers:
            return False

        dedup_key = str(delivery_id or "").strip() or hashlib.sha1(
            f"{umo}\0{response_text}".encode("utf-8", errors="ignore")
        ).hexdigest()
        recorded = getattr(self, "_livingmemory_final_delivery_ids", None)
        if not isinstance(recorded, dict):
            recorded = {}
            self._livingmemory_final_delivery_ids = recorded
        if dedup_key in recorded:
            return True

        dispatch_event = event or self._proactive_synthetic_event(
            umo,
            prompt="",
            name=str(runtime_persona_setting(self, "bot_name", "小星") or "PrivateCompanion"),
        )
        if dispatch_event is None:
            return False
        setattr(dispatch_event, "_private_companion_final_memory_dispatch", True)
        response = LLMResponse(role="assistant", completion_text=response_text)
        delivered = False
        invoked_plugins: set[int] = set()
        for handler in handlers:
            plugin_metadata = star_map.get(
                str(getattr(handler, "handler_module_path", "") or "")
            )
            plugin_instance = getattr(plugin_metadata, "star_cls", None)
            direct_handler = getattr(plugin_instance, "handle_memory_reflection", None)
            try:
                if callable(direct_handler) and id(plugin_instance) not in invoked_plugins:
                    await direct_handler(dispatch_event, response)
                    invoked_plugins.add(id(plugin_instance))
                elif not callable(direct_handler):
                    await handler.handler(dispatch_event, response)
                else:
                    continue
                delivered = True
            except Exception as exc:
                logger.warning(
                    "LivingMemory 最终回复写入失败: session=%s handler=%s error=%s",
                    _single_line(umo, 140),
                    _single_line(getattr(handler, "handler_name", ""), 80),
                    _single_line(exc, 160),
                )
        if delivered:
            recorded[dedup_key] = _now_ts()
            if len(recorded) > 512:
                for old_key, _ in sorted(
                    recorded.items(), key=lambda item: item[1]
                )[:-384]:
                    recorded.pop(old_key, None)
            logger.info(
                "已将实际发送回复交给 LivingMemory 记录: %s",
                _single_line(umo, 140),
            )
        return delivered

    async def _memory_companion_record_confirmed_assistant_message(
        self,
        event: Any,
        *,
        content: str,
        delivery_id: str = "",
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(content)[:2000]
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not response_text or not session_id:
            return False
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_visible_turn", None) if bridge else None
        if not callable(recorder):
            return False
        try:
            private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            private_chat = False
        try:
            user_id = _single_line(event.get_sender_id(), 80)
        except Exception:
            user_id = ""
        try:
            user_name = _single_line(self._sender_display_name(event), 80)
        except Exception:
            user_name = user_id
        try:
            await recorder(
                role="assistant",
                content=response_text,
                scope="private" if private_chat else "group",
                session_id=session_id,
                platform=session_id.split(":", 1)[0] if ":" in session_id else "",
                user_id=user_id,
                user_name=user_name,
                message_id=(
                    "private_companion_delivered_"
                    f"{_single_line(delivery_id, 120) or uuid.uuid4().hex}"
                ),
                source="private_companion_confirmed_reply",
                metadata={
                    "clean_visible_text": response_text,
                    "delivery_confirmed": True,
                    "conversation_turn": "passive_reply",
                },
            )
            return True
        except Exception as exc:
            optional_failed = getattr(
                self, "_memory_companion_optional_dependency_failed", None
            )
            if callable(optional_failed) and optional_failed(
                exc, where="record_confirmed_assistant_message"
            ):
                return False
            logger.debug(
                "MemoryCompanion 实际回复写入失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _confirmed_delivery_cache_key(
        self,
        event: Any,
        delivery_id: str,
    ) -> str:
        persona_getter = getattr(self, "_active_persona_scope", None)
        try:
            persona_id = str(persona_getter() if callable(persona_getter) else "")
        except Exception:
            persona_id = ""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "\0".join((persona_id, umo, str(delivery_id or "")))

    def _claim_confirmed_delivery_locked(
        self,
        event: Any,
        delivery_id: str,
    ) -> bool:
        """Claim one delivery id while the caller holds the data lock."""
        cache = getattr(self, "_private_companion_final_delivery_ids", None)
        if not isinstance(cache, dict):
            cache = {}
            self._private_companion_final_delivery_ids = cache
        key = self._confirmed_delivery_cache_key(event, delivery_id)
        if key in cache:
            return False
        cache[key] = _now_ts()
        if len(cache) > 512:
            for stale_key, _ in sorted(cache.items(), key=lambda item: item[1])[:-384]:
                cache.pop(stale_key, None)
        return True

    def _release_confirmed_delivery_claim(
        self,
        event: Any,
        delivery_id: str,
    ) -> None:
        cache = getattr(self, "_private_companion_final_delivery_ids", None)
        if isinstance(cache, dict):
            cache.pop(self._confirmed_delivery_cache_key(event, delivery_id), None)

    def _record_confirmed_private_bot_state_locked(
        self,
        event: Any,
        *,
        response_text: str,
        now: float,
    ) -> set[str]:
        visible_text = _single_line(
            _strip_internal_message_blocks(response_text),
            500,
        )
        if not visible_text:
            return set()
        recorder = getattr(self, "_record_confirmed_bot_continuity", None)
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return set()
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            return set()
        users = (
            self.data.get("users", {})
            if isinstance(getattr(self, "data", None), dict)
            else {}
        )
        user = users.get(user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return set()

        user["last_companion_message"] = visible_text
        user["last_companion_message_at"] = now
        screen_scheduler = getattr(self, "_maybe_schedule_goodnight_screen_check", None)
        if callable(screen_scheduler):
            screen_scheduler(user, visible_text, now=now)

        updated_sections = {"users"}
        expression_rule_details = getattr(
            event,
            "private_companion_expression_rule_details",
            None,
        )
        semantic_rules = getattr(
            event,
            "private_companion_semantic_expression_rules",
            None,
        )
        expression_context = getattr(
            event,
            "private_companion_semantic_expression_context",
            None,
        )
        usage_recorder = getattr(self, "_record_expression_rule_injection", None)
        if callable(usage_recorder) and (
            isinstance(expression_rule_details, dict)
            or (isinstance(semantic_rules, list) and semantic_rules)
        ):
            usage = usage_recorder(
                user,
                expression_rule_details
                if isinstance(expression_rule_details, dict)
                else {},
                visible_text,
                semantic_rules=semantic_rules if isinstance(semantic_rules, list) else [],
                context=expression_context
                if isinstance(expression_context, dict)
                else {"channel": "private"},
            )
            if isinstance(usage, dict):
                updated_sections.update(usage.get("updated_sections") or ())

        topic_recorder = getattr(self, "_remember_passive_reply_topic", None)
        if callable(topic_recorder):
            topic_recorder(
                user,
                visible_text,
                _single_line(user.get("last_user_message"), 260),
            )
        if callable(recorder):
            recorder(user, visible_text, now=now)
        reunion_observed_at = _safe_float(
            getattr(event, "_private_companion_reunion_observed_at", 0),
            0,
        )
        if reunion_observed_at > _safe_float(user.get("last_reunion_ack_at"), 0):
            user["last_reunion_ack_at"] = reunion_observed_at
        return updated_sections

    def _record_confirmed_group_bot_state_locked(
        self,
        event: Any,
        *,
        response_text: str,
        now: float,
        delivery_id: str = "",
        llm_segments: tuple[str, ...] = (),
    ) -> set[str]:
        visible_text = _single_line(
            _strip_internal_message_blocks(
                sanitize_llm_segment_control_tokens(response_text)
            ),
            500,
        )
        if not visible_text:
            return set()
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return set()
        except Exception:
            pass
        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
        group_id = _single_line(
            group_id_getter(event) if callable(group_id_getter) else "",
            80,
        )
        if not group_id:
            return set()
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(feature_checker) and not feature_checker("enable_group_companion"):
            return set()
        group_getter = getattr(self, "_get_group", None)
        if not callable(group_getter):
            return set()
        group = group_getter(group_id)
        if not isinstance(group, dict):
            return set()

        updated_sections: set[str] = set()
        semantic_rules = getattr(
            event,
            "private_companion_semantic_expression_rules",
            None,
        )
        expression_context = getattr(
            event,
            "private_companion_semantic_expression_context",
            None,
        )
        usage_recorder = getattr(self, "_record_expression_rule_injection", None)
        if (
            callable(usage_recorder)
            and isinstance(semantic_rules, list)
            and semantic_rules
        ):
            usage = usage_recorder(
                group,
                {},
                visible_text,
                semantic_rules=semantic_rules,
                context=expression_context
                if isinstance(expression_context, dict)
                else {"channel": "group"},
            )
            if isinstance(usage, dict) and usage:
                updated_sections.update(usage.get("updated_sections") or ("groups",))
                try:
                    setattr(
                        event,
                        "private_companion_group_semantic_usage_recorded",
                        True,
                    )
                except Exception:
                    pass

        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        scene = getattr(event, "private_companion_group_scene", None)
        talking_to_bot = (
            isinstance(scene, dict) and str(scene.get("talking_to") or "") == "bot"
        )
        reply_recorder = getattr(self, "_record_group_bot_reply", None)
        if callable(reply_recorder):
            recorded = reply_recorder(
                group,
                text=visible_text,
                reply_to_id=sender_id,
                kind="passive_reply",
                talking_to_bot=talking_to_bot,
                ts=now,
                delivery_id=delivery_id,
                llm_segments=llm_segments,
            )
            if isinstance(recorded, dict):
                updated_sections.add("groups")
        active_getter = getattr(self, "_group_active_conversation", None)
        active = active_getter(group) if callable(active_getter) else {}
        if talking_to_bot or (
            isinstance(active, dict)
            and str(active.get("sender_id") or "") == str(sender_id or "")
        ):
            active["last_bot_reply"] = visible_text
            active["last_bot_reply_ts"] = now
            if talking_to_bot:
                refresher = getattr(
                    self,
                    "_refresh_group_bot_conversation_after_reply",
                    None,
                )
                if callable(refresher):
                    refresher(group, sender_id, now=now)
            updated_sections.add("groups")
        return updated_sections

    async def _record_confirmed_outbound_state(
        self,
        event: Any,
        *,
        response_text: str,
        delivery_id: str,
        llm_segments: tuple[str, ...] = (),
    ) -> tuple[bool, set[str]]:
        """Commit all local continuity for one confirmed delivery exactly once."""
        if not response_text:
            return False, set()

        def record() -> tuple[bool, set[str]]:
            if not self._claim_confirmed_delivery_locked(event, delivery_id):
                return True, set()
            try:
                now = _now_ts()
                private_sections = self._record_confirmed_private_bot_state_locked(
                    event,
                    response_text=response_text,
                    now=now,
                )
                group_sections = self._record_confirmed_group_bot_state_locked(
                    event,
                    response_text=response_text,
                    now=now,
                    delivery_id=delivery_id,
                    llm_segments=llm_segments,
                )
                sections = private_sections | group_sections
                if sections:
                    self._save_data_sync(sections=sections)
                return False, sections
            except Exception:
                self._release_confirmed_delivery_claim(event, delivery_id)
                raise

        lock = getattr(self, "_data_lock", None)
        try:
            if lock is not None and hasattr(lock, "__aenter__"):
                async with lock:
                    return record()
            else:
                return record()
        except Exception as exc:
            logger.debug(
                "Confirmed delivery state commit failed: %s",
                _single_line(exc, 120),
            )
            return False, set()

    async def _finalize_passive_delivered_response(
        self,
        event: AstrMessageEvent,
        *,
        chain: list[Any] | tuple[Any, ...] | None = None,
        fallback_text: str = "",
        llm_segments: tuple[str, ...] = (),
        force: bool = False,
    ) -> bool:
        if event is None or not bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            return False
        if bool(getattr(event, "private_companion_proactive_framework", False)) and str(
            getattr(event, "_private_companion_external_proactive_source", "") or ""
        ) != "proactive_chat":
            return False
        if bool(getattr(event, "_private_companion_delivery_persisted", False)):
            return True
        if not force and not bool(getattr(event, "_has_send_oper", False)):
            return False

        delivered_chain = list(
            chain
            if chain is not None
            else getattr(event, "_private_companion_final_outbound_chain", ())
        )
        response_text = self._delivered_assistant_text_from_chain(
            delivered_chain,
            fallback_text=fallback_text,
        )
        response_text = sanitize_llm_segment_control_tokens(response_text)
        if not response_text:
            return False

        delivery_id = str(
            getattr(event, "_private_companion_delivery_id", "")
            or self._event_message_id(event)
            or f"passive:{id(event)}"
        )
        setattr(event, "_private_companion_delivery_id", delivery_id)
        duplicate, local_sections = await self._record_confirmed_outbound_state(
            event,
            response_text=response_text,
            delivery_id=delivery_id,
            llm_segments=llm_segments,
        )
        if duplicate:
            setattr(event, "_private_companion_delivery_persisted", True)
            return True

        official_written = self._stage_delivered_assistant_for_official_history(
            event=event,
            assistant_response=response_text,
        )
        if not official_written:
            official_written = await self._append_delivered_assistant_to_conversation(
                event=event,
                assistant_response=response_text,
            )
        memory_written = await self._record_final_assistant_in_livingmemory(
            umo=str(getattr(event, "unified_msg_origin", "") or ""),
            assistant_response=response_text,
            delivery_id=delivery_id,
            event=event,
        )
        memory_companion_written = False
        if bool(
            getattr(event, "_private_companion_memory_companion_plugin_names", ())
        ):
            memory_companion_written = bool(
                await self._memory_companion_record_confirmed_assistant_message(
                    event,
                    content=response_text,
                    delivery_id=delivery_id,
                )
            )
        persisted = bool(
            local_sections
            or official_written
            or memory_written
            or memory_companion_written
        )
        setattr(event, "_private_companion_delivery_persisted", True)
        return persisted
