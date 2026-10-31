"""Existing V2 owners work from real native input without a reply batch."""

import asyncio
import base64
from datetime import datetime
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.agent.tool_image_cache import tool_image_cache
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain
from mcp.types import CallToolResult

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_tools import inject_core_reply_tools, inject_core_turn_notes, native_tool_name
from astrbot_host.core_reply_reactions import observe_core_cached_image, observe_core_media_send_returned
from companion_v2.agenda.models import Schedule
from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import encode, require
from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
from tests.group_host_support import GroupFixture
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_semantic_integration import SemanticPort, animation


class NativeOwnerCalls:
    async def bind(self, event):
        prepared = require(await self.controller.prepare_core_reply(event))
        prepare_core_reply_event(event, prepared,
            authorize=lambda value: self.controller.authorize_core_reply(event, value))
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        return prepared, request

    async def call(self, event, request, action, arguments, call_id=None):
        return json.loads(await self.invoke(event, request, action, arguments, call_id))

    async def invoke(self, event, request, action, arguments, call_id=None):
        call = ToolCall(id=call_id or action, function=ToolCall.FunctionBody(
            name=native_tool_name(action), arguments=json.dumps(arguments)))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        tool = request.func_tool.get_tool(native_tool_name(action))
        self.assertIsNotNone(tool, action)
        return await tool.call(wrapper, **arguments)

    def assert_no_reply_work(self):
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])


