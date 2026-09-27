# -*- coding: utf-8 -*-
"""ReactionAssetLibraryPart02Mixin。

由 tools/split_mixin_domain.py 从 reaction_asset_library.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ReactionAssetLibrary）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import re
import time
import uuid
import zipfile
from .helpers import _safe_int, _single_line
from .reaction_asset_library_shared import (
    MAX_BATCH_BYTES,
    MAX_SINGLE_FILE_BYTES,
    MAX_ZIP_MEMBERS,
    MIME_BY_EXTENSION,
    SUPPORTED_EXTENSIONS,
    _image_signature_matches,
    _safe_bool,
    _safe_filename,
    _text_list,
)
from pathlib import Path
from typing import Any, Iterable



class ReactionAssetLibraryPart02Mixin:
    """ReactionAssetLibraryPart02Mixin（从 ReactionAssetLibrary 拆出）。"""


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
                self._usage.delete(set(removed))
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
