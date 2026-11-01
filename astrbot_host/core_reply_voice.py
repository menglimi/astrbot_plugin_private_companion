"""Apply configured TTS to authorized AstrBot core replies."""

import re
import asyncio
from collections.abc import Mapping

from astrbot.api import logger
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.voice_reply import (
    VoiceReplyPolicy,
    separate_spoken,
    speech_candidates,
    speech_chunks,
    strip_voice_markup,
    voice_wrapper_pairing,
)
from .core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.conversation.voice_markup import slice_speech, visible_speech


def voice_prompt_instruction(policy, provider_instruction=""):
    if not isinstance(policy, VoiceReplyPolicy):
        return ""
    from companion_v2.conversation.voice_reply import capability_instruction

    return "\n".join(part for part in (capability_instruction(policy), policy.instruction, provider_instruction) if part)


def observe_core_voice_response(event, response):
    """Capture speech before native regex segmentation can break its wrappers."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if (not isinstance(core, dict) or response is None or getattr(response, "is_chunk", False)
            or getattr(response, "role", None) != "assistant"
            or getattr(response, "tools_call_name", ())):
        return
    if core.get("_voice_observed_response") is response:
        return
    core["_voice_observed_response"] = response
    prepared = core.get("prepared")
    policy = prepared.get("voice_reply_policy") if isinstance(prepared, Mapping) else None
    text = getattr(response, "completion_text", "") or ""
    visible, spans = separate_spoken(text, policy)
    repaired = core.pop("_voice_repaired_response", None)
    if repaired is not None and repaired["response"] is response:
        core["_voice_authored"] = repaired["plan"]
        spans = repaired["plan"]["spans"]
    elif spans:
        core["_voice_authored"] = {"visible": visible, "spans": spans, "cursor": 0, "used": set()}
    else:
        core.pop("_voice_authored", None)
    if isinstance(policy, VoiceReplyPolicy):
        core["_voice_response_diagnostics"] = {"mode": policy.trigger_mode, "spoken_spans": len(spans),
            "reason": "authored" if spans else "plain_generation" if policy.generation_mode == "plain"
            and policy.trigger_mode != "adaptive" else "no_authored_speech"}
        logger.info("[CompanionV2][media] stage=response event_ref=%s media=voice mode=%s spans=%s reason=%s",
                    prepared.get("event_ref", "unknown"), policy.trigger_mode, len(spans),
                    core["_voice_response_diagnostics"]["reason"])
    if visible != text:
        response.completion_text = visible
        for part in getattr(getattr(response, "result_chain", None), "chain", ()):
            if isinstance(part, Plain):
                part.text = separate_spoken(part.text, policy)[0]


def capture_core_voice_message(event, message):
    """Consume native tool speech before text cleanup or segmentation."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    policy = prepared.get("voice_reply_policy") if isinstance(prepared, Mapping) else None
    if not isinstance(policy, VoiceReplyPolicy):
        return message
    visible, spans, cleaned = _clean_plain_runs(message, policy)
    if spans:
        core["_voice_authored"] = {"visible": visible, "spans": spans, "cursor": 0, "used": set()}
        return cleaned
    source = _plain_text(message)
    if visible == source:
        return message
    # 未配对/未闭合的语音包装已经被 separate_spoken 去掉；这里如果退回原始消息，
    # 字面量 <pc_tts> 就会随正文发给用户，因为下游协议清洗刻意排除了 tts 家族。
    # plain 生成模式下成对标签也走这条分支，所以 state 只描述"清理了包装"，
    # 由 pairing 区分成对/未成对，避免把成对标签误记成 unpaired。
    paired, stray = voice_wrapper_pairing(source)
    pairing = "unpaired" if stray else ("paired" if paired else "other")
    logger.info("[CompanionV2][media] stage=voice_cleanup event_ref=%s media=voice "
                "state=voice_wrapper_cleanup pairing=%s paired_wrappers=%d stray_tags=%d "
                "removed_chars=%d visible_chars=%d",
                prepared.get("event_ref", "unknown"), pairing, paired, stray,
                len(source) - len(visible), len(visible))
    return cleaned


def _captured_speech(core, visible):
    authored = core.get("_voice_authored") if isinstance(core, dict) else None
    if not authored:
        return ""
    start = authored["visible"].find(visible.strip(), authored["cursor"])
    if start < 0:
        return ""
    end = start + len(visible.strip())
    authored["cursor"] = end
    selected = []
    for index, (position, speech) in enumerate(authored["spans"]):
        displayed = visible_speech(speech)
        if authored["visible"].startswith(displayed, position):
            left, right = max(start, position), min(end, position + len(displayed))
            if right > left:
                selected.append(slice_speech(speech, left - position, right - position))
        elif index not in authored["used"] and start <= position < end:
            selected.append(speech)
            authored["used"].add(index)
    return "\n".join(selected)


