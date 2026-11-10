"""Slow continuations keep sealed calendar material and reject changed inputs."""

from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from companion_v2.conversation.foreground import PromptMaterial
from companion_v2.conversation.media.output_settings import DEFAULTS as IMAGE_OUTPUT_DEFAULTS
from companion_v2.conversation.messages import event_from_document
from companion_v2.conversation.participation import ACTION as PARTICIPATION_ACTION
from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.expression.style_management import ExpressionStyleManagement
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_messages import MessageFixture
from tests.test_expression_v1_pipeline import ExpressionFixture, wire_materials
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW
from tests.test_model_gateway import ModelFixture
from tests.test_role_state import StateFixture, state_data
from tests.test_role_tool_loop import ToolLoopFixture, answered, call, requested
from tests.test_group_participation import command


class CalendarContinuationTests(MessageFixture, unittest.TestCase):
    def test_calendar_keeps_the_last_merged_input_time_across_minutes_and_midnight(self):
        batch = self.accept()
        second_at = NOW + timedelta(seconds=90)
        self.clock.advance(timedelta(seconds=90))
        self.assertEqual(self.accept(replace(self.event("merged"), received_at=second_at), batch_id=batch), batch)
        self.messages.seal(self.context, batch, deadline_at=self.deadline).unwrap()
        row = self.messages.batch(batch)
        original = self.messages._request(row, self.context, second_at)
        calendar = next(m for m in original.context.materials if m.purpose == "local_calendar_context")
        for elapsed in (timedelta(seconds=65), timedelta(days=1)):
            with self.subTest(elapsed=elapsed):
                current = self.messages._request(row, self.context, second_at + elapsed)
                self.assertEqual(current.context.materials, original.context.materials)
                self.assertEqual(current.instruction, original.instruction)
        self.assertEqual(json.loads(calendar.payload)["local_time"],
                         second_at.astimezone(ZoneInfo(self.messages.timezone_name)).strftime("%H:%M"))


class ModelContextContinuationTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    async def test_changed_sealed_fields_stop_before_dispatch_and_log_names_only(self):
        run = self.model_run()
        original = self.request()
        session = self.gateway.open_session(run, original)
        changes = (
            ("event", replace(original, event=replace(original.event, input_hash="changed"))),
            ("role_binding", replace(original, role_binding=replace(original.role_binding,
                revision=original.role_binding.revision + 1))),
            ("role_instruction", replace(original, role_instruction="private replacement persona")),
            ("host_role_instruction", replace(original, role_guard_instruction="private replacement guard")),
            ("streaming", replace(original, streaming=True)),
            ("streaming_mode", replace(original, streaming_mode="incremental")),
            ("output_format", replace(original, output_format="journal")),
            ("response_schema_json", replace(original, response_schema_json="{}")),
            ("instruction", replace(original, instruction="private replacement instruction")),
            ("images", replace(original, reply_images=True)),
            ("context.materials", replace(original, context=replace(original.context, materials=(
                PromptMaterial("conversation", "current_source", "private changed material"),)))),
        )
        for field, request in changes:
            with self.subTest(field=field), self.assertLogs("companion_v2.model_gateway.service", "WARNING") as logs:
                with self.assertRaises(TaskError) as caught:
                    await session.generate(request)
                self.assertEqual(caught.exception.code, "model_context_mismatch")
                self.assertIn("fields=" + field, logs.output[0])
                self.assertNotIn("private", logs.output[0])
                self.assertNotIn(original.role_instruction, logs.output[0])
                self.assertEqual(self.call_rows(), ())
        await session.generate(original)
        self.assertEqual(len(self.called), 1)


