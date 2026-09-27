# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaReferencePart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_reference.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 478 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaReferenceMixin）。
"""
from __future__ import annotations

from .page_api_media_reference_shared import (
    PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS,
    PHOTO_REFERENCE_UPLOAD_MAX_COUNT,
    PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES,
    PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES,
    logger,
)
from .page_api_media_reference_shared import Any
from .page_api_media_reference_shared import CATALOG_VERSION
from .page_api_media_reference_shared import CatalogValidationError
from .page_api_media_reference_shared import PILImage
from .page_api_media_reference_shared import Path
from .page_api_media_reference_shared import PhotoReference
from .page_api_media_reference_shared import ReferenceAssetGate
from .page_api_media_reference_shared import _path_text
from .page_api_media_reference_shared import _safe_int
from .page_api_media_reference_shared import asynccontextmanager
from .page_api_media_reference_shared import asyncio
from .page_api_media_reference_shared import hashlib
from .page_api_media_reference_shared import io
from .page_api_media_reference_shared import load_catalog
from .page_api_media_reference_shared import math
from .page_api_media_reference_shared import os
from .page_api_media_reference_shared import project_reference_candidate
from .page_api_media_reference_shared import quote
from .page_api_media_reference_shared import re
from .page_api_media_reference_shared import request
from .page_api_media_reference_shared import urlparse
from .page_api_media_reference_shared import uuid



class PrivateCompanionPageApiMediaReferencePart01Mixin:
    """PrivateCompanionPageApiMediaReferencePart01Mixin（从 PrivateCompanionPageApiMediaReferenceMixin 拆出）。"""


    def _photo_reference_preset_names(self) -> tuple[str, ...]:
        preset_provider = getattr(self.plugin, "_photo_generation_scene_presets", None)
        if not callable(preset_provider):
            return ()
        try:
            presets = preset_provider()
        except Exception as exc:
            logger.warning(
                "读取生图场景预设失败，参考图目录将跳过预设关联校验: %s",
                self._single_line(exc, 160),
            )
            return ()
        return tuple(str(name) for name in presets.keys()) if isinstance(presets, dict) else ()

    def _photo_reference_metadata_review_timeout(self, provider_id: str) -> float:
        """Resolve a bounded timeout for the guided metadata approval call."""
        resolver = getattr(self.plugin, "_model_timeout_seconds_for_call", None)
        if callable(resolver):
            try:
                configured = resolver(
                    task="photo_reference_metadata_review",
                    provider_id=provider_id,
                    timeout_key="LLM_PROVIDER_ID",
                )
                if configured is not None:
                    value = float(configured)
                    if math.isfinite(value) and value >= 5.0:
                        return min(600.0, value)
            except Exception:
                pass
        return PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS

    async def _photo_reference_metadata_review_call(
        self,
        caller: Any,
        user_prompt: str,
        *,
        system_prompt: str,
        provider_id: str,
        timeout: float,
    ) -> Any:
        """Run the approval call without waiting for a non-cooperative cancellation."""
        task = self._create_page_background_task(
            caller(
                user_prompt,
                max_tokens=1400,
                provider_id=provider_id,
                task="photo_reference_metadata_review",
                system_prompt=system_prompt,
                timeout_key="LLM_PROVIDER_ID",
                strict_provider=True,
            ),
            label="photo_reference_metadata_review",
        )
        if task is None:
            raise RuntimeError("参考图模型审批任务未启动")
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task in done:
            return task.result()
        task.cancel()

        def consume_late_failure(completed: asyncio.Task[Any]) -> None:
            if completed.cancelled():
                return
            try:
                completed.exception()
            except Exception:
                pass

        task.add_done_callback(consume_late_failure)
        raise asyncio.TimeoutError

    @staticmethod
    def _photo_reference_page_id(kind: str, source: str) -> str:
        payload = f"{kind}\0{source}".encode("utf-8", errors="ignore")
        return hashlib.sha256(payload).hexdigest()[:24]

    def _photo_reference_catalog_snapshot(self, *, sync_runtime: bool = False) -> tuple[PhotoReference, ...]:
        runtime_catalog = tuple(
            reference
            for reference in (getattr(self.plugin, "photo_reference_catalog", None) or ())
            if isinstance(reference, PhotoReference) and reference.kind in {"persona", "library"}
        )
        if runtime_catalog:
            return runtime_catalog

        if self._normalize_bool_value(
            self._config_get_raw("photo_reference_catalog_user_cleared", False),
        ):
            return runtime_catalog
        persisted_catalog = self._config_get_raw("photo_reference_catalog", None)
        if persisted_catalog in (None, "", []):
            return runtime_catalog
        try:
            loaded = load_catalog(
                persisted_catalog,
                catalog_version=_safe_int(
                    self._config_get_raw("photo_reference_catalog_version", CATALOG_VERSION),
                    CATALOG_VERSION,
                    0,
                ),
                preset_names=self._photo_reference_preset_names(),
            )
        except CatalogValidationError as exc:
            logger.warning(
                "已保存的参考图目录读取失败: %s",
                self._single_line(exc, 180),
            )
            return runtime_catalog

        if loaded.references and sync_runtime:
            self.plugin.photo_reference_catalog = loaded.references
            self.plugin.photo_reference_catalog_version = CATALOG_VERSION
            self.plugin.photo_reference_catalog_read_only = loaded.read_only
            self.plugin.photo_reference_catalog_user_cleared = False
            logger.info(
                "已从保存配置恢复运行时参考图目录: %s 项",
                len(loaded.references),
            )
        return loaded.references

    def _q5_structured_reference_asset_projection(self) -> dict[str, Any]:
        enabled = bool(getattr(self.plugin, "enable_p5_structured_reference_assets", False))
        backend = self._single_line(getattr(self.plugin, "photo_generation_backend", "auto"), 30).lower()
        capacity = 4 if backend in {"auto", "comfyui"} else 0
        gate = ReferenceAssetGate(getattr(self.plugin, "data_dir", ""))
        projection = gate.public_projection(
            getattr(self.plugin, "photo_structured_reference_assets", []),
            backend_capacity=capacity,
        )
        projection["enabled"] = enabled
        projection["backend"] = backend or "auto"
        return projection

    def _photo_reference_page_items(self) -> list[dict[str, Any]]:
        catalog = self._photo_reference_catalog_snapshot(sync_runtime=True)
        if catalog:
            entries = [
                project_reference_candidate(reference)
                for reference in (catalog or ())
                if isinstance(reference, PhotoReference) and reference.kind in {"persona", "library"}
            ]
        elif _safe_int(
            self._config_get_raw(
                "photo_reference_catalog_version",
                getattr(self.plugin, "photo_reference_catalog_version", 0),
            ),
            0,
            0,
        ) < CATALOG_VERSION and not self._normalize_bool_value(
            self._config_get_raw(
                "photo_reference_catalog_user_cleared",
                getattr(self.plugin, "photo_reference_catalog_user_cleared", False),
            )
        ):
            entries: list[dict[str, Any]] = []
            persona_source = _path_text(getattr(self.plugin, "photo_persona_reference_image_path", ""), 1000)
            if persona_source:
                entries.append({
                    "id": self._photo_reference_page_id("persona", persona_source),
                    "kind": "persona",
                    "source": persona_source,
                    "note": "默认人设参考图",
                    "reference_roles": ["identity"],
                    "outfit_category": "",
                    "outfit_lock_default": False,
                    "scene_categories": [],
                    "time_categories": [],
                    "preferred_preset": "",
                    "metadata_source": "legacy",
                })
            getter = getattr(self.plugin, "_photo_reference_library_entries", None)
            try:
                parsed = getter() if callable(getter) else []
            except Exception:
                parsed = []
            for item in parsed if isinstance(parsed, list) else []:
                if not isinstance(item, dict):
                    continue
                source = _path_text(item.get("source") or item.get("path") or item.get("url"), 1000)
                if not source:
                    continue
                entries.append({
                    "id": self._photo_reference_page_id("library", source),
                    "kind": "library",
                    "source": source,
                    "note": self._single_line(item.get("note") or item.get("description"), 500),
                    "reference_roles": list(item.get("reference_roles") or ["identity"]),
                    "outfit_category": self._single_line(item.get("outfit_category"), 40),
                    "outfit_lock_default": bool(item.get("outfit_lock_default")),
                    "scene_categories": list(item.get("scene_categories") or []),
                    "time_categories": list(item.get("time_categories") or []),
                    "preferred_preset": self._single_line(item.get("preferred_preset"), 60),
                    "metadata_source": self._single_line(item.get("metadata_source"), 30) or "legacy",
                })
        else:
            entries = []

        resolver = getattr(self.plugin, "_photo_reference_local_path", None)
        result: list[dict[str, Any]] = []
        library_index = 0
        for entry in entries:
            kind = entry["kind"]
            source = entry["source"]
            remote = bool(re.match(r"^https?://", source, flags=re.I))
            inline = source.lower().startswith("data:image/")
            local_path = ""
            if not remote and not inline:
                try:
                    local_path = str(resolver(source) or "") if callable(resolver) else source
                except Exception:
                    local_path = ""
            path = Path(local_path).expanduser() if local_path else None
            available = bool(remote or inline or (path is not None and path.is_file()))
            item_id = self._single_line(entry.get("id"), 80)
            item = {
                "id": item_id,
                "kind": kind,
                "index": library_index if kind == "library" else -1,
                "source": source,
                "note": entry["note"],
                "reference_roles": list(entry.get("reference_roles") or []),
                "outfit_category": self._single_line(entry.get("outfit_category"), 40),
                "outfit_lock_default": bool(entry.get("outfit_lock_default")),
                "scene_categories": list(entry.get("scene_categories") or []),
                "time_categories": list(entry.get("time_categories") or []),
                "preferred_preset": self._single_line(entry.get("preferred_preset"), 60),
                "metadata_source": self._single_line(entry.get("metadata_source"), 30),
                "editor_intent": entry.get("editor_intent") if isinstance(entry.get("editor_intent"), dict) else None,
                "excluded_scene_categories": list(entry.get("excluded_scene_categories") or []),
                "excluded_time_categories": list(entry.get("excluded_time_categories") or []),
                "selection_eligibility": self._single_line(entry.get("selection_eligibility"), 40) or "matching_only",
                "available": available,
                "remote": remote,
                "filename": Path(urlparse(source).path).name if remote else (path.name if path else Path(source).name),
                "preview_endpoint": f"/photo_reference/image_data?id={quote(item_id, safe='')}" if available and not remote and not inline else "",
                "direct_url": source if remote or inline else "",
            }
            if path is not None and path.is_file():
                try:
                    item["file_size"] = path.stat().st_size
                except OSError:
                    item["file_size"] = 0
            else:
                item["file_size"] = 0
            result.append(item)
            if kind == "library":
                library_index += 1
        return result

    async def list_photo_references(self) -> dict[str, Any]:
        try:
            items = self._photo_reference_page_items()
            persona = next((item for item in items if item.get("kind") == "persona"), None)
            library = [item for item in items if item.get("kind") == "library"]
            preset_getter = getattr(self.plugin, "_photo_generation_scene_presets", None)
            presets = list(preset_getter().keys()) if callable(preset_getter) else []
            return self._ok({
                "enabled": bool(getattr(self.plugin, "enable_photo_reference_image", False)),
                "catalog_version": _safe_int(getattr(self.plugin, "photo_reference_catalog_version", 0), 0, 0),
                "read_only": bool(getattr(self.plugin, "photo_reference_catalog_read_only", False)),
                "limit": 24,
                "persona": persona,
                "items": library,
                "total": len(library),
                "available": sum(1 for item in library if item.get("available")),
                "structured_assets": self._q5_structured_reference_asset_projection(),
                "options": {
                    "reference_roles": [
                        {"value": "identity", "label": "身份"},
                        {"value": "outfit", "label": "服装"},
                        {"value": "pose", "label": "姿势"},
                        {"value": "scene", "label": "场景"},
                        {"value": "style", "label": "风格"},
                        {"value": "continuity", "label": "连续性"},
                        {"value": "source", "label": "原图"},
                    ],
                    "outfit_categories": [
                        {"value": "cosplay", "label": "COS"},
                        {"value": "school_uniform", "label": "校服"},
                        {"value": "sleepwear", "label": "睡衣"},
                        {"value": "swimwear", "label": "泳装"},
                        {"value": "sportswear", "label": "运动服"},
                        {"value": "formalwear", "label": "礼服/正装"},
                        {"value": "homewear", "label": "居家服"},
                        {"value": "daily_outfit", "label": "日常穿搭"},
                    ],
                    "scene_categories": [
                        {"value": "home", "label": "居家"},
                        {"value": "bedroom", "label": "卧室"},
                        {"value": "school", "label": "校园"},
                        {"value": "office", "label": "办公室"},
                        {"value": "outdoor", "label": "户外"},
                        {"value": "formal_event", "label": "正式场合"},
                        {"value": "sport", "label": "运动"},
                        {"value": "beach", "label": "海边/泳池"},
                    ],
                    "time_categories": [
                        {"value": "morning", "label": "早晨"},
                        {"value": "daytime", "label": "白天"},
                        {"value": "afternoon", "label": "下午"},
                        {"value": "evening", "label": "傍晚"},
                        {"value": "night", "label": "夜晚"},
                        {"value": "bedtime", "label": "睡前"},
                    ],
                    "role_shortcuts": [
                        {"value": ["identity"], "label": "仅身份"},
                        {"value": ["outfit"], "label": "仅服装"},
                        {"value": ["pose"], "label": "仅姿势"},
                        {"value": ["scene"], "label": "仅场景"},
                        {"value": ["style"], "label": "仅画风"},
                    ],
                    "presets": presets,
                },
            })
        except Exception as exc:
            logger.error(f"获取参考图库失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    @staticmethod
    def _photo_reference_upload_validation_error(raw: bytes, mime: str) -> str:
        """Fully decode a catalog upload before it is persisted."""
        if PILImage is None:
            return "服务器缺少 Pillow 图片校验组件，暂时无法上传参考图"
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }.get(mime)
        if not expected_format:
            return "参考图库只支持 PNG、JPEG 或 WebP 图片"
        try:
            # verify() checks the container, then a fresh decoder loads the pixels
            # so truncated images are rejected instead of merely passing a magic
            # byte check.
            with PILImage.open(io.BytesIO(raw)) as image:
                if str(getattr(image, "format", "") or "").upper() != expected_format:
                    return "图片声明类型与实际格式不一致"
                image.verify()
            with PILImage.open(io.BytesIO(raw)) as image:
                if str(getattr(image, "format", "") or "").upper() != expected_format:
                    return "图片声明类型与实际格式不一致"
                image.load()
        except Exception as exc:
            logger.info("参考图库上传图片解码失败: type=%s", type(exc).__name__)
            return "图片内容损坏或无法解码，请选择完整的 PNG、JPEG 或 WebP 文件"
        return ""

    @staticmethod
    def _photo_reference_upload_usage(directory: Path) -> tuple[int, int]:
        count = 0
        total_bytes = 0
        for entry in directory.iterdir():
            if not entry.name.startswith("webui_") or entry.suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            }:
                continue
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                size = max(0, int(entry.stat().st_size))
            except (OSError, ValueError):
                continue
            count += 1
            total_bytes += size
        return count, total_bytes

    @asynccontextmanager
    async def _photo_reference_catalog_upload_lock(self):
        data_lock = getattr(self.plugin, "_data_lock", None)
        if data_lock is not None and callable(getattr(data_lock, "__aenter__", None)):
            async with data_lock:
                yield
            return
        lock = getattr(self.plugin, "_photo_reference_catalog_upload_guard", None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self.plugin, "_photo_reference_catalog_upload_guard", lock)
        async with lock:
            yield

    async def upload_photo_reference(self) -> dict[str, Any]:
        content_length = request.content_length
        if content_length is not None:
            try:
                if int(content_length) > PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES:
                    return self._error("参考图上传请求体过大，请将图片控制在 12 MB 以内")
            except (TypeError, ValueError):
                pass
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        decoded = self._decode_photo_reference_asset_data_url(
            payload.get("data_url") or payload.get("image_data") or payload.get("image")
        )
        if decoded is None:
            return self._error("只支持不超过 12 MB 的有效 PNG、JPEG 或 WebP 图片")
        raw, mime, suffix = decoded
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            return self._error("参考图库只支持 PNG、JPEG 或 WebP 图片")
        validation_error = await asyncio.to_thread(
            self._photo_reference_upload_validation_error,
            raw,
            mime,
        )
        if validation_error:
            return self._error(validation_error)

        directory_getter = getattr(self.plugin, "_photo_reference_image_dir", None)
        try:
            target_dir = Path(directory_getter()) if callable(directory_getter) else (
                Path(str(getattr(self.plugin, "data_dir", "") or ".")).expanduser()
                / "photo_reference_images"
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            target_dir = target_dir.resolve()
            if not target_dir.is_dir():
                return self._error("参考图存储目录不可用")
        except (OSError, TypeError, ValueError) as exc:
            return self._error(f"无法创建参考图存储目录: {exc}")

        digest = hashlib.sha256(raw).hexdigest()
        target = target_dir / f"webui_{digest}{suffix}"
        temporary: Path | None = None
        async with self._photo_reference_catalog_upload_lock():
            try:
                if target.is_symlink():
                    return self._error("参考图目标文件异常，请清理后重试")
                target_exists = target.exists()
                existing_size = 0
                if target_exists:
                    if not target.is_file():
                        return self._error("参考图目标文件异常，请清理后重试")
                    existing_size = max(0, int(target.stat().st_size))
                    if existing_size == len(raw):
                        existing_hash = await asyncio.to_thread(
                            lambda: hashlib.sha256(target.read_bytes()).hexdigest()
                        )
                        if existing_hash == digest:
                            resolved = target.resolve()
                            return self._ok({
                                "source": str(resolved),
                                "filename": resolved.name,
                                "mime": mime,
                                "size": existing_size,
                            })

                count, total_bytes = self._photo_reference_upload_usage(target_dir)
                if target_exists:
                    count = max(0, count - 1)
                    total_bytes = max(0, total_bytes - existing_size)
                if count >= PHOTO_REFERENCE_UPLOAD_MAX_COUNT:
                    return self._error(
                        f"参考图库上传缓存已达到 {PHOTO_REFERENCE_UPLOAD_MAX_COUNT} 个文件上限"
                    )
                if total_bytes + len(raw) > PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES:
                    return self._error("参考图库上传缓存已达到 1 GiB 容量上限")

                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.uploading")
                await asyncio.to_thread(temporary.write_bytes, raw)
                if temporary.stat().st_size != len(raw):
                    return self._error("参考图写入不完整，请重新上传")
                await asyncio.to_thread(os.replace, temporary, target)
                temporary = None
                resolved = target.resolve()
                stored_size = resolved.stat().st_size
            except OSError as exc:
                return self._error(f"保存参考图失败: {exc}")
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        if stored_size != len(raw):
            try:
                resolved.unlink(missing_ok=True)
            except OSError:
                pass
            return self._error("参考图写入不完整，请重新上传")
        return self._ok({
            "source": str(resolved),
            "filename": resolved.name,
            "mime": mime,
            "size": stored_size,
        })
