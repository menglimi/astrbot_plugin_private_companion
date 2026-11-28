"""Read Telegram private attachments within the turn limits before AstrBot collects them.

Telegram file URLs carry the Bot API token (``/file/bot<token>/...``). Each
attachment is read here under the same per-turn byte, count and time limits as
QQ media, then replaced by its own bytes or a short note. AstrBot's request
collection, session history and later plugin hooks never see the URL.
"""

import asyncio
from collections import Counter
from contextlib import ExitStack
import logging
import mimetypes
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from astrbot.core.message.components import File, Image, Plain, Record, Reply, Video
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from yarl import URL

from companion_v2.adapters.astrbot.files import MAX_FILES, file_locator
from companion_v2.adapters.astrbot.native import MEDIA_PREPARE_TIMEOUT, NativeVideoLocator
from companion_v2.adapters.astrbot.quotes import image_locator
from companion_v2.conversation.media.audio.models import MAX_AUDIO_BYTES, MAX_INPUT_AUDIOS
from companion_v2.conversation.media.models import MAX_IMAGE_BYTES, MAX_INPUT_IMAGES, fits_budget
from companion_v2.conversation.media.understanding import media_scope_enabled
from companion_v2.conversation.media.video.models import MAX_VIDEO_BYTES, MAX_VIDEOS
from companion_v2.conversation.media import models as image_models
from companion_v2.execution.models import TaskError

from .core_reply_video_sources import file_is_video
from .media import fetch_attachment, load_native_image, metered, trusted_endpoint
from .video.loader import fetch_native_video_bytes


logger = logging.getLogger(__name__)

LIMITED = "[附件超过读取上限，本轮未读取]"
TIMED_OUT = "[附件读取超时，本轮未读取]"
UNAVAILABLE = "[附件不可用]"
QUOTED_VIDEO_OFF = "[引用视频理解未启用，本轮未读取]"
_MEDIA = (Image, Record, Video, File)
_LIMITS = {Image: MAX_INPUT_IMAGES, Record: MAX_INPUT_AUDIOS, Video: MAX_VIDEOS, File: MAX_FILES}
_PREPARED = "_companion_v2_telegram_media_prepared"
_TELEGRAM_API_HOST = "api.telegram.org"


def _private(event):
    return (getattr(getattr(event, "platform_meta", None), "name", None) == "telegram"
            and getattr(getattr(getattr(event, "message_obj", None), "type", None), "value", None)
            == "FriendMessage")


def _update_message(event):
    return getattr(getattr(event.message_obj, "raw_message", None), "message", None)


def private_token(event):
    """The Bot API token of a private Telegram event, or ``None`` for anything else."""
    token = getattr(getattr(event, "client", None), "token", None) if _private(event) else None
    return token if isinstance(token, str) and token else None


def redact(event, text):
    token = private_token(event)
    return text.replace(token, "[redacted]") if token and isinstance(text, str) else text


def sticker_label(event):
    """AstrBot writes ``Sticker: <emoji>`` as if typed; name it as a sticker instead."""
    if not _private(event):
        return ""
    sticker = getattr(_update_message(event), "sticker", None)
    if sticker is None:
        return ""
    return f"[贴纸 {sticker.emoji}]" if sticker.emoji else "[贴纸]"


def restore_voice_caption(event):
    """AstrBot keeps only the recording of a voice note; put its caption back as text."""
    raw = _update_message(event) if _private(event) else None
    caption = getattr(raw, "caption", None)
    if not getattr(raw, "voice", None) or not isinstance(caption, str) or not caption:
        return
    message = event.message_obj
    caption = caption.encode("utf-16-le")
    # Same text_link expansion as AstrBot's caption handling for other media.
    for entity in reversed(getattr(raw, "caption_entities", None) or ()):
        if entity.type == "text_link" and entity.url:
            end = 2 * (entity.offset + entity.length)
            caption = caption[:end] + f" ({entity.url})".encode("utf-16-le") + caption[end:]
    caption = caption.decode("utf-16-le")
    # AstrBot's speech-to-text may already have put its own Plain transcript here;
    # only a caption restored earlier counts as present.
    if any(isinstance(part, Plain) and part.text == caption for part in message.message):
        return
    message.message.append(Plain(text=caption))
    message.message_str = event.message_str = (event.message_str + "\n" + caption).strip()


def _unbounded_voice(raw, part):
    """A direct voice note goes to AstrBot unchanged, as on QQ's reply path.

    AstrBot has already converted it to a 48 kHz WAV of about 96 KB a second, so
    the 5 MiB audio limit would stop a voice note near 55 seconds. Music and
    quoted recordings are still read within the turn limits.
    """
    return type(part) is Record and getattr(raw, "voice", None) is not None


