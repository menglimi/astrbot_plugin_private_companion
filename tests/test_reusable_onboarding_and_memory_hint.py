"""Tests for reusable onboarding wizard APIs and MemoryCompanion status/hint preferences."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.binding_store import PendingBinding, write_binding
from astrbot_host.bootstrap_provider import active_chat_provider_info, text_provider_catalog
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.page_api import DISPLAY_PREFERENCES_FILE, PageWebAPI
from astrbot_host.setup_api import SetupWebAPI
from astrbot_host.setup_state import SCHEMA_VERSION, SetupStateStore
from companion_v2.adapters.astrbot.memory_owner import (
    MEMORY_OWNER_DISPLAY_NAME,
    MEMORY_OWNER_MODULE_NAMES,
    MEMORY_OWNER_PLUGIN_ID,
    MEMORY_OWNER_REPO_URL,
    read_memory_companion_status,
    read_memory_ownership,
)
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.capabilities.ownership import (
    MemoryOwnership,
    REASON_EXTERNAL_ACTIVE,
    RENDER_EXTERNAL,
)
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi, completion
from tests.test_astrbot_staging_host import WebContext, host_config
from tests.test_management_access import NOW


PLUGIN_NAME = "astrbot_plugin_private_companion"


class _FakeStar:
    def __init__(self, module=None, *, activated=True, name="astrbot_plugin_memory_companion"):
        self.name = name
        self.module = module
        self.module_path = "data.plugins.astrbot_plugin_memory_companion.main"
        self.root_dir_name = "astrbot_plugin_memory_companion"
        self.activated = activated


class _FakeRegistryContext:
    def __init__(self, stars=()):
        self._stars = list(stars)

    def get_all_stars(self):
        return list(self._stars)


class _FakeBridge:
    def __init__(self, active=True):
        self._active = active

    def bridge_lifecycle_status(self):
        return {"active": self._active}


class _FakeCompanionModule:
    def __init__(self, handle):
        self._handle = handle

    def get_memory_companion_bridge(self):
        return self._handle


class MemoryCompanionDetectionTests(unittest.TestCase):
    def setUp(self):
        self.original = {name: sys.modules.get(name) for name in MEMORY_OWNER_MODULE_NAMES}
        for name in MEMORY_OWNER_MODULE_NAMES:
            sys.modules.pop(name, None)
        self.addCleanup(self.restore)

    def restore(self):
        for name, mod in self.original.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_fallback_module_names_include_memory_companion_package(self):
        self.assertIn("data.plugins.astrbot_plugin_memory_companion.main", MEMORY_OWNER_MODULE_NAMES)
        self.assertIn("astrbot_plugin_memory_companion.main", MEMORY_OWNER_MODULE_NAMES)
        sys.modules["data.plugins.astrbot_plugin_memory_companion.main"] = _FakeCompanionModule(_FakeBridge(True))
        ownership = read_memory_ownership(None)
        self.assertEqual(ownership.owner, RENDER_EXTERNAL)
        self.assertEqual(ownership.provider, MEMORY_OWNER_PLUGIN_ID)
        status = read_memory_companion_status(None)
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["display_name"], MEMORY_OWNER_DISPLAY_NAME)
        self.assertEqual(status["repo_url"], MEMORY_OWNER_REPO_URL)

    def test_five_state_classification_covers_missing_disabled_bridge_connected_and_other(self):
        self.assertEqual(read_memory_companion_status(_FakeRegistryContext())["state"], "missing")

        disabled_ctx = _FakeRegistryContext([_FakeStar(_FakeCompanionModule(_FakeBridge(True)), activated=False)])
        self.assertEqual(read_memory_companion_status(disabled_ctx)["state"], "disabled")

        legacy_disabled = _FakeStar(_FakeCompanionModule(_FakeBridge(True)), activated=False, name="astrbot_plugin_remember_you")
        legacy_disabled.module_path = "data.plugins.astrbot_plugin_remember_you.main"
        legacy_disabled.root_dir_name = "astrbot_plugin_remember_you"
        self.assertEqual(read_memory_companion_status(_FakeRegistryContext([legacy_disabled]))["state"], "disabled")

        bridge_off_ctx = _FakeRegistryContext([_FakeStar(_FakeCompanionModule(None), activated=True)])
        self.assertEqual(read_memory_companion_status(bridge_off_ctx)["state"], "bridge_disabled")

        connected_ctx = _FakeRegistryContext([_FakeStar(_FakeCompanionModule(_FakeBridge(True)), activated=True)])
        self.assertEqual(read_memory_companion_status(connected_ctx)["state"], "connected")

        other = MemoryOwnership(
            owner=RENDER_EXTERNAL,
            reason=REASON_EXTERNAL_ACTIVE,
            provider="third_party_memory_plugin",
            display_name="其他记忆插件",
        )
        other_status = read_memory_companion_status(_FakeRegistryContext(), ownership=other)
        self.assertEqual(other_status["state"], "external_other")
        self.assertEqual(other_status["external_name"], "其他记忆插件")


class MemoryCompanionPageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="companion-mc-hints-")
        self.context = _FakeRegistryContext()
        self.controller = NS(
            data_root=Path(self.directory.name),
            _terminated=False,
            session=None,
            last_session=None,
            context=self.context,
        )
        self.api = PageWebAPI(self.controller)

    def tearDown(self):
        self.directory.cleanup()

    async def call(self, handler, body=None, *, username="administrator", plugin_name=PLUGIN_NAME, query=None):
        payload = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
        req = NS(username=username, plugin_name=plugin_name, query=query or {},
                 body=AsyncMock(return_value=payload))
        with patch("astrbot_host.page_api.request", req):
            return await handler()

    async def test_memory_companion_endpoint_works_before_profile_initialization(self):
        response = await self.call(self.api.memory_companion)
        self.assertEqual(response["status"], "ok", response)
        data = response["data"]
        self.assertEqual(data["state"], "missing")
        self.assertEqual(data["plugin_id"], MEMORY_OWNER_PLUGIN_ID)
        self.assertFalse(data["hint_dismissed"])

    async def test_hint_preferences_round_trip_and_coexist_with_display_preferences(self):
        display_prefs = {
            "appearance": {"theme": "night", "density": "compact", "radius": "standard",
                           "typeScale": "m", "motion": "low"},
            "followLight": False,
            "largeText": True,
        }
        self.assertEqual((await self.call(self.api.save_display_preferences, display_prefs))["status"], "ok")

        saved = await self.call(self.api.save_hint_preferences, {"key": "memoryCompanion", "dismissed": True})
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue(saved["data"]["dismissedHints"]["memoryCompanion"])

        # Display preferences must remain intact and identical.
        restored_display = await self.call(self.api.display_preferences)
        self.assertEqual(restored_display["data"]["preferences"], display_prefs)

        # Memory companion status reflects persisted per-user dismissal.
        mc_admin = await self.call(self.api.memory_companion, username="administrator")
        mc_other = await self.call(self.api.memory_companion, username="second-admin")
        self.assertTrue(mc_admin["data"]["hint_dismissed"])
        self.assertFalse(mc_other["data"]["hint_dismissed"])

        # Re-saving display preferences preserves dismissed hints.
        self.assertEqual((await self.call(self.api.save_display_preferences, display_prefs))["status"], "ok")
        still_dismissed = await self.call(self.api.hint_preferences)
        self.assertTrue(still_dismissed["data"]["dismissedHints"]["memoryCompanion"])

        # Restoring reminder flips back cleanly.
        restored = await self.call(self.api.save_hint_preferences, {"key": "memoryCompanion", "dismissed": False})
        self.assertFalse(restored["data"]["dismissedHints"]["memoryCompanion"])
        self.assertFalse((await self.call(self.api.memory_companion))["data"]["hint_dismissed"])

        path = Path(self.directory.name) / DISPLAY_PREFERENCES_FILE
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(raw.endswith(b"\r\n"))

    async def test_hint_preferences_rejects_invalid_identity_and_keys(self):
        denied = await self.call(self.api.save_hint_preferences,
                                 {"key": "memoryCompanion", "dismissed": True},
                                 username="api_key:bad")
        self.assertEqual(denied["status"], "error")
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")

        invalid = await self.call(self.api.save_hint_preferences, {"key": "unknownHint", "dismissed": True})
        self.assertEqual(invalid["status"], "error")
        self.assertEqual(invalid["data"]["code"], "hint_preferences_invalid")


class SetupStateV4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="companion-setup-v4-")
        self.store = SetupStateStore(self.tmp.name)
        self.store.root.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrates_v3_and_allows_explicit_finish_without_proactive_test(self):
        self.store.path.write_text(json.dumps({
            "schema_version": 3,
            "completed": ["model"],
            "skipped": ["knowledge"],
            "proactive_test": None,
            "ready": False,
        }) + "\n", encoding="utf-8")

        migrated = self.store.read()
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertFalse(migrated["ready"])
        self.assertIsNone(migrated["finished_at"])

        finished = self.store.finish(finished_by="administrator", finished_at="2026-11-24T12:00:00+00:00")
        self.assertTrue(finished["ready"])
        self.assertEqual(finished["finished_by"], "administrator")
        self.assertEqual(finished["finished_at"], "2026-11-24T12:00:00+00:00")
        reloaded = self.store.read()
        self.assertTrue(reloaded["ready"])
        self.assertEqual(reloaded["finished_by"], "administrator")


class ReusableOnboardingApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="companion-reusable-setup-")
        self.root = Path(self.tmp.name)
        self.native = WebContext()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.config = host_config(staging={"profile_id": "reusable-flow", "delivery_mode": "onebot_private"})
        self.config["staging"]["enabled"] = False
        self.config["multi_host"] = {"enabled": True, "installation_id": "reusable-inst"}

        self.text_only = FakeProvider()
        self.text_only.provider_config = {
            "id": "deepseek-text",
            "type": "openai_chat_completion",
            "model": "deepseek-chat",
            "modalities": ["text", "tool_use"],
        }
        self.text_only.model_name = "deepseek-chat"
        self.text_only.meta = lambda: NS(id="deepseek-text", name="DeepSeek Text")

        self.vision_model = FakeProvider()
        self.vision_model.provider_config = {
            "id": "gpt-vision",
            "type": "openai_chat_completion",
            "model": "gpt-4o",
            "modalities": ["text", "image", "tool_use"],
        }
        self.vision_model.model_name = "gpt-4o"
        self.vision_model.meta = lambda: NS(id="gpt-vision", name="GPT Vision")

        self.providers = {
            "native-model": self.native.provider,
            "deepseek-text": self.text_only,
            "gpt-vision": self.vision_model,
        }
        self.native.provider.meta = lambda: NS(id="native-model", name="Fixture model")
        self.native.get_all_providers = lambda: list(self.providers.values())
        self.native.get_provider_by_id = lambda pid: self.providers.get(pid)
        self.native.get_using_provider = lambda umo=None: self.text_only
        self.native.persona_manager.get_persona_v3_by_id = lambda pid: (
            self.native.persona if pid == "original" else None
        )

        self.controller = AstrBotMultiController(
            self.native, self.config, self.root, native_types=self.types, now=lambda: NOW
        )
        self.attach_config_writer()
        self.api = SetupWebAPI(self.controller)

    def attach_config_writer(self):
        def save(candidate):
            self.config.clear()
            self.config.update(candidate)
            self.controller.refresh_settings()
        self.controller.save_plugin_config = save

    async def asyncTearDown(self):
        await self.controller.terminate()
        self.tmp.cleanup()

    async def call(self, name, body=None, *, username="administrator"):
        payload = (b"" if name in {"status", "health", "model_tasks", "profile"} else
                   json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8"))
        req = NS(username=username, query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", req):
            return await getattr(self.api, name)()

    async def test_catalog_exposes_capabilities_and_effective_chat_model(self):
        catalog = text_provider_catalog(self.native, self.types.is_text_provider)
        by_id = {item["id"]: item for item in catalog}
        self.assertEqual(by_id["deepseek-text"]["model"], "deepseek-chat")
        self.assertNotIn("image", by_id["deepseek-text"]["capabilities"])
        self.assertIn("image", by_id["gpt-vision"]["capabilities"])
        active = active_chat_provider_info(self.native, self.types.is_text_provider)
        self.assertEqual(active["id"], "deepseek-text")

    async def test_select_binding_probe_nonsequential_model_health_and_finish(self):
        # 1. Direct binding selection (no QQ command needed).
        bad_wildcard = await self.call("select_binding", {
            "platform_id": "qq-main", "account_id": "100", "participant_id": "*",
        })
        self.assertEqual(bad_wildcard["status"], "error")
        self.assertEqual(bad_wildcard["data"]["code"], "setup_private_window_required")

        bound = await self.call("select_binding", {
            "platform_id": "qq-main", "account_id": "100", "participant_id": "200", "persona_id": "original",
        })
        self.assertEqual(bound["status"], "ok", bound)
        self.assertTrue(bound["data"]["confirmed"])
        self.assertTrue(bound["data"]["status"]["binding_confirmed"])

        # 2. Live probe works BEFORE runtime initialization!
        self.assertIsNone(self.controller.runtime)
        async def ok_reply(_payload, _tools):
            return completion("OK")
        self.text_only.client.state.behavior = ok_reply
        probed = await self.call("probe_model", {"provider_id": "deepseek-text"})
        self.assertEqual(probed["status"], "ok", probed)
        self.assertTrue(probed["data"]["ok"])
        self.assertEqual(probed["data"]["status"], "ok")
        self.assertEqual(probed["data"]["reply_preview"], "OK")
        self.assertGreaterEqual(probed["data"]["latency_ms"], 1)

        async def empty_reply(_payload, _tools):
            return completion("   ")
        self.text_only.client.state.behavior = empty_reply
        empty_probe = await self.call("probe_model", {"provider_id": "deepseek-text"})
        self.assertEqual(empty_probe["status"], "ok", empty_probe)
        self.assertFalse(empty_probe["data"]["ok"])
        self.assertEqual(empty_probe["data"]["status"], "failed")
        self.assertEqual(empty_probe["data"]["reply_preview"], "")
        self.assertIn("未返回可显示的文本", empty_probe["data"]["hint"])

        # Probe error hint handles auth failure cleanly.
        async def auth_fail(_payload, _tools):
            err = RuntimeError("Unauthorized")
            err.status_code = 401
            raise err
        self.text_only.client.state.behavior = auth_fail
        failed_probe = await self.call("probe_model", {"provider_id": "deepseek-text"})
        self.assertEqual(failed_probe["status"], "ok")
        self.assertFalse(failed_probe["data"]["ok"])
        self.assertIn("401", failed_probe["data"]["hint"])

        # 3. Save text-only primary + assign vision model + complete step in one call.
        saved = await self.call("model", {
            "provider_id": "deepseek-text",
            "vision_provider_id": "gpt-vision",
            "complete_step": True,
        })
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue(saved["data"]["prepared"])
        self.assertFalse(saved["data"]["restart_required"])
        self.assertIn("conversation.image_narration", saved["data"]["vision_applied"])
        self.assertIn("model", saved["data"]["status"]["completed"])
        self.assertFalse(saved["data"]["status"]["model"]["primary_supports_vision"])

        # 3b. Health check recognizes assigned vision model AND reads real multi-host proactive switch!
        vision_health = await self.call("health")
        self.assertEqual(vision_health["status"], "ok", vision_health)
        vchecks = {item["id"]: item for item in vision_health["data"]["checks"]}
        self.assertEqual(vchecks["model"]["status"], "ok")
        self.assertIn("gpt-vision", vchecks["model"]["summary"])
        self.assertEqual(vchecks["proactive"]["summary"], "私聊主动联系当前关闭（可随时开启）")

        # 4. Complete an optional step out of strict sequence (skip straight to "group").
        skipped_group = await self.call("complete", {"step": "group", "skipped": True})
        self.assertEqual(skipped_group["status"], "ok", skipped_group)

        # 5. Model can STILL be updated after advancing past step "model"!
        updated = await self.call("model", {"provider_id": "gpt-vision"})
        self.assertEqual(updated["status"], "ok", updated)
        self.assertEqual(self.config["model"]["provider_id"], "gpt-vision")
        self.assertTrue(updated["data"]["status"]["model"]["primary_supports_vision"])

        # 6. Zero-side-effect health check reports 6 checks and can_finish=True.
        health = await self.call("health")
        self.assertEqual(health["status"], "ok", health)
        checks = {item["id"]: item for item in health["data"]["checks"]}
        self.assertEqual(set(checks), {"account", "binding", "model", "runtime", "memory", "proactive"})
        self.assertEqual(checks["binding"]["status"], "ok")
        self.assertEqual(checks["model"]["status"], "ok")
        self.assertEqual(checks["memory"]["status"], "warn")
        self.assertTrue(health["data"]["can_finish"])

        # 7. Finish without proactive test starts host and persists ready=True.
        finished = await self.call("finish_setup", {"start_host": True})
        self.assertEqual(finished["status"], "ok", finished)
        self.assertTrue(finished["data"]["ready"])
        self.assertTrue(finished["data"]["started"])

        # 7b. Enable proactive contact while running and verify multi-host health check reflects it live.
        from companion_v2.execution.models import require as _require
        from companion_v2.proactive.management import ContactManagement
        session = self.controller.session or self.controller.last_session
        ctx = self.api._readonly_health_context(session, finished["data"]["status"]["binding"])
        self.assertIsNotNone(ctx)
        operator = self.api._operator(self.controller, "administrator")
        cm = ContactManagement(session.application, ctx)
        cs = _require(cm.status(operator))
        _require(cm.configure(operator, {
            "operation_key": "test-enable-proactive-health",
            "edit_token": cs["edit_token"],
            "changes": {"enabled": True},
        }))
        enabled_health = await self.call("health")
        echecks = {item["id"]: item for item in enabled_health["data"]["checks"]}
        self.assertEqual(echecks["proactive"]["summary"], "私聊主动联系已开启")

        # 8. Model can still be edited even AFTER ready=True (reusable wizard!).
        reused = await self.call("model", {"provider_id": "deepseek-text"})
        self.assertEqual(reused["status"], "ok", reused)
        self.assertEqual(self.config["model"]["provider_id"], "deepseek-text")

        # 9. Even if initialize_profile returns host_must_stop, vision_provider_id is still applied!
        from companion_v2.foundation.outcome import Outcome
        with patch.object(
            self.controller,
            "initialize_profile",
            AsyncMock(return_value=Outcome.failed("host_must_stop", "still draining")),
        ):
            busy_saved = await self.call("model", {
                "provider_id": "deepseek-text",
                "vision_provider_id": "gpt-vision",
            })
        self.assertEqual(busy_saved["status"], "ok", busy_saved)
        self.assertTrue(busy_saved["data"]["restart_required"])
        self.assertIn("conversation.image_narration", busy_saved["data"]["vision_applied"])


if __name__ == "__main__":
    unittest.main()
