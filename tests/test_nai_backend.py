"""Installed NAI discovery, configuration, dispatch and artifact ownership."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_host.native_extensions.image_native.extension import ImageExtensionAPI
from astrbot_host.native_extensions.image_native.nai_backend import NAIImageBackend
from astrbot_host.native_extensions.image_native.output_routing import OutputRoutingBackend
from astrbot_host.native_extensions.image_native.types import ImageInvocation, NativeImageError, ReferenceImage
from tests.test_conversation_images import image_bytes


class NAIBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mode = "openai"
        self.available = True
        self.api = NS(
            status=lambda: {"call_mode": self.mode, "reference_supported": self.mode == "openai",
                            "api_profile": "starbridge", "default_model": "nai-diffusion-5-full",
                            "default_style": "custom", "default_size": "竖图",
                            "prompt_format": "nai tag模式", "reference_mode": "director",
                            "artist_slot_name": "主画风",
                            "api_key": "must-not-be-projected"},
            capability_status=lambda owner: {"available": self.available, "reason": "ready"},
            generate_for_companion=AsyncMock(side_effect=self.generate),
        )
        self.metadata = NS(activated=True, star_cls=NS(extension_api=self.api))
        self.context = NS(get_registered_star=lambda name: self.metadata)
        self.backend = NAIImageBackend(self.context, self.root / "transport")
        self.requests = []
        self.fences = 0

    async def generate(self, owner, request):
        self.requests.append(request)
        self.assertEqual(self.fences, 1)
        for reference in request.get("reference_image_paths", []):
            self.assertTrue(Path(reference).is_file())
        path = self.root / "generated.png"
        path.write_bytes(image_bytes("blue"))
        return {"handled": True, "image_path": str(path)}

    async def dispatch(self):
        self.fences += 1

    def invocation(self, operation="generate", **changes):
        return ImageInvocation("scope", "request", "invocation", "origin", operation, "original",
                               **changes)

    def test_status_exposes_only_selected_safe_fields_and_supported_operations(self):
        status = self.backend.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["operations"], ["generate", "edit"])
        self.assertEqual(status["api_profile"], "starbridge")
        self.assertEqual(status["prompt_format"], "nai")
        self.assertEqual(status["artist_slot_name"], "主画风")
        self.assertEqual(status["reference_mode"], "director")
        self.assertNotIn("api_key", status)
        self.mode = "direct"
        self.assertEqual(self.backend.operations, ("generate",))

    def test_disabled_missing_and_broken_plugins_are_unavailable(self):
        self.metadata.activated = False
        self.assertFalse(self.backend.status()["available"])
        self.metadata = None
        self.assertFalse(self.backend.status()["installed"])
        self.metadata = NS(activated=True, star_cls=NS(extension_api=NS()))
        self.assertEqual(self.backend.operations, ())
        self.metadata = NS(activated=True, star_cls=NS(extension_api=self.api))
        self.available = False
        self.assertEqual(self.backend.operations, ())

    def test_reload_resolves_the_current_instance_without_cached_credentials(self):
        self.metadata.star_cls = None
        self.assertEqual(self.backend.operations, ())
        self.metadata.star_cls = NS(extension_api=self.api)
        self.assertEqual(self.backend.operations, ("generate", "edit"))

    async def test_generation_uses_host_prompt_and_returns_bytes_to_the_native_owner(self):
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "nai", "operation_routes": {"generate": ["nai"], "edit": []}},
            self.root / "routes", context=self.context)
        result = await router.execute(self.invocation(envelope={
            "image_prompt": {"positive": "host prepared", "negative": "host negative"},
        }), (), self.dispatch)
        self.assertEqual(self.requests[0]["prompt_text"], "host prepared")
        self.assertEqual(self.requests[0]["negative"], "host negative")
        self.assertEqual(result.images, (image_bytes("blue"),))
        self.assertEqual(result.receipt["backend"], "nai")
        self.assertNotIn("api_key", self.requests[0])
        self.assertNotIn("size", self.requests[0])

    async def test_edit_references_are_temporary_and_strength_is_forwarded(self):
        reference = ReferenceImage("reference", image_bytes("red"), "source", "0" * 64)
        await self.backend.execute(self.invocation("edit", reference_ids=("reference",),
            parameters={"denoising_strength": 0.6, "width": 832, "height": 1216}),
            (reference,), self.dispatch)
        request = self.requests[0]
        self.assertEqual(request["strength"], 0.6)
        self.assertEqual(request["size"], "832x1216")
        self.assertTrue(all(not Path(path).exists() for path in request["reference_image_paths"]))

    async def test_nai_uses_its_own_prompt_variant_without_companion_style_or_defaults(self):
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "nai", "operation_routes": {"generate": ["nai"], "edit": []}},
            self.root / "routes", context=self.context)
        await router.execute(self.invocation(envelope={
            "image_prompt": {"positive": "photorealistic, other style", "negative": "host defaults"},
            "nai_prompt": {"positive": "1girl, silver hair, green eyes", "negative": ""},
        }), (), self.dispatch)
        self.assertEqual(self.requests[0]["prompt_text"], "1girl, silver hair, green eyes")
        self.assertEqual(self.requests[0]["negative"], "")

    async def test_prompts_are_dispatched_without_calling_a_legacy_validator(self):
        self.api.validate_companion_prompt = Mock(side_effect=AssertionError("unexpected preflight"))
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "auto", "sd_webui": {"base_url": "http://example.test"},
             "operation_routes": {"generate": ["nai", "sd_webui"], "edit": []}},
            self.root / "routes", context=self.context)
        with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
            local.return_value.operations = ("generate",)
            local.return_value.execute = AsyncMock()
            result = await router.execute(self.invocation(envelope={
                "nai_prompt": {"positive": "银发女孩坐在窗边", "negative": "不要黑发"},
            }), (), self.dispatch)
            local.return_value.execute.assert_not_awaited()
        self.assertEqual(self.requests[0]["prompt_text"], "银发女孩坐在窗边")
        self.assertEqual(self.requests[0]["negative"], "不要黑发")
        self.assertEqual(result.images, (image_bytes("blue"),))
        self.assertEqual(self.fences, 1)
        self.api.validate_companion_prompt.assert_not_called()
        self.api.generate_for_companion.assert_awaited_once()

    async def test_authority_revocation_prevents_the_plugin_call(self):
        async def revoked():
            raise NativeImageError("image_authority_revoked", unconfirmed=False)
        with self.assertRaisesRegex(NativeImageError, "image_authority_revoked"):
            await self.backend.execute(self.invocation(), (), revoked)
        self.api.generate_for_companion.assert_not_awaited()

    async def test_direct_mode_never_silently_drops_edit_references(self):
        self.mode = "direct"
        with self.assertRaises(NativeImageError):
            await self.backend.execute(self.invocation("edit"), (), self.dispatch)
        self.api.generate_for_companion.assert_not_awaited()

    async def test_unsupported_parameters_are_rejected_before_paid_dispatch(self):
        with self.assertRaisesRegex(NativeImageError, "unsupported_image_parameter"):
            await self.backend.execute(self.invocation(parameters={"steps": 28}), (), self.dispatch)
        self.assertEqual(self.fences, 0)
        self.api.generate_for_companion.assert_not_awaited()

    async def test_failure_after_dispatch_does_not_try_another_paid_backend(self):
        self.api.generate_for_companion.side_effect = TimeoutError("synthetic response lost")
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "auto", "sd_webui": {"base_url": "http://example.test"},
             "operation_routes": {"generate": ["nai", "sd_webui"], "edit": []}},
            self.root / "routes", context=self.context)
        with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
            local.return_value.operations = ("generate",)
            local.return_value.execute = AsyncMock()
            with self.assertRaisesRegex(NativeImageError, "nai_generation_timed_out"):
                await router.execute(self.invocation(), (), self.dispatch)
            local.return_value.execute.assert_not_awaited()
        self.api.generate_for_companion.assert_awaited_once()

    async def test_confirmed_disabled_result_can_fall_back_without_repeating_a_paid_request(self):
        self.api.generate_for_companion.side_effect = None
        self.api.generate_for_companion.return_value = {"handled": False, "reason": "disabled"}
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "auto", "sd_webui": {"base_url": "http://example.test"},
             "operation_routes": {"generate": ["nai", "sd_webui"], "edit": []}},
            self.root / "routes", context=self.context)
        with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
            local.return_value.operations = ("generate",)
            local.return_value.execute = AsyncMock(return_value="fallback")
            self.assertEqual(await router.execute(self.invocation(), (), self.dispatch), "fallback")
            local.return_value.execute.assert_awaited_once()
        self.api.generate_for_companion.assert_awaited_once()

    async def test_opaque_error_notes_cannot_be_used_as_proof_to_switch_backends(self):
        self.api.generate_for_companion.side_effect = None
        self.api.generate_for_companion.return_value = {
            "handled": True, "image_path": "", "note": "401 unauthorized; must-not-save"}
        router = OutputRoutingBackend(
            {"enabled": True, "backend": "nai", "operation_routes": {"generate": ["nai", "sd_webui"], "edit": []}},
            self.root / "routes", context=self.context)
        with patch("astrbot_host.native_extensions.image_native.output_routing.NativeTransportBackend") as local:
            local.return_value.operations = ("generate",)
            local.return_value.execute = AsyncMock()
            with self.assertRaises(NativeImageError) as caught:
                await router.execute(self.invocation(), (), self.dispatch)
            local.return_value.execute.assert_not_awaited()
        self.assertTrue(caught.exception.unconfirmed)
        self.assertEqual(caught.exception.diagnostics[0]["reason"], "nai_generation_unconfirmed")
        self.assertNotIn("must-not-save", str(caught.exception.diagnostics))

    async def test_missing_result_is_unconfirmed_and_does_not_leak_plugin_messages(self):
        self.api.generate_for_companion.side_effect = None
        self.api.generate_for_companion.return_value = {"handled": True, "note": "secret"}
        with self.assertRaises(NativeImageError) as raised:
            await self.backend.execute(self.invocation(), (), self.dispatch)
        self.assertTrue(raised.exception.unconfirmed)
        self.assertNotIn("secret", str(raised.exception))

    def test_configuration_roundtrip_reuses_the_plugin_without_storing_its_keys(self):
        api = ImageExtensionAPI(data_root=self.root, config_path=self.root / "image.json",
                                version="test", context=self.context)
        self.addCleanup(api.close)
        before = api.output_configuration()
        saved = api.save_output_configuration(before["revision"], {
            "enabled": True, "backend": "nai",
            "operation_routes": {"generate": ["nai"], "edit": ["nai"]},
        })
        self.assertEqual(saved["settings"]["operation_routes"]["generate"], ["nai"])
        self.assertEqual(api.companion_v2_native().owner.backend.operations, ("generate", "edit"))
        self.assertTrue(saved["nai"]["available"])
        self.assertNotIn("nai", saved["settings"])
        self.assertNotIn("must-not-be-projected", (self.root / "image.json").read_text())
        self.available = False
        reread = api.output_configuration()
        self.assertFalse(reread["nai"]["available"])
        self.assertEqual(reread["revision"], saved["revision"])
        self.assertEqual(reread["settings"]["operation_routes"]["edit"], ["nai"])
