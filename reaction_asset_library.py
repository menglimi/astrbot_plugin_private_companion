# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .helpers import _safe_float, _safe_int, _single_line


CATALOG_VERSION = 2
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 120 * 1024 * 1024
MAX_ZIP_MEMBERS = 1000
LOOKUP_CACHE_TTL_SECONDS = 2.0
MAX_EMBEDDING_DIMENSION = 8192
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ANALYSIS_STATUSES = {"unprocessed", "pending", "running", "complete", "failed"}
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# Lightweight, local semantic equivalence used when no embedding provider is
# available.  These are deliberately small communication-oriented clusters,
# rather than a general thesaurus: a shared cluster only adds a soft score and
# never replaces explicit tags or a caller-supplied intent.
_SEMANTIC_MATCH_CLUSTERS: dict[str, tuple[str, ...]] = {
    "开心喜悦": ("开心", "高兴", "快乐", "愉快", "喜悦", "欢喜", "兴奋", "好耶", "庆祝", "鼓掌"),
    "安慰陪伴": ("安慰", "抱抱", "抱一抱", "陪伴", "哄哄", "哄一下", "心疼", "安抚", "鼓励", "加油", "没关系", "别难过", "别伤心"),
    "无语无奈": ("无语", "无奈", "服了", "不想说话", "说不出话", "沉默", "叹气", "扶额", "摊手"),
    "害羞脸红": ("害羞", "腼腆", "不好意思", "脸红", "扭捏"),
    "难过委屈": ("难过", "伤心", "低落", "委屈", "不开心", "不高兴", "想哭", "哭哭"),
    "惊讶意外": ("惊讶", "震惊", "意外", "吃惊", "啊这"),
    "生气恼火": ("生气", "恼火", "气愤", "发火", "愤怒"),
    "吐槽接梗": ("吐槽", "嫌弃", "质疑", "调侃", "接梗", "开玩笑"),
    "赞同回应": ("赞同", "同意", "认可", "点头", "收到", "可以"),
    "拒绝摇头": ("拒绝", "不要", "才不要", "不行", "摇头", "走开"),
}
_SEMANTIC_NEGATION_PATTERN = re.compile(
    r"(?:不是|并非|不|没|未|别|莫|无)(?:太|很|怎么|再|够|那么|特别)?$"
)


def _semantic_features(value: Any) -> tuple[set[str], set[str]]:
    """Return (cluster names, aliases blocked by a nearby negation)."""
    text = re.sub(r"[\W_]+", "", str(value or "").casefold())
    if not text:
        return set(), set()
    clusters: set[str] = set()
    blocked: set[str] = set()
    for cluster, aliases in _SEMANTIC_MATCH_CLUSTERS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            alias_key = alias.casefold()
            start = text.find(alias_key)
            while start >= 0:
                prefix = text[max(0, start - 8) : start]
                negated = bool(_SEMANTIC_NEGATION_PATTERN.search(prefix))
                if negated:
                    blocked.add(alias_key)
                else:
                    clusters.add(cluster)
                start = text.find(alias_key, start + max(1, len(alias_key)))
    return clusters, blocked


def _text_list(value: Any, *, limit: int, item_limit: int = 60) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = re.split(r"[,，;；|\n]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _single_line(item, item_limit)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _query_list(value: Any, *, limit: int = 8, item_limit: int = 160) -> list[str]:
    """Normalize a small list of lookup phrases from tool/context payloads."""
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        text = str(value or "").strip()
        parsed: Any = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        raw = list(parsed) if isinstance(parsed, list) else re.split(r"[,，;；|\n]+", text)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _single_line(item, item_limit).strip(" \t\"'")
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max(1, int(limit)):
            break
    return result


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "y", "是", "开启", "启用"}:
        return True
    if normalized in {"0", "false", "no", "off", "n", "否", "关闭", "停用"}:
        return False
    return default


def _safe_filename(value: Any, fallback: str = "reaction") -> str:
    name = Path(str(value or "")).name
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", name).strip(" ._")
    return stem[:120] or fallback


def _image_signature_matches(data: bytes, extension: str) -> bool:
    extension = extension.lower()
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension == ".gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if extension == ".bmp":
        return data.startswith(b"BM")
    return False


