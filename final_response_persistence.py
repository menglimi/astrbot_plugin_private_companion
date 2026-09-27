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

from .final_response_persistence_shared import (
    _CURRENT_DELIVERY,
    _DELIVERY_TASK_LABELS,
    collect_proactive_delivery,
    logger,
)
from .final_response_persistence_shared import logger
from .final_response_persistence_part03 import FinalResponsePersistencePart03Mixin
from .final_response_persistence_part02 import FinalResponsePersistencePart02Mixin
from .final_response_persistence_part01 import FinalResponsePersistencePart01Mixin
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
    photo_tool_chain_start: int | None = None
    finalized: bool = False
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    logical_plan_cursor: int = 0

    @property
    def delivered_chain(self) -> list[Any]:
        return [component for chain in self.confirmed_chains for component in chain]




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
        task_label = _single_line(label, 100)
        if task is None or task_label not in _DELIVERY_TASK_LABELS:
            return
        ledger = _CURRENT_DELIVERY.get()
        if ledger is None or not ledger.passive:
            return
        if task_label == "photo_tool_delivery" and ledger.photo_tool_chain_start is None:
            # The photo tool owns this visible media reply. Preserve its first
            # chain even when its acknowledgement beats the silent final reply.
            ledger.photo_tool_chain_start = len(ledger.confirmed_chains)
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
        chain_start = len(ledger.confirmed_chains)
        if ledger.photo_tool_chain_start is not None:
            chain_start = min(chain_start, ledger.photo_tool_chain_start)
        ledger.final_chain_start = chain_start

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
                suppressed = bool(getattr(event, "_private_companion_outbound_suppressed", False))
                if not (stopped or suppressed):
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

class FinalResponsePersistenceMixin(FinalResponsePersistencePart01Mixin, FinalResponsePersistencePart02Mixin, FinalResponsePersistencePart03Mixin):
    """Stable integration surface used by PrivateCompanion's thin hooks."""
