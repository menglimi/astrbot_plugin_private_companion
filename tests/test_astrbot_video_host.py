"""Installed AstrBot video component, preprocessed event recovery and lifecycle."""

import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests import test_astrbot_staging_host as sdk_environment
from astrbot.core.message.components import Plain, Video
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.native import installed_native_types
from astrbot_host.video.loader import load_native_video
from astrbot_host.media_components import private_components
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.model_gateway.models import ModelEnvelope
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_management_access import NOW
from tests.video_support import video_bytes


class AstrBotVideoHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-video-host-")
        self.addCleanup(self.temporary.cleanup)
        self.native = sdk_environment.WebContext()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
            video=Video, video_loader=load_native_video, component_reader=private_components)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(),
            Path(self.temporary.name), native_types=self.types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)

    async def start_host(self):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start()).unwrap()

    def event(self, identifier="video-1", *, locator=None, preprocessed=True):
        content = video_bytes()
        locator = locator or "base64://" + base64.b64encode(content).decode("ascii")
        event = FakeEvent(self.native, event_id=identifier)
        # Model the host preprocessing path that may replace the original
        # Video component before the plugin receives the message.
        event.message_obj.message = ([Plain("请概括这段视频")] if preprocessed else
                                     [Plain("请概括这段视频"), Video(file=locator)])
        event.message_obj.raw_message = dict(post_type="message", message_type="private", sub_type="friend",
            self_id=100, user_id=200, sender={"user_id": 200}, message_id=identifier,
            message=[{"type": "text", "data": {"text": "请概括这段视频"}},
                     {"type": "video", "data": {"file": locator}}])
        return event, content

    async def test_installed_video_component_is_recovered_and_sampled_views_reach_preview(self):
        installed = installed_native_types()
        self.assertIs(installed.video, Video)
        self.assertIs(installed.video_loader, load_native_video)
        await self.start_host()
        event, original = self.event()
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        preview = (await self.controller.preview(batch)).unwrap()
        self.assertFalse(preview["sent"])
        self.assertEqual(preview["parts"][0]["text"], "我在这里。")

        payload = self.native.provider.client.state.calls[0][0]
        content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
        images = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(images), 4)
        self.assertTrue(all(part["image_url"]["url"].startswith("data:image/png;base64,") for part in images))
        metadata = [json.loads(part["text"]) for part in content if part["type"] == "text"]
        sampled = [item for item in metadata if "video_evidence" in item]
        self.assertEqual([item["frame_index"] for item in sampled], [0, 3, 7, 11])
        self.assertTrue(all(item["coverage"] == "sampled_frames" and item["audio_status"] == "not_inspected"
                            for item in sampled))
        self.assertNotIn("C:/untrusted.mp4", json.dumps(payload, ensure_ascii=False))
        row = self.controller.session.runtime.read_one("SELECT content FROM conversation_video_payloads", ())
        self.assertEqual(row["content"], original)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_installed_sdk_preparation_preserves_frame_metadata_and_data_urls(self):
        await self.start_host()
        event, _ = self.event("prepare")
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        session = self.controller.session
        request = session.application.messages._request(session.application.messages.batch(batch),
                                                         session.profile.target, NOW)
        wire = build_wire(ModelEnvelope(request, "fixture", 100, "fixture"))
        native = object.__new__(ProviderOpenAIOfficial)
        native.provider_config, native.model_name = {}, "fixture"
        native.client = NS(base_url=NS(host="fixture.invalid"))
        native._materialize_context_image_parts = AsyncMock(side_effect=lambda values: deepcopy(values))
        payload, _ = await native._prepare_chat_payload(prompt=None, contexts=deepcopy(wire.messages), model="fixture")
        self.assertEqual(payload["messages"], wire.messages)
        native._materialize_context_image_parts.assert_awaited_once()

    async def test_raw_route_mismatch_and_stop_during_decode_block_admission(self):
        await self.start_host()
        mismatch, _ = self.event("wrong-route")
        mismatch.message_obj.raw_message["self_id"] = 101
        rejected = await self.controller.receive_claimed(mismatch)
        self.assertEqual(rejected.failure.code, "host_video_scope_mismatch")

        event, _ = self.event("held-decode")
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_video(locator)
        session.bridge.video_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(event))
        await asyncio.wait_for(entered.wait(), 2)
        (await self.controller.stop()).unwrap()
        release.set()
        result = await asyncio.wait_for(pending, 3)
        self.assertEqual(result.failure.code, "host_not_running")
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) FROM conversation_video_payloads", ())[0], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])
