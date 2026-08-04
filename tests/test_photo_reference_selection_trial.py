from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "astrbot_plugin_private_companion"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)

from astrbot_plugin_private_companion.photo_reference_selection import (
    run_photo_selection_trial,
    select_photo_reference,
)


class PhotoReferenceSelectionTrialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.candidates = [
            {
                "id": "sleep",
                "reference_roles": ["identity", "outfit", "scene"],
                "outfit_category": "sleepwear",
                "scene_categories": ["home", "bedroom"],
                "time_categories": ["night", "bedtime"],
                "selection_eligibility": "matching_only",
            },
            {
                "id": "school",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "school_uniform",
                "scene_categories": ["school"],
                "selection_eligibility": "disabled",
            },
        ]

    def test_selection_matches_and_excludes_candidates(self) -> None:
        result = select_photo_reference(
            {"request_text": "晚上了，在卧室穿着睡衣给我拍一张吧"}, self.candidates
        )
        self.assertEqual(result.selected["id"], "sleep")
        self.assertEqual(result.selection_reason, "best_match")
        self.assertEqual(result.candidates[0].candidate_id, "sleep")
        self.assertIn("disabled", result.candidates[1].excluded)

    async def test_trial_without_runner_is_explicit_and_side_effect_free(self) -> None:
        report = await run_photo_selection_trial(
            {"request_text": "晚上了，在卧室穿着睡衣给我拍一张吧"},
            candidates=self.candidates,
            runs=3,
        )
        self.assertEqual(report.tool_status, "no_tool_call")
        self.assertEqual(report.error_stage, "tool_decision")
        self.assertFalse(report.tool_called)
        self.assertIsNotNone(report.selection)

    async def test_trial_captures_only_photo_tool_arguments(self) -> None:
        calls: list[tuple[str, dict]] = []

        async def runner(text: str, request: dict) -> dict:
            calls.append((text, request))
            return {
                "tool_name": "pc_generate_photo",
                "arguments": {"kind": "selfie", "prompt": "captured"},
            }

        report = await run_photo_selection_trial(
            {"request_text": "拍一张自然自拍"},
            candidates=self.candidates,
            tool_runner=runner,
            runs=3,
        )
        self.assertEqual(report.tool_status, "captured")
        self.assertTrue(report.tool_called)
        self.assertEqual(report.tool_name, "pc_generate_photo")
        self.assertEqual(report.tool_arguments["kind"], "selfie")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "拍一张自然自拍")

    async def test_trial_requires_real_user_text(self) -> None:
        report = await run_photo_selection_trial({}, candidates=self.candidates)
        self.assertEqual(report.tool_status, "invalid_request")
        self.assertEqual(report.error_stage, "tool_decision")


if __name__ == "__main__":
    unittest.main()
