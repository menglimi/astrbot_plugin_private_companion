"""Compare explicit capability degradation and resource budgets across shells."""
from __future__ import annotations

import json

from replay_normalized_input import EXAMPLE, ROOT, semantic_error
from validate_decision_contracts import build_validators, read, PACKAGE


FIXTURE = PACKAGE / "cases" / "capability-degradation-budget.json"


def main() -> int:
    validators = build_validators()
    event = read(EXAMPLE)
    fixture = read(FIXTURE)
    budgets = fixture["budgets"]
    shells = fixture["shells"]
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    schema_errors = list(validators["normalized-interaction-event"].iter_errors(event))
    check("canonical_input_valid", not schema_errors, "all shells receive the canonical normalized event")
    check("canonical_input_fresh", semantic_error(event, event["adapter_generation"]) != "event_expired", "the input remains within its TTL")
    required = fixture["required_capability"]
    check("required_capability_explicit", all(required in shell["available_features"] for shell in shells), "every shell declares the shadow capability")
    check("degradation_matches_features", shells[0]["degradation"] == "none" and all(shell["degradation"] != "none" for shell in shells[1:]), "missing optional features produce explicit degradation")
    check("candidate_budget_bounded", all(shell["metrics"]["candidate_count"] <= budgets["max_candidates"] for shell in shells), "candidate count stays within the shared bound")
    check("model_budget_bounded", all(shell["metrics"]["model_calls"] <= budgets["max_model_calls"] for shell in shells), "model calls stay within the shared bound")
    check("snapshot_budget_bounded", all(shell["metrics"]["snapshot_bytes"] <= budgets["max_snapshot_bytes"] for shell in shells), "snapshot bytes stay within the shared bound")
    check("inflight_budget_bounded", all(shell["metrics"]["peak_inflight_previews"] <= budgets["max_inflight_previews"] for shell in shells), "inflight previews stay within the shared bound")
    check("duration_budget_bounded", all(shell["metrics"]["duration_ms"] <= budgets["max_duration_ms"] for shell in shells), "duration stays within the shared bound")
    full = shells[0]["metrics"]
    check("degradation_is_monotonic", all(shell["metrics"]["candidate_count"] <= full["candidate_count"] and shell["metrics"]["snapshot_bytes"] <= full["snapshot_bytes"] and shell["metrics"]["model_calls"] <= full["model_calls"] for shell in shells[1:]), "degraded shells do not increase work or memory")
    check("side_effects_zero", all(all(value == 0 for value in shell["side_effects"].values()) and set(shell["side_effects"]) == set(fixture["forbidden_effects"]) for shell in shells), "capability comparison cannot deliver or write")

    failures = [item for item in checks if not item["passed"]]
    result = {
        "mode": "isolated_capability_degradation_replay",
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "canonical_event": event["event_id"],
        "shells": [shell["shell_id"] for shell in shells],
        "budgets": budgets,
        "checks": checks,
        "passed": not failures,
        "failures": failures,
        "run_status": "isolated" if not failures else "isolated_failed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
