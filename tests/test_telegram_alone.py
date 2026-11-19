"""The companion keeps running on Telegram alone when QQ is retired.

QQ is the endpoint the conversation started on (the link's anchor); Telegram
joined. QQ is then disconnected, unlinked, or removed from configuration.
"""
from tests import astrbot_data_root  # noqa: F401

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import unittest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.execution.models import require
from companion_v2.memory.models import AtomRef, Proposal
from tests import test_telegram_text_tools as text_tools
from tests.linked_reply_support import RecordedConversations, TELEGRAM_OWNER
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_preferred_channel import PreferredChannelFixture

QQ, TELEGRAM = PreferredChannelFixture.origins
OWNER = str(TELEGRAM_OWNER)


class TelegramAloneFixture(PreferredChannelFixture):
    """QQ (first, the anchor) and Telegram (second) linked on the real reply path."""

    qq_configured = True
    prior = staticmethod(text_tools.TelegramTextToolTests.prior)

    def make_controller(self, config=None):
        if not self.qq_configured:
            # A removed account stays removed across a restart.
            return MultiFixture.make_controller(self, self.telegram_only(self.controller.config))
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
        from companion_v2.conversation.agenda_tools import TOOL_FIELDS
        return super().make_controller(config or multi_config(capabilities=sorted(
            REQUIRED_CAPABILITIES | set(TOOL_FIELDS) | {"reminder.deliver", "memo.reminder.set",
                "configuration.write", "memory.read", "memory.propose", "memory.reflect", "memory.subscribe",
                "calendar.read", "calendar.write", "role.life.read"})))

    @staticmethod
    def telegram_only(config):
        """The same plugin configuration with the QQ accounts, targets and personas taken out."""
        config = deepcopy(config)
        host = config["multi_host"]
        host["targets"] = [row for row in host["targets"] if row["platform_id"] == "tg-main"]
        host["personas"] = [row for row in host["personas"] if row["bot_id"] == "bot-tg"]
        return config

    async def attempt(self, index, text, identifier):
        """One dispatched input: its event, and its prepared reply when it was admitted."""
        event = self.event_for(index, text, identifier)
        if index == 0:
            # The OneBot payload names both accounts; the companion's own commands authenticate it.
            event.message_obj.raw_message.update(self_id=100, user_id=200)
        await asyncio.wait_for(await self.dispatch(event), 30)
        await self.controller.session.linked_conversations.gate.drain()
        prepared = (event.get_extra(CORE_REPLY_CONTEXT_EXTRA) or {}).get("prepared")
        if prepared is not None:
            await prepared["native_source_future"]
        return event, prepared

    async def turn(self, index, text, identifier):
        event, prepared = await self.attempt(index, text, identifier)
        self.assertIsNotNone(prepared, event.get_extra("companion_linked_failure"))
        return prepared

    async def use_tools(self, index, script, identifier):
        """A turn whose model uses the existing text tools; the turn's context and the tools' results."""
        event, results = await text_tools.TelegramTextToolTests.run_tools(
            self, index, script, event=self.event_for(index, "text tool request", identifier))
        return event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"], results

    def learn(self, prepared, text, operation):
        context = prepared["context"]
        evidence = self.app.memory.writer.access.capture(context, prepared["native_source_future"].result())
        return require(self.app.memory.writer.submit(context, Proposal("add", predicate="drink",
            object=text, representation="The owner drinks " + text, evidence=(evidence,)), operation_key=operation))

    def fact(self, context, saved):
        return require(self.app.memory.writer.read(context, AtomRef(saved["atom_id"], 1)))["object"]

    def qq_disconnect(self):
        clients = self.native.platforms["qq-main"].client._wsr_api_clients
        self.qq_clients = dict(clients)
        clients.clear()

    def qq_connect(self):
        self.native.platforms["qq-main"].client._wsr_api_clients.update(self.qq_clients)

    async def remove_qq_from_configuration(self):
        """The administrator saves the account mapping without QQ; its channel is gone from AstrBot too."""
        candidate = self.telegram_only(self.controller.config)

        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))
        self.controller.save_plugin_config = save
        for platform_id in ("qq-main", "qq-second"):
            self.native.platforms.pop(platform_id, None)
        self.qq_configured = False
        saved = await self.controller.apply_bindings(candidate)
        self.assertTrue(saved["started"], saved)
        self.app = self.controller.session.application
        return saved

    async def new_process(self):
        """Stop everything and start again from the stored state and the current configuration."""
        await self.controller.terminate()
        self.manager = RecordedConversations(self.db)
        self.native.conversation_manager = self.core_context.conversation_manager = self.manager
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        self.app = self.controller.session.application

    async def history_seen_from(self, origin):
        cid = await self.manager.get_curr_conversation_id(origin)
        return self.contents(json.loads((await self.manager.get_conversation(origin, cid)).history))

    async def spoken_on(self, origin):
        """What the human and the companion said, without the tool traffic in between."""
        return [text for text in await self.history_seen_from(origin) if text and not text.startswith("{")]

    def health(self):
        status = self.controller.status()
        return ((status["state"], status["accepting"], status["last_failure"]),
                {row["platform_id"]: (row["state"] == "online", row["reason_code"] or None)
                 for row in status["account_connections"]})

    def link_record(self):
        return self.controller.session.linked_conversations.get("owner-dialogue")

    def consent_to_contact(self, *contexts):
        """Consent to proactive contact is each endpoint's own."""
        from companion_v2.identity_policy.models import AuthorizationTier
        identities = self.controller.authority.identities
        for context in contexts:
            if not identities.authorize(context.actor, scope=context.scope, capability="proactive.contact",
                                        observed_at=self.clock.now()).is_success:
                identities.issue_grant(grant_id="consent-" + context.key, subject=context.actor.subject,
                    scope=context.scope, capability="proactive.contact", tier=AuthorizationTier.STANDARD,
                    valid_from=self.clock.now(), expected_revision=identities.revision)

    def enable_contact(self, context):
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.proactive.configuration import GROUP, target_for
        target, now = target_for(context), self.clock.now()
        snapshot = require(self.app.configuration.read(context.actor, target=target, group_id=GROUP, observed_at=now))
        identifier = "contact-settings-" + snapshot.edit_token[:16]
        require(self.app.configuration.patch(context.actor, target=target, group_id=GROUP,
            expected_edit_token=snapshot.edit_token, changes={GROUP + ".enabled": True}, reset_to_inherit=(),
            operation=OperationRef(identifier, GROUP, target.authorization_scope, VersionRef(identifier, 1), identifier),
            observed_at=now))

    async def proactive_text(self, prepared, text):
        """The host composes one proactive text for this window; what became of its delivery."""
        from companion_v2.proactive.contact_models import ContactIntent
        from companion_v2.proactive.models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
        from tests.native_adapter_support import completion

        async def compose(payload, tools):
            return completion(json.dumps({"decision": "send", "text": text}))
        self.native.provider.client.state.behavior = compose
        context, now = prepared["context"], self.clock.now()
        opportunity = Opportunity("opportunity-" + text, context.scope, now, now + timedelta(minutes=5))
        intent = ContactIntent("chat", Thought("thought-" + text, opportunity, ThoughtKind.CONVERSATION,
            "a thought not yet sent", EvidenceSnapshot(context.scope, now, ()), 1.0))
        task = require(await self.app.proactive.contact(context, opportunity, (intent,),
            role_instruction=prepared["instruction"], selection_seed="seed-" + text)).task
        async with asyncio.timeout(30):
            while True:
                self.app.notify()
                row = self.app.proactive._delivery_row(task.task_id)
                if row is not None and row["state"] not in ("pending", "dispatched"):
                    return row["state"]
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.05)

    def nothing_goes_to_qq_from_here(self):
        self.sent.clear()
        self.qq_calls().clear()

    def sent_to_qq(self):
        """Replies through AstrBot's QQ event and the host's own QQ sends."""
        return [sent for platform, sent in self.sent if platform == "qq-main"] + list(self.qq_calls())


