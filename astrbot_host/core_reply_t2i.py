"""Deferred AstrBot text-to-image (t2i) rendering for segmented and voiced core replies."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import time
from typing import Any

from astrbot.api import logger
from astrbot.core.message.components import Image, Node, Plain
from astrbot.core.message.message_event_result import MessageChain

from .core_reply import CORE_REPLY_CONTEXT_EXTRA


T2I_SEGMENTATION_MODES = frozenset(("per_segment", "whole_reply", "off"))
DEFAULT_T2I_SEGMENTATION_MODE = "per_segment"
MIN_T2I_WORD_THRESHOLD = 50
DEFAULT_T2I_WORD_THRESHOLD = 150


def resolve_t2i_segmentation_mode(prepared: Any) -> str:
    """Resolve the configured deferred T2I mode for segmented/voiced replies."""
    if not isinstance(prepared, Mapping):
        return DEFAULT_T2I_SEGMENTATION_MODE
    candidate = prepared.get("t2i_segmentation_mode")
    if candidate not in T2I_SEGMENTATION_MODES:
        seg = prepared.get("segmentation_settings")
        if isinstance(seg, Mapping):
            candidate = seg.get("t2i_segmentation_mode")
    return candidate if candidate in T2I_SEGMENTATION_MODES else DEFAULT_T2I_SEGMENTATION_MODE


def _parse_threshold(config: Mapping | None) -> int:
    raw = config.get("t2i_word_threshold", DEFAULT_T2I_WORD_THRESHOLD) if isinstance(config, Mapping) else DEFAULT_T2I_WORD_THRESHOLD
    try:
        return max(int(raw), MIN_T2I_WORD_THRESHOLD)
    except (TypeError, ValueError):
        return DEFAULT_T2I_WORD_THRESHOLD


def _event_ref(core: Mapping | None) -> str:
    prepared = core.get("prepared") if isinstance(core, Mapping) else None
    if isinstance(prepared, Mapping):
        value = prepared.get("event_ref")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def capture_deferred_core_t2i(event: Any, result: Any, *, astrbot_config: Mapping | None = None) -> dict[str, Any] | None:
    """Capture host T2I intent before suppressing early ResultDecorateStage rendering."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return None
    explicit_flag = getattr(result, "use_t2i_", None)
    if explicit_flag is False:
        core.pop("_deferred_t2i", None)
        return None
    config_t2i = bool(astrbot_config.get("t2i")) if isinstance(astrbot_config, Mapping) else False
    if explicit_flag is not True and not config_t2i:
        core.pop("_deferred_t2i", None)
        return None
    mode = resolve_t2i_segmentation_mode(core.get("prepared"))
    threshold = _parse_threshold(astrbot_config)
    intent = {
        "enabled": True,
        "explicit": explicit_flag is True,
        "word_threshold": threshold,
        "use_network": bool(isinstance(astrbot_config, Mapping) and astrbot_config.get("t2i_strategy") == "remote"),
        "template_name": astrbot_config.get("t2i_active_template") if isinstance(astrbot_config, Mapping) else None,
        "endpoint": (astrbot_config.get("t2i_endpoint") or None) if isinstance(astrbot_config, Mapping) else None,
        "use_file_service": bool(isinstance(astrbot_config, Mapping) and astrbot_config.get("t2i_use_file_service")),
        "callback_api_base": (astrbot_config.get("callback_api_base") or "") if isinstance(astrbot_config, Mapping) else "",
        "mode": mode,
    }
    core["_deferred_t2i"] = intent
    logger.debug(
        "[CompanionV2][t2i] stage=capture event_ref=%s mode=%s threshold=%d explicit=%s",
        _event_ref(core),
        mode,
        threshold,
        intent["explicit"],
    )
    return intent


def _plain_text_from_chain(chain: Any) -> str:
    if not isinstance(chain, (list, tuple)):
        return ""
    return "".join(part.text for part in chain if isinstance(part, Plain) and isinstance(part.text, str))


