"""Hardware-adaptive SQLite PRAGMA profiles shared by V2 main and sidecar stores."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import sys
from threading import Lock


_GIB = 1024 * 1024 * 1024
_MIB = 1024 * 1024
LOW_MEMORY_THRESHOLD_BYTES = int(2.5 * _GIB)
HIGH_MEMORY_THRESHOLD_BYTES = 6 * _GIB
ULTRA_MEMORY_THRESHOLD_BYTES = 14 * _GIB
VALID_SYNCHRONOUS_MODES = frozenset(("NORMAL", "FULL"))
# Linux cgroup v1 reports an effectively unlimited sentinel near 2^63 - 4096.
_CGROUP_UNLIMITED_THRESHOLD = 1 << 60
_CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")

_ACTIVE_PROFILE_LOCK = Lock()
_ACTIVE_PROFILE: "SqliteTuningProfile | None" = None


@dataclass(frozen=True, slots=True)
class SqliteTuningProfile:
    """SQLite cache, mmap, WAL, synchronous, and reader-pool limits."""

    tier: str
    reader_pool_size: int
    cache_size_kib: int
    mmap_size: int
    sidecar_cache_size_kib: int
    sidecar_mmap_size: int
    reader_cache_size_kib: int = 0
    journal_size_limit: int = 32 * _MIB
    synchronous: str = "NORMAL"

    def effective_reader_cache_size_kib(self) -> int:
        return self.reader_cache_size_kib if self.reader_cache_size_kib > 0 else self.cache_size_kib

    def effective_synchronous(self) -> str:
        mode = str(self.synchronous or "NORMAL").upper()
        return mode if mode in VALID_SYNCHRONOUS_MODES else "NORMAL"


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


def _read_cgroup_limit_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        return None
    if not raw or raw == "max" or not raw.isdigit():
        return None
    limit = int(raw)
    if limit <= 0 or limit >= _CGROUP_UNLIMITED_THRESHOLD:
        return None
    return limit


def detect_cgroup_memory_limit_bytes(
    *,
    v2_path: Path | None = None,
    v1_path: Path | None = None,
) -> int | None:
    """Return Linux container cgroup memory limit in bytes, or None if unconstrained."""
    if sys.platform == "win32" and v2_path is None and v1_path is None:
        return None
    for candidate in (v2_path or _CGROUP_V2_MEMORY_MAX, v1_path or _CGROUP_V1_MEMORY_LIMIT):
        limit = _read_cgroup_limit_file(candidate)
        if limit is not None:
            return limit
    return None


def detect_host_memory_bytes() -> int | None:
    """Return raw host physical RAM in bytes without container cgroup clamping."""
    if sys.platform == "win32":
        return _detect_windows_memory_bytes()
    total = _detect_proc_meminfo_bytes()
    if total is not None:
        return total
    return _detect_posix_sysconf_bytes()


def detect_total_memory_bytes() -> int | None:
    """Return effective available RAM in bytes (min of host RAM and cgroup limit)."""
    host = detect_host_memory_bytes()
    cgroup = detect_cgroup_memory_limit_bytes()
    if host is not None and cgroup is not None:
        return min(host, cgroup)
    return cgroup if cgroup is not None else host


def set_active_sqlite_tuning_profile(profile: SqliteTuningProfile | None) -> None:
    """Register the active process-wide SQLite tuning profile for main & sidecar stores."""
    global _ACTIVE_PROFILE
    with _ACTIVE_PROFILE_LOCK:
        _ACTIVE_PROFILE = profile


def active_sqlite_tuning_profile() -> SqliteTuningProfile:
    """Return the active configured SQLite profile, or auto-detect from host hardware."""
    with _ACTIVE_PROFILE_LOCK:
        if _ACTIVE_PROFILE is not None:
            return _ACTIVE_PROFILE
    return tuning_profile()


def clear_active_sqlite_tuning_profile() -> None:
    """Reset the active profile back to hardware auto-detection (primarily for tests)."""
    set_active_sqlite_tuning_profile(None)


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
    selected = profile or active_sqlite_tuning_profile()
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys = ON")
    if busy_timeout_ms is not None:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if enable_wal:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"PRAGMA synchronous = {selected.effective_synchronous()}")
    connection.execute(f"PRAGMA cache_size = -{int(selected.sidecar_cache_size_kib)}")
    connection.execute(f"PRAGMA mmap_size = {int(selected.sidecar_mmap_size)}")
    connection.execute("PRAGMA temp_store = MEMORY")
    if not read_only:
        connection.execute(f"PRAGMA journal_size_limit = {int(selected.journal_size_limit)}")
    else:
        connection.execute("PRAGMA query_only = ON")
