"""Read-only access to V1 plugin settings stored by AstrBot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .upgrade_source import is_legacy_configuration, usable_preserved_configuration_path


V1_PLUGIN_DATA_NAME = "astrbot_plugin_private_companion"
V1_HOST_CONFIG_NAME = V1_PLUGIN_DATA_NAME + "_config.json"
V1_REALITY_PLUGIN_DATA_NAME = "astrbot_plugin_reality_companion"
V1_IMAGE_PLUGIN_DATA_NAME = "astrbot_plugin_image_companion"
V1_DATA_MARKERS = ("companions.json", ".storage-backend-state.json")


def v1_setting(configuration, path, default=None):
    """Read a known grouped setting, including the older flat export forms."""
    if not isinstance(configuration, dict):
        return default
    parts = path.split(".")
    value = configuration
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            break
        value = value[part]
    else:
        return value
    if path in configuration:
        return configuration[path]
    return configuration.get(parts[-1], default)


def _config_directories(data_root) -> tuple[Path, ...]:
    """Find AstrBot config directories for normal and nested plugin data roots."""
    base = Path(data_root)
    ancestors = tuple(base.parents)
    plugin_data = next((parent for parent in ancestors if parent.name.casefold() == "plugin_data"), None)
    preferred = ([plugin_data.parent, plugin_data] if plugin_data is not None
                 else [base.parent.parent, base.parent])
    return tuple(dict.fromkeys(parent / "config" for parent in preferred if parent is not None))


def _has_v1_data_markers(path: Path) -> bool:
    return (path.is_dir()
            and not (path / ".companion-v2-storage-owner.json").is_file()
            and not (path / ".companion-v2-storage-location.json").is_file()
            and any((path / name).exists() for name in V1_DATA_MARKERS))


def find_v1_data_root(data_root) -> tuple[Path, str]:
    """Locate the V1 directory, including the parent of a nested ``companion-7`` root."""
    base = Path(data_root)
    if _has_v1_data_markers(base):
        return base, "current"
    if _has_v1_data_markers(base.parent):
        return base.parent, "nested-parent"
    candidates = (base.parent / V1_PLUGIN_DATA_NAME, base / V1_PLUGIN_DATA_NAME)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate, "sibling"
    return base.parent / V1_PLUGIN_DATA_NAME, "missing"


def v1_host_config_path(data_root) -> Path | None:
    """Locate the V1 settings file beside this plugin's AstrBot data root."""
    for directory in _config_directories(data_root):
        # The import-before-schema hook has already selected the complete source.
        # A normalized live file may retain legacy marker keys while losing values.
        preserved = usable_preserved_configuration_path(directory)
        if preserved is not None:
            return preserved
        path = directory / V1_HOST_CONFIG_NAME
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, UnicodeDecodeError):
                # Discovery returns the damaged source too, so the reader can
                # report it as unreadable instead of treating the request as bad.
                return path
            if is_legacy_configuration(value):
                return path
        if path.is_file():
            return path
    return None


def read_v1_host_configuration(data_root):
    """Return decoded settings, path and byte fingerprint without writing V1."""
    path = v1_host_config_path(data_root)
    if path is None:
        return None, None, ""
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if type(value) is not dict:
        raise ValueError("V1 plugin settings must be an object")
    return value, path, hashlib.sha256(raw).hexdigest()


def v1_plugin_config_path(data_root, plugin_id) -> Path | None:
    """Locate another V1 plugin's read-only settings dump beside the V2 data root."""
    if not isinstance(plugin_id, str) or not plugin_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in plugin_id):
        return None
    for directory in _config_directories(data_root):
        path = directory / f"{plugin_id}_config.json"
        if path.is_file():
            return path
    return None


def read_v1_plugin_configuration(data_root, plugin_id):
    """Return a read-only V1 plugin configuration, path and byte fingerprint."""
    path = v1_plugin_config_path(data_root, plugin_id)
    if path is None:
        return None, None, ""
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if type(value) is not dict:
        raise ValueError("V1 plugin settings must be an object")
    return value, path, hashlib.sha256(raw).hexdigest()


__all__ = [
    "V1_HOST_CONFIG_NAME",
    "V1_DATA_MARKERS",
    "V1_PLUGIN_DATA_NAME",
    "V1_REALITY_PLUGIN_DATA_NAME",
    "V1_IMAGE_PLUGIN_DATA_NAME",
    "read_v1_host_configuration",
    "read_v1_plugin_configuration",
    "find_v1_data_root",
    "v1_host_config_path",
    "v1_plugin_config_path",
    "v1_setting",
]
