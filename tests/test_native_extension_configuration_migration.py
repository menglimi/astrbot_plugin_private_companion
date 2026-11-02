"""Former standalone settings must survive the upgrade without widening authority."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

from companion_v2.legacy.migration import ConfigIntent, LegacyMigration, MigrationState
from companion_v2.legacy.native_extension_configuration import (
    LIVE_RENAMES, SOURCE_PLUGINS, convert_native_extension_configuration, settings_state_token,
)


PACKAGE = Path(__file__).resolve().parents[1] / "astrbot_host" / "native_extensions"


def baseline(name):
    return {key: json.loads((PACKAGE / name / f"settings-{key}.json").read_text(encoding="utf-8"))
            for key in ("defaults", "schema")}


class ConverterTests(TestCase):
    def test_portable_game_preference_and_together_timing_are_kept(self):
        game = convert_native_extension_configuration(
            "game", {"server": {"enabled": True},
                     "native_v2": {"gomoku_enabled": False}}, **baseline("game"))
        together = convert_native_extension_configuration(
            "together", {"conversation": {"history_turns": 20,
                                                 "chat_provider_id": "old-provider"},
                         "speech": {"tts_provider_id": "old-tts"}}, **baseline("together"))
        self.assertEqual(game.changes, {"native_v2.gomoku_enabled": False})
        self.assertEqual(together.changes, {"conversation.history_turns": 20,
                                            "conversation.chat_provider_id": "old-provider",
                                            "speech.tts_provider_id": "old-tts"})
        self.assertIn("game.server.enabled", {item["field"] for item in game.differences})
        self.assertNotIn("server.enabled", game.changes)

    def test_live_uses_the_runtime_aliases_and_schema_bounds(self):
        self.assertEqual(LIVE_RENAMES["private_companion_live_context_max_users"],
                         "live_relationship_context_max_users")
        live = convert_native_extension_configuration(
            "live", {"private_companion_live_context_max_users": 5,
                     "subtitle_hold_seconds": 300, "bilibili_enabled": True},
            **baseline("live"))
        self.assertEqual(live.changes, {"live_relationship_context_max_users": 5})
        by_field = {item["field"]: item["kind"] for item in live.differences}
        self.assertEqual(by_field["live.subtitle_hold_seconds"], "invalid_value")
        self.assertEqual(by_field["live.bilibili_enabled"], "manual_review_required")

    def test_discovery_requires_registry_for_content_and_keeps_screen_review_only(self):
        with TemporaryDirectory() as folder:
            plan = LegacyMigration(
                v1_root=folder,
                v1_native_configurations={"content": {"enabled": True},
                                          "screen": {"enabled": True}},
            ).plan().unwrap()
        states = {item.source_id: item.state for item in plan.findings}
        self.assertEqual(states["content.configuration"], MigrationState.NO_HANDLER.value)
        self.assertEqual(states["screen.configuration"], MigrationState.DOCUMENTED_SKIP.value)
        self.assertFalse(any(item.source_id.startswith(("content.", "screen."))
                             for item in plan.intents))

    def test_live_accepts_schema_floats_but_rejects_nonfinite_or_wrong_types(self):
        for value in (2.5, 3):
            with self.subTest(value=value):
                converted = convert_native_extension_configuration(
                    "live", {"subtitle_hold_seconds": value}, **baseline("live"))
                self.assertEqual(converted.changes, {"subtitle_hold_seconds": value})
        for value in (float("nan"), float("inf"), -1, 300, True, "2.5"):
            with self.subTest(value=value):
                converted = convert_native_extension_configuration(
                    "live", {"subtitle_hold_seconds": value}, **baseline("live"))
                self.assertFalse(converted.changes)
                self.assertEqual(converted.differences[0]["kind"], "invalid_value")

    def test_together_carries_vision_and_stt_without_relabeling_shared_experiences(self):
        converted = convert_native_extension_configuration("together", {
            "conversation": {"vision_provider_id": "old-vision", "system_prompt": "角色提示",
                             "record_shared_experiences": False},
            "speech": {"stt_provider_id": "old-stt", "tts_timeout_seconds": 75}},
            **baseline("together"))
        self.assertEqual(converted.changes, {"conversation.vision_provider_id": "old-vision",
            "conversation.system_prompt": "角色提示", "speech.stt_provider_id": "old-stt",
            "speech.tts_timeout_seconds": 75})
        self.assertEqual(next(item["kind"] for item in converted.differences
            if item["field"] == "together.conversation.record_shared_experiences"), "changed_semantics")

    def test_missing_target_schema_blocks_standalone_config(self):
        with TemporaryDirectory() as folder:
            plan = LegacyMigration(v1_root=folder,
                v1_native_configurations={"game": {"native_v2": {"gomoku_enabled": False}}}).plan().unwrap()
        finding = next(item for item in plan.findings if item.source_id == "game.configuration")
        self.assertEqual(finding.state, MigrationState.NO_HANDLER.value)

    def test_plan_has_one_intent_per_native_source_and_redacts_prompts(self):
        with TemporaryDirectory() as folder:
            plan = LegacyMigration(
                v1_root=folder,
                v1_native_configurations={
                    "game": {"native_v2": {"xiangqi_enabled": False}},
                    "live": {"bili_live_auto_reply_system_prompt": "private instruction marker"},
                },
                native_extension_baselines={name: baseline(name) for name in SOURCE_PLUGINS},
            ).plan().unwrap()
        native = [item for item in plan.intents if item.target_kind == "native_extension_config"]
        self.assertEqual({item.group_id for item in native}, {"game", "live"})
        self.assertNotIn("private instruction marker", json.dumps(plan.document()))
        self.assertIn("bili_live_auto_reply_system_prompt",
                      next(item.changes for item in native if item.group_id == "live"))

    def test_state_token_ignores_unrelated_extension_preferences(self):
        settings = {"native_v2": {"gomoku_enabled": True, "xiangqi_enabled": True}}
        before = settings_state_token(settings, ("native_v2.xiangqi_enabled",))
        settings["native_v2"]["gomoku_enabled"] = False
        self.assertEqual(before, settings_state_token(settings, ("native_v2.xiangqi_enabled",)))
        settings["native_v2"]["xiangqi_enabled"] = False
        self.assertNotEqual(before, settings_state_token(settings, ("native_v2.xiangqi_enabled",)))

    def test_review_only_source_does_not_invalidate_configuration_plan(self):
        with TemporaryDirectory() as folder:
            common = {"v1_root": folder,
                      "v1_native_configurations": {"game": {"native_v2": {"gomoku_enabled": False}},
                                                   "screen": {"enabled": True}}}
            original = LegacyMigration(**common)
            review_changed = LegacyMigration(**(common | {"v1_native_configurations": {
                "game": {"native_v2": {"gomoku_enabled": False}},
                "screen": {"enabled": False}}}))
            game_changed = LegacyMigration(**(common | {"v1_native_configurations": {
                "game": {"native_v2": {"gomoku_enabled": True}},
                "screen": {"enabled": True}}}))
        self.assertEqual(original.configuration_context_fingerprint,
                         review_changed.configuration_context_fingerprint)
        self.assertNotEqual(original.configuration_context_fingerprint,
                            game_changed.configuration_context_fingerprint)


class HostApplyTests(IsolatedAsyncioTestCase):
    async def test_broken_optional_source_does_not_block_core_migration(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        findings = [
            {"source_id": "private.host_configuration", "state": "ready"},
            {"source_id": "image.configuration", "state": "absent"},
            {"source_id": "game.configuration", "state": "unreadable"},
            {"source_id": "together.configuration", "state": "absent"},
            {"source_id": "live.configuration", "state": "absent"},
        ]
        self.assertEqual(LegacyMigrationWebAPI._configuration_source_blockers(findings, "plugin"), [])
        errors = LegacyMigrationWebAPI._optional_extension_source_errors(findings, "plugin")
        self.assertEqual([item["scope"] for item in errors], ["game.settings"])

    async def test_available_chat_and_voice_providers_can_be_reused(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        chat = SimpleNamespace(text_chat=lambda *args: None)
        voice = SimpleNamespace(meta=lambda: SimpleNamespace(id="voice-id"))
        context = SimpleNamespace(get_provider_by_id=lambda name: chat if name == "chat-id" else None,
                                  get_all_tts_providers=lambda: [voice])
        api = LegacyMigrationWebAPI(SimpleNamespace(context=context))
        self.assertTrue(api._legacy_provider_available("conversation.chat_provider_id", "chat-id"))
        self.assertTrue(api._legacy_provider_available("speech.tts_provider_id", "voice-id"))

    async def test_vision_and_stt_require_their_actual_capabilities(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        providers = {"text": SimpleNamespace(text_chat=lambda **kwargs: None,
                        meta=lambda: SimpleNamespace(modalities=["text"])),
                     "vision": SimpleNamespace(text_chat=lambda **kwargs: None,
                        meta=lambda: SimpleNamespace(modalities=["image"])),
                     "stt": SimpleNamespace(get_text=lambda **kwargs: None)}
        api = LegacyMigrationWebAPI(SimpleNamespace(
            context=SimpleNamespace(get_provider_by_id=providers.get)))
        self.assertFalse(api._legacy_provider_available("conversation.vision_provider_id", "text"))
        self.assertTrue(api._legacy_provider_available("conversation.vision_provider_id", "vision"))
        self.assertFalse(api._legacy_provider_available("speech.stt_provider_id", "text"))
        self.assertTrue(api._legacy_provider_available("speech.stt_provider_id", "stt"))

    async def test_unavailable_provider_is_preserved_with_a_reselection_note(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        from astrbot_host.native_extensions.settings import ExtensionSettings
        with TemporaryDirectory() as folder:
            config = ExtensionSettings(folder, "together")
            registry = SimpleNamespace(service=lambda name: SimpleNamespace(config=config),
                                       configure_service=lambda *args: None)
            api = LegacyMigrationWebAPI(SimpleNamespace(native_extensions=registry,
                context=SimpleNamespace(get_provider_by_id=lambda name: None,
                                        get_all_tts_providers=lambda: [])))
            intent = ConfigIntent("together.settings", "together",
                {"conversation.chat_provider_id": "old-chat",
                 "speech.tts_provider_id": "old-voice"},
                "together.configuration", target_kind="native_extension_config")
            differences = []
            prepared = api._prepare_native_extension_config_intent(intent, differences, [], {})
            self.assertIsNotNone(prepared)
            self.assertEqual(prepared.changes, {"conversation.chat_provider_id": "old-chat",
                                                "speech.tts_provider_id": "old-voice"})
            self.assertEqual(sum(item["kind"] == "provider_unavailable_preserved"
                                 for item in differences), 2)

    async def test_apply_saves_portable_values_and_preserves_newer_settings(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        from astrbot_host.native_extensions.settings import ExtensionSettings
        with TemporaryDirectory() as folder:
            config = ExtensionSettings(folder, "game")
            config.update_fields({"native_v2": {"gomoku_enabled": False}})
            service = SimpleNamespace(config=config)
            calls = []

            async def configure(name, changes, revision):
                calls.append((name, changes))
                self.assertEqual(revision, config.revision())
                config.update_fields(changes)
                return {"saved": True}

            registry = SimpleNamespace(service=lambda name: service, configure_service=configure)
            api = LegacyMigrationWebAPI(SimpleNamespace(native_extensions=registry))
            intent = ConfigIntent(
                "game.settings", "game",
                {"native_v2.gomoku_enabled": True, "native_v2.xiangqi_enabled": False},
                "game.configuration", target_kind="native_extension_config")
            tokens = {}
            prepared = api._prepare_native_extension_config_intent(intent, [], [], tokens)
            self.assertEqual(prepared.changes, {"native_v2.xiangqi_enabled": False})
            # An unrelated page edit does not invalidate this narrowly scoped plan.
            config.update_fields({"rooms": {"max_group_rooms": 2}})
            result = await api._apply_native_extension_config_intent(
                prepared, tokens["native_extension_states"]["game"])
            self.assertEqual(result, ["game.native_v2.xiangqi_enabled"])
            self.assertEqual(len(calls), 1)
            saved = json.loads(config.path.read_text(encoding="utf-8"))
            self.assertFalse(saved["native_v2"]["gomoku_enabled"])
            self.assertFalse(saved["native_v2"]["xiangqi_enabled"])
            self.assertEqual(saved["rooms"]["max_group_rooms"], 2)
