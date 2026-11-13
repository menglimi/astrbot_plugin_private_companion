"""The model catalog follows the operations enabled by V2 image routes."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from astrbot_host.native_extensions import NativeExtensionRegistry
from astrbot_host.native_extensions.builtins import BundledNativeLease
from astrbot_host.native_extensions.image_native.adapter import PLUGIN_ID
from astrbot_host.native_extensions.image_native.backends import NativeTransportBackend
from astrbot_host.native_extensions.image_native.comfyui_service import ComfyUIService
from astrbot_host.native_extensions.image_native.extension import ImageExtensionAPI
from astrbot_host.native_extensions.image_native.output_configuration import settings as image_settings
from astrbot_host.native_extensions.image_native.types import ImageInvocation, NativeImageError, ReferenceImage
from companion_v2.capabilities.models import FurnitureDescriptor
from companion_v2.product import PRODUCT_VERSION


class NativeImageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.api = ImageExtensionAPI(data_root=root, config_path=root / "image-config.json",
                                     version=PRODUCT_VERSION)
        lease = BundledNativeLease(PLUGIN_ID, self.api.companion_v2_native(), api=self.api,
            furniture=FurnitureDescriptor("image", "image", "画面工作室", "image"))
        self.registry = NativeExtensionRegistry(NS(get_all_stars=lambda: ()), bundled_extensions=(lease,))
        self.registry._enforcing_image_settings = True
        self.declarations = {item.descriptor.tool.action: item for item in self.registry.declarations()}

    def tearDown(self):
        self.api.close()
        self.temporary.cleanup()

    def save_online(self, platform):
        current = self.api.output_configuration()
        return self.api.save_output_configuration(current["revision"], {
            "enabled": True,
            "backend": "online_api",
            "endpoints": [{"id": "primary", "name": "Primary", "enabled": True,
                           "platform": platform, "model": "image-model", "width": 1024, "height": 1024,
                           "base_url": "https://api.example.test/v1", "timeout_seconds": 60}],
        })

    @staticmethod
    def endpoint(identifier, model):
        return {"id": identifier, "name": identifier, "enabled": True,
                "platform": "openai", "model": model, "width": 1024, "height": 1024,
                "base_url": "https://api.example.test/v1", "timeout_seconds": 60}

    def test_legacy_image_configuration_gets_independent_default_queues(self):
        plugin = NS(config={"native_v2": {"output": {
            "enabled": True, "backend": "auto", "endpoints": [{"id": "legacy"}],
            "sd_webui": {}, "comfyui": {},
        }}})
        routes = image_settings(plugin)["operation_routes"]
        self.assertEqual(routes["generate"], ["online:legacy", "comfyui", "sd_webui"])
        self.assertEqual(routes["edit"], routes["generate"])

        plugin.config["native_v2"]["output"]["operation_routes"] = {
            "generate": ["online:missing", None, "sd_webui"], "edit": []}
        routes = image_settings(plugin)["operation_routes"]
        self.assertEqual(routes["generate"], ["sd_webui"])
        self.assertEqual(routes["edit"], [])

    def test_generation_and_edit_use_independent_priorities_and_mask_edit_shares_edit_queue(self):
        current = self.api.output_configuration()
        saved = self.api.save_output_configuration(current["revision"], {
            "enabled": True,
            "backend": "online_api",
            "endpoints": [self.endpoint("primary", "image-v1"), self.endpoint("secondary", "image-v2")],
            "operation_routes": {
                "generate": ["online:primary", "online:secondary"],
                "edit": ["online:secondary", "online:primary"],
            },
        })
        self.assertEqual(saved["settings"]["operation_routes"]["generate"], ["online:primary", "online:secondary"])
        self.assertEqual(saved["settings"]["operation_routes"]["edit"], ["online:secondary", "online:primary"])

        backend = self.api.companion_v2_native().owner.backend
        route_ids = lambda operation: [item.config["id"] for item in backend._routes(operation)]
        self.assertEqual(route_ids("generate"), ["primary", "secondary"])
        self.assertEqual(route_ids("edit"), ["secondary", "primary"])
        self.assertEqual(route_ids("mask_edit"), route_ids("edit"))

    def test_unknown_model_route_is_rejected_on_save(self):
        current = self.api.output_configuration()
        with self.assertRaises(NativeImageError):
            self.api.save_output_configuration(current["revision"], {
                "enabled": False,
                "backend": "auto",
                "operation_routes": {"generate": ["online:missing"], "edit": []},
            })

    def test_unadapted_platform_can_be_saved_but_cannot_claim_an_active_route(self):
        endpoint = {**self.endpoint("primary", "image-v1"), "platform": "modelscope"}
        current = self.api.output_configuration()
        saved = self.api.save_output_configuration(current["revision"], {
            "enabled": False, "backend": "online_api", "endpoints": [endpoint],
        })
        self.assertEqual(saved["settings"]["endpoints"][0]["platform"], "modelscope")
        with self.assertRaises(NativeImageError) as error:
            self.api.save_output_configuration(saved["revision"], {"enabled": True})
        self.assertEqual(error.exception.code, "native_endpoint_unconfigured")

    def test_online_endpoint_persists_quality_and_reference_image_limit(self):
        current = self.api.output_configuration()
        endpoint = self.endpoint("primary", "image-v1")
        endpoint.update(quality="hd", max_reference_images=4)
        saved = self.api.save_output_configuration(current["revision"], {
            "enabled": True, "backend": "online_api", "endpoints": [endpoint],
        })

        projected = saved["settings"]["endpoints"][0]
        self.assertEqual(projected["quality"], "hd")
        self.assertEqual(projected["max_reference_images"], 4)
        route = next(self.api.companion_v2_native().owner.backend._routes("edit"))
        self.assertEqual(route.config["quality"], "hd")
        self.assertEqual(route.config["max_reference_images"], 4)

    def test_online_endpoint_custom_name_is_trimmed_and_blank_is_supported(self):
        current = self.api.output_configuration()
        endpoint = self.endpoint("primary", "image-v1")
        endpoint["name"] = "  主力生图  "
        saved = self.api.save_output_configuration(current["revision"], {
            "enabled": True, "backend": "online_api", "endpoints": [endpoint],
        })
        self.assertEqual(saved["settings"]["endpoints"][0]["name"], "主力生图")

        endpoint["name"] = "   "
        saved = self.api.save_output_configuration(saved["revision"], {
            "enabled": True, "backend": "online_api", "endpoints": [endpoint],
        })
        self.assertEqual(saved["settings"]["endpoints"][0]["name"], "")

    def test_legacy_image_options_network_and_header_values_survive_save_and_read(self):
        current = self.api.output_configuration()
        endpoint = self.endpoint("primary", "image-v1")
        endpoint.update(size_label="2K", custom_headers={"X-Region": "cn"})
        saved = self.api.save_output_configuration(current["revision"], {
            "enabled": True, "backend": "online_api", "endpoints": [endpoint],
            "network": {"download_proxy": "http://127.0.0.1:7890", "use_environment_proxy": True},
            "legacy_backend": "tool_call",
            "legacy_options": {"tool_call": {"name": "generate_selfie", "prompt_param": "description"}},
        })

        reread = self.api.output_configuration()
        self.assertEqual(reread["settings"]["legacy_backend"], "tool_call")
        self.assertEqual(reread["settings"]["legacy_options"]["tool_call"]["name"], "generate_selfie")
        self.assertEqual(reread["settings"]["network"]["download_proxy_set"], True)
        self.assertTrue(reread["settings"]["network"]["use_environment_proxy"])
        self.assertNotIn("download_proxy", reread["settings"]["network"])
        self.assertEqual(reread["settings"]["endpoints"][0]["size_label"], "2K")
        self.assertEqual(reread["settings"]["endpoints"][0]["custom_header_names"], ["X-Region"])
        self.assertNotIn("custom_headers", reread["settings"]["endpoints"][0])

    def test_public_projection_can_be_saved_back_unchanged(self):
        self.save_online("openai")
        current = self.api.output_configuration()
        for name in ("sd_webui", "comfyui"):
            self.assertNotIn("custom_headers_set", current["settings"][name])
            self.assertNotIn("custom_header_names", current["settings"][name])

        saved = self.api.save_output_configuration(current["revision"], current["settings"])

        self.assertEqual(saved["revision"], current["revision"])
        self.assertEqual(saved["settings"]["endpoints"][0]["model"], "image-model")

    def test_online_image_download_uses_configured_proxy(self):
        from astrbot_host.native_extensions.image_native.output_routing import OnlineImageBackend

        backend = OnlineImageBackend(self.endpoint("primary", "image-v1"), {
            "download_proxy": "http://127.0.0.1:7890", "use_environment_proxy": False,
        })
        self.assertEqual(backend._request_options("https://images.example.test/result.png"),
                         {"proxy": "http://127.0.0.1:7890"})

    def test_comfyui_http_timeout_uses_saved_configuration(self):
        import asyncio

        service = ComfyUIService({"base_url": "http://127.0.0.1:8188", "timeout_seconds": 73},
                                 Path(self.temporary.name))

        async def check():
            session = await service._http()
            try:
                return session.timeout.total
            finally:
                await service.close()

        self.assertEqual(asyncio.run(check()), 73)

    def test_sd_webui_uses_generate_and_edit_parameter_overrides(self):
        backend = NativeTransportBackend("sd_webui", {
            "base_url": "http://127.0.0.1:7860", "timeout_seconds": 60,
            "parameters": {"steps": 18, "generate": {"steps": 28},
                           "edit": {"steps": 42, "cfg_scale": 5.5}},
        }, Path(self.temporary.name))
        generate = ImageInvocation("scope", "generate", "generate", "origin", "generate", "prompt")
        reference = ReferenceImage("ref", b"test-reference", "test-source", "0" * 64)
        edit = ImageInvocation("scope", "edit", "edit", "origin", "edit", "prompt", reference_ids=("ref",))

        self.assertEqual(backend._plan(generate)["parameters"]["steps"], 28)
        edit_parameters = backend._plan(edit, (reference,))["parameters"]
        self.assertEqual(edit_parameters["steps"], 42)
        self.assertEqual(edit_parameters["cfg_scale"], 5.5)

    def test_dormant_provider_keeps_management_api_but_exposes_no_unconfigured_action(self):
        self.assertEqual(self.api.companion_v2_native().manifest()["package"]["capabilities"], [
            "image.generate", "image.edit", "image.mask_edit",
        ])
        self.assertEqual(self.api.companion_v2_native().owner.backend.operations, ())
        self.assertIs(self.registry.image_extension_api(), self.api)
        self.assertTrue(all(self.registry.current_declaration(action) is None for action in self.declarations))

    def test_output_switch_keeps_management_api_loaded_and_can_be_reenabled(self):
        current = self.api.output_configuration()
        saved = self.api.save_output_configuration(current["revision"], {"enabled": False})

        self.assertFalse(saved["settings"]["enabled"])
        self.assertFalse(self.api.companion_v2_native()._closed)
        self.assertIs(self.registry.image_extension_api(), self.api)
        self.assertIn("image.generate", self.api.companion_v2_native().manifest()["package"]["capabilities"])
        self.assertTrue(self.api.config["native_v2"]["enabled"])

        saved = self.save_online("openai")
        self.assertTrue(saved["settings"]["enabled"])
        self.assertEqual(self.api.companion_v2_native().owner.backend.operations, ("generate", "edit"))
        self.assertIs(self.registry.image_extension_api(), self.api)

    def test_disabled_output_from_previous_run_does_not_hide_configuration(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "image-config.json"
            config_path.write_text(json.dumps({"native_v2": {
                "enabled": False,
                "backend": "auto",
                "output": {"enabled": False, "backend": "auto", "endpoints": [],
                           "sd_webui": {}, "comfyui": {}},
            }}), encoding="utf-8")
            api = ImageExtensionAPI(data_root=root, config_path=config_path,
                                    version=PRODUCT_VERSION)
            try:
                self.assertFalse(api.output_configuration()["settings"]["enabled"])
                self.assertFalse(api.companion_v2_native()._closed)

                current = api.output_configuration()
                saved = api.save_output_configuration(current["revision"], {
                    "enabled": True,
                    "backend": "online_api",
                    "endpoints": [self.endpoint("primary", "image-v1")],
                })

                self.assertTrue(saved["settings"]["enabled"])
                self.assertFalse(api.companion_v2_native()._closed)
                self.assertEqual(api.companion_v2_native().owner.backend.operations,
                                 ("generate", "edit"))
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertTrue(persisted["native_v2"]["enabled"])
                self.assertTrue(persisted["native_v2"]["output"]["enabled"])
            finally:
                api.close()

    def test_online_backend_exposes_only_operations_supported_by_its_platform(self):
        self.save_online("minimax")
        self.assertEqual(self.api.companion_v2_native().owner.backend.operations, ("generate",))
        self.assertIsNotNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.generate"))
        self.assertIsNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.edit"))
        self.assertIsNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.mask_edit"))
        self.assertEqual(self.registry.declaration_readiness("native:" + PLUGIN_ID + ":image.mask_edit"),
                         "unsupported")

        self.save_online("openai")
        self.assertEqual(self.api.companion_v2_native().owner.backend.operations, ("generate", "edit"))
        self.assertIsNotNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.edit"))
        self.assertIsNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.mask_edit"))
        self.assertEqual(self.registry.declaration_readiness("native:" + PLUGIN_ID + ":image.mask_edit"),
                         "unsupported")

    def test_auto_platform_does_not_send_openai_requests_to_other_protocols(self):
        from astrbot_host.native_extensions.image_native.output_routing import OnlineImageBackend, resolve_platform

        for url, expected, operations in (
            ("https://dashscope.aliyuncs.com/api/v1", "bailian", ("generate",)),
            ("https://api-inference.modelscope.cn/v1", "modelscope", ()),
            ("https://generativelanguage.googleapis.com/v1beta", "gemini", ()),
        ):
            with self.subTest(url=url):
                endpoint = {**self.endpoint("primary", "image-v1"), "platform": "auto", "base_url": url}
                self.assertEqual(resolve_platform(endpoint), expected)
                self.assertEqual(OnlineImageBackend(endpoint).operations, operations)

    def test_dashscope_root_routes_to_native_multimodal_and_compatible_path_stays_openai(self):
        from astrbot_host.native_extensions.image_native.output_routing import (
            bailian_image_body, bailian_image_entries, effective_platform, operation_url)

        endpoint = {**self.endpoint("primary", "image-v1"), "platform": "openai",
                    "base_url": "https://dashscope.aliyuncs.com", "model": "wan2.2-t2i-flash",
                    "width": 1024, "height": 1024}
        self.assertEqual(effective_platform(endpoint), "bailian")
        self.assertEqual(operation_url(endpoint["base_url"], "generate", "bailian"),
                         "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
        self.assertEqual(operation_url("https://dashscope.aliyuncs.com/api/v1", "generate", "bailian"),
                         "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
        body = bailian_image_body(endpoint, "a frog")
        self.assertEqual(body["input"]["messages"][0]["content"][0]["text"], "a frog")
        self.assertEqual(body["parameters"]["size"], "1024*1024")
        self.assertEqual(bailian_image_entries({"output": {"choices": [{"message": {
            "content": [{"image": "https://example.test/frog.png"}]}}]}}),
            [{"url": "https://example.test/frog.png"}])
        compatible = {**endpoint, "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
        self.assertEqual(effective_platform(compatible), "openai")

    def test_sd_webui_enables_masked_edit_after_a_real_endpoint_is_configured(self):
        current = self.api.output_configuration()
        self.api.save_output_configuration(current["revision"], {
            "enabled": True,
            "backend": "sd_webui",
            "endpoints": [],
            "sd_webui": {"base_url": "http://127.0.0.1:7860", "timeout_seconds": 60,
                          "parameters": {}},
            "comfyui": {},
        })
        self.assertEqual(self.api.companion_v2_native().owner.backend.operations,
                         ("generate", "edit", "mask_edit"))
        self.assertIsNotNone(self.registry.current_declaration("native:" + PLUGIN_ID + ":image.mask_edit"))


if __name__ == "__main__":
    unittest.main()
