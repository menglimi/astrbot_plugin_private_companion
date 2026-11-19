from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.conversation.diagnostic_tools import (
    DiagnosticRoleSession, diagnostic_query, diagnostic_tool_specs, proposal_document,
)
from companion_v2.conversation.foreground import ClaimKind, DraftClaim, EvidencePromptCompiler, ForegroundContextAssembler, ModelDraft, PromptMaterial
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.diagnostics.models import DiagnosticError
from companion_v2.foundation.refs import SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from tests.test_diagnostics import DiagnosticFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE


class RecordingDiagnosticModel:
    def __init__(self, during=None):
        self.requests = []
        self.during = during

    def generate(self, request):
        self.requests.append(request)
        if self.during:
            self.during()
        return ModelDraft("我看过这次记录了，先把不确定的地方留着。", (DraftClaim("expression", ClaimKind.SUBJECTIVE, "角色表达"),))


class DiagnosticToolTests(DiagnosticFixture):
    def session(self, *, group=False, event_id="diagnostic-event"):
        context = self.context(self.important, group=group)
        self.grant(self.important, context.scope, "conversation.read", tier=AuthorizationTier.STANDARD)
        reference = SourceRef(event_id, "conversation", context.scope, VersionRef(event_id, 1), NOW, NOW + timedelta(minutes=10))
        sources = SourceRepository(self.runtime)
        record = SourceRecord(reference, context.audience, "question-hash", "看看配置为什么没有生效")
        sources.publish(record, observed_at=NOW).unwrap()
        event = InboundConversationEvent(event_id, self.important.principal, context.scope, reference,
                                          "conversation.read", "question-hash", NOW)
        prompt = EvidencePromptCompiler().compile(event=event, role_instruction="你是原角色，沿用当前关系与语气。",
                    role_binding=context.role_binding, context=ForegroundContextAssembler().seal(primary=record))
        return DiagnosticRoleSession(self.diagnostics, request=prompt, audience=context.audience, sources=sources)

    def test_one_original_role_generation_uses_actual_redacted_material_and_has_no_effects(self):
        session = self.session()
        session.inspect(self.query).unwrap()
        before = self.counts()
        model = RecordingDiagnosticModel()
        session.continue_role(model).unwrap()
        self.assertEqual(len(model.requests), 1)
        request = model.requests[0]
        self.assertEqual((request.role_instruction, request.role_binding, request.event),
                         (session.request.role_instruction, session.request.role_binding, session.request.event))
        result = json.loads(request.context.materials[-1].payload)["reports"][0]["results"][0]
        self.assertEqual(result["code"], "consumer_not_started")
        self.assertIn("不能说成正常或成功", request.instruction)
        self.assertIn("不要求固定五段", request.instruction)
        self.assertEqual(self.counts(), before)

    def test_changed_configuration_during_model_generation_is_not_used_for_answer(self):
        session = self.session()
        session.inspect(self.query).unwrap()
        model = RecordingDiagnosticModel(lambda: self.write(self.important, self.read(self.important).unwrap()).unwrap())
        self.assertEqual(session.continue_role(model).failure.code, "diagnostic_evidence_changed")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.counts()[2], 0)

    def test_expiry_and_cancellation_stop_generation_or_completed_draft(self):
        session = self.session()
        session.inspect(self.query).unwrap()
        self.clock.advance(timedelta(minutes=1))
        model = RecordingDiagnosticModel()
        self.assertEqual(session.continue_role(model).failure.code, "diagnostic_report_expired")
        self.assertEqual(model.requests, [])
        session.inspect(self.query).unwrap()
        # A new turn's report is required; expired original reports are never swapped.
        newer = self.session(event_id="new-event")
        newer.inspect(self.query).unwrap()
        model = RecordingDiagnosticModel(newer.cancel)
        self.assertEqual(newer.continue_role(model).failure.code, "role_turn_cancelled")

    def test_group_can_explain_but_cannot_receive_private_runtime_diagnostics(self):
        session = self.session(group=True)
        self.assertEqual(session.inspect(self.query).failure.code, "private_result_required")
        session.explain("configuration").unwrap()
        model = RecordingDiagnosticModel()
        session.continue_role(model).unwrap()
        report = json.loads(model.requests[0].context.materials[-1].payload)["reports"][0]
        self.assertEqual((report["mode"], report["results"]), ("explain", []))

    def test_group_cannot_gain_private_context_through_explanation(self):
        private = self.session(event_id="private-source")
        group = self.session(group=True, event_id="group-source")
        group.explain("configuration").unwrap()
        group.request = replace(group.request, context=replace(group.request.context, materials=(
            *group.request.context.materials, PromptMaterial("memory", "private", "private-body", private.request.event.source))))
        model = RecordingDiagnosticModel()
        self.assertEqual(group.continue_role(model).failure.code, "tool_material_audience_mismatch")
        self.assertEqual(model.requests, [])

    def test_role_change_during_generation_and_old_prompt_reconstruction_are_fenced(self):
        session = self.session()
        session.inspect(self.query).unwrap()
        model = RecordingDiagnosticModel(lambda: self.identity.bind_role(scope=SCOPE,
                    role=SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b"),
                    expected_revision=session.context.role_binding.revision, observed_at=NOW))
        self.assertEqual(session.continue_role(model).failure.code, "role_binding_changed")
        with self.assertRaises(DiagnosticError):
            DiagnosticRoleSession(self.diagnostics, request=session.request, audience=session.audience, sources=session.sources)

    def test_answering_about_proposal_does_not_confirm_or_apply_it(self):
        session = self.session()
        report = session.inspect(self.query).unwrap()
        proposal = session.repairs.propose(session.context, proposal_id="review", report_id=report.report_id,
                         changes={"output.enabled": False}, rationale="待用户确认后观察").unwrap()
        session.attach_proposal(proposal.proposal_id).unwrap()
        model = RecordingDiagnosticModel()
        session.continue_role(model).unwrap()
        material = json.loads(model.requests[0].context.materials[-1].payload)
        self.assertEqual(material["repair_proposals"][0]["state"], "pending")
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_diagnostic_selectors_bind_to_role_or_current_window_and_reject_extra_scope(self):
        session = self.session()
        role_query = diagnostic_query(session.context,
            {"check": "configuration", "group_id": "output"}, self.diagnostics, self.clock.now())
        self.assertEqual(role_query.target.layer.value, "role")
        self.assertIsNone(role_query.target.scope)
        window_query = diagnostic_query(session.context,
            {"check": "configuration", "group_id": "output", "configuration_target": "current_window"},
            self.diagnostics, self.clock.now())
        self.assertEqual(window_query.target.scope, session.context.scope)
        with self.assertRaises(DiagnosticError):
            diagnostic_query(session.context, {"check": "storage", "scope": "other"},
                             self.diagnostics, self.clock.now())
        operation = diagnostic_query(session.context, {"check": "operation", "operation_id": "exact-operation",
            "window_minutes": 15}, self.diagnostics, self.clock.now())
        self.assertEqual(operation.operation_scope, session.context.scope)
        self.assertEqual(operation.window.end, self.clock.now())
        self.assertEqual(operation.window.end - operation.window.start, timedelta(minutes=15))

    def test_created_proposal_is_readable_but_never_exposes_its_confirmation_token(self):
        session = self.session()
        report = session.inspect(self.query).unwrap()
        proposal = session.create_proposal(proposal_id="chat-review", report_id=report.report_id,
            changes={"output.enabled": False}, rationale="检查显示当前消费者尚未采用，先关闭输出观察。").unwrap()
        exposed = proposal_document(proposal)
        self.assertEqual(exposed["state"], "pending")
        self.assertIn("delta", exposed["review"])
        self.assertNotIn("review_token", exposed)
        session.read_proposal(proposal.proposal_id).unwrap()
        model = RecordingDiagnosticModel()
        session.continue_role(model).unwrap()
        material = model.requests[0].context.materials[-1].payload
        self.assertNotIn(proposal.review_token, material)
        self.assertNotIn("review_token", material)
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_inspect_schema_describes_branch_requirements_without_union_schemas(self):
        spec = next(spec for spec in diagnostic_tool_specs(self.diagnostics) if spec.action == "diagnostics.inspect")
        schema = json.loads(spec.parameters_json)
        self.assertEqual(schema["required"], ["check"])
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("oneOf", spec.parameters_json)
        self.assertNotIn("anyOf", spec.parameters_json)
        for field, words in {
            "group_id": ("Required", "configuration"),
            "extension_id": ("Required", "capability"),
            "capability_id": ("Optional", "extension_id"),
            "operation_id": ("Required", "operation"),
            "task_id": ("Optional", "execution"),
            "window_minutes": ("operation", "60"),
        }.items():
            with self.subTest(field=field):
                description = schema["properties"][field]["description"]
                for word in words:
                    self.assertIn(word, description)

    def test_inspect_distinguishes_missing_inapplicable_and_unknown_fields(self):
        context = self.session().context
        cases = (
            ({"check": "configuration"}, "group_id is required"),
            ({"check": "capability", "capability_id": "image.generate"}, "extension_id is required"),
            ({"check": "operation"}, "operation_id is required"),
            ({"check": "execution", "window_minutes": 60}, "window_minutes is not applicable to execution"),
            ({"check": "storage", "extension_id": "image"}, "extension_id is not applicable to storage"),
            ({"check": "storage", "private-secret-name": "private-secret-value"}, "unknown field"),
            ({"check": "configuration", "group_id": []}, "registered group"),
        )
        for arguments, reason in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(DiagnosticError) as failure:
                    diagnostic_query(context, arguments, self.diagnostics, self.clock.now())
                self.assertEqual(failure.exception.code, "invalid_tool_arguments")
                self.assertIn(reason, str(failure.exception))
                self.assertNotIn("private-secret", str(failure.exception))

    def test_inspect_rejects_malformed_identifiers_at_the_tool_boundary(self):
        context = self.session().context
        cases = (
            ("capability", "extension_id", 128, {}),
            ("capability", "capability_id", 128, {"extension_id": "image"}),
            ("execution", "task_id", 512, {}),
            ("operation", "operation_id", 512, {}),
        )
        for check, field, maximum, other in cases:
            for value in (None, [], {}, False, 1, "", " ", " padded", "padded ", "x" * (maximum + 1)):
                with self.subTest(check=check, field=field, value=value):
                    with self.assertRaises(DiagnosticError) as failure:
                        diagnostic_query(context, {"check": check, field: value, **other},
                                         self.diagnostics, self.clock.now())
                    self.assertEqual(failure.exception.code, "invalid_tool_arguments")
                    self.assertIn(field, str(failure.exception))
            query = diagnostic_query(context, {"check": check, field: "x" * maximum, **other},
                                     self.diagnostics, self.clock.now())
            attribute = "execution_task_id" if field == "task_id" else field
            self.assertEqual(getattr(query, attribute), "x" * maximum)

    def test_inspect_valid_branches_keep_defaults_and_operation_window_bounds(self):
        context = self.session().context
        self.assertTrue(diagnostic_query(context, {"check": "storage"}, self.diagnostics, self.clock.now()).storage)
        execution = diagnostic_query(context, {"check": "execution"}, self.diagnostics, self.clock.now())
        self.assertTrue(execution.execution)
        self.assertIsNone(execution.execution_task_id)
        capability = diagnostic_query(context, {"check": "capability", "extension_id": "image"},
                                      self.diagnostics, self.clock.now())
        self.assertEqual(capability.extension_id, "image")
        self.assertIsNone(capability.capability_id)
        for minutes in (None, 1, 1440):
            arguments = {"check": "operation", "operation_id": "exact-operation"}
            if minutes is not None:
                arguments["window_minutes"] = minutes
            query = diagnostic_query(context, arguments, self.diagnostics, self.clock.now())
            self.assertEqual(query.window.end - query.window.start, timedelta(minutes=minutes or 60))
            self.assertEqual(query.operation_scope, context.scope)
        for minutes in (None, True, 0, 1441, "60", 1.5):
            with self.subTest(minutes=minutes), self.assertRaises(DiagnosticError) as failure:
                diagnostic_query(context, {"check": "operation", "operation_id": "exact-operation",
                    "window_minutes": minutes}, self.diagnostics, self.clock.now())
            self.assertEqual(failure.exception.code, "invalid_tool_arguments")


if __name__ == "__main__":
    unittest.main()
