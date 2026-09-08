"""Validate the review-only companion.control@1 contract package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "control" / "v1"
DEPENDENCIES = {
    "common": Path("../../v1/schemas/common.schema.json"),
    "runtime-scope": Path("../../v1/schemas/runtime-scope.schema.json"),
}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures: list[dict] = []
    manifest = read(ROOT / "manifest.json")
    registry = Registry()
    schemas: dict[str, dict] = {}
    known_ids: set[str] = set()
    for path in sorted((ROOT / "schemas").glob("*.schema.json")):
        schema = read(path)
        Draft202012Validator.check_schema(schema)
        if schema["$id"] in known_ids:
            failures.append({"check": "duplicate_schema_id", "path": str(path)})
        known_ids.add(schema["$id"])
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        schemas[path.stem.removesuffix(".schema")] = schema
    for name, relative in DEPENDENCIES.items():
        path = (ROOT / relative).resolve()
        dependency = read(path)
        if dependency["$id"] in known_ids:
            failures.append({"check": "duplicate_dependency_id", "name": name})
        known_ids.add(dependency["$id"])
        registry = registry.with_resource(dependency["$id"], Resource.from_contents(dependency))

    for entry in manifest["schemas"]:
        path = ROOT / entry["path"]
        if not path.exists() or digest(path) != entry["sha256"]:
            failures.append({"check": "schema_fingerprint", "path": entry["path"]})
    for entry in manifest["artifacts"]:
        path = ROOT / entry["path"]
        if not path.exists() or digest(path) != entry["sha256"]:
            failures.append({"check": "artifact_fingerprint", "path": entry["path"]})

    validators = {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }
    cases = read(ROOT / "cases.json")
    checked = 0
    for case in cases["cases"]:
        document = read(ROOT / case["document"])
        errors = list(validators[case["schema"]].iter_errors(document))
        valid = not errors
        checked += 1
        if valid != case["valid"]:
            failures.append({"check": "schema_case", "case": case["id"], "errors": [e.message for e in errors[:3]]})

    fixture = read(ROOT / "fixture.json")
    if fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append({"check": "fixture_design_status"})
    for name, relative in fixture["artifacts"].items():
        if not (ROOT / relative).exists():
            failures.append({"check": "fixture_artifact", "name": name, "path": relative})
    for scenario in fixture["scenarios"]:
        if scenario["run_status"] != "not_run" or scenario["actual"] is not None:
            failures.append({"check": "scenario_design_status", "case": scenario["case_id"]})
        known_cases = {case["id"] for case in cases["cases"]}
        if not set(scenario["schema_case_ids"]).issubset(known_cases):
            failures.append({"check": "scenario_case_reference", "case": scenario["case_id"]})

    result = {
        "package_version": manifest["package_version"],
        "profile": manifest["profile"],
        "schema_count": len(schemas),
        "schema_cases": checked,
        "fixture_scenarios": len(fixture["scenarios"]),
        "not_run": ["live_authorization", "cas_registration", "generation_fence", "lost_response_retry", "resource_release", "platform_replay"],
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
