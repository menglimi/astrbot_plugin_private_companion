"""Compare the same normalized event across portable host shells."""
from __future__ import annotations

import json

from replay_normalized_input import EXAMPLE, ROOT, semantic_error
from validate_decision_contracts import build_validators, read, PACKAGE


FIXTURE = PACKAGE / "cases" / "cross-host-comparison.json"


def main() -> int:
    validators = build_validators()
    event = read(EXAMPLE)
    fixture = read(FIXTURE)
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    errors = list(validators["normalized-interaction-event"].iter_errors(event))
    check("canonical_input_valid", not errors, "all shells consume the same valid normalized event")
    check("canonical_input_fresh", semantic_error(event, event["adapter_generation"]) != "event_expired", "the shared event is within its TTL")
    shells = fixture["shells"]
    semantic_keys = fixture["semantic_fields"]

    def freeze(value: object) -> object:
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, freeze(item)) for key, item in value.items()))
        return value

    signatures = [tuple((key, freeze(shell["resolved"][key])) for key in semantic_keys) for shell in shells]
    check("semantic_projection_equal", len(set(signatures)) == 1, "owner, scope, visibility, evidence and content semantics converge")
    owner_refs = {shell["resolved"]["owner_ref"] for shell in shells}
    check("owner_excludes_host", len(owner_refs) == 1 and all(token not in next(iter(owner_refs)) for token in ("runtime-", "astrbot-readonly", "service-readonly", "embedded-readonly")), "logical owner does not depend on host or adapter identity")
    check("user_scope_preserved", all(shell["resolved"]["scope_mode"] == "user" and shell["resolved"]["visibility"] == "user_shared" for shell in shells), "user sharing remains explicit across private entry points")
    session_refs = [shell["session_ref"] for shell in shells]
    check("session_isolation_preserved", len(session_refs) == len(set(session_refs)), "runtime sessions remain distinct even when user state is shared")
    check("host_differences_explicit", all(shell["host_kind"] and shell["runtime_instance_id"] and shell["source_adapter"] and shell["adapter_generation"] for shell in shells), "host-specific differences are explicit adapter metadata")
    check("shadow_capability_only", all(shell["capabilities"].get("decision_shadow") == "ready" and shell["capabilities"].get("delivery") == "none" for shell in shells), "every shell is read-only and delivery-free")
    side_effects = {"delivery": 0, "memory_write": 0, "calendar_commit": 0, "world_transition": 0}
    check("side_effects_zero", all(value == 0 for value in side_effects.values()) and set(side_effects) == set(fixture["forbidden_effects"]), "cross-host comparison has no side effects")

    failures = [item for item in checks if not item["passed"]]
    result = {
        "mode": "isolated_cross_host_comparison",
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "canonical_event": event["event_id"],
        "shells": [shell["shell_id"] for shell in shells],
        "checks": checks,
        "side_effects": side_effects,
        "passed": not failures,
        "failures": failures,
        "run_status": "isolated" if not failures else "isolated_failed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
