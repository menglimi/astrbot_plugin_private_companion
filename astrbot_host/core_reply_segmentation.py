"""Complete native reply segmentation while retaining AstrBot delivery."""

import asyncio
from collections.abc import Mapping
from dataclasses import fields
import logging
import re

from astrbot.core.message.components import Node, Plain, Record
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.segmentation import SegmentationPolicy, part_gap_seconds
from companion_v2.conversation.segment_control import (
    LLM_SEGMENT_MARKER,
    control_token_ranges,
    controlled_parts,
)
from companion_v2.conversation.stream_segments import TextSegmentStream
from companion_v2.conversation.voice_reply import VoiceReplyPolicy, strip_voice_markup
from companion_v2.sources.privacy import redact_text
from .core_reply import CORE_REPLY_CONTEXT_EXTRA, _is_core_reply_result, is_core_reply_stream, sent_core_text
from .core_reply_presentation import (
    core_reply_presentation, has_reply_body, mark_presented, note_removed_text, present_text_parts,
    strip_plaintext_tool_calls,
)
from .core_reply_voice import capture_core_voice_message


logger = logging.getLogger(__name__)
MARKED = "_companion_v2_segmented"
NATIVE_PROMPT = (
    "群聊正文按语意自然停顿；一两句通常就够，不为凑段数扩写。只有清楚的主题转折才拆开长文。"
    "连续标点（例如 ...？、？！）保持在同一条消息里，不要生成以孤立标点开头的气泡；称呼或短分句跟随完整回应。"
    "需要主动指定独立气泡时，边界只用完整标记 {{split_marker}} 单独占一行。"
    "标记不是正文；不要放进代码、引用或链接。直接输出聊天正文，不输出 JSON 或 claims。"
)
PRIVATE_PROMPT = (
    "普通私聊正文按自然停顿组织，完整接住对方的话；不要为凑条数扩写或拆散一句话、称呼与回应、因果解释。"
    "先看完整条回复再安排边界，避免前几条很短、最后一条收进大量剩余内容。"
    "连续标点（例如 ...？、？！）保持在同一条消息里，不要生成以孤立标点开头的气泡。"
    "需要指定独立气泡时，边界只用完整标记 {{split_marker}} 单独占一行。"
    "标记不是正文；不要放进代码、引用或链接。直接输出聊天正文，不输出 JSON 或内部字段。"
)


def segmentation_instruction(event, request=None):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    segment = (core.get("_reply_segmentation") or core.get("_group_segmentation")
               if isinstance(core, dict) else None)
    if not segment or not segment["policy"].enabled:
        return ""
    policy = segment["policy"]
    request_streaming = (getattr(request, "enable_streaming", False) is True
                         or getattr(request, "stream", False) is True)
    if (request_streaming
            or getattr(event, "get_extra", lambda *_: None)("enable_streaming") is True):
        return ""
    prepared = core.get("prepared") or {}
    if not policy.llm_controlled:
        return ""
    prompt = re.sub(r"\{\{\s*split_marker\s*\}\}", lambda _: LLM_SEGMENT_MARKER,
                    policy.prompt or (NATIVE_PROMPT if prepared.get("channel") == "group" else PRIVATE_PROMPT),
                    flags=re.I)
    return prompt


def _parts(policy, text):
    if not text.strip():
        return (text,) if text else ()
    parts, _ = controlled_parts(text)
    if not parts:
        return ()
    return policy.parts(text) if len(text) <= 65536 else ("".join(parts),)


def _extract_non_ws_prefix(text, count, clean_leading_ws=0):
    if count <= 0:
        return ""
    seen = 0
    for index, char in enumerate(text):
        if not char.isspace():
            seen += 1
            if seen > count:
                return text[:max(0, index - clean_leading_ws)]
    return ""


