"""Image edit source fallback, ComfyUI edit workflow binding, and non-null error codes."""

import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot.core.provider.entities import ProviderRequest

from astrbot_host.core_reply_image_prompt import core_image_tool_contract, inject_core_image_prompt
from astrbot_host.core_reply_tools import native_tool_name
from astrbot_host.native_extensions.image_native import output_routing
from astrbot_host.native_extensions.image_native.adapter import NativeImageProvider, scope_key
from astrbot_host.native_extensions.image_native.comfyui_workflows import infer_mapping, validate_mapping
from astrbot_host.native_extensions.image_native.owner import NativeImageOwner
from astrbot_host.native_extensions.image_native.types import ImageInvocation, NativeImageError, ReferenceImage
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.media.output_settings import DEFAULTS
from companion_v2.conversation.tool_failures import failure_guidance, retry_disposition_for
from companion_v2.execution.models import TaskContext, encode, require
from companion_v2.foundation.refs import AudienceRef, ScopeKind
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.model_gateway.models import ToolCall
from tests import test_core_reply_images as _core_images_mod
from tests import test_native_image_flow as _native_flow_mod
from tests.native_image_support import NativeImageFixture
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered, requested


def _comfy_edit_workflow():
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece", "clip": ["10", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands", "clip": ["10", 0]}},
        "4": {"class_type": "KSampler", "inputs": {
            "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["1", 0], "seed": 42,
        }},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
        "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "demo.safetensors"}},
    }


def _comfy_object_info(*, omit=()):
    info = {
        "LoadImage": {"input": {"required": {"image": ["STRING"]}}, "output_node": False},
        "CLIPTextEncode": {"input": {"required": {"text": ["STRING"]}}, "output_node": False},
        "KSampler": {"input": {"required": {"seed": ["INT"]}}, "output_node": False},
        "SaveImage": {"input": {"required": {"images": ["IMAGE"]}}, "output_node": True},
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": ["STRING"]}}, "output_node": False},
    }
    for name in omit:
        info.pop(name, None)
    return info


class ImageEditFallbackAndCoreErrorsTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from companion_v2.storage.native_conversation_schema import STATEMENTS
        from companion_v2.capabilities.repositories import CapabilityRepository
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

    asyncSetUp = _core_images_mod.CoreReplyImagesTests.asyncSetUp
    prepare = _core_images_mod.CoreReplyImagesTests.prepare
    call = _core_images_mod.CoreReplyImagesTests.call
    result = _core_images_mod.CoreReplyImagesTests.result
    assert_no_reply_work = _core_images_mod.CoreReplyImagesTests.assert_no_reply_work
    async def test_prompt_only_edit_prefers_current_attachment_over_recent_generated_image(self):
        event1, request1 = self.prepare("turn-gen-1")
        generated = await self.call(event1, request1, "image.generate", {"prompt": "红色原图"}, "gen-1")
        self.result(generated, "red")

        self.clock.advance(timedelta(seconds=5))
        event2, request2 = self.prepare("turn-edit-attach", (image_bytes("green"),))
        edited = await self.call(event2, request2, "image.edit", {"prompt": "把表情换成不高兴"}, "edit-attach")
        self.result(edited, "blue")

        self.assertEqual(len(self.image_backend.calls), 2)
        edit_refs = self.image_backend.calls[1][1]
        self.assertEqual(len(edit_refs), 1)
        self.assertEqual(edit_refs[0].data, image_bytes("green"))
        self.assertEqual(edit_refs[0].role, "edit_source")

    async def test_prompt_only_edit_falls_back_to_most_recent_bot_generated_image_in_same_session(self):
        event1, request1 = self.prepare("turn-gen-fallback")
        generated = await self.call(event1, request1, "image.generate", {"prompt": "红色原图"}, "gen-fallback")
        self.result(generated, "red")

        self.clock.advance(timedelta(seconds=5))
        event2, request2 = self.prepare("turn-edit-fallback")
        edited = await self.call(event2, request2, "image.edit", {"prompt": "把表情换成不高兴"}, "edit-fallback-1")
        self.result(edited, "blue")

        self.assertEqual(len(self.image_backend.calls), 2)
        first_edit_refs = self.image_backend.calls[1][1]
        self.assertEqual(len(first_edit_refs), 1)
        self.assertEqual(first_edit_refs[0].data, image_bytes("red"))
        self.assertEqual(first_edit_refs[0].role, "edit_source")

        self.clock.advance(timedelta(seconds=5))
        event3, request3 = self.prepare("turn-edit-fallback-2")
        edited_again = await self.call(event3, request3, "image.edit", {"prompt": "换个夜景背景"}, "edit-fallback-2")
        self.result(edited_again, "blue")

        second_edit_refs = self.image_backend.calls[2][1]
        self.assertEqual(len(second_edit_refs), 1)
        self.assertEqual(second_edit_refs[0].data, image_bytes("blue"))
        self.assertEqual(second_edit_refs[0].role, "edit_source")

    async def test_prompt_only_edit_without_any_source_returns_missing_edit_source_not_null_or_unknown(self):
        event, request = self.prepare("turn-no-source")
        raw = await self.call(event, request, "image.edit", {"prompt": "把表情换成不高兴"}, "no-source-edit")
        self.assertIsInstance(raw, str)
        payload = json.loads(raw)
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["effect"], "not_committed")
        self.assertEqual(payload["code"], "missing_edit_source")
        self.assertEqual(payload["retry_disposition"], "input_invalid")
        self.assertIn("没有可用于改图的源图片", payload["reason"])
        self.assertTrue(payload["next_step"])
        self.assertEqual(self.image_backend.calls, [])

    async def test_explicit_empty_reference_indices_does_not_fallback_to_recent_generated_image(self):
        event1, request1 = self.prepare("turn-gen-before-empty-idx")
        self.result(await self.call(event1, request1, "image.generate", {"prompt": "红色原图"}, "gen-empty-idx"), "red")

        self.clock.advance(timedelta(seconds=5))
        event2, request2 = self.prepare("turn-edit-empty-idx")
        raw = await self.call(event2, request2, "image.edit",
                              {"prompt": "把表情换成不高兴", "reference_indices": []}, "edit-empty-idx")
        payload = json.loads(raw)
        self.assertEqual((payload["state"], payload["effect"], payload["code"]),
                         ("failed", "not_committed", "missing_edit_source"))
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_recent_image_fallback_strictly_isolated_across_users_and_groups(self):
        event1, request1 = self.prepare("turn-user1-gen")
        self.result(await self.call(event1, request1, "image.generate", {"prompt": "私密图"}, "user1-gen"), "red")

        # Another private user with the same persona must never inherit User 1's image.
        other_scope = replace(SCOPE, native_scope_id="other-private-user",
                              conversation_id="conv-other", session_id="sess-other")
        self.personas.register_route(ConversationRoute(other_scope, user=self.standard.subject), observed_at=NOW)
        for action in ("text", "conversation.reply", "extension.tool.invoke"):
            self.grant(self.standard, other_scope, action)
        package = self.native_registry.declarations()[0].descriptor.package
        from companion_v2.capabilities.repositories import CapabilityRepository
        capabilities = CapabilityRepository(self.runtime)
        for capability in package.capabilities:
            require(capabilities.set_authorization(
                extension_id=package.extension_id, capability_id=capability,
                subject=self.standard.subject, scope=other_scope,
                state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=NOW))

        saved_context = self.context
        try:
            self.context = TaskContext(
                self.standard,
                require(self.identity.resolve_role(other_scope, observed_at=NOW)),
                AudienceRef(other_scope, (self.standard.subject,)),
            )
            event2, request2 = self.prepare("turn-user2-edit")
            raw = await self.call(event2, request2, "image.edit", {"prompt": "改表情"}, "user2-edit")
            payload = json.loads(raw)
            self.assertEqual((payload["state"], payload["effect"], payload["code"]),
                             ("failed", "not_committed", "missing_edit_source"))
            self.assertEqual(len(self.image_backend.calls), 1)

            # A group scope with the same user and persona must also remain isolated from private chat.
            group_scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-iso",
                                  conversation_id="conv-group-iso", session_id="sess-group-iso")
            self.personas.register_route(ConversationRoute(group_scope, group_id="group-iso"), observed_at=NOW)
            for action in ("text", "conversation.reply", "extension.tool.invoke"):
                self.grant(self.important, group_scope, action)
            for capability in package.capabilities:
                require(capabilities.set_authorization(
                    extension_id=package.extension_id, capability_id=capability,
                    subject=self.important.subject, scope=group_scope,
                    state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=NOW))
            self.context = TaskContext(
                self.important,
                require(self.identity.resolve_role(group_scope, observed_at=NOW)),
                group_audience(group_scope, self.important.subject),
            )
            event3, request3 = self.prepare("turn-group-edit")
            raw_group = await self.call(event3, request3, "image.edit", {"prompt": "改表情"}, "group-edit")
            group_payload = json.loads(raw_group)
            self.assertEqual((group_payload["state"], group_payload["effect"], group_payload["code"]),
                             ("failed", "not_committed", "missing_edit_source"))
            self.assertEqual(len(self.image_backend.calls), 1)
        finally:
            self.context = saved_context

    async def test_revoked_recent_generated_image_is_not_selected_for_fallback(self):
        event1, request1 = self.prepare("turn-gen-revoke")
        generated = await self.call(event1, request1, "image.generate", {"prompt": "红色原图"}, "gen-revoke")
        gen_result = self.result(generated, "red")
        saved = json.loads(self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?",
            ("astrbot.core-tool." + gen_result["invocation_id"],))["value"])
        source = saved["source"]
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE sources SET state = 'revoked' WHERE source_id = ? AND lineage_id = ? AND source_revision = ?",
                (source["source_id"], source["version"]["lineage_id"], source["version"]["revision"]),
            )

        self.clock.advance(timedelta(seconds=5))
        event2, request2 = self.prepare("turn-edit-after-revoke")
        raw = await self.call(event2, request2, "image.edit", {"prompt": "把表情换成不高兴"}, "edit-after-revoke")
        payload = json.loads(raw)
        self.assertEqual((payload["state"], payload["effect"], payload["code"]),
                         ("failed", "not_committed", "missing_edit_source"))
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_pre_dispatch_invalid_index_and_missing_artifact_return_failed_not_unknown(self):
        event, request = self.prepare("turn-bad-inputs")
        bad_index = json.loads(await self.call(
            event, request, "image.edit", {"prompt": "改图", "reference_indices": [2]}, "bad-idx"))
        self.assertEqual((bad_index["state"], bad_index["effect"], bad_index["code"]),
                         ("failed", "not_committed", "native_reference_index_unavailable"))
        self.assertEqual(bad_index["retry_disposition"], "input_invalid")

        bad_artifact = json.loads(await self.call(
            event, request, "image.edit", {"prompt": "改图", "artifact_id": "imgart_missing"}, "bad-art"))
        self.assertEqual((bad_artifact["state"], bad_artifact["effect"], bad_artifact["code"]),
                         ("failed", "not_committed", "image_artifact_not_found"))
        self.assertEqual(bad_artifact["retry_disposition"], "input_invalid")
        self.assertEqual(self.image_backend.calls, [])

    async def test_backend_failed_and_unknown_operations_never_return_null_code(self):
        event, request = self.prepare("turn-backend-errors", (image_bytes("red"),))
        original_execute = self.image_backend.execute

        for index, (error, expected_state, expected_effect, expected_code) in enumerate((
            (NativeImageError("native_backend_unconfigured", unconfirmed=False),
             "failed", "not_committed", "native_backend_unconfigured"),
            (NativeImageError("native_endpoint_rejected", unconfirmed=False),
             "failed", "not_committed", "native_endpoint_rejected"),
            (NativeImageError("backend_rate_limited", unconfirmed=False),
             "failed", "not_committed", "backend_rate_limited"),
            (NativeImageError("backend_result_unconfirmed", unconfirmed=True),
             "unknown", "unknown", "backend_result_unconfirmed"),
        )):
            with self.subTest(code=expected_code):
                async def failing(invocation, references, before_dispatch, *, _err=error):
                    await before_dispatch()
                    raise _err

                self.image_backend.execute = failing
                raw = await self.call(event, request, "image.edit",
                                      {"prompt": f"测试失败 {index}"}, f"backend-err-{index}")
                payload = json.loads(raw)
                self.assertEqual(payload["state"], expected_state)
                self.assertEqual(payload["effect"], expected_effect)
                self.assertEqual(payload["code"], expected_code)
                self.assertIsNotNone(payload["code"])
                self.assertTrue(payload.get("reason"))
                self.assertTrue(payload.get("next_step"))
        self.image_backend.execute = original_execute

    async def test_legacy_stored_null_code_row_recovers_non_null_code_on_snapshot(self):
        event, request = self.prepare("turn-legacy-null")
        raw = await self.call(event, request, "image.edit", {"prompt": "没有底图"}, "legacy-null")
        first = json.loads(raw)
        key = "astrbot.core-tool." + first["invocation_id"]
        saved = json.loads(self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))["value"])
        saved["result"]["code"] = None
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                               (json.dumps(saved, ensure_ascii=False), key))
        replayed = json.loads(await self.call(event, request, "image.edit", {"prompt": "没有底图"}, "legacy-null"))
        self.assertEqual(replayed["state"], "failed")
        self.assertEqual(replayed["code"], "external_not_started")

    def test_core_reply_tool_descriptions_and_prompts_prefer_image_edit_over_generate(self):
        event, request = self.prepare("turn-tool-desc", (image_bytes("red"),))
        edit_tool = request.func_tool.get_tool(native_tool_name("image.edit"))
        gen_tool = request.func_tool.get_tool(native_tool_name("image.generate"))
        self.assertIn("只需传 prompt", edit_tool.description)
        self.assertIn("最近生成的图", edit_tool.description)
        self.assertIn("不要使用此工具", gen_tool.description)
        self.assertIn("自动回退当前会话最近生成的图",
                      edit_tool.parameters["properties"]["reference_indices"]["description"])

        session = NS(application=self.app, context=self.context,
                     prepared={"context": self.context, "native_input": NS(text="改这张，把表情换成不高兴", at=NOW)},
                     state={}, image_locators=(NS(images=()),))
        offered = tuple(NS(spec=NS(action=a)) for a in ("image.generate", "image.edit"))
        provider_req = ProviderRequest(prompt="改这张，把表情换成不高兴", system_prompt="人格")
        inject_core_image_prompt(session, provider_req, offered, DEFAULTS)
        note = session.state["_turn_notes"]["image"]
        self.assertIn("必须调用改图工具（image.edit）", note)
        self.assertIn("不要误走从零生成（image.generate）", note)
        self.assertIn("可只传 prompt", note)

        desc, params = core_image_tool_contract("image.edit", "", {
            "properties": {"reference_indices": {"type": "array"}, "artifact_id": {"type": "string"}},
        })
        self.assertIn("只需传 prompt", desc)
        self.assertIn("最近生成的图", params["properties"]["reference_indices"]["description"])


