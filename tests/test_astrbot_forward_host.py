"""Installed Forward component contracts and staging lifecycle, using local doubles."""

import asyncio
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import test_astrbot_staging_host as sdk_environment
from aiocqhttp.api_impl import ResultStore, WebSocketReverseApi
from astrbot.core.message.components import Forward, Image, Plain, Reply
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.forwards import load_native_forwards
from astrbot_host.media import load_native_image
from astrbot_host.native import installed_native_types
from companion_v2.adapters.astrbot.forwards import NativeForwardLocator, read_private_forwards
from companion_v2.adapters.astrbot.quotes import NativeQuoteLocator, read_private_quote
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from tests.forward_support import outer_raw
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_quotes import quote_raw
from tests.test_management_access import NOW


class AstrBotForwardHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-v2-forward-host-")
        self.addCleanup(self.temp.cleanup)
        self.native = sdk_environment.WebContext()
        quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image)
        loader = partial(read_private_forwards, reverse_api_type=FakeReverseApi, image_loader=load_native_image)
        types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi,
                                    Image, load_native_image, Reply, quote_loader, Forward, loader)
        self.controller = AstrBotStagingController(self.native, sdk_environment.host_config(), Path(self.temp.name),
                                                   native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.api = self.native.platform.client._api._wsr_api
        async def dispatch(action, **params):
            self.api.calls.append((action, params))
            self.assertEqual(action, "get_friend_msg_history")
            if params.get("message_seq") == "678":
                self.assertEqual(params, {"self_id": "100", "user_id": "200", "message_seq": "678",
                                         "count": 1, "reverse_order": False, "parse_mult_msg": True})
                return {"messages": [outer_raw()]}
            return {"messages": [quote_raw()]}
        self.api.call_action = dispatch

    async def start_host(self, *, review_only=False):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start(review_only=review_only)).unwrap()

    def event(self):
        event = FakeEvent(self.native, event_id="678")
        event.message_obj.message = [Plain("看看记录"), Forward(id="forward-resource"), Reply(id="345")]
        return event

    async def test_installed_reverse_api_unwraps_history_with_a_local_ack_peer(self):
        packets = []
        scope = NS(account_id="100", native_scope_id="200")
        async def acknowledge(text):
            packet = json.loads(text)
            packets.append(packet)
            raw = outer_raw() if packet["params"]["message_seq"] == "678" else quote_raw()
            # Alternate action data shapes; the real SDK removes the envelope.
            payload = {"messages": [raw]} if len(packets) <= 2 else [raw]
            asyncio.get_running_loop().call_soon(ResultStore.add,
                {"status": "ok", "retcode": 0, "data": payload, "echo": packet["echo"]})
        clients = {"100": NS(send=acknowledge), "101": object()}
        client = NS(_wsr_api_clients=clients, _api=NS(_wsr_api=WebSocketReverseApi(clients, set(), 1)))
        options = dict(client=client, scope=scope, reverse_api_type=WebSocketReverseApi, image_loader=load_native_image)
        for _ in range(2):
            forwards = await read_private_forwards((NativeForwardLocator(1, "forward-resource"),), message_id="678", **options)
            quote = await read_private_quote(NativeQuoteLocator(2, "345"), **options)
            self.assertEqual([n.sender_id for n in forwards[0].nodes], ["300", "400"])
            self.assertEqual(quote.sender_id, "100")
        self.assertEqual([p["action"] for p in packets], ["get_friend_msg_history"] * 4)
        self.assertEqual([p["params"]["message_seq"] for p in packets], ["678", "345"] * 2)
        self.assertTrue(all((p["params"]["self_id"], p["params"]["user_id"], p["params"]["count"]) ==
                            ("100", "200", 1) for p in packets))
        self.assertFalse(ResultStore._futures)

    async def test_attachment_deadline_cancels_forward_preparation_without_partial_intake(self):
        await self.start_host()
        session = self.controller.session
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def held(*_, **__):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        session.bridge.forward_loader = held
        with patch("companion_v2.adapters.astrbot.native.MEDIA_PREPARE_TIMEOUT", 0.1):
            result = await asyncio.wait_for(self.controller.receive_claimed(self.event()), 2)
        self.assertEqual(result.failure.code, "host_media_timeout")
        self.assertTrue(entered.is_set())
        self.assertTrue(cancelled.is_set())
        for table in ("conversation_inputs", "conversation_image_payloads", "sources", "assets"):
            self.assertEqual(session.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual([action for action, _ in self.api.calls], ["get_friend_msg_history"])

    async def test_installed_forward_and_reply_share_preview_without_platform_send(self):
        installed = installed_native_types()
        self.assertIs(installed.forward, Forward)
        self.assertIs(installed.forward_loader, load_native_forwards)
        await self.start_host(review_only=True)
        event = self.event()
        self.assertTrue(self.controller.claims(event))
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertFalse((await self.controller.preview(batch)).unwrap()["sent"])
        content = next(m["content"] for m in self.native.provider.client.state.calls[0][0]["messages"]
                       if m["role"] == "user")
        self.assertEqual(len([p for p in content if p["type"] == "image_url"]), 2)
        materials = json.loads(content[0]["text"])["materials"]
        nodes = [json.loads(m["text"]) for m in materials if m["owner"] == "conversation.forward_node"]
        self.assertEqual([n["displayed_sender"] for n in nodes], ["300", "400"])
        self.assertEqual([n["forwarded_by"] for n in nodes], ["200", "200"])
        self.assertEqual([a for a, _ in self.api.calls], ["get_friend_msg_history"] * 2)
        (await self.controller.stop()).unwrap()
        self.assertFalse(self.controller.claims(event))

    async def test_stop_during_forward_read_cannot_admit_late_snapshot(self):
        await self.start_host()
        session = self.controller.session
        original = session.bridge.forward_loader
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        session.bridge.forward_loader = held
        pending = asyncio.create_task(self.controller.receive_claimed(self.event()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            (await self.controller.stop()).unwrap()
        finally:
            release.set()
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(result.failure.code, "host_not_running")
        for table in ("conversation_inputs", "conversation_image_payloads", "sources"):
            self.assertEqual(session.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_later_text_cannot_overtake_pending_forward_read(self):
        await self.start_host()
        session = self.controller.session
        original = session.bridge.forward_loader
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        session.bridge.forward_loader = held
        first = asyncio.create_task(self.controller.receive_claimed(self.event()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(self.controller.receive_claimed(FakeEvent(self.native, event_id="next")))
            await asyncio.sleep(0)
            self.assertEqual(session.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        finally:
            release.set()
        (await first).unwrap()
        (await second).unwrap()
        rows = session.runtime.read_all("""SELECT json_extract(i.event_json, '$.event_id') id
            FROM conversation_inputs i JOIN conversation_flows f USING(batch_id) ORDER BY f.sequence, i.position""", ())
        self.assertEqual([r["id"] for r in rows], ["678", "next"])
