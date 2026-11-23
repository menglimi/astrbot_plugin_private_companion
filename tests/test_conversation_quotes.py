"""Private quoted messages across capture, original governance and reply delivery."""

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from functools import partial
import json
import io
import random
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from PIL import Image

from astrbot_host.media import load_native_image
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.conversation.quotes.models import QuoteConversationEvent
from companion_v2.conversation.quotes.service import quote_reference
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.sources.dependencies import key
from tests.native_adapter_support import FakeEvent, FakeReverseApi, completion, final_text
from tests.test_conversation_images import FakeImage, ImageFixture, image_bytes


class FakeReply:
    type = "Reply"

    def __init__(self, identifier="345"):
        self.id = identifier
        # Neither body, claimed author nor converted SDK time grants authority.
        self.chain = [NS(type="Plain", text="forged quoted body")]
        self.sender_id, self.time = "999", 0


def quote_raw(*, message_id="345", sender="100", text="这是以前发的图片", count=1, content=None):
    parts = [{"type": "text", "data": {"text": text}}] if text else []
    parts += [{"type": "image", "data": {"file": FakeImage(content).file, "url": "https://invalid.example/unused"}}
              for _ in range(count)]
    return {"self_id": 100, "user_id": int(sender), "message_type": "private", "sub_type": "friend",
        "message_id": int(message_id), "sender": {"user_id": int(sender)}, "time": 1700000000, "message": parts}


class QuoteFixture(ImageFixture):
    def setUp(self):
        super().setUp()
        self.raw = quote_raw()
        self.bridge.reply_type = FakeReply
        self.bridge.quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=load_native_image)
        self.api = self.native.platform.client._api._wsr_api
        original = self.api.call_action
        async def dispatch(action, **params):
            if action == "get_friend_msg_history":
                self.api.calls.append((action, params))
                return {"messages": [deepcopy(self.raw)]}
            return await original(action, **params)
        self.api.call_action = dispatch

    def quote_event(self, identifier="quoted-1", *, quote_id="345", with_text=True, direct=False):
        event = FakeEvent(self.native, event_id=identifier)
        event.message_obj.message = [FakeReply(quote_id)]
        if with_text:
            event.message_obj.message.append(NS(type="Plain", text="你记得这张图吗"))
        if direct:
            event.message_obj.message.append(FakeImage(image_bytes("blue")))
        return event


