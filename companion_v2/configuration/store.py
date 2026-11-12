"""Optional dedicated SQLite store for configuration rows.

The live V2 database also carries conversation, memory, task and media records.
Configuration writes are short but they must take the same single writer lock as
those large transactions; on a busy installation a one-field save can wait
behind seconds of chat/state work.  This module moves only the configuration
tables and their operation receipts to a small side database, leaving identity
and authorization reads on the main store.

The move is deliberately conservative:

* the old configuration tables are never deleted;
* migration copies them once, inside one transaction, before the service uses
  the side store;
* any migration/open failure returns ``None`` so the host keeps using the main
  store and the installation remains usable.

Both stores live under the same runtime directory, so the existing whole-
installation backup bundle already carries the side database.
"""

from __future__ import annotations

import logging
from pathlib import Path

from companion_v2.storage.runtime import StorageRuntime

logger = logging.getLogger(__name__)

STORE_DIRECTORY = "configuration"

# Foreign-key order matters: configuration_changes references both operations
# and configuration_targets, and configuration_applications references
# configuration_consumers.
_COPY_ORDER = (
    "configuration_targets",
    "configuration_values",
    "configuration_consumers",
    "configuration_changes",
    "configuration_applications",
)


def open_configuration_store(runtime: StorageRuntime) -> StorageRuntime | None:
    """Open (and migrate) the side store, or return ``None`` to keep the main one."""
    path = Path(runtime.data_root) / STORE_DIRECTORY
    try:
        store = StorageRuntime.open_configuration(data_root=path, installation_id=runtime.installation_id)
    except Exception as error:  # noqa: BLE001 - a failed side store must not block the host
        logger.warning("[CompanionV2] 配置库打开失败，继续使用主库 error_type=%s", type(error).__name__)
        return None
    try:
        _migrate(runtime, store)
    except Exception as error:  # noqa: BLE001
        logger.warning("[CompanionV2] 配置库迁移失败，继续使用主库 error_type=%s", type(error).__name__)
        store.close()
        return None
    runtime.attach_configuration_store(store)
    return store


def _columns(runtime: StorageRuntime, table: str) -> tuple[str, ...]:
    return tuple(row["name"] for row in runtime.read_all(f'PRAGMA table_info("{table}")', ()))


def _copy_rows(source: StorageRuntime, connection, table: str, *, where: str = "",
               parameters: tuple[object, ...] = ()) -> int:
    columns = _columns(source, table)
    if not columns:
        return 0
    rows = source.read_all(f'SELECT * FROM "{table}"{(" WHERE " + where) if where else ""}', parameters)
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in columns)
    names = ",".join(f'"{name}"' for name in columns)
    connection.executemany(f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({placeholders})',
        [tuple(row[name] for name in columns) for row in rows])
    return len(rows)


def _migrate(source: StorageRuntime, target: StorageRuntime) -> None:
    existing = target.read_one("SELECT COUNT(*) AS n FROM configuration_targets", ())
    if existing is not None and existing["n"]:
        return
    source_count = source.read_one("SELECT COUNT(*) AS n FROM configuration_targets", ())
    if source_count is None or not source_count["n"]:
        return

    # One transaction for the whole copy: a crash can leave neither a full store
    # nor a half-migrated one that a later open would mistake for complete.
    with target.transaction() as connection:
        # configuration_changes has an FK to operations; copy the config-related
        # receipts first.  The main operations table can be large, so do not copy
        # unrelated task/memory operations.
        config_ops = source.read_all("SELECT operation_id FROM configuration_changes", ())
        operation_ids = tuple(row["operation_id"] for row in config_ops)
        if operation_ids:
            columns = _columns(source, "operations")
            rows = source.read_all(
                f'SELECT * FROM operations WHERE operation_id IN ({",".join("?" for _ in operation_ids)})',
                operation_ids,
            )
            if rows:
                placeholders = ",".join("?" for _ in columns)
                names = ",".join(f'"{name}"' for name in columns)
                connection.executemany(
                    f'INSERT OR IGNORE INTO operations ({names}) VALUES ({placeholders})',
                    [tuple(row[name] for name in columns) for row in rows],
                )
        for table in _COPY_ORDER:
            _copy_rows(source, connection, table)
    logger.info("[CompanionV2] 配置库迁移完成 targets=%s", source_count["n"])
