"""Describe the pictures and clips attached to a QQ Zone post.

The model cannot open a Zone image or video URL, so this module turns them into
short text: pictures through a vision callback or vision chat completion, clips
through deduplicated uniform ffmpeg frames plus an optional injected audio
transcriber. Everything is best effort: a broken CDN object becomes a short note
instead of failing the read.

Configuration is read from the host, never copied into this plugin's settings:

* ``ASTRBOT_ROOT`` / ``ASTRBOT_DATA_ROOT`` locate ``data/cmd_config.json``.
* ``COMPANION_QZONE_VISION_BASE`` / ``_KEY`` / ``_MODEL`` override the vision
  provider when the host config is not the right source.
* ``COMPANION_QZONE_FFMPEG`` overrides the ffmpeg binary path or command name.
"""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
from urllib.parse import urljoin, urlsplit
import urllib.request

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
REFERER = "https://user.qzone.qq.com/"

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024
MAX_IMAGES = 20
MAX_VIDEOS = 2
MAX_VIDEO_SECONDS = 180
DESCRIBE_SIZE = 1024
DEFAULT_MAX_VISION_TOKENS = 1024
DEFAULT_MEDIA_BUDGET_SECONDS = 35.0
DEFAULT_MEDIA_CONCURRENCY = 3
MEDIA_CACHE_MAX_ITEMS = 256
MAX_REDIRECTS = 4
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_URL_CHARS = 4096

DESCRIBE_PROMPT = "用中文简短描述这张图：是什么、有什么、在做什么。一两句话，别超过 60 字。"
FRAME_PROMPT = "这是视频第 {index} 个画面。用中文一句话说画面里是什么，别超过 40 字。"

_ENV_VAR_PATTERN = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_INCOMPATIBLE_PROVIDER_MARKERS = (
    "tts", "stt", "speech", "audio", "whisper", "embed", "rerank",
    "dify", "coze", "fastgpt", "anthropic", "googlegenai", "vertex",
)
_CHAT_PROVIDER_TYPES = {"chat_completion", "chat", "llm", "vision"}
_VISION_MODALITIES = {"image", "vision", "multimodal"}


class _BoundedLRUCache:
    """Thread-safe bounded LRU cache for media descriptions."""

    def __init__(self, maxsize: int = MEDIA_CACHE_MAX_ITEMS) -> None:
        self.maxsize = max(1, int(maxsize))
        self._data: OrderedDict[object, object] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: object) -> object | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: object, value: object) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


_MEDIA_CACHE = _BoundedLRUCache(maxsize=MEDIA_CACHE_MAX_ITEMS)


def clear_media_cache() -> None:
    """Clear the bounded in-process media description cache."""
    _MEDIA_CACHE.clear()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable urllib automatic redirect following so every hop is re-validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Referer": REFERER}


def _data_root() -> Path | None:
    configured = os.environ.get("ASTRBOT_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    root = os.environ.get("ASTRBOT_ROOT", "").strip()
    if root:
        return Path(root) / "data"
    parents = Path(__file__).resolve().parents
    return parents[4] if len(parents) > 4 else None


def _resolve_ffmpeg() -> str:
    """Resolve the ffmpeg executable at runtime instead of import time."""
    configured = os.environ.get("COMPANION_QZONE_FFMPEG", "").strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        return shutil.which(configured) or ""
    return shutil.which("ffmpeg") or ""


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None and _is_forbidden_ip(ip_obj.ipv4_mapped):
            return True
        if ip_obj.sixtofour is not None and _is_forbidden_ip(ip_obj.sixtofour):
            return True
        if ip_obj.teredo is not None:
            server, client = ip_obj.teredo
            if _is_forbidden_ip(server) or _is_forbidden_ip(client):
                return True
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or not ip_obj.is_global
    )


