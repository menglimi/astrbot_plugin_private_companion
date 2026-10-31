"""Snapshot raw completion metadata before the native parser can modify it."""

from companion_v2.model_gateway.response_diagnostics import (
    FINISH_REASONS, ResponseDiagnostics, ResponseTextSummary,
)


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
    reason = "missing" if reason is None else reason if type(reason) is str and reason in FINISH_REASONS else "unknown"
    message = getattr(choice, "message", None)
    text = getattr(completion, "completion_text", None)
    if text is None:
        text = getattr(message, "content", None)
    return ResponseDiagnostics(completion_present=completion is not None,
        finish_reason=reason, parse_state="raw_observed" if completion is not None else "not_received",
        raw=ResponseTextSummary.from_text(text))
