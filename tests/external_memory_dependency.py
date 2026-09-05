"""Locate the optional memory-companion checkout used by integration tests.

This module only resolves paths.  It deliberately does not provide stand-ins for the
external plugin: integration tests either load its real source tree or skip.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


ENV_NAME = "ASTRBOT_MEMORY_PLUGIN_ROOT"
MARKER = Path("core") / "bridge.py"


@dataclass(frozen=True)
class MemoryPluginResolution:
    root: Path | None
    detail: str


def _automatic_candidates(companion_root: Path) -> tuple[Path, ...]:
    workspace_root = companion_root.parents[1]
    return (
        companion_root.parent / "memory",
        companion_root.parent / "memory-official",
        workspace_root / "astrbot_plugin_memory_companion-main",
        companion_root.parent / "astrbot_plugin_remember_you",
        companion_root.parent / "我会牢牢记住你",
    )


def resolve_memory_plugin_root(
    companion_root: Path,
    *,
    configured_root: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> MemoryPluginResolution:
    """Resolve a real external checkout without importing or fabricating it."""
    environment = os.environ if environ is None else environ
    explicit = configured_root or environment.get(ENV_NAME)
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / MARKER).is_file():
            return MemoryPluginResolution(root, f"configured by {ENV_NAME}/--memory-plugin-root: {root}")
        return MemoryPluginResolution(
            None,
            f"configured memory companion root is invalid (missing {MARKER}): {root}",
        )

    for candidate in _automatic_candidates(companion_root):
        root = candidate.resolve()
        if (root / MARKER).is_file():
            return MemoryPluginResolution(root, f"auto-discovered memory companion checkout: {root}")

    return MemoryPluginResolution(
        None,
        "memory companion checkout is not installed; set "
        f"{ENV_NAME} or pass --memory-plugin-root to run cross-plugin integration contracts",
    )
