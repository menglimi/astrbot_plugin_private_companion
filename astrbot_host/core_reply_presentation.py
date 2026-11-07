"""Apply V2 text rules at native reply delivery boundaries."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re

from astrbot.core.message.components import At, AtAll, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain

from companion_v2.conversation.reply_preparation import trim_blank_edges
from companion_v2.conversation.segment_control import controlled_parts
from companion_v2.conversation.text_processing import (
    OUTPUT_TEXT_FIELDS,
    InternalExpressionProtocolFilter,
    ReplyTextPolicy,
    has_tool_call_protocol,
    strip_internal_expression_protocol,
)
from companion_v2.sources.privacy import redact_text
from .core_reply import CORE_REPLY_CONTEXT_EXTRA


logger = logging.getLogger(__name__)
MARKED = "_companion_v2_text_presented"
CONTROL_TYPES = frozenset(("tool_call", "tool_result", "agent_stats", "reasoning", "break"))
_PRESENTED_STREAM = ContextVar("companion_v2_presented_stream", default=None)


class PresentedText(str):
    """String-compatible final parts used by hosts accepting text directly."""


def core_reply_presentation(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    if not isinstance(prepared, Mapping) or (prepared.get("group_admission") or {}).get("reason") == "repeat":
        return None
    if "_text_presentation" not in core:
        settings = prepared.get("presentation_settings")
        policy = None
        if isinstance(settings, Mapping):
            try:
                policy = ReplyTextPolicy(**{key: settings[key] for key in OUTPUT_TEXT_FIELDS if key in settings})
            except (TypeError, ValueError):
                logger.warning("[CompanionV2] 核心回复文字清理配置无效，保留宿主正文")
        if policy is None:
            policy = ReplyTextPolicy()
        core["_text_presentation"] = policy
    return core["_text_presentation"]


def core_reply_scope_kind(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    return prepared.get("channel", "private") if isinstance(prepared, Mapping) else "private"


def _plain_text(message):
    if isinstance(message, str):
        return message
    chain = getattr(message, "chain", None)
    if isinstance(chain, (list, tuple)):
        return "".join(part.text for part in chain if isinstance(part, Plain))
    return ""


def capture_core_reply_tool_names(event, request):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return
    tool_set = getattr(request, "func_tool", None)
    tools = getattr(tool_set, "tools", ()) or ()
    core["_native_tool_names"] = tuple(sorted({
        name for name in (getattr(tool, "name", None) for tool in tools)
        if isinstance(name, str) and name.strip() and len(name) <= 128
    }))


def _tool_call_name(value, allowed_names):
    if not isinstance(value, dict):
        return ""
    function = value.get("function")
    source = function if isinstance(function, dict) else value
    name = source.get("name") or value.get("tool_name")
    if not isinstance(name, str) or name not in allowed_names:
        return ""
    parameters = source.get("parameters", source.get("arguments", source.get("args")))
    if parameters is None:
        parameters = value.get("parameters", value.get("arguments", value.get("args", {})))
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except (ValueError, TypeError):
            return ""
    return name if isinstance(parameters, dict) else ""


def strip_plaintext_tool_calls(text, allowed_names):
    if not isinstance(text, str) or not text or not allowed_names or "{" not in text:
        return text, ()
    decoder = json.JSONDecoder()
    matches, names, cursor = [], [], 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except (ValueError, TypeError):
            cursor = start + 1
            continue
        name = _tool_call_name(value, allowed_names)
        if not name:
            cursor = start + 1
            continue
        end = start + consumed
        matches.append((start, end))
        names.append(name)
        cursor = end
    if not matches:
        return text, ()
    chunks, cursor = [], 0
    for start, end in matches:
        chunks.append(text[cursor:start])
        cursor = end
    chunks.append(text[cursor:])
    cleaned = "".join(chunks)
    cleaned = re.sub(r"</?(?:tool_call|function_call)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?im)^[ \t]*```(?:json)?[ \t]*$", "", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, tuple(names)


def present_text_parts(text, presentation, split=None, *, tool_names=()):
    """Apply V2 output guards and formatting before text reaches AstrBot's sender."""
    text = strip_internal_expression_protocol(text)
    text, _removed_tool_calls = strip_plaintext_tool_calls(text, tool_names)
    if presentation is not None:
        text, leaked = presentation.framework_guard(text)
        if leaked:
            return (text,)
        text = presentation.replace_text(text)
    if split:
        parts = split(text)
    else:
        parts, explicit = controlled_parts(text)
        if explicit and len(parts) > 1:
            parts = ("\n".join(parts),)
    cleaned = presentation.clean_parts(parts) if presentation is not None else tuple(parts)
    return tuple(trim_blank_edges(redact_text(part)) for part in cleaned if part.strip())