class ReactionAssetLibrary:
    """Small, self-contained reaction-image catalog owned by this plugin."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.root = (Path(data_dir) / "reaction_expression_library").resolve()
        self.images_dir = self.root / "images"
        self.catalog_path = self.root / "catalog.json"
        self._lock = threading.RLock()
        self._lookup_revision_stamp: tuple[int, int, int, int] | None = None
        self._lookup_revision_value = ""
        self._lookup_has_enabled_assets = False
        self._lookup_revision_checked_at = 0.0
        self._selection_revision = 0
        # Memory cache for catalog: avoids re-parsing catalog.json on every
        # read operation. Invalidated on file identity changes or after _save().
        self._cached_catalog: dict[str, Any] | None = None
        self._cached_catalog_stamp: tuple[int, int, int, int] | None = None
        # Lightweight flag for has_enabled_assets(), updated in _load(), _save() and
        # lookup_revision() so the hot path avoids a full catalog walk.
        self._cached_has_enabled_assets: bool = False
        # Summary cache: invalidated alongside the catalog cache.
        self._cached_summary: dict[str, Any] | None = None
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def _empty_catalog(self) -> dict[str, Any]:
        return {"version": CATALOG_VERSION, "updated_at": 0.0, "items": []}

    def _catalog_cache_stamp(self) -> tuple[int, int, int, int]:
        try:
            stat_result = self.catalog_path.stat()
        except OSError:
            return (0, 0, 0, 0)
        return (
            int(stat_result.st_mtime_ns),
            int(stat_result.st_ctime_ns),
            int(stat_result.st_size),
            int(stat_result.st_ino),
        )

    def _load(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return self._empty_catalog()
        current_stamp = self._catalog_cache_stamp()
        if self._cached_catalog is not None and current_stamp == self._cached_catalog_stamp:
            return self._cached_catalog
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return self._empty_catalog()
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            return self._empty_catalog()
        migrated_items: list[dict[str, Any]] = []
        for original in raw["items"]:
            if not isinstance(original, dict) or not original.get("id"):
                continue
            item = dict(original)
            if "manual_fields" not in item and "analysis_status" not in item:
                manual_fields: list[str] = []
                filename = _safe_filename(item.get("filename"))
                filename_stem = Path(filename).stem[:100]
                name = _single_line(item.get("name"), 100)
                if name and name != filename_stem:
                    manual_fields.append("name")
                derived_tags = set(
                    _text_list(re.sub(r"[_\-.]+", " ", filename_stem), limit=8)
                )
                existing_tags = set(_text_list(item.get("tags"), limit=20))
                if existing_tags - derived_tags:
                    manual_fields.append("tags")
                for key in ("emotions", "intents", "description", "visible_text"):
                    value_present = bool(
                        _text_list(item.get(key), limit=12)
                        if key in {"emotions", "intents"}
                        else _single_line(item.get(key), 500)
                    )
                    if value_present:
                        manual_fields.append(key)
                item["manual_fields"] = manual_fields
                item["analysis_status"] = "unprocessed"
            migrated_items.append(item)
        raw["version"] = CATALOG_VERSION
        raw["items"] = migrated_items
        # Update memory cache.
        self._cached_catalog = raw
        self._cached_catalog_stamp = current_stamp
        self._cached_summary = None  # invalidate summary cache
        self._cached_has_enabled_assets = any(
            isinstance(item, dict) and bool(item.get("enabled", True))
            for item in raw.get("items", [])
        )
        return raw

    def _lookup_source_stamp(self) -> tuple[int, int, int, int]:
        try:
            catalog_stat = self.catalog_path.stat()
            catalog_stamp = (int(catalog_stat.st_mtime_ns), int(catalog_stat.st_size))
        except OSError:
            catalog_stamp = (0, 0)
        try:
            images_stat = self.images_dir.stat()
            images_stamp = (int(images_stat.st_mtime_ns), int(images_stat.st_size))
        except OSError:
            images_stamp = (0, 0)
        return (*catalog_stamp, *images_stamp)

    def _save(self, catalog: dict[str, Any], *, lookup_changed: bool = True) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        previous_source_stamp = self._lookup_source_stamp() if not lookup_changed else None
        catalog["version"] = CATALOG_VERSION
        catalog["updated_at"] = time.time()
        temporary = self.catalog_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.catalog_path)
        # Update memory cache after successful write.
        self._cached_catalog_stamp = self._catalog_cache_stamp()
        self._cached_catalog = catalog
        self._cached_summary = None
        # Update lightweight enabled-assets flag.
        self._cached_has_enabled_assets = any(
            isinstance(item, dict) and bool(item.get("enabled", True))
            for item in catalog.get("items", [])
        )
        if lookup_changed:
            self._lookup_revision_stamp = None
            self._lookup_revision_value = ""
            self._lookup_has_enabled_assets = False
            self._lookup_revision_checked_at = 0.0
        else:
            current_source_stamp = self._lookup_source_stamp()
            can_preserve_lookup = bool(
                self._lookup_revision_value
                and previous_source_stamp == self._lookup_revision_stamp
                and previous_source_stamp is not None
                and previous_source_stamp[2:] == current_source_stamp[2:]
            )
            if not can_preserve_lookup:
                self._lookup_revision_stamp = None
                self._lookup_revision_value = ""
                self._lookup_has_enabled_assets = False
                self._lookup_revision_checked_at = 0.0
                return
            # Usage statistics do not participate in matching. Keep the hot
            # lookup result and advance its source stamp to the catalog just
            # written so the next reply does not parse the catalog again.
            self._lookup_revision_stamp = current_source_stamp
            self._lookup_revision_checked_at = time.monotonic()

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        scopes = [scope for scope in _text_list(item.get("scopes"), limit=2) if scope in {"private", "group"}]
        analysis_status = _single_line(item.get("analysis_status"), 20).lower()
        if analysis_status not in ANALYSIS_STATUSES:
            analysis_status = "unprocessed"
        manual_fields = [
            field
            for field in _text_list(item.get("manual_fields"), limit=8, item_limit=24)
            if field in {"name", "tags", "emotions", "intents", "description", "visible_text"}
        ]
        return {
            "id": _single_line(item.get("id"), 64),
            "filename": _safe_filename(item.get("filename")),
            "stored_name": _safe_filename(item.get("stored_name")),
            "sha256": _single_line(item.get("sha256"), 64).lower(),
            "name": _single_line(item.get("name"), 100),
            "tags": _text_list(item.get("tags"), limit=20),
            "emotions": _text_list(item.get("emotions"), limit=12),
            "intents": _text_list(item.get("intents"), limit=12),
            "description": _single_line(item.get("description"), 500),
            "visible_text": _single_line(item.get("visible_text"), 300),
            "scopes": scopes or ["private", "group"],
            "enabled": _safe_bool(item.get("enabled", True), True),
            "source": _single_line(item.get("source"), 40) or "upload",
            "size": _safe_int(item.get("size"), 0, 0),
            "width": _safe_int(item.get("width"), 0, 0),
            "height": _safe_int(item.get("height"), 0, 0),
            "usage_count": _safe_int(item.get("usage_count"), 0, 0),
            "last_used_at": _safe_float(item.get("last_used_at"), 0.0, 0.0),
            "created_at": _safe_float(item.get("created_at"), time.time(), 0.0),
            "updated_at": _safe_float(item.get("updated_at"), time.time(), 0.0),
            "analysis_status": analysis_status,
            "analysis_error": _single_line(item.get("analysis_error"), 240),
            "analysis_provider": _single_line(item.get("analysis_provider"), 160),
            "analyzed_at": _safe_float(item.get("analyzed_at"), 0.0, 0.0),
            "manual_fields": manual_fields,
        }

    def _path_for(self, item: dict[str, Any]) -> Path | None:
        stored_name = _safe_filename(item.get("stored_name"), "")
        if not stored_name:
            return None
        path = (self.images_dir / stored_name).resolve()
        try:
            path.relative_to(self.images_dir)
        except ValueError:
            return None
        return path

    @staticmethod
    def _dimensions(data: bytes) -> tuple[int, int]:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                return int(image.width), int(image.height)
        except Exception:
            return 0, 0

    def has_enabled_assets(self) -> bool:
        # Use the lightweight flag updated by _save(), _load(), and lookup_revision().
        # This avoids a full catalog walk on the every-normal-reply hot path.
        with self._lock:
            # Ensure catalog is loaded before checking the flag.
            if self._cached_catalog is None:
                self._load()
            return bool(self._cached_has_enabled_assets)

    def lookup_revision(self) -> str:
        """Return a stable revision for fields that affect runtime matching.

        Usage counters are intentionally excluded from this catalog revision.
        ``selection_revision`` layers an in-process usage generation over it,
        while edits to matching fields or backing files change this base value.
        """
        with self._lock:
            now = time.monotonic()
            if (
                self._lookup_revision_value
                and now - self._lookup_revision_checked_at < LOOKUP_CACHE_TTL_SECONDS
            ):
                return self._lookup_revision_value

            stamp = self._lookup_source_stamp()
            if (
                self._lookup_revision_stamp == stamp
                and self._lookup_revision_value
            ):
                self._lookup_revision_checked_at = now
                return self._lookup_revision_value

            items = [self._normalize_item(raw) for raw in self._load()["items"]]
            rows: list[dict[str, Any]] = []
            has_enabled_assets = False
            for item in items:
                path = self._path_for(item)
                try:
                    file_stat = path.stat() if path is not None else None
                    file_revision = (
                        int(file_stat.st_mtime_ns),
                        int(file_stat.st_size),
                    ) if file_stat is not None else None
                except OSError:
                    file_revision = None
                if item["enabled"] and file_revision is not None:
                    has_enabled_assets = True
                rows.append(
                    {
                        "id": item["id"],
                        "enabled": item["enabled"],
                        "scopes": item["scopes"],
                        "name": item["name"],
                        "description": item["description"],
                        "visible_text": item["visible_text"],
                        "tags": item["tags"],
                        "emotions": item["emotions"],
                        "intents": item["intents"],
                        "file": file_revision,
                    }
                )
            payload = json.dumps(
                rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            revision = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
            self._lookup_revision_stamp = stamp
            self._lookup_revision_value = revision
            self._lookup_has_enabled_assets = has_enabled_assets
            self._lookup_revision_checked_at = now
            # Also update the lightweight flag used by has_enabled_assets().
            self._cached_has_enabled_assets = has_enabled_assets
            return revision

    def selection_revision(self) -> str:
        """Include in-process usage changes in reaction selection cache keys."""
        with self._lock:
            return f"{self.lookup_revision()}:usage-{self._selection_revision}"

    def summary(self) -> dict[str, Any]:
        with self._lock:
            if self._cached_summary is not None:
                return self._cached_summary
            items = [self._normalize_item(item) for item in self._load()["items"]]
        available = []
        for item in items:
            path = self._path_for(item)
            if path is not None and path.is_file():
                available.append(item)
        result = {
            "total": len(items),
            "enabled": sum(1 for item in available if item["enabled"]),
            "disabled": sum(1 for item in items if not item["enabled"]),
            "missing": len(items) - len(available),
            "private": sum(1 for item in available if item["enabled"] and "private" in item["scopes"]),
            "group": sum(1 for item in available if item["enabled"] and "group" in item["scopes"]),
            "usage_count": sum(item["usage_count"] for item in items),
            "analyzed": sum(1 for item in items if item["analysis_status"] == "complete"),
            "analysis_pending": sum(1 for item in items if item["analysis_status"] in {"pending", "running"}),
            "analysis_failed": sum(1 for item in items if item["analysis_status"] == "failed"),
            "analysis_unprocessed": sum(1 for item in items if item["analysis_status"] == "unprocessed"),
        }
        with self._lock:
            self._cached_summary = result
        return result

    @staticmethod
    def embedding_text(item: dict[str, Any]) -> str:
        """Build a stable, metadata-only document for semantic reaction lookup."""
        parts = [
            item.get("name", ""),
            item.get("description", ""),
            item.get("visible_text", ""),
            " ".join(item.get("tags", []) or []),
            " ".join(item.get("emotions", []) or []),
            " ".join(item.get("intents", []) or []),
        ]
        return _single_line("；".join(str(part or "") for part in parts), 1800)

    @classmethod
    def embedding_text_hash(cls, item: dict[str, Any]) -> str:
        text = cls.embedding_text(item)
        return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""

    @staticmethod
    def _coerce_embedding_vector(value: Any) -> list[float]:
        if isinstance(value, dict):
            for key in ("embedding", "vector", "data", "embeddings", "vectors"):
                if key in value:
                    result = ReactionAssetLibrary._coerce_embedding_vector(value.get(key))
                    if result:
                        return result
            return []
        if not isinstance(value, (list, tuple)):
            return []
        result: list[float] = []
        for item in value:
            try:
                result.append(float(item))
            except (TypeError, ValueError):
                return ReactionAssetLibrary._coerce_embedding_vector(value[0]) if value else []
        return result[:MAX_EMBEDDING_DIMENSION]

    @classmethod
    def normalize_embedding_vector(cls, value: Any) -> list[float]:
        vector = cls._coerce_embedding_vector(value)
        norm = sum(item * item for item in vector) ** 0.5
        return [item / norm for item in vector] if norm > 0 else []

    def embedding_status(self, provider_id: Any) -> dict[str, int | str]:
        provider = _single_line(provider_id, 160)
        indexed = 0
        missing = 0
        with self._lock:
            for raw in self._load()["items"]:
                item = self._normalize_item(raw)
                valid = (
                    _single_line(raw.get("embedding_provider"), 160) == provider
                    and _single_line(raw.get("embedding_text_hash"), 80) == self.embedding_text_hash(item)
                    and bool(self.normalize_embedding_vector(raw.get("embedding")))
                )
                if valid:
                    indexed += 1
                else:
                    missing += 1
        return {"provider_id": provider, "indexed": indexed, "missing": missing, "total": indexed + missing}

    def list_embedding_rows(self, provider_id: Any, *, limit: int = 1200) -> list[tuple[dict[str, Any], list[float], str]]:
        provider = _single_line(provider_id, 160)
        safe_limit = max(1, min(5000, _safe_int(limit, 1200, 1)))
        rows: list[tuple[dict[str, Any], list[float], str]] = []
        with self._lock:
            for raw in self._load()["items"]:
                item = self._normalize_item(raw)
                path = self._path_for(item)
                text_hash = self.embedding_text_hash(item)
                vector = self.normalize_embedding_vector(raw.get("embedding"))
                if (
                    not item["enabled"]
                    or path is None
                    or not path.is_file()
                    or _single_line(raw.get("embedding_provider"), 160) != provider
                    or _single_line(raw.get("embedding_text_hash"), 80) != text_hash
                    or not vector
                ):
                    continue
                rows.append((item, vector, text_hash))
                if len(rows) >= safe_limit:
                    break
        return rows

    def list_embedding_missing(self, provider_id: Any, *, limit: int = 50) -> list[tuple[dict[str, Any], str]]:
        provider = _single_line(provider_id, 160)
        safe_limit = max(1, min(200, _safe_int(limit, 50, 1)))
        rows: list[tuple[dict[str, Any], str]] = []
        with self._lock:
            for raw in self._load()["items"]:
                item = self._normalize_item(raw)
                path = self._path_for(item)
                if not item["enabled"] or path is None or not path.is_file():
                    continue
                text_hash = self.embedding_text_hash(item)
                vector = self.normalize_embedding_vector(raw.get("embedding"))
                if (
                    _single_line(raw.get("embedding_provider"), 160) == provider
                    and _single_line(raw.get("embedding_text_hash"), 80) == text_hash
                    and vector
                ):
                    continue
                rows.append((item, text_hash))
                if len(rows) >= safe_limit:
                    break
        return rows

    def upsert_embeddings(self, provider_id: Any, rows: Any) -> int:
        provider = _single_line(provider_id, 160)
        if not provider or not isinstance(rows, list):
            return 0
        updates = {str(row.get("id") or ""): row for row in rows if isinstance(row, dict) and row.get("id")}
        changed = 0
        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                row = updates.get(item["id"])
                if not row:
                    continue
                vector = self.normalize_embedding_vector(row.get("vector"))
                text_hash = _single_line(row.get("text_hash"), 80)
                if not vector or text_hash != self.embedding_text_hash(item):
                    continue
                raw = dict(raw)
                raw["embedding_provider"] = provider
                raw["embedding_text_hash"] = text_hash
                raw["embedding"] = vector
                raw["updated_at"] = time.time()
                catalog["items"][index] = raw
                changed += 1
            if changed:
                self._save(catalog, lookup_changed=False)
                self._selection_revision += 1
        return changed

    def list_items(
        self,
        *,
        query: Any = "",
        status: Any = "all",
        scope: Any = "all",
        analysis: Any = "all",
        page: int = 1,
        page_size: int = 48,
    ) -> dict[str, Any]:
        query_text = _single_line(query, 160).casefold()
        status_text = _single_line(status, 20).lower() or "all"
        scope_text = _single_line(scope, 20).lower() or "all"
        analysis_text = _single_line(analysis, 20).lower() or "all"
        page = max(1, _safe_int(page, 1, 1))
        page_size = _safe_int(page_size, 48, 1, 120)
        with self._lock:
            catalog = self._load()
            items = [self._normalize_item(raw) for raw in catalog["items"]]
        filtered: list[dict[str, Any]] = []
        for item in items:
            path = self._path_for(item)
            missing = path is None or not path.is_file()
            if status_text == "enabled" and (not item["enabled"] or missing):
                continue
            if status_text == "disabled" and item["enabled"]:
                continue
            if status_text == "missing" and not missing:
                continue
            if scope_text in {"private", "group"} and scope_text not in item["scopes"]:
                continue
            if analysis_text == "pending" and item["analysis_status"] not in {"pending", "running"}:
                continue
            if analysis_text in {"complete", "failed", "unprocessed"} and item["analysis_status"] != analysis_text:
                continue
            haystack = " ".join(
                [
                    item["name"],
                    item["filename"],
                    item["description"],
                    item["visible_text"],
                    *item["tags"],
                    *item["emotions"],
                    *item["intents"],
                ]
            ).casefold()
            if query_text and query_text not in haystack:
                query_parts = [part for part in re.split(r"\s+", query_text) if part]
                if not query_parts or not all(part in haystack for part in query_parts):
                    continue
            public = dict(item)
            public["missing"] = missing
            public["preview_endpoint"] = f"/reaction_library/image_data?id={item['id']}" if not missing else ""
            filtered.append(public)
        filtered.sort(key=lambda item: (item["missing"], not item["enabled"], -item["updated_at"], item["name"]))
        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "items": filtered[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "summary": self.summary(),
        }

    def _metadata_defaults(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        metadata = metadata if isinstance(metadata, dict) else {}
        tags = _text_list(metadata.get("tags"), limit=20)
        emotions = _text_list(metadata.get("emotions"), limit=12)
        intents = _text_list(metadata.get("intents"), limit=12)
        return {
            "tags": tags,
            "emotions": emotions,
            "intents": intents,
            "scopes": [scope for scope in _text_list(metadata.get("scopes"), limit=2) if scope in {"private", "group"}] or ["private", "group"],
            "enabled": _safe_bool(metadata.get("enabled", True), True),
            "auto_analyze": _safe_bool(metadata.get("auto_analyze", True), True),
            "manual_fields": [
                key
                for key, value in (("tags", tags), ("emotions", emotions), ("intents", intents))
                if value
            ],
        }

    def import_blobs(
        self,
        blobs: Iterable[tuple[str, bytes]],
        *,
        metadata: dict[str, Any] | None = None,
        source: str = "upload",
    ) -> dict[str, Any]:
        defaults = self._metadata_defaults(metadata)
        now = time.time()
        imported: list[dict[str, Any]] = []
        duplicates: list[str] = []
        rejected: list[dict[str, str]] = []
        total_bytes = 0
        with self._lock:
            catalog = self._load()
            hashes = {
                _single_line(item.get("sha256"), 64).lower()
                for item in catalog["items"]
                if isinstance(item, dict)
            }
            for original_name, raw_data in blobs:
                filename = _safe_filename(original_name)
                data = bytes(raw_data or b"")
                total_bytes += len(data)
                if total_bytes > MAX_BATCH_BYTES:
                    rejected.append({"name": filename, "reason": "批次总大小超过 120 MB"})
                    break
                extension = Path(filename).suffix.lower()
                if extension not in SUPPORTED_EXTENSIONS:
                    rejected.append({"name": filename, "reason": "不支持的图片格式"})
                    continue
                if not data or len(data) > MAX_SINGLE_FILE_BYTES:
                    rejected.append({"name": filename, "reason": "文件为空或超过 20 MB"})
                    continue
                if not _image_signature_matches(data, extension):
                    rejected.append({"name": filename, "reason": "文件内容与图片格式不符"})
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if digest in hashes:
                    duplicates.append(filename)
                    continue
                item_id = uuid.uuid4().hex
                stored_name = f"{item_id}{extension}"
                target = self.images_dir / stored_name
                target.write_bytes(data)
                width, height = self._dimensions(data)
                filename_tags = _text_list(re.sub(r"[_\-.]+", " ", Path(filename).stem), limit=8)
                item = self._normalize_item(
                    {
                        "id": item_id,
                        "filename": filename,
                        "stored_name": stored_name,
                        "sha256": digest,
                        "name": Path(filename).stem[:100],
                        "tags": defaults["tags"] if defaults["tags"] else filename_tags,
                        "emotions": defaults["emotions"],
                        "intents": defaults["intents"],
                        "scopes": defaults["scopes"],
                        "enabled": defaults["enabled"],
                        "source": source,
                        "size": len(data),
                        "width": width,
                        "height": height,
                        "analysis_status": "pending" if defaults["auto_analyze"] else "unprocessed",
                        "manual_fields": defaults["manual_fields"],
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                catalog["items"].append(item)
                hashes.add(digest)
                imported.append(item)
            if imported:
                self._save(catalog)
        return {
            "imported": len(imported),
            "duplicates": duplicates,
            "rejected": rejected,
            "items": imported,
            "analysis_queued": sum(1 for item in imported if item["analysis_status"] == "pending"),
            "summary": self.summary(),
        }

    def import_base64_payloads(
        self,
        files: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = files if isinstance(files, list) else []
        blobs: list[tuple[str, bytes]] = []
        rejected: list[dict[str, str]] = []
        for entry in entries[:MAX_ZIP_MEMBERS]:
            if not isinstance(entry, dict):
                continue
            name = _safe_filename(entry.get("name"))
            encoded = str(entry.get("data") or "")
            if encoded.startswith("data:") and "," in encoded:
                encoded = encoded.split(",", 1)[1]
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                rejected.append({"name": name, "reason": "Base64 数据无效"})
                continue
            if Path(name).suffix.lower() == ".zip":
                try:
                    blobs.extend(self._read_zip(data))
                except ValueError as exc:
                    rejected.append({"name": name, "reason": _single_line(exc, 160)})
            else:
                blobs.append((name, data))
        result = self.import_blobs(blobs, metadata=metadata, source="upload")
        result["rejected"] = [*rejected, *result.get("rejected", [])]
        return result

    def _read_zip(self, data: bytes) -> list[tuple[str, bytes]]:
        if len(data) > MAX_BATCH_BYTES:
            raise ValueError("ZIP 文件超过 120 MB")
        result: list[tuple[str, bytes]] = []
        expanded = 0
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("ZIP 文件损坏或格式无效") from exc
        with archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise ValueError("ZIP 内文件数量超过 1000")
            for member in members:
                if member.is_dir():
                    continue
                normalized = member.filename.replace("\\", "/")
                path = Path(normalized)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("ZIP 包含不安全路径")
                extension = path.suffix.lower()
                if extension not in SUPPORTED_EXTENSIONS:
                    continue
                expanded += max(0, int(member.file_size))
                if member.file_size > MAX_SINGLE_FILE_BYTES or expanded > MAX_BATCH_BYTES:
                    raise ValueError("ZIP 解压后体积超过限制")
                result.append((path.name, archive.read(member)))
        return result

    def get_image_data(self, item_id: Any) -> dict[str, Any] | None:
        item_key = _single_line(item_id, 64)
        with self._lock:
            item = next(
                (self._normalize_item(raw) for raw in self._load()["items"] if _single_line(raw.get("id"), 64) == item_key),
                None,
            )
            path = self._path_for(item) if item else None
            if item is None or path is None or not path.is_file():
                return None
            data = path.read_bytes()
        mime = MIME_BY_EXTENSION.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {"data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", "mime": mime, "name": item["filename"]}

    def get_analysis_image_data(self, item_id: Any, *, max_edge: int = 1024) -> dict[str, Any] | None:
        """Return a bounded still image for visual metadata extraction."""
        item_key = _single_line(item_id, 64)
        with self._lock:
            item = next(
                (
                    self._normalize_item(raw)
                    for raw in self._load()["items"]
                    if _single_line(raw.get("id"), 64) == item_key
                ),
                None,
            )
            path = self._path_for(item) if item else None
            if item is None or path is None or not path.is_file():
                return None
            data = path.read_bytes()
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(data)) as image:
                frame_count = max(1, int(getattr(image, "n_frames", 1) or 1))
                if frame_count > 1:
                    sample_count = min(4, frame_count)
                    sample_indexes = sorted(
                        {round(index * (frame_count - 1) / max(1, sample_count - 1)) for index in range(sample_count)}
                    )
                    cell_edge = max(128, int(max_edge) // 2)
                    frames = []
                    for frame_index in sample_indexes:
                        image.seek(frame_index)
                        sampled = image.convert("RGBA")
                        sampled.thumbnail((cell_edge, cell_edge))
                        frames.append(sampled.copy())
                    columns = 2 if len(frames) > 1 else 1
                    rows = (len(frames) + columns - 1) // columns
                    frame = Image.new("RGBA", (cell_edge * columns, cell_edge * rows), (255, 255, 255, 255))
                    for frame_index, sampled in enumerate(frames):
                        left = (frame_index % columns) * cell_edge + (cell_edge - sampled.width) // 2
                        top = (frame_index // columns) * cell_edge + (cell_edge - sampled.height) // 2
                        frame.alpha_composite(sampled, (left, top))
                else:
                    image.seek(0)
                    frame = ImageOps.exif_transpose(image).copy()
                frame.thumbnail((max(128, int(max_edge)), max(128, int(max_edge))))
                output = io.BytesIO()
                if frame.mode in {"RGBA", "LA"} or "transparency" in frame.info:
                    frame = frame.convert("RGBA")
                    frame.save(output, format="PNG", optimize=True)
                    mime = "image/png"
                else:
                    frame = frame.convert("RGB")
                    frame.save(output, format="JPEG", quality=86, optimize=True)
                    mime = "image/jpeg"
                data = output.getvalue()
        except Exception:
            # The original asset remains available for delivery, but sending an
            # undecoded GIF to some Gemini-compatible vision gateways fails the
            # whole analysis batch with a provider 500.
            if path.suffix.lower() == ".gif":
                return None
            mime = MIME_BY_EXTENSION.get(path.suffix.lower()) or "application/octet-stream"
        return {
            "id": item["id"],
            "name": item["filename"],
            "data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        }

    def analysis_candidates(
        self,
        ids: Any = None,
        *,
        statuses: Iterable[str] = ("pending",),
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        item_ids = set(_text_list(ids, limit=500, item_limit=64)) if ids is not None else set()
        allowed = {str(status or "").strip().lower() for status in statuses}
        maximum = _safe_int(limit, 4, 1, 20)
        with self._lock:
            items = [self._normalize_item(raw) for raw in self._load()["items"]]
        result: list[dict[str, Any]] = []
        for item in items:
            if item_ids and item["id"] not in item_ids:
                continue
            if item["analysis_status"] not in allowed:
                continue
            path = self._path_for(item)
            if path is None or not path.is_file():
                continue
            result.append(item)
            if len(result) >= maximum:
                break
        return result

    def queue_analysis(self, ids: Any, *, include_complete: bool = False) -> dict[str, Any]:
        item_ids = set(_text_list(ids, limit=500, item_limit=64))
        queued: list[str] = []
        now = time.time()
        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] not in item_ids:
                    continue
                if item["analysis_status"] == "complete" and not include_complete:
                    continue
                item["analysis_status"] = "pending"
                item["analysis_error"] = ""
                item["updated_at"] = now
                catalog["items"][index] = item
                queued.append(item["id"])
            if queued:
                self._save(catalog)
        return {"queued": len(queued), "ids": queued, "summary": self.summary()}

    def mark_analysis_running(self, ids: Any) -> int:
        item_ids = set(_text_list(ids, limit=20, item_limit=64))
        changed = 0
        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] not in item_ids or item["analysis_status"] != "pending":
                    continue
                item["analysis_status"] = "running"
                item["analysis_error"] = ""
                catalog["items"][index] = item
                changed += 1
            if changed:
                self._save(catalog)
        return changed

    def mark_analysis_failed(self, ids: Any, error: Any) -> int:
        item_ids = set(_text_list(ids, limit=500, item_limit=64))
        error_text = _single_line(error, 240) or "视觉模型未返回可用结果"
        changed = 0
        now = time.time()
        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] not in item_ids:
                    continue
                item["analysis_status"] = "failed"
                item["analysis_error"] = error_text
                item["analyzed_at"] = now
                item["updated_at"] = now
                catalog["items"][index] = item
                changed += 1
            if changed:
                self._save(catalog)
        return changed

    def apply_analysis_results(
        self,
        results: Any,
        *,
        provider_id: Any = "",
    ) -> dict[str, Any]:
        rows = results if isinstance(results, list) else []
        by_id = {
            _single_line(row.get("id"), 64): row
            for row in rows
            if isinstance(row, dict) and _single_line(row.get("id"), 64)
        }
        completed: list[str] = []
        now = time.time()

        def merge_values(existing: list[str], generated: Any, limit: int) -> list[str]:
            return _text_list([*existing, *_text_list(generated, limit=limit)], limit=limit)

        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                row = by_id.get(item["id"])
                if row is None:
                    continue
                manual = set(item["manual_fields"])
                if "name" not in manual:
                    generated_name = _single_line(row.get("name"), 100)
                    if generated_name:
                        item["name"] = generated_name
                for key, limit in (("tags", 20), ("emotions", 12), ("intents", 12)):
                    item[key] = merge_values(item[key] if key in manual else [], row.get(key), limit)
                if "description" not in manual:
                    item["description"] = _single_line(row.get("description"), 500)
                if "visible_text" not in manual:
                    item["visible_text"] = _single_line(row.get("visible_text"), 300)
                item["analysis_status"] = "complete"
                item["analysis_error"] = ""
                item["analysis_provider"] = _single_line(provider_id, 160)
                item["analyzed_at"] = now
                item["updated_at"] = now
                catalog["items"][index] = item
                completed.append(item["id"])
            if completed:
                self._save(catalog)
        return {"completed": len(completed), "ids": completed, "summary": self.summary()}

    def update_items(self, ids: Any, changes: Any) -> dict[str, Any]:
        item_ids = set(_text_list(ids, limit=500, item_limit=64))
        changes = changes if isinstance(changes, dict) else {}
        now = time.time()
        updated: list[str] = []
        with self._lock:
            catalog = self._load()
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] not in item_ids:
                    continue
                if "name" in changes:
                    item["name"] = _single_line(changes.get("name"), 100) or item["name"]
                    if "name" not in item["manual_fields"]:
                        item["manual_fields"].append("name")
                for key, limit in (("tags", 20), ("emotions", 12), ("intents", 12)):
                    if key in changes:
                        item[key] = _text_list(changes.get(key), limit=limit)
                        if key not in item["manual_fields"]:
                            item["manual_fields"].append(key)
                for key, limit in (("description", 500), ("visible_text", 300)):
                    if key in changes:
                        item[key] = _single_line(changes.get(key), limit)
                        if key not in item["manual_fields"]:
                            item["manual_fields"].append(key)
                if "scopes" in changes:
                    scopes = [scope for scope in _text_list(changes.get("scopes"), limit=2) if scope in {"private", "group"}]
                    if scopes:
                        item["scopes"] = scopes
                if "enabled" in changes:
                    item["enabled"] = _safe_bool(changes.get("enabled"), item["enabled"])
                item["updated_at"] = now
                catalog["items"][index] = item
                updated.append(item["id"])
            if updated:
                self._save(catalog)
        return {"updated": len(updated), "ids": updated, "summary": self.summary()}

    def delete_items(self, ids: Any) -> dict[str, Any]:
        item_ids = set(_text_list(ids, limit=500, item_limit=64))
        removed: list[str] = []
        failed: list[str] = []
        with self._lock:
            catalog = self._load()
            kept: list[dict[str, Any]] = []
            for raw in catalog["items"]:
                item = self._normalize_item(raw)
                if item["id"] not in item_ids:
                    kept.append(item)
                    continue
                path = self._path_for(item)
                try:
                    if path is not None:
                        path.unlink(missing_ok=True)
                except OSError:
                    # Keep an item whose backing file could not be removed so
                    # a transient lock/permission error never loses catalog data.
                    kept.append(item)
                    failed.append(item["id"])
                    continue
                removed.append(item["id"])
            if removed:
                catalog["items"] = kept
                self._save(catalog)
        return {
            "deleted": len(removed),
            "ids": removed,
            "failed": failed,
            "summary": self.summary(),
        }

    @staticmethod
    def _tokens(value: str) -> list[str]:
        normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", value.casefold())
        tokens = [token for token in normalized.split() if token]
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return list(dict.fromkeys(tokens))[:80]

    def find(
        self,
        query: Any,
        *,
        context: Any = "",
        scope: str = "private",
        selection_preferences: Any = None,
        selection_signature: Any = "",
        embedding_query: Any = None,
        embedding_provider_id: Any = "",
        embedding_score_threshold: float = 0.42,
        embedding_weight: float = 0.7,
        embedding_candidate_limit: int = 1200,
    ) -> dict[str, Any] | None:
        query_text = _single_line(query, 500)
        context_text = _single_line(context, 1000)
        scope_text = _single_line(scope, 20).casefold() or "private"
        if scope_text not in {"private", "group"}:
            return None
        context_tokens = self._tokens(context_text)
        # Structured reaction intents put a few alternate lookup phrases in
        # the context. Treat them as first-class queries so a generic provider
        # query does not drown out a useful model-supplied synonym.
        candidate_queries: list[str] = []
        candidate_match = re.search(
            r"(?:候选检索表达|候选检索词|候选表达)\s*[:：]\s*(.*)",
            context_text,
            flags=re.IGNORECASE,
        )
        if candidate_match:
            candidate_text = candidate_match.group(1)
            # The lookup context is a semicolon-delimited diagnostic string;
            # stop at the next labeled context section instead of treating
            # relationship/emotion JSON as a search phrase.
            candidate_text = re.split(
                r"；(?=(?:当前语境|近期用户意图|当前关系状态|情绪余波|用户对近期用户意图))",
                candidate_text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            candidate_queries = _query_list(candidate_text, limit=8)
        query_semantic_clusters, _ = _semantic_features(
            "；".join([query_text, *candidate_queries])
        )
        _, blocked_query_aliases = _semantic_features(query_text)
        # Avoid turning a negated phrase such as “不开心” into a positive
        # keyword hit merely because the shorter alias “开心” is present.
        query_tokens = [
            token
            for token in self._tokens(query_text)
            if not any(alias in token for alias in blocked_query_aliases)
        ]
        preference_rows: list[dict[str, Any]] = []
        if isinstance(selection_preferences, dict):
            raw_rows = selection_preferences.get("assets")
            if isinstance(raw_rows, list):
                preference_rows = [row for row in raw_rows if isinstance(row, dict)]
            elif isinstance(raw_rows, dict):
                preference_rows = [
                    {"key": key, **value}
                    for key, value in raw_rows.items()
                    if isinstance(value, dict)
                ]
        preference_by_key = {
            _single_line(row.get("key"), 180): row
            for row in preference_rows
            if _single_line(row.get("key"), 180)
        }

        def preference_bias(item: dict[str, Any]) -> float:
            if not preference_by_key:
                return 0.0
            keys = {
                _single_line(item.get("id"), 180),
                f"pc-local:{_single_line(item.get('id'), 160)}",
            }
            matched = next(
                (preference_by_key[key] for key in keys if key in preference_by_key),
                None,
            )
            if not isinstance(matched, dict):
                return 0.0
            total_score = _safe_float(matched.get("score"), 0.0, -20.0, 20.0)
            intent_score = _safe_float(matched.get("intent_score"), 0.0, -8.0, 8.0)
            # A same-intent preference has more weight, but never enough to
            # rescue a weak lexical match or overturn a clear topic mismatch.
            return max(-1.2, min(1.2, total_score * 0.06 + intent_score * 0.16))
        embedding_provider = _single_line(embedding_provider_id, 160)
        embedding_vector = self.normalize_embedding_vector(embedding_query)
        embedding_threshold = max(0.0, min(0.99, _safe_float(embedding_score_threshold, 0.42, 0.0, 0.99)))
        embedding_factor = max(0.0, min(2.0, _safe_float(embedding_weight, 0.7, 0.0, 2.0)))
        embedding_limit = _safe_int(embedding_candidate_limit, 1200, 20, 5000)
        with self._lock:
            raw_items = self._load()["items"]
            candidates = [self._normalize_item(raw) for raw in raw_items]
            embedding_rows = sorted(
                zip(raw_items, candidates),
                key=lambda row: row[1]["updated_at"],
                reverse=True,
            )[:embedding_limit]
            embeddings_by_id = {
                item["id"]: self.normalize_embedding_vector(raw.get("embedding"))
                for raw, item in embedding_rows
                if (
                    embedding_vector
                    and embedding_provider
                    and _single_line(raw.get("embedding_provider"), 160) == embedding_provider
                    and _single_line(raw.get("embedding_text_hash"), 80) == self.embedding_text_hash(item)
                )
            }
        ranked: list[tuple[float, float, dict[str, Any], Path, list[str], float, float]] = []
        now = time.time()
        for item in candidates:
            path = self._path_for(item)
            if not item["enabled"] or scope_text not in item["scopes"] or path is None or not path.is_file():
                continue
            primary = " ".join(
                [
                    item["name"],
                    item["description"],
                    item["visible_text"],
                    *item["tags"],
                    *item["emotions"],
                    *item["intents"],
                ]
            ).casefold()
            secondary = item["filename"].casefold()
            item_semantic_clusters, _item_blocked_aliases = _semantic_features(primary)
            shared_semantic_clusters = query_semantic_clusters & item_semantic_clusters
            semantic_match = bool(shared_semantic_clusters)
            score = 0.0
            matched_phrases: list[str] = []
            if query_text and query_text.casefold() in primary:
                score += 1.7
                matched_phrases.append(query_text)
            for phrase in candidate_queries:
                phrase_key = phrase.casefold()
                if phrase_key and phrase_key != query_text.casefold() and phrase_key in primary:
                    score += 1.25
                    matched_phrases.append(phrase)
            for token in query_tokens:
                if token in primary:
                    score += 0.38 if len(token) <= 2 else 0.62
                elif token in secondary:
                    score += 0.2
            for phrase in candidate_queries:
                _phrase_clusters, blocked_phrase_aliases = _semantic_features(phrase)
                for token in self._tokens(phrase):
                    if any(alias in token for alias in blocked_phrase_aliases):
                        continue
                    if token in primary:
                        score += 0.28 if len(token) <= 2 else 0.48
                    elif token in secondary:
                        score += 0.14
            for token in context_tokens:
                if token in primary:
                    score += 0.1
            if not query_tokens and not query_text:
                score += 0.12
            # Local semantic equivalence is intentionally weaker than an
            # explicit lexical hit. It makes “高兴” find “开心” and “安慰”
            # find “抱抱”, while leaving unrelated assets below the floor.
            semantic_bonus = min(0.7, 0.4 * len(shared_semantic_clusters))
            if semantic_match:
                matched_phrases.append(
                    "语义相近：" + "、".join(sorted(shared_semantic_clusters))
                )
            embedding_score = 0.0
            candidate_vector = embeddings_by_id.get(item["id"])
            if embedding_vector and candidate_vector and len(candidate_vector) == len(embedding_vector):
                embedding_score = max(
                    -1.0,
                    min(1.0, sum(left * right for left, right in zip(embedding_vector, candidate_vector))),
                )
            embedding_bonus = (
                embedding_factor * max(0.0, (embedding_score - embedding_threshold) / max(0.01, 1.0 - embedding_threshold))
                if embedding_score >= embedding_threshold
                else 0.0
            )
            relevance_score = score + semantic_bonus + embedding_bonus
            if embedding_bonus > 0.0 and not matched_phrases:
                matched_phrases.append("语义相近")
            diversity_penalty = min(item["usage_count"], 20) * 0.004
            last_used_at = _safe_float(item.get("last_used_at"), 0.0, 0.0)
            if last_used_at > 0:
                age_seconds = max(0.0, now - last_used_at)
                if age_seconds < 6 * 3600:
                    diversity_penalty += 1.1 * (1.0 - age_seconds / (6 * 3600))
                elif age_seconds < 24 * 3600:
                    diversity_penalty += 0.18 * (
                        1.0 - (age_seconds - 6 * 3600) / (18 * 3600)
                    )
            learned_bias = preference_bias(item)
            ranked.append(
                (
                    relevance_score - diversity_penalty + learned_bias,
                    relevance_score,
                    item,
                    path,
                    matched_phrases,
                    learned_bias,
                    embedding_score,
                )
            )
        if not ranked:
            return None
        best_relevance = max(row[1] for row in ranked)
        relevance_floor = best_relevance - 0.65
        if query_tokens:
            relevance_floor = max(0.22, relevance_floor)
        eligible = [row for row in ranked if row[1] >= relevance_floor]
        if not eligible:
            return None
        eligible.sort(key=lambda row: (row[0], row[2]["updated_at"]), reverse=True)
        _selection_score, score, item, path, matched_phrases, learned_bias, embedding_score = eligible[0]
        semantic_match = any(
            phrase.startswith("语义相近：") for phrase in matched_phrases
        )
        # A weak lexical match is not enough to force an image into the conversation.
        if query_tokens and score < 0.22 and not semantic_match and embedding_score < embedding_threshold:
            return None
        confidence = max(0.22, min(0.99, 0.35 + score / 2.8))
        return {
            "success": True,
            "status": "success",
            "found": True,
            "image_id": f"pc-local:{item['id']}",
            "asset_id": item["id"],
            "path": str(path),
            "tags": [*item["tags"], *item["emotions"], *item["intents"]][:20],
            "need": query_text,
            "matched_queries": matched_phrases,
            "candidate_queries": candidate_queries,
            "reason": (
                "本插件素材库按关键词及本地语义近似匹配"
                if semantic_match and not embedding_score >= embedding_threshold
                else "本插件素材库按候选检索表达、标签、情绪和沟通用途匹配"
                if matched_phrases
                else "本插件素材库按标签、情绪和沟通用途匹配"
            ),
            "confidence": round(confidence, 3),
            "preference_bias": round(learned_bias, 3),
            "embedding_score": round(embedding_score, 4) if embedding_score else 0.0,
            "match_basis": (
                "embedding"
                if embedding_score >= embedding_threshold and not semantic_match and score < 0.28
                else "hybrid"
                if embedding_score >= embedding_threshold
                else "keyword_semantic"
                if semantic_match
                else "keyword"
            ),
            "provider": "private_companion_library",
        }

    def mark_used(self, item_id: Any) -> bool:
        item_key = _single_line(item_id, 64)
        if item_key.startswith("pc-local:"):
            item_key = item_key.split(":", 1)[1]
        if not item_key:
            return False
        with self._lock:
            catalog = self._load()
            changed = False
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] != item_key:
                    continue
                item["usage_count"] += 1
                item["last_used_at"] = time.time()
                catalog["items"][index] = item
                changed = True
                break
            if changed:
                self._save(catalog, lookup_changed=False)
                self._selection_revision += 1
            return changed

    def rescan(self) -> dict[str, Any]:
        with self._lock:
            catalog = self._load()
            indexed = {_safe_filename(item.get("stored_name")) for item in catalog["items"] if isinstance(item, dict)}
            hashes = {
                _single_line(item.get("sha256"), 64).lower()
                for item in catalog["items"]
                if isinstance(item, dict)
            }
            imported: list[dict[str, Any]] = []
            duplicates: list[str] = []
            rejected: list[dict[str, str]] = []
            scanned = 0
            for path in self.images_dir.iterdir():
                if not path.is_file() or path.name in indexed or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                scanned += 1
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if not data or len(data) > MAX_SINGLE_FILE_BYTES or not _image_signature_matches(data, path.suffix.lower()):
                    rejected.append({"name": path.name, "reason": "图片格式无效或超过 20 MB"})
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if digest in hashes:
                    duplicates.append(path.name)
                    rejected.append({"name": path.name, "reason": "内容已存在于索引"})
                    continue
                now = time.time()
                width, height = self._dimensions(data)
                item = self._normalize_item(
                    {
                        "id": uuid.uuid4().hex,
                        "filename": path.name,
                        "stored_name": path.name,
                        "sha256": digest,
                        "name": path.stem,
                        "tags": _text_list(re.sub(r"[_\-.]+", " ", path.stem), limit=8),
                        "scopes": ["private", "group"],
                        "enabled": True,
                        "source": "rescan",
                        "size": len(data),
                        "width": width,
                        "height": height,
                        "analysis_status": "pending",
                        "manual_fields": [],
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                catalog["items"].append(item)
                imported.append(item)
                hashes.add(digest)
            if imported:
                self._save(catalog)
        return {
            "scanned": scanned,
            "imported": len(imported),
            "duplicates": duplicates,
            "rejected": rejected,
            "items": imported,
            "analysis_queued": sum(1 for item in imported if item["analysis_status"] == "pending"),
            "summary": self.summary(),
        }


def get_reaction_asset_library(plugin: Any) -> ReactionAssetLibrary | None:
    data_dir = str(getattr(plugin, "data_dir", "") or "").strip()
    if not data_dir:
        return None
    current = getattr(plugin, "_reaction_asset_library_instance", None)
    if isinstance(current, ReactionAssetLibrary):
        return current
    legacy = getattr(plugin, "_reaction_asset_library", None)
    if isinstance(legacy, ReactionAssetLibrary):
        setattr(plugin, "_reaction_asset_library_instance", legacy)
        return legacy
    library = ReactionAssetLibrary(data_dir)
    setattr(plugin, "_reaction_asset_library_instance", library)
    return library
