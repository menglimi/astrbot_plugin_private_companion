"""Validate sharing contract shapes and static fixtures without a plugin runtime."""
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


DEFAULT_PACKAGE = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "sharing" / "v1"
DEPENDENCIES = {
    name: f"../../v1/schemas/{name}.schema.json"
    for name in ("common", "runtime-scope", "memory-proposal", "memory-query", "memory-result")
}


def build_manifest(package: Path) -> dict:
    schemas, fixture_schemas, artifacts = [], [], []
    for path in sorted(package.rglob("*.json")):
        if path == package / "manifest.json":
            continue
        document = read_json(path)
        relative = path.relative_to(package)
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
        "package_id": "companion.sharing-contracts", "package_version": "0.1.0",
        "profile": "companion.sharing@1", "maturity": "review",
        "dialect": "https://json-schema.org/draft/2020-12/schema",
        "fingerprint_algorithm": "sha256-utf8-lf", "dependencies": dependencies,
        "schemas": schemas, "fixture_schemas": fixture_schemas, "artifacts": artifacts,
    }


def load_validators(package: Path, inventory: dict) -> tuple[dict, dict, Registry, set]:
    registry = Registry(retrieve=unavailable_resource)
    documents, local, legacy, known_ids = [], {}, {}, set()
    for group in ("dependencies", "schemas", "fixture_schemas"):
        for entry in inventory[group]:
            path = ((package / DEPENDENCIES[entry["name"]]).resolve() if group == "dependencies"
                    else package_path(package, entry["path"]))
            document = read_json(path)
            Draft202012Validator.check_schema(document)
            if document["$id"] in known_ids:
                raise ValueError(f"Duplicate schema ID: {document['$id']}")
            known_ids.add(document["$id"])
            documents.append(document)
            registry = registry.with_resource(document["$id"], Resource.from_contents(document))
            if group == "schemas":
                local[entry["name"]] = document
            elif group == "dependencies":
                legacy[entry["name"]] = document
    # Resolve every reference, including branches not reached by the examples.
    for document in documents:
        resolver = registry.resolver(document["$id"])
        pending = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if "$ref" in value:
                    resolver.lookup(value["$ref"])
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    def compile_group(group: dict) -> dict:
        return {name: Draft202012Validator(value, registry=registry, format_checker=FORMATS)
                for name, value in group.items()}
    return compile_group(local), compile_group(legacy), registry, known_ids


def patch_errors(document: dict, registry: Registry, known_ids: set) -> list:
    schema_id = document["patch_schema"]
    if schema_id.partition("#")[0] not in known_ids:
        return ["Patch schema is not registered locally"]
    validator = Draft202012Validator({"$ref": schema_id}, registry=registry, format_checker=FORMATS)
    return [error.message for error in validator.iter_errors(document["patch"])]


def errors_for(validator: Draft202012Validator, document: dict) -> list:
    return [{"path": list(error.absolute_path), "rule": error.validator}
            for error in validator.iter_errors(document)]


def at_path(document: dict, path: list):
    for key in path:
        document = document[key]
    return document


