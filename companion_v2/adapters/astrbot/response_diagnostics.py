"""Snapshot raw completion metadata before the native parser can modify it."""

from companion_v2.model_gateway.response_diagnostics import (
    FINISH_REASONS, ResponseDiagnostics, ResponseTextSummary,
)


# How the native Google GenAI SDK names the end of a candidate. Only the names in
# this table say something the V2 classifications can express; everything else is
# an unknown ending rather than a guess at one.
_GEMINI_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "IMAGE_SAFETY": "content_filter",
    "UNEXPECTED_TOOL_CALL": "tool_calls",
    "TOO_MANY_TOOL_CALLS": "tool_calls",
    "FUNCTION_CALL": "function_call",
}


def _enum_name(value):
    """The provider enum's own name, whatever flavour of enum it is.

    ``google.genai.types`` enums expose ``.name`` and render as
    ``FinishReason.MAX_TOKENS``; other SDK builds hand back the bare string.
    Anything else is unnamed, which callers must treat as "no answer" instead of
    as a classification of their own.
    """

    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.upper()
    if isinstance(value, str):
        return value.rsplit(".", 1)[-1].strip().upper()
    return ""


def native_finish_reason(completion):
    """The candidate's own finish reason, in the gateway's classification.

    The native text adapter has no OpenAI parse hook: its ``LLMResponse`` keeps
    only the SDK response, so the candidate is the single place a truncated or
    filtered answer is still visible. Returns ``None`` when the completion says
    nothing about how it ended, so a caller keeps its own default.
    """

    raw_completion = getattr(completion, "raw_completion", None)
    candidates = getattr(raw_completion, "candidates", None) or getattr(completion, "candidates", None)
    candidate = candidates[0] if isinstance(candidates, (list, tuple)) and candidates else None
    name = _enum_name(getattr(candidate, "finish_reason", None))
    return _GEMINI_FINISH_REASONS.get(name, "unknown") if name else None


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _responses_finish_reason(raw_completion):
    """Translate a Responses status/incomplete reason into the fixed vocabulary."""
    if raw_completion is None:
        return None
    details = _field(raw_completion, "incomplete_details")
    incomplete_reason = _field(details, "reason")
    if incomplete_reason == "max_output_tokens":
        return "length"
    if incomplete_reason == "content_filter":
        return "content_filter"
    status = _field(raw_completion, "status")
    if status in ("completed", "incomplete", "failed"):
        return "stop" if status == "completed" else "unknown"
    return None


def observe_completion(completion):
    raw_completion = getattr(completion, "raw_completion", None)
    choices = getattr(completion, "choices", ()) or getattr(raw_completion, "choices", ())
    choice = choices[0] if isinstance(choices, (list, tuple)) and choices else None
    reason = getattr(choice, "finish_reason", None)
    if reason is None:
        reason = _responses_finish_reason(raw_completion)
        if reason is None:
            reason = native_finish_reason(completion)
    reason = "missing" if reason is None else reason if type(reason) is str and reason in FINISH_REASONS else "unknown"
    message = getattr(choice, "message", None)
    text = getattr(completion, "completion_text", None)
    if text is None:
        text = getattr(message, "content", None)
    return ResponseDiagnostics(completion_present=completion is not None,
        finish_reason=reason, parse_state="raw_observed" if completion is not None else "not_received",
        raw=ResponseTextSummary.from_text(text))