def _match_pre_split_parts(body, pre_split, used_count):
    if not pre_split or used_count >= len(pre_split):
        return None, used_count
    entry = pre_split[used_count]
    clean_joined = entry["clean_joined"]
    planned = entry["parts"]
    if body == clean_joined:
        return planned, used_count + 1
    if used_count == 0 and clean_joined and body.endswith(clean_joined):
        prefix = body[:len(body) - len(clean_joined)]
        return (prefix + planned[0], *planned[1:]), used_count + 1
    compact_body = re.sub(r"\s+", "", body)
    compact_clean = re.sub(r"\s+", "", clean_joined)
    if compact_body == compact_clean:
        return planned, used_count + 1
    if used_count == 0 and compact_clean and compact_body.endswith(compact_clean):
        clean_leading_ws = len(clean_joined) - len(clean_joined.lstrip())
        prefix = _extract_non_ws_prefix(
            body, len(compact_body) - len(compact_clean), clean_leading_ws=clean_leading_ws)
        if prefix:
            return (prefix + planned[0], *planned[1:]), used_count + 1
    return None, used_count


def _present_pre_split_parts(body, matched_parts, presentation, *, tool_names=()):
    from companion_v2.conversation.text_processing import strip_internal_expression_protocol

    cleaned_body = strip_internal_expression_protocol(body)
    cleaned_body, _removed = strip_plaintext_tool_calls(cleaned_body, tool_names)
    if presentation is not None:
        text, leaked = presentation.framework_guard(cleaned_body)
        if leaked:
            return (text,)
    output = []
    for segment in matched_parts:
        output.extend(present_text_parts(
            segment, presentation, split=lambda item: (item,), tool_names=tool_names))
    return tuple(output)


def split_message(message, policy, presentation=None, *, scope_kind="private", tool_names=(), pre_split=None):
    """Preserve component order; quote/mention headers occur only once."""
    if presentation is not None:
        source_text = (message if isinstance(message, str) else "".join(
            component.text for component in getattr(message, "chain", ()) if isinstance(component, Plain)))
        source_text, _removed_tool_calls = strip_plaintext_tool_calls(source_text, tool_names)
        if presentation.forbidden_word_guard(source_text, scope_kind=scope_kind):
            return ()

    def parts(text, matched=None):
        if matched is not None:
            return (_present_pre_split_parts(text, matched, presentation, tool_names=tool_names)
                    if presentation is not None else matched)
        return (present_text_parts(text, presentation, lambda body: _parts(policy, body),
                                   tool_names=tool_names)
                if presentation is not None else _parts(policy, text))

    if isinstance(message, str):
        return tuple(mark_presented(part, presentation) if presentation is not None else part
                     for part in parts(message))
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return (message,)
    groups, current, index, pre_split_index = [], [], 0, 0
    while index < len(chain):
        component = chain[index]
        if not isinstance(component, Plain):
            current.append(component)
            index += 1
            continue
        body = ""
        while index < len(chain) and isinstance(chain[index], Plain):
            body += chain[index].text
            index += 1
        matched, pre_split_index = _match_pre_split_parts(body, pre_split, pre_split_index)
        for number, text in enumerate(parts(body, matched)):
            if number:
                groups.append(current)
                current = []
            current.append(Plain(text))
    if current:
        groups.append(current)
    derive = getattr(message, "derive", None)
    result = []
    for group in groups:
        if presentation is not None and not has_reply_body(group):
            continue
        part = derive(group) if callable(derive) else MessageChain(chain=group)
        setattr(part, MARKED, True)
        if presentation is not None:
            mark_presented(part, presentation)
        result.append(part)
    return tuple(result)


def _will_voice_deliver(core, prepared):
    if isinstance(core, dict) and core.get("_voice_authored"):
        return True
    policy = prepared.get("voice_reply_policy") if isinstance(prepared, Mapping) else None
    return isinstance(policy, VoiceReplyPolicy) and not policy.uses_model_tags


