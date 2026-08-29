from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

class _Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    quart = types.ModuleType("quart")
    api.logger = _Logger()
    event.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event.MessageChain = type("MessageChain", (), {})
    astrbot_path.get_astrbot_data_path = lambda: tempfile.gettempdir()
    quart.request = types.SimpleNamespace()

    async def send_file(*_args, **_kwargs):
        return None

    quart.send_file = send_file
    astrbot.api = api
    astrbot.core = core
    core.utils = utils
    utils.astrbot_path = astrbot_path
    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "quart": quart,
    }


with mock.patch.dict(sys.modules, _astrbot_stubs()):
    PLUGIN_ROOT = Path(__file__).resolve().parents[1]
    PACKAGE_NAME = "astrbot_plugin_private_companion"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(PLUGIN_ROOT)]
        package.__package__ = PACKAGE_NAME
        sys.modules[PACKAGE_NAME] = package

    from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
    from astrbot_plugin_private_companion.helpers import _safe_int, _set_into_config, _single_line
    from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
    from astrbot_plugin_private_companion.photo_reference_catalog import (
        CATALOG_VERSION,
        CatalogValidationError,
        load_catalog,
        project_reference_candidate,
        validate_and_serialize,
    )


def _load_startup_background_maintenance():
    tree = ast.parse((PLUGIN_ROOT / "main.py").read_text(encoding="utf-8"), filename="main.py")
    plugin_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    method = next(
        node
        for node in plugin_class.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_startup_background_maintenance"
    )
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "logger": _Logger(),
        "CATALOG_VERSION": CATALOG_VERSION,
        "_safe_int": _safe_int,
        "_set_into_config": _set_into_config,
        "_single_line": _single_line,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "main.py", "exec"), namespace)
    return namespace["_run_startup_background_maintenance"]


_run_startup_background_maintenance = _load_startup_background_maintenance()


class _CommandHarness(CommandHandlersMixin):
    def __init__(self, references) -> None:
        self.photo_reference_catalog = tuple(references)
        self.photo_reference_catalog_version = CATALOG_VERSION
        self.photo_reference_catalog_read_only = False
        self.config = {
            "photo_reference_catalog": [],
            "photo_reference_catalog_version": CATALOG_VERSION,
        }

    @staticmethod
    def _photo_generation_scene_presets():
        return {"日常穿搭": "prompt", "居家睡衣": "prompt", "舞台人像": "prompt"}

    def _photo_reference_library_entries(self):
        return [
            project_reference_candidate(item)
            for item in self.photo_reference_catalog
            if item.kind == "library"
        ]

    async def _save_config_if_possible(self) -> bool:
        return True

    async def _photo_reference_images_from_command_context(self, _event, _user_id, *, limit=12):
        return [("C:/images/new-sleep.png", "新图")][:limit], True

    @staticmethod
    def _photo_reference_local_path(source):
        return source


class PhotoReferenceCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_catalog_blocks_automatic_overwrite(self) -> None:
        loaded = load_catalog(
            [{
                "id": "library-read-only",
                "kind": "library",
                "source": "C:/images/read-only.png",
                "reference_roles": ["identity"],
                "outfit_lock_default": False,
            }],
            catalog_version=CATALOG_VERSION,
        )
        harness = _CommandHarness(loaded.references)
        harness.photo_reference_catalog_read_only = True
        previous_config = dict(harness.config)

        saved = await harness._set_photo_reference_catalog_config(())

        self.assertFalse(saved)
        self.assertEqual(harness.photo_reference_catalog, loaded.references)
        self.assertEqual(harness.config, previous_config)

    async def test_command_list_and_preview_include_complete_metadata_summary(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "library-summary",
                    "kind": "library",
                    "source": "C:/images/summary.png",
                    "note": "摘要图",
                    "reference_roles": ["identity", "outfit", "scene"],
                    "outfit_category": "custom:舞台服",
                    "outfit_lock_default": True,
                    "scene_categories": ["custom:舞台"],
                    "time_categories": ["night"],
                    "preferred_preset": "舞台人像",
                    "metadata_source": "configured",
                }
            ],
            catalog_version=1,
            preset_names={"舞台人像"},
        )
        harness = _CommandHarness(loaded.references)

        listing, _ = await harness._photo_reference_library_command_payload(None, "user", "列表")
        preview, path = await harness._photo_reference_library_command_payload(None, "user", "预览 1")

        for text in (listing, preview):
            self.assertIn("职责=identity,outfit,scene", text)
            self.assertIn("服装=custom:舞台服", text)
            self.assertIn("锁定=是", text)
            self.assertIn("场景=custom:舞台", text)
            self.assertIn("时间=night", text)
            self.assertIn("预设=舞台人像", text)
        self.assertEqual(path, "C:/images/summary.png")

    async def test_command_add_and_delete_preserve_unrelated_metadata(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "library-remove",
                    "kind": "library",
                    "source": "C:/images/remove.png",
                    "note": "待删除",
                    "reference_roles": ["identity"],
                    "outfit_lock_default": False,
                },
                {
                    "id": "library-keep",
                    "kind": "library",
                    "source": "C:/images/keep.png",
                    "note": "保留完整元数据",
                    "reference_roles": ["identity", "style"],
                    "outfit_category": "custom:礼裙",
                    "outfit_lock_default": False,
                    "scene_categories": ["custom:舞台"],
                    "preferred_preset": "舞台人像",
                    "metadata_source": "configured",
                },
            ],
            catalog_version=CATALOG_VERSION,
            preset_names={"日常穿搭", "居家睡衣", "舞台人像"},
        )
        harness = _CommandHarness(loaded.references)
        kept = loaded.references[1]

        message, _ = await harness._photo_reference_library_command_payload(None, "user", "删除 1")
        self.assertIn("已从参考图库删除", message)
        self.assertEqual(harness.photo_reference_catalog, (kept,))

        message, _ = await harness._photo_reference_library_command_payload(
            None,
            "user",
            "添加 睡衣，在卧室和睡前使用",
        )
        self.assertIn("已向参考图库添加 1 张图片", message)
        self.assertEqual(harness.photo_reference_catalog[0], kept)
        added = harness.photo_reference_catalog[1]
        self.assertTrue(added.id.startswith("library_"))
        self.assertEqual(added.outfit_category, "sleepwear")
        self.assertEqual(added.reference_roles, ("identity", "outfit"))
        self.assertTrue(added.outfit_lock_default)

    async def test_command_role_shortcut_updates_only_selected_reference(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "library-change",
                    "kind": "library",
                    "source": "C:/images/change.png",
                    "reference_roles": ["identity", "outfit"],
                    "outfit_category": "sleepwear",
                    "outfit_lock_default": True,
                },
                {
                    "id": "library-keep",
                    "kind": "library",
                    "source": "C:/images/keep.png",
                    "reference_roles": ["identity", "style"],
                    "outfit_lock_default": False,
                },
            ],
            catalog_version=CATALOG_VERSION,
        )
        harness = _CommandHarness(loaded.references)

        message, _ = await harness._photo_reference_library_command_payload(
            None,
            "user",
            "设置 1 仅姿势",
        )

        self.assertIn("pose", message)
        self.assertEqual(harness.photo_reference_catalog[0].reference_roles, ("pose",))
        self.assertFalse(harness.photo_reference_catalog[0].outfit_lock_default)
        self.assertEqual(harness.photo_reference_catalog[1], loaded.references[1])


class _PagePlugin:
    def __init__(self, references) -> None:
        self.photo_reference_catalog = tuple(references)
        self.photo_reference_catalog_version = CATALOG_VERSION
        self.photo_reference_catalog_read_only = True
        self.config = {
            "photo_reference_catalog": [],
            "photo_reference_catalog_version": CATALOG_VERSION,
        }

    @staticmethod
    def _photo_generation_scene_presets():
        return {"日常穿搭": "prompt", "舞台人像": "prompt"}

    @staticmethod
    def _photo_reference_local_path(source):
        return source


class PhotoReferencePageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "persona",
                    "kind": "persona",
                    "source": "C:/images/persona.png",
                    "note": "基础人设",
                    "reference_roles": ["identity", "style"],
                    "outfit_category": "",
                    "outfit_lock_default": False,
                    "scene_categories": ["home"],
                    "preferred_preset": "",
                    "metadata_source": "configured",
                },
                {
                    "id": "library-first",
                    "kind": "library",
                    "source": "C:/images/first.png",
                    "note": "日常",
                    "reference_roles": ["identity", "outfit"],
                    "outfit_category": "daily_outfit",
                    "outfit_lock_default": True,
                    "scene_categories": ["outdoor"],
                    "preferred_preset": "日常穿搭",
                    "metadata_source": "configured",
                },
                {
                    "id": "library-second",
                    "kind": "library",
                    "source": "C:/images/second.png",
                    "note": "舞台",
                    "reference_roles": ["identity", "style"],
                    "outfit_category": "custom:礼裙",
                    "outfit_lock_default": False,
                    "scene_categories": ["custom:舞台"],
                    "preferred_preset": "舞台人像",
                    "metadata_source": "configured",
                },
            ],
            catalog_version=CATALOG_VERSION,
            preset_names={"日常穿搭", "舞台人像"},
        )
        self.plugin = _PagePlugin(loaded.references)
        self.api = PrivateCompanionPageApi(self.plugin)

    def test_page_reorder_preserves_ids_metadata_and_clears_read_only(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "persona",
                    "kind": "persona",
                    "source": "C:/images/persona.png",
                    "note": "基础人设",
                    "reference_roles": ["identity", "style"],
                    "outfit_lock_default": False,
                },
                {
                    "id": "library-first",
                    "kind": "library",
                    "source": "C:/images/first.png",
                    "note": "日常",
                    "reference_roles": ["identity", "outfit"],
                    "outfit_category": "daily_outfit",
                    "outfit_lock_default": True,
                    "scene_categories": ["outdoor"],
                    "preferred_preset": "日常穿搭",
                },
                {
                    "id": "library-second",
                    "kind": "library",
                    "source": "C:/images/second.png",
                    "note": "舞台",
                    "reference_roles": ["identity", "style"],
                    "outfit_category": "custom:礼裙",
                    "outfit_lock_default": False,
                    "scene_categories": ["custom:舞台"],
                    "preferred_preset": "舞台人像",
                },
            ],
            catalog_version=CATALOG_VERSION,
            preset_names={"日常穿搭", "舞台人像"},
        )
        plugin = _PagePlugin(loaded.references)
        api = PrivateCompanionPageApi(plugin)
        original = validate_and_serialize(
            plugin.photo_reference_catalog,
            preset_names=plugin._photo_generation_scene_presets().keys(),
        )
        reordered = [original[0], original[2], original[1]]

        normalized = api._normalize_setting_value("photo_reference_catalog", reordered)
        api._apply_config_value("photo_reference_catalog", normalized)

        self.assertEqual(
            [item.id for item in plugin.photo_reference_catalog],
            ["persona", "library-second", "library-first"],
        )
        self.assertEqual(plugin.photo_reference_catalog[1].scene_categories, ("custom:舞台",))
        self.assertEqual(plugin.photo_reference_catalog[2].outfit_lock_default, True)
        self.assertEqual(plugin.config["photo_reference_catalog"], normalized)
        self.assertEqual(plugin.config["photo_reference_catalog_version"], CATALOG_VERSION)
        self.assertFalse(plugin.photo_reference_catalog_read_only)

    def test_page_save_and_reorder_preserve_ids_and_metadata(self) -> None:
        self.plugin.photo_reference_catalog_read_only = True
        original = validate_and_serialize(
            self.plugin.photo_reference_catalog,
            preset_names=self.plugin._photo_generation_scene_presets().keys(),
        )
        reordered = [original[0], original[2], original[1]]

        normalized = self.api._normalize_setting_value("photo_reference_catalog", reordered)
        self.api._apply_config_value("photo_reference_catalog", normalized)

        self.assertEqual(
            [item.id for item in self.plugin.photo_reference_catalog],
            ["persona", "library-second", "library-first"],
        )
        by_id = {item["id"]: item for item in self.api._photo_reference_page_items()}
        self.assertEqual(by_id["persona"]["reference_roles"], ["identity", "style"])
        self.assertEqual(by_id["library-second"]["outfit_category"], "custom:礼裙")
        self.assertEqual(by_id["library-second"]["scene_categories"], ["custom:舞台"])
        self.assertEqual(by_id["library-second"]["preferred_preset"], "舞台人像")
        self.assertEqual(by_id["library-first"]["outfit_lock_default"], True)
        self.assertEqual(self.plugin.config["photo_reference_catalog"], normalized)
        self.assertEqual(self.plugin.config["photo_reference_catalog_version"], CATALOG_VERSION)
        self.assertFalse(self.plugin.photo_reference_catalog_read_only)

    def test_page_save_rejects_missing_preset_with_field_error(self) -> None:
        invalid = validate_and_serialize(
            self.plugin.photo_reference_catalog,
            preset_names=self.plugin._photo_generation_scene_presets().keys(),
        )
        invalid[1]["preferred_preset"] = "已删除预设"

        with self.assertRaises(CatalogValidationError) as raised:
            self.api._normalize_setting_value("photo_reference_catalog", invalid)

        self.assertEqual(
            raised.exception.errors,
            {"items.1.preferred_preset": ["场景预设不存在：已删除预设"]},
        )

    def test_current_empty_catalog_does_not_restore_legacy_page_items(self) -> None:
        plugin = _PagePlugin(())
        plugin.photo_persona_reference_image_path = "C:/images/legacy-persona.png"
        plugin.photo_reference_library = ["C:/images/legacy-library.png"]
        plugin.photo_reference_catalog_user_cleared = False
        api = PrivateCompanionPageApi(plugin)

        self.assertEqual(api._photo_reference_page_items(), [])

    def test_page_lists_complete_role_shortcuts(self) -> None:
        payload = asyncio.run(self.api.list_photo_references())
        shortcuts = payload["data"]["options"]["role_shortcuts"]

        self.assertEqual(
            [item["value"] for item in shortcuts],
            [["identity"], ["outfit"], ["pose"], ["scene"], ["style"]],
        )


