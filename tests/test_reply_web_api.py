"""Reply controls use the authenticated Dashboard and lazy native voice adapters."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_management_access import NOW
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.reply_api import ReplyWebAPI
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.foundation.outcome import Outcome


RECORD = {"type": "record", "data": {"file": "base64://YQ=="}}


async def request(handler, body=None, *, username="reply-admin", raw=None, query=None):
    async def read():
        return raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    with patch("astrbot_host.reply_api.request", NS(username=username, query=query or {}, body=read)):
        return await handler()


class ReplyWebAPITests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_catalog_hot_voice_toggle_and_target_isolation(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        catalog = Mock(return_value=[NS(meta=lambda: NS(id="tts-a", model="s2-pro"),
                                       provider_config={"key": "fixture-secret"})])
        self.native.get_all_tts_providers = catalog
        renderer = AsyncMock(return_value=RECORD)
        with patch("astrbot_host.multi_session.NativeVoiceReply.render", renderer):
            app = await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        replies = {handler.__self__.controller.target_id: route for route, handler in handlers.items()
                   if route.endswith("/reply") and handler.__self__.controller.role_id == "role-a"}
        first, second = replies["first"], replies["second"]
        status = (await request(handlers[first]))["data"]
        self.assertFalse(status["settings"]["voice_enabled"])
        self.assertEqual(status["tts"], {"state": "ready", "providers": [{"id": "tts-a", "model": "s2-pro"}]})
        self.assertNotIn("fixture-secret", json.dumps(status))
        await self.accepted(identifier="voice-initial-off")
        renderer.assert_not_awaited()

        command = {"operation_key": "hot-enable", "edit_token": status["edit_token"],
                   "changes": {"voice_enabled": True, "voice_segment_mode": "last",
                               "voice_delivery_mode": "voice_and_text", "voice_provider_id": "tts-a"}}
        saved = await request(handlers[first + "/configure"], command)
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue((await request(handlers[first + "/configure"], command))["data"]["replayed"])
        self.assertFalse((await request(handlers[second]))["data"]["settings"]["voice_enabled"])
        await self.accepted(identifier="voice-enabled")
        renderer.assert_awaited_once()
        self.assertEqual(renderer.await_args.kwargs["provider_id"], "tts-a")
        sent = [item["type"] for call in self.native.platform.client._api._wsr_api.calls
                for item in call[1].get("message", ())]
        self.assertEqual([kind for kind in sent if kind in {"text", "record"}][-2:], ["text", "record"])
        preview = await request(handlers[first + "/preview"], {"settings": saved["data"]["status"]["settings"],
                                                               "text": "离线正文", "image_count": 2})
        self.assertEqual(preview["status"], "ok", preview)
        self.assertEqual(preview["data"]["components"][0]["success_order"], ["text", "image", "image", "voice"])
        renderer.assert_awaited_once()
        calls = catalog.call_count
        rejected = await request(handlers[first], username="api_key:test")
        self.assertEqual(rejected["data"]["code"], "dashboard_identity_required")
        self.assertEqual(catalog.call_count, calls)
        for raw in (b'{"settings":{},"settings":{},"text":"x"}',
                    b'{"settings":{},"text":"x","target_id":"second"}'):
            self.assertEqual((await request(handlers[first + "/preview"], raw=raw))["data"]["code"],
                             "reply_request_invalid")
        disabled = await request(handlers[first + "/configure"], {
            "operation_key": "hot-disable", "edit_token": saved["data"]["status"]["edit_token"],
            "changes": {"voice_enabled": False}})
        self.assertEqual(disabled["status"], "ok", disabled)
        await self.accepted(identifier="voice-disabled")
        renderer.assert_awaited_once()
        self.assertEqual([item["type"] for item in self.native.platform.client._api._wsr_api.calls[-1][1]["message"]], ["text"])
        self.assertEqual(app.state, "running")
        self.controller.unregister_routes()
        self.assertEqual(self.native.registered_web_apis, [])


    async def test_expression_learning_bounds_share_the_reply_route_and_stay_per_target(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        replies = {handler.__self__.controller.target_id: route for route, handler in handlers.items()
                   if route.endswith("/reply") and handler.__self__.controller.role_id == "role-a"}
        first, second = replies["first"], replies["second"]
        status = (await request(handlers[first + "/expression"]))["data"]
        self.assertEqual(status["group_id"], "expression.learning")
        self.assertEqual(status["apply_mode"], "next_sealed_reply")
        self.assertEqual(status["settings"]["rotation_decay_hours"], 336)
        self.assertFalse(status["settings"]["group_learning_enabled"])
        self.assertEqual(status["settings"]["rules_per_turn"], 2)
        command = {"operation_key": "learn-bounds", "edit_token": status["edit_token"],
                   "changes": {"group_learning_enabled": True, "rotation_decay_hours": 48,
                               "sample_limit": 32, "min_samples": 6}}
        saved = await request(handlers[first + "/expression/configure"], command)
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue((await request(handlers[first + "/expression/configure"], command))["data"]["replayed"])
        after = saved["data"]["status"]["settings"]
        self.assertEqual((after["group_learning_enabled"], after["rotation_decay_hours"]), (True, 48))
        self.assertEqual((after["sample_limit"], after["min_samples"]), (32, 6))
        self.assertEqual((await request(handlers[second + "/expression"]))["data"]["settings"]["rotation_decay_hours"],
                         336)
        invalid = await request(handlers[first + "/expression/configure"],
                                {"operation_key": "learn-bad", "edit_token": saved["data"]["status"]["edit_token"],
                                 "changes": {"sample_limit": 4, "min_samples": 9}})
        self.assertIn(invalid["data"]["code"],
                      ("expression_settings_invalid", "invalid_configuration_value"))
        self.assertEqual((await request(handlers[first + "/expression"], username="api_key:test"))["data"]["code"],
                         "dashboard_identity_required")
        self.controller.unregister_routes()


class SingleReplyWebAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_style_list_runs_off_loop_without_holding_control_lock(self):
        entered, release = threading.Event(), threading.Event()

        def slow_list(**kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("the style list was not released")
            return Outcome.success({"items": []})

        controller = NS(_lifecycle_lock=asyncio.Lock(), _control_lock=asyncio.Lock(),
                        _terminated=False, session=NS(application=object(),
                        profile=NS(target=object())), last_session=None,
                        runtime=object(), now=lambda: NOW)
        api = ReplyWebAPI(controller)
        with patch("astrbot_host.reply_api.dashboard_operator", return_value=object()), patch(
                "astrbot_host.reply_api.ExpressionStyleManagement",
                return_value=NS(list=slow_list)):
            listing = asyncio.create_task(request(api.style))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.assertFalse(listing.done(), "the event loop must stay responsive")
                await asyncio.wait_for(controller._control_lock.acquire(), timeout=1)
                controller._control_lock.release()
            finally:
                release.set()
                result = await listing
        self.assertEqual(result["status"], "ok")

    async def test_single_host_enables_voice_without_reload(self):
        temporary = TemporaryDirectory(prefix="companion-reply-api-")
        self.addCleanup(temporary.cleanup)
        native = support.WebContext()
        config = support.host_config(staging={"profile_id": "reply-voice", "delivery_mode": "onebot_private"})
        types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        controller = AstrBotStagingController(native, config, Path(temporary.name), native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(controller.terminate)
        renderer = AsyncMock(return_value=RECORD)
        with patch("astrbot_host.controller.NativeVoiceReply.render", renderer):
            (await controller.initialize_profile()).unwrap()
            (await controller.authorize_delivery()).unwrap()
            (await controller.start()).unwrap()
        api = ReplyWebAPI(controller)
        status = (await request(api.status))["data"]
        self.assertEqual(status["tts"]["state"], "unavailable")
        saved = await request(api.configure, {"operation_key": "single-enable", "edit_token": status["edit_token"],
                                              "changes": {"voice_enabled": True,
                                                          "voice_delivery_mode": "voice_and_text"}})
        self.assertEqual(saved["status"], "ok", saved)
        batch = (await controller.receive_claimed(FakeEvent(native))).unwrap()
        await wait_for_batch(controller.session, batch)
        renderer.assert_awaited_once()
        sent = [item["type"] for call in native.platform.client._api._wsr_api.calls
                for item in call[1].get("message", ())]
        self.assertEqual([kind for kind in sent if kind in {"text", "record"}], ["text", "record"])
