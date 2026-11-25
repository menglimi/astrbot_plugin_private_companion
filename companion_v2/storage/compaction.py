"""One-click database health inspection and lossless compaction ("数据库瘦身与体检").

Combines:
1. Read-only health inspection across the primary V2 SQLite store and all
   sidecar SQLite databases (file/WAL sizes, freelist fragmentation ratio,
   ``auto_vacuum`` mode, pending deduplication / cold telemetry / inline media counts).
2. Lossless compaction pipeline:
   - Native turn/observation/telemetry deduplication (``role_instruction_blobs`` &
     ``conversation_contexts``).
   - Cold telemetry archiving (``astrbot_core_reply_observations`` -> daily rollups +
     ``telemetry-archive.sqlite3``; cold ``laya_decision_events`` payloads -> archive).
   - Filesystem media offload (legacy ``conversation_image_payloads``,
     ``semantic-catalog.sqlite3`` reactions/frames, ``native_image.sqlite3`` artifacts).
   - Incremental vacuum / optional full ``VACUUM`` + WAL checkpoint + ``PRAGMA optimize``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any

from companion_v2.execution.models import TaskError
from companion_v2.storage.image_payloads import MARKER_PREFIX as CONV_IMG_MARKER, migrate_image_payloads
from companion_v2.storage.native_dedup import (
    CONTEXT_REF_LEN,
    CONTEXT_REF_PREFIX,
    ROLE_BLOB_MIN_BYTES,
    dedup_native_storage,
)
from companion_v2.storage.runtime import table_exists
from companion_v2.storage.semantic import REACTION_FILE_MARKER, SemanticStore
from companion_v2.storage.sqlite_tuning import active_sqlite_tuning_profile, apply_sidecar_pragmas
from companion_v2.storage.telemetry_archive import (
    ARCHIVE_FILENAME,
    DEFAULT_TELEMETRY_TTL_DAYS,
    archive_cold_telemetry,
)
from companion_v2.storage.tuning_config import active_telemetry_ttl_days

_AUTO_VACUUM_LABELS = {0: "none", 1: "full", 2: "incremental"}
def _sqlite_database_bytes(connection) -> int:
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    return pages * page_size


_SIDECAR_VACUUM_NAMES = ("semantic_catalog", "native_image", "telemetry_archive", "memory_search")
_DB_LABELS = {
    "main": "主数据库",
    "semantic_catalog": "语义与表情库",
    "native_image": "生图制品库",
    "telemetry_archive": "冷遥测归档库",
    "memory_search": "记忆检索索引",
}
FULL_VACUUM_DISK_MULTIPLIER = 2.2
FULL_VACUUM_DISK_MARGIN_BYTES = 64 * 1024 * 1024


def full_vacuum_required_bytes(db_bytes: int) -> int:
    """Minimum safe free disk space required before running a full SQLite VACUUM."""
    return int(max(0, int(db_bytes)) * FULL_VACUUM_DISK_MULTIPLIER) + FULL_VACUUM_DISK_MARGIN_BYTES


def inspect_disk_free_bytes(path: Path) -> int | None:
    """Return free bytes on the filesystem hosting *path*, or None if unavailable."""
    target = path if path.is_dir() else path.parent
    try:
        return int(shutil.disk_usage(target).free)
    except OSError:
        return None


def _ensure_disk_space_for_full_vacuum(db_path: Path, *, db_label: str = "主数据库",
                                      logical_bytes: int = 0) -> None:
    """Fail closed when free disk space is unavailable or below 2.2x DB size + 64 MiB."""
    db_bytes = max(_file_size(db_path), logical_bytes)
    required = full_vacuum_required_bytes(db_bytes)
    free_bytes = inspect_disk_free_bytes(db_path)
    if free_bytes is None:
        raise TaskError(
            "storage_insufficient_disk_for_vacuum",
            f"无法获取{db_label}所在磁盘可用空间，出于安全保护已暂停全量深度重组；"
            "请改用「一键常规无损瘦身」。",
        )
    if free_bytes < required:
        free_mb = round(free_bytes / (1024 * 1024), 1)
        req_mb = round(required / (1024 * 1024), 1)
        raise TaskError(
            "storage_insufficient_disk_for_vacuum",
            f"{db_label}所在磁盘可用空间不足以执行深度重组（当前可用 {free_mb} MB，安全要求至少 {req_mb} MB，约为当前{db_label}的 2.2 倍）；"
            "建议改用「一键常规无损瘦身」或先释放磁盘空间。",
        )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _sqlite_triplet_bytes(path: Path) -> tuple[int, int, int]:
    db_bytes = _file_size(path)
    wal_bytes = _file_size(path.with_name(path.name + "-wal"))
    shm_bytes = _file_size(path.with_name(path.name + "-shm"))
    return db_bytes, wal_bytes, shm_bytes


def _inspect_sqlite_file(name: str, path: Path) -> dict[str, Any]:
    db_bytes, wal_bytes, shm_bytes = _sqlite_triplet_bytes(path)
    if not path.is_file():
        return {
            "name": name,
            "path": str(path),
            "exists": False,
            "db_bytes": 0,
            "wal_bytes": 0,
            "shm_bytes": 0,
            "total_bytes": 0,
            "page_count": 0,
            "freelist_count": 0,
            "page_size": 0,
            "reclaimable_bytes": 0,
            "fragmentation_ratio": 0.0,
            "journal_mode": "",
            "auto_vacuum": "none",
        }
    page_count = 0
    freelist_count = 0
    page_size = 0
    journal_mode = ""
    auto_vacuum_code = 0
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path), timeout=2.0, isolation_level=None)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 2000")
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 0)
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "").lower()
        auto_vacuum_code = int(conn.execute("PRAGMA auto_vacuum").fetchone()[0] or 0)
    except sqlite3.Error:
        pass
    finally:
        if conn is not None:
            conn.close()
    reclaimable = freelist_count * page_size
    frag_ratio = round(freelist_count / page_count, 4) if page_count > 0 else 0.0
    return {
        "name": name,
        "path": str(path),
        "exists": True,
        "db_bytes": db_bytes,
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "total_bytes": db_bytes + wal_bytes + shm_bytes,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "page_size": page_size,
        "reclaimable_bytes": reclaimable,
        "fragmentation_ratio": frag_ratio,
        "journal_mode": journal_mode,
        "auto_vacuum": _AUTO_VACUUM_LABELS.get(auto_vacuum_code, str(auto_vacuum_code)),
    }


def _discover_sidecar_paths(
    runtime: Any,
    *,
    data_root: Path | None = None,
    native_image_dir: Path | None = None,
) -> dict[str, Path]:
    rt_root = Path(data_root) if data_root is not None else Path(runtime.data_root)
    paths: dict[str, Path] = {
        "main": Path(runtime.database_path),
        "semantic_catalog": rt_root / "semantic-catalog.sqlite3",
        "memory_search": rt_root / "memory-search.sqlite3",
        "telemetry_archive": rt_root / ARCHIVE_FILENAME,
    }
    config_store = getattr(runtime, "configuration_store", None)
    if config_store is not None and config_store is not runtime:
        paths["configuration"] = Path(config_store.database_path)

    if native_image_dir is not None:
        paths["native_image"] = Path(native_image_dir) / "native_image.sqlite3"
    else:
        for candidate in (
            rt_root / "native_extensions" / "image_native" / "native_image.sqlite3",
            rt_root / "image_native" / "native_image.sqlite3",
            rt_root.parent / "native_extensions" / "image_native" / "native_image.sqlite3",
            rt_root.parent / "image_native" / "native_image.sqlite3",
        ):
            if candidate.is_file():
                paths["native_image"] = candidate
                break
    return paths


def _count_inline_sidecar_blobs(path: Path, query: str, marker: bytes) -> int:
    if not path.is_file():
        return 0
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path), timeout=2.0, isolation_level=None)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 2000")
        row = conn.execute(query, (marker,)).fetchone()
        return int(row[0] or 0) if row is not None else 0
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def database_health_report(
    runtime: Any,
    *,
    data_root: Path | None = None,
    native_image_dir: Path | None = None,
    ttl_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce a read-only SQLite health and slimming opportunity report."""
    effective_ttl_days = (
        max(1, int(ttl_days))
        if ttl_days is not None
        else active_telemetry_ttl_days(default=DEFAULT_TELEMETRY_TTL_DAYS)
    )
    paths = _discover_sidecar_paths(runtime, data_root=data_root, native_image_dir=native_image_dir)
    databases = {
        name: _inspect_sqlite_file(name, path)
        for name, path in paths.items()
    }
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_iso = (now_utc - timedelta(days=effective_ttl_days)).isoformat()

    opportunities: dict[str, int] = {
        "un_deduped_native_turns": 0,
        "un_deduped_native_observations": 0,
        "un_deduped_core_observations": 0,
        "cold_core_observations": 0,
        "cold_laya_payloads": 0,
        "inline_conversation_images": 0,
        "inline_reaction_blobs": 0,
        "inline_native_image_blobs": 0,
    }

    if table_exists(runtime, "conversation_native_turns"):
        row = runtime.read_one(
            """SELECT COUNT(*) FROM conversation_native_turns
               WHERE (length(CAST(role_instruction AS BLOB)) >= ? AND role_instruction NOT LIKE 'blob:%')
                  OR (context_json NOT LIKE '{"_ctx_ref":"%' AND length(context_json) > ?)""",
            (ROLE_BLOB_MIN_BYTES, CONTEXT_REF_LEN),
        )
        opportunities["un_deduped_native_turns"] = int(row[0]) if row else 0

    if table_exists(runtime, "conversation_native_observations"):
        row = runtime.read_one(
            f"""SELECT COUNT(*) FROM conversation_native_observations
                WHERE context_json NOT LIKE '{CONTEXT_REF_PREFIX}%' AND length(context_json) > ?""",
            (CONTEXT_REF_LEN,),
        )
        opportunities["un_deduped_native_observations"] = int(row[0]) if row else 0

    if table_exists(runtime, "astrbot_core_reply_observations"):
        row = runtime.read_one(
            f"""SELECT COUNT(*) FROM astrbot_core_reply_observations
                WHERE context_json NOT LIKE '{CONTEXT_REF_PREFIX}%' AND length(context_json) > ?""",
            (CONTEXT_REF_LEN,),
        )
        opportunities["un_deduped_core_observations"] = int(row[0]) if row else 0
        cold_row = runtime.read_one(
            "SELECT COUNT(*) FROM astrbot_core_reply_observations WHERE received_at < ?",
            (cutoff_iso,),
        )
        opportunities["cold_core_observations"] = int(cold_row[0]) if cold_row else 0

    if table_exists(runtime, "laya_decision_events"):
        laya_row = runtime.read_one(
            """SELECT COUNT(*) FROM laya_decision_events
               WHERE observed_at < ? AND (request_json IS NOT NULL OR response_json IS NOT NULL)""",
            (cutoff_iso,),
        )
        opportunities["cold_laya_payloads"] = int(laya_row[0]) if laya_row else 0

    if table_exists(runtime, "conversation_image_payloads"):
        img_row = runtime.read_one(
            "SELECT COUNT(*) FROM conversation_image_payloads WHERE substr(content, 1, ?) <> ?",
            (len(CONV_IMG_MARKER), CONV_IMG_MARKER),
        )
        opportunities["inline_conversation_images"] = int(img_row[0]) if img_row else 0

    sem_path = paths.get("semantic_catalog")
    if sem_path is not None and sem_path.is_file():
        items_inline = _count_inline_sidecar_blobs(
            sem_path,
            f"SELECT COUNT(*) FROM items WHERE kind = 'reaction' AND content IS NOT NULL "
            f"AND length(content) > 0 AND substr(content, 1, {len(REACTION_FILE_MARKER)}) <> ?",
            REACTION_FILE_MARKER,
        )
        frames_inline = _count_inline_sidecar_blobs(
            sem_path,
            f"SELECT COUNT(*) FROM reaction_frames WHERE content IS NOT NULL "
            f"AND length(content) > 0 AND substr(content, 1, {len(REACTION_FILE_MARKER)}) <> ?",
            REACTION_FILE_MARKER,
        )
        opportunities["inline_reaction_blobs"] = items_inline + frames_inline

    nimg_path = paths.get("native_image")
    if nimg_path is not None and nimg_path.is_file():
        from astrbot_host.native_extensions.image_native.store import NATIVE_IMAGE_FILE_MARKER

        opportunities["inline_native_image_blobs"] = _count_inline_sidecar_blobs(
            nimg_path,
            f"SELECT COUNT(*) FROM artifacts WHERE length(data) > 0 "
            f"AND substr(data, 1, {len(NATIVE_IMAGE_FILE_MARKER)}) <> ?",
            NATIVE_IMAGE_FILE_MARKER,
        )

    total_sqlite_bytes = sum(item["total_bytes"] for item in databases.values())
    total_reclaimable_bytes = sum(item["reclaimable_bytes"] for item in databases.values())
    max_frag = max((item["fragmentation_ratio"] for item in databases.values()), default=0.0)

    recommendations: list[dict[str, str]] = []
    if (
        opportunities["un_deduped_native_turns"]
        + opportunities["un_deduped_native_observations"]
        + opportunities["un_deduped_core_observations"]
    ) > 0:
        recommendations.append({
            "code": "dedup_native_storage",
            "label": "存在重复存储的人格提示词或会话上下文，可无损去重缩减数据库体积",
        })
    if opportunities["cold_core_observations"] + opportunities["cold_laya_payloads"] > 0:
        recommendations.append({
            "code": "archive_cold_telemetry",
            "label": f"存在超过 {effective_ttl_days} 天的冷遥测明细，可归档至冷库并保留完整每日统计",
        })
    if (
        opportunities["inline_conversation_images"]
        + opportunities["inline_reaction_blobs"]
        + opportunities["inline_native_image_blobs"]
    ) > 0:
        recommendations.append({
            "code": "offload_inline_media",
            "label": "存在库内原始图片/表情二进制大对象，可迁移至本地文件系统减负",
        })
    if total_reclaimable_bytes >= 4 * 1024 * 1024 or max_frag >= 0.15:
        recommendations.append({
            "code": "reclaim_freelist",
            "label": "数据库存在较多可回收空闲页，建议执行增量整理或 VACUUM 释放磁盘空间",
        })

    profile = getattr(runtime, "tuning_profile", None)
    batch_stats_fn = getattr(runtime, "batched_write_stats", None)
    main_db_bytes = databases["main"]["db_bytes"] if "main" in databases else 0
    disk_free_bytes = inspect_disk_free_bytes(paths["main"])
    vacuum_targets = ("main", *_SIDECAR_VACUUM_NAMES)
    largest_db_bytes = max(
        (
            int(databases[name]["db_bytes"])
            for name in vacuum_targets
            if name in databases and databases[name].get("exists")
        ),
        default=main_db_bytes,
    )
    vacuum_required_bytes = full_vacuum_required_bytes(largest_db_bytes)
    can_full_vacuum = True
    for name in vacuum_targets:
        info = databases.get(name)
        target_path = paths.get(name)
        if not info or not info.get("exists") or target_path is None:
            continue
        target_free = disk_free_bytes if name == "main" else inspect_disk_free_bytes(target_path)
        if target_free is None or target_free < full_vacuum_required_bytes(int(info.get("db_bytes") or 0)):
            can_full_vacuum = False
            break
    return {
        "status": "compact_recommended" if recommendations else "healthy",
        "checked_at": now_utc.isoformat(),
        "telemetry_ttl_days": effective_ttl_days,
        "total_sqlite_bytes": total_sqlite_bytes,
        "total_reclaimable_bytes": total_reclaimable_bytes,
        "max_fragmentation_ratio": max_frag,
        "disk_free_bytes": disk_free_bytes,
        "vacuum_required_bytes": vacuum_required_bytes,
        "can_full_vacuum": can_full_vacuum,
        "databases": databases,
        "opportunities": opportunities,
        "recommendations": recommendations,
        "runtime": {
            "reader_pool_size": getattr(runtime, "reader_pool_size", None),
            "tuning_tier": getattr(profile, "tier", None),
            "cache_size_kib": getattr(profile, "cache_size_kib", None),
            "mmap_size": getattr(profile, "mmap_size", None),
            "synchronous": (
                profile.effective_synchronous()
                if hasattr(profile, "effective_synchronous")
                else getattr(profile, "synchronous", None)
            ),
            "batched_writes": batch_stats_fn() if callable(batch_stats_fn) else None,
        },
    }


