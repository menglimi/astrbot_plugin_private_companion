"""T39 appointment execution and T41 post-draft persona judgement.

Both batches run through the real scheduler: the appointment tests drive the
original calendar owner, and the judgement tests drive a second supervised
background task that may only allow or withhold the sealed draft.
"""

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.agenda.models import Frequency, Schedule
from companion_v2.configuration.models import ConfigurationError
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import encode
from companion_v2.model_gateway.routing import configuration_fields as model_fields
from companion_v2.proactive import persona_judge
from companion_v2.proactive.prompts import TOPIC_CONTINUITY_INSTRUCTION
from companion_v2.proactive.configuration import GROUP, configuration_fields
from companion_v2.proactive.service import persona_judge_profile
from companion_v2.role_work.state.management import StateManagement
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_state import calendar_data
from tests.test_role_tool_loop import answered


class AppointmentFixture(ContactScheduleFixture):
    async def appointment_host(self, **settings):
        host, batch = await self.scheduled()
        self.app = host.application
        self.origin = self.context(self.app, batch)
        manager = StateManagement(self.app, self.origin)
        before = manager.status(self.core).unwrap()
        manager.command(self.core, dict(action="configure", operation_key="enable-calendar",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"calendar_enabled": True})).unwrap()
        self.settings(self.app, {**{purpose + ".enabled": False
            for purpose in ("affection", "care", "play", "repair", "invitation")}, **settings}).unwrap()
        return host

    def commitment(self, identifier="appointment", *, due, schedule=None):
        schedule = schedule or Schedule(due, "Asia/Shanghai")
        data = calendar_data(kind="commitment", title="一起去看展", detail="下午在入口碰面",
            schedule=json.loads(encode(schedule)))
        self.app.calendar.command(self.origin, identifier=identifier, action="create", expected_revision=0,
            operation_key="create-" + identifier, data=data)
        self.app.calendar.command(self.origin, identifier=identifier, action="accept", expected_revision=1,
            operation_key="accept-" + identifier, operator=self.core)
        return data

    def records(self):
        return [json.loads(row["record_json"]) for row in self.contacts(self.app)]

    def purposes(self):
        return [record["purpose"] for record in self.records()]


