"""Purpose-specific proactive diagnostics must use their production producers."""

from datetime import timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import ANY, AsyncMock, Mock

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, VersionRef
from companion_v2.proactive.contact_models import ContactIntent
from companion_v2.proactive.models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
from companion_v2.proactive.official_alert_events import NO_CURRENT_ALERT
from companion_v2.proactive.scheduling import ContactSchedule
from tests.test_management_access import NOW


class ProactiveDiagnosticRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ScopeRef("installation", "onebot", ScopeKind.PRIVATE, "window")
        self.context = NS(scope=self.scope, key="diagnostic-context")
        self.event_source = SourceRef("conversation-source", "conversation.host_input", self.scope,
                                      VersionRef("conversation-source", 1), NOW)
        self.producer_source = SourceRef("producer-source", "fixture.producer", self.scope,
                                         VersionRef("producer-source", 1), NOW)
        self.event = NS(source=self.event_source, scoped_key="event-key", input_hash="event-hash")
        self.batch = {"role_instruction": "original role instructions"}

    def candidate(self, purpose):
        opportunity = Opportunity("diagnostic-opportunity", self.scope, NOW, NOW + timedelta(minutes=5))
        thought = Thought("diagnostic-thought", opportunity, ThoughtKind.CONVERSATION,
            "producer-specific source evidence", EvidenceSnapshot(self.scope, NOW, (self.producer_source,)), 1.0)
        return opportunity, (ContactIntent(purpose, thought),), {"source": "producer-marker"}

    def producer(self, selection=None, prepared=None):
        return NS(select=Mock(return_value=selection), prepare=AsyncMock(return_value=prepared),
                  diagnostic_candidate=AsyncMock(return_value=(prepared, [])))

    def schedule(self):
        schedule = ContactSchedule.__new__(ContactSchedule)
        schedule.tasks = NS(now=lambda: NOW)
        schedule.runtime = NS(read_one=Mock(side_effect=lambda query, parameters:
            None if "conversation_native_turns" in query else {"batch_id": "latest-batch"}))
        schedule.contacts = NS(preferences=NS(read=Mock(return_value=Outcome.success(object()))), history=None)
        schedule._origin = lambda _context, _batch_id, _at: ({"batch_id": "latest-batch"}, self.batch, self.event)
        schedule._diagnostic_preferences = lambda preferences: preferences
        schedule.events = NS(select=Mock(return_value=(None, False)))
        schedule.growth_events = NS(select=Mock(return_value=(None, False)))
        schedule.journal_events = NS(select=Mock(return_value=(None, False)))
        schedule.calendar_events = NS(select=Mock(return_value=(None, False)),
                                      select_transitions=Mock(return_value=(None, False)))
        schedule.reminiscence_events = NS(select=Mock(return_value=(None, False)))
        schedule.greeting_events = NS(select=Mock(return_value=None))
        schedule.date_events = NS(select=Mock(return_value=None))
        schedule.screen_events = NS(select=Mock(return_value=None), diagnostic=AsyncMock(return_value=(None, [])))
        schedule.screen_peek = object()
        schedule.relationship_events = NS(select=Mock(return_value=None))
        schedule.meal_events = NS(select=Mock(return_value=None))
        schedule.news_events = self.producer()
        schedule.official_alert_events = self.producer()
        schedule.balance_events = self.producer()
        schedule.weather_events = self.producer()
        schedule.weather_change_events = self.producer()
        schedule._followup_candidate = Mock(return_value=None)
        return schedule

    async def assert_route(self, purpose, route_id, configure, *, expected_producer):
        schedule = self.schedule()
        configure(schedule)

        result = await schedule.diagnostic_candidate(self.context, purpose, at=NOW)

        self.assertEqual(result["route"]["id"], route_id)
        self.assertEqual(result["intents"][0].purpose, purpose)
        self.assertEqual(result["role_instruction"], self.batch["role_instruction"])
        expected_sources = (self.event_source,) if purpose == "chat" else (self.event_source, self.producer_source)
        self.assertEqual(result["intents"][0].thought.evidence.sources, expected_sources)
        self.assertEqual(result["diagnostic_origin"]["_diagnostic"], {
            "route_id": route_id, "purpose": purpose, "batch_id": "latest-batch", "event_key": "event-key",
            "event_hash": "event-hash", "role_hash": digest(self.batch["role_instruction"])})
        if route_id == "conversation_scheduler":
            self.assertEqual(set(result["diagnostic_origin"]), {"_diagnostic"})
        else:
            self.assertIn(route_id, result["diagnostic_origin"])
        expected_producer(schedule)

    async def test_each_page_purpose_uses_its_matching_producer(self):
        cases = (
            ("affection", "relationship_event", lambda s: setattr(s.relationship_events, "select",
                Mock(return_value=self.candidate("affection"))),
                lambda s: s.relationship_events.select.assert_called_once_with(self.context, ANY, ANY, NOW,
                    purpose="affection", prompt_judgement=True)),
            ("care", "relationship_event", lambda s: setattr(s.relationship_events, "select",
                Mock(return_value=self.candidate("care"))),
                lambda s: s.relationship_events.select.assert_called_once_with(self.context, ANY, ANY, NOW,
                    purpose="care", prompt_judgement=True)),
            ("play", "relationship_event", lambda s: setattr(s.relationship_events, "select",
                Mock(return_value=self.candidate("play"))),
                lambda s: s.relationship_events.select.assert_called_once_with(self.context, ANY, ANY, NOW,
                    purpose="play", prompt_judgement=True)),
            ("repair", "relationship_event", lambda s: setattr(s.relationship_events, "select",
                Mock(return_value=self.candidate("repair"))),
                lambda s: s.relationship_events.select.assert_called_once_with(self.context, ANY, ANY, NOW,
                    purpose="repair", prompt_judgement=True)),
            ("invitation", "relationship_event", lambda s: setattr(s.relationship_events, "select",
                Mock(return_value=self.candidate("invitation"))),
                lambda s: s.relationship_events.select.assert_called_once_with(self.context, ANY, ANY, NOW,
                    purpose="invitation", prompt_judgement=True)),
            ("chat", "conversation_scheduler", lambda _s: None, lambda _s: None),
            ("greeting", "greeting_event", lambda s: setattr(s.greeting_events, "select",
                Mock(return_value=self.candidate("greeting"))),
                lambda s: s.greeting_events.select.assert_called_once()),
            ("life", "activity_event", lambda s: setattr(s.events, "select",
                Mock(return_value=(self.candidate("life"), False))),
                lambda s: s.events.select.assert_called_once_with(self.context, ANY, NOW, purpose="life")),
            ("creation", "activity_event", lambda s: setattr(s.events, "select",
                Mock(return_value=(self.candidate("creation"), False))),
                lambda s: s.events.select.assert_called_once_with(self.context, ANY, NOW, purpose="creation")),
            ("news", "news_event", lambda s: setattr(s.news_events, "diagnostic_candidate",
                AsyncMock(return_value=(self.candidate("news"), []))),
                lambda s: s.news_events.diagnostic_candidate.assert_awaited_once()),
            ("followup", "followup_event", lambda s: setattr(s, "_followup_candidate",
                Mock(return_value=self.candidate("followup"))),
                lambda s: s._followup_candidate.assert_called_once()),
            ("reminder", "calendar_event", lambda s: setattr(s, "calendar_events",
                NS(select=Mock(return_value=(self.candidate("reminder"), False)))),
                lambda s: s.calendar_events.select.assert_called_once()),
            ("meal", "meal_event", lambda s: setattr(s.meal_events, "select",
                Mock(return_value=self.candidate("meal"))),
                lambda s: s.meal_events.select.assert_called_once()),
            ("screen", "screen_event", lambda s: setattr(s.screen_events, "diagnostic",
                AsyncMock(return_value=(self.candidate("screen"), []))),
                lambda s: s.screen_events.diagnostic.assert_awaited_once_with(self.context, s.screen_peek,
                    test_id=ANY, role_instruction=self.batch["role_instruction"])),
        )
        for purpose, route_id, configure, expected_producer in cases:
            with self.subTest(purpose=purpose):
                await self.assert_route(purpose, route_id, configure, expected_producer=expected_producer)

    async def test_life_falls_back_to_the_journal_producer_and_greeting_to_date_producer(self):
        async def verify(purpose, route_id, configure, producer_name):
            schedule = self.schedule()
            configure(schedule)
            result = await schedule.diagnostic_candidate(self.context, purpose, at=NOW)
            self.assertEqual(result["route"]["id"], route_id)
            getattr(schedule, producer_name).select.assert_called_once()

        await verify("life", "journal_event", lambda s: setattr(s.journal_events, "select",
            Mock(return_value=(self.candidate("life"), False))), "journal_events")
        await verify("greeting", "date_event", lambda s: setattr(s.date_events, "select",
            Mock(return_value=self.candidate("greeting"))), "date_events")

    async def test_environment_uses_each_available_environment_producer(self):
        cases = (
            ("official_alert_events", "official_alert_event", "official_alert"),
            ("balance_events", "balance_event", "service_balance"),
            ("news_events", "news_event", "external_news"),
            ("weather_events", "weather_event", "weather"),
            ("weather_change_events", "weather_change_event", "weather_change"),
        )
        for producer_name, route_id, variant in cases:
            with self.subTest(route=route_id):
                schedule = self.schedule()
                producer = getattr(schedule, producer_name)
                producer.select.return_value = (self.candidate("environment")[0], {"poll": route_id})
                producer.prepare.return_value = self.candidate("environment")
                producer.diagnostic_candidate.return_value = (self.candidate("environment"), [])

                result = await schedule.diagnostic_candidate(self.context, "environment", at=NOW, variant=variant)

                self.assertEqual(result["route"], {"id": route_id,
                    "label": {"official_alert_event": "官方预警生产器", "balance_event": "服务余额生产器",
                              "news_event": "真实资讯抓取", "weather_event": "真实天气观测",
                              "weather_change_event": "天气变化生产器"}[route_id], "variant": variant})
                if variant in ("weather", "external_news"):
                    producer.select.assert_not_called()
                    producer.diagnostic_candidate.assert_awaited_once()
                else:
                    producer.select.assert_called_once()
                    producer.prepare.assert_awaited_once()

    async def test_empty_official_alert_poll_is_skipped_not_a_source_failure(self):
        """轮询成功但没有生效中的预警是常态，不能写成来源失败。"""
        schedule = self.schedule()
        notes = []
        schedule._note_window = lambda _context, _row, _at, stage, outcome, reason, **options: notes.append(
            (stage, outcome, reason))
        opportunity = Opportunity("official-alert-poll", self.scope, NOW, NOW + timedelta(minutes=5))
        plan = (self.context, opportunity, (), self.batch["role_instruction"],
                {"batch_id": "latest-batch", "official_alert_pending": {"location_hash": "hash"}})

        schedule.official_alert_events.prepare = AsyncMock(return_value=NO_CURRENT_ALERT)
        self.assertIsNone(await schedule._run(plan))
        self.assertEqual(notes, [("source_preparation", "skipped", "official_alert_none_current")])

        # 真实的获取失败仍按失败记录并保留原有失败码与重试语义。
        notes.clear()
        schedule.official_alert_events.prepare = AsyncMock(return_value=None)
        result = await schedule._run(plan)
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "contact_official_alert_unavailable")
        self.assertEqual(notes, [("source_preparation", "failed", "contact_official_alert_unavailable")])

    async def test_news_source_can_only_satisfy_news_and_missing_source_is_not_synthesized(self):
        schedule = self.schedule()
        news = self.candidate("news")
        schedule.news_events.select.return_value = (news[0], {"poll": "news"})
        schedule.news_events.prepare.return_value = news

        with self.assertRaises(TaskError) as caught:
            await schedule.diagnostic_candidate(self.context, "creation", at=NOW)
        self.assertEqual(caught.exception.code, "contact_diagnostic_source_required")
        schedule.news_events.prepare.assert_not_awaited()

        schedule = self.schedule()
        with self.assertRaises(TaskError) as caught:
            await schedule.diagnostic_candidate(self.context, "news", at=NOW)
        self.assertEqual(caught.exception.code, "contact_diagnostic_source_required")

    def test_diagnostic_origin_is_invalidated_when_the_source_conversation_changes(self):
        schedule = self.schedule()
        origin = {"_diagnostic": {"route_id": "conversation_scheduler", "batch_id": "latest-batch",
            "purpose": "chat", "event_key": self.event.scoped_key, "event_hash": self.event.input_hash,
            "role_hash": digest(self.batch["role_instruction"])}}
        schedule.validate_diagnostic_origin(self.context, origin, NOW)

        self.event.input_hash = "changed-hash"
        with self.assertRaises(TaskError) as caught:
            schedule.validate_diagnostic_origin(self.context, origin, NOW)
        self.assertEqual(caught.exception.code, "contact_conversation_changed")


if __name__ == "__main__":
    unittest.main()
