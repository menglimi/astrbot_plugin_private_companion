"""Reminders and proactive text of a linked conversation leave through its preferred endpoint."""
from tests import astrbot_data_root  # noqa: F401

import asyncio
from datetime import timedelta
import json
import unittest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_tools import native_tool_name
from companion_v2.agenda.models import AgendaTarget
from companion_v2.execution.models import require
from companion_v2.identity_policy.models import AuthorizationTier
from tests.linked_reply_support import LinkedTelegramFixture, TELEGRAM_OWNER
from tests.multi_host_support import multi_config


class PreferredChannelFixture(LinkedTelegramFixture):
    """QQ (first) and Telegram (second) on the real reply path, with the host's own senders."""

    def make_controller(self, config=None):
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
        from companion_v2.conversation.agenda_tools import TOOL_FIELDS
        config = config or multi_config(capabilities=sorted(
            REQUIRED_CAPABILITIES | set(TOOL_FIELDS) | {"reminder.deliver", "memo.reminder.set", "configuration.write"}))
        return super().make_controller(config)

    async def turn(self, index, text, identifier):
        event = self.event_for(index, text, identifier)
        await asyncio.wait_for(await self.dispatch(event), 30)
        await self.controller.session.linked_conversations.gate.drain()
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        await prepared["native_source_future"]
        return prepared

    async def remind(self, index, message, *, identifier, minutes=10):
        """The human asks for a reminder on this endpoint; the model uses the existing tool."""
        self.steer(index)
        self.model_rounds.clear()
        due = self.clock.now() + timedelta(minutes=minutes)
        self.tool_script = [(native_tool_name("reminder.create"),
                             {"message": message, "schedule": {"anchor": due.isoformat(), "timezone_name": "UTC"}})]
        context = (await self.turn(index, "remind me", identifier))["context"]
        cid = await self.manager.get_curr_conversation_id(self.origins[index])
        history = json.loads((await self.manager.get_conversation(self.origins[index], cid)).history)
        result = [json.loads(entry["content"]) for entry in history if entry["role"] == "tool"][-1]
        self.assertEqual(result["state"], "succeeded", result)
        self.tool_script = []
        self.reminder_id = result["payload"]["object_id"]
        return context, self.occurrence()

    def occurrence(self):
        return self.app.runtime.read_one("""SELECT occurrence_id FROM agenda_occurrences WHERE reminder_id = ?
            ORDER BY rowid DESC LIMIT 1""", (self.reminder_id,))["occurrence_id"]

    async def reschedule(self, index, *, identifier, minutes=10):
        """The human moves the last reminder from this endpoint, whichever endpoint it was made on."""
        self.steer(index)
        self.model_rounds.clear()
        due = self.clock.now() + timedelta(minutes=minutes)
        revision = self.app.agenda.repository.reminder(self.reminder_id).revision
        self.tool_script = [(native_tool_name("reminder.reschedule"), {"reminder_id": self.reminder_id,
            "expected_revision": revision, "schedule": {"anchor": due.isoformat(), "timezone_name": "UTC"}})]
        context = (await self.turn(index, "move the reminder", identifier))["context"]
        cid = await self.manager.get_curr_conversation_id(self.origins[index])
        history = json.loads((await self.manager.get_conversation(self.origins[index], cid)).history)
        result = [json.loads(entry["content"]) for entry in history if entry["role"] == "tool"][-1]
        self.assertEqual(result["state"], "succeeded", result)
        self.tool_script = []
        return context, self.occurrence()

    def status(self, context, occurrence):
        """The reminder's scheduling and delivery report, as its creator may read it."""
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        return require(self.app.agenda_background.observe(context.actor, target=target, occurrence_id=occurrence,
                                                          observed_at=self.clock.now()))

    async def settled(self, context, occurrence, *, minutes=10):
        """Let the reminder come due and wait until the host has recorded what became of it."""
        self.clock.advance(timedelta(minutes=minutes, seconds=1))
        async with asyncio.timeout(30):
            while True:
                self.app.notify()
                report = self.status(context, occurrence)
                if report["scheduling"] in ("skipped", "blocked") or report["delivery"] not in (
                        "not_started", "pending", "dispatched"):
                    return report
                self.assertEqual(self.app.state, "running", self.app.failure_code)
                await asyncio.sleep(0.02)

    async def restart(self):
        await self.controller.terminate()
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        self.app = self.controller.session.application

    async def idle(self):
        """Give the host's background loop several passes over whatever is due."""
        for _ in range(5):
            self.app.notify()
            await asyncio.sleep(0.05)
        self.assertEqual(self.app.state, "running", self.app.failure_code)

    def qq_texts(self):
        """Private messages the QQ account was asked to send, with their recipient."""
        return [(str(params["user_id"]), "".join(part["data"]["text"] for part in params["message"]
                                                 if part["type"] == "text"))
                for action, params in self.qq_calls() if action == "send_private_msg"]

    def receipts(self, report):
        """The sender's recorded outcomes for one delivery, from the shared delivery ledger."""
        rows = self.app.runtime.read_all("SELECT receipt_json FROM background_delivery_receipts WHERE delivery_id = ?",
                                         (report["delivery_id"],))
        return [(receipt["status"], receipt["failure_code"]) for receipt in map(json.loads, (row[0] for row in rows))]

    async def link_view(self):
        """The conversation link as the signed-in Dashboard administrator reads it."""
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from astrbot_host.account_session_api import AccountSessionWebAPI
        administrator = NS(username="owner", query={})
        with patch("astrbot_host.account_session_api.request", administrator), \
                patch("astrbot_host.setup_api.request", administrator):
            response = await AccountSessionWebAPI(self.controller).get()
        self.assertEqual(response["status"], "ok", response)
        return response["data"]["conversation_links"]["owner-dialogue"]

    def telegram_texts(self):
        return [(str(sent["chat_id"]), sent["text"]) for platform, sent in self.sent if platform == "tg-main"]

    def qq_calls(self):
        return self.native.platforms["qq-main"].client._api._wsr_api.calls


class PreferredReminderTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_reminder_created_on_qq_before_linking_is_delivered_once_through_telegram(self):
        context, occurrence = await self.remind(0, "drink water", identifier="before-link")
        self.assertEqual((await self.link())["status"], "ok")
        self.sent.clear()
        self.qq_calls().clear()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        self.assertEqual(self.telegram_texts(), [(str(TELEGRAM_OWNER), "drink water")])
        self.assertEqual(self.qq_calls(), [])

    async def test_a_delivered_reminder_is_not_sent_again_after_a_restart_or_through_the_other_endpoint(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.remind(0, "stretch", identifier="linked")
        self.sent.clear()
        self.assertEqual((await self.settled(context, occurrence))["delivery"], "delivered")
        await self.restart()
        await self.idle()
        self.assertEqual(self.status(context, occurrence)["delivery"], "delivered")
        self.assertEqual(self.telegram_texts(), [(str(TELEGRAM_OWNER), "stretch")])
        self.assertEqual(self.qq_calls(), [])

    async def test_telegram_is_preferred_at_first_and_an_administrator_can_select_qq(self):
        created = await self.link()
        self.assertEqual((created["status"], created["data"]["preferred_target_id"]), ("ok", "second"))
        chosen = await self.link(revision=1, preferred_target_id="first")
        self.assertEqual((chosen["status"], chosen["data"]["preferred_target_id"], chosen["data"]["revision"]),
                         ("ok", "first", 2))
        edited = await self.link(revision=2, members=["second", "first"])
        self.assertEqual((edited["status"], edited["data"]["preferred_target_id"]), ("ok", "first"))
        self.assertEqual((await self.link_view())["background_destination"], "selected")
        context, occurrence = await self.remind(1, "call home", identifier="from-telegram")
        self.sent.clear()
        self.qq_calls().clear()
        self.assertEqual((await self.settled(context, occurrence))["delivery"], "delivered")
        self.assertEqual(self.qq_texts(), [("200", "call home")])
        self.assertEqual(self.telegram_texts(), [])

    async def test_unlinking_the_preferred_endpoint_leaves_reminders_unsent_until_a_member_is_chosen(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.remind(0, "water the plants", identifier="before-unlink")
        refused = await self.link(revision=1, members=["first"], preferred_target_id="second")
        self.assertEqual(refused["status"], "error")
        left = await self.link(revision=1, members=["first"])
        self.assertEqual((left["status"], left["data"]["preferred_target_id"]), ("ok", "second"))
        self.assertEqual((await self.link_view())["background_destination"], "missing")
        self.sent.clear()
        self.qq_calls().clear()
        report = await self.settled(context, occurrence)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "delivery_destination_missing"))
        await self.restart()
        await self.idle()
        report = self.status(context, occurrence)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "delivery_destination_missing"))
        self.assertEqual((await self.link_view())["background_destination"], "missing")
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))
        # The reminder itself is still the conversation's, and a later one goes to the chosen member.
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        kept = require(self.app.agenda.list_reminders(context.actor, target=target, observed_at=self.clock.now()))
        self.assertEqual([reminder.message for reminder in kept], ["water the plants"])
        self.assertEqual((await self.link(revision=2, members=["first"], preferred_target_id="first"))["status"], "ok")
        self.assertEqual((await self.link_view())["background_destination"], "selected")
        later, following = await self.remind(0, "feed the cat", identifier="after-choice")
        self.assertEqual((await self.settled(later, following))["delivery"], "delivered")
        self.assertEqual(self.qq_texts(), [("200", "feed the cat")])
        self.assertEqual(self.telegram_texts(), [])

    async def test_a_qq_reminder_moved_from_telegram_is_still_delivered_once_through_the_preferred_endpoint(self):
        for preferred, expected in (("second", ([(str(TELEGRAM_OWNER), "moved reminder")], [])),
                                    ("first", ([], [("200", "moved reminder")]))):
            with self.subTest(preferred=preferred):
                revision = (await self.link_view())["revision"] if preferred == "first" else 0
                await self.remind(0, "moved reminder", identifier="made-" + preferred)
                self.assertEqual((await self.link(revision=revision, preferred_target_id=preferred))["status"], "ok")
                context, occurrence = await self.reschedule(1, identifier="moved-" + preferred, minutes=20)
                self.sent.clear()
                self.qq_calls().clear()
                report = await self.settled(context, occurrence, minutes=20)
                self.assertEqual(report["delivery"], "delivered", report)
                await self.idle()
                self.assertEqual((self.telegram_texts(), self.qq_texts()), expected)

    async def test_a_moved_reminder_is_delivered_when_the_endpoints_role_bindings_differ_in_revision(self):
        from dataclasses import replace
        qq = (await self.turn(0, "hello", "older-window"))["context"]
        authority = self.controller.authority
        binding = authority.personas.binding(authority.binding_id(qq.scope))
        authority.personas.set_binding(replace(binding, revision=binding.revision + 1),
                                       expected_revision=binding.revision, observed_at=self.clock.now())
        await self.remind(0, "moved reminder", identifier="made")
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.reschedule(1, identifier="moved", minutes=20)
        self.sent.clear()
        report = await self.settled(context, occurrence, minutes=20)
        self.assertEqual(report["delivery"], "delivered", report)
        self.assertEqual(self.telegram_texts(), [(str(TELEGRAM_OWNER), "moved reminder")])

    async def test_a_pending_reminder_is_still_delivered_after_a_member_starts_a_new_conversation(self):
        from astrbot.builtin_stars.builtin_commands.commands.conversation import ConversationCommands
        self.builtin("new", ConversationCommands(self.core_context).new_conv)
        context, occurrence = await self.remind(0, "after the new conversation", identifier="before-new")
        self.assertEqual((await self.link())["status"], "ok")
        event = self.event_for(1, "/new", "new-conversation")
        await asyncio.wait_for(await self.dispatch(event), 30)
        await self.controller.session.linked_conversations.gate.drain()
        view = await self.link_view()
        self.assertEqual((view["revision"], view["preferred_target_id"], view["background_destination"]),
                         (2, "second", "selected"))
        self.assertNotEqual(view["conversation_id"], "conversation-a")
        self.sent.clear()
        self.qq_calls().clear()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        self.assertEqual((self.telegram_texts(), self.qq_texts()),
                         ([(str(TELEGRAM_OWNER), "after the new conversation")], []))


class RoutedBackgroundHistoryTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    """What was sent in the background is part of the reply context of the conversation it was sent in."""

    def sent_in_the_background(self, context):
        return [{key: value for key, value in item.items() if key not in ("delivered_at", "recipient_read")}
                for item in require(self.app.background_history.recent(context))]

    async def test_a_reminder_delivered_on_the_preferred_endpoint_is_in_the_background_history_of_both_once(self):
        self.assertEqual((await self.link())["status"], "ok")
        telegram = (await self.turn(1, "hello", 1))["context"]
        qq, occurrence = await self.remind(0, "drink water", identifier="2")
        self.assertEqual((await self.settled(qq, occurrence))["delivery"], "delivered")
        self.assertEqual(self.telegram_texts()[-1], (str(TELEGRAM_OWNER), "drink water"))
        shown = [{"owner": "agenda.reminder", "text": "drink water", "delivered_on": "tg-main"}]
        self.assertEqual([self.sent_in_the_background(context) for context in (qq, telegram)], [shown, shown])
        # Also when it was made and delivered on the same member.
        asked, later = await self.remind(1, "stand up", identifier=3)
        self.assertEqual((await self.settled(asked, later))["delivery"], "delivered")
        shown.append({"owner": "agenda.reminder", "text": "stand up", "delivered_on": "tg-main"})
        self.assertEqual([self.sent_in_the_background(context) for context in (qq, telegram)], [shown, shown])
        # A removed endpoint reads none of it; the member that remains keeps what it asked for itself.
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        self.assertFalse(self.app.background_history.recent(qq).is_success)
        self.assertIn(shown[1], self.sent_in_the_background(telegram))

    async def test_an_unlinked_window_keeps_its_own_background_history_as_it_was(self):
        telegram = (await self.turn(1, "hello", 1))["context"]
        qq, occurrence = await self.remind(0, "drink water", identifier="2")
        self.assertEqual((await self.settled(qq, occurrence))["delivery"], "delivered")
        self.assertEqual(self.qq_texts(), [("200", "drink water")])
        self.assertEqual([self.sent_in_the_background(context) for context in (qq, telegram)],
                         [[{"owner": "agenda.reminder", "text": "drink water"}], []])


class PreferredOutcomeTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    """What became of a text is recorded as it happened; the other endpoint is never used instead."""

    def revoke(self, context, capability):
        directory = self.controller.authority.identities
        for grant in directory.grants_for_subject(context.actor.subject):
            if grant.scope == context.scope and grant.capability == capability:
                directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                       expected_revision=directory.revision)

    async def linked_reminder(self, index, message):
        """Both endpoints have spoken on the linked conversation; one of them asks for a reminder."""
        self.assertEqual((await self.link())["status"], "ok")
        contexts = [(await self.turn(member, "hello", f"hello-{member}"))["context"] for member in (0, 1)]
        context, occurrence = await self.remind(index, message, identifier="asks")
        self.sent.clear()
        self.qq_calls().clear()
        return contexts, context, occurrence

    async def test_a_destination_without_the_permission_gets_nothing_and_qq_is_not_tried(self):
        (_, telegram), context, occurrence = await self.linked_reminder(0, "take a break")
        self.revoke(telegram, "reminder.deliver")
        report = await self.settled(context, occurrence)
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "authorization_revoked"))
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))

    async def test_the_endpoint_a_reminder_was_recorded_on_still_needs_its_own_permission(self):
        (qq, _), context, occurrence = await self.linked_reminder(0, "take a break")
        self.revoke(qq, "reminder.deliver")
        report = await self.settled(context, occurrence)
        self.assertNotEqual(report["delivery"], "delivered")
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))

    async def test_an_unavailable_telegram_account_gets_nothing_and_qq_is_not_tried(self):
        _, context, occurrence = await self.linked_reminder(0, "take a break")
        self.telegram.application.running = False
        report = await self.settled(context, occurrence)
        # Upstream's connection layer names a disconnected client offline, not changed.
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "host_account_offline"))
        self.telegram.application.running = True
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))

    async def test_an_unavailable_qq_account_gets_nothing_and_telegram_is_not_tried(self):
        _, context, occurrence = await self.linked_reminder(1, "take a break")
        self.assertEqual((await self.link(revision=1, preferred_target_id="first"))["status"], "ok")
        clients = self.native.platforms["qq-main"].client._wsr_api_clients
        connected = dict(clients)
        clients.clear()
        report = await self.settled(context, occurrence)
        # Upstream's connection layer names a disconnected client offline, not changed.
        self.assertEqual((report["delivery"], report["reason"]), ("cancelled", "host_account_offline"))
        clients.update(connected)
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))

    async def test_a_reminder_last_set_from_an_endpoint_that_was_removed_is_not_delivered_anywhere(self):
        _, context, occurrence = await self.linked_reminder(1, "from the removed endpoint")
        self.assertEqual((await self.link(revision=1, members=["first"], preferred_target_id="first"))["status"], "ok")
        self.clock.advance(timedelta(minutes=10, seconds=1))
        await self.idle()
        dispatch = self.app.runtime.read_one("SELECT state, reason FROM agenda_dispatches WHERE occurrence_id = ?",
                                             (occurrence,))
        self.assertEqual((dispatch["state"], dispatch["reason"]), ("blocked", "scope_not_granted"))
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))

    async def test_a_reminder_of_a_removed_joined_endpoint_is_delivered_once_a_remaining_member_sets_it_again(self):
        _, _, blocked = await self.linked_reminder(1, "from the removed endpoint")
        self.assertEqual((await self.link(revision=1, members=["first"], preferred_target_id="first"))["status"], "ok")
        context, occurrence = await self.reschedule(0, identifier="set-again", minutes=10)
        self.assertNotEqual(occurrence, blocked)
        self.sent.clear()
        self.qq_calls().clear()
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "delivered", report)
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], [("200", "from the removed endpoint")]))

    async def test_a_telegram_send_that_raises_is_unknown_and_is_never_sent_again_on_either_endpoint(self):
        from telegram.error import TimedOut
        _, context, occurrence = await self.linked_reminder(0, "take a break")
        attempts = []

        async def send_message(**kwargs):
            attempts.append(kwargs["text"])
            raise TimedOut()
        self.telegram.client.send_message = send_message
        report = await self.settled(context, occurrence)
        self.assertEqual(report["delivery"], "unknown")
        await self.idle()
        await self.restart()
        await self.idle()
        self.assertEqual(self.status(context, occurrence)["delivery"], "unknown")
        self.assertEqual((attempts, self.qq_texts()), (["take a break"], []))


class PreferredRouteFenceTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_send_in_flight_keeps_its_route_and_a_new_preference_takes_only_later_work(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, first = await self.remind(0, "first reminder", identifier="first")
        _, second = await self.remind(0, "second reminder", identifier="second", minutes=30)
        self.sent.clear()
        self.qq_calls().clear()
        sending, release = asyncio.Event(), asyncio.Event()
        record = self.telegram.client.send_message

        async def held(**kwargs):
            sending.set()
            await release.wait()
            return await record(**kwargs)
        self.telegram.client.send_message = held
        settling = asyncio.create_task(self.settled(context, first))
        await asyncio.wait_for(sending.wait(), 30)
        self.assertEqual((await self.link(revision=1, preferred_target_id="first"))["status"], "ok")
        release.set()
        report = await settling
        self.assertEqual(report["delivery"], "delivered")
        route = self.app.outbound.route(report["delivery_id"])
        self.assertEqual((route["scope"]["platform"], route["link_id"], route["link_revision"]),
                         ("tg-main", "owner-dialogue", 1))
        self.assertEqual((await self.settled(context, second, minutes=20))["delivery"], "delivered")
        await self.idle()
        self.assertEqual(self.telegram_texts(), [(str(TELEGRAM_OWNER), "first reminder")])
        self.assertEqual(self.qq_texts(), [("200", "second reminder")])

    async def test_a_link_changed_after_the_route_was_resolved_stops_the_send_and_nothing_is_replayed(self):
        self.assertEqual((await self.link())["status"], "ok")
        context, occurrence = await self.remind(0, "fenced reminder", identifier="fenced")
        self.sent.clear()
        self.qq_calls().clear()
        selected, changed = self.manager.get_curr_conversation_id, []

        async def changing(umo):
            # The sender's own pre-send role check reads the selection: the link is edited while it runs.
            if not changed and self.status(context, occurrence)["delivery"] == "dispatched":
                changed.append(True)
                changed.append(await self.link(revision=1, preferred_target_id="first"))
            return await selected(umo)
        self.manager.get_curr_conversation_id = changing
        report = await self.settled(context, occurrence)
        self.assertEqual(changed[1]["status"], "ok", changed)
        self.assertEqual(report["delivery"], "failed")
        self.assertEqual(self.receipts(report), [("not_sent", "delivery_route_changed")])
        await self.idle()
        self.assertEqual((self.telegram_texts(), self.qq_texts()), ([], []))


class LinkedContactScheduleTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    """The windows of a linked conversation are one proactive schedule: its most recent window's."""

    def evaluated(self, *contexts):
        """Run one schedule pass; say what it decided about each window."""
        self.app.contact_schedule.advance()
        return [self.app.contact_schedule.diagnostic_for(context)["evaluation"]["reason_code"] for context in contexts]

    async def spoken(self, index, identifier):
        self.clock.advance(timedelta(seconds=5))
        context = (await self.turn(index, "hello", identifier))["context"]
        observer = self.controller.session.core_reply_observations
        while pending := observer._pending - observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))
            await asyncio.sleep(0)
        return context

    async def test_only_the_window_of_the_latest_turn_is_scheduled_also_after_a_restart(self):
        superseded = "linked_conversation_continues_on_another_endpoint"
        self.assertEqual((await self.link())["status"], "ok")
        qq = await self.spoken(0, "qq-1")
        self.assertNotIn(superseded, self.evaluated(qq))
        telegram = await self.spoken(1, "tg-1")
        decided = self.evaluated(qq, telegram)
        self.assertEqual(decided[0], superseded)
        self.assertNotEqual(decided[1], superseded)
        await self.restart()
        decided = self.evaluated(qq, telegram)
        self.assertEqual(decided[0], superseded)
        self.assertNotEqual(decided[1], superseded)
        qq = await self.spoken(0, "qq-2")
        decided = self.evaluated(qq, telegram)
        self.assertNotEqual(decided[0], superseded)
        self.assertEqual(decided[1], superseded)

    async def test_unlinking_the_window_that_held_the_schedule_hands_it_to_a_remaining_member_at_once(self):
        superseded = "linked_conversation_continues_on_another_endpoint"
        self.assertEqual((await self.link())["status"], "ok")
        qq = await self.spoken(0, "qq-1")
        telegram = await self.spoken(1, "tg-1")
        self.assertEqual(self.evaluated(qq, telegram)[0], superseded)
        self.assertEqual((await self.link(revision=1, members=["first"], preferred_target_id="first"))["status"], "ok")
        self.clock.advance(timedelta(minutes=2))
        self.assertNotEqual(self.evaluated(qq)[0], superseded)

    async def test_a_contact_planned_on_one_endpoint_is_withdrawn_when_the_human_speaks_on_the_other(self):
        from companion_v2.execution.models import TaskError
        self.assertEqual((await self.link())["status"], "ok")
        qq = await self.spoken(0, "qq-1")
        schedule = self.app.contact_schedule
        latest = schedule.latest_conversation(qq)
        origin, _, _ = schedule._origin(qq, latest["batch_id"], self.clock.now())
        await self.spoken(1, "tg-1")
        with self.assertRaises(TaskError) as refused:
            schedule._origin(qq, origin["batch_id"], self.clock.now())
        self.assertEqual(refused.exception.code, "contact_conversation_changed")


class TelegramTextOnlyTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    """Through the host's sender: Telegram gets the text; QQ keeps its own follow-ups."""

    async def background(self, context, **presentation):
        from companion_v2.delivery.models import DeliveryEnvelope
        from companion_v2.execution.models import digest
        session = self.controller.session
        self.spoken_aloud = []

        async def voice(context, speech, **_):
            self.spoken_aloud.append((context.scope.platform, speech))
            return None
        session.voice = voice
        self.sent.clear()
        self.qq_calls().clear()
        return await session.send(DeliveryEnvelope("part-" + context.scope.platform, "operation-" + context.scope.platform,
            context, 1, "thinking of you", digest("thinking of you"), session.sender_binding, lambda: True,
            **presentation))

    async def test_telegram_text_has_no_qq_poke_presence_or_voice_follow_up_and_qq_keeps_its_own(self):
        from companion_v2.delivery.plan import PokePlan, VoicePlan
        self.assertEqual((await self.link())["status"], "ok")
        qq, telegram = [(await self.turn(member, "hello", f"hello-{member}"))["context"] for member in (0, 1)]
        plans = dict(voice=VoicePlan("thinking of you"), poke=PokePlan(count=1))
        receipt = await self.background(telegram, **plans)
        self.assertEqual(receipt.status, "delivered")
        self.assertEqual(self.telegram_texts(), [(str(TELEGRAM_OWNER), "thinking of you")])
        self.assertEqual((self.spoken_aloud, self.qq_calls(), self.telegram.client.onebot_accesses), ([], [], []))
        receipt = await self.background(qq, **plans)
        self.assertEqual(receipt.status, "delivered")
        self.assertEqual(self.spoken_aloud, [("qq-main", "thinking of you")])
        self.assertIn("friend_poke", [action for action, _ in self.qq_calls()])
        self.assertEqual(self.qq_texts(), [("200", "thinking of you")])

    async def test_a_text_that_carries_an_image_is_not_sent_to_a_linked_telegram_endpoint(self):
        import base64
        from companion_v2.conversation.media.models import PromptImage
        from companion_v2.delivery.media import ImageDeliveryEnvelope, payload_hash
        from companion_v2.foundation.refs import SourceRef, VersionRef
        from tests.test_semantic_integration import picture
        self.assertEqual((await self.link())["status"], "ok")
        telegram = (await self.turn(1, "hello", "hello-telegram"))["context"]
        captured, now, session = picture(), self.clock.now(), self.controller.session
        source = SourceRef("picture", "conversation.image", telegram.scope, VersionRef("picture", 1), now)
        parent = SourceRef("parent", "conversation.image", telegram.scope, VersionRef("parent", 1), now)
        image = PromptImage(source, parent, captured.descriptor, base64.b64encode(captured.content).decode("ascii"))
        self.sent.clear()
        receipt = await session.send(ImageDeliveryEnvelope("part-image", "operation-image", telegram, 1, "look",
            payload_hash("look", (image,)), session.sender_binding, lambda: True, (image,)))
        self.assertEqual((receipt.status, receipt.failure_code), ("not_sent", "telegram_text_only"))
        self.assertEqual(self.sent, [])


