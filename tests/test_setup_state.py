"""First-run progress migrates without retaining a runtime-mode choice."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from astrbot_host.setup_state import SCHEMA_VERSION, SetupStateStore, SetupStepsIncomplete


class SetupStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-setup-state-")
        self.store = SetupStateStore(self.temporary.name)
        self.store.root.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write_legacy(self, document):
        self.store.path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_schema_two_drops_single_target_selection_and_keeps_progress(self):
        self.write_legacy({"schema_version": 2, "mode": {"accounts": "single", "personas": "single"},
            "completed": ["model"], "skipped": ["knowledge"], "proactive_test": None, "ready": False})

        state = self.store.read()
        self.assertEqual(state["schema_version"], SCHEMA_VERSION)
        self.assertNotIn("mode", state)
        self.assertEqual(state["completed"], ["model"])
        self.assertEqual(state["skipped"], ["knowledge"])

        state = self.store.complete("relationships")
        persisted = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(state["completed"], ["model", "relationships"])
        self.assertNotIn("mode", persisted)

    def test_schema_one_receipt_is_kept_without_its_mode_field(self):
        tested_at = "2026-10-05T00:00:00+00:00"
        self.write_legacy({"schema_version": 1, "mode": {"accounts": "single", "personas": "single"},
            "completed": ["model", "knowledge", "relationships"],
            "proactive_test": {"state": "delivered", "tested_at": tested_at}, "ready": True})

        state = self.store.read()

        self.assertEqual(state["schema_version"], SCHEMA_VERSION)
        self.assertNotIn("mode", state)
        self.assertEqual(state["proactive_test"]["state"], "delivered")
        self.assertEqual(state["proactive_test"]["finished_at"], tested_at)
        self.assertTrue(state["ready"])

    def test_setup_progress_does_not_require_mode_selection(self):
        for step in ("model", "knowledge", "relationships", "proactive", "life", "group"):
            self.store.complete(step)

        state = self.store.begin_proactive_test(attempt_id="attempt-1", started_at="2026-10-05T00:00:00+00:00")

        self.assertEqual(state["proactive_test"]["attempt_id"], "attempt-1")
        self.assertNotIn("mode", state)

    def test_every_guided_step_must_be_done_or_skipped_before_the_proactive_test(self):
        for step in ("model", "knowledge", "relationships", "proactive", "life"):
            self.store.complete(step)
        with self.assertRaises(ValueError):
            self.store.begin_proactive_test(attempt_id="attempt-2", started_at="2026-10-05T00:00:00+00:00")
        state = self.store.complete("group", skipped=True)
        self.assertEqual(state["skipped"], ["group"])
        state = self.store.begin_proactive_test(attempt_id="attempt-3", started_at="2026-10-05T00:00:00+00:00")
        self.assertEqual(state["proactive_test"]["attempt_id"], "attempt-3")

    def test_unfinished_guide_and_unreplayable_attempt_are_different_errors(self):
        # Same store, two unrelated refusals. The host route has to tell them
        # apart, so they must not be the same exception class.
        self.store.complete("model")
        with self.assertRaises(SetupStepsIncomplete) as unfinished:
            self.store.begin_proactive_test(attempt_id="attempt-a", started_at="2026-10-05T00:00:00+00:00")
        self.assertIsInstance(unfinished.exception, ValueError)
        self.assertIn("guided setup steps", str(unfinished.exception))

        for step in ("knowledge", "relationships", "proactive", "life", "group"):
            self.store.complete(step, skipped=True)
        self.store.begin_proactive_test(attempt_id="attempt-b", started_at="2026-10-05T00:00:00+00:00")
        with self.assertRaises(ValueError) as replay:
            self.store.begin_proactive_test(attempt_id="attempt-c", started_at="2026-10-05T00:00:00+00:00")
        self.assertNotIsInstance(replay.exception, SetupStepsIncomplete)
        self.assertIn("cannot be replayed", str(replay.exception))



if __name__ == "__main__":
    unittest.main()
