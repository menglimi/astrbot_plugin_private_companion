"""Installed animation/Reply components and provider preparation, without network IO."""

import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests import test_astrbot_staging_host as sdk_environment
from astrbot.core.message.components import Image, Plain, Reply
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.media import load_native_image
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.model_gateway.models import ModelEnvelope
from tests.animation_support import animation_bytes
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_quotes import quote_raw
from tests.test_management_access import NOW


class AstrBotAnimationHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-v2-animation-host-")
        self.addCleanup(self.temp.cleanup)
        self.native = sdk_environment.WebContext()
        loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image)
        types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
                                   Image, load_native_image, Reply, loader)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(), Path(self.temp.name),
                                                   native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)

    async def start_host(self):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start()).unwrap()

    def event(self, kind="GIF", *, count=3):
        event = FakeEvent(self.native, event_id="animation-" + kind)
        event.message_obj.message = [Plain("看看这个动画"), Image.fromBytes(animation_bytes(kind, count=count))]
        return event

    async def test_installed_image_components_expand_three_animation_formats_in_delivery(self):
        await self.start_host()
        for kind in ("GIF", "PNG", "WEBP"):
            with self.subTest(kind=kind):
                event = self.event(kind)
                self.assertTrue(self.controller.claims(event))
                batch = (await self.controller.receive_claimed(event)).unwrap()
                await wait_for_batch(self.controller.session, batch)
                report = (await self.controller.inspect(batch)).unwrap()
                self.assertTrue(report["confirmed_delivery"])
                self.assertEqual(report["delivery_receipts"][0]["state"], "delivered")
                content = next(m["content"] for m in self.native.provider.client.state.calls[-1][0]["messages"]
                               if m["role"] == "user")
                urls = [p["image_url"]["url"] for p in content if p["type"] == "image_url"]
                self.assertEqual(len(urls), 3)
                self.assertTrue(all(url.startswith("data:image/png;base64,") for url in urls))
        self.assertEqual([action for action, _ in self.native.platform.client._api._wsr_api.calls],
                         ["send_private_msg"] * 3)

    async def test_installed_provider_keeps_sampled_frames_and_coverage_metadata(self):
        await self.start_host()
        batch = (await self.controller.receive_claimed(self.event(count=10))).unwrap()
        await wait_for_batch(self.controller.session, batch)
        session = self.controller.session
        request = session.application.messages._request(session.application.messages.batch(batch), session.profile.target, NOW)
        wire = build_wire(ModelEnvelope(request, "本轮原任务", 100, "fixture"))
        self.assertEqual(wire.image_count, 4)
        native = object.__new__(ProviderOpenAIOfficial)
        native.provider_config = {"id": "fixture", "type": "openai_chat_completion", "model": "fixture"}
        native.model_name = "fixture"
        native.client = NS(base_url=NS(host="fixture.invalid"))
        native._materialize_context_image_parts = AsyncMock(side_effect=lambda values: deepcopy(values))
        payload, _ = await native._prepare_chat_payload(prompt=None, contexts=wire.messages, model="fixture")
        self.assertEqual(payload["messages"], wire.messages)
        native._materialize_context_image_parts.assert_awaited_once()

    async def test_installed_reply_preserves_original_sender_for_animation_frames(self):
        api = self.native.platform.client._api._wsr_api
        async def history(action, **params):
            self.assertEqual(action, "get_friend_msg_history")
            self.assertEqual(params["user_id"], "200")
            api.calls.append((action, params))
            return {"messages": [quote_raw(content=animation_bytes())]}
        api.call_action = history
        await self.start_host()
        event = FakeEvent(self.native)
        event.message_obj.message = [Reply(id="345", chain=[Plain("伪造正文")], sender_id="999", time=1), Plain("解释这段动画")]
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        materials = json.loads(content[0]["text"])["materials"]
        frames = [json.loads(m["text"]) for m in materials if m["purpose"].startswith("quoted_image:frame:")]
        self.assertEqual([frame["sender"] for frame in frames], ["100"] * 3)
        self.assertEqual([frame["quoted_by"] for frame in frames], ["200"] * 3)
        self.assertNotIn("伪造正文", content[0]["text"])
        self.assertEqual([action for action, _ in api.calls], ["get_friend_msg_history"])

    async def test_stop_during_animation_preparation_cannot_persist_or_dispatch(self):
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_image(locator)
        session.bridge.image_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            (await self.controller.stop()).unwrap()
        finally:
            release.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(result.failure.code, "host_not_running")
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_image_payloads", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])
