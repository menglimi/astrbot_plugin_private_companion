"""FFmpeg/FFprobe resolution, duration probing and size/duration-bounded compression."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

from companion_v2.execution.models import TaskError
from .decoder import _ffmpeg_binary


MAX_DATA_URI_BYTES = 18 * 1024 * 1024
DEFAULT_TARGET_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS = 120.0
OVERSIZE_ACTIONS = frozenset({"compress", "truncate", "reject"})
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_FORMAT_RE = re.compile(r"Input\s+#\d+,\s*([^,\s]+(?:,[^,\s]+)*),\s*from\b", re.IGNORECASE)
_FFMPEG_VIDEO_CODEC_RE = re.compile(r"Stream\s+#[^\n]*?\bVideo:\s*([A-Za-z0-9_]+)", re.IGNORECASE)
_MP4_VIDEO_CODECS = frozenset({"h264", "hevc", "h265", "mpeg4"})
_EXCLUDED_SUFFIXES = frozenset({".ts", ".m2ts", ".mts"})


@dataclass(frozen=True)
class MediaProbeInfo:
    duration_s: float | None = None
    format_name: str | None = None
    video_codec: str | None = None


@dataclass(frozen=True)
class CompressionDecision:
    decision: str  # "as-is" | "compress" | "truncate" | "reject"
    should_transcode: bool
    truncate_to: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class CompressionResult:
    content: bytes
    mime_type: str
    compressed: bool
    truncated: bool
    duration_s: float | None
    original_duration_s: float | None
    original_bytes: int


def resolve_ffmpeg_tools() -> tuple[str | None, str | None]:
    """Return ``(ffmpeg_path, ffprobe_path)`` using existing decoder discovery + PATH."""
    ffmpeg = _ffmpeg_binary()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe and ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            ffprobe = str(sibling)
    return ffmpeg, ffprobe


def video_capabilities_status() -> dict[str, bool]:
    """Expose only safe boolean availability flags to dashboard routes."""
    ffmpeg, ffprobe = resolve_ffmpeg_tools()
    return {
        "ffmpeg_available": bool(ffmpeg),
        "ffprobe_available": bool(ffprobe),
    }


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Terminate a child ffmpeg/ffprobe process and any descendants."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _run_subprocess(
    args: list[str],
    *,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(args, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=max(0.05, float(timeout)))
        return process.returncode or 0, stdout or b"", stderr or b""
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            process.communicate(timeout=2)
        except Exception:
            pass
        raise TaskError("host_video_compress_timeout", "视频处理超时，请稍后重试或缩短视频。") from None


def sniff_video_container(content: bytes | bytearray) -> str | None:
    """Identify common video container formats from leading magic bytes without spawning subprocesses."""
    raw = bytes(content) if isinstance(content, bytearray) else content
    if not isinstance(raw, bytes) or len(raw) < 4:
        return None
    if raw.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"AVI ":
        return "avi"
    if raw.startswith(b"FLV"):
        return "flv"
    if raw.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "asf"
    if raw.startswith(b"OggS"):
        return "ogg"
    if raw.startswith(b"\x00\x00\x01\xba"):
        return "mpeg"
    if len(raw) >= 376 and raw[0] == 0x47 and raw[188] == 0x47:
        return "mpegts"
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        box_size = int.from_bytes(raw[:4], "big")
        major_brand = raw[8:12]
        if major_brand.startswith(b"qt"):
            return "quicktime"
        if box_size == 0 or box_size >= 12:
            return "mp4"
    if len(raw) >= 8 and raw[4:8] in (b"moov", b"wide", b"free", b"mdat", b"skip", b"pnot"):
        return "quicktime"
    return None


def _parse_positive_duration(raw_value: object) -> float | None:
    try:
        value = float(str(raw_value).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def probe_media_info(
    path: Path,
    *,
    ffprobe: str | None = None,
    ffmpeg: str | None = None,
    timeout: float = 10.0,
) -> MediaProbeInfo:
    """Probe duration, container format_name and primary video codec using ``ffprobe`` or ``ffmpeg -i``."""
    if ffprobe is None and ffmpeg is None:
        ffmpeg, ffprobe = resolve_ffmpeg_tools()
    if ffprobe:
        try:
            code, stdout, _stderr = _run_subprocess(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=format_name,duration:stream=codec_type,codec_name",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout=timeout,
            )
            if code == 0:
                text = stdout.decode("utf-8", errors="ignore").strip()
                if text:
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
                        format_name = str(fmt.get("format_name") or "").strip().lower() or None
                        duration_s = _parse_positive_duration(fmt.get("duration"))
                        video_codec = None
                        for stream in payload.get("streams") or ():
                            if isinstance(stream, dict) and str(stream.get("codec_type") or "").lower() == "video":
                                video_codec = str(stream.get("codec_name") or "").strip().lower() or None
                                break
                        if duration_s is not None or format_name is not None or video_codec is not None:
                            return MediaProbeInfo(
                                duration_s=duration_s,
                                format_name=format_name,
                                video_codec=video_codec,
                            )
                    else:
                        duration_s = _parse_positive_duration(text.splitlines()[0].strip())
                        if duration_s is not None:
                            return MediaProbeInfo(duration_s=duration_s)
        except TaskError:
            pass
    if ffmpeg:
        try:
            _code, _stdout, stderr = _run_subprocess(
                [ffmpeg, "-nostdin", "-hide_banner", "-i", str(path)],
                timeout=timeout,
            )
            err_text = stderr.decode("utf-8", errors="ignore")
            duration_s = None
            match = _DURATION_RE.search(err_text)
            if match:
                hours, minutes, secs = match.groups()
                duration_s = _parse_positive_duration(int(hours) * 3600 + int(minutes) * 60 + float(secs))
            fmt_match = _FFMPEG_FORMAT_RE.search(err_text)
            format_name = fmt_match.group(1).strip().lower() if fmt_match else None
            codec_match = _FFMPEG_VIDEO_CODEC_RE.search(err_text)
            video_codec = codec_match.group(1).strip().lower() if codec_match else None
            if duration_s is not None or format_name is not None or video_codec is not None:
                return MediaProbeInfo(
                    duration_s=duration_s,
                    format_name=format_name,
                    video_codec=video_codec,
                )
        except TaskError:
            pass
    return MediaProbeInfo()


def probe_duration(
    path: Path,
    *,
    ffprobe: str | None = None,
    ffmpeg: str | None = None,
    timeout: float = 10.0,
) -> float | None:
    """Probe media duration in seconds using ``ffprobe`` or ``ffmpeg -i`` fallback."""
    return probe_media_info(path, ffprobe=ffprobe, ffmpeg=ffmpeg, timeout=timeout).duration_s


def _is_probe_mp4(info: MediaProbeInfo, *, magic_kind: str | None) -> bool | None:
    """Return False when ffprobe/ffmpeg metadata indicates a non-MP4 container or video codec."""
    if info.format_name is None and info.video_codec is None:
        return None
    if info.format_name is not None:
        formats = {token.strip().lower() for token in info.format_name.split(",") if token.strip()}
        if "mp4" not in formats:
            return False
        # ffprobe groups QuickTime (.mov) and MP4 under "mov,mp4,m4a,3gp,3g2,mj2";
        # require an actual ISO BMFF MP4 ftyp header (not QuickTime or missing ftyp).
        if magic_kind != "mp4" and "mov" in formats:
            return False
    if info.video_codec is not None and info.video_codec not in _MP4_VIDEO_CODECS:
        return False
    return True


def _resolve_actual_is_mp4(
    ext: str,
    magic_kind: str | None,
    media_info: MediaProbeInfo,
) -> bool:
    """Combine extension, magic-byte sniffing and probe metadata before allowing MP4 passthrough."""
    if ext != ".mp4":
        return False
    if magic_kind is not None and magic_kind != "mp4":
        return False
    probe_mp4 = _is_probe_mp4(media_info, magic_kind=magic_kind)
    if probe_mp4 is False:
        return False
    if magic_kind == "mp4" or probe_mp4 is True:
        return True
    # Neither magic bytes nor ffprobe recognized a container (e.g. synthetic unit-test bytes);
    # keep legacy .mp4 pass-through so downstream inspect_container governs real invalid payloads.
    return True


def will_truncate(
    duration_s: float | None,
    max_duration_s: float,
    oversize_action: str,
) -> bool:
    """Return whether duration exceeds the ceiling and the policy is set to truncate."""
    return (
        oversize_action == "truncate"
        and max_duration_s > 0
        and duration_s is not None
        and duration_s > max_duration_s
    )


def classify(
    size_bytes: int,
    duration_s: float | None,
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    max_duration_s: float = DEFAULT_MAX_DURATION_SECONDS,
    oversize_action: str = "compress",
    force_transcode: bool = False,
) -> CompressionDecision:
    """Decide whether a video should pass through as-is, compress, truncate, or reject."""
    action = oversize_action if oversize_action in OVERSIZE_ACTIONS else "compress"
    over_size = size_bytes > target_bytes
    over_duration = max_duration_s > 0 and duration_s is not None and duration_s > max_duration_s

    if action == "reject":
        if over_duration:
            return CompressionDecision(
                "reject",
                False,
                reason=f"视频时长（{duration_s:.1f} 秒）超过上限（{int(max_duration_s)} 秒），已按设置拒收。",
            )
        if over_size:
            mb = size_bytes / (1024 * 1024)
            limit_mb = target_bytes / (1024 * 1024)
            return CompressionDecision(
                "reject",
                False,
                reason=f"视频体积（{mb:.1f} MB）超过上限（{limit_mb:.1f} MB），已按设置拒收。",
            )
    if over_duration and action == "truncate":
        return CompressionDecision("truncate", True, truncate_to=float(max_duration_s))
    if over_size or force_transcode:
        return CompressionDecision("compress", True, truncate_to=None)
    return CompressionDecision("as-is", False, truncate_to=None)


def _bitrate_plan(target_bytes: int, effective_duration_s: float, pass_index: int) -> tuple[int, int, int]:
    """Compute ``(video_kbps, audio_kbps, max_width)`` targeting <= ``target_bytes``."""
    safety = 0.86 if pass_index == 0 else 0.72
    total_kbps = int((target_bytes * 8 * safety) / max(effective_duration_s, 1.0) / 1000)
    audio_kbps = 32 if total_kbps < 260 or pass_index > 0 else 48
    video_kbps = max(80, min(1800, total_kbps - audio_kbps))
    width = 720 if (pass_index == 0 and video_kbps >= 450) else 480 if video_kbps >= 200 else 360
    return video_kbps, audio_kbps, width


def _run_ffmpeg_encode(
    ffmpeg: str,
    src: Path,
    dst: Path,
    *,
    video_kbps: int,
    audio_kbps: int,
    max_width: int,
    truncate_to: float | None,
    target_bytes: int,
    timeout: float,
) -> tuple[int, bytes]:
    scale_filter = f"scale='trunc(min({max_width},iw)/2)*2':-2"
    base_cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
    ]
    if truncate_to is not None and truncate_to > 0:
        base_cmd.extend(["-t", f"{truncate_to:.3f}"])
    # Try libx264 first, fall back to mpeg4 if ffmpeg build lacks libx264.
    encode_deadline = time.monotonic() + float(timeout)
    last_stderr = b""
    for codec, extra in (
        ("libx264", ["-preset", "veryfast"]),
        ("mpeg4", ["-q:v", "7"]),
    ):
        codec_remaining = encode_deadline - time.monotonic()
        if codec_remaining <= 0:
            raise TaskError("host_video_compress_timeout", "视频处理超时，请稍后重试或缩短视频。")
        cmd = [
            *base_cmd,
            "-vf",
            scale_filter,
            "-c:v",
            codec,
            *extra,
            "-b:v",
            f"{video_kbps}k",
            "-maxrate",
            f"{int(video_kbps * 1.25)}k",
            "-bufsize",
            f"{int(video_kbps * 2)}k",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_kbps}k",
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            "-fs",
            str(int(target_bytes)),
            str(dst),
        ]
        code, _stdout, stderr = _run_subprocess(cmd, timeout=codec_remaining)
        if code == 0 and dst.is_file() and dst.stat().st_size > 0:
            return 0, stderr
        last_stderr = stderr
    return 1, last_stderr


def compress_video(
    content: bytes,
    *,
    suffix: str = ".mp4",
    target_bytes: int = DEFAULT_TARGET_BYTES,
    max_duration_s: float = DEFAULT_MAX_DURATION_SECONDS,
    oversize_action: str = "compress",
    timeout: float = 60.0,
) -> CompressionResult:
    """Validate, optionally trim/compress a video within bounded size and duration."""
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise TaskError("host_video_invalid", "视频内容为空或格式无效。")
    budget = float(timeout)
    if budget <= 0:
        raise TaskError("host_video_compress_timeout", "视频处理超时，请稍后重试或缩短视频。")
    raw = bytes(content)
    ext = (Path(f"clip{suffix}").suffix or ".mp4").lower()
    if ext in _EXCLUDED_SUFFIXES:
        raise TaskError("host_video_format_unsupported", "不支持 .ts 切片文件作为视频理解输入。")
    safe_target = max(256 * 1024, min(int(target_bytes), MAX_DATA_URI_BYTES))
    magic_kind = sniff_video_container(raw)
    action = oversize_action if oversize_action in OVERSIZE_ACTIONS else "compress"
    # Fast-path only verified ISO BMFF MP4 (ftyp) payloads within size budget (0 extra subprocesses).
    if action == "compress" and ext == ".mp4" and magic_kind == "mp4" and len(raw) <= safe_target:
        return CompressionResult(
            content=raw,
            mime_type="video/mp4",
            compressed=False,
            truncated=False,
            duration_s=None,
            original_duration_s=None,
            original_bytes=len(raw),
        )
    started = time.monotonic()
    ffmpeg, ffprobe = resolve_ffmpeg_tools()

    with tempfile.TemporaryDirectory(prefix="companion-video-prep-") as directory:
        src = Path(directory) / f"input{ext}"
        src.write_bytes(raw)
        media_info = probe_media_info(src, ffprobe=ffprobe, ffmpeg=ffmpeg, timeout=min(10.0, budget))
        original_duration = media_info.duration_s
        actual_is_mp4 = _resolve_actual_is_mp4(ext, magic_kind, media_info)
        plan = classify(
            len(raw),
            original_duration,
            target_bytes=safe_target,
            max_duration_s=max_duration_s,
            oversize_action=oversize_action,
            force_transcode=not actual_is_mp4,
        )
        if plan.decision == "reject":
            raise TaskError("host_video_limit", plan.reason)
        if not plan.should_transcode:
            return CompressionResult(
                content=raw,
                mime_type="video/mp4" if actual_is_mp4 else f"video/{ext.lstrip('.') or 'mp4'}",
                compressed=False,
                truncated=False,
                duration_s=original_duration,
                original_duration_s=original_duration,
                original_bytes=len(raw),
            )
        if not ffmpeg:
            if actual_is_mp4 and len(raw) <= safe_target and plan.truncate_to is None:
                return CompressionResult(
                    content=raw,
                    mime_type="video/mp4",
                    compressed=False,
                    truncated=False,
                    duration_s=original_duration,
                    original_duration_s=original_duration,
                    original_bytes=len(raw),
                )
            raise TaskError(
                "host_video_ffmpeg_unavailable",
                "未检测到 ffmpeg，无法转码非 MP4 视频或压缩超出限制的视频。",
            )

        effective_duration = plan.truncate_to or original_duration or 30.0
        last_err = ""
        timed_out = False
        for pass_index in (0, 1):
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            video_kbps, audio_kbps, width = _bitrate_plan(safe_target, effective_duration, pass_index)
            dst = Path(directory) / f"output-{pass_index}.mp4"
            code, stderr = _run_ffmpeg_encode(
                ffmpeg,
                src,
                dst,
                video_kbps=video_kbps,
                audio_kbps=audio_kbps,
                max_width=width,
                truncate_to=plan.truncate_to,
                target_bytes=safe_target,
                timeout=remaining,
            )
            if code != 0 or not dst.is_file():
                last_err = stderr.decode("utf-8", errors="ignore").strip()[-160:]
                continue
            out_bytes = dst.read_bytes()
            if 0 < len(out_bytes) <= safe_target:
                probe_rem = budget - (time.monotonic() - started)
                out_duration = (
                    probe_duration(dst, ffprobe=ffprobe, ffmpeg=ffmpeg, timeout=min(5.0, probe_rem))
                    if probe_rem > 0
                    else None
                )
                return CompressionResult(
                    content=out_bytes,
                    mime_type="video/mp4",
                    compressed=True,
                    truncated=plan.truncate_to is not None,
                    duration_s=out_duration or effective_duration,
                    original_duration_s=original_duration,
                    original_bytes=len(raw),
                )
        if timed_out:
            raise TaskError("host_video_compress_timeout", "视频处理超时，请稍后重试或缩短视频。")
        detail = f"（{last_err}）" if last_err else ""
        raise TaskError(
            "host_video_compress_failed",
            f"视频压缩未能压入目标体积上限（{safe_target / (1024 * 1024):.1f} MB）{detail}。",
        )