class CoreReplyOwnerToolTests(NativeOwnerCalls, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = self.make_controller(multi_config(capabilities=sorted(REQUIRED_CAPABILITIES | {
            "memory.read", "memory.propose", "memory.reflect", "memory.subscribe",
            "role.growth.read", "role.growth.propose", "relationship.memory.read", "relationship.memory.propose",
            "calendar.read", "calendar.write"})))
        self.app = await self.start_host()

    async def test_memory_growth_relationship_and_calendar_commands_use_real_native_evidence(self):
        event = self.event(identifier="owner-native-evidence", text="我平时不喝咖啡，周末我们画水彩吧")
        prepared, request = await self.bind(event)
        evidence = await self.call(event, request, "turn.evidence", {})
        self.assertEqual(evidence["state"], "succeeded", evidence)
        handle = evidence["memory_evidence_handle"]
        memory = await self.call(event, request, "memory.proposal.submit", {"proposal": {
            "operation": "add", "predicate": "饮用偏好", "object": "不喝咖啡",
            "representation": "用户自述平时不喝咖啡", "evidence_handles": [handle]}})
        self.assertEqual(memory["state"], "succeeded", memory)
        self.assertEqual(memory["payload"]["state"], "persisted", memory)
        handles = evidence["evidence_handles"]
        growth = await self.call(event, request, "role.growth.propose", {"kind": "goal", "title": "练习水彩",
            "description": "画一张周末风景", "evidence_handles": handles})
        self.assertEqual(growth["state"], "succeeded", growth)
        self.assertTrue(growth["payload"]["requires_human_confirmation"])
        relationship = await self.call(event, request, "relationship.memory.propose", {"kind": "context",
            "interpretation": "一起分享周末水彩计划", "evidence_handles": handles})
        self.assertEqual(relationship["state"], "succeeded", relationship)
        self.assertTrue(relationship["payload"]["requires_human_review"])
        calendar = await self.call(event, request, "calendar.command", {"action": "create", "id": "native-watercolor",
            "expected_revision": 0, "evidence_handles": handles, "data": {
                "kind": "role_plan", "title": "水彩练习", "detail": "画小幅周末风景", "duration_minutes": 30,
                "presence": "画画中", "schedule": json.loads(encode(Schedule(
                    datetime.fromisoformat("2026-10-11T10:00:00+08:00"), "Asia/Shanghai")))}})
        self.assertEqual(calendar["state"], "succeeded", calendar)
        self.assert_no_reply_work()

    async def test_tool_injection_and_read_tools_do_not_wait_for_pending_observation(self):
        event = self.event(identifier="slow-native-observation")
        prepared = require(await self.controller.prepare_core_reply(event))
        reference = await prepared["native_source_future"]
        pending = asyncio.get_running_loop().create_future()
        prepared["native_source_future"] = pending
        prepare_core_reply_event(event, prepared,
            authorize=lambda value: self.controller.authorize_core_reply(event, value))
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        self.assertFalse(pending.done())
        read = await self.call(event, request, "role.growth.list", {})
        self.assertEqual(read["state"], "succeeded", read)
        evidence_task = asyncio.create_task(self.call(event, request, "turn.evidence", {}))
        await asyncio.sleep(0)
        self.assertFalse(evidence_task.done())
        pending.set_result(reference)
        self.assertEqual((await evidence_task)["state"], "succeeded")
        self.assert_no_reply_work()


class CoreReplyGroupOwnerToolTests(NativeOwnerCalls, GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def test_native_group_memory_uses_original_member_and_withdrawal(self):
        event = self.group_event(identifier="native-group-save", text="周六在公园集合")
        prepared, request = await self.bind(event)
        evidence = await self.call(event, request, "turn.evidence", {})
        self.assertEqual(evidence["state"], "succeeded", evidence)
        saved = await self.call(event, request, "group.memory.remember", {
            "input_handle": evidence["input_handle"], "statement": "周六在公园集合"})
        self.assertEqual(saved["state"], "succeeded", saved)
        other = self.group_event(identifier="native-group-query", sender="201", text="集合安排是什么")
        _, other_request = await self.bind(other)
        read = await self.call(other, other_request, "group.memory.query", {"query": "公园"})
        self.assertEqual(read["payload"]["items"][0]["speaker_id"], "200", read)
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(prepared["context"].scope,
            prepared["event_id"], prepared["context"].actor.principal.native_account_id)))
        read = await self.call(other, other_request, "group.memory.query", {"query": "公园"}, "query-after-withdrawal")
        self.assertEqual(read["payload"]["items"], [])
        self.assert_no_reply_work()

    async def test_native_group_slang_uses_original_input_and_is_removed_after_withdrawal(self):
        self.app.semantics.gateway.port = SemanticPort()
        event = self.group_event(identifier="native-group-slang", text="咕咕就是临时取消约好的活动")
        prepared, request = await self.bind(event)
        evidence = await self.call(event, request, "turn.evidence", {})
        arguments = {"term": "咕咕", "meaning": "临时取消约好的活动",
            "usage": "这周末咕咕了", "input_handle": evidence["input_handle"]}
        learned = await self.call(event, request, "slang.learn", arguments)
        self.assertEqual(learned["state"], "succeeded", learned)
        read = await self.call(event, request, "slang.search", {"query": "咕咕"})
        self.assertEqual(read["payload"]["items"][0]["payload"]["meaning"], "临时取消约好的活动", read)
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(prepared["context"].scope,
            prepared["event_id"], prepared["context"].actor.principal.native_account_id)))
        read = await self.call(event, request, "slang.search", {"query": "咕咕"}, "slang-after-withdrawal")
        print("DEBUG slang read", read)
        self.assertEqual(read["payload"]["items"], [], read)
        replay = await self.call(event, request, "slang.search", {"query": "咕咕"})
        self.assertEqual(replay["code"], "semantic_result_changed", replay)
        replay = await self.call(event, request, "slang.learn", arguments)
        self.assertEqual((replay["state"], replay["effect"]), ("succeeded", "committed"), replay)
        self.assertEqual(replay["material_state"], "unavailable", replay)
        self.assertNotIn("payload", replay)
        self.assert_no_reply_work()

    async def test_addressed_group_turn_still_offers_and_runs_the_participation_choice(self):
        # Production called companion_v2_group_participation_choose on every @
        # turn even after 7.0.1 stopped offering it there, so the host answered
        # "tool not found" and the turn was wasted.  The tool must stay in the
        # set so the call the model makes from history runs normally.
        event = self.group_event(identifier="native-group-addressed", text="吃什么")
        event.message_obj.message.append(NS(type="At", qq="100"))
        admission = require(await self.controller.group_reply_candidate(event))
        self.assertTrue(admission["admitted"], admission)
        self.assertTrue(admission["mentioned"], admission)
        prepared = require(await self.controller.prepare_core_reply(event, group_admission=admission))
        prepared["group_admission"] = dict(admission)
        prepare_core_reply_event(event, prepared,
            authorize=lambda value: self.controller.authorize_core_reply(event, value))
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        self.assertIsNotNone(request.func_tool.get_tool(native_tool_name("group.participation.choose")))
        choice = await self.call(event, request, "group.participation.choose",
                                 {"mode": "reply", "reason": "被点名"})
        self.assertEqual(choice["state"], "succeeded", choice)
        self.assertEqual(choice["payload"]["mode"], "reply")
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["group_participation"]["mode"], "reply")
        self.assert_no_reply_work()


