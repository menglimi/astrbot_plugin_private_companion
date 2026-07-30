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


CATALOG_VERSION = 1
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 120 * 1024 * 1024
MAX_ZIP_MEMBERS = 1000
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


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
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def _empty_catalog(self) -> dict[str, Any]:
        return {"version": CATALOG_VERSION, "updated_at": 0.0, "items": []}

    def _load(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return self._empty_catalog()
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return self._empty_catalog()
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            return self._empty_catalog()
        raw["version"] = CATALOG_VERSION
        raw["items"] = [
            item for item in raw["items"] if isinstance(item, dict) and item.get("id")
        ]
        return raw

    def _save(self, catalog: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        catalog["version"] = CATALOG_VERSION
        catalog["updated_at"] = time.time()
        temporary = self.catalog_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.catalog_path)

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        scopes = [scope for scope in _text_list(item.get("scopes"), limit=2) if scope in {"private", "group"}]
        return {
            "id": _single_line(item.get("id"), 64),
            "filename": _safe_filename(item.get("filename")),
            "stored_name": _safe_filename(item.get("stored_name")),
            "sha256": _single_line(item.get("sha256"), 64).lower(),
            "name": _single_line(item.get("name"), 100),
            "tags": _text_list(item.get("tags"), limit=20),
            "emotions": _text_list(item.get("emotions"), limit=12),
            "intents": _text_list(item.get("intents"), limit=12),
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
        with self._lock:
            for raw in self._load()["items"]:
                item = self._normalize_item(raw)
                path = self._path_for(item)
                if item["enabled"] and path is not None and path.is_file():
                    return True
        return False

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = [self._normalize_item(item) for item in self._load()["items"]]
        available = []
        for item in items:
            path = self._path_for(item)
            if path is not None and path.is_file():
                available.append(item)
        return {
            "total": len(items),
            "enabled": sum(1 for item in available if item["enabled"]),
            "disabled": sum(1 for item in items if not item["enabled"]),
            "missing": len(items) - len(available),
            "private": sum(1 for item in available if item["enabled"] and "private" in item["scopes"]),
            "group": sum(1 for item in available if item["enabled"] and "group" in item["scopes"]),
            "usage_count": sum(item["usage_count"] for item in items),
        }

    def list_items(
        self,
        *,
        query: Any = "",
        status: Any = "all",
        scope: Any = "all",
        page: int = 1,
        page_size: int = 48,
    ) -> dict[str, Any]:
        query_text = _single_line(query, 160).casefold()
        status_text = _single_line(status, 20).lower() or "all"
        scope_text = _single_line(scope, 20).lower() or "all"
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
            haystack = " ".join(
                [item["name"], item["filename"], *item["tags"], *item["emotions"], *item["intents"]]
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
        return {
            "tags": _text_list(metadata.get("tags"), limit=20),
            "emotions": _text_list(metadata.get("emotions"), limit=12),
            "intents": _text_list(metadata.get("intents"), limit=12),
            "scopes": [scope for scope in _text_list(metadata.get("scopes"), limit=2) if scope in {"private", "group"}] or ["private", "group"],
            "enabled": _safe_bool(metadata.get("enabled", True), True),
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
                        "tags": [*defaults["tags"], *filename_tags],
                        "emotions": defaults["emotions"],
                        "intents": defaults["intents"],
                        "scopes": defaults["scopes"],
                        "enabled": defaults["enabled"],
                        "source": source,
                        "size": len(data),
                        "width": width,
                        "height": height,
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
                for key, limit in (("tags", 20), ("emotions", 12), ("intents", 12)):
                    if key in changes:
                        item[key] = _text_list(changes.get(key), limit=limit)
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
        with self._lock:
            catalog = self._load()
            kept: list[dict[str, Any]] = []
            for raw in catalog["items"]:
                item = self._normalize_item(raw)
                if item["id"] not in item_ids:
                    kept.append(item)
                    continue
                path = self._path_for(item)
                if path is not None:
                    path.unlink(missing_ok=True)
                removed.append(item["id"])
            if removed:
                catalog["items"] = kept
                self._save(catalog)
        return {"deleted": len(removed), "ids": removed, "summary": self.summary()}

    @staticmethod
    def _tokens(value: str) -> list[str]:
        normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", value.casefold())
        tokens = [token for token in normalized.split() if token]
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return list(dict.fromkeys(tokens))[:80]

    def find(self, query: Any, *, context: Any = "", scope: str = "private") -> dict[str, Any] | None:
        query_text = _single_line(query, 500)
        context_text = _single_line(context, 1000)
        query_tokens = self._tokens(query_text)
        context_tokens = self._tokens(context_text)
        with self._lock:
            candidates = [self._normalize_item(raw) for raw in self._load()["items"]]
        ranked: list[tuple[float, dict[str, Any], Path]] = []
        for item in candidates:
            path = self._path_for(item)
            if not item["enabled"] or scope not in item["scopes"] or path is None or not path.is_file():
                continue
            primary = " ".join([item["name"], *item["tags"], *item["emotions"], *item["intents"]]).casefold()
            secondary = item["filename"].casefold()
            score = 0.0
            matched: list[str] = []
            if query_text and query_text.casefold() in primary:
                score += 1.4
            for token in query_tokens:
                if token in primary:
                    score += 0.32 if len(token) <= 2 else 0.52
                    matched.append(token)
                elif token in secondary:
                    score += 0.16
            for token in context_tokens:
                if token in primary:
                    score += 0.08
            if not query_tokens and not query_text:
                score += 0.1
            score += min(item["usage_count"], 20) * 0.002
            ranked.append((score, item, path))
        if not ranked:
            return None
        ranked.sort(key=lambda row: (row[0], row[1]["updated_at"]), reverse=True)
        score, item, path = ranked[0]
        # A weak lexical match is not enough to force an image into the conversation.
        if query_tokens and score < 0.28:
            return None
        confidence = max(0.18, min(0.98, 0.3 + score / 3.2))
        return {
            "success": True,
            "status": "success",
            "found": True,
            "image_id": f"pc-local:{item['id']}",
            "asset_id": item["id"],
            "path": str(path),
            "tags": [*item["tags"], *item["emotions"], *item["intents"]][:20],
            "need": query_text,
            "reason": "本插件素材库按标签、情绪和沟通用途匹配",
            "confidence": round(confidence, 3),
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
                self._save(catalog)
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
            "duplicates": [],
            "rejected": rejected,
            "items": imported,
            "summary": self.summary(),
        }


def get_reaction_asset_library(plugin: Any) -> ReactionAssetLibrary | None:
    data_dir = str(getattr(plugin, "data_dir", "") or "").strip()
    if not data_dir:
        return None
    current = getattr(plugin, "_reaction_asset_library", None)
    if isinstance(current, ReactionAssetLibrary):
        return current
    library = ReactionAssetLibrary(data_dir)
    setattr(plugin, "_reaction_asset_library", library)
    return library
