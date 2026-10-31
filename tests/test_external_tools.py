from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from types import SimpleNamespace

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityPackageDescriptor
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.external_tools import (
    ExtensionToolDescriptor, ExternalToolExecutor, ExternalToolProvider, ExternalToolResponse,
)
from companion_v2.conversation.foreground import ClaimKind, DraftClaim
from companion_v2.conversation.tool_loop import LocalRoleTools
from companion_v2.execution.models import Lane, ProviderBinding, SideEffect, TaskError, digest, encode
from companion_v2.execution.runner import TaskRun
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.model_gateway.models import ToolCall, ToolSpec
from companion_v2.model_gateway.routing import ModelRouter
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.sources.models import SourceRecord
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW
from tests.test_role_tool_loop import answered, requested


PACKAGE = CapabilityPackageDescriptor("companion.device", "device-package", "publisher", "1", "hash-1",
                                      ("device.read", "device.play"), ("device",))


def descriptor(effectful=False):
    action = "device.play" if effectful else "device.read"
    return ExtensionToolDescriptor(ToolSpec(action, "Use the authorized device.", encode({
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        "additionalProperties": False,
    })), PACKAGE, ProviderBinding(action, "device-provider", "1", "host-generation-1"), "device",
        SideEffect.EXTERNAL_DEVICE if effectful else SideEffect.NONE, timeout_seconds=0.08)


def tool_call(identifier="read-device", *, effectful=False, **arguments):
    return ToolCall(identifier, "device.play" if effectful else "device.read", encode(arguments or {"query": "status"}))


