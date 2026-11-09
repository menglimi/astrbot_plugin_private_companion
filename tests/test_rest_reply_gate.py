"""Rest reply gate: probability decision, no re-judgement, wake-after backlog.

V1 stopped a passive reply inside the event task while the character slept. V2
expresses the same intent as a durable ``due_at`` deferral on the accepted batch,
so these tests assert the durable column, the bounded wait and the recorded
backlog -- never a sleep. The sleepy signal is the role's own active sleep
receipt, so no state field, QQ presence or online status can hold a reply.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from companion_v2.configuration.models import ConfigLayer
from companion_v2.configuration.registry import FieldRegistry, ValueKind
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskContext
from companion_v2.foundation.refs import AudienceRef
from companion_v2.role_work.life import configuration as life_configuration
from companion_v2.role_work.life import rest_gate
from companion_v2.role_work.life.configuration import LifeConfigurationOwner
from companion_v2.role_work.life.rest_gate import Settings
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


QUIET = 0.6
TURN_DEADLINE = 180
NAP_MINUTES = 5
# Deliberately neutral: "在吗"/"睡了吗" are V1 soft wake-up requests (score 72),
# which V1 already let through at the default threshold of 65.
NEUTRAL = "今天买的那本书很好看"


def gate_settings(**changes):
    return Settings.from_values({**rest_gate.DEFAULTS, **changes})


class RestGateJudgementTests(unittest.TestCase):
    """The pure judgement: no host, no storage, no timer."""

    def test_configuration_group_contract_and_defaults(self):
        fields = {field.field_id: field for field in life_configuration.configuration_fields()}
        self.assertEqual(len(fields), len(life_configuration.DEFAULTS))
        for name, value in rest_gate.DEFAULTS.items():
            field = fields[life_configuration.GROUP + "." + name]
            self.assertEqual(field.default, value, name)
            self.assertEqual((field.group_id, field.consumer_id, field.owner_id),
                             (life_configuration.GROUP, life_configuration.GROUP, life_configuration.OWNER))
            self.assertEqual(field.layers, (ConfigLayer.WINDOW, ConfigLayer.ROLE))
            kind = (ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER if type(value) is int
                    else ValueKind.TEXT)
            self.assertIs(field.kind, kind, name)
            if name in rest_gate.LIMITS:
                self.assertEqual((field.minimum, field.maximum), rest_gate.LIMITS[name], name)
            else:
                self.assertIsNone(field.minimum, name)
        # The V1 defaults are the registered defaults, verbatim.
        self.assertFalse(gate_settings().enable_rest_reply_simulation)
        self.assertEqual(gate_settings().rest_reply_mode, "probability")
        self.assertEqual(gate_settings().rest_reply_probability, 18)
        self.assertEqual(gate_settings().rest_reply_llm_threshold, 65)
        self.assertEqual(gate_settings().rest_reply_awake_grace_minutes, 30)
        self.assertTrue(gate_settings().enable_rest_backlog_reply)
        self.assertEqual(gate_settings().rest_backlog_max_messages, 4)
        for bad in ({"rest_reply_probability": 101}, {"rest_backlog_max_messages": 0},
                    {"rest_reply_awake_grace_minutes": 241}, {"enable_rest_reply_simulation": "yes"},
                    {"rest_reply_active_windows": "23:00"}):
            with self.assertRaises(Exception, msg=bad):
                gate_settings(**bad)
        # V1 accepted the model-mode aliases and coerced unknown text (the write
        # path is the one that refuses an unknown mode).
        self.assertEqual(gate_settings(rest_reply_mode="模型").rest_reply_mode, "llm")
        self.assertEqual(gate_settings(rest_reply_mode="whatever").rest_reply_mode, "probability")
        with self.assertRaises(Exception):
            rest_gate.validate_changes({"role.life.rest_reply_mode": "sometimes"})

    def test_windows_follow_the_v1_parser_including_midnight(self):
        windows = rest_gate.parse_windows("23:00-08:30,12:20-13:40")
        self.assertEqual(windows, ((1380, 1950), (740, 820)))
        self.assertEqual(rest_gate.parse_windows("23：00～08:30；12:20到13:40"), windows)
        # The V1 default window really covers 23:30 and 07:00, and not mid-afternoon.
        self.assertTrue(rest_gate.window_active(windows, minutes=23 * 60 + 30))
        self.assertTrue(rest_gate.window_active(windows, minutes=7 * 60))
        self.assertTrue(rest_gate.window_active(windows, minutes=12 * 60 + 30))
        self.assertFalse(rest_gate.window_active(windows, minutes=15 * 60))
        # An empty setting means "follow the schedule all day", as in V1.
        self.assertEqual(rest_gate.parse_windows(""), ())
        self.assertTrue(rest_gate.window_active((), minutes=15 * 60))
        self.assertEqual(rest_gate.local_minutes(NOW, "Asia/Shanghai"), 8 * 60)
        self.assertEqual(rest_gate.local_minutes(NOW, "not/a-zone"), 8 * 60)  # falls back, never raises

    def test_boundary_score_migrates_the_v1_intent(self):
        self.assertEqual(rest_gate.boundary_score("别回我")[0], -100)
        self.assertEqual(rest_gate.boundary_score("你先别理我了")[0], -100)
        self.assertEqual(rest_gate.boundary_score("安静点")[0], -100)
        self.assertEqual(rest_gate.boundary_score("快醒醒，回我一下")[0], 100)
        self.assertEqual(rest_gate.boundary_score("你明明没睡")[0], 100)
        self.assertEqual(rest_gate.boundary_score("晚安好梦")[0], 100)
        self.assertEqual(rest_gate.boundary_score("救命，我很难受")[0], 100)
        self.assertEqual(rest_gate.boundary_score("睡了吗"), (72, "soft_wakeup_request"))
        self.assertEqual(rest_gate.boundary_score("今天天气不错"), (0, "normal"))
        self.assertEqual(rest_gate.boundary_score(""), (0, "empty"))
        # A request to keep quiet about proactive contact is not a request to stay
        # silent about this message (V1 distinguished the two).
        self.assertEqual(rest_gate.boundary_score("别主动找我")[0], 0)

    def test_the_decision_needs_an_actual_sleep_receipt(self):
        settings = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=100)
        sleeping = {"resting": True, "reason": "sleep_window", "sleep": "sleep-1", "until_seconds": 600.0}
        held = rest_gate.judgement(settings, now=NOW, sleep={"resting": False, "reason": "not_sleeping"},
                                   text=NEUTRAL)
        self.assertEqual((held["action"], held["reason"], held["defer"]), ("reply", "not_sleeping", 0.0))
        # The switch is off by default and then nothing is ever held.
        off = rest_gate.judgement(gate_settings(), now=NOW, sleep=sleeping, text=NEUTRAL)
        self.assertEqual((off["action"], off["reason"]), ("reply", "disabled"))
        hit = rest_gate.judgement(settings, now=NOW, sleep=sleeping, text=NEUTRAL)
        self.assertEqual((hit["resting"], hit["action"], hit["reason"]), (True, "reply", "probability_hit"))
        self.assertEqual(hit["defer"], 0.0)

    def test_probability_miss_and_boundaries_produce_a_bounded_hold(self):
        settings = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=0)
        sleeping = {"resting": True, "reason": "sleep_window", "sleep": "sleep-1", "until_seconds": 600.0}
        miss = rest_gate.judgement(settings, now=NOW, sleep=sleeping, text=NEUTRAL)
        self.assertEqual((miss["action"], miss["reason"]), ("hold", "probability_miss"))
        self.assertEqual(miss["defer"], 600.0)
        # A long sleep still waits inside the flow's own deferral ceiling.
        far = rest_gate.judgement(settings, now=NOW,
                                  sleep={"resting": True, "sleep": "s", "until_seconds": 8 * 3600.0}, text=NEUTRAL)
        self.assertEqual(far["defer"], float(rest_gate.MAX_DEFER_SECONDS))
        unknown = rest_gate.judgement(settings, now=NOW,
                                      sleep={"resting": True, "sleep": "s", "until_seconds": None}, text=NEUTRAL)
        self.assertEqual(unknown["defer"], float(rest_gate.MAX_DEFER_SECONDS))
        self.assertGreaterEqual(rest_gate.MAX_DEFER_SECONDS, 1)
        # An explicit wake-up request is worth waking for even at zero probability.
        for text in ("快醒醒", "别回我", "安静点"):
            decided = rest_gate.judgement(settings, now=NOW, sleep=sleeping, text=text)
            expected = "reply" if rest_gate.boundary_score(text)[0] > 0 else "hold"
            self.assertEqual(decided["action"], expected, text)
        # Below the threshold the soft request falls through to the draw.
        strict = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=0,
                               rest_reply_llm_threshold=90)
        self.assertEqual(rest_gate.judgement(strict, now=NOW, sleep=sleeping, text="睡了吗")["action"], "hold")

    def test_zero_threshold_disables_the_score_shortcut(self):
        sleeping = {"resting": True, "reason": "sleep_window", "sleep": "sleep-1", "until_seconds": 600.0}
        closed = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=0,
                               rest_reply_llm_threshold=0)
        for text in ("快醒醒", "在吗", "睡了吗", NEUTRAL):
            decided = rest_gate.judgement(closed, now=NOW, sleep=sleeping, text=text)
            self.assertEqual((decided["action"], decided["reason"]),
                             ("hold", "probability_miss"), text)
        open_draw = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=100,
                                  rest_reply_llm_threshold=0)
        self.assertEqual(rest_gate.judgement(open_draw, now=NOW, sleep=sleeping,
                                             text="在吗")["action"], "reply")

    def test_model_judgement_is_off_by_default_and_unknown_never_replies(self):
        sleeping = {"resting": True, "reason": "sleep_window", "sleep": "sleep-1", "until_seconds": 600.0}
        self.assertEqual(rest_gate.DEFAULTS["rest_reply_mode"], "probability")
        llm = gate_settings(enable_rest_reply_simulation=True, rest_reply_mode="llm")
        # No judge injected, a raising judge and an unusable answer all keep sleeping.
        for judge in (None, lambda **kwargs: 1 / 0, lambda **kwargs: ("90", "no"), lambda **kwargs: (500, "no")):
            decided = rest_gate.judgement(llm, now=NOW, sleep=sleeping, text=NEUTRAL, judge=judge)
            self.assertEqual((decided["action"], decided["reason"]), ("hold", "judge_unavailable"), judge)
        low = rest_gate.judgement(llm, now=NOW, sleep=sleeping, text=NEUTRAL, judge=lambda **kw: (30, "keep"))
        self.assertEqual((low["action"], low["reason"], low["score"]), ("hold", "judge_keeps_sleeping", 30))
        high = rest_gate.judgement(llm, now=NOW, sleep=sleeping, text=NEUTRAL, judge=lambda **kw: (90, "wake"))
        self.assertEqual((high["action"], high["reason"]), ("reply", "judge_wake"))

    def test_the_awake_grace_skips_a_second_judgement_for_the_same_sleep(self):
        settings = gate_settings(enable_rest_reply_simulation=True, rest_reply_probability=0)
        sleeping = {"resting": True, "reason": "sleep_window", "sleep": "sleep-1", "until_seconds": 600.0}
        grace = {"active": True, "sleep": "sleep-1", "until": NOW + timedelta(minutes=30)}
        allowed = rest_gate.judgement(settings, now=NOW, sleep=sleeping, text=NEUTRAL, grace=grace)
        self.assertEqual((allowed["action"], allowed["reason"], allowed["defer"]), ("reply", "awake_grace", 0.0))
        # A different (new) sleep is judged again: the grace belongs to one receipt.
        other = rest_gate.judgement(settings, now=NOW, sleep={**sleeping, "sleep": "sleep-2"}, text=NEUTRAL,
                                    grace=grace)
        self.assertEqual(other["action"], "hold")

    def test_the_awake_grace_record_expires_and_honours_zero_minutes(self):
        settings = gate_settings(enable_rest_reply_simulation=True, rest_reply_awake_grace_minutes=30)
        marked = rest_gate.mark_awake(rest_gate.empty_document(), now=NOW, settings=settings, sleep="sleep-1")
        self.assertEqual(rest_gate.awake_grace(marked, now=NOW)["active"], True)
        self.assertEqual(rest_gate.awake_grace(marked, now=NOW + timedelta(minutes=31))["active"], False)
        self.assertEqual(rest_gate.awake_grace(rest_gate.empty_document(), now=NOW)["active"], False)
        zero = rest_gate.mark_awake(rest_gate.empty_document(), now=NOW,
                                    settings=gate_settings(rest_reply_awake_grace_minutes=0), sleep="sleep-1")
        self.assertIsNone(zero["awake_until"])
        damaged = {"awake_until": "not-a-time", "awake_sleep": "s", "backlog": []}
        self.assertEqual(rest_gate.awake_grace(damaged, now=NOW)["active"], False)


class RestGateOwnerTests(HostFixture, unittest.TestCase):
    """The registered owner refuses rest values the reader would have to coerce."""

    def setUp(self):
        super().setUp()
        self.service = ConfigurationService(self.runtime,
            FieldRegistry(version="rest-gate-owner.v1", fields=life_configuration.configuration_fields()),
            owners=(LifeConfigurationOwner(self.runtime),))
        self.target = life_configuration.target_for(self.window_context())

    def window_context(self):
        return TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                           AudienceRef(SCOPE, (self.important.subject,)))

    def patch(self, changes):
        snapshot = self.service.read(self.important, target=self.target, group_id=life_configuration.GROUP,
                                     observed_at=NOW).unwrap()
        return self.service.patch(self.important, target=self.target, group_id=life_configuration.GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={life_configuration.GROUP + "." + key: value for key, value in changes.items()},
            operation=self.operation(SCOPE, owner=life_configuration.OWNER), observed_at=NOW)

    def values(self):
        return self.service.read(self.important, target=self.target, group_id=life_configuration.GROUP,
                                 observed_at=NOW).unwrap().values

    def test_owner_registers_the_group_and_rejects_an_unusable_rest_write(self):
        prefix = life_configuration.GROUP + "."
        read = rest_gate.read_settings(self.runtime, self.window_context())
        self.assertFalse(read.enable_rest_reply_simulation)
        self.assertEqual(read.rest_reply_mode, "probability")
        for bad in ({"rest_reply_mode": "sometimes"}, {"rest_backlog_max_messages": 0},
                    {"rest_reply_active_windows": "23:00 08:30"}):
            self.assertFalse(self.patch(bad).is_success, bad)
            self.assertEqual(self.values()[prefix + "rest_reply_mode"], rest_gate.DEFAULTS["rest_reply_mode"])
        self.assertTrue(self.patch({"enable_rest_reply_simulation": True, "rest_reply_mode": "llm",
                                    "rest_reply_active_windows": "22:00-07:00"}).is_success)
        self.assertTrue(self.values()[prefix + "enable_rest_reply_simulation"])
        read = rest_gate.read_settings(self.runtime, self.window_context())
        self.assertEqual((read.rest_reply_mode, read.rest_reply_active_windows), ("llm", "22:00-07:00"))


class RestGateHostTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.calls = 0

    # -- helpers -------------------------------------------------------

    def rest(self, app, context, changes):
        owner = LifeConfigurationOwner(app.runtime)
        target = life_configuration.target_for(context)
        fields = life_configuration.configuration_fields()
        snapshot = owner.read(target, fields)
        return owner.write(target, fields,
                           changes={life_configuration.GROUP + "." + key: value for key, value in changes.items()},
                           reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    def sleep_for(self, app, context, *, minutes=NAP_MINUTES, request="nap"):
        self.grant(context.actor, context.scope, "role.life.run")
        self.grant(context.actor, context.scope, "role.life.read")
        routine = app.life_runtime.bind(context, "原角色人格提示")
        return routine.start_sleep(request, minutes)

    def source(self, app, context, identifier, body):
        from companion_v2.execution.models import digest
        from companion_v2.foundation.refs import SourceRef, VersionRef
        from companion_v2.sources.models import SourceRecord
        reference = SourceRef(identifier, "conversation.host_input", context.scope,
                              VersionRef(identifier, 1), self.clock.now())
        app.messages.sources.publish(SourceRecord(reference, AudienceRef(context.scope, (context.actor.subject,)),
            digest(body), body), observed_at=self.clock.now()).unwrap()
        return reference

    def accept(self, app, context, *, defer, identifier, text=NEUTRAL, reference=None):
        from companion_v2.conversation.models import InboundConversationEvent
        from companion_v2.execution.models import digest
        reference = reference or self.source(app, context, identifier, text)
        event = InboundConversationEvent(identifier, context.actor.principal, context.scope, reference,
                                        "text", digest(text), self.clock.now())
        return app.flow.accept(context, event, role_instruction="原角色人格提示",
                               defer_seconds=defer).unwrap(), reference

    def times(self, app, batch):
        row = app.flow.row(batch)
        return (datetime.fromisoformat(row["due_at"]), datetime.fromisoformat(row["deadline_at"]))

    async def ready(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        first = host.receive(self.event()).unwrap()
        context = self.context(app, first)
        self.advance(app, 1)
        await self.done(app, first, seconds=10)
        self.rest(app, context, {"enable_rest_reply_simulation": True, "rest_reply_probability": 0,
                                 "rest_reply_active_windows": ""})
        return host, app, context

    # -- the deferral is durable and the deadline stays safe -------------

    async def test_a_sleep_window_moves_due_at_and_never_ends_in_deadline_elapsed(self):
        host, app, context = await self.ready()
        self.sleep_for(app, context)
        before = len(self.called)
        text = NEUTRAL
        reference = self.source(app, context, "slept-1", text)
        delay = rest_gate.deferral(app.runtime, context, text=text, now=self.clock.now(),
                                   event_key="evt-1", source=reference, capability="text")
        # A five-minute nap stays inside the flow's own deferral ceiling.
        self.assertEqual(delay, NAP_MINUTES * 60.0)
        batch, _ = self.accept(app, context, defer=delay, identifier="slept-1", text=text, reference=reference)
        due, deadline = self.times(app, batch)
        self.assertEqual(due, self.clock.now() + timedelta(seconds=QUIET + delay))
        self.assertGreater(deadline - due, timedelta(seconds=QUIET))
        self.assertLessEqual(deadline, self.clock.now() + timedelta(seconds=TURN_DEADLINE + delay))
        self.assertLess(due, deadline)
        self.assertEqual(len(self.called), before)
        self.advance(app, 30)
        self.assertEqual(len(self.called), before)
        self.advance(app, (due - self.clock.now()).total_seconds() + 1)
        sealed = await self.done(app, batch, seconds=10)
        self.assertEqual(sealed["state"], "complete", dict(sealed))
        self.assertNotEqual(sealed["reason"], "deadline_elapsed")
        self.assertEqual(len(self.called), before + 1)
        self.assertEqual([material.payload for material in self.called[-1].original.context.materials
                          if material.owner == "conversation.host_input"], [text])

    async def test_one_inbound_is_judged_once_and_never_redrawn(self):
        _, app, context = await self.ready()
        self.sleep_for(app, context)
        reference = self.source(app, context, "slept-2", NEUTRAL)
        draws = {rest_gate.deferral(app.runtime, context, text=NEUTRAL, now=self.clock.now(),
                                    event_key="evt-2", source=reference, capability="text") for _ in range(25)}
        self.assertEqual(draws, {NAP_MINUTES * 60.0})
        self.assertEqual(len(rest_gate.pending(app.runtime, context)), 1)  # recorded exactly once
        # A different inbound is a new judgement, not a replay of the first one.
        second = self.source(app, context, "slept-3", "书读了一半")
        rest_gate.deferral(app.runtime, context, text="书读了一半", now=self.clock.now(),
                           event_key="evt-3", source=second, capability="text")
        self.assertEqual(len(rest_gate.pending(app.runtime, context)), 2)

    async def test_a_waking_reply_is_not_judged_again_within_the_grace(self):
        _, app, context = await self.ready()
        self.rest(app, context, {"rest_reply_probability": 100, "rest_reply_awake_grace_minutes": 30})
        self.sleep_for(app, context)
        first = self.source(app, context, "wake-1", "醒醒")
        self.assertEqual(rest_gate.deferral(app.runtime, context, text="醒醒", now=self.clock.now(),
                                            event_key="evt-4", source=first, capability="text"), 0.0)
        self.assertEqual(rest_gate.pending(app.runtime, context), ())
        # The next message is not re-judged: the grace keeps the reply immediate.
        document = rest_gate.document(app.runtime, context)
        self.assertEqual(document["awake_sleep"], rest_gate.sleep_context(
            app.runtime, context, now=self.clock.now())["sleep"])
        self.assertTrue(rest_gate.awake_grace(document, now=self.clock.now())["active"])
        self.assertEqual(rest_gate.deferral(app.runtime, context, text="在吗", now=self.clock.now()), 0.0)

    async def test_real_wake_interrupts_once_then_resumes_the_remaining_sleep(self):
        _, app, context = await self.ready()
        sleep = self.sleep_for(app, context, minutes=30)
        self.clock.advance(timedelta(minutes=5))
        body_clock = app.role_state.body_clock
        first = self.source(app, context, "wake-real-1", "醒醒")
        delay = rest_gate.deferral(app.runtime, context, text="醒醒", now=self.clock.now(),
            event_key="wake-real-1", source=first, capability="text", body_clock=body_clock)
        self.assertEqual(delay, 0.0)
        interrupted = app.role_life.activity(sleep.activity_id, owner=sleep.owner)
        self.assertEqual(interrupted.state.value, "paused")
        self.assertEqual(interrupted.sleep.slept_seconds, 5 * 60)
        self.assertEqual((interrupted.sleep.interruptions, interrupted.sleep.early_interruptions), (1, 1))

        self.clock.advance(timedelta(minutes=1))
        second = self.source(app, context, "wake-real-2", "快醒醒")
        rest_gate.deferral(app.runtime, context, text="快醒醒", now=self.clock.now(),
            event_key="wake-real-2", source=second, capability="text", body_clock=body_clock)
        still_interrupted = app.role_life.activity(sleep.activity_id, owner=sleep.owner)
        self.assertEqual(still_interrupted.sleep.interruptions, 1)

        self.clock.advance(still_interrupted.sleep.wake_until - self.clock.now())
        resumed = body_clock.resume_due(context, observed_at=self.clock.now())[0]
        self.assertEqual(resumed.state.value, "active")
        self.assertEqual(resumed.sleep.slept_seconds, 5 * 60)
        self.assertEqual(resumed.work.due_at, self.clock.now() + timedelta(minutes=25))

        self.clock.advance(timedelta(minutes=10))
        third = self.source(app, context, "wake-real-3", "起来啦")
        rest_gate.deferral(app.runtime, context, text="起来啦", now=self.clock.now(),
            event_key="wake-real-3", source=third, capability="text", body_clock=body_clock)
        interrupted_again = app.role_life.activity(sleep.activity_id, owner=sleep.owner)
        self.assertEqual(interrupted_again.sleep.interruptions, 2)

    async def test_the_backlog_is_bounded_ordered_and_only_released_after_waking(self):
        _, app, context = await self.ready()
        sleep = self.sleep_for(app, context)
        settings = rest_gate.read_settings(app.runtime, context)
        limit = settings.max_backlog()
        references = []
        for index in range(limit + 2):
            reference = self.source(app, context, "backlog-" + str(index), "第 " + str(index) + " 条")
            references.append(reference)
            rest_gate.deferral(app.runtime, context, text="第 " + str(index) + " 条", now=self.clock.now(),
                               event_key="b-" + str(index), source=reference, capability="text")
        entries = rest_gate.pending(app.runtime, context)
        self.assertEqual(len(entries), limit)  # the newest N, as V1 kept them
        self.assertEqual([entry["source"]["source_id"] for entry in entries],
                         [reference.source_id for reference in references[-limit:]])
        self.assertEqual([entry["capability"] for entry in entries], ["text"] * limit)
        self.assertTrue(all(entry["reason"] and entry["at"] for entry in entries))
        # While the role is still asleep the backlog is never surfaced.
        self.assertEqual(rest_gate.consume(app.runtime, context, now=self.clock.now()), ())
        self.assertEqual(len(rest_gate.pending(app.runtime, context)), limit)
        # Only the actual wake releases it, oldest first, and takes it once.
        woke = sleep.work.due_at + timedelta(seconds=1)
        released = rest_gate.consume(app.runtime, context, now=woke)
        self.assertEqual(len(released), limit)
        self.assertEqual(rest_gate.consume(app.runtime, context, now=woke), ())
        prompt = rest_gate.backlog_prompt(released, ["第一条", None], timezone="Asia/Shanghai", max_messages=limit)
        self.assertIn("休息时有几条私聊没来得及回", prompt)
        self.assertIn("第一条", prompt)
        self.assertIn("发来了一条消息", prompt)  # an unreadable original is never invented
        self.assertNotIn("第 0 条", prompt)
        self.assertEqual(rest_gate.backlog_prompt((), (), max_messages=limit), "")

    async def test_an_unavailable_judgement_holds_the_reply_across_a_restart(self):
        _, app, context = await self.ready()
        self.rest(app, context, {"rest_reply_mode": "llm", "rest_reply_probability": 100})
        self.sleep_for(app, context)
        reference = self.source(app, context, "llm-1", NEUTRAL)
        delay = rest_gate.deferral(app.runtime, context, text=NEUTRAL, now=self.clock.now(),
                                   event_key="evt-5", source=reference, capability="text")
        self.assertEqual(delay, NAP_MINUTES * 60.0)  # unknown judgement never replies
        saved = rest_gate.document(app.runtime, context)
        self.assertEqual(saved["last"]["reason"], "judge_unavailable")
        await app.close()
        restored = rest_gate.document(self.open_runtime(), context)
        self.assertEqual(restored["last"], saved["last"])
        self.assertEqual(len(restored["backlog"]), 1)

    async def test_a_disabled_gate_or_an_awake_role_never_delays(self):
        host, app, context = await self.ready()
        self.rest(app, context, {"enable_rest_reply_simulation": False})
        self.sleep_for(app, context)
        self.assertEqual(rest_gate.deferral(app.runtime, context, text="在吗", now=self.clock.now()), 0.0)
        # Enabled, but the role is awake: the actual receipt decides, not a setting.
        self.rest(app, context, {"enable_rest_reply_simulation": True, "rest_reply_probability": 0})
        routine = app.life_runtime.routines[context.key]
        self.advance(app, 5)  # a sleep must have started strictly before it can wake
        sleep = app.role_state.body_clock.active(context, "sleep")[0]
        routine.wake(sleep.activity_id, expected_revision=sleep.revision, operation_id="wake-receipt")
        self.assertFalse(rest_gate.sleep_context(app.runtime, context, now=self.clock.now())["resting"])
        batch, _ = self.accept(app, context, defer=rest_gate.deferral(
            app.runtime, context, text="在吗", now=self.clock.now(), event_key="awake"), identifier="awake")
        row = app.flow.row(batch)
        self.assertEqual(row["reason"], "debounce")
        self.assertEqual(datetime.fromisoformat(row["due_at"]), self.clock.now() + timedelta(seconds=QUIET))

    async def test_a_due_sleep_timer_or_a_foreign_window_is_not_sleeping(self):
        _, app, context = await self.ready()
        self.sleep_for(app, context)
        later = self.clock.now() + timedelta(minutes=NAP_MINUTES + 1)
        due = rest_gate.sleep_context(app.runtime, context, now=later)
        self.assertEqual((due["resting"], due["reason"]), (False, "sleep_timer_due"))
        # Outside the configured window the schedule alone cannot hold anything.
        self.rest(app, context, {"rest_reply_active_windows": "12:20-13:40"})
        settings = rest_gate.read_settings(app.runtime, context)
        outside = rest_gate.sleep_context(app.runtime, context, now=self.clock.now(), settings=settings)
        self.assertEqual((outside["resting"], outside["reason"]), (False, "outside_active_window"))
        self.assertEqual(rest_gate.deferral(app.runtime, context, text="在吗", now=self.clock.now(),
                                            settings=settings), 0.0)

    async def test_configured_rest_window_holds_without_a_sleep_receipt(self):
        _, app, context = await self.ready()
        self.rest(app, context, {"enable_rest_reply_simulation": True,
                                 "rest_reply_active_windows": "07:00-09:00"})
        settings = rest_gate.read_settings(app.runtime, context)
        sleep = rest_gate.sleep_context(app.runtime, context, now=self.clock.now(), settings=settings)
        self.assertEqual((sleep["resting"], sleep["reason"]), (True, "rest_window"))
        self.assertIsNone(sleep["sleep"])
        self.assertGreater(rest_gate.deferral(app.runtime, context, text=NEUTRAL,
                                              now=self.clock.now(), settings=settings), 0.0)

    async def test_a_sleep_receipt_that_stops_answering_keeps_the_ordinary_window(self):
        _, app, context = await self.ready()
        self.sleep_for(app, context)
        from unittest.mock import patch
        with patch.object(rest_gate, "sleep_receipts", side_effect=RuntimeError("activities unavailable")):
            self.assertEqual(rest_gate.deferral(app.runtime, context, text="在吗", now=self.clock.now()), 0.0)
            self.assertFalse(rest_gate.sleep_context(app.runtime, context, now=self.clock.now())["resting"])


if __name__ == "__main__":
    unittest.main()