class CoreReplySemanticOwnerToolTests(NativeOwnerCalls, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.semantic = self.app.semantics
        self.semantic.gateway.port = SemanticPort()
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_trigger_probability": 1.0,
            "reactions_cooldown_seconds": 0}, settings["revision"])

    async def test_native_reaction_returns_original_animation_without_extra_model_reply_or_direct_send(self):
        event = self.event(identifier="native-reaction", text="给我发个挥手表情")
        prepared, request = await self.bind(event)
        original = animation()
        owner = self.semantic.ensure_reaction_catalog(prepared["context"])
        self.semantic.reactions.import_image(owner, original,
            {"name": "挥手", "description": "友好的挥手", "intents": "回应问候"})
        output = await self.invoke(event, request, "reaction.search", {"intent": "挥手回应问候"})
        self.assertIsInstance(output, CallToolResult, output)
        self.assertEqual(json.loads(output.content[0].text)["state"], "succeeded")
        self.assertEqual(output.content[1].mimeType, "image/gif")
        self.assertEqual(base64.b64decode(output.content[1].data), original.content)
        again = await self.invoke(event, request, "reaction.search", {"intent": "挥手回应问候"})
        self.assertEqual(again.content[1], output.content[1])
        self.assertEqual(json.loads(again.content[0].text)["payload"], json.loads(output.content[0].text)["payload"])
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 0)
        self.assertEqual(event.sent, [])
        with patch.object(tool_image_cache, "_cache_dir", str(self.root / "tool_images")):
            cached = tool_image_cache.save_image(output.content[1].data, "reaction.search",
                native_tool_name("reaction.search"), 1, "image/gif")
        self.assertTrue(observe_core_cached_image(event, cached))
        chain = MessageChain(chain=[Image.fromFileSystem(cached.file_path)])
        queued = []
        submit = lambda work: queued.append(work) or True
        self.assertTrue(observe_core_media_send_returned(event, chain, submit=submit))
        self.assertTrue(observe_core_media_send_returned(event, chain, submit=submit))
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 0)
        for work in queued:
            work()
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reaction_deliveries").fetchone()[0], 1)
        self.assert_no_reply_work()

    async def test_reaction_tool_set_and_system_prefix_stay_stable_across_turns(self):
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_trigger_probability": 0.0,
            "reactions_cooldown_seconds": 0}, settings["revision"])
        first = self.event(identifier="native-reaction-off", text="今天有点累")
        _, first_request = await self.bind(first)
        first_tools = [(tool.name, tool.description, tool.parameters) for tool in first_request.func_tool.tools]
        first_system = first_request.system_prompt
        self.assertIn(native_tool_name("reaction.search"), [tool[0] for tool in first_tools])
        self.assertIn("本轮不使用图库表情", first.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_turn_notes"]["reactions"])
        inject_core_turn_notes(first, first_request)
        self.assertTrue(any(getattr(part, "text", "").startswith("<companion_v2_native_reactions>")
                            for part in first_request.extra_user_content_parts))
        refused = json.loads(await self.invoke(first, first_request, "reaction.search", {"intent": "安慰"}))
        self.assertEqual((refused["state"], refused["code"]), ("failed", "reaction_unavailable"))

        revision = self.semantic.store.settings()["revision"]
        self.semantic.store.configure({"reactions_trigger_probability": 1.0}, revision)
        second = self.event(identifier="native-reaction-on", text="今天有点累")
        _, second_request = await self.bind(second)
        second_tools = [(tool.name, tool.description, tool.parameters) for tool in second_request.func_tool.tools]
        self.assertIn("本轮图库表情可用", second.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_turn_notes"]["reactions"])
        # The per-turn roll must not change what the provider caches: same tool
        # definitions and same system prompt on both turns.
        self.assertEqual(first_tools, second_tools)
        self.assertEqual(first_system, second_request.system_prompt)
        self.assert_no_reply_work()


if __name__ == "__main__":
    unittest.main()
