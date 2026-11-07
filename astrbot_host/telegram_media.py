"""Trusted Telegram media preparation and voice conversion.

The AstrBot Telegram adapter hands the plugin full Bot API URLs for photos,
documents and videos, and a local wav for voice.  This host module turns those
references into files inside AstrBot's own temp directory so the frozen media
locators can mark them ``host_resolved`` and the intake loaders never treat a
network message as local disk authority.

Downloads deliberately use the platform's configured ``telegram_file_base_url``
host as the allow-list and honour AstrBot's ``http_proxy`` environment (Telegram
is commonly reached through a proxy).  The plugin's generic public-URL fetch
does neither, so it cannot be used for Telegram attachments.

Outbound voice conversion also lives here: Telegram accepts OGG/OPUS, MP3 and
M4A voice notes, while AstrBot's TTS commonly produces WAV and its Telegram
adapter re-encodes every ``Record`` to WAV before ``send_voice``.  The plugin
therefore hands the adapter a ``TelegramVoiceRecord`` (see
``companion_v2.adapters.astrbot.platform_sender``) whose conversion returns the
prepared path from this module unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from companion_v2.adapters.astrbot.host_paths import is_host_temp_file, register_host_temp_root
from companion_v2.execution.models import TaskError


logger = logging.getLogger(__name__)

PLATFORM_NAME = "telegram"
DEFAULT_FILE_BASE_URL = "https://api.telegram.org/file/bot"
# Telegram Bot API refuses to serve files larger than 20 MB to bots.
MAX_TELEGRAM_MEDIA_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
VOICE_CONVERT_TIMEOUT_SECONDS = 20


def _register_temp_root():
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        register_host_temp_root(get_astrbot_temp_path())
    except Exception:
        # A host without the path helper simply never trusts a local temp file;
        # media then follows the public-URL rule instead of failing to load.
        register_host_temp_root(None)


_register_temp_root()


# ---- inbound media -------------------------------------------------------

def _platform_file_base_url(platform):
    value = getattr(platform, "file_base_url", None)
    if type(value) is not str or not value.strip():
        return DEFAULT_FILE_BASE_URL
    return value.strip().rstrip("/")


def _allowed_host(platform):
    try:
        return (urlsplit(_platform_file_base_url(platform)).hostname or "").casefold()
    except ValueError:
        return ""


def _validated_url(value, platform):
    if type(value) is not str or not value or len(value) > 8192:
        raise TaskError("host_media_location_invalid", "Telegram media URL is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise TaskError("host_media_location_invalid", "Telegram media URL is invalid") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise TaskError("host_media_location_invalid", "Telegram media URL is invalid")
    allowed = _allowed_host(platform)
    if allowed and parsed.hostname.casefold() != allowed:
        raise TaskError("host_media_location_invalid", "Telegram media URL left the configured file host")
    return value


def _temp_media_path(suffix):
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    directory = Path(get_astrbot_temp_path())
    directory.mkdir(parents=True, exist_ok=True)
    name = "companion-tg-" + uuid.uuid4().hex + (suffix if suffix and len(suffix) <= 12 else ".bin")
    return directory / name


async def download_telegram_media(event, platform, value):
    """Download one Telegram Bot API URL into AstrBot's temp directory.

    Returns the local path.  The download honours ``http_proxy`` and only
    connects to the operator-configured Telegram file host.
    """
    url = _validated_url(value, platform)
    suffix = Path(urlsplit(url).path).suffix
    destination = _temp_media_path(suffix)
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            async with session.get(url, allow_redirects=True, max_redirects=4) as response:
                if response.status != 200:
                    raise TaskError("host_media_fetch_failed", "Telegram media could not be downloaded")
                if response.content_length is not None and response.content_length > MAX_TELEGRAM_MEDIA_BYTES:
                    raise TaskError("host_media_limit", "Telegram media exceeds the download budget")
                written = 0
                with destination.open("wb") as stream:
                    async for chunk in response.content.iter_chunked(65536):
                        written += len(chunk)
                        if written > MAX_TELEGRAM_MEDIA_BYTES:
                            raise TaskError("host_media_limit", "Telegram media exceeds the download budget")
                        stream.write(chunk)
    except TaskError:
        _remove_quietly(destination)
        raise
    except asyncio.TimeoutError:
        _remove_quietly(destination)
        raise TaskError("host_media_timeout", "Telegram media download exceeded its bounded time") from None
    except Exception:
        _remove_quietly(destination)
        raise TaskError("host_media_fetch_failed", "Telegram media could not be downloaded") from None
    if not destination.is_file() or destination.stat().st_size <= 0:
        _remove_quietly(destination)
        raise TaskError("host_media_fetch_failed", "Telegram media could not be downloaded")
    track = getattr(event, "track_temporary_local_file", None)
    if callable(track):
        track(str(destination))
    return str(destination)


def _remove_quietly(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _component_value(part):
    value = getattr(part, "url", None) or getattr(part, "file", None)
    return value if type(value) is str else ""


def _set_component_value(part, value):
    for attribute in ("file", "url"):
        try:
            setattr(part, attribute, value)
        except (AttributeError, TypeError):
            continue
    if hasattr(part, "path"):
        try:
            setattr(part, "path", value)
        except (AttributeError, TypeError):
            pass


async def _prepare_part(event, platform, part):
    kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
    if kind == "Reply":
        chain = getattr(part, "chain", ())
        if isinstance(chain, (list, tuple)):
            for component in chain:
                await _prepare_part(event, platform, component)
        return
    if kind not in ("Image", "Record", "Video", "File"):
        return
    value = _component_value(part)
    if not value or value.startswith("base64://") or is_host_temp_file(value):
        return
    if value.startswith(("http://", "https://")):
        local = await download_telegram_media(event, platform, value)
        _set_component_value(part, local)
        return
    # A relative Telegram file path would mean the adapter did not rewrite it
    # to a full URL; refuse instead of treating it as a local disk path.
    if not os.path.isabs(value):
        raise TaskError("host_media_location_invalid", "Telegram media has no resolvable host attachment")


async def prepare_telegram_media(event, platform, parts):
    """Materialize every Telegram media component before locators are frozen."""
    for part in parts:
        await _prepare_part(event, platform, part)
    return parts


# ---- outbound voice ------------------------------------------------------

def _ffmpeg_binary():
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        candidate = Path(get_astrbot_data_path()) / "tools" / "bin" / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.is_file():
            return str(candidate)
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _audio_bytes(value):
    if type(value) is not str or not value:
        return None
    if value.startswith("base64://"):
        try:
            return base64.b64decode(value[len("base64://"):], validate=True)
        except (ValueError, TypeError):
            return None
    if is_host_temp_file(value):
        try:
            return Path(value).read_bytes()
        except OSError:
            return None
    return None


def audio_format(content):
    if content.startswith(b"OggS"):
        return "ogg"
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return "wav"
    if content.startswith(b"ID3") or (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        return "mp3"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return "m4a"
    return ""


async def telegram_voice_path(value):
    """Return a local OGG/MP3/M4A path Telegram ``sendVoice`` accepts.

    AstrBot's Telegram adapter calls ``Record.convert_to_file_path()``, which
    re-encodes everything to WAV before ``send_voice``; WAV voice notes are
    rejected by Telegram.  The plugin therefore hands the adapter a
    ``TelegramVoiceRecord`` whose conversion returns this already-compatible
    path instead.  ``None`` means the voice part must be dropped or sent as a
    document fallback.
    """
    content = _audio_bytes(value)
    if not content:
        return None
    detected = audio_format(content)
    if detected in ("ogg", "mp3", "m4a"):
        suffix = {"ogg": ".ogg", "mp3": ".mp3", "m4a": ".m4a"}[detected]
        try:
            path = _temp_media_path(suffix)
            path.write_bytes(content)
            return str(path)
        except OSError:
            return None
    binary = _ffmpeg_binary()
    if not binary:
        logger.warning("[CompanionV2] Telegram voice needs OGG/OPUS but FFmpeg is unavailable; keeping text only")
        return None
    source = _temp_media_path(".wav")
    target = _temp_media_path(".ogg")
    try:
        source.write_bytes(content)
        process = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-c:a", "libopus", "-b:a", "64k", "-ar", "48000", "-ac", "1", str(target),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            await asyncio.wait_for(process.wait(), timeout=VOICE_CONVERT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("[CompanionV2] Telegram voice conversion timed out; keeping text only")
            return None
        if process.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
            logger.warning("[CompanionV2] Telegram voice conversion failed; keeping text only")
            return None
        return str(target)
    except Exception:
        logger.warning("[CompanionV2] Telegram voice conversion errored; keeping text only", exc_info=True)
        return None
    finally:
        _remove_quietly(source)


async def telegram_voice_file(value):
    """Compatibility helper returning a ``base64://`` voice payload."""
    path = await telegram_voice_path(value)
    if path is None:
        return None
    try:
        return "base64://" + base64.b64encode(Path(path).read_bytes()).decode("ascii")
    finally:
        _remove_quietly(path)