def _plain_text(message):
    chain = getattr(message, "chain", None)
    if isinstance(chain, (list, tuple)):
        return "".join(component.text for component in chain if isinstance(component, Plain))
    return message if isinstance(message, str) else ""


def _clean_plain_runs(message, policy):
    if isinstance(message, str):
        visible, spans = separate_spoken(message, policy)
        return visible, spans, visible

    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return "", (), message
    output, visible_parts, spans, index = [], [], [], 0
    while index < len(chain):
        component = chain[index]
        if not isinstance(component, Plain):
            output.append(component)
            index += 1
            continue
        run = []
        while index < len(chain) and isinstance(chain[index], Plain):
            run.append(chain[index].text)
            index += 1
        run_visible, run_spans = separate_spoken("".join(run), policy)
        offset = sum(map(len, visible_parts))
        spans.extend((offset + position, speech) for position, speech in run_spans)
        visible_parts.append(run_visible)
        if run_visible:
            output.append(Plain(run_visible))
    visible = "".join(visible_parts)
    derive = getattr(message, "derive", None)
    cleaned = derive(output) if callable(derive) else MessageChain(chain=output)
    return visible, tuple(spans), cleaned


def _message_with_voice(message, visible, records, policy, *, hide_text):
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        chain = [Plain(message)] if isinstance(message, str) and message else []
        message = MessageChain(chain=chain)

    output = []
    first_plain = None
    last_plain = None
    for component in chain:
        if isinstance(component, Plain):
            if first_plain is None:
                first_plain = len(output)
            if not hide_text:
                output.append(component)
                last_plain = len(output) - 1
        else:
            output.append(component)

    if policy.position == "before_text":
        insert_at = first_plain if first_plain is not None else 0
    elif policy.position == "after_text":
        insert_at = last_plain + 1 if last_plain is not None else 0
    else:
        insert_at = len(output)
    insert_at = max(0, min(insert_at, len(output)))
    output[insert_at:insert_at] = records

    derive = getattr(message, "derive", None)
    return derive(output) if callable(derive) else MessageChain(chain=output)


