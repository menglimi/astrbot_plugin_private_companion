# -*- coding: utf-8 -*-
"""PrivateImageModelCapabilityMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 366 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from .helpers import _safe_float, _single_line
from .private_image_shared import logger
from astrbot.api.event import AstrMessageEvent
from typing import Any



class PrivateImageModelCapabilityMixin:
    """PrivateImageModelCapabilityMixin（从 PrivateImageMixin 拆出）。"""


    def _private_image_provider_timeout_seconds(
        self,
        provider_id: str = "",
        provider_source: str = "",
    ) -> float:
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        clean_source = _single_line(provider_source, 80)
        if callable(timeout_getter) and clean_source != "astrbot_image_caption":
            provider_key = self._private_image_visual_provider_card_key()
            configured_provider_id = (
                str(self._private_image_setting("PLUGIN_VISION_PROVIDER_ID", getattr(self, "plugin_vision_provider_id", "")) or "")
                if provider_key == "PLUGIN_VISION_PROVIDER_ID"
                else str(self._private_image_setting("NARRATION_PROVIDER_ID", getattr(self, "narration_provider_id", "")) or "")
            )
            override = timeout_getter(
                task="private_image_vision",
                provider_id=_single_line(provider_id, 160) or configured_provider_id,
                timeout_key=provider_key,
            )
            if override is not None:
                return max(3.0, float(override))
        configured = _safe_float(self._private_image_setting("private_image_provider_timeout_seconds", 12.0), 12.0, 0.0)
        if configured <= 0:
            return 0.0
        return max(3.0, configured)

    def _private_image_vision_wait_budget_seconds(self) -> float:
        return max(
            0.0,
            _safe_float(self._private_image_setting("private_image_vision_wait_seconds", 30.0), 30.0, 0.0),
        )

    def _private_image_model_image_items(self, image_sources: list[str]) -> list[tuple[str, str]]:
        items, _source_count, _has_gif_frames = self._private_image_model_image_items_with_meta(image_sources)
        return items

    def _private_image_model_image_items_with_meta(self, image_sources: list[str]) -> tuple[list[tuple[str, str]], int, bool]:
        image_items: list[tuple[str, str]] = []
        seen_image_keys: set[str] = set()
        gif_enhancement_enabled = bool(self._private_image_setting("enable_private_image_gif_enhancement", True))
        gif_max_frames = max(1, min(8, int(self._private_image_setting("private_image_gif_max_frames", 4) or 4)))
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
            gif_data = self._private_image_source_bytes_if_gif(source)
            if gif_data:
                had_gif_frames = True
                gif_items = (
                    self._private_image_gif_frame_model_items(
                        source,
                        image_key,
                        max_frames=gif_max_frames,
                    )
                    if gif_enhancement_enabled
                    else []
                )
                if gif_items:
                    if append_item(gif_items[0]) and len(gif_items) > 1:
                        pending_gif_frames.append(gif_items[1:])
                    if len(image_items) >= max_model_images:
                        break
                else:
                    logger.warning(
                        "GIF 无法转换为兼容的 PNG 帧,已跳过该视觉输入: source=%s",
                        _single_line(source, 120),
                    )
                # Some Gemini-compatible gateways reject image/gif even when
                # they advertise it. Never fall through to the original GIF.
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
            logger.warning("Pillow 不可用,GIF 无法转换为模型兼容的 PNG 帧")
            return []
        try:
            with PILImage.open(io.BytesIO(raw)) as image:
                frame_total = getattr(image, "n_frames", 1) or 1
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
                logger.info("GIF 已转换为 PNG 帧供视觉识别: frames=%s/%s source=%s", len(frames), frame_total, _single_line(source, 120))
                return frames
        except Exception as exc:
            logger.debug("动态 GIF 抽帧失败: %s", exc)
        return []

    def _sanitize_provider_request_gif_inputs(self, req: Any) -> tuple[int, int]:
        """Replace GIF request inputs with PNG frames before provider dispatch."""
        replaced = 0
        dropped = 0
        for attr in ("image_urls", "images"):
            current = getattr(req, attr, None)
            if not isinstance(current, (list, tuple)) or not current:
                continue
            sanitized: list[Any] = []
            changed = False
            for item in current:
                if not isinstance(item, str) or not self._private_image_source_bytes_if_gif(item):
                    sanitized.append(item)
                    continue
                changed = True
                frames = self._private_image_gif_frame_model_items(
                    item,
                    self._private_image_source_cache_key(item),
                    max_frames=max(
                        1,
                        min(
                            8,
                            int(self._private_image_setting("private_image_gif_max_frames", 4) or 4),
                        ),
                    ),
                )
                if frames:
                    sanitized.extend(url for _key, url in frames)
                    replaced += 1
                else:
                    dropped += 1
            if changed:
                try:
                    setattr(req, attr, sanitized)
                except Exception:
                    pass
        return replaced, dropped

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
                path = self._private_image_local_path_from_source(text)
                if path is None:
                    return b""
                if not path.exists() or not path.is_file() or not self._private_image_local_path_is_allowed(path):
                    return b""
                if path.suffix.lower() != ".gif":
                    head = path.read_bytes()[:6]
                    return b"" if not head.startswith((b"GIF87a", b"GIF89a")) else path.read_bytes()
                raw = path.read_bytes()
            return raw if raw.startswith((b"GIF87a", b"GIF89a")) else b""
        except Exception as exc:
            logger.debug("动态 GIF 字节读取失败: %s", exc)
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
        if modalities == []:
            # AstrBot treats an empty migrated list as unspecified/all modalities.
            return True
        return isinstance(modalities, list) and "image" in modalities

    @staticmethod
    def _private_image_delivery_mode(
        *,
        has_visual_provider: bool,
        main_provider_supports_image: bool,
        has_dynamic_gif: bool,
    ) -> str:
        # A configured caption route is an explicit user choice and carries its
        # own fallback chain. Only direct-attach when no caption route is usable.
        if has_visual_provider:
            return "caption"
        if main_provider_supports_image and not has_dynamic_gif:
            return "direct"
        return "no_vision"

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
                "私聊图片 selected_provider 是图片转述模型,不按主视觉模型直挂: provider=%s",
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
            (
                "image_url" in text
                and (
                    "do not support image" in text
                    or "not support image" in text
                    or "image input" in text
                    or "invalidparameter" in text
                )
            )
            or "does not support vision" in text
            or "doesn't support vision" in text
            or "不支持视觉" in text
            or "不支持图片输入" in text
        )

    @staticmethod
    def _private_image_reply_denies_image_capability(text: str) -> bool:
        cleaned = _single_line(text, 500).lower()
        if not cleaned:
            return False
        if any(
            marker in cleaned
            for marker in (
                "不支持视觉",
                "不支持图片输入",
                "没有视觉能力",
                "无法查看图片",
                "无法读取图片",
                "无法识别图片",
                "不能查看图片",
                "不能读取图片",
                "不能看图",
                "看不到你发的图片",
                "can't view images",
                "cannot view images",
                "can't see images",
                "cannot see images",
                "does not support vision",
                "doesn't support vision",
            )
        ):
            return True
        return bool(
            re.search(
                r"(?:我|当前模型|该模型|这个模型|模型|助手).{0,12}(?:无法|不能|不支持|没有).{0,10}(?:查看|读取|识别|处理|接收|理解|访问)?(?:图片|图像|视觉)",
                cleaned,
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
