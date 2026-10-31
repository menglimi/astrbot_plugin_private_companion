import unittest

from companion_v2.legacy.image_configuration_migration import convert_image_configuration


class ImageConfigurationMigrationTests(unittest.TestCase):
    def test_online_queue_keeps_order_headers_and_provider_size_labels(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "external",
            "enable_photo_reference_image": True,
            "external_image_api_endpoints": [
                {"name": "Agnes", "platform": "agnes", "base_url": "https://api.example.test/v1",
                 "api_key": "secret-one", "model": "agnes-image-2.1-flash", "size": "2K",
                 "timeout_seconds": 120, "custom_headers": "X-Region: cn"},
                {"name": "Second", "platform": "openai", "base_url": "https://api2.example.test/v1",
                 "api_key": "secret-two", "model": "image-v2", "size": "768x1344",
                 "timeout_seconds": 90, "custom_headers": {"X-Route": "fallback"}},
            ],
        }})

        self.assertEqual(result.output_changes["backend"], "online_api")
        endpoints = result.output_changes["endpoints"]
        self.assertEqual([item["name"] for item in endpoints], ["Agnes", "Second"])
        self.assertEqual((endpoints[0]["width"], endpoints[0]["height"], endpoints[0]["size_label"]),
                         (2048, 2048, "2K"))
        self.assertEqual(endpoints[0]["custom_headers"], {"X-Region": "cn"})
        self.assertEqual(endpoints[1]["custom_headers"], {"X-Route": "fallback"})
        self.assertEqual(result.output_changes["operation_routes"]["generate"],
                         ["online:legacy-image-01", "online:legacy-image-02"])
        self.assertTrue(result.output_changes["enabled"])

    def test_non_executable_legacy_modes_and_network_settings_remain_auditable(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "tool_call",
            "custom_photo_tool_name": "generate_selfie",
            "custom_photo_tool_prompt_param": "description",
            "custom_photo_tool_kind_param": "kind",
            "custom_photo_tool_reference_param": "image_path",
            "custom_photo_tool_extra_params": "{\"steps\": 24}",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "landscape",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "portrait",
            "external_image_download_proxy": "http://127.0.0.1:7890",
            "external_image_download_use_environment_proxy": True,
        }})

        self.assertEqual(result.output_changes["backend"], "auto")
        self.assertEqual(result.output_changes["legacy_backend"], "tool_call")
        self.assertFalse(result.output_changes["enabled"])
        self.assertEqual(result.output_changes["operation_routes"], {"generate": [], "edit": []})
        self.assertEqual(result.output_changes["legacy_options"], {
            "tool_call": {"name": "generate_selfie", "prompt_param": "description",
                          "kind_param": "kind", "reference_param": "image_path",
                          "extra_params": "{\"steps\": 24}"},
            "comfyui_workflow_names": {"generate": "landscape", "selfie": "portrait"},
        })
        self.assertEqual(result.output_changes["network"], {
            "download_proxy": "http://127.0.0.1:7890", "use_environment_proxy": True,
        })
        difference_fields = {item["field"] for item in result.differences}
        self.assertIn("image.output.legacy_options.tool_call", difference_fields)
        self.assertIn("image.output.legacy_options.comfyui_workflow_names", difference_fields)

    def test_auto_size_label_is_kept_with_a_usable_v2_dimension_fallback(self):
        result = convert_image_configuration({"photo_action_config": {
            "external_image_api_endpoints": [{"name": "Auto size", "platform": "openai",
                "base_url": "https://api.example.test/v1", "model": "image-v1", "size": "auto"}],
        }})

        endpoint = result.output_changes["endpoints"][0]
        self.assertEqual(endpoint["size_label"], "auto")
        self.assertEqual((endpoint["width"], endpoint["height"]), (1024, 1024))

    def test_sdgen_is_not_misidentified_as_native_sd_webui(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "sdgen",
        }})

        self.assertEqual(result.output_changes["backend"], "auto")
        self.assertEqual(result.output_changes["legacy_backend"], "sdgen")
        self.assertFalse(result.output_changes["enabled"])
        self.assertEqual(result.output_changes["operation_routes"], {"generate": [], "edit": []})
        self.assertTrue(any(item["kind"] == "manual_mapping" for item in result.differences))

    def test_auto_platform_retains_special_provider_protocol(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "external",
            "external_image_api_endpoints": [{"name": "ModelScope", "platform": "auto",
                "base_url": "https://api-inference.modelscope.cn/v1", "model": "sample"}],
        }})
        self.assertEqual(result.output_changes["endpoints"][0]["platform"], "modelscope")
        self.assertFalse(result.output_changes["enabled"])

    def test_local_backend_stays_dormant_until_its_connection_is_bound(self):
        result = convert_image_configuration({"photo_action_config": {
            "photo_generation_backend": "comfyui",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "legacy-flow",
        }})
        self.assertEqual(result.output_changes["operation_routes"]["generate"], ["comfyui"])
        self.assertFalse(result.output_changes["enabled"])
        self.assertTrue(any(item["kind"] == "incomplete_backend" for item in result.differences))

    def test_quality_vocabularies_match_the_save_path_exactly(self):
        # 迁移端与保存端各写一份词表（companion_v2 的隔离规则不允许反向导入
        # astrbot_host，保存端所在的镜像制品又被发布基线钉住）。这里直接比较
        # 两个集合，而不是只遍历保存端：任一方向多出一个取值都会先红——迁移端
        # 多出会让保存端整份拒绝 image.output，保存端多出会让迁入的端点被静默
        # 降级成 invalid_value。空串只属于保存端，表示“用服务端默认”。
        from astrbot_host.native_extensions.image_native.output_configuration import IMAGE_QUALITIES
        from companion_v2.legacy.image_configuration_migration import _IMAGE_QUALITIES

        self.assertEqual(IMAGE_QUALITIES, set(_IMAGE_QUALITIES) | {""})

    def test_every_save_path_quality_reaches_the_endpoint_unchanged(self):
        # 词表相等由上一个用例钉住；这里验证每个保存端取值迁入后都不被改写，
        # 也不产生差异记录（例如将来给迁移加“归一化”时先红）。
        from astrbot_host.native_extensions.image_native.output_configuration import IMAGE_QUALITIES

        for quality in sorted(IMAGE_QUALITIES):
            with self.subTest(quality=quality):
                result = convert_image_configuration({"photo_action_config": {
                    "external_image_api_endpoints": [{"name": "Quality", "platform": "openai",
                        "base_url": "https://api.example.test/v1", "model": "image-v1",
                        "quality": quality}],
                }})
                self.assertEqual(result.output_changes["endpoints"][0]["quality"], quality)
                self.assertEqual(result.differences, ())


if __name__ == "__main__":
    unittest.main()
