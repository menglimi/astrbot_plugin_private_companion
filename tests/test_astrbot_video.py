"""Container boundaries, bounded decode, sampling truth and process cleanup."""

import asyncio
import base64
from dataclasses import replace
from pathlib import Path
import subprocess
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.video.container import boxes, child, inspect_container
from astrbot_host.video.decoder import _ffmpeg_binary, inspect_video, relative_times
from astrbot_host.video.loader import decode_video, load_host_resolved_video, load_native_video
from companion_v2.adapters.astrbot.native import NativeVideoLocator
from companion_v2.conversation.media.video.models import CapturedVideo, MAX_VIDEO_BYTES, fits_video_budget
from companion_v2.execution.models import TaskError
from tests.video_support import video_bytes


def table_box(content, kind):
    movie = next(b for b in boxes(content, 0, len(content)) if b.kind == b"moov")
    track = child(content, movie, b"trak")
    table = child(content, child(content, child(content, track, b"mdia"), b"minf"), b"stbl")
    return child(content, table, kind)


class VideoDecoderTests(unittest.IsolatedAsyncioTestCase):
    def test_ffmpeg_fallback_samples_a_real_mp4_without_opencv(self):
        binary = _ffmpeg_binary()
        if binary is None:
            self.skipTest("FFmpeg is unavailable")
        with TemporaryDirectory(prefix="v2-ffmpeg-video-test-") as directory:
            path = Path(directory) / "clip.mp4"
            subprocess.run([
                binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=red:s=64x48:r=10:d=1.2", "-c:v", "mpeg4",
                "-pix_fmt", "yuv420p", str(path),
            ], check=True, timeout=10)
            with patch.dict(sys.modules, {"cv2": None}):
                video = inspect_video(path.read_bytes(), 0)
        self.assertEqual(video.descriptor.decoder, "ffmpeg:external")
        self.assertEqual(tuple(frame.index for frame in video.descriptor.frames), (0, 3, 7, 11))
        self.assertEqual(len(video.frames), 4)

    async def test_host_resolved_video_path_must_stay_in_astrbot_temp(self):
        with TemporaryDirectory(prefix="v2-video-trusted-") as trusted, TemporaryDirectory(prefix="v2-video-other-") as other:
            inside = Path(trusted) / "clip.mp4"
            outside = Path(other) / "clip.mp4"
            inside.write_bytes(b"video")
            outside.write_bytes(b"other")
            with patch("astrbot.core.utils.astrbot_path.get_astrbot_temp_path", return_value=trusted), \
                    patch("astrbot_host.video.loader.decode_video", AsyncMock(return_value="decoded")) as decode:
                self.assertEqual(await load_host_resolved_video(str(inside), 2), "decoded")
                decode.assert_awaited_once_with(b"video", 2)
                with self.assertRaises(TaskError) as caught:
                    await load_host_resolved_video(str(outside), 0)
                self.assertEqual(caught.exception.code, "host_video_location_invalid")

    async def test_original_bytes_and_four_timed_samples_survive_worker(self):
        content = video_bytes()
        expected = inspect_video(content, 2)
        actual = await load_native_video(NativeVideoLocator(2, "base64://" + base64.b64encode(content).decode("ascii")))
        self.assertEqual(expected, actual)
        self.assertEqual(actual.content, content)
        self.assertEqual([(f.index, f.start_ms) for f in actual.descriptor.frames], [(0, 0), (3, 300), (7, 700), (11, 1100)])
        self.assertEqual(actual.descriptor.coverage, "sampled_frames")
        self.assertEqual(actual.descriptor.duration_ms, 1200)
        self.assertEqual(len({f.descriptor.content_hash for f in actual.frames}), 4)

    def test_short_video_all_frames_and_unknown_decoder_timing_are_explicit(self):
        result = inspect_video(video_bytes(3), 0)
        self.assertEqual(result.descriptor.coverage, "all_decoded_frames")
        for times in ([0, 0, 0], [20, 10, 30], [float("nan"), 10], [0, 1001], [-1, 0]):
            self.assertIsNone(relative_times(times, 1000))
        self.assertEqual(relative_times([500, 540, 590], 1000), [0, 40, 90])
        unknown = replace(result.descriptor, timing="unknown", frames=tuple(replace(f, start_ms=None) for f in result.descriptor.frames))
        self.assertTrue(all(f.start_ms is None for f in unknown.frames))
        with self.assertRaises(TaskError):
            replace(unknown, timing="decoder_elapsed_ms")

    def test_frame_resize_preserves_original_dimensions_and_never_changes_source_bytes(self):
        content = video_bytes(count=1, size=(1100, 600))
        result = inspect_video(content, 0)
        self.assertEqual((result.descriptor.width, result.descriptor.height), (1100, 600))
        self.assertEqual(max(result.frames[0].descriptor.width, result.frames[0].descriptor.height), 1024)
        self.assertEqual(result.content, content)

    def test_truncated_container_missing_sample_and_external_data_ref_are_rejected_before_decode(self):
        content = video_bytes()
        bad_offset = bytearray(content)
        offset_box = table_box(content, b"stco")
        struct.pack_into(">I", bad_offset, offset_box.start + 8, len(content) + 100)
        external = bytearray(content)
        marker = content.index(b"url ")
        external[marker + 7] = 0
        for value in (content[:-1], content[:30], content + b"garbage", bytes(bad_offset), bytes(external)):
            with self.subTest(length=len(value)), patch("cv2.VideoCapture") as capture, self.assertRaises(TaskError):
                inspect_video(value, 0)
            capture.assert_not_called()

    def test_fragmented_or_non_video_files_and_unbounded_sample_counts_are_not_admitted(self):
        content = video_bytes()
        huge = bytearray(content)
        stts = table_box(content, b"stts")
        struct.pack_into(">I", huge, stts.start + 8, 1000000)
        for value in (b"#EXTM3U\nhttp://127.0.0.1", content + struct.pack(">I4s", 8, b"moof"), bytes(huge)):
            with self.assertRaises(TaskError):
                inspect_video(value, 0)

    def test_declared_audio_track_does_not_become_audio_evidence(self):
        content = video_bytes()
        movie = next(b for b in boxes(content, 0, len(content)) if b.kind == b"moov")
        track = child(content, movie, b"trak")
        # A synthetic second track tests declaration handling only; the video
        # decoder never consumes this track as an audio observation.
        audio = bytearray(content[track.start - 8:track.end])
        handler = audio.index(b"hdlr")
        audio[handler + 12:handler + 16] = b"soun"
        extended = bytearray(content[:movie.end] + audio + content[movie.end:])
        struct.pack_into(">I", extended, movie.start - 8, movie.end - movie.start + 8 + len(audio))
        self.assertEqual(inspect_container(bytes(extended)).audio_tracks, 1)

    def test_manifest_tampering_and_shared_duration_count_limits(self):
        video = inspect_video(video_bytes(), 0)
        with self.assertRaises(TaskError):
            CapturedVideo(video.descriptor, b"changed", video.frames)
        with self.assertRaises(TaskError):
            replace(video.descriptor, frames=tuple(reversed(video.descriptor.frames)))
        long = replace(video.descriptor, duration_ms=100000)
        self.assertFalse(fits_video_budget((long, long)))
        self.assertFalse(fits_video_budget((video.descriptor,) * 3))

    async def test_paths_covers_and_private_urls_never_trigger_local_or_remote_decoding(self):
        with patch("astrbot_host.video.loader.decode_video", AsyncMock()) as decode:
            for value in ("C:/private.mp4", "file:///C:/private.mp4", "http://127.0.0.1/video", "http://[::1]/video"):
                with self.subTest(value=value), self.assertRaises(TaskError) as caught:
                    await load_native_video(NativeVideoLocator(0, value))
                self.assertEqual(caught.exception.code, "host_video_location_invalid")
            decode.assert_not_awaited()

    async def test_public_download_uses_video_budget_and_oversize_does_not_spawn_worker(self):
        with patch("astrbot_host.video.loader.fetch_attachment", AsyncMock(return_value=video_bytes())) as fetch:
            result = await load_native_video(NativeVideoLocator(0, "https://attachment.example/video"))
            self.assertEqual(result.content, video_bytes())
            fetch.assert_awaited_once_with("https://attachment.example/video", maximum_bytes=MAX_VIDEO_BYTES)
        with patch("astrbot_host.video.loader.decode_video", AsyncMock()) as decode, self.assertRaises(TaskError):
            await load_native_video(NativeVideoLocator(0, "base64://" + "A" * ((MAX_VIDEO_BYTES + 2) // 3 * 4 + 1)))
        decode.assert_not_awaited()

    async def test_timeout_and_cancellation_reap_decoder_and_remove_parent_owned_temp_original(self):
        class HeldProcess:
            returncode = None
            killed = False
            drained = False
            async def communicate(self, content=None):
                if self.killed:
                    self.drained = True
                    return b"", b""
                entered.set()
                await asyncio.Event().wait()
            def kill(self):
                self.killed, self.returncode = True, -1
        for cancel in (False, True):
            entered, process, directories = asyncio.Event(), HeldProcess(), []
            async def spawn(*args, **kwargs):
                directory = Path(args[-1])
                directories.append(directory)
                (directory / "original.mp4").write_bytes(video_bytes())
                return process
            with patch("astrbot_host.video.loader.asyncio.create_subprocess_exec", spawn), patch("astrbot_host.video.loader.DECODE_TIMEOUT", 0.03):
                task = asyncio.create_task(decode_video(video_bytes(), 0))
                await entered.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(TaskError) as caught:
                        await task
                    self.assertEqual(caught.exception.code, "host_video_decode_timeout")
            self.assertTrue(process.killed and process.drained)
            self.assertTrue(directories and all(not d.exists() for d in directories))
