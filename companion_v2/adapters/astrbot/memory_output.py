"""Capture private memory replies before JSON decoding or host text repair."""

import json

from companion_v2.model_gateway.models import CapturedModelOutput

from .response_diagnostics import native_finish_reason


# Translate recognized provider endings; missing metadata retains the previous
# default, while an explicit but unknown ending is not a confirmed stop.
_CAPTURED_FINISH_REASONS = {
    "stop": "stop",
    "length": "length",
    "content_filter": "refusal",
    "tool_calls": "invalid",
    "function_call": "invalid",
}


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _structured_format(request):
    return {
        "name": "content_authoring" if request.output_format == "content_authoring" else "memory_reflection",
        "strict": True,
        "schema": json.loads(request.response_schema_json),
    }


def explicit_refusal(completion):
    choices = getattr(completion, "choices", ())
    if isinstance(choices, (list, tuple)) and len(choices) == 1:
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None or getattr(message, "tool_calls", None) or getattr(message, "function_call", None):
            return False
        return (bool(getattr(message, "refusal", None))
                or getattr(choice, "finish_reason", None) == "content_filter")
    if native_finish_reason(completion) == "content_filter":
        return True
    # Responses 的归一化 LLMResponse 没有 choices：拒绝体现在 output 的
    # refusal content part，或 incomplete_details.reason=content_filter。
    raw = getattr(completion, "raw_completion", None)
    details = _field(raw, "incomplete_details")
    if _field(details, "reason") == "content_filter":
        return True
    for item in _field(raw, "output", ()) or ():
        if _field(item, "type") != "message":
            continue
        for content in _field(item, "content", ()) or ():
            if _field(content, "type") == "refusal":
                return True
    return False


def response_format(request):
    if request.response_format == "text":
        return None
    if request.response_format == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": _structured_format(request)}


def responses_text_format(request):
    """Map the task's structured mode onto the Responses ``text.format`` field."""
    if request.response_format == "text":
        return None
    if request.response_format == "json_object":
        return {"format": {"type": "json_object"}}
    return {"format": {"type": "json_schema", **_structured_format(request)}}


def _normalized_finish_reason(completion):
    raw = getattr(completion, "raw_completion", None)
    details = _field(raw, "incomplete_details")
    if _field(details, "reason") == "max_output_tokens":
        return "length"
    return "stop"


def capture(completion):
    choices = getattr(completion, "choices", ())
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        # 原生 Gemini / Responses 只暴露归一化后的 completion_text。
        body = getattr(completion, "completion_text", None)
        if not isinstance(body, str):
            return CapturedModelOutput("", "invalid")
        if len(body) > 65536:
            return CapturedModelOutput(body[:65536], "oversized")
        if getattr(completion, "tools_call_args", None):
            return CapturedModelOutput(body, "invalid")
        if explicit_refusal(completion):
            return CapturedModelOutput(body, "refusal")
        native_reason = native_finish_reason(completion)
        if native_reason is not None:
            return CapturedModelOutput(body, _CAPTURED_FINISH_REASONS.get(native_reason, "invalid"))
        return CapturedModelOutput(body, _normalized_finish_reason(completion))
    choice = choices[0]
    message = getattr(choice, "message", None)
    body = getattr(message, "content", None)
    reason = getattr(choice, "finish_reason", None)
    if not isinstance(body, str):
        body = ""
    if len(body) > 65536:
        return CapturedModelOutput(body[:65536], "oversized")
    if explicit_refusal(completion):
        return CapturedModelOutput(body, "refusal")
    if getattr(message, "tool_calls", None) or getattr(message, "function_call", None):
        return CapturedModelOutput(body, "invalid")
    if reason not in ("stop", "length"):
        reason = "invalid"
    return CapturedModelOutput(body, reason)
