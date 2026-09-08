"""Validate public execution design assets without importing a plugin runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from validate_framework_contracts import (
    FORMATS,
    change_document,
    fingerprint,
    package_path,
    parse_json,
    read_json,
    unavailable_resource,
)


DEFAULT_PACKAGE = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "execution" / "v1"
DEPENDENCIES = {
    "common": "../../v1/schemas/common.schema.json",
    "runtime-scope": "../../v1/schemas/runtime-scope.schema.json",
}


def build_manifest(package: Path) -> dict:
    schemas, fixture_schemas, artifacts = [], [], []
    for path in sorted(package.rglob("*.json")):
        if path == package / "manifest.json":
            continue
        relative = path.relative_to(package)
        document = read_json(path)
        entry = {"path": relative.as_posix(), "sha256": fingerprint(path)}
        if path.parent.name == "schemas":
            entry = {"name": path.name.removesuffix(".schema.json"), "id": document["$id"], **entry}
            (schemas if relative.parts[0] == "schemas" else fixture_schemas).append(entry)
        else:
            artifacts.append(entry)
    dependencies = []
    for name, relative in DEPENDENCIES.items():
        path = (package / relative).resolve()
        dependencies.append({"name": name, "id": read_json(path)["$id"], "path": relative, "sha256": fingerprint(path)})
    return {
        "package_id": "companion.execution-contracts",
        "package_version": "0.1.0",
        "profile": "companion.execution@1",
        "maturity": "review",
        "dialect": "https://json-schema.org/draft/2020-12/schema",
        "fingerprint_algorithm": "sha256-utf8-lf",
        "dependencies": dependencies,
        "schemas": schemas,
        "fixture_schemas": fixture_schemas,
        "artifacts": artifacts,
    }


def validators_for(package: Path, inventory: dict) -> tuple[dict, Registry, set]:
    registry = Registry(retrieve=unavailable_resource)
    loaded, known_ids = {}, set()
    for group in ("dependencies", "schemas", "fixture_schemas"):
        for entry in inventory[group]:
            path = ((package / DEPENDENCIES[entry["name"]]).resolve() if group == "dependencies"
                    else package_path(package, entry["path"]))
            schema = read_json(path)
            Draft202012Validator.check_schema(schema)
            if schema["$id"] in known_ids:
                raise ValueError(f"Duplicate local schema ID: {schema['$id']}")
            known_ids.add(schema["$id"])
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
            if group == "schemas":
                loaded[entry["name"]] = schema
    validators = {
        name: Draft202012Validator(schema, registry=registry, format_checker=FORMATS)
        for name, schema in loaded.items()
    }
    return validators, registry, known_ids


def payload_errors(document: dict, registry: Registry, known_ids: set) -> list:
    if "payload_schema" in document:
        schema_id, payload = document["payload_schema"], document["payload"]
    elif document.get("output_schema") is not None:
        schema_id, payload = document["output_schema"], document["output"]
    else:
        return []
    if schema_id.partition("#")[0] not in known_ids:
        return ["Payload schema is not in the local registry"]
    try:
        validator = Draft202012Validator({"$ref": schema_id}, registry=registry, format_checker=FORMATS)
        return [error.message for error in validator.iter_errors(payload)]
    except (Unresolvable, ValueError) as error:
        return [str(error)]


def check_fixture(package: Path, validators: dict, registry: Registry, known_ids: set, case_ids: set, failures: list) -> dict:
    fixture = read_json(package / "fixtures" / "cross-plugin.fixture.json")
    if fixture["contract_package_version"] != "0.1.0" or fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append({"check": "fixture_version_or_design_status"})
    documents = {}
    for name, artifact in fixture["artifacts"].items():
        document = read_json(package_path(package, artifact["document"]))
        documents[name] = document
        errors = [error.message for error in validators[artifact["schema"]].iter_errors(document)]
        errors.extend(payload_errors(document, registry, known_ids))
        if errors:
            failures.append({"check": "fixture_artifact_shape", "artifact": name, "errors": errors[:5]})
    for pair in fixture["correlations"]:
        request, result = documents[pair["request"]], documents[pair["result"]]
        mismatches = [field for field in ("capability_id", "capability_version", "provider_id", "provider_generation", "request_id", "trace_id")
                      if request[field] != result[field]]
        mismatches.extend("execution." + field for field in ("task_id", "attempt_id", "part_id", "budget_ref")
                          if request["execution"][field] != result["execution"][field])
        if request["execution"]["operation_id"] is not None and request["execution"]["operation_id"] != result["execution"]["operation_id"]:
            mismatches.append("execution.operation_id")
        declaration = fixture["capabilities"][request["capability_id"]]
        if declaration["input_schema"] != request["payload_schema"]:
            mismatches.append("payload_schema")
        if result["output_schema"] is not None and declaration["output_schema"] != result["output_schema"]:
            mismatches.append("output_schema")
        if declaration["side_effect"] != "none" and request["idempotency_key"] is None:
            mismatches.append("idempotency_key")
        if mismatches:
            failures.append({"check": "fixture_pair", "request": pair["request"], "fields": mismatches})
    for workflow in fixture["workflows"]:
        if workflow["run_status"] != "not_run" or workflow["actual"] is not None:
            failures.append({"check": "workflow_is_design_only", "workflow": workflow["id"]})
        for step in workflow["steps"]:
            names = [step[field] for field in ("artifact", "request", "result") if field in step]
            names.extend(step.get("artifacts", []))
            if not set(names).issubset(documents):
                failures.append({"check": "workflow_artifact_reference", "workflow": workflow["id"]})
    scenario_ids = [scenario["case_id"] for scenario in fixture["scenarios"]]
    if sorted(scenario_ids) != [f"LC-{index:02d}" for index in range(9, 14)]:
        failures.append({"check": "fixture_scenarios"})
    for scenario in fixture["scenarios"]:
        if not set(scenario["schema_case_ids"]).issubset(case_ids):
            failures.append({"check": "scenario_case_reference", "case": scenario["case_id"]})
        if scenario["run_status"] != "not_run" or scenario["actual"] is not None:
            failures.append({"check": "scenario_is_design_only", "case": scenario["case_id"]})
    return {"artifacts": len(documents), "request_result_pairs": len(fixture["correlations"]),
            "workflows": len(fixture["workflows"]), "scenario_links": len(scenario_ids), "run_status": "not_run"}


def validate(package: Path) -> dict:
    failures = []
    inventory = build_manifest(package)
    if read_json(package / "manifest.json") != inventory:
        failures.append({"check": "manifest", "error": "Inventory or fingerprints changed; review before registering"})
    validators, registry, known_ids = validators_for(package, inventory)
    suite = read_json(package / "cases.json")
    case_ids, payload_count = set(), 0
    for case in suite["json_cases"]:
        try:
            parse_json(case["text"])
            valid = True
        except ValueError:
            valid = False
        if valid != case["valid"]:
            failures.append({"case": case["id"], "check": "strict_json"})
    for case in suite["cases"]:
        if case["id"] in case_ids:
            failures.append({"case": case["id"], "check": "duplicate_case_id"})
        case_ids.add(case["id"])
        document = change_document(read_json(package_path(package, case["document"])), case.get("changes", []))
        errors = list(validators[case["schema"]].iter_errors(document))
        if (not errors) != case["valid"]:
            failures.append({"case": case["id"], "check": "schema", "expected_valid": case["valid"],
                             "errors": [{"path": list(error.absolute_path), "rule": error.validator} for error in errors[:5]]})
        if "payload_valid" in case:
            payload_count += 1
            nested_errors = payload_errors(document, registry, known_ids) if not errors else ["Wire shape is invalid"]
            if (not nested_errors) != case["payload_valid"]:
                failures.append({"case": case["id"], "check": "payload_schema", "errors": nested_errors[:5]})
    fixture = check_fixture(package, validators, registry, known_ids, case_ids, failures)
    return {
        "package_version": inventory["package_version"],
        "schema_count": len(validators), "dependency_schemas": len(inventory["dependencies"]),
        "fixture_schemas": len(inventory["fixture_schemas"]), "schema_cases": len(suite["cases"]),
        "payload_cases": payload_count, "json_cases": len(suite["json_cases"]), "fixture": fixture,
        "checked": ["utf8_json", "strict_json", "schema_meta_validation", "local_refs_only", "package_and_dependency_fingerprints",
                    "wire_shapes", "registered_fixture_payload_shapes", "static_fixture_correlations"],
        "not_run": ["live_authorization", "runtime_descriptor_binding", "task_persistence", "attempt_fence",
                    "cancellation_barriers", "session_resume", "delivery_evidence", "budget_accounting", "resource_peaks", "platform_replay"],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--print-manifest", action="store_true", help="Print hashes for review; never writes assets")
    args = parser.parse_args()
    try:
        result = build_manifest(args.package) if args.print_manifest else validate(args.package)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(bool(result.get("failures")))
    except (ValueError, KeyError, OSError, Unresolvable) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
