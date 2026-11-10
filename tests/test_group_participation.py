"""Selective native group turns with durable listening, governed history and no replay."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.group_authority import GroupAuthority
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.configuration.models import ConfigurationError
from companion_v2.conversation.participation import ACTION, INSTRUCTION, SKIPPED
from companion_v2.conversation.reply_settings import (
    GROUP_AIR_DEFAULTS, GROUP_WAKEUP_DEFAULTS, GROUP_WAKEUP_LEGACY_FIELDS,
    GROUP_WAKEUP_PREVIOUS_FIELDS, GROUP_WAKEUP_PROMPT_FIELDS, validate as validate_reply_settings)
from companion_v2.execution.models import TaskContext, TaskError, encode
from tests.group_host_support import GroupFixture, group_config
from tests.group_media_support import GroupMediaFixture, Plain, sdk_image
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch


def command(action, arguments, identifier="choice"):
    return NS(id=identifier, type="function", function=NS(name=tool_alias(action), arguments=encode(arguments)))


class GroupParticipationTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_legacy_delivered_followups_share_the_native_fuse_and_count_batches_once(self):
        await self.setup_delivery()
        admission = self.app.tools.group_participation.admission
        for index in range(4):
            context, batch, _ = await self.receive("这一部分继续聊", commands=((ACTION, {"mode": "reply"}),))
            admission.record(context, batch, reason="mention" if index == 0 else "reply_follow_up",
                             mentioned=index == 0)
            saved = admission.recorded(context, batch)
            saved["reasons"] = ["mention" if index == 0 else "reply_follow_up"]
            with self.app.runtime.transaction() as connection:
                connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                    (json.dumps(saved, ensure_ascii=False, sort_keys=True), "conversation.group_admission.v1:" + batch))
            self.clock.advance(timedelta(seconds=1))
            result = (await self.controller.group_reply_candidate(self.group_event(
                identifier="legacy-follow-" + str(index), text="接着说"))).unwrap()
            self.assertEqual(result["continuation"]["used_turns"], index)
            self.assertEqual(result["admitted"], index < 3)

    def setUp(self):
        super().setUp()
        self.sequence = 0

    @property
    def app(self):
        return self.controller.session.application

    @property
    def choices(self):
        return self.app.tools.group_participation

    @property
    def sent(self):
        return self.native.platform.client._api._wsr_api.calls

    async def setup_delivery(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()

    async def receive(self, text="两位成员还在继续讨论", *, commands=(), behavior=None, sender="200", **options):
        async def respond(payload, tools):
            done = len([m for m in payload["messages"] if m["role"] == "tool"])
            if done < len(commands):
                action, arguments = commands[done]
                return completion(tools=[command(action, arguments, "command-" + str(done))])
            return completion(final_text("我也说一句。"))
        self.native.provider.client.state.behavior = behavior or respond
        self.sequence += 1
        event = self.group_event(identifier="participation-" + str(self.sequence), sender=sender, text=text, **options)
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await self.settle(batch)
        context = TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"]))
        return context, batch, event

    async def settle(self, batch):
        await wait_for_batch(self.controller.session, batch)
        async def wait():
            while any(work.batch_id == batch for work, _ in self.app._workers.values()):
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 3)

    def parts(self, batch):
        return self.app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))

    def history(self, context, batch):
        request = self.app.messages._request(self.app.messages.batch(batch), context, self.clock.now())
        return [json.loads(m.payload) for m in request.context.materials if m.purpose == "group_conversation_history"]

    def withdraw(self, context, batch):
        row = self.app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))
        self.app.withdraw(context, row["event_key"]).unwrap()

    async def test_skip_keeps_original_input_for_later_speaker_and_creates_no_message(self):
        await self.setup_delivery()
        a, skipped, _ = await self.receive("周末想去河边走走", commands=((ACTION, {"mode": "skip"}),))
        self.assertEqual(self.choices.decision(a, skipped)["mode"], "skip")
        self.assertEqual((self.app.flow.row(skipped)["state"], self.app.flow.row(skipped)["reason"]), ("complete", SKIPPED))
        self.assertEqual(self.app.messages.batch(skipped)["state"], "sealed")
        task = self.app.tasks.read(a, self.app.messages.batch(skipped)["generation_task_id"]).unwrap()
        self.assertEqual(task.state, "succeeded")
        self.assertEqual(self.parts(skipped), ())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ()), ())
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        b, replied, _ = await self.receive("刚才大家准备去哪里", sender="201")
        self.assertEqual([(item["speaker_id"], item["text"]) for item in self.history(b, replied)], [("200", "周末想去河边走走")])
        self.assertEqual(len(self.sent), 1)

    async def test_failed_skip_choice_still_finishes_silently(self):
        await self.setup_delivery()
        with patch.object(self.choices, "execute",
                          side_effect=TaskError("group_participation_started", "participation already started")):
            context, batch, _ = await self.receive(
                commands=((ACTION, {"mode": "skip", "reason": "这轮继续旁听"}),))

        rows = self.app.tools._rows(batch)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "failed")
        self.assertEqual(json.loads(rows[0]["result_json"])["code"], "group_participation_started")
        self.assertEqual(self.choices.decision(context, batch)["mode"], "skip")
        self.assertEqual((self.app.flow.row(batch)["state"], self.app.flow.row(batch)["reason"]),
                         ("complete", SKIPPED))
        self.assertEqual(self.parts(batch), ())
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_laya_wait_completes_as_silence_without_model_call_or_platform_ack(self):
        await self.setup_delivery()

        class DecisionService:
            async def decide(self, _facts):
                return {"status": "ok", "decision": {
                    "turn_state": "unfinished", "action": "wait", "reply_intent": "acknowledge",
                    "reply_style": "brief", "reply_form": "text", "segment_mode": "single",
                    "tool_need": "memory", "reason_codes": [],
                }}

        self.app.messages.laya_decision_resolver = lambda _context: DecisionService()
        participation = self.choices
        with patch.object(participation, "record_no_reply", wraps=participation.record_no_reply) as record_no_reply:
            _, batch, _ = await self.receive("我刚刚想说的是")

        record_no_reply.assert_called_once()
        self.assertEqual(record_no_reply.call_args.args[1], batch)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.parts(batch), ())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.app.messages.batch(batch)["presentation_action"], "silent")
        self.assertEqual((self.app.flow.row(batch)["state"], self.app.flow.row(batch)["reason"]),
                         ("complete", "model_silent"))
        self.assertNotIn(batch, participation._frozen_turns)

    async def test_direct_reply_is_persisted_participation_without_an_extra_model_round(self):
        await self.setup_delivery()
        context, batch, _ = await self.receive("skip 这个单词是什么意思？")
        self.assertEqual(self.choices.decision(context, batch)["basis"], "final_reply")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        payload, _ = self.native.provider.client.state.calls[0]
        self.assertEqual(payload["messages"][0]["content"], self.native.persona["prompt"])
        self.assertIn(INSTRUCTION, payload["messages"][1]["content"])
        self.assertEqual(self.parts(batch)[0]["state"], "delivered")

    async def test_group_air_reference_uses_confirmed_history_without_blocking_reply(self):
        await self.setup_delivery()
        _, first, _ = await self.receive("晚安")
        self.assertEqual(self.parts(first)[0]["state"], "delivered")
        _, second, _ = await self.receive("谢谢，明天见", sender="201")
        saved = json.loads(self.app.messages.batch(second)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual({key: saved[key] for key in GROUP_AIR_DEFAULTS}, GROUP_AIR_DEFAULTS)
        payload, _ = self.native.provider.client.state.calls[-1]
        instruction = payload["messages"][1]["content"]
        self.assertIn("最近 180 秒的已授权历史里有 1 轮已确认 Bot 回复", instruction)
        self.assertIn("连续接话参考值 3 轮", instruction)
        self.assertIn("礼貌收尾循环参考值 2 次", instruction)
        self.assertEqual(self.parts(second)[0]["state"], "delivered")

    async def test_group_air_legacy_snapshot_and_invalid_values(self):
        await self.setup_delivery()
        context, batch, _ = await self.receive("这条应该由原人格判断")
        saved = json.loads(self.app.messages.batch(batch)["contributions_json"])
        legacy = dict(saved["reply_settings"]["settings"])
        for key in GROUP_AIR_DEFAULTS:
            legacy.pop(key)
        validate_reply_settings(legacy)
        with self.assertRaises(ConfigurationError):
            validate_reply_settings({**legacy, "group_air_guard_enabled": True})
        for key, value in (("group_air_guard_enabled", 1), ("group_air_guard_window_seconds", 0),
                           ("group_air_guard_max_bot_replies", 21), ("group_air_guard_polite_loop_limit", 0)):
            with self.subTest(key=key):
                with self.assertRaises(ConfigurationError):
                    validate_reply_settings({**legacy, **GROUP_AIR_DEFAULTS, key: value})
        self.assertEqual(self.choices.decision(context, batch)["mode"], "reply")

    async def test_group_wakeup_legacy_snapshots_and_invalid_values(self):
        await self.start_host()
        _, batch, _ = await self.receive()
        saved = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        for retained in (set(), set(GROUP_WAKEUP_LEGACY_FIELDS), set(GROUP_WAKEUP_PREVIOUS_FIELDS),
                         set(GROUP_WAKEUP_PROMPT_FIELDS), set(GROUP_WAKEUP_DEFAULTS)):
            validate_reply_settings({key: value for key, value in saved.items()
                                     if key not in GROUP_WAKEUP_DEFAULTS or key in retained})
        with self.assertRaises(ConfigurationError):
            validate_reply_settings({key: value for key, value in saved.items()
                                     if key != "group_wakeup_topic_interest_max_boost"})
        for key, value in (("group_wakeup_bot_name_enabled", 1),
                           ("group_wakeup_image_enabled", 1),
                           ("group_wakeup_image_weight", float("nan")),
                           ("group_wakeup_generated_keyword_limit", 81),
                           ("group_wakeup_topic_interest_max_boost", 151),
                           ("group_wakeup_debounce_pending_penalty", -1),
                           ("group_wakeup_interest_probability", float("nan")),
                           ("group_wakeup_interest_probability", 10**1000),
                           ("group_wakeup_short_text_wait_ms", 30001),
                           ("group_wakeup_short_text_wait_ms", 0.5),
                           ("group_wakeup_log_limit", 9)):
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                validate_reply_settings({**saved, key: value})

    async def test_explicit_reply_continues_and_cannot_be_reversed_to_skip(self):
        await self.setup_delivery()
        context, batch, _ = await self.receive(commands=((ACTION, {"mode": "reply"}), (ACTION, {"mode": "skip"})))
        rows = self.app.tools._rows(batch)
        self.assertEqual([r["state"] for r in rows], ["succeeded", "failed"])
        self.assertEqual(json.loads(rows[1]["result_json"])["code"], "group_participation_fixed")
        self.assertEqual(self.choices.decision(context, batch)["mode"], "reply")
        self.assertEqual(self.parts(batch)[0]["state"], "delivered")

    async def test_slow_participation_tool_round_continues_after_the_calendar_minute_changes(self):
        await self.setup_delivery()
        async def slow_choice(payload, tools):
            if not any(m["role"] == "tool" for m in payload["messages"]):
                self.clock.advance(timedelta(seconds=65))
                return completion(tools=[command(ACTION, {"mode": "reply", "reason": "回应当前群话题"})])
            return completion(final_text("我也说一句。"))
        context, batch, _ = await self.receive(behavior=slow_choice)
        self.assertEqual(self.app.flow.row(batch)["state"], "complete")
        self.assertEqual(self.choices.decision(context, batch)["mode"], "reply")
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        self.assertEqual(self.parts(batch)[0]["state"], "delivered")

    async def test_business_receipt_fixes_reply_before_later_skip_request(self):
        await self.setup_delivery()
        context, batch, _ = await self.receive("我想周六去河边", commands=(
            ("group.memory.remember", {"input_handle": "group-input-1", "statement": "周六去河边"}),
            (ACTION, {"mode": "skip"})))
        rows = self.app.tools._rows(batch)
        self.assertEqual(rows[0]["state"], "succeeded")
        self.assertIsNotNone(rows[0]["operation_id"])
        self.assertEqual(json.loads(rows[1]["result_json"])["code"], "group_participation_fixed")
        self.assertEqual(self.choices.decision(context, batch)["basis"], "business_tool")
        self.assertEqual(self.parts(batch)[0]["state"], "delivered")

    async def test_mixed_skip_and_business_round_cannot_hide_a_real_operation(self):
        await self.setup_delivery()
        async def mixed(payload, tools):
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[command(ACTION, {"mode": "skip"}), command("group.memory.remember",
                    {"input_handle": "group-input-1", "statement": "在本群公开讨论"}, "remember")])
            return completion(final_text("我记得这条公开讨论了。"))
        context, batch, _ = await self.receive(behavior=mixed)
        rows = self.app.tools._rows(batch)
        self.assertEqual(json.loads(rows[0]["result_json"])["code"], "group_participation_mixed")
        self.assertEqual(rows[1]["state"], "succeeded")
        self.assertEqual(self.choices.decision(context, batch)["mode"], "reply")
        self.assertEqual(len(self.sent), 1)

    async def test_withdrawal_during_use_of_listened_history_blocks_reply(self):
        await self.setup_delivery()
        a, first, _ = await self.receive("之后撤回的旁听材料", commands=((ACTION, {"mode": "skip"}),))
        async def withdrawn(payload, tools):
            materials = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "user"))["materials"]
            self.assertTrue(any(json.loads(m["text"])["text"] == "之后撤回的旁听材料"
                for m in materials if m["purpose"] == "group_conversation_history"))
            self.withdraw(a, first)
            return completion(final_text("这句话不该发出。"))
        b, second, _ = await self.receive(sender="201", behavior=withdrawn)
        self.assertEqual(self.app.flow.row(second)["state"], "blocked")
        self.assertEqual(self.parts(second), ())
        self.assertEqual(self.sent, [])
        c, third, _ = await self.receive(sender="202")
        self.assertNotIn("之后撤回的旁听材料", [m["text"] for m in self.history(c, third)])
        with self.assertRaises(TaskError):
            self.history(b, second)

    async def test_member_revocation_invalidates_listened_material_before_reply(self):
        await self.setup_delivery()
        a, first, _ = await self.receive("需要原成员授权的旁听", commands=((ACTION, {"mode": "skip"}),))
        async def revoked(payload, tools):
            identities = self.controller.authority.identities
            identities.revoke_grant(GroupAuthority.grant_id(a.scope, a.actor.subject, "text"),
                revoked_at=self.clock.now(), expected_revision=identities.revision)
            return completion(final_text("不再有效的材料不能发送。"))
        _, second, _ = await self.receive(sender="201", behavior=revoked)
        self.assertEqual(self.app.flow.row(second)["state"], "blocked")
        self.assertEqual(self.parts(second), ())
        self.assertEqual(self.sent, [])
        c, third, _ = await self.receive(sender="202")
        self.assertNotIn("需要原成员授权的旁听", [m["text"] for m in self.history(c, third)])

    async def test_skip_and_confirmed_reply_replay_after_restart_without_model_or_send(self):
        await self.setup_delivery()
        a, skipped, event = await self.receive(commands=((ACTION, {"mode": "skip"}),))
        b, replied, second = await self.receive(sender="201")
        calls = len(self.native.provider.client.state.calls)
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        for original, batch in ((event, skipped), (second, replied)):
            self.assertEqual((await self.controller.receive_claimed(original)).unwrap(), batch)
            await self.settle(batch)
        self.assertEqual(self.choices.decision(a, skipped)["mode"], "skip")
        self.assertEqual(self.choices.decision(b, replied)["mode"], "reply")
        self.assertEqual(len(self.native.provider.client.state.calls), calls)
        self.assertEqual(len(self.sent), 1)

    async def test_restart_after_choice_receipt_completes_skip_without_second_decision(self):
        await self.setup_delivery()
        saved = asyncio.Event()
        hold = asyncio.Event()
        original = self.app.tools.execute_async
        async def stop_after_receipt(*args):
            result = await original(*args)
            saved.set()
            await hold.wait()
            return result
        self.app.tools.execute_async = stop_after_receipt
        async def skip(payload, tools):
            return completion(tools=[command(ACTION, {"mode": "skip"})])
        self.native.provider.client.state.behavior = skip
        event = self.group_event(identifier="saved-before-stop")
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await asyncio.wait_for(saved.wait(), 3)
        self.assertEqual(self.app.tools._rows(batch)[0]["state"], "succeeded")
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.clock.advance(timedelta(seconds=1))
        self.app.notify()
        await self.settle(batch)
        self.assertEqual(self.app.flow.row(batch)["reason"], SKIPPED)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.parts(batch), ())
        self.assertEqual(self.sent, [])

    async def test_legacy_preview_normalizes_to_group_mode_and_replay_preserves_admission(self):
        config = group_config(group_delivery_mode="preview")
        config["runtime"].update(quiet_seconds=2, maximum_wait_seconds=5)
        self.controller = self.make_controller(config)
        await self.start_host()
        event = self.group_event(identifier="original-preview")
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        self.clock.advance(timedelta(seconds=3))
        self.app.notify()
        await self.settle(batch)
        context = TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"]))
        self.assertEqual(self.choices.decision(context, batch)["mode"], "reply")
        self.assertEqual(self.controller.authority.group_delivery.mode(batch), "onebot_group")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.parts(batch)[0]["state"], "delivered")
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        await self.settle(batch)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_unknown_model_call_is_not_retried_as_a_new_participation_decision(self):
        await self.setup_delivery()
        async def unknown(payload, tools):
            raise TimeoutError("synthetic lost result")
        _, batch, event = await self.receive(behavior=unknown)
        self.assertEqual(self.app.flow.row(batch)["state"], "blocked")
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        await self.settle(batch)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.sent, [])

    async def test_private_and_foreign_group_cannot_adopt_a_group_choice(self):
        await self.start_host()
        a, batch, _ = await self.receive(commands=((ACTION, {"mode": "skip"}),))
        self.native.provider.client.state.behavior = None
        private, private_batch = await self.accepted()
        self.assertEqual(self.app.messages.batch(private_batch)["state"], "ready")
        self.assertNotIn(ACTION, {t.action for t in self.app.tools.catalog_for(private)})
        self.assertNotIn(INSTRUCTION, self.native.provider.client.state.calls[-1][0]["messages"][1]["content"])
        foreign, _, _ = await self.receive(target="group-b")
        for context in (private, foreign):
            with self.subTest(scope=context.scope.key), self.assertRaises(TaskError):
                self.choices.decision(context, batch)
        self.assertEqual(self.choices.decision(a, batch)["mode"], "skip")

    async def test_altered_choice_receipt_cannot_turn_listening_into_participation(self):
        await self.start_host()
        context, batch, _ = await self.receive(commands=((ACTION, {"mode": "skip"}),))
        row = self.app.tools._rows(batch)[0]
        altered = json.loads(row["result_json"])
        altered["payload"]["mode"] = "reply"
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_tool_calls SET result_json = ? WHERE task_id = ? AND tool_call_id = ?",
                               (encode(altered), row["task_id"], row["tool_call_id"]))
        with self.assertRaises(TaskError):
            self.choices.decision(context, batch)
        self.assertEqual(self.parts(batch), ())
        self.assertEqual(self.sent, [])


class GroupParticipationMediaTests(GroupMediaFixture, unittest.IsolatedAsyncioTestCase):
    async def test_listened_image_keeps_its_original_and_later_history_uses_metadata(self):
        await self.with_delivery()
        async def skip(payload, tools):
            current = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            self.assertTrue(any(part["type"] == "image_url" for part in current))
            return completion(tools=[command(ACTION, {"mode": "skip"})])
        self.native.provider.client.state.behavior = skip
        event = self.media_event([Plain("这张先给大家看看"), sdk_image()], identifier="listened-image")
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        context = TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"]))
        original = self.request(context, batch).images[0]
        self.assertEqual(self.app.tools.group_participation.decision(context, batch)["mode"], "skip")
        self.assertEqual(self.api.calls, [])
        self.native.provider.client.state.behavior = None
        later, reply = await self.receive_parts([Plain("刚才发了什么")], sender="201")
        current = self.request(later, reply)
        self.assertEqual(current.images, ())
        history = [json.loads(m.payload) for m in current.context.materials if m.purpose == "group_conversation_history"]
        self.assertEqual(history[0]["attachments"][0]["view"], "historical_metadata_only")
        self.assertEqual(self.request(context, batch).images[0], original)
        self.assertEqual(len(self.api.calls), 1)


if __name__ == "__main__":
    unittest.main()
