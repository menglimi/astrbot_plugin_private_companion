"""TTS provider management and image config delegation use the real SDK with isolated data."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture
from astrbot_host.model_api import PREFIX, _probe_error_summary
from astrbot.api.web import PluginMultiDict, PluginRequest, bind_request_context
from starlette.requests import Request


class ProbeLogTests(unittest.TestCase):
    def test_probe_failure_summary_is_bounded_and_redacted(self):
        summary = _probe_error_summary(RuntimeError(
            "upstream https://provider.invalid/v1?api_key=secret-value "
            "api_key=another-secret Bearer token-value"))
        self.assertLessEqual(len(summary), 320)
        self.assertNotIn("provider.invalid", summary)
        self.assertNotIn("secret-value", summary)
        self.assertNotIn("another-secret", summary)
        self.assertNotIn("token-value", summary)
        self.assertIn("[URL]", summary)
        self.assertIn("[REDACTED]", summary)


class FakeProviderManager:
    def __init__(self):
        self.providers_config = [{"id": "tts-a", "type": "fishaudio_tts_api", "provider": "fishaudio_tts_api",
                                  "provider_type": "text_to_speech", "enable": True,
                                  "api_key": "secret-value", "proxy": "http://proxy-secret", "model": "s2.1-pro"}]

    async def create_provider(self, config):
        self.providers_config.append(deepcopy(config))

    async def update_provider(self, provider_id, new_config):
        for index, item in enumerate(self.providers_config):
            if item.get("id") == provider_id:
                self.providers_config[index] = deepcopy(new_config)


class FakeImageExtensionAPI:
    def __init__(self):
        self.revision = "a" * 64
        self.configuration = {"enabled": False, "backend": "auto",
            "endpoints": [], "sd_webui": {"base_url": "", "api_key_set": False, "timeout_seconds": 300},
            "comfyui": {"base_url": "", "api_key_set": False, "timeout_seconds": 300}}
        self.saved = None
        self.tested = None

    async def test_output_endpoint(self, endpoint_id, operation_key, *, before_dispatch):
        before_dispatch()
        self.tested = (endpoint_id, operation_key)
        return {"status": "succeeded", "operation_id": "test-operation", "elapsed_ms": 42, "error": ""}

    def output_configuration(self):
        return {"revision": self.revision, "settings": deepcopy(self.configuration), "live": False,
                "limits": {"max_endpoints": 24}, "supported_backends": ["auto", "online_api", "comfyui", "sd_webui"],
                "supported_platforms": ["auto", "openai", "minimax"]}

    def save_output_configuration(self, revision, changes):
        if revision != self.revision:
            error = RuntimeError("configuration_conflict")
            error.code = "configuration_conflict"
            raise error
        self.configuration.update(deepcopy(changes))
        self.saved = deepcopy(changes)
        self.revision = "b" * 64
        return self.output_configuration()


async def body(payload):
    return payload


async def assert_core_reply_prompt_roundtrip(test, prefix):
    listed = await test.call(prefix)
    test.assertEqual(listed["status"], "ok", listed)
    task = next(item for item in listed["data"]["tasks"] if item["kind"] == "conversation.reply")
    test.assertEqual(task["configuration_layer"], "role")
    test.assertEqual(task["execution_path"], "astrbot_core")
    test.assertEqual(task["editable_fields"], ["prompt"])
    test.assertIn("provider", task["core_managed_fields"])
    test.assertNotIn("output_contract", {layer["key"] for layer in task["prompt_layers"]})
    payload = {"kind": task["kind"], "operation_key": "core-prompt-route", "edit_token": task["edit_token"],
               "changes": {"prompt": "像聊天一样自然接话，按语意分段。"}, "reset": []}
    saved = await test.call(prefix + "/configure", payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    test.assertEqual(saved["status"], "ok", saved)
    test.assertEqual(saved["data"]["task"]["configuration_layer"], "role")
    test.assertEqual(saved["data"]["task"]["execution_path"], "astrbot_core")
    test.assertEqual(saved["data"]["task"]["fields"]["prompt"]["value"], payload["changes"]["prompt"])
    for key, prompt in (("style", "语气轻快，像刚看到好消息那样。"),
                        ("humanized", "兴冲冲地分享自己的发现，给对方接话的余地。")):
        sections = {item["key"]: item for item in saved["data"]["task"]["prompt_sections"] if item["editable"]}
        test.assertEqual(set(sections), {"style", "humanized"})
        section = sections[key]
        expected_field = "style_prompt" if key == "style" else "humanized_expression_prompt"
        test.assertEqual(section["config_field"], "conversation.presentation." + expected_field)
        section_payload = {"kind": task["kind"], "section": key, "layer": "role",
            "operation_key": "core-expression-" + key,
            "edit_token": section["edit_token"], "changes": {"prompt": prompt}, "reset": []}
        saved = await test.call(prefix + "/configure", payload=json.dumps(section_payload, ensure_ascii=False).encode("utf-8"))
        test.assertEqual(saved["status"], "ok", saved)
        changed = next(item for item in saved["data"]["task"]["prompt_sections"] if item["key"] == key)
        test.assertEqual(changed["prompt"], prompt)
        test.assertEqual(changed["field"]["source"], "role")
        test.assertEqual(changed["configuration_layer"], "role")
    window_status = await test.call(prefix, query={"layer": "window"})
    window_task = next(item for item in window_status["data"]["tasks"] if item["kind"] == task["kind"])
    window_section = next(item for item in window_task["prompt_sections"] if item["key"] == "style")
    test.assertEqual(window_section["prompt"], "语气轻快，像刚看到好消息那样。")
    test.assertEqual(window_section["field"]["source"], "role")
    local_prompt = "只在这个私聊里使用更简洁的口吻。"
    section_payload = {"kind": task["kind"], "section": "style", "layer": "window",
        "operation_key": "core-expression-style-window", "edit_token": window_section["edit_token"],
        "changes": {"prompt": local_prompt}, "reset": []}
    saved_window = await test.call(prefix + "/configure",
        payload=json.dumps(section_payload, ensure_ascii=False).encode("utf-8"))
    test.assertEqual(saved_window["status"], "ok", saved_window)
    window_changed = next(item for item in saved_window["data"]["task"]["prompt_sections"] if item["key"] == "style")
    test.assertEqual(window_changed["prompt"], local_prompt)
    test.assertEqual(window_changed["field"]["source"], "window")
    role_status = await test.call(prefix, query={"layer": "role"})
    role_task = next(item for item in role_status["data"]["tasks"] if item["kind"] == task["kind"])
    role_section = next(item for item in role_task["prompt_sections"] if item["key"] == "style")
    test.assertEqual(role_section["prompt"], "语气轻快，像刚看到好消息那样。")
    payload.update(operation_key="core-route-not-consumed", changes={"provider": "unused-route"})
    refused = await test.call(prefix + "/configure", payload=json.dumps(payload).encode("utf-8"))
    test.assertEqual(refused["message"], "model_configuration_host_managed")


class ProviderRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-provider-api-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(self.native, support.host_config(), Path(temporary.name),
                                                           native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        self.manager = FakeProviderManager()
        self.native.provider_manager = self.manager
        self.native.get_all_tts_providers = lambda: []

    def install_text_provider_catalog(self):
        primary = self.native.provider
        primary.meta = lambda: NS(id="native-model", name="Primary model")
        secondary = support.FakeProvider()
        secondary.provider_config["id"] = "secondary-model"
        secondary.model_name = "secondary-fixture"
        secondary.meta = lambda: NS(id="secondary-model", name="Secondary model")
        providers = {"native-model": primary, "secondary-model": secondary}
        self.native.get_all_providers = lambda: list(providers.values())
        self.native.get_provider_by_id = lambda provider_id: providers.get(provider_id)
        return providers

    async def call(self, route, *, payload=None, username="admin", query=None):
        prefix = "C:/Users/99505/.astrbot/data/plugins/astrbot_plugin_private_companion/astrbot_host.model_api"
        import astrbot_host.model_api as model_api
        request = Request({"type": "http", "method": "POST" if payload is not None else "GET",
                           "path": route, "headers": [], "query_string": b""})
        pairs = list(query.items()) if isinstance(query, dict) else query or []
        fake_request = NS(username=username, path=route, query=PluginMultiDict(pairs),
                          body=(lambda: body(payload)) if payload is not None else None)
        with patch.object(model_api, "request", fake_request):
            with bind_request_context(PluginRequest(request, username=username)):
                return await self.routes[route]()

    async def test_maintenance_diagnostics_route_is_registered_as_read_only(self):
        methods = {path: methods for path, _, methods, _ in self.native.registered_web_apis}
        self.assertEqual(methods[PREFIX + "/maintenance"], ["GET"])
        self.assertEqual(methods[PREFIX + "/balance"], ["GET"])

    async def test_maintenance_reads_the_current_internal_together_statistics(self):
        (await self.controller.initialize_profile()).unwrap()
        sources = {"together": {"available": True, "totals": {"tokens": 87}}}
        main = {"available": True, "totals": {"tokens": 999}, "counted_in_private_companion_budget": False}
        with patch("astrbot_host.model_api.read_token_sources", return_value=deepcopy(sources)) as read_sources, \
                patch("astrbot_host.model_api.read_astrbot_token_usage", new=AsyncMock(return_value=main)) as read_main:
            response = await self.call(PREFIX + "/maintenance")
        self.assertEqual(response["status"], "ok", response)
        read_sources.assert_called_once_with(self.controller)
        read_main.assert_awaited_once_with(self.controller)
        self.assertEqual(response["data"]["external_token_sources"], {**sources, "main": main})
        self.assertNotEqual(response["data"]["summary"]["tokens"], main["totals"]["tokens"])

    async def test_maintenance_adds_read_only_backup_disk_and_mobile_checks(self):
        (await self.controller.initialize_profile()).unwrap()
        runtime_root = Path(self.controller.runtime.data_root)
        snapshot_id = "a" * 32
        backup_directory = runtime_root / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        (backup_directory / f"companion-v2-{snapshot_id}.sqlite3").write_bytes(b"SQLite format 3\x00")
        (backup_directory / f"companion-v2-{snapshot_id}.json").write_text(json.dumps({
            "format": "companion-v2.installation-backup.v1", "snapshot_id": snapshot_id,
            "created_at": "2026-10-30T12:00:00+00:00", "byte_size": 16,
        }), encoding="utf-8")
        extension_directory = runtime_root / "extensions" / "reality"
        extension_directory.mkdir(parents=True, exist_ok=True)
        (extension_directory / "config.json").write_text(json.dumps({
            "mobile": {"enabled": True, "pairing_token": "secret-token-value"},
        }), encoding="utf-8")

        response = await self.call(PREFIX + "/maintenance")

        self.assertEqual(response["status"], "ok", response)
        checks = response["data"]["host_checks"]
        self.assertTrue(checks["backup"]["available"])
        self.assertEqual(checks["backup"]["snapshot_count"], 1)
        self.assertEqual(checks["backup"]["latest_success_at"], "2026-10-30T12:00:00+00:00")
        self.assertTrue(checks["backup"]["same_volume"])
        self.assertGreater(checks["disk"]["data_root"]["free_bytes"], 0)
        self.assertTrue(checks["mobile"]["available"])
        self.assertTrue(checks["mobile"]["enabled"])
        self.assertTrue(checks["mobile"]["pairing_token_configured"])
        self.assertNotIn("secret-token-value", json.dumps(response, ensure_ascii=False))

    async def test_global_usage_is_not_read_before_dashboard_authorization(self):
        (await self.controller.initialize_profile()).unwrap()
        with patch("astrbot_host.model_api.read_astrbot_token_usage", new=AsyncMock()) as read_main:
            for username in (None, "", "api_key:plugin-test"):
                response = await self.call(PREFIX + "/maintenance", username=username)
                self.assertEqual(response["message"], "dashboard_identity_required")
        read_main.assert_not_awaited()

    async def test_business_prompt_section_roundtrip_uses_the_original_model_route(self):
        (await self.controller.initialize_profile()).unwrap()
        listed = await self.call(PREFIX)
        task = next(item for item in listed["data"]["tasks"] if item["kind"] == "proactive.compose")
        section = next(item for item in task["prompt_sections"] if item["key"] == "news_sharing")
        payload = {"kind": task["kind"], "section": "news_sharing", "operation_key": "news-template-route",
                   "edit_token": section["edit_token"], "changes": {"prompt": "自然分享新鲜事，保留具体感想。"}, "reset": []}
        saved = await self.call(PREFIX + "/configure", payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(saved["status"], "ok", saved)
        changed = next(item for item in saved["data"]["task"]["prompt_sections"] if item["key"] == "news_sharing")
        self.assertEqual(changed["prompt"], payload["changes"]["prompt"])
        self.assertEqual(changed["field"]["source"], "window")

    async def test_core_reply_prompt_roundtrip_exposes_only_effective_configuration(self):
        (await self.controller.initialize_profile()).unwrap()
        await assert_core_reply_prompt_roundtrip(self, PREFIX)

    async def test_model_status_accepts_sdk_query_keys_and_refuses_ambiguous_scope(self):
        (await self.controller.initialize_profile()).unwrap()
        for query in ({"layer": "role"}, {"layer": "window"}):
            with self.subTest(query=query):
                result = await self.call(PREFIX, query=query)
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["data"]["configuration_layer"], query["layer"])
        for query in ({"layer": "role", "target": "other"},
                      [("layer", "role"), ("layer", "window")]):
            with self.subTest(query=query):
                result = await self.call(PREFIX, query=query)
                self.assertEqual(result["message"], "model_request_invalid", result)

    async def test_image_provider_schema_exposes_editable_sd_sampling_fields(self):
        (await self.controller.initialize_profile()).unwrap()
        result = await self.call(PREFIX + "/image-schema")

        self.assertEqual(result["status"], "ok", result)
        fields = result["data"]["image_provider_group"]["sd_webui"]["fields"]
        self.assertEqual(fields["steps"]["kind"], "integer")
        self.assertEqual(fields["steps"]["storage"], "parameters")
        self.assertEqual(fields["cfg_scale"]["kind"], "number")
        self.assertEqual(fields["cfg_scale"]["group"], "sampling")
        self.assertIn("denoising_strength", fields)
        self.assertNotIn("endpoint", fields)
        self.assertNotIn("model", fields)

    async def test_staging_reopen_prepares_legacy_reaction_settings_before_read_only_assembly(self):
        from dataclasses import replace
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.configuration.owners import SparseConfigurationOwner
        from companion_v2.expression.reaction_configuration import GROUP, configuration_fields, read_snapshot

        (await self.controller.initialize_profile()).unwrap()
        context = self.controller.session.profile.target
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        fields = tuple(replace(field, layers=(ConfigLayer.WINDOW,)) for field in configuration_fields())
        SparseConfigurationOwner(self.controller.runtime).write(target, fields,
            changes={GROUP + ".candidate_limit": 11}, reset=(), expected_revision=0, observed_at=support.NOW)
        data_root = self.controller.data_root
        await self.controller.terminate()
        self.controller = support.AstrBotStagingController(self.native, support.host_config(), data_root,
            native_types=support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS,
                                                   support.FakeReverseApi), now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)

        loaded = (await self.controller.load_existing()).unwrap()

        self.assertTrue(loaded["loaded"])
        settings, revision = read_snapshot(self.controller.runtime, self.controller.session.profile.target)
        self.assertEqual((settings.candidate_limit, revision), (11, 1))
        self.assertIsNotNone(self.controller.runtime.read_one(
            "SELECT 1 FROM configuration_values WHERE target_key=? AND field_id=?",
            (target.key, GROUP + ".candidate_limit")))

    async def test_catalog_reads_loaded_astrbot_text_providers_without_a_registration_route(self):
        self.install_text_provider_catalog()
        listed = await self.call(PREFIX + "/catalog")
        self.assertEqual(listed["status"], "ok", listed)
        self.assertEqual({item["id"] for item in listed["data"]["providers"]},
                         {"native-model", "secondary-model"})
        self.assertNotIn(PREFIX + "/pool", self.routes)
        self.assertNotIn(PREFIX + "/pool/save", self.routes)

    async def test_balance_route_returns_only_the_normalized_observation(self):
        from companion_v2.proactive.balance_events import BalanceObservation
        import astrbot_host.model_api as model_api

        class Port:
            provider = "newapi"
            provider_label = "NewAPI"
            currency_label = "USD"

            async def fetch(self):
                return BalanceObservation(Decimal("8.5"), None, datetime(2026, 10, 2, tzinfo=timezone.utc))

        (await self.controller.initialize_profile()).unwrap()
        with patch.object(model_api, "configured_balance_port", return_value=Port()):
            result = await self.call(PREFIX + "/balance")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], {
            "configured": True, "available": True, "provider": "newapi", "provider_label": "NewAPI",
            "currency_label": "USD", "observed_at": "2026-10-02T00:00:00+00:00",
            "amount": "8.50", "state": "available",
        })
        self.assertNotIn("api_key", str(result).lower())

    async def test_tts_provider_roundtrip_masks_secrets(self):
        (await self.controller.initialize_profile()).unwrap()
        prefix = "C:/Users/99505/.astrbot/data/plugins/astrbot_plugin_private_companion/astrbot_host.model_api"
        import astrbot_host.model_api as model_api
        listed = await self.call(model_api.PREFIX + "/tts/providers")
        item = listed["data"]["items"][0]
        self.assertEqual(item["id"], "tts-a")
        secret = next(field for field in item["fields"] if field["key"] == "api_key")
        self.assertTrue(secret["secret"] and secret["set"] and secret["value"] == "")
        proxy = next(field for field in item["fields"] if field["key"] == "proxy")
        self.assertTrue(proxy["secret"] and proxy["set"] and proxy["value"] == "")
        model = next(field for field in item["fields"] if field["key"] == "model")
        self.assertEqual(model["value"], "s2.1-pro")
        self.assertEqual([option["value"] for option in model["options"]],
                         ["drama-3", "s2.1-pro-free", "s2.1-pro", "s2-pro", "s1"])
        self.assertEqual(next(field for field in item["fields"] if field["key"] == "proxy")["label"], "代理地址")
        self.assertGreaterEqual(len(listed["data"]["templates"]), 13)
        self.assertNotIn("proxy-secret", json.dumps(listed))

        created = await self.call(model_api.PREFIX + "/tts/providers/create",
                                  payload=b'{"type": "fishaudio_tts_api", "id": "tts-b"}')
        self.assertIn("tts-b", [entry["id"] for entry in created["data"]["items"]])
        new_item = next(entry for entry in created["data"]["items"] if entry["id"] == "tts-b")
        self.assertFalse(new_item["enable"])

        saved = await self.call(model_api.PREFIX + "/tts/providers/update", payload=(
            b'{"provider_id": "tts-b", "values": {"api_key": "new-key", "model": "s2.1-pro-free", "enable": true}}'))
        updated = next(entry for entry in saved["data"]["items"] if entry["id"] == "tts-b")
        self.assertTrue(updated["enable"])
        stored = next(entry for entry in self.manager.providers_config if entry["id"] == "tts-b")
        self.assertEqual(stored["api_key"], "new-key")
        self.assertEqual(stored["model"], "s2.1-pro-free")

        drama = await self.call(model_api.PREFIX + "/tts/providers/update", payload=(
            b'{"provider_id":"tts-b","values":{"model":"drama-3"}}'))
        self.assertEqual(drama["status"], "ok", drama)
        self.assertEqual(next(entry for entry in self.manager.providers_config
                              if entry["id"] == "tts-b")["model"], "drama-3")
        invalid = await self.call(model_api.PREFIX + "/tts/providers/update", payload=(
            b'{"provider_id":"tts-b","values":{"model":"drama3"}}'))
        self.assertEqual(invalid["data"]["code"], "tts_provider_invalid")

        cloned_with_draft = await self.call(model_api.PREFIX + "/tts/providers/clone", payload=(
            b'{"provider_id":"tts-b","language":"en","config":{"enable":true,"values":{"api_key":"","model":"s2-pro"}}}'))
        draft_clone_id = cloned_with_draft["data"]["created_id"]
        draft_clone = next(entry for entry in self.manager.providers_config if entry["id"] == draft_clone_id)
        self.assertTrue(draft_clone["enable"])
        self.assertEqual(draft_clone["model"], "s2-pro")
        self.assertEqual(draft_clone["api_key"], "new-key")

        blank = await self.call(model_api.PREFIX + "/tts/providers/update", payload=(
            b'{"provider_id": "tts-b", "values": {"api_key": ""}}'))
        self.assertEqual(blank["status"], "ok")
        stored = next(entry for entry in self.manager.providers_config if entry["id"] == "tts-b")
        self.assertEqual(stored["api_key"], "new-key", "blank secret must keep the stored value")

        cloned = await self.call(model_api.PREFIX + "/tts/providers/clone", payload=(
            b'{"provider_id": "tts-b", "language": "ja"}'))
        clone_id = cloned["data"]["created_id"]
        self.assertTrue(clone_id.startswith("tts-b-ja"))
        clone = next(entry for entry in self.manager.providers_config if entry["id"] == clone_id)
        self.assertFalse(clone["enable"])
        self.assertEqual(clone["api_key"], "new-key")

        duplicate = await self.call(model_api.PREFIX + "/tts/providers/create",
                                    payload=b'{"type": "fishaudio_tts_api", "id": "tts-b"}')
        self.assertEqual(duplicate["data"]["code"], "tts_provider_exists")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_voice_save_busy_keeps_settings_and_can_retry_once_host_is_ready(self):
        (await self.controller.initialize_profile()).unwrap()
        status = (await self.call(PREFIX + "/voice"))["data"]
        payload = json.dumps({"operation_key": "voice-lock-busy", "edit_token": status["edit_token"],
                              "changes": {"voice_timeout_seconds": 45}}).encode()
        lock = self.controller._control_lock
        await lock.acquire()
        try:
            with patch("astrbot_host.model_api.VOICE_SAVE_LOCK_WAIT_SECONDS", 0.01):
                busy = await asyncio.wait_for(self.call(PREFIX + "/voice/save", payload=payload), 1)
            self.assertEqual(busy["data"]["code"], "host_busy", busy)
            self.assertTrue(lock.locked(), "a refused save must not release the lifecycle owner's lock")
        finally:
            lock.release()
        unchanged = (await self.call(PREFIX + "/voice"))["data"]
        self.assertEqual(unchanged["edit_token"], status["edit_token"])
        self.assertEqual(unchanged["timeout_seconds"], status["timeout_seconds"])
        saved = await self.call(PREFIX + "/voice/save", payload=payload)
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["timeout_seconds"], 45)
        self.assertFalse(saved["data"]["replayed"])
        replayed = await self.call(PREFIX + "/voice/save", payload=payload)
        self.assertTrue(replayed["data"]["replayed"], replayed)
        self.assertEqual(replayed["data"]["edit_token"], saved["data"]["edit_token"])

    async def test_cancelled_voice_save_does_not_write_after_lock_is_released(self):
        (await self.controller.initialize_profile()).unwrap()
        status = (await self.call(PREFIX + "/voice"))["data"]
        payload = json.dumps({"operation_key": "voice-lock-cancel", "edit_token": status["edit_token"],
                              "changes": {"voice_timeout_seconds": 45}}).encode()
        lock = self.controller._control_lock
        await lock.acquire()
        try:
            task = asyncio.create_task(self.call(PREFIX + "/voice/save", payload=payload))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(lock.locked())
        finally:
            lock.release()
        await asyncio.sleep(0)
        unchanged = (await self.call(PREFIX + "/voice"))["data"]
        self.assertEqual(unchanged["edit_token"], status["edit_token"])
        self.assertFalse(lock.locked())

    async def test_voice_save_authorization_failure_releases_its_lock(self):
        (await self.controller.initialize_profile()).unwrap()
        status = (await self.call(PREFIX + "/voice"))["data"]
        payload = json.dumps({"operation_key": "voice-lock-auth", "edit_token": status["edit_token"],
                              "changes": {"voice_timeout_seconds": 45}}).encode()
        refused = await self.call(PREFIX + "/voice/save", payload=payload, username="api_key:fixture")
        self.assertEqual(refused["data"]["code"], "dashboard_identity_required", refused)
        self.assertFalse(self.controller._control_lock.locked())
        self.assertEqual((await self.call(PREFIX + "/voice"))["data"]["edit_token"], status["edit_token"])

    async def test_voice_test_returns_audio_for_inline_preview(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        class TtsProvider:
            provider_config = {"api_key": "fixture-tts-secret"}

            def meta(self):
                return NS(id="tts-live", model="gpt-4o-mini-tts")

            async def get_audio(self, text):
                self.text = text
                return "preview.wav"

        provider = TtsProvider()
        self.native.get_all_tts_providers = lambda: [provider]
        # The generic context lookup is not the authoritative TTS lookup on all
        # AstrBot releases; the route must resolve the live TTS collection.
        self.native.get_provider_by_id = lambda provider_id: self.native.provider if provider_id == "native-model" else None
        catalog = await self.call(model_api.PREFIX + "/voice")
        self.assertEqual(catalog["status"], "ok", catalog)
        self.assertEqual(catalog["data"]["providers"], [{"id": "tts-live", "model": "gpt-4o-mini-tts"}])
        self.assertNotIn("fixture-tts-secret", json.dumps(catalog))
        with patch.object(model_api, "_read_audio", return_value=b"RIFF0000WAVE"):
            result = await self.call(model_api.PREFIX + "/voice/test", payload=(
                b'{"provider_id":"tts-live","language":"ja"}'))

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["status"], "ok")
        self.assertEqual(result["data"]["audio_base64"], "UklGRjAwMDBXQVZF")
        self.assertEqual(result["data"]["audio_mime_type"], "audio/wav")
        self.assertEqual(provider.text, "こんにちは。")

    async def test_voice_test_returns_sanitized_failure_details_and_suggestion(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        class TtsProvider:
            provider_config = {"api_key": "fixture-secret", "proxy": "http://proxy-secret"}

            def meta(self):
                return NS(id="tts-error", model="fishaudio")

            async def get_audio(self, text):
                raise RuntimeError("Fish Audio API request failed: status 400 api_key=fixture-secret")

        provider = TtsProvider()
        self.native.get_all_tts_providers = lambda: [provider]
        self.native.get_provider_by_id = lambda provider_id: self.native.provider if provider_id == "native-model" else None
        result = await self.call(model_api.PREFIX + "/voice/test", payload=(
            b'{"provider_id":"tts-error","language":"ja"}'))

        self.assertEqual(result["status"], "ok", result)
        data = result["data"]
        self.assertEqual(data["status"], "unknown")
        self.assertEqual(data["code"], "model_voice_probe_failed")
        self.assertEqual(data["stage"], "synthesis")
        self.assertEqual(data["exception_type"], "RuntimeError")
        self.assertIn("status 400", data["error"])
        self.assertNotIn("fixture-secret", data["error"])
        self.assertNotIn("proxy-secret", data["error"])
        self.assertTrue(data["suggestion"])
        self.assertRegex(data["request_id"], r"^[0-9a-f]{12}$")

    async def test_voice_test_reports_audio_read_failure_stage(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        class TtsProvider:
            def meta(self):
                return NS(id="tts-read-error", model="fishaudio")

            async def get_audio(self, text):
                return "preview.wav"

        provider = TtsProvider()
        self.native.get_all_tts_providers = lambda: [provider]
        self.native.get_provider_by_id = lambda provider_id: self.native.provider if provider_id == "native-model" else None
        with patch.object(model_api, "_read_audio", side_effect=OSError("permission denied")):
            result = await self.call(model_api.PREFIX + "/voice/test", payload=(
                b'{"provider_id":"tts-read-error","language":"ja"}'))

        data = result["data"]
        self.assertEqual(data["status"], "unknown")
        self.assertEqual(data["code"], "model_voice_audio_read_failed")
        self.assertEqual(data["stage"], "audio_read")
        self.assertIn("permission denied", data["error"])

    async def test_tts_provider_connection_test_reports_loaded_provider_result(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        class TtsProvider:
            async def test(self):
                return None

        self.manager.inst_map = {"tts-a": TtsProvider()}
        result = await self.call(model_api.PREFIX + "/tts/providers/test", payload=b'{"provider_id":"tts-a"}')
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["status"], "ok")
        self.assertEqual(result["data"]["steps"][-1]["status"], "ok")

        self.manager.inst_map = {}
        unloaded = await self.call(model_api.PREFIX + "/tts/providers/test", payload=b'{"provider_id":"tts-a"}')
        self.assertEqual(unloaded["data"]["code"], "tts_provider_unloaded")

    async def test_image_output_reads_the_native_configuration_contract(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api
        fake = FakeImageExtensionAPI()
        with patch.object(model_api.ModelWebAPI, "_image_extension_api", lambda self: fake):
            listed = await self.call(model_api.PREFIX + "/image/output")
            self.assertEqual(listed["data"]["revision"], "a" * 64)
            self.assertEqual(listed["data"]["settings"]["backend"], "auto")
            self.assertTrue(listed["data"]["host_registered"])

        with patch.object(model_api.ModelWebAPI, "_image_extension_api", lambda self: None):
            missing = await self.call(model_api.PREFIX + "/image/output")
        self.assertEqual(missing["data"]["code"], "model_image_output_unavailable")

    async def test_image_output_accepts_a_registered_supported_subset(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        actions = ("image.generate", "image.edit", "image.mask_edit")
        owners = tuple(NS(
            host_tool_id="native:" + action,
            descriptor=NS(tool=NS(action=action), package=NS(extension_id="image")),
        ) for action in actions)
        by_id = {owner.host_tool_id: owner for owner in owners}
        readiness = lambda host_tool_id: "unsupported" if host_tool_id.endswith(("image.edit", "image.mask_edit")) else "ready"
        registry = NS(
            declarations=lambda: owners,
            declaration_readiness=readiness,
            current_declaration=lambda host_tool_id: by_id[host_tool_id] if readiness(host_tool_id) == "ready" else None,
        )
        session = NS(application=NS(external=NS(providers={
            "image.generate": NS(descriptor=owners[0].descriptor),
        })))
        with patch.object(self.controller, "native_extensions", registry), \
             patch.object(self.controller, "session", session), \
             patch.object(self.controller, "last_session", None):
            api = model_api.ModelWebAPI(self.controller)
            self.assertTrue(api._image_host_registered())
            session.application.external.providers.clear()
            self.assertFalse(api._image_host_registered())

    async def test_image_output_reconnect_reports_image_registration_separately(self):
        (await self.controller.initialize_profile()).unwrap()
        from companion_v2.foundation.outcome import Outcome
        import astrbot_host.model_api as model_api

        result = Outcome.success({"reconnected": True, "state": "running", "report": {}})
        with patch.object(self.controller, "reconnect_native", AsyncMock(return_value=result)), \
             patch.object(model_api.ModelWebAPI, "_image_host_registered", return_value=False):
            response = await self.call(model_api.PREFIX + "/image/output/reconnect", payload=b"{}")
        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(response["data"]["reconnected"])
        self.assertFalse(response["data"]["host_registered"])

    async def test_image_output_test_rechecks_authority_and_calls_the_saved_endpoint(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api
        fake = FakeImageExtensionAPI()
        payload = b'{"endpoint_id":"primary","operation_key":"image-test-key"}'
        with patch.object(model_api.ModelWebAPI, "_image_extension_api", lambda self: fake):
            result = await self.call(PREFIX + "/image/output/test", payload=payload)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["status"], "succeeded")
        self.assertEqual(fake.tested, ("primary", "image-test-key"))

    async def test_image_status_exposes_the_module_startup_diagnostic(self):
        (await self.controller.initialize_profile()).unwrap()
        registry = NS(
            image_enabled={"image.generate": True, "image.edit": True, "image.mask_edit": True},
            declarations=lambda: (), status=lambda _extension: {"state": "unavailable", "error_code": "native_extension_unavailable"},
            issues=(("astrbot_plugin_private_companion.image", "native_configuration_invalid"),),
        )
        with patch.object(self.controller, "native_extensions", registry):
            response = await self.call(PREFIX + "/image")
        self.assertEqual(response["data"]["module"]["issue_code"], "native_configuration_invalid")
        self.assertEqual(response["data"]["actions"][0]["diagnostic_code"], "native_configuration_invalid")

    async def test_image_status_projects_the_first_configured_model_in_the_generate_queue(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api

        fake = FakeImageExtensionAPI()
        fake.configuration = {
            "enabled": True,
            "backend": "online_api",
            "endpoints": [
                {"id": "primary", "name": "主在线 API", "enabled": True, "model": "gpt-image-2"},
                {"id": "secondary", "name": "备用在线 API", "enabled": True, "model": "gpt-image-2.5"},
            ],
            "operation_routes": {"generate": ["online:primary", "online:secondary"], "edit": []},
        }
        registry = NS(image_extension_api=lambda: fake)

        preferred = model_api.ModelWebAPI(self.controller)._preferred_image_projection(registry)

        self.assertEqual(preferred, {"model": "gpt-image-2", "name": "主在线 API",
                                     "route": "online:primary", "enabled": True})

        fake.configuration["endpoints"][0]["enabled"] = False
        preferred = model_api.ModelWebAPI(self.controller)._preferred_image_projection(registry)
        self.assertEqual(preferred, {"model": "gpt-image-2.5", "name": "备用在线 API",
                                     "route": "online:secondary", "enabled": True})

        fake.configuration["endpoints"][1]["name"] = "   "
        preferred = model_api.ModelWebAPI(self.controller)._preferred_image_projection(registry)
        self.assertEqual(preferred["name"], "gpt-image-2.5")

        fake.configuration["endpoints"][1]["name"] = "  夜间线路  "
        preferred = model_api.ModelWebAPI(self.controller)._preferred_image_projection(registry)
        self.assertEqual(preferred["name"], "夜间线路")

    async def test_image_status_distinguishes_unsupported_routes_from_stale_declarations(self):
        (await self.controller.initialize_profile()).unwrap()
        actions = ("image.generate", "image.edit", "image.mask_edit")
        owners = tuple(NS(
            host_tool_id="native:" + action,
            descriptor=NS(tool=NS(action=action), package=NS(extension_id="image"),
                          binding=NS(provider="image.native")),
        ) for action in actions)
        by_id = {owner.host_tool_id: owner for owner in owners}
        registry = NS(
            image_enabled={action: True for action in actions},
            declarations=lambda: owners,
            status=lambda _extension: {"state": "running"},
            issues=(),
            declaration_readiness=lambda host_tool_id: "unsupported" if host_tool_id.endswith("image.mask_edit") else "ready",
            current_declaration=lambda host_tool_id: None if host_tool_id.endswith("image.mask_edit") else by_id[host_tool_id],
        )
        with patch.object(self.controller, "native_extensions", registry):
            response = await self.call(PREFIX + "/image")
        mask = next(item for item in response["data"]["actions"] if item["action"] == "image.mask_edit")
        self.assertEqual(mask["state"], "unsupported")
        self.assertEqual(mask["diagnostic_code"], "native_image_operation_unavailable")
        self.assertTrue(mask["loaded"])

    async def test_image_output_save_roundtrip_and_conflict(self):
        (await self.controller.initialize_profile()).unwrap()
        import astrbot_host.model_api as model_api
        fake = FakeImageExtensionAPI()
        with patch.object(model_api.ModelWebAPI, "_image_extension_api", lambda self: fake):
            payload = json.dumps({"revision": "a" * 64, "changes": {
                "enabled": True, "backend": "online_api", "endpoints": [{"id": "primary",
                    "name": "Primary", "enabled": True, "platform": "openai",
                    "base_url": "https://api.example.test/v1", "api_key": "image-key",
                    "model": "gpt-image-1", "width": 1024, "height": 1024,
                    "timeout_seconds": 60}]}}).encode("utf-8")
            saved = await self.call(model_api.PREFIX + "/image/output/save", payload=payload)
            self.assertEqual(saved["data"]["revision"], "b" * 64)
            self.assertEqual(fake.saved["backend"], "online_api")
            self.assertEqual(fake.saved["endpoints"][0]["model"], "gpt-image-1")

            conflict_payload = json.dumps({"revision": "a" * 64, "changes": {"enabled": False}}).encode("utf-8")
            conflict = await self.call(model_api.PREFIX + "/image/output/save", payload=conflict_payload)
            self.assertEqual(conflict["data"]["code"], "model_image_output_conflict")


class MultiCoreReplyConfigurationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    call = ProviderRouteTests.call

    def setUp(self):
        super().setUp()
        self.controller.register_routes()
        self.routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}

    async def test_core_reply_prompt_roundtrip_uses_the_production_target_route(self):
        (await self.controller.initialize_profile()).unwrap()
        inventory = self.controller.web_api.target_inventory()
        target = next(item for item in inventory if item["target_id"] == "first")
        role = next(item for item in target["roles"] if item["role_id"] == "role-a")
        await assert_core_reply_prompt_roundtrip(self, role["model_api"])
