"""Hardware-adaptive SQLite PRAGMA profiles shared by V2 main and sidecar stores."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import sqlite3
import sys


_GIB = 1024 * 1024 * 1024
_MIB = 1024 * 1024
LOW_MEMORY_THRESHOLD_BYTES = int(2.5 * _GIB)
HIGH_MEMORY_THRESHOLD_BYTES = 6 * _GIB
ULTRA_MEMORY_THRESHOLD_BYTES = 14 * _GIB


@dataclass(frozen=True, slots=True)
class SqliteTuningProfile:
    """SQLite cache, mmap, WAL, and reader-pool limits scaled to host RAM and CPU."""

    tier: str
    reader_pool_size: int
    cache_size_kib: int
    mmap_size: int
    sidecar_cache_size_kib: int
    sidecar_mmap_size: int
    reader_cache_size_kib: int = 0
    journal_size_limit: int = 32 * _MIB

    def effective_reader_cache_size_kib(self) -> int:
        return self.reader_cache_size_kib if self.reader_cache_size_kib > 0 else self.cache_size_kib


_LOW_MEMORY_PROFILE = SqliteTuningProfile(
    tier="low",
    reader_pool_size=2,
    cache_size_kib=8192,
    reader_cache_size_kib=8192,
    mmap_size=0,
    sidecar_cache_size_kib=4096,
    sidecar_mmap_size=0,
    journal_size_limit=16 * _MIB,
)
_MEDIUM_MEMORY_PROFILE = SqliteTuningProfile(
    tier="medium",
    reader_pool_size=3,
    cache_size_kib=32768,
    reader_cache_size_kib=16384,
    mmap_size=128 * _MIB,
    sidecar_cache_size_kib=8192,
    sidecar_mmap_size=32 * _MIB,
    journal_size_limit=32 * _MIB,
)
_HIGH_MEMORY_PROFILE = SqliteTuningProfile(
    tier="high",
    reader_pool_size=4,
    cache_size_kib=65536,
    reader_cache_size_kib=65536,
    mmap_size=256 * _MIB,
    sidecar_cache_size_kib=16384,
    sidecar_mmap_size=128 * _MIB,
    journal_size_limit=64 * _MIB,
)
_ULTRA_MEMORY_PROFILE = SqliteTuningProfile(
    tier="ultra",
    reader_pool_size=6,
    cache_size_kib=131072,
    reader_cache_size_kib=65536,
    mmap_size=1024 * _MIB,
    sidecar_cache_size_kib=32768,
    sidecar_mmap_size=256 * _MIB,
    journal_size_limit=64 * _MIB,
)


def _detect_windows_memory_bytes() -> int | None:
    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total = int(status.ullTotalPhys)
            return total if total > 0 else None
    except Exception:
        return None
    return None


def _detect_proc_meminfo_bytes() -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="ascii", errors="ignore") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        total = int(parts[1]) * 1024
                        return total if total > 0 else None
                    break
    except OSError:
        return None
    return None


def _detect_posix_sysconf_bytes() -> int | None:
    sysconf = getattr(os, "sysconf", None)
    if not callable(sysconf):
        return None
    try:
        page_size = int(sysconf("SC_PAGE_SIZE"))
        pages = int(sysconf("SC_PHYS_PAGES"))
        total = page_size * pages
        return total if total > 0 else None
    except (OSError, ValueError):
        return None


def detect_total_memory_bytes() -> int | None:
    """Return total host physical RAM in bytes, or None if unavailable."""
    if sys.platform == "win32":
        return _detect_windows_memory_bytes()
    total = _detect_proc_meminfo_bytes()
    if total is not None:
        return total
    return _detect_posix_sysconf_bytes()


def detect_cpu_count() -> int:
    """Return available logical CPU count, defaulting conservatively to 2."""
    try:
        count = os.cpu_count()
        return int(count) if count and count > 0 else 2
    except Exception:
        return 2


def tuning_profile(
    total_memory_bytes: int | None = None,
    *,
    cpu_count: int | None = None,
) -> SqliteTuningProfile:
    """Select SQLite cache, mmap, WAL, and reader-pool limits for host RAM and CPU."""
    if total_memory_bytes is None:
        total_memory_bytes = detect_total_memory_bytes()
    if total_memory_bytes is None or total_memory_bytes < LOW_MEMORY_THRESHOLD_BYTES:
        return _LOW_MEMORY_PROFILE
    cpus = detect_cpu_count() if cpu_count is None else max(1, int(cpu_count))
    if total_memory_bytes < HIGH_MEMORY_THRESHOLD_BYTES:
        pool_size = 4 if cpus >= 4 else _MEDIUM_MEMORY_PROFILE.reader_pool_size
        if pool_size == _MEDIUM_MEMORY_PROFILE.reader_pool_size:
            return _MEDIUM_MEMORY_PROFILE
        return SqliteTuningProfile(
            tier=_MEDIUM_MEMORY_PROFILE.tier,
            reader_pool_size=pool_size,
            cache_size_kib=_MEDIUM_MEMORY_PROFILE.cache_size_kib,
            reader_cache_size_kib=_MEDIUM_MEMORY_PROFILE.reader_cache_size_kib,
            mmap_size=_MEDIUM_MEMORY_PROFILE.mmap_size,
            sidecar_cache_size_kib=_MEDIUM_MEMORY_PROFILE.sidecar_cache_size_kib,
            sidecar_mmap_size=_MEDIUM_MEMORY_PROFILE.sidecar_mmap_size,
            journal_size_limit=_MEDIUM_MEMORY_PROFILE.journal_size_limit,
        )
    if total_memory_bytes < ULTRA_MEMORY_THRESHOLD_BYTES:
        pool_size = min(6, max(4, cpus // 2))
        if pool_size == _HIGH_MEMORY_PROFILE.reader_pool_size:
            return _HIGH_MEMORY_PROFILE
        return SqliteTuningProfile(
            tier=_HIGH_MEMORY_PROFILE.tier,
            reader_pool_size=pool_size,
            cache_size_kib=_HIGH_MEMORY_PROFILE.cache_size_kib,
            reader_cache_size_kib=_HIGH_MEMORY_PROFILE.reader_cache_size_kib,
            mmap_size=_HIGH_MEMORY_PROFILE.mmap_size,
            sidecar_cache_size_kib=_HIGH_MEMORY_PROFILE.sidecar_cache_size_kib,
            sidecar_mmap_size=_HIGH_MEMORY_PROFILE.sidecar_mmap_size,
            journal_size_limit=_HIGH_MEMORY_PROFILE.journal_size_limit,
        )
    pool_size = min(8, max(6, cpus // 2))
    if pool_size == _ULTRA_MEMORY_PROFILE.reader_pool_size:
        return _ULTRA_MEMORY_PROFILE
    return SqliteTuningProfile(
        tier=_ULTRA_MEMORY_PROFILE.tier,
        reader_pool_size=pool_size,
        cache_size_kib=_ULTRA_MEMORY_PROFILE.cache_size_kib,
        reader_cache_size_kib=_ULTRA_MEMORY_PROFILE.reader_cache_size_kib,
        mmap_size=_ULTRA_MEMORY_PROFILE.mmap_size,
        sidecar_cache_size_kib=_ULTRA_MEMORY_PROFILE.sidecar_cache_size_kib,
        sidecar_mmap_size=_ULTRA_MEMORY_PROFILE.sidecar_mmap_size,
        journal_size_limit=_ULTRA_MEMORY_PROFILE.journal_size_limit,
    )


def apply_sidecar_pragmas(
    connection: sqlite3.Connection,
    *,
    profile: SqliteTuningProfile | None = None,
    enable_wal: bool = False,
    read_only: bool = False,
    busy_timeout_ms: int | None = 5000,
    foreign_keys: bool = False,
) -> None:
    """Apply WAL-friendly, hardware-adaptive PRAGMAs to a sidecar SQLite connection."""
    selected = profile or tuning_profile()
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    if busy_timeout_ms is not None:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if enable_wal:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute(f"PRAGMA cache_size = -{int(selected.sidecar_cache_size_kib)}")
    connection.execute(f"PRAGMA mmap_size = {int(selected.sidecar_mmap_size)}")
    connection.execute("PRAGMA temp_store = MEMORY")
    if not read_only:
        connection.execute(f"PRAGMA journal_size_limit = {int(selected.journal_size_limit)}")
    else:
        connection.execute("PRAGMA query_only = ON")
