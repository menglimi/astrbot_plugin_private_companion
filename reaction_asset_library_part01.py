# -*- coding: utf-8 -*-
"""ReactionAssetLibraryPart01Mixin。

由 tools/split_mixin_domain.py 从 reaction_asset_library.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 444 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ReactionAssetLibrary）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import uuid
from .helpers import _safe_float, _safe_int, _single_line
from .reaction_asset_library_shared import (
    ANALYSIS_STATUSES,
    CATALOG_VERSION,
    MAX_EMBEDDING_DIMENSION,
    _safe_bool,
    _safe_filename,
    _text_list,
)
from pathlib import Path
from typing import Any



class ReactionAssetLibraryPart01Mixin:
    """ReactionAssetLibraryPart01Mixin（从 ReactionAssetLibrary 拆出）。"""


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
            self._cached_catalog = None
            self._cached_catalog_stamp = None
            self._cached_summary = None
            self._cached_has_enabled_assets = False
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
        usage = self._usage.load()
        for item in migrated_items:
            record = usage.get(str(item.get("id") or ""))
            if record:
                item["usage_count"] = record["usage_count"]
                item["last_used_at"] = record["last_used_at"]
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
            self._lookup_index.source_stamp = None
            self._lookup_index.revision = ""
            self._lookup_index.has_enabled_assets = False
            self._lookup_index.checked_at = 0.0
        else:
            current_source_stamp = self._lookup_source_stamp()
            can_preserve_lookup = bool(
                self._lookup_index.revision
                and previous_source_stamp == self._lookup_index.source_stamp
                and previous_source_stamp is not None
                and previous_source_stamp[2:] == current_source_stamp[2:]
            )
            if not can_preserve_lookup:
                self._lookup_index.source_stamp = None
                self._lookup_index.revision = ""
                self._lookup_index.has_enabled_assets = False
                self._lookup_index.checked_at = 0.0
                return
            # Usage statistics do not participate in matching. Keep the hot
            # lookup result and advance its source stamp to the catalog just
            # written so the next reply does not parse the catalog again.
            self._lookup_index.source_stamp = current_source_stamp
            self._lookup_index.checked_at = time.monotonic()

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
        """Return availability through the same TTL-bound revision probe as lookup."""
        with self._lock:
            self.lookup_revision()
            return bool(self._lookup_index.has_enabled_assets)

    def lookup_revision(self) -> str:
        """Return a stable revision for fields that affect runtime matching.

        Usage counters are intentionally excluded from this catalog revision.
        ``selection_revision`` layers an in-process usage generation over it,
        while edits to matching fields or backing files change this base value.
        """
        with self._lock:
            now = time.monotonic()
            if self._lookup_index.hot(now):
                return self._lookup_index.revision

            stamp = self._lookup_source_stamp()
            previous_revision = self._lookup_index.revision
            if stamp != self._lookup_index.source_stamp:
                self._cached_summary = None
            items = [self._normalize_item(raw) for raw in self._load()["items"]]
            revision, has_enabled_assets = self._lookup_index.rebuild(
                items,
                path_for=self._path_for,
                source_stamp=stamp,
                now=now,
            )
            if previous_revision and revision != previous_revision:
                self._cached_summary = None
            self._cached_has_enabled_assets = has_enabled_assets
            return revision

    def selection_revision(self) -> str:
        """Include in-process usage changes in reaction selection cache keys."""
        with self._lock:
            return f"{self.lookup_revision()}:usage-{self._selection_revision}"

    def summary(self) -> dict[str, Any]:
        with self._lock:
            self.lookup_revision()
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
                    result = ReactionAssetLibraryPart01Mixin._coerce_embedding_vector(value.get(key))
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
                return ReactionAssetLibraryPart01Mixin._coerce_embedding_vector(value[0]) if value else []
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
