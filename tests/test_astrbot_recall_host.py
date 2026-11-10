"""Native recall routing, durable admission fences and receipt preservation."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from tests.recall_host_support import RecallAssertions, recall_notice, sdk_environment
from astrbot_host.controller import AstrBotStagingController
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.conversation.quotes.models import CapturedQuote
from companion_v2.foundation.clock import DeterministicClock
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import FakeEvent, FakePlatform, FakeProvider, FakeReverseApi, completion, final_text
from tests.staging_bootstrap_support import database_contents, wait_for_batch
from tests.test_management_access import NOW


class MultiHostRecallTests(RecallAssertions, GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_recall_uses_original_id_and_duplicate_notices_are_idempotent(self):
        app = await self.start_host()
        original = self.event(identifier="701")
        batch = await self.admit(original)
        notice = recall_notice(original)
        self.assertNotEqual(notice.message_obj.message_id, str(notice.message_obj.raw_message["message_id"]))
        calls = len(self.native.provider.client.state.calls)
        result = await self.recall(notice)
        self.assertEqual((result["replayed"], result["withdrawn_inputs"]), (False, 1))
        self.assertEqual((result["revoked_quotes"], result["recalled_replies"]), (0, 0))
        self.assert_withdrawn(app, batch)
        duplicate = recall_notice(original)
        self.assertNotEqual(notice.message_obj.message_id, duplicate.message_obj.message_id)
        self.assertTrue((await self.recall(duplicate))["replayed"])
        self.assertEqual(self.notice_count(app), 1)
        self.assertEqual(len(self.native.provider.client.state.calls), calls)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_non_recall_notices_are_not_claimed_or_persisted(self):
        app = await self.start_host()
        before = database_contents(app.runtime)
        self.assertIsNone(await self.controller.receive_notice(self.event(identifier="701")))
        for original in (self.event(identifier="701"), self.group_event(identifier="701")):
            notice = recall_notice(original)
            notice.message_obj.raw_message.update(notice_type="notify", sub_type="poke", target_id=100)
            self.assertFalse(self.controller.claims(notice))
            self.assertIsNone(await self.controller.receive_notice(notice))
        self.assertEqual(database_contents(app.runtime), before)

    async def test_recall_before_input_survives_restart_and_blocks_model_admission(self):
        app = await self.start_host()
        original = self.event(identifier="-701")
        result = await self.recall(recall_notice(original))
        self.assertEqual(result["withdrawn_inputs"], 0)
        self.assertEqual(self.notice_count(app), 1)
        rejected = await self.controller.receive_claimed(original)
        self.assertEqual(rejected.failure.code, "message_withdrawn")
        await self.controller.terminate()
        self.controller = self.make_controller(group_config())
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        app = self.controller.session.application
        self.assertTrue((await self.recall(recall_notice(self.event(identifier="-701"))))["replayed"])
        rejected = await self.controller.receive_claimed(self.event(identifier="-701"))
        self.assertEqual(rejected.failure.code, "message_withdrawn")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM conversation_inputs", ())[0], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_same_platform_accounts_private_and_groups_do_not_share_recall_identity(self):
        config = group_config()
        config["multi_host"]["targets"][1]["platform_id"] = "qq-main"
        config["multi_host"]["group_targets"][2]["platform_id"] = "qq-main"
        self.controller = self.make_controller(config)
        app = await self.start_host()
        originals = [self.event(target, identifier="701") for target in ("first", "second")]
        originals += [self.group_event(target, identifier="701") for target in ("group-a", "group-b", "group-c")]
        batches = [await self.admit(event) for event in originals]
        self.assertEqual((await self.recall(recall_notice(originals[0])))["withdrawn_inputs"], 1)
        for index, batch in enumerate(batches):
            self.assert_withdrawn(app, batch, index == 0)
        self.assertEqual((await self.recall(recall_notice(originals[2], operator_id="999")))["withdrawn_inputs"], 1)
        for index, batch in enumerate(batches):
            self.assert_withdrawn(app, batch, index in (0, 2))

    async def test_group_admin_recall_uses_original_author_and_preserves_other_speaker(self):
        app = await self.start_host()
        original = self.group_event(identifier="701", sender="201", unique=True)
        batch = await self.admit(original)
        other = await self.admit(self.group_event(identifier="702", sender="999", unique=True))
        notice = recall_notice(original, operator_id="999")
        self.assertEqual(notice.message_obj.session_id, "300")
        self.assertEqual(notice.session.session_id, "201_300")
        self.assertEqual((await self.recall(notice))["withdrawn_inputs"], 1)
        self.assert_withdrawn(app, batch)
        self.assert_withdrawn(app, other, False)

    async def test_recall_finds_original_personas_and_conversations_without_role_lookup(self):
        app = await self.start_host()
        original = self.event(identifier="701")
        first = await self.admit(original)
        self.clock.advance(timedelta(seconds=1))
        self.window_personas[original.unified_msg_origin] = "second"
        self.native.conversations["qq-main"] = "changed-conversation"
        second = await self.admit(self.event(identifier="701"))
        self.assertNotEqual(self.original_context(app, first).scope, self.original_context(app, second).scope)
        async def forbidden_lookup(_):
            raise AssertionError("recall must not resolve the current persona")
        bridge = self.controller.session.bridge
        original_lookup = bridge.session_persona_override_lookup
        bridge.session_persona_override_lookup = forbidden_lookup
        try:
            result = await self.recall(recall_notice(original))
        finally:
            bridge.session_persona_override_lookup = original_lookup
        self.assertEqual(result["withdrawn_inputs"], 2)
        self.assert_withdrawn(app, first)
        self.assert_withdrawn(app, second)

    async def test_untrusted_or_unconfigured_notice_has_no_persistent_effect(self):
        app = await self.start_host()
        original = self.group_event(identifier="701")
        batch = await self.admit(original)
        mutations = (
            lambda e: e.message_obj.raw_message.update(self_id=101),
            lambda e: e.message_obj.raw_message.update(user_id=201),
            lambda e: e.message_obj.raw_message.update(group_id=301),
            lambda e: e.message_obj.raw_message.update(message_id=True),
            lambda e: e.message_obj.raw_message.update(message_id="invalid"),
            lambda e: setattr(e.session, "session_id", "999_300"),
            lambda e: setattr(e, "bot", object()),
            lambda e: setattr(e.platform_meta, "name", "untrusted"),
            lambda e: setattr(e.message_obj, "raw_message", None),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                notice = recall_notice(original)
                mutate(notice)
                before = database_contents(app.runtime)
                result = await self.controller.receive_notice(notice)
                self.assertTrue(result is None or result.failure is not None, result)
                self.assertEqual(database_contents(app.runtime), before)
        unknown = self.group_event(identifier="701")
        unknown.message_obj.group_id = unknown.message_obj.session_id = unknown.session.session_id = "999"
        unknown.unified_msg_origin = "qq-main:GroupMessage:999"
        self.assertIsNone(await self.controller.receive_notice(recall_notice(unknown)))
        self.assert_withdrawn(app, batch, False)

    async def test_replaced_platform_or_disconnected_account_cannot_apply_recall(self):
        app = await self.start_host()
        original = self.event(identifier="701")
        batch = await self.admit(original)
        platform = self.native.platforms["qq-main"]
        before = database_contents(app.runtime)
        self.native.platforms["qq-main"] = FakePlatform("qq-main")
        try:
            for event in (original, self.event(identifier="701")):
                result = await self.controller.receive_notice(recall_notice(event))
                self.assertTrue(result is None or result.failure is not None, result)
        finally:
            self.native.platforms["qq-main"] = platform
        connection = platform.client._wsr_api_clients.pop("100")
        try:
            result = await self.controller.receive_notice(recall_notice(original))
            self.assertTrue(result is None or result.failure is not None, result)
        finally:
            platform.client._wsr_api_clients["100"] = connection
        self.assertEqual(database_contents(app.runtime), before)
        self.assert_withdrawn(app, batch, False)

    async def test_recall_during_persona_capture_prevents_late_input_admission(self):
        app = await self.start_host()
        entered, release = asyncio.Event(), asyncio.Event()
        bridge = self.controller.session.bridge
        original_lookup = bridge.session_persona_override_lookup
        async def held(umo):
            entered.set()
            await release.wait()
            return await original_lookup(umo)
        bridge.session_persona_override_lookup = held
        original = self.event(identifier="701")
        pending = asyncio.create_task(self.controller.receive_claimed(original))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            result = await asyncio.wait_for(self.recall(recall_notice(original)), 2)
            self.assertEqual(result["withdrawn_inputs"], 0)
        finally:
            release.set()
            bridge.session_persona_override_lookup = original_lookup
        self.assertEqual((await pending).failure.code, "message_withdrawn")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM conversation_inputs", ())[0], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_recall_while_model_runs_cannot_publish_a_reply(self):
        app = await self.start_host()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(payload, tools):
            entered.set()
            await release.wait()
            return completion()
        self.native.provider.client.state.behavior = held
        original = self.group_event(identifier="701", sender="201")
        batch = (await self.controller.receive_claimed(original)).unwrap()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual((await self.recall(recall_notice(original, operator_id="999")))["withdrawn_inputs"], 1)
        finally:
            release.set()
        await wait_for_batch(self.controller.session, batch)
        self.assert_withdrawn(app, batch)
        self.assertEqual(app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,)), ())
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_recall_during_first_send_preserves_ack_and_cancels_remaining_parts(self):
        self.controller = self.make_controller(group_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        app.generator.split = lambda text: tuple(text.splitlines(keepends=True))
        async def answer(payload, tools):
            return completion(final_text("第一段。\n第二段。"))
        self.native.provider.client.state.behavior = answer
        entered, release = asyncio.Event(), asyncio.Event()
        api = self.native.platform.client._api._wsr_api
        async def held_send(action, **params):
            api.calls.append((action, params))
            entered.set()
            await release.wait()
            return {"message_id": -456}
        api.call_action = held_send
        original = self.event(identifier="701")
        batch = (await self.controller.receive_claimed(original)).unwrap()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.recall(recall_notice(original))
        finally:
            release.set()
        await wait_for_batch(self.controller.session, batch)
        parts = app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch,))
        self.assertEqual([part["state"] for part in parts], ["delivered", "cancelled"])
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg"])
        self.assertTrue(parts[0]["platform_ref"].endswith(":-456"))
        self.assertTrue(parts[0]["receipt_ref"])
        context = self.original_context(app, batch)
        history = app.messages.history(context).unwrap()["items"]
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["body_available"])

    async def test_group_recall_of_confirmed_bot_reply_retains_delivery_receipt(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        app = await self.start_host()
        self.native.platform.client._api._wsr_api.result = {"message_id": -456}
        batch = await self.admit(self.group_event(identifier="701"))
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "delivered")
        reply = self.group_event(identifier="-456", sender="100")
        result = await self.recall(recall_notice(reply, operator_id="999"))
        self.assertEqual((result["withdrawn_inputs"], result["recalled_replies"]), (0, 1))
        self.assert_withdrawn(app, batch, False)
        after = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        for key in ("state", "receipt_ref", "platform_ref", "delivered_at"):
            self.assertEqual(after[key], part[key])
        history = app.messages.history(self.original_context(app, batch)).unwrap()["items"]
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["body_available"])
        self.assertIsNone(history[0]["text"])

    async def test_unknown_send_receipt_survives_recall_and_restart_without_replay(self):
        config = group_config(delivery_mode="onebot_private")
        self.controller = self.make_controller(config)
        app = await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        api = self.native.platform.client._api._wsr_api
        api.error = TimeoutError("synthetic lost ACK after dispatch")
        original = self.event(identifier="701")
        batch = await self.admit(original)
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "unknown")
        await self.recall(recall_notice(original))
        after = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        for key in ("state", "receipt_ref", "platform_ref", "dispatched_at", "delivered_at"):
            self.assertEqual(after[key], part[key])
        self.assert_withdrawn(app, batch)
        await self.controller.terminate()
        self.controller = self.make_controller(config)
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        app = self.controller.session.application
        restored = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual((restored["state"], restored["receipt_ref"]), ("unknown", part["receipt_ref"]))
        result = await self.controller.receive_claimed(self.event(identifier="701"))
        self.assertEqual(result.failure.code, "message_withdrawn")
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg"])


class StagingRecallTests(RecallAssertions, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-single-recall-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.native = sdk_environment.WebContext()
        self.clock = DeterministicClock.at(NOW)
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.controller = self.make_controller()

    def make_controller(self):
        controller = AstrBotStagingController(self.native, sdk_environment.host_config(), self.root,
            native_types=self.types, now=self.clock.now)
        self.addAsyncCleanup(controller.terminate)
        return controller

    async def start_host(self):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start()).unwrap()
        return self.controller.session.application

    def quoted_input(self):
        class Reply:
            type = "Reply"
            id = "600"
        async def quote(locator, **kwargs):
            return CapturedQuote(locator.position, locator.message_id, "200", None, "已经撤回的原话", ())
        self.types = replace(self.types, reply=Reply, quote_loader=quote)
        self.controller = self.make_controller()
        event = FakeEvent(self.native, event_id="701")
        event.message_obj.message.insert(0, Reply())
        return event

    async def test_single_target_recall_withdraws_original_without_current_persona(self):
        app = await self.start_host()
        original = FakeEvent(self.native, event_id="701")
        batch = await self.admit(original)
        self.native.selected = "unmapped-now"
        result = await self.recall(recall_notice(original))
        self.assertEqual(result["withdrawn_inputs"], 1)
        self.assert_withdrawn(app, batch)
        self.assertTrue((await self.recall(recall_notice(original)))["replayed"])

    async def test_single_target_out_of_order_recall_survives_reload(self):
        await self.start_host()
        original = FakeEvent(self.native, event_id="701")
        await self.recall(recall_notice(original))
        await self.controller.terminate()
        self.controller = self.make_controller()
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        result = await self.controller.receive_claimed(FakeEvent(self.native, event_id="701"))
        self.assertEqual(result.failure.code, "message_withdrawn")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_recall_of_quoted_original_revokes_quote_without_withdrawing_quoting_user(self):
        original = self.quoted_input()
        app = await self.start_host()
        batch = await self.admit(original)
        result = await self.recall(recall_notice(original, message_id="600"))
        self.assertEqual((result["withdrawn_inputs"], result["revoked_quotes"]), (0, 1))
        self.assert_withdrawn(app, batch, False)
        context = self.original_context(app, batch)
        from companion_v2.execution.models import TaskError
        with self.assertRaises(TaskError):
            app.messages._request(app.messages.batch(batch), context, self.clock.now())

    async def test_quote_of_previously_recalled_message_never_reaches_model_or_storage(self):
        original = self.quoted_input()
        app = await self.start_host()
        await self.recall(recall_notice(original, message_id="600"))
        before = database_contents(app.runtime)
        result = await self.controller.receive_claimed(original)
        self.assertEqual(result.failure.code, "message_withdrawn")
        self.assertEqual(database_contents(app.runtime), before)
        self.assertEqual(self.native.provider.client.state.calls, [])


class NativeRecallEntryTests(unittest.TestCase):
    def test_real_sdk_notice_conversion_plugin_entry_and_shutdown(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "tests.recall_entry_process"],
            cwd=root, env=os.environ.copy(), text=True, encoding="utf-8", capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: real SDK recall and poke entry", result.stdout)


if __name__ == "__main__":
    unittest.main()