def validate(package: Path) -> dict:
    failures = []
    inventory = build_manifest(package)
    if read_json(package / "manifest.json") != inventory:
        failures.append({"check": "manifest", "error": "Review changed inventory and fingerprints"})
    validators, legacy, registry, known_ids = load_validators(package, inventory)
    suite = read_json(package / "cases.json")
    fixture = read_json(package / "fixtures" / "sharing.fixture.json")
    identifiers, patch_count, legacy_count = set(), 0, 0
    for case in suite["json_cases"]:
        if case["id"] in identifiers:
            failures.append({"check": "duplicate_case", "case": case["id"]})
        identifiers.add(case["id"])
        try:
            parse_json(case["text"])
            valid = True
        except ValueError:
            valid = False
        if valid != case["valid"]:
            failures.append({"check": "strict_json", "case": case["id"]})
    scenario_ids = {scenario["case_id"] for scenario in fixture["scenarios"]}
    if scenario_ids != {f"SHR-{number:02d}" for number in range(1, 13)} or len(fixture["scenarios"]) != 12:
        failures.append({"check": "scenario_inventory"})
    for case in suite["cases"]:
        if case["id"] in identifiers:
            failures.append({"check": "duplicate_case", "case": case["id"]})
        identifiers.add(case["id"])
        document = change_document(read_json(package_path(package, case["document"])), case.get("changes", []))
        errors = errors_for(validators[case["schema"]], document)
        if (not errors) != case["valid"]:
            failures.append({"check": "schema_case", "case": case["id"], "errors": errors[:5]})
        if "patch_valid" in case:
            patch_count += 1
            nested = patch_errors(document, registry, known_ids) if not errors else ["Invalid envelope"]
            if (not nested) != case["patch_valid"]:
                failures.append({"check": "domain_patch", "case": case["id"], "errors": nested[:5]})
        if "legacy_schema" in case:
            legacy_count += 1
            if legacy[case["legacy_schema"]].is_valid(document) != case["legacy_valid"]:
                failures.append({"check": "base_schema_compatibility", "case": case["id"]})
        if "runtime_scenario" in case and case["runtime_scenario"] not in scenario_ids:
            failures.append({"check": "runtime_scenario_reference", "case": case["id"]})
    documents, example_paths = {}, set()
    for name, artifact in fixture["artifacts"].items():
        path = package_path(package, artifact["document"])
        example_paths.add(path)
        document = read_json(path)
        documents[name] = document
        errors = errors_for(validators[artifact["schema"]], document)
        if "patch_schema" in document:
            errors.extend(patch_errors(document, registry, known_ids))
        if errors:
            failures.append({"check": "fixture_artifact", "artifact": name, "errors": errors[:5]})
    if example_paths != {path.resolve() for path in (package / "examples").glob("*.json")}:
        failures.append({"check": "unvalidated_example"})
    for assertion in fixture["static_assertions"]:
        left = assertion["left"]
        right = assertion["right"]
        equal = at_path(documents[left["artifact"]], left["path"]) == at_path(documents[right["artifact"]], right["path"])
        if assertion["relation"] not in ("equal", "not_equal") or equal != (assertion["relation"] == "equal"):
            failures.append({"check": "static_correlation", "assertion": assertion["id"]})
    if fixture["contract_package_version"] != inventory["package_version"]:
        failures.append({"check": "fixture_version"})
    for item in [fixture, *fixture["scenarios"]]:
        if item["run_status"] != "not_run" or item["actual"] is not None:
            failures.append({"check": "fixture_is_design_only", "case": item.get("case_id")})
    for scenario in fixture["scenarios"]:
        if not set(scenario["schema_case_ids"]).issubset(identifiers):
            failures.append({"check": "scenario_case_reference", "case": scenario["case_id"]})
    return {
        "profile": inventory["profile"], "package_version": inventory["package_version"],
        "schema_count": len(validators), "dependency_schemas": len(legacy),
        "fixture_schemas": len(inventory["fixture_schemas"]), "schema_cases": len(suite["cases"]),
        "negative_schema_cases": sum(not case["valid"] for case in suite["cases"]),
        "json_cases": len(suite["json_cases"]), "domain_patch_cases": patch_count,
        "legacy_shape_cases": legacy_count, "fixture_artifacts": len(documents),
        "static_correlations": len(fixture["static_assertions"]),
        "runtime_scenarios": len(scenario_ids), "run_status": "not_run",
        "checked": ["strict_utf8_json", "local_schema_refs", "schema_meta_validation", "fingerprints",
                    "wire_shapes", "registered_domain_patch", "base_schema_compatibility", "static_fixture_correlations"],
        "not_run": ["trusted_anchor_resolution", "scope_policy_selection", "live_acl_and_audience", "cross_platform_identity",
                    "memory_writer", "policy_switch_and_retraction", "subscription_isolation", "resource_peaks", "platform_replay"],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--print-manifest", action="store_true", help="Print hashes for review; never writes files")
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