class DerivedMaterialContinuationTests(ToolLoopFixture, unittest.IsolatedAsyncioTestCase):
    async def test_later_intake_keeps_the_turns_original_acquaintance_counts(self):
        self.messages.acquaintance.settings = SimpleNamespace(read=lambda _: {
            "person.acquaintance_enabled": True, "person.acquaintance_card_chars": 1024})

        async def respond(envelope):
            if not envelope.tool_history:
                self.accept(self.event("later-input"))
                self.clock.advance(timedelta(seconds=5))
                return requested(call())
            return answered()

        self.responses["primary"] = respond
        await self.generate()
        self.assertEqual(len(self.called), 2)
        cards = [next(m for m in envelope.original.context.materials if m.purpose == "current_person_card")
                 for _, envelope in self.called]
        self.assertEqual(cards[1], cards[0])
        self.assertEqual(json.loads(cards[0].payload)["msg_count"], 1)
        self.assertEqual(self.messages.acquaintance.recall(self.context)["msg_count"], 2)

    async def test_image_preferences_do_not_append_conflicting_guidance_mid_turn(self):
        values = dict(IMAGE_OUTPUT_DEFAULTS)

        async def respond(envelope):
            if not envelope.tool_history:
                values.update(trigger_mode="off", prompt_format="nai")
                return requested(call())
            return answered()

        self.responses["primary"] = respond
        with patch("companion_v2.conversation.media.output_settings.effective_settings",
                   side_effect=lambda *_: dict(values)):
            await self.generate()
        original, continued = (envelope.original for _, envelope in self.called)
        self.assertEqual(continued.instruction, original.instruction)


class GroupDerivedContinuationTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_new_group_observations_do_not_rewrite_the_participation_turn(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        app = self.controller.session.application
        captured = []

        async def respond(payload, tools):
            captured.append(wire_materials(payload, "group_relationship_network"))
            if not any(message["role"] == "tool" for message in payload["messages"]):
                row = app.runtime.read_one("SELECT * FROM conversation_batches WHERE state='sealed'", ())
                context = TaskContext.restore(json.loads(row["context_json"]))
                input_row = app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id=?",
                                                 (row["batch_id"],))
                event = event_from_document(json.loads(input_row["event_json"]), context)
                later = replace(event, event_id="later-group-observation", received_at=self.clock.now())
                with app.runtime.transaction() as connection:
                    app.messages.group_network.observe(context, later, connection, text="another message")
                self.clock.advance(timedelta(seconds=65))
                return completion(tools=[command(PARTICIPATION_ACTION, {"mode": "reply"})])
            return completion(final_text("continue"))

        self.native.provider.client.state.behavior = respond
        batch = (await self.controller.receive_claimed(self.group_event())).unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(app.flow.row(batch)["state"], "complete", app.flow.row(batch)["reason"])
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[1], captured[0])


class StateProjectionContinuationTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_mood_decay_preserves_the_captured_view_and_current_source_checks(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, mood_enabled=True)
        source = self.source(app, context)
        app.role_state.write(context, data=state_data(mood={"baseline": 6, "joy": 9,
            "anger": 2, "sorrow": 3, "fear": 2, "intensity": "strong"}),
            expected_revision=0, operation_key="structured-mood", sources=(source,))
        consumer = app.messages.role_state_context
        captured = consumer.capture(context, self.clock.now())
        original = consumer.contributions(captured, context, self.clock.now())
        self.clock.advance(timedelta(seconds=65))
        self.assertEqual(consumer.contributions(captured, context, self.clock.now()), original)
        self.assertEqual(consumer.contributions(captured, context, self.clock.now(), historical=True), original)
        self.revoke_source(app, source)
        for historical in (False, True):
            with self.subTest(historical=historical), self.assertRaises(TaskError):
                consumer.contributions(captured, context, self.clock.now(), historical=historical)


class OptionalGuidanceContinuationTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_retired_style_drops_only_its_guidance_and_the_tool_turn_completes(self):
        self.expression_profiles()
        host = await self.start()
        app = host.application
        context = self.selected_context()
        self.configure_expression(app, {"style_review_enabled": False})
        identifier = self.adopt_pack(app, context)

        async def respond(envelope):
            if not envelope.tool_history:
                manager = ExpressionStyleManagement(app)
                row = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
                manager.command(operator=self.core, context=context, signal_id=identifier,
                    action="retire", view_token=row["view_token"], operation_key="retire-during-tool").unwrap()
                return requested(call())
            return answered("continue")

        self.model_behavior = respond
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        result = await self.done(app, batch)
        self.assertEqual(result["state"], "complete", result["reason"])
        original, continued = self.called
        self.assertTrue(any(m.purpose == "expression_style_rule" for m in original.original.context.materials))
        self.assertFalse(any(m.purpose == "expression_style_rule" for m in continued.original.context.materials))
        forged = replace(continued.original, context=replace(continued.original.context, materials=(
            PromptMaterial("conversation", "current_source", "replacement"), *continued.original.context.materials[1:])))
        self.assertFalse(app.tools.validate_owner_refresh(context, batch, original.original, forged,
                                                        original.tool_history, continued.tool_history))


if __name__ == "__main__":
    unittest.main()
