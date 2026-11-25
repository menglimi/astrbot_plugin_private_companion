"""Regression tests for post-reply proactive cooldown, media fallback, absorbed follow-ups and deferred review."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace
import unittest

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.native_observations import NativeConversationObservations
from companion_v2.conversation.native_turns import (
    NativeConversationTurns,
    begin_inflight_turn,
    end_inflight_turn,
    foreground_busy,
    latest_scope,
)
from companion_v2.execution.models import TaskError, digest, encode, stamp
from companion_v2.model_gateway.routing import configuration_fields as model_fields
from companion_v2.proactive import persona_judge
from companion_v2.proactive.scheduling import POST_REPLY_MIN_COOLDOWN_SECONDS
from companion_v2.proactive.service import persona_judge_profile
from companion_v2.storage.native_conversation_schema import ensure_native_conversation_migrations
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_tool_loop import answered


def _native_input(context, event_id, text, at, instruction="原角色人格提示"):
    return SimpleNamespace(
        event_id=event_id,
        text=text,
        at=at,
        instruction=instruction,
        route=SimpleNamespace(scope=context.scope),
        principal=context.actor.principal,
        role=context.role_binding.role,
        recipients=context.audience,
        quotes=(),
    )


class ProactivePostReplyCooldownTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (persona_judge_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="contact-post-reply-test.v1", fields=model_fields(self.profiles))

    async def _setup_native_app(self, **contact_settings):
        host = await self.start(self.host())
        app = host.application
        defaults = {
            "enabled": True,
            "automatic_enabled": True,
            "interval_seconds": 3600,
            "idle_seconds": 900,
            "proactive_intensity_preset": "live",
            "proactive_closing_grace_minutes": 0,
            "dedup_enabled": True,
            "dedup_last_message_enabled": True,
        }
        defaults.update(contact_settings)
        self.settings(app, defaults).unwrap()
        context = self.contact_context()
        return host, app, context

    async def test_long_native_reply_waits_full_idle_after_last_sent_at(self):
        _host, app, context = await self._setup_native_app()
        t0 = self.clock.now()
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)

        native_in = _native_input(context, "ev-long-1", "帮我详细总结今天的内容", t0)
        observations.capture_input(context, native_in, turn_id="turn-long-1")
        prepared = {"context": context, "turn_id": "turn-long-1", "instruction": native_in.instruction, "native_input": native_in}
        turns.mark(prepared, "running", at=t0 + timedelta(seconds=1))

        # Reply takes 95 seconds (exceeding live's 900 * 0.1 = 90s idle if measured from observed_at).
        reply_done_at = t0 + timedelta(seconds=95)
        self.advance(app, 95)
        observations.capture_output(context, turn_id="turn-long-1", event_id="ev-long-1",
                                    text="这是今天完整的总结。", at=reply_done_at, state="send_returned")
        turns.mark(prepared, "completed", at=reply_done_at)

        # 5 seconds after normal reply finishes (t0 + 100s > t0 + 90s): MUST NOT trigger proactive yet!
        self.advance(app, 5)
        row = app.contact_schedule.latest_conversation(context)
        self.assertIsNone(app.contact_schedule._plan(row, self.clock.now()))
        diagnostic = app.contact_schedule.diagnostic_for(context, at=self.clock.now())
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "idle_period_not_elapsed")

        origin, _batch, _event = app.contact_schedule._origin(context, row["batch_id"], self.clock.now())
        with self.assertRaises(TaskError) as raised:
            app.contact_schedule.validate_origin(context, origin, self.clock.now())
        self.assertEqual(raised.exception.code, "contact_conversation_active")

        # 85 more seconds (reply_done_at + 90s): now eligible!
        self.advance(app, 85)
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)

    async def test_live_preset_enforces_minimum_post_reply_cooldown_floor(self):
        _host, app, context = await self._setup_native_app(
            idle_seconds=300, proactive_intensity_preset="live",
        )
        preferences = app.proactive.preferences.read(context, observed_at=self.clock.now()).unwrap()
        # 300 * 0.1 = 30s would be too short; floor clamps up to POST_REPLY_MIN_COOLDOWN_SECONDS (60s).
        self.assertEqual(
            app.contact_schedule._post_reply_idle_seconds(preferences),
            POST_REPLY_MIN_COOLDOWN_SECONDS,
        )
        # Explicit idle_seconds=0 remains 0 for opt-out/test configurations.
        self.settings(app, {"idle_seconds": 0}).unwrap()
        zero_prefs = app.proactive.preferences.read(context, observed_at=self.clock.now()).unwrap()
        self.assertEqual(app.contact_schedule._post_reply_idle_seconds(zero_prefs), 0)

    async def test_media_turn_without_text_refreshes_cooldown_and_interaction(self):
        _host, app, context = await self._setup_native_app(
            idle_seconds=900, proactive_intensity_preset="custom",
        )
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)

        # Old text turn 3 hours ago.
        t_old = self.clock.now()
        old_in = _native_input(context, "ev-text-old", "早上好呀", t_old)
        observations.capture_input(context, old_in, turn_id="turn-text-old")
        old_prep = {"context": context, "turn_id": "turn-text-old", "instruction": old_in.instruction, "native_input": old_in}
        turns.mark(old_prep, "completed", at=t_old + timedelta(seconds=5))

        # Deliver an earlier proactive contact so contacts_since_last_input == 1.
        self.advance(app, 1800)
        prior_task = (await self.contact(app, self.opportunity("prior-proactive"))).unwrap().task
        await self.delivery_state(app, prior_task.task_id, "delivered")
        interaction_before = app.background_history.interaction(context, self.clock.now())
        self.assertEqual(interaction_before["contacts_since_last_input"], 1)

        # 2.5 hours later, user sends a pure photo (empty text -> input_observation_id IS NULL).
        self.advance(app, 9000)
        t_media = self.clock.now()
        media_in = _native_input(context, "ev-media-1", "", t_media)
        self.assertIsNone(observations.capture_input(context, media_in, turn_id="turn-media-1"))
        media_prep = {"context": context, "turn_id": "turn-media-1", "instruction": media_in.instruction, "native_input": media_in}
        self.advance(app, 10)
        observations.capture_output(context, turn_id="turn-media-1", event_id="ev-media-1",
                                    text="这张夕阳拍得真好看。", at=self.clock.now(), state="send_returned")
        turns.mark(media_prep, "completed", at=self.clock.now())

        # 5 seconds after normal reply to the image: MUST NOT fire immediately!
        self.advance(app, 5)
        interaction_after = app.background_history.interaction(context, self.clock.now())
        self.assertEqual(interaction_after["contacts_since_last_input"], 0)
        self.assertNotEqual(interaction_after["latest_input"], interaction_before["latest_input"])

        row = app.contact_schedule.latest_conversation(context)
        self.assertIsNone(app.contact_schedule._plan(row, self.clock.now()))
        diagnostic = app.contact_schedule.diagnostic_for(context, at=self.clock.now())
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "idle_period_not_elapsed")

        # Stale proactive origin anchored to the old text turn must fail validate_origin.
        origin, _batch, _event = app.contact_schedule._origin(context, "turn-text-old", self.clock.now())
        with self.assertRaises(TaskError) as raised:
            app.contact_schedule.validate_origin(context, origin, self.clock.now())
        self.assertEqual(raised.exception.code, "contact_conversation_active")

        # Once full idle_seconds (900s) elapse after media reply completion, automatic contact is allowed again.
        self.advance(app, 900)
        self.assertIsNotNone(app.contact_schedule._plan(row, self.clock.now()))

    async def test_absorbed_followup_turn_never_shadows_running_owner_turn(self):
        _host, app, context = await self._setup_native_app(
            idle_seconds=60, proactive_intensity_preset="custom",
        )
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)
        t0 = self.clock.now()

        # T1 is admitted and actively running.
        in1 = _native_input(context, "ev-t1", "第一句话", t0)
        observations.capture_input(context, in1, turn_id="turn-owner-1")
        prep1 = {"context": context, "turn_id": "turn-owner-1", "instruction": in1.instruction, "native_input": in1}
        turns.mark(prep1, "running", at=t0 + timedelta(seconds=1))

        # T2 arrives 2 seconds later and is absorbed into T1's active runner.
        self.advance(app, 2)
        in2 = _native_input(context, "ev-t2", "补一句", self.clock.now())
        observations.capture_input(context, in2, turn_id="turn-followup-2")
        prep2 = {"context": context, "turn_id": "turn-followup-2", "instruction": in2.instruction, "native_input": in2}
        turns.mark(prep2, "absorbed", at=self.clock.now())

        # T1 is still running: foreground_busy MUST be True, latest_scope MUST return T1, and schedule MUST skip.
        self.assertTrue(foreground_busy(app.runtime, context))
        scoped = latest_scope(app.runtime, context.scope)
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["turn_id"], "turn-owner-1")
        self.assertEqual(scoped["generation_state"], "running")

        self.advance(app, 120)
        prepared = app.contact_schedule._begin_tick()
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared[1], ())
        with self.assertRaises(TaskError) as raised:
            app.contact_schedule._origin(context, "turn-owner-1", self.clock.now())
        self.assertEqual(raised.exception.code, "contact_conversation_changed")

        # Now T1 completes and sends its reply.
        observations.capture_output(context, turn_id="turn-owner-1", event_id="ev-t1",
                                    text="两条我都看到了。", at=self.clock.now(), state="send_returned")
        turns.mark(prep1, "completed", at=self.clock.now())

        self.assertFalse(foreground_busy(app.runtime, context))
        # Immediately after T1 finishes: cooldown starts from T1's completion time, not T0!
        self.advance(app, 10)
        row = app.contact_schedule.latest_conversation(context)
        self.assertEqual(row["turn_id"], "turn-owner-1")
        self.assertIsNone(app.contact_schedule._plan(row, self.clock.now()))

        self.advance(app, 60)
        self.assertIsNotNone(app.contact_schedule._plan(row, self.clock.now()))

    async def test_early_inflight_admission_blocks_plan_and_delivery_text(self):
        _host, app, context = await self._setup_native_app(
            idle_seconds=60, proactive_intensity_preset="custom",
        )
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)
        t0 = self.clock.now()
        in1 = _native_input(context, "ev-base", "在吗", t0)
        observations.capture_input(context, in1, turn_id="turn-base")
        prep = {"context": context, "turn_id": "turn-base", "instruction": in1.instruction, "native_input": in1}
        turns.mark(prep, "completed", at=t0 + timedelta(seconds=1))
        self.advance(app, 120)

        # Queue a proactive delivery that is ready to send.
        task = (await self.contact(app, self.opportunity("inflight-blocked-contact"))).unwrap().task
        row_delivery = app.runtime.read_one(
            "SELECT * FROM background_deliveries WHERE owner = 'proactive.contact' ORDER BY rowid DESC LIMIT 1", (),
        )
        from companion_v2.delivery.service import DeliveryRequest
        delivery_request = DeliveryRequest.restore(row_delivery["request_json"])

        # Simulate a new inbound message entering prepare_core_reply BEFORE background SQLite write finishes.
        begin_inflight_turn(app.runtime, context.key, "turn-new-inflight")
        try:
            self.assertTrue(foreground_busy(app.runtime, context))
            turn_row = app.contact_schedule.latest_conversation(context)
            self.assertIsNone(app.contact_schedule._plan(turn_row, self.clock.now()))
            blocked = app.proactive.delivery_text(delivery_request, self.clock.now())
            self.assertIsNotNone(blocked.failure)
            self.assertEqual(blocked.failure.code, "contact_conversation_changed")
        finally:
            end_inflight_turn(app.runtime, context.key, "turn-new-inflight")

        self.assertFalse(foreground_busy(app.runtime, context))
        allowed = app.proactive.delivery_text(delivery_request, self.clock.now())
        self.assertTrue(allowed.is_success, allowed.failure)

    async def test_reality_location_origin_respects_post_reply_cooldown(self):
        _host, app, context = await self._setup_native_app(
            idle_seconds=900, proactive_intensity_preset="live",
        )
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)
        t0 = self.clock.now()
        in1 = _native_input(context, "ev-loc-base", "刚出门", t0)
        observations.capture_input(context, in1, turn_id="turn-loc-1")
        prep = {"context": context, "turn_id": "turn-loc-1", "instruction": in1.instruction, "native_input": in1}
        self.advance(app, 95)
        observations.capture_output(context, turn_id="turn-loc-1", event_id="ev-loc-base",
                                    text="路上注意安全。", at=self.clock.now(), state="send_returned")
        turns.mark(prep, "completed", at=self.clock.now())

        self.advance(app, 10)
        with self.assertRaises(TaskError) as raised:
            app.contact_schedule.reality_location_origin(
                context, self.clock.now(),
                captured_at=self.clock.now().timestamp(),
                source_id="reality-location-test-1",
                place_fingerprint="fp-park",
            )
        self.assertEqual(raised.exception.code, "contact_conversation_active")

        self.advance(app, 90)
        origin, _batch, _event = app.contact_schedule.reality_location_origin(
            context, self.clock.now(),
            captured_at=self.clock.now().timestamp(),
            source_id="reality-location-test-2",
            place_fingerprint="fp-park",
        )
        self.assertEqual(origin["reality_location_event"]["place_fingerprint"], "fp-park")

    async def _run_deferred_expression_review_case(self, *, scenario, rewritten_text, expected_skip, expected_dedup):
        from dataclasses import replace as dc_replace
        from companion_v2.conversation.foreground import ContributionState
        from companion_v2.expression.configuration import read_settings as real_expression_settings
        from companion_v2.proactive.contact_models import ContactIntent
        from companion_v2.proactive.models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
        from unittest.mock import patch

        _host, app, context = await self._setup_native_app(
            idle_seconds=60,
            proactive_intensity_preset="custom",
            enable_llm_proactive_persona_judge=True,
            proactive_persona_judge_threshold=60,
        )
        observations = NativeConversationObservations(app.messages)
        turns = NativeConversationTurns(app.runtime)
        t0 = self.clock.now()
        in1 = _native_input(context, f"ev-rev-{scenario}", "今天挺忙的", t0)
        observations.capture_input(context, in1, turn_id=f"turn-rev-{scenario}")
        prep = {"context": context, "turn_id": f"turn-rev-{scenario}",
                "instruction": in1.instruction, "native_input": in1}
        self.advance(app, 2)
        observations.capture_output(context, turn_id=f"turn-rev-{scenario}", event_id=f"ev-rev-{scenario}",
                                    text="辛苦了，早点歇着。", at=self.clock.now(), state="send_returned")
        turns.mark(prep, "completed", at=self.clock.now())
        self.advance(app, 120)

        if scenario == "duplicate":
            # Seed an already-delivered proactive contact with the exact text that review will rewrite into.
            async def seed_gen(_envelope):
                return answered(json.dumps({"decision": "send", "text": rewritten_text}, ensure_ascii=False))

            self.model_behavior = seed_gen
            self.settings(app, {"enable_llm_proactive_persona_judge": False}).unwrap()
            prior = (await self.contact(app, self.opportunity("seed-dup-1"))).unwrap().task
            await self.delivery_state(app, prior.task_id, "delivered")
            self.settings(app, {"enable_llm_proactive_persona_judge": True}).unwrap()
            self.advance(app, 10)

        # Attach an expression_review stub that rewrites the body after persona_judge approves.
        class FakeReviewer:
            async def review(self, _run, _req, _draft, *, protected_statements=()):
                return rewritten_text

        app.proactive.expression_review = FakeReviewer()

        async def compose_or_judge(envelope):
            if any(item.purpose == persona_judge.DRAFT_PURPOSE for item in envelope.original.context.materials):
                return answered(json.dumps({"decision": "send", "score": 88, "reason": "自然贴合"}, ensure_ascii=False))
            return answered(json.dumps({"decision": "send", "text": "刚才路过巷口闻到桂花香了。"}, ensure_ascii=False))

        self.model_behavior = compose_or_judge
        row = app.contact_schedule.latest_conversation(context)
        origin, _batch, event = app.contact_schedule._origin(context, row["batch_id"], self.clock.now())

        opp = Opportunity(f"rev-opp-{scenario}", context.scope, self.clock.now(), self.clock.now() + timedelta(minutes=5))
        intent = ContactIntent("chat", Thought(f"rev-t-{scenario}", opp, ThoughtKind.CONVERSATION, "聊一句",
                                               EvidenceSnapshot(context.scope, self.clock.now(), (event.source,)), 1.0))

        with patch("companion_v2.proactive.service.expression_settings",
                   side_effect=lambda rt, ctx: dc_replace(real_expression_settings(rt, ctx), style_review_enabled=True)), \
             patch.object(app.proactive, "_capture_expression",
                          return_value={"style_review_enabled": True}), \
             patch.object(app.proactive, "_expression_materials",
                          return_value=(SimpleNamespace(
                              owner="role.expression", purpose="expression_style_rule",
                              payload="保持口语自然", state=ContributionState.READY,
                          ),)):
            task = (await app.proactive.contact(
                context, opp, (intent,), role_instruction=in1.instruction,
                selection_seed=opp.opportunity_id, automatic_origin=origin,
            )).unwrap().task

            await self.until(lambda: app.runtime.read_one(
                "SELECT * FROM proactive_contacts WHERE task_id = ?", (task.task_id,),
            ))
            app.notify()

            def settled_contact():
                found = app.runtime.read_one(
                    "SELECT * FROM proactive_contacts WHERE task_id = ?", (task.task_id,),
                )
                if found is None:
                    return None
                if json.loads(found["record_json"]).get("decision") == "judge_pending":
                    return None
                return found

            await self.until(settled_contact)
            updated = settled_contact()
            self.assertIsNotNone(updated, "deferred judge + expression review did not settle")

        doc = json.loads(updated["record_json"])
        self.assertEqual(doc["decision"], "skip", doc)
        self.assertEqual(doc["skip_reason"], expected_skip, doc)
        if expected_dedup is not None:
            self.assertEqual(doc.get("dedup_reason"), expected_dedup, doc)
        queued = app.runtime.read_all(
            "SELECT * FROM background_deliveries WHERE json_extract(request_json, '$.source_ref') = ?",
            (updated["contact_id"],),
        )
        self.assertEqual(queued, (), "rejected post-review contact must never queue delivery")

    async def test_deferred_expression_review_rejects_dangling_continuation(self):
        await self._run_deferred_expression_review_case(
            scenario="dangling",
            rewritten_text="嗯，照这么说下去",
            expected_skip="continuation_after_silence",
            expected_dedup=None,
        )

    async def test_deferred_expression_review_rejects_duplicate_rewrite(self):
        await self._run_deferred_expression_review_case(
            scenario="duplicate",
            rewritten_text="今天风很舒服，你那边怎么样？",
            expected_skip="duplicate",
            expected_dedup="exact_repeat",
        )

    def test_legacy_turns_table_check_constraint_migrates_for_absorbed_state(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("""CREATE TABLE conversation_native_turns (
            turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
            role_instruction TEXT NOT NULL, input_observation_id TEXT,
            observed_at TEXT NOT NULL, generation_state TEXT NOT NULL
                CHECK(generation_state IN ('prepared', 'running', 'completed', 'failed')),
            phase_at TEXT NOT NULL, last_sent_at TEXT
        )""")
        conn.execute(
            """INSERT INTO conversation_native_turns VALUES
               ('t-legacy', 'ctx-1', '{"audience":{"scope":{"kind":"private"}}}', 'role', NULL,
                '2026-11-01T00:00:00+00:00', 'completed', '2026-11-01T00:00:05+00:00', NULL)"""
        )
        changed = ensure_native_conversation_migrations(conn)
        self.assertTrue(changed)
        # Existing row preserved and 'absorbed' now accepted by CHECK constraint.
        row = conn.execute("SELECT turn_id, generation_state FROM conversation_native_turns WHERE turn_id = 't-legacy'").fetchone()
        self.assertEqual(row, ("t-legacy", "completed"))
        conn.execute(
            """INSERT INTO conversation_native_turns
               (turn_id, context_key, context_json, role_instruction, input_observation_id,
                observed_at, generation_state, phase_at, last_sent_at, scope_kind, role_binding_key, context_hash)
               VALUES ('t-abs', 'ctx-1', '{}', 'role', NULL,
                       '2026-11-01T00:01:00+00:00', 'absorbed', '2026-11-01T00:01:00+00:00', NULL, 'private', '', '')"""
        )
        self.assertEqual(
            conn.execute("SELECT generation_state FROM conversation_native_turns WHERE turn_id = 't-abs'").fetchone()[0],
            "absorbed",
        )


if __name__ == "__main__":
    unittest.main()