class _StartupMigrationHarness:
    def __init__(self, save_results) -> None:
        self._startup_photo_reference_catalog_migration_pending = True
        self._startup_config_migration_changes = 1
        self.photo_reference_catalog = (object(), object())
        self.photo_reference_catalog_version = 0
        self.photo_reference_catalog_read_only = True
        self.config = {
            "photo_reference_catalog": [{"id": "persona"}],
            "photo_reference_catalog_version": 0,
        }
        self._save_results = list(save_results)
        self.saved_snapshots = []
        self._data_lock = asyncio.Lock()

    async def _save_config_if_possible(self) -> bool:
        self.saved_snapshots.append(deepcopy(self.config))
        return self._save_results.pop(0)

    async def _apply_sqlite_wal_optimizations(self) -> None:
        return None

    @staticmethod
    def _run_startup_data_maintenance_locked() -> bool:
        return False

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        raise AssertionError("没有数据变更时不应保存运行数据")


class PhotoReferenceStartupMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_is_saved_before_version_marker(self) -> None:
        harness = _StartupMigrationHarness([True, True])

        await _run_startup_background_maintenance(harness)

        self.assertEqual(len(harness.saved_snapshots), 2)
        self.assertEqual(harness.saved_snapshots[0]["photo_reference_catalog_version"], 0)
        self.assertEqual(harness.saved_snapshots[1]["photo_reference_catalog_version"], CATALOG_VERSION)
        self.assertFalse(harness._startup_photo_reference_catalog_migration_pending)
        self.assertFalse(harness.photo_reference_catalog_read_only)

    async def test_failed_catalog_save_does_not_write_version_marker(self) -> None:
        harness = _StartupMigrationHarness([False])

        await _run_startup_background_maintenance(harness)

        self.assertEqual(len(harness.saved_snapshots), 1)
        self.assertEqual(harness.config["photo_reference_catalog_version"], 0)
        self.assertTrue(harness._startup_photo_reference_catalog_migration_pending)
        self.assertTrue(harness.photo_reference_catalog_read_only)

if __name__ == "__main__":
    unittest.main()
