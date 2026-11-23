"""Voice through intake, model/tools, receipts, source withdrawal and restart."""

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.model_wire import build_wire, tool_alias
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.model_gateway.models import ModelEnvelope
from companion_v2.sources.dependencies import key
from tests.audio_support import AudioFixture, FakeRecord, audio_bytes
from tests.native_adapter_support import completion, final_text
from tests.test_conversation_images import FakeImage


class ConversationAudioTests(AudioFixture, unittest.IsolatedAsyncioTestCase):
    async def test_stored_merge_budget_is_checked_before_reading_audio_blobs(self):
        host = await self.quiet_host()
        first = host.receive(await self.capture(self.audio_event("first", count=3))).unwrap()
        second = host.receive(await self.capture(self.audio_event("second", count=2))).unwrap()
        self.assertNotEqual(first, second)
        app = host.application
        with app.runtime.transaction() as db:
            db.execute("UPDATE conversation_inputs SET batch_id = ?, position = 2 WHERE batch_id = ?", (first, second))
        with patch.object(app.messages.audio, "read", side_effect=AssertionError("must reject before reading blobs")):
            with self.assertRaises(TaskError) as caught:
                self.request(app, first)
        self.assertEqual(caught.exception.code, "audio_turn_limit")

    async def test_withdrawal_during_provider_preparation_blocks_model_dispatch(self):
        host = await self.setup_host()
        captured = await self.capture(self.audio_event())
        original_prepare = type(self.native.provider)._prepare_chat_payload
        async def withdraw(native, **kwargs):
            app = host.application
            batch = app.runtime.read_one("SELECT batch_id FROM conversation_inputs", ())[0]
            app.withdraw(self.context(app, batch), self.original(app, batch).scoped_key).unwrap()
            return await original_prepare(native, **kwargs)
        with patch.object(type(self.native.provider), "_prepare_chat_payload", withdraw):
            batch = host.receive(captured).unwrap()
            await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(host.receive(captured).failure.code, "message_withdrawn")

    async def test_asset_revocation_after_model_result_blocks_external_reply(self):
        host = await self.setup_host()
        async def revoke(payload, _):
            app = host.application
            batch = app.runtime.read_one("SELECT batch_id FROM conversation_inputs", ())[0]
            event = self.original(app, batch)
            _, _, asset = app.messages.audio.references(event, event.audios[0])
            app.messages.audio.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            return completion()
        self.native.provider.client.state.behavior = revoke
        batch = host.receive(await self.capture(self.audio_event())).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_unknown_audio_model_call_is_not_repeated_by_input_replay(self):
        async def timeout(payload, _):
            raise TimeoutError("synthetic lost response")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.audio_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_v28_text_intake_upgrades_without_rewriting_old_event_or_hash(self):
        from contextlib import closing
        import sqlite3
        from tests.execution_support import remove_v29
        from tests.native_adapter_support import FakeEvent
        host = await self.quiet_host()
        batch = host.receive(await self.capture(FakeEvent(self.native))).unwrap()
        runtime = host.application.runtime
        saved = dict(runtime.read_one("SELECT * FROM conversation_inputs WHERE batch_id = ?", (batch,)))
        database = runtime.database_path
        await host.application.close()
        with closing(sqlite3.connect(database)) as db, db:
            remove_v29(db)
            db.execute("UPDATE runtime_metadata SET value = 'companion-v2.storage.v28' WHERE key = 'schema_version'")
        reopened = self.open_runtime()
        self.assertEqual(dict(reopened.read_one("SELECT * FROM conversation_inputs WHERE batch_id = ?", (batch,))), saved)
        self.assertEqual(reopened.read_one("SELECT COUNT(*) FROM conversation_audio_payloads", ())[0], 0)

    async def test_voice_only_tool_continuation_keeps_bytes_evidence_and_confirmed_memory(self):
        views = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            views.append(content)
            evidence = next(m["evidence"] for m in json.loads(content[0]["text"])["materials"] if m["owner"] == "conversation.audio")
            if len(views) == 1:
                return completion(tools=[NS(id="voice-read", type="function", function=NS(name=tool_alias("memo.list"), arguments="{}"))])
            return completion(final_text("这段录音有声音。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        captured = await self.capture(self.audio_event())
        self.assertNotIn("可信转写", captured.text)
        batch = host.receive(captured).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(views), 2)
        self.assertEqual(views[0][-2:], views[1][-2:])
        self.assertEqual(base64.b64decode(views[0][-1]["input_audio"]["data"]), captured.audios[0].waveform)
        event = self.original(app, batch)
        source, asset, _ = app.messages.audio.references(event, event.audios[0])
        access = app.memory.writer.access.sent_sources
        reference, = tuple(access.rows(context))
        self.assertEqual(access.materialize(context, reference).payload, "这段录音有声音。")
        dependencies = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        self.assertIn(key(source), [tuple(r) for r in dependencies])
        app.messages.audio.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError) as caught:
            access.validate(context, reference)
        self.assertEqual(caught.exception.code, "asset_revoked")
        app.withdraw(context, event.scoped_key).unwrap()
        self.assertEqual(app.messages.sources.validate_reference(reference, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertFalse(app.messages.history(context).unwrap()["items"][0]["body_available"])

    async def test_text_images_and_multiple_voices_keep_positions_and_separate_sendable_images(self):
        host = await self.quiet_host()
        event = self.audio_event()
        event.message_obj.message = [FakeRecord(), NS(type="Plain", text="再看图片"), FakeImage(), FakeRecord()]
        batch = host.receive(await self.capture(event)).unwrap()
        second = host.receive(await self.capture(self.audio_event("second"))).unwrap()
        self.assertEqual(second, batch)
        request = self.request(host.application, batch)
        self.assertEqual([a.descriptor.position for a in request.audios], [0, 3, 0])
        self.assertEqual([i.descriptor.position for i in request.images], [2])
        wire = build_wire(ModelEnvelope(replace(request, reply_images=True), "fixture", 100, "fixture"))
        self.assertEqual(list(wire.reply_images.values()), [request.images[0].source])
        self.assertEqual(wire.audio_duration_ms, 600)
        self.assertLess(wire.input_estimate(), len(json.dumps(wire.messages)))

    async def test_audio_duration_and_count_split_debounce_batches(self):
        host = await self.quiet_host()
        for index in range(5):
            batch = host.receive(await self.capture(self.audio_event(str(index)))).unwrap()
            if index == 0:
                first = batch
            self.assertEqual(batch == first, index < 4)
        context = self.context(host.application, first)
        host.application.cancel(context, first).unwrap()
        host.application.cancel(self.context(host.application, batch), batch).unwrap()
        a = host.receive(await self.capture(self.audio_event("long-a", audio_bytes(45, 8000), count=2))).unwrap()
        b = host.receive(await self.capture(self.audio_event("long-b", audio_bytes(45, 8000)))).unwrap()
        self.assertNotEqual(a, b)

    async def test_manifest_or_publication_failure_rolls_back_entire_intake(self):
        host = await self.quiet_host()
        captured = await self.capture(self.audio_event())
        with patch.object(host.application.messages.audio, "publish", side_effect=TaskError("audio_content_changed", "synthetic")):
            self.assertEqual(host.receive(captured).failure.code, "audio_content_changed")
        self.assertEqual(host.application.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)
        self.assertEqual(host.application.runtime.read_one("SELECT COUNT(*) n FROM sources WHERE owner = 'conversation.host_input'", ())["n"], 0)

    async def test_changed_replay_cannot_replace_original_audio(self):
        host = await self.quiet_host()
        captured = await self.capture(self.audio_event())
        batch = host.receive(captured).unwrap()
        before = self.request(host.application, batch).audios
        changed = host.receive(await self.capture(self.audio_event(content=audio_bytes(value=2000))))
        self.assertEqual(changed.failure.code, "message_event_conflict")
        self.assertEqual(self.request(host.application, batch).audios, before)
        self.assertEqual(host.receive(captured).unwrap(), batch)

    async def test_revoked_assets_and_corrupt_original_or_wave_block_read(self):
        host = await self.quiet_host()
        for index, field in enumerate(("original", "wave_asset", "content", "waveform", "missing")):
            batch = host.receive(await self.capture(self.audio_event(str(index)))).unwrap()
            app, event = host.application, self.original(host.application, batch)
            _, asset, waveform = app.messages.audio.references(event, event.audios[0])
            if field in ("original", "wave_asset"):
                app.messages.audio.assets.revoke(asset if field == "original" else waveform,
                    expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            else:
                with app.runtime.transaction() as db:
                    if field == "missing":
                        db.execute("DELETE FROM conversation_audio_payloads WHERE parent_id = ?", (event.source.source_id,))
                    else:
                        db.execute("UPDATE conversation_audio_payloads SET " + field + " = ? WHERE parent_id = ?", (b"changed", event.source.source_id))
            with self.assertRaises(TaskError) as caught:
                self.request(app, batch)
            self.assertEqual(caught.exception.code, "asset_revoked" if field in ("original", "wave_asset") else
                             "audio_original_unavailable" if field == "missing" else "audio_content_changed")
            app.cancel(self.context(app, batch), batch).unwrap()

    async def test_restart_uses_saved_audio_without_network_or_decoding(self):
        host = await self.quiet_host()
        captured = await self.capture(self.audio_event())
        batch = host.receive(captured).unwrap()
        before = self.request(host.application, batch).audios
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host"))
        with patch("astrbot_host.audio.loader.decode_audio", side_effect=AssertionError("must not decode again")):
            await self.start(new, 1, 1)
            self.assertEqual(self.request(new.application, batch).audios, before)
            self.assertEqual(new.receive(captured).unwrap(), batch)

    async def test_provider_without_audio_capability_or_dropped_payload_cannot_dispatch(self):
        provider = replace(self.native_model.local_provider(), capabilities=("text", "tools", "image"))
        host = await self.setup_host(providers=(provider,))
        batch = host.receive(await self.capture(self.audio_event())).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_sdk_preparation_cannot_drop_audio(self):
        host = await self.setup_host()
        original = type(self.native.provider)._prepare_chat_payload
        async def drop(native, **kwargs):
            payload, contexts = await original(native, **kwargs)
            next(m for m in payload["messages"] if m["role"] == "user")["content"] = "text only"
            return payload, contexts
        with patch.object(type(self.native.provider), "_prepare_chat_payload", drop):
            batch = host.receive(await self.capture(self.audio_event())).unwrap()
            await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_capture_rechecks_role_and_frozen_locator_after_decode(self):
        event = self.audio_event()
        async def mutate(locator):
            self.assertTrue(locator.locator.startswith("base64://"))
            event.message_obj.message[0].file = "file:///C:/secret.wav"
            return self.captured_audio(locator.position)
        self.bridge.audio_loader = mutate
        self.assertEqual((await self.capture(event)).audios[0].content, audio_bytes())
        async def change_role(locator):
            self.native.cid = "different"
            return self.captured_audio(locator.position)
        self.bridge.audio_loader = change_role
        result = await self.bridge.capture(self.audio_event())
        self.assertTrue(result.is_success, result.failure)
