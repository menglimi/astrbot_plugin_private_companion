"""Installed AstrBot component/payload contracts; model and platform remain local doubles."""

import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

# This helper redirects ASTRBOT_ROOT to a temporary directory before SDK import.
from tests import test_astrbot_staging_host as sdk_environment
from astrbot.core.message.components import Image, Plain
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.media import load_native_image
from astrbot_host.native import installed_native_types
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.model_gateway.models import ModelEnvelope
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_images import image_bytes
from tests.test_management_access import NOW


class AstrBotImageHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-v2-image-host-")
        self.addCleanup(self.temp.cleanup)
        self.native = sdk_environment.WebContext()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi, Image, load_native_image)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(), Path(self.temp.name),
                                                   native_types=self.types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)

    async def start_host(self, *, review_only=False):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start(review_only=review_only)).unwrap()

    def event(self):
        event = FakeEvent(self.native)
        event.message_obj.message = [Plain("看看这张图片"), Image.fromBytes(image_bytes())]
        return event

    async def test_installed_image_type_is_wired_through_staging_preview(self):
        installed = installed_native_types()
        self.assertIs(installed.image, Image)
        self.assertIs(installed.image_loader, load_native_image)
        await self.start_host(review_only=True)
        event = self.event()
        self.assertTrue(self.controller.claims(event))
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        preview = (await self.controller.preview(batch)).unwrap()
        self.assertFalse(preview["sent"])
        self.assertEqual(preview["parts"][0]["text"], "我在这里。")
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        self.assertEqual(content[-1]["type"], "image_url")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        (await self.controller.stop()).unwrap()
        self.assertFalse(self.controller.claims(event))

    async def test_installed_provider_payload_preserves_exact_materialized_images(self):
        await self.start_host()
        batch = (await self.controller.receive_claimed(self.event())).unwrap()
        await wait_for_batch(self.controller.session, batch)
        session = self.controller.session
        request = session.application.messages._request(session.application.messages.batch(batch), session.profile.target, NOW)
        wire = build_wire(ModelEnvelope(request, "本轮原任务", 100, "fixture"))
        # Exercise the installed pure preparation code with no SDK/network client.
        native = object.__new__(ProviderOpenAIOfficial)
        native.provider_config = {"id": "fixture", "type": "openai_chat_completion", "model": "fixture"}
        native.model_name = "fixture"
        native.client = NS(base_url=NS(host="fixture.invalid"))
        native._materialize_context_image_parts = AsyncMock(side_effect=lambda values: deepcopy(values))
        payload, _ = await native._prepare_chat_payload(prompt=None, contexts=wire.messages, model="fixture")
        self.assertEqual(payload["messages"], wire.messages)
        native._materialize_context_image_parts.assert_awaited_once()

    async def test_stop_during_image_loading_cannot_admit_or_send_late_input(self):
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_image(locator)
        session.bridge.image_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        (await self.controller.stop()).unwrap()
        release.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(result.failure.code, "host_not_running")
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_image_payloads", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_later_text_cannot_overtake_pending_image_in_single_target_intake(self):
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_image(locator)
        session.bridge.image_loader = held
        first = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.controller.receive_claimed(FakeEvent(self.native, event_id="next", text="这是刚才那张图片")))
        await asyncio.sleep(0)
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        release.set()
        (await first).unwrap()
        (await second).unwrap()
        rows = session.runtime.read_all("""SELECT json_extract(i.event_json, '$.event_id') AS id
            FROM conversation_inputs i JOIN conversation_flows f USING(batch_id) ORDER BY f.sequence, i.position""", ())
        self.assertEqual([r["id"] for r in rows], ["event-1", "next"])
