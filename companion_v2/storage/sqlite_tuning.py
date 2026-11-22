"""Memory-adaptive SQLite PRAGMA profiles shared by V2 main and sidecar stores."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import sqlite3
import sys


_GIB = 1024 * 1024 * 1024
LOW_MEMORY_THRESHOLD_BYTES = 2 * _GIB
HIGH_MEMORY_THRESHOLD_BYTES = 6 * _GIB


@dataclass(frozen=True, slots=True)
class SqliteTuningProfile:
    """Bounded SQLite memory and reader-pool settings tuned to host RAM."""

    tier: str
    reader_pool_size: int
    cache_size_kib: int
    mmap_size: int
    sidecar_cache_size_kib: int
    sidecar_mmap_size: int


_LOW_MEMORY_PROFILE = SqliteTuningProfile(
    tier="low",
    reader_pool_size=2,
    cache_size_kib=8192,
    mmap_size=0,
    sidecar_cache_size_kib=4096,
    sidecar_mmap_size=0,
)
_MEDIUM_MEMORY_PROFILE = SqliteTuningProfile(
    tier="medium",
    reader_pool_size=3,
    cache_size_kib=16384,
    mmap_size=64 * 1024 * 1024,
    sidecar_cache_size_kib=8192,
    sidecar_mmap_size=0,
)
_HIGH_MEMORY_PROFILE = SqliteTuningProfile(
    tier="high",
    reader_pool_size=4,
    cache_size_kib=16384,
    mmap_size=128 * 1024 * 1024,
    sidecar_cache_size_kib=8192,
    sidecar_mmap_size=32 * 1024 * 1024,
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


def tuning_profile(total_memory_bytes: int | None = None) -> SqliteTuningProfile:
    """Select bounded SQLite cache, mmap, and reader-pool limits for host RAM."""
    if total_memory_bytes is None:
        total_memory_bytes = detect_total_memory_bytes()
    if total_memory_bytes is None or total_memory_bytes < LOW_MEMORY_THRESHOLD_BYTES:
        return _LOW_MEMORY_PROFILE
    if total_memory_bytes < HIGH_MEMORY_THRESHOLD_BYTES:
        return _MEDIUM_MEMORY_PROFILE
    return _HIGH_MEMORY_PROFILE


def apply_sidecar_pragmas(
    connection: sqlite3.Connection,
    *,
    profile: SqliteTuningProfile | None = None,
    enable_wal: bool = False,
    read_only: bool = False,
    busy_timeout_ms: int | None = 5000,
    foreign_keys: bool = False,
) -> None:
    """Apply WAL-friendly, memory-bounded PRAGMAs to a sidecar SQLite connection."""
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
    if read_only:
        connection.execute("PRAGMA query_only = ON")