def _validate_remote_url(url: str) -> str:
    """Validate that *url* is a public http/https target safe against SSRF."""
    if not isinstance(url, str):
        raise ValueError("地址无效")
    cleaned = url.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_URL_CHARS
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned)
    ):
        raise ValueError("地址无效")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        raise ValueError("地址无效") from None
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("不支持的协议")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("地址无效")
    hostname = (parsed.hostname or "").strip().rstrip(".")
    if not hostname:
        raise ValueError("地址缺少主机名")
    lowered_host = hostname.lower()
    if lowered_host == "localhost" or lowered_host.endswith(".localhost") or "%" in hostname:
        raise ValueError("禁止访问内网地址")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _is_forbidden_ip(literal_ip):
            raise ValueError("禁止访问内网地址")
        return cleaned
    target_port = port or (443 if scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(hostname, target_port, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("域名解析失败") from None
    if not addr_infos:
        raise ValueError("域名解析失败")
    for _family, _socktype, _proto, _canonname, sockaddr in addr_infos:
        if not sockaddr:
            continue
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            resolved_ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise ValueError("地址无效") from None
        if _is_forbidden_ip(resolved_ip):
            raise ValueError("禁止访问内网地址")
    return cleaned


def _read_bounded_response(response, *, limit: int) -> tuple[bytes | None, str]:
    if limit <= 0:
        return None, "文件太大"
    headers = getattr(response, "headers", None)
    raw_length = headers.get("Content-Length") if headers is not None else None
    if raw_length is not None:
        try:
            declared = int(str(raw_length).strip())
        except (TypeError, ValueError):
            declared = None
        if declared is not None and declared > limit:
            return None, "文件太大"
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = (limit - total) + 1
        chunk = response.read(min(DOWNLOAD_CHUNK_BYTES, remaining))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None, "文件太大"
        chunks.append(chunk)
    return b"".join(chunks), ""


def download(url, *, limit, timeout: float = 45.0, max_redirects: int = MAX_REDIRECTS):
    """Fetch one public CDN object with SSRF, redirect, and byte-limit guards.

    Returns ``(bytes, note)``.
    """
    if not isinstance(url, str):
        return None, "地址无效"
    current_url = url.strip()
    hops = max(0, int(max_redirects))
    per_hop_timeout = max(0.05, float(timeout))
    for hop in range(hops + 1):
        try:
            current_url = _validate_remote_url(current_url)
        except ValueError as exc:
            return None, str(exc) or "地址无效"
        request = urllib.request.Request(current_url, headers=_headers())
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=per_hop_timeout) as response:
                return _read_bounded_response(response, limit=int(limit))
        except urllib.error.HTTPError as exc:
            status = exc.code
            location = exc.headers.get("Location") if exc.headers else None
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            if status in (301, 302, 303, 307, 308):
                if not location:
                    return None, f"HTTP {status}"
                if hop >= hops:
                    return None, "重定向次数过多"
                current_url = urljoin(current_url, str(location).strip())
                continue
            return None, f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001 - any network fault becomes a note
            return None, type(exc).__name__
    return None, "重定向次数过多"


def shrink(data, *, size=DESCRIBE_SIZE):
    """Re-encode an image small enough for a vision call. Returns base64 or None."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if max(image.size) > size:
                image.thumbnail((size, size), Image.LANCZOS)
            has_alpha = image.mode in ("RGBA", "LA", "PA") or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgb = background
            else:
                rgb = image.convert("RGB")
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=82)
            return base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a corrupt picture is not a failure
        return None


def _resolve_secret_key(raw_key: object) -> str:
    if isinstance(raw_key, str):
        items = [raw_key]
    elif isinstance(raw_key, list):
        items = raw_key
    else:
        return ""
    for item in items:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate:
            continue
        match = _ENV_VAR_PATTERN.match(candidate)
        if match:
            resolved = os.environ.get(match.group(1), "").strip()
            if resolved:
                return resolved
            continue
        return candidate
    return ""


def _normalize_api_base(raw_base: object) -> str:
    base = str(raw_base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.lower().endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError:
        return ""
    if (parsed.scheme or "").lower() not in ("http", "https") or not parsed.hostname:
        return ""
    return base


def _extract_model_name(entry: dict | None) -> str:
    if not isinstance(entry, dict):
        return ""
    for field in ("vision_model", "model", "model_id"):
        value = str(entry.get(field) or "").strip()
        if value:
            return value
    model_cfg = entry.get("model_config")
    if isinstance(model_cfg, dict):
        for field in ("vision_model", "model", "model_id"):
            value = str(model_cfg.get(field) or "").strip()
            if value:
                return value
    return ""


def _is_compatible_chat_entry(entry: dict, source: dict | None = None) -> tuple[bool, bool]:
    """Return ``(compatible, explicitly_vision)`` for a provider/source pair."""
    if entry.get("enable") is False:
        return False, False
    if isinstance(source, dict) and source.get("enable") is False:
        return False, False

    provider_type = str(
        entry.get("provider_type") or (source or {}).get("provider_type") or ""
    ).strip().lower()
    if provider_type and provider_type not in _CHAT_PROVIDER_TYPES:
        return False, False

    entry_type = str(
        entry.get("type") or (source or {}).get("type") or ""
    ).strip().lower()
    if entry_type and any(marker in entry_type for marker in _INCOMPATIBLE_PROVIDER_MARKERS):
        return False, False

    modalities_raw = entry.get("modalities")
    if modalities_raw is None and isinstance(source, dict):
        modalities_raw = source.get("modalities")
    explicitly_vision = bool(
        entry.get("vision_model")
        or entry.get("support_vision") is True
        or entry.get("supports_vision") is True
        or (isinstance(source, dict) and (source.get("vision_model") or source.get("support_vision") is True))
    )
    if isinstance(modalities_raw, list) and modalities_raw:
        normalized_mods = {str(item).strip().lower() for item in modalities_raw if str(item).strip()}
        if normalized_mods:
            if normalized_mods.intersection(_VISION_MODALITIES):
                explicitly_vision = True
            else:
                return False, False
    return True, explicitly_vision


def _vision_credentials():
    """Read env overrides, else resolve a vision-capable provider from AstrBot cmd_config.json."""
    env_base = _normalize_api_base(os.environ.get("COMPANION_QZONE_VISION_BASE", ""))
    env_key = os.environ.get("COMPANION_QZONE_VISION_KEY", "").strip()
    env_model = os.environ.get("COMPANION_QZONE_VISION_MODEL", "").strip()
    if env_base and env_key and env_model:
        return {"api_base": env_base, "key": env_key, "model": env_model}

    root = _data_root()
    if root is None:
        return None
    path = root / "cmd_config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(config, dict):
        return None

    raw_sources = config.get("provider_sources") or []
    sources_by_id: dict[str, dict] = {}
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            sid = str(source.get("id") or "").strip()
            if sid and sid not in sources_by_id:
                sources_by_id[sid] = source

    default_provider_id = ""
    provider_settings = config.get("provider_settings")
    if isinstance(provider_settings, dict):
        default_provider_id = str(provider_settings.get("default_provider_id") or "").strip()
    if not default_provider_id:
        default_provider_id = str(config.get("default_provider_id") or "").strip()

    # 1. AstrBot v4.x format: config["provider"] linked to config["provider_sources"].
    raw_providers = config.get("provider") or []
    candidates: list[tuple[int, int, dict[str, str]]] = []
    if isinstance(raw_providers, list):
        for order, prov in enumerate(raw_providers):
            if not isinstance(prov, dict):
                continue
            source_id = str(prov.get("provider_source_id") or "").strip()
            source = sources_by_id.get(source_id) if source_id else None
            compatible, explicitly_vision = _is_compatible_chat_entry(prov, source)
            if not compatible:
                continue
            base = env_base or _normalize_api_base(
                prov.get("api_base")
                or prov.get("base_url")
                or (source or {}).get("api_base")
                or (source or {}).get("base_url")
            )
            key = env_key or (
                _resolve_secret_key(prov.get("key"))
                or _resolve_secret_key(prov.get("api_key"))
                or _resolve_secret_key((source or {}).get("key"))
                or _resolve_secret_key((source or {}).get("api_key"))
            )
            model = env_model or _extract_model_name(prov) or _extract_model_name(source)
            if not (base and key and model):
                continue
            pid = str(prov.get("id") or "").strip()
            is_default = bool(default_provider_id and pid == default_provider_id)
            rank = (0 if explicitly_vision else 1) * 2 + (0 if is_default else 1)
            candidates.append((rank, order, {"api_base": base, "key": key, "model": model}))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    # 2. Legacy compatibility: provider_sources carrying model / vision_model directly.
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            compatible, _ = _is_compatible_chat_entry(source)
            if not compatible:
                continue
            base = env_base or _normalize_api_base(source.get("api_base") or source.get("base_url"))
            key = env_key or _resolve_secret_key(source.get("key")) or _resolve_secret_key(source.get("api_key"))
            model = env_model or _extract_model_name(source)
            if base and key and model:
                return {"api_base": base, "key": key, "model": model}
    return None


def _extract_choice_text(answer: object) -> str:
    if not isinstance(answer, dict):
        return ""
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        return " ".join(parts).strip()
    return str(content or "").strip()


def _invoke_vision_callback(vision_callback, payload: str, prompt: str, *, max_tokens: int) -> object:
    try:
        sig = inspect.signature(vision_callback)
        accepts_max_tokens = "max_tokens" in sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_max_tokens = False
    if accepts_max_tokens:
        return vision_callback(payload, prompt, max_tokens=max_tokens)
    return vision_callback(payload, prompt)


def _describe_payload_http(
    payload: str,
    *,
    prompt: str,
    credentials: dict[str, str],
    max_tokens: int,
    timeout: float,
) -> str:
    effective_tokens = max(DEFAULT_MAX_VISION_TOKENS, int(max_tokens or DEFAULT_MAX_VISION_TOKENS))
    body = {
        "model": credentials["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{payload}"}},
                ],
            }
        ],
        "max_tokens": effective_tokens,
    }
    request = urllib.request.Request(
        credentials["api_base"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + credentials["key"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, float(timeout))) as response:
            answer = json.loads(response.read().decode("utf-8"))
        return _extract_choice_text(answer)
    except Exception as exc:  # noqa: BLE001
        logger.info("[Qzone] vision call failed: %s", type(exc).__name__)
        return ""


def describe_image(
    data,
    *,
    prompt: str = DESCRIBE_PROMPT,
    vision_callback=None,
    credentials: dict[str, str] | None = None,
    max_tokens: int = DEFAULT_MAX_VISION_TOKENS,
    timeout: float = 60.0,
    use_cache: bool = True,
) -> str:
    """Turn one picture into a short Chinese description."""
    if not data:
        return ""
    effective_tokens = max(DEFAULT_MAX_VISION_TOKENS, int(max_tokens or DEFAULT_MAX_VISION_TOKENS))
    content_key = ("sha256", hashlib.sha256(data).hexdigest(), prompt)
    if use_cache:
        cached = _MEDIA_CACHE.get(content_key)
        if isinstance(cached, str) and cached:
            return cached

    if vision_callback is None:
        resolved_credentials = credentials if credentials is not None else _vision_credentials()
        if not resolved_credentials:
            return ""
    else:
        resolved_credentials = None

    payload = shrink(data)
    if not payload:
        return ""

    if vision_callback is not None:
        try:
            raw_result = _invoke_vision_callback(
                vision_callback, payload, prompt, max_tokens=effective_tokens
            )
            if inspect.isawaitable(raw_result):
                raw_result = asyncio.run(raw_result)
            text = str(raw_result or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info("[Qzone] vision callback failed: %s", type(exc).__name__)
            return ""
    else:
        text = _describe_payload_http(
            payload,
            prompt=prompt,
            credentials=resolved_credentials,
            max_tokens=effective_tokens,
            timeout=timeout,
        )

    if text and use_cache:
        _MEDIA_CACHE.set(content_key, text)
    return text


async def describe_image_async(
    data,
    *,
    prompt: str = DESCRIBE_PROMPT,
    vision_callback=None,
    credentials: dict[str, str] | None = None,
    max_tokens: int = DEFAULT_MAX_VISION_TOKENS,
    timeout: float = 60.0,
    use_cache: bool = True,
) -> str:
    """Async variant supporting both async and sync vision callbacks cleanly."""
    if not data:
        return ""
    effective_tokens = max(DEFAULT_MAX_VISION_TOKENS, int(max_tokens or DEFAULT_MAX_VISION_TOKENS))
    content_key = ("sha256", hashlib.sha256(data).hexdigest(), prompt)
    if use_cache:
        cached = _MEDIA_CACHE.get(content_key)
        if isinstance(cached, str) and cached:
            return cached

    if vision_callback is None:
        return await asyncio.to_thread(
            describe_image,
            data,
            prompt=prompt,
            credentials=credentials,
            max_tokens=effective_tokens,
            timeout=timeout,
            use_cache=use_cache,
        )

    payload = await asyncio.to_thread(shrink, data)
    if not payload:
        return ""
    try:
        effective_timeout = max(0.05, float(timeout))
        if inspect.iscoroutinefunction(vision_callback):
            raw_result = await asyncio.wait_for(
                _invoke_vision_callback(vision_callback, payload, prompt, max_tokens=effective_tokens),
                timeout=effective_timeout,
            )
        else:
            raw_result = await asyncio.wait_for(
                asyncio.to_thread(
                    _invoke_vision_callback, vision_callback, payload, prompt, max_tokens=effective_tokens
                ),
                timeout=effective_timeout,
            )
            if inspect.isawaitable(raw_result):
                raw_result = await asyncio.wait_for(raw_result, timeout=effective_timeout)
        text = str(raw_result or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("[Qzone] vision callback failed: %s", type(exc).__name__)
        return ""

    if text and use_cache:
        _MEDIA_CACHE.set(content_key, text)
    return text


def _ffmpeg(*arguments, timeout: float = 60.0) -> bool:
    ffmpeg_bin = _resolve_ffmpeg()
    if not ffmpeg_bin:
        return False
    try:
        completed = subprocess.run(
            [ffmpeg_bin, "-y", *arguments],
            capture_output=True,
            timeout=max(0.5, float(timeout)),
        )
        return completed.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _sample_timestamps(seconds: float, count: int) -> list[float]:
    """Choose deduplicated sampling timestamps including a sample near the first frame."""
    target_count = max(1, int(count))
    duration = float(seconds) if seconds and seconds > 0 else 8.0
    if duration <= 0.4:
        return [0.0]
    end = max(0.0, duration - 0.2)
    start = min(0.2, round(end * 0.1, 3))
    if target_count == 1 or end <= start:
        return [round(start, 3)]
    span = end - start
    effective_count = min(target_count, max(1, int(span / 0.4) + 1))
    if effective_count == 1:
        return [round(start, 3)]
    stamps: list[float] = []
    seen: set[float] = set()
    for idx in range(effective_count):
        raw = start + span * (idx / (effective_count - 1))
        rounded = round(max(0.0, min(end, raw)), 3)
        if rounded not in seen:
            seen.add(rounded)
            stamps.append(rounded)
    return stamps or [round(start, 3)]


def video_frames(data, *, count: int = 3, seconds: float = 0, timeout: float = 60.0):
    """Sample a clip into deduplicated stills. Returns ``(frames, note)``."""
    if not _resolve_ffmpeg():
        return [], "抽帧失败"
    stamps = _sample_timestamps(seconds, count)
    deadline = time.monotonic() + max(0.5, float(timeout))
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "clip.mp4")
        with open(source, "wb") as handle:
            handle.write(data)
        frames = []
        for index, stamp in enumerate(stamps, start=1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                break
            target = os.path.join(folder, f"frame_{index}.jpg")
            if _ffmpeg(
                "-ss", f"{stamp:.3f}", "-i", source, "-frames:v", "1",
                "-vf", "scale=640:-2", target, timeout=remaining,
            ):
                try:
                    with open(target, "rb") as handle:
                        frames.append(handle.read())
                except OSError:
                    pass
        if not frames:
            return [], "抽帧失败"
        return frames, ""


def _soundtrack_available(*, transcriber=None) -> bool:
    return callable(transcriber) and bool(_resolve_ffmpeg())


def video_soundtrack(data, *, transcriber=None, timeout: float = 60.0) -> str:
    """Transcribe a clip's audio via an explicitly injected transcriber callable."""
    if not _soundtrack_available(transcriber=transcriber):
        return ""
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "clip.mp4")
        target = os.path.join(folder, "audio.wav")
        with open(source, "wb") as handle:
            handle.write(data)
        if not _ffmpeg(
            "-i", source, "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", target, timeout=timeout,
        ):
            return ""
        try:
            raw_text = transcriber(target)
            if inspect.isawaitable(raw_text):
                raw_text = asyncio.run(raw_text)
        except Exception:  # noqa: BLE001
            return ""
        text = str(raw_text or "").strip()
        if not text or text in ("/sil", "[sil]", "sil"):
            return ""
        return text[:300]


