"""End-to-end Issue #657 tests: conversation-triggered Qzone image+text posts."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests import test_astrbot_staging_host as _host_import  # noqa: F401
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.context import Context

from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools, native_tool_name
from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskContext, require
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.qzone.capability import (
    ALBUM_LIST_ACTION,
    CAPABILITIES,
    EXTENSION as QZONE_EXTENSION,
    PUBLISH_ACTION,
    READ_ACTION,
    package_descriptor,
)
from companion_v2.qzone.configuration import GROUP as QZONE_GROUP, QzoneConfigurationOwner, configuration_fields, target_for
from companion_v2.qzone.topic_prompt import CONVERSATION_GUIDANCE
from companion_v2.references.daily_outfit import PREFIX as OUTFIT_PREFIX, daily_outfit_image_id
from companion_v2.storage.image_payloads import store_image
from tests.native_image_support import NativeImageFixture
from tests.test_conversation_images import image_bytes
from tests.test_core_reply_pipeline import PipelineEvent
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_qzone_images import ImageTransport


class QzoneIssue657ConversationTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from companion_v2.storage.native_conversation_schema import STATEMENTS
        with self.runtime.transaction() as connection:
            for statement in STATEMENTS:
                connection.execute(statement)
        self.image_directory = self.root.parent / "image-native-issue657"
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.native_registry._default_handlers = True
        self.qzone_transport = ImageTransport(photos=1, text="雨停后在街角看到一只橘猫，顺手画了下来。", psc=True)
        self.providers = tuple(replace(provider, capabilities=("text", "tools", "image")) for provider in self.providers)
        self.sender = replace(self.sender, supports_images=True)

        self.grant(self.important, SCOPE, PUBLISH_ACTION)
        self.grant(self.important, SCOPE, READ_ACTION)
        self.grant(self.important, SCOPE, "extension.tool.invoke")

        capabilities = CapabilityRepository(self.runtime)
        image_package = self.native_registry.declarations()[0].descriptor.package
        require(capabilities.register_descriptor(image_package, registered_at=NOW))
        require(capabilities.acquire(
            extension_id=image_package.extension_id, package_id=image_package.package_id,
            package_version=image_package.package_version, expected_revision=0, observed_at=NOW,
        ))
        require(capabilities.set_role_enabled(
            extension_id=image_package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW,
        ))
        for cap in image_package.capabilities:
            require(capabilities.set_authorization(
                extension_id=image_package.extension_id, capability_id=cap,
                subject=self.important.subject, scope=SCOPE, state=CapabilityAuthorizationState.ACTIVE,
                expected_revision=0, observed_at=NOW,
            ))

        qzone_pkg = package_descriptor()
        require(capabilities.register_descriptor(qzone_pkg, registered_at=NOW))
        require(capabilities.acquire(
            extension_id=QZONE_EXTENSION, package_id=qzone_pkg.package_id,
            package_version=qzone_pkg.package_version, expected_revision=0, observed_at=NOW,
        ))
        require(capabilities.set_role_enabled(
            extension_id=QZONE_EXTENSION, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW,
        ))
        for cap in CAPABILITIES:
            require(capabilities.set_authorization(
                extension_id=QZONE_EXTENSION, capability_id=cap,
                subject=self.important.subject, scope=SCOPE, state=CapabilityAuthorizationState.ACTIVE,
                expected_revision=0, observed_at=NOW,
            ))

    async def asyncSetUp(self):
        self.app = (await self.start(self.host(
            tool_port=self.native_registry,
            qzone_transport=self.qzone_transport,
        ))).application
        self.addAsyncCleanup(close_core_reply_tools, self.app)
        self.context = TaskContext(
            self.important,
            require(self.identity.resolve_role(SCOPE, observed_at=NOW)),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        config_owner = QzoneConfigurationOwner(self.runtime)
        qzone_target = target_for(self.context)
        fields = configuration_fields()
        snapshot = config_owner.read(qzone_target, fields)
        config_owner.write(
            qzone_target,
            fields,
            changes={
                f"{QZONE_GROUP}.enabled": True,
                f"{QZONE_GROUP}.publish_enabled": True,
                f"{QZONE_GROUP}.topic_auto_enabled": True,
                f"{QZONE_GROUP}.topic_require_confirmation": False,
                f"{QZONE_GROUP}.daily_limits_enabled": False,
            },
            reset=(),
            expected_revision=snapshot.revision,
            observed_at=NOW,
        )
        self.app.qzone.verify_delays = (0.0,)
        self.app.qzone.publish_verify_delays = (0.0,)

    def _prepare(self, event_id, prompt_text):
        event = PipelineEvent()
        event.message_obj = NS(message=[])
        original = NativeConversationInput(
            event_id, self.context.actor.principal,
            ConversationRoute(SCOPE, user=self.context.actor.subject), self.context.audience,
            prompt_text, self.clock.now(), self.context.role_binding.role, "原人格",
        )
        prepared = {"context": self.context, "event_id": event_id, "native_input": original}

        async def authorize(value):
            return self.app.tasks.check_context_access(
                value["context"], action="conversation.reply", observed_at=self.clock.now(),
            )

        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = ProviderRequest(prompt=prompt_text, system_prompt="原人格", func_tool=ToolSet(tools=[]))
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    async def _run_loop(self, event, request, make_text_chat):
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS

        host = Context.__new__(Context)
        platform = NS(meta=lambda: NS(id="qq-main"), send_by_session=AsyncMock())
        host.platform_manager = NS(platform_insts=[platform])
        host.astrbot_config_mgr = NS(get_conf=lambda _: {})
        wrapper = ContextWrapper(context=AstrAgentContext(context=host, event=event))
        text_chat = make_text_chat(wrapper)

        class Hooks(BaseAgentRunHooks):
            async def on_agent_begin(self, run_context):
                observe_core_tool_dispatch_begin(event, run_context)

            async def on_agent_done(self, run_context, response):
                restore_core_tool_dispatch(event, run_context)

        provider = NS(
            provider_config={"id": "synthetic-qzone-model", "modalities": ["text", "image"]},
            get_model=lambda: "synthetic",
            text_chat=text_chat,
        )
        runner = ToolLoopAgentRunner()
        await runner.reset(provider, request, wrapper, FunctionToolExecutor(), Hooks())
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)
        self.addCleanup(restore_core_tool_dispatch, event)
        for _ in range(4):
            _ = [response async for response in runner.step()]
            if runner.done():
                break
        self.assertTrue(runner.done())
        return wrapper

    async def test_prompt_injection_and_conversation_generate_then_publish_qzone(self):
        caption = "雨停后在街角看到一只橘猫，顺手画了下来。"
        self.qzone_transport.body = caption
        event, request = self._prepare("conv-draw-qzone", "把你今天最喜欢的一件事画出来，写两句发空间")
        self.assertIn(CONVERSATION_GUIDANCE, request.system_prompt)
        self.assertIn("artifact_id", request.system_prompt)
        self.assertIsNotNone(request.func_tool.get_tool(native_tool_name(ALBUM_LIST_ACTION)))
        self.assertIsNotNone(request.func_tool.get_tool(native_tool_name(PUBLISH_ACTION)))

        calls = []

        def make_text_chat(wrapper):
            async def text_chat(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return LLMResponse(
                        role="assistant", completion_text="",
                        tools_call_ids=["call-gen-1"],
                        tools_call_name=[native_tool_name("image.generate")],
                        tools_call_args=[{"prompt": "雨后街角暖黄色路灯下的橘猫"}],
                    )
                if len(calls) == 2:
                    tool_msgs = [m.content for m in wrapper.messages
                                 if m.role == "tool" and isinstance(m.content, str)]
                    joined = "\n".join(tool_msgs)
                    import re
                    match = re.search(r"imgart_[a-zA-Z0-9]+", joined)
                    self.assertIsNotNone(match, joined)
                    artifact_id = match.group(0)
                    return LLMResponse(
                        role="assistant", completion_text="",
                        tools_call_ids=["call-pub-1"],
                        tools_call_name=[native_tool_name(PUBLISH_ACTION)],
                        tools_call_args=[{
                            "text": caption,
                            "topic": "life_documentary",
                            "scene": "conversation",
                            "confidence": 0.92,
                            "image_ids": [artifact_id],
                        }],
                    )
                return LLMResponse(role="assistant", completion_text="画好并一起发到 QQ 空间啦。")

            return text_chat

        wrapper = await self._run_loop(event, request, make_text_chat)
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual([r.description for r in self.qzone_transport.sent],
                         ["image.upload", "post.publish", "feed.list"])
        upload_req = self.qzone_transport.sent[0]
        self.assertEqual(base64.b64decode(upload_req.data["picfile"]), image_bytes("red"))
        tool_results = [m.content for m in wrapper.messages if m.role == "tool" and isinstance(m.content, str)]
        pub_json = json.loads(tool_results[-1])
        self.assertEqual((pub_json["state"], pub_json["effect"]), ("succeeded", "committed"))
        payload = json.loads(pub_json["payload"])
        self.assertTrue(payload["verified"])
        self.assertTrue(payload["images_verified"])
        self.assertEqual(payload["image_count"], 1)

    async def test_conversation_lists_album_and_publishes_selected_outfit_photo(self):
        caption = "换上舒服的衣服出门散步，顺便发张照片纪念一下。"
        self.qzone_transport.body = caption
        outfit_bytes = image_bytes("blue")
        owner = self.app.references.owner(self.context)
        marker = store_image(self.runtime.data_root, outfit_bytes, mime_type="image/png").decode("ascii")
        outfit_hash = hashlib.sha256(outfit_bytes).hexdigest()
        outfit_id = daily_outfit_image_id(owner, "2026-10-02", outfit_hash)
        with self.runtime.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (
                    f"{OUTFIT_PREFIX}{owner}:2026-10-02",
                    json.dumps({
                        "task_id": "outfit-conv-1",
                        "photo": {
                            "marker": marker,
                            "content_hash": outfit_hash,
                            "created_at": NOW.isoformat(),
                            "mime_type": "image/png",
                            "width": 3,
                            "height": 2,
                            "size_bytes": len(outfit_bytes),
                            "activities": ["散步"],
                        },
                    }),
                ),
            )

        event, request = self._prepare("conv-album-qzone", "从你相册挑一张今天的穿搭发到空间")
        calls = []

        def make_text_chat(wrapper):
            async def text_chat(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return LLMResponse(
                        role="assistant", completion_text="",
                        tools_call_ids=["call-album-1"],
                        tools_call_name=[native_tool_name(ALBUM_LIST_ACTION)],
                        tools_call_args=[{"limit": 5}],
                    )
                if len(calls) == 2:
                    tool_msgs = [m.content for m in wrapper.messages
                                 if m.role == "tool" and isinstance(m.content, str)]
                    album_res = json.loads(tool_msgs[-1])
                    album_payload = json.loads(album_res["payload"])
                    selected_id = album_payload["items"][0]["image_id"]
                    self.assertEqual(selected_id, outfit_id)
                    return LLMResponse(
                        role="assistant", completion_text="",
                        tools_call_ids=["call-pub-album-1"],
                        tools_call_name=[native_tool_name(PUBLISH_ACTION)],
                        tools_call_args=[{
                            "text": caption,
                            "topic": "life_documentary",
                            "scene": "role_activity",
                            "confidence": 0.9,
                            "image_ids": [selected_id],
                        }],
                    )
                return LLMResponse(role="assistant", completion_text="已经从相册挑好发到空间了。")

            return text_chat

        wrapper = await self._run_loop(event, request, make_text_chat)
        self.assertEqual([r.description for r in self.qzone_transport.sent],
                         ["image.upload", "post.publish", "feed.list"])
        self.assertEqual(base64.b64decode(self.qzone_transport.sent[0].data["picfile"]), outfit_bytes)
        tool_results = [m.content for m in wrapper.messages if m.role == "tool" and isinstance(m.content, str)]
        pub_json = json.loads(tool_results[-1])
        self.assertEqual((pub_json["state"], pub_json["effect"]), ("succeeded", "committed"))
        self.assertTrue(json.loads(pub_json["payload"])["images_verified"])

    async def test_permission_gates_block_revoked_consent_and_unconfirmed_topic(self):
        config_owner = QzoneConfigurationOwner(self.runtime)
        qzone_target = target_for(self.context)
        fields = configuration_fields()
        snapshot = config_owner.read(qzone_target, fields)
        config_owner.write(
            qzone_target,
            fields,
            changes={f"{QZONE_GROUP}.topic_require_confirmation": True},
            reset=(),
            expected_revision=snapshot.revision,
            observed_at=NOW,
        )

        event, request = self._prepare("conv-confirm-gate", "发一条空间说说")
        calls = []

        async def confirm_chat(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return LLMResponse(
                    role="assistant", completion_text="",
                    tools_call_ids=["call-pub-unconfirmed"],
                    tools_call_name=[native_tool_name(PUBLISH_ACTION)],
                    tools_call_args=[{
                        "text": "晚风轻拂，记录一下今天。",
                        "topic": "life_documentary",
                        "scene": "conversation",
                        "confidence": 0.9,
                    }],
                )
            return LLMResponse(role="assistant", completion_text="需要你先确认发布主题。")

        wrapper = await self._run_loop(event, request, lambda _wrapper: confirm_chat)
        tool_results = [m.content for m in wrapper.messages if m.role == "tool" and isinstance(m.content, str)]
        gate_json = json.loads(tool_results[-1])
        self.assertEqual((gate_json["state"], gate_json["effect"], gate_json["code"]),
                         ("failed", "not_committed", "qzone_topic_confirmation_required"))
        self.assertEqual(self.qzone_transport.sent, [])

        # Prepare before revocation to test stale tool invocation rejection,
        # and also verify fresh turns withhold Qzone publish/album tools.
        config_owner.write(
            qzone_target,
            fields,
            changes={f"{QZONE_GROUP}.topic_require_confirmation": False},
            reset=(),
            expected_revision=config_owner.read(qzone_target, fields).revision,
            observed_at=NOW,
        )
        event2, request2 = self._prepare("conv-revoked-gate", "看看相册并发一条空间")
        capabilities = CapabilityRepository(self.runtime)
        current = require(capabilities.consent(
            extension_id=QZONE_EXTENSION, capability_id=PUBLISH_ACTION,
            subject=self.important.subject, scope=SCOPE,
        ))
        require(capabilities.set_authorization(
            extension_id=QZONE_EXTENSION, capability_id=PUBLISH_ACTION,
            subject=self.important.subject, scope=SCOPE,
            state=CapabilityAuthorizationState.REVOKED,
            expected_revision=current.revision, observed_at=NOW,
        ))

        _, withheld_request = self._prepare("conv-revoked-withheld", "看看相册并发一条空间")
        self.assertIsNone(withheld_request.func_tool.get_tool(native_tool_name(ALBUM_LIST_ACTION)))
        self.assertIsNone(withheld_request.func_tool.get_tool(native_tool_name(PUBLISH_ACTION)))
        self.assertNotIn(CONVERSATION_GUIDANCE, withheld_request.system_prompt)

        revoked_calls = []

        async def revoked_chat(**kwargs):
            revoked_calls.append(kwargs)
            if len(revoked_calls) == 1:
                return LLMResponse(
                    role="assistant", completion_text="",
                    tools_call_ids=["call-album-revoked"],
                    tools_call_name=[native_tool_name(ALBUM_LIST_ACTION)],
                    tools_call_args=[{"limit": 3}],
                )
            if len(revoked_calls) == 2:
                return LLMResponse(
                    role="assistant", completion_text="",
                    tools_call_ids=["call-pub-revoked"],
                    tools_call_name=[native_tool_name(PUBLISH_ACTION)],
                    tools_call_args=[{
                        "text": "不应发出的说说。",
                        "topic": "life_documentary",
                        "scene": "conversation",
                        "confidence": 0.9,
                        "topic_confirmed": True,
                    }],
                )
            return LLMResponse(role="assistant", completion_text="未授权，已取消。")

        wrapper2 = await self._run_loop(event2, request2, lambda _wrapper: revoked_chat)
        tool_results2 = [json.loads(m.content) for m in wrapper2.messages
                         if m.role == "tool" and isinstance(m.content, str)]
        self.assertEqual((tool_results2[0]["state"], tool_results2[0]["effect"]), ("failed", "none"))
        self.assertIn(tool_results2[0]["code"], ("capability_consent_revoked", "core_tool_unavailable"))
        self.assertEqual(tool_results2[1]["state"], "failed")
        self.assertIn(tool_results2[1]["effect"], ("none", "not_committed"))
        self.assertIn(tool_results2[1]["code"], ("capability_consent_revoked", "core_tool_unavailable"))
        self.assertEqual(self.qzone_transport.sent, [])


if __name__ == "__main__":
    unittest.main()
