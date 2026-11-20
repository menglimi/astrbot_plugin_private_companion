"""One linked conversation continued over QQ and AstrBot's Telegram channel."""
from tests import astrbot_data_root  # noqa: F401

import asyncio
import json
import unittest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.conversation.linked_ownership import link_for_endpoint
from tests import test_linked_owners as owners
from tests.linked_reply_support import LinkedTelegramFixture, TELEGRAM_OWNER as OWNER

QQ, TELEGRAM = LinkedTelegramFixture.origins


class TelegramLinkedConversationTests(LinkedTelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def turn(self, index, text, identifier):
        event = self.event_for(index, text, identifier)
        await asyncio.wait_for(await self.dispatch(event), 20)
        await self.controller.session.linked_conversations.gate.drain()
        return event

    async def test_qq_then_telegram_then_qq_continue_one_history_and_each_reply_returns_to_its_endpoint(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(0, "from QQ", "1")
        await self.turn(1, "from Telegram", 2)
        await self.turn(0, "back on QQ", "3")
        self.assertEqual(self.contents(await self.history()), ["old", "old-reply", "from QQ", "reply-A",
            "from Telegram", "reply-B", "back on QQ", "reply-A"])
        # Each model call saw the turns completed on the other channel, under its own physical origin.
        self.assertEqual([call["session_id"] for call in self.calls], [QQ, TELEGRAM, QQ])
        self.assertEqual(self.contents(self.calls[1]["contexts"])[1:5], ["old", "old-reply", "from QQ", "reply-A"])
        self.assertEqual(self.contents(self.calls[2]["contexts"])[5:7], ["from Telegram", "reply-B"])
        self.assertEqual([write[0] for write in self.manager.writes], [QQ, TELEGRAM, QQ])
        self.assertEqual([platform for platform, _ in self.sent], ["qq-main", "tg-main", "qq-main"])
        telegram = self.sent[1][1]
        self.assertEqual((str(telegram["chat_id"]), telegram["text"]), (str(OWNER), "reply\\-B"))
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_a_linked_telegram_endpoint_still_reaches_its_own_telegram_connection(self):
        """Linked, the Telegram scope carries the link's bot id, not the Telegram account's own."""
        self.assertEqual((await self.link())["status"], "ok")
        session = self.controller.session
        target = session.settings.target("second")
        link = link_for_endpoint(session.authority.runtime, target.platform_id, target.account_id, target.participant_id)
        scope = session.scope(target, link.anchor.conversation_id)
        self.assertNotEqual(scope.bot_id, target.bot_id)
        self.assertIs(session.bridge.client(scope), self.telegram.client)

    async def stored(self, origin, conversation_id):
        conversation = await self.manager.get_conversation(origin, conversation_id)
        return self.contents(json.loads(conversation.history))

    async def test_the_conversation_telegram_held_while_independent_is_left_intact_and_no_longer_selected(self):
        await self.turn(1, "while independent", 1)
        independent = ["separate-old", "separate-reply", "while independent", "reply-B"]
        self.assertEqual(await self.stored(TELEGRAM, "conversation-b"), independent)
        self.assertEqual(self.contents(await self.history()), ["old", "old-reply"])
        self.assertEqual([platform for platform, _ in self.sent], ["tg-main"])
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(1, "after linking", 2)
        self.assertEqual(await self.manager.get_curr_conversation_id(TELEGRAM), "conversation-a")
        self.assertEqual(await self.stored(TELEGRAM, "conversation-b"), independent)
        self.assertEqual(self.contents(await self.history()), ["old", "old-reply", "after linking", "reply-B"])
        self.assertNotIn("while independent", str(self.calls[1]["contexts"]))

    async def serialized(self, holder, waiter):
        """The waiter's turn starts only after the holder's turn is saved; both use message number 7."""
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(holder)
        self.pause_after_response = self.manager.pause_save = True
        first = await self.dispatch(self.event_for(holder, "held", 7))
        await asyncio.wait_for(self.model_done.wait(), 15)
        second = await self.held_at_permit(self.event_for(waiter, "waiting", 7))
        self.allow_model.set()
        await asyncio.wait_for(self.manager.saving.wait(), 10)
        await asyncio.sleep(.05)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(second.done())
        self.manager.allow_save.set()
        await asyncio.wait_for(asyncio.gather(first, second), 20)
        await self.controller.session.linked_conversations.gate.drain()
        self.assertEqual(self.manager.tasks, [first, second])
        self.assertEqual([write[0] for write in self.manager.writes], [self.origins[holder], self.origins[waiter]])
        self.assertIn("held", str(self.calls[1]["contexts"]))
        # The same native message arriving again on either channel changes nothing.
        for index in (holder, waiter):
            await asyncio.wait_for(await self.dispatch(self.event_for(index, "again", 7)), 15)
        await self.controller.session.linked_conversations.gate.drain()
        self.assertEqual((len(self.calls), len(self.manager.writes), len(self.sent)), (2, 2, 2))

    async def test_a_qq_message_waits_for_the_telegram_turn_and_equal_numbers_and_redelivery_have_one_effect(self):
        await self.serialized(1, 0)
        self.assertEqual(self.contents(await self.history()),
            ["old", "old-reply", "held", "reply-B", "waiting", "reply-A"])
        self.assertEqual([platform for platform, _ in self.sent], ["tg-main", "qq-main"])

    async def test_a_telegram_message_waits_for_the_qq_turn_and_equal_numbers_and_redelivery_have_one_effect(self):
        await self.serialized(0, 1)
        self.assertEqual(self.contents(await self.history()),
            ["old", "old-reply", "held", "reply-A", "waiting", "reply-B"])
        self.assertEqual([platform for platform, _ in self.sent], ["qq-main", "tg-main"])

    async def refused(self, event):
        """Dispatch an event that must end before any history load, model call or save."""
        before = (len(self.manager.reads), len(self.calls), len(self.manager.writes))
        await asyncio.wait_for(await self.dispatch(event), 15)
        await self.controller.session.linked_conversations.gate.drain()
        self.assertTrue(event.is_stopped())
        self.assertEqual((len(self.manager.reads), len(self.calls), len(self.manager.writes)), before)

    async def test_telegram_endpoint_checks_and_the_link_revision_refuse_a_turn_before_any_model_work(self):
        from types import SimpleNamespace as NS
        self.assertEqual((await self.link())["status"], "ok")
        # A private topic shares the linked chat's origin and is not the linked conversation.
        await self.refused(self.event_for(1, "in a topic", 1, message_thread_id=9))
        self.telegram.application.running = False
        await self.refused(self.event_for(1, "application stopped", 2))
        self.telegram.application.running = True
        stale = self.event_for(1, "from a replaced client", 3)
        current, application = self.telegram.client, self.telegram.application
        self.telegram.client = type(current)(id=current.id, onebot_accesses=[])
        self.telegram.application = NS(bot=self.telegram.client, running=True)
        await self.refused(stale)
        self.telegram.client, self.telegram.application = current, application
        # A refusal notice goes to the chat that was refused, never to the other channel.
        self.assertNotIn("qq-main", [platform for platform, _ in self.sent])
        admitted = await self.turn(1, "admitted", 4)
        self.assertEqual(self.contents(await self.history()), ["old", "old-reply", "admitted", "reply-B"])
        # The administrator detaches Telegram: prepared work and new messages are fenced.
        prepared = admitted.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        self.assertEqual((await self.link(revision=1, members=["first"]))["status"], "ok")
        denied = await self.controller.authorize_core_reply(admitted, prepared)
        self.assertEqual(denied.failure.code, "linked_revision_changed")
        await self.refused(self.event_for(1, "after detaching", 5))
        await self.turn(0, "QQ continues", "6")
        self.assertEqual(self.contents(await self.history()),
            ["old", "old-reply", "admitted", "reply-B", "QQ continues", "reply-A"])

    async def test_a_link_revision_that_changes_during_a_telegram_turn_stops_its_tool_and_its_reply(self):
        from astrbot_host.linked_conversations import KEY
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(1)
        self.pause_model = True
        self.provider_mode = "tool"  # once resumed, the model asks for this turn's evidence
        task = await self.dispatch(self.event_for(1, "asked before the change", 1))
        await asyncio.wait_for(self.model_done.wait(), 15)
        records = self.controller.session.linked_conversations.records()
        records["owner-dialogue"]["revision"] += 1
        self.controller.authority.write(KEY, records)
        self.allow_model.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 20)
        await self.controller.session.linked_conversations.gate.drain()
        results = [message["content"] for message in await self.history() if message["role"] == "tool"]
        self.assertEqual([json.loads(result) for result in results],
                         [{"code": "linked_revision_changed", "effect": "none", "retry_disposition": "stop", "state": "failed"}])
        self.assertEqual(self.sent, [])

    async def test_a_failed_telegram_send_is_recorded_as_unknown_and_never_replayed_on_either_channel(self):
        from telegram.error import Forbidden, TimedOut
        from companion_v2.model_gateway.core_observations import observations_for_role
        self.assertEqual((await self.link())["status"], "ok")
        self.replies[TELEGRAM] = "A" * 4096 + "B" * 32  # two Telegram messages
        attempts = []

        async def send_message(**kwargs):
            attempts.append(kwargs["text"])
            outcome = outcomes[len(attempts) - 1]
            if outcome is not None:
                raise outcome
        self.telegram.client.send_message = send_message
        observer = self.controller.session.core_reply_observations
        for number, outcomes in ((1, [TimedOut()]), (2, [None, TimedOut()]), (3, [Forbidden("synthetic rejection")])):
            with self.subTest(outcomes=outcomes):
                del attempts[:]
                event = await self.turn(1, f"say something long {number}", number)
                while pending := observer._pending - observer._intervention_tasks:
                    await asyncio.gather(*tuple(pending), return_exceptions=True)
                    await asyncio.sleep(0)
                prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
                receipt = next(row for row in observations_for_role(self.app.runtime, prepared["context"])
                               if row["turn_id"] == prepared["turn_id"])["delivery"]
                self.assertEqual((receipt["state"], receipt["returned"], receipt["unknown"], receipt["failed"]),
                                 ("unknown", 0, 1, 0))
                self.assertEqual(len(attempts), len(outcomes))
        del attempts[:]
        await self.turn(0, "back on QQ", "4")
        self.assertEqual(attempts, [])
        self.assertEqual([(platform, sent["message"][0]["data"]["text"]) for platform, sent in self.sent],
                         [("qq-main", "reply-A")])

    async def test_the_linked_telegram_turn_is_offered_only_the_text_tools_of_an_unlinked_telegram_turn(self):
        await self.turn(1, "while independent", 1)
        self.assertEqual((await self.link())["status"], "ok")
        self.steer(1)
        self.model_rounds.clear()
        self.provider_mode = "tool"  # the model first reads this turn's evidence, then answers
        await self.turn(1, "after linking", 2)
        await self.turn(0, "from QQ", "3")
        (_, unlinked, _), (origin, linked, prompt), (_, qq, _) = self.requests
        self.assertEqual(origin, TELEGRAM)
        self.assertEqual(linked, unlinked)
        for action in ("turn_evidence", "source_read", "calendar_query", "conversation_news_lookup"):
            self.assertIn("companion_v2_" + action, linked)
        for unsupported in ("poke", "qzone", "image", "voice", "relay", "reaction", "camera"):
            self.assertFalse(any(unsupported in name for name in linked), (unsupported, linked))
        self.assertIn("companion_v2_interaction_poke", qq)
        for unsupported in ("interaction.poke", "qzone", "relay"):
            self.assertNotIn(unsupported, prompt.lower())
        tool_messages = [message for message in await self.history() if message["role"] == "tool"]
        self.assertEqual([json.loads(message["content"])["state"] for message in tool_messages], ["succeeded"])
        self.assertEqual([platform for platform, _ in self.sent], ["tg-main", "tg-main", "qq-main"])
        self.assertEqual(self.telegram.client.onebot_accesses, [])

    async def test_astrbots_send_tool_on_a_linked_telegram_turn_reaches_only_the_telegram_chat(self):
        self.assertEqual((await self.link())["status"], "ok")
        self.config["admins_id"].append(str(OWNER))  # AstrBot itself lets an administrator name another session
        self.steer(1)
        self.provider_mode = "tool"
        self.tool_call = ("send_message_to_user", {"session": QQ,
            "messages": [{"type": "plain", "text": "crossing to QQ"}]})
        await self.turn(1, "send this to QQ", 1)
        self.model_rounds.clear()
        self.tool_call = ("send_message_to_user", {"messages": [{"type": "plain", "text": "sent by the tool"}]})
        await self.turn(1, "send this here", 2)
        # The refused call sends nothing; the other is delivered once by the channel's own session sender.
        self.assertEqual([(platform, str(sent["chat_id"]), sent["text"]) for platform, sent in self.sent],
            [("tg-main", str(OWNER), "reply\\-B"), ("tg-main", str(OWNER), "sent by the tool"),
             ("tg-main", str(OWNER), "reply\\-B")])
        results = [message["content"] for message in await self.history() if message["role"] == "tool"]
        self.assertEqual(results, ["error: native reply tools require the current session",
                                   "Message sent to session " + TELEGRAM])

    async def test_each_channel_keeps_replying_on_the_linked_conversation_while_the_other_is_down(self):
        self.assertEqual((await self.link())["status"], "ok")
        self.telegram.application.running = False
        await self.turn(0, "QQ while Telegram is down", "1")
        self.telegram.application.running = True
        qq_clients = self.native.platforms["qq-main"].client._wsr_api_clients
        connected = dict(qq_clients)
        qq_clients.clear()
        await self.turn(1, "Telegram while QQ is down", 2)
        qq_clients.update(connected)
        await self.turn(0, "QQ again", "3")
        self.assertEqual(self.contents(await self.history()), ["old", "old-reply", "QQ while Telegram is down",
            "reply-A", "Telegram while QQ is down", "reply-B", "QQ again", "reply-A"])
        self.assertEqual([platform for platform, _ in self.sent], ["qq-main", "tg-main", "qq-main"])

    async def test_qq_keeps_the_linked_conversation_after_the_telegram_channel_is_removed_and_the_host_restarts(self):
        self.assertEqual((await self.link())["status"], "ok")
        await self.turn(1, "from Telegram", 1)
        await self.controller.terminate()
        self.native.platforms.pop("tg-main")
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        await self.turn(0, "QQ alone", "2")
        self.assertEqual(self.contents(await self.history()),
            ["old", "old-reply", "from Telegram", "reply-B", "QQ alone", "reply-A"])
        self.assertEqual([platform for platform, _ in self.sent], ["tg-main", "qq-main"])


class TelegramLinkedOwnerTests(LinkedTelegramFixture, owners.LinkedOwnerTests):
    """Every shared-owner scenario again, with the joining endpoint on Telegram."""
    second_channel = "tg-main"


if __name__ == "__main__":
    unittest.main()