class ExternalFixture(MessageFixture):
    def setUp(self):
        self.descriptors = (descriptor(), descriptor(True))
        self.extension_calls = []
        self.extension_behavior = None
        self.bindings = {d.tool.action: d.binding for d in self.descriptors}
        super().setUp()
        self.providers = tuple(replace(p, capabilities=("text", "tools")) for p in self.providers)
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=2)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)
        self.generator.gateway = self.gateway
        self.tools = LocalRoleTools(self.messages, external=self.extension)
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(PACKAGE, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=PACKAGE.extension_id, package_id=PACKAGE.package_id,
            package_version=PACKAGE.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.role_enabled(True, 0)
        for capability in PACKAGE.capabilities:
            self.consent(capability, CapabilityAuthorizationState.ACTIVE, 0)
        self.capabilities.set_settings(extension_id=PACKAGE.extension_id, scope=self.context.scope,
            proactive_enabled=False, thought_weight=73, expected_revision=0, observed_at=NOW).unwrap()

    def make_extension(self, runtime):
        providers = tuple(ExternalToolProvider(d, self.invoke,
            lambda action=d.tool.action: self.bindings.get(action)) for d in self.descriptors)
        return ExternalToolExecutor(runtime, providers)

    def components(self, runtime, *, takeover=False):
        extension = self.make_extension(runtime)
        original = self.registrations
        self.registrations += extension.registrations
        try:
            tasks, messages, delivery = super().components(runtime, takeover=takeover)
        finally:
            self.registrations = original
        extension.attach(messages)
        self.addAsyncCleanup(extension.close)
        self.extension = extension
        return tasks, messages, delivery

    def role_enabled(self, enabled, revision):
        return self.capabilities.set_role_enabled(extension_id=PACKAGE.extension_id, role=self.context.role_binding.role,
            bot_id=self.context.scope.bot_id, enabled=enabled, expected_revision=revision, observed_at=NOW).unwrap()

    def consent(self, capability, state, revision):
        return self.capabilities.set_authorization(extension_id=PACKAGE.extension_id, capability_id=capability,
            subject=self.context.actor.subject, scope=self.context.scope, state=state,
            expected_revision=revision, observed_at=NOW).unwrap()

    def response(self, invocation, *, state="succeeded", payload="Phone battery 57%; companion app in background.",
                 expires_at=None, **changes):
        effectful = invocation.descriptor.side_effect is not SideEffect.NONE
        source = None
        if state == "succeeded":
            reference = SourceRef("source-" + invocation.invocation_id, "device", invocation.context.scope,
                                  VersionRef(invocation.invocation_id, 1), self.clock.now(), expires_at)
            source = SourceRecord(reference, invocation.context.audience, digest(payload), payload)
        effect = {"succeeded": "committed", "failed": "not_committed", "unknown": "unknown"}[state] if effectful else "none"
        return replace(ExternalToolResponse(invocation.invocation_id, invocation.context.key, invocation.arguments_hash,
            invocation.descriptor.binding, state, effect, source,
            "op-" + invocation.invocation_id if effectful and state == "succeeded" else None,
            "receipt-" + invocation.invocation_id if state != "unknown" else None), **changes)

    async def invoke(self, invocation):
        self.extension_calls.append(invocation)
        if self.extension_behavior:
            return await self.extension_behavior(invocation)
        return self.response(invocation)

    def rows(self):
        return self.runtime.read_all("SELECT * FROM extension_tool_invocations ORDER BY created_at, invocation_id", ())

    def model_uses(self, call=None):
        call = call or tool_call()
        async def respond(envelope):
            return requested(call) if not envelope.tool_history else answered("Read the actual report.")
        self.responses["primary"] = respond

    async def stage(self, *calls):
        batch = self.accept()
        task = self.messages.seal(self.context, batch, deadline_at=self.deadline).unwrap()
        claim = self.tasks.claim_task(self.context, task.task_id).unwrap().claim
        task = self.tasks.begin(claim).unwrap()
        run = TaskRun(self.tasks, claim, task)
        request = self.messages.request(run)
        session = self.gateway.open_session(run, request, tools=self.tools.catalog_for(self.context))
        self.responses["primary"] = requested(*(calls or (tool_call(),)))
        completion = await session.generate(request)
        return batch, run, completion


class ExternalToolTests(ExternalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_group_catalog_filter_audit_is_bounded_and_uses_codes_only(self):
        group_context = SimpleNamespace(scope=SimpleNamespace(kind=ScopeKind.GROUP))

        offered, filtered = self.extension.catalog_with_diagnostics(group_context)

        self.assertEqual(offered, ())
        self.assertEqual([item["action"] for item in filtered], ["device.read", "device.play"])
        self.assertEqual({item["reason"] for item in filtered}, {"not_supported_for_group"})
        self.assertTrue(all(set(item) == {"action", "reason"} for item in filtered))
        self.assertLessEqual(len(filtered), 128)

    async def test_read_governed_source_continues_original_role_ignoring_proactive_weight(self):
        async def respond(envelope):
            self.assertIn("device.read", {tool.action for tool in envelope.tools})
            if not envelope.tool_history:
                return requested(tool_call())
            source = next(m.source for m in envelope.original.context.materials if m.owner == "device")
            return answered("57%, and the companion app was in the background.",
                            (DraftClaim("phone", ClaimKind.OBSERVED, "Actual report", evidence=source),))
        self.responses["primary"] = respond
        batch = await self.generate()
        row = self.rows()[0]
        self.assertEqual((row["state"], row["effect"]), ("succeeded", "none"))
        self.assertEqual(self.tasks.read(self.context, row["task_id"]).unwrap().state, "succeeded")
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].context, self.context)
        for _, envelope in self.called:
            self.assertEqual(envelope.original.role_instruction, "Original role prompt")
            self.assertEqual(envelope.original.role_binding, self.context.role_binding)
        await self.deliver_next(batch)
        self.assertTrue(self.history()[0]["body_available"])
        observed = self.messages.observe(self.context, batch).unwrap()["tool_calls"][0]
        self.assertEqual((observed["child_state"], observed["has_owner_receipt"]), ("succeeded", True))
        self.assertNotIn("Phone battery", encode(observed))
        self.assertNotIn("Phone battery", self.runtime.read_one("SELECT result_json FROM conversation_tool_calls", ())["result_json"])

    async def test_committed_effect_reuses_receipt_for_duplicate_call_without_redispatch(self):
        async def respond(envelope):
            return requested(tool_call(effectful=True)) if len(envelope.tool_history) < 2 else answered("The device confirmed.")
        self.responses["primary"] = respond
        await self.generate()
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("succeeded", "committed"))
        self.assertIsNotNone(self.rows()[0]["operation_id"])
        self.assertEqual(self.called[-1][1].tool_history[0], self.called[-1][1].tool_history[1])

    async def test_invalid_arguments_and_identity_injection_never_dispatch(self):
        _, run, completion = await self.stage(tool_call("extra", query="x", scope="other"), tool_call("type", query=7))
        for call in completion.tool_calls:
            exchange = await self.tools.execute_async(run, completion.call_id, call)
            self.assertEqual(json.loads(exchange.result_json)["code"], "invalid_tool_arguments")
        self.assertEqual(self.extension_calls, [])
        self.assertEqual(self.rows(), ())

    async def test_not_started_owner_receipt_proves_failure_without_retry(self):
        async def refuse(invocation):
            return self.response(invocation, state="failed")
        self.extension_behavior = refuse
        self.model_uses(tool_call(effectful=True))
        await self.generate()
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("failed", "not_committed"))
        self.assertEqual(len(self.extension_calls), 1)

    async def test_effect_timeout_and_changed_call_id_do_not_replay_unknown(self):
        async def hang(invocation):
            await asyncio.Event().wait()
        self.extension_behavior = hang
        async def respond(envelope):
            if len(envelope.tool_history) < 2:
                return requested(tool_call("call-" + str(len(envelope.tool_history)), effectful=True))
            return answered("The outcome is unknown.")
        self.responses["primary"] = respond
        await self.generate()
        self.assertEqual(len(self.extension_calls), 1)
        row = self.rows()[0]
        self.assertEqual((row["state"], row["effect"]), ("unknown", "unknown"))
        self.assertEqual(self.tasks.read(self.context, row["task_id"]).unwrap().state, "uncertain")
        self.assertEqual(json.loads(self.called[-1][1].tool_history[-1].result_json)["code"], "external_effect_unresolved")

    async def test_read_timeout_and_provider_exception_stay_unknown(self):
        async def broken(invocation):
            raise RuntimeError("private provider error")
        self.extension_behavior = broken
        self.model_uses()
        batch = await self.generate()
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("unknown", "none"))
        self.assertNotIn("private provider error", encode(self.messages.observe(self.context, batch).unwrap()))
        async def hang(invocation):
            await asyncio.Event().wait()
        self.extension_behavior = hang
        await self.generate(self.event("timeout-read"))
        self.assertEqual(len(self.extension_calls), 2)
        self.assertTrue(all(row["state"] == "unknown" for row in self.rows()))

    async def test_cancellation_stops_child_and_duplicate_call_returns_unknown(self):
        entered, stopped = asyncio.Event(), asyncio.Event()
        async def hang(invocation):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.extension_behavior = hang
        _, run, completion = await self.stage(tool_call(effectful=True))
        call = completion.tool_calls[0]
        worker = asyncio.create_task(self.tools.execute_async(run, completion.call_id, call))
        await asyncio.wait_for(entered.wait(), 1)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assertTrue(stopped.is_set())
        replay = await self.tools.execute_async(run, completion.call_id, call)
        self.assertEqual(json.loads(replay.result_json)["state"], "unknown")
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension.runner.active_handles, ())

    async def test_late_receipt_keeps_slot_until_worker_stops_and_requires_explicit_reconciliation(self):
        release, cancelled = asyncio.Event(), asyncio.Event()
        async def late(invocation):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return self.response(invocation)
        self.extension_behavior = late
        self.extension.runner.stop_grace = 0.005
        _, run, completion = await self.stage(tool_call(effectful=True))
        call = completion.tool_calls[0]
        result = await self.tools.execute_async(run, completion.call_id, call)
        self.assertEqual(json.loads(result.result_json)["state"], "unknown")
        self.assertTrue(cancelled.is_set())
        row = self.rows()[0]
        self.assertTrue(self.tasks.observe(self.context, row["task_id"]).unwrap()["slot_held"])
        worker = next(iter(self.extension.runner._handles.values()))[1]
        release.set()
        await asyncio.wait_for(worker, 1)
        await asyncio.sleep(0)
        self.assertFalse(self.tasks.observe(self.context, row["task_id"]).unwrap()["slot_held"])
        self.assertEqual(self.tasks.read(self.context, row["task_id"]).unwrap().state, "uncertain")
        with self.assertRaises(TaskError) as failure:
            await self.tools.execute_async(run, completion.call_id, call)
        self.assertEqual(failure.exception.code, "external_task_unresolved")
        self.assertEqual(self.extension.reconcile(self.context, row["invocation_id"]).unwrap().state, "succeeded")
        replay = await self.tools.execute_async(run, completion.call_id, call)
        self.assertEqual(json.loads(replay.result_json)["effect"], "committed")
        self.assertEqual(len(self.extension_calls), 1)

    async def test_capacity_exhaustion_retires_child_without_calling_provider(self):
        self.running("occupied", kind="fixture.read")
        self.model_uses(tool_call(effectful=True))
        await self.generate()
        row = self.rows()[0]
        self.assertEqual((row["state"], row["effect"], row["error_code"]),
                         ("failed", "not_committed", "external_capacity_unavailable"))
        self.assertEqual(self.extension_calls, [])
        child = self.tasks.read(self.context, row["task_id"]).unwrap()
        self.assertEqual((child.state, child.attempt_no), ("cancelled", 0))

    async def test_exact_child_does_not_run_unrelated_queue_item(self):
        batch, run, completion = await self.stage()
        self.submit("aaa-unrelated")
        await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        self.assertEqual(self.tasks.read(self.context, "aaa-unrelated").unwrap().state, "created")
        self.assertEqual(len(self.extension_calls), 1)

    async def test_restart_reuses_original_source_and_receipt_and_missing_owner_blocks(self):
        self.model_uses(tool_call(effectful=True))
        batch = await self.generate()
        runtime = self.open_runtime()
        _, messages, delivery = self.components(runtime)
        LocalRoleTools(messages, external=self.extension)
        delivery.queue_next(self.context, batch, deadline_at=self.deadline).unwrap()
        self.assertEqual(len(self.extension_calls), 1)
        runtime2 = self.open_runtime()
        _, messages2, delivery2 = self.components(runtime2)
        LocalRoleTools(messages2)
        self.assertEqual(delivery2.queue_next(self.context, batch, deadline_at=self.deadline).failure.code,
                         "tool_owner_unavailable")

    async def test_provider_generation_change_remaps_instead_of_filtering(self):
        self.model_uses()
        batch = await self.generate()
        changed = replace(self.bindings["device.read"], version="2", generation="host-generation-2")
        self.bindings["device.read"] = changed
        self.assertIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
        await self.deliver_next(batch)
        self.assertEqual(len(self.extension_calls), 1)

    async def test_binding_change_is_remapped_once_and_dispatch_uses_the_current_binding(self):
        _, run, completion = await self.stage()
        changed = replace(self.bindings["device.read"], version="2", generation="host-generation-2")
        self.bindings["device.read"] = changed
        with self.assertLogs("companion_v2.conversation.external_tools", level="WARNING") as logs:
            self.assertIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
            exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        self.assertEqual(json.loads(exchange.result_json)["state"], "succeeded")
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(self.extension_calls[0].descriptor.binding, changed)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("host-generation-1", logs.output[0])
        self.assertIn("host-generation-2", logs.output[0])

    async def test_remapping_refuses_a_missing_provider(self):
        _, run, completion = await self.stage()
        self.bindings.pop("device.read")
        self.assertNotIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
        exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        result = json.loads(exchange.result_json)
        self.assertEqual((result["state"], result["code"]), ("failed", "external_provider_changed"))
        self.assertEqual(self.extension_calls, [])

    async def test_remapping_refuses_a_different_provider(self):
        _, run, completion = await self.stage()
        self.bindings["device.read"] = replace(self.bindings["device.read"], provider="other-provider")
        self.assertNotIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
        exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        result = json.loads(exchange.result_json)
        self.assertEqual((result["state"], result["code"]), ("failed", "external_provider_changed"))
        self.assertEqual(self.extension_calls, [])

    async def test_remapping_refuses_a_different_capability(self):
        _, run, completion = await self.stage()
        self.bindings["device.read"] = replace(self.bindings["device.read"], capability="device.play")
        self.assertNotIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
        exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        result = json.loads(exchange.result_json)
        self.assertEqual((result["state"], result["code"]), ("failed", "external_provider_changed"))
        self.assertEqual(self.extension_calls, [])

    async def test_a_stable_binding_logs_nothing_and_keeps_the_original_declaration(self):
        _, run, completion = await self.stage()
        with self.assertNoLogs("companion_v2.conversation.external_tools", level="WARNING"):
            self.assertIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
            exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        self.assertEqual(json.loads(exchange.result_json)["state"], "succeeded")
        self.assertEqual(self.extension_calls[0].descriptor.binding, self.bindings["device.read"])
        self.assertEqual(self.extension._rebindings, {})

    async def test_pause_resume_role_disable_and_revoke_recheck_catalog_and_delivery(self):
        self.model_uses()
        batch = await self.generate()
        self.capabilities.pause(extension_id=PACKAGE.extension_id, expected_revision=1, observed_at=NOW).unwrap()
        self.assertNotIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code,
                         "capability_not_admissible")
        self.capabilities.resume(extension_id=PACKAGE.extension_id, expected_revision=2, observed_at=NOW).unwrap()
        self.role_enabled(False, 1)
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code,
                         "capability_role_not_enabled")
        self.role_enabled(True, 2)
        await self.deliver_next(batch)
        self.consent("device.read", CapabilityAuthorizationState.REVOKED, 1)
        self.assertFalse(self.history()[0]["body_available"])
        self.assertNotIn("device.read", {s.action for s in self.tools.catalog_for(self.context)})

    async def test_pinned_package_update_and_discard_invalidate_original_result(self):
        self.model_uses()
        batch = await self.generate()
        newer = replace(PACKAGE, package_version="2", artifact_hash="hash-2")
        self.capabilities.register_descriptor(newer, registered_at=NOW).unwrap()
        self.capabilities.update(extension_id=PACKAGE.extension_id, package_id=PACKAGE.package_id,
                                 package_version="2", expected_revision=1, observed_at=NOW).unwrap()
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code,
                         "external_package_changed")
        self.capabilities.update(extension_id=PACKAGE.extension_id, package_id=PACKAGE.package_id,
                                 package_version="1", expected_revision=2, observed_at=NOW).unwrap()
        self.capabilities.request_discard(extension_id=PACKAGE.extension_id, expected_revision=3, observed_at=NOW).unwrap()
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code,
                         "capability_not_admissible")

    async def test_expired_or_revoked_original_cannot_be_replaced(self):
        async def respond(invocation):
            return self.response(invocation, expires_at=NOW + timedelta(seconds=1))
        self.extension_behavior = respond
        self.model_uses()
        batch = await self.generate()
        self.clock.advance(timedelta(seconds=1))
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code, "source_expired")
        self.assertEqual(len(self.extension_calls), 1)

    async def test_revocation_during_effect_keeps_receipt_without_deliverable_result(self):
        async def revoke(invocation):
            self.consent("device.play", CapabilityAuthorizationState.REVOKED, 1)
            return self.response(invocation)
        self.extension_behavior = revoke
        self.model_uses(tool_call(effectful=True))
        batch = self.accept()
        self.messages.seal(self.context, batch, deadline_at=self.deadline).unwrap()
        await self.runner().run_once(Lane.FOREGROUND)
        self.assertEqual(self.parts(batch), ())
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("succeeded", "committed"))
        self.assertEqual(self.tasks.read(self.context, self.rows()[0]["task_id"]).unwrap().state, "uncertain")

    async def test_receipt_or_source_from_another_context_is_unknown(self):
        async def wrong(invocation):
            return self.response(invocation, origin_key="another-user")
        self.extension_behavior = wrong
        self.model_uses(tool_call(effectful=True))
        await self.generate()
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("unknown", "unknown"))
        self.assertIsNone(self.rows()[0]["result_source_json"])

    async def test_original_binding_rebound_prevents_replay_and_catalog_access(self):
        self.model_uses()
        batch = await self.generate()
        self.identity.bind(binding_id="rebound", principal=self.important.principal, subject=self.standard.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        self.assertEqual(self.extension.catalog_for(self.context), ())
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code, "binding_rebound")

    async def test_enabling_after_model_catalog_capture_does_not_grant_an_unoffered_call(self):
        self.role_enabled(False, 1)
        _, run, completion = await self.stage()
        self.role_enabled(True, 2)
        exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        self.assertEqual(json.loads(exchange.result_json)["code"], "tool_not_available")
        self.assertEqual(self.extension_calls, [])

    async def test_standard_user_needs_identity_feature_grant_as_well_as_extension_consent(self):
        context = self.context_for(self.standard)
        self.capabilities.set_authorization(extension_id=PACKAGE.extension_id, capability_id="device.read",
            subject=self.standard.subject, scope=context.scope, state=CapabilityAuthorizationState.ACTIVE,
            expected_revision=0, observed_at=NOW).unwrap()
        self.assertEqual(self.extension.catalog_for(context), ())
        self.grant(self.standard, context.scope, "extension.tool.invoke")
        self.assertEqual([s.action for s in self.extension.catalog_for(context)], ["device.read"])

    async def test_successful_source_revocation_hides_sent_body_and_stops_unsent_parts(self):
        self.model_uses()
        batch = await self.generate()
        await self.deliver_next(batch)
        from companion_v2.conversation.messages import source_from_document
        reference = source_from_document(json.loads(self.rows()[0]["result_source_json"]))
        self.messages.sources.revoke(reference, expected_governance_revision=1, observed_at=NOW).unwrap()
        self.assertFalse(self.history()[0]["body_available"])
        self.assertEqual(self.delivery.queue_next(self.context, batch, deadline_at=self.deadline).failure.code, "source_revoked")

    async def test_huge_result_keeps_unknown_effect_and_does_not_publish_an_original(self):
        async def huge(invocation):
            return self.response(invocation, payload="x" * 70000)
        self.extension_behavior = huge
        self.model_uses(tool_call(effectful=True))
        await self.generate()
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("unknown", "unknown"))
        self.assertEqual(self.runtime.read_all("SELECT source_id FROM sources WHERE owner = 'device'", ()), ())

    async def test_source_owner_or_scope_substitution_is_not_published(self):
        async def wrong_source(invocation):
            response = self.response(invocation)
            return replace(response, source=replace(response.source,
                reference=replace(response.source.reference, owner="another-owner")))
        self.extension_behavior = wrong_source
        self.model_uses()
        await self.generate()
        self.assertEqual(self.rows()[0]["state"], "unknown")
        self.assertIsNone(self.rows()[0]["result_source_json"])

    async def test_reused_provider_receipt_cannot_be_bound_to_another_call(self):
        async def shared_receipt(invocation):
            return self.response(invocation, receipt_ref="one-original-receipt")
        self.extension_behavior = shared_receipt
        async def respond(envelope):
            if not envelope.tool_history:
                return requested(tool_call("first", query="one"), tool_call("second", query="two"))
            return answered("Only the first result was confirmed.")
        self.responses["primary"] = respond
        await self.generate()
        self.assertEqual([r["state"] for r in sorted(self.rows(), key=lambda r: r["tool_call_id"])], ["succeeded", "unknown"])

    async def test_completed_child_receipt_survives_a_missing_parent_projection(self):
        _, run, completion = await self.stage(tool_call(effectful=True))
        call = completion.tool_calls[0]
        self.tools._prepare(run, completion.call_id, call)
        original = await self.extension.execute(run, call)
        # Simulate process loss after the child receipt commits and before the parent copy.
        runtime = self.open_runtime()
        tasks, messages, delivery = self.components(runtime)
        tasks.start(expected_generation=2).unwrap()
        tasks.recover().unwrap()
        tools = LocalRoleTools(messages, external=self.extension)
        batch = messages.batch(run.task.spec.request_ref)
        request = messages._request(batch, self.context, NOW)
        material = next(m for m in request.context.materials if m.purpose == "actual_tool_results")
        self.assertEqual(json.loads(material.payload)[0]["result"], original)
        self.assertEqual(len(self.extension_calls), 1)
        self.assertEqual(tools.observe(batch["batch_id"])[0]["child_state"], "succeeded")
        self.assertEqual(tasks.read(self.context, run.task.task_id).unwrap().state, "uncertain")

    async def test_same_person_on_another_account_or_bot_does_not_inherit_consent(self):
        from companion_v2.execution.models import TaskContext
        from companion_v2.foundation.refs import AudienceRef
        from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaSelector, SelectorKind
        for bot, account in (("bot-a", "login-b"), ("bot-b", "login-a")):
            principal = replace(self.important.principal, bot_id=bot, account_id=account)
            self.identity.bind(binding_id=bot + account, principal=principal, subject=self.important.subject,
                               valid_from=NOW, expected_revision=self.identity.revision)
            scope = replace(self.context.scope, bot_id=bot, account_id=account)
            self.personas.register_route(ConversationRoute(scope, user=self.important.subject), observed_at=NOW)
            if bot != "bot-a":
                self.personas.set_binding(PersonaBinding("base-b", PersonaSelector(scope.installation_id, SelectorKind.BOT, bot_id=bot),
                    self.context.role_binding.role, 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
            actor = self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()
            context = TaskContext(actor, self.identity.resolve_role(scope, observed_at=NOW).unwrap(), AudienceRef(scope, (actor.subject,)))
            self.assertEqual(self.extension.catalog_for(context), ())
            if bot != "bot-a":
                self.capabilities.set_role_enabled(extension_id=PACKAGE.extension_id, role=context.role_binding.role,
                    bot_id=bot, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
                self.assertEqual(self.extension.catalog_for(context), ())


if __name__ == "__main__":
    unittest.main()