def _maintain_sidecar_sqlite(
    path: Path,
    *,
    db_label: str = "副库",
    vacuum: bool = False,
    enable_incremental_vacuum: bool = False,
    pages: int = 512,
) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "vacuumed": False, "freed_pages": 0}
    profile = active_sqlite_tuning_profile()
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    vacuumed = False
    freed_pages = 0
    try:
        apply_sidecar_pragmas(
            conn,
            profile=profile,
            enable_wal=False,
            read_only=False,
            busy_timeout_ms=5000,
        )
        mode = int(conn.execute("PRAGMA auto_vacuum").fetchone()[0] or 0)
        if vacuum or (enable_incremental_vacuum and mode != 2):
            _ensure_disk_space_for_full_vacuum(path, db_label=db_label, logical_bytes=_sqlite_database_bytes(conn))
        if enable_incremental_vacuum and mode != 2:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("VACUUM")
            vacuumed = True
        elif vacuum:
            conn.execute("VACUUM")
            vacuumed = True
        elif mode == 2:
            before = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            if before > 0:
                conn.execute(f"PRAGMA incremental_vacuum({max(1, int(pages))})")
                after = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
                freed_pages = max(0, before - after)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)" if vacuumed else "PRAGMA wal_checkpoint(PASSIVE)")
        conn.execute("PRAGMA analysis_limit = 400")
        conn.execute("PRAGMA optimize")
    except sqlite3.Error as error:
        return {"exists": True, "vacuumed": vacuumed, "freed_pages": freed_pages, "error": type(error).__name__}
    finally:
        conn.close()
    return {"exists": True, "vacuumed": vacuumed, "freed_pages": freed_pages}