def prepare_core_result_decoration(event):
    """Sanitize control tokens and keep segmented/voiced replies intact across ResultDecorateStage."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return
    if not core.get("delivery_guard_active") or not _is_core_reply_result(event):
        core.pop("_prepared_decoration", None)
        return
    getter = getattr(event, "get_result", None)
    result = getter() if callable(getter) else None
    chain = getattr(result, "chain", None)
    if result is None or not isinstance(chain, list) or not chain:
        core.pop("_prepared_decoration", None)
        return
    content_type = getattr(getattr(result, "result_content_type", None), "name", None)
    if content_type in {"STREAMING_RESULT", "STREAMING_FINISH"}:
        core.pop("_prepared_decoration", None)
        return

    plain_snapshot = tuple(part.text for part in chain if isinstance(part, Plain))
    previous = core.get("_prepared_decoration")
    if (isinstance(previous, dict) and previous.get("result") is result
            and previous.get("plain_texts") == plain_snapshot):
        return
    core.pop("_prepared_decoration", None)

    prepared = core.get("prepared")
    prepared = prepared if isinstance(prepared, Mapping) else {}
    voice_policy = prepared.get("voice_reply_policy")
    if isinstance(voice_policy, VoiceReplyPolicy):
        cleaned = capture_core_voice_message(event, result)
        if cleaned is not result and isinstance(getattr(cleaned, "chain", None), list):
            chain[:] = cleaned.chain
    elif any(isinstance(part, Plain) and strip_voice_markup(part.text) != part.text for part in chain):
        updated = []
        for part in chain:
            if isinstance(part, Plain):
                cleaned_text = strip_voice_markup(part.text)
                if cleaned_text or not part.text:
                    updated.append(part if cleaned_text == part.text else Plain(cleaned_text))
            else:
                updated.append(part)
        chain[:] = updated

    if not chain:
        return

    seg_state = core.get("_reply_segmentation") or core.get("_group_segmentation")
    seg_policy = seg_state.get("policy") if isinstance(seg_state, dict) else None

    new_chain = []
    runs = []
    has_multi_segments = False
    index = 0
    while index < len(chain):
        component = chain[index]
        if not isinstance(component, Plain):
            new_chain.append(component)
            index += 1
            continue
        start = index
        body_parts = []
        while index < len(chain) and isinstance(chain[index], Plain):
            body_parts.append(chain[index].text)
            index += 1
        raw_body = "".join(body_parts)
        if seg_policy is not None:
            planned = _parts(seg_policy, raw_body)
        else:
            clean_parts, _ = controlled_parts(raw_body)
            joined_clean = "".join(clean_parts)
            planned = (joined_clean,) if clean_parts else (
                () if control_token_ranges(raw_body) or not raw_body else (raw_body,))
        if len(planned) > 1:
            has_multi_segments = True
        if not planned:
            continue
        clean_joined = "".join(planned)
        runs.append({"clean_joined": clean_joined, "parts": tuple(planned)})
        if clean_joined == raw_body:
            new_chain.extend(chain[start:index])
        else:
            new_chain.append(Plain(clean_joined))

    will_voice = _will_voice_deliver(core, prepared)
    owns_delivery = bool((seg_policy is not None and seg_policy.enabled and has_multi_segments) or will_voice)

    if not owns_delivery:
        tool_names = core.get("_native_tool_names", ())
        sanitized_chain = []
        for part in new_chain:
            if isinstance(part, Plain):
                cleaned_text = redact_text(strip_plaintext_tool_calls(part.text, tool_names)[0])
                if cleaned_text or not part.text:
                    sanitized_chain.append(part if cleaned_text == part.text else Plain(cleaned_text))
            else:
                sanitized_chain.append(part)
        chain[:] = sanitized_chain
        return

    chain[:] = new_chain
    if getattr(result, "use_t2i_", None) is None:
        result.use_t2i_ = False
    core["_prepared_decoration"] = {
        "result": result,
        "plain_texts": tuple(part.text for part in chain if isinstance(part, Plain)),
        "owns_delivery": True,
        "source_has_node": any(isinstance(part, Node) for part in chain),
        "runs": tuple(runs),
        "unwrapped": False,
    }


def unwrap_core_forward_node(event, message):
    """Recover the original chain if ResultDecorateStage wrapped a Companion reply into a forward Node."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    dec = core.get("_prepared_decoration") if isinstance(core, dict) else None
    if not isinstance(dec, dict) or not dec.get("owns_delivery") or dec.get("source_has_node") or dec.get("unwrapped"):
        return message
    getter = getattr(event, "get_platform_name", None)
    if not callable(getter) or getter() != "aiocqhttp":
        return message
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)) or len(chain) != 1 or not isinstance(chain[0], Node):
        return message
    node = chain[0]
    content = getattr(node, "content", None)
    if not isinstance(content, list) or not any(isinstance(part, Plain) for part in content):
        return message
    if getattr(node, "name", None) != "AstrBot":
        return message
    self_id_getter = getattr(event, "get_self_id", None)
    expected_uin = self_id_getter() if callable(self_id_getter) else None
    node_uin = getattr(node, "uin", None)
    if expected_uin is not None:
        if node_uin is not None and str(node_uin) != str(expected_uin):
            return message
    elif node_uin is not None:
        return message
    dec["unwrapped"] = True
    derive = getattr(message, "derive", None)
    return derive(list(content)) if callable(derive) else MessageChain(chain=list(content))


