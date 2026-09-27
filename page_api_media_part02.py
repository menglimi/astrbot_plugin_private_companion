# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_media.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 417 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaMixin）。
"""
from __future__ import annotations

from .page_api_media_shared import logger
from .page_api_media_shared import Any
from .page_api_media_shared import MAX_ASSET_BYTES
from .page_api_media_shared import Path
from .page_api_media_shared import _path_text
from .page_api_media_shared import _safe_int
from .page_api_media_shared import deepcopy
from .page_api_media_shared import mimetypes
from .page_api_media_shared import quote
from .page_api_media_shared import re
from .page_api_media_shared import request
from .page_api_media_shared import send_file



class PrivateCompanionPageApiMediaPart02Mixin:
    """PrivateCompanionPageApiMediaPart02Mixin（从 PrivateCompanionPageApiMediaMixin 拆出）。"""


    async def list_owned_reaction_assets(self) -> dict[str, Any]:
        try:
            catalog = self._owned_reaction_asset_catalog()
            projection = catalog.public_projection(self._owned_reaction_asset_entries())
            return self._ok(
                {
                    "enabled": bool(
                        getattr(
                            self.plugin,
                            "enable_owned_reaction_asset_workbench",
                            False,
                        )
                    ),
                    **projection,
                }
            )
        except Exception:
            logger.error("owned reaction asset status failed")
            return self._exception_error("无法读取自有反应图素材状态")

    async def get_owned_reaction_asset_image_data(self) -> dict[str, Any]:
        asset_id = self._single_line(request.args.get("id"), 80)
        if not asset_id:
            return self._error("缺少素材 id")
        try:
            asset = self._owned_reaction_asset_catalog().resolve(
                self._owned_reaction_asset_entries(),
                asset_id,
            )
            if asset is None:
                return self._error("素材不存在、未登记或校验失败")
            mime = mimetypes.guess_type(str(asset.path))[0] or ""
            if not mime.startswith("image/"):
                return self._error("素材文件类型不受支持")
            return self._ok(
                await self._encode_image_cache_file_data_url(
                    asset.path,
                    mime,
                    max_bytes=MAX_ASSET_BYTES,
                )
            )
        except Exception:
            logger.error("owned reaction asset preview failed")
            return self._exception_error("无法读取自有反应图预览")

    async def get_image_cache_preview(self) -> Any:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            _key, path = resolved
            response = await send_file(str(path))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            logger.error(f"获取图片缓存预览失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def get_image_cache_preview_data(self) -> dict[str, Any]:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            _key, path = resolved
            return self._ok(await self._encode_image_cache_file_data_url(path))
        except Exception as exc:
            logger.error(f"获取图片缓存预览数据失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def get_image_cache_thumbnail_data(self) -> dict[str, Any]:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            key, source = resolved
            thumbnail = await self._get_or_create_image_cache_thumbnail(key, source)
            if thumbnail is not None:
                return self._ok(await self._encode_image_cache_file_data_url(thumbnail, "image/webp"))
            return self._ok(await self._encode_image_cache_file_data_url(source))
        except Exception as exc:
            logger.error(f"获取图片缓存缩略图失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def delete_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        try:
            preview_path = ""
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or key not in cache:
                    return self._error("缓存条目不存在")
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    preview_path = self._single_line(removed.get("preview_path"), 260)
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                remaining = len(cache)
            self._remove_image_cache_preview_file(preview_path, key)
            return self._ok({"key": key, "remaining": remaining})
        except Exception as exc:
            logger.error(f"删除图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def bulk_delete_image_cache_items(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            return self._error("keys 必须是缓存 key 数组")
        if payload.get("confirm") is not True:
            return self._error("批量删除需要 confirm=true")
        keys: list[str] = []
        for value in raw_keys[:500]:
            key = self._single_line(value, 120)
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return self._error("没有选择缓存条目")
        try:
            removed_items: list[tuple[str, str]] = []
            missing_keys: list[str] = []
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict):
                    return self._error("图片缓存不存在")
                for key in keys:
                    if key not in cache:
                        missing_keys.append(key)
                        continue
                    removed = cache.pop(key, None)
                    removed_items.append(
                        (
                            key,
                            self._single_line(removed.get("preview_path"), 260)
                            if isinstance(removed, dict)
                            else "",
                        )
                    )
                if removed_items:
                    self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                remaining = len(cache)
            for key, preview_path in removed_items:
                self._remove_image_cache_preview_file(preview_path, key)
            return self._ok(
                {
                    "removed": len(removed_items),
                    "removed_keys": [key for key, _path in removed_items],
                    "missing_keys": missing_keys,
                    "remaining": remaining,
                }
            )
        except Exception as exc:
            logger.error(f"批量删除图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _remove_image_cache_preview_file(self, preview_path: str, key: str = "") -> None:
        path: Path | None = None
        try:
            base = self._image_cache_preview_dir()
            if preview_path:
                path = Path(preview_path).resolve()
            if path is not None and path.is_file() and path.is_relative_to(base):
                path.unlink(missing_ok=True)
        except Exception:
            pass
        thumbnail = self._image_cache_thumbnail_file(key or (path.stem if path is not None else ""))
        if thumbnail is None:
            return
        try:
            if thumbnail.is_relative_to(self._image_cache_preview_dir()):
                thumbnail.unlink(missing_ok=True)
        except Exception:
            pass

    def _image_cache_item_summary(self, key: str, raw: dict[str, Any]) -> dict[str, Any]:
        text = str(raw.get("text") or "").strip()
        image_keys = [str(value).strip() for value in raw.get("image_keys", []) if str(value or "").strip()]
        image_aliases = [str(value).strip() for value in raw.get("image_aliases", []) if str(value or "").strip()]
        scope = self._single_line(raw.get("scope"), 40) or "private_image"
        created_ts = self._float(raw.get("created_ts"))
        last_hit_ts = self._float(raw.get("last_hit_ts"))
        edited_ts = self._float(raw.get("edited_ts"))
        preview_file = self._image_cache_preview_file(key, raw)
        preview_exists = preview_file is not None
        return {
            "key": self._single_line(key, 120),
            "text": self._single_line(text, 900 if scope == "forward_image" else 600),
            "provider_id": self._single_line(raw.get("provider_id"), 160),
            "scope": scope,
            "prompt_sig": self._single_line(raw.get("prompt_sig"), 32),
            "image_keys": image_keys[:8],
            "image_aliases": image_aliases[:12],
            "image_keys_text": " ".join(image_keys[:8]),
            "image_aliases_text": " ".join(image_aliases[:12]),
            "image_count": _safe_int(raw.get("image_count"), len(image_keys), 0),
            "preview_url": f"{self._page_asset_prefix()}/image_cache/preview?key={quote(key, safe='')}" if preview_exists else "",
            "preview_endpoint": f"/image_cache/preview_data?key={quote(key, safe='')}" if preview_exists else "",
            "thumbnail_endpoint": f"/image_cache/thumbnail_data?key={quote(key, safe='')}" if preview_exists else "",
            "preview_size": _safe_int(raw.get("preview_size"), 0, 0),
            "preview_width": _safe_int(raw.get("preview_width"), 0, 0),
            "preview_height": _safe_int(raw.get("preview_height"), 0, 0),
            "hits": _safe_int(raw.get("hits"), 0, 0),
            "created_ts": created_ts,
            "last_hit_ts": last_hit_ts,
            "edited_ts": edited_ts,
            "created": self.plugin._format_timestamp_elapsed(created_ts),
            "last_hit": self.plugin._format_timestamp_elapsed(last_hit_ts),
            "edited": self.plugin._format_timestamp_elapsed(edited_ts),
            "image_type": self._extract_labeled_text(text, "图片类型", 40),
            "visible": self._extract_labeled_text(text, "可见内容", 180),
            "intent": self._extract_labeled_text(text, "图像表达意图", 180),
            "ownership": self._extract_labeled_text(text, "图像归属判断", 80),
        }

    async def get_image_extension_status(self) -> dict[str, Any]:
        """Expose the split image runtime through the companion-owned page."""
        getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            api = getter() if callable(getter) else None
        except Exception as exc:
            logger.warning(
                "生图扩展发现失败: %s",
                self._single_line(exc, 160),
                exc_info=True,
            )
            api = None
        if api is None:
            return self._ok(
                {
                    "installed": False,
                    "enabled": False,
                    "available": False,
                    "reason": "image_companion_unavailable",
                    "state": "unavailable",
                    "generation_count": 0,
                    "last_generation": {},
                    "unified_engine": {},
                    "metrics": {},
                }
            )
        status_getter = getattr(api, "status", None)
        if not callable(status_getter):
            return self._ok(
                {
                    "installed": True,
                    "enabled": False,
                    "available": False,
                    "reason": "status_api_unavailable",
                    "state": "unavailable",
                    "generation_count": 0,
                    "last_generation": {},
                    "unified_engine": {},
                    "metrics": {},
                }
            )
        try:
            value = status_getter()
        except Exception as exc:
            logger.warning(
                "获取生图扩展状态失败: %s",
                self._single_line(exc, 160),
                exc_info=True,
            )
            return self._exception_error("获取生图扩展状态失败")
        status = dict(value) if isinstance(value, dict) else {}
        status.setdefault("installed", True)
        status.setdefault("available", bool(status.get("enabled")))
        status.setdefault("state", "managed")
        image_contract = self._image_extension_contract_status(api)
        if image_contract:
            status["companion_contract"] = image_contract
            if not image_contract["available"]:
                status["available"] = False
                status["state"] = "incompatible"
                status["reason"] = image_contract["reason"] or "image_contract_incompatible"
        debug_summary = self._recent_photo_generation_debug(
            event_limit=240,
            summary_only=True,
        )
        status["photo_debug"] = debug_summary
        return self._ok(status)

    @staticmethod
    def _image_generation_result_used_reference(
        *,
        workflow_kind: str,
        image_path: str,
        image_exists: bool,
        note: Any,
    ) -> bool:
        if not image_path or not image_exists:
            return False
        if str(workflow_kind or "").strip().lower() not in {
            "selfie", "portrait", "自拍", "人像", "edit", "改图", "修图", "重绘", "p图"
        }:
            return False
        note_text = str(note or "")
        return bool(
            re.search(
                r"(?:已使用|已提交|成功提交|已带入)[^；。]{0,16}参考图|参考图[^；。]{0,16}(?:已使用|已提交|成功提交|已带入)",
                note_text,
                flags=re.I,
            )
            or "已使用本地人设参考图" in note_text
        )

    async def get_bookshelf_image(self):
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            response = await send_file(path)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            logger.error(f"读取资料柜图片失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def get_bookshelf_image_data(self) -> dict[str, Any]:
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = await self._read_file_base64(path)
            return self._ok(
                {
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                    "size": path.stat().st_size,
                    "mtime": int(path.stat().st_mtime),
                }
            )
        except Exception as exc:
            logger.error(f"读取资料柜图片数据失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def _resolve_bookshelf_image_path_from_request(self) -> Path | dict[str, str]:
        album_id = self._single_line(request.args.get("album_id"), 40)
        page_index = self._int(request.args.get("page"))
        cover_requested = str(request.args.get("cover") or "").lower() in {"1", "true", "yes"}
        if not album_id or (page_index < 1 and not cover_requested):
            return {"error": "缺少图片参数"}
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            archive_state = data.get("reading_archive_integration") if isinstance(data.get("reading_archive_integration"), dict) else {}
            deleted_ids = self._bookshelf_deleted_album_ids(archive_state)
            if album_id in deleted_ids:
                return {"error": "图片不存在"}
            shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
            target = None
            for item in shelf_items:
                if not self._is_bookshelf_archive_item(item):
                    continue
                if self._bookshelf_album_id(item, limit=40) == album_id:
                    target = item
                    break
            if target is None:
                last_album = archive_state.get("last_album") if isinstance(archive_state.get("last_album"), dict) else {}
                if self._single_line(last_album.get("id") or last_album.get("album_id"), 40) == album_id:
                    target = last_album
            pages = target.get("pages") if isinstance(target, dict) and isinstance(target.get("pages"), list) else []
            data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
            path: Path | None = None
            if cover_requested and isinstance(target, dict):
                cover_path = _path_text(target.get("cover_path"), 1000)
                if cover_path:
                    path = Path(cover_path).resolve()
            if path is None:
                page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) == page_index), None)
                if cover_requested and not isinstance(page, dict):
                    page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) > 0), None)
                if not isinstance(page, dict):
                    return {"error": "图片不存在"}
                path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                return {"error": "图片路径不在资料柜目录内"}
            if not path.exists() or not path.is_file():
                return {"error": "图片文件不存在"}
            return path
        except Exception as exc:
            logger.error(f"读取资料柜图片失败: {exc}", exc_info=True)
            return {"error": str(exc)}

    def _persona_style_reference_text(self, questionnaire: dict[str, Any]) -> str:
        if not isinstance(questionnaire, dict):
            return ""
        raw = questionnaire.get("style_reference_text")
        if raw is None:
            raw = questionnaire.get("supplement_text")
        return self._multi_line_head_tail(raw, 4000)

    def _bookshelf_image_url(
        self,
        album_id: str,
        *,
        data_root: Path,
        page_index: int = 0,
        cover: bool = False,
        path_value: Any = "",
        access_token: str = "",
    ) -> str:
        if not album_id or (page_index < 1 and not cover):
            return ""
        url = f"{self._page_asset_prefix()}/bookshelf/image?album_id={quote(str(album_id), safe='')}"
        if cover:
            url += "&cover=1"
        elif page_index > 0:
            url += f"&page={page_index}"
        if access_token:
            url += f"&access_token={quote(str(access_token), safe='')}"
        raw_path = self._single_line(path_value, 500)
        if raw_path:
            try:
                path = Path(raw_path).resolve()
                path.relative_to(data_root)
                if path.exists() and path.is_file():
                    stat = path.stat()
                    url += f"&v={int(stat.st_mtime)}-{stat.st_size}"
            except Exception:
                pass
        return url
