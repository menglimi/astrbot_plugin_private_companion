# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import mimetypes
import re
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .helpers import _now_ts, _safe_float, _safe_int, _single_line


QZONE_IMAGE_UPLOAD_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
QZONE_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
QZONE_REMOTE_IMAGE_MAX_BYTES = 12 * 1024 * 1024
QZONE_UNSAFE_MEDIA_HOSTS = {"localhost", "localhost.localdomain"}


class QzoneIntegrationError(RuntimeError):
    """User-facing Qzone error with a coarse failure stage."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"{stage}：{message}")


class QzoneMediaMixin:
    """QZone image reading, upload and publish helpers."""

    @staticmethod
    def _qzone_auth_failure_message(message: Any) -> bool:
        text = str(message or "").lower()
        if not text:
            return False
        markers = (
            "code=-3000",
            "请先登录",
            "未登录",
            "login",
            "cookie",
            "p_skey",
            "pskey",
            "skey",
            "g_tk",
            "gtk",
        )
        return any(marker in text for marker in markers)

    def _qzone_state_dict(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        state = data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            data["qzone_integration"] = {}
            state = data["qzone_integration"]
        return state

    def _qzone_format_block_until(self, ts: float) -> str:
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
        except Exception:
            return ""

    def _qzone_auto_publish_block_reason(self, state: dict[str, Any] | None = None, *, now: float | None = None) -> str:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return ""
        current = _now_ts() if now is None else float(now)
        until = _safe_float(state.get("auth_block_until"), 0)
        if until <= current:
            return ""
        reason = _single_line(state.get("last_auth_failure_reason") or "QQ 空间登录状态异常", 100)
        if (
            ("没有可用的 OneBot 连接" in reason or "未配置手动 QZONE_COOKIE" in reason)
            and callable(getattr(self, "_qzone_find_runtime_bot", None))
            and self._qzone_find_runtime_bot() is not None
        ):
            self._qzone_clear_auth_failure(state)
            return ""
        if "qzonetoken" in reason.lower() or "qzonetoken 未在 H5 首页中找到" in reason:
            self._qzone_clear_auth_failure(state)
            return ""
        until_text = self._qzone_format_block_until(until)
        return f"{reason}，自动说说已暂停到 {until_text}" if until_text else reason

    def _qzone_mark_auth_failure(
        self,
        reason: str,
        *,
        source: str = "",
        cooldown_hours: float = 12.0,
        state: dict[str, Any] | None = None,
        save: bool = True,
    ) -> None:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return
        now = _now_ts()
        clean_reason = _single_line(reason or "QQ 空间登录状态异常", 160)
        last_failed_at = _safe_float(state.get("last_auth_failed_at"), 0)
        previous_count = _safe_int(state.get("auth_failure_count"), 0, 0, 999)
        if last_failed_at and now - last_failed_at > 7 * 24 * 3600:
            previous_count = 0
        failure_count = previous_count + 1
        if failure_count <= 1:
            effective_hours = max(1.0, float(cooldown_hours or 12.0))
            status = "blocked"
        elif failure_count == 2:
            effective_hours = 24.0
            status = "blocked"
        else:
            effective_hours = 24.0 * 7
            status = "stopped"
        cooldown_seconds = effective_hours * 3600.0
        state["last_auth_failed_at"] = now
        state["last_auth_failure_reason"] = clean_reason
        state["last_auth_failure_source"] = _single_line(source, 40)
        state["auth_failure_count"] = failure_count
        state["auth_block_until"] = now + cooldown_seconds
        state["last_auth_status"] = status
        if status == "stopped":
            logger.warning(
                "[PrivateCompanion] QQ 空间认证连续失败,自动说说进入保守等待: count=%s until=%s reason=%s",
                failure_count,
                self._qzone_format_block_until(state["auth_block_until"]),
                clean_reason,
            )
        if save:
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver()
                except Exception:
                    pass

    def _qzone_clear_auth_failure(self, state: dict[str, Any] | None = None) -> None:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return
        changed = False
        for key in (
            "auth_block_until",
            "last_auth_status",
            "auth_failure_count",
            "last_auth_failure_source",
            "last_auth_failure_reason",
        ):
            if key in state:
                state.pop(key, None)
                changed = True
        for key in ("last_life_publish_status", "last_emotional_vent_status"):
            value = str(state.get(key) or "")
            if value.startswith("paused:auth:"):
                state[key] = "ready:auth_ok"
                changed = True
        if changed:
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver()
                except Exception:
                    pass

    async def _qzone_preflight_auto_publish(
        self,
        event: AstrMessageEvent | None,
        *,
        state: dict[str, Any] | None = None,
        source: str = "auto",
    ) -> str:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        block_reason = self._qzone_auto_publish_block_reason(state)
        if block_reason:
            return block_reason
        try:
            cookie_header = await self._qzone_get_cookies(event)
            ctx = self._qzone_context_from_cookies(cookie_header)
            token = ctx.get("qzonetoken") or await self._qzone_ensure_qzonetoken(event, cookie_header=cookie_header, ctx=ctx)
            if not str(token or "").strip():
                state["last_qzonetoken_status"] = "missing:h5_index"
                logger.info("[PrivateCompanion] QQ 空间自动发布预检继续: qzonetoken 未找到,纯文字发布仍可使用 g_tk")
            else:
                state["last_qzonetoken_status"] = "ok"
        except Exception as exc:
            reason = _single_line(exc, 160)
            self._qzone_mark_auth_failure(reason, source=source, state=state, save=False)
            return reason
        self._qzone_clear_auth_failure(state)
        return ""

    async def _qzone_ensure_qzonetoken(
        self,
        event: AstrMessageEvent | None,
        *,
        cookie_header: str,
        ctx: dict[str, Any],
    ) -> str:
        token = str(ctx.get("qzonetoken") or "").strip()
        if token:
            return token
        cache = getattr(self, "_qzone_qzonetoken_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._qzone_qzonetoken_cache = cache
        cache_key = str(ctx.get("uin") or "")
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and _now_ts() - _safe_float(cached.get("at"), 0) < 1800:
            token = str(cached.get("token") or "").strip()
            if token:
                return token

        import aiohttp

        url = "https://h5.qzone.qq.com/mqzone/index"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Cookie": ctx.get("cookie_header") or cookie_header,
            "Referer": f"https://user.qzone.qq.com/{ctx['uin']}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    text = await response.text()
                    if response.status >= 400:
                        logger.info("[PrivateCompanion] QQ 空间 qzonetoken 获取失败: HTTP %s", response.status)
                        return ""
        except Exception as exc:
            logger.info("[PrivateCompanion] QQ 空间 qzonetoken 获取失败: %s", _single_line(exc, 120))
            return ""
        match = re.search(r'window\.shine0callback.*?return\s+"([0-9a-f]+?)";', text, flags=re.S)
        if not match:
            logger.info("[PrivateCompanion] QQ 空间 qzonetoken 未在 H5 首页中找到")
            return ""
        token = match.group(1).strip()
        if token:
            cache[cache_key] = {"token": token, "at": _now_ts()}
            logger.info("[PrivateCompanion] QQ 空间 qzonetoken 已自动获取: uin=%s", ctx.get("uin"))
        return token

    @staticmethod
    def _qzone_find_first(payload: Any, keys: tuple[str, ...], *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
        if payload is None or _depth > 8:
            return None
        if _seen is None:
            _seen = set()
        if isinstance(payload, dict):
            obj_id = id(payload)
            if obj_id in _seen:
                return None
            _seen.add(obj_id)
            normalized = {str(key).lower().replace("-", "_"): value for key, value in payload.items()}
            for key in keys:
                value = normalized.get(str(key).lower().replace("-", "_"))
                if value not in (None, ""):
                    return value
            for value in payload.values():
                found = QzoneMediaMixin._qzone_find_first(value, keys, _depth=_depth + 1, _seen=_seen)
                if found not in (None, ""):
                    return found
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                found = QzoneMediaMixin._qzone_find_first(item, keys, _depth=_depth + 1, _seen=_seen)
                if found not in (None, ""):
                    return found
        return None

    async def _qzone_call_platform_action(self, event: AstrMessageEvent | None, action: str, **kwargs: Any) -> Any:
        if event is None:
            return None
        caller = getattr(self, "_call_platform_action", None)
        if callable(caller):
            try:
                return await caller(event, action, **kwargs)
            except Exception:
                pass
        bot = getattr(event, "bot", None)
        direct = getattr(bot, action, None)
        if callable(direct):
            try:
                maybe = direct(**kwargs)
                return await maybe if hasattr(maybe, "__await__") else maybe
            except Exception:
                pass
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if callable(call_action):
            try:
                maybe = call_action(action, **kwargs)
                return await maybe if hasattr(maybe, "__await__") else maybe
            except Exception:
                return None
        return None

    async def _qzone_resolve_onebot_image_source(self, event: AstrMessageEvent | None, source: str) -> str:
        text = str(source or "").strip()
        if not text or event is None:
            return ""
        if Path(text).exists() or re.match(r"^(?:https?://|file://|data:|base64://)", text, flags=re.I):
            return ""
        attempts = (
            ("get_image", {"file": text}),
            ("get_image", {"file_id": text}),
            ("get_file", {"file": text}),
            ("get_file", {"file_id": text}),
        )
        for action, kwargs in attempts:
            result = await self._qzone_call_platform_action(event, action, **kwargs)
            if not result:
                continue
            for key_group in (
                ("path", "file_path", "local_path", "local_file", "file"),
                ("url", "origin_url", "source_url"),
            ):
                found = self._qzone_find_first(result, key_group)
                candidate = str(found or "").strip()
                if candidate and candidate != text:
                    return candidate
        return ""

    @staticmethod
    def _qzone_guess_image_type(source: str, image_bytes: bytes, content_type: str = "") -> tuple[str, str]:
        probe = (content_type or mimetypes.guess_type(str(source or ""))[0] or "").lower()
        head = image_bytes[:16]
        if head.startswith(b"\x89PNG\r\n\x1a\n") or "png" in probe:
            return "png", "image/png"
        if head.startswith(b"GIF8") or "gif" in probe:
            return "gif", "image/gif"
        if (head.startswith(b"RIFF") and b"WEBP" in image_bytes[:32]) or "webp" in probe:
            return "webp", "image/webp"
        return "jpg", "image/jpeg"

    @staticmethod
    def _qzone_supported_image_header(image_bytes: bytes) -> bool:
        head = image_bytes[:16]
        return bool(
            head.startswith(b"\xff\xd8\xff")
            or head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"GIF8")
            or head.startswith(b"BM")
            or (head.startswith(b"RIFF") and b"WEBP" in image_bytes[:32])
        )

    def _qzone_validate_image_bytes(self, image_bytes: bytes, *, filename: str = "") -> None:
        size = len(image_bytes or b"")
        if size <= 0:
            raise QzoneIntegrationError("图片校验失败", "图片内容为空")
        max_bytes = QZONE_REMOTE_IMAGE_MAX_BYTES
        if size > max_bytes:
            raise QzoneIntegrationError("图片校验失败", f"图片过大，超过 {max_bytes // 1024 // 1024}MB")
        if not self._qzone_supported_image_header(image_bytes):
            raise QzoneIntegrationError("图片校验失败", f"不是 QQ 空间支持的图片文件：{_single_line(filename, 80) or 'unknown'}")
        try:
            from PIL import Image as PILImage
            import io

            with PILImage.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
            if min(width, height) < 16:
                raise QzoneIntegrationError("图片校验失败", f"图片尺寸过小：{width}x{height}")
        except QzoneIntegrationError:
            raise
        except Exception:
            # PIL may be unavailable in some AstrBot environments; magic header validation still protects the upload.
            return

    @staticmethod
    def _qzone_is_unsafe_media_host(host: str) -> bool:
        normalized = str(host or "").strip().lower().rstrip(".")
        if not normalized or normalized in QZONE_UNSAFE_MEDIA_HOSTS or normalized.endswith(".localhost"):
            return True
        try:
            address = ipaddress.ip_address(normalized.strip("[]"))
        except ValueError:
            return False
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    @classmethod
    def _qzone_remote_media_url_allowed(cls, source: str) -> bool:
        parsed = urlparse(str(source or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return False
        host = parsed.hostname
        if not host or cls._qzone_is_unsafe_media_host(host):
            return False
        try:
            ipaddress.ip_address(host.strip("[]"))
            return True
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host.rstrip("."), None, type=socket.SOCK_STREAM)
        except OSError:
            return False
        addresses = {item[4][0] for item in infos if item and item[4]}
        return bool(addresses) and not any(cls._qzone_is_unsafe_media_host(address) for address in addresses)

    @classmethod
    def _qzone_safe_redirect_url(cls, current_url: str, location: str) -> str:
        if not location:
            return ""
        target = urljoin(current_url, location)
        return target if cls._qzone_remote_media_url_allowed(target) else ""

    async def _qzone_load_image_bytes(
        self,
        event: AstrMessageEvent | None,
        source: Any,
        *,
        cookie_header: str,
    ) -> tuple[bytes, str, str]:
        import aiohttp

        if isinstance(source, bytes):
            return source, "image.jpg", "image/jpeg"
        text = str(source or "").strip().strip('"')
        if not text:
            raise QzoneIntegrationError("图片读取失败", "图片路径为空")
        if text.startswith("data:") and "," in text:
            meta, encoded = text.split(",", 1)
            content_type = meta[5:].split(";", 1)[0] or "image/jpeg"
            estimated_size = (len(encoded) * 3) // 4
            if estimated_size > QZONE_REMOTE_IMAGE_MAX_BYTES + 3:
                raise QzoneIntegrationError("图片校验失败", "图片过大，已跳过上传")
            try:
                return base64.b64decode(encoded), f"image.{content_type.rsplit('/', 1)[-1]}", content_type
            except Exception as exc:
                raise QzoneIntegrationError("图片读取失败", f"图片 data URL 解析失败：{_single_line(exc, 80)}") from exc
        if text.startswith("file://"):
            text = unquote(urlparse(text).path or "")
            if re.match(r"^/[A-Za-z]:/", text):
                text = text[1:]
        path = Path(text)
        if path.exists() and path.is_file():
            data = path.read_bytes()
            image_type, content_type = self._qzone_guess_image_type(str(path), data)
            return data, path.name or f"image.{image_type}", content_type
        if re.match(r"^https?://", text, flags=re.I):
            if not self._qzone_remote_media_url_allowed(text):
                raise QzoneIntegrationError("图片读取失败", "图片 URL 指向本机、内网或不可安全解析的地址")
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": f"https://user.qzone.qq.com/{self._qzone_context_from_cookies(cookie_header)['uin']}",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                current_url = text
                for _redirect in range(4):
                    async with session.get(current_url, allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location") or response.headers.get("location") or ""
                            redirected = self._qzone_safe_redirect_url(current_url, location)
                            if not redirected:
                                raise QzoneIntegrationError("图片读取失败", "图片重定向目标不安全，已拒绝下载")
                            current_url = redirected
                            continue
                        if response.status >= 400:
                            raise QzoneIntegrationError("图片读取失败", f"图片下载失败 HTTP {response.status}")
                        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                        length = _safe_int(response.headers.get("Content-Length"), 0, 0)
                        if length and length > QZONE_REMOTE_IMAGE_MAX_BYTES:
                            raise QzoneIntegrationError("图片校验失败", "图片过大，已跳过上传")
                        data = await response.read()
                        if len(data) > QZONE_REMOTE_IMAGE_MAX_BYTES:
                            raise QzoneIntegrationError("图片校验失败", "图片过大，已跳过上传")
                        image_type, guessed_type = self._qzone_guess_image_type(current_url, data, content_type)
                        name = Path(urlparse(current_url).path).name or f"image.{image_type}"
                        return data, name, content_type or guessed_type
                raise QzoneIntegrationError("图片读取失败", "图片重定向次数过多，已停止下载")
        resolved = await self._qzone_resolve_onebot_image_source(event, text)
        if resolved and resolved != text:
            return await self._qzone_load_image_bytes(event, resolved, cookie_header=cookie_header)
        raise QzoneIntegrationError("图片读取失败", f"无法读取图片：{_single_line(text, 120)}")

    @staticmethod
    def _qzone_extract_pic_bo(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        for key in ("bo", "pic_bo", "picbo"):
            values = query.get(key)
            if values and values[0]:
                return str(values[0]).strip()
        match = re.search(r"(?:^|[?&])(?:bo|pic_bo|picbo)=([^&\s]+)", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _qzone_build_richval(upload_payload: dict[str, Any]) -> str:
        albumid = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("albumid", "album_id")) or "").strip()
        lloc = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("lloc",)) or "").strip()
        sloc = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("sloc",)) or "").strip()
        image_type = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("type", "phototype", "image_type")) or "").strip() or "1"
        height = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("height", "h")) or "").strip() or "0"
        width = str(QzoneMediaMixin._qzone_find_first(upload_payload, ("width", "w")) or "").strip() or "0"
        if albumid and lloc and sloc:
            return ",".join(["", albumid, lloc, sloc, image_type, height, width, "", height, width])
        return ""

    async def _qzone_upload_image(
        self,
        event: AstrMessageEvent | None,
        source: Any,
        *,
        cookie_header: str,
        ctx: dict[str, Any],
    ) -> dict[str, str]:
        image_bytes, filename, content_type = await self._qzone_load_image_bytes(event, source, cookie_header=cookie_header)
        return await self._qzone_upload_image_bytes(
            event,
            image_bytes,
            filename=filename,
            content_type=content_type,
            source=source,
            cookie_header=cookie_header,
            ctx=ctx,
        )

    async def _qzone_upload_image_bytes(
        self,
        event: AstrMessageEvent | None,
        image_bytes: bytes,
        *,
        filename: str,
        content_type: str,
        source: Any,
        cookie_header: str,
        ctx: dict[str, Any],
    ) -> dict[str, str]:
        if not image_bytes:
            raise QzoneIntegrationError("图片读取失败", "图片内容为空")
        self._qzone_validate_image_bytes(image_bytes, filename=filename)
        image_type, _ = self._qzone_guess_image_type(filename, image_bytes, content_type)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        referrer = f"https://user.qzone.qq.com/{ctx['uin']}"
        payload = await self._qzone_request(
            event,
            "POST",
            QZONE_IMAGE_UPLOAD_URL,
            params={"g_tk": ctx["gtk"]},
            data={
                "filename": filename,
                "uin": ctx["uin"],
                "skey": ctx.get("skey") or ctx.get("p_skey") or "",
                "p_skey": ctx.get("p_skey") or ctx.get("skey") or "",
                "zzpaneluin": ctx["uin"],
                "p_uin": ctx["uin"],
                "qzonetoken": ctx.get("qzonetoken") or "",
                "zzpanelkey": "",
                "uploadtype": "1",
                "albumtype": "7",
                "exttype": "0",
                "refer": "shuoshuo",
                "output_type": "json",
                "charset": "utf-8",
                "output_charset": "utf-8",
                "upload_hd": "1",
                "hd_width": "2048",
                "hd_height": "10000",
                "hd_quality": "96",
                "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                "url": f"{QZONE_IMAGE_UPLOAD_URL}?g_tk={ctx['gtk']}",
                "base64": "1",
                "picfile": encoded,
                "qzreferrer": referrer,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Origin": "https://user.qzone.qq.com",
                "Referer": referrer,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout_seconds=60.0,
            cookie_header=cookie_header,
        )
        code = payload.get("code", payload.get("ret", payload.get("_raw_code", 0)))
        if code not in {0, "0", None, ""}:
            raise QzoneIntegrationError("图片上传失败", _single_line(payload.get("message") or payload.get("msg") or payload.get("_raw_message") or f"code={code}", 160))
        richval = self._qzone_build_richval(payload)
        pic_bo = str(self._qzone_find_first(payload, ("pic_bo", "picbo", "bo")) or "").strip()
        if not pic_bo:
            pic_bo = self._qzone_extract_pic_bo(self._qzone_find_first(payload, ("url", "origin_url", "pre", "raw_url")))
        if not richval:
            raise QzoneIntegrationError("图片上传失败", "图片上传成功但未返回 richval 所需字段")
        if not pic_bo:
            raise QzoneIntegrationError("图片上传失败", "图片上传成功但未返回 pic_bo")
        return {
            "source": _single_line(source, 160),
            "filename": filename,
            "type": image_type,
            "richval": richval,
            "pic_bo": pic_bo,
        }

    async def _qzone_image_sources_from_event(self, event: AstrMessageEvent | None) -> list[str]:
        if event is None:
            return []
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        extractor = getattr(self, "_extract_image_sources_from_message_obj", None)
        message_obj = getattr(event, "message_obj", None)
        if callable(extractor):
            try:
                for source in extractor(message_obj):
                    add(source)
            except Exception:
                pass
            try:
                for source in extractor(getattr(message_obj, "message", None) if message_obj is not None else None):
                    add(source)
            except Exception:
                pass
        components_getter = getattr(self, "_event_components", None)
        components: list[Any] = []
        if callable(components_getter):
            try:
                components = list(components_getter(event) or [])
            except Exception:
                components = []
        image_component_count = 0
        image_source_getter = getattr(self, "_image_component_source", None)
        for comp in components:
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name != "image":
                continue
            image_component_count += 1
            if callable(image_source_getter):
                try:
                    add(image_source_getter(comp))
                except Exception:
                    pass
            converter = getattr(comp, "convert_to_file_path", None)
            if callable(converter):
                try:
                    maybe = converter()
                    add(await maybe if hasattr(maybe, "__await__") else maybe)
                except Exception:
                    pass
            data = getattr(comp, "data", None)
            if not isinstance(data, dict) and isinstance(comp, dict):
                data = comp.get("data") if isinstance(comp.get("data"), dict) else {}
            for key in ("url", "origin_url", "source_url", "src", "path", "file", "image_path", "file_path", "local_path"):
                if isinstance(data, dict):
                    add(data.get(key))
                if isinstance(comp, dict):
                    add(comp.get(key))
                else:
                    add(getattr(comp, key, None))
        reply_image_getter = getattr(self, "_find_reply_image_sources_for_event", None)
        if callable(reply_image_getter):
            try:
                for source in await reply_image_getter(event):
                    add(source)
            except Exception as exc:
                logger.info("[PrivateCompanion] QQ 空间引用图片读取失败: %s", _single_line(exc, 120))
        normalized: list[str] = []
        for source in sources:
            resolved = await self._qzone_resolve_onebot_image_source(event, source)
            candidate = resolved or source
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        if image_component_count <= 1 and len(normalized) > 1:
            def rank(source: str) -> tuple[int, int]:
                text = str(source or "").strip()
                path_text = text[len("file://"):] if text.startswith("file://") else text
                path = Path(path_text)
                if path.exists() and path.is_file():
                    suffix_score = 3 if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else 2
                    return (suffix_score, len(str(path)))
                if re.match(r"^https?://", text, flags=re.I):
                    return (1, len(text))
                return (0, len(text))

            best = sorted(normalized, key=rank, reverse=True)[0]
            logger.info("[PrivateCompanion] QQ 空间单图消息解析到多个来源,已选择一个: candidates=%s chosen=%s", len(normalized), _single_line(best, 120))
            return [best]
        return normalized[:9]

    @staticmethod
    def _qzone_select_image_sources(command_text: str, image_sources: list[str]) -> tuple[list[str], str]:
        sources = [str(item).strip() for item in list(image_sources or []) if str(item or "").strip()]
        if len(sources) <= 1:
            return sources, ""
        text = _single_line(command_text, 120)
        if re.search(r"(全部|所有|都发|全发|多图|这几张|这些图)", text):
            return sources[:9], ""
        cn_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        match = re.search(r"(?:第)?([1-9]\d*)\s*张", text)
        if not match:
            match = re.search(r"第([一二两三四五六七八九])张", text)
        if match:
            raw = match.group(1)
            index = int(raw) if raw.isdigit() else cn_digits.get(raw, 0)
            if 1 <= index <= len(sources):
                return [sources[index - 1]], ""
            return [], f"检测到 {len(sources)} 张图片，但你指定的第 {index} 张不存在。"
        return [], (
            f"检测到 {len(sources)} 张图片。为避免误发公开说说，请补充："
            f"“陪伴 发说说 全部 <正文>” 或 “陪伴 发说说 第1张 <正文>”。"
        )

    @staticmethod
    def _qzone_clean_publish_text(command_text: str) -> str:
        text = _single_line(command_text, 300)
        text = re.sub(r"^(?:全部|所有|都发|全发|多图|这几张|这些图)\s*", "", text)
        text = re.sub(r"^(?:第)?[1-9]\d*\s*张\s*", "", text)
        text = re.sub(r"^第[一二两三四五六七八九]张\s*", "", text)
        return _single_line(text, 300)

    async def _qzone_publish_post(
        self,
        event: AstrMessageEvent | None = None,
        *,
        text: str = "",
        images: list[str] | None = None,
    ) -> Any:
        cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        ctx["qzonetoken"] = ctx.get("qzonetoken") or await self._qzone_ensure_qzonetoken(event, cookie_header=cookie_header, ctx=ctx)
        content = _single_line(text, 300)
        if not content and not images:
            raise RuntimeError("说说内容为空")
        publish_content = content or (" " if images else "")
        referrer = f"https://user.qzone.qq.com/{ctx['uin']}"
        prepared_images: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for image in list(images or [])[:12]:
            image_bytes, filename, content_type = await self._qzone_load_image_bytes(event, image, cookie_header=cookie_header)
            self._qzone_validate_image_bytes(image_bytes, filename=filename)
            digest = hashlib.sha256(image_bytes).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            prepared_images.append(
                {
                    "source": image,
                    "bytes": image_bytes,
                    "filename": filename,
                    "content_type": content_type,
                    "sha256": digest,
                }
            )
            if len(prepared_images) >= 9:
                break
        uploaded_images: list[dict[str, str]] = []
        seen_pic_bos: set[str] = set()
        for image in prepared_images:
            uploaded = await self._qzone_upload_image_bytes(
                event,
                image["bytes"],
                filename=str(image.get("filename") or "image.jpg"),
                content_type=str(image.get("content_type") or "image/jpeg"),
                source=image.get("source"),
                cookie_header=cookie_header,
                ctx=ctx,
            )
            pic_bo = str(uploaded.get("pic_bo") or "")
            if pic_bo and pic_bo in seen_pic_bos:
                continue
            if pic_bo:
                seen_pic_bos.add(pic_bo)
            uploaded_images.append(uploaded)
        pic_bos = [item.get("pic_bo", "") for item in uploaded_images if item.get("pic_bo")]
        richvals = [item.get("richval", "") for item in uploaded_images if item.get("richval")]
        base_data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": publish_content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": 1,
            "to_sign": 0,
            "hostuin": ctx["uin"],
            "code_version": "1",
            "issyncweibo": 0,
            "format": "json",
            "qzreferrer": referrer,
        }
        if uploaded_images:
            base_data.update(
                {
                    "richtype": "1",
                    "subrichtype": "1",
                    "richval": "\t".join(richvals),
                    "pic_bo": ",".join(pic_bos),
                    "pic_template": "",
                }
            )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": referrer,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        endpoint = QZONE_PUBLISH_URL
        payload: dict[str, Any] = {}
        data = dict(base_data)
        payload = await self._qzone_request(
            event,
            "POST",
            endpoint,
            params={"g_tk": ctx["gtk"]},
            data=data,
            headers=headers,
            cookie_header=cookie_header,
        )
        code = payload.get("code", 0)
        if code not in {0, "0"}:
            message = _single_line(payload.get("message") or payload.get("msg") or f"code={code}", 160)
            if str(code) not in message:
                message = _single_line(f"code={code} {message}", 160)
            logger.info(
                "[PrivateCompanion] QQ 空间发布失败: endpoint=%s code=%s msg=%s",
                urlparse(endpoint).netloc,
                code,
                message,
            )
            stage = "Cookie/g_tk" if self._qzone_auth_failure_message(message) else "发布失败"
            raise QzoneIntegrationError(stage, message)
        logger.info(
            "[PrivateCompanion] QQ 空间说说发布成功: endpoint=%s images=%s",
            urlparse(endpoint).netloc,
            len(richvals),
        )
        return SimpleNamespace(
            tid=str(payload.get("tid") or ""),
            uin=int(ctx["uin"]),
            name=str(ctx["uin"]),
            text=content,
            images=uploaded_images or (images or []),
            create_time=payload.get("now") or int(time.time()),
            status="approved",
        )

    async def _qzone_verify_published_post(
        self,
        event: AstrMessageEvent | None,
        post: Any,
        *,
        expected_text: str,
        expected_images: int,
    ) -> dict[str, Any]:
        tid = str(getattr(post, "tid", "") or "")
        expected_clean = _single_line(expected_text, 80)
        last_error = ""
        for attempt, delay in enumerate((0.0, 0.8, 1.8), start=1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                feeds = await self._qzone_query_feeds(event, target_id=str(getattr(post, "uin", "") or ""), pos=0, num=5, with_detail=False)
            except Exception as exc:
                last_error = _single_line(exc, 120)
                if attempt >= 3:
                    return {"verified": False, "message": f"反查失败：{last_error}"}
                continue
            for feed in feeds:
                feed_tid = str(getattr(feed, "tid", "") or "")
                feed_text = _single_line(getattr(feed, "text", "") or getattr(feed, "rt_con", ""), 120)
                feed_images = len(list(getattr(feed, "images", []) or []))
                tid_match = bool(tid and feed_tid and tid == feed_tid)
                text_match = bool(expected_clean and expected_clean in feed_text)
                image_match = expected_images <= 0 or feed_images >= expected_images
                if tid_match or (image_match and (text_match or not expected_clean)):
                    return {
                        "verified": True,
                        "message": f"已反查到最近说说，图片 {feed_images} 张",
                        "tid": feed_tid or tid,
                        "images": feed_images,
                    }
        if last_error:
            return {"verified": False, "message": f"反查失败：{last_error}"}
        return {"verified": False, "message": "发布接口已返回成功，但最近说说中暂未反查到匹配内容"}

    async def _publish_qzone_text(
        self,
        text: str,
        event: AstrMessageEvent | None = None,
        images: list[str] | None = None,
        auto_generate_image: bool = False,
        publish_reason: str = "manual_publish",
    ) -> dict[str, Any]:
        if not self.enable_qzone_integration:
            return {"success": False, "message": "QQ 空间动态层未启用"}
        content = _single_line(text, 300)
        image_list = [str(item).strip() for item in list(images or []) if str(item or "").strip()]
        if auto_generate_image and content and not image_list:
            image_generator = getattr(self, "_maybe_generate_qzone_publish_image", None)
            if callable(image_generator):
                try:
                    generated = await image_generator(
                        post_text=content,
                        reason="manual_publish",
                        state=self._qzone_state_dict(),
                        force=True,
                    )
                    image_list = [str(item).strip() for item in list(generated or []) if str(item or "").strip()]
                except Exception as exc:
                    logger.info(
                        "[PrivateCompanion] QQ 空间手动发说说自动配图失败,继续纯文字发布: %s",
                        _single_line(exc, 160),
                    )
        if not content and not image_list:
            return {"success": False, "message": "说说内容为空"}

        async def publish_once(candidates: list[str]) -> dict[str, Any]:
            post = await self._qzone_publish_post(event, text=content, images=candidates)
            self._qzone_clear_auth_failure()
            post_images = list(getattr(post, "images", []) or [])
            verification = await self._qzone_verify_published_post(
                event,
                post,
                expected_text=content,
                expected_images=len(post_images),
            )
            verified_images = _safe_int(verification.get("images"), len(post_images), 0, 99)
            result = {
                "success": True,
                "text": _single_line(getattr(post, "text", content), 300) or content,
                "tid": str(getattr(post, "tid", "") or ""),
                "uin": str(getattr(post, "uin", "") or ""),
                "images": post_images,
                "image_count": max(len(post_images), verified_images if bool(verification.get("verified")) else 0),
                "verified": bool(verification.get("verified")),
                "verify_message": verification.get("message") or "",
            }
            recorder = getattr(self, "_qzone_record_published_post", None)
            if callable(recorder):
                try:
                    await recorder(
                        result.get("text") or content,
                        reason=publish_reason,
                        tid=result.get("tid", ""),
                        image_count=_safe_int(result.get("image_count"), 0, 0, 99),
                        verified=bool(result.get("verified")),
                        event=event,
                    )
                except Exception as record_exc:
                    logger.debug("[PrivateCompanion] QQ 空间发布后自我记录失败: %s", _single_line(record_exc, 120))
            return result

        try:
            return await publish_once(image_list)
        except Exception as exc:
            message = _single_line(exc, 180)
            if isinstance(exc, QzoneIntegrationError):
                if exc.stage == "Cookie/g_tk" or self._qzone_auth_failure_message(message):
                    self._qzone_mark_auth_failure(message, source="publish", save=True)
                if content and image_list and exc.stage in {"图片读取失败", "图片校验失败", "图片上传失败"}:
                    logger.warning(
                        "[PrivateCompanion] QQ 空间图片发布失败,尝试降级纯文字: stage=%s msg=%s",
                        exc.stage,
                        message,
                    )
                    try:
                        fallback = await publish_once([])
                        fallback["image_fallback"] = True
                        fallback["image_fallback_stage"] = exc.stage
                        fallback["image_fallback_message"] = message
                        fallback["image_count"] = 0
                        fallback["images"] = []
                        return fallback
                    except Exception as retry_exc:
                        retry_message = _single_line(retry_exc, 180)
                        if isinstance(retry_exc, QzoneIntegrationError) and (
                            retry_exc.stage == "Cookie/g_tk" or self._qzone_auth_failure_message(retry_message)
                        ):
                            self._qzone_mark_auth_failure(retry_message, source="publish", save=True)
                        return {
                            "success": False,
                            "stage": exc.stage,
                            "message": f"{message}；降级纯文字也失败：{retry_message}",
                            "image_fallback_failed": True,
                        }
                return {"success": False, "stage": exc.stage, "message": message}
            lowered = message.lower()
            if "cookie" in lowered or "p_skey" in lowered or "skey" in lowered or "g_tk" in lowered or "登录" in message:
                self._qzone_mark_auth_failure(message, source="publish", save=True)
                return {"success": False, "stage": "Cookie/g_tk", "message": f"Cookie/g_tk：{message}"}
            if "权限" in message or "403" in message:
                return {"success": False, "stage": "权限失败", "message": f"权限失败：{message}"}
            return {"success": False, "stage": "发布失败", "message": f"发布失败：{message}"}

