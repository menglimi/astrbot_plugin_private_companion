"""Expression applicability tolerates the model's natural ``any`` listing."""

from __future__ import annotations

import unittest

from companion_v2.expression.learning import normalize_applicability


class ApplicabilityTests(unittest.TestCase):
    def test_any_collapses_with_specific_values(self):
        for field, specific in (("relationship_stages", "close"), ("emotion_gates", "normal")):
            with self.subTest(field=field):
                checked = normalize_applicability({"channels": ["private"], field: ["any", specific]})
                self.assertEqual(checked[field], ["any"])

    def test_any_alone_stays_any(self):
        checked = normalize_applicability({"channels": ["private"], "relationship_stages": ["any"]})
        self.assertEqual(checked["relationship_stages"], ["any"])

    def test_unknown_and_duplicate_values_are_still_rejected(self):
        for value in (["close", "unknown"], ["close", "close"]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_applicability({"channels": ["private"], "relationship_stages": value})

    def test_empty_values_fall_back_to_any(self):
        for value in ([], None):
            with self.subTest(value=value):
                checked = normalize_applicability({"channels": ["private"], "relationship_stages": value})
                self.assertEqual(checked["relationship_stages"], ["any"])


if __name__ == "__main__":
    unittest.main()
