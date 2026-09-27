# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_media.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 491 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaMixin）。
"""
from __future__ import annotations

from .page_api_media_shared import IMAGE_CACHE_THUMBNAIL_MAX_EDGE, IMAGE_CACHE_THUMBNAIL_QUALITY, PAGE_API_PREFIX, logger
from .page_api_media_shared import Any
from .page_api_media_shared import OwnedReactionAssetCatalog
from .page_api_media_shared import PILImage
from .page_api_media_shared import PILImageOps
from .page_api_media_shared import Path
from .page_api_media_shared import _path_text
from .page_api_media_shared import _safe_int
from .page_api_media_shared import asset_abs_path
from .page_api_media_shared import asset_root
from .page_api_media_shared import asyncio
from .page_api_media_shared import base64
from .page_api_media_shared import deepcopy
from .page_api_media_shared import load_asset_index
from .page_api_media_shared import mimetypes
from .page_api_media_shared import re
from .page_api_media_shared import request
from .page_api_media_shared import send_file
from .page_api_media_shared import time
from .page_api_media_shared import uuid



class PrivateCompanionPageApiMediaPart01Mixin:
    """PrivateCompanionPageApiMediaPart01Mixin（从 PrivateCompanionPageApiMediaMixin 拆出）。"""


    def _image_api_runtime_lock(self) -> asyncio.Lock:
        lock = getattr(self.plugin, "_external_image_api_runtime_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self.plugin._external_image_api_runtime_lock = lock
        return lock

    @staticmethod
    def _page_asset_prefix() -> str:
        """Keep generated asset URLs on the transport serving this request."""
        try:
            if str(request.path or "").startswith("/api/v1/"):
                return "/api/v1"
        except RuntimeError:
            pass
        return PAGE_API_PREFIX

    def _image_extension_contract_status(self, api: Any | None = None) -> dict[str, Any]:
        """Expose the effective Companion/Image contract without invoking generation."""
        getter = getattr(self.plugin, "_image_companion_contract", None)
        if not callable(getter):
            return {}
        try:
            mode, _current, generation, reason = getter(api=api) if api is not None else getter()
        except TypeError:
            try:
                mode, _current, generation, reason = getter()
            except Exception:
                return {"mode": "unknown", "generation": 0, "available": False, "reason": "contract_probe_failed"}
        except Exception:
            return {"mode": "unknown", "generation": 0, "available": False, "reason": "contract_probe_failed"}
        return {
            "mode": self._single_line(mode, 30),
            "generation": self._int(generation),
            "available": mode in {"current", "current_compat"},
            "reason": self._single_line(reason, 120),
        }

    def _image_generation_extension_status(self) -> dict[str, Any]:
        """Read the public Image extension snapshot without making it mandatory."""
        getter = getattr(self.plugin, "_image_companion_status", None)
        if not callable(getter):
            return {}
        try:
            status = getter()
        except Exception:
            return {}
        return dict(status) if isinstance(status, dict) else {}

    def _image_generation_called_plugin_diagnostics(
        self,
        status: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        snapshot = dict(status) if isinstance(status, dict) else self._image_generation_extension_status()
        generation = snapshot.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        has_image_bridge = callable(getattr(self.plugin, "_image_companion_status", None))
        plugin_id = self._single_line(
            generation.get("plugin_id") or snapshot.get("plugin_id"),
            100,
        )
        if not plugin_id and has_image_bridge:
            plugin_id = "astrbot_plugin_image_companion"
        plugin_name = self._single_line(
            generation.get("plugin_name") or snapshot.get("plugin_name"),
            100,
        )
        if not plugin_name and plugin_id == "astrbot_plugin_image_companion":
            plugin_name = "我会画给你看"
        return {
            "called_plugin": plugin_id,
            "called_plugin_name": plugin_name,
            "called_plugin_version": self._single_line(
                generation.get("plugin_version") or snapshot.get("plugin_version"),
                60,
            ),
            "called_plugin_api_version": self._single_line(
                generation.get("api_version") or snapshot.get("api_version"),
                80,
            ),
            "called_plugin_status_schema": self._single_line(
                generation.get("status_schema_version") or snapshot.get("status_schema_version"),
                80,
            ),
        }

    def _daily_outfit_image_path(self) -> Path | None:
        data = getattr(self.plugin, "data", {}) if isinstance(getattr(self.plugin, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path_text = self._single_line(item.get("path"), 500)
        if not path_text:
            return None
        try:
            path = Path(path_text).resolve()
        except Exception:
            return None
        if not path.exists() or not path.is_file():
            return None
        return path

    async def get_daily_outfit_image(self):
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        response = await send_file(str(path))
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    async def get_daily_outfit_image_data(self) -> dict[str, Any]:
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            raw = await asyncio.to_thread(path.read_bytes)
            return self._ok(
                {
                    "mime": mime,
                    "size": len(raw),
                    "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                }
            )
        except Exception as exc:
            logger.warning("读取每日穿搭图片失败: %s", self._single_line(exc, 160))
            return self._exception_error("读取每日穿搭图片失败")

    @staticmethod
    def _vision_provider_test_image_data_url() -> str:
        # Keep the connection test cheap while satisfying visual providers such
        # as Qwen-VL that reject 1x1 placeholders (both sides are 32 pixels).
        return (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAmElEQVR42mP8//8/Ay0BEwONwdC3gAWZo5e4kCqGXpofP0A+wLSfVIAZBqOpiJw4wBWsaHGDS5w0HyBHGjHsEVhUIIcvMWxyIhmXfiLz4/COA73EhcSUr/iVDYIgwu8Jgl5kIiP9kKSSqHyANZThglTIBxCDsIYGQV+yEB8CaBYQGYAsDGRV5RTVB9RqW9ApHzCOtk0H3AIAj19C2ZNGr00AAAAASUVORK5CYII="
        )

    def _visual_provider_for_test(self, provider_id: str) -> Any:
        getter = getattr(self.plugin, "_private_image_provider_by_id", None)
        provider = getter(provider_id) if callable(getter) else None
        if provider is not None:
            return provider
        context = getattr(self.plugin, "context", None)
        context_getter = getattr(context, "get_provider_by_id", None)
        return context_getter(provider_id) if callable(context_getter) else None

    def _visual_provider_test_timeout(self, provider_id: str, key: str, requested: float | None) -> float:
        if requested is not None:
            return requested
        getter = getattr(self.plugin, "_private_image_provider_timeout_seconds", None)
        if callable(getter):
            source = "plugin_vision" if key == "PLUGIN_VISION_PROVIDER_ID" else "reading_archive_vision"
            return max(0.0, float(getter(provider_id, source)))
        return 30.0

    def _visual_call_error_text(self, exc: Exception, *, timeout: float = 0.0) -> str:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            duration = f"（{timeout:g} 秒）" if timeout > 0 else ""
            return f"视觉模型图片请求超时{duration}"
        unsupported = getattr(self.plugin, "_exception_indicates_image_input_unsupported", None)
        if callable(unsupported) and unsupported(exc):
            return "Provider 拒绝图片输入，请确认所选模型支持视觉"
        detail = self._single_line(exc, 220)
        return detail or f"视觉模型调用失败（{exc.__class__.__name__}）"

    async def get_reaction_library_image_data(self) -> dict[str, Any]:
        item_id = self._single_line(request.args.get("id"), 64)
        if not item_id:
            return self._error("缺少表情包 id")
        try:
            result = await asyncio.to_thread(self._reaction_library().get_image_data, item_id)
            return self._ok(result) if result else self._error("表情包不存在或图片文件已丢失")
        except Exception as exc:
            logger.error("读取表情包图片失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def list_image_cache(self) -> dict[str, Any]:
        try:
            scope_filter = self._single_line(request.args.get("scope"), 40)
            keyword = self._single_line(request.args.get("q"), 120).lower()
            limit = self._query_int("limit", 80, 1, 300)
            offset = self._query_int("offset", 0, 0, 100000)
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            cache = data.get("private_image_vision_cache") if isinstance(data.get("private_image_vision_cache"), dict) else {}
            rows: list[dict[str, Any]] = []
            scopes: set[str] = set()
            for key, raw in cache.items():
                if not isinstance(raw, dict):
                    continue
                item = self._image_cache_item_summary(str(key), raw)
                scope = item.get("scope") or "private_image"
                scopes.add(str(scope))
                if scope_filter and scope_filter != "all" and scope != scope_filter:
                    continue
                if keyword:
                    haystack = " ".join(
                        str(item.get(name) or "")
                        for name in (
                            "key",
                            "text",
                            "provider_id",
                            "scope",
                            "image_keys_text",
                            "image_aliases_text",
                            "image_type",
                            "ownership",
                            "intent",
                        )
                    ).lower()
                    if keyword not in haystack:
                        continue
                rows.append(item)
            rows.sort(
                key=lambda item: (
                    self._float(item.get("last_hit_ts"))
                    or self._float(item.get("created_ts")),
                    self._float(item.get("created_ts")),
                ),
                reverse=True,
            )
            total = len(rows)
            return self._ok(
                {
                    "items": rows[offset : offset + limit],
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "scopes": sorted(scopes),
                    "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                    "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                }
            )
        except Exception as exc:
            logger.error(f"获取图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def update_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        action = self._single_line(payload.get("action"), 20).lower() or "regenerate"
        if not key:
            return self._error("缺少缓存 key")
        text = str(payload.get("text") or "").strip()
        if not text:
            return self._error("视觉摘要不能为空")
        try:
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or not isinstance(cache.get(key), dict):
                    return self._error("缓存条目不存在")
                item = cache[key]
                scope = self._single_line(item.get("scope"), 40) or "private_image"
                item["text"] = self._single_line(text, 900 if scope == "forward_image" else 600)
                if "provider_id" in payload:
                    item["provider_id"] = self._single_line(payload.get("provider_id"), 160)
                if "scope" in payload:
                    next_scope = self._single_line(payload.get("scope"), 40)
                    if next_scope:
                        item["scope"] = next_scope
                item["edited_ts"] = time.time()
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                updated = deepcopy(item)
            return self._ok(self._image_cache_item_summary(key, updated))
        except Exception as exc:
            logger.error(f"更新图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _image_cache_preview_dir(self) -> Path:
        return (Path(getattr(self.plugin, "data_dir", "")) / "private_image_cache_previews").resolve()

    @staticmethod
    def _image_cache_clean_key(key: str) -> str:
        return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(key or ""))[:80]

    def _image_cache_preview_file(self, key: str, raw: dict[str, Any] | None = None) -> Path | None:
        clean_key = self._image_cache_clean_key(key)
        if not clean_key:
            return None
        base = self._image_cache_preview_dir()
        candidates: list[Path] = []
        preview_path = self._single_line((raw or {}).get("preview_path"), 260) if isinstance(raw, dict) else ""
        if preview_path:
            try:
                candidates.append(Path(preview_path).resolve())
            except Exception:
                pass
        candidates.extend(base / f"{clean_key}{suffix}" for suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.is_relative_to(base):
                    return candidate
            except Exception:
                continue
        return None

    def _image_cache_thumbnail_file(self, key: str) -> Path | None:
        clean_key = self._image_cache_clean_key(key)
        if not clean_key:
            return None
        return self._image_cache_preview_dir() / ".thumbnails" / f"{clean_key}.webp"

    async def _get_or_create_image_cache_thumbnail(self, key: str, source: Path) -> Path | None:
        return await asyncio.to_thread(self._build_image_cache_thumbnail_sync, key, source)

    def _build_image_cache_thumbnail_sync(self, key: str, source: Path) -> Path | None:
        if PILImage is None:
            return None
        target = self._image_cache_thumbnail_file(key)
        if target is None:
            return None
        try:
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
        except OSError:
            pass

        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with PILImage.open(source) as image:
                if PILImageOps is not None:
                    image = PILImageOps.exif_transpose(image)
                image.seek(0)
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = PILImage.new("RGBA", rgba.size, (255, 255, 255, 255))
                    background.alpha_composite(rgba)
                    image = background.convert("RGB")
                else:
                    image = image.convert("RGB")
                resampling = getattr(PILImage, "Resampling", PILImage)
                image.thumbnail(
                    (IMAGE_CACHE_THUMBNAIL_MAX_EDGE, IMAGE_CACHE_THUMBNAIL_MAX_EDGE),
                    resampling.LANCZOS,
                )
                image.save(
                    temporary,
                    format="WEBP",
                    quality=IMAGE_CACHE_THUMBNAIL_QUALITY,
                    method=6,
                )
            temporary.replace(target)
            return target
        except Exception as exc:
            logger.warning(
                "生成图片缓存缩略图失败: key=%s error=%s",
                self._single_line(key, 80),
                self._single_line(exc, 160),
            )
            return None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _restore_image_cache_preview_metadata(self, key: str, raw: dict[str, Any]) -> bool:
        preview = self._image_cache_preview_file(key, raw)
        if preview is None:
            return False
        changed = False
        if self._single_line(raw.get("preview_path"), 260) != str(preview):
            raw["preview_path"] = str(preview)
            changed = True
        try:
            preview_size = preview.stat().st_size
        except Exception:
            preview_size = 0
        if _safe_int(raw.get("preview_size"), 0, 0) != preview_size:
            raw["preview_size"] = preview_size
            changed = True
        return changed

    async def _resolve_image_cache_preview_for_request(self) -> tuple[str, Path] | tuple[dict[str, Any], int]:
        key = self._single_line(request.args.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        async with self.plugin._data_lock:
            cache = self.plugin.data.get("private_image_vision_cache")
            item = cache.get(key) if isinstance(cache, dict) else None
            if not isinstance(item, dict):
                return self._error("缓存条目不存在")
            path = self._image_cache_preview_file(key, item)
            if path is None:
                return self._error("缓存预览文件不存在")
            if self._restore_image_cache_preview_metadata(key, item):
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
        return key, path

    @staticmethod
    async def _encode_image_cache_file_data_url(
        path: Path,
        mime: str = "",
        *,
        max_bytes: int = 0,
    ) -> dict[str, str]:
        def read_file() -> bytes:
            if max_bytes <= 0:
                return path.read_bytes()
            with path.open("rb") as stream:
                payload = stream.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError(f"预览文件超过 {max_bytes} bytes 上限")
            return payload

        raw = await asyncio.to_thread(read_file)
        content_type = mime or mimetypes.guess_type(str(path))[0] or "image/jpeg"
        encoded = base64.b64encode(raw).decode("ascii")
        return {"data_url": f"data:{content_type};base64,{encoded}", "mime": content_type}

    async def describe_wardrobe_image(self) -> dict[str, Any]:
        """Describe one garment image with the vision model for the wardrobe panel."""

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        describer = getattr(self.plugin, "_wardrobe_describe_image", None)
        if not callable(describer):
            return self._error("当前插件实例不支持衣柜识图")
        raw_source = _path_text(payload.get("source") or payload.get("path"), 1200)
        if not raw_source:
            return self._error("缺少图片路径")
        source = self._wardrobe_page_local_path(raw_source)
        if source is None:
            return self._error("只能描述已上传到插件目录的 PNG、JPEG 或 WebP 图片")
        note = self._single_line(payload.get("note"), 200)
        # 面板可以先选模型再识图，不必等保存；这里只把候选排到最前，
        # 无效或不支持图片的 id 会被下游跳过并回退到已配置模型。
        preferred = self._single_line(payload.get("provider_id"), 160)
        try:
            parsed, error = await describer(
                [str(source)], note=note, umo="", provider_id=preferred
            )
        except Exception as exc:
            logger.warning("衣柜识图接口失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("识图失败，请稍后再试")
        if parsed is None:
            return self._error(error or "识图模型没有返回可用的衣物描述")
        return self._ok(
            {
                "source": str(source),
                # 类型决定去向：散件进 wardrobe_items，整套/参考进 wardrobe_outfits
                "kind": str(parsed.get("kind") or ""),
                "slot": str(parsed.get("slot") or ""),
                "name": parsed.get("name", ""),
                "description": parsed.get("description", ""),
                "tags": list(parsed.get("tags") or []),
            }
        )

    def _wardrobe_asset_local_path(self, asset_id: Any) -> Path | None:
        """Resolve one asset id to a file inside this plugin's wardrobe asset store."""

        clean_id = self._single_line(asset_id, 80)
        data_dir = str(getattr(self.plugin, "data_dir", "") or "")
        if not clean_id or not data_dir:
            return None
        try:
            record = load_asset_index(data_dir).get(clean_id)
            if record is None:
                return None
            root = asset_root(data_dir).expanduser().resolve()
            path = asset_abs_path(data_dir, record).expanduser().resolve()
            if not path.is_file():
                return None
            # 索引里的 path 是相对路径，但被手改成 ../.. 就能读到插件之外，
            # 所以这里必须按目录兜住（与 _wardrobe_page_local_path 同一思路）。
            if path != root and root not in path.parents:
                return None
            return path
        except (OSError, ValueError):
            return None

    async def get_wardrobe_asset_image(self) -> dict[str, Any]:
        """Return one wardrobe asset as a data URL for the draft queue thumbnails."""

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        asset_id = self._single_line(payload.get("asset_id"), 80)
        path = self._wardrobe_asset_local_path(asset_id)
        if path is None:
            return self._error("找不到这个素材，或它不是插件目录里的图片")
        try:
            # 与同文件其它素材接口一致\uff1a未知后缀不能猜成 png\u3002
            mime = mimetypes.guess_type(str(path))[0] or ""
            if not mime.startswith("image/") or mime not in self.WARDROBE_ASSET_IMAGE_MIMES:
                return self._error("这个素材不是可以预览的图片")
            # 先 stat 再读：导入期有上限，但手工放进目录、手改 index 的文件不受约束，
            # 整份读进内存再拒绝会白白分配这份内存（与同文件其它素材接口一致）。
            try:
                size = int((await asyncio.to_thread(path.stat)).st_size)
            except OSError:
                return self._error("这个素材已经读不到了")
            if size > self.WARDROBE_ASSET_IMAGE_MAX_BYTES:
                return self._error("素材图片过大，无法在面板里预览")
            raw = await asyncio.to_thread(path.read_bytes)
            # 读完再比一次：文件可能在 stat 与 read 之间被换掉。
            if len(raw) > self.WARDROBE_ASSET_IMAGE_MAX_BYTES:
                return self._error("素材图片过大，无法在面板里预览")
            return self._ok(
                {
                    "asset_id": asset_id,
                    "mime": mime,
                    "size": len(raw),
                    "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                }
            )
        except Exception as exc:
            logger.warning("读取衣柜素材图片失败: %s", self._single_line(exc, 160))
            return self._exception_error("读取衣柜素材图片失败")

    def _owned_reaction_asset_catalog(self) -> OwnedReactionAssetCatalog:
        return OwnedReactionAssetCatalog(getattr(self.plugin, "data_dir", ""))

    def _owned_reaction_asset_entries(self) -> list[dict[str, Any]]:
        entries = getattr(self.plugin, "owned_reaction_assets", [])
        if not isinstance(entries, list):
            return []
        return [dict(entry) for entry in entries if isinstance(entry, dict)][:96]
