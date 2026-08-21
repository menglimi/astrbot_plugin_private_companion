"""Read-only schema inspection for REQ-041 automatic migration sources.

The inspector deliberately records only structural metadata.  User identifiers,
profile values, chat text and arbitrary section names never enter its inventory.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

INVENTORY_SCHEMA = "req041.source_inventory.v1"
SUPPORTED_STORE_VERSIONS = frozenset({1})
SUPPORTED_SECTION_SCHEMA_VERSIONS = frozenset({1, 2})
_REQUIRED_STORE_SECTIONS = frozenset({"version", "users", "groups"})
_SQLITE_V1_REQUIRED_COLUMNS = frozenset(
    {
        "section_name",
        "payload_json",
        "updated_at",
        "checksum",
        "schema_version",
    }
)
_SQLITE_V2_REQUIRED_COLUMNS = _SQLITE_V1_REQUIRED_COLUMNS | frozenset(
    {
        "revision",
        "is_deleted",
    }
)
_SQLITE_V2_ONLY_COLUMNS = _SQLITE_V2_REQUIRED_COLUMNS - _SQLITE_V1_REQUIRED_COLUMNS
_SQLITE_V2_COLUMNS = (
    "section_name",
    "payload_json",
    "updated_at",
    "checksum",
    "schema_version",
    "revision",
    "is_deleted",
)
_SQLITE_V2_COLUMN_TYPES = (
    "TEXT",
    "TEXT",
    "REAL",
    "TEXT",
    "INTEGER",
    "INTEGER",
    "INTEGER",
)
_SQLITE_META_COLUMNS = ("meta_key", "meta_value")
_SQLITE_INTEGER_MAX = (1 << 63) - 1
_MAX_SECTION_REVISION = _SQLITE_INTEGER_MAX - 1
_MAX_ACTIVE_SECTIONS = 512
_MAX_PHYSICAL_SECTION_ROWS = 512
# Store sections are schema keys, including internal/runtime sections prefixed
# with an underscore (for example ``_req041_memory_scope_state``).  Keep the
# character set and length bounded while allowing that established prefix.
_SAFE_SECTION_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}\Z")


class MigrationSourceInspectionError(ValueError):
    """Raised when a legacy source cannot be proven to match a supported store."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError as exc:
        raise MigrationSourceInspectionError("migration_source_unreadable") from exc


def _store_version(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value not in SUPPORTED_STORE_VERSIONS
    ):
        raise MigrationSourceInspectionError(
            "migration_source_store_version_unsupported"
        )
    return value


def _validate_critical_sections(
    sections: dict[str, Any],
) -> tuple[int, dict[str, bool]]:
    if not _REQUIRED_STORE_SECTIONS.issubset(sections):
        raise MigrationSourceInspectionError(
            "migration_source_required_section_missing"
        )
    version = _store_version(sections.get("version"))
    if not isinstance(sections.get("users"), dict) or not isinstance(
        sections.get("groups"), dict
    ):
        raise MigrationSourceInspectionError("migration_source_section_shape_invalid")
    for optional_mapping in ("unified_person", "persona_lifecycle"):
        if optional_mapping in sections and not isinstance(
            sections[optional_mapping], dict
        ):
            raise MigrationSourceInspectionError(
                "migration_source_section_shape_invalid"
            )
    return version, {
        "has_unified_person": isinstance(sections.get("unified_person"), dict),
        "has_persona_lifecycle": isinstance(sections.get("persona_lifecycle"), dict),
    }


def _inspect_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationSourceInspectionError("migration_source_json_invalid") from exc
    if not isinstance(payload, dict):
        raise MigrationSourceInspectionError("migration_source_root_invalid")
    if len(payload) > 512 or any(not isinstance(key, str) for key in payload):
        raise MigrationSourceInspectionError("migration_source_section_set_invalid")
    version, features = _validate_critical_sections(payload)
    return {
        "kind": "json",
        "store_version": version,
        "section_schema_versions": [],
        "section_count": len(payload),
        **features,
    }


