"""Collective memory through real host intake, model tools, delivery and recovery."""

import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.group_authority import GroupAuthority
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.execution.models import TaskContext, TaskError, encode
from companion_v2.memory.group.access import owner_key
from companion_v2.memory.group.service import KIND
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch


class GroupMemoryTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.sequence = 0

    async def receive(self, text="记住我的群内发言", *, sender="200", commands=(), behavior=None, **options):
        async def respond(payload, tools):
            done = len([m for m in payload["messages"] if m["role"] == "tool"])
            if done < len(commands):
                action, arguments = commands[done]
                return completion(tools=[NS(id="command-" + str(done), type="function",
                    function=NS(name=tool_alias(action), arguments=encode(arguments)))])
            return completion(final_text("已处理本轮请求。"))
        self.native.provider.client.state.behavior = behavior or respond
        self.sequence += 1
        event = self.group_event(sender=sender, identifier="group-memory-" + str(self.sequence), text=text, **options)
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch_id = result.unwrap()
        await wait_for_batch(self.controller.session, batch_id)
        app = self.controller.session.application
        context = TaskContext.restore(json.loads(app.messages.batch(batch_id)["context_json"]))
        return context, batch_id

    @property
    def app(self):
        return self.controller.session.application

    @property
    def memory(self):
        return self.app.messages.group_memory

    def enable_group_reactions(self):
        semantic = self.app.tools.semantic
        semantic.store.configure({"reactions_group_enabled": True}, semantic.store.settings()["revision"])

    def command_results(self, batch):
        return [json.loads(row["result_json"]) for row in self.app.tools._rows(batch)]

    async def save(self, statement="周六在公园集合", **kwargs):
        context, batch = await self.receive(statement, commands=[("group.memory.remember",
            {"input_handle": "group-input-1", "statement": statement})], **kwargs)
        result = self.command_results(batch)
        self.assertTrue(result, self.app.flow.row(batch))
        self.assertEqual(result[-1]["state"], "succeeded", result)
        self.assertEqual(self.app.messages.batch(batch)["state"], "ready", self.app.flow.row(batch))
        return context, batch, result[-1]["payload"]

    def materials(self, context, batch):
        return self.app.messages._request(self.app.messages.batch(batch), context, self.clock.now()).context.materials

    async def test_member_statement_is_shared_and_grounded_without_private_source_access(self):
        await self.start_host()
        self.enable_group_reactions()
        a, first, receipt = await self.save()
        b, second = await self.receive("刚才的集合安排是什么", sender="201")
        material = next(m for m in self.materials(b, second) if m.purpose == "group_memory")
        item = json.loads(material.payload)
        self.assertEqual((item["speaker_id"], item["attribution"], item["visibility"]), ("200", "member_statement", "group"))
        self.assertEqual(item["memory_id"], receipt["memory_id"])
        self.assertEqual(owner_key(a), owner_key(b))
        direct = self.app.messages.sources.read(material.source, resolved=b.actor, capability="text",
            observed_at=self.clock.now(), authorizer=self.app.tasks.identity)
        self.assertEqual(direct.failure.code, "source_audience_denied")
        self.assertEqual({t.action for t in self.app.tools.catalog_for(b)},
                         self.app.tools.group_memory.actions | self.app.tools.group_participation.actions
                         | self.app.tools.acquaintance.actions
                         | {"reaction.search", "slang.search", "slang.learn"})
        private, private_batch = await self.accepted(identifier="private-after-group", text="回到私聊")
        self.assertFalse(any(m.owner == "memory.group" for m in self.materials(private, private_batch)))
        with self.assertRaises(TaskError):
            self.memory.read(private, receipt["memory_id"], 1)
        sealed = json.loads(self.app.messages.batch(second)["contributions_json"])["group_memory"]
        self.assertNotIn("周六", encode(sealed))
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0], 0)

    async def test_only_original_member_can_revise_or_retract_and_receipts_keep_identity(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        a, first, receipt = await self.save()
        args = {"input_handle": "group-input-1", "memory_id": receipt["memory_id"], "expected_revision": 1}
        for action, extras in (("group.memory.remember", {"statement": "替别人改成周日"}), ("group.memory.retract", {})):
            _, batch = await self.receive(sender="201", commands=[(action, args | extras)])
            self.assertEqual(self.command_results(batch)[0]["code"], "group_memory_author_required")
        a, corrected = await self.receive("改成周日", commands=[("group.memory.remember", args | {"statement": "周日在公园集合"})])
        result = self.command_results(corrected)[0]
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(self.app.messages.batch(corrected)["state"], "ready", self.app.flow.row(corrected))
        self.assertEqual(self.memory.read(a, receipt["memory_id"], 2)[0]["statement"], "周日在公园集合")
        _, retracted = await self.receive("撤回这条群记忆", commands=[("group.memory.retract", args | {"expected_revision": 2})])
        self.assertEqual(self.command_results(retracted)[0]["payload"]["state"], "retracted")
        self.assertEqual(self.app.messages.batch(retracted)["state"], "ready", self.app.flow.row(retracted))
        self.assertEqual(self.memory.query(a)["items"], [])
        self.assertEqual(self.memory.operation(a, receipt["operation_id"]), receipt)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 5)

    async def test_query_then_revision_refreshes_old_tool_result_in_same_turn(self):
        await self.start_host()
        _, _, receipt = await self.save()
        context, batch = await self.receive("改成周日", commands=[("group.memory.query", {"query": "公园"}),
            ("group.memory.remember", {"input_handle": "group-input-1", "statement": "周日去公园",
                                       "memory_id": receipt["memory_id"], "expected_revision": 1})])
        self.assertEqual(self.app.messages.batch(batch)["state"], "ready", self.app.flow.row(batch))
        result = self.app.tools.group_memory.validate(self.app.tools._rows(batch)[0], context, self.command_results(batch)[0])[0]
        self.assertEqual(result["state"], "superseded")
        self.assertNotIn("周六", encode(result))

    async def test_group_bot_persona_and_native_member_sessions_are_separate(self):
        await self.start_host()
        a, _, receipt = await self.save()
        for options in (dict(target="group-b"), dict(target="group-c"), dict(unique=True), dict(unique=True, sender="201")):
            current, batch = await self.receive(**options)
            self.assertEqual(self.memory.query(current)["items"], [])
            with self.assertRaises(TaskError):
                self.memory.read(current, receipt["memory_id"], 1)
        self.native.selections["qq-main:GroupMessage:300"] = "second"
        b, _ = await self.receive()
        self.assertEqual(self.memory.query(b)["items"], [])
        self.native.selections["qq-main:GroupMessage:300"] = "original"
        self.native.conversations["qq-main:GroupMessage:300"] = "reset-native-conversation"
        returned, batch = await self.receive(sender="201")
        self.assertNotEqual(returned.scope.conversation_id, a.scope.conversation_id)
        self.assertEqual(self.memory.query(returned)["items"][0]["memory_id"], receipt["memory_id"])
        self.assertTrue(any(m.purpose == "group_memory" for m in self.materials(returned, batch)))

    async def test_source_withdrawal_hides_memory_and_derived_replies_keeps_send_receipts(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        a, first, receipt = await self.save()
        b, second = await self.receive(sender="201", commands=[("group.memory.query", {})])
        receipts = [tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        event = self.app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (first,))
        self.app.withdraw(a, event[0]).unwrap()
        self.assertEqual(self.memory.query(b)["items"], [])
        with self.assertRaises(TaskError):
            self.materials(b, second)
        self.assertEqual([tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())], receipts)
        c, third = await self.receive(sender="202")
        self.assertFalse(any(m.purpose == "group_memory" for m in self.materials(c, third)))
        self.assertFalse(any(m.purpose == "group_conversation_history" and json.loads(m.payload)["type"] == "confirmed_group_reply"
                             for m in self.materials(c, third)))

    async def test_origin_grant_revocation_does_not_block_chat_or_restore_memory_right(self):
        await self.start_host()
        self.enable_group_reactions()
        a, _, receipt = await self.save()
        identity = self.controller.authority.identities
        grant = GroupAuthority.grant_id(a.scope, a.actor.subject, "group.memory.write")
        identity.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identity.revision)
        current, batch = await self.receive()
        self.assertEqual(self.app.messages.batch(batch)["state"], "ready")
        self.assertEqual({t.action for t in self.app.tools.catalog_for(current)},
                         {"group.memory.query", "group.memory.legacy.query", "group.memory.operation", "reaction.search", "slang.search"}
                         | self.app.tools.group_participation.actions
                         | self.app.tools.acquaintance.actions)
        b, _ = await self.receive(sender="201")
        self.assertEqual(self.memory.query(b)["items"], [])
        self.assertFalse(identity.authorize(current.actor, scope=a.scope, capability="group.memory.write", observed_at=self.clock.now()).is_success)

    async def test_restarts_keep_memory_and_do_not_repeat_unknown_delivery_or_writes(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        api = self.native.platform.client._api._wsr_api
        api.error = TimeoutError()
        a, first, receipt = await self.save()
        event = self.group_event(identifier="group-memory-1", text="周六在公园集合")
        await self.controller.terminate()
        api.error = None
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        self.assertTrue((await self.controller.load_existing()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), first)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(self.memory.operation(a, receipt["operation_id"]), receipt)
        b, _ = await self.receive(sender="201")
        self.assertEqual(self.memory.query(b)["items"][0]["memory_id"], receipt["memory_id"])
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_views WHERE kind = ?", (KIND,))[0], 1)

    async def test_foreign_handles_identity_arguments_and_receipts_cannot_be_injected(self):
        await self.start_host()
        a, _, receipt = await self.save()
        for arguments in ({"input_handle": "another-turn", "statement": "伪造"},
                          {"input_handle": "group-input-1", "statement": "伪造", "speaker_id": "200"}):
            _, batch = await self.receive(sender="201", commands=[("group.memory.remember", arguments)])
            self.assertEqual(self.command_results(batch)[0]["state"], "failed")
        b, batch = await self.receive(sender="201", commands=[("group.memory.operation", {"operation_id": receipt["operation_id"]})])
        self.assertEqual(self.command_results(batch)[0]["code"], "group_memory_operation_unavailable")
        with self.assertRaises(TaskError):
            self.memory.operation(b, receipt["operation_id"])

    async def test_recalled_statement_can_be_cited_as_evidence_in_a_real_model_wire(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        await self.save()
        async def grounded(payload, tools):
            materials = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "user"))["materials"]
            note = next(m for m in materials if m["purpose"] == "group_memory")
            self.assertTrue(note["evidence"])
            return completion(final_text("200 刚才提到周六在公园集合。", evidence=note["evidence"]))
        context, batch = await self.receive(sender="201", behavior=grounded)
        part = self.app.runtime.read_one("SELECT state, text FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "delivered", self.app.flow.row(batch))
        self.assertIn("200", part["text"])

    async def test_withdrawal_during_model_use_blocks_delivery_and_preserves_memory_receipt(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        a, first, receipt = await self.save()
        source_key = self.app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (first,))[0]
        async def revoked(payload, tools):
            self.app.withdraw(a, source_key).unwrap()
            return completion(final_text("不能发送这个过期结论。"))
        b, batch = await self.receive(sender="201", behavior=revoked)
        self.assertEqual(self.app.flow.row(batch)["state"], "blocked")
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,)), ())
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)
        self.assertEqual(self.memory.operation(a, receipt["operation_id"]), receipt)

    async def test_query_pagination_and_frozen_snapshot_keep_original_ids_after_new_saves(self):
        await self.start_host()
        a, first = await self.receive("三个群内记录", commands=[("group.memory.remember",
            {"input_handle": "group-input-1", "statement": "群内记录" + str(i)}) for i in range(3)])
        self.assertTrue(all(r["state"] == "succeeded" for r in self.command_results(first)))
        found, cursor = [], ""
        while cursor is not None:
            page = self.memory.query(a, query="群内记录", cursor=cursor, limit=1)
            found.extend(item["memory_id"] for item in page["items"])
            cursor = page["next_cursor"]
        self.assertEqual(len(set(found)), 3)
        b, frozen = await self.receive(sender="201")
        snapshot = self.app.messages.batch(frozen)["contributions_json"]
        old = [m.payload for m in self.materials(b, frozen) if m.purpose == "group_memory"]
        await self.save("后来才保存的记录")
        self.assertEqual(self.app.messages.batch(frozen)["contributions_json"], snapshot)
        self.assertEqual([m.payload for m in self.materials(b, frozen) if m.purpose == "group_memory"], old)

    async def test_same_operation_replays_and_conflicting_arguments_do_not_write(self):
        await self.start_host()
        a, first, receipt = await self.save()
        replay = self.memory.change(a, first, "command-0", action="remember", input_handle="group-input-1", statement="周六在公园集合")
        self.assertEqual(replay, receipt)
        with self.assertRaises(TaskError):
            self.memory.change(a, first, "command-0", action="remember", input_handle="group-input-1", statement="不同的内容")
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_views WHERE kind = ?", (KIND,))[0], 1)

    async def test_read_right_can_be_revoked_without_disabling_ordinary_group_chat(self):
        await self.start_host()
        self.enable_group_reactions()
        a, _, _ = await self.save()
        identity = self.controller.authority.identities
        grant = GroupAuthority.grant_id(a.scope, a.actor.subject, "group.memory.read")
        identity.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identity.revision)
        a, batch = await self.receive()
        self.assertEqual(self.app.messages.batch(batch)["state"], "ready")
        self.assertEqual({t.action for t in self.app.tools.catalog_for(a)},
                         self.app.tools.group_participation.actions | self.app.tools.acquaintance.actions
                         | {"reaction.search", "slang.learn"})
        self.assertFalse(any(m.purpose == "group_memory" for m in self.materials(a, batch)))

    async def test_retraction_prevents_reusing_the_same_source_under_another_call_id(self):
        await self.start_host()
        async def respond(payload, tools):
            done = [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]
            if not done:
                action, args = "group.memory.remember", {"input_handle": "group-input-1", "statement": "临时说法"}
            elif len(done) == 1:
                action, args = "group.memory.retract", {"input_handle": "group-input-1", "memory_id": done[0]["payload"]["memory_id"], "expected_revision": 1}
            elif len(done) == 2:
                action, args = "group.memory.remember", {"input_handle": "group-input-1", "statement": "试图换编号恢复"}
            else:
                return completion(final_text())
            return completion(tools=[NS(id="command-" + str(len(done)), type="function",
                function=NS(name=tool_alias(action), arguments=encode(args)))])
        a, batch = await self.receive(behavior=respond)
        results = self.command_results(batch)
        self.assertEqual(len(results), 3, self.app.flow.row(batch))
        self.assertEqual(results[2]["code"], "group_memory_evidence_retracted")
        self.assertEqual(self.memory.query(a)["items"], [])
        self.assertEqual(self.app.messages.batch(batch)["state"], "ready", self.app.flow.row(batch))

    async def test_failed_source_publication_rolls_back_group_memory_and_receipt(self):
        await self.start_host()
        from companion_v2.foundation.outcome import Outcome
        original = self.memory.sources.publish
        def failing(record, **kwargs):
            if record.reference.owner == "memory.group":
                return Outcome.failed("synthetic_write_failure", "test failure")
            return original(record, **kwargs)
        with patch.object(self.memory.sources, "publish", failing):
            context, batch = await self.receive(commands=[("group.memory.remember",
                {"input_handle": "group-input-1", "statement": "事务中的群记忆"})])
        self.assertEqual(self.command_results(batch)[0]["code"], "synthetic_write_failure")
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_views WHERE kind = ?", (KIND,))[0], 0)
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM operations WHERE owner = 'memory.group'", ())[0], 0)

    async def test_oversized_statement_is_rejected_without_partial_save(self):
        await self.start_host()
        context, batch = await self.receive(commands=[("group.memory.remember",
            {"input_handle": "group-input-1", "statement": "长" * 1025})])
        self.assertEqual(self.command_results(batch)[0]["code"], "invalid_tool_arguments")
        self.assertEqual(self.memory.query(context)["items"], [])


if __name__ == "__main__":
    unittest.main()