class RetiredQQScenario(TelegramAloneFixture):
    """One conversation built on both endpoints, then continued on Telegram with QQ retired."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # AstrBot's own command handlers, registered as its loader registers them.
        from astrbot.builtin_stars.builtin_commands.commands.conversation import ConversationCommands
        from astrbot.builtin_stars.builtin_commands.commands.setunset import SetUnsetCommands
        from astrbot.core import sp
        self.builtin("new", ConversationCommands(self.core_context).new_conv)
        method = SetUnsetCommands(self.core_context).set_variable
        self.builtin("set", method.__func__, method.__name__).handler = method
        for origin in self.origins:
            self.addAsyncCleanup(sp.session_put, origin, "session_variables", {})

    async def before_retirement(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.conversation.reply_settings import ReplyConfigurationOwner, configuration_fields, target_for
        from companion_v2.role_work.journal.models import JournalContext, JournalDraft, JournalKey, JournalKind
        from companion_v2.role_work.state.configuration import StateConfigurationOwner, configuration_fields as state_fields
        self.assertEqual((await self.link())["status"], "ok")
        on_qq = await self.turn(0, "from QQ", "1")
        self.qq = on_qq["context"]
        self.qq_source = on_qq["native_source_future"].result()
        self.tea = self.learn(on_qq, "tea", "learned-on-qq")
        _, noted = await self.use_tools(0, [("memo.create", {"title": "tea", "body": "noted on QQ"})], "2")
        self.note_id = noted[0]["payload"]["object_id"]
        life, at = self.app.life_context, self.clock.now()
        self.diary = JournalKey(life.owner(self.qq), JournalKind.DIARY, at.date().isoformat())
        life.journal.save(JournalDraft(JournalContext(self.diary, self.qq.scope, "UTC"), body="A day on QQ"),
                          expected_revision=0, operation_id="diary-on-qq")
        common = self.controller.settings.common
        self.fields = configuration_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        owner, target = ReplyConfigurationOwner(self.app.runtime), target_for(self.qq)
        owner.write(target, self.fields, changes={"conversation.presentation.file_understanding_max_chars": 12000},
            reset=(), expected_revision=owner.read(target, self.fields).revision, observed_at=self.clock.now())
        # A role setting made while QQ was there: the calendar command answers from it.
        role = ConfigTarget(self.qq.scope.installation_id, ConfigLayer.ROLE, self.qq.role_binding.role,
                            bot_id=self.qq.scope.bot_id)
        state = StateConfigurationOwner(self.app.runtime)
        state.write(role, state_fields(), changes={"role.state.calendar_enabled": False}, reset=(),
            expected_revision=state.read(role, state_fields()).revision, observed_at=self.clock.now())
        self.clock.advance(timedelta(seconds=2))
        self.telegram_before = (await self.turn(1, "from Telegram", 3))["context"]
        self.anchor = deepcopy(self.link_record()["anchor"])
        self.shared = ["old", "old-reply", "from QQ", "reply-A", "text tool request", "reply-A",
                       "from Telegram", "reply-B"]
        self.assertEqual(await self.spoken_on(TELEGRAM), self.shared)

    async def telegram_keeps_the_conversation(self):
        """Replies, tools, settings, logs and a reminder on Telegram after QQ was retired."""
        from companion_v2.conversation.linked_ownership import owner_scope
        from companion_v2.conversation.reply_settings import ReplyConfigurationOwner, target_for
        from companion_v2.model_gateway.core_observations import observations_for_role
        from companion_v2.role_work.state.ownership import role_schedule_key
        self.assertEqual(self.health()[0], ("running", True, None))
        prepared = await self.turn(1, "still here", 10)
        context = prepared["context"]
        # The same conversation and history, on the same stored owners.
        self.assertEqual(await self.manager.get_curr_conversation_id(TELEGRAM), "conversation-a")
        self.assertEqual(await self.spoken_on(TELEGRAM), self.shared + ["still here", "reply-B"])
        self.assertEqual(self.link_record()["anchor"], self.anchor)
        self.assertEqual(owner_scope(self.app.runtime, context.scope), self.qq.scope)
        self.assertEqual(context.actor.subject, self.qq.actor.subject)
        self.assertEqual(role_schedule_key(context), role_schedule_key(self.qq))
        # What QQ contributed is read in place, under Telegram's own permissions, with its source.
        self.assertEqual(self.fact(context, self.tea), "tea")
        self.assertEqual(self.app.memory.writer.access.capture(context, self.qq_source).source.scope.platform, "qq-main")
        archive = self.app.life_context.journal.get_for_audience(self.diary, scope=context.scope,
            recipient=context.actor.subject, observed_at=self.clock.now())
        self.assertEqual((archive.draft.body, archive.draft.context.scope.platform), ("A day on QQ", "qq-main"))
        self.assertEqual({row["source_channel"] for row in observations_for_role(self.app.runtime, context)},
                         {"qq-main", "tg-main"})
        settings = ReplyConfigurationOwner(self.app.runtime).read(target_for(context), self.fields)
        self.assertEqual(settings.fields["conversation.presentation.file_understanding_max_chars"].value, 12000)
        # Text tools: the note made on QQ, edited in place, and a new one.
        _, results = await self.use_tools(1, [("memo.read", {"memo_id": self.note_id}),
            ("memo.edit", {"memo_id": self.note_id, "expected_revision": 1, "changes": {"body": "kept on Telegram"}}),
            ("memo.create", {"title": "coffee", "body": "noted on Telegram"}), ("memo.list", {})], 11)
        self.assertEqual((results[0]["payload"]["body"], results[0]["payload"]["target"]["scope"]["platform"]),
                         ("noted on QQ", "qq-main"))
        self.assertEqual({item["title"] for item in results[3]["payload"]}, {"tea", "coffee"})
        self.assertEqual(self.app.agenda.repository.memo(self.note_id).target.scope, self.qq.scope)
        # A reminder asked for on Telegram arrives on Telegram.
        asked, occurrence = await self.remind(1, "stand up", identifier=12)
        self.assertEqual((await self.settled(asked, occurrence))["delivery"], "delivered")
        self.assertEqual(self.telegram_texts()[-1], (OWNER, "stand up"))
        # No QQ-only capability is offered to the Telegram turn, and none was attempted.
        origin, offered, prompt = self.requests[-1]
        self.assertEqual(origin, TELEGRAM)
        for qq_only in ("poke", "qzone", "presence", "forward", "recall", "relay", "voice", "image", "reaction"):
            self.assertFalse(any(qq_only in name for name in offered), (qq_only, offered))
        for qq_only in ("interaction.poke", "qzone", "relay"):
            self.assertNotIn(qq_only, prompt.lower())
        # A QQ status asked for the Telegram window is not started and reaches no client.
        from astrbot_host.multi_session import MultiPresence
        status = await MultiPresence(self.controller.session).custom_status(context, "在")
        self.assertEqual(status["state"], "not_started")
        self.assertEqual(self.telegram.client.onebot_accesses, [])
        return context

    async def telegram_commands_and_controls(self, context):
        """A text command, a session setting and a new conversation, all from Telegram."""
        from astrbot.core import sp
        calls = len(self.calls)
        await self.attempt(1, "生成日程", 13)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual((self.sent[-1][0], str(self.sent[-1][1]["chat_id"])), ("tg-main", OWNER))
        self.assertIn("角色日程总开关已关闭", self.sent[-1][1]["text"])
        event, _ = await self.attempt(1, "/set mood calm", 14)
        self.assertFalse(event.is_stopped())
        self.assertEqual(await sp.session_get(TELEGRAM, "session_variables", {}), {"mood": "calm"})
        await self.turn(1, "after the setting", 15)
        kept = await self.spoken_on(TELEGRAM)
        self.assertEqual(kept[-2:], ["after the setting", "reply-B"])
        event, _ = await self.attempt(1, "/new", 16)
        self.assertFalse(event.is_stopped())
        fresh = self.link_record()["conversation_id"]
        self.assertNotEqual(fresh, "conversation-a")
        self.assertEqual(await self.manager.get_curr_conversation_id(TELEGRAM), fresh)
        after = await self.turn(1, "a new conversation", 17)
        self.assertEqual(await self.spoken_on(TELEGRAM), ["a new conversation", "reply-B"])
        # The previous conversation is kept as AstrBot keeps it, and memory is not part of the context.
        previous = await self.manager.get_conversation(TELEGRAM, "conversation-a")
        self.assertEqual([text for text in self.contents(json.loads(previous.history))
                          if text and not text.startswith("{")], kept)
        self.assertEqual(self.fact(after["context"], self.tea), "tea")
        self.assertEqual(self.link_record()["anchor"], self.anchor)
        return fresh


class DisconnectedQQTests(RetiredQQScenario, unittest.IsolatedAsyncioTestCase):
    async def test_telegram_keeps_the_conversation_while_the_qq_client_is_disconnected(self):
        await self.before_retirement()
        self.qq_disconnect()
        self.assertEqual(self.health(), (("running", True, None),
            {"qq-main": (False, "host_account_unavailable"), "tg-main": (True, None)}))
        self.nothing_goes_to_qq_from_here()
        fresh = await self.telegram_commands_and_controls(await self.telegram_keeps_the_conversation())
        self.assertEqual(self.sent_to_qq(), [])
        # QQ is still a member: when its client returns it continues the same conversation.
        self.qq_connect()
        await self.turn(0, "QQ is back", "20")
        self.assertEqual(await self.manager.get_curr_conversation_id(QQ), fresh)
        self.assertEqual(await self.spoken_on(QQ), ["a new conversation", "reply-B", "QQ is back", "reply-A"])

    async def test_telegram_keeps_the_conversation_across_a_restart_without_the_qq_channel(self):
        await self.before_retirement()
        self.native.platforms.pop("qq-main")
        await self.new_process()
        self.assertEqual(self.health()[1]["qq-main"], (False, "host_platform_unavailable"))
        self.assertEqual(self.health()[1]["tg-main"], (True, None))
        await self.telegram_commands_and_controls(await self.telegram_keeps_the_conversation())


class UnlinkedQQTests(RetiredQQScenario, unittest.IsolatedAsyncioTestCase):
    async def test_telegram_keeps_the_conversation_after_qq_is_unlinked(self):
        await self.before_retirement()
        unlinked = await self.link(revision=1, members=["second"])
        self.assertEqual((unlinked["status"], unlinked["data"]["revision"]), ("ok", 2))
        self.nothing_goes_to_qq_from_here()
        await self.telegram_commands_and_controls(await self.telegram_keeps_the_conversation())
        self.assertEqual(self.sent_to_qq(), [])


class NoQQConfiguredTests(RetiredQQScenario, unittest.IsolatedAsyncioTestCase):
    async def test_telegram_keeps_the_conversation_with_qq_removed_from_configuration(self):
        await self.before_retirement()
        await self.remove_qq_from_configuration()
        self.assertEqual(self.health(), (("running", True, None), {"tg-main": (True, None)}))
        fresh = await self.telegram_commands_and_controls(await self.telegram_keeps_the_conversation())
        await self.new_process()
        self.assertEqual(self.health(), (("running", True, None), {"tg-main": (True, None)}))
        prepared = await self.turn(1, "after a restart", 30)
        self.assertEqual(await self.manager.get_curr_conversation_id(TELEGRAM), fresh)
        self.assertEqual((await self.spoken_on(TELEGRAM))[-2:], ["after a restart", "reply-B"])
        self.assertEqual(self.fact(prepared["context"], self.tea), "tea")


class RemindersAfterRetirementTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    """Reminders that were set on QQ, and reminders whose destination was QQ."""

    async def test_a_reminder_set_on_qq_arrives_on_telegram_while_the_qq_client_is_disconnected(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.remind(0, "water the plants", identifier="1")
        self.qq_disconnect()
        self.nothing_goes_to_qq_from_here()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "water the plants")], []))

    async def test_a_reminder_set_on_qq_arrives_on_telegram_with_qq_removed_from_configuration(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.remind(0, "water the plants", identifier="1")
        await self.remove_qq_from_configuration()
        self.sent.clear()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        self.assertEqual(self.telegram_texts(), [(OWNER, "water the plants")])

    async def test_a_reminder_set_on_qq_is_revoked_with_it_until_it_is_set_again_from_telegram(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.remind(0, "water the plants", identifier="1")
        kept = self.reminder_id
        _, stale = await self.remind(0, "feed the cat", identifier="2")
        left = self.reminder_id
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        self.nothing_goes_to_qq_from_here()
        # The records stay the conversation's: Telegram lists both and sets one of them again.
        _, listed = await self.use_tools(1, [("reminder.list", {})], 3)
        self.assertEqual({item["reminder_id"] for item in listed[0]["payload"]}, {kept, left})
        self.reminder_id = kept
        context, occurrence = await self.reschedule(1, identifier=4, minutes=10)
        self.sent.clear()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "water the plants")], []))
        dispatch = self.app.runtime.read_one("SELECT state, reason FROM agenda_dispatches WHERE occurrence_id = ?",
                                             (stale,))
        self.assertEqual((dispatch["state"], dispatch["reason"]), ("blocked", "scope_not_granted"))


class PreferredQQUnavailableTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    """The administrator had selected QQ for background text; QQ is then no longer there."""

    async def two_reminders_for_qq(self):
        self.assertEqual((await self.link(preferred_target_id="first"))["status"], "ok")
        context, first = await self.remind(1, "first reminder", identifier=1)
        _, later = await self.remind(1, "later reminder", identifier=2, minutes=30)
        self.assertEqual((await self.link_view())["background_destination"], "selected")
        return context, first, later

    async def select_telegram_and_wait_for(self, context, later, **members):
        chosen = await self.link(revision=1, preferred_target_id="second", **members)
        self.assertEqual((chosen["status"], chosen["data"]["preferred_target_id"]), ("ok", "second"))
        self.assertEqual((await self.link_view())["background_destination"], "selected")
        self.sent.clear()
        self.assertEqual((await self.settled(context, later, minutes=20))["delivery"], "delivered")
        await self.idle()
        # What was not yet dispatched follows the selection; what was already settled is not sent again.
        self.assertEqual(self.telegram_texts(), [(OWNER, "later reminder")])

    async def test_text_for_a_disconnected_qq_is_left_unsent_and_shown_until_telegram_is_selected(self):
        context, first, later = await self.two_reminders_for_qq()
        self.qq_disconnect()
        self.nothing_goes_to_qq_from_here()
        self.assertEqual((await self.link_view())["background_destination"], "unavailable")
        report = await self.settled(context, first)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "host_account_changed"))
        self.assertEqual(self.telegram_texts(), [])
        await self.select_telegram_and_wait_for(context, later)
        self.qq_connect()
        await self.idle()
        self.assertEqual(self.status(context, first)["delivery"], "cancelled")
        self.assertEqual(self.sent_to_qq(), [])

    async def test_text_for_a_qq_whose_channel_is_no_longer_loaded_is_left_unsent_until_telegram_is_selected(self):
        context, first, later = await self.two_reminders_for_qq()
        self.native.platforms.pop("qq-main")
        await self.new_process()
        self.sent.clear()
        self.assertEqual((await self.link_view())["background_destination"], "unavailable")
        report = await self.settled(context, first)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "host_platform_unavailable"))
        # QQ stays a member: the edit that selects Telegram needs nothing from QQ's channel.
        await self.select_telegram_and_wait_for(context, later)
        self.assertEqual([row["origin"] for row in self.link_record()["members"]], [QQ, TELEGRAM])

    async def test_text_for_a_qq_removed_from_configuration_is_left_unsent_and_shown_until_telegram_is_selected(self):
        context, first, later = await self.two_reminders_for_qq()
        await self.remove_qq_from_configuration()
        self.sent.clear()
        self.assertEqual((await self.link_view())["background_destination"], "missing")
        report = await self.settled(context, first)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "delivery_destination_missing"))
        self.assertEqual(self.telegram_texts(), [])
        await self.new_process()
        self.assertEqual((await self.link_view())["background_destination"], "missing")
        # A member list can only name configured targets, so this edit also takes QQ out of the link.
        refused = await self.link(revision=1, preferred_target_id="second")
        self.assertEqual((refused["status"], refused["data"]["code"]), ("error", "host_target_unavailable"))
        await self.select_telegram_and_wait_for(context, later, members=["second"])
        self.assertEqual(self.status(context, first)["delivery"], "cancelled")


class ProactiveAfterRetirementTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    superseded = "linked_conversation_continues_on_another_endpoint"

    async def both_have_spoken(self, last):
        """Both windows have a completed turn and their own consent; ``last`` spoke most recently."""
        self.assertEqual((await self.link())["status"], "ok")
        prepared = {}
        for index, identifier in ((1 - last, "1"), (last, "2")):
            self.clock.advance(timedelta(seconds=5))
            prepared[index] = await self.turn(index, "hello", identifier if index == 0 else int(identifier))
            observer = self.controller.session.core_reply_observations
            while pending := observer._pending - observer._intervention_tasks:
                await asyncio.gather(*tuple(pending))
                await asyncio.sleep(0)
        self.consent_to_contact(prepared[0]["context"], prepared[1]["context"])
        self.enable_contact(prepared[0]["context"])
        self.nothing_goes_to_qq_from_here()
        return prepared[0], prepared[1]

    def schedule_decision(self, prepared):
        self.app.contact_schedule.advance()
        return self.app.contact_schedule.diagnostic_for(prepared["context"])["evaluation"]["reason_code"]

    async def test_a_proactive_text_arrives_on_telegram_while_the_qq_client_is_disconnected(self):
        _, telegram = await self.both_have_spoken(last=1)
        self.qq_disconnect()
        self.assertNotEqual(self.schedule_decision(telegram), self.superseded)
        self.assertEqual(await self.proactive_text(telegram, "thinking of you"), "delivered")
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))

    async def test_a_proactive_text_arrives_on_telegram_after_qq_is_unlinked(self):
        qq, telegram = await self.both_have_spoken(last=0)
        # QQ had the conversation's most recent turn, so its window carried the schedule.
        self.assertEqual(self.schedule_decision(telegram), self.superseded)
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        self.clock.advance(timedelta(minutes=2))
        self.assertNotEqual(self.schedule_decision(telegram), self.superseded)
        self.assertEqual(await self.proactive_text(telegram, "thinking of you"), "delivered")
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))
        # Nothing is composed for the removed endpoint any more.
        refused = await self.app.proactive.contact(qq["context"], *self.thought(qq["context"]),
            role_instruction=qq["instruction"], selection_seed="removed")
        self.assertEqual(refused.failure.code, "conversation_link_revoked")

    def thought(self, context):
        from companion_v2.proactive.contact_models import ContactIntent
        from companion_v2.proactive.models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
        now = self.clock.now()
        opportunity = Opportunity("opportunity-removed", context.scope, now, now + timedelta(minutes=5))
        return opportunity, (ContactIntent("chat", Thought("thought-removed", opportunity, ThoughtKind.CONVERSATION,
            "a thought not yet sent", EvidenceSnapshot(context.scope, now, ()), 1.0)),)

    async def test_a_proactive_text_arrives_on_telegram_with_qq_removed_from_configuration(self):
        _, telegram = await self.both_have_spoken(last=1)
        await self.remove_qq_from_configuration()
        self.sent.clear()
        self.assertNotEqual(self.schedule_decision(telegram), self.superseded)
        self.assertEqual(await self.proactive_text(telegram, "thinking of you"), "delivered")
        self.assertEqual(self.telegram_texts(), [(OWNER, "thinking of you")])


class ContactLedgerFixture(TelegramAloneFixture):
    both_have_spoken = ProactiveAfterRetirementTests.both_have_spoken
    schedule_decision = ProactiveAfterRetirementTests.schedule_decision
    thought = ProactiveAfterRetirementTests.thought

    def contact_settings(self, context, **changes):
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.proactive.configuration import GROUP, target_for
        target, now = target_for(context), self.clock.now()
        snapshot = require(self.app.configuration.read(context.actor, target=target, group_id=GROUP, observed_at=now))
        identifier = "contact-settings-" + snapshot.edit_token[:16]
        require(self.app.configuration.patch(context.actor, target=target, group_id=GROUP,
            expected_edit_token=snapshot.edit_token, changes={GROUP + "." + key: value for key, value in changes.items()},
            reset_to_inherit=(), operation=OperationRef(identifier, GROUP, target.authorization_scope,
                                                        VersionRef(identifier, 1), identifier), observed_at=now))

    def deliveries_today(self, context):
        preferences = require(self.app.proactive.preferences.read(context, observed_at=self.clock.now()))
        usage = self.app.proactive.budget.usage_summary(context, preferences, self.clock.now())["deliveries"]["session"]
        return usage["used"], usage["limit"]

    async def composed(self, prepared, text, seed):
        """One contact composed for this window with this text; what was decided about it."""
        from tests.native_adapter_support import completion

        async def compose(payload, tools):
            return completion(json.dumps({"decision": "send", "text": text}))
        self.native.provider.client.state.behavior = compose
        context = prepared["context"]
        opportunity, intents = self.thought(context)
        opportunity = type(opportunity)("opportunity-" + seed, context.scope, opportunity.opened_at, opportunity.expires_at)
        intents = tuple(type(intent)(intent.purpose, type(intent.thought)("thought-" + seed, opportunity,
            intent.thought.kind, intent.thought.summary, intent.thought.evidence, intent.thought.weight))
            for intent in intents)
        task = require(await self.app.proactive.contact(context, opportunity, intents,
            role_instruction=prepared["instruction"], selection_seed=seed)).task
        async with asyncio.timeout(30):
            while True:
                self.app.notify()
                row = self.app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?",
                                                (task.task_id,))
                delivery = self.app.proactive._delivery_row(task.task_id)
                if row is not None and (json.loads(row[0])["decision"] == "skip" or (
                        delivery is not None and delivery["state"] not in ("pending", "dispatched"))):
                    await self.idle()  # the work has released its background slot
                    return json.loads(row[0])
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.05)


class OneContactLedgerTests(ContactLedgerFixture, unittest.IsolatedAsyncioTestCase):
    """How often the human is contacted is counted for the conversation, not per endpoint."""

    async def test_a_contact_composed_for_one_endpoint_leaves_no_allowance_for_the_other(self):
        qq, telegram = await self.both_have_spoken(last=1)
        self.contact_settings(qq["context"], max_contacts_24h=1)
        self.assertEqual([self.deliveries_today(prepared["context"]) for prepared in (qq, telegram)], [(0, 1), (0, 1)])
        self.assertEqual(await self.proactive_text(qq, "thinking of you"), "delivered")
        await self.idle()
        self.assertEqual([self.deliveries_today(prepared["context"]) for prepared in (qq, telegram)], [(1, 1), (1, 1)])
        for prepared in (telegram, qq):
            context = prepared["context"]
            refused = await self.app.proactive.contact(context, *self.thought(context),
                role_instruction=prepared["instruction"], selection_seed="no-allowance-left")
            self.assertEqual(refused.failure and refused.failure.code, "contact_delivery_budget")
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))

    async def test_a_text_already_sent_for_one_endpoint_is_not_sent_again_for_the_other(self):
        qq, telegram = await self.both_have_spoken(last=1)
        first = await self.composed(qq, "thinking of you", "said-once")
        self.assertEqual(first["decision"], "send")
        self.clock.advance(timedelta(minutes=1))
        repeated = await self.composed(telegram, "thinking of you", "said-again")
        self.assertEqual((repeated["decision"], repeated["dedup_reason"]), ("skip", "exact_repeat"))
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))

    async def test_the_pause_after_an_attempt_holds_on_every_endpoint_of_the_conversation(self):
        qq, telegram = await self.both_have_spoken(last=1)
        self.assertEqual(await self.proactive_text(qq, "thinking of you"), "delivered")
        await self.idle()  # the send has released its background slot
        # Telegram's window carries the schedule; the attempt was made from QQ's.
        self.contact_settings(qq["context"], automatic_enabled=True, idle_seconds=1, interval_seconds=600)
        self.clock.advance(timedelta(seconds=30))
        self.assertEqual(self.schedule_decision(telegram), "recent_attempt_cooldown")
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))


class OtherConversationTests(ContactLedgerFixture, unittest.IsolatedAsyncioTestCase):
    """What an endpoint is told outside a linked conversation neither counts nor shows inside it."""

    # One bot on both channels: only the conversation tells a member's window from its own.
    telegram_bot_id = "bot-a"

    def sent_in_the_background(self, context):
        return [item["text"] for item in require(self.app.background_history.recent(context))]

    async def test_a_contact_in_the_own_conversation_of_an_endpoint_that_left_stays_out_of_the_linked_one(self):
        qq, telegram = await self.both_have_spoken(last=1)
        self.contact_settings(qq["context"], max_contacts_24h=1)
        self.assertEqual((await self.link(revision=1, members=["first"], preferred_target_id="first"))["status"], "ok")
        self.assertEqual((await self.link(revision=2, members=["first"],
                                          independent_target_ids=["second"]))["status"], "ok")
        own = await self.turn(1, "on my own", 3)
        self.assertNotEqual(own["context"].scope.conversation_id, telegram["context"].scope.conversation_id)
        self.consent_to_contact(own["context"])
        self.enable_contact(own["context"])
        self.assertEqual(await self.proactive_text(own, "only for the other conversation"), "delivered")
        await self.idle()
        self.assertEqual(self.sent_in_the_background(own["context"]), ["only for the other conversation"])
        self.assertEqual((self.deliveries_today(qq["context"]), self.sent_in_the_background(qq["context"])),
                         ((0, 1), []))

    async def test_what_an_endpoint_was_told_before_it_joined_stays_out_of_the_linked_conversation(self):
        own = await self.turn(1, "before joining", 1)
        self.consent_to_contact(own["context"])
        self.enable_contact(own["context"])
        self.assertEqual(await self.proactive_text(own, "told before joining"), "delivered")
        await self.idle()
        # Telegram joins and does not speak: its member window has no turn yet.
        self.assertEqual((await self.link())["status"], "ok")
        qq = await self.turn(0, "hello", "2")
        self.consent_to_contact(qq["context"])
        self.contact_settings(qq["context"], enabled=True, max_contacts_24h=1)
        self.assertEqual((self.deliveries_today(qq["context"]), self.sent_in_the_background(qq["context"])),
                         ((0, 1), []))


    async def test_a_member_that_has_not_spoken_since_it_joined_holds_no_schedule_with_its_earlier_conversation(self):
        qq = await self.turn(0, "hello", "1")
        self.clock.advance(timedelta(minutes=10))
        own = await self.turn(1, "before joining", 2)
        self.consent_to_contact(qq["context"], own["context"])
        self.assertEqual((await self.link())["status"], "ok")
        self.contact_settings(qq["context"], enabled=True, automatic_enabled=True, idle_seconds=300,
                              interval_seconds=600)
        self.clock.advance(timedelta(seconds=30))
        # QQ has the conversation's only turn, ten minutes old: it carries, and the idle period is over.
        self.assertNotIn(self.schedule_decision(qq), (ProactiveAfterRetirementTests.superseded,
                                                     "idle_period_not_elapsed"))


class TwoLinkedConversationsTests(ContactLedgerFixture, unittest.IsolatedAsyncioTestCase):
    """Two humans each have a linked conversation on the one QQ bot; Telegram moves from one to the other."""

    extra_targets = ({"target_id": "outside", "platform_id": "qq-main", "account_id": "100",
                      "bot_id": "bot-a", "participant_id": "999"},)
    sent_in_the_background = OtherConversationTests.sent_in_the_background

    async def telegram_moved_to_the_other_conversation(self):
        """QQ's linked conversation, and Telegram's window in the other one with what it was told there."""
        qq, _ = await self.both_have_spoken(last=1)
        self.contact_settings(qq["context"], max_contacts_24h=1)
        self.assertEqual((await self.link(revision=1, members=["first"], preferred_target_id="first"))["status"], "ok")
        self.assertEqual((await self.link(revision=2, members=["first"],
                                          independent_target_ids=["second"]))["status"], "ok")
        await self.turn(1, "on my own", 3)
        # The other human's conversation, on the same QQ bot; Telegram joins it.
        other = "qq-main:FriendMessage:999"
        await self.db.create_conversation(other, "qq-main", cid="conversation-other", persona_id="original", content=[])
        await self.manager.switch_conversation(other, "conversation-other")
        from astrbot.core import sp
        self.addAsyncCleanup(sp.session_remove, other, "sel_conv_id")  # AstrBot keeps the selection per process
        joined = await self.link(link_id="other-dialogue", conversation_id="conversation-other",
                                 source_target_id="outside", members=["outside", "second"])
        self.assertEqual(joined["status"], "ok", joined)
        there, occurrence = await self.remind(1, "for the other human", identifier=4)
        self.assertNotEqual(there.scope.conversation_id, qq["context"].scope.conversation_id)
        self.assertEqual((await self.settled(there, occurrence))["delivery"], "delivered")
        self.consent_to_contact(there)
        self.enable_contact(there)
        prepared = await self.turn(1, "still in the other conversation", 5)
        self.assertEqual(await self.proactive_text(prepared, "also for the other human"), "delivered")
        await self.idle()
        self.assertEqual(sorted(self.sent_in_the_background(prepared["context"])),
                         ["also for the other human", "for the other human"])
        return qq["context"], prepared["context"]

    async def test_what_an_endpoint_is_told_in_its_next_linked_conversation_stays_out_of_the_one_it_left(self):
        qq, _ = await self.telegram_moved_to_the_other_conversation()
        self.assertEqual((self.deliveries_today(qq), self.sent_in_the_background(qq)), ((0, 1), []))

    async def test_a_new_conversation_started_in_the_one_it_left_resets_nothing_in_its_next_one(self):
        from astrbot.builtin_stars.builtin_commands.commands.conversation import ConversationCommands
        self.builtin("new", ConversationCommands(self.core_context).new_conv)
        _, telegram = await self.telegram_moved_to_the_other_conversation()
        event, _ = await self.attempt(0, "/new", "6")
        self.assertFalse(event.is_stopped())
        self.assertNotEqual(self.link_record()["conversation_id"], "conversation-a")
        self.assertEqual(sorted(self.sent_in_the_background(telegram)),
                         ["also for the other human", "for the other human"])

    async def test_a_full_reset_in_the_one_it_left_resets_nothing_in_its_next_one(self):
        import re
        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        qq, telegram = await self.telegram_moved_to_the_other_conversation()
        policy = ObjectAccessPolicy(self.app.runtime)
        policy.set_tier(qq.actor.subject, AuthorizationTier.CORE, expected_revision=policy.revision,
                        observed_at=self.clock.now())
        await self.attempt(0, "陪伴 全部重置", "6")
        code = re.search(r"确认 ([0-9A-F]{6})", str(self.qq_calls()[-1])).group(1)
        await self.attempt(0, "陪伴 全部重置 确认 " + code, "7")
        self.assertIn("已执行", str(self.qq_calls()[-1]))
        self.assertEqual(sorted(self.sent_in_the_background(telegram)),
                         ["also for the other human", "for the other human"])


class ScheduleCarrierTests(ContactLedgerFixture, unittest.IsolatedAsyncioTestCase):
    """One proactive schedule per conversation, carried by a member that can carry it now."""

    superseded = ProactiveAfterRetirementTests.superseded

    async def evaluated(self, *prepared):
        """A complete schedule pass after native observation and any current scan."""
        schedule = self.app.contact_schedule
        observer = self.controller.session.core_reply_observations
        async with asyncio.timeout(30):
            while pending := observer._pending - observer._intervention_tasks:
                await asyncio.gather(*tuple(pending))
                await asyncio.sleep(0)
            while schedule._advancing:
                await asyncio.sleep(0.05)
        schedule.advance()
        return [schedule.diagnostic_for(item["context"])["evaluation"]["reason_code"] for item in prepared]

    async def contact_recorded(self, prepared):
        """Wait until the conversation's ledger counts the contact that was just sent.

        The channel accepts the text before the delivery is recorded, and a schedule
        pass in between queues another candidate instead of reporting the cooldown.
        """
        async with asyncio.timeout(30):
            while not self.deliveries_today(prepared["context"])[0]:
                self.app.notify()
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.05)
        await self.idle()

    async def test_telegram_carries_the_schedule_while_the_qq_that_spoke_last_is_disconnected(self):
        from tests.native_adapter_support import completion

        async def compose(payload, tools):
            return completion(json.dumps({"decision": "send", "text": "thinking of you"}))
        self.native.provider.client.state.behavior = compose
        qq, telegram = await self.both_have_spoken(last=0)
        self.qq_disconnect()
        self.contact_settings(telegram["context"], automatic_enabled=True, idle_seconds=1, interval_seconds=600)
        self.clock.advance(timedelta(seconds=30))
        # QQ's window has the latest turn and cannot carry anything; Telegram's does, with its own consent.
        self.assertEqual(await self.evaluated(telegram), ["candidate_queued"])
        async with asyncio.timeout(30):
            while not self.telegram_texts():
                self.app.notify()
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.05)
        await self.contact_recorded(telegram)
        self.assertEqual(await self.evaluated(qq, telegram), [self.superseded, "recent_attempt_cooldown"])
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))
        self.assertEqual((self.link_record()["preferred_target_id"], (await self.link_view())["background_destination"]),
                         ("second", "selected"))
        # QQ returns. Its window carries the one schedule again and counts what was sent meanwhile.
        self.qq_connect()
        self.clock.advance(timedelta(seconds=30))
        self.assertEqual(await self.evaluated(qq, telegram), ["recent_attempt_cooldown", self.superseded])
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.sent_to_qq()), ([(OWNER, "thinking of you")], []))


    async def test_telegram_carries_the_schedule_when_the_qq_that_spoke_last_rejoined_without_speaking(self):
        from tests.native_adapter_support import completion

        async def compose(payload, tools):
            return completion(json.dumps({"decision": "send", "text": "thinking of you"}))
        self.native.provider.client.state.behavior = compose
        qq, telegram = await self.both_have_spoken(last=0)
        # QQ leaves and rejoins. Its last turn was captured before it left, and nothing runs from it.
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        self.assertEqual((await self.link(revision=2, members=["second", "first"]))["status"], "ok")
        self.contact_settings(telegram["context"], automatic_enabled=True, idle_seconds=1, interval_seconds=600)
        self.clock.advance(timedelta(seconds=30))
        self.assertEqual(await self.evaluated(telegram), ["candidate_queued"])
        async with asyncio.timeout(30):
            while not self.telegram_texts():
                self.app.notify()
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.05)
        await self.contact_recorded(telegram)
        self.assertEqual(await self.evaluated(qq, telegram), [self.superseded, "recent_attempt_cooldown"])
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([(OWNER, "thinking of you")], []))
        # QQ speaks again: its new turn carries the one schedule and counts what was sent meanwhile.
        back = await self.turn(0, "back again", "3")
        self.clock.advance(timedelta(seconds=30))
        self.assertEqual(await self.evaluated(back, telegram), ["recent_attempt_cooldown", self.superseded])
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([(OWNER, "thinking of you")], []))


class UnlinkAndRelinkQQTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    def access(self, context, action):
        result = self.app.tasks.check_context_access(context, action=action, observed_at=self.clock.now())
        return "allowed" if result.is_success else result.failure.code

    async def test_unlinking_qq_fences_its_earlier_work_and_relinking_it_joins_the_same_conversation(self):
        self.assertEqual((await self.link())["status"], "ok")
        event, stale = await self.attempt(0, "from QQ", "1")
        qq = stale["context"]
        _, unsent = await self.remind(0, "set on QQ, never sent", identifier="2")
        anchor = deepcopy(self.link_record()["anchor"])
        self.assertEqual(self.access(qq, "memory.read"), "allowed")

        unlinked = await self.link(revision=1, members=["second"])
        self.assertEqual((unlinked["status"], unlinked["data"]["revision"]), ("ok", 2))
        self.nothing_goes_to_qq_from_here()
        record = self.link_record()
        self.assertEqual(([row["origin"] for row in record["members"]], [row["origin"] for row in record["detached"]],
                          record["anchor"]), ([TELEGRAM], [QQ], anchor))
        # Its saved context reads, replies, contacts and delivers nothing more.
        for action in ("memory.read", "conversation.reply", "proactive.contact", "reminder.deliver"):
            self.assertEqual(self.access(qq, action), "conversation_link_revoked", action)
        refused = await self.controller.authorize_core_reply(event, stale)
        self.assertIn(refused.failure.code, ("linked_revision_changed", "linked_endpoint_revoked"))
        closed, prepared = await self.attempt(0, "still here?", "3")
        self.assertTrue(closed.is_stopped())
        self.assertIsNone(prepared)
        self.assertNotIn(await self.manager.get_curr_conversation_id(QQ), (None, "conversation-a"))
        # Telegram goes on, and learns something while QQ is out.
        on_telegram = await self.turn(1, "from Telegram", 4)
        coffee = self.learn(on_telegram, "coffee", "learned-while-qq-was-out")
        self.clock.advance(timedelta(minutes=10, seconds=1))
        await self.idle()
        dispatch = self.app.runtime.read_one("SELECT state, reason FROM agenda_dispatches WHERE occurrence_id = ?",
                                             (unsent,))
        self.assertEqual((dispatch["state"], dispatch["reason"]), ("blocked", "scope_not_granted"))
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([(OWNER, "reply\\-B")], []))

        # Linked again through the normal operation: the same conversation, owners and history.
        relinked = await self.link(revision=2, members=["second", "first"])
        self.assertEqual((relinked["status"], relinked["data"]["revision"], relinked["data"]["detached"]),
                         ("ok", 3, []))
        self.assertEqual((self.link_record()["anchor"], self.link_record()["conversation_id"]),
                         (anchor, "conversation-a"))
        self.assertEqual(await self.manager.get_curr_conversation_id(QQ), "conversation-a")
        back = await self.turn(0, "back on QQ", "5")
        self.assertEqual(await self.spoken_on(QQ), ["old", "old-reply", "from QQ", "reply-A", "remind me", "reply-A",
            "from Telegram", "reply-B", "back on QQ", "reply-A"])
        self.assertEqual(back["context"].actor.subject, qq.actor.subject)
        self.assertEqual(self.fact(back["context"], coffee), "coffee")
        # Nothing prepared or left unsent under the earlier membership comes back.
        revived = await self.controller.authorize_core_reply(event, stale)
        self.assertEqual(revived.failure.code, "linked_revision_changed")
        await self.idle()
        self.assertEqual(self.status(qq, unsent)["delivery"], "not_started")
        self.assertEqual(self.qq_texts(), [])
        # While it was removed QQ was told the conversation is unavailable; since then, its new reply.
        self.assertEqual([sent["message"][0]["data"]["text"] for platform, sent in self.sent if platform == "qq-main"],
                         ["共享对话暂不可用，请等待当前对话结束或由管理员检查连接。", "reply-A"])

    async def test_removing_the_only_remaining_endpoint_is_refused_with_its_reason_and_changes_nothing(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        before = deepcopy(self.link_record())
        refused = await self.link(revision=2, members=[])
        self.assertEqual((refused["status"], refused["data"]["code"]), ("error", "conversation_link_last_endpoint"))
        self.assertIn("at least one endpoint", refused["reason"])
        # Neither can the last member be turned loose as an independent endpoint.
        independent = await self.link(revision=2, members=[], independent_target_ids=["second"])
        self.assertEqual(independent["data"]["code"], "conversation_link_last_endpoint")
        self.assertEqual(self.link_record(), before)
        await self.turn(1, "still the conversation", 2)
        self.assertEqual(await self.spoken_on(TELEGRAM),
                         ["old", "old-reply", "from QQ", "reply-A", "still the conversation", "reply-B"])
        # A link that does not exist yet keeps the general validation answer.
        invalid = await self.link(link_id="another", members=[])
        self.assertEqual(invalid["data"]["code"], "conversation_link_invalid")


class RejoinFenceTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    """What an endpoint captured before it was removed stays refused after it rejoins."""

    access = UnlinkAndRelinkQQTests.access
    thought = ProactiveAfterRetirementTests.thought

    async def work_captured_before_a_removal_stays_refused_after_rejoining(self, leaving):
        staying, names = 1 - leaving, ("first", "second")
        # Background text goes to the member that stays, so nothing here depends on the one that leaves.
        self.assertEqual((await self.link(preferred_target_id=names[staying]))["status"], "ok")
        event, stale = await self.attempt(leaving, "before leaving", "1")
        captured = stale["context"]
        _, pending = await self.remind(leaving, "set before leaving", identifier="2", minutes=30)
        set_before_leaving = self.reminder_id
        keeper, kept = await self.remind(staying, "set by the member that stays", identifier="3", minutes=30)
        self.assertEqual(self.access(captured, "memory.read"), "allowed")
        self.assertEqual((await self.link(revision=1, members=[names[staying]]))["status"], "ok")
        rejoined = await self.link(revision=2, members=[names[staying], names[leaving]])
        self.assertEqual((rejoined["status"], rejoined["data"]["detached"]), ("ok", []))
        self.sent.clear()
        self.qq_calls().clear()
        # The saved context, a reply prepared with it and a contact composed for it are refused.
        for action in ("memory.read", "conversation.reply", "proactive.contact", "reminder.deliver",
                       "memo.read", "extension.tool.invoke"):
            self.assertEqual(self.access(captured, action), "role_binding_changed", action)
        self.assertFalse((await self.controller.authorize_core_reply(event, stale)).is_success)
        contact = await self.app.proactive.contact(captured, *self.thought(captured),
            role_instruction=stale["instruction"], selection_seed="captured-before-leaving")
        self.assertEqual(contact.failure.code, "role_binding_changed")
        # The reminder it had set comes due after it rejoined and is not delivered.
        self.assertEqual((await self.settled(keeper, kept, minutes=30))["delivery"], "delivered")
        await self.idle()
        dispatch = self.app.runtime.read_one("SELECT state, reason FROM agenda_dispatches WHERE occurrence_id = ?",
                                             (pending,))
        self.assertEqual((dispatch["state"], dispatch["reason"]), ("blocked", "role_binding_changed"))
        delivered = self.telegram_texts() if staying == 1 else self.qq_texts()
        self.assertEqual([text for _, text in delivered], ["set by the member that stays"])
        # Fresh work after rejoining is accepted: a turn, and the same reminder once it is set again.
        fresh = await self.turn(leaving, "back again", "5")
        self.assertEqual(self.access(fresh["context"], "memory.read"), "allowed")
        self.assertEqual(self.access(captured, "memory.read"), "role_binding_changed")
        self.reminder_id = set_before_leaving
        context, occurrence = await self.reschedule(leaving, identifier="6", minutes=10)
        self.assertEqual((await self.settled(context, occurrence))["delivery"], "delivered")
        delivered = self.telegram_texts() if staying == 1 else self.qq_texts()
        self.assertEqual([text for _, text in delivered][-1], "set before leaving")

    async def test_work_of_an_endpoint_left_out_when_its_conversation_was_linked_stays_refused_after_it_joins(self):
        _, stale = await self.attempt(0, "before the link", "1")
        captured = stale["context"]
        self.assertEqual(self.access(captured, "memory.read"), "allowed")
        # The conversation is linked without the endpoint it started on, which is closed from then on.
        created = await self.link(members=["second"])
        self.assertEqual((created["status"], [row["origin"] for row in created["data"]["detached"]]), ("ok", [QQ]))
        self.assertEqual(self.access(captured, "memory.read"), "conversation_link_revoked")
        self.assertEqual((await self.link(revision=1, members=["second", "first"]))["status"], "ok")
        self.assertEqual(self.access(captured, "memory.read"), "role_binding_changed")
        fresh = await self.turn(0, "a member now", "2")
        self.assertEqual(self.access(fresh["context"], "memory.read"), "allowed")
        await self.turn(1, "and the other member", 3)

    async def test_work_of_the_endpoint_the_conversation_started_on_stays_refused_after_it_rejoins(self):
        await self.work_captured_before_a_removal_stays_refused_after_rejoining(leaving=0)

    async def test_work_of_the_joined_endpoint_stays_refused_after_it_rejoins(self):
        await self.work_captured_before_a_removal_stays_refused_after_rejoining(leaving=1)


class OneCompanionWithoutQQTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_start_without_any_qq_channel_has_one_identity_one_set_of_owners_and_one_schedule(self):
        from companion_v2.conversation.linked_ownership import owner_scope
        from companion_v2.execution.models import TaskError
        from companion_v2.role_work.state.ownership import role_owner, role_schedule_key
        self.assertEqual((await self.link())["status"], "ok")
        qq = (await self.turn(0, "from QQ", "1"))["context"]
        before = (await self.turn(1, "from Telegram", 2))["context"]
        await self.remove_qq_from_configuration()
        await self.new_process()

        self.assertEqual(self.health(), (("running", True, None), {"tg-main": (True, None)}))
        self.assertEqual([target.target_id for target in self.controller.settings.targets], ["second"])
        telegram = self.controller.authority.context("second", "role-a")
        # One identity and one set of owners: those the conversation had on QQ.
        self.assertEqual((telegram.actor.subject, telegram.role_binding.role), (qq.actor.subject, qq.role_binding.role))
        self.assertEqual(telegram.scope, before.scope)
        self.assertEqual(owner_scope(self.app.runtime, telegram.scope), qq.scope)
        self.assertEqual((role_owner(telegram), role_schedule_key(telegram)), (role_owner(qq), role_schedule_key(qq)))
        # One background schedule, bound to the endpoint that is there.
        life = self.app.life_runtime
        routines = {id(routine): routine for routine in life.routines.values() if routine.instruction is not None}
        self.assertEqual([routine.context.scope.platform for routine in routines.values()], ["tg-main"])
        self.assertEqual([context.scope.platform for context, _, _ in self.app.state_maintenance.bindings.values()],
                         ["tg-main"])
        # Nothing can be scheduled or sent under the endpoint that is no longer configured.
        with self.assertRaises(TaskError) as gone:
            self.controller.authority.context("first", "role-a")
        self.assertEqual(gone.exception.code, "host_context_unavailable")
        view = await self.link_view()
        self.assertEqual((view["revision"], view["preferred_target_id"], view["background_destination"]),
                         (1, "second", "selected"))
        # The administrator can still take the unconfigured QQ endpoint out of the link.
        unlinked = await self.link(revision=1, members=["second"])
        self.assertEqual((unlinked["status"], [row["origin"] for row in unlinked["data"]["detached"]]), ("ok", [QQ]))
        prepared = await self.turn(1, "alone", 3)
        self.assertEqual((await self.spoken_on(TELEGRAM))[-2:], ["alone", "reply-B"])
        self.assertEqual(prepared["context"].actor.subject, qq.actor.subject)


class RoutinesWithoutQQTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    async def test_the_daily_routine_runs_under_telegram_after_a_start_without_any_qq_channel(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.role_work.life.configuration import LifeConfigurationOwner, configuration_fields
        # Each endpoint enables its routines itself, before any link exists: grants are never shared.
        for index, name, identifier in ((0, "first", "1"), (1, "second", 2)):
            context = (await self.turn(index, "before linking", identifier))["context"]
            target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                                  context.scope)
            owner = LifeConfigurationOwner(self.app.runtime)
            owner.write(target, configuration_fields(), changes={"role.life.enabled": True}, reset=(),
                expected_revision=owner.read(target, configuration_fields()).revision, observed_at=self.clock.now())
            self.controller.authority.select(self.controller.settings.target(name), context.scope,
                                             context.role_binding.role)
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "good morning", "3")
        await self.turn(1, "good morning again", 4)
        await self.remove_qq_from_configuration()
        await self.new_process()

        telegram = self.controller.authority.context("second", "role-a")
        routine = self.app.life_runtime.for_context(telegram)
        routine.advance()
        ran = require(await routine.diary(local_date=self.clock.now().date().isoformat()))
        task = self.app.runtime.read_one("SELECT origin_key FROM execution_tasks WHERE task_id = ?",
                                         (ran.writing_task_id,))
        self.assertEqual(task["origin_key"], telegram.key)
        # One routine and one daily schedule for the conversation, under the endpoint that is there.
        self.assertEqual([key for key in self.app.journal_schedule._schedules if key.startswith("role.life.daily:")],
                         ["role.life.daily:" + telegram.key])


class LinkedMappingSaveTests(TelegramAloneFixture, unittest.IsolatedAsyncioTestCase):
    async def test_saving_the_unchanged_account_mapping_of_a_linked_conversation_restarts_the_host(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        await self.turn(1, "from Telegram", 2)
        self.controller.save_plugin_config = lambda document: None
        saved = await self.controller.apply_bindings(deepcopy(self.controller.config))
        self.assertTrue(saved["started"], saved)
        self.app = self.controller.session.application
        await self.turn(0, "after the save", "3")
        await self.turn(1, "after the save", 4)
        self.assertEqual((await self.history_seen_from(QQ))[-4:],
                         ["after the save", "reply-A", "after the save", "reply-B"])

    async def test_a_mapping_save_that_removes_the_last_configured_endpoint_of_a_link_is_refused_before_anything_stops(self):
        from companion_v2.execution.models import TaskError
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        await self.turn(1, "from Telegram", 2)
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        written, session, before = [], self.controller.session, deepcopy(self.controller.config)
        self.controller.save_plugin_config = written.append
        candidate = deepcopy(before)
        host = candidate["multi_host"]
        host["targets"] = [row for row in host["targets"] if row["platform_id"] != "tg-main"]
        host["personas"] = [row for row in host["personas"] if row["bot_id"] != "bot-tg"]
        with self.assertRaises(TaskError) as refused:
            await self.controller.apply_bindings(candidate)
        self.assertEqual(refused.exception.code, "conversation_link_last_endpoint")
        self.assertIn("at least one", str(refused.exception))
        # Nothing was stopped or saved: the same session goes on with the same mapping and link.
        self.assertEqual((written, self.controller.config), ([], before))
        self.assertIs(self.controller.session, session)
        self.assertEqual(self.health()[0], ("running", True, None))
        self.assertEqual([row["origin"] for row in self.link_record()["members"]], [TELEGRAM])
        await self.turn(1, "still the conversation", 3)

    async def test_a_corrected_mapping_is_saved_and_started_after_a_stored_one_that_did_not_resolve(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        corrected = deepcopy(self.controller.config)
        await self.controller.terminate()
        # The stored mapping names a persona of no registered bot: the host has no settings and does not run.
        stored = deepcopy(corrected)
        stored["multi_host"]["personas"].append(dict(bot_id="bot-gone", persona_id="original", role_id="role-a"))
        self.manager = RecordedConversations(self.db)
        self.native.conversation_manager = self.core_context.conversation_manager = self.manager
        self.controller = MultiFixture.make_controller(self, stored)
        self.plugin.staging = self.controller
        self.assertIsNone(self.controller.settings)
        written = []

        def save(document):
            written.append(deepcopy(document))
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))
        self.controller.save_plugin_config = save
        saved = await self.controller.apply_bindings(deepcopy(corrected))
        self.assertEqual((saved["started"], saved.get("startup_error"), written), (True, None, [corrected]), saved)
        self.assertEqual(self.health()[0], ("running", True, None))
        self.app = self.controller.session.application
        await self.turn(1, "still the conversation", 2)
        self.assertEqual((await self.spoken_on(TELEGRAM))[-4:],
                         ["from QQ", "reply-A", "still the conversation", "reply-B"])

    async def saved_with_a_removed_endpoint_still_configured(self, removed):
        remaining, names = 1 - removed, ("first", "second")
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        await self.turn(1, "from Telegram", 2)
        self.assertEqual((await self.link(revision=1, members=[names[remaining]]))["status"], "ok")
        self.controller.save_plugin_config = lambda document: None
        saved = await self.controller.apply_bindings(deepcopy(self.controller.config))
        self.assertEqual((saved["started"], saved.get("startup_error")), (True, None), saved)
        self.assertEqual(self.health()[0], ("running", True, None))
        self.app = self.controller.session.application
        # The remaining endpoint goes on; the removed one is as closed as before the save.
        await self.turn(remaining, "after the save", "3")
        closed, prepared = await self.attempt(removed, "still closed", "4")
        self.assertTrue(closed.is_stopped())
        self.assertIsNone(prepared)

    async def test_saving_the_mapping_starts_the_host_while_the_removed_qq_endpoint_stays_configured(self):
        await self.saved_with_a_removed_endpoint_still_configured(removed=0)

    async def test_saving_the_mapping_starts_the_host_while_the_removed_telegram_endpoint_stays_configured(self):
        await self.saved_with_a_removed_endpoint_still_configured(removed=1)


if __name__ == "__main__":
    unittest.main()
