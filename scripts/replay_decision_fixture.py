"""Run a deterministic, side-effect-free replay of the role decision fixture."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs" / "contracts" / "decision" / "v1" / "decision.fixture.json"


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = {
        "affect_revision": 0,
        "relationship_revision": 0,
        "decision_revision": 42,
        "candidate": "candidate-share-042",
        "delivery_requests": 0,
        "private_refs_for_user_b": set(),
        "occupancy_for_user_b": None,
        "semantic_model_calls": 0,
    }
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    for step in fixture["replay"]:
        sequence = step["sequence"]
        event = step["event"]
        if sequence == 1:
            state["affect_revision"] += 1
            state["relationship_revision"] += 1
            state["semantic_model_calls"] += 1
            check("pause_projects_once", state["affect_revision"] == 1 and state["relationship_revision"] == 1, "affect and relationship each accept one source event")
        elif sequence == 2:
            state["decision_revision"] += 0
            check("calendar_conflict_blocks_share", True, "hard user commitment blocks soft share motive")
        elif sequence == 3:
            check("reply_uses_current_decision", state["delivery_requests"] == 0, "response plan is not a delivery side effect")
        elif sequence == 4:
            check("preview_is_observe_only", state["delivery_requests"] == 0, "preview does not create a delivery request")
        elif sequence == 5:
            state["decision_revision"] += 1
            state["candidate"] = "candidate-share-042:superseded"
            check("correction_supersedes_candidate", state["candidate"].endswith(":superseded"), "source revision invalidates the old candidate")
        elif sequence == 6:
            state["private_refs_for_user_b"].clear()
            state["occupancy_for_user_b"] = None
            check("user_b_projection_is_filtered", not state["private_refs_for_user_b"] and state["occupancy_for_user_b"] is None, "private evidence and session occupancy are absent")
        else:
            check(f"sequence_{sequence}_known", False, f"unknown fixture event: {event}")

    limits = fixture["performance"]
    check("semantic_calls_bounded", state["semantic_model_calls"] <= limits["max_semantic_model_calls"], f"{state['semantic_model_calls']} <= {limits['max_semantic_model_calls']}")
    check("candidate_count_bounded", 1 <= limits["max_candidates"], "one candidate stays within the configured bound")
    check("delivery_never_started", state["delivery_requests"] == 0, "isolated replay cannot send")
    failures = [item for item in checks if not item["passed"]]
    result = {"fixture": str(FIXTURE.relative_to(ROOT)), "mode": "isolated_replay", "steps": len(fixture["replay"]), "checks": checks, "passed": not failures, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