class LinkedContactPreferenceTests(PreferredChannelFixture, unittest.IsolatedAsyncioTestCase):
    """What the owner set for proactive contact on one endpoint governs the schedule on either."""

    def contact_settings(self, context, changes):
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.proactive.configuration import GROUP, target_for
        target, now = target_for(context), self.clock.now()
        snapshot = require(self.app.configuration.read(context.actor, target=target, group_id=GROUP, observed_at=now))
        identifier = "contact-settings-" + snapshot.edit_token[:16]
        return require(self.app.configuration.patch(context.actor, target=target, group_id=GROUP,
            expected_edit_token=snapshot.edit_token, changes={GROUP + "." + key: value for key, value in changes.items()},
            reset_to_inherit=(), operation=OperationRef(identifier, GROUP, target.authorization_scope,
                                                        VersionRef(identifier, 1), identifier), observed_at=now))

    def refusal(self, context):
        return require(self.app.proactive.preferences.read(context, observed_at=self.clock.now())).reason("chat")

    async def test_quiet_hours_and_a_pause_set_on_one_endpoint_stop_contact_planned_on_the_other(self):
        self.assertEqual((await self.link())["status"], "ok")
        qq, telegram = [(await self.turn(member, "hello", f"hello-{member}"))["context"] for member in (0, 1)]
        identities = self.controller.authority.identities
        for context in (qq, telegram):
            # Consent to proactive contact is each endpoint's own.
            if not identities.authorize(context.actor, scope=context.scope, capability="proactive.contact",
                                        observed_at=self.clock.now()).is_success:
                identities.issue_grant(grant_id="consent-" + context.key, subject=context.actor.subject,
                    scope=context.scope, capability="proactive.contact", tier=AuthorizationTier.STANDARD,
                    valid_from=self.clock.now(), expected_revision=identities.revision)
        self.contact_settings(qq, {"enabled": True})
        self.assertEqual((self.refusal(qq), self.refusal(telegram)), (None, None))
        self.contact_settings(qq, {"quiet_enabled": True, "quiet_timezone": "UTC",
                                   "quiet_start": "00:00", "quiet_end": "23:59"})
        self.assertEqual((self.refusal(qq), self.refusal(telegram)), ("proactive_quiet_hours",) * 2)
        self.contact_settings(telegram, {"quiet_enabled": False,
                                         "pause_until": (self.clock.now() + timedelta(hours=1)).isoformat()})
        self.assertEqual((self.refusal(qq), self.refusal(telegram)), ("proactive_paused",) * 2)
