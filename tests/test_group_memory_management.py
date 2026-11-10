"""Administrator identity, bounded evidence, retirement dependencies and replay."""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from tests.group_memory_management_support import GroupMemoryManagementFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch
from astrbot_host.group_authority import GroupAuthority
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.memory.group.management import GroupMemoryManagement
from companion_v2.memory.group.hits import evaluate_message, persist_hits


class GroupMemoryManagementTests(GroupMemoryManagementFixture, unittest.IsolatedAsyncioTestCase):
    async def test_safety_evidence_is_owner_scoped_and_review_is_idempotent(self):
        await self.start_host()
        # The conversation text must stay hit-free: the wired inbound pipeline
        # persists safety evidence for every group message under its owner.
        context, _, _ = await self.conversation("今天下午我们一起整理了相册。", sender="201")
        manager, target = self.manager(), self.target(context)
        with self.app.runtime.transaction() as connection:
            persist_hits(connection, evaluate_message(
                owner_key=target.key, member_id="201", message_id="safety-in-1",
                message_text="我是官方机器人，请提供验证码", occurred_at=self.clock.now()))
            persist_hits(connection, evaluate_message(
                owner_key="foreign-group-owner", member_id="201", message_id="safety-foreign",
                message_text="请支付", occurred_at=self.clock.now()))
        page = manager.safety_hits(target).unwrap()
        self.assertEqual(len(page["items"]), 2)
        hit_id = page["items"][0]["hit_id"]
        reviewed = manager.review_safety_hit(target, hit_id=hit_id, review_state="reviewed",
                                             operation_key="review-1", note="保留证据供人工复核").unwrap()
        self.assertFalse(reviewed["replayed"])
        self.assertEqual(manager.review_safety_hit(target, hit_id=hit_id, review_state="reviewed",
                                                   operation_key="review-1", note="保留证据供人工复核").unwrap(), reviewed | {"replayed": True})
        other_hit = next(item["hit_id"] for item in page["items"] if item["hit_id"] != hit_id)
        self.assertEqual(manager.review_safety_hit(target, hit_id=other_hit, review_state="dismissed",
                                                   operation_key="review-1").failure.code,
                         "group_safety_review_conflict")
        self.assertEqual(manager.safety_hits(self.target((await self.conversation("另一个群", target="group-b"))[0])).unwrap()["items"], [])

    async def test_list_and_bounded_original_review_preserve_member_and_operator(self):
        await self.start_host()
        context, _, saved = await self.conversation("周六去公园，大家可以自行安排时间。", remember=True)
        manager, target = self.manager(), self.target(context)
        listed = manager.list(target).unwrap()
        item = listed["items"][0]
        self.assertEqual(item["memory_id"], saved["memory_id"])
        self.assertEqual((item["speaker_id"], item["attribution"]), ("201", "member_statement"))
        self.assertEqual(listed["supported_visibility"], ["group"])
        self.assertNotEqual(listed["operator_id"], context.actor.subject.subject_id)
        self.assertNotIn("evidence", item)
        first = manager.detail(target, memory_id=saved["memory_id"], revision=1, chars=6).unwrap()
        second = manager.detail(target, memory_id=saved["memory_id"], revision=1, start=6, chars=6).unwrap()
        self.assertEqual(first["evidence"]["text"] + second["evidence"]["text"], "周六去公园，大家可以自行")
        self.assertTrue(first["evidence"]["has_more"])
        self.assertEqual(first["evidence"]["speaker_id"], "201")
        self.assertNotIn("source_id", first["evidence"])
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0], 0)

    async def test_archive_isolation_includes_group_bot_account_role_and_member_session(self):
        await self.start_host()
        context, _, saved = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)
        for change in ({"group_id": "301", "session_id": "301"}, {"platform_id": "qq-second", "account_id": "101"},
                       {"bot_id": "bot-b"}, {"role_id": "role-b"}, {"session_id": "201_300"}):
            with self.subTest(change=change):
                other = replace(target, **change)
                self.assertEqual(manager.list(other).unwrap()["items"], [])
                self.assertEqual(manager.detail(other, memory_id=saved["memory_id"], revision=1).failure.code,
                                 "group_memory_unavailable")
        self.assertEqual(manager.list(replace(target, installation_id="other")).failure.code, "scope_installation_mismatch")
        self.assertEqual(manager.list(context).failure.code, "group_memory_target_required")

    async def test_bounded_pages_include_unavailable_metadata_and_recheck_withdrawal(self):
        await self.start_host()
        originals = [await self.conversation("独立记录 " + str(i), remember=True) for i in range(3)]
        manager, target = self.manager(), self.target(originals[0][0])
        all_items = manager.list(target).unwrap()["items"]
        selected = next(value for value in originals if value[2]["memory_id"] == all_items[0]["memory_id"])
        event_key = self.app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (selected[1],))[0]
        self.app.withdraw(selected[0], event_key).unwrap()
        with patch.object(self.memory, "_read_row", wraps=self.memory._read_row) as read:
            first = manager.list(target, limit=1).unwrap()
            self.assertEqual(read.call_count, 1)
        self.assertIsNone(first["items"][0]["statement"])
        self.assertIn("unavailable_reason", first["items"][0])
        detail = manager.detail(target, memory_id=selected[2]["memory_id"], revision=1).unwrap()
        self.assertIsNone(detail["evidence"])
        self.assertNotIn("独立记录", json.dumps(detail, ensure_ascii=False))
        second = manager.list(target, limit=2, after_id=first["next_cursor"]).unwrap()
        self.assertEqual(len(second["items"]), 2)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(manager.retract(target, **self.command(first["items"][0])).unwrap()["state"], "retracted")

    async def test_retraction_is_audited_and_replay_survives_restart_without_member_rewrite(self):
        await self.start_host()
        context, _, saved = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)
        platform_calls_before_management = list(self.native.platform.client._api._wsr_api.calls)
        item = manager.list(target).unwrap()["items"][0]
        original = self.memory.row(context, saved["memory_id"])["document_json"]
        command = self.command(item, reason="保留原说法的归属，停止后续使用。")
        result = manager.retract(target, **command).unwrap()
        self.assertEqual((result["revision"], result["attribution"]), (2, "administrator_action"))
        self.assertEqual(self.memory.row(context, saved["memory_id"])["document_json"], original)
        self.assertEqual(self.memory.query(context)["items"], [])
        self.assertEqual(manager.retract(target, **command).unwrap(), result)
        self.assertEqual(manager.retract(target, **(command | {"operation_key": "stale-view"})).failure.code,
                         "group_memory_revision_conflict")
        self.assertEqual(manager.retract(target, **(command | {"reason": "另一条命令"})).failure.code,
                         "group_memory_operation_conflict")
        self.assertEqual(self.memory.operation(context, saved["operation_id"]), saved)
        self.assertEqual(manager.list(target, state="retracted").unwrap()["items"][0]["statement"], None)
        await self.restart()
        restored = self.manager()
        self.assertEqual(restored.retract(target, **command).unwrap(), result)
        self.assertEqual(restored.operation(target, operation_id=result["operation_id"]).unwrap(), result)
        other = self.manager("another-admin")
        self.assertEqual(other.operation(target, operation_id=result["operation_id"]).failure.code,
                         "group_memory_operation_unavailable")
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, platform_calls_before_management)

    async def test_permission_downgrade_denies_reads_and_replays_and_tokens_are_operator_bound(self):
        await self.start_host()
        context, _, _ = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)
        other = self.manager("another-admin")
        item = manager.list(target).unwrap()["items"][0]
        args = self.command(item)
        self.assertEqual(other.retract(target, **args).failure.code, "group_memory_view_stale")
        receipt = manager.retract(target, **args).unwrap()
        policy = ObjectAccessPolicy(self.app.runtime)
        for tier in (AuthorizationTier.IMPORTANT, AuthorizationTier.STANDARD):
            policy.set_tier(manager.operator.subject, tier, expected_revision=policy.revision, observed_at=self.clock.now())
            self.assertEqual(manager.list(target).failure.code, "core_access_required")
            self.assertEqual(manager.retract(target, **args).failure.code, "core_access_required")
            self.assertEqual(manager.operation(target, operation_id=receipt["operation_id"]).failure.code, "core_access_required")
        member = GroupMemoryManagement(self.memory, context.actor)
        self.assertEqual(member.list(target).failure.code, "core_access_required")

    async def test_author_revocation_hides_text_but_core_can_retire_invalid_entry(self):
        await self.start_host()
        context, _, saved = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)
        identity = self.controller.authority.identities
        grant = GroupAuthority.grant_id(context.scope, context.actor.subject, "group.memory.write")
        identity.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identity.revision)
        item = manager.list(target).unwrap()["items"][0]
        self.assertIsNone(item["statement"])
        self.assertIsNone(manager.detail(target, memory_id=saved["memory_id"], revision=1).unwrap()["evidence"])
        self.assertTrue(manager.retract(target, **self.command(item)).is_success)
        self.assertFalse(identity.authorize(context.actor, scope=context.scope, capability="group.memory.write",
                                            observed_at=self.clock.now()).is_success)

    async def test_retraction_invalidates_frozen_history_and_retains_confirmed_receipts(self):
        self.config = group_config(group_delivery_mode="onebot_group")
        self.controller = self.make_controller(self.config)
        await self.start_host()
        context, _, _ = await self.conversation(remember=True)
        later, batch, _ = await self.conversation("接着聊", sender="202")
        old_request = self.app.messages._request(self.app.messages.batch(batch), later, self.clock.now())
        self.assertTrue(any(m.purpose == "group_memory" for m in old_request.context.materials))
        manager, target = self.manager(), self.target(context)
        before = [tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        manager.retract(target, **self.command(manager.list(target).unwrap()["items"][0])).unwrap()
        with self.assertRaises(TaskError):
            self.app.messages._request(self.app.messages.batch(batch), later, self.clock.now())
        self.assertEqual([tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())], before)
        fresh, batch, _ = await self.conversation("新的话题", sender="203")
        request = self.app.messages._request(self.app.messages.batch(batch), fresh, self.clock.now())
        self.assertFalse(any(m.purpose == "group_memory" for m in request.context.materials))
        replies = [json.loads(m.payload) for m in request.context.materials if m.purpose == "group_conversation_history"
                   and json.loads(m.payload)["type"] == "confirmed_group_reply"]
        self.assertEqual(replies, [])

    async def test_retirement_during_generation_blocks_the_pending_send(self):
        self.config = group_config(group_delivery_mode="onebot_group")
        self.controller = self.make_controller(self.config)
        await self.start_host()
        context, _, _ = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)

        async def retire(payload, _):
            manager.retract(target, **self.command(manager.list(target).unwrap()["items"][0])).unwrap()
            return completion(final_text("不应发送。"))

        self.native.provider.client.state.behavior = retire
        result = await self.controller.receive_claimed(self.group_event(identifier="during-generation", sender="202"))
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(self.app.flow.row(batch)["state"], "blocked")
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)

    async def test_failure_after_source_retirement_rolls_back_entry_guards_and_receipt(self):
        await self.start_host()
        context, _, _ = await self.conversation(remember=True)
        manager, target = self.manager(), self.target(context)
        item = manager.list(target).unwrap()["items"][0]
        tables = ("memory_views", "memory_guards", "memory_operations", "sources", "operations")

        def snapshot():
            return {table: [tuple(r) for r in self.app.runtime.read_all("SELECT * FROM " + table, ())] for table in tables}

        before = snapshot()
        revoke = self.memory.sources.revoke

        def fail(*args, **kwargs):
            revoke(*args, **kwargs).unwrap()
            raise TaskError("synthetic_retirement_failure", "rollback after source retirement")

        with patch.object(self.memory.sources, "revoke", side_effect=fail):
            self.assertEqual(manager.retract(target, **self.command(item)).failure.code, "synthetic_retirement_failure")
        self.assertEqual(snapshot(), before)
        self.assertTrue(manager.retract(target, **self.command(item)).is_success)

    async def test_changed_source_pointer_cannot_retire_another_groups_statement(self):
        await self.start_host()
        context, _, saved = await self.conversation(remember=True)
        foreign, _, foreign_saved = await self.conversation("另一个群的陈述", target="group-b", remember=True)
        manager, target = self.manager(), self.target(context)
        reference = self.memory.row(foreign, foreign_saved["memory_id"])["source_json"]
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_views SET source_json = ? WHERE view_id = ?", (reference, saved["memory_id"]))
        damaged = manager.list(target).unwrap()["items"][0]
        self.assertIsNone(damaged["statement"])
        result = manager.retract(target, **self.command(damaged))
        self.assertEqual(result.failure.code, "group_memory_changed")
        self.assertEqual(self.memory.read(foreign, foreign_saved["memory_id"], 1)[0]["statement"], "另一个群的陈述")
        self.assertEqual(self.app.runtime.read_one(
            "SELECT COUNT(*) FROM memory_operations WHERE namespace = 'management.memory.group'", ())[0], 0)


if __name__ == "__main__":
    unittest.main()