def mark_presented(message, presentation):
    if isinstance(message, str):
        message = PresentedText(message)
    setattr(message, MARKED, presentation)
    return message


def has_reply_body(chain):
    return any(bool(component.text.strip()) if isinstance(component, Plain)
               else not isinstance(component, (At, AtAll, Reply)) for component in chain)


def strip_internal_expression_message(message):
    """Remove private reaction metadata even when normal presentation is skipped."""
    if isinstance(message, str):
        cleaned = strip_internal_expression_protocol(message)
        if cleaned == message:
            return message
        return cleaned if cleaned.strip() else None
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return message
    output, changed, index = [], False, 0
    while index < len(chain):
        component = chain[index]
        if not isinstance(component, Plain):
            output.append(component)
            index += 1
            continue
        start, text = index, []
        while index < len(chain) and isinstance(chain[index], Plain):
            text.append(chain[index].text)
            index += 1
        source = "".join(text)
        cleaned = strip_internal_expression_protocol(source)
        changed = changed or cleaned != source
        if cleaned.strip():
            if cleaned == source:
                output.extend(chain[start:index])
            else:
                output.append(Plain(cleaned))
    if not changed:
        return message
    if not has_reply_body(output):
        return None
    derive = getattr(message, "derive", None)
    return derive(output) if callable(derive) else MessageChain(chain=output)


def note_removed_text(event):
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(core, dict):
        core["_text_presentation_removed"] = True