def _telegram_hosted(url):
    """Whether ``url`` is on Telegram's own Bot API host, however the operator spelled it."""
    try:
        parsed = URL(url)
    except (TypeError, ValueError):
        return False
    return (parsed.scheme == "https" and (parsed.host or "").lower().rstrip(".") == _TELEGRAM_API_HOST
            and parsed.port == 443)


def _file_endpoint(event):
    """The bot's own file URL prefix (it ends in the token), and whether the operator runs the server.

    AstrBot downloads these through its proxy setting, including from a
    ``telegram_file_base_url`` the operator pointed at a self-hosted Bot API
    server. A local-mode server answers ``getFile`` (``telegram_api_base_url``)
    with paths on a shared disk, so either endpoint being self-hosted counts.
    """
    client = getattr(event, "client", None)
    base = getattr(client, "base_file_url", None)
    if not isinstance(base, str) or not base or private_token(event) is None:
        return None, False
    api = getattr(client, "base_url", None)
    self_hosted = not _telegram_hosted(base) or isinstance(api, str) and not _telegram_hosted(api)
    return base, self_hosted


def needs_preparation(event):
    """Whether this private event holds an attachment AstrBot would otherwise fetch itself."""
    raw = _update_message(event) if _private(event) else None
    if raw is None or event.get_extra(_PREPARED):
        return False
    return bool(getattr(raw, "media_group_id", None)) or any(
        type(part) in _LIMITS and not _unbounded_voice(raw, part)
        or isinstance(part, Reply) and any(isinstance(child, _MEDIA) for child in part.chain or ())
        for part in event.message_obj.message)


def _read_temp_record(value, maximum_bytes):
    """Read AstrBot's converted recording without following links out of its temp directory."""
    root = Path(get_astrbot_temp_path()).resolve()
    try:
        if not isinstance(value, str) or value.startswith(("//", "\\")) or not Path(value).is_absolute():
            raise ValueError
        path = Path(value).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError
        with ExitStack() as stack:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                # Hold each directory open so a swapped link cannot redirect a later open.
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory = os.open(root, flags)
                stack.callback(os.close, directory)
                relative = path.relative_to(root)
                for name in relative.parts[:-1]:
                    directory = os.open(name, flags, dir_fd=directory)
                    stack.callback(os.close, directory)
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
                stream = stack.enter_context(os.fdopen(descriptor, "rb"))
            else:
                stream = stack.enter_context(path.open("rb"))
                if not os.path.samestat(os.fstat(stream.fileno()), path.stat()):
                    raise ValueError
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError
            if info.st_size > maximum_bytes:
                raise TaskError("host_audio_limit", "recording exceeds the remaining turn allowance")
            content = stream.read(maximum_bytes + 1)
    except TaskError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise TaskError("host_audio_location_invalid", "recording is not an AstrBot temporary file") from None
    if not 0 < len(content) <= maximum_bytes:
        raise TaskError("host_audio_limit", "recording exceeds the remaining turn allowance")
    return content


def _temporary_path(event, suffix, data):
    """A local copy owned by the event, so AstrBot removes it with the event's other files."""
    directory = Path(get_astrbot_temp_path())
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=directory, suffix=suffix[:16], delete=False) as stream:
        event.track_temporary_local_file(stream.name)
        stream.write(data)
    return stream.name


def _temporary_file(event, name, data):
    """Hand AstrBot a local copy so File.get_file does not download the original again."""
    return File(name=name, file=_temporary_path(event, Path(name).suffix, data))


def _video_name(part, name):
    """Keep a video suffix on the local copy: the request hook recognizes a video file by it.

    A document without a file name gets a random hex name from AstrBot; its
    Telegram file path still ends in the video suffix.
    """
    if file_is_video(File(name=name)):
        return name
    for value in (part.url, getattr(part, "file_", None)):
        if isinstance(value, str) and file_is_video(File(name=value)):
            return name + Path(urlparse(value).path if "://" in value else value).suffix.lower()
    return name


