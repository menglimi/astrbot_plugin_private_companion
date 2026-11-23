"""Regression tests for full-site search catalog API, fallback provider editing, and 100% coverage gate."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - Establish isolation before SDK import.
from tests import test_astrbot_staging_host as support
from tests.group_host_support import group_config
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.page_api import PREFIX
from astrbot_host.search_catalog import build_search_catalog
from tools.audit_search_coverage import run_audit


class SearchCatalogUnitTests(unittest.TestCase):
    def test_catalog_covers_all_schemas_without_leaking_secrets(self):
        fake_controller = NS(
            config={
                "performance": {"enabled": True},
                "storage": {"auto_backup_enabled": True, "path": "D:/secret/internal/path"},
                "model": {
                    "provider_id": "secret-provider-id",
                    "fallback_provider_id": "secret-fallback-id",
                    "fallback_on_refusal": True,
                },
                "news": {"feed_url": "https://private-token.example/feed?key=SUPERSECRET"},
            }
        )
        catalog = build_search_catalog(fake_controller)
        self.assertEqual(catalog["version"], 1)
        self.assertGreaterEqual(len(catalog["model_tasks"]), 25)
        self.assertEqual(len(catalog["plugin_config"]), 107)

        by_path = {item["path"]: item for item in catalog["plugin_config"]}
        self.assertIs(by_path["performance.enabled"]["current_value"], True)
        self.assertIs(by_path["storage.auto_backup_enabled"]["current_value"], True)
        self.assertIs(by_path["model.fallback_on_refusal"]["current_value"], True)
        self.assertIs(by_path["model.streaming"]["current_value"], False)

        # Non-boolean config entries must NEVER expose live string/secret values.
        serialized = json.dumps(catalog, ensure_ascii=False)
        self.assertNotIn("SUPERSECRET", serialized)
        self.assertNotIn("secret-provider-id", serialized)
        self.assertNotIn("D:/secret/internal/path", serialized)
        for item in catalog["plugin_config"]:
            if item["type"] != "bool":
                self.assertNotIn("current_value", item)

        ext_counts = {ext["id"]: ext["field_count"] for ext in catalog["native_extensions"]}
        self.assertEqual(ext_counts, {"game": 61, "live": 157, "reality": 50, "together": 41})

    def test_automated_search_coverage_audit_gate_passes(self):
        result = run_audit()
        self.assertTrue(result["ok"], f"Search coverage audit failed: {result['errors']}")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["conf_schema_visible_count"], 107)
        self.assertEqual(result["models_page_task_count"], 25)
        self.assertEqual(result["frontend"]["native_extension_fields"], 309)
        self.assertEqual(result["frontend"]["learning_subtabs_count"], 8)


class SearchCatalogRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-search-catalog-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes(
            "4.27.2", support.FakeEvent, support.FakeProvider, NS, NS, support.FakeReverseApi
        )
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), Path(temporary.name),
            native_types=types, now=lambda: support.NOW,
        )
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.handler = next(
            handler for path, handler, _, _ in self.native.registered_web_apis
            if path == PREFIX + "/search/catalog"
        )

    async def _request(self, *, username="admin", query=""):
        req = Request({
            "type": "http", "method": "GET", "path": PREFIX + "/search/catalog",
            "headers": [], "query_string": query.encode(),
        })
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.handler()

    async def test_search_catalog_route_enforces_auth_and_returns_catalog(self):
        for bad_user in (None, "", "api_key:synthetic"):
            resp = await self._request(username=bad_user)
            self.assertEqual(resp["data"]["code"], "dashboard_identity_required")

        bad_query = await self._request(query="unexpected=1")
        self.assertEqual(bad_query["data"]["code"], "page_request_invalid")

        ok = await self._request()
        self.assertEqual(ok["status"], "ok")
        data = ok["data"]
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["plugin_config"]), 107)
        self.assertEqual(len(data["native_extensions"]), 4)
        self.assertGreaterEqual(len(data["model_tasks"]), 25)


class FallbackProviderSaveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = group_config()
        self.config.setdefault("model", {})["provider_id"] = "p1"
        self.providers = [
            NS(provider_config={"id": "p1", "name": "Primary"}, meta=lambda: NS(id="p1", provider_type="chat_completion")),
            NS(provider_config={"id": "p2", "name": "Backup"}, meta=lambda: NS(id="p2", provider_type="chat_completion")),
            NS(provider_config={"id": "p3", "name": "Third"}, meta=lambda: NS(id="p3", provider_type="chat_completion")),
        ]
        self.context = NS(
            get_all_providers=lambda: list(self.providers),
            get_provider_by_id=lambda pid: next((p for p in self.providers if p.meta().id == pid), None),
        )

        def save_plugin_config(candidate):
            self.config.clear()
            self.config.update(candidate)

        self.controller = NS(
            config=self.config,
            context=self.context,
            settings_error=None,
            save_plugin_config=save_plugin_config,
            initialize_profile=AsyncMock(return_value=NS(failure=None)),
            status=lambda: {"state": "running", "accepting": True},
        )
        self.api = AccountSessionWebAPI(self.controller)
        self.api._discovered = AsyncMock(return_value={"accounts": [], "personas": [], "targets": []})

    async def _post_model(self, body: dict):
        req = NS(username="admin", query={}, body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", req):
            return await self.api.save_model_provider()

    async def test_save_clear_preserve_and_validate_fallback_provider_id(self):
        # 1. Save both primary and fallback
        saved = await self._post_model({"provider_id": "p1", "fallback_provider_id": "p2"})
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(self.config["model"]["provider_id"], "p1")
        self.assertEqual(self.config["model"]["fallback_provider_id"], "p2")
        self.assertEqual(saved["data"]["model"]["fallback_provider_id"], "p2")

        # 2. Omitting fallback_provider_id preserves existing fallback
        preserved = await self._post_model({"provider_id": "p3"})
        self.assertEqual(preserved["status"], "ok", preserved)
        self.assertEqual(self.config["model"]["provider_id"], "p3")
        self.assertEqual(self.config["model"]["fallback_provider_id"], "p2")

        # 3. Rejecting fallback == primary
        before = deepcopy(self.config)
        dup = await self._post_model({"provider_id": "p1", "fallback_provider_id": "p1"})
        self.assertEqual(dup["data"]["code"], "model_fallback_duplicate")
        self.assertEqual(self.config, before)

        # 4. Rejecting unknown fallback provider
        unknown = await self._post_model({"provider_id": "p1", "fallback_provider_id": "missing-provider"})
        self.assertEqual(unknown["data"]["code"], "host_provider_unavailable")
        self.assertEqual(self.config, before)

        # 5. Clearing fallback with empty string
        cleared = await self._post_model({"provider_id": "p1", "fallback_provider_id": ""})
        self.assertEqual(cleared["status"], "ok", cleared)
        self.assertEqual(self.config["model"]["fallback_provider_id"], "")
        self.assertEqual(cleared["data"]["model"]["fallback_provider_id"], "")

        # 6. Auto-clears colliding fallback when primary switches onto previous fallback
        await self._post_model({"provider_id": "p1", "fallback_provider_id": "p2"})
        collided = await self._post_model({"provider_id": "p2"})
        self.assertEqual(collided["status"], "ok", collided)
        self.assertEqual(self.config["model"]["provider_id"], "p2")
        self.assertEqual(self.config["model"]["fallback_provider_id"], "")


if __name__ == "__main__":
    unittest.main()