async def prepare_core_reply_voice_message(
    event,
    prepared,
    message,
    *,
    renderer,
    delivery,
    authorize,
    segment_index=0,
    segment_count=1,
):
    """Synthesize one completed segment while preserving text and media order."""
    policy = prepared.get("voice_reply_policy") if hasattr(prepared, "get") else None
    if not isinstance(policy, VoiceReplyPolicy):
        if isinstance(message, str):
            return strip_voice_markup(message)
        chain = getattr(message, "chain", None)
        if isinstance(chain, (list, tuple)) and any(
                isinstance(part, Plain) and strip_voice_markup(part.text) != part.text for part in chain):
            return _clean_plain_runs(message, None)[2]
        return message
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    diagnostics = core.setdefault("_voice_reply_diagnostics", []) if isinstance(core, dict) else []
    note = {"segment": segment_index + 1, "language": policy.language, "state": "not_started"}
    diagnostics.append(note)
    if not callable(renderer) or not callable(getattr(event, "get_extra", None)):
        note["state"] = "renderer_unavailable"
        return _clean_plain_runs(message, policy)[2]

    chain = getattr(message, "chain", None)
    if isinstance(message, Record) or (isinstance(chain, (list, tuple)) and
                                       any(isinstance(component, Record) for component in chain)):
        note["state"] = "native_voice_exists"
        return message
    segment_index = max(0, int(segment_index))
    segment_count = max(1, int(segment_count))
    # Whole-reply voice speaks the joined text of the turn, so a segment with no
    # text of its own (a trailing image, say) still has to reach the bucket
    # below: bailing out here dropped the speech collected so far and the turn
    # ended silently.
    whole = policy.segment_mode == "whole" and isinstance(core, dict)
    source = _plain_text(message)
    if not source.strip() and not whole:
        note["state"] = "no_text"
        return message

    visible, spans, cleaned_message = _clean_plain_runs(message, policy)
    if not visible.strip() and not whole:
        note["state"] = "no_visible_text"
        return cleaned_message
    captured_speech = _captured_speech(core, getattr(message, "_companion_v2_voice_source", visible)) if not spans else ""
    candidates = speech_candidates(visible, (visible,), spans, policy)
    segment_speech = captured_speech or (candidates[0].strip() if candidates else "")
    if whole:
        # Whole-reply voice waits until every text segment has been prepared,
        # then synthesizes the joined speech once.  Per-segment TTS cuts the
        # sentence context and produces noticeably flatter prosody.
        bucket = core.setdefault("_voice_whole_segments", {"visible": [], "speech": []})
        if visible.strip():
            bucket["visible"].append(visible)
        if segment_speech:
            bucket["speech"].append(segment_speech)
        if segment_index < segment_count - 1:
            note["state"] = "segment_deferred"
            return cleaned_message
        visible = "\n".join(bucket["visible"]).strip()
        speech = "\n".join(bucket["speech"]).strip()
        core.pop("_voice_whole_segments", None)
        if not speech:
            note["state"] = "no_authored_speech"
            logger.info("[CompanionV2][media] stage=delivery event_ref=%s media=voice state=%s segment=%s/%s",
                        prepared.get("event_ref", "unknown"), note["state"], segment_index + 1, segment_count)
            return cleaned_message
    else:
        selected = (policy.segment_mode in {"whole", "each"} or
                    policy.segment_mode == "first" and segment_index == 0 or
                    policy.segment_mode == "last" and segment_index == segment_count - 1)
        if not selected:
            note["state"] = "segment_skipped"
            return cleaned_message
        speech = segment_speech
        if not speech:
            note["state"] = "no_authored_speech"
            logger.info("[CompanionV2][media] stage=delivery event_ref=%s media=voice state=%s segment=%s/%s",
                        prepared.get("event_ref", "unknown"), note["state"], segment_index + 1, segment_count)
            return cleaned_message
    chunks = speech_chunks(speech, policy.max_speech_chars)
    note["chars"], note["chunks"] = len(speech), len(chunks)

    context = prepared.get("context")
    event_id = prepared.get("event_id") or prepared.get("turn_id") or "core-reply"
    claim = None
    claim_route = getattr(delivery, "claim_core_route", None)
    kind = getattr(getattr(getattr(context, "scope", None), "kind", None), "value", None)
    cooldown = policy.cooldown(kind) if kind is not None else policy.cooldown_seconds
    if callable(claim_route) and cooldown:
        claim = claim_route(context, event_id, cooldown)
        if claim is None:
            note["state"] = "cooldown"
            return cleaned_message

    try:
        records = []
        for chunk in chunks:
            await authorize()
            render_options = {"provider_id": policy.provider_for_language(kind),
                              "timeout_seconds": policy.timeout_seconds}
            model_override = getattr(policy, "_model_override", "")
            voice_override = getattr(policy, "_voice_override", "")
            if model_override:
                render_options["model_override"] = model_override
            if voice_override:
                render_options["voice_override"] = voice_override
            audio = await renderer(context, chunk, **render_options)
            audio_data = audio.get("data") if isinstance(audio, dict) and audio.get("type") == "record" else None
            file_value = audio_data.get("file") if isinstance(audio_data, dict) else None
            if not isinstance(file_value, str) or not file_value.startswith("base64://"):
                raise ValueError("tts_audio_unavailable")
            record = Record(file=file_value, text=chunk)
            object.__setattr__(record, "_companion_v2_spoken_text", visible_speech(chunk).strip())
            records.append(record)
    except asyncio.CancelledError:
        if claim is not None:
            delivery.release(claim, claim["part_id"])
        raise
    except Exception as error:
        note["state"] = "synthesis_failed"
        note["error_type"] = type(error).__name__
        logger.warning("[CompanionV2] 核心回复 TTS 降级为文字 event_ref=%s error_type=%s",
                       prepared.get("event_ref", "unknown"), type(error).__name__)
        if claim is not None:
            delivery.release(claim, claim["part_id"])
        return cleaned_message
    note["state"] = "ready"
    logger.info("[CompanionV2] 核心回复 TTS 已合成 event_ref=%s language=%s chars=%s segment=%s/%s",
                prepared.get("event_ref", "unknown"), policy.language, len(speech),
                segment_index + 1, segment_count)
    if isinstance(core, dict) and claim is not None:
        core["_voice_reply_pending_claim"] = (delivery, claim)
    same_text = re.sub(r"\s+", "", visible) == re.sub(r"\s+", "", visible_speech(speech))
    return _message_with_voice(cleaned_message, visible, records, policy,
                               hide_text=(policy.delivery_mode == "voice_only"
                                          and not (policy.segment_mode == "whole" and segment_count > 1)
                                          and same_text))


def prepare_core_reply_voice(event):
    """Prepare voice policy from the already captured core reply context."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    if not isinstance(prepared, dict) or not isinstance(prepared.get("voice_reply_policy"), VoiceReplyPolicy):
        return

    setter = getattr(event, "set_extra", None)
    if callable(setter):
        setter("enable_streaming", False)


def release_pending_voice_claim(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    pending = core.pop("_voice_reply_pending_claim", None) if isinstance(core, dict) else None
    if pending is not None:
        delivery, claim = pending
        delivery.release(claim, claim["part_id"])


def commit_pending_voice_claim(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(core, dict):
        core.pop("_voice_reply_pending_claim", None)
