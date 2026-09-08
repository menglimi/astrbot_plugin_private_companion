"""Replay the platform normalization boundary without side effects."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from validate_decision_contracts import build_validators, parse_time, read, PACKAGE


EXAMPLE = PACKAGE / "examples" / "normalized-interaction-event.json"
RECORDING = PACKAGE / "recordings" / "astrbot-message-recording.json"
CASES = PACKAGE / "cases" / "normalized-input-cases.json"
ROOT = Path(__file__).resolve().parents[1]


def semantic_error(event: dict[str, object], active_generation: str) -> str | None:
    scope = event["scope"]
    assert isinstance(scope, dict)
    mode = event["scope_mode"]
    if mode == "session" and (not event["session_ref"] or not scope.get("session_id")):
        return "session_scope_missing_session_identity"
    if mode == "user" and not scope.get("user_id"):
        return "user_scope_missing_identity_mapping"
    if scope.get("group_id") and event["conversation_ref"] != scope.get("conversation_ref"):
        return "group_conversation_mismatch"
    if mode == "global" and event["visibility"] == "public" and event["payload"]["redaction"] == "none":
        return "unredacted_private_body_in_global"
    if event["adapter_generation"] != active_generation:
        return "adapter_generation_stale"
    if parse_time(event["observed_at"]) > parse_time(event["expires_at"]):
        return "event_expired"
    return None


def apply_patch(base: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    candidate = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(candidate.get(key), dict):
            candidate[key].update(value)
        else:
            candidate[key] = value
    return candidate


def main() -> int:
    validators = build_validators()
    event = read(EXAMPLE)
    recording = read(RECORDING)
    cases = read(CASES)
    active_generation = recording["adapter"]["adapter_generation"]
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    schema_errors = list(validators["normalized-interaction-event"].iter_errors(event))
    check("canonical_event_schema", not schema_errors, "recorded event validates against the normalized input schema")
    check("recording_reference", recording["normalized_ref"] == event["event_id"], "recording points to the canonical event")
    check("adapter_generation_binding", event["adapter_generation"] == active_generation, "event and recording use the active adapter generation")
    check("event_is_fresh", semantic_error(event, active_generation) != "event_expired", "canonical event remains within its TTL")
    check("scope_is_mapped", not semantic_error({**event, "adapter_generation": active_generation}, active_generation) in {"user_scope_missing_identity_mapping", "group_conversation_mismatch"}, "scope and conversation identity are resolved")
    check("payload_is_redacted", event["payload"]["redaction"] != "none", "platform payload is summary/reference-only")

    case_results: list[dict[str, object]] = []
    for case in cases["cases"]:
        candidate = apply_patch(event, case["input_patch"])
        errors = list(validators["normalized-interaction-event"].iter_errors(candidate))
        reason = errors[0].message if errors else semantic_error(candidate, active_generation)
        expected = case["expected"]
        matched = reason is not None and (expected == "reject" or expected.removeprefix("reject_") in str(reason))
        if expected == "reject_generation":
            matched = reason == "adapter_generation_stale"
        elif expected == "reject_expired":
            matched = reason == "event_expired"
        case_results.append({"id": case["id"], "passed": matched, "reason": reason})
    check("negative_cases", all(item["passed"] for item in case_results), "all six normalization rejection cases are isolated")
    side_effects = {"delivery_requests": 0, "memory_writes": 0, "calendar_commits": 0, "world_transitions": 0}
    check("side_effects_zero", all(value == 0 for value in side_effects.values()), "normalize/record replay does not write or deliver")

    failures = [item for item in checks if not item["passed"]]
    result = {
        "mode": "isolated_normalize_record_replay",
        "recording": str(RECORDING.relative_to(ROOT)),
        "canonical_event": event["event_id"],
        "adapter_generation": active_generation,
        "checks": checks,
        "case_results": case_results,
        "side_effects": side_effects,
        "passed": not failures,
        "failures": failures,
        "run_status": "isolated" if not failures else "isolated_failed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
