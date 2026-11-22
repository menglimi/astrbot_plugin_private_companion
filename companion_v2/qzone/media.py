"""Describe the pictures and clips attached to a QQ Zone post.

The model cannot open a Zone image or video URL, so this module turns them into
short text: pictures through a vision chat completion, clips through uniform
ffmpeg frames plus an optional local ASR transcription.  Everything is best
effort: a broken CDN object becomes a short note instead of failing the read.

Configuration is read from the host, never copied into this plugin's settings:

* ``ASTRBOT_ROOT`` / ``ASTRBOT_DATA_ROOT`` locate ``data/cmd_config.json`` and
  the installed plugin directory.
* ``COMPANION_QZONE_VISION_BASE`` / ``_KEY`` / ``_MODEL`` override the vision
  provider when the host config is not the right source.
* ``COMPANION_QZONE_FFMPEG`` and ``COMPANION_QZONE_ASR_ROOT`` override the
  ffmpeg binary and local SenseVoice root.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
REFERER = "https://user.qzone.qq.com/"

FFMPEG = (os.environ.get("COMPANION_QZONE_FFMPEG") or shutil.which("ffmpeg") or "").strip()
ASR_ROOT = os.environ.get("COMPANION_QZONE_ASR_ROOT", "").strip()

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024
MAX_IMAGES = 20
MAX_VIDEO_SECONDS = 180
DESCRIBE_SIZE = 1024
DESCRIBE_PROMPT = "用中文简短描述这张图：是什么、有什么、在做什么。一两句话，别超过 60 字。"
FRAME_PROMPT = "这是视频第 {index} 个画面。用中文一句话说画面里是什么，别超过 40 字。"


def _headers():
    return {"User-Agent": USER_AGENT, "Referer": REFERER}


def _data_root() -> Path | None:
    configured = os.environ.get("ASTRBOT_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    root = os.environ.get("ASTRBOT_ROOT", "").strip()
    if root:
        return Path(root) / "data"
    # media.py lives at <data>/plugins/<plugin>/companion_v2/qzone/media.py.
    return Path(__file__).resolve().parents[4]


def download(url, *, limit):
    """Fetch one public CDN object. Returns (bytes, note)."""
    if not isinstance(url, str) or not url.startswith("http"):
        return None, "地址无效"
    try:
        request = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(request, timeout=45) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                return None, "文件太大"
            data = response.read(limit + 1)
        if len(data) > limit:
            return None, "文件太大"
        return data, ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any network fault becomes a note
        return None, type(exc).__name__


def shrink(data, *, size=DESCRIBE_SIZE):
    """Re-encode an image small enough for a vision call. Returns base64 or None."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        if max(image.size) > size:
            image.thumbnail((size, size), Image.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=82)
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a corrupt picture is not a failure
        return None


def _vision_credentials():
    """Read an explicit override, else the first vision-capable host source."""
    base = os.environ.get("COMPANION_QZONE_VISION_BASE", "").strip().rstrip("/")
    key = os.environ.get("COMPANION_QZONE_VISION_KEY", "").strip()
    model = os.environ.get("COMPANION_QZONE_VISION_MODEL", "").strip()
    if base and key and model:
        return {"api_base": base, "key": key, "model": model}
    root = _data_root()
    if root is None:
        return None
    path = root / "cmd_config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None
    for source in config.get("provider_sources") or []:
        if not isinstance(source, dict):
            continue
        base = str(source.get("api_base") or "").strip().rstrip("/")
        key = source.get("key")
        if isinstance(key, list):
            key = key[0] if key else ""
        model = str(source.get("model") or source.get("vision_model") or "").strip()
        if base and key and model:
            return {"api_base": base, "key": key, "model": model}
    return None