class AppointmentTests(AppointmentFixture, unittest.IsolatedAsyncioTestCase):
    def test_appointment_fields_are_registered_and_bounded(self):
        fields = {field.field_id: field for field in configuration_fields()}
        for name, default, minimum, maximum in (("appointment_lead_minutes", 30, 0, 240),
                                                ("appointment_lag_minutes", 0, 0, 180),
                                                ("appointment_pre_silence_minutes", 20, 0, 240)):
            field = fields[GROUP + "." + name]
            self.assertEqual(field.default, default, name)
            self.assertEqual((field.minimum, field.maximum), (minimum, maximum), name)
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".appointment_lead_minutes"].validate(241)

    async def test_appointment_opens_only_inside_its_configured_lead(self):
        await self.appointment_host(appointment_lead_minutes=5, appointment_pre_silence_minutes=0,
                                    **{"chat.enabled": False})
        self.commitment(due=self.clock.now() + timedelta(minutes=20))
        self.advance(self.app, 60)
        self.app.contact_schedule.advance()
        self.assertEqual(self.contacts(self.app), ())
        # Sixteen minutes later the configured lead has opened the appointment.
        self.advance(self.app, 16 * 60)
        await self.automatic_delivery(self.app)
        self.assertEqual(self.purposes(), ["reminder"])
        self.assertEqual(json.loads(self.contacts(self.app)[0]["record_json"])["purpose"], "reminder")

    async def test_appointment_can_open_exactly_at_its_own_instant(self):
        await self.appointment_host(appointment_lead_minutes=0, appointment_lag_minutes=10,
                                    appointment_pre_silence_minutes=0, **{"chat.enabled": False})
        self.commitment(due=self.clock.now() + timedelta(minutes=5))
        self.advance(self.app, 60)
        self.app.contact_schedule.advance()
        self.assertEqual(self.contacts(self.app), ())
        self.advance(self.app, 5 * 60)
        await self.automatic_delivery(self.app)
        self.assertEqual(self.purposes(), ["reminder"])
        self.assertEqual(json.loads(self.contacts(self.app)[0]["record_json"])["purpose"], "reminder")

    async def test_missed_appointment_is_dropped_instead_of_replayed(self):
        await self.appointment_host(appointment_lead_minutes=0, appointment_lag_minutes=0,
                                    appointment_pre_silence_minutes=0, **{"chat.enabled": False})
        self.commitment(due=self.clock.now() + timedelta(minutes=1))
        self.advance(self.app, 5 * 60)
        self.app.contact_schedule.advance()
        self.assertEqual(self.contacts(self.app), ())
        self.assertEqual(self.contact_calls(), [])
        # The configured lag is what keeps a just-missed appointment eligible.
        self.settings(self.app, {"appointment_lag_minutes": 10}).unwrap()
        await self.automatic_delivery(self.app)
        self.assertEqual(self.purposes(), ["reminder"])

    async def test_pre_appointment_silence_holds_voluntary_contacts_only(self):
        await self.appointment_host(**{"reminder.enabled": False, "appointment_pre_silence_minutes": 20})
        self.commitment(due=self.clock.now() + timedelta(minutes=20))
        self.advance(self.app, 60)
        self.app.contact_schedule.advance()
        self.assertEqual(self.contacts(self.app), ())
        self.assertEqual(self.contact_calls(), [])
        # Without the silence window the same moment produces the ordinary check-in.
        self.settings(self.app, {"appointment_pre_silence_minutes": 0}).unwrap()
        await self.automatic_delivery(self.app)
        self.assertEqual(self.purposes(), ["chat"])

    async def test_recurring_appointment_still_bounds_each_occurrence(self):
        await self.appointment_host(appointment_lead_minutes=0, appointment_lag_minutes=15,
                                    appointment_pre_silence_minutes=0, **{"chat.enabled": False})
        self.commitment(due=self.clock.now() + timedelta(minutes=5),
                        schedule=Schedule(self.clock.now() + timedelta(minutes=5), "Asia/Shanghai",
                                          Frequency.DAILY, count=3))
        self.advance(self.app, 5 * 60)
        await self.automatic_delivery(self.app)
        self.assertEqual(self.purposes(), ["reminder"])
        self.advance(self.app, 20 * 60)
        self.app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(self.app)), 1)


