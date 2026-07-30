# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.photo_reference_catalog import CATALOG_VERSION


ROOT = Path(__file__).resolve().parents[1]


class _PhotoReferencePagePlugin:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = str(data_dir)
        self.enable_photo_reference_image = True
        self.photo_persona_reference_image_path = ""
        self.photo_reference_library: list[str] = []

    def _photo_reference_library_entries(self) -> list[dict[str, str]]:
        entries = []
        for raw in self.photo_reference_library:
            source, separator, note = str(raw).partition(" || ")
            entries.append({"source": source.strip(), "note": note.strip() if separator else ""})
        return entries

    @staticmethod
    def _photo_reference_local_path(source: str) -> str:
        return source


class PhotoReferenceLibraryPageApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = Quart(__name__)

    async def test_list_reports_persona_library_order_and_availability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona = root / "persona.png"
            home = root / "home look.jpg"
            persona.write_bytes(b"persona")
            home.write_bytes(b"home")
            plugin = _PhotoReferencePagePlugin(root)
            plugin.photo_persona_reference_image_path = str(persona)
            plugin.photo_reference_library = [
                f"{home} || 居家服，在家时使用",
                "https://example.com/formal.webp || 礼服，正式场合",
                f"{root / 'missing.png'} || 已失效",
            ]
            api = PrivateCompanionPageApi(plugin)

            result = await api.list_photo_references()

            self.assertTrue(result["success"])
            payload = result["data"]
            self.assertEqual(payload["limit"], 24)
            self.assertEqual(payload["total"], 3)
            self.assertEqual(payload["available"], 2)
            self.assertEqual(payload["persona"]["source"], str(persona))
            self.assertTrue(payload["persona"]["preview_endpoint"])
            self.assertEqual([item["note"] for item in payload["items"]], [
                "居家服，在家时使用",
                "礼服，正式场合",
                "已失效",
            ])
            self.assertTrue(payload["items"][0]["available"])
            self.assertEqual(payload["items"][1]["direct_url"], "https://example.com/formal.webp")
            self.assertFalse(payload["items"][2]["available"])

    async def test_list_recovers_saved_catalog_when_runtime_catalog_is_stale_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona = root / "persona.png"
            persona.write_bytes(b"persona")
            raw_catalog = [{
                "id": "persona",
                "kind": "persona",
                "source": str(persona),
                "note": "基础人物身份和外貌参考",
                "reference_roles": ["identity"],
            }]
            plugin = _PhotoReferencePagePlugin(root)
            plugin.config = {
                "photo_reference_catalog": raw_catalog,
                "photo_reference_catalog_version": CATALOG_VERSION,
                "photo_reference_catalog_user_cleared": False,
            }
            plugin.photo_reference_catalog = ()
            plugin.photo_reference_catalog_version = CATALOG_VERSION
            plugin.photo_reference_catalog_read_only = False
            api = PrivateCompanionPageApi(plugin)

            result = await api.list_photo_references()

            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["persona"]["source"], str(persona))
            self.assertTrue(result["data"]["persona"]["available"])
            self.assertEqual(len(plugin.photo_reference_catalog), 1)
            self.assertEqual(plugin.photo_reference_catalog[0].kind, "persona")

    async def test_list_does_not_restore_catalog_after_explicit_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = _PhotoReferencePagePlugin(root)
            plugin.config = {
                "photo_reference_catalog": [{
                    "id": "persona",
                    "kind": "persona",
                    "source": str(root / "stale-persona.png"),
                    "note": "过期兼容副本",
                    "reference_roles": ["identity"],
                }],
                "photo_reference_catalog_version": CATALOG_VERSION,
                "photo_reference_catalog_user_cleared": True,
            }
            plugin.photo_reference_catalog = ()
            plugin.photo_reference_catalog_version = CATALOG_VERSION
            plugin.photo_reference_catalog_read_only = False
            api = PrivateCompanionPageApi(plugin)

            result = await api.list_photo_references()

            self.assertTrue(result["success"])
            self.assertIsNone(result["data"]["persona"])
            self.assertEqual(result["data"]["items"], [])
            self.assertEqual(plugin.photo_reference_catalog, ())

    async def test_image_data_only_reads_currently_configured_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "configured.png"
            outside = root / "outside.png"
            configured.write_bytes(b"configured-image")
            outside.write_bytes(b"outside-image")
            plugin = _PhotoReferencePagePlugin(root)
            plugin.photo_reference_library = [f"{configured} || 已配置"]
            api = PrivateCompanionPageApi(plugin)
            item_id = api._photo_reference_page_id("library", str(configured))
            forged_id = api._photo_reference_page_id("library", str(outside))

            async with self.app.test_request_context(f"/?id={item_id}"):
                result = await api.get_photo_reference_image_data()
            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["mime"], "image/png")
            self.assertTrue(result["data"]["data_url"].startswith("data:image/png;base64,"))

            async with self.app.test_request_context(f"/?id={forged_id}"):
                rejected = await api.get_photo_reference_image_data()
            self.assertFalse(rejected["success"])
            self.assertIn("不在当前配置", rejected["error"])

            plugin.photo_reference_library = []
            async with self.app.test_request_context(f"/?id={item_id}"):
                removed = await api.get_photo_reference_image_data()
            self.assertFalse(removed["success"])

    async def test_remote_reference_is_not_downloaded_by_local_preview_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = _PhotoReferencePagePlugin(Path(temp_dir))
            source = "https://example.com/reference.png"
            plugin.photo_reference_library = [source]
            api = PrivateCompanionPageApi(plugin)
            item_id = api._photo_reference_page_id("library", source)

            async with self.app.test_request_context(f"/?id={item_id}"):
                result = await api.get_photo_reference_image_data()

            self.assertFalse(result["success"])
            self.assertIn("原始地址", result["error"])

    async def test_local_preview_rejects_oversized_file_before_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "large.png"
            configured.write_bytes(b"oversized")
            plugin = _PhotoReferencePagePlugin(root)
            plugin.photo_reference_library = [str(configured)]
            api = PrivateCompanionPageApi(plugin)
            item_id = api._photo_reference_page_id("library", str(configured))

            with patch("astrbot_plugin_private_companion.page_api.PHOTO_REFERENCE_PREVIEW_MAX_BYTES", 4):
                async with self.app.test_request_context(f"/?id={item_id}"):
                    result = await api.get_photo_reference_image_data()

            self.assertFalse(result["success"])
            self.assertIn("文件过大", result["error"])

    def test_page_normalizer_preserves_multiline_note_false_and_unknown_metadata(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = object()
        api._schema_key_index_cache = None
        value = json.dumps(
            [
                {
                    "path": "C:/references/home  look.png",
                    "note": "居家服\n睡前使用",
                    "reference_roles": "identity, outfit",
                    "outfit_lock_default": "false",
                    "scene_categories": "home, bedroom",
                    "custom_metadata": {"source": "migration"},
                }
            ],
            ensure_ascii=False,
        )

        normalized = api._normalize_setting_value("photo_reference_library", value)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["path"], "C:/references/home  look.png")
        self.assertEqual(normalized[0]["note"], "居家服\n睡前使用")
        self.assertEqual(normalized[0]["reference_roles"], ["identity", "outfit"])
        self.assertEqual(normalized[0]["scene_categories"], ["home", "bedroom"])
        self.assertIs(normalized[0]["outfit_lock_default"], False)
        self.assertEqual(normalized[0]["custom_metadata"], {"source": "migration"})

    def test_legacy_metadata_suffix_is_promoted_to_structured_item(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = object()
        api._schema_key_index_cache = None
        value = (
            "https://example.com/sleepwear.png || 睡衣参考 || "
            '{"outfit_category":"sleepwear","outfit_lock_default":false,'
            '"preferred_preset":"居家睡衣","custom_field":"kept"}'
        )

        normalized = api._normalize_setting_value("photo_reference_library", value)

        self.assertEqual(len(normalized), 1)
        self.assertIsInstance(normalized[0], dict)
        self.assertEqual(normalized[0]["outfit_category"], "sleepwear")
        self.assertIs(normalized[0]["outfit_lock_default"], False)
        self.assertEqual(normalized[0]["preferred_preset"], "居家睡衣")
        self.assertEqual(normalized[0]["custom_field"], "kept")

    def test_empty_lock_value_keeps_automatic_inference(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = object()
        api._schema_key_index_cache = None

        normalized = api._normalize_setting_value(
            "photo_reference_library",
            [{"path": "C:/references/auto.png", "outfit_lock_default": "   "}],
        )

        self.assertEqual(len(normalized), 1)
        self.assertNotIn("outfit_lock_default", normalized[0])

    async def test_runtime_sync_preserves_structured_metadata_and_explicit_false_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = _PhotoReferencePagePlugin(Path(temp_dir))
            plugin.config = {
                "photo_reference_library": [
                    {
                        "source": "C:/references/identity-only.png",
                        "note": "只负责人物身份",
                        "reference_roles": ["identity"],
                        "outfit_category": "",
                        "outfit_lock_default": False,
                        "scene_categories": ["home"],
                    }
                ]
            }
            api = PrivateCompanionPageApi(plugin)

            api._sync_photo_generation_runtime_config()

            self.assertEqual(len(plugin.photo_reference_library), 1)
            item = plugin.photo_reference_library[0]
            self.assertIsInstance(item, dict)
            self.assertEqual(item["source"], "C:/references/identity-only.png")
            self.assertEqual(item["reference_roles"], ["identity"])
            self.assertIs(item["outfit_lock_default"], False)
            self.assertEqual(item["scene_categories"], ["home"])

    async def test_generation_chain_test_preserves_long_reference_path_and_double_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated.png"
            generated.write_bytes(b"generated")
            reference = "C:/reference/" + ("nested folder/" * 22) + "persona  original.png"
            captured: dict[str, str] = {}
            plugin = _PhotoReferencePagePlugin(root)
            plugin.photo_persona_reference_image_path = reference

            plugin._photo_persona_reference_image_path = lambda: reference

            async def reference_for_kind(*_args, **_kwargs):
                return reference

            async def generate(**kwargs):
                captured.update(kwargs)
                return "test-backend", str(generated), "ok；已使用参考图"

            plugin._photo_persona_reference_image_for_kind_async = reference_for_kind
            plugin._generate_photo_image = generate
            api = PrivateCompanionPageApi(plugin)
            api._image_api_runtime_lock = lambda: asyncio.Lock()
            api._sync_photo_generation_runtime_config = lambda: None
            api._image_generation_timeout_diagnostics = lambda **_kwargs: {
                "test_timeout_seconds": 60,
                "estimated_timeout_seconds": 30,
                "warnings": [],
            }

            result = await api._run_image_generation_chain_test(
                {"workflow_kind": "selfie", "prompt": "保持人物身份生成自拍"}
            )

        self.assertGreater(len(reference), 260)
        self.assertEqual(captured["reference_image_path"], reference)
        self.assertEqual(result["reference_image"], reference)
        self.assertIn("persona  original.png", result["reference_image"])


class PhotoReferenceLibraryPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        page_dir = ROOT / "pages" / "陪伴面板"
        cls.script = (page_dir / "app.js").read_text(encoding="utf-8")
        cls.styles = (page_dir / "app.css").read_text(encoding="utf-8")
        cls.html = (page_dir / "index.html").read_text(encoding="utf-8")
        cls.api = (ROOT / "page_api.py").read_text(encoding="utf-8")

    def test_photo_feature_opens_a_dedicated_third_level_manager(self) -> None:
        self.assertIn('data-photo-reference-open', self.script)
        self.assertIn('data-photo-reference-manager', self.script)
        self.assertIn('data-photo-reference-back', self.script)
        self.assertIn('/ 参考图库管理', self.script)
        self.assertIn('state.featureDetailSubpage === "photo_reference_library"', self.script)
        self.assertIn('state.featureDetailSubpage = "";', self.script)

    def test_manager_replaces_raw_library_textarea_with_structured_controls(self) -> None:
        self.assertIn('name === "photo_reference_catalog" ? photoReferenceManagerLaunchControl(value)', self.script)
        self.assertIn('name === "bot_relationship_cards" ? relationshipCardEditorHtml(value)', self.script)
        self.assertIn('data-feature-param="photo_reference_catalog" hidden', self.script)
        for marker in (
            'data-photo-reference-add-form',
            'data-photo-reference-source',
            'data-photo-reference-note',
            'data-photo-reference-move',
            'data-photo-reference-delete',
            'data-photo-reference-filter',
            'data-photo-reference-save',
            'data-photo-reference-roles',
            'data-photo-reference-outfit-category',
            'data-photo-reference-outfit-lock',
            'data-photo-reference-scenes',
            'data-photo-reference-preferred-preset',
        ):
            self.assertIn(marker, self.script)
        self.assertIn('再次点击确认删除这张参考图', self.script)
        self.assertIn('"确认")) return;', self.script)

    def test_manager_uses_bridge_safe_preview_and_existing_settings_save(self) -> None:
        self.assertIn('fetchJson("/photo_reference/list")', self.script)
        self.assertIn('data-preview-endpoint', self.script)
        self.assertIn('saveCurrentFeatureDetail(event.currentTarget, "已保存参考图库")', self.script)
        self.assertIn('("/photo_reference/list", self.list_photo_references', self.api)
        self.assertIn('("/photo_reference/image_data", self.get_photo_reference_image_data', self.api)
        self.assertIn('参考图不存在或已不在当前配置中', self.api)

    def test_manager_distinguishes_load_failure_and_hydrates_saved_status(self) -> None:
        self.assertIn('function hydratePhotoReferenceDraftFromStatus(status)', self.script)
        self.assertIn('hydratePhotoReferenceDraftFromStatus(status);', self.script)
        self.assertNotIn('state.photoReferenceLibraryStatus = { items: [], persona: null };', self.script)
        self.assertIn('暂时无法读取图库', self.script)
        self.assertIn('读取失败，未判定为空', self.script)
        self.assertIn('基础人设已设置', self.script)
        self.assertIn('暂无附加参考图', self.script)

    def test_manager_has_responsive_stable_layout_and_fresh_assets(self) -> None:
        self.assertIn('.photo-reference-grid', self.styles)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr));', self.styles)
        self.assertIn('@media (max-width: 520px)', self.styles)
        self.assertIn('.photo-reference-manager[hidden]', self.styles)
        self.assertIn('./app.css?v=20260730-panel-asset-guard-v1', self.html)
        self.assertIn('./app.js?v=20260730-panel-asset-guard-v1', self.html)

    def test_structured_metadata_round_trip_keeps_explicit_false_lock(self) -> None:
        self.assertIn('function normalizePhotoReferenceMetadataBoolean(value)', self.script)
        self.assertIn('["0", "false", "no", "off", "否", "关闭", "不锁定"]', self.script)
        self.assertIn('return JSON.stringify(payload);', self.script)
        self.assertIn('function canonicalPhotoReference(item, kind)', self.script)
        self.assertIn('id: kind === "persona" ? "persona" : String(item?.id || newPhotoReferenceId())', self.script)
        self.assertIn('const metadata = { ...(rawItem && typeof rawItem === "object" ? rawItem : {}) };', self.script)


if __name__ == "__main__":
    unittest.main()
