"""Installed Reply contract and staging lifecycle, using only local API doubles."""

import asyncio
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from tests import test_astrbot_staging_host as sdk_environment
from astrbot.core.message.components import Forward, Image, Plain, Reply
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.media import load_native_image
from astrbot_host.native import installed_native_types
from astrbot_host.quotes import load_native_quote
from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.forward_support import node_raw, quoted_forward_raw
from tests.animation_support import animation_bytes
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_quotes import quote_raw
from tests.test_management_access import NOW


class AstrBotQuoteHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-v2-quote-host-")
        self.addCleanup(self.temp.cleanup)
        self.native = sdk_environment.WebContext()
        loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image)
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
                                        Image, load_native_image, Reply, loader)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(), Path(self.temp.name),
                                                   native_types=self.types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.api = self.native.platform.client._api._wsr_api
        self.raw = quote_raw()
        async def history(action, **params):
            self.assertEqual(action, "get_friend_msg_history")
            self.api.calls.append((action, params))
            return {"messages": [deepcopy(self.raw)]}
        self.api.call_action = history

    async def start_host(self, *, review_only=False):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start(review_only=review_only)).unwrap()

    async def test_installed_reply_with_expanded_forward_reaches_preview(self):
        self.raw = quoted_forward_raw(nodes=[node_raw(nested=[node_raw(images=(animation_bytes(),))])])
        await self.start_host(review_only=True)
        batch = (await self.controller.receive_claimed(self.event())).unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertFalse((await self.controller.preview(batch)).unwrap()["sent"])
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        self.assertEqual(len([p for p in content if p["type"] == "image_url"]), 3)
        materials = json.loads(content[0]["text"])["materials"]
        nodes = [json.loads(m["text"]) for m in materials if m["owner"] == "conversation.forward_node"]
        self.assertEqual([n["node_path"] for n in nodes], [[0], [0, 1, 0]])
        self.assertEqual([(n["forwarded_by"], n["quoted_by"]) for n in nodes], [("100", "200")] * 2)
        self.assertEqual([a for a, _ in self.api.calls], ["get_friend_msg_history"])
        (await self.controller.stop()).unwrap()
        self.assertFalse(self.controller.claims(self.event()))

    async def test_stop_during_quoted_node_image_cannot_publish_late_input(self):
        self.raw = quoted_forward_raw()
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_image(locator)
        session.bridge.quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=held)
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            (await self.controller.stop()).unwrap()
        finally:
            release.set()
        self.assertEqual((await asyncio.wait_for(pending, 2)).failure.code, "host_not_running")
        for table in ("conversation_inputs", "conversation_image_payloads", "sources", "assets"):
            self.assertEqual(session.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_later_text_waits_for_quoted_node_preparation(self):
        self.raw = quoted_forward_raw()
        await self.start_host()
        session = self.controller.session
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(locator):
            entered.set()
            await release.wait()
            return await load_native_image(locator)
        session.bridge.quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=held)
        event = self.event()
        event.message_obj.message_id = "quoted-678"
        first = asyncio.create_task(self.controller.receive_claimed(event))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(self.controller.receive_claimed(FakeEvent(self.native, event_id="later-text")))
            await asyncio.sleep(0)
            self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        finally:
            release.set()
        (await first).unwrap()
        (await second).unwrap()
        rows = session.runtime.read_all("""SELECT json_extract(i.event_json, '$.event_id') id
            FROM conversation_inputs i JOIN conversation_flows f USING(batch_id) ORDER BY f.sequence, i.position""", ())
        self.assertEqual([r["id"] for r in rows], ["quoted-678", "later-text"])

    def event(self):
        event = FakeEvent(self.native)
        event.message_obj.message = [Reply(id="345", chain=[Plain("伪造的引用正文")], sender_id="999", time=1), Plain("看看引用图")]
        return event

    async def test_installed_reply_types_are_wired_to_preview_and_stop(self):
        installed = installed_native_types()
        self.assertIs(installed.reply, Reply)
        self.assertIs(installed.quote_loader, load_native_quote)
        self.assertIs(installed.forward, Forward)
        self.assertTrue(callable(installed.forward_loader))
        self.assertTrue(callable(installed.group_forward_loader))
        await self.start_host(review_only=True)
        event = self.event()
        self.assertTrue(self.controller.claims(event))
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        preview = (await self.controller.preview(batch)).unwrap()
        self.assertFalse(preview["sent"])
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        self.assertNotIn("伪造的引用正文", content[0]["text"])
        self.assertEqual(content[-1]["type"], "image_url")
        self.assertEqual([a for a, _ in self.api.calls], ["get_friend_msg_history"])
        (await self.controller.stop()).unwrap()
        self.assertFalse(self.controller.claims(event))

    async def test_stop_while_reading_quote_cannot_admit_late_input(self):
        await self.start_host()
        session = self.controller.session
        original = session.bridge.quote_loader
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        session.bridge.quote_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        (await self.controller.stop()).unwrap()
        release.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(result.failure.code, "host_not_running")
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_later_text_does_not_overtake_quote_preparation(self):
        await self.start_host()
        session = self.controller.session
        original = session.bridge.quote_loader
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        session.bridge.quote_loader = held
        first = asyncio.create_task(self.controller.receive_claimed(self.event()))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.controller.receive_claimed(FakeEvent(self.native, event_id="next")))
        await asyncio.sleep(0)
        self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        release.set()
        (await first).unwrap()
        (await second).unwrap()
        rows = session.runtime.read_all("""SELECT json_extract(i.event_json, '$.event_id') AS id
            FROM conversation_inputs i JOIN conversation_flows f USING(batch_id) ORDER BY f.sequence, i.position""", ())
        self.assertEqual([r["id"] for r in rows], ["event-1", "next"])
