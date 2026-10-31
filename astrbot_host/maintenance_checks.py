"""Read-only host checks shown by the maintenance diagnostics page.

The maintenance response is role-scoped model accounting; backup snapshots,
the phone gateway and disk headroom live in the host/installation layer.  This
module projects only the facts the page needs, without listing payload trees,
starting the gateway or returning any secret.

Every helper fails closed: a filesystem or settings problem becomes
``available: false`` instead of failing the whole maintenance route.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil

from .storage_location import selected_backup_directory


def _merge(defaults, saved):
    result = copy.deepcopy(defaults)
    for key, value in saved.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _existing_ancestor(path: Path) -> Path:
    target = Path(path)
    for _attempt in range(64):
        if target.exists():
            return target
        parent = target.parent
        if parent == target:
            break
        target = parent
    return Path(path)


def _volume_key(path: Path) -> str | None:
    target = _existing_ancestor(path)
    try:
        if os.name == "nt":
            drive = target.drive or Path(path).drive
            return drive.casefold() or None
        return str(target.stat().st_dev)
    except OSError:
        return None


def _disk_usage(path: Path) -> dict | None:
    target = _existing_ancestor(path)
    try:
        usage = shutil.disk_usage(str(target))
    except OSError:
        return None
    total = int(usage.total)
    free = int(usage.free)
    return {"free_bytes": free, "total_bytes": total,
            "used_ratio": round((total - free) / total, 6) if total > 0 else None}


def _backup_context(controller, data_root: Path):
    configuration = controller.config if isinstance(getattr(controller, "config", None), dict) else {}
    storage = configuration.get("storage") if isinstance(configuration.get("storage"), dict) else {}
    selected = storage.get("backup_path", "")
    plugin_root = Path(getattr(controller, "data_root", data_root)).resolve()
    runtime = getattr(controller, "runtime", None)
    installation_id = getattr(runtime, "installation_id", None)
    if type(installation_id) is not str:
        installation_id = getattr(getattr(controller, "settings", None), "installation_id", None)
    if type(installation_id) is not str:
        installation_id = getattr(getattr(getattr(controller, "configuration", None), "scope", None),
                                  "installation_id", None)
    try:
        directory = selected_backup_directory(data_root, selected, installation_id, plugin_root=plugin_root)
    except (OSError, ValueError, TypeError):
        directory = data_root / "backups"
    return {"selected": bool(type(selected) is str and selected.strip()),
            "directory": Path(directory)}


def _backup_check(controller, data_root: Path) -> dict:
    context = _backup_context(controller, data_root)
    directory = context["directory"]
    data = {
        "available": False,
        "configured": context["selected"],
        "data_root": str(data_root),
        "backup_root": str(directory),
        "snapshot_count": 0,
        "latest_success_at": None,
        "same_volume": None,
        "problem_count": 0,
        "free_bytes": None,
        "total_bytes": None,
        "error": None,
    }
    usage = _disk_usage(directory)
    if usage:
        data.update(free_bytes=usage["free_bytes"], total_bytes=usage["total_bytes"])
    data_key, backup_key = _volume_key(data_root), _volume_key(directory)
    if data_key and backup_key:
        data["same_volume"] = data_key == backup_key
    runtime = getattr(controller, "runtime", None)
    if runtime is None:
        data["error"] = "staging_not_initialized"
        return data
    try:
        from companion_v2.storage.backup import InstallationBackup

        listing = InstallationBackup(runtime, directory=directory).listing()
        if not listing.is_success:
            data["error"] = listing.failure.code if listing.failure is not None else "backup_listing_failed"
            return data
        document = listing.unwrap()
        snapshots = document.get("snapshots") if isinstance(document, dict) else None
        problems = document.get("problems") if isinstance(document, dict) else None
        data["snapshot_count"] = len(snapshots) if isinstance(snapshots, list) else 0
        data["problem_count"] = len(problems) if isinstance(problems, list) else 0
        if data["snapshot_count"]:
            created_at = snapshots[0].get("created_at") if isinstance(snapshots[0], dict) else None
            data["latest_success_at"] = created_at if type(created_at) is str and created_at else None
        data["available"] = True
    except (OSError, ValueError, TypeError, AttributeError):
        data["error"] = "backup_listing_unavailable"
    return data


def _mobile_check(controller, data_root: Path) -> dict:
    data = {
        "available": False,
        "enabled": False,
        "pairing_token_configured": False,
        "host": "",
        "port": 6322,
        "module_state": "unavailable",
    }
    try:
        packaged = Path(__file__).resolve().parent / "native_extensions" / "reality"
        defaults = json.loads((packaged / "settings-defaults.json").read_text(encoding="utf-8"))
        saved = {}
        path = Path(data_root) / "extensions" / "reality" / "config.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
        if type(saved) is not dict or type(defaults) is not dict:
            raise ValueError()
        mobile = _merge(defaults, saved).get("mobile")
        if type(mobile) is not dict:
            raise ValueError()
        data.update(
            available=True,
            enabled=mobile.get("enabled") is True,
            pairing_token_configured=bool(mobile.get("pairing_token")),
            host=str(mobile.get("host") or "")[:240],
            port=int(mobile.get("port") or 6322),
        )
    except (OSError, ValueError, TypeError):
        return data
    try:
        registry = getattr(controller, "native_extensions", None)
        status = registry.status("reality") if registry is not None else None
        state = status.get("state") if isinstance(status, dict) else None
        data["module_state"] = state if isinstance(state, str) and state else "unavailable"
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        data["module_state"] = "unavailable"
    return data


def read_host_checks(controller) -> dict:
    """Return additive, read-only backup/disk/mobile facts for the maintenance page."""
    runtime = getattr(controller, "runtime", None)
    try:
        data_root = Path(getattr(runtime, "data_root", None) or getattr(controller, "data_root", "")).resolve()
    except (OSError, TypeError, ValueError):
        data_root = Path(".").resolve()
    backup = _backup_check(controller, data_root)
    mobile = _mobile_check(controller, data_root)
    data_usage = _disk_usage(data_root)
    backup_usage = _disk_usage(Path(backup["backup_root"]))
    return {
        "backup": backup,
        "mobile": mobile,
        "disk": {
            "available": data_usage is not None,
            "data_root": data_usage,
            "backup_root": backup_usage,
        },
    }
