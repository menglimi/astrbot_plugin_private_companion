"""Production Screen interpretation and the autonomous peek decisions.

The older Screen tests cover the injected-port pipeline and the device capture
port. These checks cover what the audit found missing: an adopted original now
reaches the background model profile, its interpretation is recorded with
explicit feedback anchors, the diary can read that day's journal anchor, and the
two V1 peek episodes (goodnight check and unanswered follow-up) only happen when
the window, the clock and the episode allow it.
"""

from __future__ import annotations

from datetime import timedelta
import base64
import hashlib
import json
import unittest

from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityRepository
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.foundation.refs import (AssetRef, AudienceRef, OperationRef, RoleOwnerRef, SourceRef, VersionRef)
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.model_gateway.models import Amount, ProviderBinding, LocalProvider, ProviderResponse
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.proactive.configuration import target_for as contact_target_for
from companion_v2.role_work.journal import JournalContext, JournalKey, JournalKind
from companion_v2.screen.configuration import GROUP as SCREEN_GROUP
from companion_v2.screen.interpretation import screen_interpretation_profile
from companion_v2.screen.models import (SCREEN_EXTENSION, SCREEN_PROCESS, SCREEN_READ, ClockQuality,
                                        ScreenCapture, ScreenCaptureRef, ScreenCoverage, ScreenMediaKind)
from companion_v2.screen.originals import ScreenImage
from companion_v2.screen.peek import KIND as PEEK_KIND
from companion_v2.storage.repositories import OperationRepository
from tests.g6_support import descriptor
from tests.proactive_support import ContactFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE
from tests.test_role_tool_loop import answered


INTERPRETATION = json.dumps({"interpretation": "画面里是一个代码编辑器窗口，看不到其他内容。"}, ensure_ascii=False)
# One-pixel PNG: the same decoder the live adopter uses validates the fixture body.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")


