"""Native core tools use the real optional Image owner and original attachments."""

import base64
from dataclasses import replace
import hashlib
import json
import io
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.message.components import Image
from mcp.types import CallToolResult
from PIL import Image as PILImage

from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools, native_tool_name
from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.quota import GROUP, QUOTA_FIELDS, ImageQuotaConfigurationOwner
from companion_v2.conversation.media.output_settings import (
    GROUP as OUTPUT_GROUP, ImageOutputConfigurationOwner, configuration_fields as output_fields,
)
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from datetime import timedelta
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.references.configuration import (
    GROUP as REFERENCE_GROUP, ReferenceConfigurationOwner, configuration_fields as reference_fields,
    target_for as reference_target,
)
from companion_v2.references.models import (
    ContentDigest, ReferenceAsset, ReferenceKind, ReferenceRole, ReferenceSource,
)
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.references.service import owner_key as reference_owner_key
from tests.native_image_support import NativeImageFixture
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


class CoreReplyImagesTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        # The native-turn ledger is optional in production and is not part of
        # the minimal HostFixture schema. These album tests deliberately add
        # historical context rows, so install the same schema used by the
        # runtime before exercising that projection.
        from companion_v2.storage.native_conversation_schema import STATEMENTS
        with self.runtime.transaction() as connection:
            for statement in STATEMENTS:
                connection.execute(statement)
        self.image_directory = self.root.parent / "image-native-v2"
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.native_registry._default_handlers = True
        self.providers = tuple(replace(provider, capabilities=("text", "tools", "image")) for provider in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        package = self.native_registry.declarations()[0].descriptor.package
        capabilities = CapabilityRepository(self.runtime)
        require(capabilities.register_descriptor(package, registered_at=NOW))
        require(capabilities.acquire(extension_id=package.extension_id, package_id=package.package_id,
            package_version=package.package_version, expected_revision=0, observed_at=NOW))
        require(capabilities.set_role_enabled(extension_id=package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW))
        for capability in package.capabilities:
            require(capabilities.set_authorization(extension_id=package.extension_id, capability_id=capability,
                subject=self.important.subject, scope=SCOPE, state=CapabilityAuthorizationState.ACTIVE,
                expected_revision=0, observed_at=NOW))

    async def asyncSetUp(self):
        self.app = (await self.start(self.host(tool_port=self.native_registry))).application
        self.addAsyncCleanup(close_core_reply_tools, self.app)
        self.context = TaskContext(self.important, require(self.identity.resolve_role(SCOPE, observed_at=NOW)),
                                   AudienceRef(SCOPE, (self.important.subject,)))

    def prepare(self, event_id="native-image", images=(), *, event=None):
        event = event if event is not None else FakeEvent()
        parts = []
        for data in images:
            parts.append(Image.fromBase64(base64.b64encode(data).decode("ascii")))
        if getattr(event, "message_obj", None) is None:
            event.message_obj = NS(message=parts)
        else:
            event.message_obj.message = parts
        original = NativeConversationInput(event_id, self.context.actor.principal,
            ConversationRoute(SCOPE, user=self.context.actor.subject), self.context.audience,
            "请帮我画图", self.clock.now(), self.context.role_binding.role, "原人格")
        prepared = {"context": self.context, "event_id": event_id, "native_input": original}
        async def authorize(value):
            return self.app.tasks.check_context_access(value["context"], action="conversation.reply", observed_at=self.clock.now())
        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    async def call(self, event, request, action, arguments, call_id):
        call = ToolCall(id=call_id, function=ToolCall.FunctionBody(
            name=native_tool_name(action), arguments=json.dumps(arguments)))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        tool = request.func_tool.get_tool(native_tool_name(action))
        self.assertIsNotNone(tool)
        return await tool.call(wrapper, **arguments)

    def result(self, received, color):
        self.assertIsInstance(received, CallToolResult, received)
        result = json.loads(received.content[0].text)
        self.assertEqual((result["state"], result["effect"]), ("succeeded", "committed"), result)
        self.assertEqual(base64.b64decode(received.content[1].data), image_bytes(color))
        return result

    def assert_no_reply_work(self, event):
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM model_rounds", ())["n"], 0)
        self.assertEqual(self.called, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(event.sent, [])

    def configure_images(self, owner, fields, changes):
        target = ConfigTarget(SCOPE.installation_id, ConfigLayer.ROLE,
                              self.context.role_binding.role, bot_id=SCOPE.bot_id)
        owner.write(target, fields, changes=changes, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

    async def test_native_daily_quota_counts_attempts_and_replays_without_a_second_charge(self):
        gate = self.app.messages.image_quota
        scope = gate._classify_context(self.context)
        self.configure_images(ImageQuotaConfigurationOwner(self.runtime), QUOTA_FIELDS,
                              {GROUP + "." + scope + "_max_daily": 1})
        event, request = self.prepare("native-quota")
        generated = await self.call(event, request, "image.generate", {"prompt": "红色"}, "quota-first")
        self.result(generated, "red")
        self.assertEqual(gate._used(self.context, scope, self.clock.now()), 1)
        replay = await self.call(event, request, "image.generate", {"prompt": "红色"}, "quota-first")
        self.assertEqual(replay.content, generated.content)
        denied = await self.call(event, request, "image.generate", {"prompt": "红色"}, "quota-second")
        self.assertEqual(json.loads(denied)["code"], "image_quota_exhausted")
        self.assertEqual(len(self.image_backend.calls), 1)
        self.clock.advance(timedelta(days=1))
        self.assertEqual(gate._used(self.context, scope, self.clock.now()), 0)
        self.assert_no_reply_work(event)

    async def test_real_sdk_loop_generates_caches_and_sends_the_actual_image_once(self):
        from astrbot.core.agent.hooks import BaseAgentRunHooks
        from astrbot.core.agent.run_context import ContextWrapper
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
        from astrbot.core.agent.tool import ToolSet
        from astrbot.core.astr_agent_context import AstrAgentContext
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
        from astrbot.core.provider.entities import LLMResponse, ProviderRequest
        from astrbot.core.star.context import Context
        from astrbot_host.core_reply_native_send import SendMessageToUserTool, inject_core_reply_send_tool
        from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
        from tests.test_core_reply_pipeline import PipelineEvent

        event, original = self.prepare("native-image-loop", event=PipelineEvent())
        request = ProviderRequest(prompt="来张自拍看看", system_prompt=original.system_prompt,
            func_tool=ToolSet(tools=[*original.func_tool.tools, SendMessageToUserTool()]))
        inject_core_reply_send_tool(event, request)
        host = Context.__new__(Context)
        platform = NS(meta=lambda: NS(id="qq-main"), send_by_session=AsyncMock())
        host.platform_manager = NS(platform_insts=[platform])
        host.astrbot_config_mgr = NS(get_conf=lambda _: {})
        wrapper = ContextWrapper(context=AstrAgentContext(context=host, event=event))
        cached, requests = [], []

        async def text_chat(**arguments):
            requests.append(arguments)
            if len(requests) == 1:
                return LLMResponse(role="assistant", completion_text="", tools_call_ids=["selfie-generate"],
                    tools_call_name=[native_tool_name("image.generate")],
                    tools_call_args=[{"prompt": "selfie，在书桌边穿着家居服"}])
            if len(requests) == 2:
                results = "\n".join(message.content for message in wrapper.messages
                                    if message.role == "tool" and isinstance(message.content, str))
                path = re.search(r"cached at path='([^']+)'", results).group(1)
                cached.append(Path(path))
                self.assertEqual(cached[0].read_bytes(), image_bytes("red"))
                return LLMResponse(role="assistant", completion_text="", tools_call_ids=["selfie-send"],
                    tools_call_name=["send_message_to_user"], tools_call_args=[{"messages": [
                        {"type": "plain", "text": "头发还有一点乱呢。"}, {"type": "image", "path": path}]}])
            return LLMResponse(role="assistant", completion_text="给你看看。")

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                observe_core_tool_dispatch_begin(event, run_context)

            async def on_agent_done(self, run_context, response):
                restore_core_tool_dispatch(event, run_context)

        provider = NS(provider_config={"id": "synthetic-image-model", "modalities": ["text", "image"]},
                      get_model=lambda: "synthetic", text_chat=text_chat)
        runner = ToolLoopAgentRunner()
        await runner.reset(provider, request, wrapper, FunctionToolExecutor(), Hooks())
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        self.addCleanup(restore_core_tool_dispatch, event)
        for _ in range(3):
            _responses = [response async for response in runner.step()]
        self.assertTrue(runner.done())
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(len(cached), 1)
        platform.send_by_session.assert_awaited_once()
        target, message = platform.send_by_session.call_args.args
        self.assertEqual(str(target), event.unified_msg_origin)
        self.assertEqual(message.get_plain_text(), "")
        self.assertEqual([type(part) for part in message.chain], [Image])
        self.assertEqual(len([part for part in message.chain if isinstance(part, Image)]), 1)
        self.assert_no_reply_work(event)

    async def test_native_image_request_switch_is_consumed_before_provider_dispatch(self):
        event, request = self.prepare("native-images-off")
        original_request = NS(func_tool=request.func_tool)
        self.configure_images(ImageOutputConfigurationOwner(self.runtime), output_fields(),
                              {OUTPUT_GROUP + ".allow_user_requests": False})
        actions = inject_core_reply_tools(event, request, application=self.app)
        self.assertNotIn("image.generate", actions)
        self.assertNotIn("image.edit", actions)
        self.assertIsNone(request.func_tool.get_tool(native_tool_name("image.generate")))
        denied = await self.call(event, original_request, "image.generate", {"prompt": "红色"}, "disabled")
        self.assertEqual(json.loads(denied)["code"], "image_requests_disabled")
        self.assertEqual(self.image_backend.calls, [])
        self.assert_no_reply_work(event)

    async def test_natural_selfie_prompt_and_role_preferences_reach_native_provider(self):
        self.configure_images(ImageOutputConfigurationOwner(self.runtime), output_fields(),
            {OUTPUT_GROUP + ".prompt_format": "natural", OUTPUT_GROUP + ".style": "anime",
             OUTPUT_GROUP + ".selfie_fixed_prompt": "角色一致性", OUTPUT_GROUP + ".selfie_negative_prompt": "错误人物"})
        event, request = self.prepare("native-selfie")
        self.assertIn("来张自拍看看", request.system_prompt)
        self.assertIn("连贯的自然语言", request.system_prompt)
        self.assertIn(native_tool_name("image.generate"), request.system_prompt)
        self.assertIn("send_message_to_user", request.system_prompt)
        tool = request.func_tool.get_tool(native_tool_name("image.generate"))
        self.assertIn("生成成功本身不代表已发送", tool.description)
        self.assertIn("selfie", tool.parameters["properties"]["prompt"]["description"])
        original = next(spec for spec in self.app.tools.external.catalog_for(self.context) if spec.action == "image.generate")
        self.assertNotIn("生成成功本身不代表已发送", original.description)
        self.assertEqual(json.loads(original.parameters_json)["properties"]["prompt"], {"type": "string"})
        result = await self.call(event, request, "image.generate", {"prompt": "selfie，在书桌边穿着家居服"}, "selfie")
        self.result(result, "red")
        invocation = self.image_backend.calls[0][0]
        self.assertEqual(invocation.envelope["image_prompt"], {
            "positive": "角色一致性\nanime illustration\nselfie，在书桌边穿着家居服", "negative": "错误人物"})
        self.assert_no_reply_work(event)

    async def test_generate_then_edit_saved_artifact_returns_native_mcp_original_without_independent_send(self):
        event, request = self.prepare()
        generation = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate")
        result = self.result(generation, "red")
        native = json.loads(result["payload"])["native"]
        artifact = native["artifacts"][0]["artifact_id"]
        album = self.app.messages.generated_images
        native_item = album.album_image(self.context, self.clock.now(), artifact)
        self.assertEqual(native_item.image_id, "generated-image:" + result["invocation_id"] + ":0")
        self.assertEqual(native_item.content, image_bytes("red"))
        with self.assertRaisesRegex(TaskError, "selected album image is unavailable"):
            album.album_image(self.context, self.clock.now(), artifact + "-forged")
        edited = await self.call(event, request, "image.edit", {"prompt": "蓝色", "artifact_id": artifact}, "edit")
        self.result(edited, "blue")
        replay = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate")
        self.assertEqual(replay.content, generation.content)
        self.assertEqual(len(self.image_backend.calls), 2)
        self.assertEqual(len(self.image_backend.calls[1][1]), 1)
        page = album.album_page(self.context, self.clock.now(), limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNotNone(page["next_cursor"])
        next_page = album.album_page(self.context, self.clock.now(), limit=1, cursor=page["next_cursor"])
        self.assertEqual(len(next_page["items"]), 1)
        self.assertNotEqual(page["items"][0].image_id, next_page["items"][0].image_id)
        for item in (*page["items"], *next_page["items"]):
            self.assertEqual(album.album_image(self.context, self.clock.now(), item.image_id), item)
            parent, source, asset = album.album_refs(item, self.context)
            self.assertEqual(parent, item.parent)
            self.assertTrue(self.app.messages.sources.validate_reference(source, observed_at=self.clock.now()).is_success)
        self.assertEqual(album.observability_page(self.context, self.clock.now())["total"], 2)
        selected = page["items"][0]
        self.app.messages.sources.revoke(selected.parent, expected_governance_revision=1,
                                         observed_at=self.clock.now()).unwrap()
        self.assertEqual(len(album.album_page(self.context, self.clock.now())["items"]), 1)
        self.assert_no_reply_work(event)

    async def test_native_album_retains_originals_after_provider_package_upgrade(self):
        event, request = self.prepare("native-image-upgrade")
        generated = self.result(await self.call(event, request, "image.generate",
            {"prompt": "红色"}, "upgrade-generate"), "red")
        artifact = json.loads(generated["payload"])["native"]["artifacts"][0]["artifact_id"]
        edited = self.result(await self.call(event, request, "image.edit",
            {"prompt": "蓝色", "artifact_id": artifact}, "upgrade-edit"), "blue")
        album = self.app.messages.generated_images
        external = self.app.tools.external
        old_providers = dict(external.providers)
        old_package = old_providers["image.generate"].descriptor.package
        new_package = replace(old_package, package_version=old_package.package_version + ".upgrade",
                              artifact_hash="upgraded-image-package")
        capabilities = CapabilityRepository(self.runtime)
        require(capabilities.register_descriptor(new_package, registered_at=self.clock.now()))
        require(capabilities.update(extension_id=new_package.extension_id, package_id=new_package.package_id,
            package_version=new_package.package_version, expected_revision=1, observed_at=self.clock.now()))
        providers = {}
        for action, provider in old_providers.items():
            if provider.descriptor.package != old_package:
                providers[action] = provider
                continue
            old = provider.descriptor
            schema = json.loads(old.tool.parameters_json)
            schema["properties"]["new_option"] = {"type": "string"}
            tool = replace(old.tool, description=old.tool.description + " (updated)", parameters_json=encode(schema))
            binding = replace(old.binding, version=new_package.package_version, generation="new-generation")
            descriptor = replace(old, package=new_package, tool=tool, binding=binding)
            providers[action] = replace(provider, descriptor=descriptor, current_binding=lambda binding=binding: binding)
        external.providers = providers

        first = album.album_page(self.context, self.clock.now(), limit=1)
        second = album.album_page(self.context, self.clock.now(), limit=1, cursor=first["next_cursor"])
        items = (*first["items"], *second["items"])
        self.assertEqual({item.image_id for item in items}, {
            "generated-image:" + generated["invocation_id"] + ":0",
            "generated-image:" + edited["invocation_id"] + ":0"})
        self.assertEqual(album.album_image(self.context, self.clock.now(), artifact).content, image_bytes("red"))
        for item in items:
            self.assertEqual(album.album_image(self.context, self.clock.now(), item.image_id), item)
        self.assertEqual(len(self.image_backend.calls), 2)

        key = "astrbot.core-tool." + generated["invocation_id"]
        operation = json.loads(self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (key,))["value"])
        task_id = operation["task_id"]
        record = self.runtime.read_one("SELECT record_json FROM execution_tasks WHERE task_id=?", (task_id,))["record_json"]
        forged = json.loads(record)
        forged["definition"]["version"] = "forged"
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET record_json=? WHERE task_id=?", (encode(forged), task_id))
        with self.assertRaises(TaskError):
            album.album_image(self.context, self.clock.now(), artifact)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET record_json=? WHERE task_id=?", (record, task_id))

        declaration = json.loads(operation["provider_json"])
        declaration["package"]["artifact_hash"] = "forged-package"
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value=? WHERE key=?",
                (encode({**operation, "provider_json": encode(declaration)}), key))
        with self.assertRaises(TaskError):
            album.album_image(self.context, self.clock.now(), artifact)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value=? WHERE key=?", (encode(operation), key))

        provider = providers["image.generate"]
        changed_binding = replace(provider.descriptor.binding, provider="other-provider")
        providers["image.generate"] = replace(provider, descriptor=replace(provider.descriptor, binding=changed_binding),
                                               current_binding=lambda: changed_binding)
        with self.assertRaises(TaskError):
            album.album_image(self.context, self.clock.now(), artifact)
        providers["image.generate"] = provider

        selected = album.album_image(self.context, self.clock.now(), artifact)
        self.app.messages.sources.revoke(selected.parent, expected_governance_revision=1,
                                         observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError):
            album.album_image(self.context, self.clock.now(), artifact)
        self.assertEqual(len(album.album_page(self.context, self.clock.now())["items"]), 1)
        self.assert_no_reply_work(event)

    async def test_native_artifact_id_still_resolves_after_more_than_one_page_of_new_images(self):
        event, request = self.prepare("native-image-artifact-retention")
        first = await self.call(event, request, "image.generate", {"prompt": "第一张"}, "retention-000")
        first_result = self.result(first, "red")
        first_artifact = json.loads(first_result["payload"])["native"]["artifacts"][0]["artifact_id"]

        # The historical UI once retained only a bounded page of recent results.
        # Exercise the real image operation path so the stored artifact, source,
        # and receipt all have the same shape as a normal provider generation.
        for index in range(1, 130):
            received = await self.call(event, request, "image.generate", {"prompt": f"第 {index + 1} 张"},
                                       f"retention-{index:03d}")
            self.result(received, "red")

        resolved = self.app.messages.generated_images.album_image(
            self.context, self.clock.now(), first_artifact)
        self.assertEqual(resolved.image_id, "generated-image:" + first_result["invocation_id"] + ":0")
        self.assertEqual(resolved.content, image_bytes("red"))
        self.assertEqual(len(self.image_backend.calls), 130)
        self.assert_no_reply_work(event)

    async def test_native_album_ignores_json_context_rows_with_unexpected_shapes(self):
        event, request = self.prepare("native-image-context-shapes")
        received = await self.call(event, request, "image.generate", {"prompt": "红色"}, "context-shapes")
        result = self.result(received, "red")
        artifact_id = json.loads(result["payload"])["native"]["artifacts"][0]["artifact_id"]
        malformed_contexts = ("[]", "null", '"legacy string"',
                              '{"role_binding":[]}',
                              '{"role_binding":{"role":[],"scope":[]}}')
        with self.runtime.transaction() as connection:
            for index, context_json in enumerate(malformed_contexts):
                connection.execute("""INSERT INTO conversation_native_turns
                    (turn_id, context_key, context_json, role_instruction, input_observation_id,
                     observed_at, generation_state, phase_at, last_sent_at)
                    VALUES (?, ?, ?, '', NULL, ?, 'completed', ?, NULL)""",
                    (f"malformed-album-context-{index}", f"legacy-context-{index}", context_json,
                     self.clock.now().isoformat(), self.clock.now().isoformat()))

        resolved = self.app.messages.generated_images.album_image(self.context, self.clock.now(), artifact_id)
        self.assertEqual(resolved.image_id, "generated-image:" + result["invocation_id"] + ":0")
        self.assertEqual(resolved.content, image_bytes("red"))
        self.assert_no_reply_work(event)

    async def test_native_album_handles_many_historical_origin_keys_under_a_low_sqlite_bind_limit(self):
        event, request = self.prepare("native-image-origin-key-limit")
        received = await self.call(event, request, "image.generate", {"prompt": "红色"}, "origin-key-limit")
        result = self.result(received, "red")
        artifact_id = json.loads(result["payload"])["native"]["artifacts"][0]["artifact_id"]
        context_json = encode(self.context)
        with self.runtime.transaction() as connection:
            for index in range(80):
                connection.execute("""INSERT INTO conversation_native_turns
                    (turn_id, context_key, context_json, role_instruction, input_observation_id,
                     observed_at, generation_state, phase_at, last_sent_at)
                    VALUES (?, ?, ?, '', NULL, ?, 'completed', ?, NULL)""",
                    (f"historical-album-context-{index}", f"historical-context-{index}", context_json,
                     self.clock.now().isoformat(), self.clock.now().isoformat()))

        # The old one-placeholder-per-origin-key SQL exceeded SQLite's variable
        # limit as soon as a role accumulated enough windows. Keep the limit low
        # so this test exercises the production query shape rather than the
        # default build's generous limit.
        with self.runtime.read_snapshot() as connection:
            previous_limit = connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 8)
            try:
                page = self.app.messages.generated_images.album_page(self.context, self.clock.now(), limit=1)
                resolved = self.app.messages.generated_images.album_image(
                    self.context, self.clock.now(), artifact_id)
            finally:
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)
        self.assertEqual(page["items"][0].content, image_bytes("red"))
        self.assertEqual(resolved.content, image_bytes("red"))
        self.assert_no_reply_work(event)

    async def test_native_album_exhausted_history_has_no_next_cursor(self):
        event, request = self.prepare("native-image-exhausted-history")
        received = await self.call(event, request, "image.generate", {"prompt": "红色"}, "exhausted-history")
        result = self.result(received, "red")
        metadata_key = "astrbot.core-tool." + result["invocation_id"]
        saved = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key=?", (metadata_key,))["value"])
        # More than one scan window of stale receipts must not hide the final
        # valid image or advertise another page after the history is exhausted.
        with self.runtime.transaction() as connection:
            for index in range(128):
                stale = dict(saved, invocation_id=f"zz-stale-album-{index:03d}")
                connection.execute("INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                                   ("astrbot.core-tool." + stale["invocation_id"], encode(stale)))

        page = self.app.messages.generated_images.album_page(self.context, self.clock.now(), limit=2)
        self.assertEqual([item.image_id for item in page["items"]],
                         ["generated-image:" + result["invocation_id"] + ":0"])
        self.assertIsNone(page["next_cursor"])
        self.assert_no_reply_work(event)

    async def test_native_generated_image_builds_qzone_upload_request(self):
        from companion_v2.capabilities.qzone_access import QzoneAccess
        from companion_v2.qzone.images import album_images
        from companion_v2.qzone.protocol import credential_from_cookies, parse_cookie_text, upload_image_request
        from companion_v2.sharing.planner import ShareCatalog, SharePlanner
        from companion_v2.sources.repositories import AssetRepository, SourceRepository

        event, request = self.prepare("native-image-qzone-upload")
        received = await self.call(event, request, "image.generate", {"prompt": "自拍"}, "qzone-selfie")
        result = self.result(received, "red")
        artifact_id = json.loads(result["payload"])["native"]["artifacts"][0]["artifact_id"]
        planner = SharePlanner(sources=SourceRepository(self.runtime), assets=AssetRepository(self.runtime),
                               catalog=ShareCatalog())
        with self.assertRaises(TaskError) as denied:
            album_images(self.app, self.context, self.clock.now(), [artifact_id], planner=planner)
        self.assertEqual(denied.exception.code, "authorization_denied")

        access = QzoneAccess(self.app, self.context)
        status = access.status(self.core, self.clock.now())
        installed = access.configure(self.core, {"operation_key": "enable-image-share",
            "edit_token": status["edit_token"], "enabled": True}, self.clock.now())["status"]
        self.assertTrue(installed["publish_granted"])
        images, candidates = album_images(self.app, self.context, self.clock.now(), [artifact_id], planner=planner)
        self.assertEqual(len(images), 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(images[0].content, image_bytes("red"))
        self.assertEqual(images[0].mime_type, "image/png")
        credential = credential_from_cookies(parse_cookie_text("uin=o123456789; p_skey=TEST; qzonetoken=TOKEN"))
        upload = upload_image_request(credential, images[0])
        self.assertEqual(upload.description, "image.upload")
        self.assertEqual(base64.b64decode(upload.data["picfile"], validate=True), image_bytes("red"))
        self.assertEqual(upload.data["filename"], f"{images[0].content_hash[:16]}.png")
        self.assertEqual(upload.data["qzonetoken"], "TOKEN")

        revoked = access.configure(self.core, {"operation_key": "revoke-image-share",
            "edit_token": installed["edit_token"], "enabled": False}, self.clock.now())["status"]
        self.assertFalse(revoked["publish_granted"])
        with self.assertRaises(TaskError) as denied:
            album_images(self.app, self.context, self.clock.now(), [artifact_id], planner=planner)
        self.assertEqual(denied.exception.code, "authorization_revoked")
        self.assert_no_reply_work(event)

    async def test_corrupt_native_artifact_receipt_is_refused_without_crashing(self):
        event, request = self.prepare("native-image-corrupt-receipt")
        generated = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-corrupt")
        result = self.result(generated, "red")
        artifact_id = json.loads(result["payload"])["native"]["artifacts"][0]["artifact_id"]
        saved = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                      ("astrbot.core-tool." + result["invocation_id"],))
        operation = json.loads(saved["value"])
        reference = operation["source"]
        source = self.runtime.read_one("""SELECT payload_json FROM sources
            WHERE source_id=? AND lineage_id=? AND source_revision=?""",
            (reference["source_id"], reference["version"]["lineage_id"],
             reference["version"]["revision"]))
        document = json.loads(json.loads(source["payload_json"]))
        document["native"]["artifacts"] = [None]
        malformed = encode(document)
        with self.runtime.transaction() as connection:
            connection.execute("""UPDATE sources SET payload_json=?,content_hash=?
                WHERE source_id=? AND lineage_id=? AND source_revision=?""",
                (encode(malformed), digest(malformed), reference["source_id"],
                 reference["version"]["lineage_id"], reference["version"]["revision"]))

        with self.assertRaisesRegex(TaskError, "selected album image is unavailable"):
            self.app.messages.generated_images.album_image(self.context, self.clock.now(), artifact_id)
        self.assert_no_reply_work(event)

    async def test_native_replay_rejects_execution_ledger_receipt_divergence(self):
        event, request = self.prepare("native-image-receipt-divergence")
        generated = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-receipt")
        result = self.result(generated, "red")
        invocation_id = result["invocation_id"]
        metadata_key = "astrbot.core-tool." + invocation_id
        saved = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (metadata_key,))
        operation = json.loads(saved["value"])
        task_id = operation["task_id"]
        task = self.runtime.read_one("SELECT record_json FROM execution_tasks WHERE task_id = ?", (task_id,))
        original_task = json.loads(task["record_json"])

        for field, forged in (("effect", "none"), ("operation_id", "forged-operation"),
                              ("result_ref", "forged-result"), ("receipt_ref", "forged-receipt")):
            with self.subTest(field=field):
                tampered = dict(original_task)
                tampered[field] = forged
                with self.runtime.transaction() as connection:
                    connection.execute("UPDATE execution_tasks SET record_json = ? WHERE task_id = ?",
                                       (encode(tampered), task_id))
                replay = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-receipt")
                self.assertEqual(json.loads(replay)["code"], "core_tool_result_unresolved")
                with self.runtime.transaction() as connection:
                    connection.execute("UPDATE execution_tasks SET record_json = ? WHERE task_id = ?",
                                       (encode(original_task), task_id))

        tampered_result = dict(operation["result"])
        tampered_result["receipt_ref"] = "forged-native-receipt"
        tampered_operation = dict(operation, result=tampered_result)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                               (encode(tampered_operation), metadata_key))
        replay = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-receipt")
        self.assertEqual(json.loads(replay)["code"], "core_tool_result_unresolved")
        self.assert_no_reply_work(event)

    async def test_native_replay_rejects_source_hash_divergence(self):
        event, request = self.prepare("native-image-source-divergence")
        generated = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-source")
        result = self.result(generated, "red")
        metadata_key = "astrbot.core-tool." + result["invocation_id"]
        operation = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?", (metadata_key,))["value"])
        reference = operation["source"]
        source_key = (reference["source_id"], reference["version"]["lineage_id"], reference["version"]["revision"])
        source = self.runtime.read_one("SELECT payload_json FROM sources "
                                       "WHERE source_id = ? AND lineage_id = ? AND source_revision = ?", source_key)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET payload_json = ? "
                               "WHERE source_id = ? AND lineage_id = ? AND source_revision = ?",
                               (json.dumps("tampered native source", ensure_ascii=False), *source_key))
        replay = await self.call(event, request, "image.generate", {"prompt": "红色"}, "generate-source")
        replayed = json.loads(replay)
        self.assertEqual((replayed["state"], replayed["effect"], replayed["code"]),
                         ("succeeded", "committed", "core_tool_result_changed"))
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET payload_json = ? WHERE source_id = ? AND lineage_id = ? "
                               "AND source_revision = ?", (source["payload_json"], *source_key))
        self.assert_no_reply_work(event)

    async def test_edit_reads_actual_current_native_attachment_only_on_tool_invocation(self):
        event, request = self.prepare("native-edit-input", (image_bytes("red"),))
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_image_payloads", ())["n"], 0)
        received = await self.call(event, request, "image.edit", {"prompt": "蓝色"}, "edit-input")
        self.result(received, "blue")
        reference = self.image_backend.calls[0][1][0]
        self.assertEqual(reference.data, image_bytes("red"))
        self.assertEqual(reference.role, "edit_source")
        self.assert_no_reply_work(event)

    async def test_empty_artifact_id_uses_selected_saved_reference(self):
        reference_config = ReferenceConfigurationOwner(self.runtime)
        reference_config.write(reference_target(self.context), reference_fields(),
            changes={REFERENCE_GROUP + ".enabled": True}, reset=(),
            expected_revision=0, observed_at=self.clock.now())
        payload = image_bytes("purple")
        asset = ReferenceAsset("saved-identity", ReferenceKind.REFERENCE_IMAGE,
            ReferenceRole.IDENTITY, ContentDigest(hashlib.sha256(payload).hexdigest()),
            len(payload), "image/png", "role-reference:test:saved-identity",
            ReferenceSource.OPERATOR, created_at=self.clock.now())
        ReferenceAssetRepository(self.runtime).put(
            reference_owner_key(self.context), asset, created_at=self.clock.now(), payload=payload)

        event, request = self.prepare("native-edit-empty-artifact")
        tool = request.func_tool.get_tool(native_tool_name("image.edit"))
        self.assertIn("省略此字段", tool.parameters["properties"]["artifact_id"]["description"])
        arguments = {"prompt": "保留角色身份，背景改为蓝色", "artifact_id": "",
                     "reference_ids": ["saved-identity"], "reference_indices": [],
                     "reference_roles": ["identity"]}
        received = await self.call(event, request, "image.edit", arguments, "empty-artifact")
        self.result(received, "blue")
        invocation, references = self.image_backend.calls[0]
        self.assertEqual(invocation.envelope["arguments"], arguments)
        self.assertEqual(invocation.envelope["arguments_hash"], digest(arguments))
        self.assertEqual(len(references), 1)
        self.assertEqual((references[0].role, references[0].data), ("identity", payload))
        self.assert_no_reply_work(event)

    async def test_mask_edit_receives_original_input_and_mask_through_native_references(self):
        mask = PILImage.new("RGB", (3, 2), "black")
        mask.putpixel((0, 0), (255, 255, 255))
        mask_bytes = io.BytesIO()
        mask.save(mask_bytes, format="PNG")
        event, request = self.prepare("native-mask-input", (image_bytes("red"), mask_bytes.getvalue()))
        received = await self.call(event, request, "image.mask_edit", {
            "prompt": "蓝色", "source_index": 0, "mask_index": 1}, "mask-input")
        self.assertIsInstance(received, CallToolResult, received)
        self.assertEqual(json.loads(received.content[0].text)["state"], "succeeded")
        with PILImage.open(io.BytesIO(base64.b64decode(received.content[1].data))) as final:
            self.assertEqual(final.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(final.getpixel((1, 0)), (255, 0, 0))
        references = self.image_backend.calls[0][1]
        self.assertEqual([reference.role for reference in references], ["edit_source", "edit_mask"])
        self.assertEqual(references[1].data, mask_bytes.getvalue())
        self.assert_no_reply_work(event)


if __name__ == "__main__":
    unittest.main()
