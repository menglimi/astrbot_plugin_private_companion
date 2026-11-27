"""Humanised state expression: band bounds, zero means nothing, daily patrol.

The V2 role body already publishes an authorized ``simulation`` view; these tests
cover the two T35 additions -- the bounded expression dial that now reaches the
reply prompt, and the V1 daily-review cadence with its retention window -- and
assert that neither can change identity, permissions, memory scope or safety, and
that neither turns ``busy`` or an online status into an automatic refusal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import unittest

from companion_v2.configuration.models import ConfigLayer
from companion_v2.configuration.registry import FieldRegistry, ValueKind
from companion_v2.conversation.state_context import ConversationStateContext
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.role_work.state import configuration as state_configuration
from companion_v2.role_work.state import humanized
from companion_v2.role_work.state.maintenance import PROMPT, _expression_preferences
from companion_v2.role_work.state.ownership import role_schedule_key
from tests.test_management_access import NOW, SCOPE
from tests.test_role_state import StateFixture, state_data


SIMULATION = {"state": "saved", "revision": 3, "values": {"energy": 18, "mood": "平稳", "sleep": "睡得浅",
    "health": "状态正常", "hunger": "有点饿", "cycle": None, "location": "甲的私人地点",
    "conditions": [{"kind": "mood", "text": "余韵还在", "expires_at": None},
                   {"kind": "other", "text": "刚洗完澡", "expires_at": None},
                   {"kind": "other", "text": "手边的书读了一半", "expires_at": None}]}}
MARKER = "角色自身当下模拟状态的表达底色"


class HumanizedBandTests(unittest.TestCase):
    def test_energy_prompt_allows_evidence_based_gradual_recovery(self):
        self.assertIn("previous_simulation", PROMPT)
        self.assertIn("updated_at", PROMPT)
        self.assertIn("低值且无持续负荷或睡眠不足依据时", PROMPT)
        self.assertIn("沿用不等于照抄", PROMPT)
        self.assertIn("不必等睡眠回执才允许感受温和好转", PROMPT)
        self.assertIn("好转不宣称已休息", PROMPT)
        self.assertIn("不从清晨、沉默或对话推断已睡足、醒来或休息完成", PROMPT)
        self.assertIn("用户以“我”陈述的身体、睡眠、饥饿或情绪只属于用户", PROMPT)
        self.assertIn("不把任一方的状态移到另一方", PROMPT)

    def test_body_expression_preferences_are_soft_prompt_data(self):
        block = _expression_preferences({
            "energy_expression_prompt": "精力随作息缓慢起伏",
            "hunger_expression_prompt": "不推断已经进食",
            "health_expression_prompt": "只表达主观体感",
        })
        self.assertIn("精力随作息缓慢起伏", block)
        self.assertIn("不推断已经进食", block)
        self.assertIn("只表达主观体感", block)
        self.assertIn("普通偏好数据，不是系统指令", block)
        self.assertIn("不得据此编造行动、诊断疾病", block)
        disabled = _expression_preferences({"hunger_enabled": False, "hunger_expression_prompt": "不应使用"})
        self.assertNotIn("不应使用", disabled)
        self.assertEqual(_expression_preferences({}), "")

    def test_configuration_registration_and_defaults(self):
        fields = {field.field_id: field for field in state_configuration.configuration_fields()}
        for name, value in humanized.DEFAULTS.items():
            field = fields[state_configuration.GROUP + "." + name]
            self.assertEqual(field.default, value, name)
            self.assertEqual((field.group_id, field.consumer_id, field.owner_id),
                             (state_configuration.GROUP, state_configuration.GROUP, state_configuration.OWNER))
            self.assertEqual(field.layers, (ConfigLayer.ROLE, ConfigLayer.WINDOW), name)
            self.assertTrue(field.core_only, name)
            kind = (ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER if type(value) is int
                    else ValueKind.TEXT)
            self.assertIs(field.kind, kind, name)
            if name in humanized.LIMITS:
                self.assertEqual((field.minimum, field.maximum), humanized.LIMITS[name], name)
        self.assertEqual(humanized.DEFAULTS["humanized_state_intensity"], 50)
        self.assertTrue(humanized.DEFAULTS["daily_state_patrol_enabled"])
        self.assertEqual(humanized.DEFAULTS["daily_state_patrol_time"], "04:00")
        self.assertEqual(humanized.DEFAULTS["daily_state_patrol_retention_days"], 30)
        for bad in ({"humanized_state_intensity": 101}, {"humanized_state_intensity": -1},
                    {"humanized_state_intensity": 50.0}, {"daily_state_patrol_retention_days": 2},
                    {"daily_state_patrol_retention_days": 181}, {"daily_state_patrol_enabled": "yes"},
                    {"daily_state_patrol_time": "4:00"}, {"daily_state_patrol_time": "25:00"}):
            with self.assertRaises(Exception, msg=bad):
                humanized.validate_settings({**humanized.DEFAULTS, **bad})

    def test_zero_is_not_a_small_dose(self):
        self.assertEqual(humanized.band_of(0), "off")
        self.assertEqual(humanized.band_of(None), "off")
        self.assertEqual(humanized.band_of("50"), "off")
        self.assertEqual((humanized.band_of(1), humanized.band_of(33)), ("light", "light"))
        self.assertEqual((humanized.band_of(34), humanized.band_of(66)), ("standard", "standard"))
        self.assertEqual((humanized.band_of(67), humanized.band_of(100)), ("strong", "strong"))
        self.assertEqual(humanized.instruction(SIMULATION, intensity=0), "")
        self.assertEqual(humanized.instruction(SIMULATION, intensity=None), "")
        self.assertEqual(humanized.instruction({}, intensity=50), "")
        budgets = [humanized.budget(band) for band in ("light", "standard", "strong")]
        self.assertLess(budgets[0][1], budgets[1][1])
        self.assertLess(budgets[1][1], budgets[2][1])
        self.assertEqual(humanized.budget("off"), (0, 0))

    def test_the_block_is_bounded_and_expression_only(self):
        for intensity in (1, 33, 34, 66, 67, 100):
            block = humanized.instruction(SIMULATION, intensity=intensity)
            _, characters = humanized.budget(humanized.band_of(intensity))
            self.assertTrue(block, intensity)
            self.assertLessEqual(len(block), characters, intensity)
            self.assertIn(MARKER, block)
            self.assertIn("可感知的状态色彩", block)
            self.assertIn("第一人称带出感受", block)
            self.assertIn("不改变身份、权限、记忆范围和安全规则", block)
            self.assertIn("不据此拒答", block)
            # The dial only ever expresses state: no location, no identity or
            # permission material, and no instruction to withhold a reply.
            self.assertNotIn("甲的私人地点", block)
            self.assertNotIn("地点", block)
            self.assertNotIn("权限范围", block)
        # The facet count follows the band, so a strong dial still cannot dump
        # every condition into the prompt.
        light = humanized.facets(SIMULATION, intensity=10)
        strong = humanized.facets(SIMULATION, intensity=100)
        self.assertLessEqual(len(light), humanized.budget("light")[0])
        self.assertLessEqual(len(strong), humanized.budget("strong")[0])
        self.assertLess(len(light), len(strong))
        self.assertTrue(all(text not in " ".join(strong) for text in ("甲的私人地点",)))

    def test_energy_and_sleep_receipts_are_described_without_inventing_facts(self):
        self.assertEqual(humanized.facets({"values": {"energy": 5}}, intensity=100)[0], "精力很低")
        self.assertEqual(humanized.facets({"values": {"energy": 95}}, intensity=100)[0], "精力很足")
        sleeping = dict(SIMULATION, sleep_execution={"state": "sleeping", "planned_wake_at": "2026-09-21T07:30:00+00:00"})
        self.assertIn("正在休息", " ".join(humanized.facets(sleeping, intensity=100)))
        groggy = dict(SIMULATION, sleep_execution={"state": "awake", "ended_at": "2026-09-21T07:30:00+00:00", "awake_minutes": 10})
        self.assertIn("刚睡醒不久，还带着一点睡醒后的迷糊感", " ".join(humanized.facets(groggy, intensity=100)))
        self.assertTrue(humanized.is_sleepy(groggy))
        interrupted = dict(SIMULATION, sleep_execution={"state": "interrupted", "early_interruptions": 1})
        self.assertIn("刚入睡不久被唤醒", " ".join(humanized.facets(interrupted, intensity=100)))
        self.assertTrue(humanized.is_sleepy(interrupted))
        fully_awake = dict(SIMULATION, sleep_execution={"state": "awake", "ended_at": "2026-09-21T07:30:00+00:00", "awake_minutes": 60})
        self.assertNotIn("刚睡醒", " ".join(humanized.facets(fully_awake, intensity=100)))
        self.assertFalse(humanized.is_sleepy({"values": {"energy": 80}, "sleep_execution": {"state": "awake", "ended_at": "2026-09-21T07:30:00+00:00", "awake_minutes": 60}}))
        # Boundary checks for #903: only 0 <= awake_minutes <= 25 injects the freshly-awake hint.
        for mins in (0, 1, 25):
            sample = dict(SIMULATION, sleep_execution={"state": "awake", "ended_at": "2026-09-21T07:30:00+00:00", "awake_minutes": mins})
            self.assertIn("刚睡醒不久，还带着一点睡醒后的迷糊感", humanized.facets(sample, intensity=100), mins)
            self.assertTrue(humanized.is_sleepy(sample), mins)
        for mins in (-5, -1, 26, 60, 480, None, "10", True):
            sample = dict(SIMULATION, sleep_execution={"state": "awake", "ended_at": "2026-09-21T07:30:00+00:00", "awake_minutes": mins})
            self.assertNotIn("刚睡醒", " ".join(humanized.facets(sample, intensity=100)), mins)
            self.assertFalse(humanized.is_sleepy(sample), mins)
        # Without material the dial adds nothing rather than a generic claim.
        self.assertEqual(humanized.instruction({"values": {}}, intensity=100), "")

    def test_sleep_words_do_not_determine_pacing_without_a_current_receipt(self):
        for text in ("睡得很好，不困了", "睡眠充足", "没有疲劳", "睡前想看书", "昨晚有点困", "刚被吵醒"):
            with self.subTest(text=text):
                self.assertFalse(humanized.is_sleepy({"values": {"sleep": text,
                    "conditions": [{"kind": "sleep", "text": text}]}}))
        self.assertFalse(humanized.is_sleepy({"sleep_execution": {
            "state": "awake", "ended_at": "2026-09-21T07:30:00+00:00"}}))

    def test_patrol_time_contract_and_retention_pruning(self):
        self.assertEqual(humanized.parse_patrol_time("04:00").hour, 4)
        for bad in ("4:00", "04:00:00", "24:00", "", None, "04:60"):
            with self.assertRaises(Exception, msg=bad):
                humanized.parse_patrol_time(bad)
        today = date(2026, 9, 21)
        reports = [{"day": (today - timedelta(days=offset)).isoformat(), "at": "x"} for offset in range(40)]
        kept = humanized.prune_reports(reports, today=today, retention_days=3)
        self.assertEqual([item["day"] for item in kept], [(today - timedelta(days=offset)).isoformat()
                                                          for offset in (3, 2, 1, 0)])
        self.assertEqual(humanized.prune_reports([{"day": "not-a-day"}, {"nope": 1}, "x"], today=today,
                                                 retention_days=30), ())
        self.assertEqual(len(humanized.prune_reports(
            [{"day": today.isoformat()} for _ in range(humanized.MAX_REPORTS + 10)],
            today=today, retention_days=30)), humanized.MAX_REPORTS)


class HumanizedStateHostTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    def configure(self, manager, **changes):
        result = super().configure(manager, **changes)
        next_at = (self.clock.now() + timedelta(days=1)).isoformat()
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ? WHERE context_key = ?",
                               (next_at, role_schedule_key(manager.context)))
        return result

    async def ready(self, **changes):
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, **changes)
        app.state_maintenance.bind(context, "原角色人格提示")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", ((NOW + timedelta(days=1)).isoformat(),))
        await self.start(host)
        return host, app, context, manager

    def consumer(self, app):
        return ConversationStateContext(app.role_state)

    def replies(self):
        """Foreground replies only: a maintenance round is a background trigger."""
        from companion_v2.conversation.foreground import BackgroundPromptTrigger
        return [envelope for envelope in self.called
                if not isinstance(envelope.original.event, BackgroundPromptTrigger)]

    async def test_the_dial_is_sealed_with_the_state_and_zero_injects_nothing(self):
        _, app, context, manager = await self.ready()
        app.role_state.write(context, data=state_data(energy=18, mood="平稳", hunger="有点饿", busy="忙碌",
                                                     location="甲的私人地点"),
                             expected_revision=0, operation_key="seed")
        consumer = self.consumer(app)
        captured = consumer.capture(context, self.clock.now())
        self.assertEqual(captured["humanized_intensity"], 50)
        materials = consumer.contributions(captured, context, self.clock.now())
        dial = [item for item in materials if item.purpose == "role_state_intensity"]
        self.assertEqual(len(dial), 1)
        self.assertIn(MARKER, consumer.instruction(materials))
        # The sealed dial travels with the state, so a replay is byte-identical.
        self.assertEqual(consumer.contributions(captured, context, self.clock.now(), historical=True), materials)
        self.configure(manager, humanized_state_intensity=0)
        captured = consumer.capture(context, self.clock.now())
        self.assertEqual(captured["humanized_intensity"], 0)
        self.assertNotIn(MARKER, consumer.instruction(consumer.contributions(captured, context, self.clock.now())))
        self.assertEqual(humanized.instruction(SIMULATION, intensity=captured["humanized_intensity"]), "")

    async def test_an_unusable_dial_in_a_sealed_snapshot_is_refused_not_defaulted(self):
        _, app, context, _ = await self.ready()
        app.role_state.write(context, data=state_data(energy=30), expected_revision=0, operation_key="seed")
        consumer = self.consumer(app)
        captured = consumer.capture(context, self.clock.now())
        self.assertEqual([item.purpose for item in consumer.contributions(captured, context, self.clock.now())],
                         ["role_state", "role_state_intensity"])
        for bad in (500, -1, "high", 50.5):
            with self.assertRaises(TaskError, msg=bad):
                consumer.contributions({**captured, "humanized_intensity": bad}, context, self.clock.now())
        # A batch sealed before the dial existed simply injects no expression.
        legacy = {key: value for key, value in captured.items() if key != "humanized_intensity"}
        materials = consumer.contributions(legacy, context, self.clock.now())
        self.assertEqual([item.payload for item in materials if item.purpose == "role_state_intensity"],
                         ['{"intensity":null}'])
        self.assertNotIn(MARKER, consumer.instruction(materials))

    async def test_a_real_reply_carries_the_bounded_block_and_still_replies_while_busy(self):
        host, app, context, manager = await self.ready(humanized_state_intensity=80)
        app.role_state.write(context, data=state_data(energy=15, mood="平稳", busy="手头有事",
                                                     location="甲的私人地点"),
                             expected_revision=0, operation_key="seed")
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        # A loaded machine can exceed the fixture's three-second default.
        self.assertEqual((await self.done(app, batch, seconds=15))["state"], "complete")
        instruction = self.replies()[0].original.instruction
        self.assertIn(MARKER, instruction)
        self.assertIn("精力很低", instruction)
        self.assertIn("不据此拒答", instruction)
        self.assertNotIn("甲的私人地点", instruction)
        self.assertEqual(len(self.sent), 2)  # busy never became an automatic refusal
        self.configure(manager, humanized_state_intensity=0)
        second = host.receive(self.event("quiet")).unwrap()
        self.advance(app)
        self.assertEqual((await self.done(app, second, seconds=15))["state"], "complete")
        self.assertNotIn(MARKER, self.replies()[-1].original.instruction)
        self.assertEqual(len(self.replies()), 2)

    async def test_role_simulation_is_injected_in_another_private_window_without_a_state_grant(self):
        host, app, context, _ = await self.ready(humanized_state_intensity=80)
        app.role_state.write(context, data=state_data(energy=15, mood="平稳"),
                             expected_revision=0, operation_key="seed")
        other_scope = replace(context.scope, native_scope_id="second-private",
                              conversation_id="conversation-second", session_id="session-second")
        self.personas.register_route(ConversationRoute(other_scope, user=context.actor.subject),
                                     observed_at=self.clock.now())
        self.grant(context.actor, other_scope, "conversation.reply")
        self.grant(context.actor, other_scope, "text")
        self.assertFalse(self.identity.authorize(context.actor, scope=other_scope,
            capability="role.state.read", observed_at=self.clock.now()).is_success)
        self.assertFalse(self.identity.authorize(context.actor, scope=other_scope,
            capability="role.state.maintain", observed_at=self.clock.now()).is_success)

        batch = host.receive(self.event("other-private-window", scope=other_scope,
                                        actor=context.actor)).unwrap()
        self.advance(app)
        result = await self.done(app, batch, seconds=15)
        self.assertEqual(result["state"], "complete", dict(result))
        instruction = self.replies()[-1].original.instruction
        self.assertIn(MARKER, instruction)
        self.assertIn("精力很低", instruction)
        self.assertEqual(len(self.sent), 2)

    async def test_the_daily_patrol_records_once_per_local_day_and_prunes_by_retention(self):
        _, app, context, manager = await self.ready(daily_state_patrol_time="23:59")
        self.assertIsNone(humanized.document(app.runtime, context)["day"])
        app.state_maintenance._patrol_checked_at = None
        app.state_maintenance.advance()
        self.assertIsNone(humanized.document(app.runtime, context)["day"])  # not due before its local time
        self.configure(manager, daily_state_patrol_time="04:00")
        revision = app.role_state.read(context)["revision"]
        app.state_maintenance._patrol_checked_at = None
        app.state_maintenance.advance()
        recorded = humanized.document(app.runtime, context)
        self.assertEqual(recorded["day"], "2026-09-21")  # 00:00 UTC is 08:00 in Asia/Shanghai
        self.assertEqual(len(recorded["reports"]), 1)
        self.assertEqual(recorded["reports"][0]["revision"], revision)
        self.assertEqual(app.state_maintenance.patrol_results[context.key]["state"], "recorded")
        self.assertEqual(app.role_state.read(context)["revision"], revision)  # the patrol never rewrites state
        # One record per local day, and the retention window drops the rest.
        self.clock.advance(timedelta(seconds=120))
        app.state_maintenance.advance()
        self.assertEqual(len(humanized.document(app.runtime, context)["reports"]), 1)
        self.configure(manager, daily_state_patrol_retention_days=3)
        values, _ = app.role_state.settings(context)
        later = NOW + timedelta(days=5)
        result = humanized.patrol(app.role_state, context, settings=values, now=later)
        self.assertEqual((result["state"], result["pruned"], result["kept"]), ("recorded", 1, 1))
        reports = humanized.document(app.runtime, context)["reports"]
        self.assertEqual([item["day"] for item in reports], [later.date().isoformat()])
        self.assertEqual(reports[0]["retention_days"], 3)

    async def test_the_patrol_needs_its_own_grant_and_never_blocks_maintenance(self):
        _, app, context, _ = await self.ready()
        grant = self.runtime.read_one(
            "SELECT grant_id FROM identity_grants WHERE capability = 'role.state.read'", ())["grant_id"]
        self.identity.revoke_grant(grant, expected_revision=self.identity.revision, revoked_at=self.clock.now())
        app.state_maintenance._patrol_checked_at = None
        app.state_maintenance.advance()
        blocked = app.state_maintenance.patrol_results[context.key]
        self.assertEqual(blocked["state"], "blocked")  # fail closed without its own grant
        self.assertTrue(blocked["code"], blocked)
        self.assertIsNone(humanized.document(app.runtime, context)["day"])

    async def test_a_disabled_patrol_leaves_no_records(self):
        _, app, context, _ = await self.ready(daily_state_patrol_enabled=False)
        values, _ = app.role_state.settings(context)
        self.assertFalse(humanized.patrol_due(app.runtime, context, settings=values, now=NOW))
        app.state_maintenance._patrol_checked_at = None
        app.state_maintenance.advance()
        self.assertEqual(app.state_maintenance.patrol_results, {})
        self.assertIsNone(humanized.document(app.runtime, context)["day"])


if __name__ == "__main__":
    unittest.main()
