from types import SimpleNamespace
import unittest

from astrbot_host.bootstrap_provider import resolve_bootstrap_provider, text_provider_catalog


class Provider:
    def __init__(self, identifier, name):
        self.provider_config = {"id": identifier}
        self._name = name

    def meta(self):
        return SimpleNamespace(id=self.provider_config["id"], name=self._name)


class Context:
    def __init__(self, providers, active=None):
        self.providers = providers
        self.active = active

    def get_all_providers(self):
        return list(self.providers)

    def get_using_provider(self):
        return self.active

    def get_provider_by_id(self, identifier):
        return next((item for item in self.providers if item.provider_config["id"] == identifier), None)


class BootstrapProviderTests(unittest.TestCase):
    def test_configured_provider_wins(self):
        context = Context([Provider("a", "A"), Provider("b", "B")])
        result = resolve_bootstrap_provider(context, Provider, "a")
        self.assertEqual((result.provider_id, result.source), ("a", "configured"))

    def test_active_astrbot_provider_is_automatic_default(self):
        active = Provider("b", "B")
        context = Context([Provider("a", "A"), active], active=active)
        result = resolve_bootstrap_provider(context, Provider)
        self.assertEqual((result.provider_id, result.source), ("b", "auto"))

    def test_first_supported_provider_is_deterministic_fallback(self):
        context = Context([Provider("a", "A"), Provider("b", "B")])
        result = resolve_bootstrap_provider(context, Provider)
        self.assertEqual((result.provider_id, result.source), ("a", "auto"))

    def test_missing_saved_provider_falls_back_to_active(self):
        active = Provider("b", "B")
        context = Context([active], active=active)
        result = resolve_bootstrap_provider(context, Provider, "removed")
        self.assertEqual((result.provider_id, result.source), ("b", "fallback"))

    def test_catalog_exposes_provider_names(self):
        context = Context([Provider("a", "友好模型")])
        self.assertEqual(text_provider_catalog(context, Provider), [{"id": "a", "name": "友好模型"}])

    def test_unavailable_when_no_supported_provider_exists(self):
        result = resolve_bootstrap_provider(Context([]), Provider, "")
        self.assertEqual((result.provider_id, result.source), ("", "unavailable"))



if __name__ == "__main__":
    unittest.main()
