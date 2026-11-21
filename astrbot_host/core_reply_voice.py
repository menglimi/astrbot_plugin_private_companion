"""Apply configured TTS to authorized AstrBot core replies."""

import re
import asyncio
from collections.abc import Mapping

from astrbot.api import logger
from astrbot.core.message.components import At, AtAll, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.text_processing import markdown_code_runs, unwrap_text_emit_calls
from companion_v2.conversation.voice_reply import (
    VoiceReplyPolicy,
    authored_singing,
    detected_speech_language,
    separate_spoken,
    speech_candidates,
    speech_chunks,
    strip_voice_markup,
    tts_wait_budget,
    voice_wrapper_pairing,
)
from .core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.conversation.voice_markup import slice_speech, strip_singing_provider_cue, visible_speech
from .voice import singsing_config_origin


_SINGSING_INSTRUCTION = (
    '遵守本轮触发条件和朗读范围，按语境自主选择说话或唱歌。'
    '适合说话时用 <pc_tts>实际要说的话</pc_tts>，'
    '适合唱歌时在最终正文用 <pc_tts style="sing">实际要唱的歌词</pc_tts>；'
    '同一条回复只选一种语音方式，多个唱歌标签均注明 style="sing"。'
    '按语境决定，触发模式允许且无合适内容时可以回复纯文字；用户只要文字时不要使用语音标签。'
    '用户明确要语音或唱歌时优先按其要求；不能满足时坦诚回复，不声称已经发送。'
    '唱歌标签内只写歌词，不叠加表演标记；不要在可见正文中写合成指令。'
)


def voice_prompt_instruction(policy, provider_instruction="", rules_override="", *, singsing=False):
    if not isinstance(policy, VoiceReplyPolicy):
        return ""
    from companion_v2.conversation.voice_reply import capability_instruction

    rules = (policy.instruction_with_rules(rules_override)
             if isinstance(rules_override, str) and rules_override.strip() else policy.instruction)
    singing_instruction = (_SINGSING_INSTRUCTION
                           if singsing and policy.uses_model_tags and policy.direction_mode != "off" else "")
    return "\n".join(part for part in (capability_instruction(policy), rules, provider_instruction,
                                      singing_instruction) if part)


def observe_core_voice_response(event, response, *, final=True):
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
    singsing = bool(isinstance(prepared, Mapping) and prepared.get("voice_singsing_enabled"))
    result_chain = getattr(response, "result_chain", None)
    chain = getattr(result_chain, "chain", None)
    if isinstance(chain, list):
        text = _plain_text(result_chain)
        visible, spans, cleaned = _clean_plain_runs(result_chain, policy, unwrap_calls=False, singsing=singsing)
        if visible != text:
            chain[:] = cleaned.chain
    else:
        text = getattr(response, "completion_text", "") or ""
        visible, spans = separate_spoken(text, policy, singsing=singsing)
        if visible != text:
            response.completion_text = visible
    core["_voice_checked_code_reply"] = visible if any(is_code for is_code, _ in markdown_code_runs(visible)) else ""
    repaired = core.pop("_voice_repaired_response", None)
    if repaired is not None and repaired["response"] is response:
        core["_voice_authored"] = repaired["plan"]
        spans = repaired["plan"]["spans"]
    elif spans:
        core["_voice_authored"] = {"visible": visible, "spans": spans, "cursor": 0, "used": set(),
            "singing": bool(final and singsing and authored_singing(text))}
    else:
        core.pop("_voice_authored", None)
    if isinstance(policy, VoiceReplyPolicy):
        core["_voice_response_diagnostics"] = {"mode": policy.trigger_mode, "spoken_spans": len(spans),
            "reason": "authored" if spans else "plain_generation" if policy.generation_mode == "plain"
            and policy.trigger_mode != "adaptive" else "no_authored_speech"}
        logger.info("[CompanionV2][media] stage=response event_ref=%s media=voice mode=%s spans=%s reason=%s",
                    prepared.get("event_ref", "unknown"), policy.trigger_mode, len(spans),
                    core["_voice_response_diagnostics"]["reason"])


