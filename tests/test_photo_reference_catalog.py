from __future__ import annotations

import json
import importlib.util
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
if PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load plugin package for tests")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)

from astrbot_plugin_private_companion.photo_reference_catalog import (
    CatalogValidationError,
    add_reference,
    build_daily_outfit_reference,
    delete_reference,
    load_catalog,
    project_reference_candidate,
    validate_and_serialize,
)


class PhotoReferenceCatalogTests(unittest.TestCase):
    def test_time_categories_round_trip_with_backward_compatible_default(self) -> None:
        serialized = validate_and_serialize(
            [
                {
                    "id": "library-night",
                    "kind": "library",
                    "source": "C:/images/night.png",
                    "reference_roles": ["identity", "scene"],
                    "outfit_lock_default": False,
                    "scene_categories": ["outdoor"],
                    "time_categories": ["night", "custom:blue_hour"],
                },
                {
                    "id": "library-any-time",
                    "kind": "library",
                    "source": "C:/images/any.png",
                    "reference_roles": ["identity"],
                    "outfit_lock_default": False,
                },
            ]
        )
        loaded = load_catalog(serialized, catalog_version=1)

        self.assertEqual(
            loaded.references[0].time_categories,
            ("night", "custom:blue_hour"),
        )
        self.assertEqual(loaded.references[1].time_categories, ())
        self.assertEqual(serialized[0]["time_categories"], ["night", "custom:blue_hour"])

    def test_unversioned_legacy_config_is_migrated_to_canonical_references(self) -> None:
        loaded = load_catalog(
            raw_catalog=[],
            catalog_version=0,
            legacy_persona="C:/images/persona.png",
            legacy_library=[
                "C:/images/home.png || 居家服，在卧室和睡前使用",
                {
                    "path": "C:/images/school.png",
                    "description": "校服，在校园使用",
                    "reference_role": "人设, 穿搭",
                },
            ],
            preset_names={"居家睡衣", "校服人像", "日常穿搭"},
        )

        self.assertTrue(loaded.needs_persist)
        self.assertEqual([item.kind for item in loaded.references], ["persona", "library", "library"])
        self.assertEqual(loaded.references[0].id, "persona")
        self.assertEqual(loaded.references[0].reference_roles, ("identity",))
        self.assertTrue(loaded.references[1].id.startswith("library_"))
        self.assertEqual(loaded.references[1].outfit_category, "homewear")
        self.assertEqual(loaded.references[1].scene_categories, ("home", "bedroom"))
        self.assertEqual(loaded.references[2].reference_roles, ("identity", "outfit"))
        self.assertEqual(loaded.references[2].outfit_category, "school_uniform")

    def test_legacy_delimited_metadata_is_tolerantly_normalized_with_warnings(self) -> None:
        loaded = load_catalog(
            raw_catalog=[],
            catalog_version=None,
            legacy_library=[
                'C:/images/custom.png || 舞台造型 || '
                '{"reference_roles":["人物","穿搭","未知职责"],'
                '"outfit_category":"礼裙","scene_categories":["舞台","海边"],'
                '"outfit_lock_default":"是","preferred_preset":"已删除预设"}',
            ],
            preset_names={"日常穿搭"},
        )

        reference = loaded.references[0]
        self.assertEqual(reference.reference_roles, ("identity", "outfit"))
        self.assertEqual(reference.outfit_category, "custom:礼裙")
        self.assertEqual(reference.scene_categories, ("custom:舞台", "beach"))
        self.assertTrue(reference.outfit_lock_default)
        self.assertEqual(reference.preferred_preset, "")
        self.assertTrue(any("未知职责" in warning for warning in loaded.warnings))
        self.assertTrue(any("已删除预设" in warning for warning in loaded.warnings))

    def test_legacy_json_array_string_and_quoted_sources_are_migrated(self) -> None:
        loaded = load_catalog(
            raw_catalog=[],
            catalog_version=0,
            legacy_persona='"C:/images/persona quoted.png"',
            legacy_library=json.dumps(
                [
                    '"C:/images/home quoted.png" || 居家服，在卧室使用',
                    {"path": "'C:/images/school quoted.png'", "description": "校服，在校园使用"},
                ],
                ensure_ascii=False,
            ),
            preset_names={"居家服", "校服人像"},
        )

        self.assertEqual(
            [item.source for item in loaded.references],
            [
                "C:/images/persona quoted.png",
                "C:/images/home quoted.png",
                "C:/images/school quoted.png",
            ],
        )

    def test_unversioned_nonempty_canonical_catalog_wins_over_legacy_retry(self) -> None:
        canonical = [
            {
                "id": "library-preserved",
                "kind": "library",
                "source": "C:/images/new.png",
                "note": "已经保存的新目录",
                "reference_roles": ["identity", "style"],
                "outfit_category": "custom:舞台服",
                "outfit_lock_default": False,
                "scene_categories": ["custom:舞台"],
                "preferred_preset": "舞台人像",
                "metadata_source": "configured",
            }
        ]

        loaded = load_catalog(
            canonical,
            catalog_version=0,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
            preset_names={"舞台人像"},
        )

        self.assertTrue(loaded.needs_persist)
        self.assertEqual([item.id for item in loaded.references], ["library-preserved"])
        self.assertEqual(loaded.references[0].reference_roles, ("identity", "style"))
        self.assertEqual(loaded.references[0].outfit_category, "custom:舞台服")

    def test_invalid_unversioned_canonical_catalog_is_read_only_and_never_overwritten(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "library-valid",
                    "kind": "library",
                    "source": "C:/images/valid.png",
                    "reference_roles": ["identity"],
                    "outfit_lock_default": False,
                },
                "invalid-entry-that-must-not-be-overwritten",
            ],
            catalog_version=0,
            legacy_library=["C:/images/legacy.png || 旧图库"],
        )

        self.assertFalse(loaded.needs_persist)
        self.assertTrue(loaded.read_only)
        self.assertEqual([item.id for item in loaded.references], ["library-valid"])
        self.assertTrue(any("只读" in warning for warning in loaded.warnings))

        fallback = load_catalog(
            ["invalid-entry-that-must-not-be-overwritten"],
            catalog_version=0,
            legacy_library=["C:/images/legacy.png || 旧图库"],
        )
        self.assertFalse(fallback.needs_persist)
        self.assertTrue(fallback.read_only)
        self.assertEqual([item.source for item in fallback.references], ["C:/images/legacy.png"])
        self.assertTrue(any("只读" in warning for warning in fallback.warnings))

    def test_unversioned_json_string_empty_catalog_does_not_reimport_legacy(self) -> None:
        loaded = load_catalog(
            "[]",
            catalog_version=0,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
        )

        self.assertTrue(loaded.needs_persist)
        self.assertEqual(loaded.references, ())

    def test_versioned_empty_catalog_recovers_residual_legacy_references(self) -> None:
        loaded = load_catalog(
            [],
            catalog_version=1,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
        )

        self.assertTrue(loaded.needs_persist)
        self.assertEqual(
            [item.source for item in loaded.references],
            ["C:/images/legacy-persona.png", "C:/images/legacy-library.png"],
        )
        self.assertTrue(any("异常为空" in warning for warning in loaded.warnings))

    def test_explicitly_cleared_versioned_catalog_does_not_restore_legacy_references(self) -> None:
        loaded = load_catalog(
            [],
            catalog_version=1,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
            user_cleared=True,
        )

        self.assertFalse(loaded.needs_persist)
        self.assertEqual(loaded.references, ())

    def test_note_inference_covers_all_reference_responsibilities(self) -> None:
        loaded = load_catalog(
            raw_catalog=[],
            catalog_version=0,
            legacy_library=[
                "C:/images/all-roles.png || 人物身份与服装参考，保留姿势、背景、画风、连续性，并作为改图原图"
            ],
        )

        self.assertEqual(
            loaded.references[0].reference_roles,
            ("identity", "outfit", "pose", "scene", "style", "continuity", "source"),
        )

        added = add_reference(
            (),
            kind="library",
            source="C:/images/chat-add.png",
            note="人物与服装参考，保留姿势、背景、画风、连续性，并作为改图原图",
        )[0]
        self.assertEqual(
            added.reference_roles,
            ("identity", "outfit", "pose", "scene", "style", "continuity", "source"),
        )

    def test_invalid_legacy_boolean_metadata_warns_and_uses_inferred_default(self) -> None:
        loaded = load_catalog(
            raw_catalog=[],
            catalog_version=0,
            legacy_library=[
                {
                    "path": "C:/images/sleep.png",
                    "description": "睡衣参考",
                    "outfit_lock_default": "有时候",
                }
            ],
        )

        self.assertTrue(loaded.references[0].outfit_lock_default)
        self.assertTrue(any("布尔" in warning and "有时候" in warning for warning in loaded.warnings))

    def test_strict_save_normalizes_aliases_and_adds_outfit_role_for_lock(self) -> None:
        serialized = validate_and_serialize(
            [
                {
                    "id": "library-user-id",
                    "kind": "library",
                    "source": "C:/images/stage.png",
                    "note": "演出造型",
                    "reference_roles": ["身份"],
                    "outfit_category": "custom:礼裙",
                    "outfit_lock_default": True,
                    "scene_categories": ["海边", "custom:舞台"],
                    "preferred_preset": "舞台人像",
                    "metadata_source": "configured",
                }
            ],
            preset_names={"舞台人像"},
        )

        self.assertEqual(serialized[0]["reference_roles"], ["identity", "outfit"])
        self.assertEqual(serialized[0]["outfit_category"], "custom:礼裙")
        self.assertEqual(serialized[0]["scene_categories"], ["beach", "custom:舞台"])
        self.assertEqual(serialized[0]["preferred_preset"], "舞台人像")

    def test_unicode_url_is_valid_and_duplicate_library_entry_merges_into_persona(self) -> None:
        source = "https://图片.example.com/角色资料/基础 人设.png?名称=星缘&版本=一"

        serialized = validate_and_serialize(
            [
                {
                    "id": "persona",
                    "kind": "persona",
                    "source": source,
                    "note": "基础身份",
                    "reference_roles": ["identity"],
                },
                {
                    "id": "library-same-source",
                    "kind": "library",
                    "source": source,
                    "note": "居家服参考",
                    "reference_roles": ["outfit"],
                    "outfit_category": "homewear",
                    "scene_categories": ["home"],
                },
            ]
        )

        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["kind"], "persona")
        self.assertEqual(serialized[0]["source"], source)
        self.assertEqual(serialized[0]["reference_roles"], ["identity", "outfit"])
        self.assertEqual(serialized[0]["outfit_category"], "homewear")
        self.assertEqual(serialized[0]["scene_categories"], ["home"])
        self.assertIn("居家服参考", serialized[0]["note"])

    def test_strict_save_reports_all_field_errors(self) -> None:
        with self.assertRaises(CatalogValidationError) as raised:
            validate_and_serialize(
                [
                    {
                        "id": "library-bad",
                        "kind": "library",
                        "source": "C:/images/bad.png",
                        "reference_roles": ["identity", "主体气质"],
                        "outfit_category": "礼裙",
                        "scene_categories": ["舞台"],
                        "preferred_preset": "已删除预设",
                    }
                ],
                preset_names={"日常穿搭"},
            )

        self.assertEqual(
            set(raised.exception.errors),
            {
                "items.0.reference_roles",
                "items.0.outfit_category",
                "items.0.scene_categories",
                "items.0.preferred_preset",
            },
        )

    def test_strict_save_rejects_invalid_item_shapes_and_non_boolean_lock(self) -> None:
        with self.assertRaises(CatalogValidationError) as raised:
            validate_and_serialize(
                [
                    "not-an-object",
                    True,
                    {
                        "id": "library-bad-bool",
                        "kind": "library",
                        "source": "C:/images/bad-bool.png",
                        "reference_roles": ["identity"],
                        "outfit_lock_default": "sometimes",
                    },
                ]
            )

        self.assertIn("items.0", raised.exception.errors)
        self.assertIn("items.1", raised.exception.errors)
        self.assertIn("items.2.outfit_lock_default", raised.exception.errors)

    def test_version_one_catalog_round_trips_and_ignores_legacy_fields(self) -> None:
        raw_catalog = validate_and_serialize(
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
                    "id": "library-stable",
                    "kind": "library",
                    "source": "C:/images/library.png",
                    "note": "通勤",
                    "reference_roles": ["identity", "outfit", "continuity"],
                    "outfit_category": "daily_outfit",
                    "outfit_lock_default": True,
                    "scene_categories": ["office", "outdoor"],
                    "preferred_preset": "日常穿搭",
                    "metadata_source": "configured",
                },
            ],
            preset_names={"日常穿搭"},
        )

        loaded = load_catalog(
            raw_catalog,
            catalog_version=1,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
            preset_names={"日常穿搭"},
        )

        self.assertFalse(loaded.needs_persist)
        self.assertEqual(validate_and_serialize(loaded.references, preset_names={"日常穿搭"}), raw_catalog)
        empty = load_catalog(
            [],
            catalog_version=1,
            legacy_persona="C:/images/legacy-persona.png",
            legacy_library=["C:/images/legacy-library.png || 旧图库"],
            user_cleared=True,
        )
        self.assertEqual(empty.references, ())
        self.assertFalse(empty.needs_persist)

    def test_version_one_load_keeps_valid_entries_and_enforces_catalog_invariants(self) -> None:
        raw_catalog = [
            {
                "id": "persona",
                "kind": "persona",
                "source": "C:/images/persona.png",
                "reference_roles": ["identity"],
            },
            {
                "id": "persona-copy",
                "kind": "persona",
                "source": "C:/images/persona-copy.png",
                "reference_roles": ["identity"],
            },
            {
                "id": "library-preserved",
                "kind": "library",
                "source": "C:/images/shared.png",
                "reference_roles": ["identity"],
                "preferred_preset": "已删除预设",
            },
            {
                "id": "library-preserved",
                "kind": "library",
                "source": "C:/images/duplicate-id.png",
                "reference_roles": ["identity"],
            },
            {
                "id": "library-duplicate-source",
                "kind": "library",
                "source": "C:/images/shared.png",
                "reference_roles": ["identity"],
            },
        ]
        raw_catalog.extend(
            {
                "id": f"library-{index}",
                "kind": "library",
                "source": f"C:/images/{index}.png",
                "reference_roles": ["identity"],
            }
            for index in range(30)
        )

        loaded = load_catalog(raw_catalog, catalog_version=1, preset_names={"日常穿搭"})

        self.assertEqual(sum(item.kind == "persona" for item in loaded.references), 1)
        self.assertEqual(sum(item.kind == "library" for item in loaded.references), 24)
        preserved = next(item for item in loaded.references if item.id == "library-preserved")
        self.assertEqual(preserved.preferred_preset, "")
        self.assertEqual(len({item.id for item in loaded.references}), len(loaded.references))
        self.assertEqual(len({item.source for item in loaded.references}), len(loaded.references))
        self.assertTrue(any("已删除预设" in warning for warning in loaded.warnings))
        self.assertTrue(any("重复" in warning for warning in loaded.warnings))
        self.assertTrue(any("24" in warning for warning in loaded.warnings))
        self.assertTrue(loaded.read_only)

    def test_strict_save_enforces_catalog_cardinality_and_uniqueness(self) -> None:
        items = [
            {
                "id": "persona",
                "kind": "persona",
                "source": "C:/images/shared.png",
                "reference_roles": ["identity"],
            },
            {
                "id": "persona-copy",
                "kind": "persona",
                "source": "C:/images/persona-copy.png",
                "reference_roles": ["identity"],
            },
        ]
        items.extend(
            {
                "id": f"library-{index if index < 24 else 23}",
                "kind": "library",
                "source": "C:/images/shared.png" if index == 0 else f"C:/images/{index}.png",
                "reference_roles": ["identity"],
            }
            for index in range(26)
        )

        with self.assertRaises(CatalogValidationError) as raised:
            validate_and_serialize(items)

        fields = set(raised.exception.errors)
        self.assertIn("items.1.kind", fields)
        self.assertNotIn("items.2.source", fields)
        self.assertIn("items.27.kind", fields)
        self.assertIn("items.26.id", fields)

    def test_add_and_delete_use_stable_ids_without_losing_other_metadata(self) -> None:
        loaded = load_catalog(
            [
                {
                    "id": "library-keep",
                    "kind": "library",
                    "source": "C:/images/keep.png",
                    "note": "保留项",
                    "reference_roles": ["identity", "style"],
                    "outfit_category": "custom:礼裙",
                    "outfit_lock_default": False,
                    "scene_categories": ["custom:舞台"],
                    "preferred_preset": "舞台人像",
                    "metadata_source": "configured",
                }
            ],
            catalog_version=1,
            preset_names={"舞台人像", "居家睡衣"},
        )
        kept = loaded.references[0]

        with_added = add_reference(
            loaded.references,
            kind="library",
            source="C:/images/sleep.png",
            note="睡衣，在卧室和睡前使用",
            preset_names={"舞台人像", "居家睡衣"},
        )
        added = with_added[-1]
        self.assertTrue(added.id.startswith("library_"))
        self.assertNotEqual(added.id, "library-keep")
        self.assertEqual(added.outfit_category, "sleepwear")
        self.assertEqual(added.reference_roles, ("identity", "outfit"))
        self.assertTrue(added.outfit_lock_default)
        self.assertEqual(added.preferred_preset, "居家睡衣")

        remaining = delete_reference(with_added, added.id)
        self.assertEqual(remaining, (kept,))

    def test_daily_outfit_projects_to_candidate_but_is_never_serialized(self) -> None:
        daily = build_daily_outfit_reference(
            "C:/images/today.png",
            note="今天的上班穿搭",
            preset_names={"日常穿搭"},
        )

        candidate = project_reference_candidate(daily, resolved_source="C:/cache/today.png")
        self.assertEqual(candidate["id"], "daily_outfit")
        self.assertEqual(candidate["kind"], "daily_outfit")
        self.assertEqual(candidate["path"], "C:/cache/today.png")
        self.assertEqual(candidate["reference_roles"], ["identity", "outfit"])
        self.assertTrue(candidate["outfit_lock_default"])
        self.assertEqual(candidate["preferred_preset"], "日常穿搭")
        self.assertEqual(validate_and_serialize([daily], preset_names={"日常穿搭"}), [])


if __name__ == "__main__":
    unittest.main()