def _extract_image_url(item) -> str:
    if isinstance(item, (tuple, list)):
        return str(item[0]) if item else ""
    if hasattr(item, "url"):
        return str(getattr(item, "url") or "")
    return str(item or "")


def _extract_video_spec(item) -> tuple[str, int]:
    if isinstance(item, (tuple, list)):
        url = str(item[0]) if item else ""
        try:
            seconds = int(item[2] or 0) if len(item) > 2 else 0
        except (TypeError, ValueError):
            seconds = 0
        return url, seconds
    url = str(getattr(item, "url", "") or "") if hasattr(item, "url") else ""
    try:
        seconds = int(getattr(item, "seconds", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0
    return url, seconds


def _remaining(deadline: float | None) -> float:
    if deadline is None:
        return DEFAULT_MEDIA_BUDGET_SECONDS
    return deadline - time.monotonic()


async def describe_post_async(
    post,
    *,
    vision_callback=None,
    transcriber=None,
    timeout_budget: float = DEFAULT_MEDIA_BUDGET_SECONDS,
    max_concurrency: int = DEFAULT_MEDIA_CONCURRENCY,
    semaphore: asyncio.Semaphore | None = None,
    use_cache: bool = True,
):
    """Async post media description bounded by an ``asyncio.Semaphore`` and total budget."""
    raw_images = list(getattr(post, "images", ()) or ())[:MAX_IMAGES]
    raw_videos = list(getattr(post, "videos", ()) or ())[:MAX_VIDEOS]
    if not raw_images and not raw_videos:
        return {"images": [], "videos": []}

    credentials = None if vision_callback is not None else await asyncio.to_thread(_vision_credentials)
    has_vision = vision_callback is not None or credentials is not None
    has_audio = _soundtrack_available(transcriber=transcriber)

    if not has_vision and not has_audio:
        pictures = [{"index": i, "text": "", "note": "未配置视觉模型"} for i in range(1, len(raw_images) + 1)]
        clips = []
        for i, item in enumerate(raw_videos, start=1):
            _url, seconds = _extract_video_spec(item)
            note = "视频太长，没看" if seconds > MAX_VIDEO_SECONDS else "未配置视觉模型"
            clips.append({"index": i, "seconds": seconds, "frames": [], "audio": "", "note": note})
        return {"images": pictures, "videos": clips}

    deadline = (
        time.monotonic() + max(0.0, float(timeout_budget))
        if timeout_budget is not None
        else None
    )
    sem = semaphore or asyncio.Semaphore(max(1, int(max_concurrency)))

    async def process_image(idx: int, url: str) -> dict:
        if not has_vision:
            return {"index": idx, "text": "", "note": "未配置视觉模型"}
        url_cache_key = ("url_img", url, DESCRIBE_PROMPT)
        if use_cache and url:
            cached = _MEDIA_CACHE.get(url_cache_key)
            if isinstance(cached, str) and cached:
                return {"index": idx, "text": cached, "note": ""}
        if _remaining(deadline) <= 0.0:
            return {"index": idx, "text": "", "note": "媒体解析超时"}
        async with sem:
            rem = _remaining(deadline)
            if rem <= 0.0:
                return {"index": idx, "text": "", "note": "媒体解析超时"}
            try:
                data, note = await asyncio.wait_for(
                    asyncio.to_thread(download, url, limit=MAX_IMAGE_BYTES, timeout=min(45.0, max(0.05, rem))),
                    timeout=rem,
                )
            except asyncio.TimeoutError:
                return {"index": idx, "text": "", "note": "媒体解析超时"}
            if not data:
                return {"index": idx, "text": "", "note": note or "下载失败"}
            rem = _remaining(deadline)
            if rem <= 0.0:
                return {"index": idx, "text": "", "note": "媒体解析超时"}
            try:
                text = await asyncio.wait_for(
                    describe_image_async(
                        data,
                        prompt=DESCRIBE_PROMPT,
                        vision_callback=vision_callback,
                        credentials=credentials,
                        timeout=min(60.0, max(0.05, rem)),
                        use_cache=use_cache,
                    ),
                    timeout=rem,
                )
            except asyncio.TimeoutError:
                return {"index": idx, "text": "", "note": "媒体解析超时"}
            if text and use_cache and url:
                _MEDIA_CACHE.set(url_cache_key, text)
            return {"index": idx, "text": text, "note": ""}

    async def process_video(idx: int, url: str, seconds: int) -> dict:
        if seconds > MAX_VIDEO_SECONDS:
            return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "视频太长，没看"}
        url_cache_key = ("url_vid", url, seconds, has_vision, has_audio)
        if use_cache and url:
            cached = _MEDIA_CACHE.get(url_cache_key)
            if isinstance(cached, dict):
                return {
                    "index": idx,
                    "seconds": seconds,
                    "frames": list(cached.get("frames") or ()),
                    "audio": str(cached.get("audio") or ""),
                    "note": str(cached.get("note") or ""),
                }
        if _remaining(deadline) <= 0.0:
            return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "媒体解析超时"}
        async with sem:
            rem = _remaining(deadline)
            if rem <= 0.0:
                return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "媒体解析超时"}
            try:
                data, note = await asyncio.wait_for(
                    asyncio.to_thread(download, url, limit=MAX_VIDEO_BYTES, timeout=min(45.0, max(0.05, rem))),
                    timeout=rem,
                )
            except asyncio.TimeoutError:
                return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "媒体解析超时"}
            if not data:
                return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": note or "下载失败"}

            described: list[str] = []
            frame_note = ""
            if has_vision:
                rem = _remaining(deadline)
                if rem <= 0.0:
                    return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "媒体解析超时"}
                try:
                    frames, frame_note = await asyncio.wait_for(
                        asyncio.to_thread(video_frames, data, seconds=seconds, timeout=min(60.0, max(0.05, rem))),
                        timeout=rem,
                    )
                except asyncio.TimeoutError:
                    return {"index": idx, "seconds": seconds, "frames": [], "audio": "", "note": "媒体解析超时"}
                for order, frame in enumerate(frames, start=1):
                    rem = _remaining(deadline)
                    if rem <= 0.0:
                        frame_note = frame_note or "媒体解析超时"
                        break
                    try:
                        line = await asyncio.wait_for(
                            describe_image_async(
                                frame,
                                prompt=FRAME_PROMPT.format(index=order),
                                vision_callback=vision_callback,
                                credentials=credentials,
                                timeout=min(60.0, max(0.05, rem)),
                                use_cache=use_cache,
                            ),
                            timeout=rem,
                        )
                    except asyncio.TimeoutError:
                        frame_note = frame_note or "媒体解析超时"
                        break
                    if line:
                        described.append(line)

            spoken = ""
            if has_audio:
                rem = _remaining(deadline)
                if rem > 0.0:
                    try:
                        spoken = await asyncio.wait_for(
                            asyncio.to_thread(
                                video_soundtrack,
                                data,
                                transcriber=transcriber,
                                timeout=min(60.0, max(0.05, rem)),
                            ),
                            timeout=rem,
                        )
                    except asyncio.TimeoutError:
                        spoken = ""

            result = {
                "index": idx,
                "seconds": seconds,
                "frames": described,
                "audio": spoken,
                "note": frame_note if has_vision else ("" if spoken else "未配置视觉模型"),
            }
            if use_cache and url and (described or spoken):
                _MEDIA_CACHE.set(
                    url_cache_key,
                    {"frames": tuple(described), "audio": spoken, "note": result["note"]},
                )
            return result

    image_tasks = [process_image(i, _extract_image_url(item)) for i, item in enumerate(raw_images, start=1)]
    video_tasks = [process_video(i, *_extract_video_spec(item)) for i, item in enumerate(raw_videos, start=1)]
    pictures = list(await asyncio.gather(*image_tasks)) if image_tasks else []
    clips = list(await asyncio.gather(*video_tasks)) if video_tasks else []
    return {"images": pictures, "videos": clips}


def describe_post(
    post,
    *,
    vision_callback=None,
    transcriber=None,
    timeout_budget: float = DEFAULT_MEDIA_BUDGET_SECONDS,
    max_concurrency: int = DEFAULT_MEDIA_CONCURRENCY,
    use_cache: bool = True,
):
    """Describe pictures and clips of one post with bounded concurrency and time budget."""
    coro = describe_post_async(
        post,
        vision_callback=vision_callback,
        transcriber=transcriber,
        timeout_budget=timeout_budget,
        max_concurrency=max_concurrency,
        use_cache=use_cache,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
