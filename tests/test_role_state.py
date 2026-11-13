"""State and calendar contracts through the actual isolated host assembly."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from companion_v2.agenda.models import Frequency, Schedule
from companion_v2.agenda.calendar_access import role_grant_prefix
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.execution.models import TaskContext, TaskError, encode
from companion_v2.foundation.refs import AudienceRef, SourceRef, VersionRef
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.model_gateway.models import MAX_TOOL_CATALOG, Amount, BudgetPolicy, ToolCall
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.role_work.growth import GrowthKind, GrowthState, RoleGrowthRecord
from companion_v2.role_work.state.maintenance import (PROMPT as STATE_MAINTENANCE_PROMPT,
    STATE_INTERVENTION_INSTRUCTION, state_output_budget, state_profile)
from companion_v2.role_work.state.management import StateManagement
from companion_v2.role_work.state.service import FIELDS
from companion_v2.sources.models import SourceRecord
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered, call, requested


def state_data(**changes):
    return {name: [] if name == "conditions" else None for name in FIELDS} | changes


class StateOutputBudgetTests(unittest.TestCase):
    @staticmethod
    def provider(ceiling):
        return type("Provider", (), {"maximum_output_tokens": ceiling})()

    def test_reasoning_models_get_state_headroom_but_never_exceed_provider(self):
        self.assertEqual(state_output_budget(2048, [self.provider(65536)]), 8192)
        self.assertEqual(state_output_budget(2048, [self.provider(4096)]), 4096)
        self.assertEqual(state_output_budget(2048, [self.provider(2048)]), 2048)
        self.assertEqual(state_output_budget(2048, [self.provider(2048), self.provider(1024)]), 1024)
        self.assertEqual(state_output_budget(100, [self.provider(65536)]), 8192)


def calendar_data(**changes):
    return dict(kind="role_plan", title="私人阅读安排", detail="读自己选的书",
        schedule=json.loads(encode(Schedule(NOW + timedelta(hours=1), "Asia/Shanghai"))),
        duration_minutes=30, presence="阅读中") | changes


class StateInterventionPromptTests(unittest.TestCase):
    def test_authorized_context_can_update_state_without_role_echo(self):
        self.assertIn("不要求特定说法或角色复述", STATE_INTERVENTION_INSTRUCTION)
        self.assertIn("把握较高的主观状态可以适度、短时变化", STATE_INTERVENTION_INSTRUCTION)
        self.assertIn("睡眠、休息和现实活动仍须对应执行回执", STATE_INTERVENTION_INSTRUCTION)
        self.assertNotIn("关心、建议、邀请", STATE_MAINTENANCE_PROMPT)


class StateFixture(HostFixture):
    def setUp(self):
        super().setUp()
        self.profiles += (state_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="state-tests", fields=configuration_fields(self.profiles))

    def setup_state(self, **options):
        host = self.host(**options)
        app = host.application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        return host, app, context, StateManagement(app, context)

    def configure(self, manager, **changes):
        self.counter += 1
        before = manager.status(self.core).unwrap()
        return manager.command(self.core, dict(action="configure", operation_key="config-" + str(self.counter),
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"], changes=changes)).unwrap()

    def source(self, app, context, identifier="original"):
        source = SourceRef(identifier, "test", context.scope, VersionRef(identifier, 1), self.clock.now())
        app.messages.sources.publish(SourceRecord(source, context.audience, identifier, "原始材料 " + identifier),
                                     observed_at=self.clock.now()).unwrap()
        return source

    def revoke_source(self, app, source):
        from companion_v2.sources.dependencies import revoke_dependants
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET state = 'revoked' WHERE source_id = ?", (source.source_id,))
            revoke_dependants(self.runtime, source, self.clock.now())

    def event_command(self, app, context, *, identifier="event", revision=0, action="create", key=None, **kw):
        return app.calendar.command(context, identifier=identifier, expected_revision=revision, action=action,
                                    operation_key=key or action + str(revision), **kw)


class RoleStateTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_feature_switches_hide_projection_without_erasing_records_or_display_preferences(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, mood_enabled=True)
        payload = state_data(energy=48, sleep="难以入睡", busy="忙碌", location="小屋", mood="心情平和",
            conditions=[dict(kind="sleep", text="有点失眠", expires_at=None),
                        dict(kind="mood", text="平缓的余韵", expires_at=None)])
        app.role_state.write(context, data=payload, expected_revision=0, operation_key="seed-facets")
        original = app.role_state.records.get(context, "current")
        self.configure(manager, energy_enabled=False, sleep_enabled=False, busy_enabled=False,
            location_enabled=False, mood_enabled=False, home_show_mood=False, home_only_special_sleep=True,
            home_only_special_cycle=True)
        read = app.role_state.read(context)
        for name in ("energy", "sleep", "busy", "location", "mood"):
            self.assertIsNone(read["values"][name], name)
        self.assertEqual(read["values"]["conditions"], [])
        self.assertEqual(app.role_state.records.get(context, "current"), original)
        saved = manager.status(self.core).unwrap()["settings"]
        self.assertFalse(saved["home_show_mood"])
        self.assertTrue(saved["home_only_special_sleep"])
        self.assertTrue(saved["home_only_special_cycle"])
        self.configure(manager, energy_enabled=True, sleep_enabled=True, busy_enabled=True,
            location_enabled=True, mood_enabled=True)
        self.assertEqual(app.role_state.read(context)["values"]["sleep"], "难以入睡")
        self.assertEqual(app.role_state.read(context)["values"]["energy"], 48)

    async def test_defaults_are_unknown_and_no_optional_grants(self):
        host, app, context, manager = self.setup_state()
        result = manager.status(self.core).unwrap()
        self.assertEqual(result["state"]["state"], "unknown")
        self.assertTrue(all(value is None for key, value in result["state"]["values"].items() if key != "conditions"))
        self.assertFalse(any(item["allowed"] for item in result["effective"].values()))
        settings, _token = app.role_state.settings(context)
        self.assertTrue(settings["calendar_enabled"])
        self.assertFalse(settings["planning_enabled"])
        self.assertIn("calendar.query", {t.action for t in app.tools.catalog_for(context)})
        self.assertLessEqual(len(app.tools.catalog_for(context)), MAX_TOOL_CATALOG)

    async def test_home_occurrences_cover_the_role_local_day(self):
        _host, app, _context, manager = self.setup_state()
        captured = {}

        def occurrences(_context, *, start, end, operator=None, diagnostics=False):
            captured.update(start=start, end=end)
            return {"items": [], "partial": False, "skipped": 0}

        app.calendar.occurrences = occurrences
        result = manager.status(self.core).unwrap()
        zone = ZoneInfo(result["settings"]["planning_timezone"])
        local_start = app.now().astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(captured["start"], local_start.astimezone(timezone.utc))
        self.assertEqual(captured["end"], (local_start + timedelta(days=1)).astimezone(timezone.utc))

    async def test_core_settings_exact_grants_replay_and_revoke(self):
        _, app, context, manager = self.setup_state()
        before = manager.status(self.core).unwrap()
        command = dict(action="configure", operation_key="enable", edit_token=before["edit_token"],
                       role_edit_token=before["role_edit_token"],
                       changes={"enabled": True, "calendar_enabled": True, "typing_enabled": True})
        manager.command(self.core, command).unwrap()
        self.assertTrue(manager.command(self.core, command).unwrap()["replayed"])
        for action in ("role.state.read", "role.state.maintain", "platform.typing"):
            self.assertTrue(app.tasks.check_context_access(context, action=action, observed_at=NOW).is_success, action)
        for action in ("calendar.read", "calendar.write"):
            app.calendar.access.check(context, action)
            self.assertTrue(app.tasks.policy.scope_access(context.actor, scope=context.scope, action=action,
                observed_at=NOW, allow_private_owner=True).is_success, action)
            self.assertFalse(app.tasks.identity.authorize(context.actor, scope=context.scope,
                capability=action, observed_at=NOW).is_success, action)
        self.assertFalse(app.tasks.identity.authorize(context.actor, scope=context.scope, capability="platform.presence", observed_at=NOW).is_success)
        self.assertFalse(manager.command(self.important, command).is_success)
        role_prefix = role_grant_prefix(context.scope.installation_id, context.scope.bot_id, context.role_binding.role)
        for action in ("calendar.read", "calendar.write"):
            app.tasks.identity.issue_grant(grant_id=role_prefix + "legacy-" + action,
                subject=context.actor.subject, scope=context.scope, capability=action,
                tier=AuthorizationTier.STANDARD, valid_from=NOW,
                expected_revision=app.tasks.identity.revision)
        self.configure(manager, enabled=False, calendar_enabled=False, typing_enabled=False)
        self.assertFalse(app.tasks.identity.authorize(context.actor, scope=context.scope, capability="platform.typing", observed_at=NOW).is_success)
        for action in ("calendar.read", "calendar.write"):
            self.assertFalse(app.tasks.identity.authorize(context.actor, scope=context.scope, capability=action, observed_at=NOW).is_success)
        self.assertEqual(app.role_state.activity_context(context)["state"], "unavailable")

    async def test_expiry_and_disabled_facets_do_not_invent_recovery(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, hunger_enabled=False)
        payload = state_data(energy=12, hunger="有点饿", health="此前感到不适", sleep="困倦", cycle="可能处于周期",
            conditions=[dict(kind="health", text="短暂不适", expires_at=(NOW + timedelta(minutes=1)).isoformat()),
                        dict(kind="hunger", text="想吃饭", expires_at=None)])
        result = app.role_state.write(context, data=payload, expected_revision=0, operation_key="first")
        self.assertEqual(result["revision"], 1)
        self.assertTrue(app.role_state.write(context, data=payload, expected_revision=0, operation_key="first")["replayed"])
        read = app.role_state.read(context)
        self.assertIsNone(read["values"]["hunger"])
        self.assertIsNone(read["values"]["cycle"])
        self.clock.advance(timedelta(minutes=2))
        read = app.role_state.read(context)
        self.assertEqual(read["values"]["conditions"], [])
        self.assertEqual(read["expired_conditions"], 1)
        self.assertEqual(read["values"]["health"], "此前感到不适")
        self.assertEqual(read["values"]["sleep"], "困倦")
        with self.assertRaises(TaskError):
            app.role_state.write(context, data=payload, expected_revision=0, operation_key="stale")

    async def test_source_withdrawal_invalidates_derived_state_and_prevents_cross_scope(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        original = self.source(app, context)
        app.role_state.write(context, data=state_data(mood="被这段话触动"), expected_revision=0,
                             operation_key="one", sources=(original,))
        root = app.role_state.records.get(context, "current")["sources"]
        from companion_v2.identity_policy.private_domain import source_ref
        app.role_state.write(context, data=state_data(mood="余韵仍在"), expected_revision=1,
                             operation_key="two", sources=tuple(source_ref(s) for s in root))
        self.revoke_source(app, original)
        with self.assertRaises(TaskError):
            app.role_state.read(context)
        foreign = replace(original, scope=replace(SCOPE, session_id="elsewhere"))
        with self.assertRaises(TaskError):
            app.role_state.write(context, data=state_data(), expected_revision=2, operation_key="foreign", sources=(foreign,))

    async def test_contract_rejects_invalid_numbers_dates_and_partial_objects(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        for payload in ({"hunger": "饿"}, state_data(energy=True), state_data(energy=101),
                        state_data(online_mode={}), state_data(online_mode=["busy"]),
                        state_data(online_mode="sleeping"),
                        state_data(conditions=[dict(kind="sleep", text="困", expires_at="2026-09-21T00:00:00")])):
            with self.assertRaises((TaskError, ValueError)):
                app.role_state.write(context, data=payload, expected_revision=0, operation_key="invalid")
        self.assertIsNone(app.role_state.records.get(context, "current"))

    async def test_legacy_state_without_online_mode_remains_readable(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        legacy = state_data(mood="平静")
        del legacy["online_mode"]
        app.role_state.write(context, data=legacy, expected_revision=0, operation_key="legacy")
        self.assertIsNone(app.role_state.read(context)["values"]["online_mode"])

    async def test_role_grant_scope_is_saved_with_the_state_switch(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, maintenance_grant_scope="role")
        saved = manager.status(self.core).unwrap()["settings"]
        self.assertEqual(saved["maintenance_grant_scope"], "role")
        self.assertTrue(app.tasks.identity.authorize(context.actor, scope=context.scope,
            capability="role.state.maintain", observed_at=NOW).is_success)

    async def test_short_status_and_online_mode_share_presence_grant(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, presence_enabled=True, online_status_enabled=True)
        allowed = lambda: app.tasks.identity.authorize(context.actor, scope=context.scope,
            capability="platform.presence", observed_at=NOW).is_success
        self.assertTrue(allowed())
        self.configure(manager, presence_enabled=False)
        self.assertTrue(allowed())
        self.configure(manager, online_status_enabled=False)
        self.assertFalse(allowed())


class CalendarTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.host1, self.app, self.ctx, self.manager = self.setup_state()
        self.configure(self.manager, calendar_enabled=True)

    async def test_role_plan_evidence_is_readable_from_another_window_of_the_same_role(self):
        scope = replace(self.ctx.scope, native_scope_id="second-private", conversation_id="conversation-second",
                        session_id="session-second")
        self.personas.register_route(ConversationRoute(scope, user=self.ctx.actor.subject), observed_at=NOW)
        self.grant(self.ctx.actor, scope, "conversation.reply")
        other_context = TaskContext(self.ctx.actor, self.identity.resolve_role(scope, observed_at=NOW).unwrap(),
                                    AudienceRef(scope, (self.ctx.actor.subject,)))
        source = self.source(self.app, self.ctx, "cross-window-calendar-source")
        self.event_command(self.app, self.ctx, identifier="cross-window-plan", data=calendar_data(),
                           sources=(source,))

        record = self.app.calendar.read(other_context, "cross-window-plan")
        self.assertEqual(record["sources"], self.app.calendar.read(self.ctx, "cross-window-plan")["sources"])
        self.assertEqual(self.app.calendar.inventory(other_context)["skipped"], 0)
        window = self.app.calendar.occurrences(other_context, start=NOW, end=NOW + timedelta(days=1))
        self.assertEqual([item["id"] for item in window["items"]], ["cross-window-plan"])

    async def test_public_wording_edit_preserves_plan_sources_exceptions_and_reminder(self):
        app, context = self.app, self.ctx
        source = self.source(app, context)
        plan = calendar_data(schedule=json.loads(encode(Schedule(NOW + timedelta(hours=1), "Asia/Shanghai", Frequency.DAILY, count=3))))
        self.event_command(app, context, data=plan, sources=(source,))
        self.event_command(app, context, action="exception", revision=1, data={"sequence": 1, "start": None})
        app.calendar.remind(context, identifier="event", expected_revision=2, operation_key="remind")
        before = app.calendar.read(context, "event")
        reminder = app.agenda.repository.reminder(before["reminder_id"])
        item = self.manager.status(self.core).unwrap()["calendar"]["items"][0]
        command = dict(action="calendar.presence", id="event", operation_key="public-wording",
                       expected_revision=item["revision"], view_token=item["view_token"], data={"presence": "散步中"})
        result = self.manager.command(self.core, command).unwrap()
        self.assertEqual(result["revision"], 4)
        after = app.calendar.read(context, "event")
        for name in ("title", "detail", "kind", "schedule", "duration_minutes", "state", "accepted_by", "exceptions", "sources", "reminder_id"):
            self.assertEqual(after[name], before[name], name)
        self.assertEqual(after["presence"], "散步中")
        self.assertEqual(after["changed_by"], json.loads(encode(self.core.subject)))
        self.assertEqual(app.agenda.repository.reminder(before["reminder_id"]), reminder)
        self.assertTrue(self.manager.command(self.core, command).unwrap()["replayed"])
        self.assertEqual(self.manager.command(self.core, command | {"operation_key": "stale"}).failure.code, "record_revision_conflict")
        self.revoke_source(app, source)
        with self.assertRaises(TaskError):
            self.event_command(app, context, action="presence", revision=4, data={"presence": "休息中"})

    async def test_public_wording_edit_rejects_other_fields_nonplans_and_closed_plans(self):
        for kind in ("role_plan", "user_report", "commitment"):
            self.event_command(self.app, self.ctx, identifier=kind, key=kind, data=calendar_data(kind=kind))
        for kind in ("user_report", "commitment"):
            with self.assertRaises(TaskError) as error:
                self.event_command(self.app, self.ctx, identifier=kind, key="public-" + kind, action="presence", revision=1, data={"presence": "阅读中"})
            self.assertEqual(error.exception.code, "calendar_presence_target_invalid")
        for index, value in enumerate(({}, {"presence": "阅读中", "title": "覆盖"}, {"presence": "超过八个字的公开文案"}, {"presence": None})):
            with self.assertRaises(TaskError):
                self.event_command(self.app, self.ctx, identifier="role_plan", key="invalid-" + str(index), action="presence", revision=1, data=value)
        self.event_command(self.app, self.ctx, identifier="role_plan", key="empty", action="presence", revision=1, data={"presence": ""})
        self.assertEqual(self.app.calendar.read(self.ctx, "role_plan")["presence"], "")
        self.event_command(self.app, self.ctx, identifier="role_plan", key="close", action="cancel", revision=2)
        with self.assertRaises(TaskError) as error:
            self.event_command(self.app, self.ctx, identifier="role_plan", key="closed", action="presence", revision=3, data={"presence": "阅读中"})
        self.assertEqual(error.exception.code, "calendar_closed")

    async def test_commitments_need_human_acceptance_and_revision_invalidates_it(self):
        app, context = self.app, self.ctx
        data = calendar_data(kind="commitment")
        first = self.event_command(app, context, data=data)
        self.assertEqual(first["state"], "proposed")
        self.assertTrue(self.event_command(app, context, data=data)["replayed"])
        with self.assertRaises(TaskError):
            self.event_command(app, context, revision=1, action="accept")
        self.event_command(app, context, revision=1, action="accept", operator=self.core)
        self.event_command(app, context, revision=2, action="revise", data=data | {"title": "改个时间"})
        current = app.calendar.read(context, "event")
        self.assertEqual((current["state"], current["accepted_by"]), ("proposed", []))
        with self.assertRaises(TaskError):
            self.event_command(app, context, revision=1, action="cancel")

    async def test_recurrence_month_end_and_moved_or_skipped_occurrence(self):
        schedule = Schedule(datetime(2026, 1, 31, 4, tzinfo=timezone.utc), "Asia/Shanghai", Frequency.MONTHLY, count=4)
        self.event_command(self.app, self.ctx, data=calendar_data(schedule=json.loads(encode(schedule))))
        window = self.app.calendar.occurrences(self.ctx, start=datetime(2026, 2, 1, tzinfo=timezone.utc),
            end=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(window["items"][0]["start"][:10], "2026-02-28")
        self.event_command(self.app, self.ctx, action="exception", revision=1, data={"sequence": 1, "start": None})
        moved = "2026-02-20T04:00:00+00:00"
        self.event_command(self.app, self.ctx, action="exception", revision=2, data={"sequence": 2, "start": moved})
        window = self.app.calendar.occurrences(self.ctx, start=datetime(2026, 2, 1, tzinfo=timezone.utc),
            end=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual([(i["sequence"], i["start"][:10]) for i in window["items"]], [(2, "2026-02-20")])
        self.assertEqual(self.app.calendar.read(self.ctx, "event")["state"], "accepted")

    async def test_reminder_revision_cancels_original_and_withdrawal_blocks_dispatch(self):
        source = self.source(self.app, self.ctx)
        self.event_command(self.app, self.ctx, data=calendar_data(), sources=(source,))
        self.app.calendar.remind(self.ctx, identifier="event", expected_revision=1, operation_key="remind")
        reminder = self.app.calendar.read(self.ctx, "event")["reminder_id"]
        self.app.calendar.validate_reminder(self.ctx, reminder, NOW)
        self.event_command(self.app, self.ctx, action="revise", revision=2, data=calendar_data(title="新日程"), sources=(source,))
        self.assertEqual(self.app.agenda.repository.reminder(reminder).state, "cancelled")
        with self.assertRaises(TaskError):
            self.app.calendar.validate_reminder(self.ctx, reminder, NOW)
        self.app.calendar.remind(self.ctx, identifier="event", expected_revision=3, operation_key="remind-new")
        reminder = self.app.calendar.read(self.ctx, "event")["reminder_id"]
        self.revoke_source(self.app, source)
        with self.assertRaises(TaskError):
            self.app.calendar.validate_reminder(self.ctx, reminder, NOW)
        inventory = self.app.calendar.inventory(self.ctx)
        self.assertEqual(inventory["skipped"], 1)
        self.assertEqual(inventory["skipped_reasons"], {"source_unavailable": 1})

    async def test_calendar_tool_query_then_revision_and_evidence_dependencies(self):
        source = self.source(self.app, self.ctx)
        self.event_command(self.app, self.ctx, data=calendar_data(), sources=(source,))
        async def model(envelope):
            count = len(envelope.tool_history)
            if count == 0:
                return requested(call("query", "calendar.query", limit=20))
            if count == 1:
                return requested(ToolCall("edit", "calendar.command", encode(dict(action="revise", id="event", expected_revision=1,
                    data=calendar_data(title="新计划"), evidence_handles=["growth-input-1"]))))
            self.assertEqual(json.loads(envelope.tool_history[0].result_json)["state"], "superseded")
            return answered("计划改好了，仍只是计划。")
        self.model_behavior = model
        await self.start(self.host1)
        batch = self.host1.receive(self.event()).unwrap()
        self.advance(self.app)
        result = await self.done(self.app, batch)
        self.assertEqual(result["state"], "complete", dict(result))
        self.assertEqual(self.app.calendar.read(self.ctx, "event")["revision"], 2)
        rows = self.runtime.read_all("SELECT * FROM conversation_tool_calls WHERE batch_id = ? ORDER BY sequence", (batch,))
        dependencies = self.app.tools.calendar.confirmed_dependencies(rows[0], self.ctx, json.loads(rows[0]["result_json"]), rows[1:])
        self.assertIn(source, dependencies)
        self.assertEqual(len(self.sent), 1)


class StateMaintenanceTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self):
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        app.state_maintenance.bind(context, "原角色人格提示")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", ((NOW + timedelta(days=1)).isoformat(),))
        await self.start(host)
        return host, app, context, manager

    async def test_source_conversion_accepts_existing_references_and_stored_mappings(self):
        _, app, context, _ = await self.ready()
        source = self.source(app, context, "maintenance-source-ref")
        from companion_v2.identity_policy.private_domain import as_source_ref

        self.assertIs(as_source_ref(source), source)
        self.assertEqual(as_source_ref(json.loads(encode(source))), source)

    async def test_model_maintenance_keeps_persona_shared_budget_and_no_duplicate_calls(self):
        _, app, context, _ = await self.ready()
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data(hunger="有点饿", energy=45))))
        result = await app.state_maintenance.run(context, "原角色人格提示", "manual")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(app.role_state.read(context)["values"]["hunger"], "有点饿")
        envelope = self.called[0]
        self.assertIsInstance(envelope.original.event, BackgroundPromptTrigger)
        self.assertEqual(envelope.original.role_instruction, "原角色人格提示")
        self.assertEqual(envelope.original.output_format, "role_state")
        self.assertEqual(envelope.original.response_format, "json_object")
        self.assertIn("不能根据时间经过", envelope.original.instruction)
        replay = await app.state_maintenance.run(context, "原角色人格提示", "manual")
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.called), 1)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) AS n FROM model_calls", ())["n"], 1)

    async def test_new_private_input_fences_a_stale_state_intervention_before_dispatch(self):
        _, app, context, _ = await self.ready()
        self.identity.issue_grant(grant_id="state-intervention-window", subject=context.actor.subject,
            scope=context.scope, capability="role.state.intervene", tier=AuthorizationTier.STANDARD,
            valid_from=NOW, expected_revision=self.identity.revision)
        expected_epoch = app.state_maintenance._input_epochs.get(context.key, 0)
        app.state_maintenance.note_input(context, "new-message")

        with self.assertRaises(TaskError) as raised:
            await app.state_maintenance.run(context, "原角色人格提示", "stale-intervention",
                intervention=True, expected_input_epoch=expected_epoch)

        self.assertEqual(raised.exception.code, "state_inputs_changed")
        self.assertEqual(self.runtime.read_all("SELECT task_id FROM execution_tasks", ()), ())
        self.assertEqual(self.called, [])

    async def test_known_model_budget_failure_is_returned_for_maintenance_status(self):
        _, app, context, _ = await self.ready()
        async def exhausted(_envelope):
            raise TaskError("model_budget_exhausted", "private budget details")
        self.model_behavior = exhausted
        result = await app.state_maintenance.run(context, "原角色人格提示", "budget-exhausted")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["reason"], "worker_error")
        self.assertEqual(result["code"], "model_budget_exhausted")

    async def test_daily_budget_refusal_defers_maintenance_without_repeated_tasks(self):
        _, app, context, _ = await self.ready()
        app.gateway.ledger = BudgetLedger(self.runtime, BudgetPolicy(*(Amount(1, 100000) for _ in range(5))))
        result = await app.state_maintenance.run(context, "原角色人格提示", "deferred-first")
        self.assertEqual(result["state"], "deferred")
        self.assertEqual(result["code"], "model_budget_exhausted")
        before = self.runtime.read_one("SELECT COUNT(*) AS n FROM execution_tasks", ())["n"]
        again = await app.state_maintenance.run(context, "原角色人格提示", "deferred-next")
        self.assertEqual(again["state"], "deferred")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) AS n FROM execution_tasks", ())["n"], before)
        self.assertEqual(self.called, [])

    async def test_state_maintenance_does_not_reuse_conversation_inputs_already_in_the_previous_state(self):
        host, app, context, _ = await self.ready()
        host.receive(self.event("before-state")).unwrap()
        app.role_state.write(context, data=state_data(energy=18), expected_revision=0, operation_key="seed")
        self.clock.advance(timedelta(seconds=1))
        host.receive(self.event("after-state")).unwrap()

        job = app.state_maintenance.prepare(context, "原角色人格提示", "new-period")
        inputs = [item.payload for item in job["materials"] if item.purpose == "reported_conversation"]
        self.assertEqual(inputs, ["输入 after-state"])

    async def test_maintenance_processes_care_dialogue_in_its_authorized_window_once(self):
        from companion_v2.conversation.native_observations import NativeConversationObservations
        _, app, context, _ = await self.ready()
        other_scope = replace(context.scope, native_scope_id="care-window",
                              conversation_id="conversation-care", session_id="session-care")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject), observed_at=NOW)
        other = TaskContext(context.actor, self.identity.resolve_role(other_scope, observed_at=NOW).unwrap(),
                            AudienceRef(other_scope, (context.actor.subject,)))
        self.grant(other.actor, other.scope, "text")
        self.grant(other.actor, other.scope, "role.state.read")
        self.grant(other.actor, other.scope, "role.state.maintain")
        app.state_maintenance.bind(other, "原角色人格提示")
        observations = NativeConversationObservations(app.messages)
        observations.capture_together(other, observation_id="care-input", event_id="care-input",
            text="粥和药都给你了", at=NOW, kind="input")
        app.role_state.write(context, data=state_data(energy=12, health="之前感冒不适"),
                             expected_revision=0, operation_key="other-window-state")
        self.clock.advance(timedelta(seconds=1))
        observations.capture_together(other, observation_id="care-output", event_id="care-output",
            text="现在舒服些了，药好像起效了", at=self.clock.now(), kind="assistant")

        selected = app.state_maintenance._conversation_anchor((context, "原角色人格提示", None), self.clock.now())
        self.assertEqual(selected[0], other)
        job = app.state_maintenance.prepare(other, selected[1], "care-state")
        self.assertEqual([item["speaker"] for item in job["recent_dialogue"]], ["user", "assistant"])
        self.assertIn("药好像起效了", job["recent_dialogue"][-1]["text"])
        self.assertFalse(job["sleep_profile"]["has_sleep_history"])
        self.assertIsNone(job["sleep_execution"])
        self.assertEqual(app.state_maintenance.prepare(context, selected[1], "no-private-leak")["recent_dialogue"], ())
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data(energy=25, health="舒服些了"))))
        result = await app.state_maintenance.run(other, selected[1], "care-state")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(app.role_state.read(context)["values"]["health"], "舒服些了")
        self.assertEqual(app.state_maintenance.prepare(other, selected[1], "next-round")["recent_dialogue"], ())

    async def test_maintenance_does_not_replay_unsent_or_withdrawn_dialogue(self):
        from companion_v2.conversation.native_observations import NativeConversationObservations
        _, app, context, _ = await self.ready()
        observations = NativeConversationObservations(app.messages)
        reference = observations.capture_together(context, observation_id="care-source", event_id="care-source",
            text="给你的药", at=NOW, kind="input")
        observations.capture_output(context, turn_id="not-sent", event_id="not-sent",
            text="还没发出的好转感受", at=NOW)
        job = app.state_maintenance.prepare(context, "原角色人格提示", "before-withdrawal")
        self.assertEqual(len(job["recent_dialogue"]), 1)
        self.revoke_source(app, reference)
        self.assertEqual(app.state_maintenance.prepare(context, "原角色人格提示", "after-withdrawal")["recent_dialogue"], ())

    async def test_bounded_maintenance_dialogue_keeps_the_latest_care_feedback(self):
        from companion_v2.conversation.native_observations import NativeConversationObservations
        _, app, context, _ = await self.ready()
        observations = NativeConversationObservations(app.messages)
        for index in range(8):
            observations.capture_together(context, observation_id=f"older-{index}", event_id=f"older-{index}",
                text="较早的闲聊" * 320, at=self.clock.now(), kind="input")
            self.clock.advance(timedelta(seconds=1))
        observations.capture_together(context, observation_id="latest-care", event_id="latest-care",
            text="把粥和药给你了", at=self.clock.now(), kind="input")
        self.clock.advance(timedelta(seconds=1))
        observations.capture_together(context, observation_id="latest-relief", event_id="latest-relief",
            text="药好像起效了，舒服些了", at=self.clock.now(), kind="assistant")

        job = app.state_maintenance.prepare(context, "原角色人格提示", "bounded-care")
        items = job["recent_dialogue"]
        self.assertLessEqual(sum(len(item["text"]) for item in items), 10000)
        self.assertEqual([item["observation_id"] for item in items[-2:]], ["latest-care", "latest-relief"])
        self.assertEqual([item["at"] for item in items], sorted(item["at"] for item in items))

    async def test_history_marks_old_symptoms_as_simulation_without_resetting_state(self):
        _, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(energy=12, health="感冒不适", sleep="未睡好"),
                             expected_revision=0, operation_key="old-simulation")
        self.clock.advance(timedelta(days=2))
        job = app.state_maintenance.prepare(context, "原角色人格提示", "history-review")
        self.assertEqual(len(job["simulation_history"]), 1)
        self.assertEqual(job["simulation_history"][0]["updated_at"], NOW.isoformat(timespec="microseconds"))
        self.assertEqual(job["simulation_history"][0]["basis"], "previous_simulation")
        self.assertEqual(app.role_state.read(context)["values"]["energy"], 12)

    async def test_low_energy_enters_the_original_reply_without_refusal_gate(self):
        host, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(energy=1, hunger="饿了", busy="手头有事"),
                             expected_revision=0, operation_key="seed")
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        result = await self.done(app, batch)
        self.assertEqual(result["state"], "complete", dict(result))
        materials = [m for m in self.called[0].original.context.materials if m.purpose == "role_state"]
        self.assertEqual(len(materials), 1)
        self.assertEqual(json.loads(materials[0].payload)["simulation"]["values"]["energy"], 1)
        self.assertIsNotNone(materials[0].source)
        self.assertEqual(self.called[0].original.role_instruction, "原角色人格提示")
        self.assertEqual(len(self.sent), 2)

    async def test_actual_sleep_state_needs_independent_permission_and_wake_receipt(self):
        _, app, context, _ = await self.ready()
        life = app.life_runtime.bind(context, "原角色人格提示")
        self.grant(context.actor, context.scope, "role.life.run")
        sleep = life.start_sleep("nap", 5)
        self.assertEqual(app.role_state.activity_context(context)["items"], [])
        self.grant(context.actor, context.scope, "role.life.read")
        self.clock.advance(timedelta(minutes=6))
        # Merely querying after the planned end cannot complete an activity.
        actual = app.role_state.activity_context(context)["items"][0]
        self.assertEqual((actual["kind"], actual["state"]), ("sleep", "active"))
        life.wake(sleep.activity_id, expected_revision=sleep.revision, operation_id="wake-receipt")
        self.assertEqual(app.role_state.activity_context(context)["items"][0]["state"], "completed")

    async def test_state_progress_and_condition_expiry_keep_history_but_withdrawal_does_not(self):
        host, app, context, _ = await self.ready()
        source = self.source(app, context)
        app.role_state.write(context, data=state_data(energy=25, conditions=[dict(kind="mood", text="余韵",
            expires_at=(NOW + timedelta(seconds=10)).isoformat())]), expected_revision=0, operation_key="seed", sources=(source,))
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.clock.advance(timedelta(seconds=30))
        app.role_state.write(context, data=state_data(energy=50), expected_revision=1, operation_key="later")
        history = app.messages.history(context).unwrap()["items"]
        self.assertTrue(all(item["body_available"] for item in history))
        quotes = app.memory.writer.access.sent_sources
        reference = next(quotes.rows(context))
        quotes.materialize(context, reference)
        self.assertIsNotNone(quotes.validate(context, reference))
        self.revoke_source(app, source)
        self.assertFalse(any(item["body_available"] for item in app.messages.history(context).unwrap()["items"]))
        with self.assertRaises(TaskError):
            quotes.validate(context, reference)
        self.assertEqual(app.role_state.read(context)["revision"], 2)

    async def test_state_changed_during_generation_blocks_only_unsent_reply(self):
        host, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(energy=20), expected_revision=0, operation_key="seed")
        async def respond(envelope):
            app.role_state.write(context, data=state_data(energy=50), expected_revision=1, operation_key="human-update", operator=self.core)
            return answered("依据旧状态生成的回复")
        self.model_behavior = respond
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "blocked")
        self.assertEqual(self.sent, [])

    async def test_bad_json_keeps_previous_state_and_retry_does_not_regenerate(self):
        _, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(health="未知"), expected_revision=0, operation_key="seed")
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered("not json"))
        result = await app.state_maintenance.run(context, "原角色人格提示", "bad-json")
        self.assertNotEqual(result["state"], "succeeded")
        self.assertEqual(app.role_state.read(context)["revision"], 1)
        await app.state_maintenance.run(context, "原角色人格提示", "bad-json")
        self.assertEqual(len(self.called), 1)

    async def test_revocation_during_model_call_discards_the_new_state(self):
        _, app, context, manager = await self.ready()
        async def respond(envelope):
            self.configure(manager, enabled=False)
            return answered(encode(state_data(energy=50)))
        self.model_behavior = respond
        result = await app.state_maintenance.run(context, "原角色人格提示", "revoked")
        self.assertNotEqual(result["state"], "succeeded")
        self.assertIsNone(app.role_state.records.get(context, "current"))

    async def test_background_cannot_mutate_state_while_reply_uses_it(self):
        host, app, context, _ = await self.ready()
        async def respond(envelope):
            host.receive(self.event()).unwrap()
            return answered(encode(state_data(busy="忙碌")))
        self.model_behavior = respond
        result = await app.state_maintenance.run(context, "原角色人格提示", "foreground")
        self.assertNotEqual(result["state"], "succeeded")
        self.assertIsNone(app.role_state.records.get(context, "current"))

    async def test_due_time_survives_restart_and_requires_fresh_persona_binding(self):
        host, app, context, _ = await self.ready()
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data(mood="平静"))))
        self.advance(app, 86401)
        await self.until(lambda: app.role_state.records.get(context, "current") is not None)
        next_at = self.runtime.read_one("SELECT next_at FROM role_state_schedules", ())["next_at"]
        await app.close()
        reopened = self.host()
        other = reopened.application
        await self.start(reopened, task_generation=1, model_generation=1)
        other.state_maintenance.advance()
        self.assertEqual(len(self.called), 1)
        self.assertEqual(other.state_maintenance.bindings, {})
        other.state_maintenance.bind(context, "原角色人格提示")
        other.state_maintenance.advance()
        self.assertEqual(self.runtime.read_one("SELECT next_at FROM role_state_schedules", ())["next_at"], next_at)
        self.assertEqual(other.role_state.read(context)["revision"], 1)

    async def test_due_maintenance_waits_for_fresh_binding_then_resumes(self):
        host, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(mood="平静", energy=42), expected_revision=0,
                             operation_key="seed-before-window-close")
        self.model_behavior = lambda env: asyncio.sleep(0,
            result=answered(encode(state_data(mood="平静", energy=42))))
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (NOW.isoformat(),))
        schedule_before = self.runtime.read_one("SELECT context_key, next_at, enabled FROM role_state_schedules", ())
        await app.close()

        reopened = self.host()
        other = reopened.application
        await self.start(reopened, task_generation=1, model_generation=1)
        with self.assertLogs("companion_v2.role_work.state.maintenance", level="INFO") as captured:
            other.state_maintenance.advance()
        self.assertTrue(any("角色状态计划已保留，等待可信运行上下文" in record.getMessage()
                            for record in captured.records))
        self.assertFalse(any(record.levelname == "WARNING" for record in captured.records))
        waiting = self.runtime.read_one("SELECT result_json FROM role_state_schedules", ())
        self.assertEqual(json.loads(waiting["result_json"]),
                         {"state": "waiting", "code": "state_role_context_unavailable", "stage": "binding"})
        schedule_after = self.runtime.read_one("SELECT context_key, next_at, enabled FROM role_state_schedules", ())
        self.assertEqual(tuple(schedule_after), tuple(schedule_before))
        self.assertEqual(other.role_state.read(context)["values"]["energy"], 42)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) AS n FROM execution_tasks WHERE kind = ?",
                                               ("role.state.maintain",))["n"], 0)

        other.state_maintenance.bind(context, "原角色人格提示")
        self.assertIsNone(self.runtime.read_one("SELECT result_json FROM role_state_schedules", ())["result_json"])
        other.state_maintenance.advance()
        await self.until(lambda: (other.role_state.records.get(context, "current") or {}).get("revision") == 2)
        self.assertEqual(other.role_state.read(context)["revision"], 2)
        self.assertEqual(other.role_state.read(context)["values"]["energy"], 42)

    async def test_role_schedule_resumes_through_another_authorized_context(self):
        _, app, context, _ = await self.ready()
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data(mood="平静"))))
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (NOW.isoformat(),))

        scope_b = replace(context.scope, native_scope_id="native-b", conversation_id="conversation-b",
                          session_id="session-b")
        self.personas.register_route(ConversationRoute(scope_b, user=self.important.subject), observed_at=NOW)
        role_b = self.identity.resolve_role(scope_b, observed_at=NOW).unwrap()
        context_b = TaskContext(self.important, role_b, AudienceRef(scope_b, (self.important.subject,)))
        for capability in ("conversation.reply", "role.state.read", "role.state.maintain"):
            self.grant(self.important, scope_b, capability)

        # A live trusted role context can outlast the exact conversation that
        # originally created the role-owned schedule.
        app.state_maintenance.bindings = {context_b.key: (context_b, "原角色人格提示", None)}
        app.state_maintenance.advance()

        row = self.runtime.read_one("SELECT context_json FROM role_state_schedules", ())
        self.assertEqual(TaskContext.restore(json.loads(row["context_json"])).key, context_b.key)
        await self.until(lambda: app.role_state.records.get(context_b, "current") is not None)
        self.assertEqual(app.role_state.read(context_b)["revision"], 1)

    async def test_stale_exact_context_does_not_mask_an_authorized_role_context(self):
        _, app, context, _ = await self.ready()
        scope_b = replace(context.scope, native_scope_id="native-b", conversation_id="conversation-b",
                          session_id="session-b")
        self.personas.register_route(ConversationRoute(scope_b, user=self.important.subject), observed_at=NOW)
        role_b = self.identity.resolve_role(scope_b, observed_at=NOW).unwrap()
        context_b = TaskContext(self.important, role_b, AudienceRef(scope_b, (self.important.subject,)))
        for capability in ("conversation.reply", "role.state.read", "role.state.maintain"):
            self.grant(self.important, scope_b, capability)
        app.state_maintenance.bindings[context_b.key] = (context_b, "原角色人格提示", None)

        with patch.object(app.state_maintenance, "_can_anchor_schedule",
                          side_effect=lambda candidate: candidate.key == context_b.key):
            selected = app.state_maintenance._current_role_binding(context)

        self.assertEqual(selected[0].key, context_b.key)

    async def test_unauthorized_window_cannot_replace_the_role_schedule_anchor(self):
        _, app, context, _ = await self.ready()
        scope_b = replace(context.scope, native_scope_id="native-b", conversation_id="conversation-b",
                          session_id="session-b")
        self.personas.register_route(ConversationRoute(scope_b, user=self.standard.subject), observed_at=NOW)
        role_b = self.identity.resolve_role(scope_b, observed_at=NOW).unwrap()
        context_b = TaskContext(self.standard, role_b, AudienceRef(scope_b, (self.standard.subject,)))
        self.grant(self.standard, scope_b, "conversation.reply")

        blocked = {"state": "blocked", "code": "authorization_denied", "stage": "authorization"}
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET result_json = ?", (encode(blocked),))
        app.state_maintenance.bind(context_b, "原角色人格提示")

        row = self.runtime.read_one("SELECT context_json, result_json FROM role_state_schedules", ())
        self.assertEqual(TaskContext.restore(json.loads(row["context_json"])).key, context.key)
        self.assertEqual(json.loads(row["result_json"]), blocked)

        self.grant(self.standard, scope_b, "role.state.read")
        self.grant(self.standard, scope_b, "role.state.maintain")
        app.state_maintenance.bind(context_b, "原角色人格提示")
        row = self.runtime.read_one("SELECT context_json, result_json FROM role_state_schedules", ())
        self.assertEqual(TaskContext.restore(json.loads(row["context_json"])).key, context_b.key)
        self.assertIsNone(row["result_json"])

    async def test_preflight_task_errors_are_visible_and_retried_with_backoff(self):
        _, app, context, _ = await self.ready()
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (NOW.isoformat(),))
        denied = TaskError("state_maintenance_permission_denied", "private authorization detail")
        with patch.object(app.role_state.access, "check", side_effect=denied) as check:
            app.state_maintenance.advance()
            row = self.runtime.read_one("SELECT result_json FROM role_state_schedules", ())
            self.assertEqual(json.loads(row["result_json"]), {"state": "blocked",
                "code": "state_maintenance_permission_denied", "stage": "authorization"})
            first_checks = check.call_count
            self.assertGreater(first_checks, 0)
            app.state_maintenance.bind(context, "原角色人格提示")
            app.state_maintenance.advance()
            self.assertEqual(check.call_count, first_checks)
            self.clock.advance(timedelta(seconds=31))
            app.state_maintenance.advance()
            self.assertGreater(check.call_count, first_checks)
        self.assertEqual(self.called, [])


class StateSkillMaterialTests(StateMaintenanceTests):
    """C2-5 follow-up: confirmed skills shape the activities the role picks."""

    def skill(self, app, context, *, title="练习水彩", growth_id="watercolor", state=GrowthState.ACTIVE):
        source = self.source(app, context, "skill-" + growth_id)
        owner = app.role_growth_context.reader.owner(context)
        record = RoleGrowthRecord(
            growth_id=growth_id, owner=owner, scope=context.scope, kind=GrowthKind.SKILL,
            title=title, description="慢慢练色彩。", progress_note="能把静物画完了。",
            sources=(source,), created_at=NOW, updated_at=NOW, proposed_by=context.actor.subject)
        app.role_growth.add(record)
        record = app.role_growth.confirm(growth_id, owner=owner, scope=context.scope,
            expected_revision=1, operation_id="confirm-" + growth_id,
            actor=context.actor.subject, observed_at=NOW)
        return record

    def grant_read(self, app, context):
        """Only the read grant is needed here; changing the actor's tier would change
        the state round itself and hide what this suite is measuring."""
        return self.grant(context.actor, context.scope, "role.growth.read")


    def revoke_read(self, app, context):
        """Revoke every active read grant for this participant."""
        revoked = 0
        for grant in self.identity.snapshot().grants:
            if (grant.capability == "role.growth.read" and grant.state is GrantState.ACTIVE
                    and grant.subject == context.actor.subject):
                self.identity.revoke_grant(grant.grant_id,
                    expected_revision=self.identity.revision, revoked_at=NOW)
                revoked += 1
        return revoked

    async def test_the_scope_owner_reaches_its_own_skills_without_a_grant(self):
        # A private window's registered participant is a "verified owner" container in
        # scope_access (allow_private_owner), so the skill material does not depend on
        # an explicit role.growth.read grant. This pins that contract: the gate is the
        # participant's ownership of the window, not a delegation.
        _, app, context, _ = await self.ready()
        self.skill(app, context)
        self.grant_read(app, context)
        self.assertGreaterEqual(self.revoke_read(app, context), 1)
        _, references, _ = app.state_maintenance._skills(context, NOW)
        self.assertEqual(len(references), 1)

    async def test_a_window_without_any_skill_contributes_none(self):
        _, app, context, _ = await self.ready()
        reader = app.role_growth_context.reader
        _, references, partial = app.state_maintenance._skills(context, NOW)
        self.assertEqual(references, ())
        self.assertFalse(partial)

    def instruction_of(self, envelope):
        return envelope.original.instruction

    def growth_materials(self, envelope):
        return [item for item in envelope.original.context.materials
                if item.owner == "role.growth"]

    async def test_no_skills_means_no_skill_material_and_no_claim(self):
        _, app, context, _ = await self.ready()
        self.grant_read(app, context)
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data())))
        result = await app.state_maintenance.run(context, "原角色人格提示", "empty")
        self.assertEqual(result["state"], "succeeded", result)
        envelope = self.called[0]
        self.assertEqual(self.growth_materials(envelope), [])
        self.assertIn("没有可用的角色技能资料", self.instruction_of(envelope))

    async def test_a_paused_skill_is_still_material_but_an_unconfirmed_one_is_not(self):
        _, app, context, _ = await self.ready()
        paused = self.skill(app, context, growth_id="paused", title="暂停的技能")
        app.role_growth.transition("paused", owner=paused.owner, scope=paused.scope,
            expected_revision=paused.revision, operation_id="pause-paused",
            state=GrowthState.PAUSED, actor=context.actor.subject, observed_at=NOW)
        source = self.source(app, context, "skill-proposed")
        app.role_growth.add(RoleGrowthRecord(
            growth_id="proposed", owner=paused.owner, scope=context.scope, kind=GrowthKind.SKILL,
            title="还没确认的技能", description="等确认。", progress_note="",
            sources=(source,), created_at=NOW, updated_at=NOW, proposed_by=context.actor.subject))
        self.grant_read(app, context)
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data())))
        result = await app.state_maintenance.run(context, "原角色人格提示", "paused")
        self.assertEqual(result["state"], "succeeded", result)
        titles = [json.loads(item.payload)["title"] for item in self.growth_materials(self.called[0])]
        self.assertIn("暂停的技能", titles)
        self.assertNotIn("还没确认的技能", titles)

if __name__ == "__main__":
    unittest.main()
