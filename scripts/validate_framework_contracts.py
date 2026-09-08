"""Validate the offline design package without importing any plugin runtime."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = ROOT / "docs" / "contracts" / "v1"
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def check_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    return parsed.tzinfo is not None


def reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON number exceeds the finite float range")
    return result


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(text: str) -> object:
    return json.loads(
        text,
        parse_constant=reject_constant,
        parse_float=finite_float,
        object_pairs_hook=unique_object,
    )


def read_json(path: Path) -> object:
    return parse_json(path.read_text(encoding="utf-8"))


def package_path(package: Path, relative: str) -> Path:
    path = (package / relative).resolve()
    if not path.is_relative_to(package.resolve()):
        raise ValueError("Package reference leaves the contract directory")
    return path


def fingerprint(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    data.decode("utf-8")
    return hashlib.sha256(data).hexdigest()


def build_manifest(package: Path) -> dict:
    schema_entries = []
    artifacts = []
    for path in sorted(package.rglob("*.json")):
        if path == package / "manifest.json":
            continue
        document = read_json(path)
        entry = {"path": path.relative_to(package).as_posix(), "sha256": fingerprint(path)}
        if path.parent.name == "schemas":
            schema_entries.append({"name": path.name.removesuffix(".schema.json"), "id": document["$id"], **entry})
        else:
            artifacts.append(entry)
    return {
        "package_id": "companion.memory-contracts",
        "package_version": "0.2.0",
        "maturity": "review",
        "dialect": "https://json-schema.org/draft/2020-12/schema",
        "fingerprint_algorithm": "sha256-utf8-lf",
        "legacy_namespace_fingerprint": "49398a609b60cadf",
        "schemas": schema_entries,
        "artifacts": artifacts,
    }


def unavailable_resource(uri: str) -> Resource:
    raise ValueError(f"Schema reference is not registered locally: {uri}")


def validators_for(package: Path) -> dict:
    schemas = {}
    registry = Registry(retrieve=unavailable_resource)
    for path in sorted((package / "schemas").glob("*.schema.json")):
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
        name = path.name.removesuffix(".schema.json")
        schemas[name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return {
        name: Draft202012Validator(schema, registry=registry, format_checker=FORMATS)
        for name, schema in schemas.items()
    }


def change_document(document: object, changes: list[dict]) -> object:
    result = copy.deepcopy(document)
    for change in changes:
        target = result
        keys = change["path"]
        if not keys:
            raise ValueError("Example changes must name an object field")
        for key in keys[:-1]:
            target = target[key]
        if change.get("remove"):
            del target[keys[-1]]
        else:
            target[keys[-1]] = copy.deepcopy(change["value"])
    return result


def load_namespace(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the dependency-free namespace contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check_legacy_examples(package: Path, validators: dict, failures: list) -> int:
    fixtures = read_json(package / "compatibility.json")
    sources = [
        ROOT / "identity_namespace.py",
        ROOT.parent / "astrbot_plugin_remember_you" / "core" / "namespace.py",
    ]
    modules = [load_namespace(path, f"_contract_namespace_{i}") for i, path in enumerate(sources)]
    for module in modules:
        if module.CONTRACT_FINGERPRINT != fixtures["legacy_fingerprint"]:
            failures.append({"check": "legacy_fingerprint", "source": module.__name__})
    count = 0
    for case in fixtures["cases"]:
        count += 1
        for module in modules:
            errors = module.validate_namespace_context(case["legacy_context"])
            context = module.build_namespace_context(case["legacy_context"])
            decision = module.AssurancePolicy.authorize(context, case["purpose"])
            if errors or {"allowed": decision.allowed, "code": decision.code} != case["expected"]:
                failures.append({"case": case["id"], "check": "legacy_policy", "source": module.__name__})
    for case in fixtures["isolation_examples"]:
        count += 1
        first, second = case["owners"]
        for owner in case["owners"]:
            validators["memory-owner"].validate(owner)
        context_a, context_b = case["legacy_contexts"]
        for module in modules:
            if module.validate_namespace_context(context_a) or module.validate_namespace_context(context_b):
                failures.append({"case": case["id"], "check": "legacy_shape"})
                continue
            same_legacy_key = module.build_namespace_context(context_a).cache_scope() == module.build_namespace_context(context_b).cache_scope()
            if first == second or not same_legacy_key:
                failures.append({"case": case["id"], "check": "expected_legacy_isolation_gap"})
    return count


def check_subscription_fixture(package: Path, validators: dict, cases: list, schemas: list, failures: list) -> dict:
    fixture = read_json(package / "subscription-recovery.fixture.json")
    canonical = "".join(f"{item['id']}\t{item['sha256']}\n" for item in sorted(schemas, key=lambda item: item["id"]))
    expected_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if fixture["contract_package_version"] != "0.2.0" or fixture["contract_fingerprint"] != expected_fingerprint:
        failures.append({"check": "subscription_fixture_version_or_fingerprint"})
    if fixture["run_status"] != "not_run" or fixture["actual"] is not None:
        failures.append({"check": "subscription_fixture_is_design_only"})
    for name, artifact in fixture["artifacts"].items():
        document = read_json(package_path(package, artifact["document"]))
        errors = list(validators[artifact["schema"]].iter_errors(document))
        if errors:
            failures.append({"check": "subscription_fixture_artifact", "artifact": name, "errors": [error.message for error in errors[:5]]})
        pending = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if "contract_fingerprint" in value and value["contract_fingerprint"] != expected_fingerprint:
                    failures.append({"check": "subscription_artifact_fingerprint", "artifact": name})
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    known_cases = {case["id"] for case in cases}
    scenario_ids = [scenario["case_id"] for scenario in fixture["scenarios"]]
    if sorted(scenario_ids) != [f"SUB-{index:02d}" for index in range(1, 17)]:
        failures.append({"check": "subscription_fixture_scenarios"})
    for scenario in fixture["scenarios"]:
        if not set(scenario["schema_case_ids"]).issubset(known_cases):
            failures.append({"check": "subscription_fixture_case_reference", "case": scenario["case_id"]})
        if scenario["run_status"] != "not_run" or scenario["actual"] is not None:
            failures.append({"check": "subscription_scenario_is_design_only", "case": scenario["case_id"]})
    for step in fixture["steps"]:
        for field in ("request", "result", "delivery", "checkpoint"):
            if field in step and step[field] not in fixture["artifacts"]:
                failures.append({"check": "subscription_fixture_step_reference", "sequence": step["sequence"]})
    return {"artifacts": len(fixture["artifacts"]), "scenario_links": len(scenario_ids), "run_status": "not_run"}


def validate(package: Path) -> dict:
    failures = []
    expected_manifest = build_manifest(package)
    manifest = read_json(package / "manifest.json")
    if manifest != expected_manifest:
        failures.append({"check": "manifest", "error": "File inventory or fingerprints changed; review the package revision"})
    validators = validators_for(package)
    casebooks = {
        name: read_json(package / name)
        for name in ("cases.json", "subscription-cases.json")
    }
    cases = [case for book in casebooks.values() for case in book["cases"]]
    json_cases = [case for book in casebooks.values() for case in book["json_cases"]]
    for case in json_cases:
        try:
            parse_json(case["text"])
            valid = True
        except ValueError:
            valid = False
        if valid != case["valid"]:
            failures.append({"case": case["id"], "check": "strict_json"})
    identifiers = set()
    for case in cases:
        if case["id"] in identifiers:
            failures.append({"case": case["id"], "check": "duplicate_case_id"})
        identifiers.add(case["id"])
        document = read_json(package_path(package, case["document"]))
        document = change_document(document, case.get("changes", []))
        errors = list(validators[case["schema"]].iter_errors(document))
        if (not errors) != case["valid"]:
            failures.append({
                "case": case["id"], "check": "schema",
                "expected_valid": case["valid"],
                "errors": [{"path": list(error.absolute_path), "rule": error.validator} for error in errors[:5]],
            })
    compatibility_count = check_legacy_examples(package, validators, failures)
    fixture_checks = check_subscription_fixture(package, validators, cases, expected_manifest["schemas"], failures)
    return {
        "package_version": manifest["package_version"],
        "schema_count": len(validators),
        "schema_cases": len(cases),
        "schema_case_suites": {name: len(book["cases"]) for name, book in casebooks.items()},
        "json_cases": len(json_cases),
        "compatibility_examples": compatibility_count,
        "subscription_fixture": fixture_checks,
        "checked": ["utf8_json", "finite_numbers", "unique_json_keys", "local_schema_registry", "schema_meta_validation", "explicit_datetime_validation", "package_fingerprints", "legacy_policy_examples", "subscription_fixture_references_and_shapes"],
        "not_run": ["authorization_service", "live_identity_mapping", "request_result_correlation", "budget_accounting", "timestamp_ordering", "evidence_authorization", "writer_atomicity", "outbox_recovery", "generation_fence", "subscription_binding", "lease_ack_ordering", "snapshot_consistency", "checkpoint_durability", "resource_peaks", "platform_replay"],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--print-manifest", action="store_true", help="Print reviewed file hashes; never writes files")
    args = parser.parse_args()
    try:
        result = build_manifest(args.package) if args.print_manifest else validate(args.package)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(bool(result.get("failures")))
    except (ValueError, KeyError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
