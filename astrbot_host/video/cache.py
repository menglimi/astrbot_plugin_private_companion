"""Disk compression cache and bounded memory cache for video understanding."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from .limits import SingleFlight


DEFAULT_COMPRESS_CACHE_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_COMPRESS_CACHE_TTL_SECONDS = 24 * 3600
DEFAULT_VISION_CACHE_MAX_ITEMS = 256
DEFAULT_VISION_CACHE_TTL_SECONDS = 300.0


def default_compress_cache_dir() -> Path:
    """Resolve the cache directory inside AstrBot temp storage."""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        base = Path(get_astrbot_temp_path())
    except Exception:
        base = Path(tempfile.gettempdir())
    return base / "companion_video_cache"


def _safe_key(raw_key: str) -> str:
    return hashlib.sha256(str(raw_key).encode("utf-8")).hexdigest()


def build_compress_cache_key(
    *,
    source_sha256: str,
    target_bytes: int,
    max_duration_s: float,
    oversize_action: str,
) -> str:
    """Build a deterministic disk cache key from source digest and policy knobs."""
    payload = json.dumps(
        {
            "v": 1,
            "source_sha256": str(source_sha256),
            "target_bytes": int(target_bytes),
            "max_duration_s": round(float(max_duration_s), 2),
            "oversize_action": str(oversize_action),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class CachedCompressedVideo:
    content: bytes
    mime_type: str
    compressed: bool
    truncated: bool
    duration_s: float | None
    original_duration_s: float | None
    original_bytes: int


class CompressCache:
    """Thread-safe bounded disk cache for compressed or trimmed video payloads."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        max_bytes: int = DEFAULT_COMPRESS_CACHE_MAX_BYTES,
        ttl_seconds: float = DEFAULT_COMPRESS_CACHE_TTL_SECONDS,
    ):
        self._explicit_dir = Path(cache_dir) if cache_dir is not None else None
        self.max_bytes = max(1024 * 1024, int(max_bytes))
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self._lock = threading.Lock()

    @property
    def cache_dir(self) -> Path:
        return self._explicit_dir if self._explicit_dir is not None else default_compress_cache_dir()

    def _paths(self, key: str) -> tuple[Path, Path]:
        digest = _safe_key(key)
        root = self.cache_dir
        return root / f"{digest}.mp4", root / f"{digest}.meta.json"

    def get(self, key: str) -> CachedCompressedVideo | None:
        now = time.time()
        with self._lock:
            video_path, meta_path = self._paths(key)
            if not video_path.is_file() or not meta_path.is_file():
                return None
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created_at = float(meta.get("created_at") or 0.0)
                if now - created_at > self.ttl_seconds:
                    self._remove_entry(video_path, meta_path)
                    return None
                content = video_path.read_bytes()
                expected = int(meta.get("byte_count") or -1)
                if expected != len(content) or not content:
                    self._remove_entry(video_path, meta_path)
                    return None
                os.utime(video_path, (now, now))
                os.utime(meta_path, (now, now))
                return CachedCompressedVideo(
                    content=content,
                    mime_type=str(meta.get("mime_type") or "video/mp4"),
                    compressed=bool(meta.get("compressed")),
                    truncated=bool(meta.get("truncated")),
                    duration_s=(float(meta["duration_s"]) if meta.get("duration_s") is not None else None),
                    original_duration_s=(
                        float(meta["original_duration_s"])
                        if meta.get("original_duration_s") is not None
                        else None
                    ),
                    original_bytes=int(meta.get("original_bytes") or len(content)),
                )
            except (OSError, ValueError, TypeError, KeyError):
                self._remove_entry(video_path, meta_path)
                return None

    def put(self, key: str, item: CachedCompressedVideo) -> None:
        if not item.content:
            return
        with self._lock:
            root = self.cache_dir
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                return
            video_path, meta_path = self._paths(key)
            meta = {
                "created_at": time.time(),
                "mime_type": item.mime_type,
                "byte_count": len(item.content),
                "compressed": bool(item.compressed),
                "truncated": bool(item.truncated),
                "duration_s": item.duration_s,
                "original_duration_s": item.original_duration_s,
                "original_bytes": int(item.original_bytes),
            }
            try:
                self._atomic_write(video_path, item.content)
                self._atomic_write(meta_path, json.dumps(meta, ensure_ascii=True).encode("utf-8"))
                self._prune_unlocked(root)
            except OSError:
                self._remove_entry(video_path, meta_path)

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-cv-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(tmp_name, target)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)
            except OSError:
                pass

    @staticmethod
    def _remove_entry(video_path: Path, meta_path: Path) -> None:
        for path in (video_path, meta_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _prune_unlocked(self, root: Path) -> None:
        now = time.time()
        entries: list[tuple[float, int, Path, Path]] = []
        total = 0
        try:
            items = list(root.glob("*.mp4"))
        except OSError:
            return
        for video_path in items:
            meta_path = video_path.with_name(video_path.stem + ".meta.json")
            try:
                stat = video_path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > self.ttl_seconds or not meta_path.is_file():
                self._remove_entry(video_path, meta_path)
                continue
            entries.append((stat.st_mtime, stat.st_size, video_path, meta_path))
            total += stat.st_size
        if total <= self.max_bytes:
            return
        entries.sort(key=lambda entry: entry[0])
        for _mtime, size, video_path, meta_path in entries:
            if total <= self.max_bytes:
                break
            self._remove_entry(video_path, meta_path)
            total -= size


def api_key_fingerprint(api_key: str) -> str:
    """Hash API credentials before placing them into any in-memory cache key."""
    if not isinstance(api_key, str) or not api_key:
        return "none"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def build_vision_cache_key(
    *,
    content_sha256: str,
    mime_type: str,
    prompt: str,
    protocol: str,
    endpoint_url: str,
    model: str,
    max_tokens: int,
    max_chars: int,
    api_key: str,
) -> str:
    """Build a secret-free SHA-256 cache key over all output-affecting inputs."""
    payload = json.dumps(
        {
            "v": 1,
            "content_sha256": str(content_sha256),
            "mime_type": str(mime_type),
            "prompt": str(prompt),
            "protocol": str(protocol),
            "endpoint_url": str(endpoint_url).rstrip("/"),
            "model": str(model),
            "max_tokens": int(max_tokens),
            "max_chars": int(max_chars),
            "key_fp": api_key_fingerprint(api_key),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VisionCache:
    """Bounded LRU + TTL memory cache for video narration results."""

    def __init__(
        self,
        *,
        max_items: int = DEFAULT_VISION_CACHE_MAX_ITEMS,
        ttl_seconds: float = DEFAULT_VISION_CACHE_TTL_SECONDS,
    ):
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            return
        now = time.monotonic()
        with self._lock:
            self._items[key] = (now + self.ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class VisionFlights(SingleFlight):
    """Async singleflight deduplicating identical simultaneous video vision calls."""