def _inspect_sqlite(path: Path) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15.0)
    except sqlite3.Error as exc:
        raise MigrationSourceInspectionError("migration_source_sqlite_invalid") from exc
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or str(check[0]).lower() != "ok":
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_integrity_invalid"
            )
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='store_sections'"
        ).fetchone()
        if table is None:
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_missing"
            )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        section_info = list(connection.execute("PRAGMA table_info(store_sections)"))
        columns = {str(row[1]) for row in section_info}
        if not _SQLITE_V1_REQUIRED_COLUMNS.issubset(columns):
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_invalid"
            )
        v2_columns = columns & _SQLITE_V2_ONLY_COLUMNS
        if v2_columns and not _SQLITE_V2_ONLY_COLUMNS.issubset(columns):
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_invalid"
            )
        physical_schema_version = 2 if _SQLITE_V2_ONLY_COLUMNS.issubset(columns) else 1
        user_version = connection.execute("PRAGMA user_version").fetchone()
        try:
            schema_marker = int(user_version[0]) if user_version is not None else 0
        except (TypeError, ValueError, OverflowError) as exc:
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_invalid"
            ) from exc
        if schema_marker not in ({0, 1} if physical_schema_version == 1 else {2}):
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_invalid"
            )
        if physical_schema_version == 2:
            if tables != {"store_sections", "store_meta"}:
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            if (
                tuple(str(row[1]) for row in section_info) != _SQLITE_V2_COLUMNS
                or tuple(str(row[2]).upper() for row in section_info)
                != _SQLITE_V2_COLUMN_TYPES
                or any(int(row[3] or 0) != 1 for row in section_info)
                or [str(row[1]) for row in section_info if int(row[5] or 0) > 0]
                != ["section_name"]
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            schema_row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='store_sections'"
            ).fetchone()
            schema_sql = str(schema_row[0] if schema_row else "").casefold()
            if any(
                guard not in schema_sql
                for guard in (
                    "store_sections_schema_v2",
                    "store_sections_positive_revision",
                    "store_sections_deleted_flag",
                )
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            meta_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='store_meta'"
            ).fetchone()
            if meta_table is None:
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            meta_info = list(connection.execute("PRAGMA table_info(store_meta)"))
            if (
                tuple(str(row[1]) for row in meta_info) != _SQLITE_META_COLUMNS
                or tuple(str(row[2]).upper() for row in meta_info) != ("TEXT", "TEXT")
                or any(int(row[3] or 0) != 1 for row in meta_info)
                or [str(row[1]) for row in meta_info if int(row[5] or 0) > 0]
                != ["meta_key"]
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            metadata = {
                str(key): str(value)
                for key, value in connection.execute(
                    "SELECT meta_key,meta_value FROM store_meta"
                )
            }
            if set(metadata) != {"initialized", "next_revision"} or metadata.get(
                "initialized"
            ) not in {"0", "1"}:
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            raw_next_revision = metadata.get("next_revision", "")
            try:
                next_revision = int(raw_next_revision)
            except (TypeError, ValueError, OverflowError) as exc:
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                ) from exc
            if (
                str(next_revision) != raw_next_revision
                or next_revision <= 0
                or next_revision > _SQLITE_INTEGER_MAX
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_sqlite_contract_invalid"
                )
            rows = connection.execute(
                "SELECT section_name,payload_json,checksum,schema_version,revision,is_deleted "
                "FROM store_sections ORDER BY section_name"
            ).fetchall()
        else:
            rows = [
                (row[0], row[1], "", row[2], 1, 0)
                for row in connection.execute(
                    "SELECT section_name,payload_json,schema_version "
                    "FROM store_sections ORDER BY section_name"
                ).fetchall()
            ]
            next_revision = 0
            metadata = {}
        if not rows or len(rows) > _MAX_PHYSICAL_SECTION_ROWS:
            raise MigrationSourceInspectionError("migration_source_section_set_invalid")
        sections: dict[str, Any] = {}
        schema_versions: set[int] = set()
        section_names: set[str] = set()
        maximum_revision = 0
        for (
            raw_name,
            raw_payload,
            raw_checksum,
            raw_schema_version,
            raw_revision,
            raw_is_deleted,
        ) in rows:
            name = str(raw_name or "")
            if _SAFE_SECTION_NAME.fullmatch(name) is None:
                raise MigrationSourceInspectionError(
                    "migration_source_section_name_invalid"
                )
            if name in section_names:
                raise MigrationSourceInspectionError(
                    "migration_source_section_name_invalid"
                )
            section_names.add(name)
            if (
                isinstance(raw_schema_version, bool)
                or not isinstance(raw_schema_version, int)
                or raw_schema_version not in SUPPORTED_SECTION_SCHEMA_VERSIONS
                or raw_schema_version != physical_schema_version
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_section_version_unsupported"
                )
            if (
                isinstance(raw_revision, bool)
                or not isinstance(raw_revision, int)
                or raw_revision <= 0
                or raw_revision > _MAX_SECTION_REVISION
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_revision_invalid"
                )
            maximum_revision = max(maximum_revision, raw_revision)
            if (
                isinstance(raw_is_deleted, bool)
                or not isinstance(raw_is_deleted, int)
                or raw_is_deleted not in (0, 1)
            ):
                raise MigrationSourceInspectionError(
                    "migration_source_tombstone_invalid"
                )
            if raw_is_deleted == 1:
                if raw_payload != "null":
                    raise MigrationSourceInspectionError(
                        "migration_source_tombstone_invalid"
                    )
                if str(raw_checksum or "") not in {
                    "",
                    hashlib.sha256(b"null").hexdigest(),
                }:
                    raise MigrationSourceInspectionError(
                        "migration_source_checksum_invalid"
                    )
                continue
            if len(sections) >= _MAX_ACTIVE_SECTIONS:
                raise MigrationSourceInspectionError(
                    "migration_source_section_set_invalid"
                )
            try:
                parsed = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError) as exc:
                raise MigrationSourceInspectionError(
                    "migration_source_section_json_invalid"
                ) from exc
            checksum = str(raw_checksum or "")
            expected_checksum = hashlib.sha256(
                _canonical(parsed).encode("utf-8")
            ).hexdigest()
            if checksum and checksum != expected_checksum:
                raise MigrationSourceInspectionError(
                    "migration_source_checksum_invalid"
                )
            sections[name] = parsed
            schema_versions.add(1)
        if physical_schema_version == 2 and (
            next_revision <= maximum_revision
            or (metadata["initialized"] == "0" and rows)
        ):
            raise MigrationSourceInspectionError(
                "migration_source_sqlite_contract_invalid"
            )
        version, features = _validate_critical_sections(sections)
        return {
            "kind": "sqlite",
            "store_version": version,
            "section_schema_versions": sorted(schema_versions),
            "section_count": len(sections),
            **features,
        }
    except sqlite3.Error as exc:
        raise MigrationSourceInspectionError("migration_source_sqlite_invalid") from exc
    finally:
        connection.close()