def _already_native_parts(event, message):
    getter = getattr(event, "get_result", None)
    result = getter() if callable(getter) else None
    source = getattr(result, "chain", ())
    chain = getattr(message, "chain", ())
    # RespondStage sends one component at a time when native segmentation is
    # enabled. Keep those completed native boundaries and their native delay.
    return (sum(isinstance(part, Plain) for part in source) > 1
            and sum(isinstance(part, Plain) for part in chain) == 1
            and any(part is plain for part in chain for plain in source if isinstance(plain, Plain)))


def _contains_native_voice(event, message):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(core, dict) and core.get("_native_voice_response"):
        return True
    contains_voice = isinstance(message, Record)
    chain = getattr(message, "chain", ())
    contains_voice = contains_voice or (
        isinstance(chain, (list, tuple)) and any(isinstance(part, Record) for part in chain))
    if not contains_voice:
        getter = getattr(event, "get_result", None)
        result = getter() if callable(getter) else None
        source = getattr(result, "chain", ())
        contains_voice = isinstance(source, (list, tuple)) and any(
            isinstance(part, Record) for part in source)
    if contains_voice and isinstance(core, dict):
        core["_native_voice_response"] = True
    return contains_voice


def prepare_core_reply_segmentation(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    if (not isinstance(prepared, Mapping) or prepared.get("channel") not in {"group", "private"}
            or (prepared.get("channel") == "group"
                and (prepared.get("group_admission") or {}).get("reason") == "repeat")):
        return
    settings = prepared.get("segmentation_settings")
    if not isinstance(settings, Mapping):
        return
    try:
        policy = SegmentationPolicy(**{field.name: settings[field.name] for field in fields(SegmentationPolicy)
                                       if field.name in settings})
    except (TypeError, ValueError):
        logger.warning("[CompanionV2] 回复分段配置无效，保留宿主发送")
        return
    state = {"policy": policy, "settings": dict(settings), "prepared": prepared,
             "send": event.send, "stopped": False, "sent": 0}

    async def send_parts(message, *args, **kwargs):
        if not core.get("delivery_guard_active") or getattr(message, "type", None) in {
                "tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
            return await state["send"](message, *args, **kwargs)
        if state["stopped"]:
            return None
        message = unwrap_core_forward_node(event, message)
        from .core_reply_reaction_delivery import capture_core_reaction_intent
        capture_core_reaction_intent(event, message)
        from .core_reply_voice import capture_core_voice_message
        message = capture_core_voice_message(event, message)
        from .core_reply_idempotency import mark_native_tool_checked, should_drop_native_tool_response
        already_native_parts = _already_native_parts(event, message)
        if not already_native_parts and should_drop_native_tool_response(event, message):
            return None
        if prepared.get("channel") == "private" and is_core_reply_stream(event):
            try:
                return await state["send"](mark_native_tool_checked(message), *args, **kwargs)
            except BaseException:
                state["stopped"] = True
                raise
        if _contains_native_voice(event, message):
            try:
                return await state["send"](mark_native_tool_checked(message), *args, **kwargs)
            except BaseException:
                state["stopped"] = True
                raise
        if getattr(message, MARKED, False):
            try:
                return await state["send"](mark_native_tool_checked(message), *args, **kwargs)
            except BaseException:
                state["stopped"] = True
                raise
        if already_native_parts:
            source = getattr(getattr(event, "get_result", lambda: None)(), "chain", ())
            count = max(1, sum(isinstance(part, Plain) for part in source))
            core["voice_segment_index"], core["voice_segment_count"] = state["sent"], count
            core["outbound_first_text_segment"] = 0
            attempts = core["send_attempts"]
            try:
                result = await state["send"](mark_native_tool_checked(message), *args, **kwargs)
                if core["send_attempts"] > attempts:
                    state["sent"] += 1
                return result
            except BaseException:
                state["stopped"] = True
                raise
            finally:
                core.pop("voice_segment_index", None)
                core.pop("voice_segment_count", None)
                core.pop("outbound_first_text_segment", None)
        result = None
        presentation = core_reply_presentation(event)
        dec = core.get("_prepared_decoration")
        pre_split = dec.get("runs") if isinstance(dec, dict) else None
        parts = split_message(message, policy, presentation,
                              scope_kind=prepared.get("channel", "private"),
                              tool_names=core.get("_native_tool_names", ()),
                              pre_split=pre_split)
        if not parts and presentation is not None:
            note_removed_text(event)
        if core.get("_voice_authored") and presentation is not None:
            raw_parts = split_message(message, policy, pre_split=pre_split)
            if len(raw_parts) == len(parts):
                for raw, part in zip(raw_parts, parts):
                    if hasattr(part, "chain"):
                        object.__setattr__(part, "_companion_v2_voice_source", sent_core_text(raw))
        first_text_segment = next((index for index, part in enumerate(parts)
                                   if sent_core_text(part).strip()), 0)
        for index, part in enumerate(parts):
            core["voice_segment_index"], core["voice_segment_count"] = index, len(parts)
            core["outbound_first_text_segment"] = first_text_segment
            try:
                result = await _send_part(event, state, mark_native_tool_checked(part), args, kwargs)
            finally:
                core.pop("voice_segment_index", None)
                core.pop("voice_segment_count", None)
                core.pop("outbound_first_text_segment", None)
        return result

    state["wrapper"] = send_parts
    core["_reply_segmentation"] = state
    if prepared.get("channel") == "group":
        core["_group_segmentation"] = state
    event.send = send_parts


def prepare_core_reply_streaming_boundary(event):
    """Give a segmented private reply one complete body to plan before sending."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    state = (core.get("_reply_segmentation") if isinstance(core, dict) else None)
    if (not state or state["prepared"].get("channel") != "private"
            or not state["policy"].enabled):
        return
    setter = getattr(event, "set_extra", None)
    if not callable(setter):
        return
    getter = getattr(event, "get_extra", None)
    previous = getter("enable_streaming") if callable(getter) else None
    if previous is not False:
        setter("enable_streaming", False)
        logger.debug("[CompanionV2] 私聊分段预留完整回复 event_ref=%s previous_streaming=%s",
                     state["prepared"].get("event_ref", "unknown"), previous)


def _group_under_concurrent_pressure(event):
    from .core_reply_tracking import CORE_REPLY_TRACKING_EXTRA
    tracking = event.get_extra(CORE_REPLY_TRACKING_EXTRA)
    if not isinstance(tracking, dict):
        return False
    admission = tracking.get("admission")
    checker = getattr(admission, "has_concurrent_or_newer_core_turn", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(tracking.get("context"), tracking.get("event_id")))
    except Exception:
        return False


async def _send_part(event, state, part, args=(), kwargs=None):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if state["stopped"] or core.get("group_participation", {}).get("mode") == "skip":
        return None
    try:
        if state["sent"] and not _group_under_concurrent_pressure(event):
            gap = part_gap_seconds(state["settings"], sent_core_text(part),
                                   (core["prepared"].get("event_id"), state["sent"]))
            if gap:
                await asyncio.sleep(gap)
        attempts = core["send_attempts"]
        sent_before = state["sent"]
        result = await state["send"](part, *args, **(kwargs or {}))
    except BaseException:
        # RespondStage catches send failures per component. A later component
        # must not continue after authorization denial or an unknown send.
        state["stopped"] = True
        from .core_reply import finish_core_reply_presence
        finish_core_reply_presence(event, force=True)
        raise
    if core["send_attempts"] > attempts:
        state["sent"] += 1
    elif state["sent"] == sent_before:
        # Group streaming can send through the frozen transport without the
        # outer tracking wrapper, so send_attempts may not move.  A confirmed
        # return still advances the part counter and preserves the gap.
        state["sent"] += 1
    return result


def activate_core_reply_segmentation(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    state = (core.get("_reply_segmentation") or core.get("_group_segmentation")
             if isinstance(core, dict) else None)
    if state:
        state["sent"] = 0
        event.send = state["wrapper"]


def uses_managed_group_stream(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    state = (core.get("_reply_segmentation") or core.get("_group_segmentation")
             if isinstance(core, dict) else None)
    getter = getattr(event, "get_platform_name", None)
    platform = getter() if callable(getter) else getattr(getattr(event, "platform_meta", None), "name", None)
    return bool(state and state["prepared"].get("channel") == "group" and platform == "aiocqhttp")


async def send_group_stream(event, generator):
    """QQ has no native text stream: deliver complete native MessageChains."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    state = core["_group_segmentation"]
    policy, buffered = state["policy"], None
    # Core streaming can bypass decorators; arm the outer splitter now so its
    # inner tracking/authorization wrappers observe each actual native send.
    activate_core_reply_segmentation(event)
    if policy.streaming_mode != "incremental" or not policy.enabled:
        async for chunk in generator:
            if getattr(chunk, "type", None) in {"tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
                continue
            if isinstance(chunk, str):
                chunk = MessageChain(chain=[Plain(chunk)])
            if not isinstance(chunk, MessageChain):
                continue
            if buffered is None:
                buffered = chunk.derive(list(chunk.chain))
            else:
                buffered.chain.extend(chunk.chain)
        if buffered is not None:
            return await event.send(buffered)
        return None
    text = TextSegmentStream(policy)
    text_sequence = 0

    async def publish(parts):
        nonlocal text_sequence
        for body in parts:
            chain = MessageChain(chain=[Plain(body)])
            setattr(chain, MARKED, True)
            core["voice_segment_index"], core["voice_segment_count"] = text_sequence, 1
            core["outbound_first_text_segment"] = 0
            sent_before = state["sent"]
            try:
                await _send_part(event, state, chain)
            finally:
                core.pop("voice_segment_index", None)
                core.pop("voice_segment_count", None)
                core.pop("outbound_first_text_segment", None)
            if state["sent"] > sent_before:
                text_sequence += 1

    async for chunk in generator:
        if getattr(chunk, "type", None) in {"tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
            continue
        components = [Plain(chunk)] if isinstance(chunk, str) else getattr(chunk, "chain", ())
        for component in components:
            if isinstance(component, Plain):
                await publish(text.feed(component.text))
            else:
                await publish(text.finish())
                text = TextSegmentStream(policy)
                await _send_part(event, state, MessageChain(chain=[component]))
    await publish(text.finish())
