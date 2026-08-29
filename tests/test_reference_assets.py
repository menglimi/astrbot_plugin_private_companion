# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
import base64
import asyncio
from pathlib import Path
from urllib.parse import quote

from quart import Quart

from astrbot_plugin_private_companion.photo_reference_intent import ReferenceIntent
from astrbot_plugin_private_companion.photo_wardrobe_decision import analyze_photo_wardrobe
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.reference_assets import (
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)


class _ScopedReferenceHarness(ProactiveMessageMixin):
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.enable_photo_reference_image = True
        self.enable_bot_relationship_network = True
        self.bot_relationship_cards = []
        self.photo_reference_catalog = ()
        self.photo_reference_library = []
        self.photo_persona_reference_image_path = ""
        self.roleplay_knowledge_source_ids = []
        self.data = {"photo_reference_assets": [], "worldbook_member_profiles": {}}

    def _canonical_private_user_id(self, value: str) -> str:
        return str(value or "")


class _ReferenceAssetPagePlugin:
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.data = {"photo_reference_assets": [], "worldbook_member_profiles": {}}
        self._data_lock = asyncio.Lock()

    def _photo_reference_source_to_stable_path(self, source: str, *, stem: str = "reference") -> str:
        encoded = str(source).split(",", 1)[-1]
        raw = base64.b64decode(encoded)
        target_dir = Path(self.data_dir) / "photo_reference_images"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{stem}.png"
        target.write_bytes(raw)
        return str(target)

    def _photo_reference_local_path(self, source: str) -> str:
        path = Path(source)
        if not path.is_absolute():
            path = Path(self.data_dir) / path
        return str(path)

    def _save_data_sync(self, **_kwargs) -> None:
        return None

    def _format_timestamp_elapsed(self, _value: object) -> str:
        return "刚刚"


class ReferenceAssetNormalizationTests(unittest.TestCase):
    def test_scope_aliases_and_defaults_are_stable(self) -> None:
        self.assertEqual(normalize_reference_asset_scope("user"), "relation_user")
        self.assertEqual(normalize_reference_asset_scope("setting_role"), "relation_role")
        self.assertEqual(normalize_reference_owner_id("relation_role", "姐姐"), "role:姐姐")
        self.assertEqual(normalize_reference_owner_id("relation_role", "role:Sis"), "role:sis")
        asset = normalize_reference_asset(
            {"id": "a1", "scope": "knowledge", "owner_id": "kb:world", "path": "x.png"}
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset["reference_roles"], ["scene", "style"])
        self.assertIsNone(
            normalize_reference_asset(
                {"id": "bad", "scope": "knowledge", "owner_id": "not-a-source", "path": "x.png"}
            )
        )

    def test_role_asset_normalizes_to_a_stable_role_owner(self) -> None:
        asset = normalize_reference_asset(
            {"id": "role-1", "scope": "setting_role", "role_name": "姐姐", "path": "x.png"}
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset["scope"], "relation_role")
        self.assertEqual(asset["owner_id"], "role:姐姐")
        self.assertEqual(asset["role_name"], "姐姐")
        self.assertEqual(asset["reference_roles"], ["identity"])


class ScopedReferenceCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_assets_require_role_mention_or_group_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            role_path = root / "sister.png"
            role_path.write_bytes(b"sister")
            harness = _ScopedReferenceHarness(root)
            harness.bot_relationship_cards = ["姐姐 || 家人 || 长发"]
            harness.data["photo_reference_assets"] = [
                {
                    "id": "sister-ref",
                    "scope": "relation_role",
                    "owner_id": "role:姐姐",
                    "path": str(role_path),
                }
            ]

            ordinary = await harness._photo_reference_candidates_async(
                request_text="给我拍一张普通自拍",
            )
            self.assertEqual(ordinary, [])
            explicit = await harness._photo_reference_candidates_async(
                request_text="和姐姐合影",
            )
            self.assertEqual({item["id"] for item in explicit}, {"sister-ref"})
            self.assertTrue(explicit[0]["role_explicit_mention"])
            group = await harness._photo_reference_candidates_async(
                request_text="和朋友们一起拍大合照",
            )
            self.assertEqual({item["id"] for item in group}, {"sister-ref"})
            self.assertFalse(group[0]["role_explicit_mention"])

    async def test_named_role_group_plan_keeps_bot_and_role_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona_path = root / "persona.png"
            role_path = root / "sister.png"
            persona_path.write_bytes(b"persona")
            role_path.write_bytes(b"sister")
            harness = _ScopedReferenceHarness(root)
            harness.photo_reference_catalog = None
            harness.photo_persona_reference_image_path = str(persona_path)
            harness.bot_relationship_cards = ["姐姐 || 家人 || 长发"]
            harness.data["photo_reference_assets"] = [
                {
                    "id": "sister-ref",
                    "scope": "relation_role",
                    "owner_id": "role:姐姐",
                    "path": str(role_path),
                }
            ]
            plan = await harness._select_photo_reference_plan_async(
                "selfie",
                reference_intent=ReferenceIntent(
                    ("identity",), (), "ambiguous", 0.55, "workflow_default"
                ),
                wardrobe_intent=analyze_photo_wardrobe("我和姐姐拍一张合影"),
                request_text="我和姐姐拍一张合影",
            )
            kinds = {binding.candidate.get("kind") for binding in plan.bindings}
            self.assertEqual(kinds, {"persona", "relation_role"})
            self.assertEqual(plan.primary_reference_id, "persona")

    async def test_text2img_uses_named_role_reference_without_requester_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            role_path = root / "friend.png"
            role_path.write_bytes(b"friend")
            harness = _ScopedReferenceHarness(root)
            harness.bot_relationship_cards = ["阿青 || 好朋友 || 短发"]
            harness.data["photo_reference_assets"] = [
                {
                    "id": "friend-ref",
                    "scope": "relation_role",
                    "owner_id": "role:阿青",
                    "path": str(role_path),
                }
            ]
            plan = await harness._select_photo_reference_plan_async(
                "text2img",
                reference_intent=ReferenceIntent((), (), "ambiguous", 0.0, "none"),
                wardrobe_intent=analyze_photo_wardrobe("画一张阿青在公园里的头像"),
                request_text="画一张阿青在公园里的头像",
            )
            self.assertEqual(plan.primary_reference_id, "friend-ref")
            self.assertEqual(plan.bindings[0].candidate["kind"], "relation_role")

    async def test_relation_assets_are_scoped_to_requester_or_explicit_mention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            harness = _ScopedReferenceHarness(root)
            harness.data["worldbook_member_profiles"] = {
                "10001": {"user_id": "10001", "name": "小林", "aliases": ["林林"], "enabled": True},
                "10002": {"user_id": "10002", "name": "小夏", "aliases": [], "enabled": True},
            }
            harness.data["photo_reference_assets"] = [
                {"id": "user-a", "scope": "relation_user", "owner_id": "10001", "path": str(first), "title": "小林身份"},
                {"id": "user-b", "scope": "relation_user", "owner_id": "10002", "path": str(second), "title": "小夏身份"},
            ]

            requester = await harness._photo_reference_candidates_async(
                requester_user_id="10001",
                request_text="拍一张自然自拍",
            )
            self.assertEqual({item["id"] for item in requester}, {"user-a"})

            mentioned = await harness._photo_reference_candidates_async(
                request_text="和小夏在校园里合影，但只参考她的身份",
            )
            self.assertEqual({item["id"] for item in mentioned}, {"user-b"})

    async def test_knowledge_assets_require_selected_source_and_context_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            castle = root / "castle.png"
            other = root / "other.png"
            castle.write_bytes(b"castle")
            other.write_bytes(b"other")
            harness = _ScopedReferenceHarness(root)
            harness.roleplay_knowledge_source_ids = ["kb:world"]
            harness.data["photo_reference_assets"] = [
                {
                    "id": "castle-ref",
                    "scope": "knowledge",
                    "owner_id": "doc:world:castle",
                    "path": str(castle),
                    "title": "城堡大厅",
                    "tags": ["城堡", "大厅"],
                },
                {
                    "id": "other-ref",
                    "scope": "knowledge",
                    "owner_id": "kb:other",
                    "path": str(other),
                    "title": "海边",
                    "tags": ["海边"],
                },
            ]

            hit = await harness._photo_reference_candidates_async(
                request_text="在城堡大厅拍一张角色自拍",
            )
            self.assertEqual({item["id"] for item in hit}, {"castle-ref"})
            miss = await harness._photo_reference_candidates_async(
                request_text="普通街头自拍",
            )
            self.assertEqual(miss, [])

    async def test_workflow_default_can_use_a_knowledge_reference_when_it_is_the_only_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "castle.png"
            reference.write_bytes(b"castle")
            harness = _ScopedReferenceHarness(root)
            harness.roleplay_knowledge_source_ids = ["kb:world"]
            harness.data["photo_reference_assets"] = [
                {
                    "id": "castle-ref",
                    "scope": "knowledge",
                    "owner_id": "kb:world",
                    "path": str(reference),
                    "title": "城堡大厅",
                    "tags": ["城堡"],
                },
            ]
            plan = await harness._select_photo_reference_plan_async(
                "selfie",
                reference_intent=ReferenceIntent(("identity",), (), "ambiguous", 0.55, "workflow_default"),
                wardrobe_intent=analyze_photo_wardrobe("在城堡大厅拍一张角色自拍"),
                request_text="在城堡大厅拍一张角色自拍",
            )
            self.assertEqual(plan.primary_reference_id, "castle-ref")
            self.assertEqual(plan.bindings[0].candidate["kind"], "knowledge_reference")

    async def test_text2img_uses_scoped_relation_reference_without_loading_global_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "member.png"
            reference.write_bytes(b"member")
            harness = _ScopedReferenceHarness(root)
            harness.data["worldbook_member_profiles"] = {
                "10001": {"user_id": "10001", "name": "小林", "enabled": True},
            }
            harness.data["photo_reference_assets"] = [
                {
                    "id": "member-ref",
                    "scope": "relation_user",
                    "owner_id": "10001",
                    "path": str(reference),
                },
            ]
            plan = await harness._select_photo_reference_plan_async(
                "text2img",
                reference_intent=ReferenceIntent((), (), "ambiguous", 0.0, "none"),
                wardrobe_intent=analyze_photo_wardrobe("给小林画一张校园插画"),
                requester_user_id="10001",
                request_text="给小林画一张校园插画",
            )
            self.assertEqual(plan.primary_reference_id, "member-ref")


class ReferenceAssetPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_list_preview_and_delete_use_stable_scoped_storage(self) -> None:
        app = Quart(__name__)
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = _ReferenceAssetPagePlugin(Path(temp_dir))
            api = PrivateCompanionPageApi(plugin)
            api._worldbook_summary = lambda _data: {}
            image = base64.b64encode(b"small-image").decode("ascii")
            async with app.test_request_context(
                "/",
                method="POST",
                json={
                    "scope": "relation_user",
                    "owner_id": "10001",
                    "data_url": f"data:image/png;base64,{image}",
                    "title": "身份图",
                    "tags": "发型, 眼睛",
                },
            ):
                uploaded = await api.upload_reference_asset()
            self.assertTrue(uploaded["success"])
            asset_id = uploaded["data"]["asset"]["id"]
            self.assertTrue((Path(temp_dir) / "photo_reference_images").exists())

            async with app.test_request_context(f"/?scope=relation_user&owner_id=10001"):
                listed = await api.list_reference_assets()
            self.assertEqual(listed["data"]["total"], 1)
            self.assertEqual(listed["data"]["items"][0]["id"], asset_id)

            async with app.test_request_context(f"/?id={asset_id}"):
                preview = await api.get_reference_asset_image_data()
            self.assertTrue(preview["success"])
            self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

            async with app.test_request_context("/", method="POST", json={"id": asset_id}):
                deleted = await api.delete_reference_asset()
            self.assertTrue(deleted["success"])
            self.assertEqual(plugin.data["photo_reference_assets"], [])

    async def test_role_upload_accepts_role_name_and_lists_role_scope(self) -> None:
        app = Quart(__name__)
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = _ReferenceAssetPagePlugin(Path(temp_dir))
            api = PrivateCompanionPageApi(plugin)
            image = base64.b64encode(b"role-image").decode("ascii")
            async with app.test_request_context(
                "/",
                method="POST",
                json={
                    "scope": "setting_role",
                    "role_name": "姐姐",
                    "data_url": f"data:image/png;base64,{image}",
                    "title": "姐姐参考图",
                },
            ):
                uploaded = await api.upload_reference_asset()
            self.assertTrue(uploaded["success"])
            self.assertEqual(uploaded["data"]["asset"]["scope"], "relation_role")
            self.assertEqual(uploaded["data"]["asset"]["owner_id"], "role:姐姐")
            async with app.test_request_context(
                f"/?scope=relation_role&role_name={quote('姐姐')}"
            ):
                listed = await api.list_reference_assets()
            self.assertEqual(listed["data"]["total"], 1)
            self.assertEqual(listed["data"]["items"][0]["role_name"], "姐姐")
            plugin.bot_relationship_cards = ["姐姐 || 家人 || 长发"]
            summary = api._worldbook_summary(plugin.data)
            self.assertEqual(summary["relationship_role_reference_count"], 1)
            self.assertEqual(summary["relationship_roles"][0]["owner_id"], "role:姐姐")
            self.assertEqual(summary["relationship_roles"][0]["reference_asset_count"], 1)


if __name__ == "__main__":
    unittest.main()
