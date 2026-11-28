"""Tests for backend-agnostic aspect_ratio support on image.generate and image.edit."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.core_reply_image_prompt import core_image_tool_contract
from astrbot_host.native_extensions.image_native.adapter import NativeImageProvider, PROVIDER_ID, SCHEMA
from astrbot_host.native_extensions.image_native.backends import NativeTransportBackend
from astrbot_host.native_extensions.image_native.nai_backend import NAIImageBackend
from astrbot_host.native_extensions.image_native.output_routing import (
    OnlineImageBackend,
    openai_aspect_size,
)
from astrbot_host.native_extensions.image_native.owner import NativeImageOwner
from astrbot_host.native_extensions.image_native.types import (
    ASPECT_RATIOS,
    BackendResult,
    ImageInvocation,
    NativeImageError,
    ReferenceImage,
    aspect_ratio_dimensions,
)
from companion_v2.conversation.tool_failures import retry_disposition_for
from companion_v2.execution.models import digest as v2_digest
from tests.test_conversation_images import image_bytes


class _FakeHTTPResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self.headers = {}
        raw = json.dumps(payload).encode("utf-8")

        async def _chunks(_size: int):
            yield raw

        self.content = NS(iter_chunked=_chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return b""


class _FakeHTTPSession:
    def __init__(self, captured: list[dict], *, png_bytes: bytes):
        self._captured = captured
        self._png_b64 = base64.b64encode(png_bytes).decode("ascii")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, **kwargs):
        record = {"url": url, "json": kwargs.get("json"), "form_fields": None}
        data = kwargs.get("data")
        if data is not None and hasattr(data, "_fields"):
            fields = {}
            for type_options, _headers, value in data._fields:
                name = type_options.get("name")
                if isinstance(value, str):
                    fields[name] = value
            record["form_fields"] = fields
        self._captured.append(record)
        if url.endswith("/files"):
            return _FakeHTTPResponse({"id": "file_test123"})
        if "multimodal-generation" in url:
            return _FakeHTTPResponse({
                "output": {"choices": [{"message": {"content": [{"image": "https://img.example.test/out.png"}]}}]}
            })
        return _FakeHTTPResponse({"data": [{"b64_json": self._png_b64}]})

    def get(self, url, **kwargs):
        raw = base64.b64decode(self._png_b64)

        async def _chunks(_size: int):
            yield raw

        resp = _FakeHTTPResponse({})
        resp.content = NS(iter_chunked=_chunks)
        return resp

    def delete(self, url, **kwargs):
        return _FakeHTTPResponse({})


class AspectRatioGeometryTests(unittest.TestCase):
    def test_standard_1mp_dimensions_are_64_multiples_bounded_and_symmetric(self):
        expected = {
            "1:1": (1024, 1024),
            "2:3": (832, 1216),
            "3:2": (1216, 832),
            "3:4": (896, 1152),
            "4:3": (1152, 896),
            "9:16": (768, 1344),
            "16:9": (1344, 768),
        }
        for ratio, target in expected.items():
            with self.subTest(ratio=ratio):
                w, h = aspect_ratio_dimensions(ratio, base_width=1024, base_height=1024)
                self.assertEqual((w, h), target)
                self.assertEqual(w % 64, 0)
                self.assertEqual(h % 64, 0)
                self.assertLessEqual(w * h, 1024 * 1024)

    def test_sd_base_canvas_preserves_area_budget(self):
        w, h = aspect_ratio_dimensions("16:9", base_width=768, base_height=768)
        self.assertEqual((w, h), (1024, 576))
        self.assertLessEqual(w * h, 768 * 768)

    def test_invalid_ratio_rejected(self):
        with self.assertRaises(NativeImageError) as exc:
            aspect_ratio_dimensions("21:9")
        self.assertEqual(exc.exception.code, "invalid_aspect_ratio")
        self.assertFalse(exc.exception.unconfirmed)

    def test_invocation_accepts_aspect_ratio_on_generate_and_edit_and_rejects_mask_edit(self):
        inv_gen = ImageInvocation("s", "r", "i", "o", "generate", "sunset", parameters={"aspect_ratio": "16:9"})
        self.assertEqual(inv_gen.parameters["aspect_ratio"], "16:9")

        inv_edit = ImageInvocation("s", "r", "i", "o", "edit", "wider", parameters={"aspect_ratio": "3:2"})
        self.assertEqual(inv_edit.parameters["aspect_ratio"], "3:2")

        with self.assertRaises(NativeImageError) as bad_val:
            ImageInvocation("s", "r", "i", "o", "generate", "sunset", parameters={"aspect_ratio": "wide"})
        self.assertEqual(bad_val.exception.code, "invalid_aspect_ratio")

        with self.assertRaises(NativeImageError) as bad_op:
            ImageInvocation("s", "r", "i", "o", "mask_edit", "fix", parameters={"aspect_ratio": "3:2"})
        self.assertEqual(bad_op.exception.code, "unsupported_image_parameter")

    def test_tool_failure_disposition_for_invalid_aspect_ratio(self):
        self.assertEqual(retry_disposition_for("invalid_aspect_ratio", "failed"), "input_invalid")


class NativeImageProviderAspectRatioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.invocations: list[ImageInvocation] = []
        self.png = image_bytes("green")

        class _CaptureBackend:
            operations = ("generate", "edit", "mask_edit")
            config = {}

            def __init__(self, outer):
                self.outer = outer

            async def execute(self, invocation, references, before_dispatch):
                await before_dispatch()
                self.outer.invocations.append(invocation)
                return BackendResult((self.outer.png,), receipt={"backend": "stub"})

            async def close(self):
                pass

        self.owner = NativeImageOwner(self.root / "store.sqlite3", _CaptureBackend(self))
        self.provider = NativeImageProvider(self.owner)
        self.addAsyncCleanup(self.provider.close)

    def _build_request(self, action: str, arguments: dict, *, invocation_id: str = "inv-1") -> dict:
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        return {
            "schema": SCHEMA,
            "invocation_id": invocation_id,
            "parent_task_id": "turn-1",
            "tool_call_id": "call-1",
            "action": action,
            "origin_key": "origin-1",
            "context": {"scope": {"kind": "private", "id": "u1"}},
            "binding": {
                "provider": PROVIDER_ID,
                "version": self.provider.version,
                "generation": self.owner.generation,
                "capability": action,
            },
            "arguments": arguments,
            "arguments_hash": v2_digest(arguments),
            "deadline_at": deadline,
        }

    async def test_manifest_exposes_aspect_ratio_only_on_generate_and_edit(self):
        tools = {tool["action"]: tool for tool in self.provider.manifest()["tools"]}
        for action in ("image.generate", "image.edit"):
            props = tools[action]["parameters"]["properties"]
            self.assertIn("aspect_ratio", props)
            self.assertEqual(props["aspect_ratio"]["enum"], list(ASPECT_RATIOS))
            self.assertNotIn("aspect_ratio", tools[action]["parameters"]["required"])
            _, rewritten = core_image_tool_contract(action, tools[action]["description"],
                                                    json.loads(json.dumps(tools[action]["parameters"])))
            self.assertIn("3:2", rewritten["properties"]["aspect_ratio"]["description"])
            self.assertIn("16:9", rewritten["properties"]["aspect_ratio"]["description"])
        self.assertNotIn("aspect_ratio", tools["image.mask_edit"]["parameters"]["properties"])

    async def test_execute_forwards_aspect_ratio_and_keeps_omission_backward_compatible(self):
        async def allow():
            return None

        req_default = self._build_request("image.generate", {"prompt": "cafe morning"}, invocation_id="inv-default")
        res_default = await self.provider.execute(req_default, before_dispatch=allow)
        self.assertEqual(res_default["state"], "succeeded")
        self.assertEqual(dict(self.invocations[-1].parameters), {})

        req_wide = self._build_request(
            "image.generate",
            {"prompt": "cafe morning", "aspect_ratio": "16:9"},
            invocation_id="inv-wide",
        )
        res_wide = await self.provider.execute(req_wide, before_dispatch=allow)
        self.assertEqual(res_wide["state"], "succeeded")
        self.assertEqual(dict(self.invocations[-1].parameters), {"aspect_ratio": "16:9"})

        # Edit with saved reference also forwards aspect_ratio cleanly.
        ref_bytes = image_bytes("red")
        ref_sha = hashlib.sha256(ref_bytes).hexdigest()
        req_edit = self._build_request(
            "image.edit",
            {"prompt": "landscape selfie", "aspect_ratio": "3:2", "reference_ids": ["ref-id"], "reference_roles": ["identity"]},
            invocation_id="inv-edit",
        )

        async def fetch_ref(_handle, _sha, *, role):
            return ref_bytes

        res_edit = await self.provider.execute(
            req_edit,
            before_dispatch=allow,
            host_references=({
                "reference_handle": "h1",
                "reference_id": "ref-id",
                "source_key": "src-1",
                "content_hash": ref_sha,
                "position": 0,
                "roles": ("identity",),
            },),
            fetch_reference=fetch_ref,
        )
        self.assertEqual(res_edit["state"], "succeeded")
        self.assertEqual(dict(self.invocations[-1].parameters), {"aspect_ratio": "3:2"})

    async def test_invalid_aspect_ratio_and_mask_edit_aspect_ratio_rejected_before_dispatch(self):
        dispatched = False

        async def allow():
            nonlocal dispatched
            dispatched = True

        bad_req = self._build_request("image.generate", {"prompt": "test", "aspect_ratio": "5:4"}, invocation_id="inv-bad")
        with self.assertRaises(NativeImageError) as exc:
            await self.provider.execute(bad_req, before_dispatch=allow)
        self.assertEqual(exc.exception.code, "invalid_aspect_ratio")
        self.assertFalse(exc.exception.unconfirmed)
        self.assertFalse(dispatched)

        mask_req = self._build_request(
            "image.mask_edit",
            {"prompt": "test", "source_index": 0, "mask_index": 1, "aspect_ratio": "3:2"},
            invocation_id="inv-mask",
        )
        with self.assertRaises(NativeImageError) as exc2:
            await self.provider.execute(mask_req, before_dispatch=allow)
        self.assertEqual(exc2.exception.code, "invalid_image_arguments")
        self.assertFalse(exc2.exception.unconfirmed)
        self.assertFalse(dispatched)


class BackendAspectRatioRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.png = image_bytes("blue")

    async def _allow(self):
        return None

    async def test_nai_backend_forwards_ratio_and_preserves_explicit_width_height_precedence(self):
        requests: list[dict] = []

        async def fake_generate(owner, request):
            requests.append(request)
            out = self.root / f"out-{len(requests)}.png"
            out.write_bytes(self.png)
            return {"handled": True, "image_path": str(out)}

        api = NS(
            status=lambda: {"call_mode": "openai", "reference_supported": True, "operations": ["generate", "edit"]},
            capability_status=lambda owner: {"available": True, "reason": "ready", "operations": ["generate", "edit"]},
            generate_for_companion=AsyncMock(side_effect=fake_generate),
        )
        context = NS(get_registered_star=lambda name: NS(activated=True, star_cls=NS(extension_api=api)))
        backend = NAIImageBackend(context, self.root / "nai")

        await backend.execute(
            ImageInvocation("s", "r1", "i1", "o", "generate", "landscape view", parameters={"aspect_ratio": "16:9"}),
            (),
            self._allow,
        )
        self.assertEqual(requests[-1].get("ratio"), "16:9")
        self.assertNotIn("size", requests[-1])

        ref_bytes = image_bytes("red")
        ref = ReferenceImage("ref-1", ref_bytes, "src", hashlib.sha256(ref_bytes).hexdigest(), role="identity")
        await backend.execute(
            ImageInvocation("s", "r2", "i2", "o", "edit", "wide selfie", parameters={"aspect_ratio": "3:2"}, reference_ids=("ref-1",)),
            (ref,),
            self._allow,
        )
        self.assertEqual(requests[-1].get("ratio"), "3:2")
        self.assertEqual(requests[-1]["workflow_kind"], "edit")

        await backend.execute(
            ImageInvocation("s", "r3", "i3", "o", "generate", "exact", parameters={"width": 832, "height": 1216, "aspect_ratio": "3:2"}),
            (),
            self._allow,
        )
        self.assertEqual(requests[-1].get("size"), "832x1216")
        self.assertNotIn("ratio", requests[-1])

    async def test_online_backends_adapt_aspect_ratio_per_platform(self):
        cases = [
            (
                "gpt-image-1 generate 16:9",
                {"id": "ep1", "enabled": True, "platform": "openai", "model": "gpt-image-1",
                 "base_url": "https://api.openai.com/v1", "width": 1024, "height": 1024},
                "generate",
                {"aspect_ratio": "16:9"},
                lambda rec: self.assertEqual(rec["json"]["size"], "1536x1024"),
            ),
            (
                "gpt-image-1 edit 9:16",
                {"id": "ep1", "enabled": True, "platform": "openai", "model": "gpt-image-1",
                 "base_url": "https://api.openai.com/v1", "width": 1024, "height": 1024, "max_reference_images": 4},
                "edit",
                {"aspect_ratio": "9:16"},
                lambda rec: self.assertEqual(rec["form_fields"]["size"], "1024x1536"),
            ),
            (
                "dall-e-3 generate 3:2",
                {"id": "ep2", "enabled": True, "platform": "openai", "model": "dall-e-3",
                 "base_url": "https://api.openai.com/v1", "width": 1024, "height": 1024},
                "generate",
                {"aspect_ratio": "3:2"},
                lambda rec: self.assertEqual(rec["json"]["size"], "1792x1024"),
            ),
            (
                "generic openai gateway generate 3:2",
                {"id": "ep3", "enabled": True, "platform": "openai", "model": "flux-pro",
                 "base_url": "https://gateway.example.test/v1", "width": 1024, "height": 1024},
                "generate",
                {"aspect_ratio": "3:2"},
                lambda rec: self.assertEqual(rec["json"]["size"], "1216x832"),
            ),
            (
                "xai generate 16:9",
                {"id": "ep4", "enabled": True, "platform": "xai", "model": "grok-imagine-image",
                 "base_url": "https://api.x.ai/v1", "width": 1024, "height": 1024, "ratio": "2:3"},
                "generate",
                {"aspect_ratio": "16:9"},
                lambda rec: self.assertEqual(rec["json"]["aspect_ratio"], "16:9"),
            ),
            (
                "xai edit omitted preserves source framing",
                {"id": "ep4", "enabled": True, "platform": "xai", "model": "grok-imagine-image",
                 "base_url": "https://api.x.ai/v1", "width": 1024, "height": 1024, "ratio": "2:3"},
                "edit",
                {},
                lambda rec: self.assertNotIn("aspect_ratio", rec["json"]),
            ),
            (
                "xai edit explicit 3:2",
                {"id": "ep4", "enabled": True, "platform": "xai", "model": "grok-imagine-image",
                 "base_url": "https://api.x.ai/v1", "width": 1024, "height": 1024, "ratio": "2:3"},
                "edit",
                {"aspect_ratio": "3:2"},
                lambda rec: self.assertEqual(rec["json"]["aspect_ratio"], "3:2"),
            ),
            (
                "minimax generate 16:9",
                {"id": "ep5", "enabled": True, "platform": "minimax", "model": "image-01",
                 "base_url": "https://api.minimax.chat/v1", "width": 1024, "height": 1024},
                "generate",
                {"aspect_ratio": "16:9"},
                lambda rec: self.assertEqual(rec["json"]["aspect_ratio"], "16:9"),
            ),
            (
                "bailian generate 16:9",
                {"id": "ep6", "enabled": True, "platform": "bailian", "model": "wanx-v1",
                 "base_url": "https://dashscope.aliyuncs.com", "width": 1024, "height": 1024},
                "generate",
                {"aspect_ratio": "16:9"},
                lambda rec: self.assertEqual(rec["json"]["parameters"]["size"], "1344*768"),
            ),
            (
                "agnes 2K size_label preserved",
                {"id": "ep7", "enabled": True, "platform": "agnes", "model": "agnes-image-1",
                 "base_url": "https://api.agnes-ai.com/v1", "width": 1024, "height": 1024, "size_label": "2K"},
                "generate",
                {"aspect_ratio": "16:9"},
                lambda rec: self.assertEqual(rec["json"]["size"], "2K"),
            ),
        ]
        ref_bytes = image_bytes("red")
        ref = ReferenceImage("ref-1", ref_bytes, "src", hashlib.sha256(ref_bytes).hexdigest(), role="edit_source")

        for label, cfg, operation, params, check in cases:
            with self.subTest(label=label):
                captured: list[dict] = []
                backend = OnlineImageBackend(cfg)
                refs = (ref,) if operation == "edit" else ()
                inv = ImageInvocation(
                    "s", "r", "i", "o", operation, "prompt text",
                    parameters=params,
                    reference_ids=("ref-1",) if operation == "edit" else (),
                )
                with patch("astrbot_host.native_extensions.image_native.output_routing.aiohttp.ClientSession",
                           return_value=_FakeHTTPSession(captured, png_bytes=self.png)):
                    result = await backend.execute(inv, refs, self._allow)
                self.assertEqual(len(result.images), 1)
                # For xAI edits, the first POST is /files upload and the second is /images/edits.
                target_rec = next(rec for rec in reversed(captured) if not rec["url"].endswith("/files"))
                check(target_rec)

    def test_openai_aspect_size_helper_covers_square_portrait_landscape(self):
        self.assertEqual(openai_aspect_size({"model": "gpt-image-1"}, "1:1"), "1024x1024")
        self.assertEqual(openai_aspect_size({"model": "gpt-image-1"}, "2:3"), "1024x1536")
        self.assertEqual(openai_aspect_size({"model": "gpt-image-1"}, "3:2"), "1536x1024")
        self.assertEqual(openai_aspect_size({"model": "dall-e-3"}, "9:16"), "1024x1792")

    def test_local_sd_webui_and_comfyui_translate_aspect_ratio_to_width_height(self):
        sd = NativeTransportBackend(
            "sd_webui",
            {"base_url": "http://127.0.0.1:7860", "parameters": {"width": 768, "height": 768}},
            self.root / "sd",
        )
        sd_plan = sd._plan(ImageInvocation("s", "r", "i", "o", "generate", "wide scene",
                                           parameters={"aspect_ratio": "16:9"}))
        self.assertEqual(sd_plan["parameters"]["width"], 1024)
        self.assertEqual(sd_plan["parameters"]["height"], 576)
        self.assertNotIn("aspect_ratio", sd_plan["parameters"])

        sd_explicit = sd._plan(ImageInvocation("s", "r", "i", "o", "generate", "explicit",
                                               parameters={"width": 832, "height": 1216, "aspect_ratio": "16:9"}))
        self.assertEqual((sd_explicit["parameters"]["width"], sd_explicit["parameters"]["height"]), (832, 1216))
        self.assertNotIn("aspect_ratio", sd_explicit["parameters"])

        comfy = NativeTransportBackend(
            "comfyui",
            {
                "base_url": "http://127.0.0.1:8188",
                "parameters": {"width": 1024, "height": 1024},
                "workflows": {
                    "generate": {
                        "workflow": {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
                                     "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}},
                        "mapping": {
                            "output_node": "2",
                            "fields": {"positive_prompt": {"kind": "prompt", "node_id": "1", "input_name": "text"}},
                        },
                    }
                },
            },
            self.root / "comfy",
        )
        comfy_plan = comfy._plan(ImageInvocation("s", "r", "i", "o", "generate", "wide",
                                                 parameters={"aspect_ratio": "3:2"}))
        self.assertEqual(comfy_plan["parameters"]["width"], 1216)
        self.assertEqual(comfy_plan["parameters"]["height"], 832)
        self.assertNotIn("aspect_ratio", comfy_plan["parameters"])


if __name__ == "__main__":
    unittest.main()
