"""Validate the review-only role decision contract package."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs" / "contracts" / "decision" / "v1"
COMMON = ROOT / "docs" / "contracts" / "v1" / "schemas"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_validators():
    registry = Registry()
    schemas = {}
    for path in [COMMON / "common.schema.json", COMMON / "runtime-scope.schema.json", *sorted((PACKAGE / "schemas").glob("*.schema.json"))]:
        schema = read(path)
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        if path.parent == PACKAGE / "schemas":
            schemas[path.stem.removesuffix(".schema")] = schema
    return {name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()) for name, schema in schemas.items()}


def main() -> int:
    failures = []
    validators = build_validators()
    examples = {path.stem: path for path in (PACKAGE / "examples").glob("*.json")}
    for name, path in examples.items():
        errors = list(validators[name].iter_errors(read(path)))
        if errors:
            failures.append(f"{name}: {errors[0].message}")
    preview = read(examples["proactive-preview"])
    if preview["mode"] != "observe" or preview["risk_class"] != "low" or preview["delivery_allowed"]:
        failures.append("proactive preview must be observe-only and low risk")
    shadow = read(examples["shadow-run"])
    if shadow["mode"] != "observe" or any(value != 0 for value in shadow["side_effects"].values()):
        failures.append("shadow run must remain observe-only with zero side effects")
    if shadow["metrics"]["model_calls"] > 1 or shadow["metrics"]["peak_inflight_previews"] > 8:
        failures.append("shadow run exceeds resource limits")
    binding = read(examples["shadow-adapter-binding"])
    if binding["mode"] != "observe" or binding["delivery_permission"] != "none" or binding["max_inflight"] > 32:
        failures.append("shadow adapter binding grants an invalid mode, permission, or concurrency")
    snapshot = read(examples["role-decision-snapshot"])
    if len(snapshot["motives"]) > 5 or len(snapshot["candidate_refs"]) > 8:
        failures.append("decision snapshot exceeds bounded output limits")
    fixture = read(PACKAGE / "decision.fixture.json")
    if fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append("fixture must remain design-only")
    normalized = read(examples["normalized-interaction-event"])
    if normalized["scope_mode"] == "user" and not normalized["scope"].get("user_id"):
        failures.append("user-scoped normalized input needs a mapped user")
    if normalized["scope"].get("group_id") and normalized["conversation_ref"] != normalized["scope"].get("conversation_ref"):
        failures.append("group input conversation reference must match resolved scope")
    if normalized["scope_mode"] == "global" and normalized["visibility"] == "public" and normalized["payload"]["redaction"] == "none":
        failures.append("global public input cannot carry an unredacted private body")
    if normalized["observed_at"] > normalized["expires_at"]:
        failures.append("normalized input is expired")
    cases = read(PACKAGE / "cases" / "normalized-input-cases.json")
    for case in cases["cases"]:
        candidate = deepcopy(normalized)
        patch = case["input_patch"]
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(candidate.get(key), dict):
                candidate[key].update(value)
            else:
                candidate[key] = value
        errors = list(validators["normalized-interaction-event"].iter_errors(candidate))
        semantic_error = False
        if case["id"] == "NIN-02":
            semantic_error = not candidate["scope"].get("user_id")
        elif case["id"] == "NIN-03":
            semantic_error = bool(candidate["scope"].get("group_id")) and candidate["conversation_ref"] != candidate["scope"].get("conversation_ref")
        elif case["id"] == "NIN-04":
            semantic_error = candidate["scope_mode"] == "global" and candidate["visibility"] == "public" and candidate["payload"]["redaction"] == "none"
        elif case["id"] == "NIN-05":
            semantic_error = candidate["adapter_generation"] != normalized["adapter_generation"]
        elif case["id"] == "NIN-06":
            semantic_error = parse_time(candidate["observed_at"]) > parse_time(candidate["expires_at"])
        if case["expected"].startswith("reject") and not errors and not semantic_error:
            failures.append(f"{case['id']} should be rejected")
        if case["expected"] == "reject" and errors == [] and not semantic_error:
            failures.append(f"{case['id']} did not fail schema or semantic checks")
    recording = read(PACKAGE / "recordings" / "astrbot-message-recording.json")
    if recording["normalization_status"] != "recorded" or recording["dto_schema"] != normalized["schema_version"]:
        failures.append("AstrBot recording must point to a recorded normalized input")
    forbidden = set(recording["forbidden_fields"])
    if any(key in recording.get("native_event_summary", {}) for key in forbidden):
        failures.append("AstrBot recording contains a forbidden native field")
    cross_host = read(PACKAGE / "cases" / "cross-host-comparison.json")
    if len(cross_host["shells"]) != 3 or set(cross_host["forbidden_effects"]) != {"delivery", "memory_write", "calendar_commit", "world_transition"}:
        failures.append("cross-host fixture must define three shells and four forbidden effects")
    degradation = read(PACKAGE / "cases" / "capability-degradation-budget.json")
    if len(degradation["shells"]) != 3 or any(value < 0 for value in degradation["budgets"].values()):
        failures.append("degradation fixture must define three shells and finite nonnegative budgets")
    manifest = read(PACKAGE / "manifest.json")
    for entry in [*manifest["dependencies"], *manifest["schemas"], *manifest["artifacts"]]:
        path = (PACKAGE / entry["path"]).resolve()
        if not path.is_file() or digest(path) != entry["sha256"]:
            failures.append(f"fingerprint mismatch: {entry['path']}")
    output = {"package_version": manifest["package_version"], "schema_count": len(validators), "examples": len(examples), "cross_host_shells": len(cross_host["shells"]), "degradation_shells": len(degradation["shells"]), "fixture": "not_run", "failures": failures}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
