"""Private, bounded cache-signature collection and comparison."""

from __future__ import annotations

import json

from companion_v2.execution.models import digest, encode

from .models import CacheSignature


_CHANGE_SEVERITY = {
    "provider_change": "high",
    "model_change": "high",
    "system_prompt_change": "high",
    "tool_definition_change": "high",
    "context_reset": "high",
    "context_prefix_change": "high",
    "system_order_drift": "medium",
    "tool_order_drift": "medium",
    "context_order_drift": "medium",
    "context_tail_trim": "medium",
    "context_availability_change": "low",
}
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def event_severity(changes):
    """Return the strongest structural signal in a multi-change event."""
    return max((_CHANGE_SEVERITY.get(change, "low") for change in changes),
               key=_SEVERITY_RANK.__getitem__, default="low")


def _estimated_text_tokens(text):
    """Use the cost-control plugin's CJK/ASCII heuristic for a rough text estimate."""
    if not text:
        return 0
    cjk = sum("一" <= char <= "鿿" or "　" <= char <= "〿" for char in text)
    return max(1, int(cjk * 0.6 + (len(text) - cjk) * 0.25))


def estimated_text_tokens(text):
    """Expose the one token estimate the gateway already uses for its signatures.

    Callers that only need a loggable number (the expression-learning owner, for
    example) reuse this instead of inventing a second formula. The estimate is a
    CJK/ASCII heuristic, not reported provider usage, so it is always a rough
    input estimate and never a billing figure.
    """
    return _estimated_text_tokens(text)


def _text_parts(content):
    if type(content) is str:
        return content
    if type(content) is list:
        parts = []
        for item in content:
            if type(item) is not dict:
                continue
            key = "text" if item.get("type") == "text" else "think" if item.get("type") == "think" else None
            text = item.get(key) if key else None
            if type(text) is str:
                parts.append(text)
        return "".join(parts)
    return ""


def _content_text_tokens(content):
    if type(content) is str:
        return _estimated_text_tokens(content)
    if type(content) is list:
        total = 0
        for item in content:
            if type(item) is not dict:
                continue
            key = "text" if item.get("type") == "text" else "think" if item.get("type") == "think" else None
            text = item.get(key) if key else None
            if type(text) is str:
                total += _estimated_text_tokens(text)
        return total
    return 0


def _message_estimate_tokens(message):
    total = _content_text_tokens(message.get("content"))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        total += _estimated_text_tokens(json.dumps(tool_calls, ensure_ascii=False))
    return total


def _message_text_size(message):
    text = _text_parts(message.get("content"))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        text += json.dumps(tool_calls, ensure_ascii=False)
    return len(text), len(text.encode("utf-8", errors="replace"))


def _context_document(messages):
    for message in messages:
        if type(message) is not dict or message.get("role") != "user":
            continue
        text = _text_parts(message.get("content"))
        try:
            document = json.loads(text)
        except (TypeError, ValueError):
            continue
        if type(document) is dict and type(document.get("materials")) is list:
            return document
    return None


def _payload_shape(messages):
    message_count = system_count = system_chars = system_bytes = 0
    image_count = audio_count = video_count = 0
    for message in messages:
        if type(message) is not dict:
            continue
        message_count += 1
        if message.get("role") == "system":
            text = _text_parts(message.get("content"))
            system_count += 1
            system_chars += len(text)
            system_bytes += len(text.encode("utf-8", errors="replace"))
        content = message.get("content")
        if type(content) is not list:
            continue
        for item in content:
            if type(item) is not dict:
                continue
            item_type = item.get("type")
            if item_type in ("image", "image_url", "input_image"):
                image_count += 1
            elif item_type in ("audio", "input_audio", "input_audio_buffer"):
                audio_count += 1
            elif item_type in ("video", "video_data", "input_video"):
                video_count += 1
    return (message_count, system_count, system_chars, system_bytes,
            image_count, audio_count, video_count)


