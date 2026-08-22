#!/usr/bin/env python3
"""Build and verify a complete AstrBot upload archive."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
import zipfile
from pathlib import Path


RUNTIME_FILES = (
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "logo.png",
    "README.md",
    "CHANGELOG.md",
)
RUNTIME_DIRECTORIES = (
    ".astrbot-plugin",
    "domains",
    "pages",
    "storage",
)
REQUIRED_ARCHIVE_FILES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "plugin_bootstrap.py",
    "unified_person_registry.py",
    "persona_config.py",
)
IGNORED_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "__pycache__",
}
IGNORED_SUFFIXES = (
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".pyo",
    ".tmp",
)
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _metadata_value(path: Path, key: str) -> str:
    pattern = rf"^{re.escape(key)}:\s*['\"]?(?P<value>[^'\"\s#]+)"
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"cannot read {key} from {path}")
    return match.group("value")


def _is_ignored(relative: Path) -> bool:
    return any(part in IGNORED_NAMES for part in relative.parts) or relative.name.endswith(
        IGNORED_SUFFIXES
    )


def collect_runtime_files(root: Path) -> list[Path]:
    """Collect the complete runtime tree, including uncommitted source changes."""

    missing = [name for name in REQUIRED_ARCHIVE_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError(f"missing required plugin files: {', '.join(missing)}")

    selected: set[Path] = set()
    for path in root.glob("*.py"):
        if path.is_symlink():
            raise ValueError(f"runtime package cannot contain symlinks: {path}")
        if path.is_file():
            selected.add(path)
    for name in RUNTIME_FILES:
        path = root / name
        if path.is_symlink():
            raise ValueError(f"runtime package cannot contain symlinks: {path}")
        if path.is_file():
            selected.add(path)
    for directory_name in RUNTIME_DIRECTORIES:
        directory = root / directory_name
        if directory.is_symlink():
            raise ValueError(f"runtime package cannot contain symlinks: {directory}")
        if not directory.is_dir():
            raise ValueError(f"missing runtime directory: {directory_name}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"runtime package cannot contain symlinks: {path}")
            if path.is_file() and not _is_ignored(path.relative_to(root)):
                selected.add(path)
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def _archive_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def validate_archive(path: Path, archive_root: str) -> None:
    """Reject incomplete, corrupt, or multi-root upload archives."""

    with zipfile.ZipFile(path, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"archive CRC check failed: {bad_member}")
        names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("archive contains duplicate entries")
    expected = {f"{archive_root}/{name}" for name in REQUIRED_ARCHIVE_FILES}
    missing = sorted(expected.difference(names))
    if missing:
        raise ValueError(f"archive is missing required entries: {', '.join(missing)}")
    prefix = f"{archive_root}/"
    if any(
        not name.startswith(prefix)
        or name.startswith("/")
        or ".." in Path(name).parts
        for name in names
    ):
        raise ValueError("archive contains an invalid path or multiple roots")


def build_package(root: Path, output: Path | None = None) -> Path:
    root = root.resolve()
    plugin_name = _metadata_value(root / "metadata.yaml", "name")
    version = _metadata_value(root / "metadata.yaml", "version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", plugin_name):
        raise ValueError(f"metadata plugin name is unsafe for an archive: {plugin_name}")
    files = collect_runtime_files(root)
    destination = (
        output.resolve()
        if output is not None
        else root / "dist" / f"{plugin_name}-v{version}.zip"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for source in files:
                relative = source.relative_to(root).as_posix()
                archive.writestr(
                    _archive_info(f"{plugin_name}/{relative}"),
                    source.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        validate_archive(temporary_path, plugin_name)
        os.replace(temporary_path, destination)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    print(f"Built {destination}")
    print(f"Files: {len(files)}; size: {destination.stat().st_size} bytes")
    print(f"SHA256: {digest}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify a complete ZIP for AstrBot plugin upload."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="plugin working tree (default: repository root)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output ZIP path (default: dist/<plugin>-v<version>.zip)",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="verify an existing ZIP instead of building one",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.verify is not None:
        if args.output is not None:
            parser.error("--output cannot be combined with --verify")
        plugin_name = _metadata_value(root / "metadata.yaml", "name")
        validate_archive(args.verify.resolve(), plugin_name)
        print(f"Verified {args.verify.resolve()}")
        return 0
    build_package(root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
