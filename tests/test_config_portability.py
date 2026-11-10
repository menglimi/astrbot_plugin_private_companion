"""Portable configuration export keeps settings and excludes personal data."""

from __future__ import annotations

import unittest

from companion_v2.configuration.portability import (
    EXCLUDED_SECTIONS, SCHEMA, changes_for, portable,
)

SECTIONS = {"quota": object, "image.output": object, "role_profile": object,
            "relationship_profile": object, "person": object}


class PortabilityTests(unittest.TestCase):
    def test_personal_data_sections_are_never_exported_or_imported(self):
        for name in EXCLUDED_SECTIONS:
            with self.subTest(name=name):
                self.assertFalse(portable(name, SECTIONS))
        self.assertTrue(portable("quota", SECTIONS))
        self.assertTrue(portable("image.output", SECTIONS))
        self.assertFalse(portable("unknown", SECTIONS))

    def test_only_known_short_field_names_survive_import(self):
        self.assertEqual(changes_for({"enabled": False, "limit": 3, "extra": 1},
                                     {"enabled", "limit"}), {"enabled": False, "limit": 3})
        self.assertEqual(changes_for({"enabled": True}, set()), {})
        self.assertEqual(changes_for(None, {"enabled"}), {})
        self.assertEqual(changes_for({"enabled": True}, None), {})

    def test_document_schema_is_versioned(self):
        self.assertEqual(SCHEMA, "companion.config-export.v1")


if __name__ == "__main__":
    unittest.main()
