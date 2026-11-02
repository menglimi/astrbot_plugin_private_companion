import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v2.proactive.search_settings import SearchSettingsError, SearchSettingsStore


class SearchSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name

    def test_settings_are_global_revisioned_and_never_store_credentials(self):
        store = SearchSettingsStore(self.root)
        initial = store.read()
        saved = store.update(initial["revision"], {"deepseek_provider_id": "deepseek_main"})

        self.assertEqual(saved["deepseek_provider_id"], "deepseek_main")
        self.assertEqual(store.selected_provider_id(), "deepseek_main")
        self.assertNotIn("secret", store.path.read_text(encoding="utf-8"))
        with self.assertRaises(SearchSettingsError) as error:
            store.update(initial["revision"], {"deepseek_provider_id": ""})
        self.assertEqual(error.exception.code, "search_config_conflict")

    def test_model_search_projection_lists_only_verified_deepseek_providers(self):
        from astrbot_host.model_api import ModelWebAPI

        provider = SimpleNamespace(id="deepseek_main", provider_config={"api_base": "https://api.deepseek.com/v1"},
                                   get_current_key=lambda: "fixture-secret",
                                   meta=lambda: SimpleNamespace(name="DeepSeek"))
        unrelated = SimpleNamespace(id="proxy", provider_config={"api_base": "https://proxy.example/v1"},
                                    get_current_key=lambda: "must-not-leak")
        context = SimpleNamespace(get_all_providers=lambda: [provider, unrelated])
        controller = SimpleNamespace(data_root=self.root, context=context)
        store = SearchSettingsStore(self.root)
        store.update(store.read()["revision"], {"deepseek_provider_id": "deepseek_main"})

        status = ModelWebAPI(controller)._search_status()

        self.assertEqual(status["deepseek_provider_id"], "deepseek_main")
        self.assertTrue(status["provider_available"])
        self.assertEqual(status["providers"], [{"id": "deepseek_main", "name": "DeepSeek"}])
        self.assertNotIn("fixture-secret", json.dumps(status))
        self.assertNotIn("must-not-leak", json.dumps(status))


    def test_source_priority_is_validated_and_saved_without_losing_provider(self):
        store = SearchSettingsStore(self.root)
        initial = store.read()
        self.assertEqual(initial["source_priority"], "auto")
        saved = store.update(initial["revision"], {"deepseek_provider_id": "deepseek_main",
                                                   "source_priority": "astrbot"})
        self.assertEqual(saved["source_priority"], "astrbot")
        self.assertEqual(saved["deepseek_provider_id"], "deepseek_main")
        self.assertEqual(store.source_priority(), "astrbot")
        with self.assertRaises(SearchSettingsError) as error:
            store.update(saved["revision"], {"source_priority": "network"})
        self.assertEqual(error.exception.code, "search_config_invalid")
        # 只改优先级时保留原有 Provider 选择。
        kept = store.update(saved["revision"], {"source_priority": "deepseek"})
        self.assertEqual(kept["deepseek_provider_id"], "deepseek_main")
        self.assertEqual(kept["source_priority"], "deepseek")
if __name__ == "__main__":
    unittest.main()