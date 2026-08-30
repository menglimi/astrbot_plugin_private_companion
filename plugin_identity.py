from __future__ import annotations

import re
from typing import Any


PLUGIN_ID = "astrbot_plugin_private_companion"
PLUGIN_DISPLAY_NAME = "我会永远陪着你"
PLUGIN_VERSION = "6.4.3c"
PLUGIN_DATA_DIRECTORY_KEY = PLUGIN_ID

_IDENTITY_SEPARATORS = re.compile(r"[.:/\\]+")


def _identity_text(value: Any, *, limit: int = 240) -> str:
    if value is None:
        return ""
    try:
        return " ".join(str(value).split())[:limit]
    except Exception:
        return ""


def _identity_segments(value: Any) -> tuple[str, ...]:
    text = _identity_text(value)
    if not text:
        return ()
    return tuple(part for part in _IDENTITY_SEPARATORS.split(text) if part)


def is_exact_plugin_id(value: Any, expected: str = PLUGIN_ID) -> bool:
    """Match an ID only as a complete token, never as a raw prefix."""

    candidate = _identity_text(value)
    target = _identity_text(expected, limit=120)
    if not candidate or not target:
        return False
    return candidate == target or target in _identity_segments(candidate)


def is_module_path_for_package(module_path: Any, package_prefix: Any) -> bool:
    """Accept a package and its descendants while rejecting similarly named packages."""

    module = _identity_text(module_path, limit=320).replace("/", ".").replace("\\", ".")
    package = _identity_text(package_prefix, limit=240).replace("/", ".").replace("\\", ".")
    if not module or not package:
        return False
    return module == package or module.startswith(f"{package}.")


def plugin_identity_snapshot() -> dict[str, str]:
    return {
        "plugin_id": PLUGIN_ID,
        "display_name": PLUGIN_DISPLAY_NAME,
        "version": PLUGIN_VERSION,
        "data_directory": PLUGIN_DATA_DIRECTORY_KEY,
        "match_rule": "exact_id_or_module_segment",
    }


__all__ = [
    "PLUGIN_DATA_DIRECTORY_KEY",
    "PLUGIN_DISPLAY_NAME",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "is_exact_plugin_id",
    "is_module_path_for_package",
    "plugin_identity_snapshot",
]