class ConversationQuoteTests(QuoteFixture, unittest.IsolatedAsyncioTestCase):
    async def test_robot_quote_keeps_separate_body_original_time_and_author(self):
        captured = await self.capture(self.quote_event(direct=True))
        self.assertEqual(captured.quotes[0].sender_id, "100")
        self.assertEqual(captured.quotes[0].sent_at, 1700000000)
        self.assertEqual(self.api.calls, [("get_friend_msg_history", {
            "self_id": "100", "user_id": "200", "message_seq": "345", "count": 1,
            "reverse_order": False, "parse_mult_msg": True})])
        host = await self.quiet_host()
        batch = host.receive(captured).unwrap()
        app = host.application
        event, request = self.original(app, batch), self.request(app, batch)
        self.assertIsInstance(event, QuoteConversationEvent)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        self.assertNotIn("以前发", request.context.materials[0].payload)
        materials = [m for m in request.context.materials if m.owner == "conversation.quote"]
        metadata = json.loads(materials[0].payload)
        self.assertEqual((metadata["sender"], metadata["quoted_by"]), ("100", "200"))
        self.assertNotIn("forged", materials[0].payload)
        self.assertIn("不是用户本轮亲口说的话", request.instruction)
        self.assertEqual([base64.b64decode(i.data_base64) for i in request.images], [image_bytes(), image_bytes("blue")])
        self.assertEqual(request.images[0].parent, materials[0].source)
        image_metadata = [json.loads(m.payload) for m in request.context.materials if m.purpose.startswith("quoted_image:")]
        self.assertEqual(image_metadata[0]["sender"], "100")
        journal = app.messages.journal_inputs(self.context(app, batch))
        self.assertNotIn("以前发", journal.items[0].source.payload)

    async def test_peer_history_response_wrappers_are_normalized_for_quotes(self):
        self.raw = quote_raw(count=0, text="以前说过的话")
        for wrapper in (
            {"messages": [self.raw]},
            {"status": "ok", "retcode": 0, "data": {"messages": [self.raw]}},
            {"data": [self.raw]},
            [self.raw],
        ):
            async def dispatch(*_, response=wrapper, **__):
                return response

            self.api.call_action = dispatch
            captured = await self.capture(self.quote_event())
            self.assertEqual(captured.quotes[0].text, "以前说过的话")

        async def ambiguous(*_, **__):
            return {"messages": [self.raw], "data": {"messages": [self.raw]}}

        self.api.call_action = ambiguous
        result = await self.bridge.capture(self.quote_event())
        self.assertEqual(result.failure.code, "host_quote_unavailable")

    async def test_quote_only_text_can_run_with_no_direct_images(self):
        self.raw = quote_raw(count=0, text="以前说过的话")
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.quote_event(with_text=False))).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 1)

    async def test_quoted_image_survives_tool_continuation_and_confirmed_memory_source(self):
        originals = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            originals.append(content[1:])
            evidence = next(m["evidence"] for m in json.loads(content[0]["text"])["materials"]
                            if m["owner"] == "conversation.image")
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="quote-memos", type="function", function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            return completion(final_text("引用图片里是一片红色。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(originals), 2)
        self.assertEqual(originals[0], originals[1])
        event = self.original(app, batch)
        owner = app.memory.writer.access.sent_sources
        reference = tuple(owner.rows(context))[0]
        owner.materialize(context, reference)
        app.withdraw(context, event.scoped_key).unwrap()
        with self.assertRaises(TaskError):
            owner.validate(context, reference)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 1)

    async def test_quote_secret_redaction_retains_replay_conflict(self):
        self.raw = quote_raw(text="password=synthetic-first-secret", count=0)
        host = await self.quiet_host()
        captured = await self.capture(self.quote_event())
        batch = host.receive(captured).unwrap()
        self.raw = quote_raw(text="password=synthetic-second-secret", count=0)
        changed = host.receive(await self.capture(self.quote_event()))
        self.assertEqual(changed.failure.code, "message_event_conflict")
        request = self.request(host.application, batch)
        self.assertIn("[REDACTED]", str(request.context.materials))
        for table, column in (("sources", "payload_json"), ("conversation_inputs", "event_json")):
            stored = " ".join(r[column] for r in host.application.runtime.read_all(f"SELECT {column} FROM {table}", ()))
            self.assertNotIn("synthetic-first-secret", stored)
            self.assertNotIn("synthetic-second-secret", stored)

    async def test_quote_publish_failure_rolls_back_current_input_and_all_attachments(self):
        host = await self.quiet_host()
        captured = await self.capture(self.quote_event(direct=True))
        original = host.application.messages.quotes.publish
        def fail_after(*args):
            original(*args)
            raise TaskError("fixture_failure", "failure after quote publication")
        with patch.object(host.application.messages.quotes, "publish", side_effect=fail_after):
            self.assertEqual(host.receive(captured).failure.code, "fixture_failure")
        for table in ("sources", "source_dependencies", "assets", "conversation_image_payloads", "conversation_inputs", "conversation_batches"):
            self.assertEqual(host.application.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0, table)

    async def test_original_user_input_withdrawal_revokes_quote_and_cannot_be_reimported(self):
        host = await self.quiet_host()
        app = host.application
        original_event = self.image_event("345", with_text=False)
        original_batch = host.receive(await self.capture(original_event)).unwrap()
        original = self.original(app, original_batch)
        self.raw = quote_raw(sender="200", text="")
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        quote = self.original(app, batch, position=2)
        reference = quote_reference(quote.source, quote.quotes[0].position)
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?",
                                    (reference.source_id,))
        self.assertIn(key(original.source), [tuple(r) for r in links])
        app.withdraw(self.context(app, original_batch), original.scoped_key).unwrap()
        self.assertEqual(app.messages.sources.validate_reference(reference, observed_at=self.clock.now()).failure.code, "source_revoked")
        result = host.receive(await self.capture(self.quote_event("reimport")))
        self.assertEqual(result.failure.code, "source_revoked")

    async def test_original_asset_revocation_blocks_existing_quote_read(self):
        host = await self.quiet_host()
        app = host.application
        batch = host.receive(await self.capture(self.image_event("345", with_text=False))).unwrap()
        original = self.original(app, batch)
        self.raw = quote_raw(sender="200", text="")
        host.receive(await self.capture(self.quote_event())).unwrap()
        context = self.context(app, batch)
        event = self.original(app, batch, position=2)
        _, asset = app.messages.images.references(original, original.images[0])
        app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError) as caught:
            app.messages.quotes.read(context, event, self.clock.now())
        self.assertEqual(caught.exception.code, "asset_revoked")

    async def test_quote_of_known_original_after_host_new_session_is_not_reowned(self):
        host = await self.quiet_host()
        host.receive(await self.capture(self.image_event("345", with_text=False))).unwrap()
        self.native.cid = "new-session"
        self.raw = quote_raw(sender="200", text="")
        result = host.receive(await self.capture(self.quote_event()))
        self.assertEqual(result.failure.code, "quote_original_scope_mismatch")

    async def test_quote_of_confirmed_reply_links_its_original_sources(self):
        host = await self.setup_host()
        first = host.receive(await self.capture(FakeEvent(self.native, event_id="111"))).unwrap()
        app = host.application
        await self.done(app, first)
        self.raw = quote_raw(message_id="456", text="我在这里。", count=0)
        second = host.receive(await self.capture(self.quote_event(quote_id="456"))).unwrap()
        quote = self.original(app, second)
        reference = quote_reference(quote.source, quote.quotes[0].position)
        links = app.runtime.read_all("SELECT input_id FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        self.assertTrue(any(r["input_id"].startswith("sent:") for r in links))
        app.withdraw(self.context(app, first), self.original(app, first).scoped_key).unwrap()
        with self.assertRaises(TaskError):
            self.request(app, second)
        await self.done(app, second)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 1)

    async def test_restart_reads_persisted_quote_images_without_native_fetch(self):
        host = await self.quiet_host()
        captured = await self.capture(self.quote_event())
        batch = host.receive(captured).unwrap()
        original = self.request(host.application, batch)
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host"))
        await self.start(new, 1, 1)
        self.assertEqual(self.request(new.application, batch).images, original.images)
        self.assertEqual(new.receive(captured).unwrap(), batch)
        self.assertEqual(len(self.api.calls), 1)

    async def test_missing_or_changed_quote_original_blocks_use(self):
        host = await self.quiet_host()
        app = host.application
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        event = self.original(app, batch)
        reference = quote_reference(event.source, event.quotes[0].position)
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET payload_json = ? WHERE source_id = ?", (json.dumps("changed"), reference.source_id))
        with self.assertRaises(TaskError) as caught:
            self.request(app, batch)
        self.assertEqual(caught.exception.code, "quote_content_changed")

    async def test_combined_image_budget_splits_batches_and_rejects_oversized_input(self):
        self.raw = quote_raw(count=5)
        host = await self.quiet_host()
        one = host.receive(await self.capture(self.quote_event())).unwrap()
        two = host.receive(await self.capture(self.quote_event("quoted-2"))).unwrap()
        self.assertNotEqual(one, two)
        self.raw = quote_raw(count=8)
        result = await self.bridge.capture(self.quote_event("oversized", direct=True))
        self.assertEqual(result.failure.code, "host_image_limit")

    async def test_native_scope_checks_reject_other_id_sender_group_account_or_peer_miss(self):
        for change in ({"message_id": 346}, {"self_id": 101}, {"sender": {"user_id": 999}},
                       {"group_id": 123}, {"message_type": "group"}, {"sub_type": "group"}):
            with self.subTest(change=change):
                self.raw = {**quote_raw(), **change}
                result = await self.bridge.capture(self.quote_event())
                self.assertFalse(result.is_success)
        self.api.call_action = lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []})
        self.assertEqual((await self.bridge.capture(self.quote_event())).failure.code, "host_quote_unavailable")

    async def test_nested_or_unsupported_quote_components_are_not_silently_dropped(self):
        for kind in ("reply", "forward", "record", "video"):
            self.raw = quote_raw()
            self.raw["message"].append({"type": kind, "data": {"id": "345"}})
            result = await self.bridge.capture(self.quote_event())
            self.assertEqual(result.failure.code, "host_forward_unsupported" if kind == "forward" else "host_quote_unsupported")

    async def test_signed_message_id_and_missing_original_time(self):
        self.raw = quote_raw(message_id="-345", count=0)
        self.raw["time"] = 0
        captured = await self.capture(self.quote_event(quote_id="-345"))
        self.assertIsNone(captured.quotes[0].sent_at)
        self.assertEqual(captured.quotes[0].message_id, "-345")

    async def test_conversation_reset_during_quote_preparation_keeps_the_same_role(self):
        original = self.bridge.quote_loader
        async def load(*args, **kwargs):
            quote = await original(*args, **kwargs)
            self.native.cid = "changed"
            return quote
        self.bridge.quote_loader = load
        self.assertTrue((await self.bridge.capture(self.quote_event())).is_success)

    async def test_quote_locator_is_frozen_before_host_await(self):
        event = self.quote_event()
        async def mutate(_):
            event.message_obj.message[0].id = "999"
        self.native.before_resolve = mutate
        captured = await self.capture(event)
        self.assertEqual(captured.quotes[0].message_id, "345")
        self.assertEqual(self.api.calls[0][1]["message_seq"], "345")

    async def test_connection_replacement_while_loading_quote_is_rejected(self):
        async def replace_connection(locator):
            self.api._api_clients["100"] = object()
            return await load_native_image(locator)
        self.bridge.quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi, image_loader=replace_connection)
        self.assertEqual((await self.bridge.capture(self.quote_event())).failure.code, "host_quote_account_changed")

    async def test_quote_history_failure_is_single_attempt_without_global_fallback(self):
        calls = []
        async def fail(action, **params):
            calls.append((action, params))
            raise TimeoutError("fixture history timeout")
        self.api.call_action = fail
        self.assertEqual((await self.bridge.capture(self.quote_event())).failure.code, "host_quote_timeout")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "get_friend_msg_history")

    async def test_malformed_quote_message_numbers_fail_before_history_read(self):
        for identifier in ("--345", "+345", "0345", "0", str(2**63)):
            with self.subTest(identifier=identifier):
                result = await self.bridge.capture(self.quote_event(quote_id=identifier))
                self.assertEqual(result.failure.code, "host_quote_invalid")
        self.assertEqual(self.api.calls, [])

    async def test_large_quoted_base64_preserves_original_without_filename_interpretation(self):
        stream = io.BytesIO()
        Image.frombytes("RGB", (950, 950), random.Random(41).randbytes(950 * 950 * 3)).save(stream, format="PNG")
        content = stream.getvalue()
        self.assertGreater(len(content), 2_500_000)
        self.raw = quote_raw(content=content)
        captured = await self.capture(self.quote_event())
        host = await self.quiet_host()
        batch = host.receive(captured).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual(base64.b64decode(request.images[0].data_base64), content)

    async def test_revoked_quote_image_after_generation_blocks_send(self):
        host = await self.setup_host()
        app = host.application
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        event = self.original(app, batch)
        parent = app.messages.quotes.image_event(event, event.quotes[0])
        _, asset = app.messages.images.references(parent, parent.images[0])
        revoked = False
        async def revoke(_):
            nonlocal revoked
            if self.native.provider.client.state.calls and not revoked:
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        self.native.before_resolve = revoke
        await self.done(app, batch)
        self.assertTrue(revoked)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 0)

    async def test_unknown_quote_model_result_does_not_dispatch_again_on_replay(self):
        async def timeout(*_):
            raise TimeoutError("fixture unknown model result")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.quote_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 0)
