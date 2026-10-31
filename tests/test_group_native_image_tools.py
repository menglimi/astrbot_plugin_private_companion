"""Group image tools retain their external owner and collective source authority."""

from dataclasses import replace
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityPackageDescriptor
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.external_tools import (
    ExtensionToolDescriptor, ExternalToolExecutor, ExternalToolProvider, ExternalToolResponse,
)
from companion_v2.conversation.tool_loop import LocalRoleTools
from companion_v2.conversation.group_history import _READ_CACHE, _READ_STEPS
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor
from companion_v2.execution.models import ProviderBinding, SideEffect, TaskContext, TaskError, digest, encode
from companion_v2.execution.runner import TaskRun
from companion_v2.foundation.refs import AudienceRef, ScopeKind, SourceRef, VersionRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.model_gateway.models import ToolCall, ToolSpec
from companion_v2.model_gateway.routing import ModelRouter
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.sources.models import SourceRecord
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered, requested


PACKAGE = CapabilityPackageDescriptor("fixture.image", "image-tools", "fixture", "1", "image-tools-1",
                                      ("image.generate", "image.edit", "device.read"), ("fixture.image",))


def call(action="image.generate", identifier="image-call"):
    return ToolCall(identifier, action, encode({"prompt": "合成图片任务"}))


