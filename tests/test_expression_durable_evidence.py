"""Durable expression evidence survives a window reset; temporary recall does not.

A ``/reset`` ends the temporary conversation context: raw recall, in-flight
batches and session atoms. The samples a learned rule cites are durable
evidence, so they keep reading their governed source (state, expiry, content
hash, withdrawal) and the audience, role and scope they were captured under.
The reset only dates such evidence; it must never turn a rule into
"unavailable" while its original message is still readable.

These tests drive the native-observation path the host actually writes
(``conversation_inputs`` stays empty) and a real reset boundary.
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.native_observations import inbound_event
from companion_v2.conversation.recalls import MessageRecall
from companion_v2.conversation.reset import (ConversationWindow, durable_reset_at, reset_window,
                                             source_current)
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.expression import rule_details
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.memory.codec import source as decode_source
from companion_v2.storage.codec import encode_timestamp
from tests.group_host_support import GroupFixture


SIGNAL_ID = "expression-run:durable:signal:0"
RUN_ID = "expression-run:durable"
SAMPLE = "嗯哼，这样的说法很轻松"
OBSERVATION = "在群里习惯把话说完再收尾"
PROPOSAL = "用一句完整的话收尾，不急着补第二句"
FORM = {"kind": "style", "situation": "群里聊到熟悉的话题时", "pattern": "一句话说完", "avoid": ""}
APPLICABILITY = {"channels": ["group"], "relationship_stages": ["any"],
                 "emotion_gates": ["any"], "intent": "any", "keywords": []}


class ExpressionDurableEvidenceTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.sequence = 0

    async def observed(self, *, text=SAMPLE, sender="200"):
        """One group message captured as a native observation, without a reply batch."""
        self.sequence += 1
        result = await self.controller.prepare_core_reply(
            self.group_event(identifier="durable-" + str(self.sequence), text=text, sender=sender))
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertIsNotNone(await prepared["native_source_future"])
        return prepared["context"]

    def native_sample(self, context):
        """The observed inbound message with its governed source, as the host stores it."""
        row = self.app.runtime.read_one("""SELECT * FROM conversation_native_observations
            WHERE kind = 'input' AND context_key = ? ORDER BY observed_at DESC LIMIT 1""",
            (context.key,))
        if row is None:
            raise AssertionError("the native input observation is missing")
        event = inbound_event(row)
        return event, self.app.expression_samples.input_handle(event), decode_source(
            json.loads(row["source_json"]))

    @staticmethod
    def owner_key(context):
        return digest(RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                   context.role_binding.role.subject_id))

    def write_rule(self, context, *, handle, characters, closed_at, state="adopted", texts=None):
        at = stamp(self.app.tasks.now())
        owner_key, scope_key = self.owner_key(context), digest(context.scope)
        window = {"opened_at": stamp(closed_at - timedelta(hours=1)), "closed_at": stamp(closed_at),
                  "handles": [handle], "characters": characters,
                  "context_key": context.key, "source_kind": "conversation_input"}
        if texts is not None:
            window["texts"] = texts
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '对话表达', 'decided', ?, ?)""",
                (RUN_ID, owner_key, scope_key, encode(window), RUN_ID + ":material", at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', ?, ?, ?, 0.8, ?, ?, ?, ?,
                        'tester', ?, 0)""",
                (SIGNAL_ID, RUN_ID, owner_key, scope_key, OBSERVATION, PROPOSAL,
                 encode(APPLICABILITY), encode([handle]), state, at, at, "tester:" + SIGNAL_ID))
        rule_details.write(self.app.runtime, SIGNAL_ID, FORM, at=self.app.tasks.now(),
                           actor="tester", operation_key="durable-form")

    def reset(self, context):
        reset_window(self.app.runtime, ConversationWindow.from_scope(context.scope),
                     reset_id="durable-evidence-reset", at=self.app.tasks.now())

    def page(self, context, state="adopted"):
        return ExpressionStyleManagement(self.app).list(
            operator=dashboard_operator(self.app.runtime, "tester", at=self.app.tasks.now()),
            context=context, state=state)

    async def adopted_rule(self):
        """One observed group message plus the adopted rule that cites it."""
        context = await self.observed()
        event, handle, reference = self.native_sample(context)
        closed_at = self.app.tasks.now()
        self.write_rule(context, handle=handle, characters=len(SAMPLE), closed_at=closed_at)
        return context, event, handle, reference, closed_at

    def sample_window(self):
        window = json.loads(self.app.runtime.read_one(
            "SELECT sample_json FROM expression_style_runs WHERE run_id = ?", (RUN_ID,))["sample_json"])
        return window, datetime.fromisoformat(window["opened_at"]), datetime.fromisoformat(
            window["closed_at"])

    async def test_reset_never_revokes_stored_native_evidence(self):
        context, event, handle, reference, closed_at = await self.adopted_rule()
        # The native path leaves the legacy batch table empty, which is exactly
        # why the old check degraded into a bare timestamp comparison.
        self.assertEqual(int(self.app.runtime.read_one(
            "SELECT COUNT(*) AS count FROM conversation_inputs", ())["count"]), 0)
        self.assertTrue(source_current(self.app.runtime, reference))

        self.clock.advance(timedelta(seconds=1))
        self.reset(context)
        moment = durable_reset_at(self.app.runtime, context.scope)
        self.assertEqual(moment, stamp(self.clock.now()))
        # Temporary and raw recall are still gated by the reset boundary.
        self.assertFalse(source_current(self.app.runtime, reference))

        # Durable evidence rechecks its governed source instead of the timestamp.
        window, opened_at, closed_at = self.sample_window()
        sample_context = self.app.expression_samples.evidence_context(window, context)
        self.assertEqual(self.app.expression_samples.evidence_reset_at(sample_context, closed_at),
                         moment)
        self.assertEqual(self.app.expression_samples.describe_handles((handle,), opened_at,
            closed_at, sample_context, self.app.tasks.now(), source_kind="conversation_input",
            durable=True), [SAMPLE])
        # The reply path rechecks every adopted rule through resolve_handles.
        self.assertTrue(self.app.expression_samples.resolve_handles((handle,), opened_at, closed_at,
            sample_context, self.app.tasks.now(), source_kind="conversation_input"))
        # A fresh learning batch is not durable evidence and stays inside the window.
        with self.assertRaises(TaskError) as caught:
            self.app.expression_samples.describe_handles((handle,), opened_at, closed_at,
                sample_context, self.app.tasks.now(), source_kind="conversation_input")
        self.assertEqual(caught.exception.code, "expression_sample_changed")

    async def test_reply_injection_keeps_a_pre_reset_rule(self):
        """The reply path rechecks adopted rules through resolve_handles."""
        context, event, handle, reference, closed_at = await self.adopted_rule()
        self.clock.advance(timedelta(seconds=1))
        self.reset(context)

        at = self.app.tasks.now()
        style = self.app.expression_style_context
        snapshot = style.capture(context, at)
        self.assertEqual(snapshot["state"], "ready", snapshot)
        self.assertEqual([item["signal_id"] for item in snapshot["references"]], [SIGNAL_ID])
        materials = [item for item in style.contributions(snapshot, context, at,
            batch_id="durable-injection") if item.purpose == "expression_style_rule"]
        self.assertEqual(len(materials), 1)
        payload = json.loads(materials[0].payload)
        self.assertEqual(payload["signal_id"], SIGNAL_ID)
        self.assertEqual(payload["basis"], "adopted_dialogue_expression")

    async def test_list_keeps_pre_reset_evidence_and_labels_it_historical(self):
        context, event, handle, reference, closed_at = await self.adopted_rule()
        self.clock.advance(timedelta(seconds=1))
        self.reset(context)

        page = self.page(context)
        self.assertTrue(page.is_success, page.failure)
        item = page.unwrap()["items"][0]
        self.assertIsNone(item["unavailable_reason"])
        self.assertEqual(item["evidence"], [SAMPLE])
        self.assertEqual(item["observation"], OBSERVATION)
        self.assertEqual(item["proposal"], PROPOSAL)
        self.assertEqual(item["expression"]["pattern"], "一句话说完")
        self.assertEqual(item["evidence_reset_at"], stamp(self.clock.now()))
        self.assertTrue(item["can_revise"])
        self.assertEqual(item["actions"], ["retire"])

    async def test_withdrawn_evidence_keeps_the_rule_text_but_marks_it_unreadable(self):
        context, event, handle, reference, closed_at = await self.adopted_rule()
        self.clock.advance(timedelta(seconds=1))
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(
            context.scope, event.event_id, event.principal.native_account_id)))

        page = self.page(context)
        self.assertTrue(page.is_success, page.failure)
        item = page.unwrap()["items"][0]
        self.assertEqual(item["unavailable_reason"], "expression_sample_changed")
        # The learned rule itself never disappears: only its evidence is gone.
        self.assertEqual(item["observation"], OBSERVATION)
        self.assertEqual(item["proposal"], PROPOSAL)
        self.assertEqual(item["expression"]["pattern"], "一句话说完")
        self.assertEqual(item["evidence"], [])
        self.assertIsNone(item["evidence_reset_at"])
        self.assertFalse(item["can_revise"])
        self.assertEqual(item["actions"], ["retire"])

    async def test_list_reuses_evidence_reads_for_rules_from_the_same_window(self):
        context, _, handle, _, closed_at = await self.adopted_rule()
        second_id = SIGNAL_ID + ":same-window"
        at = stamp(self.app.tasks.now())
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'punctuation', ?, ?, ?, 0.8, ?, 'adopted', ?, ?,
                        'tester', ?, 1)""",
                (second_id, RUN_ID, self.owner_key(context), digest(context.scope),
                 OBSERVATION, PROPOSAL, encode(APPLICABILITY), encode([handle]), at, at,
                 "tester:" + second_id))
        rule_details.write(self.app.runtime, second_id, FORM, at=self.app.tasks.now(),
                           actor="tester", operation_key="durable-form-same-window")

        samples = self.app.expression_samples
        with patch.object(samples, "describe_handles", wraps=samples.describe_handles) as describe:
            page = self.page(context)

        self.assertTrue(page.is_success, page.failure)
        self.assertEqual(describe.call_count, 1)
        self.assertEqual(len(page.unwrap()["items"]), 2)
        self.assertEqual([item["evidence"] for item in page.unwrap()["items"]], [[SAMPLE], [SAMPLE]])

    async def test_saved_evidence_excerpt_survives_source_cleanup(self):
        context = await self.observed()
        event, handle, _reference = self.native_sample(context)
        closed_at = self.app.tasks.now()
        self.write_rule(context, handle=handle, characters=len(SAMPLE), closed_at=closed_at,
                        state="pending", texts={handle: SAMPLE})
        # Simulate retention cleaning both the legacy input table and native
        # observations. The bounded excerpt on the run is now the only evidence.
        with self.app.runtime.transaction() as connection:
            connection.execute("DELETE FROM conversation_native_observations WHERE context_key = ?",
                               (context.key,))
            connection.execute("DELETE FROM conversation_batches WHERE origin_key = ?",
                               (context.key,))

        page = self.page(context, state="pending")
        self.assertTrue(page.is_success, page.failure)
        item = page.unwrap()["items"][0]
        self.assertIsNone(item["unavailable_reason"])
        self.assertEqual(item["evidence"], [SAMPLE])
        self.assertTrue(item["evidence_snapshot"])
        self.assertEqual(item["actions"], ["adopt", "reject"])

        manager = ExpressionStyleManagement(self.app)
        adopted = manager.command(
            operator=dashboard_operator(self.app.runtime, "tester", at=self.app.tasks.now()),
            context=context, signal_id=item["signal_id"], action="adopt",
            view_token=item["view_token"], operation_key="snapshot-adopt")
        self.assertTrue(adopted.is_success, adopted.failure)
        self.assertEqual(adopted.unwrap()["state"], "adopted")

    async def test_retired_rule_with_unreadable_evidence_cannot_be_restored(self):
        context, event, handle, reference, closed_at = await self.adopted_rule()
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE expression_style_signals SET state = 'retired' "
                               "WHERE signal_id = ?", (SIGNAL_ID,))
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(
            context.scope, event.event_id, event.principal.native_account_id)))

        page = self.page(context, state="retired")
        self.assertTrue(page.is_success, page.failure)
        item = page.unwrap()["items"][0]
        self.assertEqual(item["unavailable_reason"], "expression_sample_changed")
        self.assertEqual(item["expression"]["pattern"], "一句话说完")
        self.assertEqual(item["actions"], [])

    async def test_expired_source_keeps_the_rule_text_but_marks_it_unreadable(self):
        context, event, handle, reference, closed_at = await self.adopted_rule()
        # A stored source can only expire after it was issued; reading it later
        # must report unreadable evidence, not a corrupt applicability.
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET expires_at = ? WHERE source_id = ?",
                               (encode_timestamp(self.clock.now() + timedelta(seconds=1)),
                                reference.source_id))
        self.clock.advance(timedelta(seconds=2))

        page = self.page(context)
        self.assertTrue(page.is_success, page.failure)
        item = page.unwrap()["items"][0]
        self.assertEqual(item["unavailable_reason"], "expression_sample_changed")
        self.assertEqual(item["observation"], OBSERVATION)
        self.assertEqual(item["expression"]["pattern"], "一句话说完")
        self.assertEqual(item["evidence"], [])


if __name__ == "__main__":
    unittest.main()