def _note_unparsed_tool_call(event, text):
    """One operator-facing line when a provider returns a call as text."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict) or not has_tool_call_protocol(text):
        return
    prepared = core.get("prepared")
    event_ref = prepared.get("event_ref", "unknown") if isinstance(prepared, Mapping) else "unknown"
    logger.warning("[CompanionV2] 核心回复只有未解析的工具调用文本，已拦截 event_ref=%s；"
                   "请检查 Provider 是否返回结构化 tool_calls", event_ref)


def present_core_reply_message(event, message, *, from_stream=False):
    if getattr(message, "type", None) in CONTROL_TYPES:
        return message
    raw_text = _plain_text(message)
    message = strip_internal_expression_message(message)
    if message is None:
        # A reply that carried only protocol is an intentional silence, not a
        # failed delivery: record the removal and tell the operator why.
        if has_tool_call_protocol(raw_text):
            _note_unparsed_tool_call(event, raw_text)
            note_removed_text(event)
        return None
    presentation = core_reply_presentation(event)
    if (presentation is None or getattr(message, MARKED, None) is presentation
            or (from_stream and _PRESENTED_STREAM.get() is presentation)):
        return message
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    tool_names = core.get("_native_tool_names", ()) if isinstance(core, dict) else ()
    source_text, _removed_tool_calls = strip_plaintext_tool_calls(_plain_text(message), tool_names)
    if presentation.forbidden_word_guard(source_text, scope_kind=core_reply_scope_kind(event)):
        note_removed_text(event)
        prepared = (event.get_extra(CORE_REPLY_CONTEXT_EXTRA) or {}).get("prepared", {})
        logger.warning("[CompanionV2] 核心回复命中发送屏蔽词，已拦截 event_ref=%s",
                       prepared.get("event_ref", "unknown"))
        return None
    if isinstance(message, str):
        parts = present_text_parts(message, presentation, tool_names=tool_names)
        if parts:
            return mark_presented("".join(parts), presentation)
        _note_unparsed_tool_call(event, raw_text)
        note_removed_text(event)
        return None
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)) or not any(isinstance(part, Plain) for part in chain):
        return message
    # Adjacent SDK chunks are one body. Media and mention components keep
    # their order and identity; the original result/history is never mutated.
    result, index = [], 0
    while index < len(chain):
        component = chain[index]
        if not isinstance(component, Plain):
            result.append(component)
            index += 1
            continue
        text = ""
        while index < len(chain) and isinstance(chain[index], Plain):
            text += chain[index].text
            index += 1
        result.extend(Plain(part) for part in present_text_parts(text, presentation, tool_names=tool_names))
    if not has_reply_body(result):
        _note_unparsed_tool_call(event, raw_text)
        note_removed_text(event)
        return None
    derive = getattr(message, "derive", None)
    output = derive(result) if callable(derive) else MessageChain(chain=result)
    return mark_presented(output, presentation)


@contextmanager
def presented_stream(presentation):
    """Adapters may reconstruct chains and send them through event.send again."""
    token = _PRESENTED_STREAM.set(presentation)
    try:
        yield
    finally:
        _PRESENTED_STREAM.reset(token)


async def present_core_reply_stream(event, generator):
    """Native streams need complete text for regex, quotes and cross-chunk rules.

    QQ's bubble-based fallback is processed at event.send instead, preserving
    incremental delivery. Other adapters receive completed text runs. Agent
    metadata and nontext components retain their own native boundaries.
    """
    buffered = None
    iterator = generator.__aiter__()
    while True:
        # Upstream tools still produce raw messages and must use their own
        # presentation; only the downstream native adapter sees prepared text.
        with presented_stream(None):
            try:
                chunk = await anext(iterator)
            except StopAsyncIteration:
                break
        if getattr(chunk, "type", None) in CONTROL_TYPES:
            if buffered is not None:
                output = present_core_reply_message(event, buffered)
                if output is not None:
                    yield output
                buffered = None
            yield chunk
            continue
        if isinstance(chunk, str):
            chunk = MessageChain(chain=[Plain(chunk)])
        if not isinstance(chunk, MessageChain):
            yield chunk
            continue
        if buffered is None:
            buffered = chunk.derive([])
        for component in chunk.chain:
            if isinstance(component, Plain):
                buffered.chain.append(component)
            else:
                if buffered.chain:
                    output = present_core_reply_message(event, buffered)
                    if output is not None:
                        yield output
                    buffered = chunk.derive([])
                yield chunk.derive([component])
    if buffered is not None and buffered.chain:
        output = present_core_reply_message(event, buffered)
        if output is not None:
            yield output


async def strip_internal_expression_stream(generator):
    """Remove reaction metadata before native stream adapters can split it."""
    protocol = InternalExpressionProtocolFilter()
    async for chunk in generator:
        if getattr(chunk, "type", None) in CONTROL_TYPES:
            yield chunk
            continue
        if isinstance(chunk, str):
            visible = protocol.feed(chunk)
            if visible:
                yield visible
            continue
        chain = getattr(chunk, "chain", None)
        if not isinstance(chain, (list, tuple)):
            yield chunk
            continue
        output, changed = [], False
        for component in chain:
            if not isinstance(component, Plain):
                output.append(component)
                continue
            visible = protocol.feed(component.text)
            if visible:
                output.append(component if visible == component.text else Plain(visible))
            changed = changed or visible != component.text
        if output:
            if changed:
                derive = getattr(chunk, "derive", None)
                chunk = derive(output) if callable(derive) else MessageChain(chain=output)
            yield chunk
    trailing = protocol.finish()
    if trailing:
        yield trailing
