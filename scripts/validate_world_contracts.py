"""Validate the review-only world simulation contract package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs" / "contracts" / "world" / "v1"
COMMON = ROOT / "docs" / "contracts" / "v1" / "schemas"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build_validators():
    registry = Registry()
    schemas = {}
    paths = [COMMON / "common.schema.json", COMMON / "runtime-scope.schema.json", *sorted((PACKAGE / "schemas").glob("*.schema.json"))]
    for path in paths:
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

    entity = read(examples["world-entity"])
    if entity["visibility"] == "global_safe" and entity["reality_mode"] == "observed":
        failures.append("global_safe observed entities require an explicit public observation policy")
    event = read(examples["world-event"])
    if event["reality_mode"] == "simulated" and event["event_type"] == "observation_received":
        failures.append("observation_received cannot be simulated")
    request = read(examples["world-request"])
    if request["operation"] == "world.activity.advance" and request["budget"]["max_model_calls"] != 0:
        failures.append("activity advance must be model-call-free by default")
    result = read(examples["world-result"])
    if result["status"] == "succeeded" and not result["committed"]:
        failures.append("succeeded world result must be committed")

    fixture = read(PACKAGE / "world.fixture.json")
    if fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append("fixture must remain design-only")
    if [item["id"] for item in fixture["scenarios"]] != [f"WMS-{i:02d}" for i in range(1, 9)]:
        failures.append("fixture scenario ids are not contiguous")

    manifest = read(PACKAGE / "manifest.json")
    for entry in [*manifest["dependencies"], *manifest["schemas"], *manifest["artifacts"]]:
        path = (PACKAGE / entry["path"]).resolve()
        if not path.is_file() or digest(path) != entry["sha256"]:
            failures.append(f"fingerprint mismatch: {entry['path']}")

    output = {"package_version": manifest["package_version"], "schema_count": len(validators), "examples": len(examples), "fixture": "not_run", "failures": failures}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