class _FakeComfyContent:
    def __init__(self, body: bytes):
        self._body = body

    async def iter_chunked(self, size: int):
        if self._body:
            yield self._body


class _FakeComfyResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self.content = _FakeComfyContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeComfyHTTP:
    def __init__(self):
        self.calls = []
        self.replies = {}

    def reply(self, url: str, body: bytes, status: int = 200):
        self.replies[url] = (body, status)

    def session(self, **kwargs):
        http = self

        class _Session:
            closed = False

            def request(self, method, url, **req_kwargs):
                http.calls.append({"method": method.upper(), "url": url, **req_kwargs})
                if url not in http.replies:
                    raise AssertionError(f"unexpected {method} {url}: {sorted(http.replies)}")
                body, status = http.replies[url]
                return _FakeComfyResponse(body, status=status)

            async def close(self):
                self.closed = True

        return _Session()


class ComfyUIEditWorkflowAndErrorCodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.http = _FakeComfyHTTP()
        patcher = patch("aiohttp.ClientSession", side_effect=lambda **kwargs: self.http.session(**kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _comfy_settings(self, workflow=None, mapping=None):
        wf = workflow or _comfy_edit_workflow()
        mp = mapping if mapping is not None else validate_mapping(wf, infer_mapping(wf))
        return {
            "enabled": True,
            "backend": "comfyui",
            "operation_routes": {"generate": [], "edit": ["comfyui"]},
            "endpoints": [],
            "comfyui": {
                "base_url": "http://comfy.local.test:8188",
                "api_key": "",
                "timeout_seconds": 30,
                "workflows": {"edit": {"workflow": wf, "mapping": mp}},
            },
        }

    def _edit_invocation(self, ref):
        return ImageInvocation(
            scope_key="scope-comfy", request_id="req-comfy-1", invocation_id="inv-comfy-1",
            origin_key="origin-comfy", operation="edit", prompt="change expression to unhappy",
            negative_prompt="blurry", reference_ids=(ref.reference_id,),
            envelope={"action": "image.edit"},
        )

    def _ref(self, role="edit_source"):
        raw = image_bytes("red")
        import hashlib
        return ReferenceImage("ref-1", raw, "source-1", hashlib.sha256(raw).hexdigest(), role=role)

    async def test_comfyui_inferred_edit_workflow_binds_generic_slot_and_append_prompt(self):
        settings = self._comfy_settings()
        # Verify infer_mapping produced the exact real-world defaults that previously broke _plan():
        edit_fields = settings["comfyui"]["workflows"]["edit"]["mapping"]["fields"]
        self.assertEqual(edit_fields["reference_image_1"]["role"], "generic")
        self.assertEqual(edit_fields["positive_prompt"]["mode"], "append")
        self.assertEqual(edit_fields["negative_prompt"]["mode"], "append")

        base = "http://comfy.local.test:8188"
        self.http.reply(base + "/object_info", json.dumps(_comfy_object_info()).encode())
        self.http.reply(base + "/upload/image", json.dumps({"name": "ref_001.png", "subfolder": ""}).encode())
        self.http.reply(base + "/prompt", json.dumps({"prompt_id": "comfy-job-1"}).encode())

        backend = output_routing.OutputRoutingBackend(settings, self.root / "routing")
        owner = NativeImageOwner(self.root / "owner", backend)
        ref = self._ref("edit_source")
        operation = await owner.execute(self._edit_invocation(ref), references=(ref,), before_dispatch=lambda: None)

        self.assertEqual(operation["state"], "pending")
        self.assertEqual(operation["locator"]["task_id"], "comfy-job-1")
        urls = [call["url"] for call in self.http.calls]
        self.assertEqual(urls, [base + "/object_info", base + "/upload/image", base + "/prompt"])
        prompt_payload = self.http.calls[-1]["json"]["prompt"]
        self.assertEqual(prompt_payload["1"]["inputs"]["image"], "ref_001.png")
        self.assertIn("masterpiece", prompt_payload["2"]["inputs"]["text"])
        self.assertIn("change expression to unhappy", prompt_payload["2"]["inputs"]["text"])
        self.assertIn("bad hands", prompt_payload["3"]["inputs"]["text"])
        self.assertIn("blurry", prompt_payload["3"]["inputs"]["text"])

    async def test_comfyui_explicit_non_generic_slot_role_mismatch_keeps_exact_code(self):
        wf = _comfy_edit_workflow()
        mp = validate_mapping(wf, infer_mapping(wf))
        mp["fields"]["reference_image_1"]["role"] = "identity"
        settings = self._comfy_settings(wf, mp)

        backend = output_routing.OutputRoutingBackend(settings, self.root / "routing-role")
        owner = NativeImageOwner(self.root / "owner-role", backend)
        provider = NativeImageProvider(owner)
        ref = self._ref("edit_source")
        context = {"scope": "scope-comfy-role"}
        from astrbot_host.native_extensions.image_native.adapter import scope_key
        inv = ImageInvocation(
            scope_key=scope_key(context), request_id="req-comfy-role", invocation_id="inv-comfy-role",
            origin_key="origin-comfy", operation="edit", prompt="change expression",
            reference_ids=(ref.reference_id,), envelope={"action": "image.edit"},
        )
        operation = await owner.execute(inv, references=(ref,), before_dispatch=lambda: None)

        self.assertEqual(operation["state"], "failed")
        self.assertEqual(operation["error"], "native_workflow_reference_role_mismatch")
        self.assertEqual(self.http.calls, [])
        # Exact 11-key wire contract stays untouched while operation_error_code recovers the exact error.
        req = {
            "schema": "companion.native-provider.v1",
            "invocation_id": "inv-comfy-role",
            "origin_key": "origin-comfy",
            "arguments_hash": "hash",
            "context": context,
            "binding": {"capability": "image.edit", "provider": "image.native",
                        "version": provider.version, "generation": owner.generation},
        }
        wire = provider._response(req, operation)
        self.assertEqual(set(wire), {
            "schema", "invocation_id", "origin_key", "arguments_hash", "binding",
            "state", "effect", "operation_id", "receipt_ref", "result", "artifacts",
        })
        self.assertEqual((wire["state"], wire["effect"]), ("failed", "not_committed"))
        self.assertIsNone(wire["result"])
        self.assertEqual(provider.operation_error_code(req, operation["id"]),
                         "native_workflow_reference_role_mismatch")

    async def test_comfyui_prepare_and_upload_failures_stay_failed_with_specific_codes(self):
        base = "http://comfy.local.test:8188"
        ref = self._ref("edit_source")

        # 1. Missing node in /object_info -> failed + workflow_nodes_missing
        self.http.calls.clear()
        self.http.reply(base + "/object_info", json.dumps(_comfy_object_info(omit=("KSampler",))).encode())
        backend = output_routing.OutputRoutingBackend(self._comfy_settings(), self.root / "routing-nodes")
        owner = NativeImageOwner(self.root / "owner-nodes", backend)
        op_nodes = await owner.execute(self._edit_invocation(ref), references=(ref,), before_dispatch=lambda: None)
        self.assertEqual(op_nodes["state"], "failed")
        self.assertEqual(op_nodes["error"], "workflow_nodes_missing")

        # 2. HTTP 400 on /upload/image -> failed + comfyui_rejected (must NOT be unknown!)
        self.http.calls.clear()
        self.http.reply(base + "/object_info", json.dumps(_comfy_object_info()).encode())
        self.http.reply(base + "/upload/image", b'{"error":"bad upload"}', status=400)
        backend2 = output_routing.OutputRoutingBackend(self._comfy_settings(), self.root / "routing-upload")
        owner2 = NativeImageOwner(self.root / "owner-upload", backend2)
        op_upload = await owner2.execute(self._edit_invocation(ref), references=(ref,), before_dispatch=lambda: None)
        self.assertEqual(op_upload["state"], "failed")
        self.assertEqual(op_upload["error"], "comfyui_rejected")

        # 3. Invalid mapping against /object_info (SaveImage not output_node) -> failed + native_workflow_mapping_invalid
        self.http.calls.clear()
        bad_info = _comfy_object_info()
        bad_info["SaveImage"]["output_node"] = False
        self.http.reply(base + "/object_info", json.dumps(bad_info).encode())
        backend3 = output_routing.OutputRoutingBackend(self._comfy_settings(), self.root / "routing-map")
        owner3 = NativeImageOwner(self.root / "owner-map", backend3)
        op_map = await owner3.execute(self._edit_invocation(ref), references=(ref,), before_dispatch=lambda: None)
        self.assertEqual(op_map["state"], "failed")
        self.assertEqual(op_map["error"], "native_workflow_mapping_invalid")

    def test_image_failure_guidance_covers_missing_source_workflow_and_unknown(self):
        missing = failure_guidance("missing_edit_source", "failed", action="image.edit")
        self.assertEqual(retry_disposition_for("missing_edit_source", "failed"), "input_invalid")
        self.assertIn("没有可用于改图的源图片", missing["reason"])
        self.assertIn("不要编造额度或尺寸错误", missing["next_step"])

        workflow = failure_guidance("workflow_nodes_missing", "failed", action="image.edit")
        self.assertIn("ComfyUI 工作流", workflow["reason"])

        unknown = failure_guidance("backend_result_unconfirmed", "unknown", action="image.edit")
        self.assertEqual(retry_disposition_for("backend_result_unconfirmed", "unknown"), "external_effect_unknown")
        self.assertIn("尚未确认", unknown["reason"])


class StagingBatchImageEditFallbackTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from companion_v2.capabilities.repositories import CapabilityRepository
        self.image_directory = self.root.parent / "image-native-v2"
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.providers = tuple(replace(p, capabilities=("text", "tools", "image")) for p in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=self.package.extension_id, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.capabilities.set_role_enabled(extension_id=self.package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for capability in self.package.capabilities:
            self.consent(capability, CapabilityAuthorizationState.ACTIVE, 0)
        self.model_behavior = self.image_model

    consent = _native_flow_mod.NativeImageFlowTests.consent
    image_model = _native_flow_mod.NativeImageFlowTests.image_model
    image_answer = _native_flow_mod.NativeImageFlowTests.image_answer
    image_host = _native_flow_mod.NativeImageFlowTests.image_host
    complete_image = _native_flow_mod.NativeImageFlowTests.complete_image
    async def test_prompt_only_edit_in_staging_batch_flow_falls_back_to_recent_generated_image(self):
        host, _batch1, _context1 = await self.complete_image()
        self.assertEqual(len(self.image_backend.calls), 1)

        async def edit_model(envelope):
            if not envelope.tool_history:
                return requested(ToolCall("edit-recent", "image.edit", encode({"prompt": "把表情换成不高兴"})))
            if not envelope.original.images:
                return answered("没有拿到改图结果。")
            return self.image_answer(envelope, "改好了。")

        self.model_behavior = edit_model
        self.clock.advance(timedelta(seconds=5))
        batch2 = host.receive(self.event("event-2", text="把表情换成不高兴")).unwrap()
        state = await self.done(host.application, batch2)
        self.assertEqual(state["state"], "complete", dict(state))
        self.assertEqual(len(self.image_backend.calls), 2)
        edit_refs = self.image_backend.calls[1][1]
        self.assertEqual(len(edit_refs), 1)
        self.assertEqual(edit_refs[0].data, image_bytes("red"))
        self.assertEqual(edit_refs[0].role, "edit_source")
        self.assertEqual(base64.b64decode(self.sent[-1].images[0].data_base64), image_bytes("blue"))


if __name__ == "__main__":
    unittest.main()
