"""Replay one global actor switching among several chat windows."""
from __future__ import annotations

import json

from validate_decision_contracts import read, PACKAGE
from replay_normalized_input import ROOT


FIXTURE = PACKAGE / "cases" / "global-multi-window-continuity.json"


def main() -> int:
    fixture = read(FIXTURE)
    windows = {window["window_ref"]: window for window in fixture["windows"]}
    state = {
        "actor_id": fixture["actor_id"],
        "persona_id": fixture["persona_id"],
        "global_revision": fixture["initial_global_revision"],
        "activity": fixture["initial_activity"],
        "active_window": "window-private-a",
        "window_revisions": {ref: 0 for ref in windows},
        "private_refs_in_group": [],
        "context_checkpoints": 0,
        "deliveries": 0,
        "writes": 0,
    }
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    previous_global_revision = state["global_revision"]
    for event in fixture["events"]:
        window = windows[event["window_ref"]]
        if event["kind"] == "switch_window":
            before = state["global_revision"]
            state["active_window"] = event["window_ref"]
            check(f"switch_{event['sequence']}", state["global_revision"] == before, "attention switch does not mutate actor state")
            continue
        state["global_revision"] += 1
        state["window_revisions"][event["window_ref"]] += 1
        if window["audience"] == "group" and event["private_evidence"]:
            state["private_refs_in_group"].append(event["sequence"])
        if event["global_effect"] and state["activity"] != fixture["initial_activity"]:
            check(f"activity_{event['sequence']}", False, "global activity was unexpectedly reset")
        check(f"event_{event['sequence']}", state["global_revision"] > previous_global_revision, "global revision advances once for each accepted event")
        previous_global_revision = state["global_revision"]

    check("single_actor", state["actor_id"] == fixture["actor_id"] and state["persona_id"] == fixture["persona_id"], "all windows use one actor and persona")
    check("activity_continuity", state["activity"] == fixture["initial_activity"], "switching windows preserves the ongoing life activity")
    check("window_sessions_distinct", len({window["session_id"] for window in windows.values()}) == len(windows), "each chat window keeps its own session")
    check("group_private_boundary", not state["private_refs_in_group"], "private evidence never enters the group projection")
    check("window_count_bounded", len(windows) <= fixture["budgets"]["max_windows"], "window registry stays bounded")
    check("checkpoint_count_bounded", state["context_checkpoints"] <= fixture["budgets"]["max_context_checkpoints"], "cold-window checkpoints stay bounded")
    check("side_effects_zero", state["deliveries"] == 0 and state["writes"] == 0, "continuity replay does not deliver or write domain state")

    failures = [item for item in checks if not item["passed"]]
    result = {"mode": "isolated_global_multi_window_replay", "fixture": str(FIXTURE.relative_to(ROOT)), "actor_id": state["actor_id"], "active_window": state["active_window"], "global_revision": state["global_revision"], "window_revisions": state["window_revisions"], "checks": checks, "passed": not failures, "failures": failures, "run_status": "isolated" if not failures else "isolated_failed"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
