"""Verify one self-contained V2 release and optionally check its isolated host."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

if __package__:
    from .build_v2_release import (CORE_ID, NATIVE_CAPABILITY_BASELINE, NATIVE_EXTENSION_FILES,
        RELEASE_SCHEMA, VERSION, native_artifact_hashes, verify_native_baseline, verify_runtime_sources)
else:
    from build_v2_release import (CORE_ID, NATIVE_CAPABILITY_BASELINE, NATIVE_EXTENSION_FILES,
        RELEASE_SCHEMA, VERSION, native_artifact_hashes, verify_native_baseline, verify_runtime_sources)

MIGRATION_ACCEPTANCE_MODULES = (
    "tests.test_storage_runtime",
    "tests.test_storage_api",
    "tests.test_data_root_compat",
    "tests.test_configuration_migration",
    "tests.test_legacy_discovery_profile_state",
    "tests.test_legacy_persona_mapping",
    "tests.test_upgrade_source",
    "tests.test_astrbot_native",
    "tests.test_astrbot_multi_host",
    "tests.test_main_core_reply",
    "tests.test_main_no_reply",
    "tests.test_session_topology",
    "tests.test_legacy_reply_configuration",
    "tests.test_legacy_proactive_configuration",
    "tests.test_legacy_expression_data",
    "tests.test_expression_sharing",
    "tests.test_expression_style",
    "tests.test_legacy_data_acceptance_process",
    "tests.test_legacy_memory_archive",
    "tests.test_legacy_memory_package_archive",
    "tests.test_legacy_private_memory",
    "tests.test_legacy_memory_materials",
    "tests.test_legacy_sqlite_snapshot",
    "tests.test_legacy_archive",
    "tests.test_legacy_data_host",
    "tests.test_voice_reply",
    "tests.test_private_debounce",
    "tests.test_core_reply_voice",
    "tests.test_reply_management",
    "tests.test_role_profile_migration",
    "tests.test_multi_target_install_apis",
    "tests.test_background_history",
    "tests.test_proactive_history_limits",
    "tests.test_release_secret_detection",
    "tests.test_native_release_version_guard",
)


def _verify_v2_contracts(core: Path) -> dict[str, object]:
    """Check the release's executable migration and binding contracts.

    These checks intentionally import only the extracted package.  A source-tree
    test could pass while the builder omitted a fixture or a legacy module.
    """

    sys.path.insert(0, str(core))
    try:
        from companion_v2.adapters.astrbot.staging_config import StagingConfiguration
        from companion_v2.legacy.catalog import CatalogAction, LEGACY_COMPATIBILITY_CATALOG
        from companion_v2.legacy.configuration_migration import convert_account_configuration
        from companion_v2.legacy.data_migration import build_data_plan, map_legacy_worldbook
        from companion_v2.legacy.migration import HANDLERS

        fields = {field.name: field for field in dataclasses.fields(StagingConfiguration)}
        if fields["delivery_mode"].default != "onebot_private":
            raise ValueError("StagingConfiguration delivery_mode default is not onebot_private")

        fixture_path = core / "fixtures" / "astrbot_staging.example.json"
        fixture = StagingConfiguration.parse(fixture_path.read_text(encoding="utf-8"))
        if fixture.delivery_mode != "onebot_private":
            raise ValueError("legacy preview fixture did not normalize to onebot_private")

        weather_path = core / "companion_v2" / "legacy" / "weather_migration.py"
        if not weather_path.is_file():
            raise ValueError("weather migration source missing from release")
        expected_handlers = frozenset({"v2.private-config-converter",
            "v2.role-journal-importer", "v2.role-journal-and-worldbook-importer",
            "v2.group-relationship-importer", "v2.reaction-asset-importer",
            "v2.memory-snapshot-archive", "v2.reality-config-migrator",
            "v2.person-private-memory-importer", "v2.image-config-migrator",
            "v2.native-extension-config-migrator", "v2.content-config-migrator",
            "v2.memory-config-migrator"})
        # Adding an implemented migration must not invalidate the whole release.
        # Required upgrade paths still cannot disappear from a later package.
        missing_handlers = expected_handlers - HANDLERS
        if missing_handlers:
            raise ValueError("required legacy migration handlers missing: " + ", ".join(sorted(missing_handlers)))
        if not callable(build_data_plan) or not callable(map_legacy_worldbook):
            raise ValueError("V1 data migration converters are missing from the release")

        account_plan = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "allowlist", "bot_scope_ids": ["100"],
                             "target_user_ids": ["200"], "plugin_specific_persona_id": "native"},
        }, accounts=({"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-100",
                      "persona_roles": {"native": "role-100-native", "[%None]": "role-100-default",
                                        "": "role-100-default"}},))
        expected_identity = {
            "target.platform_id": "qq-main", "target.account_id": "100",
            "target.bot_id": "bot-100", "target.participant_id": "200",
            "target.role_id": "role-100-native", "target.persona_id": "native",
        }
        if any(account_plan.changes.get(field) != value for field, value in expected_identity.items()):
            raise ValueError("account configuration migration contract failed")
        if account_plan.changes.get("multi_host.targets", [{}])[0].get("participant_id") != "200":
            raise ValueError("multi-target configuration migration contract failed")
        if "multi_host.enabled" in account_plan.changes:
            raise ValueError("account migration must not change the selected runtime mode")

        data_sources = {
            "private.core_profiles", "private.group_relationship_graph",
            "private.reaction_assets", "private.owned_reaction_assets", "private.authoritative_memory",
        }
        for source_id in data_sources:
            if LEGACY_COMPATIBILITY_CATALOG.entry(source_id).action is not CatalogAction.DATA_CONVERTED:
                raise ValueError("legacy data source is not marked DATA_CONVERTED: " + source_id)
        documented_skips = {"private.work_and_delivery",
                            "private.world_assets"}
        for source_id in documented_skips:
            if LEGACY_COMPATIBILITY_CATALOG.entry(source_id).action is not CatalogAction.DOCUMENTED_SKIP:
                raise ValueError("legacy data boundary is not documented: " + source_id)
        return {
            "delivery_mode_default": "onebot_private",
            "legacy_preview_normalized": True,
            "weather_migration": True,
            "account_configuration_migration": True,
            "handlers": sorted(HANDLERS),
            "data_sources": sorted(data_sources),
            "documented_skip_sources": sorted(documented_skips),
        }
    finally:
        # Keep verification imports from affecting the optional host check below.
        sys.path.remove(str(core))


def _host_check_environment(temporary_root: str, core: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["ASTRBOT_ROOT"] = temporary_root

    # AstrBot's bundled Python keeps the backend application beside its venv.
    app_path = Path(sys.prefix).resolve().parent / "app"
    if not (app_path / "astrbot").is_dir():
        raise ValueError("--host-check requires AstrBot's bundled Python with backend/app beside it")
    # Do not let a developer's PYTHONPATH satisfy missing candidate modules.
    environment["PYTHONPATH"] = os.pathsep.join([str(Path(core).resolve()), str(app_path)])
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_host_process(core: str, environment: dict[str, str], arguments: list[str],
                      label: str, timeout: int) -> str:
    try:
        result = subprocess.run([sys.executable, "-X", "utf8", *arguments],
            cwd=core, env=environment, timeout=timeout, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        raise RuntimeError(f"{label} exceeded {timeout}s:\n{output[-12000:]}") from error
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}:\n{result.stdout[-12000:]}")
    return result.stdout


def _run_migration_acceptance(core: str, environment: dict[str, str]) -> dict[str, object]:
    # SDK imports start process-wide services. Isolate modules so one module's
    # scheduler, event loop or monkeypatch cannot alter another module's host.
    modules = []
    for module in MIGRATION_ACCEPTANCE_MODULES:
        output = _run_host_process(core, environment,
            ["-c", "import tests.astrbot_data_root; import unittest; unittest.main(module=None)",
             "-v", module], "candidate migration acceptance: " + module, timeout=300)
        summary = next((line.strip() for line in reversed(output.splitlines())
                        if line.strip().startswith("Ran ")), "")
        if not summary:
            raise RuntimeError("candidate migration acceptance did not report a unittest summary: " + module)
        try:
            tests_run = int(summary.split()[1])
        except (IndexError, ValueError) as error:
            raise RuntimeError("candidate migration acceptance returned an invalid unittest summary: " + module) from error
        modules.append({"module": module, "tests_run": tests_run, "status": "passed"})
    return {"status": "passed", "tests_run": sum(item["tests_run"] for item in modules),
            "modules": list(MIGRATION_ACCEPTANCE_MODULES), "module_results": modules}


def verify(release, destination):
    release = release.resolve()
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("verification destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != RELEASE_SCHEMA or manifest.get("version") != VERSION:
        raise ValueError("not a current self-contained V2 release manifest")
    if (manifest.get("product") != "我会永远陪着你"
            or manifest.get("data_policy") != "隔离运行数据；仅在用户确认后读取旧版来源并写入新数据根"
            or manifest.get("provider_integration") != "Seven native internal extensions are bundled and owned by one plugin"):
        raise ValueError("release product identity or data policy is stale")
    packages = manifest.get("packages", [])
    if len(packages) != 1 or packages[0].get("plugin_id") != CORE_ID:
        raise ValueError("a V2 release must contain exactly one installable V2 plugin")
    if manifest.get("native_extensions") != sorted(NATIVE_EXTENSION_FILES) or manifest.get("external_plugin_dependencies") != []:
        raise ValueError("release must bundle seven internal extensions without companion plugin dependencies")
    reports = []
    runtime_contract = None
    for package in packages:
        if package.get("version") != VERSION or package.get("execution_owner") != CORE_ID or package.get("integration") != "native-bundled":
            raise ValueError("invalid single-plugin execution owner or version")
        filename = package["filename"]
        if Path(filename).name != filename:
            raise ValueError("invalid archive filename")
        source = release / filename
        if hashlib.sha256(source.read_bytes()).hexdigest() != package["sha256"]:
            raise ValueError("package hash mismatch: " + filename)
        prefix = package["plugin_id"] + "/"
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("duplicate archive entries")
            for name in names:
                target = (destination / name).resolve()
                if (not name.startswith(prefix) or "\\" in name or not target.is_relative_to(destination)
                        or any(part in {"..", "data", ".git", "__pycache__", "node_modules", "protected_model"} for part in Path(name).parts)):
                    raise ValueError("invalid source-only archive path")
            if archive.testzip() is not None:
                raise ValueError("invalid archive CRC")
            content = json.loads(archive.read(prefix + "RELEASE_FILES.json"))
            if content.get("product_version") != VERSION or any(content.get("plugin", {}).get(key) != package.get(key)
                                                               for key in ("plugin_id", "version", "astrbot_version")):
                raise ValueError("package metadata and release manifest disagree")
            expected = {prefix + name for name in content["files"]} | {prefix + "RELEASE_FILES.json"}
            if set(names) != expected:
                raise ValueError("file inventory mismatch")
            python_count = 0
            relative_files = {}
            for name, digest in content["files"].items():
                payload = archive.read(prefix + name)
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError("file hash mismatch: " + name)
                if name.endswith(".py"):
                    compile(payload.decode("utf-8"), name, "exec", dont_inherit=True)
                    python_count += 1
                relative_files[name] = payload
            runtime_contract = verify_runtime_sources(relative_files)
            verify_native_baseline(VERSION, native_artifact_hashes(relative_files),
                                   json.loads(relative_files[NATIVE_CAPABILITY_BASELINE]))
            if any(content.get(key) != value or package.get(key) != value for key, value in runtime_contract.items()):
                raise ValueError("bundled native extension or unique registration contract mismatch")
            # Paths have been validated above; destination contains no user data.
            archive.extractall(destination)
            reports.append({"plugin_id": package["plugin_id"], "files": len(names), "python_files": python_count})
    core = destination / CORE_ID
    import yaml
    metadata = yaml.safe_load((core / "metadata.yaml").read_text(encoding="utf-8"))
    if metadata.get("repo") != "https://github.com/menglimi/astrbot_plugin_private_companion":
        raise ValueError("release metadata must preserve the public repository update source")
    schema = json.loads((core / "_conf_schema.json").read_text(encoding="utf-8"))
    if schema["staging"]["items"]["enabled"]["default"] is not False or schema["multi_host"]["items"]["enabled"]["default"] is not False:
        raise ValueError("default host configuration must remain disabled")
    if [p.name for p in (core / "pages").iterdir() if (p / "index.html").is_file()] != ["陪伴小屋"]:
        raise ValueError("V2 must expose exactly one Dashboard page: 陪伴小屋")
    contracts = _verify_v2_contracts(core)
    return {"packages": reports, "core": str(core), "default_enabled": False,
            "public_pages": ["陪伴小屋"], "hashes_and_syntax": "passed",
            "contracts": contracts, **runtime_contract}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--extract-to", required=True, type=Path)
    parser.add_argument("--host-check", action="store_true")
    args = parser.parse_args()
    report = verify(args.release, args.extract_to)
    if args.host_check:
        source_tests = Path(__file__).resolve().parents[1] / "tests"
        if not (source_tests / "multi_life_entry_process.py").is_file():
            raise ValueError("--host-check requires the tests directory from the source checkout")
        # Test helpers resolve the plugin root relative to their own files.
        with TemporaryDirectory(prefix="companion-v2-release-host-check-") as temporary:
            candidate = Path(temporary) / CORE_ID
            shutil.copytree(report["core"], candidate)
            shutil.copytree(source_tests, candidate / "tests", ignore=shutil.ignore_patterns("__pycache__"))
            core = str(candidate)
            environment = _host_check_environment(temporary, core)
            output = _run_host_process(core, environment,
                ["-m", "tests.multi_life_entry_process"],
                "candidate host lifecycle check", timeout=90)
            report["migration_acceptance"] = _run_migration_acceptance(core, environment)
            reload_output = _run_host_process(core, environment,
                ["-m", "tests.plugin_reload_process"],
                "candidate plugin reload check", timeout=300)
            report["reload_check"] = json.loads(next(line for line in reversed(
                reload_output.splitlines()) if line.startswith('{"startup_cycles"')))
        lines = output.splitlines()
        report["host_check"] = json.loads(next(line for line in reversed(lines) if line.startswith('{"life_routes"')))
    (args.extract_to / "package-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
