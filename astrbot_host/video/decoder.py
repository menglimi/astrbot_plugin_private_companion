"""Decode one verified local MP4 and retain only declared sample PNGs."""

import hashlib
import io
import math
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor
from companion_v2.conversation.media.video.models import (
    CapturedVideo, VideoDescriptor, VideoFrameDescriptor, MAX_VIDEO_BYTES, MAX_FRAME_SIDE, selected_indices,
)
from .container import inspect_container, invalid


def relative_times(values, duration):
    if (not values or any(not math.isfinite(v) or v < 0 for v in values)
            or any(left > right for left, right in zip(values, values[1:]))
            or len(values) > 1 and values[-1] == values[0]):
        return None
    result = [round(v - values[0]) for v in values]
    return result if result[-1] <= duration else None


def _ffmpeg_binary():
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        candidate = Path(get_astrbot_data_path()) / "tools" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.is_file():
            return str(candidate)
    except ImportError:
        pass
    return shutil.which("ffmpeg")


def _decode_with_ffmpeg(movie, selected, directory):
    binary = _ffmpeg_binary()
    if binary is None:
        invalid("host_video_decoder_unavailable")
    output = Path(directory) / "sample_%02d.png"
    expression = "+".join(f"eq(n\\,{index})" for index in selected)
    try:
        result = subprocess.run(
            [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
             "-i", str(Path(directory) / "original.mp4"), "-vf", "select=" + expression,
             "-vsync", "0", "-frames:v", str(len(selected)), str(output)],
            capture_output=True, timeout=8, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        invalid("host_video_decoder_unavailable")
    if result.returncode:
        invalid("host_video_format_unsupported")
    frames = []
    for position in range(len(selected)):
        path = Path(directory) / f"sample_{position + 1:02d}.png"
        if not path.is_file():
            invalid("host_video_incomplete")
        try:
            with Image.open(path) as decoded:
                decoded.load()
                if sorted(decoded.size) != sorted((movie.width, movie.height)):
                    invalid("host_video_format_unsupported")
                frame = decoded.convert("RGB")
                frame.thumbnail((MAX_FRAME_SIDE, MAX_FRAME_SIDE), Image.Resampling.LANCZOS)
                encoded = io.BytesIO()
                frame.save(encoded, format="PNG", compress_level=6)
                data = encoded.getvalue()
                image = ImageDescriptor(position, "image/png", frame.width, frame.height,
                                        len(data), hashlib.sha256(data).hexdigest())
                frames.append(CapturedImage(image, data))
        except OSError:
            invalid("host_video_invalid")
    return tuple(frames)


def inspect_video(content, position, *, scratch=None):
    if type(content) is not bytes or not 0 < len(content) <= MAX_VIDEO_BYTES:
        invalid("host_video_limit")
    movie = inspect_container(content)
    try:
        import cv2
    except (ImportError, OSError):
        cv2 = None
    selected = selected_indices(movie.frame_count)
    frames, times, decoded_size = [], [], None
    # Only our bounded bytes get a generated local path. Network component
    # paths, covers and external MP4 data references never reach OpenCV.
    with (TemporaryDirectory(prefix="v2-video-decode-") if scratch is None else nullcontext(scratch)) as directory:
        path = Path(directory) / "original.mp4"
        path.write_bytes(content)
        if cv2 is None:
            frames = _decode_with_ffmpeg(movie, selected, directory)
            descriptors = tuple(VideoFrameDescriptor(index, None, frame.descriptor)
                                for index, frame in zip(selected, frames))
            descriptor = VideoDescriptor(position, "video/mp4", len(content), hashlib.sha256(content).hexdigest(),
                movie.width, movie.height, movie.frame_count, movie.duration_ms, movie.audio_tracks, descriptors,
                "unknown", "ffmpeg:external")
            return CapturedVideo(descriptor, content, frames)
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                invalid("host_video_decoder_unavailable")
            cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
            for index in range(movie.frame_count + 1):
                success, frame = cap.read()
                if not success:
                    if index != movie.frame_count:
                        invalid("host_video_incomplete")
                    break
                if index == movie.frame_count:
                    invalid("host_video_incomplete")
                height, width = frame.shape[:2]
                if (len(frame.shape) != 3 or frame.shape[2] != 3 or frame.dtype.name != "uint8"
                        or sorted((width, height)) != sorted((movie.width, movie.height))
                        or decoded_size is not None and decoded_size != (width, height)):
                    invalid("host_video_format_unsupported")
                decoded_size = width, height
                times.append(cap.get(cv2.CAP_PROP_POS_MSEC))
                if index in selected:
                    scale = min(1, MAX_FRAME_SIDE / max(width, height))
                    if scale < 1:
                        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
                    ok, png = cv2.imencode(".png", frame, [cv2.IMWRITE_PNG_COMPRESSION, 6])
                    if not ok:
                        invalid()
                    data = png.tobytes()
                    image = ImageDescriptor(len(frames), "image/png", frame.shape[1], frame.shape[0], len(data), hashlib.sha256(data).hexdigest())
                    frames.append(CapturedImage(image, data))
        finally:
            cap.release()
    starts = relative_times(times, movie.duration_ms)
    descriptors = tuple(VideoFrameDescriptor(index, starts[index] if starts is not None else None, frame.descriptor)
                        for index, frame in zip(selected, frames))
    descriptor = VideoDescriptor(position, "video/mp4", len(content), hashlib.sha256(content).hexdigest(),
        *decoded_size, movie.frame_count, movie.duration_ms, movie.audio_tracks, descriptors,
        "decoder_elapsed_ms" if starts is not None else "unknown", "opencv:" + cv2.__version__)
    return CapturedVideo(descriptor, content, tuple(frames))