class _TurnReader:
    """One turn's shared allowance: direct attachments first, then quoted ones."""

    def __init__(self, event, scope, settings):
        self.event, self.scope, self.settings = event, scope, settings
        self.deadline = asyncio.get_running_loop().time() + MEDIA_PREPARE_TIMEOUT
        self.file_deadline = None
        # Read through the module so a patched turn limit applies to this reader.
        self.remaining = image_models.MAX_TURN_BYTES
        self.counts = Counter()
        self.photos = []
        self.timed_out = False

    def _enabled(self, *keys):
        settings = self.settings
        return settings is not None and all(settings.get(key) is True for key in keys)

    async def _bounded(self, awaitable, meter, deadline=None):
        deadline = self.deadline if deadline is None else min(deadline, self.deadline)
        try:
            return await asyncio.wait_for(awaitable, max(0, deadline - asyncio.get_running_loop().time()))
        except asyncio.TimeoutError:
            # A cancelled download still spent what it received.
            self.remaining = max(0, self.remaining - meter.bytes)
            if deadline == self.deadline:
                self.timed_out = True
            raise
        except Exception:
            self.remaining = max(0, self.remaining - meter.bytes)
            raise

    async def _photo(self, part, meter):
        locator = image_locator(len(self.photos), part.file, part.url)
        image = await self._bounded(load_native_image(
            locator, maximum_bytes=min(self.remaining, MAX_IMAGE_BYTES)), meter)
        size = image.descriptor.retained_bytes
        if size > self.remaining or not fits_budget((*self.photos, image.descriptor)):
            self.remaining = max(0, self.remaining - meter.bytes)
            raise TaskError("host_image_limit", "photo exceeds the turn image limits")
        self.photos.append(image.descriptor)
        if locator.host_resolved:
            # AstrBot's preprocessing already saved this photo; keep its file rather than a second copy.
            return part, size
        # Shaped like AstrBot's localized photos. Request collection adopts image files and keeps
        # them past event cleanup for later turns, as it does for its own.
        path = _temporary_path(self.event, mimetypes.guess_extension(image.descriptor.mime_type) or "", image.content)
        return Image(file=path, url=path, path=path), size

    async def _video(self, part, meter):
        if isinstance(part, File):
            locator = file_locator(0, part)
            name, value = _video_name(part, locator.name), locator.locator
        else:
            value = part.file
        if not isinstance(value, str) or not value:
            raise TaskError("host_video_unavailable", "video has no original attachment")
        data = await self._bounded(fetch_native_video_bytes(
            NativeVideoLocator(0, value), maximum_bytes=min(self.remaining, MAX_VIDEO_BYTES)), meter)
        if isinstance(part, File):
            # Keep the name: the request hook recognizes a video file by its suffix.
            return _temporary_file(self.event, name, data), len(data)
        suffix = Path(urlparse(value).path if "://" in value else value).suffix.lower()
        return Video(file=_temporary_path(
            self.event, suffix if file_is_video(File(name=value)) else ".mp4", data)), len(data)

    async def _recording(self, part, meter):
        data = await self._bounded(asyncio.to_thread(
            _read_temp_record, part.path or part.file, min(self.remaining, MAX_AUDIO_BYTES)), meter)
        path = _temporary_path(self.event, ".wav", data)
        record = Record(file=path, url=path)
        record.path = path
        return record, len(data)

    async def _document(self, part):
        locator = file_locator(0, part)
        name = locator.name

        def note(status):
            return Plain(f"[文件 {name}：{status}]")

        if not self._enabled("file_understanding_enabled"):
            return note("文件理解未启用，未读取正文")
        if not media_scope_enabled(self.settings, "file_understanding", self.scope):
            return note("当前会话范围未启用文件理解，未读取正文")
        if Path(name).suffix.lower() not in self.settings["file_understanding_allowed_suffixes"]:
            return note("文件格式暂不支持")
        if self.counts[File] >= MAX_FILES or self.remaining <= 0:
            return note("本轮文件读取上限已达到，未读取此附件")
        if self.timed_out:
            return note("文件读取超时，未取得正文")
        if self.file_deadline is None:
            self.file_deadline = (asyncio.get_running_loop().time()
                                  + self.settings["file_understanding_timeout_seconds"])
        try:
            with metered() as meter:
                data = await self._bounded(fetch_attachment(locator.locator, maximum_bytes=min(
                    self.remaining, self.settings["file_understanding_max_size_mb"] * 1024 * 1024)),
                    meter, self.file_deadline)
        except asyncio.TimeoutError:
            return note("文件读取超时，未取得正文")
        except TaskError as error:
            return note("文件超过读取大小上限" if error.code == "host_image_limit" else "文件读取失败，未取得正文")
        except Exception:
            # aiohttp and OS errors can quote the URL; keep only the outcome.
            return note("文件读取失败，未取得正文")
        self.remaining -= len(data)
        self.counts[File] += 1
        return _temporary_file(self.event, name, data)

    async def replace(self, part, *, quoted=False):
        """Return the component that takes ``part``'s place in the message."""
        # Telegram delivers GIFs and videos sent as files as documents.
        kind = Video if isinstance(part, File) and file_is_video(part) else type(part)
        if kind is Video and quoted and not (
                self._enabled("video_understanding_enabled", "video_understanding_quoted_enabled")
                and media_scope_enabled(self.settings, "video_understanding", self.scope)):
            return Plain(QUOTED_VIDEO_OFF)
        if kind is not File and self.timed_out:
            return Plain(TIMED_OUT)
        if kind is not File and (self.counts[kind] >= _LIMITS[kind] or self.remaining <= 0):
            return Plain(LIMITED)
        try:
            if kind is File:
                return await self._document(part)
            with metered() as meter:
                if kind is Image:
                    replacement, size = await self._photo(part, meter)
                elif kind is Video:
                    replacement, size = await self._video(part, meter)
                else:
                    replacement, size = await self._recording(part, meter)
        except asyncio.TimeoutError:
            return Plain(TIMED_OUT)
        except TaskError as error:
            logger.info("[CompanionV2] Telegram 附件未读取 reason=%s", error.code)
            return Plain(LIMITED if error.code.endswith("_limit") else UNAVAILABLE)
        except Exception as error:
            logger.warning("[CompanionV2] Telegram 附件未读取 error_type=%s", type(error).__name__)
            return Plain(UNAVAILABLE)
        self.remaining -= size
        self.counts[kind] += 1
        return replacement


