"""Synthetic host connection fixtures; no credentials or network from the real host."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.service_balance import configured_balance_port
from astrbot_host.service_balance_provider import balance_provider_choices, provider_balance_settings
from companion_v2.proactive.balance_events import BalanceObservation
from tests.test_service_balance_script import SCRIPT, settings


def context():
    sources = [dict(id="source-a", provider_type="chat_completion", enable=True,
                    api_base="https://a.example.test/v1", key=["host-key-a"], proxy="http://u:host-proxy-secret@localhost:1080"),
               dict(id="source-b", provider_type="chat_completion", enable=True,
                    api_base="https://b.example.test", key=["", "host-key-b"], proxy="")]
    models = [dict(id="model-a", provider_source_id="source-a"),
              dict(id="model-b", provider_source_id="source-b")]
    config = dict(agent_runner=dict(runner_type="local", config=dict(model=dict(provider_id="model-a"))))
    return NS(provider_manager=NS(provider_sources_config=sources, providers_config=models),
              get_config=lambda:config)


class HostBalanceBindingTests(unittest.TestCase):
    def setUp(self):
        self.context = context()
        self.settings = settings(advanced_from_astrbot=True, advanced_provider_id="",
                                 advanced_proxy_url="http://manual:manual-secret@localhost:1081")

    def test_default_selects_the_models_source_not_the_first_source(self):
        self.context.get_config()["agent_runner"]["config"]["model"]["provider_id"] = "model-b"
        effective = provider_balance_settings(self.settings, self.context)
        self.assertEqual(effective["advanced_api_key"], "host-key-b")
        self.assertEqual(effective["advanced_base_url"], "https://b.example.test")
        self.assertEqual(effective["advanced_proxy_url"], "")

    def test_explicit_source_overrides_default_and_does_not_mutate_settings(self):
        before = deepcopy(self.settings)
        self.settings["advanced_provider_id"] = "source-b"
        effective = provider_balance_settings(self.settings, self.context)
        self.assertEqual(effective["advanced_api_key"], "host-key-b")
        self.assertEqual(self.settings["advanced_base_url"], before["advanced_base_url"])
        self.assertEqual(self.settings["advanced_api_key"], before["advanced_api_key"])
        self.assertEqual(self.settings["advanced_proxy_url"], before["advanced_proxy_url"])

    def test_choices_contain_only_ids_labels_enabled_and_default(self):
        data = balance_provider_choices(self.context)
        self.assertEqual(data["astrbot_default_provider_id"], "source-a")
        self.assertEqual(len(data["astrbot_providers"]), 2)
        text = json.dumps(data)
        for secret in ("host-key-a", "host-key-b", "host-proxy-secret", "a.example.test"):
            self.assertNotIn(secret, text)

    def test_missing_default_deleted_disabled_and_empty_key_fail_closed(self):
        for update in ("missing-default", "deleted", "disabled", "key", "url", "proxy"):
            ctx = context()
            if update == "missing-default":
                ctx.get_config()["agent_runner"]["config"]["model"]["provider_id"] = "missing"
            elif update == "deleted":
                ctx.provider_manager.provider_sources_config.pop(0)
            elif update == "disabled":
                ctx.provider_manager.provider_sources_config[0]["enable"] = False
            else:
                ctx.provider_manager.provider_sources_config[0][{"key":"key", "url":"api_base", "proxy":"proxy"}[update]] = [] if update == "key" else None
            with self.subTest(update=update), self.assertRaises(ValueError):
                configured_balance_port({"service_balance":self.settings}, context=ctx)

    def test_key_environment_reference_and_first_nonempty_key(self):
        src = self.context.provider_manager.provider_sources_config[0]
        src["key"] = ["$NOT_DEFINED_TEST_BALANCE", "${TEST_BALANCE_KEY}", "later-key"]
        with patch.dict("os.environ", {"TEST_BALANCE_KEY":"env-test-key"}):
            result = provider_balance_settings(self.settings, self.context)
        self.assertEqual(result["advanced_api_key"], "env-test-key")

    def test_disabled_binding_uses_manual_settings_without_context(self):
        self.settings["advanced_from_astrbot"] = False
        port = configured_balance_port({"service_balance":self.settings})
        self.assertEqual(port.api_key, "synthetic-token")
        self.assertEqual(port.proxy_url, self.settings["advanced_proxy_url"])

    def test_legacy_standalone_models_are_available_without_sources(self):
        manager = self.context.provider_manager
        manager.provider_sources_config = []
        manager.providers_config = [dict(id="model-a", key="standalone-key", api_base="https://legacy.example.test")]
        result = provider_balance_settings(self.settings, self.context)
        self.assertEqual(result["advanced_api_key"], "standalone-key")

    def test_invalid_proxy_and_http_base_never_use_manual_fallback(self):
        src = self.context.provider_manager.provider_sources_config[0]
        for field, value in (("proxy", "invalid-proxy"), ("api_base", "http://a.example.test")):
            old = src[field]
            src[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                configured_balance_port({"service_balance":self.settings}, context=self.context)
            src[field] = old

    def test_fingerprint_tracks_key_proxy_url_and_default_changes(self):
        port = configured_balance_port({"service_balance":self.settings}, context=self.context)
        first = port.source_fingerprint
        src = self.context.provider_manager.provider_sources_config[0]
        for field, value in (("key", ["rotated-key"]), ("proxy", ""), ("api_base", "https://new.example.test")):
            src[field] = value
            self.assertNotEqual(first, port.source_fingerprint)
            first = port.source_fingerprint
        self.context.get_config()["agent_runner"]["config"]["model"]["provider_id"] = "model-b"
        self.assertNotEqual(first, port.source_fingerprint)

    def test_request_resolves_latest_key_and_rejects_inflight_changes(self):
        port = configured_balance_port({"service_balance":self.settings}, context=self.context)
        src = self.context.provider_manager.provider_sources_config[0]
        src["key"] = ["fresh-key"]
        captured = []
        async def fetch(script_port):
            captured.append(script_port.api_key)
            src["key"] = ["changed-during-query"]
            return object()
        with patch("astrbot_host.service_balance_script.ScriptServiceBalancePort.fetch", fetch):
            with self.assertRaises(ValueError):
                asyncio.run(port.fetch())
        self.assertEqual(captured, ["fresh-key"])

    def test_unknown_source_never_falls_back_to_default(self):
        self.settings["advanced_provider_id"] = "removed-source"
        with self.assertRaises(ValueError):
            provider_balance_settings(self.settings, self.context)

    def test_explicit_selection_does_not_follow_default_and_filters_non_chat_sources(self):
        self.settings["advanced_provider_id"] = "source-a"
        self.context.get_config()["agent_runner"]["config"]["model"]["provider_id"] = "model-b"
        self.context.provider_manager.provider_sources_config.append(
            dict(id="voice-source", provider_type="text_to_speech", api_base="https://voice.example.test", key=["voice-key"]))
        self.assertEqual(provider_balance_settings(self.settings,self.context)["advanced_api_key"],"host-key-a")
        self.assertNotIn("voice-source", json.dumps(balance_provider_choices(self.context)))

    def test_host_connection_change_invalidates_keyword_cache_before_it_is_reused(self):
        from astrbot_host.service_balance import balance_keyword_material
        from companion_v2.foundation.refs import ScopeKind
        from unittest.mock import Mock
        port = configured_balance_port({"service_balance":self.settings}, context=self.context)
        invalidate = Mock()
        app = NS(balance_port=port, runtime=object(), keyword_materials=NS(invalidate=invalidate))
        with patch("companion_v2.expression.configuration.source_subject_allowed_for_context", return_value=True):
            spec = balance_keyword_material(app)
            private = NS(scope=NS(kind=ScopeKind.PRIVATE))
            self.assertTrue(spec.available(private))
            invalidate.reset_mock()
            self.context.provider_manager.provider_sources_config[0]["key"] = ["rotated-key"]
            self.assertTrue(spec.available(private))
            invalidate.assert_called_once_with("service.balance")
            invalidate.reset_mock()
            self.assertTrue(spec.available(private))
            invalidate.assert_not_called()