def capture_core_voice_message(event, message):
    """Consume native tool speech before text cleanup or segmentation."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    policy = prepared.get("voice_reply_policy") if isinstance(prepared, Mapping) else None
    source = _plain_text(message)
    if not isinstance(policy, VoiceReplyPolicy) or _already_checked_code(core, source):
        return message
    visible, spans, cleaned = _clean_plain_runs(message, policy, singsing=bool(prepared.get("voice_singsing_enabled")))
    if spans:
        core["_voice_authored"] = {"visible": visible, "spans": spans, "cursor": 0, "used": set()}
        return cleaned
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


def _find_authored_span(authored_visible, needle, cursor):
    start = authored_visible.find(needle, cursor)
    if start >= 0:
        return start, start + len(needle)
    if not needle or cursor >= len(authored_visible):
        return -1, -1
    from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER, control_token_ranges
    if not control_token_ranges(authored_visible):
        return -1, -1
    stripped_parts, orig_indices = [], []
    offset = cursor
    for line in authored_visible[cursor:].splitlines(keepends=True):
        if line.count(LLM_SEGMENT_MARKER) == 1:
            left, right = line.split(LLM_SEGMENT_MARKER)
            if not (left.strip() and right.strip()):
                if left.strip():
                    for idx, ch in enumerate(left.rstrip()):
                        stripped_parts.append(ch)
                        orig_indices.append(offset + idx)
                if left.strip() and not right.strip():
                    right_start = len(left) + len(LLM_SEGMENT_MARKER)
                    for idx, ch in enumerate(right):
                        stripped_parts.append(ch)
                        orig_indices.append(offset + right_start + idx)
                elif right.strip():
                    trimmed = right.lstrip()
                    right_start = len(left) + len(LLM_SEGMENT_MARKER) + (len(right) - len(trimmed))
                    for idx, ch in enumerate(trimmed):
                        stripped_parts.append(ch)
                        orig_indices.append(offset + right_start + idx)
                offset += len(line)
                continue
        tokens = control_token_ranges(line)
        if not tokens:
            for idx, ch in enumerate(line):
                stripped_parts.append(ch)
                orig_indices.append(offset + idx)
        else:
            pos = 0
            for tok_start, tok_end in tokens:
                for idx in range(pos, tok_start):
                    stripped_parts.append(line[idx])
                    orig_indices.append(offset + idx)
                if (tok_start and tok_end < len(line)
                        and not line[tok_start - 1].isspace() and not line[tok_end].isspace()):
                    stripped_parts.append(" ")
                    orig_indices.append(offset + tok_start)
                pos = tok_end
            for idx in range(pos, len(line)):
                stripped_parts.append(line[idx])
                orig_indices.append(offset + idx)
        offset += len(line)
    stripped = "".join(stripped_parts)
    match_start = stripped.find(needle)
    if match_start < 0:
        return -1, -1
    match_end = match_start + len(needle) - 1
    return orig_indices[match_start], orig_indices[match_end] + 1


def _captured_speech(core, visible):
    authored = core.get("_voice_authored") if isinstance(core, dict) else None
    if not authored:
        return ""
    needle = visible.strip()
    start, end = _find_authored_span(authored["visible"], needle, authored["cursor"])
    if start < 0:
        return ""
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


def _already_checked_code(core, source):
    """Do not reinterpret a literal tag if Core separated it from its fence."""
    authored = core.get("_voice_checked_code_reply", "") if isinstance(core, dict) else ""
    segment = source.strip()
    return bool(segment and segment in authored)


def _clean_plain_runs(message, policy, *, unwrap_calls=True, singsing=False):
    def separate(text):
        return separate_spoken(unwrap_text_emit_calls(text) if unwrap_calls else text, policy, singsing=singsing)

    if isinstance(message, str):
        visible, spans = separate(message)
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
        start = index
        run = []
        while index < len(chain) and isinstance(chain[index], Plain):
            run.append(chain[index].text)
            index += 1
        source = "".join(run)
        run_visible, run_spans = separate(source)
        offset = sum(map(len, visible_parts))
        spans.extend((offset + position, speech) for position, speech in run_spans)
        visible_parts.append(run_visible)
        if run_visible == source:
            output.extend(chain[start:index])
        elif run_visible:
            output.append(Plain(run_visible))
    visible = "".join(visible_parts)
    derive = getattr(message, "derive", None)
    cleaned = derive(output) if callable(derive) else MessageChain(chain=output)
    return visible, tuple(spans), cleaned


def _message_with_voice(message, visible, records, policy, *, hide_text, singing=False):
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

    if singing:
        insert_at = len(output)
    elif policy.position == "before_text":
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
    get_extra = getattr(event, "get_extra", None)
    core = get_extra(CORE_REPLY_CONTEXT_EXTRA) if callable(get_extra) else None
    source = _plain_text(message)
    matching_source = getattr(message, "_companion_v2_voice_source", source)
    checked_code = _already_checked_code(core, matching_source)
    if not isinstance(policy, VoiceReplyPolicy):
        if checked_code:
            return message
        if isinstance(message, str):
            return strip_voice_markup(message)
        chain = getattr(message, "chain", None)
        if isinstance(chain, (list, tuple)) and any(
                isinstance(part, Plain) and strip_voice_markup(part.text) != part.text for part in chain):
            return _clean_plain_runs(message, None)[2]
        return message
    diagnostics = core.setdefault("_voice_reply_diagnostics", []) if isinstance(core, dict) else []
    singsing = bool(prepared.get("voice_singsing_enabled"))
    note = {"segment": segment_index + 1, "language": policy.language, "state": "not_started"}
    diagnostics.append(note)
    if not callable(renderer) or not callable(get_extra):
        note["state"] = "renderer_unavailable"
        return message if checked_code else _clean_plain_runs(message, policy, singsing=singsing)[2]

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
    if not source.strip() and not whole:
        note["state"] = "no_text"
        return message

    visible, spans, cleaned_message = ((source, (), message) if checked_code
                                       else _clean_plain_runs(message, policy, singsing=singsing))
    if not visible.strip() and not whole:
        note["state"] = "no_visible_text"
        return cleaned_message
    voice_source = getattr(message, "_companion_v2_voice_source", source if checked_code else visible)
    captured_speech = _captured_speech(core, voice_source) if not spans else ""
    candidates = speech_candidates(visible, (visible,), spans, policy)
    segment_speech = captured_speech or (candidates[0].strip() if candidates else "")
    singing = bool(captured_speech and isinstance(core, dict) and singsing
                   and core.get("_voice_authored", {}).get("singing", False))
    if whole:
        # Whole-reply voice waits until every text segment has been prepared,
        # then synthesizes the joined speech once.  Per-segment TTS cuts the
        # sentence context and produces noticeably flatter prosody.
        bucket = core.setdefault("_voice_whole_segments", {"visible": [], "speech": [], "singing": True})
        if visible.strip():
            bucket["visible"].append(visible)
        if segment_speech:
            bucket["speech"].append(segment_speech)
            bucket["singing"] = bucket.get("singing", False) and singing
        if segment_index < segment_count - 1:
            note["state"] = "segment_deferred"
            return cleaned_message
        visible = "\n".join(bucket["visible"]).strip()
        speech = "\n".join(bucket["speech"]).strip()
        singing = bucket.get("singing", False)
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
    if singsing:
        speech = strip_singing_provider_cue(speech)
    chunks = speech_chunks(speech, policy.max_speech_chars)
    if not chunks:
        note["state"] = "no_authored_speech"
        return cleaned_message
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
            # The model may have honoured a user request for another language than
            # the configured default; route the provider by the text that will
            # actually be spoken instead of trusting the setting alone.
            language = detected_speech_language(chunk) or policy.language
            note["language"] = language
            render_options = {"provider_id": policy.provider_for_language(kind, language=language),
                              "timeout_seconds": policy.timeout_seconds}
            model_override = getattr(policy, "_model_override", "")
            voice_override = getattr(policy, "_voice_override", "")
            if model_override:
                render_options["model_override"] = model_override
            if voice_override:
                render_options["voice_override"] = voice_override
            if singing:
                render_options["singing"] = True
                umo = getattr(event, "unified_msg_origin", "")
                if umo:
                    render_options.update(singing_umo=umo, singing_config_umo=singsing_config_origin(event))
            render = renderer(context, chunk, **render_options)
            budget = tts_wait_budget(policy, segment_index=segment_index,
                                     segment_count=segment_count, whole_reply=whole)
            if budget is not None:
                # Only a mid-reply segment is capped: the final one (and the
                # whole-reply synthesis) is bounded by the renderer's own timeout.
                audio = await asyncio.wait_for(render, timeout=budget)
            else:
                audio = await render
            audio_data = audio.get("data") if isinstance(audio, dict) and audio.get("type") == "record" else None
            file_value = audio_data.get("file") if isinstance(audio_data, dict) else None
            if not isinstance(file_value, str) or not file_value.startswith("base64://"):
                raise ValueError("tts_audio_unavailable")
            record = Record(file=file_value, text=chunk)
            object.__setattr__(record, "_companion_v2_spoken_text", visible_speech(chunk).strip())
            if singing:
                object.__setattr__(record, "_companion_v2_optional_singing", True)
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
                prepared.get("event_ref", "unknown"), note.get("language") or policy.language, len(speech),
                segment_index + 1, segment_count)
    if isinstance(core, dict) and claim is not None:
        core["_voice_reply_pending_claim"] = (delivery, claim)
    same_text = re.sub(r"\s+", "", visible) == re.sub(r"\s+", "", visible_speech(speech))
    # Native quotes and mentions are metadata on a text send, not on a media
    # send. Keep the authored text as their carrier even in voice-only mode;
    # otherwise TTS leaves an unsupported Reply/At + Record payload.
    platform = getattr(event, "get_platform_name", None)
    native_text_carrier = (callable(platform) and platform() == "wechat_native"
                           and any(isinstance(part, (Reply, At, AtAll))
                                   for part in getattr(cleaned_message, "chain", ())))
    return _message_with_voice(cleaned_message, visible, records, policy, singing=singing,
                               hide_text=(policy.delivery_mode == "voice_only"
                                          and not singing
                                          and not (policy.segment_mode == "whole" and segment_count > 1)
                                          and same_text and not native_text_carrier))


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