def _trusted_quote(event, reply):
    """Media in a quote is read only from the update's own full reply to the user or this bot."""
    raw = _update_message(event)
    original = getattr(raw, "reply_to_message", None)
    bot = str(getattr(getattr(event, "client", None), "token", None) or "").partition(":")[0]
    return (original is not None and getattr(raw, "quote", None) is None
            and str(reply.id) == str(getattr(original, "message_id", None))
            and str(reply.sender_id) in (str(event.get_sender_id()), bot))


async def _prepare(event, scope, settings):
    reader = _TurnReader(event, scope, settings)
    message = event.message_obj
    parts = list(message.message)
    notes = []
    raw = _update_message(event)
    for index, part in enumerate(parts):
        if type(part) in _LIMITS and not _unbounded_voice(raw, part):
            parts[index] = await reader.replace(part)
            if isinstance(parts[index], Plain):
                notes.append(parts[index].text)
    for part in parts:
        if isinstance(part, Reply) and any(isinstance(child, _MEDIA) for child in part.chain or ()):
            trusted = _trusted_quote(event, part)
            part.chain = [(await reader.replace(child, quoted=True) if trusted else Plain(UNAVAILABLE))
                          if isinstance(child, _MEDIA) else child for child in part.chain]
    message.message = parts
    notes = list(dict.fromkeys(notes))
    if notes:
        message.message_str = event.message_str = "\n".join([event.message_str, *notes]).strip()


def _withhold_credentials(event):
    """Last guard: nothing that still names the token may reach AstrBot's collection."""
    token = private_token(event)
    if token is None:
        return

    def named(part):
        # File.file may start a synchronous download; its stored source is file_.
        names = ("file_", "url") if isinstance(part, File) else ("file", "url", "path")
        return any(isinstance(value, str) and token in value for value in (getattr(part, name, None) for name in names))

    def clean(chain):
        return [Plain(UNAVAILABLE) if named(part) else part for part in chain]

    message = event.message_obj
    message.message = clean(message.message)
    for part in message.message:
        if isinstance(part, Reply):
            part.chain = clean(part.chain or ())
    message.message_str = event.message_str = redact(event, event.message_str)


async def prepare_private_media(event, scope, settings):
    """Replace each attachment of a private Telegram event by its bounded bytes or a note.

    ``settings`` are the window's media understanding settings, or ``None`` when
    they cannot be read; documents and quoted videos then stay unread.
    """
    event.set_extra(_PREPARED, True)
    try:
        prefix, self_hosted = _file_endpoint(event)
        with trusted_endpoint(prefix, local_files=self_hosted):
            await _prepare(event, scope, settings)
    except Exception as error:
        logger.warning("[CompanionV2] Telegram 附件准备失败，本轮不读取附件 error_type=%s", type(error).__name__)
        message = event.message_obj
        message.message = [Plain(UNAVAILABLE) if isinstance(part, _MEDIA) else part for part in message.message]
        for part in message.message:
            if isinstance(part, Reply):
                part.chain = [Plain(UNAVAILABLE) if isinstance(child, _MEDIA) else child for child in part.chain or ()]
    finally:
        _withhold_credentials(event)