def compact_and_slim_databases(
    runtime: Any,
    *,
    data_root: Path | None = None,
    native_image_dir: Path | None = None,
    ttl_days: int | None = None,
    vacuum: bool = False,
    enable_incremental_vacuum: bool = False,
    archive_telemetry: bool = True,
    dedup_native: bool = True,
    offload_media: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run lossless database slimming, archiving, media offload, and vacuuming."""
    started = time.perf_counter()
    effective_ttl_days = (
        max(1, int(ttl_days))
        if ttl_days is not None
        else active_telemetry_ttl_days(default=DEFAULT_TELEMETRY_TTL_DAYS)
    )
    rt_root = Path(data_root) if data_root is not None else Path(runtime.data_root)
    paths = _discover_sidecar_paths(runtime, data_root=rt_root, native_image_dir=native_image_dir)

    flush_fn = getattr(runtime, "flush_batched_writes", None)
    if callable(flush_fn):
        flush_fn(timeout=5.0)

    before = database_health_report(
        runtime,
        data_root=rt_root,
        native_image_dir=native_image_dir,
        ttl_days=effective_ttl_days,
        now=now,
    )
    for name in ("main", *_SIDECAR_VACUUM_NAMES):
        info = before["databases"].get(name)
        if info and info["exists"] and (vacuum or (enable_incremental_vacuum and info["auto_vacuum"] != "incremental")):
            _ensure_disk_space_for_full_vacuum(
                paths[name], db_label=_DB_LABELS[name], logical_bytes=info["page_count"] * info["page_size"])

    steps: dict[str, Any] = {}

    # 1. Lossless native prompt & context deduplication.
    if dedup_native:
        steps["dedup"] = dedup_native_storage(runtime)

    # 2. Cold telemetry archiving + pre-aggregated daily rollups.
    if archive_telemetry:
        steps["archive"] = archive_cold_telemetry(runtime, ttl_days=effective_ttl_days, now=now)

    # 3. Media filesystem offload (conversation images, reactions, native images).
    if offload_media:
        media_stats: dict[str, Any] = {}
        if table_exists(runtime, "conversation_image_payloads"):
            with runtime.transaction() as connection:
                media_stats["conversation_images"] = migrate_image_payloads(
                    runtime.data_root, connection, batch_size=128
                )
        sem_path = paths.get("semantic_catalog")
        if sem_path is not None and sem_path.is_file():
            sem_store = SemanticStore(sem_path.parent)
            media_stats["reactions"] = sem_store.migrate_reaction_blobs_to_files(batch_size=64)
            media_stats["reactions_gc"] = sem_store.gc_unreferenced_reaction_files()
        nimg_path = paths.get("native_image")
        if nimg_path is not None and nimg_path.is_file():
            from astrbot_host.native_extensions.image_native.store import migrate_native_image_store_at

            media_stats["native_images"] = migrate_native_image_store_at(nimg_path.parent, batch_size=64)
        steps["media_offload"] = media_stats

    # 4. Reclaim freelist space, checkpoint WAL, and refresh SQLite query planner stats.
    if callable(flush_fn):
        flush_fn(timeout=5.0)
    vacuum_stats: dict[str, Any] = {}
    main_vacuumed = False
    main_freed_pages = 0
    main_error: str | None = None
    with runtime._lock:
        connection = runtime._connection
        if connection is not None and not connection.in_transaction:
            try:
                mode = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0] or 0)
                if vacuum or (enable_incremental_vacuum and mode != 2):
                    _ensure_disk_space_for_full_vacuum(paths["main"], logical_bytes=_sqlite_database_bytes(connection))
                if enable_incremental_vacuum and mode != 2:
                    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                    connection.execute("VACUUM")
                    main_vacuumed = True
                elif vacuum:
                    connection.execute("VACUUM")
                    main_vacuumed = True
                elif mode == 2:
                    before_free = int(connection.execute("PRAGMA freelist_count").fetchone()[0] or 0)
                    if before_free > 0:
                        connection.execute("PRAGMA incremental_vacuum(1024)")
                        after_free = int(connection.execute("PRAGMA freelist_count").fetchone()[0] or 0)
                        main_freed_pages = max(0, before_free - after_free)
                if main_vacuumed:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as error:
                main_error = type(error).__name__
    if not main_vacuumed:
        try:
            runtime.checkpoint_passive()
        except sqlite3.Error:
            pass
    runtime.optimize(force=True)
    vacuum_stats["main"] = {"vacuumed": main_vacuumed, "freed_pages": main_freed_pages}
    if main_error is not None:
        vacuum_stats["main"]["error"] = main_error

    for sidecar_name in _SIDECAR_VACUUM_NAMES:
        sidecar_path = paths.get(sidecar_name)
        if sidecar_path is not None and sidecar_path.is_file():
            vacuum_stats[sidecar_name] = _maintain_sidecar_sqlite(
                sidecar_path,
                db_label=_DB_LABELS.get(sidecar_name, sidecar_name),
                vacuum=vacuum,
                enable_incremental_vacuum=enable_incremental_vacuum,
            )
    steps["vacuum"] = vacuum_stats

    after = database_health_report(
        runtime,
        data_root=rt_root,
        native_image_dir=native_image_dir,
        ttl_days=effective_ttl_days,
        now=now,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return {
        "before": before,
        "after": after,
        "reclaimed_sqlite_bytes": max(0, before["total_sqlite_bytes"] - after["total_sqlite_bytes"]),
        "elapsed_ms": elapsed_ms,
        "steps": steps,
    }