class ScreenChainFixture(ContactFixture):
    """A running application whose window may read and interpret Screen captures."""

    def setUp(self):
        super().setUp()
        self.profiles += (screen_interpretation_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="screen-chain.v1", fields=configuration_fields(self.profiles))
        # The interpretation profile needs a provider that can carry the original.
        self.providers = (LocalProvider(ProviderBinding("model.text", "primary", "1", "model-1"),
                                        ("text", "tools", "image"), 100, lambda envelope: Amount(100, 100),
                                        self.generate),)
        self.grant(self.important, SCOPE, SCREEN_READ, tier=AuthorizationTier.IMPORTANT)
        self.grant(self.important, SCOPE, SCREEN_PROCESS, tier=AuthorizationTier.IMPORTANT)
        self.grant(self.important, SCOPE, "extension.tool.invoke", tier=AuthorizationTier.IMPORTANT)
        capabilities = CapabilityRepository(self.runtime)
        selected = descriptor()
        capabilities.register_descriptor(selected, registered_at=NOW).unwrap()
        capabilities.acquire(extension_id=SCREEN_EXTENSION, package_id=selected.package_id,
                             package_version=selected.package_version, expected_revision=0, observed_at=NOW).unwrap()
        capabilities.set_role_enabled(extension_id=SCREEN_EXTENSION, role=ROLE, bot_id=SCOPE.bot_id,
                                      enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for capability in (SCREEN_READ, SCREEN_PROCESS):
            capabilities.set_authorization(extension_id=SCREEN_EXTENSION, capability_id=capability,
                subject=self.important.subject, scope=SCOPE, state=CapabilityAuthorizationState.ACTIVE,
                expected_revision=0, observed_at=NOW).unwrap()
        # A proactive anchor is refused unless this exact window allows sharing.
        capabilities.set_settings(extension_id=SCREEN_EXTENSION, scope=SCOPE, proactive_enabled=True,
                                  thought_weight=1.0, expected_revision=0, observed_at=NOW).unwrap()

    def make_core(self):
        """隐私能力现在只对核心管理员窗口默认可用。"""
        self.policy.set_tier(self.important.subject, AuthorizationTier.CORE,
                             expected_revision=self.policy.revision, observed_at=NOW)

    # -- fixtures ------------------------------------------------------

    def sql(self, statement, parameters=()):
        with self.runtime.transaction() as connection:
            connection.execute(statement, parameters)

    def window_settings(self, app, changes, *, group=SCREEN_GROUP):
        # Contact preferences belong to the resolved human relationship of this
        # window; the Screen group only needs the role and the scope.
        target = (contact_target_for(self.contact_context()) if group == "proactive.contact"
                  else ConfigTarget(INSTALLATION, ConfigLayer.WINDOW, ROLE, SCOPE))
        snapshot = app.configuration.read(self.important, target=target, group_id=group,
                                          observed_at=self.clock.now()).unwrap()
        return app.configuration.patch(self.important, target=target, group_id=group,
            expected_edit_token=snapshot.edit_token,
            changes={group + "." + key: value for key, value in changes.items()},
            operation=self.operation(target.authorization_scope, owner=group), observed_at=self.clock.now())

    def publish_capture(self, app, capture_id="screen-capture-fixture", *, units=("image.viewport", "window.title")):
        reference = ScreenCaptureRef(capture_id,
            SourceRef(f"screen-source-{capture_id}", "screen", SCOPE, VersionRef(f"screen-source-{capture_id}", 1),
                      NOW, NOW + timedelta(hours=1)),
            AssetRef(f"screen-asset-{capture_id}", "screen", SCOPE, VersionRef(f"screen-asset-{capture_id}", 1),
                     hashlib.sha256(PNG).hexdigest()))
        screens = app.screen_repository
        admitted = screens.admit_capture(reference, role=ROLE, resolved=self.important,
                                         observed_at=self.clock.now()).unwrap()
        capture = ScreenCapture(reference, "fixture-device", ScreenMediaKind.IMAGE,
            ScreenCoverage("fixture-window", units, (), ClockQuality.CLIENT_REPORTED), NOW, NOW, NOW)
        screens.publish_image(capture, ScreenImage("image/png", PNG),
            audience=AudienceRef(SCOPE, (self.important.subject,)), window_title="editor.py",
            role=ROLE, resolved=self.important, observed_at=self.clock.now(),
            expected_admission_stamp=admitted).unwrap()
        return capture

    def delivered_goodnight(self, at):
        batch = "batch-goodnight"
        self.sql("""INSERT INTO conversation_batches(batch_id, origin_key, context_json, role_instruction, state,
                revision, created_at, contributions_json, part_count)
            VALUES (?, ?, '{}', '原角色人格提示', 'ready', 1, ?, '{}', 1)""",
            (batch, SCOPE.key, stamp(at)))
        self.sql("""INSERT INTO conversation_parts(part_id, batch_id, sequence, text, payload_hash, state, delivered_at)
            VALUES ('part-goodnight', ?, 1, '晚安，做个好梦。', 'h', 'delivered', ?)""", (batch, stamp(at)))
        self.sql("""INSERT INTO conversation_flows(batch_id, route_key, merge_key, state, due_at, merge_until,
                deadline_at, policy_json, reason)
            VALUES (?, ?, 'merge', 'complete', ?, ?, ?, '{}', 'fixture')""",
            (batch, SCOPE.key, stamp(at), stamp(at), stamp(at)))
        return batch

    def user_returns(self, at):
        batch = "batch-later-input"
        self.sql("""INSERT INTO conversation_batches(batch_id, origin_key, context_json, role_instruction, state,
                revision, created_at, contributions_json, part_count)
            VALUES (?, ?, '{}', '原角色人格提示', 'ready', 1, ?, '{}', 0)""", (batch, SCOPE.key, stamp(at)))
        self.sql("""INSERT INTO conversation_flows(batch_id, route_key, merge_key, state, due_at, merge_until,
                deadline_at, policy_json, reason)
            VALUES (?, ?, 'merge', 'collecting', ?, ?, ?, '{}', 'fixture')""",
            (batch, SCOPE.key, stamp(at), stamp(at), stamp(at)))
        self.sql("""INSERT INTO conversation_inputs(event_key, batch_id, position, event_json, event_hash, withdrawn)
            VALUES ('later-input', ?, 1, '{}', 'h', 0)""", (batch,))

    def delivered_contact(self, at, purpose="chat"):
        context_key = self.contact_context().key
        operation = OperationRepository(self.runtime).reserve(
            OperationRef("operation-fixture-contact", "proactive.contact", SCOPE,
                         VersionRef("operation-fixture-contact", 1), "operation-fixture-contact"),
            parameter_hash="h", observed_at=at).unwrap()
        self.sql("""INSERT INTO proactive_contacts(contact_id, task_id, origin_key, record_json, body, payload_hash,
                operation_id, created_at)
            VALUES ('contact-1', 'contact-task-1', ?, ?, 'body', 'h', ?, ?)""",
            (context_key, json.dumps({"purpose": purpose}), operation.operation.operation_id, stamp(at)))
        self.sql("""INSERT INTO background_deliveries(delivery_id, owner, origin_key, request_json, payload_hash,
                sender_json, task_id, state, delivered_at)
            VALUES ('delivery-1', 'proactive.contact', ?, '{}', 'h', '{}', 'contact-task-1', 'delivered', ?)""",
            (context_key, stamp(at)))


class ScreenInterpretationTests(ScreenChainFixture, unittest.IsolatedAsyncioTestCase):
    async def test_adopted_original_is_interpreted_and_recorded_with_both_intents(self):
        async def interpret(envelope):
            return answered(INTERPRETATION)

        self.model_behavior = interpret
        host = await self.start()
        app = host.application
        self.window_settings(app, {"interpretation_enabled": True, "journal_material_enabled": True}).unwrap()
        capture = self.publish_capture(app)
        context = self.contact_context()

        original_instruction = "\n保持这个角色。\n"
        task_id = app.screen_interpretation.submit(context, capture.reference, purpose="reply",
                                                  role_instruction=original_instruction)
        self.assertIsNotNone(task_id)
        self.assertIsNone(app.screen_interpretation.submit(context, capture.reference, purpose="reply"))

        await self.until(lambda: self._anchors() == 2)
        rows = self.runtime.read_all("SELECT feedback_kind FROM screen_feedback_anchors ORDER BY feedback_kind", ())
        self.assertEqual([row["feedback_kind"] for row in rows], ["journal", "reply"])
        analysis = self.runtime.read_one("SELECT payload_json, purpose FROM screen_analyses", ())
        self.assertEqual(analysis["purpose"], "reply")
        self.assertIn("代码编辑器", json.loads(analysis["payload_json"]))
        request = self.called[-1].original
        self.assertEqual(request.role_instruction, original_instruction)
        self.assertEqual(len(request.images), 1)
        self.assertEqual(request.images[0].source.owner, "screen")
        self.assertEqual(request.output_format, "screen_interpretation")

        owner = RoleOwnerRef(INSTALLATION, SCOPE.bot_id, ROLE.subject_id)
        target = JournalContext(JournalKey(owner, JournalKind.DIARY, NOW.date().isoformat()), SCOPE, "Asia/Shanghai")
        page = app.journal_screen_material_producer.for_day(context, target)
        self.assertEqual(len(page.materials), 1)
        self.assertIn("代码编辑器", page.materials[0].text)

        latest = app.screen_repository.latest_analysis(scope=context.scope, resolved=context.actor,
                                                       observed_at=self.clock.now()).unwrap()
        self.assertEqual(len(latest), 1)
        self.assertIn("代码编辑器", latest[0].analysis.interpretation)
        CapabilityRepository(self.runtime).set_authorization(extension_id=SCREEN_EXTENSION,
            capability_id=SCREEN_READ, subject=self.important.subject, scope=SCOPE,
            state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
            observed_at=self.clock.now()).unwrap()
        withdrawn = app.screen_repository.latest_analysis(scope=context.scope, resolved=context.actor,
                                                          observed_at=self.clock.now())
        self.assertEqual(withdrawn.failure.code, "capability_consent_revoked")

    async def test_confirmed_model_not_started_is_failed_while_unknown_stays_uncertain(self):
        async def not_started(envelope):
            return ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref="model:not-started")

        self.model_behavior = not_started
        app = (await self.start()).application
        self.window_settings(app, {"interpretation_enabled": True}).unwrap()
        context = self.contact_context()
        for capture_id, behavior, expected_state in (
            ("not-started", not_started, "failed"),
            ("unknown", None, "uncertain"),
        ):
            async def unknown(envelope):
                return ProviderResponse("unknown")
            self.model_behavior = behavior or unknown
            capture = self.publish_capture(app, capture_id)
            task_id = app.screen_interpretation.submit(context, capture.reference, purpose="reply",
                                                      role_instruction="保持这个角色。")
            await self.until(lambda: app.tasks.read(context, task_id).unwrap().state == expected_state)
            task = app.tasks.read(context, task_id).unwrap()
            if expected_state == "failed":
                row = self.runtime.read_one("SELECT record_json FROM execution_tasks WHERE task_id = ?", (task_id,))
                self.assertEqual((task.effect, json.loads(row["record_json"])["receipt_ref"]),
                                 ("not_committed", "model:not-started"))
                self.assertEqual(app.screen_interpretation.error_codes[task_id], "model_not_started")
                app.screen_interpretation.advance()
                self.assertNotIn(task_id, app.screen_interpretation.pending)
            self.assertIsNone(app.screen_interpretation.submit(context, capture.reference, purpose="reply"))
        self.assertEqual(self._anchors(), 0)

    async def test_authorized_screen_producer_validates_and_rejects_revocation(self):
        self.make_core()

        async def interpret(envelope):
            return answered(INTERPRETATION)

        self.model_behavior = interpret
        app = (await self.start()).application
        self.window_settings(app, {"interpretation_enabled": True}).unwrap()
        self.settings(app, {"enabled": True, "automatic_enabled": True, "screen.enabled": True,
                            "screen_glance_enabled": True,
                            "chat.enabled": False}).unwrap()
        context = self.contact_context()
        capture = self.publish_capture(app)
        self.assertIsNotNone(app.screen_interpretation.submit(
            context, capture.reference, purpose="glance", role_instruction="保持这个角色。"))
        await self.until(lambda: self._anchors() == 1)

        preferences = self.preferences(app, context)
        events = app.contact_schedule.screen_events
        candidate = events.select(context, preferences, self.clock.now())
        self.assertIsNotNone(candidate)
        _opportunity, intents, marker = candidate
        self.assertEqual(intents[0].purpose, "screen")
        events.validate(context, marker, self.clock.now())
        source = intents[0].thought.evidence.sources[0]
        self.assertTrue(app.proactive.sources.validate_reference_for_audience(
            source, recipient=context.actor.subject, observed_at=self.clock.now()).is_success)

        CapabilityRepository(self.runtime).set_authorization(extension_id=SCREEN_EXTENSION,
            capability_id=SCREEN_READ, subject=self.important.subject, scope=SCOPE,
            state=CapabilityAuthorizationState.REVOKED, expected_revision=1,
            observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError):
            events.validate(context, marker, self.clock.now())

    async def test_screen_observation_reaches_contact_composition_and_confirmed_delivery(self):
        self.make_core()

        async def respond(envelope):
            if envelope.original.output_format == "screen_interpretation":
                return answered(INTERPRETATION)
            if envelope.original.output_format == "proactive_contact":
                return answered('{"decision":"send","text":"看起来你还在查看代码编辑器。"}')
            return answered("收到。")

        self.model_behavior = respond
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.window_settings(app, {"interpretation_enabled": True}).unwrap()
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 0,
                            "screen.enabled": True, "screen_glance_enabled": True,
                            "chat.enabled": False}).unwrap()
        context = self.contact_context()
        capture = self.publish_capture(app)
        self.assertIsNotNone(app.screen_interpretation.submit(
            context, capture.reference, purpose="glance", role_instruction="保持这个角色。"))
        await self.until(lambda: self._anchors() == 1)

        batch = host.receive(self.event("screen-contact-input", text="我准备继续处理代码。")).unwrap()
        await self.done(app, batch)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""",
            (context.scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan, app.runtime.read_all("""SELECT task_id, kind, state FROM execution_tasks
            WHERE kind LIKE 'proactive.%' ORDER BY created_at DESC""", ()))
        self.assertIn("screen_event", plan[4])
        self.assertEqual([intent.purpose for intent in plan[2]], ["screen"])
        self.assertTrue(any(source.owner == "proactive.screen" for source in plan[2][0].thought.evidence.sources))

        await app.contact_schedule._run(plan)
        contact = app.runtime.read_one("SELECT * FROM proactive_contacts WHERE origin_key = ?",
                                       (context.key,))
        self.assertIsNotNone(contact)
        self.assertEqual(json.loads(contact["record_json"])["purpose"], "screen")
        await self.delivery_state(app, contact["task_id"], "delivered")
        compose = [call.original for call in self.called if call.original.output_format == "proactive_contact"]
        self.assertEqual(len(compose), 1)
        evidence = [item.payload for item in compose[0].context.materials if item.purpose == "contact_evidence"]
        self.assertTrue(any("代码编辑器" in item for item in evidence))
        self.assertEqual(app.proactive.observe(context, contact["task_id"]).unwrap()["delivery"], "delivered")

    async def test_disabled_window_never_calls_the_model(self):
        host = await self.start()
        app = host.application
        capture = self.publish_capture(app, "screen-capture-off")
        self.assertIsNone(app.screen_interpretation.submit(self.contact_context(), capture.reference, purpose="reply"))
        self.assertEqual(self._anchors(), 0)
        self.assertEqual(self.called, [])

    def _anchors(self):
        return self.runtime.read_one("SELECT COUNT(*) AS n FROM screen_feedback_anchors", ())["n"]


class ScreenPeekDecisionTests(ScreenChainFixture, unittest.IsolatedAsyncioTestCase):
    async def test_goodnight_peek_waits_and_cancels_when_the_user_returns(self):
        self.make_core()
        host = await self.start()
        app = host.application
        self.window_settings(app, {"interpretation_enabled": True}).unwrap()
        self.settings(app, {"enabled": True, "automatic_enabled": True, "screen_glance_enabled": True,
                            "screen_glance_goodnight_enabled": True}).unwrap()
        context = self.contact_context()
        self.delivered_goodnight(self.clock.now())
        events = app.contact_schedule.screen_events

        self.assertIsNone(events.peek_due(context, self.preferences(app, context), self.clock.now()))
        self.advance(app, 46 * 60)
        decision = events.peek_due(context, self.preferences(app, context), self.clock.now())
        self.assertEqual(decision["purpose"], "goodnight")
        self.assertTrue(decision["task_id"].startswith("screen-peek-goodnight:"))
        self.user_returns(self.clock.now())
        self.assertIsNone(events.peek_due(context, self.preferences(app, context), self.clock.now()))

    async def test_followup_peek_needs_silence_and_keeps_its_own_cooldown(self):
        self.make_core()
        host = await self.start()
        app = host.application
        self.window_settings(app, {"interpretation_enabled": True}).unwrap()
        self.settings(app, {"enabled": True, "automatic_enabled": True, "screen_glance_enabled": True,
                            "screen_glance_followup_enabled": True}).unwrap()
        context = self.contact_context()
        events = app.contact_schedule.screen_events
        self.delivered_contact(self.clock.now() - timedelta(hours=2))

        decision = events.peek_due(context, self.preferences(app, context), self.clock.now())
        self.assertEqual(decision["purpose"], "followup")
        self.sql("""INSERT INTO execution_tasks(task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                state, generation, attempt_no, next_run_at, deadline_at, created_at, updated_at, record_json)
            VALUES (?, ?, ?, 'background', ?, 'h', 1, 'created', 1, 0, ?, ?, ?, ?, '{}')""",
            (decision["task_id"], context.key, PEEK_KIND, decision["task_id"], stamp(self.clock.now()),
             stamp(self.clock.now() + timedelta(minutes=1)), stamp(self.clock.now()), stamp(self.clock.now())))
        self.assertIsNone(events.peek_due(context, self.preferences(app, context), self.clock.now()))


__all__ = ["PNG", "ScreenChainFixture", "ScreenInterpretationTests", "ScreenPeekDecisionTests"]
