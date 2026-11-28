"""Tests for image subject_mode, NAI transient errors, terminal receipts, turn cap, and image history budget."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult
from PIL import Image

from astrbot_host.native_extensions.image_native.adapter import NativeImageProvider
from astrbot_host.native_extensions.image_native.nai_backend import NAIImageBackend
from astrbot_host.native_extensions.image_native.output_routing import OutputRoutingBackend
from astrbot_host.native_extensions.image_native.owner import NativeImageOwner
from astrbot_host.native_extensions.image_native.types import ImageInvocation, NativeImageError, ReferenceImage
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.foreground import PromptMaterial
from companion_v2.conversation.media.generated import OWNER as GENERATED_IMAGE_OWNER, _GeneratedPromptImage
from companion_v2.conversation.media.models import ImageDescriptor, PromptImage
from companion_v2.conversation.media.screen import ScreenPromptImage
from companion_v2.device.prompt_image import PhonePromptImage
from companion_v2.conversation.media.output_settings import (
    DEFAULTS,
    GROUP as OUTPUT_GROUP,
    ImageOutputConfigurationOwner,
    apply_prompts,
    configuration_fields as output_fields,
    effective_settings,
    resolve_image_subject_intent,
)
from companion_v2.conversation.tool_failures import failure_guidance
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.model_gateway.media_budget import (
    ImageHistoryBudgetPolicy,
    estimate_inline_image_weight,
    select_inline_image_indices,
)
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.storage.runtime import StorageRuntime
from tests.native_adapter_support import NativeFixture
from tests.native_image_support import NativeImageFixture
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


def noisy_png_bytes(width: int, height: int, *, seed: int = 0) -> bytes:
    """Create a deterministic high-entropy PNG with nontrivial byte length."""
    raw = bytearray(width * height * 3)
    state = (seed * 2654435761 + 17) & 0xFFFFFFFF
    for idx in range(len(raw)):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        raw[idx] = (state >> 16) & 0xFF
    img = Image.frombytes("RGB", (width, height), bytes(raw))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class SubjectModeAndOutputSettingsTests(unittest.TestCase):
    def test_subject_mode_controls_fixed_prompt_injection_and_style_preservation(self):
        values = {
            **DEFAULTS,
            "prompt_format": "traditional",
            "style": "anime",
            "style_prompt": "artist:sample",
            "text2img_fixed_prompt": "silver hair, green eyes, fixed_role_anchor",
            "edit_fixed_prompt": "fixed_edit_role_anchor",
            "apply_fixed_prompt_only_for_character": True,
        }

        char_prompt = apply_prompts(
            {"prompt": "at a cafe", "subject_mode": "character"},
            "image.generate",
            values,
        )
        self.assertIn("fixed_role_anchor", char_prompt["prompt"])
        self.assertIn("anime illustration", char_prompt["prompt"])

        scene_prompt = apply_prompts(
            {"prompt": "quiet mountain sunset", "subject_mode": "scene_only"},
            "image.generate",
            values,
        )
        self.assertNotIn("fixed_role_anchor", scene_prompt["prompt"])
        self.assertIn("anime illustration", scene_prompt["prompt"])
        self.assertIn("quiet mountain sunset", scene_prompt["prompt"])

        edit_preserve = apply_prompts(
            {"prompt": "make the sky dusk purple", "subject_mode": "edit_preserve"},
            "image.edit",
            values,
        )
        self.assertNotIn("fixed_edit_role_anchor", edit_preserve["prompt"])
        self.assertNotIn("anime illustration", edit_preserve["prompt"])
        self.assertNotIn("artist:sample", edit_preserve["prompt"])
        self.assertEqual(edit_preserve["prompt"], "make the sky dusk purple")

        # Disabling the toggle restores legacy always-inject behavior for fixed prompts.
        legacy_values = {**values, "apply_fixed_prompt_only_for_character": False}
        legacy_scene = apply_prompts(
            {"prompt": "quiet mountain sunset", "subject_mode": "scene_only"},
            "image.generate",
            legacy_values,
        )
        self.assertIn("fixed_role_anchor", legacy_scene["prompt"])

    def test_auto_guardrails_detect_non_character_prompts_and_pure_edit_references(self):
        values = {
            **DEFAULTS,
            "text2img_fixed_prompt": "role_hair_anchor",
            "edit_fixed_prompt": "role_edit_anchor",
            "apply_fixed_prompt_only_for_character": True,
        }

        no_humans = resolve_image_subject_intent(
            {"prompt": "autumn forest stream, no humans, landscape only"},
            "image.generate",
            values,
        )
        self.assertEqual(no_humans["subject_mode"], "scene_only")
        self.assertFalse(no_humans["inject_fixed_prompt"])
        self.assertFalse(no_humans["allow_fallback_references"])

        neg_excluded = resolve_image_subject_intent(
            {"prompt": "wooden desk with coffee cup", "negative_prompt": "1girl, human, 人物"},
            "image.generate",
            values,
        )
        self.assertEqual(neg_excluded["subject_mode"], "scene_only")
        self.assertFalse(neg_excluded["inject_fixed_prompt"])

        selfie_override = resolve_image_subject_intent(
            {"prompt": "selfie by the autumn lake, no humans in background"},
            "image.generate",
            values,
        )
        self.assertEqual(selfie_override["subject_mode"], "character")
        self.assertTrue(selfie_override["inject_fixed_prompt"])

        pure_edit = resolve_image_subject_intent(
            {"prompt": "change hat color to blue"},
            "image.edit",
            values,
            reference_roles=("edit_source",),
        )
        self.assertEqual(pure_edit["subject_mode"], "edit_preserve")
        self.assertFalse(pure_edit["inject_fixed_prompt"])
        self.assertTrue(pure_edit["preserve_source_style"])
        self.assertFalse(pure_edit["allow_fallback_references"])

        identity_edit = resolve_image_subject_intent(
            {"prompt": "put me into this cafe scene"},
            "image.edit",
            values,
            reference_roles=("edit_source", "identity"),
        )
        self.assertEqual(identity_edit["subject_mode"], "character")
        self.assertTrue(identity_edit["inject_fixed_prompt"])
        self.assertFalse(identity_edit["preserve_source_style"])
        self.assertTrue(identity_edit["allow_fallback_references"])

        explicit_character_edit = resolve_image_subject_intent(
            {"prompt": "keep me smiling", "subject_mode": "character"},
            "image.edit",
            values,
            reference_roles=("edit_source",),
        )
        self.assertEqual(explicit_character_edit["subject_mode"], "character")
        self.assertTrue(explicit_character_edit["inject_fixed_prompt"])

    def test_config_owner_validates_max_calls_per_turn_and_toggle(self):
        temporary = TemporaryDirectory(prefix="companion-image-cfg-")
        self.addCleanup(temporary.cleanup)
        installation = "inst-image-output"
        runtime = StorageRuntime.open(data_root=Path(temporary.name) / "v2", installation_id=installation)
        self.addCleanup(runtime.close)
        role = SubjectRef(installation, SubjectKind.BOT, "role-a")
        target = ConfigTarget(installation, ConfigLayer.ROLE, role, bot_id="bot-a")
        owner = ImageOutputConfigurationOwner(runtime)
        fields = output_fields()
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)

        for invalid in (0, -2, 21):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TaskError):
                    owner.write(
                        target, fields, reset=(),
                        expected_revision=owner.read(target, fields).revision,
                        observed_at=now,
                        changes={OUTPUT_GROUP + ".max_calls_per_turn": invalid},
                    )

        for valid in (-1, 1, 5, 20):
            owner.write(
                target, fields, reset=(),
                expected_revision=owner.read(target, fields).revision,
                observed_at=now,
                changes={
                    OUTPUT_GROUP + ".max_calls_per_turn": valid,
                    OUTPUT_GROUP + ".proactive_max_daily": -1,
                    OUTPUT_GROUP + ".apply_fixed_prompt_only_for_character": False,
                },
            )
        context = NS(scope=NS(installation_id=installation, bot_id="bot-a"), role_binding=NS(role=role))
        loaded = effective_settings(runtime, context)
        self.assertEqual(loaded["max_calls_per_turn"], 20)
        self.assertEqual(loaded["proactive_max_daily"], -1)
        self.assertIs(loaded["apply_fixed_prompt_only_for_character"], False)

        # Verify all registered integer fields preserve -1 (unlimited) lower bound.
        int_fields = {f.field_id: f for f in fields if f.field_id.endswith(("max_calls_per_turn", "proactive_max_daily"))}
        self.assertEqual(int_fields[OUTPUT_GROUP + ".proactive_max_daily"].minimum, -1)
        self.assertEqual(int_fields[OUTPUT_GROUP + ".proactive_max_daily"].maximum, 100)
        self.assertEqual(int_fields[OUTPUT_GROUP + ".max_calls_per_turn"].minimum, -1)
        self.assertEqual(int_fields[OUTPUT_GROUP + ".max_calls_per_turn"].maximum, 20)


class NativeAdapterAndNAIContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-image-nai-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.requests = []
        self.api = NS(
            status=lambda: {
                "call_mode": "novelai",
                "reference_supported": True,
                "operations": ["generate", "edit"],
                "prompt_format": "nai",
            },
            capability_status=lambda owner: {"available": True, "reason": "ready", "operations": ["generate", "edit"]},
            generate_for_companion=AsyncMock(side_effect=self._generate),
        )
        self.metadata = NS(activated=True, star_cls=NS(extension_api=self.api))
        self.context = NS(get_registered_star=lambda name: self.metadata)
        self.backend = NAIImageBackend(self.context, self.root / "transport")

    async def _generate(self, owner, request):
        self.requests.append(request)
        path = self.root / "out.png"
        path.write_bytes(image_bytes("green"))
        return {"handled": True, "image_path": str(path)}

    async def _dispatch(self):
        return None

    async def test_nai_forwards_explicit_image_intent_and_infers_edit_preserve_default(self):
        scene_inv = ImageInvocation(
            "scope", "req-1", "inv-1", "origin", "generate", "empty beach at dawn",
            envelope={
                "nai_prompt": {"positive": "empty beach at dawn", "negative": ""},
                "image_intent": {
                    "subject_mode": "scene_only",
                    "preserve_source_style": False,
                    "allow_fallback_references": False,
                },
            },
        )
        await self.backend.execute(scene_inv, (), self._dispatch)
        req = self.requests[-1]
        self.assertEqual(req["workflow_kind"], "generate")
        self.assertEqual(req["subject_mode"], "scene_only")
        self.assertIs(req["preserve_source_style"], False)
        self.assertIs(req["allow_fallback_references"], False)

        raw = image_bytes("red")
        ref = ReferenceImage("ref-0", raw, "src-0", hashlib.sha256(raw).hexdigest(), role="edit_source")
        edit_inv = ImageInvocation("scope", "req-2", "inv-2", "origin", "edit", "change jacket to blue",
                                   reference_ids=("ref-0",))
        await self.backend.execute(edit_inv, (ref,), self._dispatch)
        edit_req = self.requests[-1]
        self.assertEqual(edit_req["workflow_kind"], "edit")
        self.assertEqual(edit_req["reference_roles"], ["edit_source"])
        self.assertEqual(edit_req["subject_mode"], "edit_preserve")
        self.assertIs(edit_req["preserve_source_style"], True)
        self.assertIs(edit_req["allow_fallback_references"], False)

    async def test_nai_classifies_transient_upstream_and_auth_errors_without_switching_paid_backends(self):
        cases = (
            (
                {"handled": True, "image_path": "", "reason": "upstream_unavailable", "http_status": 503,
                 "retryable": True, "note": "🔥 上游返回 HTTP 503：服务暂时不可用"},
                "nai_upstream_unavailable",
                503,
                False,
                True,
            ),
            (
                {"handled": True, "image_path": "", "http_status": 502, "retryable": True,
                 "note": "💥 Bad Gateway https://secret.host/v1"},
                "nai_upstream_unavailable",
                502,
                False,
                True,
            ),
            (
                {"handled": True, "image_path": "", "reason": "http_504",
                 "note": "🔥 上游返回 HTTP 504：网关超时"},
                "nai_upstream_unavailable",
                504,
                False,
                True,
            ),
            (
                {"handled": True, "image_path": "", "reason": "rate_limited", "http_status": 429,
                 "retryable": True, "note": "⚠️ 触发限流 HTTP 429"},
                "backend_rate_limited",
                429,
                False,
                True,
            ),
            (
                {"handled": True, "image_path": "", "reason": "timeout", "retryable": True,
                 "note": "⏱ 生图超时，请稍后重试"},
                "nai_generation_timed_out",
                0,
                True,
                True,
            ),
        )
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "auto", "sd_webui": {"base_url": "http://example.test"},
             "operation_routes": {"generate": ["nai", "sd_webui"], "edit": []}},
            self.root / "routes",
            context=self.context,
        )
        for payload, expected_code, expected_status, expected_unconfirmed, expected_retryable in cases:
            with self.subTest(expected_code=expected_code, status=expected_status):
                self.api.generate_for_companion.side_effect = None
                self.api.generate_for_companion.return_value = payload
                with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
                    local.return_value.operations = ("generate",)
                    local.return_value.execute = AsyncMock()
                    with self.assertRaises(NativeImageError) as raised:
                        await router.execute(
                            ImageInvocation("scope", "req", "inv", "origin", "generate", "sky"),
                            (),
                            self._dispatch,
                        )
                    local.return_value.execute.assert_not_awaited()
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(raised.exception.unconfirmed, expected_unconfirmed)
                diag = raised.exception.diagnostics[0]
                self.assertEqual(diag["status"], expected_status)
                self.assertIs(diag.get("retryable"), expected_retryable)
                self.assertNotIn("secret.host", diag["detail"])

        # Confirmed 401 auth refusal classifies as nai_plugin_unavailable (unconfirmed=False)
        # and safely allows fallback to the next configured backend.
        self.api.generate_for_companion.side_effect = None
        self.api.generate_for_companion.return_value = {
            "handled": True, "image_path": "", "reason": "http_401", "http_status": 401,
            "retryable": False, "note": "❌ 认证失败 HTTP 401",
        }
        with self.assertRaises(NativeImageError) as auth_err:
            await self.backend.execute(
                ImageInvocation("scope", "req-auth", "inv-auth", "origin", "generate", "sky"),
                (),
                self._dispatch,
            )
        self.assertEqual(auth_err.exception.code, "nai_plugin_unavailable")
        self.assertFalse(auth_err.exception.unconfirmed)
        self.assertEqual(auth_err.exception.diagnostics[0]["status"], 401)
        self.assertIs(auth_err.exception.diagnostics[0]["retryable"], False)
        with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
            local.return_value.operations = ("generate",)
            local.return_value.execute = AsyncMock(return_value="fallback-ok")
            self.assertEqual(
                await router.execute(
                    ImageInvocation("scope", "req-auth-fb", "inv-auth-fb", "origin", "generate", "sky"),
                    (),
                    self._dispatch,
                ),
                "fallback-ok",
            )
            local.return_value.execute.assert_awaited_once()

    async def test_nai_preserves_unconfirmed_for_partial_billing_or_explicit_non_retryable_and_legacy_401(self):
        unconfirmed_cases = (
            (
                {"handled": True, "image_path": "", "reason": "http_503", "http_status": 503,
                 "retryable": False, "note": "🔥 多图参考编码中断（可能已计费），请勿重复重试"},
                503,
            ),
            (
                {"handled": True, "image_path": "", "reason": "http_429", "http_status": 429,
                 "note": "⚠️ 参考图处理触发限流，前面步骤可能已扣费"},
                429,
            ),
            (
                {"handled": True, "image_path": "", "reason": "http_503", "http_status": 503,
                 "retryable": False, "note": "🔥 上游返回 HTTP 503"},
                503,
            ),
            (
                {"handled": True, "image_path": "", "reason": "rate_limited", "http_status": 429,
                 "retryable": False, "note": "⚠️ 触发限流 HTTP 429"},
                429,
            ),
        )
        for payload, expected_status in unconfirmed_cases:
            with self.subTest(reason=payload["reason"], status=expected_status, note=payload["note"][:20]):
                self.api.generate_for_companion.side_effect = None
                self.api.generate_for_companion.return_value = payload
                with self.assertRaises(NativeImageError) as raised:
                    await self.backend.execute(
                        ImageInvocation("scope", "req-unconf", "inv-unconf", "origin", "generate", "sky"),
                        (),
                        self._dispatch,
                    )
                self.assertEqual(raised.exception.code, "nai_generation_unconfirmed")
                self.assertTrue(raised.exception.unconfirmed)
                diag = raised.exception.diagnostics[0]
                self.assertEqual(diag["status"], expected_status)
                self.assertIs(diag.get("retryable"), False)

        # Legacy NAI payloads without integer http_status carrying reason="http_401"/"http_403".
        for legacy_reason, expected_status in (("http_401", 401), ("http_403", 403)):
            with self.subTest(legacy_reason=legacy_reason):
                self.api.generate_for_companion.side_effect = None
                self.api.generate_for_companion.return_value = {
                    "handled": True, "image_path": "", "reason": legacy_reason,
                    "note": f"❌ 上游拒绝 {legacy_reason}",
                }
                with self.assertRaises(NativeImageError) as raised:
                    await self.backend.execute(
                        ImageInvocation("scope", "req-legacy", "inv-legacy", "origin", "generate", "sky"),
                        (),
                        self._dispatch,
                    )
                self.assertEqual(raised.exception.code, "nai_plugin_unavailable")
                self.assertFalse(raised.exception.unconfirmed)
                self.assertEqual(raised.exception.diagnostics[0]["status"], expected_status)
                self.assertIs(raised.exception.diagnostics[0]["retryable"], False)

    def test_timeout_and_upstream_failure_guidance_stops_waiting_deadlock(self):
        unknown_hint = failure_guidance("nai_generation_timed_out", "unknown", action="image.generate")
        self.assertIn("尚未确认", unknown_hint["reason"])
        self.assertIn("不会自动补发", unknown_hint["reason"])
        self.assertIn("无需继续干等", unknown_hint["next_step"])

        upstream_hint = failure_guidance("nai_upstream_unavailable", "failed", action="image.generate")
        self.assertIn("502/503/504", upstream_hint["reason"])
        self.assertIn("不要声称已生成", upstream_hint["next_step"])

        turn_limit_hint = failure_guidance("image_turn_limit_reached", "failed", action="image.generate")
        self.assertIn("单轮上限", turn_limit_hint["reason"])
        self.assertIn("不要继续重复调用", turn_limit_hint["next_step"])

    def test_provider_manifest_and_request_validation_for_subject_mode(self):
        owner = NativeImageOwner(self.root / "store", self.backend)
        provider = NativeImageProvider(owner)
        self.addAsyncCleanup(provider.close)
        manifest = provider.manifest()
        for tool in manifest["tools"]:
            props = tool["parameters"]["properties"]
            self.assertEqual(props["prompt"], {"type": "string"})
            self.assertEqual(props["subject_mode"]["enum"], ["character", "scene_only", "edit_preserve"])

        bad_request = {
            "schema": "companion.native-provider.v1",
            "invocation_id": "inv-1",
            "parent_task_id": "task-1",
            "tool_call_id": "call-1",
            "action": "image.generate",
            "origin_key": "origin",
            "context": {"scope": "s1"},
            "binding": {
                "provider": "image.native",
                "version": provider.version,
                "generation": owner.generation,
                "capability": "image.generate",
            },
            "arguments": {"prompt": "a sunset", "subject_mode": "invalid_mode"},
            "arguments_hash": "0" * 64,
            "deadline_at": "2099-01-01T00:00:00+00:00",
        }
        with self.assertRaises(NativeImageError) as raised:
            provider._request(bad_request)
        self.assertEqual(raised.exception.code, "invalid_subject_mode")
        self.assertFalse(raised.exception.unconfirmed)


class CoreReplyImageReceiptAndTurnLimitTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from astrbot.core.agent.message import Message, ToolCall
        from astrbot.core.message.components import Image as AstrImage
        from astrbot_host.core_reply import prepare_core_reply_event
        from astrbot_host.core_reply_tools import close_core_reply_tools, inject_core_reply_tools, native_tool_name
        from companion_v2.adapters.astrbot.native import NativeConversationInput
        from companion_v2.capabilities.models import CapabilityAuthorizationState
        from companion_v2.capabilities.repositories import CapabilityRepository
        from companion_v2.execution.models import TaskContext, require
        from companion_v2.foundation.refs import AudienceRef
        from companion_v2.identity_policy.personas import ConversationRoute

        self._deps = NS(
            Message=Message, ToolCall=ToolCall, AstrImage=AstrImage,
            prepare_core_reply_event=prepare_core_reply_event,
            close_core_reply_tools=close_core_reply_tools,
            inject_core_reply_tools=inject_core_reply_tools,
            native_tool_name=native_tool_name,
            NativeConversationInput=NativeConversationInput,
            TaskContext=TaskContext, require=require,
            AudienceRef=AudienceRef, ConversationRoute=ConversationRoute,
        )
        self.image_directory = self.root.parent / "image-native-v2-receipt-cap"
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
        self.addAsyncCleanup(self._deps.close_core_reply_tools, self.app)
        self.context = self._deps.TaskContext(
            self.important,
            self._deps.require(self.identity.resolve_role(SCOPE, observed_at=NOW)),
            self._deps.AudienceRef(SCOPE, (self.important.subject,)),
        )

    def prepare(self, event_id="receipt-image"):
        event = FakeEvent()
        event.message_obj = NS(message=[])
        original = self._deps.NativeConversationInput(
            event_id, self.context.actor.principal,
            self._deps.ConversationRoute(SCOPE, user=self.context.actor.subject),
            self.context.audience, "请帮我画图", self.clock.now(),
            self.context.role_binding.role, "原人格",
        )
        prepared = {"context": self.context, "event_id": event_id, "native_input": original}

        async def authorize(value):
            return self.app.tasks.check_context_access(
                value["context"], action="conversation.reply", observed_at=self.clock.now()
            )

        self._deps.prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格")
        self._deps.inject_core_reply_tools(event, request, application=self.app)
        return event, request

    async def call(self, event, request, action, arguments, call_id):
        call = self._deps.ToolCall(
            id=call_id,
            function=self._deps.ToolCall.FunctionBody(
                name=self._deps.native_tool_name(action),
                arguments=json.dumps(arguments),
            ),
        )
        wrapper = NS(context=NS(event=event), messages=[self._deps.Message(role="assistant", tool_calls=[call])])
        tool = request.func_tool.get_tool(self._deps.native_tool_name(action))
        self.assertIsNotNone(tool)
        return await tool.call(wrapper, **arguments)

    def result(self, received, color):
        self.assertIsInstance(received, CallToolResult, received)
        result = json.loads(received.content[0].text)
        self.assertEqual((result["state"], result["effect"]), ("succeeded", "committed"), result)
        self.assertEqual(base64.b64decode(received.content[1].data), image_bytes(color))
        return result

    def configure_images(self, owner, fields, changes):
        target = ConfigTarget(SCOPE.installation_id, ConfigLayer.ROLE,
                              self.context.role_binding.role, bot_id=SCOPE.bot_id)
        owner.write(target, fields, changes=changes, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

    async def test_success_receipt_is_clean_terminal_and_propagates_subject_mode(self):
        self.configure_images(
            ImageOutputConfigurationOwner(self.runtime),
            output_fields(),
            {
                OUTPUT_GROUP + ".text2img_fixed_prompt": "ROLE_FIXED_ANCHOR",
                OUTPUT_GROUP + ".apply_fixed_prompt_only_for_character": True,
            },
        )
        event, request = self.prepare("clean-receipt-turn")
        received = await self.call(
            event, request, "image.generate",
            {"prompt": "quiet misty forest", "subject_mode": "scene_only"},
            "call-scene-1",
        )
        self.assertIsInstance(received, CallToolResult)
        receipt = json.loads(received.content[0].text)
        self.assertIs(receipt["ok"], True)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(receipt["effect"], "committed")
        self.assertEqual(receipt["image_count"], 1)
        self.assertTrue(receipt["artifact_ids"])
        self.assertIn("请勿重复调用生图工具", receipt["status_summary"])
        self.assertNotIn("code", receipt)
        self.assertNotIn("retry_disposition", receipt)

        invocation = self.image_backend.calls[0][0]
        self.assertNotIn("ROLE_FIXED_ANCHOR", invocation.envelope["image_prompt"]["positive"])
        self.assertEqual(
            invocation.envelope["image_intent"],
            {
                "subject_mode": "scene_only",
                "preserve_source_style": False,
                "allow_fallback_references": False,
            },
        )

        # Replay returns byte-for-byte identical result.
        replay = await self.call(
            event, request, "image.generate",
            {"prompt": "quiet misty forest", "subject_mode": "scene_only"},
            "call-scene-1",
        )
        self.assertEqual(replay.content, received.content)
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_turn_image_call_limit_blocks_excess_calls_and_preserves_replays_and_new_turns(self):
        self.configure_images(
            ImageOutputConfigurationOwner(self.runtime),
            output_fields(),
            {OUTPUT_GROUP + ".max_calls_per_turn": 2},
        )
        event, request = self.prepare("turn-limit-1")
        first = await self.call(event, request, "image.generate", {"prompt": "first pic"}, "limit-1")
        self.result(first, "red")
        second = await self.call(event, request, "image.generate", {"prompt": "second pic"}, "limit-2")
        self.result(second, "red")

        # Replaying an already succeeded call_id still returns its receipt without hitting turn limit.
        replay_first = await self.call(event, request, "image.generate", {"prompt": "first pic"}, "limit-1")
        self.assertEqual(replay_first.content, first.content)

        # Third distinct call in the same turn is blocked fail-closed with clear guidance.
        third_raw = await self.call(event, request, "image.generate", {"prompt": "third pic"}, "limit-3")
        third = json.loads(third_raw)
        self.assertEqual(third["state"], "failed")
        self.assertEqual(third["effect"], "none")
        self.assertEqual(third["code"], "image_turn_limit_reached")
        self.assertIn("单轮上限", third["reason"])
        self.assertIn("不要继续重复调用", third["next_step"])
        self.assertEqual(len(self.image_backend.calls), 2)

        # A new turn gets a fresh per-turn allowance.
        event2, request2 = self.prepare("turn-limit-2")
        next_turn = await self.call(event2, request2, "image.generate", {"prompt": "new turn pic"}, "limit-next-1")
        self.result(next_turn, "red")
        self.assertEqual(len(self.image_backend.calls), 3)

    async def test_confirmed_preflight_failure_does_not_consume_turn_image_limit(self):
        self.configure_images(
            ImageOutputConfigurationOwner(self.runtime),
            output_fields(),
            {OUTPUT_GROUP + ".max_calls_per_turn": 1},
        )
        event, request = self.prepare("turn-limit-preflight")
        # Confirmed preflight failure (no edit source available) must not burn the single turn slot.
        failed_edit = json.loads(
            await self.call(event, request, "image.edit", {"prompt": "recolor"}, "preflight-fail")
        )
        self.assertEqual(failed_edit["state"], "failed")

        # Valid generate in the same turn still succeeds.
        ok = await self.call(event, request, "image.generate", {"prompt": "valid pic"}, "after-preflight-fail")
        self.result(ok, "red")
        self.assertEqual(len(self.image_backend.calls), 1)


class ImageHistoryBudgetAndEstimateTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    def _make_prompt_image(
        self, scope, source_id: str, parent, png: bytes, *, position: int = 0
    ) -> PromptImage:
        parent_ref = (
            parent
            if isinstance(parent, SourceRef)
            else SourceRef(parent, "conversation.message", scope, VersionRef(parent, 1), NOW)
        )
        source = SourceRef(source_id, "conversation.image", scope, VersionRef(source_id, 1), NOW)
        with Image.open(io.BytesIO(png)) as img:
            width, height = img.size
        descriptor = ImageDescriptor(
            position=position,
            mime_type="image/png",
            width=width,
            height=height,
            byte_count=len(png),
            content_hash=hashlib.sha256(png).hexdigest(),
        )
        return PromptImage(
            source=source,
            parent=parent_ref,
            descriptor=descriptor,
            data_base64=base64.b64encode(png).decode("ascii"),
        )

    @staticmethod
    def _image_materials(existing_materials, images):
        existing_sources = {m.source for m in existing_materials if m.source is not None}
        added = []
        seen = set(existing_sources)
        for img in images:
            if img.parent not in seen:
                added.append(
                    PromptMaterial("conversation.message", f"history:{img.parent.source_id}", "msg", source=img.parent)
                )
                seen.add(img.parent)
            if img.source not in seen:
                added.append(
                    PromptMaterial("conversation.image", f"image:{img.source.source_id}", "image", source=img.source)
                )
                seen.add(img.source)
        return (*existing_materials, *added)

    def test_select_inline_image_indices_respects_latest_turn_and_count_byte_caps(self):
        entries = (
            (0, "turn-1", 300_000, False),
            (1, "turn-2", 300_000, False),
            (2, "turn-3", 100_000, False),
        )
        policy = ImageHistoryBudgetPolicy(max_inline_images=2, max_inline_bytes=450_000, keep_latest_turn_images=True)
        # Current turn is turn-3 (100KB) -> kept; turn-2 (300KB) fits (400KB <= 450KB) -> kept; turn-1 omitted.
        self.assertEqual(
            select_inline_image_indices(entries, policy, current_parent_source_id="turn-3"),
            frozenset({1, 2}),
        )
        # Text-only current turn ("turn-4"): all images are historical; newest fitting kept (turn-3 + turn-2).
        self.assertEqual(
            select_inline_image_indices(entries, policy, current_parent_source_id="turn-4"),
            frozenset({1, 2}),
        )
        # Strict byte budget: only turn-3 kept when current turn is turn-3.
        tight = ImageHistoryBudgetPolicy(max_inline_images=4, max_inline_bytes=200_000, keep_latest_turn_images=True)
        self.assertEqual(
            select_inline_image_indices(entries, tight, current_parent_source_id="turn-3"),
            frozenset({2}),
        )
        # Disabled policy keeps all images.
        self.assertEqual(
            select_inline_image_indices(entries, ImageHistoryBudgetPolicy(enabled=False)),
            frozenset({0, 1, 2}),
        )
        # Zero inline cap omits all.
        self.assertEqual(
            select_inline_image_indices(entries, ImageHistoryBudgetPolicy(max_inline_images=0)),
            frozenset(),
        )
        # Atomic group preservation: a 3-frame historical animation does not get sliced
        # into a partial tail when only 2 inline slots remain; an older 1-frame image fits.
        grouped_entries = (
            (0, "turn-1", 50_000, False, "static-old"),
            (1, "turn-2", 50_000, False, "anim-old"),
            (2, "turn-2", 50_000, False, "anim-old"),
            (3, "turn-2", 50_000, False, "anim-old"),
            (4, "turn-3", 50_000, True, "current-0"),
            (5, "turn-3", 50_000, True, "current-1"),
        )
        anim_policy = ImageHistoryBudgetPolicy(max_inline_images=4, max_inline_bytes=1_000_000, keep_latest_turn_images=True)
        self.assertEqual(
            select_inline_image_indices(grouped_entries, anim_policy, current_parent_source_id="turn-3"),
            frozenset({0, 4, 5}),
        )

    async def test_build_wire_degrades_older_images_to_metadata_placeholders_while_keeping_evidence(self):
        envelope = await self.envelope()
        scope = envelope.original.event.scope
        current_parent = envelope.original.event.source
        old_png_1 = noisy_png_bytes(160, 160, seed=1)
        old_png_2 = noisy_png_bytes(160, 160, seed=2)
        new_png = noisy_png_bytes(90, 90, seed=3)

        img_old_1 = self._make_prompt_image(scope, "img-old-1", "msg-old-1", old_png_1, position=0)
        img_old_2 = self._make_prompt_image(scope, "img-old-2", "msg-old-2", old_png_2, position=0)
        img_new = self._make_prompt_image(scope, "img-new-1", current_parent, new_png, position=0)

        request = replace(
            envelope.original,
            context=replace(
                envelope.original.context,
                materials=self._image_materials(envelope.original.context.materials, (img_old_1, img_old_2, img_new)),
            ),
            images=(img_old_1, img_old_2, img_new),
            user_input_image_sources=(img_new.source,),
            reply_images=True,
        )
        policy = ImageHistoryBudgetPolicy(
            max_inline_images=2,
            max_inline_bytes=len(new_png) + 1024,
            keep_latest_turn_images=True,
        )
        wire = build_wire(replace(envelope, original=request, image_history_budget=policy))

        self.assertEqual(wire.image_count, 1)
        self.assertEqual(wire.omitted_image_count, 2)
        self.assertEqual(wire.image_bytes, len(new_png))

        user_parts = wire.messages[-1]["content"]
        image_url_parts = [part for part in user_parts if part.get("type") == "image_url"]
        self.assertEqual(len(image_url_parts), 1)
        self.assertIn(img_new.data_base64, image_url_parts[0]["image_url"]["url"])

        # Older images were degraded to lightweight text metadata placeholders with media_inspect guidance.
        omitted_docs = []
        for part in user_parts:
            if part.get("type") != "text":
                continue
            try:
                parsed = json.loads(part["text"])
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("omitted_from_inline_media") is True:
                omitted_docs.append(parsed)
        self.assertEqual(len(omitted_docs), 2)
        for doc in omitted_docs:
            self.assertEqual(doc["omit_reason"], "image_history_budget_exceeded")
            self.assertEqual(doc["original_mime_type"], "image/png")
            self.assertGreater(doc["original_bytes"], 1000)
            self.assertIn("media_inspect", doc["inspect_hint"])
            self.assertIn(doc["image_evidence"], wire.evidence)
            self.assertIn(doc["sendable_image"], wire.reply_images)

        # Ensure neither old image's base64 leaked onto the wire.
        wire_blob = json.dumps(wire.messages, ensure_ascii=False)
        self.assertNotIn(img_old_1.data_base64, wire_blob)
        self.assertNotIn(img_old_2.data_base64, wire_blob)

    async def test_input_estimate_and_provider_fail_fast_include_inline_image_bytes(self):
        envelope = await self.envelope()
        scope = envelope.original.event.scope
        current_parent = envelope.original.event.source
        big_png = noisy_png_bytes(420, 420, seed=99)
        self.assertGreater(len(big_png), 400_000)
        img = self._make_prompt_image(scope, "img-big-1", current_parent, big_png, position=0)
        request = replace(
            envelope.original,
            context=replace(
                envelope.original.context,
                materials=self._image_materials(envelope.original.context.materials, (img,)),
            ),
            images=(img,),
            user_input_image_sources=(img.source,),
        )
        wire = build_wire(replace(envelope, original=request))
        small_wire = replace(wire, image_bytes=100)
        self.assertGreater(wire.input_estimate(), small_wire.input_estimate() + 5000)
        self.assertEqual(
            estimate_inline_image_weight(1, len(big_png)),
            16384 + (len(big_png) + 63) // 64,
        )

        # Tight provider ceiling: fits base-only estimate, fails fast with actual image byte weight.
        self.native_model.maximum_input_estimate = small_wire.input_estimate() + 1000
        with self.assertRaises(TaskError) as caught:
            self.native_model.reserve(replace(envelope, original=request))
        self.assertEqual(caught.exception.code, "host_model_limit_input")
        self.assertEqual(caught.exception.details["inline_image_count"], 1)
        self.assertEqual(caught.exception.details["inline_image_bytes"], len(big_png))
        self.assertEqual(caught.exception.details["omitted_image_count"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_gateway_estimate_and_generate_share_image_history_budget_policy(self):
        envelope = await self.envelope()
        scope = envelope.original.event.scope
        current_parent = envelope.original.event.source
        old_png = noisy_png_bytes(380, 380, seed=7)
        new_png = noisy_png_bytes(40, 40, seed=8)
        img_old = self._make_prompt_image(scope, "gw-old", "gw-msg-old", old_png, position=0)
        img_new = self._make_prompt_image(scope, "gw-new", current_parent, new_png, position=0)
        request = replace(
            envelope.original,
            context=replace(
                envelope.original.context,
                materials=self._image_materials(envelope.original.context.materials, (img_old, img_new)),
            ),
            images=(img_old, img_new),
            user_input_image_sources=(img_new.source,),
        )
        observed_policies = []
        original_reserve = self.native_model.reserve

        def spy_reserve(env):
            observed_policies.append(env.image_history_budget)
            return original_reserve(env)

        tight_policy = ImageHistoryBudgetPolicy(
            max_inline_images=1,
            max_inline_bytes=100_000,
            keep_latest_turn_images=True,
        )
        wire_with_budget = build_wire(replace(envelope, original=request, image_history_budget=tight_policy))
        wire_without_budget = build_wire(
            replace(envelope, original=request, image_history_budget=ImageHistoryBudgetPolicy(enabled=False))
        )
        self.assertEqual(wire_with_budget.image_count, 1)
        self.assertEqual(wire_with_budget.omitted_image_count, 1)
        self.assertEqual(wire_without_budget.image_count, 2)
        self.assertGreater(wire_without_budget.input_estimate(), wire_with_budget.input_estimate() + 16000)

        # Verify LocalModelGateway propagates its configured policy onto estimate().
        gateway = LocalModelGateway(
            router=NS(
                configuration=NS(runtime=self.runtime),
                resolve=lambda _run, _req: NS(
                    providers=(
                        replace(self.native_model.local_provider(), reserve=spy_reserve),
                    ),
                    envelope=replace(envelope, original=request),
                ),
            ),
            ledger=NS(runtime=self.runtime),
            tasks=NS(runtime=self.runtime, now=self.clock.now),
            image_history_budget=tight_policy,
        )
        fits, _ceiling = gateway.estimate(NS(task=NS(task_id="t1", spec=NS(kind="conversation.reply"))), request)
        self.assertTrue(fits)
        self.assertEqual(observed_policies, [tight_policy])

    async def test_build_wire_preserves_merged_inputs_generated_images_and_screen_captures_in_current_turn(self):
        envelope = await self.envelope()
        scope = envelope.original.event.scope
        current_parent = envelope.original.event.source

        # Older historical image that should degrade when budget is saturated.
        old_png = noisy_png_bytes(120, 120, seed=11)
        img_old = self._make_prompt_image(scope, "hist-old-1", "msg-hist-old", old_png, position=0)

        # Debounced merged input messages (4 current-turn images across primary + merged_input + quote).
        merged_parent = SourceRef("msg-merged-2", "conversation.message", scope, VersionRef("msg-merged-2", 1), NOW)
        quote_parent = SourceRef("msg-quoted-1", "conversation.quotes", scope, VersionRef("msg-quoted-1", 1), NOW)
        p0 = noisy_png_bytes(40, 40, seed=21)
        p1 = noisy_png_bytes(40, 40, seed=22)
        p2 = noisy_png_bytes(40, 40, seed=23)
        p3 = noisy_png_bytes(40, 40, seed=24)
        img_c0 = self._make_prompt_image(scope, "cur-0", current_parent, p0, position=0)
        img_c1 = self._make_prompt_image(scope, "cur-1", current_parent, p1, position=1)
        img_c2 = self._make_prompt_image(scope, "cur-merged-2", merged_parent, p2, position=0)
        img_c3 = self._make_prompt_image(scope, "cur-quoted-3", quote_parent, p3, position=0)

        # Freshly generated image in this turn (_GeneratedPromptImage).
        gen_png = noisy_png_bytes(45, 45, seed=25)
        gen_parent = SourceRef("gen-op-1", GENERATED_IMAGE_OWNER, scope, VersionRef("gen-op-1", 1), NOW)
        gen_source = SourceRef("gen-img-1", "conversation.image", scope, VersionRef("gen-img-1", 1), NOW)
        gen_desc = ImageDescriptor(0, "image/png", 45, 45, len(gen_png), hashlib.sha256(gen_png).hexdigest())
        img_gen = _GeneratedPromptImage(
            source=gen_source,
            parent=gen_parent,
            descriptor=gen_desc,
            data_base64=base64.b64encode(gen_png).decode("ascii"),
            manifest_payload="{}",
            metadata_payload="{}",
            first_in_manifest=True,
        )

        # Live screen capture in this turn (ScreenPromptImage).
        scr_png = noisy_png_bytes(45, 45, seed=26)
        scr_parent = SourceRef("scr-cap-1", "screen", scope, VersionRef("scr-cap-1", 1), NOW)
        scr_source = SourceRef("scr-img-1", "screen", scope, VersionRef("scr-img-1", 1), NOW)
        scr_desc = ImageDescriptor(0, "image/png", 45, 45, len(scr_png), hashlib.sha256(scr_png).hexdigest())
        img_scr = ScreenPromptImage(
            source=scr_source,
            parent=scr_parent,
            descriptor=scr_desc,
            data_base64=base64.b64encode(scr_png).decode("ascii"),
            capture_payload="{}",
        )

        all_images = (img_old, img_c0, img_c1, img_c2, img_c3, img_gen, img_scr)
        extra_materials = (
            PromptMaterial("conversation.message", "merged_input:msg-merged-2", "second msg", source=merged_parent),
            PromptMaterial("conversation.quotes", "quoted_message:msg-quoted-1", "quoted msg", source=quote_parent),
            PromptMaterial(GENERATED_IMAGE_OWNER, "generated_image:gen-op-1", "generated", source=gen_parent),
            PromptMaterial("screen", "screen_capture:scr-cap-1", "screen", source=scr_parent),
        )
        base_materials = (*envelope.original.context.materials, *extra_materials)
        request = replace(
            envelope.original,
            context=replace(
                envelope.original.context,
                materials=self._image_materials(base_materials, all_images),
            ),
            images=all_images,
            user_input_image_sources=(img_c0.source, img_c1.source, img_c2.source, img_c3.source),
        )
        # Default policy (max_inline_images=4) is already saturated by the 4 user attachments;
        # merged_input, quoted, generated, and screen images must ALL stay inline while img_old degrades.
        wire = build_wire(replace(envelope, original=request, image_history_budget=ImageHistoryBudgetPolicy(max_inline_images=4)))
        self.assertEqual(wire.image_count, 6)
        self.assertEqual(wire.omitted_image_count, 1)
        wire_blob = json.dumps(wire.messages, ensure_ascii=False)
        for kept_img in (img_c0, img_c1, img_c2, img_c3, img_gen, img_scr):
            self.assertIn(kept_img.data_base64, wire_blob)
        self.assertNotIn(img_old.data_base64, wire_blob)

    async def test_material_only_omitted_image_never_emits_media_inspect_hint(self):
        envelope = await self.envelope()
        scope = envelope.original.event.scope
        phone_png = noisy_png_bytes(40, 40, seed=31)
        phone_desc = ImageDescriptor(0, "image/png", 40, 40, len(phone_png), hashlib.sha256(phone_png).hexdigest())
        phone_src = SourceRef("phone-src-1", "device.phone", scope, VersionRef("phone-src-1", 1), NOW)
        phone_parent = SourceRef("phone-parent-1", "device.phone", scope, VersionRef("phone-parent-1", 1), NOW)
        phone_img = PhonePromptImage(
            source=phone_src,
            parent=phone_parent,
            descriptor=phone_desc,
            data_base64=base64.b64encode(phone_png).decode("ascii"),
        )
        # PhonePromptImage bypasses standard PromptRequest image validation only under phone_action.
        fake_request = NS(
            output_format="phone_action",
            reply_actions=("text",),
            reply_images=False,
            native_video_input=False,
            context=NS(materials=(), deferred=(), unavailable=()),
            role_instruction="role",
            instruction="inst",
            user_instruction="",
            streaming_mode="buffered",
            images=(phone_img,),
            user_input_image_sources=(),
            audios=(),
            videos=(),
            event=NS(source=None),
        )
        wire = build_wire(
            replace(envelope, original=fake_request, tools=(), tool_history=(),
                    image_history_budget=ImageHistoryBudgetPolicy(max_inline_images=0)),
        )
        self.assertEqual(wire.image_count, 0)
        self.assertEqual(wire.omitted_image_count, 1)
        omitted_part = json.loads(wire.messages[-1]["content"][-1]["text"])
        self.assertEqual(omitted_part["image_material"], "device_screenshot")
        self.assertTrue(omitted_part["omitted_from_inline_media"])
        self.assertNotIn("inspect_hint", omitted_part)


if __name__ == "__main__":
    unittest.main()
