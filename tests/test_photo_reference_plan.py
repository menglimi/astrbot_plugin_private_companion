from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
package = sys.modules.setdefault(PACKAGE_NAME, types.ModuleType(PACKAGE_NAME))
package.__path__ = [str(PLUGIN_ROOT)]
package.__package__ = PACKAGE_NAME

from astrbot_plugin_private_companion.photo_reference_intent import ReferenceIntent
from astrbot_plugin_private_companion.photo_reference_plan import (
    build_photo_reference_plan,
    evaluate_reference_fallback,
    project_reference_plan_for_backend,
)


class PhotoReferencePlanTests(unittest.TestCase):
    def test_edit_without_source_stops_with_explicit_fallback(self) -> None:
        intent = ReferenceIntent(("source",), (), "edit", 0.99, "rule")

        plan = build_photo_reference_plan(intent, ())
        fallback = evaluate_reference_fallback(intent, plan)

        self.assertEqual(plan.bindings, ())
        self.assertEqual(plan.primary_reference_id, "")
        self.assertEqual(plan.fallback_reason, "missing_source_reference")
        self.assertEqual(fallback.fulfilled_roles, ())
        self.assertEqual(fallback.missing_roles, ("source",))
        self.assertIn("停止改图", fallback.message)

    def test_explicit_edit_source_becomes_explainable_primary_binding(self) -> None:
        intent = ReferenceIntent(("source",), ("style",), "edit", 0.99, "rule")

        plan = build_photo_reference_plan(
            intent,
            (
                {
                    "id": "user-source-1",
                    "path": "C:/images/source.png",
                    "kind": "source",
                    "reference_roles": ["source", "style"],
                },
            ),
        )

        self.assertEqual(plan.primary_reference_id, "user-source-1")
        self.assertEqual(plan.selection_reason, "explicit_source_reference")
        self.assertEqual(plan.fallback_reason, "")
        self.assertEqual(len(plan.bindings), 1)
        self.assertEqual(plan.bindings[0].roles, ("source",))
        self.assertEqual(plan.bindings[0].preserve, ("source",))
        self.assertEqual(plan.bindings[0].ignore, ("style",))

    def test_multiple_references_keep_distinct_roles_and_single_image_fallback(self) -> None:
        intent = ReferenceIntent(
            ("identity", "outfit", "pose"),
            (),
            "ambiguous",
            0.99,
            "rule",
        )
        plan = build_photo_reference_plan(
            intent,
            (
                {
                    "id": "face",
                    "path": "C:/images/face.png",
                    "kind": "explicit",
                    "reference_roles": ["identity"],
                    "priority": 300,
                },
                {
                    "id": "clothes",
                    "path": "C:/images/clothes.png",
                    "kind": "explicit",
                    "reference_roles": ["outfit"],
                    "priority": 200,
                },
                {
                    "id": "pose",
                    "path": "C:/images/pose.png",
                    "kind": "explicit",
                    "reference_roles": ["pose"],
                    "priority": 100,
                },
            ),
        )

        self.assertEqual([binding.roles for binding in plan.bindings], [
            ("identity",),
            ("outfit",),
            ("pose",),
        ])
        self.assertEqual(plan.primary_reference_id, "face")

        submitted, textual_fallback = project_reference_plan_for_backend(
            plan,
            max_images=1,
        )
        fallback = evaluate_reference_fallback(
            intent,
            plan,
            submitted_reference_ids=[binding.reference_id for binding in submitted],
        )

        self.assertEqual([binding.reference_id for binding in submitted], ["face"])
        self.assertIn("outfit", textual_fallback)
        self.assertIn("pose", textual_fallback)
        self.assertEqual(fallback.fulfilled_roles, ("identity",))
        self.assertEqual(fallback.missing_roles, ("outfit", "pose"))

    def test_missing_outfit_reference_reports_text_generation_fallback(self) -> None:
        intent = ReferenceIntent(
            ("identity", "outfit"),
            (),
            "ambiguous",
            0.95,
            "rule",
        )
        plan = build_photo_reference_plan(
            intent,
            (
                {
                    "id": "persona",
                    "path": "C:/images/persona.png",
                    "kind": "persona",
                    "reference_roles": ["identity"],
                },
            ),
        )

        fallback = evaluate_reference_fallback(intent, plan)

        self.assertEqual(fallback.fulfilled_roles, ("identity",))
        self.assertEqual(fallback.missing_roles, ("outfit",))
        self.assertIn("已保持人物身份", fallback.message)
        self.assertIn("服装按文字要求生成", fallback.message)
        self.assertNotIn("完整满足", fallback.message)


if __name__ == "__main__":
    unittest.main()
