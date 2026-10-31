"""The real multi-role admission exposes native Image tools without manual grants."""

import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools, native_tool_name
from astrbot_host.native_extensions.image_native.adapter import NativeImageProvider
from astrbot_host.native_extensions.image_native.owner import NativeImageOwner
from astrbot_host.native_extensions.image_native.types import BackendResult
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityRepository
from companion_v2.execution.models import TaskContext
from companion_v2.product import PRODUCT_VERSION
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_image_support import ImageBackend
from tests.native_internal_support import internal_lease


class MultiImageAccessTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.backend = ImageBackend(BackendResult)
        self.owner = NativeImageOwner(self.root / "native-image", self.backend)
        self.addAsyncCleanup(self.owner.close)
        provider = NativeImageProvider(self.owner, package_version=PRODUCT_VERSION, artifact_hash="image-fixture")
        self.factory = patch("astrbot_host.multi_controller.create_bundled_extensions",
                             return_value=((internal_lease(provider),), ()))
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def image_host(self):
        app = await self.start_host()
        await self.controller.native_extensions._start_task
        self.addAsyncCleanup(close_core_reply_tools, app)
        return app

    async def prepare(self, target="first", identifier="image-event"):
        event = self.event(target, identifier, "帮我画张图片")
        session = self.controller.session
        plan = (await session.prepare_core_reply_context(event, self.controller.settings.target(target))).unwrap()
        prepared = {"context": plan.context, "event_id": plan.captured.event_id, "native_input": plan.captured}

        async def authorize(state):
            return session.application.tasks.check_context_access(
                state["context"], action="conversation.reply", observed_at=self.clock.now())

        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=session.application)
        return plan.context, event, request

    def assert_images(self, request):
        for action in ("image.generate", "image.edit", "image.mask_edit"):
            self.assertIsNotNone(request.func_tool.get_tool(native_tool_name(action)), action)

    async def test_first_admitted_context_exposes_and_executes_native_image_tool(self):
        app = await self.image_host()
        self.assertFalse(hasattr(self.controller.session, "profile"))
        context, event, request = await self.prepare()
        self.assert_images(request)
        self.assertEqual(context.actor.policy_revision, self.controller.authority.identities.revision)
        from astrbot.core.agent.message import Message, ToolCall
        call = ToolCall(id="image-call", function=ToolCall.FunctionBody(
            name=native_tool_name("image.generate"), arguments=json.dumps({"prompt": "红色"})))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        result = await request.func_tool.get_tool(native_tool_name("image.generate")).call(wrapper, prompt="红色")
        self.assertEqual(json.loads(result.content[0].text)["state"], "succeeded")
        self.assertEqual(len(self.backend.calls), 1)
        actual = TaskContext.restore(self.backend.calls[0][0].envelope["context"])
        self.assertEqual(actual, context)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM capability_authorizations", ())["n"], 3)

    async def test_request_injection_repairs_image_defaults_missing_during_admission(self):
        app = await self.image_host()
        event = self.event(identifier="image-ready-after-admission", text="来张自拍看看")
        session = self.controller.session
        with patch("astrbot_host.image_access.ensure_image_access"):
            plan = (await session.prepare_core_reply_context(
                event, self.controller.settings.target("first"))).unwrap()
        self.assertFalse(any(tool.action.startswith("image.")
                             for tool in app.tools.external.catalog_for(plan.context)))
        prepared = {"context": plan.context, "event_id": plan.captured.event_id, "native_input": plan.captured}

        async def authorize(state):
            return app.tasks.check_context_access(state["context"], action="conversation.reply",
                                                  observed_at=self.clock.now())

        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=app)

        self.assert_images(request)
        self.assertIn("用户用自然语言", request.system_prompt)
        self.assertFalse(self.backend.calls)

    async def test_optional_semantic_catalog_failure_keeps_native_image_execution(self):
        app = await self.image_host()
        from astrbot.core.agent.message import Message, ToolCall

        with patch.object(app.tools.semantic, "catalog_for", side_effect=TypeError("optional catalogue mismatch")), \
                self.assertLogs("astrbot_host.core_reply_tools", level="WARNING") as logs:
            _, event, request = await self.prepare(identifier="selfie-with-semantic-unavailable")
            self.assert_images(request)
            call = ToolCall(id="selfie-with-semantic-unavailable", function=ToolCall.FunctionBody(
                name=native_tool_name("image.generate"), arguments=json.dumps({"prompt": "selfie, 红色"})))
            wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
            result = await request.func_tool.get_tool(native_tool_name("image.generate")).call(
                wrapper, prompt="selfie, 红色")

        self.assertTrue(any("error_type=TypeError" in message for message in logs.output))
        self.assertEqual(json.loads(result.content[0].text)["state"], "succeeded")
        self.assertEqual(len(self.backend.calls), 1)

    async def test_request_repair_preserves_core_only_and_explicit_standard_access(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.conversation.media.output_settings import (
            GROUP, ImageOutputConfigurationOwner, configuration_fields,
        )
        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy

        app = await self.image_host()
        context, _, _ = await self.prepare()
        owner = ImageOutputConfigurationOwner(app.runtime)
        fields = configuration_fields()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)

        def permission(value):
            owner.write(target, fields, changes={GROUP + ".request_permission": value}, reset=(),
                        expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

        permission("core_only")
        _, _, request = await self.prepare(identifier="standard-core-only")
        for action in ("image.generate", "image.edit", "image.mask_edit"):
            self.assertIsNone(request.func_tool.get_tool(native_tool_name(action)))

        policy = ObjectAccessPolicy(app.runtime)
        policy.set_tier(context.actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        _, _, request = await self.prepare(identifier="core-image-access")
        self.assert_images(request)

        policy.set_tier(context.actor.subject, AuthorizationTier.STANDARD,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        permission("everyone")
        _, _, request = await self.prepare(identifier="standard-image-access-enabled")
        self.assert_images(request)
        self.assertFalse(self.backend.calls)

    async def test_role_and_bot_switch_receive_their_own_default_records(self):
        app = await self.image_host()
        first, _, request = await self.prepare()
        self.assert_images(request)
        self.window_personas["qq-main:FriendMessage:200"] = "second"
        second, _, request = await self.prepare(identifier="second-role")
        self.assert_images(request)
        self.assertNotEqual(first.role_binding.role, second.role_binding.role)
        other, _, request = await self.prepare("second", "second-bot")
        self.assert_images(request)
        self.assertNotEqual(first.scope.bot_id, other.scope.bot_id)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM capability_role_selections", ())["n"], 3)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM capability_authorizations", ())["n"], 9)

    async def test_background_capacity_increase_restarts_host_and_preserves_natural_image_tools(self):
        app = await self.image_host()
        context, _, request = await self.prepare()
        self.assert_images(request)
        grants = app.runtime.read_all("SELECT * FROM capability_authorizations ORDER BY capability_id", ())
        generation = app.tasks.generation
        stopped = await self.controller.stop()
        self.assertTrue(stopped.is_success, stopped.failure)
        self.controller.config["runtime"]["background_slots"] = 3
        # Production assembly creates a fresh lease after stop closes its owner.
        next_owner = NativeImageOwner(self.root / "native-image", self.backend)
        self.addAsyncCleanup(next_owner.close)
        next_provider = NativeImageProvider(next_owner, package_version=PRODUCT_VERSION,
                                           artifact_hash="image-fixture")
        with patch("astrbot_host.multi_controller.create_bundled_extensions",
                   return_value=((internal_lease(next_provider),), ())):
            started = await self.controller.start()

        self.assertTrue(started.is_success, started.failure)
        await self.controller.native_extensions._start_task
        current_app = self.controller.session.application
        self.addAsyncCleanup(close_core_reply_tools, current_app)
        self.assertTrue(self.controller._accepting)
        self.assertEqual(current_app.tasks.limits.background_slots, 3)
        self.assertGreater(current_app.tasks.generation, generation)
        current_context, event, request = await self.prepare(identifier="selfie-after-expansion")
        self.assertEqual(current_context.scope, context.scope)
        self.assert_images(request)
        current_grants = [dict(row) for row in current_app.runtime.read_all(
            "SELECT * FROM capability_authorizations ORDER BY capability_id", ())]
        self.assertTrue(all(dict(grant) in current_grants for grant in grants))
        from astrbot.core.agent.message import Message, ToolCall
        call = ToolCall(id="selfie-after-expansion", function=ToolCall.FunctionBody(
            name=native_tool_name("image.generate"), arguments=json.dumps({"prompt": "selfie, 红色"})))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        result = await request.func_tool.get_tool(native_tool_name("image.generate")).call(
            wrapper, prompt="selfie, 红色")
        self.assertEqual(json.loads(result.content[0].text)["state"], "succeeded")
        self.assertEqual(len(self.backend.calls), 1)

    async def test_explicit_disable_and_revocation_remain_missing_from_model_catalog(self):
        app = await self.image_host()
        context, _, _ = await self.prepare()
        repository = CapabilityRepository(app.runtime)
        repository.set_authorization(extension_id="image", capability_id="image.generate",
            subject=context.actor.subject, scope=context.scope, state=CapabilityAuthorizationState.REVOKED,
            expected_revision=1, observed_at=self.clock.now()).unwrap()
        _, _, request = await self.prepare(identifier="after-revoke")
        self.assertIsNone(request.func_tool.get_tool(native_tool_name("image.generate")))
        self.assertIsNotNone(request.func_tool.get_tool(native_tool_name("image.edit")))
        repository.set_role_enabled(extension_id="image", role=context.role_binding.role,
            bot_id=context.scope.bot_id, enabled=False, expected_revision=1, observed_at=self.clock.now()).unwrap()
        _, _, request = await self.prepare(identifier="after-disable")
        for action in ("image.generate", "image.edit", "image.mask_edit"):
            self.assertIsNone(request.func_tool.get_tool(native_tool_name(action)))
        self.assertFalse(self.backend.calls)

    async def test_wildcard_user_is_provisioned_only_after_real_admission(self):
        config = multi_config()
        config["multi_host"]["targets"][0]["participant_id"] = "*"
        self.controller = self.make_controller(config)
        app = await self.image_host()
        self.assertIsNone(app.runtime.read_one(
            "SELECT 1 FROM capability_authorizations WHERE account_id = ?", ("100",)))
        from tests.native_adapter_support import FakeEvent
        event = FakeEvent(NS(platform=self.native.platforms["qq-main"]), event_id="new-user",
                          sender="201", login="100", text="帮我画图")
        event.created_at = self.clock.now().timestamp()
        self.window_personas[event.unified_msg_origin] = "original"
        target = self.controller.target_for_event(event)
        plan = (await self.controller.session.prepare_core_reply_context(event, target)).unwrap()
        catalog = app.tools.external.catalog_for(plan.context)
        self.assertEqual({tool.action for tool in catalog if tool.action.startswith("image.")},
                         {"image.generate", "image.edit", "image.mask_edit"})
        self.assertEqual(plan.context.scope.native_scope_id, "201")

    async def test_revoked_invocation_grant_is_not_replaced_by_next_admission(self):
        app = await self.image_host()
        context, _, request = await self.prepare()
        self.assert_images(request)
        identities = self.controller.session.authority.identities
        grant = next(grant for grant in identities.grants_for_subject(context.actor.subject)
                     if grant.scope == context.scope and grant.capability == "extension.tool.invoke")
        identities.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                expected_revision=identities.revision)
        _, _, request = await self.prepare(identifier="after-tool-revoke")
        for action in ("image.generate", "image.edit", "image.mask_edit"):
            self.assertIsNone(request.func_tool.get_tool(native_tool_name(action)))
        self.assertFalse(self.backend.calls)