class JudgeFixture(ContactScheduleFixture):
    judge_registered = True

    def setUp(self):
        super().setUp()
        self.judge_calls, self.judge_behavior = [], None
        if self.judge_registered:
            self.profiles += (persona_judge_profile("primary", max_output_tokens=100),)
            self.registry = FieldRegistry(version="contact-test.v1", fields=model_fields(self.profiles))

    async def generate(self, envelope):
        if any(item.purpose == persona_judge.DRAFT_PURPOSE for item in envelope.original.context.materials):
            self.judge_calls.append(envelope)
            if self.judge_behavior:
                return await self.judge_behavior(envelope)
            return answered('{"decision":"send","score":90,"reason":"符合当前人格"}')
        return await super().generate(envelope)

    async def judged(self, app, *, rounds=6):
        """Let the scheduler seal the draft, judge it, and settle its ledger."""
        await self.until(lambda: self.contacts(app) or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        for _ in range(rounds):
            await self.until(lambda: not app.proactive.active_handles)
            self.advance(app, 5)
            app.contact_schedule.advance()
            await self.until(lambda: not app.contact_schedule.active_handles, seconds=5)
            records = self.records(app)
            if records and records[0]["decision"] != "judge_pending":
                return records[0]
        return self.records(app)[0]

    def records(self, app):
        return [json.loads(row["record_json"]) for row in self.contacts(app)]

    def deliveries(self, app):
        return app.runtime.read_all("SELECT * FROM background_deliveries", ())

    async def judged_host(self, **settings):
        host, _ = await self.scheduled()
        self.settings(host.application, {"enable_llm_proactive_persona_judge": True,
            "max_contacts_24h": 5, **{purpose + ".enabled": False
                for purpose in ("affection", "care", "play", "repair", "invitation")}, **settings}).unwrap()
        self.advance(host.application, 60)
        return host


class PersonaJudgeTests(JudgeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_judge_receives_the_composers_verified_dialogue_reply_and_evidence(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_llm_proactive_persona_judge": True,
            **{purpose + ".enabled": False for purpose in ("affection", "care", "play", "repair", "invitation")}}).unwrap()
        self.advance(app, 1)
        batch = host.receive(self.event("photo-correction", text="照片刚才发过啦，这次先不画。")).unwrap()
        await self.done(app, batch)
        self.advance(app, 60)
        record = await self.judged(app)

        self.assertTrue(record["judge"]["allowed"])
        compose = self.contact_calls()[-1].original
        reviewed = self.judge_calls[-1].original
        owners = {"conversation.private_history", "delivery.history"}
        expected = [(item.owner, item.payload) for item in compose.context.materials if item.owner in owners]
        actual = [(item.owner, item.payload) for item in reviewed.context.materials if item.owner in owners]
        self.assertTrue(expected)
        self.assertEqual(actual, expected)
        self.assertTrue(any("照片刚才发过啦，这次先不画。" in payload for _, payload in actual))
        self.assertTrue(any("记住了。" in payload for _, payload in actual))
        expected_evidence = [item for item in compose.context.materials if item.purpose == "contact_evidence"]
        actual_evidence = [item for item in reviewed.context.materials if item.purpose == "contact_evidence"]
        self.assertTrue(expected_evidence)
        self.assertEqual(actual_evidence, expected_evidence)
        self.assertIn(TOPIC_CONTINUITY_INSTRUCTION, compose.instruction)
        self.assertIn(TOPIC_CONTINUITY_INSTRUCTION, reviewed.instruction)

    async def test_judge_rejects_evidence_changed_after_composition(self):
        host = await self.judged_host()
        app = host.application
        await self.until(lambda: len(self.contacts(app)) == 1)
        row = self.contacts(app)[0]
        document = json.loads(row["record_json"])
        self.assertEqual(document["decision"], "judge_pending")
        self.assertTrue(document["source_hashes"])
        document["source_hashes"][0] = "changed-after-composition"
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE proactive_contacts SET record_json=? WHERE contact_id=?",
                               (encode(document), row["contact_id"]))

        record = await self.judged(app)

        self.assertEqual(record["decision"], "skip")
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(self.judge_calls, [])
        self.assertEqual(self.deliveries(app), ())

    async def test_judge_does_not_read_a_history_snapshot_from_another_context(self):
        host = await self.judged_host()
        app = host.application
        await self.until(lambda: len(self.contacts(app)) == 1)
        row = self.contacts(app)[0]
        document = json.loads(row["record_json"])
        self.assertEqual(document["decision"], "judge_pending")
        document["dialogue_history"]["context_key"] = "another-conversation"
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE proactive_contacts SET record_json=? WHERE contact_id=?",
                               (encode(document), row["contact_id"]))

        record = await self.judged(app)

        self.assertEqual(record["decision"], "skip")
        self.assertFalse(record["judge"]["allowed"])
        self.assertEqual(self.judge_calls, [])
        self.assertEqual(self.deliveries(app), ())

    def test_judge_fields_are_registered_and_bounded(self):
        fields = {field.field_id: field for field in configuration_fields()}
        for name, default in (("enable_llm_proactive_persona_judge", False),
                              ("proactive_persona_judge_threshold", 62),
                              ("proactive_persona_judge_cache_minutes", 180),
                              ("proactive_persona_judge_exempt_purposes", "")):
            self.assertEqual(fields[GROUP + "." + name].default, default, name)
        self.assertEqual(persona_judge_profile("primary").group, "model.proactive.judge")
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".proactive_persona_judge_threshold"].validate(101)
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".proactive_persona_judge_exempt_purposes"].validate("unknown-purpose")

    async def test_judge_is_off_by_default_and_delivers_unchanged(self):
        host, _ = await self.scheduled()
        app = host.application
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.assertEqual(self.judge_calls, [])
        self.assertIsNone(self.records(app)[0]["judge"])
        self.assertEqual(len(self.sent), 2)

    async def test_allowed_judgement_delivers_the_sealed_body(self):
        host = await self.judged_host()
        app = host.application
        record = await self.judged(app)
        self.assertEqual((record["decision"], record["judge"]["decision"], record["judge"]["allowed"]),
                         ("send", "send", True))
        self.assertEqual(record["judge"]["profile"], persona_judge.KIND)
        self.assertEqual(len(self.judge_calls), 1)
        task_id = self.contacts(app)[0]["task_id"]
        await self.delivery_state(app, task_id, "delivered")
        status = app.proactive.observe(self.contact_context(), task_id).unwrap()
        self.assertEqual((status["delivery"], status["judge"], status["skipped"]), ("delivered", "resolved", False))
        self.assertEqual(self.sent[-1].text, "刚想起上次的话题。")

    async def test_dropped_judgement_withholds_the_draft_without_any_delivery(self):
        async def drop(envelope):
            return answered('{"decision":"drop","score":12,"reason":"此刻不符合人格"}')
        host = await self.judged_host()
        app = host.application
        self.judge_behavior = drop
        record = await self.judged(app)
        self.assertEqual((record["decision"], record["judge"]["decision"], record["judge"]["allowed"]),
                         ("skip", "drop", False))
        self.assertEqual(len(self.judge_calls), 1)
        self.assertEqual(len(self.sent), 1)  # The foreground reply only.
        task_id = self.contacts(app)[0]["task_id"]
        self.assertIsNone(app.proactive._delivery_row(task_id))
        status = app.proactive.observe(self.contact_context(), task_id).unwrap()
        self.assertEqual((status["skipped"], status["delivery"]), (True, None))
        self.assertEqual(self.deliveries(app), ())
        judge_id = record["judge"]["task_id"]
        await self.until(lambda: app.runtime.read_one(
            "SELECT state FROM execution_tasks WHERE task_id=?", (judge_id,))[0] == "succeeded")
        task = app.tasks.read(self.contact_context(), judge_id).unwrap()
        self.assertEqual(task.effect, "committed")
        self.assertEqual(task.operation_id, "save-" + judge_id)
        self.assertEqual(app.proactive.operations.get(task.operation_id).unwrap().state.value, "completed")
        self.assertFalse(app.proactive.validate_judge(task.spec, self.clock.now()).is_success)
        self.assertFalse(app.proactive.validate_judge(replace(task.spec, task_id="another-judge"),
            self.clock.now()).is_success)

    async def test_judgement_below_the_threshold_withholds_the_draft(self):
        async def low(envelope):
            return answered('{"decision":"send","score":40,"reason":"勉强可以"}')
        host = await self.judged_host(proactive_persona_judge_threshold=80)
        app = host.application
        self.judge_behavior = low
        record = await self.judged(app)
        self.assertEqual((record["decision"], record["judge"]["allowed"]), ("skip", False))
        self.assertEqual(len(self.sent), 1)

    async def test_unusable_judgement_never_sends_and_records_the_failure(self):
        async def broken(envelope):
            return answered("我觉得可以发")
        host = await self.judged_host()
        app = host.application
        self.judge_behavior = broken
        record = await self.judged(app)
        self.assertEqual(record["decision"], "skip")
        self.assertFalse(record["judge"]["allowed"])
        self.assertTrue(record["judge"]["failure"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.deliveries(app), ())

    async def test_sealed_draft_is_not_delivered_before_its_judgement(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_llm_proactive_persona_judge": True, "max_contacts_24h": 5}).unwrap()
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        record = self.records(app)[0]
        self.assertEqual(record["decision"], "judge_pending")
        self.assertEqual(record["judge"]["state"], "pending")
        self.assertEqual(self.judge_calls, [])
        self.assertEqual(len(self.sent), 1)


class UnwiredJudgeTests(JudgeFixture, unittest.IsolatedAsyncioTestCase):
    judge_registered = False

    async def test_judge_without_its_registered_model_profile_never_sends(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_llm_proactive_persona_judge": True, "max_contacts_24h": 5}).unwrap()
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        record = self.records(app)[0]
        self.assertEqual((record["decision"], record["judge"]["allowed"]), ("skip", False))
        self.assertEqual(record["judge"]["failure"], "judge_profile_unavailable")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(app.runtime.read_all("SELECT * FROM background_deliveries", ()), ())


class JudgeCacheTests(JudgeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_repeated_body_is_reviewed_again_after_a_contact_was_sent(self):
        host = await self.judged_host(dedup_enabled=False, proactive_closing_contacts_threshold=9)
        app = host.application
        first = await self.judged(app)
        self.assertEqual(first["decision"], "send")
        await self.delivery_state(app, self.contacts(app)[0]["task_id"], "delivered")
        self.advance(app, 120)
        await self.until(lambda: len(self.contacts(app)) == 2)
        self.advance(app, 5)
        app.contact_schedule.advance()
        await self.until(lambda: self.records(app)[1]["decision"] != "judge_pending")
        second = self.records(app)[1]
        self.assertEqual(second["decision"], "send")
        self.assertFalse(second["judge"]["cached"])
        self.assertNotEqual(first["judge"]["signature"], second["judge"]["signature"])
        self.assertEqual(len(self.judge_calls), 2)
        history = [json.loads(item.payload) for item in self.judge_calls[-1].original.context.materials
                   if item.purpose == "confirmed_background_message"]
        self.assertTrue(any(item.get("text") == "刚想起上次的话题。" for item in history))

    async def test_same_review_signature_can_still_use_the_cache(self):
        host = await self.judged_host()
        app = host.application
        first = await self.judged(app)

        cached = app.proactive.persona_judge.cached(self.contact_context(), first["judge"]["signature"],
            self.clock.now(), ttl_minutes=180, threshold=62)

        self.assertIsNotNone(cached)
        self.assertTrue(cached.allowed)
        self.assertTrue(cached.cached)
        self.assertEqual(len(self.judge_calls), 1)

    async def test_exempt_purpose_skips_the_judge_entirely(self):
        host = await self.judged_host(proactive_persona_judge_exempt_purposes="chat")
        app = host.application
        record = await self.judged(app)
        self.assertEqual((record["decision"], record["judge"]["allowed"]), ("send", True))
        self.assertEqual(self.judge_calls, [])
        await self.delivery_state(app, self.contacts(app)[0]["task_id"], "delivered")

    async def test_judge_runs_without_a_separate_daily_call_limit(self):
        host = await self.judged_host()
        app = host.application
        record = await self.judged(app)
        self.assertEqual((record["decision"], record["judge"]["decision"], record["judge"]["allowed"]),
                         ("send", "send", True))
        self.assertEqual(len(self.judge_calls), 1)
        await self.delivery_state(app, self.contacts(app)[0]["task_id"], "delivered")
        self.assertEqual(len(self.sent), 2)

    async def test_judge_settings_have_no_daily_cap_field(self):
        fields = {field.field_id for field in configuration_fields()}
        self.assertNotIn(GROUP + ".proactive_persona_judge_daily_limit", fields)
