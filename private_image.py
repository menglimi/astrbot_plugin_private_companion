# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Image, Plain
except ImportError:
    from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .helpers import _missing_optional_model_dependency, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .segmented_message import split_plain_component_chain

class PrivateImageMixin:
    """Methods split from main.PrivateCompanionPlugin."""

    def _private_event_has_image(self, event: AstrMessageEvent) -> bool:
        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name == "image":
                return True
        return bool(self._raw_private_image_sources(event))

    def _private_event_has_image_safe(self, event: AstrMessageEvent, *, label: str = "") -> bool:
        try:
            return self._private_event_has_image(event)
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if missing:
                logger.warning(
                    "[PrivateCompanion] 私聊图片存在性检测缺少可选模型依赖，已按无图片继续: label=%s module=%s err=%s",
                    _single_line(label, 40) or "-",
                    missing,
                    _single_line(exc, 160),
                )
                return False
            logger.warning(
                "[PrivateCompanion] 私聊图片存在性检测失败，已按无图片继续: label=%s err=%s",
                _single_line(label, 40) or "-",
                _single_line(exc, 160),
            )
            return False

    def _is_private_image_only_message(self, event: AstrMessageEvent, text: str) -> bool:
        cleaned = _single_line(text, 120)
        if cleaned and cleaned not in {"[图片]", "【图片】", "图片"}:
            return False
        components = self._event_components(event)
        if not components:
            return False
        has_image = False
        for comp in components:
            class_name = comp.__class__.__name__.lower()
            if class_name == "image":
                has_image = True
                continue
            if class_name in {"at", "reply"}:
                continue
            comp_text = _single_line(
                getattr(comp, "text", "")
                or getattr(comp, "message", "")
                or getattr(comp, "content", ""),
                120,
            )
            if comp_text and comp_text not in {"[图片]", "【图片】", "图片"}:
                return False
        return has_image

    def _image_component_source(self, comp: Any) -> str:
        data = getattr(comp, "data", None)
        if not isinstance(data, dict):
            data = comp.get("data") if isinstance(comp, dict) and isinstance(comp.get("data"), dict) else {}
        candidates: list[Any] = []
        for source in (data, comp if isinstance(comp, dict) else None):
            if not isinstance(source, dict):
                continue
            nested = source.get("data")
            if isinstance(nested, dict):
                candidates.append(nested)
            candidates.append(source)
        attrs = (
            "url",
            "origin_url",
            "source_url",
            "src",
            "path",
            "image_path",
            "file_path",
            "local_path",
            "file",
        )
        for attr in attrs:
            for candidate in candidates:
                value = candidate.get(attr)
                text = str(value or "").strip()
                if text:
                    return text
            value = getattr(comp, attr, None)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _raw_private_image_sources(self, event: AstrMessageEvent) -> list[str]:
        message_obj = getattr(event, "message_obj", None)
        raw_values = [
            getattr(message_obj, "raw_message", None) if message_obj is not None else None,
            getattr(message_obj, "message", None) if message_obj is not None else None,
            getattr(event, "message_str", None),
        ]
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if isinstance(value, dict):
                item_type = str(value.get("type") or value.get("post_type") or "").lower()
                data = value.get("data") if isinstance(value.get("data"), dict) else value
                if item_type == "image":
                    add(self._extract_image_url_from_segment_data(data))
                    for key in ("url", "origin_url", "source_url", "path", "image_path", "file_path", "local_path", "file"):
                        add(data.get(key))
                for key in ("message", "messages", "content", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested)
                return
            raw_text = str(value or "")
            for match in re.finditer(r"\[CQ:image,([^\]]+)\]", raw_text):
                fields: dict[str, str] = {}
                for part in match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = html.unescape(val.strip())
                add(self._extract_image_url_from_segment_data(fields))
                for key in ("url", "path", "file"):
                    add(fields.get(key))

        for raw in raw_values:
            visit(raw)
        return [source for source in sources if source]

    async def _persist_private_inbound_images(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        result: list[str] = []
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(user_id or "unknown"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return result
        now_ms = int(_now_ts() * 1000)

        async def resolve_source(comp: Any) -> str:
            source = self._image_component_source(comp)
            if source:
                return source
            converter = getattr(comp, "convert_to_file_path", None)
            if callable(converter):
                try:
                    maybe = converter()
                    return str(await maybe if hasattr(maybe, "__await__") else maybe or "").strip()
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 私聊图片组件转换失败: %s", exc)
            return ""

        for index, comp in enumerate(self._event_components(event), 1):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name != "image":
                continue
            source = await resolve_source(comp)
            if not source:
                data = getattr(comp, "data", None)
                data_keys = ",".join(sorted(str(key) for key in data.keys())) if isinstance(data, dict) else ""
                logger.info(
                    "[PrivateCompanion] 私聊图片组件未能解析出文件路径: class=%s data_keys=%s",
                    comp.__class__.__name__,
                    data_keys or "-",
                )
                continue
            source_path = Path(source)
            if source_path.exists() and source_path.is_file():
                suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                target = target_dir / f"{now_ms}_{index}{suffix}"
                try:
                    shutil.copy2(source_path, target)
                    result.append(str(target))
                    continue
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 私聊图片暂存失败: %s", exc)
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(source, target_dir, f"{now_ms}_{index}")
                if persisted:
                    result.append(persisted)
                    continue
            if re.match(r"^(?:data|file|base64)://", source, flags=re.I):
                result.append(source)
        if not result:
            for source in self._raw_private_image_sources(event):
                if not source or source in result:
                    continue
                persisted = await self._persist_private_remote_image_source(source, target_dir, f"{now_ms}_raw_{len(result) + 1}")
                if persisted:
                    result.append(persisted)
                    continue
                if self._private_image_source_to_model_url(source):
                    result.append(source)
        return result

    async def _persist_private_remote_image_source(self, source: str, target_dir: Path, stem: str) -> str:
        text = str(source or "").strip()
        if not re.match(r"^https?://", text, flags=re.I):
            return ""

        def download() -> str:
            try:
                request = urllib.request.Request(
                    text,
                    headers={
                        "User-Agent": "Mozilla/5.0 AstrBot PrivateCompanion/5.0.0",
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    },
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    length = _safe_int(response.headers.get("Content-Length"), 0, 0)
                    max_bytes = 12 * 1024 * 1024
                    if length and length > max_bytes:
                        logger.info("[PrivateCompanion] 私聊远程图片过大,跳过下载: size=%s url=%s", length, _single_line(text, 120))
                        return ""
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            logger.info("[PrivateCompanion] 私聊远程图片下载超过限制,已中止: url=%s", _single_line(text, 120))
                            return ""
                        chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    return ""
                prefix = data[:16]
                suffix = ".jpg"
                if prefix.startswith(b"\x89PNG\r\n\x1a\n") or "png" in content_type:
                    suffix = ".png"
                elif (prefix.startswith(b"RIFF") and b"WEBP" in data[:32]) or "webp" in content_type:
                    suffix = ".webp"
                elif prefix.startswith(b"GIF8") or "gif" in content_type:
                    suffix = ".gif"
                elif prefix.startswith(b"\xff\xd8\xff") or "jpeg" in content_type or "jpg" in content_type:
                    suffix = ".jpg"
                elif "image/" not in content_type:
                    logger.info("[PrivateCompanion] 私聊远程图片响应不是图片,跳过: content_type=%s url=%s", content_type or "-", _single_line(text, 120))
                    return ""
                target = target_dir / f"{re.sub(r'[^0-9A-Za-z_.-]+', '_', stem)}{suffix}"
                target.write_bytes(data)
                return str(target)
            except Exception as exc:
                logger.info("[PrivateCompanion] 私聊远程图片下载失败: %s url=%s", _single_line(exc, 120), _single_line(text, 120))
                return ""

        return await asyncio.to_thread(download)

    async def _prepare_private_image_sources_for_model(self, image_sources: list[str], *, namespace: str = "vision") -> list[str]:
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(namespace or "vision"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        prepared: list[str] = []
        now_ms = int(_now_ts() * 1000)
        for index, source in enumerate([str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:12], 1):
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(source, target_dir, f"{now_ms}_{index}")
                if persisted and persisted not in prepared:
                    prepared.append(persisted)
                continue
            if self._private_image_source_to_model_url(source) and source not in prepared:
                prepared.append(source)
        return prepared

    def _private_image_sources_for_astrbot_request(self, image_sources: list[str]) -> list[str]:
        refs: list[str] = []
        for source in [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]:
            text = source
            if text.startswith("file://"):
                text = text[len("file://"):]
            if text.startswith("data:") or text.startswith("base64://"):
                continue
            if re.match(r"^https?://", text, flags=re.I):
                continue
            path = Path(text)
            if not path.exists() or not path.is_file():
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
        if text.startswith("file://"):
            text = text[len("file://"):]
        path = Path(text)
        if not path.exists() or not path.is_file():
            return ""
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/gif" if suffix == ".gif" else "image/jpeg"
        try:
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except Exception as exc:
            logger.debug("[PrivateCompanion] 私聊图片转 data url 失败: %s", exc)
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
            if text.startswith("file://"):
                text = text[len("file://"):]
            path = Path(text)
            if path.exists() and path.is_file():
                return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 私聊图片缓存键生成失败: %s", exc)
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
            if path.exists() and path.is_file():
                return path.read_bytes()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 私聊图片缓存别名字节读取失败: %s", exc)
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
                ahash_pixels = list(ahash_image.getdata())
                average = sum(ahash_pixels) / max(1, len(ahash_pixels))
                ahash_bits = "".join("1" if value >= average else "0" for value in ahash_pixels)
                dhash_image = gray.resize((9, 8))
                dhash_pixels = list(dhash_image.getdata())
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
            logger.debug("[PrivateCompanion] 私聊图片视觉指纹生成失败: %s", exc)
        return []

    def _private_image_cache_preview_dir(self) -> Path:
        return Path(self.data_dir) / "private_image_cache_previews"

    def _remove_private_image_cache_preview_file(self, preview_path: str) -> None:
        if not preview_path:
            return
        try:
            path = Path(preview_path).resolve()
            base = self._private_image_cache_preview_dir().resolve()
            if path.is_file() and path.is_relative_to(base):
                path.unlink(missing_ok=True)
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
                logger.debug("[PrivateCompanion] 图片缓存预览生成失败: %s", exc)
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

    def _private_image_vision_cache_key(self, image_keys: list[str], provider_id: str, prompt: str = "", *, scope: str = "private_image") -> str:
        clean_keys = [str(item).strip() for item in image_keys if str(item or "").strip()]
        if not clean_keys:
            return ""
        prompt_sig = hashlib.sha1(str(prompt or "").encode("utf-8", errors="ignore")).hexdigest()[:16] if prompt else ""
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
    ) -> str:
        if not bool(getattr(self, "enable_private_image_vision_cache", True)):
            return ""
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item).strip() for item in (image_keys or []) if str(item or "").strip()]
        clean_aliases = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        expected_count = max(0, int(image_count or 0))

        def use_item(key: str, item: dict[str, Any], *, fallback: bool = False, detail: str = "") -> str:
            text = _single_line(item.get("text"), 900 if scope == "forward_image" else self._private_image_vision_text_limit(expected_count))
            if not text:
                cache.pop(key, None)
                return ""
            item["hits"] = _safe_int(item.get("hits"), 0, 0) + 1
            item["last_hit_ts"] = _now_ts()
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

    def _set_private_image_vision_cache(
        self,
        cache_key: str,
        text: str,
        *,
        provider_id: str,
        image_keys: list[str],
        image_aliases: list[str] | None = None,
        image_count: int = 0,
        prompt: str = "",
        scope: str = "private_image",
        preview: dict[str, Any] | None = None,
    ) -> None:
        if not bool(getattr(self, "enable_private_image_vision_cache", True)):
            return
        cleaned = _single_line(text, 900 if scope == "forward_image" else self._private_image_vision_text_limit(image_count))
        if not cache_key or not cleaned:
            return
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item) for item in image_keys[:5] if str(item or "").strip()]
        clean_scope = _single_line(scope, 40)
        clean_provider = _single_line(provider_id, 160)
        clean_aliases = [str(item).strip() for item in (image_aliases or []) if str(item or "").strip()]
        clean_aliases = list(dict.fromkeys(clean_aliases))[:24]
        clean_count = max(0, int(image_count or 0))
        if clean_count <= 0:
            clean_count = len(clean_image_keys)
        prompt_sig = hashlib.sha1(str(prompt or "").encode("utf-8", errors="ignore")).hexdigest()[:16] if prompt else ""
        removed_variants = 0
        for old_key, old_item in list(cache.items()):
            if old_key == cache_key or not isinstance(old_item, dict):
                continue
            old_keys = [str(value).strip() for value in old_item.get("image_keys", []) if str(value or "").strip()]
            old_scope = _single_line(old_item.get("scope"), 40)
            old_provider = _single_line(old_item.get("provider_id"), 160)
            old_prompt_sig = _single_line(old_item.get("prompt_sig"), 32)
            same_reusable_image = old_keys == clean_image_keys and old_scope == clean_scope
            old_aliases = {str(value).strip() for value in old_item.get("image_aliases", []) if str(value or "").strip()}
            old_count = _safe_int(old_item.get("image_count"), 0, 0)
            same_single_alias = clean_count == 1 and old_count == 1 and bool(old_aliases & set(clean_aliases)) and old_scope == clean_scope
            same_reusable_image = same_reusable_image or same_single_alias
            same_provider_variant = same_reusable_image and old_provider == clean_provider
            stale_prompt_variant = same_provider_variant and old_prompt_sig != prompt_sig
            duplicate_provider_variant = same_reusable_image and old_provider and old_provider != clean_provider and _safe_int(old_item.get("hits"), 0, 0) == 0
            if stale_prompt_variant or duplicate_provider_variant:
                if isinstance(old_item, dict):
                    self._remove_private_image_cache_preview_file(_single_line(old_item.get("preview_path"), 260))
                cache.pop(old_key, None)
                removed_variants += 1
        existing_preview_path = ""
        existing_item = cache.get(cache_key)
        if isinstance(existing_item, dict):
            existing_preview_path = _single_line(existing_item.get("preview_path"), 260)
        item = {
            "text": cleaned,
            "provider_id": clean_provider,
            "image_keys": clean_image_keys,
            "image_aliases": clean_aliases,
            "image_count": clean_count,
            "scope": clean_scope,
            "prompt_sig": prompt_sig,
            "created_ts": _now_ts(),
            "last_hit_ts": 0,
            "hits": 0,
        }
        if isinstance(preview, dict) and preview.get("preview_path"):
            item.update(
                {
                    "preview_path": _single_line(preview.get("preview_path"), 260),
                    "preview_width": _safe_int(preview.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(preview.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(preview.get("preview_size"), 0, 0),
                }
            )
            if existing_preview_path and existing_preview_path != item["preview_path"]:
                self._remove_private_image_cache_preview_file(existing_preview_path)
        elif isinstance(existing_item, dict) and existing_preview_path:
            item.update(
                {
                    "preview_path": existing_preview_path,
                    "preview_width": _safe_int(existing_item.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(existing_item.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(existing_item.get("preview_size"), 0, 0),
                }
            )
        cache[cache_key] = item
        if removed_variants:
            self._record_cache_metric(f"image_vision:{scope}", hit=True, detail=f"dedupe:{removed_variants}")
        max_items = int(getattr(self, "private_image_vision_cache_max_items", 300) or 0)
        if max_items > 0 and len(cache) > max_items:
            stale = sorted(
                cache.items(),
                key=lambda item: (
                    _safe_int((item[1] if isinstance(item[1], dict) else {}).get("hits"), 0, 0),
                    _safe_float((item[1] if isinstance(item[1], dict) else {}).get("last_hit_ts"), 0)
                    or _safe_float((item[1] if isinstance(item[1], dict) else {}).get("created_ts"), 0),
                ),
            )
            evicted = 0
            for key, _ in stale[: max(1, len(cache) - max_items)]:
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    self._remove_private_image_cache_preview_file(_single_line(removed.get("preview_path"), 260))
                evicted += 1
            if evicted:
                self._record_cache_metric(f"image_vision:{scope}", hit=False, detail=f"evict:{evicted}")
        try:
            self._save_data_sync()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 私聊图片视觉缓存保存失败: %s", exc)

    def _invalidate_private_image_vision_cache_by_image_keys(self, image_keys: list[str], *, image_aliases: list[str] | None = None, reason: str = "") -> int:
        targets = {str(item) for item in image_keys or [] if str(item or "").strip()}
        alias_targets = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        if not targets and not alias_targets:
            return 0
        cache = self._private_image_vision_cache_store()
        removed = 0
        for key, item in list(cache.items()):
            if not isinstance(item, dict):
                continue
            cached_keys = {str(value) for value in item.get("image_keys", []) if str(value or "").strip()}
            cached_aliases = {str(value).strip() for value in item.get("image_aliases", []) if str(value or "").strip()}
            if (cached_keys & targets) or (cached_aliases & alias_targets):
                cache.pop(key, None)
                removed += 1
        if removed:
            logger.info("[PrivateCompanion] 私聊图片视觉缓存已因负反馈失效: removed=%s reason=%s", removed, _single_line(reason, 120))
            try:
                self._save_data_sync()
            except Exception as exc:
                logger.debug("[PrivateCompanion] 私聊图片视觉缓存失效保存失败: %s", exc)
        return removed

    def _is_private_image_vision_negative_feedback(self, text: str) -> bool:
        cleaned = _single_line(text, 160)
        if not cleaned:
            return False
        negative_patterns = (
            r"(识别|看|理解|读|认).{0,8}(错|不对|不准|偏了|歪了)",
            r"(不是|不对|错了).{0,12}(这个意思|这样|这意思|你说的|图里|图片|表情包)",
            r"(你|bot|机器人).{0,8}(看错|认错|理解错|识别错)",
            r"(不是.{0,8}你|不是.{0,8}bot|不是.{0,8}本人|不是.{0,8}这个)",
        )
        return any(re.search(pattern, cleaned, flags=re.I) for pattern in negative_patterns)

    def _apply_private_image_vision_negative_feedback(self, user: dict[str, Any], text: str) -> bool:
        if not self._is_private_image_vision_negative_feedback(text):
            return False
        target = user.get("last_private_image_vision_feedback_target")
        if not isinstance(target, dict):
            return False
        ts = _safe_float(target.get("ts"), 0)
        if ts <= 0 or _now_ts() - ts > 180:
            return False
        image_keys = [str(item) for item in target.get("image_keys", []) if str(item or "").strip()]
        image_aliases = [str(item) for item in target.get("image_aliases", []) if str(item or "").strip()]
        removed = self._invalidate_private_image_vision_cache_by_image_keys(image_keys, image_aliases=image_aliases, reason=text)
        target["negative_feedback_ts"] = _now_ts()
        target["negative_feedback_text"] = _single_line(text, 160)
        target["invalidated_cache_items"] = removed
        logger.info(
            "[PrivateCompanion] 私聊图片视觉负反馈记录: user_image_keys=%s removed=%s text=%s",
            len(image_keys),
            removed,
            _single_line(text, 120),
        )
        return bool(removed or image_keys)

    def _astrbot_provider_settings_for_umo(self, umo: str = "") -> dict[str, Any]:
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except Exception:
            try:
                cfg = self.context.get_config()
            except Exception:
                cfg = {}
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(provider_settings) if isinstance(provider_settings, dict) else {}

    def _private_image_caption_provider_id(self, umo: str = "") -> tuple[str, str, str]:
        provider_settings = self._astrbot_provider_settings_for_umo(umo)
        astrbot_provider_id = _single_line(provider_settings.get("default_image_caption_provider_id"), 160)
        prompt = str(provider_settings.get("image_caption_prompt") or "").strip()
        if astrbot_provider_id:
            return astrbot_provider_id, "astrbot_image_caption", prompt
        fallback_provider_id = self._task_provider(getattr(self, "plugin_vision_provider_id", ""), self.narration_provider_id)
        if fallback_provider_id:
            return fallback_provider_id, "plugin_vision", prompt
        default_provider_id = self._default_chat_provider_id(umo)
        return default_provider_id, "astrbot_default", prompt

    def _private_image_provider_by_id(self, provider_id: str) -> Any:
        provider_id = _single_line(provider_id, 160)
        if not provider_id:
            return None
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            return None
        try:
            return getter(provider_id)
        except Exception:
            return None

    def _private_image_visual_provider_candidates(self, umo: str = "") -> list[tuple[str, str, str]]:
        provider_settings = self._astrbot_provider_settings_for_umo(umo)
        prompt = str(provider_settings.get("image_caption_prompt") or "").strip()
        return [
            (_single_line(provider_settings.get("default_image_caption_provider_id"), 160), "astrbot_image_caption", prompt),
            (self._task_provider(getattr(self, "plugin_vision_provider_id", ""), self.narration_provider_id), "plugin_vision", prompt),
            (self._task_provider(self.llm_provider_id), "plugin_main", prompt),
            (self._default_chat_provider_id(umo), "astrbot_default", prompt),
        ]

    def _select_private_image_visual_provider(self, umo: str = "") -> tuple[str, str, str, Any]:
        seen: set[str] = set()
        for provider_id, provider_source, prompt in self._private_image_visual_provider_candidates(umo):
            provider_id = _single_line(provider_id, 160)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if self._private_image_provider_in_failure_cooldown(provider_id, provider_source):
                continue
            provider = self._private_image_provider_by_id(provider_id)
            if provider is not None and self._provider_supports_image(provider):
                return provider_id, provider_source, prompt, provider
        return "", "", "", None

    def _has_private_image_visual_provider(self, umo: str = "") -> bool:
        provider_id, _provider_source, _prompt, provider = self._select_private_image_visual_provider(umo)
        return bool(provider_id and provider is not None)

    def _private_image_provider_failure_cache(self) -> dict[str, Any]:
        cache = getattr(self, "_private_image_provider_failures", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_private_image_provider_failures", cache)
            except Exception:
                return {}
        return cache

    def _private_image_provider_failure_key(self, provider_id: str, provider_source: str = "") -> str:
        return f"{_single_line(provider_source, 80)}:{_single_line(provider_id, 160)}"

    def _private_image_provider_in_failure_cooldown(self, provider_id: str, provider_source: str = "") -> bool:
        key = self._private_image_provider_failure_key(provider_id, provider_source)
        item = self._private_image_provider_failure_cache().get(key)
        if not isinstance(item, dict):
            return False
        until = _safe_float(item.get("until"), 0)
        if until <= _now_ts():
            self._private_image_provider_failure_cache().pop(key, None)
            return False
        return True

    def _mark_private_image_provider_failure(self, provider_id: str, provider_source: str, exc: Exception | str, *, task: str) -> None:
        key = self._private_image_provider_failure_key(provider_id, provider_source)
        cooldown = 300.0
        self._private_image_provider_failure_cache()[key] = {
            "until": _now_ts() + cooldown,
            "provider_id": _single_line(provider_id, 160),
            "source": _single_line(provider_source, 80),
            "task": _single_line(task, 80),
            "error": _single_line(exc, 180),
        }
        logger.info(
            "[PrivateCompanion] 图片视觉 provider 临时降权: provider=%s source=%s task=%s cooldown=%ss error=%s",
            provider_id,
            provider_source,
            task,
            int(cooldown),
            _single_line(exc, 160),
        )

    def _clear_private_image_provider_failure(self, provider_id: str, provider_source: str = "") -> None:
        self._private_image_provider_failure_cache().pop(
            self._private_image_provider_failure_key(provider_id, provider_source),
            None,
        )

    def _private_image_provider_timeout_seconds(self) -> float:
        configured = _safe_float(getattr(self, "private_image_provider_timeout_seconds", 12.0), 12.0, 3.0)
        wait_budget = _safe_float(getattr(self, "private_image_vision_wait_seconds", 30.0), 30.0, 0.0)
        if wait_budget > 0:
            configured = min(configured, max(3.0, wait_budget))
        return max(3.0, configured)

    def _private_image_model_image_items(self, image_sources: list[str]) -> list[tuple[str, str]]:
        items, _source_count, _has_gif_frames = self._private_image_model_image_items_with_meta(image_sources)
        return items

    def _private_image_model_image_items_with_meta(self, image_sources: list[str]) -> tuple[list[tuple[str, str]], int, bool]:
        image_items: list[tuple[str, str]] = []
        seen_image_keys: set[str] = set()
        gif_enhancement_enabled = bool(getattr(self, "enable_private_image_gif_enhancement", True))
        gif_max_frames = max(1, min(8, int(getattr(self, "private_image_gif_max_frames", 4) or 4)))
        sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]
        max_model_images = max(len(sources), max(8, min(16, len(sources) * 2)))
        pending_gif_frames: list[list[tuple[str, str]]] = []
        had_gif_frames = False

        def append_item(item: tuple[str, str]) -> bool:
            frame_key, frame_url = item
            if not frame_key or frame_key in seen_image_keys:
                return False
            seen_image_keys.add(frame_key)
            image_items.append((frame_key, frame_url))
            return True

        for source in sources:
            image_key = self._private_image_source_cache_key(source)
            gif_items = self._private_image_gif_frame_model_items(source, image_key, max_frames=gif_max_frames) if gif_enhancement_enabled else []
            if gif_items:
                had_gif_frames = True
                if append_item(gif_items[0]) and len(gif_items) > 1:
                    pending_gif_frames.append(gif_items[1:])
                if len(image_items) >= max_model_images:
                    break
                continue
            url = self._private_image_source_to_model_url(source)
            if not url:
                continue
            image_key = image_key or ("model_url:" + hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest())
            append_item((image_key, url))
            if len(image_items) >= max_model_images:
                break
        while pending_gif_frames and len(image_items) < max_model_images:
            progressed = False
            next_round: list[list[tuple[str, str]]] = []
            for frames in pending_gif_frames:
                if not frames:
                    continue
                if len(image_items) >= max_model_images:
                    next_round.append(frames)
                    continue
                if append_item(frames[0]):
                    progressed = True
                if len(frames) > 1:
                    next_round.append(frames[1:])
            if not progressed:
                break
            pending_gif_frames = next_round
        return image_items, len(sources), bool(had_gif_frames)

    def _private_image_gif_frame_model_items(self, source: str, image_key: str, *, max_frames: int = 4) -> list[tuple[str, str]]:
        raw = self._private_image_source_bytes_if_gif(source)
        if not raw:
            return []
        image_key = image_key or ("gif:" + hashlib.sha256(raw).hexdigest())
        try:
            from PIL import Image as PILImage, ImageSequence
        except Exception:
            logger.debug("[PrivateCompanion] Pillow 不可用,动态 GIF 将按原图交给视觉模型")
            return []
        try:
            with PILImage.open(io.BytesIO(raw)) as image:
                frame_total = getattr(image, "n_frames", 1) or 1
                is_animated = bool(getattr(image, "is_animated", False) or frame_total > 1)
                if not is_animated:
                    return []
                indices = self._private_image_sample_gif_frame_indices(frame_total, max_frames=max_frames)
                frames: list[tuple[str, str]] = []
                seen_hashes: set[str] = set()
                for index, frame in enumerate(ImageSequence.Iterator(image)):
                    if index not in indices:
                        continue
                    rgba = frame.convert("RGBA")
                    output = io.BytesIO()
                    rgba.save(output, format="PNG")
                    payload = output.getvalue()
                    frame_hash = hashlib.sha1(payload).hexdigest()
                    if frame_hash in seen_hashes:
                        continue
                    seen_hashes.add(frame_hash)
                    key = f"gifframe:v1:{image_key}:f{index}:n{frame_total}:{frame_hash[:12]}"
                    url = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                    frames.append((key, url))
                    if len(frames) >= max_frames:
                        break
                if not frames:
                    return []
                logger.info("[PrivateCompanion] 动态 GIF 已抽帧供视觉识别: frames=%s/%s source=%s", len(frames), frame_total, _single_line(source, 120))
                return frames
        except Exception as exc:
            logger.debug("[PrivateCompanion] 动态 GIF 抽帧失败: %s", exc)
        return []

    @staticmethod
    def _private_image_sample_gif_frame_indices(frame_total: int, *, max_frames: int = 4) -> set[int]:
        total = max(1, int(frame_total or 1))
        limit = max(1, int(max_frames or 4))
        if total <= limit:
            return set(range(total))
        if limit == 1:
            anchors = [total // 2]
        elif limit == 2:
            anchors = [0, total - 1]
        elif limit == 3:
            anchors = [0, total // 2, total - 1]
        else:
            anchors = [0, total // 3, (total * 2) // 3, total - 1]
        result: list[int] = []
        for item in anchors:
            index = max(0, min(total - 1, int(item)))
            if index not in result:
                result.append(index)
            if len(result) >= limit:
                break
        return set(result)

    def _private_image_source_bytes_if_gif(self, source: str) -> bytes:
        text = str(source or "").strip()
        if not text:
            return b""
        try:
            raw = b""
            if text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                if "gif" not in meta.lower():
                    return b""
                raw = base64.b64decode(payload, validate=False) if ";base64" in meta.lower() else payload.encode("utf-8", errors="ignore")
            elif text.startswith("base64://"):
                raw = base64.b64decode(text[len("base64://"):], validate=False)
            else:
                if text.startswith("file://"):
                    text = text[len("file://"):]
                path = Path(text)
                if not path.exists() or not path.is_file():
                    return b""
                if path.suffix.lower() != ".gif":
                    head = path.read_bytes()[:6]
                    return b"" if not head.startswith((b"GIF87a", b"GIF89a")) else path.read_bytes()
                raw = path.read_bytes()
            return raw if raw.startswith((b"GIF87a", b"GIF89a")) else b""
        except Exception as exc:
            logger.debug("[PrivateCompanion] 动态 GIF 字节读取失败: %s", exc)
            return b""

    def _private_image_sources_include_gif(self, image_sources: list[str]) -> bool:
        for source in [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]:
            if self._private_image_source_bytes_if_gif(source):
                return True
        return False

    @staticmethod
    def _provider_supports_image(provider: Any) -> bool:
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        modalities = config.get("modalities") if isinstance(config, dict) else None
        return isinstance(modalities, list) and "image" in modalities

    def _event_main_provider_supports_image(self, event: AstrMessageEvent) -> bool:
        provider = None
        try:
            selected = _single_line(event.get_extra("selected_provider"), 160)
        except Exception:
            selected = ""
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            image_caption_provider_id = _single_line(
                self._astrbot_provider_settings_for_umo(umo).get("default_image_caption_provider_id"),
                160,
            )
        except Exception:
            image_caption_provider_id = ""
        if selected and image_caption_provider_id and selected == image_caption_provider_id:
            logger.info(
                "[PrivateCompanion] 私聊图片 selected_provider 是图片转述模型,不按主视觉模型直挂: provider=%s",
                selected,
            )
            selected = ""
        getter = getattr(self.context, "get_provider_by_id", None)
        if selected and callable(getter):
            try:
                provider = getter(str(selected))
            except Exception:
                provider = None
        if provider is None:
            get_using = getattr(self.context, "get_using_provider", None)
            if callable(get_using):
                try:
                    provider = get_using(umo=getattr(event, "unified_msg_origin", ""))
                except TypeError:
                    try:
                        provider = get_using(getattr(event, "unified_msg_origin", ""))
                    except Exception:
                        provider = None
                except Exception:
                    provider = None
        return self._provider_supports_image(provider)

    @staticmethod
    def _exception_indicates_image_input_unsupported(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return bool(
            "image_url" in text
            and (
                "do not support image" in text
                or "not support image" in text
                or "image input" in text
                or "invalidparameter" in text
            )
        )

    @staticmethod
    def _exception_indicates_tool_schema_invalid(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return bool(
            ("functiondeclaration" in text or "function declaration" in text)
            and ("schema" in text and "type" in text)
        )

    @staticmethod
    def _private_image_reply_is_internal_error(text: str) -> bool:
        lowered = str(text or "").lower()
        if not lowered:
            return False
        markers = (
            "all chat models failed",
            "badrequesterror",
            "provider api error",
            "invalid_request",
            "functiondeclaration",
            "schema didn't specify",
            "traceback",
        )
        return any(marker in lowered for marker in markers)

    def _private_image_role_self_recognition_hint(self) -> str:
        raw = str(getattr(self, "private_image_self_recognition_hint", "") or "")
        if not raw.strip():
            return ""
        user_labels = (
            "对用户的称呼", "用户性别", "用户生日", "用户年龄", "用户职业",
            "是角色的XX", "与角色的相处方式", "与用户关系", "相处边界",
        )
        kept: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(stripped.startswith(f"{label}：") or stripped.startswith(f"{label}:") for label in user_labels):
                continue
            kept.append(stripped)
        return _single_line("\n".join(kept), 900)

    def _private_image_default_persona_prompt(self) -> str:
        getter = getattr(self, "_get_default_persona_prompt", None)
        if not callable(getter):
            return ""
        try:
            return str(getter() or "")
        except Exception:
            return ""

    def _private_image_self_recognition_prompt(self) -> str:
        if not bool(getattr(self, "enable_private_image_self_recognition", True)):
            return ""
        context_prompt = self._private_image_self_recognition_context_prompt()
        if not context_prompt:
            return ""
        return (
            f"{context_prompt}\n"
            "只在最后一行输出归属标签：图像归属判断：疑似当前角色/非当前角色/无法判断。"
        )

    def _private_image_self_recognition_context_prompt(self) -> str:
        if not bool(getattr(self, "enable_private_image_self_recognition", True)):
            return ""
        bot_name = _single_line(getattr(self, "bot_name", ""), 40)
        default_persona = self._private_image_default_persona_prompt()
        schedule_persona = str(getattr(self, "schedule_persona_prompt", "") or "")
        custom_hint = self._private_image_role_self_recognition_hint()
        visual_profile_parts = self._private_image_visual_profile_parts(default_persona, schedule_persona)
        visual_profile = "\n".join(visual_profile_parts)
        parts = [
            f"当前角色名称/可能出现在图中的名字：{bot_name}" if bot_name else "",
            f"角色外观线索：\n{visual_profile}" if visual_profile else "",
            f"额外角色自我识别线索：{custom_hint}" if custom_hint else "",
        ]
        context = "\n".join(part for part in parts if part)
        if not context:
            return ""
        return (
            "【角色识别线索】\n"
            "以下只用于给图片归属打三档标签,不要展开推理,不要复述规则。当前角色不是发图用户。\n"
            "“疑似当前角色”包括当前角色本人、头像、Q版、二创、表情包、聊天截图等,但必须命中核心外观或名字锚点。\n"
            "核心发型、发色、瞳色、物种或标志性服饰明显冲突时,标为“非当前角色”或“无法判断”。\n"
            "视觉锚点过少、只能泛泛说可爱/少女/二次元时,标为“无法判断”；明显无关人物/物品时,标为“非当前角色”。\n"
            f"{context}\n"
        )

    def _private_image_visual_profile_parts(self, default_persona: str = "", schedule_persona: str = "") -> list[str]:
        labels = (
            "姓名", "年龄", "生日", "性别", "识别点", "视觉特征", "外貌", "外观", "形象",
            "发型发色", "发型", "发色", "瞳色", "眼睛", "服饰风格", "服装", "衣着", "种族", "职业/身份",
        )
        parts: list[str] = []
        seen: set[str] = set()

        def add(line: str) -> None:
            item = _single_line(line, 220)
            key = re.sub(r"\s+", "", item)
            if item and key not in seen:
                seen.add(key)
                parts.append(item)

        for source_name, source in (("AstrBot人格", default_persona), ("日程角色设定", schedule_persona)):
            text = str(source or "")
            if not text.strip():
                continue
            for label in labels:
                value = self._roleplay_labeled_value(text, label)
                if value:
                    add(f"{source_name}{label}：{value}")
            freeform = self._private_image_freeform_visual_clues(text)
            if freeform:
                add(f"{source_name}外貌摘录：{freeform}")
        return parts[:12]

    def _private_image_freeform_visual_clues(self, text: str) -> str:
        source = str(text or "")
        if not source.strip():
            return ""
        visual_tokens = (
            "外貌", "长相", "发型", "头发", "发色", "瞳色", "眼睛", "眼眸", "服饰", "穿着", "衣服",
            "校服", "制服", "裙", "外套", "帽", "角", "耳朵", "尾巴", "翅膀", "光环", "纹身", "标志",
            "银发", "白发", "黑发", "金发", "蓝发", "粉发", "紫发", "红发", "绿发", "短发", "长发", "双马尾",
        )
        user_tokens = ("用户", "主人", "对方", "称呼", "关系", "相处", "职业")
        snippets: list[str] = []
        seen: set[str] = set()
        for raw in re.split(r"[\r\n。；;]+", source):
            line = _single_line(raw, 180)
            if not line or any(token in line for token in user_tokens):
                continue
            if any(token in line for token in visual_tokens):
                key = re.sub(r"\s+", "", line)
                if key not in seen:
                    seen.add(key)
                    snippets.append(line)
            if len(snippets) >= 4:
                break
        return _single_line("；".join(snippets), 500)

    def _roleplay_labeled_value(self, text: str, label: str, *, limit: int = 180) -> str:
        source = str(text or "")
        if not source or not label:
            return ""
        label_pattern = re.escape(str(label))
        known_labels = (
            "姓名", "种族", "年龄", "生日", "性别", "识别点", "视觉特征", "外貌", "外观", "形象",
            "发型发色", "发型", "发色", "瞳色", "眼睛", "服饰风格", "服装", "衣着",
            "职业/身份", "身份", "性格描述", "核心欲望/目标", "爱好", "禁忌",
            "关键设定", "其他补充信息", "所处世界", "所在世界", "时代背景",
            "基本法则/基调", "特殊规则", "主要活动场景", "世界观关系网",
            "对用户的称呼", "用户性别", "用户生日", "用户职业", "是角色的XX", "与角色的相处方式",
        )
        stop_pattern = "|".join(re.escape(item) for item in known_labels if item != label)
        match = re.search(
            rf"(?m)^\s*{label_pattern}\s*[：:]\s*(.*?)(?=^\s*(?:{stop_pattern})\s*[：:]|\Z)",
            source,
            flags=re.S,
        )
        if not match:
            return ""
        return _single_line(match.group(1), limit)

    def _private_image_identity_disambiguation_instruction(self) -> str:
        return (
            "归属判断只作辅助：当前角色/Bot 指回复者，用户指发图者。"
            "优先回应用户借图表达的意思；只有用户问归属或梗依赖身份时再轻带自我关联。"
        )

    def _private_image_intent_line(self, text: str) -> str:
        segment = self._private_image_labeled_segment(text, "图像表达意图")
        if segment:
            return segment
        for raw_line in str(text or "").replace("；", "\n").replace("。", "\n").splitlines():
            line = _single_line(raw_line, 220)
            if "图像表达意图" in line or "表达意图" in line:
                return line
        return ""

    def _private_image_ownership_line(self, text: str) -> str:
        segment = self._private_image_labeled_segment(text, "图像归属判断")
        if segment:
            return segment
        for raw_line in str(text or "").replace("；", "\n").replace("。", "\n").splitlines():
            line = _single_line(raw_line, 180)
            if "图像归属判断" in line or "归属判断" in line:
                return line
        return ""

    def _private_image_role_visual_text(self) -> str:
        default_persona = self._private_image_default_persona_prompt()
        schedule_persona = str(getattr(self, "schedule_persona_prompt", "") or "")
        custom_hint = self._private_image_role_self_recognition_hint()
        parts = self._private_image_visual_profile_parts(default_persona, schedule_persona)
        if custom_hint:
            parts.append(custom_hint)
        return _single_line("\n".join(parts), 900)

    def _private_image_direct_role_appearance_prompt(self) -> str:
        lines: list[str] = []
        bot_name = _single_line(getattr(self, "bot_name", ""), 40)
        visual_text = _single_line(self._private_image_role_visual_text(), 520)
        visual_text = re.sub(r"(?:AstrBot人格|日程角色设定)", "", visual_text)
        if bot_name:
            lines.append(f"角色名：{bot_name}")
        if visual_text:
            lines.append(f"外貌线索：{visual_text}")
        if not lines:
            return ""
        lines.append("用途：仅辅助本轮图片识别，避免把无关人物或表情包误认成当前角色；不代表用户正在询问外貌。")
        return "【当前角色外貌】\n" + "\n".join(lines)

    def _private_image_role_visual_cache_signature(self) -> str:
        role_text = re.sub(r"\s+", "", self._private_image_role_visual_text())
        if not role_text:
            return ""
        anchors = (
            "短发", "长发", "双马尾", "马尾", "麻花辫", "辫子", "卷发", "直发",
            "黑发", "白发", "银发", "金发", "黄发", "蓝发", "紫发", "红发", "粉发", "棕发", "绿发", "灰发",
            "黑髮", "白髮", "銀髮", "金髮", "藍髮", "紫髮", "紅髮", "粉髮", "棕髮", "綠髮", "灰髮",
            "黑瞳", "蓝瞳", "紫瞳", "红瞳", "金瞳", "绿瞳", "异色瞳",
            "兽耳", "猫耳", "狐耳", "角", "尾巴", "翅膀", "光环", "眼镜", "校服", "制服", "女仆装",
        )
        found = [anchor for anchor in anchors if anchor in role_text]
        name = re.sub(r"\s+", "", _single_line(getattr(self, "bot_name", ""), 40))
        raw = "|".join([name, *found])
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12] if raw.strip("|") else ""

    @staticmethod
    def _private_image_has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token and token in text for token in tokens)

    def _private_image_ownership_conflict_reason(self, vision_text: str) -> str:
        ownership = self._private_image_ownership_kind(self._private_image_ownership_line(vision_text))
        if ownership not in {"bot_self", "bot_sticker", "bot_chat"}:
            return ""
        role = re.sub(r"\s+", "", self._private_image_role_visual_text())
        visible = re.sub(r"\s+", "", self._private_image_visible_line(vision_text) or vision_text)
        if not role or not visible:
            return ""
        if "短发" in role and self._private_image_has_any_token(
            visible,
            ("长发", "双马尾", "雙馬尾", "马尾", "馬尾", "麻花辫", "辫子", "辮子"),
        ):
            return "发型冲突：角色线索为短发,图片主体为长发/马尾类发型"
        if self._private_image_has_any_token(role, ("长发", "長髮", "长髮")) and "短发" in visible:
            return "发型冲突：角色线索为长发,图片主体为短发"
        hair_colors = ("黑", "白", "银", "金", "黄", "蓝", "紫", "红", "粉", "棕", "绿", "灰")
        role_hair = {color for color in hair_colors if f"{color}发" in role or f"{color}髮" in role}
        visible_hair = {color for color in hair_colors if f"{color}发" in visible or f"{color}髮" in visible}
        if role_hair and visible_hair and role_hair.isdisjoint(visible_hair):
            return f"发色冲突：角色线索={','.join(sorted(role_hair))} 图片主体={','.join(sorted(visible_hair))}"
        return ""

    def _private_image_downgrade_conflicting_ownership(self, vision_text: str) -> str:
        text = _single_line(vision_text, 1400)
        reason = self._private_image_ownership_conflict_reason(text)
        if not reason:
            return text
        old_line = self._private_image_ownership_line(text)
        new_line = "图像归属判断：无法判断"
        if old_line and old_line in text:
            corrected = text.replace(old_line, new_line, 1)
        else:
            corrected = f"{text} {new_line}".strip()
        logger.info(
            "[PrivateCompanion] 图片归属自我识别因外观冲突降级: reason=%s before=%s after=%s",
            _single_line(reason, 120),
            old_line or "无",
            new_line,
        )
        return corrected

    def _private_image_visible_line(self, text: str) -> str:
        segment = self._private_image_labeled_segment(text, "可见内容")
        if segment:
            return segment
        for raw_line in str(text or "").replace("；", "\n").replace("。", "\n").splitlines():
            line = _single_line(raw_line, 260)
            if "可见内容" in line:
                return line
        return ""

    @staticmethod
    def _private_image_labeled_segment(text: str, label: str) -> str:
        source = _single_line(text, 1400)
        if not source or not label:
            return ""
        next_labels = ("图片类型", "可见内容", "图像表达意图", "图像归属判断")
        starts = [source.find(f"{label}："), source.find(f"{label}:")]
        starts = [idx for idx in starts if idx >= 0]
        if not starts:
            return ""
        start = min(starts)
        colon_idx = source.find("：", start)
        ascii_colon_idx = source.find(":", start)
        colon_candidates = [idx for idx in (colon_idx, ascii_colon_idx) if idx >= 0]
        if not colon_candidates:
            return ""
        value_start = min(colon_candidates) + 1
        value_end = len(source)
        for next_label in next_labels:
            if next_label == label:
                continue
            for marker in (f" {next_label}：", f" {next_label}:", f"{next_label}：", f"{next_label}:"):
                idx = source.find(marker, value_start)
                if idx >= 0:
                    value_end = min(value_end, idx)
        value = _single_line(source[value_start:value_end], 160)
        return f"{label}：{value}" if value else ""

    def _private_image_ownership_kind(self, ownership_line: str) -> str:
        compact = re.sub(r"\s+", "", str(ownership_line or "")).lower()
        if re.search(r"\d+=", compact):
            return "mixed"
        if "非当前角色" in compact or "不是当前角色" in compact:
            return "unrelated"
        if "疑似当前角色" in compact:
            return "bot_self"
        if "当前角色的表情包" in compact or "bot的表情包" in compact:
            return "bot_sticker"
        if "当前角色的聊天截图" in compact or "bot的聊天截图" in compact:
            return "bot_chat"
        if "当前角色自己" in compact or "当前回复角色自己" in compact or "bot自己" in compact:
            return "bot_self"
        if "发图用户本人" in compact or "用户本人" in compact:
            return "user_self"
        if "用户发来的无关图片" in compact:
            return "unrelated"
        if "无法判断" in compact:
            return "unknown"
        return ""

    def _private_image_type_line(self, text: str) -> str:
        segment = self._private_image_labeled_segment(text, "图片类型")
        if segment:
            return segment
        for raw_line in str(text or "").replace("；", "\n").replace("。", "\n").splitlines():
            line = _single_line(raw_line, 120)
            if "图片类型" in line:
                return line
        return ""

    def _private_image_type_kind(self, text: str) -> str:
        compact = re.sub(r"\s+", "", str(self._private_image_type_line(text) or text or "")).lower()
        if any(token in compact for token in ("表情包", "贴纸", "sticker", "emoji", "gif", "动图")):
            return "sticker"
        if "聊天记录" in compact or "聊天截图" in compact:
            return "chat"
        if "截图" in compact:
            return "screenshot"
        if "漫画" in compact:
            return "manga"
        if "照片" in compact or "photo" in compact:
            return "photo"
        return ""

    @staticmethod
    def _private_image_user_asks_content(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        patterns = (
            "图里是什么", "图里有啥", "图里有什么", "图片里是什么", "图片里有啥", "图片里有什么",
            "这图是什么", "这个图是什么", "这张图是什么", "这是啥", "这是什么", "什么内容",
            "看到了什么", "你看到了什么", "画了什么", "写了什么", "什么意思",
        )
        return any(item in compact for item in patterns)

    def _private_image_reply_objective(self, ownership_line: str, vision_text: str = "", user_text: str = "") -> str:
        kind = self._private_image_ownership_kind(ownership_line)
        image_kind = self._private_image_type_kind(vision_text)
        asks_content = self._private_image_user_asks_content(user_text)
        if asks_content:
            return (
                "回复目标：用户在问图片内容。先概括可见内容，再接住表达意图；"
                "不确定就说不确定，不要套历史图。"
            )
        if image_kind == "sticker":
            return (
                "回复目标：按表情包/贴纸/GIF 接住情绪、动作变化、文字梗或调侃点；短句自然回复。"
            )
        if image_kind in {"photo", "screenshot", "manga", "chat"}:
            return (
                "回复目标：按普通图片/截图/漫画/聊天记录处理。先看主体、文字和场景，再回应用户的疑问、吐槽或分享意图。"
            )
        if kind in {"bot_self", "bot_sticker", "bot_chat"}:
            return (
                "回复目标：直接回应用户这次借图调侃、吐槽、撒娇或分享的意思；"
                "归属指向当前角色时只作语气辅助，不要主动把重点放在认自己。"
            )
        if kind == "user_self":
            return (
                "回复目标：回应用户借图表达的意思；归属指向用户本人时，不要说成当前角色自己。"
            )
        return (
            "回复目标：优先回应用户借图表达的意思；归属不明时不要强行认定。"
        )

    def _private_image_user_has_specific_vision_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        request_tokens = (
            "看清", "仔细看", "放大", "左上", "左下", "右上", "右下", "中间", "背景", "文字", "台词",
            "写了什么", "写的啥", "几个人", "几个", "是谁", "像谁", "是不是", "有没有", "哪里", "哪儿",
            "识别", "判断", "分析", "帮我看", "图里", "截图里", "画面里", "细节", "表情", "动作",
        )
        return any(token in compact for token in request_tokens)

    def _private_image_user_mentions_combo_result(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        combo_tokens = (
            "赛博老虎机", "老虎机", "抽签", "抽卡", "组合结果", "这组", "这一组",
            "五张", "5张", "结果", "今日份", "天意",
        )
        return any(token in compact for token in combo_tokens)

    @staticmethod
    def _private_image_vision_text_limit(image_count: int = 1) -> int:
        count = max(1, int(image_count or 1))
        if count <= 1:
            return 600
        return min(1400, 600 + count * 160)

    def _private_image_query_prompt_suffix(self, user_text: str) -> str:
        user_text = _single_line(user_text, 240)
        if not user_text:
            return ""
        return (
            "\n\n【本轮用户看图要求】\n"
            f"用户这次带着新的具体要求问这张图：{user_text}\n"
            "请在 4 行摘要里优先补足与这个要求直接相关的可见细节；"
            "如果用户要求识别文字、数量、位置、人物、动作、表情或截图内容,必须在“可见内容”中回答到这些点。"
            "不知道就写无法判断,不要用旧摘要概括带过。"
        )

    def _private_image_vision_cache_prompt_signature(self, base_prompt: str, user_text: str = "", *, contextual: bool = False) -> str:
        """Keep cache keys stable when prompt wording changes, but refresh on core appearance changes."""
        role_sig = self._private_image_role_visual_cache_signature()
        return (
            "private_image_vision_v4|"
            f"contextual={1 if contextual else 0}|"
            f"base={hashlib.sha1(str(base_prompt or '').encode('utf-8', errors='ignore')).hexdigest()[:16]}|"
            f"role={role_sig}|"
            f"user={hashlib.sha1(_single_line(user_text, 240).encode('utf-8', errors='ignore')).hexdigest()[:16] if contextual and user_text else ''}"
        )

    async def _transcribe_private_inbound_images(self, image_sources: list[str], *, umo: str = "", user_text: str = "", force_contextual: bool = False) -> str:
        original_sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]
        try:
            sources = await self._prepare_private_image_sources_for_model(
                original_sources,
                namespace="private_vision",
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if missing:
                logger.warning(
                    "[PrivateCompanion] 私聊图片预处理缺少可选模型依赖，已跳过本轮识图: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                return ""
            raise
        if not sources:
            return ""
        try:
            image_items, source_image_count, has_gif_frames = self._private_image_model_image_items_with_meta(sources)
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if missing:
                logger.warning(
                    "[PrivateCompanion] 私聊图片模型输入构造缺少可选模型依赖，已跳过本轮识图: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                return ""
            raise
        image_keys = [key for key, _ in image_items]
        image_urls = [url for _, url in image_items]
        if not image_urls:
            return ""
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                result = refresher(umo)
                if hasattr(result, "__await__"):
                    await asyncio.wait_for(result, timeout=2.0)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 图片自我识别刷新人格缓存失败: %s", exc)
        image_aliases = self._private_image_cache_aliases_for_sources([*original_sources, *sources])
        original_image_keys = self._private_image_cache_image_keys(original_sources or sources)
        if original_image_keys:
            image_keys = original_image_keys
        image_count = source_image_count or len(original_sources) or len(sources)
        text_limit = self._private_image_vision_text_limit(image_count)
        multi_ownership_hint = (
            "多张图片的归属判断请按序输出在同一行,例如：图像归属判断：1=非当前角色；2=疑似当前角色；3=无法判断。\n"
            if image_count >= 2
            else ""
        )
        combo_hint = (
            "如果用户文本明确把多张图称为抽签、抽卡、老虎机、赛博老虎机或组合结果,请按顺序综合理解这组结果,保留每张图的关键文字并概括最终含义。"
            if image_count >= 2 and self._private_image_user_mentions_combo_result(user_text)
            else "如果用户一次发多张图,请先分别保留每张图的关键可见内容；只有用户文本明确表示它们是一组组合结果时,才合并成一个梗来解读。"
        )
        gif_hint = (
            "如果同一张动态 GIF 被抽成多帧,这些帧属于同一张动图；请按整体动图主体判断归属,不要因某一帧局部相似就误判。\n"
            if has_gif_frames
            else ""
        )
        default_prompt = (
            f"请把用户刚发的 {len(original_sources)} 张图片压缩成给聊天模型看的短摘要。先判断它们更像表情包/贴纸/GIF,还是照片/截图/漫画/聊天记录。"
            "只输出下面 4 行,不要写标题、分析过程、帧列表或长篇描述。\n"
            "图片类型：<照片/截图/漫画/表情包/聊天记录/其他>\n"
            "可见内容：<客观画面主体、文字、动作或最关键细节,125字内；多张图要按顺序保留每张图的关键文字/结果,不要只概括第一张>\n"
            "图像表达意图：<用户可能借图表达的情绪、态度、疑问、分享意图、动作变化或梗,125字内>\n"
            "图像归属判断：<疑似当前角色/非当前角色/无法判断>\n"
            f"{multi_ownership_hint}"
            "完整性规则：这是在原有基础上的增强,不是二选一。任何类型都要保留可见内容和表达意图；"
            "区别只是图片侧多给内容细节,表情包/GIF侧多给情绪、态度和梗点。"
            "使用规则：表情包/贴纸/GIF 的表达意图常来自文字、表情、动作和梗点；普通图片的表达意图常来自用户分享、询问、吐槽或展示的语境。"
            f"{combo_hint}"
            "无法确定就写无法判断；不要为了归属判断反复比较。"
            "如果同一张动态 GIF 被抽成多帧,请按时间顺序综合动作、表情变化和文字变化,不要把它们当成多张无关图片。"
            f"{gif_hint}"
        )
        attempts = 0
        seen: set[str] = set()
        for provider_id, provider_source, _configured_prompt in self._private_image_visual_provider_candidates(umo):
            provider_id = _single_line(provider_id, 160)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if self._private_image_provider_in_failure_cooldown(provider_id, provider_source):
                continue
            provider = self._private_image_provider_by_id(provider_id)
            if provider is None or not self._provider_supports_image(provider):
                continue
            attempts += 1
            contextual = bool(force_contextual or self._private_image_user_has_specific_vision_request(user_text))
            prompt = default_prompt + self._private_image_query_prompt_suffix(user_text if contextual else "")
            self_recognition_prompt = self._private_image_self_recognition_prompt()
            if self_recognition_prompt and self_recognition_prompt not in prompt:
                prompt = f"{prompt}\n\n{self_recognition_prompt}"
            scope = "private_image_query" if contextual else "private_image"
            cache_prompt_sig = self._private_image_vision_cache_prompt_signature(
                default_prompt,
                user_text,
                contextual=contextual,
            )
            cache_key = self._private_image_vision_cache_key(image_keys, provider_id, cache_prompt_sig, scope=scope)
            cached_text = self._get_private_image_vision_cache(
                cache_key,
                provider_id=provider_id,
                image_keys=image_keys,
                image_aliases=image_aliases,
                image_count=image_count,
                scope=scope,
                allow_image_key_fallback=not contextual,
            )
            if cached_text:
                cached_text = self._private_image_downgrade_conflicting_ownership(cached_text)
                intent_line = self._private_image_intent_line(cached_text)
                ownership_line = self._private_image_ownership_line(cached_text)
                logger.info(
                    "[PrivateCompanion] 私聊图片视觉转述命中缓存: provider=%s scope=%s images=%s intent=%s ownership=%s preview=%s",
                    provider_id,
                    scope,
                    len(image_urls),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(cached_text, 220),
                )
                return cached_text
            if not self._can_run_llm_task(provider_id, task="private_image_vision"):
                self._record_llm_budget_skip(provider_id=provider_id, task="private_image_vision", prompt=prompt)
                continue
            try:
                start = time.time()
                attempt_timeout = self._private_image_provider_timeout_seconds()
                result = await asyncio.wait_for(
                    provider.text_chat(prompt=prompt, image_urls=image_urls, max_tokens=min(520, 220 + image_count * 50)),
                    timeout=attempt_timeout,
                )
                text = str(getattr(result, "completion_text", result) or "").strip()
                cleaned_text = _single_line(_strip_internal_message_blocks(text), text_limit)
                cleaned_text = self._private_image_downgrade_conflicting_ownership(cleaned_text)
                intent_line = self._private_image_intent_line(cleaned_text)
                ownership_line = self._private_image_ownership_line(cleaned_text)
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="private_image_vision",
                    prompt=prompt,
                    completion=text,
                    resp=result,
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=True,
                    budget_exempt=True,
                )
                self._clear_private_image_provider_failure(provider_id, provider_source)
                logger.info(
                    "[PrivateCompanion] 私聊图片视觉转述完成: provider=%s source=%s scope=%s images=%s chars=%s intent=%s ownership=%s preview=%s",
                    provider_id,
                    provider_source,
                    scope,
                    len(image_urls),
                    len(text),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(cleaned_text, 220),
                )
                self._set_private_image_vision_cache(
                    cache_key,
                    cleaned_text,
                    provider_id=provider_id,
                    image_keys=image_keys,
                    image_aliases=image_aliases,
                    image_count=image_count,
                    prompt=cache_prompt_sig,
                    scope=scope,
                    preview=self._private_image_cache_preview_from_sources(cache_key, [*original_sources, *sources]),
                )
                return cleaned_text
            except asyncio.TimeoutError:
                elapsed_ms = int((time.time() - start) * 1000) if "start" in locals() else 0
                timeout_note = f"识图单次调用超过 {self._private_image_provider_timeout_seconds():.1f}s"
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="private_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=timeout_note,
                    budget_exempt=True,
                )
                self._mark_private_image_provider_failure(provider_id, provider_source, timeout_note, task="private_image_vision")
                continue
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if missing:
                    logger.warning(
                        "[PrivateCompanion] 私聊图片视觉 provider 缺少可选模型依赖，已降级跳过该 provider: provider=%s module=%s err=%s",
                        provider_id,
                        missing,
                        _single_line(exc, 160),
                    )
                    self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="private_image_vision")
                    continue
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="private_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000) if "start" in locals() else 0,
                    success=False,
                    error=_single_line(exc, 180),
                    budget_exempt=True,
                )
                self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="private_image_vision")
                continue
        logger.info("[PrivateCompanion] 私聊图片视觉转述失败: 所有候选 provider 均不可用或失败 attempts=%s", attempts)
        return ""

    @staticmethod
    def _context_image_placeholder_pattern() -> re.Pattern[str]:
        return re.compile(r"(?:\[图片\]|【图片】)")

    def _context_image_has_placeholder(self, text: Any) -> bool:
        return bool(self._context_image_placeholder_pattern().search(str(text or "")))

    def _context_image_plain_text(self, value: Any, *, depth: int = 0) -> str:
        if value is None or depth > 5:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return "\n".join(self._context_image_plain_text(item, depth=depth + 1) for item in value)
        if isinstance(value, tuple):
            return "\n".join(self._context_image_plain_text(item, depth=depth + 1) for item in value)
        if isinstance(value, dict):
            parts: list[str] = []
            for key in ("content", "text", "message", "value"):
                if key in value:
                    parts.append(self._context_image_plain_text(value.get(key), depth=depth + 1))
            data = value.get("data")
            if isinstance(data, dict):
                for key in ("content", "text", "message", "value"):
                    if key in data:
                        parts.append(self._context_image_plain_text(data.get(key), depth=depth + 1))
            return "\n".join(part for part in parts if part)
        for attr in ("content", "text", "message", "value"):
            current = getattr(value, attr, None)
            if current is not None:
                text = self._context_image_plain_text(current, depth=depth + 1)
                if text:
                    return text
        return ""

    def _context_image_inline_sources(self, value: Any, *, depth: int = 0) -> list[str]:
        if value is None or depth > 5:
            return []
        sources: list[str] = []

        def add(source: Any) -> None:
            text = str(source or "").strip()
            if text and text not in sources:
                sources.append(text)

        if isinstance(value, (list, tuple)):
            for item in value:
                for source in self._context_image_inline_sources(item, depth=depth + 1):
                    add(source)
            return sources
        if isinstance(value, dict):
            type_name = str(value.get("type") or value.get("post_type") or "").strip().lower()
            data = value.get("data") if isinstance(value.get("data"), dict) else value
            if type_name == "image":
                extractor = getattr(self, "_extract_image_url_from_segment_data", None)
                if callable(extractor):
                    try:
                        add(extractor(data))
                    except Exception:
                        pass
                for key in ("url", "origin_url", "source_url", "path", "image_path", "file_path", "local_path", "file"):
                    add(data.get(key) if isinstance(data, dict) else "")
            for key in ("content", "message", "messages", "value", "data"):
                nested = value.get(key)
                if nested is not value:
                    for source in self._context_image_inline_sources(nested, depth=depth + 1):
                        add(source)
            return sources
        type_name = str(getattr(value, "type", "") or value.__class__.__name__).strip().lower()
        if type_name == "image":
            add(self._image_component_source(value))
        for attr in ("content", "message", "messages", "value", "data"):
            nested = getattr(value, attr, None)
            if nested is not None and nested is not value:
                for source in self._context_image_inline_sources(nested, depth=depth + 1):
                    add(source)
        return sources

    def _replace_context_image_placeholder(self, value: Any, replacement: str, *, depth: int = 0) -> tuple[Any, bool]:
        if value is None or depth > 5:
            return value, False
        pattern = self._context_image_placeholder_pattern()
        if isinstance(value, str):
            inserted = False

            def repl(_match: re.Match[str]) -> str:
                nonlocal inserted
                if inserted:
                    return ""
                inserted = True
                return replacement

            updated = pattern.sub(repl, value)
            updated = re.sub(r"[ \t]{2,}", " ", updated).strip()
            return updated, updated != value
        if isinstance(value, list):
            changed = False
            updated_items: list[Any] = []
            for item in value:
                updated, item_changed = self._replace_context_image_placeholder(item, replacement, depth=depth + 1)
                changed = changed or item_changed
                updated_items.append(updated)
            return updated_items, changed
        if isinstance(value, tuple):
            updated, changed = self._replace_context_image_placeholder(list(value), replacement, depth=depth + 1)
            return tuple(updated), changed
        if isinstance(value, dict):
            changed = False
            updated = dict(value)
            for key in ("content", "text", "message", "value"):
                if key not in updated:
                    continue
                new_value, item_changed = self._replace_context_image_placeholder(updated.get(key), replacement, depth=depth + 1)
                if item_changed:
                    updated[key] = new_value
                    changed = True
            return updated, changed
        for attr in ("content", "text", "message", "value"):
            current = getattr(value, attr, None)
            if current is None:
                continue
            new_value, changed = self._replace_context_image_placeholder(current, replacement, depth=depth + 1)
            if not changed:
                continue
            try:
                setattr(value, attr, new_value)
            except Exception:
                return value, False
            return value, True
        return value, False

    def _context_image_skip_text(self, text: str) -> bool:
        raw = str(text or "")
        if not raw:
            return True
        if not self._context_image_has_placeholder(raw):
            return True
        skip_markers = (
            "【本轮延迟图片】",
            "【本轮引用图片】",
            "【当前引用图片锚点】",
            "【本轮合并消息】",
            "【本轮合并消息转述】",
            "合并消息中的图片：",
            "不要把摘要里的[图片]当成已看见原图",
            "遇到 [图片]",
            "图片占位",
        )
        return any(marker in raw for marker in skip_markers)

    @staticmethod
    def _context_image_normalize_text(text: str) -> str:
        normalized = re.sub(r"\s+", "", str(text or ""))
        normalized = re.sub(r"^(?:user|assistant|system|用户|机器人|bot|Bot)[:：]", "", normalized)
        return normalized[:1000]

    def _context_image_recall_rows_for_event(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            return []
        cleaner = getattr(self, "_cleanup_recall_message_cache", None)
        if callable(cleaner):
            try:
                cleaner()
            except Exception:
                pass
        try:
            scope = _single_line(self._event_scope_key(event), 160)
        except Exception:
            scope = ""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key, row in list(cache.items()):
            if not isinstance(row, dict):
                continue
            row_scope = _single_line(row.get("scope"), 160)
            if scope and row_scope and row_scope != scope:
                continue
            unique = _single_line(row.get("message_id"), 120) or _single_line(key, 120)
            if unique and unique in seen:
                continue
            text = str(row.get("text") or "")
            image_items = []
            getter = getattr(self, "_recall_image_items_from_snapshot", None)
            if callable(getter):
                try:
                    image_items = getter(row)
                except Exception:
                    image_items = []
            if not image_items and not self._context_image_has_placeholder(text):
                continue
            if unique:
                seen.add(unique)
            rows.append(row)
        rows.sort(key=lambda item: _safe_float(item.get("ts"), 0))
        return rows

    def _context_image_sources_from_recall_row(self, row: dict[str, Any]) -> list[str]:
        items = []
        getter = getattr(self, "_recall_image_items_from_snapshot", None)
        if callable(getter):
            try:
                items = getter(row)
            except Exception:
                items = []
        sources: list[str] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            tier = _single_line(item.get("tier"), 40)
            if source and tier != "placeholder" and source not in sources:
                sources.append(source)
        if sources:
            return sources[:5]
        for source in row.get("images") if isinstance(row.get("images"), list) else []:
            text = str(source or "").strip()
            if text and text not in sources:
                sources.append(text)
        return sources[:5]

    def _match_context_image_recall_row(
        self,
        text: str,
        rows: list[dict[str, Any]],
        used_rows: set[str],
    ) -> dict[str, Any] | None:
        normalized = self._context_image_normalize_text(text)
        if not normalized:
            return None
        for row in rows:
            unique = _single_line(row.get("message_id"), 120)
            if unique and unique in used_rows:
                continue
            row_text = self._context_image_normalize_text(str(row.get("text") or ""))
            if row_text and (row_text in normalized or normalized in row_text):
                return row
        if len(normalized) <= 220:
            for row in rows:
                unique = _single_line(row.get("message_id"), 120)
                if unique and unique in used_rows:
                    continue
                if self._context_image_sources_from_recall_row(row):
                    return row
        return None

    async def _caption_context_image_sources(self, sources: list[str], *, umo: str = "") -> str:
        clean_sources = [str(item).strip() for item in sources if str(item or "").strip()][:5]
        if not clean_sources:
            return ""
        wait_seconds = max(0.0, _safe_float(getattr(self, "context_image_caption_timeout_seconds", 8.0), 8.0, 0.0))
        task = self._transcribe_private_inbound_images(clean_sources, umo=umo)
        try:
            if wait_seconds > 0:
                return _single_line(await asyncio.wait_for(task, timeout=wait_seconds), self._private_image_vision_text_limit(len(clean_sources)))
            return _single_line(await task, self._private_image_vision_text_limit(len(clean_sources)))
        except asyncio.TimeoutError:
            logger.info("[PrivateCompanion] 上下文图片补全等待超时: images=%s timeout=%.1fs", len(clean_sources), wait_seconds)
            return ""
        except Exception as exc:
            logger.info("[PrivateCompanion] 上下文图片补全失败: images=%s error=%s", len(clean_sources), _single_line(exc, 120))
            return ""

    async def _enrich_request_context_image_placeholders(self, event: AstrMessageEvent, req: ProviderRequest) -> dict[str, int]:
        if not bool(getattr(self, "enable_context_image_captioning", True)):
            return {"contexts": 0, "replaced": 0, "missed": 0}
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return {"contexts": 0, "replaced": 0, "missed": 0}
        max_items = max(0, _safe_int(getattr(self, "context_image_caption_max_items", 12), 12, 0, 50))
        if max_items <= 0:
            return {"contexts": len(contexts), "replaced": 0, "missed": 0}

        rows = self._context_image_recall_rows_for_event(event)
        used_rows: set[str] = set()
        caption_cache: dict[tuple[str, ...], str] = {}
        changed = False
        replaced = 0
        missed = 0
        updated_contexts = list(contexts)
        umo = str(getattr(event, "unified_msg_origin", "") or "")

        for index, item in enumerate(contexts):
            if replaced >= max_items:
                break
            text = self._context_image_plain_text(item)
            inline_sources = self._context_image_inline_sources(item)
            has_placeholder = self._context_image_has_placeholder(text)
            if not has_placeholder and not inline_sources:
                continue
            if self._context_image_skip_text(text):
                continue

            sources = inline_sources[:5]
            row = None
            if not sources:
                row = self._match_context_image_recall_row(text, rows, used_rows)
                if row:
                    sources = self._context_image_sources_from_recall_row(row)
            if not sources:
                missed += 1
                continue

            cache_key = tuple(sources)
            caption = caption_cache.get(cache_key, "")
            if not caption:
                caption = await self._caption_context_image_sources(sources, umo=umo)
                if caption:
                    caption_cache[cache_key] = caption
            if not caption:
                missed += 1
                continue

            replacement = f"【图片摘要：{caption}】"
            updated_item, item_changed = self._replace_context_image_placeholder(item, replacement)
            if not item_changed:
                missed += 1
                continue
            updated_contexts[index] = updated_item
            changed = True
            replaced += 1
            if row:
                unique = _single_line(row.get("message_id"), 120)
                if unique:
                    used_rows.add(unique)

        if changed:
            try:
                req.contexts = updated_contexts
            except Exception:
                return {"contexts": len(contexts), "replaced": 0, "missed": missed}
            logger.info(
                "[PrivateCompanion] 已将历史上下文图片占位替换为视觉摘要: contexts=%s replaced=%s missed=%s",
                len(contexts),
                replaced,
                missed,
            )
        return {"contexts": len(contexts), "replaced": replaced, "missed": missed}

    def _message_debounce_seconds(self, kind: str = "text") -> float:
        if not bool(getattr(self, "enable_message_debounce", getattr(self, "enable_semantic_message_debounce", True))):
            return 0.0
        text_wait = _safe_float(getattr(self, "text_message_debounce_seconds", 0.0), 0.0, 0.0)
        if kind == "image":
            return max(0.0, _safe_float(getattr(self, "image_message_debounce_seconds", 8.0), 8.0, 0.0))
        if kind == "forward":
            return max(0.0, _safe_float(getattr(self, "forward_message_debounce_seconds", 0.0), 0.0, 0.0))
        if kind == "group":
            return max(0.0, text_wait)
        return max(0.0, text_wait)

    async def _consume_semantic_message_buffer_for_event(self, event: AstrMessageEvent, *, private_chat: bool) -> str:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return ""
        force_consume = False
        if private_chat:
            if not bool(getattr(self, "enable_message_debounce", getattr(self, "enable_semantic_message_debounce", True))):
                return ""
            scope = f"private:{sender_id}"
            key = self._semantic_buffer_key(scope, sender_id)
        else:
            group_id = self._extract_group_id_from_event(event)
            if not group_id:
                return ""
            scope = f"group:{group_id}"
            high_intensity = getattr(event, "private_companion_group_high_intensity", None)
            buffers = getattr(self, "_semantic_message_buffers", None)
            high_key = self._group_high_intensity_buffer_key(group_id, sender_id)
            legacy_high_key = self._group_high_intensity_buffer_key(group_id)
            active_high_key = high_key
            if (
                isinstance(buffers, dict)
                and not isinstance(buffers.get(active_high_key), dict)
                and isinstance(buffers.get(legacy_high_key), dict)
            ):
                active_high_key = legacy_high_key
            if isinstance(high_intensity, dict) and high_intensity.get("active") and isinstance(buffers, dict) and isinstance(buffers.get(active_high_key), dict):
                key = active_high_key
                force_consume = True
            else:
                key = self._semantic_buffer_key(scope, sender_id)
                if isinstance(buffers, dict) and isinstance(buffers.get(key), dict):
                    buffer_wait = _safe_float(buffers.get(key, {}).get("wait_seconds"), 0.0, 0.0)
                    if buffer_wait <= 0:
                        return ""
                else:
                    wait = self._message_debounce_seconds("group")
                    if wait <= 0:
                        return ""
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            return ""
        buffer = buffers.get(key)
        if not isinstance(buffer, dict):
            return ""
        wait = max(0.0, _safe_float(buffer.get("wait_seconds"), getattr(self, "text_message_debounce_seconds", 0.0), 0.0))
        if wait <= 0:
            return ""
        identity = getattr(self, "_semantic_buffer_identity", None)
        if callable(identity):
            log_scope, log_sender = identity(key)
        else:
            log_scope, log_sender = (key.rsplit(":", 1) + [""])[:2] if ":" in key else (key, "")
        if not log_sender:
            log_sender = sender_id
        buffer_kind = _single_line(buffer.get("kind"), 40) or ("group_high_intensity" if force_consume else "text")
        initial_messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        deadline_ts = _safe_float(buffer.get("deadline_ts"), 0.0, 0.0)
        max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), 0.0, 0.0)
        updated_ts = _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0.0)
        initial_target_ts = deadline_ts if deadline_ts > 0 else updated_ts + wait
        if max_deadline_ts > 0:
            initial_target_ts = min(initial_target_ts, max_deadline_ts)
        already_due = initial_target_ts > 0 and _now_ts() >= initial_target_ts
        logger.info(
            "[PrivateCompanion] 消息收口等待开始: kind=%s scope=%s sender=%s wait=%.1fs count=%s deadline=%s",
            buffer_kind,
            log_scope,
            log_sender,
            wait,
            len(initial_messages),
            "fixed" if deadline_ts > 0 else "sliding",
        )
        deadline_guard = _now_ts() if already_due else deadline_ts if deadline_ts > 0 else _now_ts() + max(wait + 2.0, min(30.0, wait * 3.0 + 2.0))
        while True:
            buffer = buffers.get(key)
            if not isinstance(buffer, dict):
                return ""
            updated_ts = _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), _now_ts())
            deadline_ts = _safe_float(buffer.get("deadline_ts"), deadline_ts, deadline_ts)
            max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), max_deadline_ts, max_deadline_ts)
            target_ts = deadline_ts if deadline_ts > 0 else updated_ts + wait
            if max_deadline_ts > 0:
                target_ts = min(target_ts, max_deadline_ts)
            remaining = max(0.0, target_ts - _now_ts())
            if remaining <= 0:
                break
            if _now_ts() + remaining > deadline_guard:
                remaining = max(0.0, deadline_guard - _now_ts())
                if remaining <= 0:
                    break
            await asyncio.sleep(min(remaining, 1.0))
        buffer = buffers.pop(key, None)
        if not isinstance(buffer, dict):
            return ""
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        smart_meta = buffer.get("smart_debounce") if isinstance(buffer.get("smart_debounce"), dict) else {}
        if smart_meta.get("enabled"):
            learned_messages = [
                _single_line(item.get("text"), 180)
                for item in messages
                if isinstance(item, dict) and _single_line(item.get("text"), 180)
            ]
            if len(learned_messages) <= 1:
                scope = self._event_scope_key(event)
                self._record_smart_message_debounce_example(
                    kind="false_incomplete",
                    scope=scope,
                    sender_id=sender_id,
                    messages=learned_messages,
                    previous_decision="incomplete",
                    note="模型判断未说完并等待,但用户没有继续补充。",
                )
                recorder = getattr(self, "_record_smart_message_debounce_log", None)
                if callable(recorder):
                    recorder(
                        scope=scope,
                        sender_id=sender_id,
                        text=" / ".join(learned_messages[:3]),
                        decision="incomplete",
                        outcome="timeout_single",
                        note="等待结束但没有等到补话,本次会作为误等样本。",
                        source="buffer",
                        message_count=len(learned_messages),
                    )
                logger.info(
                    "[PrivateCompanion] 智能防抖等待结束未等到补话: scope=%s sender=%s messages=%s",
                    scope,
                    sender_id,
                    len(learned_messages),
                )
            else:
                scope = self._event_scope_key(event)
                recorder = getattr(self, "_record_smart_message_debounce_log", None)
                if callable(recorder):
                    recorder(
                        scope=scope,
                        sender_id=sender_id,
                        text=" / ".join(learned_messages[:3]),
                        decision="incomplete",
                        outcome="merged_followup",
                        note="等待期间收到补话,已合并为同一轮。",
                        source="buffer",
                        message_count=len(learned_messages),
                    )
                logger.info(
                    "[PrivateCompanion] 智能防抖等待命中补话: scope=%s sender=%s messages=%s",
                    scope,
                    sender_id,
                    len(learned_messages),
                )
        lines = []
        for item in messages[:8]:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 260)
            if text:
                name = _single_line(item.get("sender_name"), 40)
                if force_consume and name:
                    lines.append(f"{name}: {text}")
                else:
                    lines.append(text)
        if len(lines) <= 1:
            logger.info(
                "[PrivateCompanion] 消息收口等待结束: kind=%s scope=%s sender=%s count=%s result=single",
                buffer_kind,
                log_scope,
                log_sender,
                len(lines),
            )
            return ""
        merged = "\n".join(f"{idx + 1}. {line}" for idx, line in enumerate(lines))
        logger.info(
            "[PrivateCompanion] 消息收口等待结束: kind=%s scope=%s sender=%s count=%s result=merged preview=%s",
            buffer_kind,
            log_scope,
            log_sender,
            len(lines),
            _single_line(merged, 120),
        )
        return merged

    def _take_buffered_private_images_for_event(self, event: AstrMessageEvent) -> list[str]:
        context = self._take_buffered_private_image_context_for_event(event)
        return [str(item) for item in context.get("images", [])[:5] if str(item or "").strip()] if isinstance(context, dict) else []

    def _completed_private_image_vision_task_text(self, vision_task: Any) -> str:
        if not isinstance(vision_task, asyncio.Task) or not vision_task.done() or vision_task.cancelled():
            return ""
        try:
            return _single_line(vision_task.result(), 1400)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊单图后台视觉任务结果读取失败: %s", _single_line(exc, 120))
            return ""

    def _normalize_private_image_reply_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        if "\n" not in cleaned and re.search(r"[\u4e00-\u9fff][ \t]+[\u4e00-\u9fff]", cleaned):
            # Some providers use spaces as short-message pauses. Preserve that intent for manual sends.
            cleaned = re.sub(r"(?<=[\u4e00-\u9fff…！？?！~～])\s+(?=[\u4e00-\u9fff])", "\n", cleaned)
        return cleaned.strip()

    def _private_image_reply_ignores_vision_summary(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        markers = (
            "没看到图", "没看到图片", "没看见图", "没看见图片",
            "看不到图", "看不到图片", "看不见图", "看不见图片",
            "无法看到图", "无法看到图片", "不能看到图", "不能看到图片",
            "看不了图", "看不了图片", "图片没显示", "图没显示",
            "再发一次", "重新发一次", "重发一次",
        )
        return any(marker in compact for marker in markers)

    def _private_image_reply_drifts_to_stale_context(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        stale_markers = (
            "下午我会", "下午陪你", "五点放学", "放学就行", "放学之后",
            "到时候叫我", "到时候喊我", "ちゃんと付き合う", "午後",
        )
        image_markers = (
            "图", "图片", "画面", "漫画", "这个", "这张", "大腿", "夹头",
            "好笑", "离谱", "表情", "梗", "幽灵", "月亮",
        )
        return any(marker in compact for marker in stale_markers) and any(marker in compact for marker in image_markers)

    def _record_user_recent_group_message_from_observation(
        self,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any] | None = None,
        message_id: str = "",
        ts: float | None = None,
    ) -> None:
        user_id = str(sender_id or "").strip()
        if not user_id:
            return
        users = self.data.get("users")
        configured_ids = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        if not isinstance(users, dict):
            return
        if user_id not in users and user_id not in configured_ids:
            return
        user = self._get_user(user_id)
        now = _now_ts() if ts is None else float(ts or 0)
        recent = user.setdefault("recent_group_messages", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_group_messages"] = recent
        recent.append(
            {
                "ts": now,
                "group_id": _single_line(group_id, 40),
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 180),
                "message_id": _single_line(message_id, 120),
                "talking_to": _single_line((scene or {}).get("talking_to"), 40) if isinstance(scene, dict) else "",
                "scene_trigger": _single_line((scene or {}).get("trigger"), 40) if isinstance(scene, dict) else "",
            }
        )
        cutoff = now - 2 * 3600
        kept = [
            item for item in recent
            if isinstance(item, dict) and _safe_float(item.get("ts"), 0) >= cutoff
        ]
        user["recent_group_messages"] = kept[-8:]

    def _format_recent_group_messages_for_private_image_prompt(self, user_id: str) -> str:
        if not user_id:
            return ""
        try:
            user = self._get_user(user_id)
        except Exception:
            return ""
        recent = user.get("recent_group_messages")
        if not isinstance(recent, list):
            return ""
        now = _now_ts()
        items = [
            item for item in recent
            if isinstance(item, dict) and 0 <= now - _safe_float(item.get("ts"), 0) <= 20 * 60
        ][-4:]
        if not items:
            return ""
        lines = ["【用户刚刚在群里的近况】"]
        for item in items:
            elapsed = self._format_elapsed(max(0, now - _safe_float(item.get("ts"), 0)))
            group_id = _single_line(item.get("group_id"), 40)
            text = _single_line(item.get("text"), 160)
            if text:
                lines.append(f"- {elapsed}前｜群 {group_id}｜{text}")
        if len(lines) <= 1:
            return ""
        lines.append("使用方式：这比私聊压缩历史更新，只作为当前用户近况和语气背景；当前回复仍然优先回应这张图片。")
        return "\n".join(lines)

    def _trim_private_image_stale_context_tail(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        stale_patterns = (
            r"\s*<tts\b[^>]*>[^<]*(?:午後|付き合う)[^<]*</tts>\s*[^。！？!?]*?(?:下午|五点|放学|陪你)[^。！？!?\n]*[。！？!?]?",
            r"\s*(?:另外|还有|それと|顺便)[，,、\s]*[^。！？!?\n]*(?:下午|五点|放学|到时候|陪你)[^。！？!?\n]*[。！？!?]?",
            r"\s*[^。！？!?\n]*(?:下午我会|下午陪你|五点放学|放学就行|到时候叫我|到时候喊我)[^。！？!?\n]*[。！？!?]?",
        )
        trimmed = cleaned
        for pattern in stale_patterns:
            trimmed = re.sub(pattern, "", trimmed, flags=re.IGNORECASE).strip()
        trimmed = re.sub(r"\n{3,}", "\n\n", trimmed).strip()
        return trimmed or cleaned

    def _private_image_reply_misses_content_question(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if self._private_image_reply_ignores_vision_summary(text):
            return True
        source_only_markers = (
            "从哪搞的", "从哪弄的", "哪搞的", "哪弄的", "哪里搞的", "哪里弄的",
            "哪来的", "哪里来的", "出处", "来源", "你怎么突然发这个", "怎么突然发这个",
        )
        content_markers = (
            "图里", "图片里", "画面", "可见", "内容", "漫画", "截图", "照片", "文字",
        )
        return any(marker in compact for marker in source_only_markers) and not any(marker in compact for marker in content_markers)

    def _private_image_content_answer_from_vision(self, vision_text: str, *, user_text: str = "") -> str:
        visible = self._private_image_visible_line(vision_text)
        image_type = self._private_image_type_line(vision_text)
        intent = self._private_image_intent_line(vision_text)
        visible_value = re.sub(r"^可见内容[：:]\s*", "", _single_line(visible, 180)).strip()
        type_value = re.sub(r"^图片类型[：:]\s*", "", _single_line(image_type, 80)).strip()
        intent_value = re.sub(r"^图像表达意图[：:]\s*", "", _single_line(intent, 140)).strip()
        parts: list[str] = []
        if type_value and visible_value:
            parts.append(f"图里大概是{type_value}：{visible_value}")
        elif visible_value:
            parts.append(f"图里大概是：{visible_value}")
        elif type_value:
            parts.append(f"图里像是{type_value}。")
        if intent_value:
            parts.append(f"它主要是在表达{intent_value}")
        answer = "；".join(parts).strip("；")
        if not answer:
            return ""
        if self._private_image_user_asks_content(user_text):
            answer += "。"
        return answer

    async def _send_private_image_reply_text(self, event: AstrMessageEvent, reply: str) -> None:
        text = self._normalize_private_image_reply_text(reply)
        if not text:
            return
        chain = await self._private_image_reply_chain(text, event)
        if not chain:
            return
        should_segment = (
            bool(getattr(self, "enable_segmented_proactive_reply", False))
            and str(getattr(self, "segmented_proactive_scope", "") or "") == "all_llm"
        )
        outbound_chains = self._private_image_split_reply_chain(chain, should_segment=should_segment)
        if not outbound_chains:
            return
        if len(outbound_chains) <= 1:
            await self._send_private_image_reply_chain(event, outbound_chains[0])
            return
        logger.info("[PrivateCompanion] 私聊单图回复按手动链路分段发送: segments=%s", len(outbound_chains))
        remainder_started_at = _now_ts()
        await self._send_private_image_reply_chain(event, outbound_chains[0])
        asyncio.create_task(
            self._send_private_image_reply_remainder_chains(
                event,
                outbound_chains[1:],
                previous_text=self._private_image_chain_text(outbound_chains[0]),
                started_at=remainder_started_at,
            )
        )

    async def _private_image_reply_chain(self, text: str, event: AstrMessageEvent) -> list[Any]:
        normalized = str(text or "").strip()
        normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(normalizer) and re.search(r"</?t{2,}s\b", normalized, flags=re.IGNORECASE):
            try:
                normalized = str(normalizer(normalized) or normalized).strip()
            except Exception:
                pass
        has_tts_block = bool(re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL))
        if has_tts_block and bool(getattr(self, "enable_tts_enhancement", False)):
            processor = getattr(self, "_process_tts_tags", None)
            if callable(processor):
                fallback_plain = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized, flags=re.IGNORECASE).strip()
                try:
                    chain = await processor(normalized, event, fallback_plain=fallback_plain)
                except Exception as exc:
                    logger.warning("[PrivateCompanion] 私聊单图 TTS 组件生成失败,回退文本发送: %s", _single_line(exc, 120))
                    chain = []
                cleaned_chain = self._private_image_clean_reply_chain(chain)
                if cleaned_chain:
                    return cleaned_chain
                if fallback_plain:
                    return [Plain(fallback_plain)]
        visible_text = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized, flags=re.IGNORECASE).strip() if has_tts_block else normalized
        return [Plain(visible_text)] if visible_text else []

    @staticmethod
    def _private_image_chain_text(chain: list[Any]) -> str:
        return _single_line(" ".join(str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)), 260)

    @staticmethod
    def _private_image_clean_reply_chain(chain: list[Any]) -> list[Any]:
        cleaned: list[Any] = []
        for comp in chain or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if text:
                    cleaned.append(Plain(text))
                continue
            cleaned.append(comp)
        return cleaned

    def _private_image_split_reply_chain(self, chain: list[Any], *, should_segment: bool) -> list[list[Any]]:
        return split_plain_component_chain(
            chain,
            plain_type=Plain,
            split_text=self._split_proactive_text,
            fallback_line_split=not should_segment,
        )

    async def _send_private_image_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> None:
        if not chain:
            return
        try:
            await event.send(event.chain_result(chain))
            return
        except Exception:
            await event.send(self._build_result_from_chain(chain))

    async def _send_private_image_reply_remainder_chains(
        self,
        event: AstrMessageEvent,
        chains: list[list[Any]],
        *,
        previous_text: str = "",
        started_at: float | None = None,
    ) -> None:
        prev = previous_text
        total = len([item for item in chains if item])
        sent_index = 0
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        if not scope:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        lock_getter = getattr(self, "_segmented_remainder_lock", None)
        lock = lock_getter(scope) if callable(lock_getter) else asyncio.Lock()
        started_at = _safe_float(started_at, _now_ts(), 0.0) or _now_ts()
        async with lock:
            for chain in chains:
                if not chain:
                    continue
                sent_index += 1
                try:
                    wait_for = prev or self._private_image_chain_text(chain)
                    delay = await self._calc_segmented_proactive_interval(wait_for)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    activity_checker = getattr(self, "_scope_has_new_inbound_activity", None)
                    if callable(activity_checker):
                        try:
                            if activity_checker(scope, started_at, ignore_self=True):
                                logger.info(
                                    "[PrivateCompanion] 会话已有新消息，停止发送私聊单图剩余片段: scope=%s sent=%s/%s",
                                    scope,
                                    max(0, sent_index - 1),
                                    total,
                                )
                                return
                        except Exception:
                            pass
                    await self._send_private_image_reply_chain(event, chain)
                    logger.info(
                        "[PrivateCompanion] 私聊单图剩余片段已发送: index=%s/%s preview=%s",
                        sent_index,
                        total,
                        self._private_image_chain_text(chain) or chain[0].__class__.__name__,
                    )
                    prev = self._private_image_chain_text(chain) or prev
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanion] 私聊单图剩余片段发送失败: error=%s",
                        _single_line(exc, 160),
                        exc_info=True,
                    )
                    return

    def _take_buffered_private_image_context_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return {}
        key = self._semantic_buffer_key(f"private:{sender_id}", sender_id)
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            return {}
        buffer = buffers.get(key)
        if not isinstance(buffer, dict):
            return {}
        if _now_ts() - _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0) > max(30.0, self._message_debounce_seconds("image") + 30.0):
            return {}
        images = buffer.pop("images", [])
        image_limit = self._private_image_vision_text_limit(len(images))
        return {
            "images": [str(item) for item in images[:5] if str(item or "").strip()],
            "image_mode": _single_line(buffer.pop("image_mode", ""), 20),
            "vision_task": buffer.pop("vision_task", None),
            "vision_text": _single_line(buffer.pop("vision_text", ""), image_limit),
        }

    async def _send_delayed_private_image_only_event(
        self,
        event: AstrMessageEvent,
        user_id: str,
        buffer: dict[str, Any],
    ) -> None:
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        feature_enabled = (
            feature_checker("enable_private_image_self_recognition")
            if callable(feature_checker)
            else bool(getattr(self, "enable_private_image_self_recognition", True))
        )
        if not feature_enabled:
            logger.info("[PrivateCompanion] 私聊单图延迟任务已跳过: 图片转述增强已关闭 user=%s", user_id)
            return
        images = buffer.get("images") if isinstance(buffer.get("images"), list) else []
        vision_task = buffer.get("vision_task")
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_text = _single_line(buffer.get("vision_text"), image_limit)
        vision_wait_timed_out = False
        if not vision_text and isinstance(vision_task, asyncio.Task):
            timeout = max(0.0, float(getattr(self, "private_image_vision_wait_seconds", 30.0) or 0.0))
            try:
                if timeout > 0:
                    logger.info("[PrivateCompanion] 私聊单图等待视觉转述完成: user=%s timeout=%.1fs", user_id, timeout)
                    vision_text = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=timeout), image_limit)
            except asyncio.TimeoutError:
                vision_wait_timed_out = True
                logger.info("[PrivateCompanion] 私聊单图延迟处理时视觉转述仍未完成: user=%s timeout=%.1fs", user_id, timeout)
            except Exception as exc:
                logger.info("[PrivateCompanion] 私聊单图延迟视觉转述失败: user=%s error=%s", user_id, _single_line(exc, 120))
        ownership_line = self._private_image_ownership_line(vision_text)
        intent_line = self._private_image_intent_line(vision_text)
        reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
        prompt = _single_line(getattr(event, "message_str", ""), 120)
        if not prompt or prompt == "[图片]":
            prompt = (
                "用户刚刚只发了一张图片,没有补充文字。"
                "图片内容已在系统提示的【本轮延迟图片】视觉摘要中给出；请直接回应那张图,不要说没看到图片。"
                "本轮只回应当前图片和用户发图可能表达的态度/梗/疑问；聊天历史只作语气背景,不要续写、答应或安排旧话题。"
                if vision_text
                else "用户刚刚只发了一张图片,没有补充文字。"
            )
        logger.info(
            "[PrivateCompanion] 私聊单图准备进入主链: user=%s images=%s has_vision=%s intent=%s ownership=%s objective=%s vision_preview=%s",
            user_id,
            len(images),
            bool(vision_text),
            intent_line or "无",
            ownership_line or "无",
            _single_line(reply_objective, 120),
            _single_line(vision_text, 220),
        )
        raw_image_sources = [str(item) for item in images[:5] if str(item or "").strip()]
        image_items = self._private_image_model_image_items(raw_image_sources)
        model_image_urls = [url for _, url in image_items]
        request_image_refs = self._private_image_sources_for_astrbot_request(raw_image_sources)
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            framework_event = event
            if umo:
                try:
                    from astrbot.core.platform.message_session import MessageSession
                    from .proactive_message import SyntheticPrivateWakeEvent

                    session = MessageSession.from_str(umo)
                    sender_name = ""
                    try:
                        sender_name = _single_line(event.get_sender_name(), 60)
                    except Exception:
                        sender_name = ""
                    framework_event = SyntheticPrivateWakeEvent(
                        context=self.context,
                        session=session,
                        message="[图片]",
                        sender_name=sender_name or "PrivateCompanion",
                    )
                    try:
                        selected_provider = event.get_extra("selected_provider")
                        if selected_provider:
                            framework_event.set_extra("selected_provider", selected_provider)
                    except Exception:
                        pass
                    logger.info("[PrivateCompanion] 私聊单图主链使用合成私聊事件执行: user=%s session=%s", user_id, umo)
                except Exception as exc:
                    framework_event = event
                    logger.info("[PrivateCompanion] 私聊单图合成私聊事件创建失败,回退原事件: user=%s error=%s", user_id, _single_line(exc, 160))
            setattr(framework_event, "private_companion_deferred_private_image_only_ready", True)
            setattr(framework_event, "private_companion_deferred_private_image_only", False)
            setattr(framework_event, "private_companion_skip_external_token_stats", True)
            setattr(framework_event, "private_companion_force_sanitize_tools", True)
            setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
            setattr(framework_event, "private_companion_delayed_image_sources", list(request_image_refs))
            buffered_image_mode = _single_line(buffer.get("image_mode"), 20)
            main_provider_supports_image = self._event_main_provider_supports_image(framework_event)
            has_visual_provider = self._has_private_image_visual_provider(umo)
            has_dynamic_gif_sources = (
                bool(getattr(self, "enable_private_image_gif_enhancement", True))
                and self._private_image_sources_include_gif(raw_image_sources)
            )
            direct_image_mode = bool(
                request_image_refs
                and buffered_image_mode == "direct"
                and main_provider_supports_image
                and not has_dynamic_gif_sources
            )
            direct_provider_id = ""
            direct_provider_source = "current_main_provider"
            if direct_image_mode:
                try:
                    direct_provider_id = _single_line(framework_event.get_extra("selected_provider"), 160)
                except Exception:
                    direct_provider_id = ""
                if not direct_provider_id:
                    direct_provider_id = "current_main_provider"
                setattr(framework_event, "private_companion_delayed_image_mode", "direct")
            elif request_image_refs:
                setattr(framework_event, "private_companion_delayed_image_mode", "caption" if has_visual_provider else "no_vision")
            if not direct_image_mode and has_visual_provider and not vision_text and images:
                completed_vision = self._completed_private_image_vision_task_text(vision_task)
                if completed_vision:
                    vision_text = _single_line(completed_vision, self._private_image_vision_text_limit(len(images)))
                    logger.info(
                        "[PrivateCompanion] 私聊单图主链前取到后台视觉摘要: user=%s preview=%s",
                        user_id,
                        _single_line(vision_text, 220),
                    )
                elif not vision_wait_timed_out:
                    vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                else:
                    logger.info("[PrivateCompanion] 私聊单图识图等待已超时,主链不再重复发起视觉转述: user=%s", user_id)
                    setattr(framework_event, "private_companion_delayed_image_mode", "no_vision")
                if vision_text:
                    setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
            if has_dynamic_gif_sources and request_image_refs:
                logger.info(
                    "[PrivateCompanion] 私聊单图检测到动态 GIF,已改用抽帧视觉摘要链路: user=%s has_vision=%s",
                    user_id,
                    bool(vision_text),
            )
            conv = None
            if umo:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="private_image_framework_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if conv_id:
                        conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            build_cfg = MainAgentBuildConfig(
                tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
                llm_safety_mode=False,
                streaming_response=False,
            )
            req = ProviderRequest(
                prompt=prompt,
                conversation=conv,
                session_id=getattr(framework_event, "session_id", None) or umo,
            )
            previous_selected_provider = ""
            selected_provider_changed = False
            if direct_image_mode:
                req.image_urls = list(request_image_refs)
            await self.inject_humanized_state(framework_event, req)
            boundary_prompt = (
                "【本轮图片回复边界】\n"
                "用户当前只发了一张图片,没有文字补充。你的当前任务是回应这张图片本身和用户借图表达的态度/梗/疑问。\n"
                "不要把聊天历史、长期记忆、主动消息、旧 TTS 文本或压缩摘要里的邀约当成当前输入；"
                "不要顺便提下午、五点、放学、出去走走、陪你、到时候叫我等旧约定。"
            )
            recent_group_context = self._format_recent_group_messages_for_private_image_prompt(user_id)
            if recent_group_context:
                boundary_prompt = f"{boundary_prompt}\n\n{recent_group_context}"
            current_prompt = str(getattr(req, "system_prompt", "") or "")
            req.system_prompt = f"{current_prompt}\n\n{boundary_prompt}".strip() if current_prompt else boundary_prompt
            if direct_image_mode:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in request_image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                logger.info(
                    "[PrivateCompanion] 私聊单图主链已挂载图片: user=%s provider=%s source=%s images=%s has_vision=%s",
                    user_id,
                    direct_provider_id,
                    direct_provider_source,
                    len(existing),
                    bool(vision_text),
                )
            start = time.time()
            captured_tool_sends = []
            llm_resp = None
            try:
                async def _runner_factory():
                    built = await build_main_agent(
                        event=framework_event,
                        plugin_context=self.context,
                        config=build_cfg,
                        req=req,
                    )
                    built_req = getattr(built, "provider_request", None)
                    if built_req is not None:
                        sanitizer = getattr(self, "_sanitize_passive_private_agent_tools", None)
                        if callable(sanitizer):
                            sanitizer(framework_event, built_req)
                        runner_obj = getattr(built, "agent_runner", None)
                        if runner_obj is not None:
                            try:
                                runner_obj.req = built_req
                            except Exception:
                                pass
                    return built

                capture_runner = getattr(self, "_capture_framework_send_message_calls", None)
                framework_lock = getattr(self, "_framework_agent_lock", None)
                if not isinstance(framework_lock, asyncio.Lock):
                    framework_lock = asyncio.Lock()
                    self._framework_agent_lock = framework_lock
                async with framework_lock:
                    if callable(capture_runner) and umo:
                        result, captured_tool_sends = await capture_runner(
                            target_session=umo,
                            runner_factory=_runner_factory,
                        )
                        if captured_tool_sends:
                            logger.info(
                                "[PrivateCompanion] 私聊单图主链拦截到框架工具直发: user=%s count=%s",
                                user_id,
                                len(captured_tool_sends),
                            )
                    else:
                        result = await _runner_factory()
                        runner_for_step = getattr(result, "agent_runner", None) if result else None
                        if runner_for_step is not None and hasattr(runner_for_step, "step_until_done"):
                            async for _ in runner_for_step.step_until_done(20):
                                pass
            except Exception as exc:
                if direct_image_mode and self._exception_indicates_image_input_unsupported(exc):
                    logger.warning(
                        "[PrivateCompanion] 私聊单图主链模型不支持图片输入,已降级为视觉摘要兜底: user=%s provider=%s error=%s",
                        user_id,
                        direct_provider_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "image_input_unsupported_fallback"
                    result = None
                elif self._exception_indicates_tool_schema_invalid(exc):
                    logger.warning(
                        "[PrivateCompanion] 私聊单图主链工具 schema 不兼容,已转入兜底回复: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "tool_schema_invalid_fallback"
                    result = None
                else:
                    raise
            finally:
                if selected_provider_changed:
                    try:
                        framework_event.set_extra("selected_provider", previous_selected_provider)
                    except Exception:
                        pass
            runner = getattr(result, "agent_runner", None) if result else None
            if llm_resp is None:
                llm_resp = runner.get_final_llm_resp() if runner else None
            if "reply" not in locals():
                reply = str(getattr(llm_resp, "completion_text", "") or "").strip()
            if "reply_source" not in locals():
                reply_source = "main_chain"
            if reply and self._private_image_reply_is_internal_error(reply):
                logger.warning(
                    "[PrivateCompanion] 私聊单图主链返回内部错误文本,已拦截转入兜底: user=%s preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
                reply_source = "internal_error_fallback"
            if not reply and captured_tool_sends:
                captured_text_parts: list[str] = []
                sanitizer = getattr(self, "_sanitize_captured_plain_text", None)
                for call in reversed(captured_tool_sends):
                    messages = getattr(call, "messages", [])
                    if not isinstance(messages, list):
                        continue
                    for item in messages:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("type") or "").strip().lower() != "plain":
                            continue
                        raw_text = item.get("text")
                        text_value = sanitizer(raw_text) if callable(sanitizer) else _single_line(raw_text, 260)
                        if text_value:
                            captured_text_parts.append(text_value)
                    if captured_text_parts:
                        break
                reply = _single_line("\n".join(captured_text_parts), 500)
                if reply:
                    reply_source = "main_chain_tool_capture"
                    logger.info(
                        "[PrivateCompanion] 私聊单图主链工具直发文本已转为普通回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
            if reply and vision_text and self._private_image_reply_ignores_vision_summary(reply):
                logger.info(
                    "[PrivateCompanion] 私聊单图主链疑似忽略视觉摘要,转入兜底回复: user=%s reply_preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
            if reply and vision_text and self._private_image_reply_drifts_to_stale_context(reply):
                trimmed_reply = self._trim_private_image_stale_context_tail(reply)
                if trimmed_reply and trimmed_reply != reply and not self._private_image_reply_drifts_to_stale_context(trimmed_reply):
                    logger.info(
                        "[PrivateCompanion] 私聊单图主链回复夹带旧上下文,已裁剪: user=%s before=%s after=%s",
                        user_id,
                        _single_line(reply, 180),
                        _single_line(trimmed_reply, 180),
                    )
                    reply = trimmed_reply
                else:
                    logger.info(
                        "[PrivateCompanion] 私聊单图主链回复夹带旧上下文,转入兜底回复: user=%s reply_preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
                    reply = ""
            if reply:
                logger.info(
                    "[PrivateCompanion] 私聊单图主链回复生成: user=%s chars=%s intent=%s ownership=%s reply_preview=%s",
                    user_id,
                    len(reply),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply, 180),
                )
            if not reply:
                if not vision_text and images and has_visual_provider:
                    vision_text = self._completed_private_image_vision_task_text(vision_task)
                    if vision_text:
                        logger.info(
                            "[PrivateCompanion] 私聊单图兜底前取到后台视觉摘要: user=%s preview=%s",
                            user_id,
                            _single_line(vision_text, 220),
                        )
                    elif not vision_wait_timed_out:
                        vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                    else:
                        logger.info("[PrivateCompanion] 私聊单图兜底阶段跳过重复视觉转述: user=%s", user_id)
                    setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
                fallback_prompt = (
                    "用户只发了一张图片。请用当前私聊人格短句回应，不要提模型、插件、视觉转述或路径。\n"
                    f"{self._private_image_identity_disambiguation_instruction()}\n"
                    f"{reply_objective}\n"
                    f"图片内容摘要：{vision_text}"
                    if vision_text
                    else "用户只发了一张图片，但当前没有可靠图片内容。请自然回复一句，不要编造画面，可以让用户补一句想看哪里。"
                )
                fallback_reply = await self._llm_call(
                    fallback_prompt,
                    max_tokens=160,
                    task="private_image_only_fallback",
                )
                reply = _single_line(_strip_internal_message_blocks(fallback_reply or ""), 500)
                if reply and self._private_image_reply_is_internal_error(reply):
                    logger.warning(
                        "[PrivateCompanion] 私聊单图兜底 LLM 返回内部错误文本,已丢弃: user=%s preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
                    reply = ""
                reply_source = "fallback_llm"
                logger.info(
                    "[PrivateCompanion] 私聊单图兜底回复生成: user=%s chars=%s intent=%s ownership=%s objective=%s reply_preview=%s",
                    user_id,
                    len(reply),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_objective, 120),
                    _single_line(reply, 180),
                )
                if not reply:
                    logger.warning(
                        "[PrivateCompanion] 私聊单图原生链路与兜底 LLM 均未生成有效回复,不启用本地静态兜底: user=%s images=%s has_vision=%s",
                        user_id,
                        len(images),
                        bool(vision_text),
                    )
                    self._record_llm_usage(
                        provider_id="framework",
                        task="private_image_only_framework",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        resp=llm_resp,
                        budget_exempt=True,
                    )
                    return
                logger.warning("[PrivateCompanion] 私聊单图原生链路回复为空,已使用兜底 LLM 回复: user=%s images=%s", user_id, len(images))
            self._record_llm_usage(
                provider_id="framework",
                task="private_image_only_framework",
                prompt=prompt,
                completion=reply,
                elapsed_ms=int((time.time() - start) * 1000),
                success=True,
                resp=llm_resp,
                budget_exempt=True,
            )
            await self._send_private_image_reply_text(event, reply)
            image_keys = self._private_image_cache_image_keys([str(item) for item in images[:5] if str(item or "").strip()])
            image_aliases = self._private_image_cache_aliases_for_sources([str(item) for item in images[:5] if str(item or "").strip()])
            if image_keys:
                try:
                    async with self._data_lock:
                        user = self._get_user(user_id)
                        user["last_private_image_vision_feedback_target"] = {
                            "ts": _now_ts(),
                            "image_keys": image_keys,
                            "image_aliases": image_aliases,
                            "vision_text": _single_line(vision_text, image_limit),
                            "reply": _single_line(reply, 300),
                            "ownership": ownership_line,
                            "intent": intent_line,
                        }
                        self._save_data_sync()
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 私聊图片视觉反馈目标记录失败: %s", exc)
            if reply_source == "main_chain":
                logger.info("[PrivateCompanion] 私聊单图无补充说明,已由原生 LLM 链路回复: user=%s images=%s", user_id, len(images))
            else:
                logger.info(
                    "[PrivateCompanion] 私聊单图无补充说明,原生链路为空,已由兜底回复发送: user=%s images=%s source=%s",
                    user_id,
                    len(images),
                    reply_source,
                )
        except Exception as exc:
            logger.warning("[PrivateCompanion] 私聊单图延迟回复失败: user=%s error=%s", user_id, _single_line(exc, 180), exc_info=True)

    async def _finalize_private_image_buffer_after_wait(self, key: str, user_id: str, first_ts: float) -> None:
        wait = self._message_debounce_seconds("image")
        remaining = max(0.0, first_ts + wait - _now_ts())
        if remaining > 0:
            await asyncio.sleep(remaining)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        if not isinstance(buffer, dict):
            return
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        placeholder = "用户刚刚先单独发送了一张图片,可能马上会补充说明。"
        has_followup = any(
            isinstance(item, dict)
            and (cleaned := _single_line(item.get("text"), 260))
            and cleaned != placeholder
            for item in messages
        )
        if has_followup:
            logger.info("[PrivateCompanion] 私聊单图已由补充消息接管: user=%s", user_id)
            return
        buffers.pop(key, None)
        original_event = buffer.get("original_event")
        if isinstance(original_event, AstrMessageEvent):
            await self._send_delayed_private_image_only_event(original_event, user_id, buffer)
            return
        vision_task = buffer.get("vision_task")
        if isinstance(vision_task, asyncio.Task) and not vision_task.done():
            vision_task.cancel()
        logger.info("[PrivateCompanion] 私聊单图等待补充后无文字指示,但原事件不可用: user=%s", user_id)