def describe_image(data, *, prompt=DESCRIBE_PROMPT):
    """One picture to one line of Chinese."""
    credentials = _vision_credentials()
    if not credentials:
        return ""
    payload = shrink(data)
    if not payload:
        return ""
    body = {
        "model": credentials["model"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{payload}"}},
        ]}],
        "max_tokens": 200,
    }
    request = urllib.request.Request(
        credentials["api_base"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + credentials["key"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            answer = json.loads(response.read().decode())
        return str((answer["choices"][0]["message"].get("content") or "")).strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("[Qzone] vision call failed: %s", type(exc).__name__)
        return ""


def _ffmpeg(*arguments, timeout=60):
    if not FFMPEG or not os.path.isfile(FFMPEG):
        return False
    try:
        completed = subprocess.run([FFMPEG, "-y", *arguments], capture_output=True, timeout=timeout)
        return completed.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def video_frames(data, *, count=3, seconds=0):
    """Sample a clip into a few stills. Returns (frames, note)."""
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "clip.mp4")
        with open(source, "wb") as handle:
            handle.write(data)
        if seconds <= 0:
            seconds = 8
        step = max(1.0, seconds / max(1, count))
        frames = []
        for index in range(1, count + 1):
            target = os.path.join(folder, f"frame_{index}.jpg")
            stamp = min(seconds - 0.2, step * index)
            if _ffmpeg("-ss", str(max(0.0, stamp)), "-i", source, "-frames:v", "1",
                       "-vf", "scale=640:-2", target):
                try:
                    with open(target, "rb") as handle:
                        frames.append(handle.read())
                except OSError:
                    pass
        if not frames:
            return [], "抽帧失败"
        return frames, ""


def video_soundtrack(data):
    """Transcribe a clip's audio with the local ASR the video plugins already use."""
    if not ASR_ROOT:
        return ""
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "clip.mp4")
        target = os.path.join(folder, "audio.wav")
        with open(source, "wb") as handle:
            handle.write(data)
        if not _ffmpeg("-i", source, "-vn", "-acodec", "pcm_s16le",
                       "-ar", "16000", "-ac", "1", target):
            return ""
        try:
            root = _data_root()
            if root is not None:
                sys.path.insert(0, str(root / "plugins"))
            from astrbot_plugin_dafeiyu_video.video import transcribe_local  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return ""
        try:
            import asyncio

            text, _ = asyncio.run(transcribe_local(ASR_ROOT, [target]))
        except Exception:  # noqa: BLE001
            return ""
        text = str(text or "").strip()
        if not text or text in ("/sil", "[sil]", "sil"):
            return ""
        return text[:300]


def describe_post(post):
    """Every picture and clip of one post, as words the model can read."""
    pictures = []
    for index, item in enumerate(post.images[:MAX_IMAGES], start=1):
        if isinstance(item, (tuple, list)):
            url = item[0]
        elif hasattr(item, "url"):
            url = getattr(item, "url")
        else:
            url = str(item)
        data, note = download(url, limit=MAX_IMAGE_BYTES)
        if not data:
            pictures.append({"index": index, "text": "", "note": note or "下载失败"})
            continue
        pictures.append({"index": index, "text": describe_image(data), "note": ""})

    clips = []
    for index, item in enumerate(post.videos[:2], start=1):
        if isinstance(item, (tuple, list)):
            url = item[0] if item else ""
            seconds = int(item[2] or 0) if len(item) > 2 else 0
        else:
            url = getattr(item, "url", "") if hasattr(item, "url") else ""
            seconds = int(getattr(item, "seconds", 0) or 0)
        if seconds > MAX_VIDEO_SECONDS:
            clips.append({"index": index, "seconds": seconds, "frames": [], "audio": "",
                          "note": "视频太长，没看"})
            continue
        data, note = download(url, limit=MAX_VIDEO_BYTES)
        if not data:
            clips.append({"index": index, "seconds": seconds, "frames": [], "audio": "",
                          "note": note or "下载失败"})
            continue
        frames, frame_note = video_frames(data, seconds=seconds)
        spoken = video_soundtrack(data)
        described = []
        for order, frame in enumerate(frames, start=1):
            line = describe_image(frame, prompt=FRAME_PROMPT.format(index=order))
            if line:
                described.append(line)
        clips.append({"index": index, "seconds": seconds, "frames": described,
                      "audio": spoken, "note": frame_note})

    return {"images": pictures, "videos": clips}
