# -*- coding: utf-8 -*-
"""PrivateImageIngestCachePart01Mixin。

由 tools/split_mixin_domain.py 从 private_image_ingest_cache.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 471 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageIngestCacheMixin）。
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import shutil
import tempfile
import urllib.request
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import PromptSection
from .helpers import _missing_optional_model_dependency, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .private_image_shared import _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit



class PrivateImageIngestCachePart01Mixin:
    """PrivateImageIngestCachePart01Mixin（从 PrivateImageIngestCacheMixin 拆出）。"""


    def _private_image_setting(self, key: str, default: Any = None) -> Any:
        """Read a config key in the active persona without mutating shared attrs."""
        return runtime_persona_setting(self, key, default)

    @staticmethod
    def _register_materialized_private_image_context(
        req: ProviderRequest,
        *,
        section: PromptSection,
        marker: str = "",
        priority: int,
    ) -> bool:
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        if marker and plan.contains_marker(marker):
            return False
        return plan.materialize_system_block(
            req,
            section=section,
            marker=marker,
            priority=priority,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

    def _private_image_framework_context(self) -> Any | None:
        resolver = getattr(self, "_proactive_framework_context", None)
        if callable(resolver):
            return resolver()
        return getattr(self, "context", None)

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
                    "私聊图片存在性检测缺少可选模型依赖，已按无图片继续: label=%s module=%s err=%s",
                    _single_line(label, 40) or "-",
                    missing,
                    _single_line(exc, 160),
                )
                return False
            logger.warning(
                "私聊图片存在性检测失败，已按无图片继续: label=%s err=%s",
                _single_line(label, 40) or "-",
                _single_line(exc, 160),
            )
            return False

    def _private_event_has_nontext_content(self, event: AstrMessageEvent) -> bool:
        """Keep non-text message segments available to AstrBot's default chain.

        File-only private messages commonly have an empty ``message_str``. They
        must not be mistaken for an empty adapter event, otherwise the
        companion's empty-message guard prevents the framework and file-aware
        tools from receiving the attachment at all.
        """
        try:
            components = self._event_components(event)
        except Exception:
            return False
        for component in components:
            if isinstance(component, dict):
                type_name = str(component.get("type") or component.get("post_type") or "").strip().lower()
            else:
                type_name = component.__class__.__name__.strip().lower()
            if type_name and type_name not in {"plain", "text"}:
                return True
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

    def _private_image_local_path_is_allowed(self, path: Path) -> bool:
        """Allow image files only from plugin, AstrBot, or temporary storage roots."""
        try:
            resolved = path.resolve()
        except Exception:
            return False
        roots: list[Path] = []
        for candidate in (getattr(self, "data_dir", ""), tempfile.gettempdir()):
            if candidate:
                try:
                    roots.append(Path(candidate).resolve())
                except Exception:
                    continue
        try:
            astrbot_root = Path(_private_image_host.get_astrbot_data_path()).resolve()
        except Exception:
            astrbot_root = None
        if astrbot_root is not None:
            roots.append(astrbot_root)
        for root in roots:
            try:
                if resolved.is_relative_to(root):
                    return True
            except AttributeError:
                if str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep):
                    return True
        return False

    @staticmethod
    def _private_image_local_path_from_source(source: Any) -> Path | None:
        """Normalize plain and file-URI paths, including Windows drive URIs."""

        text = str(source or "").strip().strip('"')
        if not text:
            return None
        if text.lower().startswith("file:"):
            try:
                parsed = urlsplit(text)
                path_text = unquote(parsed.path or "")
                netloc = unquote(parsed.netloc or "")
                if re.fullmatch(r"[A-Za-z]:", netloc):
                    path_text = netloc + path_text
                elif netloc and netloc.lower() != "localhost":
                    path_text = f"//{netloc}{path_text}"
                if os.name == "nt" and re.match(r"^/[A-Za-z]:[\\/]", path_text):
                    path_text = path_text[1:]
                text = path_text
            except (UnicodeError, ValueError):
                return None
        else:
            text = unquote(text)
        try:
            return Path(text).expanduser()
        except (OSError, ValueError):
            return None

    async def _persist_private_inbound_images(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        # Image files are private user state. Resolve the sender against the
        # current platform/adapter/bot account before choosing the debounce
        # directory so equal raw IDs cannot share cached media.
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                raw_sender = event.get_sender_id()
            except Exception:
                raw_sender = ""
            if raw_sender:
                try:
                    scoped = _single_line(resolver(event, raw_sender), 160)
                except Exception:
                    scoped = ""
                if scoped:
                    user_id = scoped
        result: list[str] = []
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(user_id or "unknown"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return result
        now_ms = int(_private_image_host._now_ts() * 1000)

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
                    logger.debug("私聊图片组件转换失败: %s", exc)
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
                    "私聊图片组件未能解析出文件路径: class=%s data_keys=%s",
                    comp.__class__.__name__,
                    data_keys or "-",
                )
                continue
            source_path = Path(source)
            if source_path.exists() and source_path.is_file():
                if not self._private_image_local_path_is_allowed(source_path):
                    logger.warning(
                        "private image local path rejected: path=%s",
                        _single_line(source, 200),
                    )
                    continue
                suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                target = target_dir / f"{now_ms}_{index}{suffix}"
                try:
                    shutil.copy2(source_path, target)
                    result.append(str(target))
                    continue
                except Exception as exc:
                    logger.debug("私聊图片暂存失败: %s", exc)
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_{index}",
                    public_hosts_only=True,
                )
                if persisted:
                    result.append(persisted)
                    continue
            if re.match(r"^(?:data|file|base64)://", source, flags=re.I):
                result.append(source)
        if not result:
            for source in self._raw_private_image_sources(event):
                if not source or source in result:
                    continue
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_raw_{len(result) + 1}",
                    public_hosts_only=True,
                )
                if persisted:
                    result.append(persisted)
                    continue
                if re.match(r"^https?://", source, flags=re.I):
                    continue
                if self._private_image_source_to_model_url(source):
                    result.append(source)
        return result

    async def _persist_private_remote_image_source(
        self,
        source: str,
        target_dir: Path,
        stem: str,
        *,
        public_hosts_only: bool = False,
    ) -> str:
        text = str(source or "").strip()
        if not re.match(r"^https?://", text, flags=re.I):
            return ""
        if public_hosts_only and not await asyncio.to_thread(_private_image_host._url_host_is_public, text):
            logger.warning(
                "remote image host rejected: url=%s",
                _single_line(text, 160),
            )
            return ""

        request_url = self._private_image_request_url(text)
        if not request_url:
            return ""

        def download() -> str:
            try:
                request = urllib.request.Request(
                    request_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 AstrBot PrivateCompanion/5.0.0",
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    },
                )
                opener = urllib.request.build_opener(_private_image_host._PublicOnlyRedirectHandler()) if public_hosts_only else None
                response_cm = opener.open(request, timeout=15) if opener is not None else urllib.request.urlopen(request, timeout=15)
                with response_cm as response:
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    length = _safe_int(response.headers.get("Content-Length"), 0, 0)
                    max_bytes = 12 * 1024 * 1024
                    if length and length > max_bytes:
                        logger.info("私聊远程图片过大,跳过下载: size=%s url=%s", length, _single_line(text, 120))
                        return ""
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            logger.info("私聊远程图片下载超过限制,已中止: url=%s", _single_line(text, 120))
                            return ""
                        chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    logger.info("私聊远程图片响应为空,跳过: url=%s", _single_line(text, 120))
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
                    logger.info("私聊远程图片响应不是图片,跳过: content_type=%s url=%s", content_type or "-", _single_line(text, 120))
                    return ""
                target = target_dir / f"{re.sub(r'[^0-9A-Za-z_.-]+', '_', stem)}{suffix}"
                target.write_bytes(data)
                return str(target)
            except Exception as exc:
                logger.warning("私聊远程图片下载失败: %s url=%s", _single_line(exc, 120), _single_line(text, 120))
                return ""

        return await asyncio.to_thread(download)

    @staticmethod
    def _private_image_request_url(source: str) -> str:
        text = str(source or "").strip()
        if not re.match(r"^https?://", text, flags=re.I):
            return ""
        try:
            parsed = urlsplit(text)
            hostname = str(parsed.hostname or "")
            if not hostname:
                return ""
            ascii_hostname = hostname.encode("idna").decode("ascii")
            host = f"[{ascii_hostname}]" if ":" in ascii_hostname and not ascii_hostname.startswith("[") else ascii_hostname
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            userinfo = ""
            if parsed.username is not None:
                userinfo = quote(parsed.username, safe="%")
                if parsed.password is not None:
                    userinfo += f":{quote(parsed.password, safe='%')}"
                userinfo += "@"
            netloc = f"{userinfo}{host}"
            return urlunsplit((
                parsed.scheme.lower(),
                netloc,
                quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
                quote(parsed.query, safe="=&%:@/?+;,!$'()*-._~"),
                quote(parsed.fragment, safe="=&%:@/?+;,!$'()*-._~"),
            ))
        except (UnicodeError, ValueError):
            return ""

    async def _prepare_private_image_sources_for_model(self, image_sources: list[str], *, namespace: str = "vision") -> list[str]:
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(namespace or "vision"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        self._sweep_stale_prepared_image_files(target_dir)
        prepared: list[str] = []
        now_ms = int(_private_image_host._now_ts() * 1000)
        for index, source in enumerate([str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:12], 1):
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_{index}",
                    public_hosts_only=True,
                )
                if persisted and persisted not in prepared:
                    prepared.append(persisted)
                continue
            local_path = self._private_image_local_path_from_source(source)
            normalized_source = str(local_path) if local_path is not None else source
            if not self._private_image_source_to_model_url(normalized_source):
                logger.info(
                    "本地图片源不可读,已跳过: namespace=%s source=%s",
                    namespace,
                    _single_line(source, 160),
                )
                continue
            if normalized_source not in prepared:
                prepared.append(normalized_source)
        return prepared
