# -*- coding: utf-8 -*-
"""PrivateImageIngestCachePart02Mixin。

由 tools/split_mixin_domain.py 从 private_image_ingest_cache.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 414 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageIngestCacheMixin）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from .helpers import _safe_int, _single_line
from .private_image_shared import PREPARED_IMAGE_MAX_AGE_SECONDS, _private_image_host, logger
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse



class PrivateImageIngestCachePart02Mixin:
    """PrivateImageIngestCachePart02Mixin（从 PrivateImageIngestCacheMixin 拆出）。"""


    def _sweep_stale_prepared_image_files(self, target_dir: Path) -> int:
        """Remove stale downloaded images left behind by cancellation or errors."""
        removed = 0
        try:
            deadline = _private_image_host._now_ts() - PREPARED_IMAGE_MAX_AGE_SECONDS
            for path in target_dir.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < deadline:
                        path.unlink(missing_ok=True)
                        removed += 1
                except Exception:
                    continue
        except Exception:
            return removed
        if removed:
            logger.info("stale prepared images removed: dir=%s removed=%s", target_dir.name, removed)
        return removed

    def _cleanup_prepared_image_sources(self, sources: list[str], *, namespace: str) -> None:
        """Remove only temporary files downloaded into this plugin's vision namespace."""
        try:
            base = (
                Path(self.data_dir)
                / "private_inbound_images"
                / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(namespace or "vision"))
            ).resolve()
        except Exception:
            return
        for source in sources or []:
            text = str(source or "").strip()
            if not text or text.startswith(("data:", "base64://", "http://", "https://")):
                continue
            if text.startswith("file://"):
                text = text[len("file://"):]
            try:
                path = Path(text).resolve()
                if path.is_file() and path.is_relative_to(base):
                    path.unlink(missing_ok=True)
            except Exception:
                continue

    def _private_image_sources_for_astrbot_request(self, image_sources: list[str]) -> list[str]:
        refs: list[str] = []
        for source in [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]:
            text = source
            if text.startswith("data:") or text.startswith("base64://"):
                continue
            if re.match(r"^https?://", text, flags=re.I):
                continue
            path = self._private_image_local_path_from_source(text)
            if path is None:
                continue
            if not path.exists() or not path.is_file() or not self._private_image_local_path_is_allowed(path):
                continue
            ref = str(path.resolve())
            if ref not in refs:
                refs.append(ref)
        return refs

    def _private_image_source_to_model_url(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        if re.match(r"^https?://", text, flags=re.I) or text.startswith("data:"):
            return text
        if text.startswith("base64://"):
            return f"data:image/jpeg;base64,{text[len('base64://'):]}"
        path = self._private_image_local_path_from_source(text)
        if path is None:
            return ""
        if not path.exists() or not path.is_file():
            return ""
        if not self._private_image_local_path_is_allowed(path):
            return ""
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/gif" if suffix == ".gif" else "image/jpeg"
        try:
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except Exception as exc:
            logger.debug("私聊图片转 data url 失败: %s", exc)
            return ""

    def _private_image_source_cache_key(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        try:
            if text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                raw = base64.b64decode(payload, validate=False) if ";base64" in meta.lower() else payload.encode("utf-8", errors="ignore")
                return "sha256:" + hashlib.sha256(raw).hexdigest()
            if text.startswith("base64://"):
                raw = base64.b64decode(text[len("base64://"):], validate=False)
                return "sha256:" + hashlib.sha256(raw).hexdigest()
            path = self._private_image_local_path_from_source(text)
            if path is None:
                return ""
            if path.exists() and path.is_file() and self._private_image_local_path_is_allowed(path):
                return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            logger.debug("私聊图片缓存键生成失败: %s", exc)
        if re.match(r"^https?://", text, flags=re.I):
            return self._private_image_normalized_url_cache_key(text)
        return ""

    def _private_image_normalized_url_cache_key(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        try:
            parsed = urlparse(text)
            volatile_keys = {
                "term", "is_origin", "spec", "rkey", "token", "sign", "expires", "expire", "ts",
                "timestamp", "t", "time", "cache", "cache_key", "ck", "rand", "random", "nonce",
                "download", "disposition", "file_size", "size", "width", "height", "w", "h",
                "quality", "format", "fmt", "x-oss-process", "imageView2", "imageMogr2",
            }
            query_parts = []
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                lowered = key.lower()
                if lowered in volatile_keys or lowered.startswith("utm_"):
                    continue
                query_parts.append((key, value))
            normalized = urlunparse((
                parsed.scheme.lower() or "https",
                parsed.netloc.lower(),
                parsed.path,
                "",
                urlencode(sorted(query_parts)),
                "",
            ))
            return "url:" + hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()
        except Exception:
            return "url:" + hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

    def _private_image_source_cache_aliases(self, source: str) -> list[str]:
        text = str(source or "").strip()
        aliases: list[str] = []

        def add(value: str) -> None:
            item = str(value or "").strip()
            if item and item not in aliases:
                aliases.append(item)

        primary = self._private_image_source_cache_key(text)
        add(primary)
        if re.match(r"^https?://", text, flags=re.I):
            add(self._private_image_normalized_url_cache_key(text))
            try:
                parsed = urlparse(text)
                name = unquote((parsed.path or "").rsplit("/", 1)[-1]).lower()
                stem = re.sub(r"\.(?:jpg|jpeg|png|webp|gif|bmp)$", "", name, flags=re.I)
                for token in re.findall(r"[a-f0-9]{16,64}", stem):
                    add("urlhex:" + token)
            except Exception:
                pass
        raw = self._private_image_source_bytes_for_cache_alias(text)
        if raw:
            for alias in self._private_image_visual_cache_aliases_from_bytes(raw):
                add(alias)
        return aliases[:8]

    def _private_image_source_bytes_for_cache_alias(self, source: str) -> bytes:
        text = str(source or "").strip()
        if not text:
            return b""
        try:
            if text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                return base64.b64decode(payload, validate=False) if ";base64" in meta.lower() else payload.encode("utf-8", errors="ignore")
            if text.startswith("base64://"):
                return base64.b64decode(text[len("base64://"):], validate=False)
            if text.startswith("file://"):
                text = text[len("file://"):]
            if re.match(r"^https?://", text, flags=re.I):
                return b""
            path = Path(text)
            if path.exists() and path.is_file() and self._private_image_local_path_is_allowed(path):
                return path.read_bytes()
        except Exception as exc:
            logger.debug("私聊图片缓存别名字节读取失败: %s", exc)
        return b""

    def _private_image_visual_cache_aliases_from_bytes(self, raw: bytes) -> list[str]:
        if not raw:
            return []
        try:
            from PIL import Image as PILImage
        except Exception:
            return []
        try:
            with PILImage.open(io.BytesIO(raw)) as image:
                frame_total = int(getattr(image, "n_frames", 1) or 1)
                if bool(getattr(image, "is_animated", False) or frame_total > 1):
                    return []
                width, height = image.size
                if width <= 0 or height <= 0:
                    return []
                gray = image.convert("L")
                ahash_image = gray.resize((8, 8))
                ahash_reader = getattr(ahash_image, "get_flattened_data", None)
                ahash_pixels = list(ahash_reader() if callable(ahash_reader) else ahash_image.getdata())
                average = sum(ahash_pixels) / max(1, len(ahash_pixels))
                ahash_bits = "".join("1" if value >= average else "0" for value in ahash_pixels)
                dhash_image = gray.resize((9, 8))
                dhash_reader = getattr(dhash_image, "get_flattened_data", None)
                dhash_pixels = list(dhash_reader() if callable(dhash_reader) else dhash_image.getdata())
                dhash_bits = []
                for row in range(8):
                    offset = row * 9
                    for col in range(8):
                        dhash_bits.append("1" if dhash_pixels[offset + col] > dhash_pixels[offset + col + 1] else "0")
                ahash = f"{int(ahash_bits, 2):016x}"
                dhash = f"{int(''.join(dhash_bits), 2):016x}"
                aspect_bucket = max(1, min(999, int(round((width / max(1, height)) * 100))))
                return [f"pxhash:v1:a{aspect_bucket}:ah{ahash}:dh{dhash}"]
        except Exception as exc:
            logger.debug("私聊图片视觉指纹生成失败: %s", exc)
        return []

    def _private_image_cache_preview_dir(self) -> Path:
        return Path(self.data_dir) / "private_image_cache_previews"

    def _remove_private_image_cache_preview_file(self, preview_path: str) -> None:
        if not preview_path:
            return
        try:
            path = Path(preview_path).resolve()
            base = self._private_image_cache_preview_dir().resolve()
            if not path.is_relative_to(base):
                return
            path.unlink(missing_ok=True)
            (base / ".thumbnails" / f"{path.stem}.webp").unlink(missing_ok=True)
        except Exception:
            pass

    def _private_image_cache_preview_from_sources(
        self,
        cache_key: str,
        sources: list[str],
    ) -> dict[str, Any]:
        clean_key = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(cache_key or ""))[:80]
        if not clean_key:
            return {}
        try:
            from PIL import Image as PILImage, ImageOps
        except Exception:
            return {}
        for source in [str(item).strip() for item in (sources or []) if str(item or "").strip()][:6]:
            raw = self._private_image_source_bytes_for_cache_alias(source)
            if not raw:
                continue
            try:
                with PILImage.open(io.BytesIO(raw)) as image:
                    image.seek(0)
                    image = ImageOps.exif_transpose(image)
                    if image.mode not in {"RGB", "L"}:
                        image = image.convert("RGBA")
                        background = PILImage.new("RGBA", image.size, (255, 255, 255, 255))
                        background.alpha_composite(image)
                        image = background.convert("RGB")
                    else:
                        image = image.convert("RGB")
                    image.thumbnail((320, 320))
                    target_dir = self._private_image_cache_preview_dir()
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / f"{clean_key}.jpg"
                    image.save(target, format="JPEG", quality=72, optimize=True, progressive=True)
                    try:
                        file_size = target.stat().st_size
                    except Exception:
                        file_size = 0
                    return {
                        "preview_path": str(target),
                        "preview_width": int(image.width),
                        "preview_height": int(image.height),
                        "preview_size": int(file_size),
                    }
            except Exception as exc:
                logger.debug("图片缓存预览生成失败: %s", exc)
        return {}

    def _private_image_cache_aliases_for_sources(self, sources: list[str]) -> list[str]:
        aliases: list[str] = []
        for source in [str(item).strip() for item in (sources or []) if str(item or "").strip()][:5]:
            for alias in self._private_image_source_cache_aliases(source):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return aliases[:24]

    def _private_image_cache_image_keys(self, sources: list[str]) -> list[str]:
        keys: list[str] = []
        for source in sources or []:
            key = self._private_image_source_cache_key(source)
            if key and key not in keys:
                keys.append(key)
        return keys[:5]

    def _private_image_vision_cache_store(self) -> dict[str, Any]:
        cache = self.data.setdefault("private_image_vision_cache", {})
        if not isinstance(cache, dict):
            cache = {}
            self.data["private_image_vision_cache"] = cache
        return cache

    @staticmethod
    def _private_image_vision_cache_prompt_sig(prompt: str = "") -> str:
        """Return the compact signature stored alongside a vision cache item."""
        value = str(prompt or "")
        return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16] if value else ""

    def _private_image_vision_cache_key(self, image_keys: list[str], provider_id: str, prompt: str = "", *, scope: str = "private_image") -> str:
        clean_keys = [str(item).strip() for item in image_keys if str(item or "").strip()]
        if not clean_keys:
            return ""
        prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)
        raw = "v3|" + _single_line(scope, 40) + "|" + str(provider_id or "") + "|" + prompt_sig + "|" + "|".join(clean_keys)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _get_private_image_vision_cache(
        self,
        cache_key: str,
        *,
        provider_id: str = "",
        image_keys: list[str] | None = None,
        image_aliases: list[str] | None = None,
        image_count: int = 0,
        scope: str = "private_image",
        allow_image_key_fallback: bool = True,
        prompt: str = "",
    ) -> str:
        if not bool(self._private_image_setting("enable_private_image_vision_cache", True)):
            return ""
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item).strip() for item in (image_keys or []) if str(item or "").strip()]
        clean_aliases = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        expected_count = max(0, int(image_count or 0))
        expected_prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)

        def prompt_matches(item: dict[str, Any]) -> bool:
            """Allow unsigned legacy entries, but never cross prompt variants."""
            if not expected_prompt_sig:
                return True
            cached_prompt_sig = _single_line(item.get("prompt_sig"), 32)
            return not cached_prompt_sig or cached_prompt_sig == expected_prompt_sig

        def use_item(key: str, item: dict[str, Any], *, fallback: bool = False, detail: str = "") -> str:
            if not prompt_matches(item):
                return ""
            text = _single_line(item.get("text"), 900 if scope == "forward_image" else self._private_image_vision_text_limit(expected_count))
            if not text:
                cache.pop(key, None)
                return ""
            item["hits"] = _safe_int(item.get("hits"), 0, 0) + 1
            item["last_hit_ts"] = _private_image_host._now_ts()
            if fallback and cache_key and key != cache_key:
                item.setdefault("migrated_from", key)
                cache[cache_key] = item
                cache.pop(key, None)
            self._record_cache_metric(f"image_vision:{scope}", hit=True, detail=detail or ("fallback" if fallback else "direct"))
            return text

        item = cache.get(cache_key)
        if isinstance(item, dict):
            text = use_item(cache_key, item)
            if text:
                return text

        if allow_image_key_fallback and clean_image_keys:
            expected_provider = _single_line(provider_id, 160)
            expected_scope = _single_line(scope, 40)
            provider_fallback: tuple[str, dict[str, Any]] | None = None
            for key, item in list(cache.items()):
                if key == cache_key or not isinstance(item, dict):
                    continue
                cached_keys = [str(value).strip() for value in item.get("image_keys", []) if str(value or "").strip()]
                if cached_keys != clean_image_keys:
                    continue
                cached_scope = _single_line(item.get("scope"), 40)
                if cached_scope and expected_scope and cached_scope != expected_scope:
                    continue
                if not prompt_matches(item):
                    continue
                cached_provider = _single_line(item.get("provider_id"), 160)
                if expected_provider and cached_provider and cached_provider != expected_provider:
                    if provider_fallback is None:
                        provider_fallback = (key, item)
                    continue
                text = use_item(key, item, fallback=True)
                if text:
                    return text
            if provider_fallback is not None:
                key, item = provider_fallback
                text = use_item(key, item, fallback=True, detail="provider_fallback")
                if text:
                    return text

            if clean_aliases and expected_count == 1:
                alias_provider_fallback: tuple[str, dict[str, Any]] | None = None
                for key, item in list(cache.items()):
                    if key == cache_key or not isinstance(item, dict):
                        continue
                    cached_scope = _single_line(item.get("scope"), 40)
                    if cached_scope and expected_scope and cached_scope != expected_scope:
                        continue
                    if not prompt_matches(item):
                        continue
                    cached_count = _safe_int(item.get("image_count"), 0, 0)
                    if cached_count <= 0:
                        cached_count = 1 if len([value for value in item.get("image_keys", []) if str(value or "").strip()]) == 1 else 0
                    if cached_count != 1:
                        continue
                    cached_aliases = {str(value).strip() for value in item.get("image_aliases", []) if str(value or "").strip()}
                    if not (cached_aliases & clean_aliases):
                        continue
                    cached_provider = _single_line(item.get("provider_id"), 160)
                    if expected_provider and cached_provider and cached_provider != expected_provider:
                        if alias_provider_fallback is None:
                            alias_provider_fallback = (key, item)
                        continue
                    text = use_item(key, item, fallback=True, detail="alias_fallback")
                    if text:
                        return text
                if alias_provider_fallback is not None:
                    key, item = alias_provider_fallback
                    text = use_item(key, item, fallback=True, detail="alias_provider_fallback")
                    if text:
                        return text

        self._record_cache_metric(f"image_vision:{scope}", hit=False, detail="miss")
        return ""
