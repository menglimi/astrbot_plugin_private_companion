"""Replay the HDSI window/persona binding matrix without side effects."""
from __future__ import annotations

import json

from replay_normalized_input import ROOT
from validate_decision_contracts import PACKAGE, read


FIXTURE = PACKAGE / "cases" / "persona-window-matrix.json"


def main() -> int:
    fixture = read(FIXTURE)
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    same = fixture["same_persona"]
    check("same_actor", len({same["actor_id"] for _ in same["windows"]}) == 1, "all same-persona windows share one actor")
    check("same_persona", same["persona_id"] == "persona-main", "window changes do not select another persona")
    check("group_projection", "private_transcript" in same["private_state"], "private transcript remains outside shared state")

    runtimes = fixture["different_personas"]["runtimes"]
    check("different_runtime", len({item["actor_id"] for item in runtimes}) == len(runtimes), "different personas have distinct actor runtimes")
    check("different_persona", len({item["persona_id"] for item in runtimes}) == len(runtimes), "different personas are explicitly bound")
    check("state_owner_isolation", set(fixture["different_personas"]["isolated_state"]) == {"world", "affect", "relationship", "memory", "event_ledger"}, "domain owners stay isolated")

    stale = fixture["stale_draft"]
    check("stale_revision", stale["draft_global_revision"] < stale["committed_global_revision"], "older drafts cannot overwrite newer global state")
    check("forbidden_rules", len(fixture["forbidden"]) == 3, "matrix records the three forbidden implicit behaviors")

    failures = [item for item in checks if not item["passed"]]
    result = {
        "mode": "isolated_persona_window_matrix_replay",
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "checks": checks,
        "passed": not failures,
        "failures": failures,
        "run_status": "isolated" if not failures else "isolated_failed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
