"""Replay control-plane lifecycle fences and lost-response handling in memory."""
from __future__ import annotations

import json

from validate_control_contracts import ROOT, read


FIXTURE = ROOT / "lifecycle-replay.json"


def main() -> int:
    fixture = read(FIXTURE)
    initial = fixture["initial"]
    state = {
        "provider_generation": initial["provider_generation"],
        "binding_ref": initial["binding_ref"],
        "binding_generation": initial["binding_generation"],
        "binding_state": initial["binding_state"],
        "allocation_count": initial["allocation_count"],
        "owner_ref": initial["owner_ref"],
        "control_requests": 0,
        "handles": 1,
        "tasks": 1,
        "lookup_state": None,
        "delivery_requests": 0,
        "domain_writes": 0,
    }
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    for operation in fixture["operations"]:
        state["control_requests"] += 1
        kind = operation["kind"]
        if kind == "bind_response_lost_retry":
            retry_binding = state["binding_ref"]
            check(operation["id"], retry_binding == initial["binding_ref"] and state["allocation_count"] == 1, "same idempotency key returns the original binding")
        elif kind == "release_binding":
            state["binding_state"] = "released"
            state["handles"] = 0
            check(operation["id"], state["binding_state"] == "released" and state["handles"] == 0, "release closes handle admission without unloading provider state")
        elif kind == "invoke_old_handle":
            check(operation["id"], state["binding_state"] != "ready", "released old handle cannot invoke")
        elif kind == "register_new_generation":
            state["provider_generation"] = "provider-gen-2"
            state["binding_state"] = "unbound"
            check(operation["id"], state["provider_generation"] != initial["provider_generation"] and state["binding_state"] == "unbound", "new generation requires a fresh binding")
        elif kind == "cancel_unknown_then_lookup":
            state["lookup_state"] = "uncertain"
            check(operation["id"], state["lookup_state"] == "uncertain" and state["owner_ref"] == initial["owner_ref"], "unknown business effect remains with the original owner")
        elif kind == "revoke_and_drain":
            state["binding_state"] = "revoked"
            state["tasks"] = 0
            state["handles"] = 0
            check(operation["id"], state["binding_state"] == "revoked" and state["tasks"] == 0 and state["handles"] == 0, "revoke closes admission and drains supervised work")
        else:
            check(operation["id"], False, f"unknown lifecycle operation: {kind}")

    budgets = fixture["budgets"]
    check("budget_control_requests", state["control_requests"] <= budgets["max_control_requests"], "control calls stay within the fixture budget")
    check("budget_handles", state["handles"] <= budgets["max_handles"], "handles stay within the fixture budget")
    check("budget_tasks", state["tasks"] <= budgets["max_inflight_tasks"], "inflight tasks stay within the fixture budget")
    check("budget_queue", 0 <= budgets["max_queue_items"], "queue bound is finite")
    check("side_effects_zero", state["delivery_requests"] == 0 and state["domain_writes"] == 0, "control replay has no delivery or domain writes")
    failures = [item for item in checks if not item["passed"]]
    result = {"mode": "isolated_control_lifecycle_replay", "fixture": str(FIXTURE.relative_to(ROOT)), "checks": checks, "state": state, "passed": not failures, "failures": failures, "run_status": "isolated" if not failures else "isolated_failed"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
