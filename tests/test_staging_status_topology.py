"""Single-target topology follows confirmed identity without activating the host."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_astrbot_staging_host import WebContext, host_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.test_management_access import NOW
from astrbot_host.binding_store import PendingBinding, write_binding
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.identity_derivation import derive_bot_id, derive_role_id
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes


class StagingStatusTopologyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-topology-")
        self.root = Path(self.temporary.name)
        self.native = WebContext()
        self.config = host_config()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.controller = self.make_controller()

    def make_controller(self):
        return AstrBotStagingController(self.native, self.config, self.root,
                                       native_types=self.types, now=lambda: NOW)

    async def asyncTearDown(self):
        await self.controller.terminate()
        self.temporary.cleanup()

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_no_external_io(self):
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_status_route_projects_the_configured_target_without_initializing(self):
        self.controller.register_routes()
        handler = next(handler for route, handler, _, _ in self.native.registered_web_apis
                       if route.endswith("/staging/status"))
        before = self.files()
        with patch("astrbot_host.web_api.request", NS(username="admin", query={})), \
             patch("astrbot_host.web_api.display_names",
                   return_value=NS(targets=AsyncMock(side_effect=lambda rows: rows))):
            response = await handler()
        self.assertEqual(response["status"], "ok")
        status = response["data"]
        self.assertEqual(status["mode"], "single")
        self.assertEqual(status["targets"], [{
            "target_id": "preview-1", "scope_kind": "private",
            "platform_id": "qq-main", "account_id": "100", "participant_id": "200",
            "bot_id": derive_bot_id("100"),
            "roles": [{"role_id": derive_role_id("100", "original"), "persona_ids": ["original"]}],
        }])
        self.assertFalse(status["configured"])
        self.assertIsNone(self.controller.runtime)
        self.assertEqual(self.files(), before)
        self.assertNotIn(self.config["model"]["task_prompt"], json.dumps(status, ensure_ascii=False))
        self.assert_no_external_io()

    async def test_status_route_exposes_document_parser_availability(self):
        self.controller.register_routes()
        handler = next(handler for route, handler, _, _ in self.native.registered_web_apis
                       if route.endswith("/staging/status"))
        expected = {".pdf": True, ".docx": False, ".xlsx": True, ".pptx": False}
        with patch("astrbot_host.controller.parser_availability", return_value=expected), \
             patch("astrbot_host.web_api.request", NS(username="admin", query={})):
            response = await handler()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["parser_availability"], expected)

    async def test_unbound_and_pending_targets_stay_empty_until_dashboard_confirmation(self):
        self.config["target"] = {}
        self.controller.refresh_settings()
        self.assertEqual(self.controller.status()["targets"], [])
        write_binding(self.controller.binding_root, PendingBinding("qq-main", "100", "200", "original"))
        before = self.files()
        self.assertEqual(self.controller.status()["targets"], [])
        self.assertEqual(self.files(), before)
        self.assertTrue(self.controller.confirm_binding()["confirmed"])
        before = self.files()
        target = self.controller.status()["targets"][0]
        self.assertEqual((target["account_id"], target["participant_id"]), ("100", "200"))
        self.assertEqual(target["roles"][0]["persona_ids"], ["original"])
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.controller.runtime)
        self.assert_no_external_io()

    async def test_pinned_mapping_survives_stop_and_reload_and_ignores_stale_candidates(self):
        (await self.controller.initialize_profile()).unwrap()
        expected = self.controller.status()["targets"]
        (await self.controller.start()).unwrap()
        self.assertEqual(self.controller.status()["targets"], expected)
        (await self.controller.stop()).unwrap()
        self.assertEqual(self.controller.status()["targets"], expected)
        await self.controller.terminate()
        self.assertEqual(self.controller.status()["targets"], expected)
        self.assertEqual(self.native.registered_web_apis, [])

        self.config["target"].update(account_id="101", participant_id="201", persona_id="other")
        self.controller = self.make_controller()
        write_binding(self.controller.binding_root,
                      PendingBinding("qq-main", "102", "202", "candidate", confirmed=True))
        before = self.files()
        self.assertEqual(self.controller.status()["targets"], expected)
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.controller.runtime)
        (await self.controller.load_existing()).unwrap()
        self.assertEqual(self.controller.status()["targets"], expected)
        self.assertFalse(self.controller.status()["accepting"])
        self.assert_no_external_io()

    async def test_unreadable_pin_reports_an_error_without_drawing_a_fallback_target(self):
        self.controller.intent_path.parent.mkdir(parents=True)
        self.controller.intent_path.write_text("{broken pin\n", encoding="utf-8", newline="\n")
        before = self.files()
        with patch("astrbot_host.web_api.request", NS(username="admin", query={})):
            status = await self.controller.web_api.api_status()
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["data"]["settings_valid"])
        self.assertEqual(status["data"]["settings_error"], "staging_intent_invalid__invalid")
        self.assertEqual(status["data"]["targets"], [])
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.controller.last_failure)
        self.assert_no_external_io()

    async def test_disabled_or_invalid_settings_are_readable_and_no_persona_is_not_invented(self):
        self.config["staging"]["enabled"] = False
        self.config["target"]["persona_id"] = ""
        self.controller.refresh_settings()
        status = self.controller.status()
        self.assertFalse(status["enabled"])
        self.assertTrue(status["settings_valid"])
        self.assertEqual(status["targets"][0]["roles"], [{"role_id": "role-100", "persona_ids": []}])
        self.config["staging"]["enabled"] = "invalid"
        self.controller.refresh_settings()
        status = self.controller.status()
        self.assertFalse(status["settings_valid"])
        self.assertEqual(status["settings_error"], "staging_settings_invalid")
        self.assertEqual(status["targets"], [])
        self.assert_no_external_io()


if __name__ == "__main__":
    unittest.main()
