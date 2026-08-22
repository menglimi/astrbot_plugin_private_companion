from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_plugin_package.py"
ASTRBOT_ROOT = ROOT.parent / "AstrBot"
ARCHIVE_ROOT = "astrbot_plugin_private_companion"


def _build(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_plugin_package_contains_complete_runtime_tree(tmp_path: Path) -> None:
    output = tmp_path / "private-companion.zip"

    result = _build(output)

    assert output.is_file()
    assert "SHA256:" in result.stdout
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        assert archive.read(f"{ARCHIVE_ROOT}/metadata.yaml") == (
            ROOT / "metadata.yaml"
        ).read_bytes()
    required = {
        f"{ARCHIVE_ROOT}/main.py",
        f"{ARCHIVE_ROOT}/plugin_bootstrap.py",
        f"{ARCHIVE_ROOT}/unified_person_registry.py",
        f"{ARCHIVE_ROOT}/persona_config.py",
        f"{ARCHIVE_ROOT}/pages/companion-panel/index.html",
        f"{ARCHIVE_ROOT}/pages/陪伴面板/index.html",
        f"{ARCHIVE_ROOT}/storage/backend_base.py",
    }
    assert required.issubset(names)
    packaged_top_level_python = {
        PurePosixPath(name).name
        for name in names
        if len(PurePosixPath(name).parts) == 2 and name.endswith(".py")
    }
    assert packaged_top_level_python == {path.name for path in ROOT.glob("*.py")}
    for name in names:
        parts = PurePosixPath(name).parts
        assert parts[0] == ARCHIVE_ROOT
        assert not set(parts).intersection(
            {".git", ".github", "data", "docs", "node_modules", "scripts", "tests"}
        )
        assert "__pycache__" not in parts
        assert not name.endswith((".db", ".pyc", ".log", ".tmp"))


def test_plugin_package_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    _build(first)
    _build(second)

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()


def test_plugin_package_imports_from_astrbot_plugin_path(tmp_path: Path) -> None:
    from astrbot.core.star.updater import _PluginUpdater

    output = tmp_path / "private-companion.zip"
    _build(output)
    runtime_root = tmp_path / "runtime"
    plugin_store = runtime_root / "data" / "plugins"
    plugin_store.mkdir(parents=True)
    upload = tmp_path / "plugin-upload.zip"
    extracted = tmp_path / "plugin-upload"
    shutil.copy2(output, upload)
    _PluginUpdater()._extract_plugin_archive(str(upload), str(extracted))
    extracted.rename(plugin_store / ARCHIVE_ROOT)
    assert (plugin_store / ARCHIVE_ROOT / "unified_person_registry.py").is_file()

    environment = os.environ.copy()
    environment["ASTRBOT_ROOT"] = str(runtime_root)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(runtime_root), str(ASTRBOT_ROOT), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib; "
                "module = importlib.import_module("
                "'data.plugins.astrbot_plugin_private_companion.main'); "
                "assert module.UnifiedPersonRegistry.__module__.endswith("
                "'.unified_person_registry'); "
                "print('formal-import-ok')"
            ),
        ],
        cwd=runtime_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "formal-import-ok" in result.stdout


def test_archive_verifier_rejects_missing_registry_module(tmp_path: Path) -> None:
    complete = tmp_path / "complete.zip"
    broken = tmp_path / "broken.zip"
    _build(complete)
    omitted = f"{ARCHIVE_ROOT}/unified_person_registry.py"
    with zipfile.ZipFile(complete) as source, zipfile.ZipFile(broken, "w") as target:
        for info in source.infolist():
            if info.filename != omitted:
                target.writestr(info, source.read(info.filename))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(ROOT),
            "--verify",
            str(broken),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unified_person_registry.py" in result.stderr
