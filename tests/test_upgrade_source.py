"""Exercise the real import-before-schema boundary without a live installation."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.upgrade_source import (
    CONFIG_NAME, preserve_before_schema, preserved_configuration_path,
    usable_preserved_configuration_path,
)
from astrbot_host.legacy_config import read_v1_host_configuration


class UpgradeSourceTests(unittest.TestCase):
    def test_schema_pruning_and_reloads_keep_exact_legacy_source(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            config = data / "config"
            config.mkdir()
            old = b'\xef\xbb\xbf{"basic_config": {}, "target_user_ids": ["123"]}\r\n'
            path = config / CONFIG_NAME
            path.write_bytes(old)
            saved = preserve_before_schema(config)
            self.assertEqual(saved.read_bytes(), old)
            path.write_text('{"multi_host": {"enabled": false}}\n', encoding="utf-8")
            self.assertEqual(preserve_before_schema(config), saved)
            self.assertEqual(saved.read_bytes(), old)
            runtime = data / "plugin_data" / "astrbot_plugin_private_companion" / "companion-7"
            loaded, found, fingerprint = read_v1_host_configuration(runtime)
            self.assertEqual(found, saved)
            self.assertEqual(loaded["target_user_ids"], ["123"])
            self.assertEqual(len(fingerprint), 64)

    def test_changed_archive_is_not_silently_treated_as_a_new_install(self):
        with TemporaryDirectory() as directory:
            config = Path(directory)
            (config / CONFIG_NAME).write_text('{"basic_config": {}}', encoding="utf-8")
            saved = preserve_before_schema(config)
            saved.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                preserved_configuration_path(config)

    def test_later_defaulted_legacy_snapshot_does_not_replace_complete_source(self):
        with TemporaryDirectory() as directory:
            config = Path(directory)
            path = config / CONFIG_NAME
            complete = {
                "basic_config": {
                    "target_user_ids": ["123"],
                    "plugin_specific_persona_id": "persona-a",
                    "LLM_PROVIDER_ID": "provider-a",
                },
                "group_observation_config": {"group_whitelist_ids": ["g1", "g2", "g3"]},
            }
            path.write_text(json.dumps(complete), encoding="utf-8")
            saved = preserve_before_schema(config)
            source_pointer = json.loads((config / ".private-companion-upgrade" / "source.json")
                                        .read_text(encoding="utf-8"))

            defaulted = {
                "basic_config": {
                    "target_user_ids": [],
                    "plugin_specific_persona_id": "",
                    "LLM_PROVIDER_ID": "",
                },
                "group_observation_config": {"group_whitelist_ids": []},
            }
            path.write_text(json.dumps(defaulted), encoding="utf-8")
            self.assertEqual(preserve_before_schema(config), saved)
            self.assertEqual(
                json.loads((config / ".private-companion-upgrade" / "source.json")
                           .read_text(encoding="utf-8")),
                source_pointer,
            )
            loaded = json.loads(preserved_configuration_path(config).read_text(encoding="utf-8"))
            self.assertEqual(loaded, complete)
            runtime = config / "plugin_data" / "astrbot_plugin_private_companion" / "companion-7"
            # The reader must honor the retained source even when the rewritten
            # current file still looks like a legacy configuration.
            with patch("astrbot_host.legacy_config._config_directories", return_value=(config,)):
                decoded, selected, _ = read_v1_host_configuration(runtime)
            self.assertEqual(selected, saved)
            self.assertEqual(decoded, complete)
            # Keep the rejected later snapshot for diagnosis without pointing
            # the migration at it.
            self.assertEqual(len(list((config / ".private-companion-upgrade").glob("*.json"))), 3)

    def test_source_can_advance_when_it_retains_previous_populated_values(self):
        with TemporaryDirectory() as directory:
            config = Path(directory)
            path = config / CONFIG_NAME
            original = {"basic_config": {"target_user_ids": ["123"], "LLM_PROVIDER_ID": "provider-a"}}
            path.write_text(json.dumps(original), encoding="utf-8")
            first = preserve_before_schema(config)
            updated = {
                "basic_config": {"target_user_ids": ["123"], "LLM_PROVIDER_ID": "provider-b"},
                "newly_saved_setting": "kept",
            }
            path.write_text(json.dumps(updated), encoding="utf-8")
            second = preserve_before_schema(config)
            self.assertNotEqual(second, first)
            self.assertEqual(json.loads(second.read_text(encoding="utf-8")), updated)

    def test_new_install_does_not_create_an_upgrade_archive(self):
        with TemporaryDirectory() as directory:
            config = Path(directory)
            (config / CONFIG_NAME).write_text('{"multi_host": {}}', encoding="utf-8")
            self.assertIsNone(preserve_before_schema(config))
            self.assertEqual(len(list(config.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()


class UpgradeReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_uses_the_reviewed_source_and_live_inventory(self):
        from astrbot_host.upgrade_setup import UpgradeSetup
        with TemporaryDirectory() as directory:
            data = Path(directory)
            source = data / "plugin_data" / "astrbot_plugin_private_companion"
            source.mkdir(parents=True)
            (source / "companions.json").write_text("{}", encoding="utf-8")
            config = data / "config"
            config.mkdir()
            legacy = {"basic_config": {"target_user_ids": ["123"], "plugin_specific_persona_id": "test-persona",
                      "LLM_PROVIDER_ID": "test-model"}, "operation_access": {"public_operations": []}}
            (config / CONFIG_NAME).write_text(json.dumps(legacy), encoding="utf-8")
            controller = NS(data_root=source / "companion-7", config={"multi_host": {}},
                context=NS(get_provider_by_id=lambda _: object(),
                    conversation_manager=NS(get_curr_conversation_id=AsyncMock(return_value=None)),
                    get_config=lambda **_: {"provider_settings": {"default_personality": "test-persona"}}),
                session_topology=NS(accounts=lambda: [{"platform_id": "qq", "account_id": "456", "bot_id": "test-bot"}]),
                apply_bindings=AsyncMock(return_value={"saved": True, "started": False}))
            api = UpgradeSetup(controller)
            with patch("astrbot_host.upgrade_setup.available_personas", AsyncMock(return_value=["test-persona"])):
                plan, candidate = await api.plan()
                self.assertTrue(plan["can_prepare"], plan)
                self.assertEqual(candidate["multi_host"]["targets"][0]["participant_id"], "123")
                self.assertEqual(candidate["model"]["provider_id"], "test-model")
                self.assertIn("conversation.reply", candidate["multi_host"]["capabilities"])
                self.assertIn("memory.read", candidate["multi_host"]["capabilities"])
                self.assertIn("memory.reflect", candidate["multi_host"]["capabilities"])
                from companion_v2.execution.models import TaskError
                with self.assertRaises(TaskError):
                    await api.prepare("stale-review")
                controller.apply_bindings.assert_not_awaited()
                await api.prepare(plan["plan_token"])
                controller.apply_bindings.assert_awaited_once_with(candidate)
                controller.config["multi_host"]["accounts"] = [{"account_id": "456"}]
                existing, candidate = await api.plan()
                self.assertTrue(existing["configured"])
                self.assertIsNone(candidate)

    async def test_shared_role_upgrade_confirms_the_actual_private_persona_once(self):
        from astrbot_host.binding_store import read_binding
        from astrbot_host.upgrade_setup import UpgradeSetup
        from companion_v2.execution.models import TaskError

        with TemporaryDirectory() as directory:
            data = Path(directory)
            source = data / "plugin_data" / "astrbot_plugin_private_companion"
            source.mkdir(parents=True)
            (source / "companions.json").write_text("{}\n", encoding="utf-8")
            configuration = data / "config"
            configuration.mkdir()
            legacy = {"basic_config": {"target_user_ids": ["123"],
                "plugin_specific_persona_id": "private-persona", "enable_multi_persona_mode": False,
                "LLM_PROVIDER_ID": "test-model"},
                "group_observation_config": {"enable_group_companion": True,
                    "group_access_mode": "whitelist", "group_whitelist_ids": ["789"]}}
            (configuration / CONFIG_NAME).write_text(json.dumps(legacy), encoding="utf-8")
            choices = {"qq:FriendMessage:123": "private-persona", "qq:GroupMessage:789": "group-persona"}
            controller = NS(data_root=source / "companion-7", binding_root=data / "binding",
                config={"multi_host": {}},
                context=NS(get_provider_by_id=lambda _: object(),
                    conversation_manager=NS(get_curr_conversation_id=AsyncMock(return_value="same-cid"))),
                native_types=NS(persona_snapshot_lookup=AsyncMock(side_effect=lambda _ctx, umo, _cid: choices[umo]),
                    persona_selection_lookup=lambda _ctx, _umo, selected: selected),
                session_topology=NS(accounts=lambda: [{"platform_id": "qq", "account_id": "456", "bot_id": "bot"}]),
                apply_bindings=AsyncMock(return_value={"started": True}),
                confirm_binding=lambda: {"confirmed": True})
            api = UpgradeSetup(controller)
            with patch("astrbot_host.upgrade_setup.available_personas",
                       AsyncMock(return_value=["private-persona", "group-persona"])):
                plan, candidate = await api.plan()
                self.assertTrue(plan["can_prepare"])
                self.assertEqual({row["persona_id"] for row in candidate["multi_host"]["personas"]},
                                 {"private-persona", "group-persona"})
                self.assertEqual(len({row["role_id"] for row in candidate["multi_host"]["personas"]}), 1)
                choices["qq:FriendMessage:123"] = "group-persona"
                with self.assertRaises(TaskError) as changed:
                    await api.prepare(plan["plan_token"])
                self.assertEqual(changed.exception.code, "legacy_plan_stale")
                controller.apply_bindings.assert_not_awaited()
                choices["qq:FriendMessage:123"] = "private-persona"
                result = await api.prepare(plan["plan_token"])
                self.assertTrue(result["binding_confirmed"])
                self.assertEqual(read_binding(controller.binding_root).persona_id, "private-persona")
                controller.apply_bindings.assert_awaited_once_with(candidate)


class UpgradeArchiveResilienceTests(unittest.TestCase):
    """Damage inside ``.private-companion-upgrade`` must not stop the plugin loading.

    ``preserve_host_configuration`` runs at import time (``main.py``), so every
    raise from this module is an ``ImportError`` for the whole plugin — and
    ``star_manager`` keeps the archive on a failed load, so the next restart
    fails identically.  The verified-source guard still has to raise for the
    migration reader; only the import hook degrades.
    """

    LEGACY = {"basic_config": {"target_user_ids": ["123"], "LLM_PROVIDER_ID": "provider-a"}}

    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.config = Path(self._temporary.name) / "config"
        self.config.mkdir()
        self.live = self.config / CONFIG_NAME
        self.live.write_text(json.dumps(self.LEGACY), encoding="utf-8")
        self.archive = self.config / ".private-companion-upgrade"

    def _pointer(self):
        return json.loads((self.archive / "source.json").read_text(encoding="utf-8"))

    def _assert_recovers(self):
        recovered = preserve_before_schema(self.config)
        self.assertIsNotNone(recovered, "the import hook must still return a source")
        self.assertEqual(recovered.read_bytes(), self.live.read_bytes())
        self.assertEqual(self._pointer()["sha256"], recovered.stem)
        return recovered

    def test_deleted_archive_file_still_lets_the_hook_finish(self):
        preserve_before_schema(self.config).unlink()
        self._assert_recovers()

    def test_tampered_archive_file_still_lets_the_hook_finish(self):
        saved = preserve_before_schema(self.config)
        original = saved.read_bytes()
        saved.write_bytes(original[:-8] + b" " * 8)  # same length, different bytes
        self._assert_recovers()

    def test_truncated_pointer_still_lets_the_hook_finish(self):
        preserve_before_schema(self.config)
        (self.archive / "source.json").write_text('{"format": "x"', encoding="utf-8")
        self._assert_recovers()

    def test_non_hex_fingerprint_still_lets_the_hook_finish(self):
        preserve_before_schema(self.config)
        (self.archive / "source.json").write_text(
            json.dumps({"format": "private-companion.upgrade-source.v1", "sha256": "NOTHEX"}),
            encoding="utf-8")
        self._assert_recovers()

    def test_archive_that_is_no_longer_legacy_still_lets_the_hook_finish(self):
        preserve_before_schema(self.config)
        # A pointer that is internally consistent but no longer names a legacy
        # document: only the shape check can notice this one.
        raw = json.dumps({"performance": {}, "storage": {}}).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        (self.archive / (sha + ".json")).write_bytes(raw)
        (self.archive / "source.json").write_text(
            json.dumps({"format": "private-companion.upgrade-source.v1", "sha256": sha}),
            encoding="utf-8")
        self._assert_recovers()

    def test_pruned_live_config_with_a_broken_archive_returns_no_source(self):
        preserve_before_schema(self.config)
        (self.archive / "source.json").write_text('{"format": "x"', encoding="utf-8")
        self.live.write_text(json.dumps({"performance": {}, "storage": {}}), encoding="utf-8")
        with self.assertLogs("astrbot_host.upgrade_source", level="WARNING") as logs:
            self.assertIsNone(preserve_before_schema(self.config))
        self.assertIn(".private-companion-upgrade", "\n".join(logs.output))

    def test_unreadable_archive_is_reported_not_swallowed(self):
        preserve_before_schema(self.config)
        (self.archive / "source.json").write_text('{"format": "x"', encoding="utf-8")
        with self.assertLogs("astrbot_host.upgrade_source", level="WARNING") as logs:
            self.assertIsNone(usable_preserved_configuration_path(self.config))
        report = "\n".join(logs.output)
        self.assertIn("JSONDecodeError", report)
        self.assertIn(".private-companion-upgrade", report)

    def test_healthy_archive_is_never_rewritten(self):
        saved = preserve_before_schema(self.config)
        contents = saved.read_bytes()
        pointer = (self.archive / "source.json").read_bytes()
        self.assertEqual(preserve_before_schema(self.config), saved)
        self.assertEqual(saved.read_bytes(), contents)
        self.assertEqual((self.archive / "source.json").read_bytes(), pointer)

    def test_changed_archive_still_raises_for_the_migration_reader(self):
        saved = preserve_before_schema(self.config)
        saved.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            preserved_configuration_path(self.config)

    def test_audit_copy_mismatch_is_not_a_load_failure(self):
        saved = preserve_before_schema(self.config)
        # A later, defaulted snapshot of the live file: same role as the existing
        # test, except the audit destination already exists with other bytes.
        defaulted = {"basic_config": {"target_user_ids": [], "LLM_PROVIDER_ID": ""}}
        self.live.write_text(json.dumps(defaulted), encoding="utf-8")
        fingerprint = hashlib.sha256(self.live.read_bytes()).hexdigest()
        (self.archive / (fingerprint + ".json")).write_text("{}", encoding="utf-8")
        with self.assertLogs("astrbot_host.upgrade_source", level="WARNING"):
            self.assertEqual(preserve_before_schema(self.config), saved)
        self.assertEqual(saved.read_bytes(), json.dumps(self.LEGACY).encode("utf-8"))
