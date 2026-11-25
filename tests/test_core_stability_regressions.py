"""Regression tests for startup assembly, failure chain logging, style learning and tunnel search."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from starlette.requests import Request
from astrbot.api.web import PluginRequest, bind_request_context

from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.native_extensions.together.runtime import TogetherRuntime
from astrbot_host.native_extensions.together.tunnel import CloudflareQuickTunnel
from astrbot_host.reply_api import PREFIX as REPLY_PREFIX, ReplyWebAPI
from companion_v2.conversation.media.video.narration import KIND as VIDEO_NARRATION_KIND
from companion_v2.expression.style_service import KIND as EXPRESSION_LEARN_KIND, ExpressionStyleService
from companion_v2.model_gateway.output_defaults import with_thinking_margins
from tests.multi_host_support import MultiFixture
from tests.test_management_access import NOW


def _fake_model_entry(provider_id: str, *, capabilities=("text",), supports_video_relay: bool = False):
    local = NS(capabilities=frozenset(capabilities), supports_video_relay=supports_video_relay)
    return NS(binding=NS(provider=provider_id), local_provider=lambda: local)


class VideoProfileAssemblyTests(unittest.TestCase):
    def _resolve_video_provider(self, models):
        local_providers = tuple((m.binding.provider, m.local_provider()) for m in models)
        return (
            next((pid for pid, lp in local_providers if "video" in lp.capabilities), None)
            or next((pid for pid, lp in local_providers if lp.supports_video_relay), None)
        )

    def test_video_provider_resolution_handles_gemini_only_relay_only_both_and_neither(self):
        gemini_only = (_fake_model_entry("gemini-primary", capabilities=("text", "image", "video"), supports_video_relay=False),)
        relay_only = (_fake_model_entry("openai-vision", capabilities=("text", "image"), supports_video_relay=True),)
        both = (
            _fake_model_entry("openai-vision", capabilities=("text", "image"), supports_video_relay=True),
            _fake_model_entry("gemini-native", capabilities=("text", "video"), supports_video_relay=False),
        )
        neither = (_fake_model_entry("text-only", capabilities=("text",), supports_video_relay=False),)

        self.assertEqual(self._resolve_video_provider(gemini_only), "gemini-primary")
        self.assertEqual(self._resolve_video_provider(relay_only), "openai-vision")
        self.assertEqual(self._resolve_video_provider(both), "gemini-native")
        self.assertIsNone(self._resolve_video_provider(neither))

    def test_with_thinking_margins_rejects_non_profile_with_clear_type_error(self):
        with self.assertRaises(TypeError) as caught:
            with_thinking_margins((("not-a-profile", "secret-token"),))
        message = str(caught.exception)
        self.assertIn("index 0", message)
        self.assertIn("tuple", message)
        self.assertNotIn("secret-token", message)


class MultiSessionGeminiStartupTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await self.controller.terminate()

    async def test_multi_host_assemble_succeeds_with_native_video_only_provider(self):
        """Reproduces PEP 479 StopIteration->RuntimeError when the sole model has video capability without relay."""
        def fake_attach_native_video(_ctx, *, bridge, native_types, text, settings):
            orig_local = text.local_provider

            def native_video_local():
                lp = orig_local()
                object.__setattr__(lp, "capabilities", frozenset(lp.capabilities) | {"video"})
                object.__setattr__(lp, "supports_video_relay", False)
                return lp

            text.local_provider = native_video_local
            return text

        with patch("astrbot_host.multi_session.attach_native_video", side_effect=fake_attach_native_video):
            result = await self.controller.initialize_profile()
        self.assertTrue(result.is_success, getattr(result, "failure", None))
        profiles = self.controller.session.application.gateway.router.profiles
        self.assertIn(VIDEO_NARRATION_KIND, profiles)
        self.assertEqual(profiles[VIDEO_NARRATION_KIND].default_provider, self.controller.session.model.binding.provider)

    async def test_failure_logs_pep479_cause_chain_without_leaking_exception_values(self):
        secret_value = "secret-stop-value"

        async def broken_assemble(_probed):
            def inner_helper():
                raise StopIteration(secret_value)
            inner_helper()

        with patch.object(self.controller, "_assemble", side_effect=broken_assemble):
            with self.assertLogs("astrbot_host.multi_controller", level="ERROR") as captured:
                result = await self.controller.initialize_profile()
        self.assertEqual(result.failure.code, "host_control_failed")
        diagnostic = "\n".join(captured.output)
        self.assertIn("error_type=RuntimeError", diagnostic)
        self.assertIn("cause_chain=StopIteration", diagnostic)
        self.assertIn("[cause #1] StopIteration", diagnostic)
        self.assertIn("inner_helper", diagnostic)
        self.assertIn("cause=StopIteration", result.failure.reason)
        self.assertIn("inner_helper", result.failure.reason)
        self.assertNotIn(secret_value, diagnostic + result.failure.reason)


class StyleLearnEventLoopRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_style_learn_prepares_off_loop_and_spawns_task_on_running_loop(self):
        """Ensures POST /reply/style/learn never raises RuntimeError: no running event loop."""
        ran = asyncio.Event()

        class DummyStyleService(ExpressionStyleService):
            def __init__(self):
                self.closed = False
                self.workers = {}
                self._worker_lock = None
                self.gateway = NS(router=NS(profiles={EXPRESSION_LEARN_KIND: object()}))
                self.tasks = NS(now=lambda: NOW)
                self.app = NS(notify=lambda: None)
                self.prepare_thread_id = None

            def _lock(self):
                import threading
                if self._worker_lock is None:
                    self._worker_lock = threading.RLock()
                return self._worker_lock

            def _work_key(self, context):
                return context.key

            def prepare(self, context, instruction, at):
                import threading
                self.prepare_thread_id = threading.get_ident()
                return {"material_hash": "hash-1", "policy": NS(learning_mode="auto")}

            def _stopped_code(self, request_key):
                return None

            async def run(self, context, instruction):
                ran.set()

        service = DummyStyleService()
        context = NS(key="ctx-1")
        app = NS(
            expression_style=service,
            expression_samples=NS(),
            role_growth=NS(runtime=NS(), ledger=NS(now=lambda: NOW), tasks=NS(now=lambda: NOW)),
            state_maintenance=NS(bindings={"ctx-1": (context, "role instruction")}),
            runtime=NS(read_snapshot=lambda: _NullCtx()),
        )
        controller = NS(
            _lifecycle_lock=asyncio.Lock(),
            _control_lock=asyncio.Lock(),
            _terminated=False,
            session=NS(application=app, profile=NS(target=context)),
            last_session=None,
            runtime=app.runtime,
            now=lambda: NOW,
        )
        api = ReplyWebAPI(controller)

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        req = Request(
            {"type": "http", "method": "POST", "path": REPLY_PREFIX + "/style/learn", "headers": [], "query_string": b""},
            receive=receive,
        )
        import threading
        main_thread_id = threading.get_ident()
        with patch("astrbot_host.reply_api.dashboard_operator", return_value=object()), patch(
            "companion_v2.expression.style_management.RoleGrowthManagement",
            return_value=NS(ledger=NS(now=lambda: NOW)),
        ), patch(
            "companion_v2.expression.style_management.ExpressionStyleManagement._authorize",
            return_value=(object(), 1),
        ):
            with bind_request_context(PluginRequest(req, username="admin")):
                response = await api.style_learn()

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"], {"state": "queued"})
        self.assertIsNotNone(service.prepare_thread_id)
        self.assertNotEqual(service.prepare_thread_id, main_thread_id)
        await asyncio.wait_for(ran.wait(), timeout=2.0)


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TogetherCloudflaredSearchTests(unittest.TestCase):
    def test_cloudflared_search_paths_include_astrbot_data_tools_bin(self):
        with TemporaryDirectory(prefix="companion-together-tunnel-") as root:
            astrbot_data = Path(root) / "data"
            plugin_together_dir = astrbot_data / "plugin_data" / "astrbot_plugin_private_companion" / "together"
            plugin_together_dir.mkdir(parents=True)
            tools_bin = astrbot_data / "tools" / "bin"
            tools_bin.mkdir(parents=True)
            binary_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
            expected_bin = tools_bin / binary_name
            expected_bin.write_bytes(b"stub")

            fake_runtime = NS(data_dir=plugin_together_dir, plugin_root=Path(root) / "plugin")
            with patch("astrbot.core.utils.astrbot_path.get_astrbot_data_path", return_value=str(astrbot_data)):
                paths = TogetherRuntime._cloudflared_search_paths(fake_runtime)
            self.assertIn(tools_bin, paths)
            with patch("astrbot_host.native_extensions.together.tunnel.shutil.which", return_value=None):
                tunnel = CloudflareQuickTunnel(local_url="http://127.0.0.1:6321", search_paths=paths)
                self.assertEqual(tunnel.binary_path(), expected_bin)

            # Fallback when get_astrbot_data_path fails: still resolves via plugin_data ancestor.
            with patch("astrbot.core.utils.astrbot_path.get_astrbot_data_path", side_effect=RuntimeError("no sdk")):
                fallback_paths = TogetherRuntime._cloudflared_search_paths(fake_runtime)
            self.assertIn(tools_bin, fallback_paths)
            with patch("astrbot_host.native_extensions.together.tunnel.shutil.which", return_value=None):
                tunnel_fb = CloudflareQuickTunnel(local_url="http://127.0.0.1:6321", search_paths=fallback_paths)
                self.assertEqual(tunnel_fb.binary_path(), expected_bin)


if __name__ == "__main__":
    unittest.main()