def telegram_voice_document(value):
    """Persist an unconvertible voice payload as a document fallback.

    Returns ``(path, name)`` for a ``Comp.File`` fallback, or ``None`` when the
    bytes are unavailable.  Telegram cannot play a WAV voice note, but it can
    deliver the same file as a document so the user still receives the audio.
    """
    content = _audio_bytes(value)
    if not content:
        return None
    suffix = {"wav": ".wav", "mp3": ".mp3", "ogg": ".ogg", "m4a": ".m4a"}.get(audio_format(content), ".bin")
    try:
        path = _temp_media_path(suffix)
        path.write_bytes(content)
    except OSError:
        return None
    return str(path), "voice" + suffix


def telegram_mention_name(event, account):
    """The username Telegram needs on an outbound ``At`` component, or ``""``.

    Telegram renders mentions from usernames, not numeric ids.  Only the
    current speaker and the bot itself are known synchronously; other group
    members stay unmentioned rather than guessing a username.
    """
    message = getattr(event, "message_obj", None)
    sender = getattr(message, "sender", None)
    sender_id = str(getattr(sender, "user_id", "") or "")
    if sender_id and str(account) == sender_id:
        return str(getattr(sender, "nickname", "") or "")
    self_id = str(getattr(message, "self_id", "") or "")
    if self_id and str(account) == self_id:
        return self_id
    return ""
