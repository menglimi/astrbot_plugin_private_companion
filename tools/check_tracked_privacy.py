"""Reject local runtime state and credentials from the Git source inventory."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess

from tools.build_v2_release import SECRET_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"data", "plugin_data", "logs", "backups", "dist", "build", "verification"}
PRIVATE_FILES = {"agents.md", "cmd_config.json", "credentials.json", "secrets.json"}
PRIVATE_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".p12", ".pfx",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2",
    ".bak", ".backup", ".orig", ".patch",
}
PLUGIN_CONFIG = re.compile(r"astrbot_plugin_.*_config\.json\Z")


def is_private_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    return (
        parts[:2] == ("docs", "design-preview")
        or any(part in PRIVATE_DIRS for part in parts[:-1])
        or name in PRIVATE_FILES
        or name == ".env"
        or name.startswith(".env.")
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or bool(PLUGIN_CONFIG.fullmatch(name))
    )


def check_tracked(root: Path = ROOT) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True, capture_output=True,
    )
    names = [name.decode("utf-8", "surrogateescape") for name in result.stdout.split(b"\0") if name]
    failures = []
    for name in names:
        if is_private_path(name):
            failures.append(f"private path tracked: {name}")
            continue
        path = root / name
        if path.is_symlink() or not path.is_file():
            failures.append(f"unexpected tracked file: {name}")
            continue
        payload = path.read_bytes().replace(b"\\n", b"\n")
        if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
            failures.append(f"credential-like content tracked: {name}")
    return len(names), failures


def main() -> int:
    count, failures = check_tracked()
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(f"tracked privacy check: ok ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