def inspect_migration_sources(
    data_dir: str | Path,
    source_files: Sequence[str | Path],
) -> dict[str, Any]:
    """Return a deterministic, content-free schema inventory for legacy stores."""
    root = Path(data_dir).resolve()
    inspected: list[dict[str, Any]] = []
    for source in source_files:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_symlink():
            raise MigrationSourceInspectionError("migration_source_file_invalid")
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise MigrationSourceInspectionError(
                "migration_source_path_invalid"
            ) from exc
        if not path.is_file():
            raise MigrationSourceInspectionError("migration_source_file_invalid")
        inspected.append(
            _inspect_sqlite(path) if _is_sqlite(path) else _inspect_json(path)
        )
    if not inspected:
        raise MigrationSourceInspectionError("migration_source_missing")

    store_versions = sorted({int(item["store_version"]) for item in inspected})
    if len(store_versions) != 1:
        raise MigrationSourceInspectionError("migration_source_store_version_mixed")
    section_versions = sorted(
        {
            int(version)
            for item in inspected
            for version in item["section_schema_versions"]
        }
    )
    formats = {
        kind: sum(1 for item in inspected if item["kind"] == kind)
        for kind in ("json", "sqlite")
    }
    contract = {
        "store_version": store_versions[0],
        "section_schema_versions": section_versions,
        "formats": formats,
    }
    fingerprint = hashlib.sha256(_canonical(contract).encode("utf-8")).hexdigest()
    return {
        "schema": INVENTORY_SCHEMA,
        "source_schema_version": f"companion-v{store_versions[0]}-{fingerprint[:32]}",
        "fingerprint": fingerprint,
        "source_count": len(inspected),
        "formats": formats,
        "store_version": store_versions[0],
        "section_schema_versions": section_versions,
        "all_have_unified_person": all(
            item["has_unified_person"] for item in inspected
        ),
        "all_have_persona_lifecycle": all(
            item["has_persona_lifecycle"] for item in inspected
        ),
        "section_count_min": min(int(item["section_count"]) for item in inspected),
        "section_count_max": max(int(item["section_count"]) for item in inspected),
    }


__all__ = [
    "INVENTORY_SCHEMA",
    "MigrationSourceInspectionError",
    "inspect_migration_sources",
]