class GroupImageToolFixture(MessageFixture):
    def setUp(self):
        self.invocations = []
        self.provider_behavior = None
        self.descriptors = tuple(ExtensionToolDescriptor(
            ToolSpec(action, "Use the current authorized provider.", encode({
                "type": "object", "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"], "additionalProperties": False,
            })), PACKAGE, ProviderBinding(action, "image-fixture", "1", "provider-generation-1"),
            "fixture.image", SideEffect.EXTERNAL_DEVICE, timeout_seconds=3,
        ) for action in PACKAGE.capabilities)
        self.bindings = {d.tool.action: d.binding for d in self.descriptors}
        super().setUp()
        self.private_context = self.context
        scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-1",
                        conversation_id="group-conversation", session_id="group-session")
        self.personas.register_route(ConversationRoute(scope, group_id="group-1"), observed_at=NOW)
        self.context = self.member(self.important, scope)
        self.providers = tuple(replace(p, capabilities=("text", "tools", "image")) for p in self.providers)
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=2)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)
        self.generator.gateway = self.gateway
        self.tools = LocalRoleTools(self.messages, external=self.external)
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(PACKAGE, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=PACKAGE.extension_id, package_id=PACKAGE.package_id,
            package_version=PACKAGE.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.capabilities.set_role_enabled(extension_id=PACKAGE.extension_id, role=self.context.role_binding.role,
            bot_id=self.context.scope.bot_id, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for action in PACKAGE.capabilities:
            self.consent(action)
            self.consent(action, context=self.private_context)

    def components(self, runtime, *, takeover=False):
        providers = tuple(ExternalToolProvider(d, self.invoke,
            lambda action=d.tool.action: self.bindings.get(action)) for d in self.descriptors)
        external = ExternalToolExecutor(runtime, providers)
        registrations = self.registrations
        self.registrations += external.registrations
        try:
            tasks, messages, delivery = super().components(runtime, takeover=takeover)
        finally:
            self.registrations = registrations
        self.external = external.attach(messages)
        self.addAsyncCleanup(external.close)
        return tasks, messages, delivery

    def member(self, actor, scope=None):
        scope = scope or self.context.scope
        for action in ("text", "conversation.reply", "configuration.read", "extension.tool.invoke",
                       "group.memory.read", "group.memory.write"):
            self.grant(actor, scope, action)
        role = self.identity.resolve_role(scope, observed_at=NOW).unwrap()
        return TaskContext(actor, role, group_audience(scope, actor.subject))

    def consent(self, action, *, context=None, active=True, revision=0):
        context = context or self.context
        return self.capabilities.set_authorization(extension_id=PACKAGE.extension_id, capability_id=action,
            subject=context.actor.subject, scope=context.scope,
            state=CapabilityAuthorizationState.ACTIVE if active else CapabilityAuthorizationState.REVOKED,
            expected_revision=revision, observed_at=NOW).unwrap()

    def set_image_output(self, **changes):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.conversation.media.output_settings import (
            GROUP as IMAGE_OUTPUT_GROUP, ImageOutputConfigurationOwner, configuration_fields,
        )
        target = ConfigTarget(self.context.scope.installation_id, ConfigLayer.ROLE,
                              self.context.role_binding.role, bot_id=self.context.scope.bot_id)
        fields = configuration_fields()
        owner = ImageOutputConfigurationOwner(self.runtime)
        owner.write(target, fields,
                    changes={IMAGE_OUTPUT_GROUP + "." + name: value for name, value in changes.items()},
                    reset=(), expected_revision=owner.read(target, fields).revision, observed_at=NOW)

    def response(self, invocation, *, state="succeeded"):
        source = None
        images = ()
        if state == "succeeded":
            reference = SourceRef("source-" + invocation.invocation_id, "fixture.image", invocation.context.scope,
                                  VersionRef(invocation.invocation_id, 1), self.clock.now())
            payload = "图像扩展合成结果；本测试仅合成图片，不调用网络。"
            source = SourceRecord(reference, invocation.context.audience, digest(payload), payload)
            output = io.BytesIO()
            Image.new("RGB", (3, 2), "red").save(output, format="PNG")
            content = output.getvalue()
            images = (CapturedImage(ImageDescriptor(0, "image/png", 3, 2, len(content),
                                    hashlib.sha256(content).hexdigest()), content),)
        return ExternalToolResponse(invocation.invocation_id, invocation.context.key, invocation.arguments_hash,
            invocation.descriptor.binding, state, "committed" if state == "succeeded" else "unknown", source,
            "operation-" + invocation.invocation_id if state == "succeeded" else None,
            "receipt-" + invocation.invocation_id if state == "succeeded" else None, images=images)

    async def invoke(self, invocation):
        self.invocations.append(invocation)
        return await self.provider_behavior(invocation) if self.provider_behavior else self.response(invocation)

    async def stage(self):
        batch = self.accept()
        task = self.messages.seal(self.context, batch, deadline_at=self.deadline).unwrap()
        claim = self.tasks.claim_task(self.context, task.task_id).unwrap().claim
        run = TaskRun(self.tasks, claim, self.tasks.begin(claim).unwrap())
        request = self.messages.request(run)
        session = self.gateway.open_session(run, request, tools=self.tools.catalog_for(self.context))
        self.responses["primary"] = requested(call())
        return batch, run, await session.generate(request)

    def model_uses(self, *calls):
        async def respond(envelope):
            count = len(envelope.tool_history)
            return requested(calls[count]) if count < len(calls) else answered("本群任务已有实际结果。")
        self.responses["primary"] = respond

    def rows(self):
        return self.runtime.read_all("SELECT * FROM extension_tool_invocations ORDER BY invocation_id", ())

    def view(self, context, batch):
        return self.messages._request(self.messages.batch(batch), context, self.clock.now())

    def later_group_request(self):
        context = self.member(self.standard)
        event = self.event("next-member", context=context)
        batch = self.accept(event, context=context)
        self.messages.seal(context, batch, deadline_at=self.deadline).unwrap()
        return context, batch


class GroupNativeImageToolTests(GroupImageToolFixture, unittest.IsolatedAsyncioTestCase):
    async def test_catalog_exposes_only_authorized_image_actions_beside_group_memory(self):
        # This fixture has no V1 legacy-memory service.  The current catalog
        # must omit that compatibility action instead of exposing a dead tool.
        group_memory_actions = self.tools.group_memory.actions - {"group.memory.legacy.query"}
        group = {t.action for t in self.tools.catalog_for(self.context)}
        self.assertEqual(group, group_memory_actions | self.tools.group_participation.actions | {"image.generate", "image.edit"})
        self.assertNotIn("group.memory.legacy.query", group)
        self.assertIn("device.read", {t.action for t in self.tools.catalog_for(self.private_context)})
        self.assertTrue({"source.read", "memo.read", "device.read"}.isdisjoint(group))
        self.consent("image.edit", active=False, revision=1)
        self.assertEqual({t.action for t in self.tools.catalog_for(self.context)},
                         group_memory_actions | self.tools.group_participation.actions | {"image.generate"})
        self.capabilities.set_role_enabled(extension_id=PACKAGE.extension_id, role=self.context.role_binding.role,
            bot_id=self.context.scope.bot_id, enabled=False, expected_revision=1, observed_at=NOW).unwrap()
        self.assertEqual({t.action for t in self.tools.catalog_for(self.context)},
                         group_memory_actions | self.tools.group_participation.actions)

    async def test_group_switch_and_core_only_permission_remove_image_tools(self):
        group_memory_actions = self.tools.group_memory.actions - {"group.memory.legacy.query"}
        baseline = group_memory_actions | self.tools.group_participation.actions | {"image.generate", "image.edit"}
        self.assertEqual({t.action for t in self.tools.catalog_for(self.context)}, baseline)

        self.set_image_output(group_enabled=False)
        self.assertEqual({t.action for t in self.tools.catalog_for(self.context)},
                         group_memory_actions | self.tools.group_participation.actions)

        self.set_image_output(group_enabled=True, request_permission="core_only")
        self.assertEqual({t.action for t in self.tools.catalog_for(self.context)},
                         group_memory_actions | self.tools.group_participation.actions)

        core_context = self.member(self.core, self.context.scope)
        for action in ("image.generate", "image.edit"):
            self.consent(action, context=core_context)
        self.assertEqual({t.action for t in self.tools.catalog_for(core_context)}, baseline)

    async def test_private_audience_cannot_open_group_image_catalog(self):
        forged = replace(self.context, audience=AudienceRef(self.context.scope, (self.context.actor.subject,)))
        self.assertEqual(self.tools.catalog_for(forged), ())

    async def test_image_results_and_memory_queries_keep_their_owners_and_confirmed_history(self):
        self.model_uses(call(), ToolCall("group-query", "group.memory.query", encode({"query": "合成"})))
        batch = await self.generate()
        material = next(m for m in self.called[-1][1].original.context.materials if m.owner == "fixture.image")
        self.assertEqual(self.rows()[0]["state"], "succeeded")
        self.assertEqual(self.tasks.read(self.context, self.rows()[0]["task_id"]).unwrap().state, "succeeded")
        visible = next(m for m in self.view(self.context, batch).context.materials if m.purpose == "actual_tool_results")
        self.assertEqual([v["action"] for v in json.loads(visible.payload)], ["image.generate", "group.memory.query"])
        await self.deliver_next(batch)
        other, next_batch = self.later_group_request()
        self.assertIn("confirmed_group_reply", [json.loads(m.payload)["type"]
            for m in self.view(other, next_batch).context.materials if m.purpose == "group_conversation_history"])
        self.messages.sources.revoke(material.source, expected_governance_revision=1, observed_at=NOW).unwrap()
        with self.assertRaises(TaskError) as error:
            self.view(other, next_batch)
        self.assertEqual(error.exception.code, "source_revoked")
        self.assertEqual(len(self.invocations), 1)

    async def test_duplicate_edit_reuses_the_original_durable_child_receipt(self):
        self.model_uses(call("image.edit"), call("image.edit"))
        await self.generate()
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("succeeded", "committed"))
        self.assertEqual(self.called[-1][1].tool_history[0], self.called[-1][1].tool_history[1])

    async def test_unknown_generation_cannot_retry_with_another_call_id(self):
        async def unknown(invocation):
            return self.response(invocation, state="unknown")
        self.provider_behavior = unknown
        self.model_uses(call(identifier="first"), call(identifier="changed-id"))
        await self.generate()
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual((self.rows()[0]["state"], self.rows()[0]["effect"]), ("unknown", "unknown"))
        rejected = self.tools._rows(self.rows()[0]["batch_id"])[-1]
        self.assertEqual(json.loads(rejected["result_json"])["code"], "external_effect_unresolved")
        self.assertEqual(json.loads(self.called[-1][1].tool_history[-1].result_json)["state"], "unknown")

    async def test_other_external_actions_are_rejected_before_dispatch(self):
        _, run, completion = await self.stage()
        with self.assertRaises(TaskError) as error:
            await self.tools.execute_async(run, completion.call_id, call("device.read", "private-action"))
        self.assertEqual(error.exception.code, "group_action_unavailable")
        self.assertEqual(self.rows(), ())
        self.assertEqual(self.invocations, [])

    async def test_revocation_after_catalog_capture_keeps_a_not_dispatched_result(self):
        batch, run, completion = await self.stage()
        self.consent("image.generate", active=False, revision=1)
        exchange = await self.tools.execute_async(run, completion.call_id, completion.tool_calls[0])
        self.assertEqual(json.loads(exchange.result_json)["state"], "failed")
        self.assertEqual(json.loads(exchange.result_json)["effect"], "not_committed")
        self.assertEqual(self.rows(), ())
        self.assertEqual(self.invocations, [])
        visible = next(m for m in self.view(self.context, batch).context.materials if m.purpose == "actual_tool_results")
        self.assertEqual(json.loads(visible.payload)[0]["result"], json.loads(exchange.result_json))

    async def test_changed_provider_binding_remaps_without_hiding_group_image_history(self):
        self.model_uses(call())
        batch = await self.generate()
        await self.deliver_next(batch)
        other, next_batch = self.later_group_request()
        self.bindings["image.generate"] = replace(self.bindings["image.generate"], generation="replacement")
        self.assertIn("image.generate", {t.action for t in self.tools.catalog_for(self.context)})
        self.assertTrue(self.view(other, next_batch))
        self.assertEqual(len(self.invocations), 1)

    async def test_removed_image_provider_still_hides_the_tool_and_blocks_history(self):
        self.model_uses(call())
        batch = await self.generate()
        await self.deliver_next(batch)
        other, next_batch = self.later_group_request()
        self.bindings.pop("image.generate")
        self.assertNotIn("image.generate", {t.action for t in self.tools.catalog_for(self.context)})
        with self.assertRaises(TaskError) as error:
            self.view(other, next_batch)
        self.assertEqual(error.exception.code, "external_provider_changed")
        self.assertEqual(len(self.invocations), 1)

    async def assert_generated_revocation_blocks_text_history(self, target):
        self.model_uses(call())
        batch = await self.generate()
        images = self.messages.generated_images.read_batch(batch, self.context, NOW)
        await self.deliver_next(batch)
        self.assertFalse(getattr(self.sent[-1], "images", ()))
        other, next_batch = self.later_group_request()
        self.assertTrue(self.view(other, next_batch))
        if target == "manifest":
            owner, reference = self.messages.sources, images[0].parent
        else:
            row = self.rows()[0]
            payload = self.runtime.read_one("SELECT payload_json FROM sources WHERE source_id = ?",
                                            (images[0].parent.source_id,))[0]
            view = self.messages.generated_images._view(images[0].parent, self.context, json.loads(payload))
            _, reference = self.messages.images.references(view, images[0].descriptor)
            owner = self.messages.images.assets
            self.assertEqual(row["state"], "succeeded")
        owner.revoke(reference, expected_governance_revision=1, observed_at=NOW).unwrap()
        with self.assertRaises(TaskError) as error:
            self.view(other, next_batch)
        self.assertEqual(error.exception.code, "source_revoked" if target == "manifest" else "asset_revoked")
        self.assertEqual(len(self.invocations), 1)

    async def test_revoked_generated_manifest_blocks_even_text_only_confirmed_history(self):
        await self.assert_generated_revocation_blocks_text_history("manifest")

    async def test_revoked_generated_asset_blocks_even_text_only_confirmed_history(self):
        await self.assert_generated_revocation_blocks_text_history("asset")

    async def test_generated_history_uses_one_budget_and_reuses_completed_ancestry(self):
        self.model_uses(call())
        for index in range(3):
            batch = await self.generate(self.event("image-turn-" + str(index)))
            await self.deliver_next(batch)
        other, next_batch = self.later_group_request()
        saved = json.loads(self.messages.batch(next_batch)["contributions_json"])["group_context"]
        self.assertEqual(len(saved["references"]), 6)
        owner = self.messages.group_context.replies
        with patch("companion_v2.conversation.group_history.MAX_ANCESTORS", 6):
            with patch.object(owner, "_reply", wraps=owner._reply) as reads:
                evidence = owner.source_evidence(saved, other, next_batch, NOW)
            self.assertEqual(reads.call_count, 3)
            self.assertTrue(evidence)
        with patch("companion_v2.conversation.group_history.MAX_ANCESTORS", 5):
            with self.assertRaises(TaskError) as error:
                owner.source_evidence(saved, other, next_batch, NOW)
        self.assertEqual(error.exception.code, "group_history_budget")
        self.assertIsNone(_READ_STEPS.get())
        self.assertIsNone(_READ_CACHE.get())
        self.assertEqual(owner.source_evidence(saved, other, next_batch, NOW), evidence)
        self.assertEqual(len(self.invocations), 3)

    async def test_cached_reply_cannot_hide_a_later_merged_input(self):
        self.model_uses(call())
        batch = self.accept(self.event("first-merged-input"))
        self.accept(self.event("second-merged-input"), batch_id=batch)
        await self.generate(batch=batch)
        await self.deliver_next(batch)
        other, next_batch = self.later_group_request()
        groups = self.messages.group_context
        reference = groups.reply_ref(self.parts(batch)[0])
        with groups.replies._reading():
            groups.replies.read(reference, other, "Original role prompt", NOW, groups.cutoff(next_batch))
            with self.assertRaises(TaskError) as error:
                groups.replies.read(reference, other, "Original role prompt", NOW, groups.cutoff(batch) + 1)
        self.assertEqual(error.exception.code, "group_history_changed")

    async def test_private_or_unowned_success_cannot_be_adopted_from_the_group_ledger(self):
        self.model_uses(call())
        batch = await self.generate()
        row = dict(self.tools._rows(batch)[0])
        for change, code in (({"action": "device.read"}, "group_action_unavailable"),
                             ({"task_id": "missing-original-child"}, "tool_owner_unavailable")):
            with self.subTest(change=change), self.assertRaises(TaskError) as error:
                self.tools._validate_result(row | change, None, self.context, NOW)
            self.assertEqual(error.exception.code, code)


if __name__ == "__main__":
    unittest.main()
