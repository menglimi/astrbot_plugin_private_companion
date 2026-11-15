"""Move legacy image BLOBs out of companion-v2.sqlite3 into the media folder.

Run this while AstrBot is stopped (or at least while the companion is idle):

    python -X utf8 tools/migrate_image_payloads.py \
        --database data/plugin_data/astrbot_plugin_private_companion/companion-7/installations/main/runtime/companion-v2.sqlite3 \
        --data-root data/plugin_data/astrbot_plugin_private_companion/companion-7/installations/main/runtime \
        --vacuum

The conversion is resumable: rows that already carry the file marker are
skipped, so an interrupted run can be started again.  Keep a backup of the
database before running it.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion_v2.storage.image_payloads import migrate_image_payloads  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if not args.database.is_file():
        parser.error(f"database not found: {args.database}")
    connection = sqlite3.connect(str(args.database), isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        if args.dry_run:
            total = connection.execute(
                "SELECT COUNT(*) FROM conversation_image_payloads WHERE substr(content, 1, 8) <> ?",
                (b"\x00PCIMG1/",),
            ).fetchone()[0]
            print(f"would migrate {total} image payload(s)")
            return 0
        connection.execute("BEGIN IMMEDIATE")
        result = migrate_image_payloads(
            args.data_root, connection, batch_size=args.batch_size,
        )
        connection.execute("COMMIT")
        print(
            "migrated={migrated} already_file_backed={already_file_backed}".format(**result)
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if args.vacuum:
            connection.execute("VACUUM")
            print("vacuumed")
        return 0
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
