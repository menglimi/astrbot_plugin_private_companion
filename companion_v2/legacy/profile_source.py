"""Read V1 profile snapshots and select the primary store's explicit authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import stat

from .sqlite_snapshot import open_readonly_sqlite_snapshot


MAX_PROFILE_JSON_BYTES = 64 * 1024 * 1024
MAX_PROFILE_DB_BYTES = 256 * 1024 * 1024
BACKEND_STATE_FILENAME = ".storage-backend-state.json"


def _reject_json_constant(_value):
    raise ValueError("non-standard JSON number")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _regular(info, maximum):
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and not int(getattr(info, "st_file_attributes", 0)) & reparse
            and 0 <= info.st_size <= maximum)


def read_json(path: Path, *, max_bytes=128 * 1024 * 1024):
    try:
        info = path.lstat()
        if not _regular(info, max_bytes):
            return None
        raw = path.read_bytes()
        if len(raw) != info.st_size or len(raw) > max_bytes:
            return None
        return json.loads(raw.decode("utf-8-sig", errors="strict"),
                          parse_constant=_reject_json_constant, object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def read_sqlite(path: Path):
    """Keep WAL commits in a query-only transaction; never initialize the source."""
    try:
        if not _regular(path.lstat(), MAX_PROFILE_DB_BYTES):
            return None, "profile_database_invalid"
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                details = Path(str(path) + suffix).lstat()
            except FileNotFoundError:
                continue
            if not _regular(details, MAX_PROFILE_DB_BYTES):
                return None, "profile_database_invalid"
        connection = open_readonly_sqlite_snapshot(path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if "store_sections" not in tables or version not in (0, 1, 2):
                return None, "profile_database_schema_unknown"
            columns = {row[1] for row in connection.execute("PRAGMA table_info(store_sections)")}
            common = {"section_name", "payload_json", "updated_at", "checksum", "schema_version"}
            modern = common | {"revision", "is_deleted"}
            legacy = columns == common and version in (0, 1) and tables == {"store_sections"}
            if not legacy and (not modern.issubset(columns) or "store_meta" not in tables
                               or version not in (0, 2)):
                return None, "profile_database_schema_unknown"
            if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
                return None, "profile_database_integrity_invalid"
            total = connection.execute(
                "SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM store_sections").fetchone()[0]
            if type(total) is not int or not 0 <= total <= MAX_PROFILE_JSON_BYTES:
                return None, "profile_snapshot_too_large"
            projection = ("section_name,payload_json,updated_at,checksum,schema_version"
                          if legacy else "section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted")
            result, seen_names, row_count, maximum_revision = {}, set(), 0, 0
            for row in connection.execute("SELECT " + projection + " FROM store_sections"):
                row_count += 1
                try:
                    name, raw, checksum = row["section_name"], row["payload_json"], row["checksum"]
                    if (type(name) is not str or not name or len(name) > 256 or "\x00" in name
                            or type(raw) is not str or type(checksum) is not str
                            or type(row["schema_version"]) is not int
                            or row["schema_version"] != (1 if legacy else 2)
                            or not math.isfinite(float(row["updated_at"]))):
                        return None, "profile_database_integrity_invalid"
                    payload = json.loads(raw, parse_constant=_reject_json_constant,
                                         object_pairs_hook=_strict_object)
                    canonical = _canonical(payload)
                    accepted_hashes = {"", hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
                    if legacy:
                        accepted_hashes.add(hashlib.sha256(raw.encode("utf-8")).hexdigest())
                    if checksum not in accepted_hashes:
                        return None, "profile_database_integrity_invalid"
                    if name in seen_names:
                        return None, "profile_database_integrity_invalid"
                    seen_names.add(name)
                    if not legacy:
                        revision, deleted = row["revision"], row["is_deleted"]
                        if (type(revision) is not int or not 0 < revision < (1 << 63) - 1
                                or type(deleted) is not int or deleted not in (0, 1)):
                            return None, "profile_database_integrity_invalid"
                        maximum_revision = max(maximum_revision, revision)
                        if deleted:
                            if payload is not None or raw != "null":
                                return None, "profile_database_integrity_invalid"
                            continue
                    result[name] = payload
                except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
                    return None, "profile_database_payload_invalid"
            if not legacy:
                if not {"meta_key", "meta_value"}.issubset(
                        {row[1] for row in connection.execute("PRAGMA table_info(store_meta)")}):
                    return None, "profile_database_schema_unknown"
                metadata = dict(connection.execute("SELECT meta_key,meta_value FROM store_meta"))
                try:
                    next_revision = int(metadata.get("next_revision", ""))
                except (TypeError, ValueError):
                    return None, "profile_database_integrity_invalid"
                if (set(metadata) != {"initialized", "next_revision"}
                        or metadata["initialized"] not in ("0", "1")
                        or next_revision <= maximum_revision
                        or not 1 <= next_revision < (1 << 63)):
                    return None, "profile_database_integrity_invalid"
                if metadata["initialized"] == "0":
                    return None, "profile_database_not_initialized"
                if not row_count and next_revision < 2:
                    return None, "profile_database_integrity_invalid"
            return result, ""
        finally:
            connection.rollback()
            connection.close()
    except (OSError, sqlite3.Error, ValueError):
        return None, "profile_database_unreadable"


@dataclass(frozen=True)
class PrimaryProfile:
    payload: dict | None
    error: str
    path: Path | None
    backend: str
    authority: str

    def selected_path(self, root):
        if self.path is None:
            return ""
        try:
            resolved_root = Path(root).resolve()
            resolved_path = self.path.resolve()
            return resolved_path.relative_to(resolved_root).as_posix()
        except (OSError, ValueError):
            return self.path.as_posix()

    def document(self, root):
        state = "ready" if self.payload is not None else (
            "ambiguous" if self.error in {"core_profile_ambiguous", "core_profile_authority_conflict"}
            else "absent" if self.error == "core_profile_missing" else "unreadable")
        filename = self.path.name if self.path is not None else "来源待确认"
        result = {"persona_id": "", "label": f"主角色（{filename}）", "format": self.backend,
                  "state": state, "source_path": self.selected_path(root), "authority": self.authority}
        if self.error:
            result["reason"] = self.error
        return result


def _configuration_value(configuration, key):
    if not isinstance(configuration, dict):
        return False, None
    basic = configuration.get("basic_config")
    if isinstance(basic, dict) and key in basic:
        return True, basic[key]
    if "basic_config." + key in configuration:
        return True, configuration["basic_config." + key]
    return (True, configuration[key]) if key in configuration else (False, None)


def _sqlite_path(root, value):
    if value in (None, ""):
        return root / "companions.db"
    if type(value) is not str or "\x00" in value:
        raise ValueError("core_profile_sqlite_path_invalid")
    path = Path(value.strip()) if value.strip() else root / "companions.db"
    return (path if path.is_absolute() else root / path).absolute()


def _sqlite_paths_match(root, configured_value, configured_path, state_path):
    if configured_path.resolve() == state_path.resolve():
        return True
    # V1 resolves custom relative paths from the AstrBot process directory.
    if (
        isinstance(configured_value, str)
        and configured_value.strip()
        and not Path(configured_value.strip()).is_absolute()
        and root.parent.name == "plugin_data"
        and root.parent.parent.name == "data"
    ):
        host_path = root.parent.parent.parent / configured_value.strip()
        return host_path.resolve() == state_path.resolve()
    return False


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _load(path, backend, authority):
    if not _exists(path):
        return PrimaryProfile(None, "core_profile_missing", path, backend, authority)
    if backend == "sqlite":
        payload, reason = read_sqlite(path)
    else:
        payload = read_json(path, max_bytes=MAX_PROFILE_JSON_BYTES)
        reason = "" if isinstance(payload, dict) else "core_profile_unreadable"
    return PrimaryProfile(payload if isinstance(payload, dict) else None, reason, path, backend, authority)


def primary_profile(root: Path, configuration=None):
    present, raw_backend = _configuration_value(configuration, "storage_backend")
    config_backend = raw_backend.strip().lower() if type(raw_backend) is str else ""
    if present and config_backend not in ("json", "sqlite"):
        return PrimaryProfile(None, "core_profile_backend_invalid", None, "unknown", "configuration")
    path_present, configured_path = _configuration_value(configuration, "storage_sqlite_path")
    marker = root / BACKEND_STATE_FILENAME
    state = read_json(marker, max_bytes=64 * 1024) if _exists(marker) else None
    state_backend = ""
    if _exists(marker):
        if (not isinstance(state, dict) or type(state.get("backend")) is not str
                or state["backend"].strip().lower() not in ("json", "sqlite")):
            return PrimaryProfile(None, "core_profile_backend_state_invalid", marker, "unknown", "backend_state")
        state_backend = state["backend"].strip().lower()
    if config_backend and state_backend and config_backend != state_backend:
        return PrimaryProfile(None, "core_profile_authority_conflict", None, "mixed", "configuration+backend_state")
    backend = config_backend or state_backend
    authority = "configuration+backend_state" if config_backend and state_backend else (
        "configuration" if config_backend else "backend_state" if state_backend else "detected")
    json_path = root / "companions.json"
    if backend == "json":
        return _load(json_path, backend, authority)
    try:
        database_path = _sqlite_path(root, configured_path if path_present else "")
        if state_backend == "sqlite":
            state_path = _sqlite_path(root, state.get("sqlite_path", ""))
            if path_present and not _sqlite_paths_match(root, configured_path, database_path, state_path):
                return PrimaryProfile(None, "core_profile_authority_conflict", None, "sqlite", authority)
            database_path = state_path
    except (OSError, ValueError):
        return PrimaryProfile(None, "core_profile_sqlite_path_invalid", None, "sqlite", authority)
    if backend == "sqlite":
        return _load(database_path, backend, authority)
    json_exists, sqlite_exists = _exists(json_path), _exists(database_path)
    if json_exists and sqlite_exists:
        json_source = _load(json_path, "json", authority)
        sqlite_source = _load(database_path, "sqlite", authority)
        if (json_source.payload is None or sqlite_source.payload is None
                or _canonical(json_source.payload) != _canonical(sqlite_source.payload)):
            return PrimaryProfile(None, "core_profile_ambiguous", None, "mixed", authority)
        return PrimaryProfile(json_source.payload, "", json_path, "json", "matching_copies")
    return _load(database_path, "sqlite", authority) if sqlite_exists else _load(json_path, "json", authority)
