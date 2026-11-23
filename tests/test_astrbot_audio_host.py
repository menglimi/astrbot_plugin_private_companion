"""Installed SDK voice components, preparation and staging lifecycle contracts."""

import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from tests import test_astrbot_staging_host as sdk_environment
from astrbot.core.message.components import Plain, Record
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.audio.components import private_components
from astrbot_host.audio.loader import load_native_audio
from astrbot_host.native import installed_native_types
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.model_gateway.models import ModelEnvelope
from tests.audio_support import audio_bytes
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_management_access import NOW


class AstrBotAudioHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-audio-host-")
        self.addCleanup(temporary.cleanup)
        self.native = sdk_environment.WebContext()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
            audio=Record, audio_loader=load_native_audio, component_reader=private_components)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(), Path(temporary.name),
            native_types=self.types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)

    async def start_host(self, *, review_only=False):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start(review_only=review_only)).unwrap()

    def event(self, identifier="voice-1", *, transcribed=False):
        event = FakeEvent(self.native, event_id=identifier)
        locator = "base64://" + base64.b64encode(audio_bytes()).decode("ascii")
        event.message_obj.message = [Plain("不可信宿主转写") if transcribed else Record(file=locator, text="非可信转写")]
        event.message_obj.raw_message = dict(post_type="message", message_type="private", sub_type="friend",
            self_id=100, user_id=200, sender={"user_id": 200}, message_id=identifier,
            message=[{"type": "record", "data": {"file": locator}}])
        return event

    async def test_installed_audio_type_and_pre_stt_original_reach_staging_preview(self):
        installed = installed_native_types()
        self.assertIs(installed.audio, Record)
        self.assertIs(installed.audio_loader, load_native_audio)
        self.assertIs(installed.component_reader, private_components)
        await self.start_host(review_only=True)
        batch = (await self.controller.receive_claimed(self.event(transcribed=True))).unwrap()
        await wait_for_batch(self.controller.session, batch)
        preview = (await self.controller.preview(batch)).unwrap()
        self.assertFalse(preview["sent"])
        self.assertEqual(preview["parts"][0]["text"], "我在这里。")
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        self.assertEqual(content[-1]["type"], "input_audio")
        self.assertNotIn("不可信宿主转写", json.dumps(content, ensure_ascii=False))
        self.assertEqual(base64.b64decode(content[-1]["input_audio"]["data"]), audio_bytes())
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        (await self.controller.stop()).unwrap()
        self.assertFalse(self.controller.claims(self.event()))

    async def test_installed_provider_preparation_preserves_exact_audio(self):
        await self.start_host()
        batch = (await self.controller.receive_claimed(self.event())).unwrap()
        await wait_for_batch(self.controller.session, batch)
        session = self.controller.session
        request = session.application.messages._request(session.application.messages.batch(batch), session.profile.target, NOW)
        wire = build_wire(ModelEnvelope(request, "fixture", 100, "fixture"))
        native = object.__new__(ProviderOpenAIOfficial)
        native.provider_config, native.model_name = {}, "fixture"
        native.client = NS(base_url=NS(host="fixture.invalid"))
        # Invoke the installed materializer too: input_audio must not be
        # mistaken for a URL/path requiring download, conversion or logging.
        payload, _ = await native._prepare_chat_payload(prompt=None, contexts=deepcopy(wire.messages), model="fixture")
        self.assertEqual(payload["messages"], wire.messages)

    async def test_raw_route_mismatch_cannot_promote_stt_plain_to_original_audio(self):
        await self.start_host()
        event = self.event(transcribed=True)
        event.message_obj.raw_message["self_id"] = 101
        result = await self.controller.receive_claimed(event)
        self.assertEqual(result.failure.code, "host_audio_scope_mismatch")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_stop_during_audio_loading_prevents_late_admission(self):
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_audio(locator)
        session.bridge.audio_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        (await self.controller.stop()).unwrap()
        release.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(result.failure.code, "host_not_running")
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) FROM conversation_audio_payloads", ())[0], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_later_text_waits_for_audio_capture_and_routes_unregister(self):
        self.controller.register_routes()
        routes = {r[0] for r in self.native.registered_web_apis}
        await self.start_host()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_audio(locator)
        self.controller.session.bridge.audio_loader = held
        first = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.controller.receive_claimed(FakeEvent(self.native, event_id="after")))
        await asyncio.sleep(0)
        release.set()
        (await first).unwrap()
        (await second).unwrap()
        runtime = self.controller.session.runtime
        rows = runtime.read_all("SELECT json_extract(event_json, '$.event_id') FROM conversation_inputs JOIN conversation_flows USING(batch_id) ORDER BY sequence, position", ())
        self.assertEqual([r[0] for r in rows], ["voice-1", "after"])
        await self.controller.terminate()
        self.assertTrue(routes)
        self.assertEqual(self.native.registered_web_apis, [])
