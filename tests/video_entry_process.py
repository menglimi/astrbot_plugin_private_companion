"""Exercise the current plugin entry's core video request without model or QQ traffic."""

import asyncio
import base64
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS


def run():
    from astrbot_host.video.decoder import _ffmpeg_binary

    binary = _ffmpeg_binary()
    if binary is None:
        print("SKIP: FFmpeg is unavailable")
        return
    os.environ["PATH"] = str(Path(binary).parent) + os.pathsep + os.environ.get("PATH", "")
    with TemporaryDirectory(prefix="v2-core-video-entry-") as root:
        os.environ["ASTRBOT_ROOT"] = root
        path = Path(root) / "data" / "temp" / "clip.mp4"
        path.parent.mkdir(parents=True)
        subprocess.run([
            binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=red:s=64x48:r=10:d=1.2", "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
            str(path),
        ], check=True, timeout=10)

        from astrbot.core.agent.message import TextPart
        from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
        from astrbot_host.core_reply_perception import video_request_state
        from tests.test_core_reply_video_sources import ENTRY, private_context

        context = private_context()
        settings = {"video_understanding_enabled": True,
                    "video_understanding_private_enabled": True}
        application = NS(reply_settings=NS(capture=lambda _context, _at: {"settings": settings}))
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS(now=lambda: None, context=None, session=NS(application=application))
        state = {"prepared": {"context": context, "event_ref": "entry-video"}}
        event = NS(get_extra=lambda _key: state)
        request = NS(extra_user_content_parts=[TextPart(text=(
            f"[Video Attachment: name {path.name}, path {path}]"))])

        asyncio.run(plugin.restate_companion_core_video_state(event, request))
        frames = [part for part in request.extra_user_content_parts if part.type == "image_url"]
        assert video_request_state(request) == "sampled_frames"
        assert [part.image_url.id for part in frames] == [
            "companion-video-frame:0:0", "companion-video-frame:0:3",
            "companion-video-frame:0:7", "companion-video-frame:0:11",
        ]
        assert all(part._no_save for part in request.extra_user_content_parts[1:])
        assert all(base64.b64decode(part.image_url.url.partition(",")[2]).startswith(b"\x89PNG\r\n\x1a\n")
                   for part in frames)
        provider = object.__new__(ProviderGoogleGenAI)
        assembled = asyncio.run(provider.assemble_context("", extra_user_content_parts=request.extra_user_content_parts))
        assert len([part for part in assembled["content"] if part["type"] == "image_url"]) == 4
        print("PASS: main.py plugin entry supplied four real sampled video frames to the core request; no model or QQ traffic.")


if __name__ == "__main__":
    run()
