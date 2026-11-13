"""Goal-driven exploration: query quality and bounded research notes."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
import unittest

from companion_v2.foundation.refs import ScopeKind
from companion_v2.proactive.exploration import (
    ExplorationEvents,
    build_queries,
    plan_goals,
    score_item,
)
from companion_v2.proactive.external_materials import NewsItem, ParseBatch
from tests.test_management_access import NOW


class FakeRuntime:
    def __init__(self):
        self.values = {}

    def read_one(self, query, parameters):
        value = self.values.get(parameters[0])
        return None if value is None else {"value": value}

    @contextmanager
    def transaction(self):
        class Connection:
            def __init__(self, values):
                self.values = values

            def execute(self, query, parameters):
                self.values[parameters[0]] = parameters[1]
        yield Connection(self.values)


class FakeDates:
    def __init__(self, entries):
        self.entries = entries

    def upcoming(self, context, preferences, at, *, lookahead_days=None):
        return tuple(self.entries)


class FakePort:
    def __init__(self):
        self.calls = []

    async def fetch(self):
        self.calls.append("fetch")
        return ParseBatch((NewsItem(
            "摄影杂志", "one", "摄影爱好者生日礼物推荐",
            "https://example.com/gift", "适合入门的摄影小物，预算友好。",
            NOW - timedelta(days=2)),), ())


class FakeRouter:
    def __init__(self):
        self.port = FakePort()
        self.queries = []

    def for_settings(self, values, context=None):
        self.queries.append(values["web_exploration_interests"])
        return self.port


def context():
    return NS(key="exploration-context", scope=NS(kind=ScopeKind.PRIVATE))


def preferences(**overrides):
    values = {
        "web_exploration_enabled": True,
        "news_source_kind": "search",
        "web_exploration_min_interval_hours": 8,
        "web_exploration_interests": "摄影,手冲咖啡",
        "web_exploration_result_count": 4,
    }
    values.update(overrides)
    return NS(values=values)


class GoalPlanningTests(unittest.TestCase):
    def test_birthday_uses_interest_and_never_emits_a_bare_gift_query(self):
        goals = plan_goals(({"basis": "profile_relationship_date", "label": "对方生日",
                             "days_until": 9, "date": "2026-11-20"},), ("摄影", "手冲咖啡"))
        self.assertEqual(goals[0]["kind"], "birthday_gift")
        self.assertEqual(goals[0]["share_policy"], "private_note")
        queries = build_queries(goals[0])
        self.assertEqual(len(queries), 3)
        self.assertTrue(all("摄影" in query or "手冲咖啡" in query for query in queries))
        self.assertTrue(any("生日礼物" in query for query in queries))
        self.assertNotIn("生日礼物", queries)

    def test_fixed_holiday_and_role_birthday_are_not_partner_gift_goals(self):
        goals = plan_goals((
            {"basis": "fixed_gregorian_holiday", "label": "国庆节", "days_until": 3},
            {"basis": "profile_role_birthday", "label": "角色生日", "days_until": 5},
        ), ("摄影",))
        self.assertEqual([goal["kind"] for goal in goals], ["interest_tracking"])

    def test_event_goal_uses_the_event_label_and_interest(self):
        goals = plan_goals(({"basis": "operator_configured_recurring_date",
                             "label": "周末露营", "days_until": 3},), ("户外",))
        self.assertEqual(goals[0]["kind"], "event_preparation")
        queries = build_queries(goals[0])
        self.assertTrue(all("周末露营" in query for query in queries))
        self.assertTrue(any("户外" in query for query in queries))

    def test_interest_goals_are_low_priority_and_bounded(self):
        goals = plan_goals((), ("摄影", "手冲咖啡", "天文", "木工", "徒步"))
        self.assertEqual(len(goals), 5)
        self.assertTrue(all(goal["kind"] == "interest_tracking" for goal in goals))
        self.assertTrue(all(goal["priority"] == 5 for goal in goals))

    def test_query_validation_rejects_private_identifiers(self):
        goal = {"kind": "event_preparation", "label": "联系我 13800138000", "interests": ()}
        self.assertEqual(build_queries(goal), ())


class ScoringTests(unittest.TestCase):
    def test_recent_interest_match_beats_an_unrelated_page(self):
        goal = {"kind": "birthday_gift", "label": "对方生日", "interests": ("摄影",)}
        matching = NewsItem("摄影杂志", "one", "摄影生日礼物", "https://example.com/a",
                            "适合摄影爱好者的实用礼物", NOW)
        unrelated = NewsItem("财经", "two", "基金走势", "https://example.com/b",
                             "今日市场", NOW - timedelta(days=400))
        self.assertGreater(score_item(matching, goal), score_item(unrelated, goal))


class ExplorationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_session_stores_a_bounded_note_and_records_queries(self):
        runtime = FakeRuntime()
        contacts = NS(runtime=runtime, tasks=NS(now=lambda: NOW))
        router = FakeRouter()
        service = ExplorationEvents(contacts, FakeDates((
            {"basis": "profile_relationship_date", "label": "对方生日",
             "days_until": 9, "date": "2026-11-20"},
        )), router)

        await service._run(context(), preferences().values)

        snapshot = service.read(context())
        self.assertEqual(snapshot["last_result"], "notes")
        self.assertEqual(snapshot["last_goal"]["kind"], "birthday_gift")
        self.assertEqual(len(snapshot["notes"]), 1)
        self.assertEqual(snapshot["notes"][0]["share_policy"], "private_note")
        self.assertTrue(router.queries)
        self.assertTrue(all("摄影" in query or "手冲咖啡" in query for query in router.queries))

    async def test_disabled_or_non_search_source_never_starts_a_worker(self):
        runtime = FakeRuntime()
        contacts = NS(runtime=runtime, tasks=NS(now=lambda: NOW))
        service = ExplorationEvents(contacts, FakeDates(()), FakeRouter())
        service.maintain(context(), preferences(web_exploration_enabled=False))
        service.maintain(context(), preferences(news_source_kind="host"))
        self.assertEqual(service._workers, {})


if __name__ == "__main__":
    unittest.main()
