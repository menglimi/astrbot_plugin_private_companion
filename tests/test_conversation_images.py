"""Real local message/model/delivery loops using captured static-image fixtures."""

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta
import io
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from PIL import Image

from astrbot_host.media import inspect_image, load_native_image
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.adapters.astrbot.native import NativeImageLocator
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.messages import event_from_document
from companion_v2.conversation.media.models import ImageConversationEvent
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.model_gateway.models import Amount, BudgetPolicy
from companion_v2.sources.dependencies import key
from tests.native_adapter_support import NativeFixture, FakeEvent, completion, final_text
from tests.image_delivery_support import (attach_originals, intercept_image_delivery_verification,
                                          user_content, user_message)


def image_bytes(color="red", kind="PNG", size=(3, 2)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=kind)
    return stream.getvalue()


class FakeImage:
    type = "Image"

    def __init__(self, content=None):
        self.file = "base64://" + base64.b64encode(content or image_bytes()).decode("ascii")
        self.url = ""


class ImageFixture(NativeFixture):
    def setUp(self):
        super().setUp()
        self.bridge.image_type = FakeImage
        self.bridge.image_loader = load_native_image
        self.bridge.now = self.clock.now

    async def setup_host(self, **changes):
        options = dict(event_port=self.bridge.ports, identity_port=self.bridge.ports, role_port=self.bridge.ports,
            providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            budget=BudgetPolicy(*(Amount(10**8, 10**9) for _ in range(5))), policy=ConversationPolicy(0, 0, 180, 0))
        options.update(changes)
        return await self.start(self.host(**options))

    def image_event(self, identifier="image-1", content=None, *, count=1, with_text=True):
        event = FakeEvent(self.native, event_id=identifier)
        event.message_obj.message = ([NS(type="Plain", text="看看这张图片：")] if with_text else [])
        event.message_obj.message += [FakeImage(content) for _ in range(count)]
        return event

    async def quiet_host(self, **options):
        return await self.setup_host(policy=ConversationPolicy(2, 3, 180, 0), **options)

    def original(self, app, batch, *, position=1):
        row = app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id = ? AND position = ?", (batch, position))
        return event_from_document(json.loads(row["event_json"]), self.context(app, batch))

    def request(self, app, batch):
        return app.messages._request(app.messages.batch(batch), self.context(app, batch), self.clock.now(), include_tools=False)