def signature_for_payload(model, messages, tools) -> CacheSignature:
    """Fingerprint the prepared provider request without retaining prompt bodies."""
    messages = list(messages) if type(messages) in (list, tuple) else []
    tools = list(tools) if type(tools) in (list, tuple) else []
    systems = [digest(message.get("content")) for message in messages
               if type(message) is dict and message.get("role") == "system"]
    system_est_tokens = sum(_content_text_tokens(message.get("content"))
                            for message in messages
                            if type(message) is dict and message.get("role") == "system")
    message_chars = message_bytes = raw_message_est_tokens = 0
    for message in messages:
        if type(message) is not dict or message.get("role") == "system":
            continue
        chars, byte_count = _message_text_size(message)
        message_chars += chars
        message_bytes += byte_count
        raw_message_est_tokens += _message_estimate_tokens(message)
    tool_parts = [digest(tool) for tool in tools]
    tool_json = json.dumps(tools, ensure_ascii=False) if tools else ""
    tool_est_tokens = _estimated_text_tokens(tool_json) if tools else 0
    document = _context_document(messages)
    materials = document.get("materials") if document is not None else None
    context_hashes = []
    context_chars = 0
    context_bytes = 0
    context_est_tokens = 0
    if materials is not None:
        for material in materials:
            if type(material) is not dict or material.get("content_class") == "current_user_request":
                continue
            purpose = material.get("purpose")
            if purpose == "current_source" or (type(purpose) is str and purpose.startswith("merged_input:")):
                continue
            text = material.get("text")
            if type(text) is not str:
                text = ""
            context_hashes.append(digest([material.get("owner"), purpose,
                                          material.get("content_class"), text]))
            context_chars += len(text)
            context_bytes += len(text.encode("utf-8", errors="replace"))
            context_est_tokens += _estimated_text_tokens(text)
    deferred = document.get("deferred", []) if document is not None else []
    unavailable = document.get("unavailable", []) if document is not None else []
    availability_hash = digest({"deferred": deferred, "unavailable": unavailable})

    message_count, system_count, system_chars, system_bytes, image_count, audio_count, video_count = (
        _payload_shape(messages))
    tool_bytes = len(tool_json.encode("utf-8", errors="replace")) if tools else 0
    # The materials are serialized inside a user message. Split their estimated
    # text back out so the four displayed request components do not overlap.
    message_est_tokens = max(0, raw_message_est_tokens - context_est_tokens)
    request_est_tokens = system_est_tokens + message_est_tokens + context_est_tokens + tool_est_tokens
    return CacheSignature(
        model_hash=digest(model if type(model) is str else ""),
        system_hash=digest(systems), system_parts=tuple(systems[:32]),
        tools_hash=digest(tool_parts), tool_parts=tuple(tool_parts[:64]),
        context_sequence_hash=digest(context_hashes),
        context_multiset_hash=digest(sorted(context_hashes)),
        context_hashes=tuple(context_hashes[:256]),
        context_material_count=len(context_hashes), context_chars=context_chars,
        context_bytes=context_bytes, context_observed=materials is not None,
        estimates_observed=True, context_est_tokens=context_est_tokens,
        system_est_tokens=system_est_tokens, tool_est_tokens=tool_est_tokens,
        message_estimates_observed=True, message_est_tokens=message_est_tokens,
        message_chars=message_chars, message_bytes=message_bytes,
        request_estimate_observed=True, request_est_tokens=request_est_tokens,
        context_availability_observed=document is not None,
        context_availability_hash=availability_hash,
        deferred_material_count=len(deferred) if type(deferred) is list else 0,
        unavailable_material_count=len(unavailable) if type(unavailable) is list else 0,
        request_stats_observed=True, message_count=message_count,
        system_message_count=system_count, system_chars=system_chars, system_bytes=system_bytes,
        tool_count=len(tools), tool_bytes=tool_bytes,
        image_count=image_count, audio_count=audio_count, video_count=video_count,
    )


