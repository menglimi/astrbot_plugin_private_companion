# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaReferencePart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_reference.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 351 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaReferenceMixin）。
"""
from __future__ import annotations

from .page_api_media_reference_shared import (
    PHOTO_REFERENCE_ASSET_MAX_BYTES,
    PHOTO_REFERENCE_ASSET_MAX_COUNT,
    PHOTO_REFERENCE_ASSET_MAX_PER_OWNER,
    PHOTO_REFERENCE_ASSET_MIMES,
    PHOTO_REFERENCE_ASSET_SCOPES,
    logger,
)
from .page_api_media_reference_shared import Any
from .page_api_media_reference_shared import Path
from .page_api_media_reference_shared import asynccontextmanager
from .page_api_media_reference_shared import base64
from .page_api_media_reference_shared import mimetypes
from .page_api_media_reference_shared import quote
from .page_api_media_reference_shared import re
from .page_api_media_reference_shared import request
from .page_api_media_reference_shared import time
from .page_api_media_reference_shared import uuid



class PrivateCompanionPageApiMediaReferencePart04Mixin:
    """PrivateCompanionPageApiMediaReferencePart04Mixin（从 PrivateCompanionPageApiMediaReferenceMixin 拆出）。"""


    def _normalize_photo_reference_asset(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        asset_id = self._single_line(value.get("id"), 80)
        scope = self._normalize_photo_reference_asset_scope(value.get("scope"))
        owner_id = self._single_line(value.get("owner_id"), 180)
        path = self._single_line(value.get("path") or value.get("source"), 800)
        if not asset_id or scope not in PHOTO_REFERENCE_ASSET_SCOPES or not owner_id or not path:
            return None
        tags = self._dedupe_text_list(value.get("tags"), limit=24)
        try:
            size = max(0, int(value.get("size") or 0))
        except (TypeError, ValueError, OverflowError):
            size = 0
        try:
            created_at = float(value.get("created_at") or 0.0)
        except (TypeError, ValueError, OverflowError):
            created_at = 0.0
        try:
            updated_at = float(value.get("updated_at") or created_at or 0.0)
        except (TypeError, ValueError, OverflowError):
            updated_at = created_at
        mime = self._single_line(value.get("mime"), 80).lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            mime = mimetypes.guess_type(path)[0] or ""
        return {
            "id": asset_id,
            "scope": scope,
            "owner_id": owner_id,
            "title": self._single_line(value.get("title") or value.get("name"), 160),
            "note": self._multi_line(value.get("note") or value.get("description"), 1200),
            "tags": tags,
            "path": path,
            "mime": mime,
            "filename": self._single_line(value.get("filename") or Path(path).name, 180),
            "size": size,
            "enabled": self._photo_reference_asset_bool(value.get("enabled"), True),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _photo_reference_asset_items(self) -> list[dict[str, Any]]:
        """Return normalized assets, dropping malformed persisted entries from views."""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in self._photo_reference_asset_raw_items():
            item = self._normalize_photo_reference_asset(raw)
            if item is None or item["id"] in seen:
                continue
            seen.add(item["id"])
            result.append(item)
        return result

    def _photo_reference_asset_page_item(self, item: dict[str, Any]) -> dict[str, Any]:
        path = self._photo_reference_asset_path(item.get("path"))
        available = bool(path is not None and path.is_file())
        file_size = int(item.get("size") or 0)
        if path is not None and available:
            try:
                file_size = max(0, int(path.stat().st_size))
            except OSError:
                pass
        mime = str(item.get("mime") or "").strip().lower() or (mimetypes.guess_type(str(path or ""))[0] or "")
        public = dict(item)
        public.update({
            "kind": item.get("scope", ""),
            "source": item.get("path", ""),
            "available": available,
            "file_size": file_size,
            "preview_endpoint": (
                f"/photo_reference/assets/image_data?id={quote(str(item.get('id') or ''), safe='')}"
                if available else ""
            ),
        })
        public["mime"] = mime
        return public

    def _photo_reference_asset_page_items(
        self,
        *,
        scope: str = "",
        owner_id: str = "",
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_scope = self._normalize_photo_reference_asset_scope(scope) if scope else ""
        owner_filter = self._single_line(owner_id, 180) if owner_id else ""
        items = []
        for item in self._photo_reference_asset_items():
            if normalized_scope and item["scope"] != normalized_scope:
                continue
            if owner_filter and item["owner_id"] != owner_filter:
                continue
            if not include_disabled and not item["enabled"]:
                continue
            items.append(self._photo_reference_asset_page_item(item))
        items.sort(key=lambda value: (float(value.get("updated_at") or 0.0), str(value.get("id") or "")), reverse=True)
        return items

    @asynccontextmanager
    async def _photo_reference_asset_lock(self):
        lock = getattr(self.plugin, "_data_lock", None)
        if lock is None or not callable(getattr(lock, "__aenter__", None)):
            yield
            return
        async with lock:
            yield

    @staticmethod
    def _decode_photo_reference_asset_data_url(value: Any) -> tuple[bytes, str, str] | None:
        text = str(value or "").strip()
        if not text.lower().startswith("data:") or "," not in text:
            return None
        meta, payload = text.split(",", 1)
        parts = meta[5:].split(";")
        mime = str(parts[0] or "").strip().lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        if mime not in PHOTO_REFERENCE_ASSET_MIMES or not any(part.strip().lower() == "base64" for part in parts[1:]):
            return None
        payload = re.sub(r"\s+", "", payload)
        if not payload or len(payload) > ((PHOTO_REFERENCE_ASSET_MAX_BYTES * 4) // 3 + 4096):
            return None
        try:
            raw = base64.b64decode(payload, validate=True)
        except (ValueError, TypeError, base64.binascii.Error):
            return None
        if not raw or len(raw) > PHOTO_REFERENCE_ASSET_MAX_BYTES:
            return None
        signature_ok = (
            (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
            or (mime == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
            or (mime == "image/gif" and raw[:6] in {b"GIF87a", b"GIF89a"})
        )
        if not signature_ok:
            return None
        return raw, mime, PHOTO_REFERENCE_ASSET_MIMES[mime]

    def _photo_reference_asset_metadata_from_payload(
        self,
        payload: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> tuple[str, str, str, str, list[str], bool] | None:
        current = existing or {}
        scope = self._normalize_photo_reference_asset_scope(payload.get("scope", current.get("scope")))
        owner_id = self._single_line(payload.get("owner_id", current.get("owner_id")), 180)
        if scope not in PHOTO_REFERENCE_ASSET_SCOPES or not owner_id:
            return None
        title = self._single_line(payload.get("title", current.get("title")), 160)
        note = self._multi_line(payload.get("note", current.get("note")), 1200)
        tags = self._dedupe_text_list(payload.get("tags", current.get("tags")), limit=24)
        enabled = self._photo_reference_asset_bool(payload.get("enabled"), bool(current.get("enabled", True)))
        return scope, owner_id, title, note, tags, enabled

    async def list_photo_reference_assets(self) -> dict[str, Any]:
        try:
            scope_raw = self._single_line(request.args.get("scope"), 40)
            scope = self._normalize_photo_reference_asset_scope(scope_raw) if scope_raw else ""
            if scope_raw and not scope:
                return self._error("scope 只支持 relation_user、group 或 knowledge")
            owner_id = self._single_line(request.args.get("owner_id"), 180)
            include_disabled = self._photo_reference_asset_bool(request.args.get("include_disabled"), True)
            items = self._photo_reference_asset_page_items(
                scope=scope,
                owner_id=owner_id,
                include_disabled=include_disabled,
            )
            return self._ok({
                "items": items,
                "assets": items,
                "total": len(items),
                "available": sum(1 for item in items if item.get("available")),
                "limit": PHOTO_REFERENCE_ASSET_MAX_COUNT,
                "per_owner_limit": PHOTO_REFERENCE_ASSET_MAX_PER_OWNER,
                "scopes": sorted(PHOTO_REFERENCE_ASSET_SCOPES),
            })
        except Exception as exc:
            logger.error("获取参考资产列表失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def get_photo_reference_asset_image_data(self) -> dict[str, Any]:
        asset_id = self._single_line(request.args.get("id") or request.args.get("asset_id"), 80)
        if not asset_id:
            return self._error("缺少参考资产 id")
        try:
            item = next((candidate for candidate in self._photo_reference_asset_items() if candidate.get("id") == asset_id), None)
            if item is None:
                return self._error("参考资产不存在")
            path = self._photo_reference_asset_path(item.get("path"))
            if path is None or not path.is_file():
                return self._error("参考资产文件不存在")
            try:
                file_size = path.stat().st_size
            except OSError:
                return self._error("无法读取参考资产文件大小")
            if file_size > PHOTO_REFERENCE_ASSET_MAX_BYTES:
                return self._error(f"参考资产文件过大（{file_size} bytes）")
            mime = str(item.get("mime") or "").strip().lower() or (mimetypes.guess_type(str(path))[0] or "")
            if mime == "image/jpg":
                mime = "image/jpeg"
            if mime not in PHOTO_REFERENCE_ASSET_MIMES:
                return self._error("参考资产文件类型不受支持")
            return self._ok(await self._encode_image_cache_file_data_url(path, mime, max_bytes=PHOTO_REFERENCE_ASSET_MAX_BYTES))
        except Exception as exc:
            logger.error("获取参考资产预览失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def upload_photo_reference_asset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        decoded = self._decode_photo_reference_asset_data_url(
            payload.get("data_url") or payload.get("image_data") or payload.get("image")
        )
        if decoded is None:
            return self._error("只支持有效的 PNG/JPEG/WebP/GIF data URL")
        metadata = self._photo_reference_asset_metadata_from_payload(payload)
        if metadata is None:
            return self._error("scope 必须是 relation_user、group 或 knowledge，且 owner_id 不能为空")
        raw, mime, suffix = decoded
        scope, owner_id, title, note, tags, enabled = metadata
        async with self._photo_reference_asset_lock():
            items = self._photo_reference_asset_items()
            if len(items) >= PHOTO_REFERENCE_ASSET_MAX_COUNT:
                return self._error(f"参考资产数量已达到上限 {PHOTO_REFERENCE_ASSET_MAX_COUNT}")
            owner_count = sum(1 for item in items if item["scope"] == scope and item["owner_id"] == owner_id)
            if owner_count >= PHOTO_REFERENCE_ASSET_MAX_PER_OWNER:
                return self._error(f"该归属对象的参考资产数量已达到上限 {PHOTO_REFERENCE_ASSET_MAX_PER_OWNER}")
            asset_id = f"asset_{uuid.uuid4().hex}"
            target = self._photo_reference_asset_dir() / f"{asset_id}{suffix}"
            try:
                target.write_bytes(raw)
            except OSError as exc:
                return self._error(f"保存参考资产失败: {exc}")
            now = time.time()
            item = {
                "id": asset_id,
                "scope": scope,
                "owner_id": owner_id,
                "title": title,
                "note": note,
                "tags": tags,
                "path": str(target.relative_to(self._photo_reference_asset_dir().parent)),
                "mime": mime,
                "filename": f"{asset_id}{suffix}",
                "size": len(raw),
                "enabled": enabled,
                "created_at": now,
                "updated_at": now,
            }
            self._photo_reference_asset_raw_items().append(item)
            saver = getattr(self.plugin, "_save_data_sync", None)
            if callable(saver):
                saver(sections={"photo_reference_assets"})
            return self._ok({"asset": self._photo_reference_asset_page_item(item)})

    async def update_photo_reference_asset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        asset_id = self._single_line(payload.get("id") or payload.get("asset_id"), 80)
        if not asset_id:
            return self._error("缺少参考资产 id")
        decoded = None
        image_value = payload.get("data_url") or payload.get("image_data") or payload.get("image")
        if image_value:
            decoded = self._decode_photo_reference_asset_data_url(image_value)
            if decoded is None:
                return self._error("只支持有效的 PNG/JPEG/WebP/GIF data URL")
        async with self._photo_reference_asset_lock():
            raw_items = self._photo_reference_asset_raw_items()
            index = next((idx for idx, raw in enumerate(raw_items) if isinstance(raw, dict) and str(raw.get("id") or "") == asset_id), -1)
            if index < 0:
                return self._error("参考资产不存在")
            existing = self._normalize_photo_reference_asset(raw_items[index])
            if existing is None:
                return self._error("参考资产记录无效")
            metadata = self._photo_reference_asset_metadata_from_payload(payload, existing=existing)
            if metadata is None:
                return self._error("scope 必须是 relation_user、group 或 knowledge，且 owner_id 不能为空")
            scope, owner_id, title, note, tags, enabled = metadata
            if (scope, owner_id) != (existing["scope"], existing["owner_id"]):
                owner_count = sum(
                    1 for item in self._photo_reference_asset_items()
                    if item["id"] != asset_id and item["scope"] == scope and item["owner_id"] == owner_id
                )
                if owner_count >= PHOTO_REFERENCE_ASSET_MAX_PER_OWNER:
                    return self._error(f"该归属对象的参考资产数量已达到上限 {PHOTO_REFERENCE_ASSET_MAX_PER_OWNER}")
            replacement_path = existing["path"]
            replacement_target: Path | None = None
            if decoded is not None:
                raw, mime, suffix = decoded
                replacement_target = self._photo_reference_asset_dir() / f"{asset_id}{suffix}"
                try:
                    replacement_target.write_bytes(raw)
                except OSError as exc:
                    return self._error(f"保存参考资产失败: {exc}")
                replacement_path = str(replacement_target.relative_to(self._photo_reference_asset_dir().parent))
            else:
                raw = b""
                mime = existing.get("mime", "")
            now = time.time()
            updated = dict(existing)
            updated.update({
                "scope": scope,
                "owner_id": owner_id,
                "title": title,
                "note": note,
                "tags": tags,
                "enabled": enabled,
                "path": replacement_path,
                "updated_at": now,
            })
            if decoded is not None:
                updated.update({
                    "mime": mime,
                    "filename": f"{asset_id}{replacement_target.suffix if replacement_target else ''}",
                    "size": len(raw),
                })
            raw_items[index] = updated
            if decoded is not None and existing.get("path") != replacement_path:
                old_path = self._photo_reference_asset_path(existing.get("path"))
                if old_path is not None and old_path != replacement_target:
                    try:
                        old_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            saver = getattr(self.plugin, "_save_data_sync", None)
            if callable(saver):
                saver(sections={"photo_reference_assets"})
            return self._ok({"asset": self._photo_reference_asset_page_item(updated)})

    async def delete_photo_reference_asset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        asset_id = self._single_line(payload.get("id") or payload.get("asset_id"), 80)
        if not asset_id:
            return self._error("缺少参考资产 id")
        async with self._photo_reference_asset_lock():
            raw_items = self._photo_reference_asset_raw_items()
            index = next((idx for idx, raw in enumerate(raw_items) if isinstance(raw, dict) and str(raw.get("id") or "") == asset_id), -1)
            if index < 0:
                return self._error("参考资产不存在")
            existing = self._normalize_photo_reference_asset(raw_items[index])
            raw_items.pop(index)
            removed_path = self._photo_reference_asset_path(existing.get("path")) if existing else None
            saver = getattr(self.plugin, "_save_data_sync", None)
            if callable(saver):
                saver(sections={"photo_reference_assets"})
            if removed_path is not None:
                try:
                    removed_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return self._ok({
                "id": asset_id,
                "remaining": len(self._photo_reference_asset_items()),
            })