class ConversationImageTests(ImageFixture, unittest.IsolatedAsyncioTestCase):
    async def test_image_only_intake_model_evidence_and_confirmed_reply(self):
        captured = await self.capture(self.image_event(with_text=False))
        host = await self.setup_host()
        def image_evidence(payload):
            content = user_content(payload)
            metadata = json.loads(content[0]["text"])
            return next(m["evidence"] for m in metadata["materials"] if m["owner"] == "conversation.image")
        async def respond(payload, _):
            content = user_content(payload)
            self.assertTrue(content[-1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(content[-1]["image_url"]["url"].split(",", 1)[1]), image_bytes())
            return completion(final_text("图片中是一片红色。", evidence=image_evidence(payload)))
        self.native.provider.client.state.behavior = respond
        batch = host.receive(captured).unwrap()
        self.assertEqual(host.receive(captured).unwrap(), batch)
        app = host.application
        self.assertIsInstance(self.original(app, batch), ImageConversationEvent)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)
        self.assertEqual(app.messages.history(self.context(app, batch)).unwrap()["items"][0]["text"], "图片中是一片红色。")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM conversation_image_payloads", ())["n"], 1)
        encoded = captured.images[0].content
        self.assertEqual(app.runtime.read_one("SELECT content FROM conversation_image_payloads", ())["content"], encoded)
        text_sources = " ".join(row["payload_json"] for row in app.runtime.read_all("SELECT payload_json FROM sources", ()))
        self.assertNotIn(base64.b64encode(encoded).decode("ascii"), text_sources)

    async def test_tool_continuation_keeps_original_images_and_evidence_until_confirmed_reply(self):
        originals, evidence = [], []
        async def respond(payload, _):
            content = user_content(payload)
            originals.append(content[1:])
            materials = json.loads(content[0]["text"])["materials"]
            evidence.append([m for m in materials if m["owner"] == "conversation.image"])
            results = [m for m in payload["messages"] if m["role"] == "tool"]
            if not results:
                return completion(tools=[NS(id="image-read-memos", type="function",
                    function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["tool_call_id"], "image-read-memos")
            self.assertIn("succeeded", results[0]["content"])
            return completion(final_text("图片中是一片红色。", evidence=evidence[-1][0]["evidence"]))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(originals), 2)
        self.assertEqual(originals[0], originals[1])
        self.assertEqual(evidence[0], evidence[1])
        self.assertEqual(base64.b64decode(originals[1][-1]["image_url"]["url"].split(",", 1)[1]), image_bytes())
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)
        context = self.context(host.application, batch)
        self.assertEqual(host.application.messages.history(context).unwrap()["items"][0]["text"], "图片中是一片红色。")

    async def test_confirmed_memory_quote_keeps_image_dependencies_and_rechecks_original_access(self):
        async def respond(payload, _):
            materials = json.loads(user_content(payload)[0]["text"])["materials"]
            evidence = next(m["evidence"] for m in materials if m["owner"] == "conversation.image")
            return completion(final_text("图片中是一片红色。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        original = self.original(app, batch)
        image, asset = app.messages.images.references(original, original.images[0])
        quotes = app.memory.writer.access.sent_sources
        references = tuple(quotes.rows(context))
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual(quotes.materialize(context, reference).payload, "图片中是一片红色。")
        dependencies = app.runtime.read_all("""SELECT input_id, input_lineage, input_revision
            FROM source_dependencies WHERE dependent_id = ? AND dependent_lineage = ? AND dependent_revision = ?""",
            key(reference))
        self.assertIn(key(image), [tuple(row) for row in dependencies])
        app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError) as caught:
            quotes.validate(context, reference)
        self.assertEqual(caught.exception.code, "asset_revoked")
        app.withdraw(context, original.scoped_key).unwrap()
        self.assertEqual(app.messages.sources.validate_reference(reference, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM conversation_sent_history", ())["n"], 1)

    async def test_text_and_images_keep_component_and_merged_message_order(self):
        first = self.image_event("image-a")
        first.message_obj.message += [NS(type="Plain", text="和另一张："), FakeImage(image_bytes("blue"))]
        host = await self.quiet_host()
        batch = host.receive(await self.capture(first)).unwrap()
        second = host.receive(await self.capture(self.image_event("image-b", image_bytes("green")))).unwrap()
        self.assertEqual(batch, second)
        request = self.request(host.application, batch)
        self.assertEqual([i.descriptor.position for i in request.images], [1, 3, 1])
        self.assertEqual([base64.b64decode(i.data_base64) for i in request.images],
                         [image_bytes(), image_bytes("blue"), image_bytes("green")])
        self.assertEqual(len({i.source for i in request.images}), 3)
        self.assertIn("和另一张：", request.context.materials[0].payload)
        self.assertIn("图片 2", request.context.materials[0].payload)

    async def test_turn_image_budget_starts_another_batch(self):
        host = await self.quiet_host()
        a = host.receive(await self.capture(self.image_event("full", count=8))).unwrap()
        b = host.receive(await self.capture(self.image_event("next"))).unwrap()
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.request(host.application, a).images), 8)
        self.assertEqual(len(self.request(host.application, b).images), 1)

    async def test_image_content_change_on_duplicate_does_not_replace_original(self):
        host = await self.quiet_host()
        first = await self.capture(self.image_event())
        batch = host.receive(first).unwrap()
        changed = host.receive(await self.capture(self.image_event(content=image_bytes("blue"))))
        self.assertEqual(changed.failure.code, "message_event_conflict")
        self.assertEqual(base64.b64decode(self.request(host.application, batch).images[0].data_base64), image_bytes())

    async def test_image_publication_failure_rolls_back_entire_intake(self):
        host = await self.quiet_host()
        original = host.application.messages.images.publish
        def fail_after(*args):
            original(*args)
            raise TaskError("fixture_failure", "rollback after publishing image bytes")
        captured = await self.capture(self.image_event())
        with patch.object(host.application.messages.images, "publish", side_effect=fail_after):
            self.assertEqual(host.receive(captured).failure.code, "fixture_failure")
        for table in ("conversation_inputs", "conversation_batches", "conversation_image_payloads", "sources", "assets"):
            self.assertEqual(host.application.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0, table)

    async def test_withdrawn_input_revokes_image_evidence_and_rejects_replay(self):
        host = await self.quiet_host()
        captured = await self.capture(self.image_event())
        batch = host.receive(captured).unwrap()
        app, context = host.application, self.context(host.application, batch)
        original = self.original(app, batch)
        reference = self.request(app, batch).images[0].source
        app.withdraw(context, original.scoped_key).unwrap()
        self.assertEqual(app.messages.sources.validate_reference(reference, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertEqual(host.receive(captured).failure.code, "message_withdrawn")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_withdrawal_during_native_preparation_prevents_model_dispatch(self):
        captured = await self.capture(self.image_event())
        entered, release = asyncio.Event(), asyncio.Event()
        provider = self.native.provider
        prepare = provider._prepare_chat_payload
        async def wait(**kwargs):
            entered.set()
            await release.wait()
            return await prepare(**kwargs)
        host = await self.setup_host()
        with patch.object(provider, "_prepare_chat_payload", wait):
            batch = host.receive(captured).unwrap()
            await asyncio.wait_for(entered.wait(), 2)
            app = host.application
            app.withdraw(self.context(app, batch), self.original(app, batch).scoped_key).unwrap()
            release.set()
            await self.done(app, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_revoked_asset_or_missing_or_corrupted_body_blocks_original_request(self):
        host = await self.quiet_host()
        app = host.application
        for index, reason in enumerate(("asset_revoked", "image_original_unavailable", "image_content_changed")):
            batch = host.receive(await self.capture(self.image_event("variant-" + str(index)))).unwrap()
            original = self.original(app, batch)
            if reason == "asset_revoked":
                _, asset = app.messages.images.references(original, original.images[0])
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            else:
                with app.runtime.transaction() as connection:
                    if index == 1:
                        connection.execute("DELETE FROM conversation_image_payloads WHERE parent_id = ?", (original.source.source_id,))
                    else:
                        connection.execute("UPDATE conversation_image_payloads SET content = ? WHERE parent_id = ?", (b"corrupt", original.source.source_id))
            with self.assertRaises(TaskError) as caught:
                self.request(app, batch)
            self.assertEqual(caught.exception.code, reason)
            app.cancel(self.context(app, batch), batch).unwrap()

    async def test_collecting_images_survive_restart_without_refetch(self):
        host = await self.quiet_host()
        captured = await self.capture(self.image_event())
        batch = host.receive(captured).unwrap()
        request = self.request(host.application, batch)
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host"))
        await self.start(new, 1, 1)
        self.assertEqual(self.request(new.application, batch).images, request.images)
        self.assertEqual(new.receive(captured).unwrap(), batch)

    async def test_text_only_provider_cannot_silently_drop_images(self):
        provider = replace(self.native_model.local_provider(), capabilities=("text", "tools"))
        host = await self.setup_host(providers=(provider,))
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])
        sends = self.native.platform.client._api._wsr_api.calls
        self.assertEqual(len(sends), 1)
        self.assertEqual([part["type"] for part in sends[0][1]["message"]], ["text"])

    async def test_native_preparer_cannot_drop_or_replace_images(self):
        host = await self.setup_host()
        original = type(self.native.provider)._prepare_chat_payload
        async def drop_images(native, **kwargs):
            payload, contexts = await original(native, **kwargs)
            user_message(payload)["content"] = "text only"
            return payload, contexts
        with patch.object(type(self.native.provider), "_prepare_chat_payload", drop_images):
            batch = host.receive(await self.capture(self.image_event())).unwrap()
            await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_capture_freezes_locator_and_rechecks_host_after_image_await(self):
        event = self.image_event()
        observed = []
        async def loader(locator):
            observed.append(locator)
            event.message_obj.message[-1].file = "file:///C:/private.txt"
            return inspect_image(image_bytes(), locator.position)
        self.bridge.image_loader = loader
        captured = await self.capture(event)
        self.assertEqual(captured.images[0].content, image_bytes())
        self.assertTrue(observed[0].locator.startswith("base64://"))
        async def switch_role(locator):
            self.native.cid = "replaced-conversation"
            return inspect_image(image_bytes(), locator.position)
        self.bridge.image_loader = switch_role
        self.assertTrue((await self.bridge.capture(self.image_event("changed"))).is_success)

    async def test_loading_images_does_not_hold_a_storage_transaction(self):
        async def loader(locator):
            other = self.open_runtime()
            with other.transaction() as connection:
                connection.execute("INSERT INTO runtime_metadata VALUES ('image-loading-proof', 'ok')")
            return inspect_image(image_bytes(), locator.position)
        self.bridge.image_loader = loader
        await self.capture(self.image_event())
        self.assertEqual(self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = 'image-loading-proof'", ())["value"], "ok")

    async def test_other_participant_cannot_read_original_image(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        event = self.original(app, batch)
        other = replace(context, actor=self.standard)
        with self.assertRaises(TaskError):
            app.messages.images.read(other, (event,), self.clock.now())
        other_role = replace(context.role_binding, role=SubjectRef(event.scope.installation_id, SubjectKind.BOT, "another-role"))
        with self.assertRaises(TaskError):
            app.messages.images.read(replace(context, role_binding=other_role), (event,), self.clock.now())

    async def test_asset_revoked_after_generation_prevents_platform_send(self):
        self.native.provider.client.state.behavior = attach_originals
        host = await self.setup_host()
        app = host.application
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        event = self.original(app, batch)
        _, asset = app.messages.images.references(event, event.images[0])
        revoked = False
        async def revoke_after_model(_):
            nonlocal revoked
            if self.native.provider.client.state.calls and not revoked:
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        reached = intercept_image_delivery_verification(self.bridge, app.runtime, revoke_after_model)
        await self.done(app, batch)
        self.assertTrue(reached())
        self.assertTrue(revoked)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_unknown_image_model_result_is_not_retried_on_event_replay(self):
        async def timeout(*_):
            raise TimeoutError("provider processing unknown")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.image_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        sends = self.native.platform.client._api._wsr_api.calls
        self.assertEqual(len(sends), 1)
        self.assertEqual([part["type"] for part in sends[0][1]["message"]], ["text"])

    async def test_credentials_are_redacted_without_corrupting_image_bytes(self):
        event = self.image_event()
        event.message_obj.message[0].text = "password=example-test-value 看图片"
        host = await self.quiet_host()
        batch = host.receive(await self.capture(event)).unwrap()
        request = self.request(host.application, batch)
        self.assertNotIn("example-test-value", request.context.materials[0].payload)
        self.assertIn("[REDACTED]", request.context.materials[0].payload)
        self.assertEqual(base64.b64decode(request.images[0].data_base64), image_bytes())

    async def test_same_platform_event_and_same_bytes_under_another_login_have_distinct_owners(self):
        from companion_v2.adapters.astrbot.native import HostAccount, HostPersona, persona_instruction
        from companion_v2.identity_policy.personas import PersonaBinding, PersonaSelector, SelectorKind
        from tests.test_management_access import NOW, ROLE, INSTALLATION
        self.bind_native("101", "bot-b")
        role_b = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b")
        self.personas.set_binding(PersonaBinding("b-role", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-b"),
            role_b, 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
        self.bridge = self.make_bridge(accounts=(HostAccount("qq-main", "100", "bot-a"), HostAccount("qq-main", "101", "bot-b")),
            personas=(HostPersona("bot-a", "original", ROLE, digest(persona_instruction(self.native.persona))),
                      HostPersona("bot-b", "original", role_b, digest(persona_instruction(self.native.persona)))))
        self.bridge.image_type, self.bridge.image_loader, self.bridge.now = FakeImage, load_native_image, self.clock.now
        self.native_model.bridge = self.bridge
        self.native_sender.bridge = self.bridge
        host = await self.quiet_host()
        event_a, event_b = self.image_event(), self.image_event()
        event_b.message_obj.self_id = "101"
        a, b = host.receive(await self.capture(event_a)).unwrap(), host.receive(await self.capture(event_b)).unwrap()
        ia, ib = self.request(host.application, a).images[0], self.request(host.application, b).images[0]
        self.assertNotEqual(ia.source, ib.source)
        self.assertEqual(ia.descriptor.content_hash, ib.descriptor.content_hash)
        with self.assertRaises(TaskError):
            host.application.messages.images.read(self.context(host.application, b), (self.original(host.application, a),), self.clock.now())
