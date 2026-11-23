"""Regression tests for Qzone media/service/API hardening and mobile gateway proxy safety."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.error
from uuid import uuid4

from PIL import Image

from tests import astrbot_data_root  # noqa: F401 - isolate ASTRBOT_ROOT before SDK imports

from astrbot_host.native_extensions.reality import mobile_gateway
from astrbot_host.qzone_api import QzoneWebAPI
from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier, RoleBinding
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import (
    ConversationRoute,
    PersonaBinding,
    PersonaDirectory,
    PersonaSelector,
    SelectorKind,
)
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.qzone import media as qzone_media
from companion_v2.qzone import policy as qzone_policy
from companion_v2.qzone import protocol as qzone_protocol
from companion_v2.qzone.references import COMMENT, LIKE, QzonePhoto, QzonePost
from companion_v2.qzone.service import QzoneService, owner_key
from companion_v2.qzone.transport import TransportOutcome
from companion_v2.storage.runtime import StorageRuntime
from tests.test_qzone_extension import COOKIE, MSGLIST, PUBLISH, FakeSettings, FakeTransport, QzoneServiceFixture


NOW = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
INSTALLATION = "installation-pr04-test"


def _png_bytes(mode="RGB", color=(12, 34, 56), size=(32, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeHTTPResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, *, chunk_limit: int | None = None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {}
        self.bytes_read = 0
        self._chunk_limit = chunk_limit

    def read(self, amt: int = -1) -> bytes:
        if self._chunk_limit is not None and amt > 0:
            amt = min(amt, self._chunk_limit)
        data = self._buf.read(amt)
        self.bytes_read += len(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class QzoneMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        qzone_media.clear_media_cache()
        self.tmp = Path(tempfile.gettempdir()) / f"pr04-qzone-media-{uuid4().hex}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(qzone_media.clear_media_cache)

    def _write_cmd_config(self, payload: dict) -> Path:
        data_dir = self.tmp / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "cmd_config.json").write_text(json.dumps(payload), encoding="utf-8")
        return data_dir

    # --- 1. _vision_credentials() AstrBot v4.29.4 + legacy + env override ---

    def test_vision_credentials_parses_astrbot_v4_provider_join_and_skips_disabled(self):
        data_dir = self._write_cmd_config({
            "provider_sources": [
                {"id": "src-disabled", "api_base": "https://disabled.example/v1", "key": ["sk-dis"]},
                {"id": "src-tts", "api_base": "https://tts.example/v1", "key": ["sk-tts"], "provider_type": "text_to_speech"},
                {"id": "src-relay", "api_base": "https://relay.example/v1/", "key": ["", "$TEST_RELAY_KEY"]},
                {"id": "src-vision", "api_base": "https://vision.example/v1/chat/completions", "key": "sk-vision-str"},
            ],
            "provider": [
                {"id": "p-disabled", "provider_source_id": "src-disabled", "model": "gpt-4o", "enable": False},
                {"id": "p-tts", "provider_source_id": "src-tts", "model": "tts-1", "provider_type": "text_to_speech", "enable": True},
                {"id": "p-anthropic", "provider_source_id": "src-relay", "model": "claude-3-7", "type": "anthropic_chat_completion", "enable": True},
                {"id": "p-text-only", "provider_source_id": "src-relay", "model": "deepseek-r1", "modalities": ["text"], "enable": True},
                {"id": "p-chat", "provider_source_id": "src-relay", "model_config": {"model": "gpt-4o-mini"}, "enable": True},
                {"id": "p-vision", "provider_source_id": "src-vision", "model": "gemini-2.5-flash", "modalities": ["text", "image"], "enable": True},
            ],
        })
        env = {
            "ASTRBOT_DATA_ROOT": str(data_dir),
            "TEST_RELAY_KEY": "sk-from-env",
            "COMPANION_QZONE_VISION_BASE": "",
            "COMPANION_QZONE_VISION_KEY": "",
            "COMPANION_QZONE_VISION_MODEL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            creds = qzone_media._vision_credentials()
        self.assertEqual(creds, {
            "api_base": "https://vision.example/v1",
            "key": "sk-vision-str",
            "model": "gemini-2.5-flash",
        })

        # Without explicit vision modality, falls back to enabled OpenAI-compatible chat provider with list+$ENV key.
        data_dir = self._write_cmd_config({
            "provider_sources": [
                {"id": "src-relay", "api_base": "https://relay.example/v1/", "key": ["", "$TEST_RELAY_KEY"]},
            ],
            "provider": [
                {"id": "p-chat", "provider_source_id": "src-relay", "model_config": {"model": "gpt-4o-mini"}, "enable": True},
            ],
        })
        with patch.dict(os.environ, env, clear=False):
            creds2 = qzone_media._vision_credentials()
        self.assertEqual(creds2, {
            "api_base": "https://relay.example/v1",
            "key": "sk-from-env",
            "model": "gpt-4o-mini",
        })

    def test_vision_credentials_keeps_legacy_provider_sources_and_env_override(self):
        data_dir = self._write_cmd_config({
            "provider_sources": [
                {"id": "legacy", "api_base": "https://legacy.example/v1", "key": ["sk-legacy"], "vision_model": "qwen-vl"},
            ],
        })
        with patch.dict(os.environ, {
            "ASTRBOT_DATA_ROOT": str(data_dir),
            "COMPANION_QZONE_VISION_BASE": "",
            "COMPANION_QZONE_VISION_KEY": "",
            "COMPANION_QZONE_VISION_MODEL": "",
        }, clear=False):
            self.assertEqual(qzone_media._vision_credentials(), {
                "api_base": "https://legacy.example/v1",
                "key": "sk-legacy",
                "model": "qwen-vl",
            })

        with patch.dict(os.environ, {
            "ASTRBOT_DATA_ROOT": str(data_dir),
            "COMPANION_QZONE_VISION_BASE": "https://override.example/v1/",
            "COMPANION_QZONE_VISION_KEY": "sk-override",
            "COMPANION_QZONE_VISION_MODEL": "gemini-2.5-pro",
        }, clear=False):
            self.assertEqual(qzone_media._vision_credentials(), {
                "api_base": "https://override.example/v1",
                "key": "sk-override",
                "model": "gemini-2.5-pro",
            })

    def test_describe_image_callback_priority_and_relaxed_max_tokens(self):
        seen_tokens = []
        img = _png_bytes()

        def cb(b64_payload, prompt, *, max_tokens=0):
            seen_tokens.append(max_tokens)
            self.assertTrue(len(b64_payload) > 20)
            return "一只小猫"

        text = qzone_media.describe_image(img, vision_callback=cb, max_tokens=200, use_cache=False)
        self.assertEqual(text, "一只小猫")
        self.assertGreaterEqual(seen_tokens[0], 1024)

        # HTTP path also clamps max_tokens >= 1024 to avoid Gemini thinking truncation/400.
        captured_request = {}

        def fake_urlopen(req, timeout=0):
            captured_request["url"] = req.full_url
            captured_request["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeHTTPResponse(json.dumps({
                "choices": [{"message": {"content": "窗台上的绿萝"}}]
            }).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = qzone_media.describe_image(
                img,
                credentials={"api_base": "https://api.example/v1", "key": "sk-test", "model": "gemini-2.5-flash"},
                max_tokens=200,
                use_cache=False,
            )
        self.assertEqual(result, "窗台上的绿萝")
        self.assertEqual(captured_request["url"], "https://api.example/v1/chat/completions")
        self.assertGreaterEqual(captured_request["body"]["max_tokens"], 1024)

    def test_describe_post_early_skips_downloads_without_vision_credentials(self):
        post = QzonePost(
            post_id="p1",
            author_uin="1",
            author_name="me",
            excerpt="hi",
            images=(QzonePhoto("https://cdn.example/1.jpg", 100, 100, ("1",)),),
            videos=(("https://cdn.example/clip.mp4", "", 10),),
        )
        with patch.object(qzone_media, "_vision_credentials", return_value=None), \
             patch.object(qzone_media, "download", side_effect=AssertionError("must not download")) as dl, \
             patch.object(qzone_media, "video_frames", side_effect=AssertionError("must not extract frames")):
            out = qzone_media.describe_post(post)
        dl.assert_not_called()
        self.assertEqual(out["images"][0]["note"], "未配置视觉模型")
        self.assertEqual(out["videos"][0]["note"], "未配置视觉模型")

    def test_describe_post_without_vision_runs_audio_only_when_transcriber_available(self):
        post = QzonePost(
            post_id="p2",
            author_uin="1",
            author_name="me",
            excerpt="video",
            images=(QzonePhoto("https://cdn.example/1.jpg", 100, 100, ("1",)),),
            videos=(("https://cdn.example/clip.mp4", "", 8),),
        )
        downloaded = []

        def fake_download(url, *, limit, timeout=45.0):
            downloaded.append(url)
            return b"fake-mp4-bytes", ""

        with patch.object(qzone_media, "_vision_credentials", return_value=None), \
             patch.object(qzone_media, "_resolve_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             patch.object(qzone_media, "download", side_effect=fake_download), \
             patch.object(qzone_media, "video_frames", side_effect=AssertionError("no frame extraction")), \
             patch.object(qzone_media, "video_soundtrack", return_value="今天风很轻"):
            out = qzone_media.describe_post(post, transcriber=lambda wav: "今天风很轻")

        self.assertEqual(downloaded, ["https://cdn.example/clip.mp4"])
        self.assertEqual(out["images"][0]["note"], "未配置视觉模型")
        self.assertEqual(out["videos"][0]["frames"], [])
        self.assertEqual(out["videos"][0]["audio"], "今天风很轻")

    # --- 2. SSRF validation, controlled redirects, streaming byte limit ---

    def test_validate_remote_url_blocks_ssrf_targets(self):
        blocked_urls = [
            "",
            "file:///etc/passwd",
            "ftp://93.184.216.34/a.jpg",
            "gopher://127.0.0.1:6379/_INFO",
            "http:///no-host.jpg",
            "http://user:pass@93.184.216.34/a.jpg",
            "http://localhost/secret",
            "http://sub.localhost/secret",
            "http://127.0.0.1/secret",
            "http://10.0.0.1/secret",
            "http://172.16.0.1/secret",
            "http://192.168.1.1/secret",
            "http://169.254.169.254/latest/meta-data",
            "http://0.0.0.0/secret",
            "http://224.0.0.1/secret",
            "http://240.0.0.1/secret",
            "http://[::1]/secret",
            "http://[fe80::1]/secret",
            "http://[fc00::1]/secret",
            "http://[::ffff:127.0.0.1]/secret",
            "http://[::ffff:10.0.0.1]/secret",
            "http://[::ffff:169.254.169.254]/secret",
        ]
        for bad in blocked_urls:
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    qzone_media._validate_remote_url(bad)

        # Dual-stack DNS returning one public IP and one loopback IP MUST be rejected.
        dual_stack = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=dual_stack):
            with self.assertRaises(ValueError):
                qzone_media._validate_remote_url("https://dual.example.com/pic.jpg")

        public_only = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_only):
            self.assertEqual(
                qzone_media._validate_remote_url("https://cdn.example.com/pic.jpg"),
                "https://cdn.example.com/pic.jpg",
            )

    def test_download_blocks_redirect_to_internal_and_enforces_hop_limit(self):
        opened_urls = []
        public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        def redirect_to_internal(req, timeout=0):
            opened_urls.append(req.full_url)
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": "http://127.0.0.1:8080/admin"}, None
            )

        with patch("socket.getaddrinfo", return_value=public_dns), \
             patch.object(qzone_media._NO_REDIRECT_OPENER, "open", side_effect=redirect_to_internal):
            data, note = qzone_media.download("https://cdn.example.com/pic.jpg", limit=1024)
        self.assertIsNone(data)
        self.assertIn("内网", note)
        self.assertEqual(opened_urls, ["https://cdn.example.com/pic.jpg"])

        # Redirect loop stops at max_redirects.
        hop_count = 0

        def infinite_redirect(req, timeout=0):
            nonlocal hop_count
            hop_count += 1
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": f"https://cdn.example.com/hop/{hop_count}"}, None
            )

        with patch("socket.getaddrinfo", return_value=public_dns), \
             patch.object(qzone_media._NO_REDIRECT_OPENER, "open", side_effect=infinite_redirect):
            data2, note2 = qzone_media.download("https://cdn.example.com/start", limit=1024, max_redirects=3)
        self.assertIsNone(data2)
        self.assertIn("重定向", note2)
        self.assertEqual(hop_count, 4)

    def test_download_handles_bad_content_length_and_stops_streaming_bomb(self):
        public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        # Malformed Content-Length must not crash.
        resp_bad_cl = _FakeHTTPResponse(b"hello", headers={"Content-Length": "not-a-number"})
        with patch("socket.getaddrinfo", return_value=public_dns), \
             patch.object(qzone_media._NO_REDIRECT_OPENER, "open", return_value=resp_bad_cl):
            data, note = qzone_media.download("https://cdn.example.com/a.jpg", limit=100)
        self.assertEqual(data, b"hello")
        self.assertEqual(note, "")

        # Spoofed tiny Content-Length with huge streaming body stops early at limit + 1.
        huge_body = b"A" * (512 * 1024)
        resp_bomb = _FakeHTTPResponse(huge_body, headers={"Content-Length": "10"}, chunk_limit=4096)
        with patch("socket.getaddrinfo", return_value=public_dns), \
             patch.object(qzone_media._NO_REDIRECT_OPENER, "open", return_value=resp_bomb):
            data_bomb, note_bomb = qzone_media.download("https://cdn.example.com/bomb.jpg", limit=8192)
        self.assertIsNone(data_bomb)
        self.assertEqual(note_bomb, "文件太大")
        self.assertLessEqual(resp_bomb.bytes_read, 8192 + 4096)

    # --- 3. Dynamic ffmpeg + no sys.path mutation ---

    def test_ffmpeg_resolved_dynamically_and_soundtrack_never_mutates_sys_path(self):
        with patch.dict(os.environ, {"COMPANION_QZONE_FFMPEG": "ffmpeg"}, clear=False), \
             patch("shutil.which", return_value="/dynamic/bin/ffmpeg") as which_mock:
            self.assertEqual(qzone_media._resolve_ffmpeg(), "/dynamic/bin/ffmpeg")
            which_mock.assert_called_with("ffmpeg")

        before_path = list(sys.path)
        self.assertEqual(qzone_media.video_soundtrack(b"fake-video"), "")
        self.assertEqual(sys.path, before_path)
        source_text = Path(qzone_media.__file__).read_text(encoding="utf-8")
        self.assertNotIn("astrbot_plugin_dafeiyu_video", source_text)
        self.assertNotIn("sys.path.insert", source_text)

    # --- 4. video_frames deduplication & shrink white-background alpha composite ---

    def test_video_frames_deduplicates_short_clips_and_includes_first_frame(self):
        for seconds in (0.3, 1.0, 2.0):
            stamps = qzone_media._sample_timestamps(seconds, 3)
            self.assertEqual(len(stamps), len(set(stamps)), f"duplicate stamps for {seconds}s: {stamps}")
            self.assertEqual(stamps, sorted(stamps))
            self.assertLessEqual(stamps[0], 0.2, f"missing near-first frame for {seconds}s: {stamps}")

        called_stamps = []

        def fake_ffmpeg(*args, timeout=60.0):
            if "-ss" in args:
                called_stamps.append(float(args[args.index("-ss") + 1]))
                target = args[-1]
                Path(target).write_bytes(_png_bytes())
                return True
            return False

        with patch.object(qzone_media, "_resolve_ffmpeg", return_value="/bin/ffmpeg"), \
             patch.object(qzone_media, "_ffmpeg", side_effect=fake_ffmpeg):
            frames, note = qzone_media.video_frames(b"clip", count=3, seconds=1)
        self.assertEqual(note, "")
        self.assertEqual(len(frames), len(set(called_stamps)))
        self.assertLessEqual(called_stamps[0], 0.2)

    def test_shrink_composites_rgba_and_palette_transparency_onto_white_background(self):
        # Fully transparent RGBA image should become white (255, 255, 255), NOT black (0, 0, 0).
        rgba_bytes = _png_bytes(mode="RGBA", color=(0, 0, 0, 0), size=(24, 24))
        b64_rgba = qzone_media.shrink(rgba_bytes)
        self.assertIsNotNone(b64_rgba)
        with Image.open(io.BytesIO(base64.b64decode(b64_rgba))) as decoded:
            r, g, b = decoded.getpixel((12, 12))
            self.assertGreater(r, 240)
            self.assertGreater(g, 240)
            self.assertGreater(b, 240)

        # Paletted P mode image with transparency index should also composite onto white.
        p_img = Image.new("P", (24, 24), 0)
        p_img.putpalette([0, 0, 0, 255, 0, 0] + [0] * (768 - 6))
        p_buf = io.BytesIO()
        p_img.save(p_buf, format="PNG", transparency=0)
        b64_p = qzone_media.shrink(p_buf.getvalue())
        self.assertIsNotNone(b64_p)
        with Image.open(io.BytesIO(base64.b64decode(b64_p))) as decoded_p:
            r, g, b = decoded_p.getpixel((12, 12))
            self.assertGreater(r, 240)
            self.assertGreater(g, 240)
            self.assertGreater(b, 240)

    # --- 5. Bounded concurrency, timeout budget, and LRU cache ---

    async def test_describe_post_bounds_concurrency_enforces_budget_and_uses_lru_cache(self):
        images = tuple(
            QzonePhoto(f"https://cdn.example/img-{i}.png", 20, 20, (f"id-{i}",))
            for i in range(6)
        )
        post = QzonePost(post_id="p-conc", author_uin="1", author_name="me", excerpt="photos", images=images)

        active = 0
        peak = 0
        download_calls = 0
        vision_calls = 0

        def fake_download(url, *, limit, timeout=45.0):
            nonlocal download_calls
            download_calls += 1
            idx = int(url.rsplit("-", 1)[-1].split(".")[0])
            return _png_bytes(color=(idx * 30, 50, 100)), ""

        async def slow_vision(b64_img, prompt):
            nonlocal active, peak, vision_calls
            active += 1
            peak = max(peak, active)
            vision_calls += 1
            try:
                await asyncio.sleep(0.03)
                return f"图片描述-{vision_calls}"
            finally:
                active -= 1

        with patch.object(qzone_media, "download", side_effect=fake_download):
            first = await qzone_media.describe_post_async(
                post, vision_callback=slow_vision, timeout_budget=5.0, max_concurrency=2
            )
            # Second identical post hits URL LRU cache -> zero extra downloads or vision calls!
            second = await qzone_media.describe_post_async(
                post, vision_callback=slow_vision, timeout_budget=5.0, max_concurrency=2
            )

        self.assertEqual(len(first["images"]), 6)
        self.assertTrue(all(item["text"] for item in first["images"]))
        self.assertLessEqual(peak, 2)
        self.assertEqual(download_calls, 6)
        self.assertEqual(vision_calls, 6)
        self.assertEqual(second["images"], first["images"])

        # Tight timeout budget cuts off slow work cleanly.
        qzone_media.clear_media_cache()

        async def very_slow_vision(b64_img, prompt):
            await asyncio.sleep(1.5)
            return "too late"

        with patch.object(qzone_media, "download", side_effect=fake_download):
            timed_out = await qzone_media.describe_post_async(
                post, vision_callback=very_slow_vision, timeout_budget=0.08, max_concurrency=2
            )
        self.assertTrue(any(item["note"] == "媒体解析超时" for item in timed_out["images"]))

        # Verify bounded LRU eviction at maxsize=256.
        cache = qzone_media._BoundedLRUCache(maxsize=256)
        for i in range(300):
            cache.set(f"k-{i}", f"v-{i}")
        self.assertEqual(len(cache), 256)
        self.assertIsNone(cache.get("k-0"))
        self.assertEqual(cache.get("k-299"), "v-299")


class QzoneServiceHardeningTests(QzoneServiceFixture):
    def test_publish_reloads_latest_state_in_transaction_preventing_lost_updates(self):
        """Concurrent auth_block + like/comment quotas written mid-publish must survive."""
        service = self.service
        context = self.context

        class InterleavingTransport(FakeTransport):
            async def send(inner_self, request, *, timeout):
                outcome = await super().send(request, timeout=timeout)
                if request.description == "post.publish":
                    # Simulate concurrent auth failure + like/comment quota writes while publish awaits.
                    current = service.state(context)
                    concurrent = qzone_policy.note_failure(
                        current, reason="concurrent auth blip", source="feed", at=NOW
                    )
                    concurrent = qzone_policy.note_action(concurrent, kind=LIKE, at=NOW)
                    concurrent = qzone_policy.note_action(concurrent, kind=COMMENT, at=NOW)
                    service.store.write(owner_key(context), concurrent, at=NOW)
                return outcome

        self.transport = InterleavingTransport()
        self.service.transport = self.transport

        result = asyncio.run(
            self.service.publish(
                self.context, text="今天把窗台擦干净了", operation_key="lost-update-check", **PUBLISH
            )
        )
        self.assertEqual(result["status"], "done")
        final_state = self.service.state(self.context)
        self.assertEqual(final_state.publish_count, 1)
        self.assertIn("今天把窗台擦干净了", final_state.recent_publish_texts)
        # Concurrent state updates MUST NOT be overwritten!
        self.assertEqual(final_state.auth_failure_count, 1)
        self.assertTrue(final_state.auth_block_until)
        self.assertIn(LIKE, final_state.last_action_at)
        self.assertIn(COMMENT, final_state.last_action_at)
        self.assertIn("publish", final_state.last_action_at)

    def test_scan_detail_respects_media_budget_and_never_fails_scan(self):
        qzone_media.clear_media_cache()
        self.addCleanup(qzone_media.clear_media_cache)
        msglist_with_pic = json.dumps({
            "code": 0,
            "msglist": [{
                "tid": "T-IMG",
                "uin": 123456789,
                "name": "me",
                "content": "带图动态",
                "pic": [{"url3": "https://cdn.example/photo.png", "width": 20, "height": 20}],
            }],
        })
        self.transport.text = msglist_with_pic

        async def slow_cb(b64, prompt):
            await asyncio.sleep(1.0)
            return "慢描述"

        self.service.vision_callback = slow_cb
        with patch.object(qzone_media, "download", return_value=(_png_bytes(), "")):
            scanned = asyncio.run(
                self.service.scan(self.context, scope="own", detail=True, media_budget_seconds=0.05)
            )
        self.assertEqual(scanned["count"], 1)
        item = scanned["items"][0]
        self.assertTrue(
            item.get("media_error") == "媒体解析超时"
            or "媒体解析超时" in (item.get("image_texts") or ())
        )


class QzoneWebAPIHardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / f"pr04-qzone-api-{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = StorageRuntime.open(data_root=self.root / "v2", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.scope = ScopeRef(
            INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a",
            bot_id="bot-a", account_id="account-a", conversation_id="private-a", session_id="private-a",
        )
        self.identities = PersistentIdentityDirectory(self.runtime)
        self.policy = ObjectAccessPolicy(self.runtime)
        target = self._person("target", AuthorizationTier.IMPORTANT)
        self.core_operator = self._person("core-admin", AuthorizationTier.CORE)
        self.standard_operator = self._person("std-user", AuthorizationTier.STANDARD)
        self.important_operator = self._person("imp-user", AuthorizationTier.IMPORTANT)
        self.role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
        personas = PersonaDirectory(self.runtime)
        personas.register_route(ConversationRoute(self.scope, user=target.subject), observed_at=NOW)
        personas.set_binding(
            PersonaBinding(
                "base", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-a"),
                self.role, 0, NOW, "fixture", "admin",
            ),
            expected_revision=0,
            observed_at=NOW,
        )
        self.context = TaskContext(target, RoleBinding(self.scope, self.role, 1), AudienceRef(self.scope, (target.subject,)))
        self.transport = FakeTransport()
        self.service = QzoneService(
            self.runtime, FakeSettings(), self.transport, now=lambda: NOW,
            verify_delays=(0.0,), publish_verify_delays=(0.0,),
        )
        self.application = SimpleNamespace(
            runtime=self.runtime,
            tasks=SimpleNamespace(authorize_context=lambda context, action=None, observed_at=None: Outcome.success(True)),
            qzone=self.service,
        )
        self.controller = SimpleNamespace(
            _control_lock=asyncio.Lock(),
            _terminated=False,
            runtime=self.runtime,
            now=lambda: NOW,
            session=SimpleNamespace(profile=SimpleNamespace(target=self.context), application=self.application),
            last_session=None,
        )
        self.api = QzoneWebAPI(self.controller)

    def _person(self, name: str, tier: AuthorizationTier):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "astrbot-dashboard", "dashboard", name)
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, f"subj-{name}")
        self.identities.bind(
            binding_id=f"bind-{name}", principal=principal, subject=subject,
            valid_from=NOW - timedelta(minutes=1), expected_revision=self.identities.revision,
        )
        self.policy.set_tier(subject, tier, expected_revision=self.policy.revision, observed_at=NOW)
        return self.identities.resolve_authenticated(principal, observed_at=NOW).unwrap()

    @staticmethod
    def _req(*, username="", query=None, body=b"{}"):
        async def _body():
            return body
        return SimpleNamespace(username=username, query=query or {}, body=_body)

    async def test_all_seven_endpoints_and_refresh_require_authenticated_core_operator(self):
        endpoints = [
            ("feed", self._req(query={"scope": "own"})),
            ("like", self._req(body=json.dumps({"post_ref": "r1", "operation_key": "k1"}).encode())),
            ("comment", self._req(body=json.dumps({"post_ref": "r1", "text": "hi", "operation_key": "k2"}).encode())),
            ("publish", self._req(body=json.dumps({"text": "hi", **PUBLISH, "operation_key": "k3"}).encode())),
            ("resolve_suggestion", self._req(body=json.dumps({"suggestion_id": "s1", "state": "dismissed", "operation_key": "k4"}).encode())),
            ("scan_comments", self._req()),
            ("reply_comment", self._req(body=json.dumps({"post_ref": "r1", "post_id": "p1", "text": "ok"}).encode())),
            ("refresh", self._req()),
        ]
        # 1. Unauthenticated & api_key identities must all be rejected before any transport/service call.
        for bad_user in ("", "   ", None, "api_key:service-token"):
            for name, template_req in endpoints:
                req = SimpleNamespace(username=bad_user, query=template_req.query, body=template_req.body)
                with patch("astrbot_host.qzone_api.request", req):
                    res = await getattr(self.api, name)()
                self.assertEqual(res["status"], "error", (name, bad_user, res))
                self.assertEqual(res["message"], "dashboard_identity_required", (name, bad_user, res))
        self.assertEqual(self.transport.acquired, 0)
        self.assertEqual(self.transport.sent, [])

        # 2. Non-CORE operators (STANDARD / IMPORTANT) must be rejected BEFORE refresh touches credentials.
        for non_core in (self.standard_operator, self.important_operator):
            for name, template_req in endpoints:
                req = SimpleNamespace(username="non-core", query=template_req.query, body=template_req.body)
                with patch("astrbot_host.qzone_api.request", req), \
                     patch("astrbot_host.qzone_api.dashboard_operator", return_value=non_core):
                    res = await getattr(self.api, name)()
                self.assertEqual(res["status"], "error", (name, non_core.subject.subject_id, res))
                self.assertEqual(res["message"], "core_access_required", (name, res))
        self.assertEqual(self.transport.acquired, 0)
        self.assertEqual(self.transport.sent, [])

        # 3. CORE operator passes refresh authorization and acquires credentials.
        req = self._req(username="admin")
        with patch("astrbot_host.qzone_api.request", req), \
             patch("astrbot_host.qzone_api.dashboard_operator", return_value=self.core_operator):
            res = await self.api.refresh()
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(self.transport.acquired, 1)

    async def test_run_releases_control_lock_during_remote_io(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_credential(*, refresh=False):
            entered.set()
            await release.wait()
            return qzone_protocol.credential_from_cookies(qzone_protocol.parse_cookie_text(COOKIE))

        self.transport.credential = slow_credential
        req = self._req(username="admin")
        with patch("astrbot_host.qzone_api.request", req), \
             patch("astrbot_host.qzone_api.dashboard_operator", return_value=self.core_operator):
            task = asyncio.create_task(self.api.refresh())
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            # Control lock MUST NOT be held while remote credential refresh awaits.
            self.assertFalse(self.controller._control_lock.locked())
            async with self.controller._control_lock:
                acquired_concurrently = True
            self.assertTrue(acquired_concurrently)
            release.set()
            res = await task
        self.assertEqual(res["status"], "ok", res)

    def test_share_planner_rebuilds_when_runtime_changes(self):
        second_root = self.root / "v2-second"
        runtime_b = StorageRuntime.open(data_root=second_root, installation_id=INSTALLATION)
        self.addCleanup(runtime_b.close)

        planner_a1 = self.api._planner()
        planner_a2 = self.api._planner()
        self.assertIs(planner_a1, planner_a2)

        self.controller.runtime = runtime_b
        planner_b = self.api._planner()
        self.assertIsNot(planner_a1, planner_b)
        self.assertIs(self.api._share_planner_runtime, runtime_b)

    async def test_post_endpoints_handle_malformed_json_without_unhandled_500(self):
        bad_bodies = (b"{not-json", b"[1, 2, 3]", b'{"a":1,"a":2}', b"x" * 20000)
        for endpoint in ("configure", "like", "comment", "publish", "resolve_suggestion", "reply_comment"):
            for payload in bad_bodies:
                if endpoint == "publish" and len(payload) == 20000:
                    continue
                req = self._req(username="admin", body=payload)
                with patch("astrbot_host.qzone_api.request", req), \
                     patch("astrbot_host.qzone_api.dashboard_operator", return_value=self.core_operator):
                    res = await getattr(self.api, endpoint)()
                self.assertEqual(res["status"], "error", (endpoint, payload[:20], res))
                self.assertEqual(res["message"], "qzone_request_invalid", (endpoint, res))


class _FakeGateway(mobile_gateway.MobileGatewayMixin):
    def __init__(self, *, upstreams: dict[str, str] | None = None):
        self._mobile_gateway_init()
        self._upstreams = dict(upstreams or {
            "together": "http://127.0.0.1:6321",
            "game": "http://127.0.0.1:6323",
        })
        self._mobile_config = {
            "enabled": True,
            "pairing_token": "synthetic-long-pairing-token-1234567890",
            "proxy_rooms": True,
            "host": "127.0.0.1",
            "port": 0,
        }

    def _mobile_enabled(self) -> bool:
        return bool(self._mobile_config["enabled"])

    def _mobile_pairing_token(self) -> str:
        return str(self._mobile_config["pairing_token"])

    def _mobile_proxy_rooms_enabled(self) -> bool:
        return bool(self._mobile_config["proxy_rooms"])

    def _mobile_host(self) -> str:
        return str(self._mobile_config["host"])

    def _mobile_port(self) -> int:
        return int(self._mobile_config["port"])

    def _mobile_room_upstream(self, service: str) -> str:
        return self._upstreams.get(service, "")


class MobileGatewayProxyHardeningTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _aio_req(raw_path: str, *, method: str = "GET", headers: dict[str, str] | None = None):
        path_only, _, query = raw_path.partition("?")
        return SimpleNamespace(
            raw_path=raw_path,
            path=path_only,
            query_string=query,
            method=method,
            headers=headers or {},
            can_read_body=False,
            read=AsyncMock(return_value=b""),
        )

    async def test_proxy_blocks_path_traversal_and_disallowed_routes_before_headers(self):
        gw = _FakeGateway()
        gw._mobile_proxy_session = SimpleNamespace(closed=False, request=MagicMock(side_effect=AssertionError("must not call upstream")))
        header_spy = MagicMock(side_effect=AssertionError("must not build proxy headers for rejected path"))

        bad_cases = [
            ("together", "/join/../health", 400),
            ("together", "/join/%2e%2e/health", 400),
            ("together", "/join/%2E%2E%2Fhealth", 400),
            ("together", "/join/tok%2f..%2fhealth", 400),
            ("together", "/join/tok\\..\\health", 400),
            ("together", "/join/tok%5c..%5chealth", 400),
            ("together", "/join/tok%5C..%5Chealth", 400),
            ("together", "/join/tok\x00.png", 400),
            ("together", "/join/tok%00.png", 400),
            ("together", "/join/tok#frag", 400),
            ("together", "/join/tok%23frag", 400),
            ("together", "/join/%252e%252e/health", 400),
            ("together", "//evil.com/join/tok", 400),
            ("together", "/health", 403),
            ("together", "/room/tok123", 403),
            ("game", "/join/tok123", 403),
            ("game", "/avatar", 403),
            ("game", "/health", 403),
            ("unknown", "/join/tok123", 403),
        ]
        with patch.object(gw, "_mobile_room_proxy_headers", header_spy):
            for service, raw_path, expected_status in bad_cases:
                with self.subTest(service=service, raw_path=raw_path):
                    resp = await gw._mobile_room_proxy(self._aio_req(raw_path), service)
                    self.assertEqual(resp.status, expected_status)
        header_spy.assert_not_called()

    async def test_proxy_allows_valid_whitelisted_routes_and_replaces_closed_session(self):
        gw = _FakeGateway()
        gw._mobile_proxy_session = SimpleNamespace(closed=True)
        captured_calls = []

        class FakeUpstreamResp:
            status = 200
            reason = "OK"
            headers = {"Content-Type": "text/plain"}
            content_type = "text/plain"

            class _Content:
                async def iter_chunked(self, size):
                    yield b"ok-body"

            content = _Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class FakeTempSession:
            def __init__(self, *args, **kwargs):
                self.closed = False
                self.close_called = 0

            def request(self, method, target, **kwargs):
                captured_calls.append((method, target, kwargs))
                return FakeUpstreamResp()

            async def close(self):
                self.closed = True
                self.close_called += 1

        created_sessions = []

        def make_session(*args, **kwargs):
            sess = FakeTempSession(*args, **kwargs)
            created_sessions.append(sess)
            return sess

        class FakeStreamResponse:
            def __init__(self, status=200, reason="OK"):
                self.status = status
                self.reason = reason
                self.headers = {}
                self.written = []
                self.eof = False

            async def prepare(self, req):
                return None

            async def write(self, chunk):
                self.written.append(chunk)

            async def write_eof(self):
                self.eof = True

        with patch.object(mobile_gateway.aiohttp, "ClientSession", side_effect=make_session), \
             patch.object(mobile_gateway.web, "StreamResponse", FakeStreamResponse):
            for service, raw_path in (
                ("together", "/join/tok1?mode=call"),
                ("together", "/avatar"),
                ("game", "/api/room/tok1/state"),
            ):
                stream = await gw._mobile_room_proxy(self._aio_req(raw_path), service)
                self.assertEqual(stream.status, 200)
                self.assertEqual(stream.written, [b"ok-body"])
                self.assertTrue(stream.eof)

        self.assertEqual(len(created_sessions), 3)
        self.assertTrue(all(s.closed and s.close_called == 1 for s in created_sessions))
        self.assertEqual(captured_calls[0][1], "http://127.0.0.1:6321/join/tok1?mode=call")
        self.assertEqual(captured_calls[0][2]["headers"]["Origin"], "http://127.0.0.1:6321")
        self.assertFalse(captured_calls[0][2]["allow_redirects"])
        self.assertEqual(captured_calls[2][1], "http://127.0.0.1:6323/api/room/tok1/state")

    async def test_proxy_mid_stream_error_after_prepare_does_not_return_new_502_response(self):
        gw = _FakeGateway()

        class FailingStreamResp:
            status = 200
            reason = "OK"
            headers = {"Content-Type": "application/octet-stream"}
            content_type = "application/octet-stream"

            class _Content:
                async def iter_chunked(self, size):
                    yield b"first-chunk"
                    raise ConnectionResetError("upstream dropped mid-stream")

            content = _Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        gw._mobile_proxy_session = SimpleNamespace(closed=False, request=lambda *a, **kw: FailingStreamResp())

        class TrackingStream:
            def __init__(self, status=200, reason="OK"):
                self.status = status
                self.reason = reason
                self.headers = {}
                self.prepared = False
                self.eof_called = False

            async def prepare(self, req):
                self.prepared = True

            async def write(self, chunk):
                pass

            async def write_eof(self):
                self.eof_called = True

        with patch.object(mobile_gateway.web, "StreamResponse", TrackingStream):
            result = await gw._mobile_room_proxy(self._aio_req("/media/tok123"), "together")

        self.assertIsInstance(result, TrackingStream)
        self.assertTrue(result.prepared)
        self.assertTrue(result.eof_called)

        # Pre-prepare failure still returns 502 Response.
        gw._mobile_proxy_session = SimpleNamespace(
            closed=False, request=MagicMock(side_effect=OSError("connection refused"))
        )
        res_502 = await gw._mobile_room_proxy(self._aio_req("/media/tok123"), "together")
        self.assertEqual(res_502.status, 502)

    async def test_start_mobile_server_closes_and_clears_proxy_session_on_bind_failure(self):
        gw = _FakeGateway()
        fake_session = SimpleNamespace(closed=False, close=AsyncMock())
        fake_runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
        fake_site = SimpleNamespace(start=AsyncMock(side_effect=OSError("port in use")))

        with patch.object(mobile_gateway.aiohttp, "ClientSession", return_value=fake_session), \
             patch.object(mobile_gateway.web, "AppRunner", return_value=fake_runner), \
             patch.object(mobile_gateway.web, "TCPSite", return_value=fake_site):
            started = await gw._start_mobile_server()

        self.assertFalse(started)
        self.assertIsNone(gw._mobile_proxy_session)
        fake_session.close.assert_awaited_once()
        fake_runner.cleanup.assert_awaited_once()

    async def test_ws_proxy_cancels_and_gathers_both_pump_tasks_before_closing_sockets(self):
        gw = _FakeGateway()
        events: list[str] = []
        server_pump_cancelled = False

        class FakeServerWS:
            def __init__(self, *args, **kwargs):
                pass

            async def prepare(self, req):
                return None

            def __aiter__(self):
                return self

            async def __anext__(self):
                nonlocal server_pump_cancelled
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    server_pump_cancelled = True
                    events.append("pump_cancelled")
                    raise
                raise StopAsyncIteration

            async def send_str(self, data):
                pass

            async def send_bytes(self, data):
                pass

            async def close(self):
                events.append("server_ws_closed")

        class FakeUpstreamWS:
            def __aiter__(self):
                return self

            async def __anext__(self):
                # Trigger immediate exception on one pump to verify gather absorbs it without leak.
                raise RuntimeError("upstream ws fault")

            async def send_str(self, data):
                pass

            async def send_bytes(self, data):
                pass

            async def close(self):
                events.append("upstream_ws_closed")

        fake_upstream_ws = FakeUpstreamWS()

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def ws_connect(self, *args, **kwargs):
                return fake_upstream_ws

            async def close(self):
                events.append("session_closed")

        with patch.object(mobile_gateway.web, "WebSocketResponse", FakeServerWS), \
             patch.object(mobile_gateway.aiohttp, "ClientSession", FakeSession):
            await gw._mobile_room_ws_proxy(self._aio_req("/ws?ticket=abc"))

        self.assertTrue(server_pump_cancelled)
        self.assertEqual(
            events,
            ["pump_cancelled", "upstream_ws_closed", "session_closed", "server_ws_closed"],
        )


if __name__ == "__main__":
    unittest.main()
