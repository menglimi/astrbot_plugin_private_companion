"""Validate the review-only calendar contract package and its bounded replay fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs" / "contracts" / "calendar" / "v1"
COMMON = ROOT / "docs" / "contracts" / "v1" / "schemas"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def validators() -> dict[str, Draft202012Validator]:
    registry = Registry()
    schemas = {}
    for path in [COMMON / "common.schema.json", COMMON / "runtime-scope.schema.json", *sorted((PACKAGE / "schemas").glob("*.schema.json"))]:
        schema = read(path)
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        if path.parent == PACKAGE / "schemas":
            schemas[path.stem.removesuffix(".schema")] = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    # Rebuild after all resources are registered so cross-schema references resolve.
    return {name: Draft202012Validator(v.schema, registry=registry, format_checker=FormatChecker()) for name, v in schemas.items()}


def main() -> int:
    failures: list[str] = []
    vs = validators()
    examples = {path.stem: path for path in (PACKAGE / "examples").glob("*.json")}
    for name, path in examples.items():
        schema_name = name
        errors = list(vs[schema_name].iter_errors(read(path)))
        if errors:
            failures.append(f"{name}: {errors[0].message}")

    intent = read(examples["activity-intent"])
    if intent["reality_mode"] == "simulated" and intent["status"] == "converted":
        failures.append("simulated intent cannot be converted without an explicit calendar command")

    request = read(examples["calendar-request"])
    if request["operation"] == "commit" and request["authorization_ref"] is None:
        failures.append("commit requires authorization_ref")

    result = read(examples["calendar-result"])
    if result["status"] == "succeeded" and not result["receipt"]["committed"]:
        failures.append("succeeded result must carry a committed receipt")

    fixture = read(PACKAGE / "calendar.fixture.json")
    if fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append("fixture must remain design-only")
    if [scenario["id"] for scenario in fixture["scenarios"]] != [f"CAL-{i:02d}" for i in range(1, 5)]:
        failures.append("fixture scenario ids are not contiguous")

    manifest = read(PACKAGE / "manifest.json")
    for entry in [*manifest["dependencies"], *manifest["schemas"], *manifest["artifacts"]]:
        path = (PACKAGE / entry["path"]).resolve()
        if not path.is_file() or digest(path) != entry["sha256"]:
            failures.append(f"fingerprint mismatch: {entry['path']}")

    output = {"package_version": manifest["package_version"], "schema_count": len(vs), "examples": len(examples), "fixture": "not_run", "failures": failures}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
