# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TtsLanguageModelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        cls.api = (ROOT / "page_api.py").read_text(encoding="utf-8")

    def test_model_page_has_dedicated_tts_section(self) -> None:
        self.assertIn('data-models-section="tts"', self.html)
        self.assertIn('id="modelsTtsPane"', self.html)
        self.assertIn('data-tts-provider-save', self.script)
        self.assertNotIn('id="saveTtsModelsBtn"', self.html)

    def test_tts_provider_list_is_loaded_separately_from_llm_providers(self) -> None:
        self.assertIn("state.availableTtsProviders = availableProviders.tts_items || [];", self.script)
        self.assertIn('"tts_items": tts_items', self.api)
        self.assertIn("get_all_tts_providers", self.api)

    def test_all_languages_are_saved_as_runtime_settings(self) -> None:
        for key in ("tts_provider_id_zh", "tts_provider_id_ja", "tts_provider_id_en"):
            self.assertIn(key, self.script)
            self.assertIn(key, self.api)
        self.assertIn('postJson("/settings/update", { settings: { ...ttsStrategyValues(), ...savedRouteValues } })', self.script)

    def test_tts_drafts_do_not_trap_main_navigation(self) -> None:
        self.assertIn("function discardUnsavedTtsProviderChanges()", self.script)
        self.assertIn("if (ttsDirty) discardUnsavedTtsProviderChanges();", self.script)
        self.assertIn("TTS 还有未保存的 Provider、语种路由或语音策略", self.script)

    def test_astrbot_provider_management_is_complete(self) -> None:
        for route in ("/tts/providers", "/tts/provider/create", "/tts/provider/clone", "/tts/provider/update", "/tts/provider/test"):
            self.assertIn(route, self.script)
            self.assertIn(route, self.api)
        for field_type in ('type === "bool"', 'type === "object"', 'type === "list"', 'field.secret ? "password"'):
            self.assertIn(field_type, self.script)
        self.assertIn('label: "Fish Audio 回退模型"', self.script)
        self.assertIn("options.length", self.script)
        self.assertIn('"s2.1-pro-free", "S2.1 Pro Free"', self.script)

    def test_provider_configuration_follows_selected_language(self) -> None:
        self.assertIn('data-tts-config-language=', self.script)
        self.assertIn('const activeProviderId = activeRouteMeta ? String(ttsProviderValues()[activeRouteMeta.key]', self.script)
        self.assertIn('创建并用于${escapeHtml(meta.label)}', self.script)
        self.assertIn('保存${escapeHtml(meta.label)}配置', self.script)
        self.assertIn('postJson("/settings/update", { settings: { ...ttsStrategyValues(), ...savedRouteValues } })', self.script)

    def test_shared_provider_is_copied_before_language_specific_save(self) -> None:
        self.assertIn("function ttsProviderConfigDraftKey(providerId, language", self.script)
        self.assertIn("function ttsProviderSharedLanguages(providerId, language", self.script)
        self.assertIn("function ttsSharedProviderGroups(values", self.script)
        self.assertIn('postJson("/tts/provider/clone", {', self.script)
        self.assertIn("三个语种互不覆盖", self.script)
        self.assertIn("保存并拆分独立配置", self.script)
        self.assertIn("...savedRouteValues", self.script)

    def test_tts_language_configurator_is_mobile_accessible(self) -> None:
        self.assertIn("tts-language-configurator", self.script)
        self.assertIn(".tts-language-provider-bar", self.css)
        mobile = self.css.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".tts-language-provider-bar", mobile)
        self.assertIn("grid-template-columns: 1fr;", mobile)

    def test_tts_section_is_keyboard_and_mobile_accessible(self) -> None:
        self.assertIn('["ArrowLeft", "ArrowRight", "Home", "End"]', self.script)
        self.assertIn("button.tabIndex = active ? 0 : -1;", self.script)
        self.assertIn(".tts-model-pane[hidden]", self.css)
        self.assertIn('aria-controls="modelsTtsPane"', self.html)
        self.assertIn('role="tabpanel" aria-labelledby="modelsTtsTab"', self.html)
        mobile = self.css.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".tts-language-provider-bar", mobile)
        self.assertIn(".tts-provider-field-grid", mobile)
        self.assertIn("grid-template-columns: 1fr;", mobile)

    def test_model_subnav_has_clear_segmented_states(self) -> None:
        subnav = self.css.split(".models-subnav {", 1)[1].split(".image-model-pane", 1)[0]
        self.assertIn('class="section-head models-page-head"', self.html)
        for index in ("01", "02", "03"):
            self.assertIn(f'data-index="{index}"', self.html)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", subnav)
        self.assertIn("min-height: 48px;", subnav)
        self.assertIn("content: attr(data-index);", subnav)
        self.assertIn("background: color-mix(in srgb, var(--accent) 70%, var(--ink));", subnav)
        self.assertIn("inset 0 -3px 0 var(--red);", subnav)
        self.assertIn('data-active-section="tts"', subnav)
        self.assertIn("models-pane-enter-next", subnav)
        self.assertIn("prefers-reduced-motion: reduce", subnav)
        self.assertIn("tablist.dataset.activeSection = section;", self.script)
        self.assertIn('panel.dataset.modelsDirection = nextIndex < previousIndex ? "previous" : "next";', self.script)
        mobile = self.css.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".models-subnav", mobile)
        self.assertIn("width: 100%;", mobile)
        self.assertIn("white-space: nowrap;", mobile)

    def test_assets_use_tts_language_configurator_cache_version(self) -> None:
        self.assertIn('./app.css?v=20260730-panel-asset-guard-v1', self.html)
        self.assertIn('./app.js?v=20260730-panel-asset-guard-v1', self.html)

    def test_opening_model_pages_resets_the_workspace_scroll_position(self) -> None:
        self.assertIn("function resetActiveWorkspaceScroll()", self.script)
        self.assertIn('const layout = document.querySelector(".layout");', self.script)
        self.assertIn('window.scrollTo({ top, behavior: "auto" });', self.script)
        self.assertGreaterEqual(self.script.count("resetActiveWorkspaceScroll();"), 2)


if __name__ == "__main__":
    unittest.main()