def checked_signature(raw):
    """Read one persisted signature defensively; malformed rows are ignored."""
    try:
        value = json.loads(raw)
        if (type(value) is not dict or type(value.get("version")) is not int
                or value["version"] not in (1, 2, 3, 4, 5)):
            return None
        context_hashes = tuple(value["context_hashes"])
        version = value["version"]
        message_est_tokens = value.get("message_est_tokens", 0)
        if version == 4 and value.get("message_estimates_observed") is True:
            message_est_tokens = max(0, message_est_tokens - value.get("context_est_tokens", 0))
        return CacheSignature(
            model_hash=value["model_hash"], system_hash=value["system_hash"],
            system_parts=tuple(value["system_parts"]), tools_hash=value["tools_hash"],
            tool_parts=tuple(value["tool_parts"]),
            context_sequence_hash=value.get("context_sequence_hash", digest(context_hashes)),
            context_multiset_hash=value.get("context_multiset_hash", digest(sorted(context_hashes))),
            context_hashes=context_hashes,
            context_material_count=value.get("context_material_count", len(context_hashes)),
            context_chars=value.get("context_chars", 0), context_bytes=value["context_bytes"],
            context_observed=value["context_observed"],
            estimates_observed=version >= 3 and value.get("estimates_observed") is True,
            context_est_tokens=value.get("context_est_tokens", 0),
            system_est_tokens=value.get("system_est_tokens", 0),
            tool_est_tokens=value.get("tool_est_tokens", 0),
            message_estimates_observed=version >= 4 and value.get("message_estimates_observed") is True,
            message_est_tokens=message_est_tokens,
            message_chars=value.get("message_chars", 0), message_bytes=value.get("message_bytes", 0),
            request_estimate_observed=version >= 4 and value.get("request_estimate_observed") is True,
            request_est_tokens=value.get("request_est_tokens", 0),
            context_availability_observed=version >= 4 and value.get("context_availability_observed") is True,
            context_availability_hash=value.get("context_availability_hash", digest({"deferred": [], "unavailable": []})),
            deferred_material_count=value.get("deferred_material_count", 0),
            unavailable_material_count=value.get("unavailable_material_count", 0),
            request_stats_observed=version >= 2 and value.get("request_stats_observed") is True,
            message_count=value.get("message_count", 0), system_message_count=value.get("system_message_count", 0),
            system_chars=value.get("system_chars", 0), system_bytes=value.get("system_bytes", 0),
            tool_count=value.get("tool_count", 0), tool_bytes=value.get("tool_bytes", 0),
            image_count=value.get("image_count", 0), audio_count=value.get("audio_count", 0),
            video_count=value.get("video_count", 0),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def signature_snapshot(signature: CacheSignature) -> dict:
    """Return compact statistics and short fingerprints without prompt bodies."""
    has_context = signature.context_observed
    has_request_stats = signature.request_stats_observed
    return {
        "model_fingerprint": signature.model_hash[:8],
        "system_fingerprint": signature.system_hash[:8],
        "tools_fingerprint": signature.tools_hash[:8],
        "context_fingerprint": signature.context_sequence_hash[:8],
        "availability_fingerprint": signature.context_availability_hash[:8],
        "context_materials": signature.context_material_count if has_context else None,
        "context_chars": signature.context_chars if has_context and has_request_stats else None,
        "context_bytes": signature.context_bytes if has_context else None,
        "context_est_tokens": signature.context_est_tokens if has_context and signature.estimates_observed else None,
        "messages": signature.message_count if has_request_stats else None,
        "system_messages": signature.system_message_count if has_request_stats else None,
        "system_chars": signature.system_chars if has_request_stats else None,
        "system_bytes": signature.system_bytes if has_request_stats else None,
        "system_est_tokens": signature.system_est_tokens if signature.estimates_observed else None,
        "tools": signature.tool_count if has_request_stats else None,
        "tool_bytes": signature.tool_bytes if has_request_stats else None,
        "tool_est_tokens": signature.tool_est_tokens if signature.estimates_observed else None,
        "message_est_tokens": signature.message_est_tokens if signature.message_estimates_observed else None,
        "message_chars": signature.message_chars if signature.message_estimates_observed else None,
        "message_bytes": signature.message_bytes if signature.message_estimates_observed else None,
        "request_est_tokens": signature.request_est_tokens if signature.request_estimate_observed else None,
        "deferred_materials": signature.deferred_material_count if signature.context_availability_observed else None,
        "unavailable_materials": signature.unavailable_material_count if signature.context_availability_observed else None,
        "images": signature.image_count if has_request_stats else None,
        "audio": signature.audio_count if has_request_stats else None,
        "video": signature.video_count if has_request_stats else None,
    }


def signature_changes(current: CacheSignature, previous: CacheSignature) -> tuple[str, ...]:
    """Classify observed prefix changes without claiming they caused a cache miss."""
    changes = []
    if current.model_hash != previous.model_hash:
        changes.append("model_change")
    if current.system_hash != previous.system_hash:
        if (current.system_parts != previous.system_parts
                and sorted(current.system_parts) == sorted(previous.system_parts)):
            changes.append("system_order_drift")
        else:
            changes.append("system_prompt_change")
    if current.tools_hash != previous.tools_hash:
        if (current.tool_parts != previous.tool_parts
                and sorted(current.tool_parts) == sorted(previous.tool_parts)):
            changes.append("tool_order_drift")
        else:
            changes.append("tool_definition_change")
    if (current.context_availability_observed and previous.context_availability_observed
            and current.context_availability_hash != previous.context_availability_hash):
        changes.append("context_availability_change")
    if current.context_observed and previous.context_observed:
        old_count, new_count = previous.context_material_count, current.context_material_count
        reset = ((old_count > 0 and new_count == 0)
                 or (old_count >= 4 and new_count * 2 < old_count)
                 or (previous.context_bytes >= 4096 and current.context_bytes * 2 < previous.context_bytes))
        if reset:
            changes.append("context_reset")
        elif current.context_sequence_hash != previous.context_sequence_hash:
            prefix_stable = (
                old_count <= len(previous.context_hashes)
                and new_count >= old_count
                and current.context_hashes[:old_count] == previous.context_hashes
            )
            tail_trim = (
                new_count < old_count
                and new_count <= len(current.context_hashes)
                and new_count <= len(previous.context_hashes)
                and current.context_hashes == previous.context_hashes[:new_count]
            )
            if tail_trim:
                changes.append("context_tail_trim")
            elif not prefix_stable:
                if (new_count == old_count
                        and current.context_multiset_hash == previous.context_multiset_hash):
                    changes.append("context_order_drift")
                else:
                    changes.append("context_prefix_change")
    return tuple(changes)
