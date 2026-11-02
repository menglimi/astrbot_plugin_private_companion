"""Synthetic planning against real calendar, supervisor, memory and host owners."""

import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
import unittest
from zoneinfo import ZoneInfo

from companion_v2.agenda.annual_calendar import SCHEMA
from companion_v2.agenda.models import Frequency, Schedule
from companion_v2.agenda.planning import KIND, CalendarPlanning, planning_profile
from companion_v2.agenda.planning_config import configured_annual_calendars, planning_values
from companion_v2.agenda.planning_inputs import capture, validate, MATERIAL_CHARS, planning_calendar_context
from companion_v2.agenda.planning_output import INSTRUCTION, INTERVENTION_INSTRUCTION, parse_output
from companion_v2.configuration.models import ConfigurationError
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.conversation.state_context import ConversationStateContext
from companion_v2.execution.models import TaskContext, TaskError, digest, encode
from companion_v2.foundation.refs import AudienceRef, SourceRef, VersionRef, OperationRef
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.interventions import CALENDAR_INTERVENTION
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.model_gateway.models import Amount, BudgetPolicy, CapturedModelOutput, ProviderResponse
from companion_v2.proactive.management import ContactManagement
from companion_v2.role_work.growth import RoleGrowthRecord, GrowthKind
from companion_v2.role_work.growth.progress import growth_progress_profile
from companion_v2.role_work.growth.progress_management import GrowthProgressManagement
from companion_v2.role_work.models import RoleActivity, RoleActivityState
from companion_v2.role_work.state.configuration import configuration_fields as state_configuration_fields, target_for
from companion_v2.role_work.state.ownership import role_schedule_key
from companion_v2.storage.versioned_records import VersionedRecords
from companion_v2.sources.models import SourceRecord
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture, calendar_data, state_data
from tests.test_role_tool_loop import answered


def output(at=NOW, **changes):
    return {"title": "读下一小节", "start": (at + timedelta(hours=1)).isoformat(), "minutes": 30,
            "detail": "先回顾上次疑问，再读一小节"} | changes


def annual(year, *, holidays=(), makeup_workdays=(), solar_terms=()):
    return {"schema": SCHEMA, "year": year, "holidays": list(holidays),
            "makeup_workdays": list(makeup_workdays), "solar_terms": list(solar_terms)}