def should_collapse_segments_for_t2i(event: Any, parts: Any) -> bool:
    """True when whole_reply T2I mode should merge segments into one long image."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    intent = core.get("_deferred_t2i") if isinstance(core, dict) else None
    if not isinstance(intent, Mapping) or not intent.get("enabled"):
        return False
    if intent.get("mode") != "whole_reply" or core.get("_native_voice_response"):
        return False
    if not isinstance(parts, (list, tuple)) or len(parts) <= 1:
        return False
    texts = []
    for part in parts:
        chain = getattr(part, "chain", None)
        if isinstance(chain, (list, tuple)) and any(isinstance(comp, Node) for comp in chain):
            return False
        text = _plain_text_from_chain(chain).strip()
        if text:
            texts.append(text)
    if not texts:
        return False
    combined = "\n\n".join(texts)
    return len(combined) > int(intent.get("word_threshold", DEFAULT_T2I_WORD_THRESHOLD))


def collapse_segments_for_t2i(message: Any, parts: Any) -> Any:
    """Merge multi-segment parts into a single chain while preserving non-text items and voice source."""
    merged_texts: list[str] = []
    non_plain: list[Any] = []
    voice_sources: list[str] = []
    for part in parts:
        chain = getattr(part, "chain", None)
        if isinstance(chain, (list, tuple)):
            part_text = _plain_text_from_chain(chain).strip()
            if part_text:
                merged_texts.append(part_text)
            for comp in chain:
                if not isinstance(comp, Plain):
                    non_plain.append(comp)
        source = getattr(part, "_companion_v2_voice_source", None)
        if isinstance(source, str) and source.strip():
            voice_sources.append(source.strip())
    chain_items: list[Any] = []
    if merged_texts:
        chain_items.append(Plain("\n\n".join(merged_texts)))
    chain_items.extend(non_plain)
    derive = getattr(message, "derive", None)
    collapsed = derive(chain_items) if callable(derive) else MessageChain(chain=chain_items)
    if voice_sources and hasattr(collapsed, "chain"):
        object.__setattr__(collapsed, "_companion_v2_voice_source", "\n".join(voice_sources))
    return collapsed


def _derive(message: Any, chain: list[Any]) -> Any:
    derive = getattr(message, "derive", None)
    return derive(chain) if callable(derive) else MessageChain(chain=chain)


async def apply_deferred_core_t2i(event: Any, part: Any) -> Any:
    """Render eligible long Plain text into an Image using AstrBot's HtmlRenderer.

    The caller keeps the original ``part`` for history, memory, and idempotency
    deduplication, and only sends the returned rendered message on the wire.
    """
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    intent = core.get("_deferred_t2i") if isinstance(core, dict) else None
    if not isinstance(intent, Mapping) or not intent.get("enabled"):
        return part
    ref = _event_ref(core)
    mode = str(intent.get("mode") or DEFAULT_T2I_SEGMENTATION_MODE)
    if mode == "off":
        logger.debug("[CompanionV2][t2i] stage=skip event_ref=%s mode=off reason=mode_off", ref)
        return part
    if isinstance(core, dict) and core.get("_native_voice_response"):
        logger.debug("[CompanionV2][t2i] stage=skip event_ref=%s mode=%s reason=native_tts", ref, mode)
        return part

    chain = getattr(part, "chain", None)
    if not isinstance(chain, (list, tuple)) or not chain:
        return part
    if any(isinstance(comp, Node) for comp in chain):
        return part

    plain_parts = [comp.text for comp in chain if isinstance(comp, Plain) and isinstance(comp.text, str)]
    if not plain_parts:
        # voice_only success hides Plain text; standalone Record/Image parts carry no text.
        return part
    plain_text = "".join(plain_parts)
    if not plain_text.strip():
        return part

    threshold = int(intent.get("word_threshold", DEFAULT_T2I_WORD_THRESHOLD))
    if len(plain_text) <= threshold:
        logger.debug(
            "[CompanionV2][t2i] stage=skip event_ref=%s mode=%s reason=below_threshold chars=%d threshold=%d",
            ref,
            mode,
            len(plain_text),
            threshold,
        )
        return part

    from astrbot.core import file_token_service, html_renderer

    started = time.monotonic()
    try:
        url = await html_renderer.render_t2i(
            plain_text,
            return_url=True,
            use_network=bool(intent.get("use_network")),
            template_name=intent.get("template_name"),
            endpoint=intent.get("endpoint") or None,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "[CompanionV2][t2i] stage=render event_ref=%s mode=%s state=fallback_text chars=%d threshold=%d error_type=%s",
            ref,
            mode,
            len(plain_text),
            threshold,
            type(error).__name__,
        )
        return part

    elapsed_ms = round((time.monotonic() - started) * 1000)
    if elapsed_ms > 3000:
        logger.warning(
            "[CompanionV2][t2i] stage=render event_ref=%s mode=%s state=slow ms=%d chars=%d",
            ref,
            mode,
            elapsed_ms,
            len(plain_text),
        )
    if not isinstance(url, str) or not url:
        logger.warning(
            "[CompanionV2][t2i] stage=render event_ref=%s mode=%s state=fallback_text reason=empty_url chars=%d",
            ref,
            mode,
            len(plain_text),
        )
        return part

    try:
        if url.startswith("http"):
            image = Image.fromURL(url)
        elif intent.get("use_file_service") and intent.get("callback_api_base"):
            token = await file_token_service.register_file(url)
            image = Image.fromURL(f"{intent['callback_api_base']}/api/file/{token}")
        else:
            image = Image.fromFileSystem(url)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "[CompanionV2][t2i] stage=render event_ref=%s mode=%s state=fallback_text reason=image_wrap_failed error_type=%s",
            ref,
            mode,
            type(error).__name__,
        )
        return part

    output: list[Any] = []
    inserted = False
    for comp in chain:
        if isinstance(comp, Plain):
            if not inserted:
                output.append(image)
                inserted = True
        else:
            output.append(comp)
    logger.info(
        "[CompanionV2][t2i] stage=render event_ref=%s mode=%s state=rendered chars=%d threshold=%d ms=%d",
        ref,
        mode,
        len(plain_text),
        threshold,
        elapsed_ms,
    )
    return _derive(part, output)
