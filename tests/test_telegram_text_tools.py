"""Telegram text capabilities through AstrBot's real event bus and tool runner."""
from tests import astrbot_data_root  # noqa: F401

import asyncio
import json
from types import SimpleNamespace as NS
import unittest

from tests.linked_reply_support import LinkedTelegramFixture, TELEGRAM_OWNER
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_tools import native_tool_name

TEXT_ACTIONS = (
    "turn.evidence", "source.read", "memo.create", "memo.read", "memo.list", "memo.edit", "memo.complete",
    "memo.reopen", "memo.pin", "memo.unpin", "memo.delete", "reminder.create", "reminder.read", "reminder.list",
    "reminder.cancel", "reminder.reschedule", "operation.lookup", "memory.query", "memory.legacy.query",
    "memory.sources", "memory.proposal.submit", "memory.operation.get", "memory.forget", "memory.query.status",
    "memory.experience.save", "calendar.query", "calendar.command", "role.growth.list", "role.growth.propose",
    "role.growth.revise", "relationship.memory.propose", "role.timeline.query", "weather.current",
    "conversation.news.lookup", "diagnostics.explain", "diagnostics.inspect", "diagnostics.propose", "diagnostics.proposal.read",
)


class TelegramTextToolTests(LinkedTelegramFixture, unittest.IsolatedAsyncioTestCase):
    def make_controller(self, config=None):
        from tests.multi_host_support import multi_config
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
        from companion_v2.conversation.agenda_tools import TOOL_FIELDS
        config = config or multi_config(capabilities=sorted(REQUIRED_CAPABILITIES | set(TOOL_FIELDS) | {
            "memory.read", "memory.propose", "memory.reflect", "memory.subscribe", "memory.forget",
            "calendar.read", "calendar.write", "role.growth.read", "role.growth.propose", "role.life.read",
            "relationship.memory.read", "relationship.memory.propose", "reminder.deliver", "memo.reminder.set", "configuration.write"}))
        return super().make_controller(config)

    async def turn(self, index, text, identifier=1, **fields):
        event = self.event_for(index, text, identifier, **fields)
        await asyncio.wait_for(await self.dispatch(event), 30)
        await self.controller.session.linked_conversations.gate.drain()
        return event

    async def test_calendar_command_returns_its_existing_service_result_to_the_authenticated_telegram_chat(self):
        from companion_v2.role_work.state.configuration import StateConfigurationOwner, configuration_fields
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        context = self.controller.authority.context("first", "role-a")
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role, bot_id=context.scope.bot_id)
        owner, fields = StateConfigurationOwner(self.app.runtime), configuration_fields()
        owner.write(target, fields, changes={"role.state.calendar_enabled": False}, reset=(),
            expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(1, "生成日程")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.sent), 1)
        platform, receipt = self.sent[0]
        self.assertEqual((platform, str(receipt["chat_id"])), ("tg-main", str(TELEGRAM_OWNER)))
        self.assertIn("角色日程总开关已关闭", receipt["text"])
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def calendar_command_with_the_planning_model_paused(self, index, *, link_changes):
        """The real command path on a linked endpoint; returns the plans written and the receipts sent."""
        from datetime import timedelta
        from astrbot_host.linked_conversations import KEY
        from tests.native_adapter_support import completion
        self.assertEqual((await self.link())["status"], "ok")
        planning, release = asyncio.Event(), asyncio.Event()

        async def paused_planning(payload, tools):
            planning.set()
            await release.wait()
            return completion(json.dumps({"items": [{"title": "read the next chapter", "minutes": 30,
                "start": (self.clock.now() + timedelta(hours=1)).isoformat()}]}))
        self.native.provider.client.state.behavior = paused_planning
        task = await self.dispatch(self.event_for(index, "生成日程"))
        try:
            await asyncio.wait_for(planning.wait(), 15)
            if link_changes == "new conversation":
                # AstrBot's own /new on the other endpoint, registered as its loader registers it.
                from astrbot.builtin_stars.builtin_commands.commands.conversation import ConversationCommands
                self.builtin("new", ConversationCommands(self.core_context).new_conv)
                await asyncio.wait_for(await self.dispatch(self.event_for(0, "/new", 2)), 20)
                await self.controller.session.linked_conversations.gate.drain()
                self.assertEqual(self.controller.session.linked_conversations.get("owner-dialogue")["revision"], 2)
            elif link_changes:
                records = self.controller.session.linked_conversations.records()
                records["owner-dialogue"]["revision"] += 1
                self.controller.authority.write(KEY, records)
        finally:
            release.set()
        await asyncio.wait_for(task, 30)
        context = self.controller.authority.context("first", "role-a")
        return self.app.calendar.inventory(context)["items"], [
            receipt["text"] for platform, receipt in self.sent if platform == "tg-main"]

    async def test_calendar_command_on_a_linked_endpoint_writes_its_plan_and_returns_the_receipt(self):
        plans, receipts = await self.calendar_command_with_the_planning_model_paused(1, link_changes=False)
        self.assertEqual([plan["title"] for plan in plans], ["read the next chapter"])
        self.assertEqual(len(receipts), 1)
        self.assertIn("共写入 1 项", receipts[0])

    async def test_link_revision_changed_while_a_calendar_command_plans_prevents_entry_and_receipt(self):
        plans, receipts = await self.calendar_command_with_the_planning_model_paused(1, link_changes=True)
        self.assertEqual((plans, receipts), ([], []))

    async def test_a_new_conversation_started_while_a_calendar_command_plans_prevents_entry_and_receipt(self):
        # /new gives the link a new revision: a command still planning under the old one is refused.
        plans, receipts = await self.calendar_command_with_the_planning_model_paused(
            1, link_changes="new conversation")
        self.assertEqual((plans, receipts), ([], []))

    async def test_command_receipt_refuses_a_client_replaced_since_route_authentication(self):
        from astrbot_host.recalls import authenticate_notice_route
        from astrbot_host.private_notice import send_private_notice
        from companion_v2.foundation.refs import ScopeKind
        event = self.event_for(1, "生成日程")
        route = authenticate_notice_route(self.controller, event, ScopeKind.PRIVATE, expected_post="message")
        self.assertIsNotNone(route)
        original = self.telegram.client
        self.telegram.client = type(original)(**vars(original))
        self.telegram.application.bot = self.telegram.client
        result = await send_private_notice(self.controller, event, text="calendar result", route=route)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.sent, [])

    async def test_telegram_command_receipt_send_exception_is_unknown_and_not_replayed(self):
        from telegram.error import TimedOut
        from astrbot_host.recalls import authenticate_notice_route
        from astrbot_host.private_notice import send_private_notice
        from companion_v2.foundation.refs import ScopeKind
        attempts = []

        async def uncertain_send(**kwargs):
            attempts.append(kwargs)
            raise TimedOut()

        self.telegram.client.send_message = uncertain_send
        event = self.event_for(1, "生成日程")
        route = authenticate_notice_route(self.controller, event, ScopeKind.PRIVATE, expected_post="message")
        receipt = await send_private_notice(self.controller, event, text="calendar result", route=route)
        self.assertEqual(receipt["state"], "unknown")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.sent, [])

    async def test_other_plugins_commands_keep_their_telegram_route_without_companion_model_work(self):
        from astrbot.core.message.message_event_result import MessageChain
        called = []

        async def update(event):
            called.append(event.unified_msg_origin)
            await event.send(MessageChain().message("synthetic update result"))

        self.config["plugin_set"].append("synthetic_updater")
        self.builtin("update_stack", update, module="synthetic_updater.main")
        self.assertEqual((await self.link())["status"], "ok")
        for number, text in enumerate(("/update_stack", "update_stack"), start=1):
            await self.turn(1, text, number)
        self.assertEqual(called, [self.origins[1], self.origins[1]])
        self.assertEqual([platform for platform, _ in self.sent], ["tg-main", "tg-main"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.manager.writes, [])

    async def test_replacing_the_source_client_while_model_waits_blocks_tool_effects_and_sends(self):
        from companion_v2.agenda.models import AgendaTarget
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(1)
        self.pause_model = True
        self.tool_script = [(native_tool_name("memo.create"), {"title": "stale client", "body": "must not exist"})]
        event = self.event_for(1, "make a note")
        task = await self.dispatch(event)
        await asyncio.wait_for(self.model_done.wait(), 15)
        context = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"]
        original = self.telegram.client
        self.telegram.client = type(original)(**vars(original))
        self.telegram.application.bot = self.telegram.client
        self.allow_model.set()
        await asyncio.wait_for(task, 30)
        await self.controller.session.linked_conversations.gate.drain()
        results = [json.loads(message["content"]) for message in await self.history() if message["role"] == "tool"]
        # Upstream fences the tool round on the connection lease: the tool never runs, so it records no result.
        self.assertEqual(results, [])
        notes = self.app.agenda.list_memos(context.actor,
            target=AgendaTarget(context.scope, context.role_binding.role, context.actor.subject), observed_at=self.clock.now())
        self.assertTrue(notes.is_success, notes.failure)
        self.assertEqual(len(notes.unwrap()), 0)
        # Nothing is sent on a turn whose connection lease is no longer current.
        self.assertEqual(self.sent, [])

    @staticmethod
    def prior(args, index=-1):
        messages = [message if isinstance(message, dict) else message.model_dump() for message in args["contexts"]]
        start = max(i for i, message in enumerate(messages) if message["role"] == "user")
        content = [message["content"] for message in messages[start:] if message["role"] == "tool"][index]
        if isinstance(content, list):
            content = "".join(part["text"] for part in content if part["type"] == "text")
        return json.loads(content.split("\n\n[SYSTEM NOTICE:")[0])

    async def run_tools(self, index, script, *, text="text tool request", identifier=1, event=None):
        self.steer(index)
        self.model_rounds.clear()
        self.tool_script = [(native_tool_name(action), arguments) if not callable(arguments)
                            else lambda args, action=action, arguments=arguments: (native_tool_name(action), arguments(args))
                            for action, arguments in script]
        if event is None:
            event = await self.turn(index, text, identifier)
        else:
            await asyncio.wait_for(await self.dispatch(event), 30)
            await self.controller.session.linked_conversations.gate.drain()
        for action, _ in script:
            self.assertIn(native_tool_name(action), self.requests[-1][1])
        cid = await self.manager.get_curr_conversation_id(self.origins[index])
        conversation = await self.manager.get_conversation(self.origins[index], cid)
        results = [json.loads(message["content"]) for message in json.loads(conversation.history)
                   if message["role"] == "tool"][-len(script):]
        self.assertEqual(len(results), len(script))
        for result in results:
            self.assertEqual(result["state"], "succeeded", result)
        return event, results

    async def test_telegram_notes_use_the_existing_crud_tools_and_receipts(self):
        memo_id = lambda args: self.prior(args, 0)["payload"]["object_id"]
        reminder_id = lambda args: self.prior(args, 10)["payload"]["object_id"]
        schedule = {"anchor": "2026-10-11T10:00:00+00:00", "timezone_name": "UTC"}
        _, results = await self.run_tools(1, [("memo.create", {"title": "synthetic note", "body": "remember tea"}),
            ("memo.read", lambda args: {"memo_id": memo_id(args)}), ("memo.list", {}),
            ("memo.edit", lambda args: {"memo_id": memo_id(args), "expected_revision": 1, "changes": {"body": "updated tea"}}),
            ("memo.pin", lambda args: {"memo_id": memo_id(args), "expected_revision": 2}),
            ("memo.unpin", lambda args: {"memo_id": memo_id(args), "expected_revision": 3}),
            ("memo.complete", lambda args: {"memo_id": memo_id(args), "expected_revision": 4}),
            ("memo.reopen", lambda args: {"memo_id": memo_id(args), "expected_revision": 5}),
            ("memo.delete", lambda args: {"memo_id": memo_id(args), "expected_revision": 6}),
            ("operation.lookup", lambda args: {"operation_id": self.prior(args, 0)["operation_id"]}),
            ("reminder.create", {"message": "synthetic reminder", "schedule": schedule}),
            ("reminder.read", lambda args: {"reminder_id": reminder_id(args)}), ("reminder.list", {}),
            ("reminder.reschedule", lambda args: {"reminder_id": reminder_id(args), "expected_revision": 1,
                "schedule": {**schedule, "anchor": "2026-10-12T10:00:00+00:00"}}),
            ("reminder.cancel", lambda args: {"reminder_id": reminder_id(args), "expected_revision": 2})])
        self.assertEqual(results[1]["payload"]["title"], "synthetic note")
        self.assertEqual(results[8]["payload"]["state"], "deleted")
        self.assertIn("synthetic reminder", json.dumps(results[12]))
        self.assertEqual(results[-1]["payload"]["state"], "cancelled")
        self.assertEqual(self.sent[0][0], "tg-main")
        names = self.requests[-1][1]
        self.assertIn("companion_v2_memo_create", names)
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_independently_admitted_telegram_tools_keep_their_own_data_isolated_from_qq(self):
        _, saved = await self.run_tools(1, [("memo.create", {"title": "independent Telegram note", "body": "private local data"})])
        _, qq = await self.run_tools(0, [("memo.list", {})], identifier=2)
        self.assertEqual(qq[0]["payload"], [])
        _, telegram = await self.run_tools(1, [("memo.read", {"memo_id": saved[0]["payload"]["object_id"]})], identifier=3)
        self.assertEqual(telegram[0]["payload"]["title"], "independent Telegram note")

    async def test_linked_text_tools_read_existing_qq_memory_role_calendar_and_timeline(self):
        evidence = lambda args: self.prior(args, 0)["evidence_handles"]
        await self.run_tools(0, [("turn.evidence", {}),
            ("memory.proposal.submit", lambda args: {"proposal": {"operation": "add", "predicate": "drink",
                "object": "tea", "representation": "The owner drinks tea",
                "evidence_handles": [self.prior(args, 0)["memory_evidence_handle"]]}}),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "Shared watercolor practice",
                "evidence_handles": evidence(args)}),
            ("calendar.command", lambda args: {"action": "create", "id": "shared-role-plan", "expected_revision": 0,
                "evidence_handles": evidence(args), "data": {"kind": "role_plan", "title": "Shared watercolor session",
                    "detail": "practice", "duration_minutes": 30, "presence": "painting",
                    "schedule": {"anchor": "2026-10-11T10:00:00+00:00", "timezone_name": "UTC"}}})],
            text="I drink tea and we can practise watercolor together")
        self.assertEqual((await self.link())["status"], "ok")
        _, results = await self.run_tools(1, [("memory.query", {"query_intent": "", "kind": "facts"}),
            ("calendar.query", {}), ("role.timeline.query", {"kinds": ["growth"]})], identifier=2)
        self.assertIn("The owner drinks tea", json.dumps(results[0]))
        self.assertIn("Shared watercolor session", json.dumps(results[1]))
        self.assertIn("Shared watercolor practice", json.dumps(results[2]))
        self.assertEqual([platform for platform, _ in self.sent], ["qq-main", "tg-main"])

    async def test_repeated_native_tool_call_id_returns_one_note_and_the_original_receipt(self):
        self.tool_call_ids = {0: "same-native-call", 1: "same-native-call"}
        arguments = {"title": "one note", "body": "one effect"}
        _, results = await self.run_tools(1, [("memo.create", arguments), ("memo.create", arguments), ("memo.list", {})])
        self.assertEqual(results[0]["operation_id"], results[1]["operation_id"])
        self.assertTrue(results[1]["replayed"])
        self.assertEqual(len(results[2]["payload"]), 1)

    async def test_revoked_endpoint_action_while_model_waits_prevents_the_write(self):
        self.steer(1)
        self.pause_model = True
        self.tool_script = [(native_tool_name("memo.create"), {"title": "must not exist", "body": "revoked"})]
        event = self.event_for(1, "make a note")
        task = await self.dispatch(event)
        await asyncio.wait_for(self.model_done.wait(), 15)
        context = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"]
        directory = self.controller.authority.identities
        grants = [grant for grant in directory.grants_for_subject(context.actor.subject)
                  if grant.scope == context.scope and grant.capability == "memo.create"]
        self.assertTrue(grants)
        for grant in grants:
            directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(), expected_revision=directory.revision)
        self.allow_model.set()
        await asyncio.wait_for(task, 30)
        conversation = await self.manager.get_conversation(self.origins[1], "conversation-b")
        results = [json.loads(message["content"]) for message in json.loads(conversation.history)
                   if message["role"] == "tool"]
        self.assertEqual(results, [{"code": "core_tool_unavailable", "effect": "none", "retry_disposition": "stop", "state": "failed"}])
        from companion_v2.agenda.models import AgendaTarget
        notes = self.app.agenda.list_memos(context.actor,
            target=AgendaTarget(context.scope, context.role_binding.role, context.actor.subject), observed_at=self.clock.now())
        self.assertTrue(notes.is_success, notes.failure)
        self.assertEqual(len(notes.unwrap()), 0)

    async def test_link_revision_changed_while_tool_awaits_real_source_prevents_any_effect(self):
        await self.revision_changes_while_a_tool_awaits_its_source(1)

    async def test_link_revision_changed_while_a_linked_qq_tool_awaits_real_source_prevents_any_effect(self):
        await self.revision_changes_while_a_tool_awaits_its_source(0)

    async def revision_changes_while_a_tool_awaits_its_source(self, index):
        from astrbot.core.star.star_handler import EventType
        from astrbot_host.linked_conversations import KEY
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(index)
        self.pause_model = True
        source_ready, release_source = asyncio.Event(), asyncio.Event()
        original_source = []

        async def hold_source_return(event, req):
            prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
            original = prepared["native_source_future"]
            original_source.append(original)

            async def paused_return():
                reference = await asyncio.shield(original)
                source_ready.set()
                await release_source.wait()
                return reference
            prepared["native_source_future"] = asyncio.create_task(paused_return())

        self.register(EventType.OnLLMRequestEvent, hold_source_return, priority=-3000000)
        event = self.event_for(index, "let us practise watercolor")
        task = await self.dispatch(event)
        try:
            await asyncio.wait_for(source_ready.wait(), 15)
            await asyncio.wait_for(self.model_done.wait(), 15)
            handle = "native-input:" + original_source[0].result().source_id
            self.tool_script = [(native_tool_name("role.growth.propose"), {"kind": "goal", "title": "paint",
                "evidence_handles": [handle]})]
            self.allow_model.set()
            state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            async with asyncio.timeout(10):
                while not state.get("_business_tools_started"):
                    await asyncio.sleep(0)
            records = self.controller.session.linked_conversations.records()
            records["owner-dialogue"]["revision"] += 1
            self.controller.authority.write(KEY, records)
        finally:
            release_source.set()
            self.allow_model.set()
        await asyncio.wait_for(task, 30)
        await self.controller.session.linked_conversations.gate.drain()
        results = [json.loads(message["content"]) for message in await self.history() if message["role"] == "tool"]
        self.assertEqual(results, [{"code": "linked_revision_changed", "effect": "none", "retry_disposition": "stop", "state": "failed"}])
        timeline = self.app.role_timeline.query(state["prepared"]["context"], kinds=["growth"])
        self.assertEqual(timeline["items"], [])

    async def test_partial_quote_uses_receiver_text_and_never_recurses_into_nested_messages(self):
        from datetime import datetime, timezone
        from telegram import Chat, Message, User
        nested = Message(40, datetime.now(timezone.utc), Chat(TELEGRAM_OWNER, "private"),
            from_user=User(TELEGRAM_OWNER, "Synthetic", False), text="nested secret")
        event = await self.quoted_event(partial=True, reply_to_message=nested)
        _, results = await self.run_tools(1, [("turn.evidence", {}),
            ("source.read", lambda args: {"handle": next(handle for handle in self.prior(args)["evidence_handles"]
                                                        if handle.startswith("native-quote:"))})], event=event)
        snapshot = json.loads(results[1]["payload"])
        self.assertEqual(snapshot["text"], "quoted words")
        self.assertTrue(snapshot["partial"])
        self.assertFalse(snapshot["sender_is_self"])
        self.assertNotIn("nested secret", results[1]["payload"])
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_memory_growth_relationship_calendar_timeline_and_diagnostics_use_existing_services(self):
        from companion_v2.diagnostics.service import EXPLANATIONS
        evidence = lambda args: self.prior(args, 0)["evidence_handles"]
        _, results = await self.run_tools(1, [("turn.evidence", {}),
            ("memory.proposal.submit", lambda args: {"proposal": {"operation": "add", "predicate": "饮用偏好",
                "object": "不喝咖啡", "representation": "用户自述平时不喝咖啡",
                "evidence_handles": [self.prior(args, 0)["memory_evidence_handle"]]}}),
            ("memory.query", {"query_intent": "", "kind": "facts"}),
            ("memory.query.status", {}),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "练习水彩",
                "description": "画一张周末风景", "evidence_handles": evidence(args)}),
            ("role.growth.list", {}),
            ("relationship.memory.propose", lambda args: {"kind": "context", "interpretation": "分享水彩计划",
                "evidence_handles": evidence(args)}),
            ("calendar.command", lambda args: {"action": "create", "id": "synthetic-watercolor",
                "expected_revision": 0, "evidence_handles": evidence(args), "data": {
                    "kind": "role_plan", "title": "水彩练习", "detail": "画小幅周末风景", "duration_minutes": 30,
                    "presence": "画画中", "schedule": {"anchor": "2026-10-11T10:00:00+08:00", "timezone_name": "Asia/Shanghai"}}}),
            ("calendar.query", {}), ("role.timeline.query", {}),
            ("source.read", lambda args: {"handle": evidence(args)[0]}),
            ("diagnostics.explain", {"topic": sorted(EXPLANATIONS)[0]}),
            ("diagnostics.inspect", {"check": "configuration", "group_id": "conversation.presentation",
                "configuration_target": "current_window"}),
            ("role.growth.revise", lambda args: {"growth_id": self.prior(args, 4)["payload"]["growth_id"],
                "expected_revision": 1, "title": "继续练习水彩", "description": "练习风景", "progress_note": "刚开始",
                "evidence_handles": evidence(args)})], text="我平时不喝咖啡，周末我们画水彩吧")
        self.assertEqual(results[1]["payload"]["state"], "persisted")
        self.assertIn("不喝咖啡", json.dumps(results[2], ensure_ascii=False))
        self.assertTrue(results[4]["payload"]["requires_human_confirmation"])
        self.assertTrue(results[6]["payload"]["requires_human_review"])
        self.assertIn("水彩练习", json.dumps(results[8], ensure_ascii=False))
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_weather_and_news_reuse_configured_services_with_synthetic_source_ports(self):
        from companion_v2.role_work.state.configuration import StateConfigurationOwner, configuration_fields
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.proactive.external_materials import WeatherSnapshot, parse_news_response
        context = self.controller.authority.context("second", "role-a")
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role, bot_id=context.scope.bot_id)
        owner, fields = StateConfigurationOwner(self.app.runtime), configuration_fields()
        owner.write(target, fields, changes={"role.state.planning_weather_enabled": True,
            "role.state.planning_weather_location": json.dumps({"name": "Synthetic town", "latitude": 10, "longitude": 20})},
            reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())
        fetched = []

        async def weather(location):
            fetched.append(location.name)
            return WeatherSnapshot("open-meteo", location.name, self.clock.now(), 20, "晴", 40, 8, ())

        async def news():
            fetched.append("news")
            return parse_news_response({"items": [{"id": "synthetic-news", "title": "AI model release",
                "summary": "Synthetic source summary", "source": "Synthetic feed", "url": "https://example.invalid/news",
                "published_at": self.clock.now().isoformat()}]})

        self.app.tools.weather.port = NS(fetch=weather)
        self.app.contact_schedule.news_events.port = NS(fetch=news, source_label="Synthetic feed", fingerprint="synthetic-news")
        _, results = await self.run_tools(1, [("weather.current", {}), ("conversation.news.lookup", {"query": "recent AI models"})])
        self.assertEqual(results[0]["payload"]["role_location"], "Synthetic town")
        self.assertIn("AI model release", json.dumps(results[1]))
        self.assertEqual(fetched, ["Synthetic town", "news"])
        names = self.requests[-1][1]
        self.assertEqual({name for name in names if name.startswith("companion_v2_")},
                         {native_tool_name(action) for action in TEXT_ACTIONS})
        for excluded in ("poke", "qzone", "reaction", "image", "voice", "relay", "camera", "slang"):
            self.assertFalse(any(excluded in name for name in names), (excluded, names))
        self.assertNotIn("interaction.poke", self.requests[-1][2])
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_remaining_memory_actions_and_diagnostic_proposals_keep_their_existing_contracts(self):
        evidence = lambda args: [self.prior(args, 0)["memory_evidence_handle"]]
        _, results = await self.run_tools(1, [("turn.evidence", {}),
            ("memory.sources", lambda args: {"handle": evidence(args)[0]}),
            ("memory.legacy.query", {"query_intent": "coffee"}),
            ("memory.proposal.submit", lambda args: {"proposal": {"operation": "add", "predicate": "饮用偏好",
                "object": "不喝咖啡", "representation": "用户自述平时不喝咖啡", "evidence_handles": evidence(args)}}),
            ("memory.operation.get", lambda args: {"operation_id": self.prior(args, 3)["payload"]["operation_id"]}),
            ("memory.experience.save", lambda args: {"view_id": "synthetic-experience", "expected_revision": 0,
                "title": "coffee conversation", "statements": [{"text": "用户自述平时不喝咖啡",
                    "evidence_handles": evidence(args), "atoms": []}]}),
            ("memory.forget", lambda args: {"atom_id": self.prior(args, 3)["payload"]["atom_id"], "expected_revision": 1,
                "evidence_handles": evidence(args)}),
            ("diagnostics.inspect", {"check": "configuration", "group_id": "conversation.presentation",
                "configuration_target": "current_window"}),
            ("diagnostics.propose", lambda args: {"report_id": self.prior(args, 7)["report"]["report_id"],
                "changes": {"conversation.presentation.private_debounce_enabled": True}, "rationale": "synthetic requested change"}),
            ("diagnostics.proposal.read", lambda args: {"proposal_id": self.prior(args, 8)["proposal"]["proposal_id"]})],
            text="我平时不喝咖啡")
        self.assertIn("不喝咖啡", json.dumps(results[1], ensure_ascii=False))
        self.assertEqual(results[8]["proposal"]["state"], "pending")
        self.assertEqual(results[9]["proposal"]["state"], "pending")

    async def test_diagnostic_terminal_tools_require_the_existing_literal_private_confirmation(self):
        for index, terminal in enumerate(("cancel", "confirm")):
            _, results = await self.run_tools(1, [
                ("diagnostics.inspect", {"check": "configuration", "group_id": "conversation.presentation",
                    "configuration_target": "current_window"}),
                ("diagnostics.propose", lambda args: {"report_id": self.prior(args, 0)["report"]["report_id"],
                    "changes": {"conversation.presentation.private_debounce_enabled": True}, "rationale": "synthetic requested change"})],
                identifier=index * 2 + 1)
            identifier = results[1]["proposal"]["proposal_id"]
            command = "陪伴 答疑" + ("取消" if terminal == "cancel" else "确认") + " " + identifier
            _, results = await self.run_tools(1, [("diagnostics.proposal." + terminal, {"proposal_id": identifier})],
                text=command, identifier=index * 2 + 2)
            self.assertEqual(results[0]["proposal"]["state"], "cancelled" if terminal == "cancel" else "confirmed")

    async def quoted_event(self, *, partial=False, bot=False, identifier=42, **fields):
        from datetime import datetime, timezone
        from telegram import Chat, Message, TextQuote, User
        from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
        self.telegram.client.username = "mutable_bot_name"
        original = Message(41, datetime(2026, 1, 1, tzinfo=timezone.utc), Chat(TELEGRAM_OWNER, "private"),
            from_user=User(self.telegram.client.id if bot else TELEGRAM_OWNER, "Quoted sender", bot),
            text="hello 😀 quoted words", **fields)
        event = self.event_for(1, "about that quote", identifier, reply_to_message=original,
            **({"quote": TextQuote("quoted words", position=9)} if partial else {}))
        message = await self.telegram.convert_message(event.message_obj.raw_message, NS(bot=self.telegram.client))
        converted = TelegramPlatformEvent(message.message_str, message, self.telegram.meta(),
            str(TELEGRAM_OWNER), self.telegram.client)
        converted.created_at = self.clock.now().timestamp()
        return converted

    async def test_whole_quote_is_a_separate_governed_snapshot_with_physical_source_attribution(self):
        from companion_v2.conversation.native_tool_sources import native_offers
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(1)
        self.provider_mode = "tool"
        event = await self.quoted_event(bot=True)
        await asyncio.wait_for(await self.dispatch(event), 30)
        await self.controller.session.linked_conversations.gate.drain()
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        context = prepared["context"]
        offers = native_offers(self.app.messages, context, prepared["turn_id"])
        quotes = [(ref, cap) for ref, cap, purpose in offers.values() if purpose == "quoted_message"]
        self.assertEqual(len(quotes), 1)
        reference, capability = quotes[0]
        snapshot = json.loads(self.app.messages._read_source(reference, context, capability, self.clock.now()).payload)
        self.assertEqual(reference.scope, context.scope)
        self.assertEqual(snapshot, {"text": "hello 😀 quoted words", "platform": "tg-main", "account_id": "tg-main",
            "chat_id": str(TELEGRAM_OWNER), "message_id": "41", "sender_id": str(self.telegram.client.id),
            "sender_is_self": True, "sent_at_unix": 1767225600, "partial": False, "truncated": False,
            "attribution": "sending_account_only", "original_author": "unknown"})
        self.assertEqual(prepared["native_input"].text, "about that quote")
        self.assertIn("hello 😀 quoted words", str(self.calls[0]))
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_native_history_retains_the_bounded_quote_separately_from_the_human_text(self):
        from companion_v2.conversation.native_history import body, reference
        from companion_v2.conversation.native_tool_sources import input_row
        event = await self.quoted_event()
        await asyncio.wait_for(await self.dispatch(event), 30)
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        await prepared["native_source_future"]
        context = prepared["context"]
        row = input_row(self.app.messages, context, prepared["turn_id"])
        read, owners = self.app.messages._read_source, []

        def counted(source, *args):
            owners.append(source.owner)
            return read(source, *args)
        self.app.messages._read_source = counted
        history = body(self.app.messages, reference(row), context, self.clock.now())
        self.assertEqual(history["text"], "about that quote")
        self.assertEqual(history["quotes"][0]["text"], "hello 😀 quoted words")
        self.assertEqual(owners.count("conversation.native_quote"), 1)

    async def test_remote_or_unattributed_reply_components_cannot_offer_another_source(self):
        from telegram import Update
        from companion_v2.conversation.native_tool_sources import native_offers
        for index, change in enumerate(("chat", "sender", "inaccessible"), start=42):
            with self.subTest(change=change):
                event = await self.quoted_event(identifier=index)
                document = event.message_obj.raw_message.to_dict()
                original = document["message"]["reply_to_message"]
                if change == "chat":
                    original["chat"]["id"] = TELEGRAM_OWNER + 1
                elif change == "sender":
                    original["from"]["id"] = TELEGRAM_OWNER + 1
                else:
                    document["message"].pop("reply_to_message")
                event.message_obj.raw_message = Update.de_json(document, None)
                await asyncio.wait_for(await self.dispatch(event), 30)
                prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
                await prepared["native_source_future"]
                offers = native_offers(self.app.messages, prepared["context"], prepared["turn_id"])
                self.assertEqual([purpose for _, _, purpose in offers.values()], ["current_source"])
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_receiver_snapshot_has_an_explicit_local_text_budget_and_truncation_marker(self):
        from companion_v2.conversation.native_tool_sources import native_offers
        event = await self.quoted_event()
        # The receiver can expand text links; the companion still bounds its own snapshot.
        event.message_obj.message[0].message_str = "x" * 9000
        await asyncio.wait_for(await self.dispatch(event), 30)
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        await prepared["native_source_future"]
        offers = native_offers(self.app.messages, prepared["context"], prepared["turn_id"])
        reference, capability, _ = next(value for value in offers.values() if value[2] == "quoted_message")
        payload = json.loads(self.app.messages._read_source(reference, prepared["context"], capability, self.clock.now()).payload)
        self.assertEqual(payload["text"], "x" * 8000)
        self.assertTrue(payload["truncated"])


if __name__ == "__main__":
    unittest.main()