class ConfiguredAnnualCalendarTests(unittest.TestCase):
    def test_utf8_byte_limit_rejects_oversized_multibyte_rows_and_blank_text(self):
        rows = [{"date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                 "name": "节" * 64} for index in range(80)]
        value = json.dumps([annual(2026, solar_terms=rows)], ensure_ascii=False)
        self.assertLess(len(value), 16384)
        self.assertGreater(len(value.encode("utf-8")), 16384)
        for invalid in (value, " " * 16385):
            with self.subTest(size=len(invalid)), self.assertRaises(ConfigurationError):
                configured_annual_calendars(invalid)

    def test_timezone_window_keeps_configured_days_distinct_from_fixed_holidays(self):
        calendars = configured_annual_calendars(json.dumps([
            annual(2026, holidays=[{"name": "合成假期", "start": "2026-12-31", "end": "2026-12-31"}]),
            annual(2027, makeup_workdays=[{"date": "2027-01-01", "name": "合成调休"}],
                   solar_terms=[{"date": "2027-01-01", "name": "合成节气"},
                                {"date": "2027-01-03", "name": "窗口外节气"}]),
        ], ensure_ascii=False))
        context = planning_calendar_context(datetime(2026, 12, 31, 15, 30, tzinfo=timezone.utc),
                                            datetime(2027, 1, 1, 16, 0, tzinfo=timezone.utc),
                                            ZoneInfo("Asia/Shanghai"), calendars)
        self.assertEqual(context["today"]["local_date"], "2026-12-31")
        self.assertEqual(context["fixed_gregorian_holidays_in_window"],
                         [{"date": "2027-01-01", "name": "元旦"}])
        self.assertEqual(context["configured_annual_calendar"], {
            "basis": "operator_configured_annual_calendar", "years": [2026, 2027], "partial": False,
            "days": [
                {"date": "2026-12-31", "holiday": "合成假期", "makeup_workday": None, "solar_terms": []},
                {"date": "2027-01-01", "holiday": None, "makeup_workday": "合成调休",
                 "solar_terms": ["合成节气"]},
            ],
        })

    def test_expanded_holiday_is_bounded_and_marked_partial(self):
        calendars = configured_annual_calendars(json.dumps([
            annual(2026, holidays=[{"name": "合成休假", "start": "2026-01-01", "end": "2026-04-06"}])
        ], ensure_ascii=False))
        context = planning_calendar_context(datetime(2026, 1, 1, tzinfo=timezone.utc),
                                            datetime(2026, 5, 1, tzinfo=timezone.utc),
                                            ZoneInfo("UTC"), calendars)["configured_annual_calendar"]
        self.assertTrue(context["partial"])
        self.assertLess(len(context["days"]), 96)
        self.assertEqual(context["days"][0]["date"], "2026-01-01")
        self.assertLessEqual(sum(len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                                 for row in context["days"]), 4096)


class PlanningOutputTests(unittest.TestCase):
    def job(self):
        return dict(mode="daily", at=NOW, end=NOW + timedelta(hours=16), slot="today",
            settings={"planning_max_items": 12, "planning_timezone": "Asia/Shanghai"},
            goal_refs={}, calendar_refs={}, existing=[])

    def test_instruction_does_not_treat_planning_window_as_a_wake_schedule(self):
        self.assertIn("按 timezone 指定的当地日期与钟点理解", INSTRUCTION)
        self.assertIn("规划窗口的开始时间只表示可规划范围", INSTRUCTION)
        self.assertIn("模型过去生成的日程只是计划", INSTRUCTION)
        self.assertIn("不排在规划时区的 23:00 至次日 07:00", INSTRUCTION)
        self.assertIn("宁可留空", INSTRUCTION)

    def test_fence_and_optional_fields_do_not_lose_valid_siblings(self):
        text = "```json\n" + json.dumps({"items": [output(), output(title="错误时长", minutes=-1),
            output(title="散步", detail="想看看落叶", start=(NOW + timedelta(hours=2)).isoformat())]}, ensure_ascii=False) + "\n```"
        items, rejected = parse_output(text, self.job())
        self.assertEqual([i[0]["title"] for i in items], ["读下一小节", "散步"])
        self.assertEqual(rejected, ({"index": 1, "code": "invalid_plan_item"},))

    def test_no_identity_or_unknown_parent_or_naive_time_enters_calendar(self):
        for changes in ({"owner": "forged"}, {"goal_id": "unknown"}, {"parent_id": "unknown"},
                        {"start": "2026-09-21T10:00:00"}, {"minutes": True}):
            with self.subTest(changes=changes), self.assertRaises(TaskError):
                parse_output(json.dumps({"items": [output(**changes)]}), self.job())

    def test_duplicate_items_and_existing_calendar_are_not_saved_twice(self):
        job = self.job()
        item = output()
        items, _ = parse_output(json.dumps([item, item]), job)
        self.assertEqual(len(items), 1)
        event = items[0][0]
        job["existing"] = [(event["title"], event["schedule"]["anchor"], event["duration_minutes"])]
        self.assertEqual(parse_output(json.dumps([item]), job)[0], [])

    def test_empty_plan_is_allowed_and_unfinished_json_is_not_inferred(self):
        self.assertEqual(parse_output('{"items":[]}', self.job()), ([], ()))
        with self.assertRaises(TaskError):
            parse_output('{"items":[', self.job())

    def test_truncated_json_keeps_complete_items_and_drops_the_tail(self):
        text = '{"items":[' + json.dumps(output(), ensure_ascii=False) + ',{"title":"半截'
        items, rejected = parse_output(text, self.job())
        self.assertEqual([item[0]["title"] for item in items], ["读下一小节"])
        self.assertEqual(rejected, ())

    def test_intervention_only_changes_accepted_role_plan_records(self):
        plan = {"id": "role-plan", "revision": 3, "kind": "role_plan", "state": "accepted",
            "title": "阅读", "schedule": json.loads(encode(Schedule(NOW + timedelta(hours=2), "Asia/Shanghai"))),
            "exceptions": {}}
        report = {**plan, "id": "user-report", "kind": "user_report"}
        job = {"mode": "intervention", "at": NOW, "end": NOW + timedelta(days=14),
            "settings": {"planning_max_items": 12, "planning_timezone": "Asia/Shanghai"},
            "material": {"calendar": [plan, report]}, "calendar_refs": {"role-plan": {"id": "role-plan"}}}

        accepted, rejected = parse_output(json.dumps({"changes": [
            {"action": "cancel", "id": "role-plan", "revision": 3},
            {"action": "cancel", "id": "user-report", "revision": 3},
        ]}), job)

        self.assertEqual(accepted, [{"action": "cancel", "id": "role-plan",
            "expected_revision": 3, "data": None}])
        self.assertEqual(rejected, ({"index": 1, "code": "invalid_intervention_change"},))
        with self.assertRaises(TaskError):
            parse_output(json.dumps({"changes": [{"action": "cancel", "id": "user-report", "revision": 3}]}), job)
        self.assertIn("不得修改 user_report、commitment", INTERVENTION_INSTRUCTION)
        self.assertIn("不要求角色复述确认", INTERVENTION_INSTRUCTION)
        self.assertIn("按上下文对安排影响的大小与确定程度", INTERVENTION_INSTRUCTION)
        self.assertNotIn("普通关心、建议、邀请或假设", INTERVENTION_INSTRUCTION)
        self.assertNotIn("只有角色真实送达的 assistant 回复", INTERVENTION_INSTRUCTION)


class CalendarRefinementTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_refinement_preserves_schedule_other_occurrences_and_revision_checks(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=True)
        data = calendar_data(schedule=json.loads(encode(Schedule(NOW + timedelta(hours=1), "Asia/Shanghai", Frequency.DAILY, count=3))))
        app.calendar.command(context, identifier="reading", action="create", expected_revision=0,
            operation_key="create", data=data)
        receipt = app.calendar.command(context, identifier="reading", action="refine", expected_revision=1,
            operation_key="refine", data={"sequence": 1, "detail": "继续第二小节，保留未完成的疑问"})
        self.assertEqual(receipt["revision"], 2)
        current = app.calendar.read(context, "reading")
        self.assertEqual(current["schedule"], data["schedule"])
        self.assertEqual(current["state"], "accepted")
        occurrences = app.calendar.occurrences(context, start=NOW, end=NOW + timedelta(days=3))["items"]
        self.assertEqual([x["detail"] for x in occurrences], [data["detail"], "继续第二小节，保留未完成的疑问", data["detail"]])
        with self.assertRaises(TaskError):
            app.calendar.command(context, identifier="reading", action="refine", expected_revision=1,
                operation_key="stale", data={"sequence": 1, "detail": "不能覆盖人工新稿"})
        self.assertTrue(app.calendar.command(context, identifier="reading", action="refine", expected_revision=1,
            operation_key="refine", data={"sequence": 1, "detail": "继续第二小节，保留未完成的疑问"})["replayed"])

    async def test_planning_options_require_core_and_do_not_issue_calendar_grants(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=False, planning_enabled=True, planning_horizon_days=180)
        with self.assertRaises(TaskError):
            app.calendar.access.check(context, "calendar.write")
        before = manager.status(self.core).unwrap()
        rejected = manager.command(self.important, dict(action="configure", operation_key="not-core",
            edit_token=before["edit_token"], changes={"calendar_enabled": True}))
        self.assertFalse(rejected.is_success)
        for changes in ({"planning_timezone": "Mars/Unknown"}, {"planning_horizon_days": 367},
                        {"planning_prompt": "过长" * 3000},
                        {"planning_annual_calendars": "{}"},
                        {"planning_annual_calendars": json.dumps([annual(2026), annual(2026)])},
                        {"planning_annual_calendars": json.dumps([
                            annual(2026, holidays=[{"name": "太多", "start": "2026-01-01", "end": "2026-04-08"}])])},
                        {"planning_annual_calendars": json.dumps([annual(2026)], ensure_ascii=False) + " " * 16385}):
            before = manager.status(self.core).unwrap()
            result = manager.command(self.core, dict(action="configure", operation_key="bad-" + str(len(str(changes))),
                edit_token=before["edit_token"], changes=changes))
            self.assertFalse(result.is_success)
            self.assertEqual(manager.status(self.core).unwrap()["settings"], before["settings"])


class CalendarPlanningTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (planning_profile("primary", max_output_tokens=100),
                          growth_progress_profile("primary", max_output_tokens=100))
        self.registry = FieldRegistry(version="planning-tests", fields=configuration_fields(self.profiles))

    async def test_planning_edits_get_a_new_attempt_while_home_display_edits_keep_the_plan(self):
        app, context, manager = await self.ready()
        settings, _ = app.role_state.settings(context)
        original = app.calendar_planning.opportunity(context, settings, NOW)
        self.configure(manager, home_show_location=False)
        unchanged, _ = app.role_state.settings(context)
        self.assertEqual(app.calendar_planning.opportunity(context, unchanged, NOW), original)

        self.configure(manager, planning_enabled=False)
        self.configure(manager, planning_enabled=True)
        updated, _ = app.role_state.settings(context)
        revised = app.calendar_planning.opportunity(context, updated, NOW)
        self.assertEqual(revised[0], "daily")
        self.assertNotEqual(revised[1], original[1])

    async def ready(self, *, automatic=False):
        host, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=True, planning_enabled=True)
        await self.start(host)
        app.state_maintenance.bind(context, "这是原角色，喜欢阅读也愿意休息。")
        if not automatic:
            app.calendar_planning.next_checks[context.key] = NOW + timedelta(days=1)
        self.model_behavior = lambda envelope: self.response(envelope)
        return app, context, manager

    async def test_new_private_input_fences_a_stale_calendar_intervention_before_dispatch(self):
        app, context, _ = await self.ready()
        self.identity.issue_grant(grant_id="calendar-intervention-window", subject=context.actor.subject,
            scope=context.scope, capability=CALENDAR_INTERVENTION, tier=AuthorizationTier.STANDARD,
            valid_from=NOW, expected_revision=self.identity.revision)
        expected_epoch = app.state_maintenance._input_epochs.get(context.key, 0)
        app.state_maintenance.note_input(context, "new-message")

        with self.assertRaises(TaskError) as raised:
            await app.calendar_planning.run_intervention(context, "这是原角色，喜欢阅读也愿意休息。",
                "stale-intervention", expected_input_epoch=expected_epoch)

        self.assertEqual(raised.exception.code, "planning_inputs_changed")
        self.assertEqual(self.runtime.read_all("SELECT task_id FROM execution_tasks", ()), ())
        self.assertEqual(self.called, [])

    async def test_budget_deferral_waits_and_later_recaptures_the_unstarted_plan(self):
        app, context, _ = await self.ready()
        old = app.gateway.ledger
        app.gateway.ledger = BudgetLedger(self.runtime, BudgetPolicy(*(Amount(1, 100000) for _ in range(5))))
        result = await self.plan(app, context)
        self.assertEqual((result["state"], result["code"]), ("deferred", "model_budget_exhausted"))
        self.assertFalse(app.calendar_planning.attempted(context, "manual-day"))
        count = self.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks", ())["n"]
        self.assertEqual((await self.plan(app, context))["state"], "deferred")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks", ())["n"], count)
        self.assertEqual(self.called, [])
        app.gateway.ledger = old
        resumed = await self.plan(app, context)
        self.assertEqual(resumed["state"], "succeeded", resumed)
        self.assertNotEqual(resumed["task_id"], result["task_id"])
        self.assertEqual(len(self.called), 1)

    async def test_a_plan_cut_off_by_the_output_ceiling_keeps_the_items_that_arrived_whole(self):
        """A cut-off round is evidence, not a lost day.

        Production lost every calendar round whose body ended at the output
        ceiling, because the transport refused to hand the body over at all.
        The planning contract already salvages complete items and discards the
        trailing one, so the body must reach it.
        """
        app, context, _ = await self.ready()
        kept = output(self.clock.now())
        half_body = ('{"items":[' + json.dumps(kept, ensure_ascii=False)
                     + ',{"title":"被截断的半截')

        async def truncated(envelope):
            return ProviderResponse("generated", captured_output=CapturedModelOutput(half_body, "length"),
                                    usage=Amount(15, 40), receipt_ref="truncated-usage")

        self.model_behavior = truncated

        result = await self.plan(app, context)

        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["rejected"], [])

    async def response(self, envelope):
        return answered(json.dumps({"items": [output(self.clock.now())]}, ensure_ascii=False))

    async def test_foreground_private_date_reads_role_calendar_without_turning_gaps_into_facts(self):
        app, context, manager = await self.ready()
        self.configure(manager, planning_annual_calendars=json.dumps([annual(2026,
            holidays=[{"name": "合成休假", "start": "2026-09-22", "end": "2026-09-22"}],
            solar_terms=[{"date": "2026-09-23", "name": "合成节气"}])], ensure_ascii=False))
        self.clock.advance(datetime(2026, 9, 21, 16, 30, tzinfo=timezone.utc) - self.clock.now())
        source = self.source(app, context, "date-input")
        event = InboundConversationEvent("date-event", context.actor.principal, context.scope,
                                         source, "text", "date-input", self.clock.now())
        batch = app.messages.accept(context, event, role_instruction="原角色提示").unwrap()
        app.messages.seal(context, batch, deadline_at=self.clock.now() + timedelta(minutes=2)).unwrap()
        row = app.messages.batch(batch)

        request = app.messages._request(row, context, self.clock.now())
        payload = json.loads(next(item.payload for item in request.context.materials
                                  if item.purpose == "local_calendar_context"))
        self.assertEqual(payload["local_date"], "2026-09-22")
        self.assertEqual(payload["configured_annual_calendar"]["listed_today"], {"holiday": "合成休假"})
        self.assertIn("未列日期和未配置年份", request.instruction)

        self.clock.advance(timedelta(days=1))
        continued = app.messages._request(row, context, self.clock.now())
        self.assertEqual(next(item.payload for item in continued.context.materials
                              if item.purpose == "local_calendar_context"),
                         next(item.payload for item in request.context.materials
                              if item.purpose == "local_calendar_context"))
        next_source = self.source(app, context, "next-date-input")
        next_event = InboundConversationEvent("next-date-event", context.actor.principal, context.scope,
                                              next_source, "text", "next-date-input", self.clock.now())
        next_batch = app.messages.accept(context, next_event, role_instruction="原角色提示").unwrap()
        next_day = app.messages._request(app.messages.batch(next_batch), context, self.clock.now())
        payload = json.loads(next(item.payload for item in next_day.context.materials
                                  if item.purpose == "local_calendar_context"))
        self.assertEqual(payload["configured_annual_calendar"]["listed_today"],
                         {"solar_terms": ["合成节气"]})

    async def test_sealed_private_reply_receives_only_configured_upcoming_dates(self):
        app, context, _ = await self.ready()
        contact = ContactManagement(app, context)
        before = contact.status(self.core).unwrap()
        target_day = self.clock.now().astimezone(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=2)
        contact.configure(self.core, {"operation_key": "important-date-context",
            "edit_token": before["edit_token"], "changes": {
                "date_checkin_enabled": False,
                "important_dates": json.dumps([{"month": target_day.month, "day": target_day.day,
                                                "label": "相识纪念日"}], ensure_ascii=False),
                "important_date_lookahead_days": 2,
                "quiet_timezone": "Asia/Shanghai",
            }}).unwrap()
        from companion_v2.relationships import anniversaries
        anniversaries.remember(app.runtime, context, {
            "label": "第一次一起看星星", "reason": "用户原话明确说这是第一次一起看星星。",
            "confidence": 0.91, "evidence_handle": "s1",
            "occurred_at": self.clock.now().isoformat(), "month": target_day.month,
            "day": target_day.day, "timezone": "Asia/Shanghai",
        }, source_run_id="important-date-context", at=self.clock.now())
        source = self.source(app, context, "important-date-context")
        event = InboundConversationEvent("important-date-context", context.actor.principal, context.scope,
                                         source, "text", "important-date-context", self.clock.now())
        batch = app.messages.accept(context, event, role_instruction="原角色提示").unwrap()
        app.messages.seal(context, batch, deadline_at=self.clock.now() + timedelta(minutes=2)).unwrap()
        request = app.messages._request(app.messages.batch(batch), context, self.clock.now())
        material = next(item for item in request.context.materials if item.purpose == "upcoming_important_dates")
        projection = json.loads(material.payload)
        self.assertEqual(projection["entries"], [{"label": "相识纪念日", "date": target_day.isoformat(),
                                                  "days_until": 2, "calendar": "gregorian"}])
        self.assertEqual(projection["auto_anniversaries"], [{
            "basis": "auto_relationship_anniversary", "label": "第一次一起看星星",
            "date": target_day.isoformat(), "days_until": 2, "calendar": "gregorian"}])
        self.assertIn("不证明用户与日期的关系", request.instruction)
        self.assertIn("自动纪念日", request.instruction)

    async def plan(self, app, context, *, slot="manual-day", mode="daily", occurrence=None,
                   manual=False, refresh=False):
        return await app.calendar_planning.run(context, app.state_maintenance.bindings[context.key][1],
            mode=mode, slot=slot, occurrence=occurrence, manual=manual, refresh=refresh)

    async def test_manual_plan_does_not_require_automatic_planning_and_refresh_replaces_only_generated_daily_plans(self):
        app, context, manager = await self.ready()
        self.configure(manager, planning_enabled=False)
        with self.assertRaises(TaskError):
            await self.plan(app, context, slot="initial")
        manual = await self.plan(app, context, slot="manual-first", manual=True)
        self.assertEqual(manual["state"], "succeeded", manual)
        old = app.calendar.inventory(context)["items"][0]

        refreshed = await self.plan(app, context, slot="manual-refresh", manual=True, refresh=True)
        self.assertEqual(refreshed["state"], "succeeded", refreshed)
        self.assertEqual(refreshed["replaced"], 1)
        items = app.calendar.inventory(context)["items"]
        prior = next(item for item in items if item["id"] == old["id"])
        current = next(item for item in items if item["id"] != old["id"])
        self.assertEqual(prior["state"], "cancelled")
        self.assertEqual(current["state"], "accepted")

    async def test_real_host_saves_same_calendar_and_only_once_without_delivery_or_completion(self):
        app, context, manager = await self.ready()
        result = await self.plan(app, context)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(result["saved"], 1)
        entries = app.calendar.inventory(context)["items"]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["kind"], entries[0]["state"]), ("role_plan", "accepted"))
        self.assertEqual(entries[0]["planning"]["mode"], "daily")
        self.assertEqual(manager.status(self.core).unwrap()["calendar"]["items"][0]["id"], entries[0]["id"])
        self.assertEqual(self.called[0].original.role_instruction, app.state_maintenance.bindings[context.key][1])
        self.assertEqual(self.sent, [])
        self.assertTrue((await self.plan(app, context))["replayed"])
        self.assertEqual(len(self.called), 1)
        self.advance(app, 86400)
        self.assertEqual(app.calendar.read(context, entries[0]["id"])["state"], "accepted")
        report = await app.close()
        self.assertEqual(report.pending_handles, ())

    async def test_concurrent_manual_change_blocks_stale_whole_plan(self):
        app, context, _ = await self.ready()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(envelope):
            entered.set()
            await release.wait()
            return answered(json.dumps({"items": [output()]}))
        self.model_behavior = delayed
        task = asyncio.create_task(self.plan(app, context))
        await asyncio.wait_for(entered.wait(), 3)
        app.calendar.command(context, identifier="human-edit", action="create", expected_revision=0,
            operation_key="human-edit", data=calendar_data(title="人工安排"))
        release.set()
        result = await task
        self.assertNotEqual(result["state"], "succeeded")
        self.assertEqual([i["id"] for i in app.calendar.inventory(context)["items"]], ["human-edit"])

    async def test_configuration_disabled_during_generation_prevents_commit(self):
        app, context, manager = await self.ready()
        async def disable(envelope):
            self.configure(manager, planning_enabled=False)
            return answered(json.dumps({"items": [output()]}))
        self.model_behavior = disable
        result = await self.plan(app, context)
        self.assertNotEqual(result["state"], "succeeded")
        self.assertEqual(app.calendar.inventory(context)["items"], [])

    async def test_confirmed_goal_links_long_term_node_and_daily_step_with_revocable_sources(self):
        app, context, _ = await self.ready()
        self.grant(context.actor, context.scope, "role.growth.read")
        source = SourceRef("drawing-wish", "test", context.scope, VersionRef("drawing-wish", 1), NOW)
        body = "可以慢慢学水彩，保留休息时间。"
        app.messages.sources.publish(SourceRecord(source, context.audience, digest(body), body), observed_at=NOW).unwrap()
        owner = app.role_growth_context.reader.owner(context)
        app.role_growth.add(RoleGrowthRecord("watercolor", owner, context.scope, GrowthKind.GOAL,
            "练习水彩", "先熟悉色彩，再尝试风景", "尚未开始", (source,), NOW, NOW,
            proposed_by=context.actor.subject))
        app.role_growth.confirm("watercolor", owner=owner, scope=context.scope, expected_revision=1,
            operation_id="confirm-watercolor", actor=self.core.subject, observed_at=NOW)
        async def horizon(envelope):
            return answered(json.dumps({"items": [output(title="尝试第一张风景", start=(NOW + timedelta(days=7)).isoformat(), goal_id="watercolor")]}))
        self.model_behavior = horizon
        result = await self.plan(app, context, mode="horizon", slot="long-node")
        self.assertEqual(result["state"], "succeeded", result)
        long = app.calendar.inventory(context)["items"][0]
        self.assertEqual(long["planning"]["goal"], {"growth_id": "watercolor", "revision": 2})
        activity = app.role_life.add_activity(RoleActivity("watercolor-practice",
            context.role_binding.role.subject_id, context.scope, NOW, RoleActivityState.ACTIVE,
            description="到河边练习水彩"))
        app.role_life.transition_activity(activity.activity_id, owner=activity.owner,
            expected_revision=activity.revision, state=RoleActivityState.COMPLETED,
            operation_id="watercolor-practice-done", observed_at=NOW)
        async def progress(_):
            return answered(json.dumps({"growth_id": "watercolor",
                "progress_note": "完成一次河边水彩练习。"}, ensure_ascii=False))
        self.model_behavior = progress
        report = await app.growth_progress.run(context, app.state_maintenance.bindings[context.key][1],
            "activity", "watercolor-practice-done")
        self.assertEqual(report["state"], "succeeded", report)
        review = GrowthProgressManagement(app)
        item = review.list(operator=self.core, context=context).unwrap()["items"][0]
        self.assertEqual(item["base_revision"], 2)
        self.assertEqual(review.command(operator=self.core, context=context, review_id=item["review_id"],
            action="accept", expected_revision=2, view_token=item["view_token"],
            operation_key="accept-watercolor-practice").unwrap()["applied_revision"], 3)
        captured = capture(app.calendar_planning, context, app.state_maintenance.bindings[context.key][1],
            "daily", "after-growth-review")
        self.assertEqual(captured["goal_refs"]["watercolor"], {"growth_id": "watercolor", "revision": 3})
        self.assertEqual(captured["material"]["confirmed_goals_and_skills"][0]["progress_note"],
                         "完成一次河边水彩练习。")
        async def daily(envelope):
            return answered(json.dumps({"items": [output(title="练习调色", goal_id="watercolor", parent_id=long["id"])]}))
        self.model_behavior = daily
        result = await self.plan(app, context)
        self.assertEqual(result["state"], "succeeded", result)
        step = next(i for i in app.calendar.inventory(context)["items"] if i["id"] != long["id"])
        self.assertEqual(step["planning"]["goal"], {"growth_id": "watercolor", "revision": 3})
        self.assertEqual(step["planning"]["parent"], {"id": long["id"], "revision": 1})
        self.revoke_source(app, source)
        self.assertEqual(app.calendar.inventory(context)["items"], [])
        with self.assertRaises(TaskError):
            app.calendar.read(context, step["id"])

    async def test_refines_only_current_period_and_skips_expired_slots(self):
        app, context, _ = await self.ready()
        app.calendar.command(context, identifier="recurring", action="create", expected_revision=0,
            operation_key="recurring", data=calendar_data(schedule=json.loads(encode(
                Schedule(NOW + timedelta(minutes=5), "Asia/Shanghai", Frequency.DAILY, count=3)))))
        item = app.calendar.occurrences(context, start=NOW, end=NOW + timedelta(minutes=15))["items"][0]
        async def detail(envelope):
            return answered('{"detail":"先翻回书签，再读下一页"}')
        self.model_behavior = detail
        result = await self.plan(app, context, mode="refine", slot="first-refine", occurrence=item)
        self.assertEqual(result["state"], "succeeded", result)
        record = app.calendar.read(context, "recurring")
        self.assertEqual(record["details"], {"0": "先翻回书签，再读下一页"})
        self.assertEqual(record["state"], "accepted")
        self.assertFalse(record.get("reminder_id"))

    async def test_automatic_tick_uses_current_date_then_weekly_nodes(self):
        app, context, _ = await self.ready(automatic=True)
        await self.until(lambda: context.key in app.calendar_planning.results)
        first = app.calendar_planning.results[context.key]
        self.assertEqual(first["mode"], "daily", first)
        self.advance(app, 31)
        await self.until(lambda: app.calendar_planning.results[context.key].get("mode") == "horizon")
        self.assertEqual(app.state, "running", app.failure_code)
        self.assertEqual(len(self.called), 2)
        self.assertEqual(self.sent, [])

    async def test_automatic_slot_is_shared_across_private_windows_for_the_same_role(self):
        app, context, _ = await self.ready()
        other_scope = replace(context.scope, native_scope_id="another-window",
                              conversation_id="conversation-another", session_id="session-another")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject), observed_at=NOW)
        other_context = TaskContext(context.actor, self.identity.resolve_role(other_scope, observed_at=NOW).unwrap(),
                                    AudienceRef(other_scope, (context.actor.subject,)))
        instruction = app.state_maintenance.bindings[context.key][1]
        app.state_maintenance.bind(other_context, instruction)
        settings = app.role_state.settings(context)[0]
        mode, slot, occurrence = app.calendar_planning.opportunity(context, settings, NOW)

        first, second = await asyncio.gather(
            app.calendar_planning.run(context, instruction, mode=mode, slot=slot,
                                      occurrence=occurrence, automatic=True),
            app.calendar_planning.run(other_context, instruction, mode=mode, slot=slot,
                                      occurrence=occurrence, automatic=True),
        )

        self.assertEqual({first["state"], second["state"]}, {"succeeded", "already_attempted"})
        self.assertEqual(len(self.called), 1)
        self.assertTrue(app.calendar_planning.attempted(other_context, slot, automatic=True))
        self.assertEqual(len(app.calendar.inventory(other_context)["items"]), 1)
        restarted = CalendarPlanning(self.runtime)
        restarted.attach(app)
        self.assertTrue(restarted.attempted(other_context, slot, automatic=True))
        marker_key = restarted._automatic_marker_key(context, slot)
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (marker_key,))
        app.state_maintenance.bindings.pop(context.key)
        self.assertTrue(restarted.attempted(other_context, slot, automatic=True))
        marker = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (marker_key,))
        self.assertEqual(json.loads(marker["value"])["status"], "legacy_attempt")
        await restarted.close()

    async def test_automatic_scheduler_uses_one_entry_for_all_windows_of_a_role(self):
        app, context, _ = await self.ready()
        other_scope = replace(context.scope, native_scope_id="scheduler-window",
                              conversation_id="conversation-scheduler", session_id="session-scheduler")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject), observed_at=NOW)
        other_context = TaskContext(context.actor, self.identity.resolve_role(other_scope, observed_at=NOW).unwrap(),
                                    AudienceRef(other_scope, (context.actor.subject,)))
        instruction = app.state_maintenance.bindings[context.key][1]
        app.state_maintenance.bind(other_context, instruction)
        app.calendar_planning.invalidate(context)

        app.calendar_planning.advance()

        role_key = app.calendar.records.role_key(context)
        self.assertEqual(set(app.calendar_planning.workers), {role_key})
        await self.until(lambda: app.calendar_planning.results.get(context.key, {}).get("state") == "succeeded")
        self.assertEqual(len(self.called), 1)
        shared = app.calendar.inventory(other_context)["items"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["sources"], [])
        self.assertEqual(app.calendar_planning.status(other_context)["last_result"]["mode"], "daily")

    async def test_role_global_planning_excludes_private_window_material(self):
        app, context, _ = await self.ready()
        app.calendar.command(context, identifier="shared-role-plan", action="create", expected_revision=0,
            operation_key="shared-role-plan", data=calendar_data(title="角色自己的计划"))
        app.calendar.command(context, identifier="private-report", action="create", expected_revision=0,
            operation_key="private-report", data=calendar_data(kind="user_report", title="窗口自述"))
        app.calendar.command(context, identifier="private-commitment", action="create", expected_revision=0,
            operation_key="private-commitment", data=calendar_data(kind="commitment", title="窗口约定"))
        private_source = self.source(app, context, "private-plan-evidence")
        app.calendar.command(context, identifier="private-evidence-plan", action="create", expected_revision=0,
            operation_key="private-evidence-plan", data=calendar_data(title="带私聊来源的计划"),
            sources=(private_source,))
        instruction = app.state_maintenance.bindings[context.key][1]

        job = capture(app.calendar_planning, context, instruction, "daily", "role-global-test", role_global=True)

        self.assertEqual(job["sources"], ())
        self.assertEqual([item["title"] for item in job["material"]["calendar"]], ["角色自己的计划"])
        self.assertEqual(job["material"]["current_reports"], [])
        self.assertEqual(job["material"]["confirmed_goals_and_skills"], [])
        self.assertEqual(job["material"]["memories"], [])
        self.assertEqual(job["material"]["planning_scope"], "role")

    async def test_planning_material_injects_role_state_and_mode_specific_instruction(self):
        app, context, manager = await self.ready()
        self.grant(context.actor, context.scope, "role.life.read")
        self.configure(manager, enabled=True, mood_enabled=True)
        app.role_state.write(context, data=state_data(energy=55, sleep="刚醒不久", mood="安稳专注"),
                             expected_revision=0, operation_key="seed-planning-state")
        app.role_life.add_activity(RoleActivity("morning-reading",
            context.role_binding.role.subject_id, context.scope, NOW, RoleActivityState.ACTIVE,
            description="翻看散文集"))
        instruction = app.state_maintenance.bindings[context.key][1]

        job = capture(app.calendar_planning, context, instruction, "daily", "state-aware-daily")
        self.assertEqual(job["material"]["role_state"]["energy"], 55)
        self.assertEqual(job["material"]["role_state"]["sleep"], "刚醒不久")
        self.assertEqual(job["material"]["role_state"]["mood"], "安稳专注")
        self.assertEqual(job["material"]["recent_life"][0]["description"], "翻看散文集")

        result = await self.plan(app, context, slot="state-aware-run", mode="daily", manual=True)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertIn("当前为短期（当日）规划", self.called[-1].original.instruction)

    async def test_calendar_material_is_bounded_and_marks_omitted_details(self):
        app, context, manager = await self.ready()
        self.configure(manager, planning_prompt="保留空闲" * 800, planning_annual_calendars=json.dumps([
            annual(2026, holidays=[{"name": "合成假期", "start": "2026-09-21", "end": "2026-12-25"}])
        ], ensure_ascii=False))
        for index in range(30):
            app.calendar.command(context, identifier=f"large-{index:03}", action="create", expected_revision=0,
                operation_key=f"large-{index:03}", data=calendar_data(title="待考虑的安排 " + str(index), detail="旧说明" * 1365))
        job = capture(app.calendar_planning, context, app.state_maintenance.bindings[context.key][1], "horizon", "bounded")
        self.assertLessEqual(len(json.dumps(job["material"], ensure_ascii=False)), MATERIAL_CHARS)
        self.assertTrue(job["material"]["calendar_context"]["configured_annual_calendar"]["partial"])
        self.assertTrue(job["material"]["coverage"]["material_limited"])
        self.assertTrue(job["material"]["coverage"]["calendar_details_partial"])
        self.assertLess(len(job["material"]["calendar"]), 30)

    async def test_planning_calendar_context_uses_role_timezone_without_claiming_leave(self):
        from datetime import datetime, timezone

        app, context, manager = await self.ready()
        self.configure(manager, planning_timezone="Asia/Shanghai", planning_horizon_days=120)
        self.clock.advance(datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc) - self.clock.now())
        instruction = app.state_maintenance.bindings[context.key][1]
        daily = capture(app.calendar_planning, context, instruction, "daily", "calendar-day")
        self.assertEqual(daily["material"]["calendar_context"]["today"]["local_date"], "2026-10-01")
        self.assertEqual(daily["material"]["calendar_context"]["today"]["weekday"], "星期四")
        self.assertEqual(daily["material"]["calendar_context"]["fixed_gregorian_holidays_in_window"],
                         [{"date": "2026-10-01", "name": "国庆节"}])
        horizon = capture(app.calendar_planning, context, instruction, "horizon", "calendar-horizon")
        self.assertEqual(horizon["material"]["calendar_context"]["fixed_gregorian_holidays_in_window"],
                         [{"date": "2026-10-01", "name": "国庆节"}, {"date": "2027-01-01", "name": "元旦"}])
        self.assertIn("不等于放假", INSTRUCTION)

    async def test_capture_uses_configured_annual_dates_and_rejects_stale_settings(self):
        app, context, manager = await self.ready()
        data = json.dumps([annual(2026, holidays=[
            {"name": "合成休假", "start": "2026-10-01", "end": "2026-10-02"}],
            makeup_workdays=[{"date": "2026-10-04", "name": "合成调休"}])], ensure_ascii=False)
        self.configure(manager, planning_annual_calendars=data)
        self.clock.advance(datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc) - self.clock.now())
        instruction = app.state_maintenance.bindings[context.key][1]
        daily = capture(app.calendar_planning, context, instruction, "daily", "configured-day")
        projected = daily["material"]["calendar_context"]["configured_annual_calendar"]
        self.assertEqual([row["date"] for row in projected["days"]], ["2026-10-01"])
        self.assertEqual(projected["days"][0]["holiday"], "合成休假")
        self.assertEqual(projected["basis"], "operator_configured_annual_calendar")
        horizon = capture(app.calendar_planning, context, instruction, "horizon", "configured-horizon")
        self.assertEqual([row["date"] for row in
                          horizon["material"]["calendar_context"]["configured_annual_calendar"]["days"]],
                         ["2026-10-01", "2026-10-02", "2026-10-04"])
        self.assertIn("未提供的年份或日期视为未知", INSTRUCTION)
        self.configure(manager, planning_annual_calendars="")
        with self.assertRaises(TaskError):
            validate(app.calendar_planning, horizon, self.clock.now())


    async def test_dst_gap_blocks_planning_without_faulting_host(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        app, context, manager = await self.ready()
        self.configure(manager, planning_timezone="America/New_York", planning_refine_minutes=180)
        self.clock.advance(datetime(2027, 3, 14, 6, 30, tzinfo=timezone.utc) - self.clock.now())
        now = self.clock.now()
        app.calendar.command(context, identifier="dst-gap", action="create", expected_revision=0,
            operation_key="dst-gap", data=calendar_data(schedule=json.loads(encode(Schedule(
                datetime(2027, 3, 13, 7, 30, tzinfo=timezone.utc), "America/New_York", Frequency.DAILY, count=3)))))
        local = now.astimezone(ZoneInfo("America/New_York"))
        for mode, period in (("daily", local.date().isoformat()), ("horizon", "%04d-W%02d" % local.isocalendar()[:2])):
            slot = mode + ":America/New_York:" + period + ":settings:" + app.role_state.planning_settings_token(context)
            identifier = app.calendar_planning.identifier(context, slot)
            app.calendar_planning.operations.reserve(OperationRef(identifier, "calendar.planning", context.scope,
                VersionRef(identifier, 1), identifier), parameter_hash=digest(slot), observed_at=now).unwrap()
        app.calendar_planning.advance()
        self.assertEqual(app.calendar_planning.results[context.key]["state"], "blocked")
        self.assertEqual(app.calendar_planning.results[context.key]["code"], "schedule_needs_resolution")
        app.notify()
        await asyncio.sleep(0.02)
        self.assertEqual(app.state, "running", app.failure_code)

    async def test_moving_one_occurrence_retains_its_refinement_identity(self):
        app, context, _ = await self.ready()
        app.calendar.command(context, identifier="movable", action="create", expected_revision=0,
            operation_key="movable", data=calendar_data(schedule=json.loads(encode(Schedule(NOW + timedelta(minutes=5), "Asia/Shanghai")))))
        settings = app.role_state.settings(context)[0]
        # Mark the two broader opportunities so the scheduler reaches refinement.
        for _ in range(2):
            mode, slot, _ = app.calendar_planning.opportunity(context, settings, NOW)
            identifier = app.calendar_planning.identifier(context, slot)
            app.calendar_planning.operations.reserve(OperationRef(identifier, "calendar.planning", context.scope,
                VersionRef(identifier, 1), identifier), parameter_hash=digest(slot), observed_at=NOW).unwrap()
        original = app.calendar_planning.opportunity(context, settings, NOW)
        app.calendar.command(context, identifier="movable", action="exception", expected_revision=1,
            operation_key="move", data={"sequence": 0, "start": (NOW + timedelta(minutes=10)).isoformat()})
        moved = app.calendar_planning.opportunity(context, settings, NOW)
        self.assertEqual(original[1], moved[1])
        self.assertNotEqual(original[2]["start"], moved[2]["start"])

    async def test_invalid_model_output_keeps_original_calendar_and_does_not_replay(self):
        app, context, _ = await self.ready()
        async def malformed(envelope):
            return answered('{"items":[{"title":"半截')
        self.model_behavior = malformed
        result = await self.plan(app, context)
        self.assertNotEqual(result["state"], "succeeded")
        self.assertEqual(app.calendar.inventory(context)["items"], [])
        self.assertTrue((await self.plan(app, context))["replayed"])
        self.assertEqual(len(self.called), 1)

    async def test_failed_midnight_plan_is_observed_again_with_new_future_material_after_restart(self):
        app, context, _ = await self.ready()
        zone = ZoneInfo("Asia/Shanghai")
        local_midnight = datetime.combine(NOW.astimezone(zone).date() + timedelta(days=1), time(), zone)
        self.clock.advance(local_midnight - self.clock.now())
        planner = app.calendar_planning
        settings = app.role_state.settings(context)[0]
        mode, original_slot, _ = planner.opportunity(context, settings, self.clock.now())
        instruction = app.state_maintenance.bindings[context.key][1]
        async def timed_out(_):
            raise TaskError("provider_wait_timed_out", "unknown remote processing; no local plan")
        self.model_behavior = timed_out
        failed = await planner.run(context, instruction, mode=mode, slot=original_slot, automatic=True)
        self.assertEqual(failed["state"], "failed")
        self.assertTrue((await planner.run(context, instruction, mode=mode, slot=original_slot, automatic=True))["replayed"])
        self.assertEqual(planner.status(context)["daily_result"]["code"], "provider_wait_timed_out")
        self.assertEqual(datetime.fromisoformat(planner.status(context)["next_review_at"]).hour, 6)
        self.assertEqual(len(self.called), 1)

        restarted = CalendarPlanning(self.runtime)
        restarted.attach(app)
        self.assertEqual(restarted.status(context)["daily_result"]["code"], "provider_wait_timed_out")
        self.clock.advance(timedelta(hours=13))
        planner.next_checks[context.key] = self.clock.now() + timedelta(days=1)
        mode, new_slot, _ = restarted.opportunity(context, settings, self.clock.now())
        self.assertEqual(mode, "daily")
        self.assertEqual(new_slot, original_slot + ":review:12")
        self.assertNotEqual(restarted.identifier(context, new_slot), failed["task_id"])
        self.model_behavior = self.response
        # The real host creates a fresh registration with its fresh planner on
        # restart; this fixture keeps the original supervisor registration, so
        # run through the original owner after checking the durable restart view.
        result = await planner.run(context, instruction, mode=mode, slot=new_slot, automatic=True)
        self.assertEqual(result["state"], "succeeded", result)
        material = json.loads(self.called[-1].original.context.materials[0].payload)
        self.assertEqual(datetime.fromisoformat(material["now"]), self.clock.now())
        self.assertEqual(self.called[-1].original.response_format, "json_object")
        self.assertEqual(len(app.calendar.inventory(context)["items"]), 1)
        self.assertTrue(restarted.attempted(context, original_slot, automatic=True))
        self.assertEqual(len(self.called), 2)
        await restarted.close()

    async def test_intentionally_empty_daily_plan_is_not_regenerated_by_daypart(self):
        app, context, _ = await self.ready()
        planner = app.calendar_planning
        settings = app.role_state.settings(context)[0]
        mode, slot, _ = planner.opportunity(context, settings, NOW)
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered('{"items":[]}'))
        result = await planner.run(context, app.state_maintenance.bindings[context.key][1],
                                   mode=mode, slot=slot, automatic=True)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(result["saved"], 0)
        self.clock.advance(timedelta(hours=6))
        self.assertEqual(planner.opportunity(context, settings, self.clock.now())[0], "horizon")
        self.assertIsNone(planner.status(context)["next_review_at"])

    async def test_daily_review_waits_between_failures_and_does_not_catch_up_old_dayparts(self):
        app, context, _ = await self.ready()
        planner = app.calendar_planning
        settings = app.role_state.settings(context)[0]
        mode, slot, _ = planner.opportunity(context, settings, NOW)
        async def failed(_):
            raise TaskError("provider_wait_timed_out", "model wait timed out")
        self.model_behavior = failed
        instruction = app.state_maintenance.bindings[context.key][1]
        await planner.run(context, instruction, mode=mode, slot=slot, automatic=True)
        self.assertEqual(planner.opportunity(context, settings, self.clock.now())[0], "horizon")
        self.clock.advance(timedelta(hours=5))
        opportunity = planner.opportunity(context, settings, self.clock.now())
        self.assertEqual(opportunity[1], slot + ":review:12")
        await planner.run(context, instruction, mode=opportunity[0], slot=opportunity[1], automatic=True)
        self.assertEqual(planner.opportunity(context, settings, self.clock.now())[0], "horizon")
        self.assertEqual(len(self.called), 2)
        self.assertEqual(datetime.fromisoformat(planner.status(context)["next_review_at"]).hour, 18)

    async def test_daily_review_reports_the_actual_wait_within_a_daypart(self):
        app, context, _ = await self.ready()
        planner = app.calendar_planning
        settings = app.role_state.settings(context)[0]
        mode, slot, _ = planner.opportunity(context, settings, NOW)
        async def failed(_):
            raise TaskError("provider_wait_timed_out", "model wait timed out")
        self.model_behavior = failed
        await planner.run(context, app.state_maintenance.bindings[context.key][1],
                          mode=mode, slot=slot, automatic=True)
        next_review = datetime.fromisoformat(planner.status(context)["next_review_at"])
        self.assertEqual(next_review, NOW + timedelta(hours=1))
        self.clock.advance(timedelta(minutes=59))
        self.assertEqual(planner.opportunity(context, settings, self.clock.now())[0], "horizon")
        self.clock.advance(timedelta(minutes=1))
        self.assertEqual(planner.opportunity(context, settings, self.clock.now())[1], slot + ":review:6")
        self.assertEqual(len(self.called), 1)

    async def test_shutdown_cancels_planning_and_restart_keeps_attempt_ledger(self):
        app, context, _ = await self.ready()
        entered = asyncio.Event()
        async def hanging(envelope):
            entered.set()
            await asyncio.Event().wait()
        self.model_behavior = hanging
        task = asyncio.create_task(self.plan(app, context))
        await asyncio.wait_for(entered.wait(), 3)
        await app.close()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(app.calendar_planning.attempted(context, "manual-day"))
        self.assertEqual(app.calendar.records.list(context), ())
        self.assertEqual(app.calendar_planning.active_handles, ())

    async def test_the_daily_slot_is_gated_by_local_date_and_never_by_time_of_day(self):
        # C2-5: life_schedule_config.daily_plan_time is retired because V2 gates the
        # daily plan by local date, not by a time. V1 required "at or after 07:30"
        # (daily_state.py:10208-10213); here any hour on the same local day produces
        # the identical attempt key, which is what makes a configurable time
        # meaningless rather than merely missing.
        app, context, _ = await self.ready()
        settings = app.role_state.settings(context)[0]
        values = planning_values(settings)
        zone = ZoneInfo(values["planning_timezone"])
        day = self.clock.now().astimezone(zone).date()
        seen = set()
        for hour in range(24):
            local = datetime.combine(day, time(hour=hour, minute=5), tzinfo=zone)
            opportunity = app.calendar_planning.opportunity(context, values, local.astimezone(timezone.utc))
            self.assertIsNotNone(opportunity, f"hour {hour} produced no daily opportunity")
            self.assertEqual(opportunity[0], "daily")
            seen.add(opportunity[1])
        # One key for the whole local day, and it carries the date, not an hour.
        self.assertEqual(len(seen), 1, seen)
        slot = seen.pop()
        self.assertEqual(slot, "daily:" + values["planning_timezone"] + ":" + day.isoformat()
                         + ":settings:" + app.role_state.planning_settings_token(context))
        self.assertNotIn(":", day.isoformat())

    async def test_the_next_local_day_is_a_new_slot(self):
        app, context, _ = await self.ready()
        settings = app.role_state.settings(context)[0]
        values = planning_values(settings)
        zone = ZoneInfo(values["planning_timezone"])
        day = self.clock.now().astimezone(zone).date()
        first = app.calendar_planning.opportunity(
            context, values, datetime.combine(day, time(23, 59), tzinfo=zone).astimezone(timezone.utc))
        after_midnight = datetime.combine(day + timedelta(days=1), time(0, 1), tzinfo=zone)
        second = app.calendar_planning.opportunity(context, values, after_midnight.astimezone(timezone.utc))
        self.assertNotEqual(first[1], second[1])
        self.assertIn(":" + (day + timedelta(days=1)).isoformat() + ":settings:", second[1])


class CalendarGlobalSwitchTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_state_maintenance_schedule_is_one_record_per_role(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        other_scope = replace(context.scope, native_scope_id="other-window", conversation_id="conversation-other",
                              session_id="session-other")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject), observed_at=NOW)
        other_context = TaskContext(context.actor, self.identity.resolve_role(other_scope, observed_at=NOW).unwrap(),
                                    AudienceRef(other_scope, (context.actor.subject,)))

        app.state_maintenance.bind(context, "same persona")
        app.state_maintenance.bind(other_context, "same persona")
        rows = self.runtime.read_all("SELECT * FROM role_state_schedules", ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["context_key"], role_schedule_key(context))
        self.assertEqual(TaskContext.restore(json.loads(rows[0]["context_json"])), context)

        self.grant(other_context.actor, other_scope, "role.state.read")
        self.grant(other_context.actor, other_scope, "role.state.maintain")
        app.state_maintenance.bind(other_context, "same persona")
        rows = self.runtime.read_all("SELECT * FROM role_state_schedules", ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["context_key"], role_schedule_key(context))
        self.assertEqual(TaskContext.restore(json.loads(rows[0]["context_json"])), other_context)

    async def test_role_plans_follow_role_while_private_calendar_entries_stay_in_their_window(self):
        _, app, context, manager = self.setup_state()
        legacy = VersionedRecords(self.runtime, "calendar")
        legacy.change(context, "legacy-role-plan", operation_key="legacy-role-plan", expected_revision=0,
            parameters=["legacy"], at=NOW, transform=lambda current: {
                **calendar_data(), "state": "accepted", "sources": [], "exceptions": {}, "details": {},
                "accepted_by": [], "changed_by": context.role_binding.role, "reminder_id": None,
            })
        legacy_fields = tuple(field for field in state_configuration_fields()
                              if field.field_id in {"role.state.calendar_enabled", "role.state.planning_enabled",
                                                    "role.state.schedule_simulation_mode",
                                                    "role.state.planning_weather_enabled",
                                                    "role.state.planning_weather_location",
                                                    "role.state.planning_weather_max_age_minutes"})
        SparseConfigurationOwner(self.runtime).write(target_for(context), legacy_fields,
            changes={"role.state.calendar_enabled": False, "role.state.planning_enabled": False,
                     "role.state.schedule_simulation_mode": "continuous",
                     "role.state.planning_weather_enabled": True,
                     "role.state.planning_weather_location": json.dumps({"name":"旧窗口地点"}, ensure_ascii=False),
                     "role.state.planning_weather_max_age_minutes": 30},
            reset=(), expected_revision=0, observed_at=NOW)
        defaults = app.role_state.settings(context)[0]
        self.assertTrue(defaults["calendar_enabled"])
        self.assertFalse(defaults["planning_enabled"])
        self.assertEqual(defaults["schedule_simulation_mode"], "orchestrated")
        self.assertFalse(defaults["planning_weather_enabled"])
        self.assertEqual(defaults["planning_weather_location"], "")
        self.assertEqual(defaults["planning_weather_max_age_minutes"], 120)

        other_scope = replace(context.scope, native_scope_id="second-private", conversation_id="conversation-second",
                              session_id="session-second")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject), observed_at=NOW)
        other_context = TaskContext(context.actor, self.identity.resolve_role(other_scope, observed_at=NOW).unwrap(),
                                    AudienceRef(other_scope, (context.actor.subject,)))

        role_location = json.dumps({"name":"角色所在地"}, ensure_ascii=False)
        self.configure(manager, calendar_enabled=True, planning_enabled=True, schedule_simulation_mode="orchestrated",
            planning_weather_enabled=True, planning_weather_location=role_location,
            planning_weather_max_age_minutes=45)
        for target in (context, other_context):
            settings = app.role_state.settings(target)[0]
            self.assertTrue(settings["calendar_enabled"])
            self.assertTrue(settings["planning_enabled"])
            self.assertEqual(settings["schedule_simulation_mode"], "orchestrated")
            self.assertTrue(settings["planning_weather_enabled"])
            self.assertEqual(settings["planning_weather_location"], role_location)
            self.assertEqual(settings["planning_weather_max_age_minutes"], 45)
            app.calendar.access.check(target, "calendar.write")
            self.assertTrue(app.tasks.policy.scope_access(target.actor, scope=target.scope,
                action="calendar.write", observed_at=NOW, allow_private_owner=True).is_success)
            self.assertFalse(app.tasks.identity.authorize(target.actor, scope=target.scope,
                capability="calendar.write", observed_at=NOW).is_success)

        app.calendar.command(context, identifier="first-window-plan", action="create", expected_revision=0,
            operation_key="create-first-window-plan", data=calendar_data(), sources=())
        app.calendar.command(context, identifier="first-window-report", action="create", expected_revision=0,
            operation_key="create-first-window-report", data=calendar_data(kind="user_report"), sources=())
        app.calendar.command(context, identifier="first-window-commitment", action="create", expected_revision=0,
            operation_key="create-first-window-commitment", data=calendar_data(kind="commitment"), sources=())
        self.assertEqual(app.calendar.read(other_context, "first-window-plan")["kind"], "role_plan")
        for identifier in ("first-window-report", "first-window-commitment"):
            with self.subTest(identifier=identifier), self.assertRaises(TaskError) as missing:
                app.calendar.read(other_context, identifier)
            self.assertEqual(missing.exception.code, "calendar_unavailable")

        self.assertEqual(app.calendar.read(other_context, "legacy-role-plan")["kind"], "role_plan")

        private_source = self.source(app, context, "private-calendar-evidence")
        app.calendar.command(context, identifier="evidence-bound-plan", action="create", expected_revision=0,
            operation_key="create-evidence-bound-plan", data=calendar_data(), sources=(private_source,))
        shared_evidence_plan = app.calendar.read(other_context, "evidence-bound-plan")
        self.assertEqual(shared_evidence_plan["sources"], app.calendar.read(context, "evidence-bound-plan")["sources"])
        self.assertEqual(app.calendar.inventory(other_context)["skipped"], 0)

        self.identity.issue_grant(grant_id="role-plan-reminder-create", subject=context.actor.subject,
            scope=context.scope, capability="reminder.create", tier=AuthorizationTier.STANDARD,
            valid_from=NOW, expected_revision=self.identity.revision)
        self.identity.issue_grant(grant_id="role-plan-reminder-deliver", subject=context.actor.subject,
            scope=context.scope, capability="reminder.deliver", tier=AuthorizationTier.STANDARD,
            valid_from=NOW, expected_revision=self.identity.revision)
        app.calendar.remind(context, identifier="first-window-plan", expected_revision=1,
            operation_key="first-window-reminder", sequence=0)
        reminder_id = app.calendar.read(context, "first-window-plan")["reminder_id"]
        active_reminder = app.agenda.repository.reminder(reminder_id)
        with self.assertRaises(TaskError) as cross_window:
            app.calendar.command(other_context, identifier="first-window-plan", action="cancel",
                expected_revision=2, operation_key="cross-window-cancel")
        self.assertEqual(cross_window.exception.code, "calendar_reminder_context_mismatch")
        self.assertEqual(app.agenda.repository.reminder(reminder_id).state, "active")
        self.assertEqual(app.calendar.read(other_context, "first-window-plan")["state"], "accepted")

        self.configure(manager, calendar_enabled=False)
        for target in (context, other_context):
            self.assertFalse(app.tasks.identity.authorize(target.actor, scope=target.scope,
                capability="calendar.write", observed_at=NOW).is_success)
            with self.assertRaises(TaskError) as disabled:
                app.calendar.access.check(target, "calendar.read")
            self.assertEqual(disabled.exception.code, "calendar_disabled")
        calendar_items = manager.status(self.core).unwrap()["calendar"]["items"]
        self.assertEqual(len(calendar_items), 5)
        self.assertEqual({item["kind"] for item in calendar_items}, {"role_plan", "user_report", "commitment"})


class OrchestratedScheduleContextTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_orchestrated_mode_injects_the_narrow_current_window(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=True, schedule_simulation_mode="orchestrated", enabled=True)
        plan_source = self.source(app, context, "orchestrated-plan-source")
        app.calendar.command(context, identifier="orchestrated-plan", action="create", expected_revision=0,
            operation_key="orchestrated-plan", data=calendar_data(), sources=(plan_source,))

        consumer = app.messages.role_state_context
        snapshot = consumer.capture(context, NOW)
        self.assertEqual(snapshot["schedule_simulation_mode"], "orchestrated")
        self.assertEqual(snapshot["calendar"]["items"][0]["title"], "私人阅读安排")
        source = self.source(app, context, "orchestrated-message")
        materials = consumer.contributions(snapshot, context, NOW, fallback_source=source)
        instruction = consumer.instruction(materials)
        self.assertIn("窄窗口", instruction)
        self.assertIn("私人阅读安排", instruction)
        self.assertIn("不得声称该时段没课、没安排或完全空闲", instruction)
        self.assertNotIn("连续日程（HDSI）", instruction)


class ContinuousScheduleContextTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_continuous_mode_preserves_partial_context_without_calendar(self):
        _, app, context, manager = self.setup_state()
        for calendar_enabled, calendar in ((False, app.calendar), (True, None)):
            with self.subTest(calendar_enabled=calendar_enabled):
                self.configure(manager, calendar_enabled=calendar_enabled, schedule_simulation_mode="continuous")
                consumer = ConversationStateContext(app.role_state, calendar)
                snapshot = consumer.capture(context, NOW)
                self.assertEqual(snapshot["projection_version"], 4)
                self.assertEqual(snapshot["calendar"], {"items": [], "partial": True, "skipped": 0})
                materials = consumer.contributions(snapshot, context, NOW)
                self.assertEqual(len(materials), 1)
                self.assertEqual(materials[0].source.owner, "role.calendar.projection")
                instruction = consumer.instruction(materials)
                self.assertIn("连续日程（HDSI）", instruction)
                self.assertIn("材料可能不完整", instruction)

    async def test_continuous_mode_supplies_calendar_context_without_role_state_and_rechecks_access(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, calendar_enabled=True, schedule_simulation_mode="continuous")
        plan_source = self.source(app, context, "continuous-plan-source")
        app.calendar.command(context, identifier="continuous-plan", action="create", expected_revision=0,
            operation_key="continuous-plan", data=calendar_data(), sources=(plan_source,))

        consumer = app.messages.role_state_context
        self.assertIs(consumer.calendar, app.calendar)
        snapshot = consumer.capture(context, NOW)
        self.assertEqual(snapshot["projection_version"], 4)
        self.assertEqual(snapshot["calendar"]["items"][0]["title"], "私人阅读安排")
        source = self.source(app, context, "continuous-message")
        materials = consumer.contributions(snapshot, context, NOW, fallback_source=source)
        instruction = consumer.instruction(materials)
        self.assertIn("连续日程（HDSI）", instruction)
        self.assertIn("私人阅读安排", instruction)
        self.assertIsNone(app.calendar_planning.opportunity(
            context, planning_values(app.role_state.settings(context)[0]), NOW))

        self.revoke_source(app, plan_source)
        with self.assertRaises(TaskError):
            consumer.contributions(snapshot, context, NOW, fallback_source=source)

        second_source = self.source(app, context, "continuous-plan-source-2")
        app.calendar.command(context, identifier="continuous-plan-2", action="create", expected_revision=0,
            operation_key="continuous-plan-2", data=calendar_data(title="另一项角色计划"), sources=(second_source,))
        second_snapshot = consumer.capture(context, NOW)
        self.configure(manager, calendar_enabled=False)
        with self.assertRaises(TaskError):
            consumer.contributions(second_snapshot, context, NOW, fallback_source=source)
