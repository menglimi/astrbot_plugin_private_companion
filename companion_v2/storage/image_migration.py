"""Background, resumable migration of legacy image BLOBs to files.

The storage layer already accepts both shapes.  Starting this once after an
upgrade keeps the existing database from staying large until an operator runs
the CLI by hand.  It is deliberately one transaction per batch and resumable:
if the process stops, the next start continues with the rows that still carry
a BLOB.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from .image_payloads import migrate_image_payloads

logger = logging.getLogger(__name__)

MARKER_PREFIX = b"\x00PCIMG1/"
BATCH_SIZE = 128


def _migrate(runtime) -> dict:
    path = runtime.database_path
    connection = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        legacy = connection.execute(
            "SELECT COUNT(*) FROM conversation_image_payloads WHERE substr(content, 1, 8) <> ?",
            (MARKER_PREFIX,),
        ).fetchone()[0]
        if not legacy:
            return {"migrated": 0, "already_file_backed": 0}
        connection.execute("BEGIN IMMEDIATE")
        result = migrate_image_payloads(runtime.data_root, connection, batch_size=BATCH_SIZE)
        connection.execute("COMMIT")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


async def migrate_image_payloads_in_background(runtime) -> None:
    try:
        result = await asyncio.to_thread(_migrate, runtime)
    except Exception as error:
        logger.warning("[CompanionV2] 图片 BLOB 后台迁移失败 error_type=%s", type(error).__name__)
        return
    if result.get("migrated"):
        logger.info(
            "[CompanionV2] 图片 BLOB 已迁移到文件 migrated=%s already=%s",
            result["migrated"], result["already_file_backed"],
        )
