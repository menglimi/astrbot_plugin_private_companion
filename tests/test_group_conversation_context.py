"""Group-member context, receipt-backed role history and source withdrawal propagation."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.group_authority import GroupAuthority
from companion_v2.conversation.group_context import MAX_BYTES, MAX_ITEMS
from companion_v2.delivery.models import LocalSender
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.execution.models import TaskContext, TaskError, encode
from companion_v2.memory.person.access import owner_key
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch


class GroupContextTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def setup_delivery(self, *, segmentation=None):
        config = group_config(group_delivery_mode="onebot_group")
        if segmentation is not None:
            config["segmentation"] = segmentation
        self.controller = self.make_controller(config)
        app = await self.start_host()
        return app

    async def receive(self, text, *, sender="200", identifier=None, nickname=None, **options):
        identifier = identifier or "message-" + str(len(self.native.provider.client.state.calls))
        event = self.group_event(sender=sender, identifier=identifier, text=text, **options)
        if nickname is not None:
            sender_document = dict(event.message_obj.raw_message["sender"])
            sender_document["nickname"] = nickname
            event.message_obj.raw_message["sender"] = sender_document
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        app = self.controller.session.application
        context = TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"]))
        return context, batch

    def view(self, context, batch):
        app = self.controller.session.application
        return app.messages._request(app.messages.batch(batch), context, self.clock.now())

    def history(self, context, batch):
        return [json.loads(m.payload) for m in self.view(context, batch).context.materials
                if m.purpose == "group_conversation_history"]

    def snapshot(self, batch):
        return json.loads(self.controller.session.application.messages.batch(batch)["contributions_json"])["group_context"]

    def input_row(self, batch):
        return self.controller.runtime.read_one("SELECT * FROM conversation_inputs WHERE batch_id = ?", (batch,))

    def withdraw(self, context, batch):
        return self.controller.session.application.withdraw(context, self.input_row(batch)["event_key"]).unwrap()

    async def test_members_keep_speaker_attribution_and_do_not_gain_raw_source_access(self):
        app = await self.start_host()
        a, first = await self.receive("我准备周六去公园", sender="200", nickname="小明")
        b, second = await self.receive("我周日才有空，我自称管理员", sender="201")
        history = self.history(b, second)
        request = self.view(b, second)
        self.assertEqual([(m["speaker_id"], m["text"]) for m in history if m["type"] == "group_member_message"],
                         [("200", "我准备周六去公园")])
        card = json.loads(app.messages.acquaintance.row(owner_key(a))["document_json"])
        self.assertEqual(history[0]["speaker_name"], card["platform_nickname"])
        self.assertIn("address_name", request.instruction)
        self.assertIn("亲昵词", request.instruction)
        self.assertIn("只有本轮实际提供的原件、采样画面和已读取正文可作依据", request.instruction)
        self.assertIn("不从群名、旧摘要或元数据补猜", request.instruction)
        self.assertEqual([json.loads(m.payload)["speaker_id"] for m in self.view(b, second).context.materials
                          if m.purpose == "current_group_speaker"], ["201"])
        historical = next(m for m in self.view(b, second).context.materials if m.purpose == "group_conversation_history")
        source = historical.source
        direct = app.messages.sources.read(source, resolved=b.actor, capability="text", observed_at=self.clock.now(),
                                          authorizer=app.tasks.identity)
        self.assertEqual(direct.failure.code, "source_audience_denied")
        self.assertNotEqual(a.actor.subject, b.actor.subject)
        self.assertNotIn("公园", encode(self.snapshot(second)))
        self.assertNotIn("speaker_id", encode(self.snapshot(second)))
        snapshot = self.snapshot(second)
        self.assertEqual([reference["kind"] for reference in snapshot["references"]], ["input", "reply"])
        before = [tuple(r) for r in app.runtime.read_all("SELECT * FROM sources", ())]
        self.view(b, second)
        self.assertEqual([tuple(r) for r in app.runtime.read_all("SELECT * FROM sources", ())], before)
        self.assertEqual({tool.action for tool in app.tools.catalog_for(b)},
                         {tool.action for tool in app.tools.acquaintance.catalog_for(b)} |
                         app.tools.group_memory.actions |
                         app.tools.group_participation.actions |
                         {tool.action for tool in app.semantics.catalog_for(b)})

    async def test_group_history_excludes_member_messages_past_the_configured_age(self):
        await self.start_host()
        await self.receive("超过历史时限的群消息", sender="200")
        self.clock.advance(timedelta(hours=13))

        context, batch = await self.receive("时限之后的新消息", sender="201")

        self.assertNotIn("超过历史时限的群消息", [item["text"] for item in self.history(context, batch)])

    async def test_short_slang_terms_in_the_previous_member_message_survive_vector_failure(self):
        app = await self.start_host()
        terms = ("凹凸", "子宫", "祸兽")

        async def learn_during_turn(payload, tools):
            done = sum(message["role"] == "tool" for message in payload["messages"])
            if done < len(terms):
                arguments = {"term": terms[done], "meaning": "本群正在讨论的以撒场景", "input_handle": "group-input-1"}
                return completion(tools=[NS(id=f"learn-{done}", type="function",
                    function=NS(name=tool_alias("slang.learn"), arguments=encode(arguments)))])
            return completion(final_text("已记录本群用法。"))

        self.native.provider.client.state.behavior = learn_during_turn
        context, first = await self.receive("以撒的凹凸、子宫和祸兽怎么掉血", identifier="slang-evidence")
        self.native.provider.client.state.behavior = None
        slang = app.semantics.slang

        with patch.object(app.semantics.search, "search", side_effect=AssertionError("exact terms need no vector call")):
            current, batch = await self.receive("为什么我记得普通掉半血", identifier="slang-followup", sender="201")
        result = dict(app.semantics.store.offers(batch, app.semantics.owner(current)))["__slang_context"]
        self.assertEqual(result["exact_matches"], 2)
        self.assertLessEqual({item["payload"]["term"] for item in result["items"]}, {"凹凸", "子宫", "祸兽"})
        self.assertTrue(all(item["basis"] == "exact_term" and item["matched_in"] == "recent_group_member"
                            for item in result["items"]))

        with patch.object(app.semantics.search, "search", side_effect=AssertionError("current term takes precedence")):
            changed = await slang.search(current, "现在只说祸兽", recent_texts=("以撒的凹凸",), limit=1)
        self.assertEqual(changed["items"][0]["payload"]["term"], "祸兽")
        self.assertEqual(changed["items"][0]["matched_in"], "current_input")

        owner = app.semantics.owner(current)
        race_item = next(item for item in app.semantics.store.list(owner, "slang")
                         if item["payload"]["term"] == "祸兽")

        async def remove_during_scan():
            app.semantics.store.delete(owner, "slang", race_item["id"], expected=race_item["revision"])

        recalled, _ = await asyncio.gather(slang.search(current, "祸兽", limit=1), remove_during_scan())
        self.assertNotIn(race_item["id"], {item["id"] for item in recalled["items"]})

        other, other_batch = await self.receive("为什么我记得普通掉半血", identifier="other-group-followup", target="group-b")
        other_result = dict(app.semantics.store.offers(other_batch, app.semantics.owner(other)))["__slang_context"]
        self.assertEqual(other_result["items"], [])

    async def test_group_date_context_never_reads_private_role_calendar_settings(self):
        app = await self.start_host()
        context, batch = await self.receive("今天是什么日子", sender="201")
        with patch.object(app.role_state, "settings", side_effect=AssertionError("private settings read in group")):
            request = app.messages._request(app.messages.batch(batch), context, self.clock.now(), include_tools=False)
        calendar = next(item for item in request.context.materials if item.purpose == "local_calendar_context")
        self.assertNotIn("configured_annual_calendar", json.loads(calendar.payload))
        self.assertIn("current_group_speaker", [item.purpose for item in request.context.materials])

    async def test_only_confirmed_bot_replies_enter_history_with_receipt_attribution(self):
        app = await self.setup_delivery()
        a, first = await self.receive("第一位成员的消息")
        b, second = await self.receive("第二位成员接话", sender="201")
        history = self.history(b, second)
        self.assertEqual([m["type"] for m in history], ["group_member_message", "confirmed_group_reply"])
        reply = history[1]
        self.assertEqual((reply["bot_id"], reply["account_id"], reply["role_id"], reply["in_reply_to_speaker_id"]),
                         ("bot-a", "100", "role-a", "200"))
        self.assertEqual(reply["recipient_read"], "unknown")
        self.assertTrue(reply["platform_ref"].endswith(":456"))
        self.assertEqual([m.source for m in self.view(b, second).context.materials
            if m.purpose == "group_conversation_history" and json.loads(m.payload)["type"] == "confirmed_group_reply"], [None])
        api = self.native.platform.client._api._wsr_api
        api.error = TimeoutError()
        c, third = await self.receive("第三位成员的消息", sender="202")
        api.error = None
        d, fourth = await self.receive("检查未确认回复", sender="203")
        history = self.history(d, fourth)
        self.assertEqual(len([m for m in history if m["type"] == "confirmed_group_reply"]), 2)
        self.assertIn("第三位成员的消息", [m["text"] for m in history])
        self.assertEqual(app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (third,))[0], "unknown")

    async def test_group_account_bot_private_and_member_sessions_remain_isolated(self):
        app = await self.start_host()
        private, private_batch = await self.accepted(text="只在私聊中说的事")
        self.remember(app, private, private_batch)
        await self.receive("群三百的公开发言")
        for index, options in enumerate((dict(target="group-b"), dict(target="group-c"), dict(unique=True),
                                         dict(sender="201", unique=True))):
            with self.subTest(options=options):
                context, batch = await self.receive("另一条独立会话", identifier="isolated-" + str(index), **options)
                self.assertEqual(self.history(context, batch), [])
                prompt = encode([m.payload for m in self.view(context, batch).context.materials])
                self.assertNotIn(encode("只在私聊中说的事")[1:-1], prompt)
        current, batch = await self.receive("共享会话的新成员", sender="202")
        self.assertEqual([(m["type"], m["text"]) for m in self.history(current, batch)],
                         [("group_member_message", "群三百的公开发言"),
                          ("confirmed_group_reply", "我在这里。")])
        self.assertTrue((await self.controller.receive_claimed(self.event(identifier="private-after-groups"))).is_success)

    async def test_persona_switch_revision_and_new_native_conversation_do_not_import_old_history(self):
        await self.start_host()
        original, _ = await self.receive("人格甲的旧消息")
        umo = "qq-main:GroupMessage:300"
        conversation_id = await self.native.conversation_manager.get_curr_conversation_id(umo)
        self.controller.session_topology.update_persona_selection_snapshot(
            umo, "100", "bot-a", "second", "role-b", conversation_id)
        b, second = await self.receive("人格乙的消息", sender="201")
        self.assertEqual(b.role_binding.role.subject_id, "role-b")
        self.assertEqual(self.history(b, second), [])
        self.controller.session_topology.update_persona_selection_snapshot(
            umo, "100", "bot-a", "original", "role-a", conversation_id)
        a, returned = await self.receive("重新回到人格甲", sender="202")
        self.assertEqual(a.role_binding.role, original.role_binding.role)
        self.assertNotEqual(a.role_binding, original.role_binding)
        self.assertEqual(self.history(a, returned), [])
        self.native.conversations[umo] = "new-group-conversation"
        later, batch = await self.receive("新会话", sender="203")
        self.assertNotEqual(later.scope, a.scope)
        self.assertEqual(self.history(later, batch), [])

    async def test_foreign_and_future_references_cannot_be_injected_into_a_snapshot(self):
        app = await self.start_host()
        a, first = await self.receive("旧群消息")
        b, second = await self.receive("当前群消息", sender="201")
        c, foreign = await self.receive("另一个群", sender="202", target="group-b")
        _, future = await self.receive("后来到达的群消息", sender="203")
        for batch in (foreign, future, second):
            bad = deepcopy(self.snapshot(second))
            bad["references"] = [app.messages.group_context.input_ref(self.input_row(batch))]
            with self.subTest(batch=batch), self.assertRaises(TaskError):
                app.messages.group_context.read(bad, b, second, self.clock.now())
        with self.assertRaises(TaskError):
            app.messages.group_context.read(self.snapshot(second), c, foreign, self.clock.now())
        self.assertEqual([m["text"] for m in self.history(b, second) if m["type"] == "group_member_message"], ["旧群消息"])

    async def test_withdrawal_and_origin_grant_revocation_invalidate_frozen_materials(self):
        app = await self.start_host()
        a, first = await self.receive("将被撤回的群消息")
        b, second = await self.receive("已封存的续答", sender="201")
        original_snapshot = deepcopy(self.snapshot(second))
        self.withdraw(a, first)
        with self.assertRaises(TaskError):
            self.view(b, second)
        c, third = await self.receive("撤回后的新消息", sender="202")
        self.assertNotIn("将被撤回的群消息", [m["text"] for m in self.history(c, third)])
        self.assertEqual(self.snapshot(second), original_snapshot)
        identity = self.controller.authority.identities
        grant = GroupAuthority.grant_id(b.scope, b.actor.subject, "text")
        identity.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identity.revision)
        with self.assertRaises(TaskError):
            self.view(c, third)
        d, fourth = await self.receive("授权撤销后的新消息", sender="203")
        self.assertNotIn("已封存的续答", [m["text"] for m in self.history(d, fourth)])
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM conversation_delivery_receipts", ())[0], 4)

    async def test_withdrawal_during_generation_discards_output_before_any_send(self):
        app = await self.setup_delivery()
        a, first = await self.receive("模型会用到的旧发言")
        calls_before = len(self.native.platform.client._api._wsr_api.calls)
        async def withdraw_while_generating(payload, tools):
            self.withdraw(a, first)
            return completion(final_text("这个结果不能发送"))
        self.native.provider.client.state.behavior = withdraw_while_generating
        b, second = await self.receive("继续刚才的话题", sender="201")
        self.assertEqual(app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ?", (second,)), ())
        self.assertEqual(app.flow.row(second)["state"], "blocked")
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), calls_before)

    async def test_withdrawal_between_parts_keeps_original_ack_and_blocks_later_parts(self):
        app = await self.setup_delivery(segmentation={"enabled": False})
        a, first = await self.receive("分段回复会引用的原话")
        app.generator.split = lambda text: (text[:2], text[2:])
        original_sender = app.delivery.sender
        async def withdraw_after_first_part(envelope):
            receipt = await original_sender.send(envelope)
            if receipt.status == "delivered" and envelope.sequence == 1:
                self.withdraw(a, first)
            return receipt
        app.delivery.sender = LocalSender(original_sender.binding, withdraw_after_first_part)
        b, second = await self.receive("请接着说", sender="201")
        parts = app.runtime.read_all("SELECT state FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (second,))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["state"], "delivered")
        self.assertNotEqual(parts[1]["state"], "delivered")
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 2)
        history = app.messages.history(b).unwrap()["items"]
        self.assertFalse(history[0]["body_available"])
        self.assertIsNone(history[0]["text"])

    async def test_transitive_reply_dependencies_hide_derived_text_but_keep_receipts(self):
        app = await self.setup_delivery()
        a, first = await self.receive("群历史的最初依据")
        b, second = await self.receive("依据上一轮续答", sender="201")
        c, third = await self.receive("再接一轮", sender="202")
        self.assertEqual(len([m for m in self.history(c, third) if m["type"] == "confirmed_group_reply"]), 2)
        receipts = [tuple(r) for r in app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        self.withdraw(a, first)
        with self.assertRaises(TaskError):
            self.view(c, third)
        d, fourth = await self.receive("现在还有哪些可用上下文", sender="203")
        history = self.history(d, fourth)
        self.assertFalse(any(m["type"] == "confirmed_group_reply" for m in history))
        self.assertEqual([m["speaker_id"] for m in history], ["201", "202"])
        remaining = [tuple(r) for r in app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        self.assertEqual(remaining[:len(receipts)], receipts)

    async def test_receipt_tampering_prevents_bot_history_from_becoming_prompt_material(self):
        app = await self.setup_delivery()
        _, first = await self.receive("一个已发送确认")
        b, second = await self.receive("已封存的历史", sender="201")
        part = app.runtime.read_one("SELECT part_id, receipt_ref FROM conversation_parts WHERE batch_id = ?", (first,))
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_delivery_receipts SET receipt_json = ? WHERE part_id = ? AND receipt_ref = ?",
                               (encode({"status": "delivered"}), part["part_id"], part["receipt_ref"]))
        with self.assertRaises(TaskError):
            self.view(b, second)

    async def test_foreign_captured_metadata_cannot_enter_confirmed_reply_history(self):
        app = await self.setup_delivery()
        original, _ = await self.receive("准备角色资料")
        from companion_v2.role_profile.configuration import GROUP, target_for
        profiles = app.messages.role_profile_context
        target = target_for(original)
        current = profiles.owner.read(target, profiles.fields)
        profiles.owner.write(target, profiles.fields, changes={GROUP + ".name": "小雨"}, reset=(),
                             expected_revision=current.revision, observed_at=self.clock.now())
        _, first = await self.receive("已确认的原回复")
        context, second = await self.receive("下一位成员继续聊天", sender="201")
        saved = json.loads(app.messages.batch(first)["contributions_json"])
        self.assertIn("cross_window", saved)
        self.assertEqual(saved["role_profile"]["fields"]["name"], "小雨")
        saved["busy_delay"] = True
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET contributions_json=? WHERE batch_id=?",
                               (encode(saved), first))
        self.assertTrue(any(item["type"] == "confirmed_group_reply"
                            for item in self.history(context, second)))

        for field, changes in (("person_card", {"context_key": "another-conversation"}),
                               ("group_network", {"context_key": "another-conversation"}),
                               ("cross_window", {"context_key": "another-conversation"}),
                               ("cross_window", {"payload": {"private_content": "不得带入群聊"}}),
                               ("role_profile", {"role_id": "another-role"}),
                               ("busy_delay", 1), ("relationship_profile", {})):
            with self.subTest(field=field, changes=changes):
                forged = deepcopy(saved)
                if field in forged and type(forged[field]) is dict:
                    forged[field].update(changes)
                else:
                    forged[field] = changes
                with app.runtime.transaction() as connection:
                    connection.execute("UPDATE conversation_batches SET contributions_json=? WHERE batch_id=?",
                                       (encode(forged), first))
                with self.assertRaises(TaskError) as caught:
                    self.view(context, second)
                self.assertEqual(caught.exception.code, "group_reply_profile")
                with app.runtime.transaction() as connection:
                    connection.execute("UPDATE conversation_batches SET contributions_json=? WHERE batch_id=?",
                                       (encode(saved), first))

        self.assertTrue(any(item["type"] == "confirmed_group_reply"
                            for item in self.history(context, second)))

    async def test_changed_input_body_cannot_replace_a_frozen_member_message(self):
        app = await self.start_host()
        _, first = await self.receive("原始群消息")
        b, second = await self.receive("引用原始消息的下一轮", sender="201")
        source = json.loads(self.input_row(first)["event_json"])["source"]
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET payload_json = ? WHERE source_id = ?",
                               (encode("替换了正文但保留旧哈希"), source["source_id"]))
        with self.assertRaises(TaskError):
            self.view(b, second)
        c, third = await self.receive("篡改之后的新消息", sender="202")
        self.assertEqual([item["text"] for item in self.history(c, third)], ["引用原始消息的下一轮"])

    async def test_history_is_bounded_and_oversized_entries_are_not_silently_truncated(self):
        await self.start_host()
        message_count = MAX_ITEMS // 2 + 3
        for index in range(message_count):
            self.clock.advance(timedelta(seconds=1))
            await self.receive("群消息-" + str(index), sender=str(500 + index))
        current, batch = await self.receive("最后一条", sender="800")
        history = self.history(current, batch)
        self.assertEqual(len(history), MAX_ITEMS)
        self.assertEqual([item["type"] for item in history],
                         ["group_member_message", "confirmed_group_reply"] * (MAX_ITEMS // 2))
        first_retained = message_count - MAX_ITEMS // 2
        self.assertEqual([item["text"] for item in history if item["type"] == "group_member_message"],
                         ["群消息-" + str(index) for index in range(first_retained, message_count)])
        self.assertLessEqual(sum(len(encode(item).encode("utf-8")) for item in history), MAX_BYTES)
        oversized = "完整内容不能截断" * 900
        await self.receive(oversized, sender="801", identifier="oversized")
        current, batch = await self.receive("超预算之后", sender="802", identifier="after-oversized")
        self.assertFalse(any("完整内容不能截断" in m["text"] for m in self.history(current, batch)))

    async def test_shared_context_survives_restart_and_duplicate_intake_without_new_delivery(self):
        await self.setup_delivery()
        event = self.group_event(identifier="shared-origin", text="重启前的群消息")
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        b, second = await self.receive("重启前的第二位成员", sender="201")
        snapshot = self.snapshot(second)
        await self.controller.terminate()
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        self.assertTrue((await self.controller.load_existing()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        self.assertEqual(self.snapshot(second), snapshot)
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 2)
        c, third = await self.receive("重启后的新成员", sender="202")
        history = self.history(c, third)
        self.assertEqual([m["speaker_id"] for m in history if m["type"] == "group_member_message"], ["200", "201"])
        self.assertEqual(len([m for m in history if m["type"] == "confirmed_group_reply"]), 2)

    async def test_policy_revision_refresh_does_not_replace_private_routine_authority(self):
        app = await self.start_host()
        before = self.controller.authority.context("first", "role-a")
        routine = app.life_runtime.for_context(before)
        await self.receive("新群成员推进公共授权版本", sender="999")
        private, _ = await self.accepted(identifier="private-continuation")
        self.assertEqual(private.key, before.key)
        self.assertNotEqual(private.actor.policy_revision, before.actor.policy_revision)
        self.assertIs(app.life_runtime.for_context(private), routine)
        self.assertEqual(routine.context.actor.policy_revision, private.actor.policy_revision)
        changed = replace(private, actor=replace(private.actor, binding_revision=private.actor.binding_revision + 1))
        with self.assertRaises(TaskError):
            routine.bind(changed, routine.instruction)


if __name__ == "__main__":
    unittest.main()
