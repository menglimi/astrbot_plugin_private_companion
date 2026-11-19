"""FFmpeg/FFprobe resolution, duration probing and size/duration-bounded compression."""

from __future__ import annotations

from dataclasses import dataclass
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
_EXCLUDED_SUFFIXES = frozenset({".ts", ".m2ts", ".mts"})


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
        stdout, stderr = process.communicate(timeout=max(0.5, float(timeout)))
        return process.returncode or 0, stdout or b"", stderr or b""
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            process.communicate(timeout=2)
        except Exception:
            pass
        raise TaskError("host_video_compress_timeout", "视频处理超时，请稍后重试或缩短视频。") from None


def probe_duration(
    path: Path,
    *,
    ffprobe: str | None = None,
    ffmpeg: str | None = None,
    timeout: float = 10.0,
) -> float | None:
    """Probe media duration in seconds using ``ffprobe`` or ``ffmpeg -i`` fallback."""
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
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                timeout=timeout,
            )
            if code == 0:
                text = stdout.decode("utf-8", errors="ignore").strip().splitlines()
                if text:
                    value = float(text[0].strip())
                    if math.isfinite(value) and value > 0:
                        return value
        except (TaskError, ValueError):
            pass
    if ffmpeg:
        try:
            _code, _stdout, stderr = _run_subprocess(
                [ffmpeg, "-nostdin", "-hide_banner", "-i", str(path)],
                timeout=timeout,
            )
            match = _DURATION_RE.search(stderr.decode("utf-8", errors="ignore"))
            if match:
                hours, minutes, secs = match.groups()
                value = int(hours) * 3600 + int(minutes) * 60 + float(secs)
                if math.isfinite(value) and value > 0:
                    return value
        except (TaskError, ValueError):
            pass
    return None


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
    scale_filter = f"scale='min({max_width},iw)':-2"
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
    last_stderr = b""
    for codec, extra in (
        ("libx264", ["-preset", "veryfast"]),
        ("mpeg4", ["-q:v", "7"]),
    ):
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
        code, _stdout, stderr = _run_subprocess(cmd, timeout=timeout)
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
    raw = bytes(content)
    ext = (Path(f"clip{suffix}").suffix or ".mp4").lower()
    if ext in _EXCLUDED_SUFFIXES:
        raise TaskError("host_video_format_unsupported", "不支持 .ts 切片文件作为视频理解输入。")
    safe_target = max(256 * 1024, min(int(target_bytes), MAX_DATA_URI_BYTES))
    non_mp4 = ext != ".mp4"
    action = oversize_action if oversize_action in OVERSIZE_ACTIONS else "compress"
    if action == "compress" and not non_mp4 and len(raw) <= safe_target:
        return CompressionResult(
            content=raw,
            mime_type="video/mp4",
            compressed=False,
            truncated=False,
            duration_s=None,
            original_duration_s=None,
            original_bytes=len(raw),
        )
    ffmpeg, ffprobe = resolve_ffmpeg_tools()

    with tempfile.TemporaryDirectory(prefix="companion-video-prep-") as directory:
        src = Path(directory) / f"input{ext}"
        src.write_bytes(raw)
        original_duration = probe_duration(src, ffprobe=ffprobe, ffmpeg=ffmpeg, timeout=min(10.0, timeout))
        plan = classify(
            len(raw),
            original_duration,
            target_bytes=safe_target,
            max_duration_s=max_duration_s,
            oversize_action=oversize_action,
            force_transcode=non_mp4 and len(raw) > safe_target,
        )
        if plan.decision == "reject":
            raise TaskError("host_video_limit", plan.reason)
        if not plan.should_transcode:
            return CompressionResult(
                content=raw,
                mime_type="video/mp4" if ext == ".mp4" else f"video/{ext.lstrip('.') or 'mp4'}",
                compressed=False,
                truncated=False,
                duration_s=original_duration,
                original_duration_s=original_duration,
                original_bytes=len(raw),
            )
        if not ffmpeg:
            if len(raw) <= safe_target and plan.truncate_to is None:
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
                "未检测到 ffmpeg，无法压缩或截断超出限制的视频。",
            )

        effective_duration = plan.truncate_to or original_duration or 30.0
        started = time.monotonic()
        last_err = ""
        for pass_index in (0, 1):
            remaining = max(5.0, float(timeout) - (time.monotonic() - started))
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
                out_duration = probe_duration(dst, ffprobe=ffprobe, ffmpeg=ffmpeg, timeout=5.0)
                return CompressionResult(
                    content=out_bytes,
                    mime_type="video/mp4",
                    compressed=True,
                    truncated=plan.truncate_to is not None,
                    duration_s=out_duration or effective_duration,
                    original_duration_s=original_duration,
                    original_bytes=len(raw),
                )
        detail = f"（{last_err}）" if last_err else ""
        raise TaskError(
            "host_video_compress_failed",
            f"视频压缩未能压入目标体积上限（{safe_target / (1024 * 1024):.1f} MB）{detail}。",
        )
