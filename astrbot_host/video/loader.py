"""Video network capture and disposable decoder lifetime; no event-loop decoding."""

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from companion_v2.adapters.astrbot.native import NativeVideoLocator
from companion_v2.conversation.media.models import CapturedImage, MAX_TURN_BYTES
from companion_v2.conversation.media.video.models import CapturedVideo, VideoDescriptor, MAX_VIDEO_BYTES
from companion_v2.execution.models import TaskError
from ..media import fetch_attachment, read_host_resolved_attachment


DECODE_TIMEOUT = 12
DECODE_ERRORS = frozenset({"host_video_invalid", "host_video_format_unsupported", "host_video_limit",
    "host_video_external_reference", "host_video_decoder_unavailable", "host_video_incomplete", "video_manifest_invalid"})


async def decode_video(content, position):
    if type(content) is not bytes or not 0 < len(content) <= MAX_VIDEO_BYTES:
        raise TaskError("host_video_limit", "video exceeds its intake byte budget")
    # The parent owns this directory so a killed native decoder cannot leave
    # an ungoverned original recording behind on disk.
    with TemporaryDirectory(prefix="v2-video-worker-") as scratch:
        return await _decode(content, position, scratch)


async def _decode(content, position, scratch):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-X", "utf8", "-m", "astrbot_host.video.worker", scratch,
        cwd=str(Path(__file__).resolve().parents[2]), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        output, _ = await asyncio.wait_for(process.communicate(content), DECODE_TIMEOUT)
        if len(output) > MAX_TURN_BYTES + 8192:
            raise TaskError("host_video_limit", "video frames exceed their bounded retention budget")
        header, _, pixels = output.partition(b"\n")
        data = json.loads(header)
        if process.returncode:
            code = data.get("error")
            raise TaskError(code if code in DECODE_ERRORS else "host_video_invalid", "video could not be prepared completely")
        descriptor = VideoDescriptor.restore({**data, "position": position})
        frames, offset = [], 0
        for frame in descriptor.frames:
            end = offset + frame.image.byte_count
            frames.append(CapturedImage(frame.image, pixels[offset:end]))
            offset = end
        if offset != len(pixels):
            raise TaskError("video_content_changed", "video decoder returned undeclared frame bytes")
        return CapturedVideo(descriptor, content, tuple(frames))
    except asyncio.TimeoutError:
        raise TaskError("host_video_decode_timeout", "video decoding exceeded its bounded preparation time") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


async def load_native_video(locator):
    return await decode_video(await fetch_native_video_bytes(locator), locator.position)


async def fetch_native_video_bytes(locator):
    """Fetch a frozen host component once; native models need no frame decoder."""
    if type(locator) is not NativeVideoLocator:
        raise TaskError("host_video_location_invalid", "video needs its frozen original component")
    try:
        value = locator.locator
        if value.startswith("base64://"):
            encoded = value[9:]
            if len(encoded) > (MAX_VIDEO_BYTES + 2) // 3 * 4:
                raise TaskError("host_video_limit", "video exceeds its intake byte budget")
            content = base64.b64decode(encoded, validate=True)
        elif locator.host_resolved is True:
            content = await asyncio.to_thread(
                read_host_resolved_attachment, value, MAX_VIDEO_BYTES)
        else:
            content = await fetch_attachment(value, maximum_bytes=MAX_VIDEO_BYTES)
        if not 0 < len(content) <= MAX_VIDEO_BYTES:
            raise TaskError("host_video_limit", "video exceeds its intake byte budget")
        return content
    except TaskError as error:
        if error.code.startswith("host_image_"):
            raise TaskError(error.code.replace("host_image_", "host_video_", 1), "original video attachment is unavailable") from None
        if error.code.startswith("host_attachment_"):
            raise TaskError(error.code.replace("host_attachment_", "host_video_", 1), "original video attachment is unavailable") from None
        raise
    except Exception:
        raise TaskError("host_video_fetch_failed", "original video could not be prepared") from None


async def load_host_resolved_video(value, position):
    return await decode_video(await read_host_resolved_video_bytes(value), position)


async def read_host_resolved_video_bytes(value):
    """Read only a video path that AstrBot resolved into its event temp directory."""
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    try:
        root = Path(get_astrbot_temp_path()).resolve(strict=True)
        path = Path(value).resolve(strict=True)
        path.relative_to(root)
        if not path.is_file():
            raise ValueError("not a regular file")
    except (OSError, TypeError, ValueError):
        raise TaskError("host_video_location_invalid", "video is not a resolved host attachment") from None

    def read():
        with path.open("rb") as stream:
            return stream.read(MAX_VIDEO_BYTES + 1)

    content = await asyncio.to_thread(read)
    if not 0 < len(content) <= MAX_VIDEO_BYTES:
        raise TaskError("host_video_limit", "video exceeds its intake byte budget")
    return content
