# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImageModelConfigUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        cls.provider_tree = (
            ROOT / "pages" / "陪伴面板" / "js" / "panels" / "provider-tree.js"
        ).read_text(encoding="utf-8")
        cls.html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        cls.api = (ROOT / "page_api.py").read_text(encoding="utf-8")

    def test_model_page_has_separate_image_model_navigation(self) -> None:
        self.assertIn('data-models-section="providers"', self.html)
        self.assertIn('data-models-section="image"', self.html)
        self.assertIn('id="modelsImagePane"', self.html)
        self.assertIn('id="saveImageModelsBtn"', self.html)

    def test_split_plugin_ui_is_hidden_until_the_plugin_is_detected(self) -> None:
        self.assertIn('id="modelsImageTab"', self.html)
        self.assertIn('aria-selected="false" hidden>生图模型</button>', self.html)
        self.assertIn("function imageCompanionInstalled()", self.script)
        self.assertIn("function realityCompanionInstalled()", self.script)
        self.assertIn("function syncExternalCompanionVisibility()", self.script)
        self.assertIn("enable_photo_text_action: () => anyImageGeneratorInstalled()", self.script)
        self.assertIn(
            "enable_experimental_bluetooth_wakeup: () => realityCompanionInstalled()",
            self.script,
        )
        self.assertIn(
            "enable_qzone_generated_image_publish: () => imageCompanionInstalled()",
            self.script,
        )
        self.assertIn("function visibleExperimentalFeatureKeys()", self.script)
        self.assertIn("function visibleTroubleshootingCategories()", self.script)
        self.assertIn("function visibleSetupGuideAdvancedItems(blockId)", self.script)
        self.assertIn("const items = visibleSetupGuideAdvancedItems(block.id);", self.script)
        self.assertIn("if (!setting || !visibleConfigKey(setting.key)) return false;", self.script)
        self.assertIn("if (!realityCompanionInstalled())", self.script)
        self.assertIn("if (!imageCompanionInstalled())", self.script)

    def test_legacy_qzone_image_settings_remain_editable_without_image_extension(self) -> None:
        self.assertIn("const legacyConfigGraceKeys = new Set([", self.script)
        for key in (
            "enable_qzone_generated_image_publish",
            "qzone_generated_image_probability",
            "qzone_publish_image_style_prompt",
        ):
            self.assertIn(f'"{key}"', self.script)
        self.assertIn(
            "if (unavailablePluginIntegrationOwner(key) && !legacyConfigGraceKeys.has(key)) return false;",
            self.script,
        )
        self.assertIn(
            "未检测到生图扩展；仍可修改此配置，运行时会自动跳过配图并发布纯文字",
            self.script,
        )
        companion_script = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(self.script, companion_script)

    def test_provider_category_switch_uses_one_delegated_click(self) -> None:
        toolbar_binding = self.provider_tree.split("function bindProviderToolbar", 1)[1]
        self.assertIn('document.querySelector(".provider-mode-switch")', toolbar_binding)
        self.assertIn('modeSwitch.addEventListener("click"', toolbar_binding)
        self.assertIn('event.target?.closest?.("[data-provider-mode]")', toolbar_binding)
        self.assertIn("state.providerMode = button.dataset.providerMode", toolbar_binding)
        self.assertIn("rerenderProviders();", toolbar_binding)
        self.assertIn("rerenderProviders: renderProviders", self.script)
        self.assertNotIn("renderProviders(context);", toolbar_binding)
        self.assertIn('button.setAttribute("aria-pressed"', self.script)
        self.assertIn(
            'loadOptionalClassicScript("./js/panels/provider-tree.js?v=20260804-reading-archive-capability-v1',
            self.script,
        )

    def test_reading_archive_provider_ui_follows_runtime_capability(self) -> None:
        summary = self.provider_tree.split("function renderProviderSummary", 1)[1].split(
            "function deepseekPeakProviderControl", 1
        )[0]
        flow = self.provider_tree.split("function renderProviderFlow", 1)[1].split(
            "function providerGroupMarkup", 1
        )[0]
        capability_check = 'visibleConfigKey("READING_ARCHIVE_VISION_PROVIDER_ID")'
        self.assertIn(capability_check, summary)
        self.assertIn("readingVisionAvailable", summary)
        self.assertIn(capability_check, flow)
        self.assertIn("readingVisionNode", flow)
        self.assertNotIn("JM 本子", summary)

    def test_authoritative_overview_clears_saved_provider_drafts(self) -> None:
        apply_overview = self.script.split("function applyOverviewData", 1)[1].split(
            "async function loadUserGroupLists", 1
        )[0]
        self.assertIn("state.providerDraft = {};", apply_overview)
        run_action = self.script.split("async function runAction", 1)[1].split(
            "function actionResultPersisted", 1
        )[0]
        self.assertIn("applyOverviewData(result);", run_action)

    def test_image_model_page_tests_the_current_endpoint_draft(self) -> None:
        self.assertIn('postJson("/image_api/test"', self.script)
        self.assertIn("endpoint_index: index", self.script)
        self.assertIn("endpoint,", self.script)
        self.assertIn("data-image-api-test", self.script)
        self.assertIn('"未执行旧式测试"', self.script)
        self.assertIn('result.test_status === "unsupported"', self.script)
        self.assertIn('unsupported: "未执行旧式测试"', self.script)

    def test_saved_test_result_is_invalidated_when_the_request_draft_changes(self) -> None:
        self.assertIn("imageApiEndpointSavedFingerprints", self.script)
        self.assertIn("!== fingerprint", self.script)
        self.assertIn("custom_headers: item.custom_headers", self.script)

    def test_image_api_routes_delegate_to_the_optional_image_owner(self) -> None:
        self.assertIn('("/image_api/status", self.get_image_api_status', self.api)
        self.assertIn('("/image_api/test", self.test_image_api_endpoint', self.api)
        self.assertIn('"_image_companion_test_endpoint"', self.api)
        self.assertNotIn('"_run_external_photo_generation_with_endpoint"', self.api)
        self.assertIn("不会尝试队列中的其他 API", self.api)

    def test_feature_detail_no_longer_owns_the_endpoint_editor(self) -> None:
        self.assertNotIn(
            'key === "enable_photo_text_action" ? photoApiEndpointEditorHtml()',
            self.script,
        )

    def test_photo_feature_switch_points_to_image_model_page(self) -> None:
        self.assertIn(
            'enable_photo_text_action: ["主动拍照/生图", "允许 Bot 在合适的主动动机下生成真实图片；生图 API 地址、Key、模型和队列请到“模型配置 → 生图模型”配置。"]',
            self.script,
        )

    def test_photo_feature_has_direct_image_model_config_shortcut(self) -> None:
        self.assertIn(
            'enable_photo_text_action: ["image", "前往生图模型"]',
            self.script,
        )
        self.assertIn('data-model-config-jump="${target[0]}"', self.script)
        self.assertIn("function openModelConfigSection(section)", self.script)
        self.assertIn("state.modelsSection = target;", self.script)
        self.assertIn('switchTab("models");', self.script)

    def test_legacy_backup_api_switch_is_not_rendered_as_orphan_feature(self) -> None:
        self.assertIn(
            'enable_backup_external_image_api: "enable_photo_text_action"',
            self.script,
        )

    def test_photo_feature_card_does_not_duplicate_online_model_credentials(self) -> None:
        photo_card = self.script.split('key: "enable_photo_text_action"', 1)[1].split(
            'key: "enable_news_integration"', 1
        )[0]
        for key in (
            "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL",
            "EXTERNAL_IMAGE_API_KEY",
            "EXTERNAL_IMAGE_API_MODEL",
            "external_image_api_size",
        ):
            self.assertNotIn(f'{{ key: "{key}"', photo_card)
        self.assertIn("模型配置 → 生图模型", photo_card)

    def test_all_photo_child_switches_are_embedded_under_photo_feature(self) -> None:
        for key in (
            "enable_photo_reference_image",
            "enable_group_nsfw_private_fallback",
            "enable_daily_outfit_photo",
            "enable_creative_cover_generation",
            "enable_natural_language_photo_generation",
            "enable_user_requested_photo_generation",
            "enable_local_photo_load_guard",
        ):
            self.assertIn(f'{key}: "enable_photo_text_action"', self.script)

    def test_photo_feature_visibility_is_centralized_and_reactive(self) -> None:
        self.assertIn("function photoSettingVisibleForValues", self.script)
        self.assertIn('if (toolOnly.has(settingKey)) return backend === "tool_call";', self.script)
        self.assertIn('return localBackends.has(backend) && enabled("enable_local_photo_load_guard");', self.script)
        self.assertIn('return backend !== "sdgen" && enabled("enable_photo_reference_image");', self.script)
        for key in (
            "photo_generation_backend",
            "enable_photo_reference_image",
            "enable_group_nsfw_private_fallback",
            "enable_daily_outfit_photo",
            "natural_language_photo_generation_mode",
            "enable_natural_language_photo_generation",
            "enable_user_requested_photo_generation",
            "enable_local_photo_load_guard",
            "photo_generation_style",
        ):
            self.assertIn(f'"{key}"', self.script)

    def test_setting_backed_photo_toggles_save_as_settings(self) -> None:
        photo_card = self.script.split('key: "enable_photo_text_action"', 1)[1].split(
            'key: "enable_news_integration"', 1
        )[0]
        for key in (
            "enable_group_nsfw_private_fallback",
            "enable_daily_outfit_photo",
            "enable_creative_cover_generation",
            "enable_natural_language_photo_generation",
            "enable_user_requested_photo_generation",
            "enable_local_photo_load_guard",
        ):
            self.assertIn(f'key: "{key}", type: "bool", kind: "setting"', photo_card)

    def test_feature_detail_does_not_collect_or_reset_image_endpoint_editor(self) -> None:
        payload_function = self.script.split("function collectFeatureDetailPayload", 1)[1].split(
            "function featureDependencyLines", 1
        )[0]
        self.assertNotIn("collectPhotoApiEndpointEditor", payload_function)
        self.assertNotIn('featureKey === "enable_photo_text_action"', payload_function)

    def test_troubleshooting_points_online_model_fields_to_model_page(self) -> None:
        handlers = (ROOT / "command_handlers.py").read_text(encoding="utf-8")
        self.assertIn(
            '"EXTERNAL_IMAGE_API_BASE_URL": {"label": "在线图片 API 地址", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"}',
            handlers,
        )

    def test_private_image_timeout_inputs_match_backend_range(self) -> None:
        for key in (
            "private_image_vision_wait_seconds",
            "private_image_provider_timeout_seconds",
        ):
            with self.subTest(key=key):
                self.assertIn(
                    f'{key}: {{ type: "number", min: 0, max: 600, step: 1 }}',
                    self.script,
                )
        self.assertIn(
            'context_image_caption_timeout_seconds: { type: "number", min: 0, max: 600, step: 0.5 }',
            self.script,
        )


if __name__ == "__main__":
    unittest.main()
