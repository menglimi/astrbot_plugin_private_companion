#!/usr/bin/env python3
"""Release gate for AstrBot plugin archives, installs, upgrades, and rollback.

The gate only writes below the system temporary directory.  It builds the upload
ZIP with ``build_plugin_package.py``, validates its manifest and metadata, then
uses the real AstrBot updater to exercise clean install, upgrade, rollback, and
Windows long-path extraction/import behavior.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


TEMP_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov"}
TEMP_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".bak", ".db-journal", ".db-shm", ".db-wal")
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[A-Za-z][A-Za-z0-9.-]*)?$")


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+", subprocess.list2cmdline(command))
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        result.check_returncode()
    return result


def metadata_value(path: Path, key: str) -> str:
    match = re.search(
        rf"^{re.escape(key)}:\s*['\"]?(?P<value>[^'\"\s#]+)",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError(f"metadata key missing: {key}")
    return match.group("value")


def identity_constants(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {"PLUGIN_ID", "PLUGIN_VERSION"}:
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                values[target.id] = value
    return values


def snapshot_temp_artifacts(root: Path) -> set[str]:
    findings: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in TEMP_NAMES for part in relative.parts):
            findings.add(relative.as_posix())
        elif path.is_file() and path.name.endswith(TEMP_SUFFIXES):
            findings.add(relative.as_posix())
    return findings


def tracked_temp_artifacts(root: Path) -> list[str]:
    result = run(["git", "ls-files"], cwd=root)
    bad: list[str] = []
    for name in result.stdout.splitlines():
        path = PurePosixPath(name)
        if any(part in TEMP_NAMES or part in {"dist", "build"} for part in path.parts):
            bad.append(name)
        elif path.name.endswith(TEMP_SUFFIXES) or path.name == ".coverage":
            bad.append(name)
    return bad


def load_builder(root: Path):
    path = root / "scripts" / "build_plugin_package.py"
    spec = importlib.util.spec_from_file_location("release_package_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load package builder: {path}")
    module = importlib.util.module_from_spec(spec)
    # Loading the builder is validation, not a runtime import. Suppress its
    # bytecode cache so auditing does not dirty an otherwise clean worktree.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def inspect_archive(archive_path: Path, root: Path, builder) -> tuple[str, str, set[str]]:
    plugin_name = metadata_value(root / "metadata.yaml", "name")
    version = metadata_value(root / "metadata.yaml", "version")
    identity = identity_constants(root / "plugin_identity.py")
    if identity.get("PLUGIN_ID") != plugin_name:
        raise RuntimeError(f"plugin ID mismatch: metadata={plugin_name!r}, identity={identity.get('PLUGIN_ID')!r}")
    if identity.get("PLUGIN_VERSION") != version:
        raise RuntimeError(
            f"plugin version mismatch: metadata={version!r}, identity={identity.get('PLUGIN_VERSION')!r}"
        )
    if not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError(f"unsupported release version syntax: {version!r}")

    expected = {
        f"{plugin_name}/{path.relative_to(root).as_posix()}"
        for path in builder.collect_runtime_files(root)
    }
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("archive CRC validation failed")
        names = archive.namelist()
        actual = set(names)
        if len(actual) != len(names):
            raise RuntimeError("archive has duplicate members")
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise RuntimeError(f"archive file set mismatch; missing={missing[:20]}, extra={extra[:20]}")
        packaged_metadata = archive.read(f"{plugin_name}/metadata.yaml")
        if packaged_metadata != (root / "metadata.yaml").read_bytes():
            raise RuntimeError("packaged metadata differs from source metadata")
        for name in names:
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != plugin_name or ".." in parts or name.startswith("/"):
                raise RuntimeError(f"unsafe archive member: {name}")
            if any(part in TEMP_NAMES or part in {".git", ".github", "tests", "scripts", "docs"} for part in parts):
                raise RuntimeError(f"non-runtime artifact in archive: {name}")
            if name.endswith(TEMP_SUFFIXES):
                raise RuntimeError(f"temporary artifact in archive: {name}")
    return plugin_name, version, actual


def astrbot_environment(astrbot_root: Path, runtime_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ASTRBOT_ROOT"] = str(runtime_root)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(runtime_root), str(astrbot_root), env.get("PYTHONPATH", "")])
    )
    return env


def import_installed(plugin_name: str, astrbot_root: Path, runtime_root: Path) -> None:
    statement = (
        "import importlib;"
        f"m=importlib.import_module('data.plugins.{plugin_name}.main');"
        "assert getattr(m,'UnifiedPersonRegistry').__module__.endswith('.unified_person_registry');"
        "print('formal-import-ok')"
    )
    result = run(
        [sys.executable, "-c", statement],
        cwd=runtime_root,
        env=astrbot_environment(astrbot_root, runtime_root),
    )
    if "formal-import-ok" not in result.stdout:
        raise RuntimeError("installed plugin import did not complete")


def updater_extract(archive: Path, destination: Path, astrbot_root: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    statement = (
        "from astrbot.core.star.updater import _PluginUpdater;"
        f"_PluginUpdater()._extract_plugin_archive({str(archive)!r},{str(destination)!r})"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(astrbot_root), env.get("PYTHONPATH", "")]))
    # Keep the child process cwd short. Windows CreateProcess can reject a long
    # cwd before AstrBot's own long-path-safe extraction code gets to run.
    run([sys.executable, "-c", statement], cwd=astrbot_root, env=env)


def install_archive(archive: Path, plugin_name: str, astrbot_root: Path, runtime_root: Path) -> Path:
    plugin_store = runtime_root / "data" / "plugins"
    plugin_store.mkdir(parents=True, exist_ok=True)
    staging = runtime_root / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    # AstrBot removes the upload ZIP after extraction, matching production
    # upload cleanup. Preserve the gate's canonical artifact via a per-install copy.
    upload = runtime_root / "plugin-upload.zip"
    shutil.copy2(archive, upload)
    updater_extract(upload, staging, astrbot_root)
    target = plugin_store / plugin_name
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    return target


def make_previous_archive(root: Path, output: Path) -> Path | None:
    tags = run(["git", "tag", "--merged", "HEAD", "--sort=-version:refname"], cwd=root).stdout.splitlines()
    if not tags:
        return None
    tag = tags[0]
    if run(["git", "rev-parse", tag], cwd=root).stdout.strip() == run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip():
        tags = tags[1:]
    if not tags:
        return None
    tag = tags[0]
    source = output.parent / "previous-source"
    source.mkdir()
    archive = output.parent / "previous-source.zip"
    run(["git", "archive", "--format=zip", f"--output={archive}", tag], cwd=root)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(source)
    previous_builder = source / "scripts" / "build_plugin_package.py"
    if not previous_builder.is_file():
        return None
    run([sys.executable, str(previous_builder), "--root", str(source), "--output", str(output)], cwd=source)
    return output


def exercise_installs(current: Path, previous: Path | None, plugin_name: str, astrbot_root: Path, base: Path) -> None:
    clean_runtime = base / "clean-runtime"
    install_archive(current, plugin_name, astrbot_root, clean_runtime)
    import_installed(plugin_name, astrbot_root, clean_runtime)

    # Keep the extraction root itself below MAX_PATH so CreateProcess and
    # directory setup remain valid, while packaged deep members exceed it.
    long_runtime = base / ("windows-long-path-" + "x" * 20) / ("nested-" + "y" * 20)
    install_archive(current, plugin_name, astrbot_root, long_runtime)
    import_installed(plugin_name, astrbot_root, long_runtime)
    longest = max(len(str(path)) for path in long_runtime.rglob("*"))
    if os.name == "nt" and longest <= 260:
        raise RuntimeError(f"long-path exercise did not exceed MAX_PATH: {longest}")

    if previous is None:
        print("WARN: no prior tagged package builder available; upgrade/rollback gate skipped")
        return
    upgrade_runtime = base / "upgrade-runtime"
    old_target = install_archive(previous, plugin_name, astrbot_root, upgrade_runtime)
    import_installed(plugin_name, astrbot_root, upgrade_runtime)
    rollback_copy = base / "rollback-copy"
    shutil.copytree(old_target, rollback_copy)
    install_archive(current, plugin_name, astrbot_root, upgrade_runtime)
    import_installed(plugin_name, astrbot_root, upgrade_runtime)
    shutil.rmtree(upgrade_runtime / "data" / "plugins" / plugin_name)
    shutil.copytree(rollback_copy, upgrade_runtime / "data" / "plugins" / plugin_name)
    import_installed(plugin_name, astrbot_root, upgrade_runtime)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--astrbot-root", type=Path, default=os.environ.get("ASTRBOT_SOURCE_ROOT"))
    parser.add_argument("--previous-archive", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    astrbot_root = args.astrbot_root.resolve() if args.astrbot_root else None
    if astrbot_root is None or not (astrbot_root / "astrbot" / "__init__.py").is_file():
        raise SystemExit("--astrbot-root must point to the real AstrBot source directory containing astrbot/__init__.py")

    tracked_bad = tracked_temp_artifacts(root)
    if tracked_bad:
        raise SystemExit(f"tracked temporary/build artifacts: {tracked_bad}")
    before = snapshot_temp_artifacts(root)
    builder = load_builder(root)
    with tempfile.TemporaryDirectory(prefix="private-companion-release-gate-") as temporary:
        base = Path(temporary)
        current = base / "current.zip"
        first = builder.build_package(root, current)
        second = builder.build_package(root, base / "current-rebuilt.zip")
        if hashlib.sha256(first.read_bytes()).digest() != hashlib.sha256(second.read_bytes()).digest():
            raise RuntimeError("package build is not reproducible")
        plugin_name, version, names = inspect_archive(current, root, builder)
        previous = args.previous_archive.resolve() if args.previous_archive else make_previous_archive(root, base / "previous.zip")
        exercise_installs(current, previous, plugin_name, astrbot_root, base)
        print(json.dumps({"plugin": plugin_name, "version": version, "files": len(names), "sha256": hashlib.sha256(current.read_bytes()).hexdigest()}, ensure_ascii=False))
    after = snapshot_temp_artifacts(root)
    created = sorted(after - before)
    if created:
        raise RuntimeError(f"release gate left temporary validation artifacts: {created}")
    print("RELEASE GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
