import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion_v2.storage.semantic import SemanticStore


class SemanticStoreTests(unittest.TestCase):
    def test_schema_six_learning_rows_gain_retry_cooldown_without_data_loss(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantic-catalog.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE learning (owner TEXT PRIMARY KEY, checked_at REAL NOT NULL)")
            db.execute("INSERT INTO learning VALUES ('group-a', 123.5)")
            db.execute("PRAGMA user_version=6")
            db.commit()
            db.close()

            store = SemanticStore(Path(temporary))

            with store.db() as db:
                row = db.execute("SELECT checked_at,retry_after FROM learning WHERE owner='group-a'").fetchone()
                self.assertEqual((row["checked_at"], row["retry_after"]), (123.5, 0))
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)

    def test_group_slang_learning_uses_v1_cadence_and_preserves_explicit_interval(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary)
            store = SemanticStore(path)
            self.assertEqual(store.settings()["learning_interval_seconds"], 21600)
            with store.db() as db:
                self.assertIn("retry_after", {row["name"] for row in db.execute("PRAGMA table_info(learning)")})
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            with store.db() as db:
                db.execute("UPDATE settings SET revision=3,payload=?,explicit='[]' WHERE id=1",
                           (json.dumps({"learning_interval_seconds": 900}),))

            upgraded = SemanticStore(path)
            snapshot = upgraded.configuration_snapshot()
            self.assertEqual(snapshot["values"]["learning_interval_seconds"], 21600)
            self.assertEqual(snapshot["revision"], 4)

            with upgraded.db() as db:
                db.execute("UPDATE settings SET revision=5,payload=?,explicit=? WHERE id=1",
                           (json.dumps({"learning_interval_seconds": 900}),
                            json.dumps(["learning_interval_seconds"])))
            explicit = SemanticStore(path).configuration_snapshot()
            self.assertEqual(explicit["values"]["learning_interval_seconds"], 900)
            self.assertEqual(explicit["revision"], 5)

    def test_group_candidate_counts_accumulate_once_per_source_and_stay_isolated(self):
        with TemporaryDirectory() as temporary:
            store = SemanticStore(Path(temporary))
            first = store.record_slang_candidates("group-a", "event-1", ("观星局", "观星局"), observed_at=1)
            duplicate = store.record_slang_candidates("group-a", "event-1", ("观星局",), observed_at=2)
            second = store.record_slang_candidates("group-a", "event-2", ("观星局", "摸鱼王"), observed_at=3)

            self.assertEqual(first, {"观星局": 1})
            self.assertEqual(duplicate, {"观星局": 1})
            self.assertEqual(second, {"观星局": 2, "摸鱼王": 1})
            self.assertEqual(store.slang_candidate_counts("group-b"), {})

    def test_vision_timeout_and_provider_settings_defaults_bounds_and_migration(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary)
            store = SemanticStore(path)
            defaults = store.settings()
            self.assertEqual(defaults["timeout_seconds"], 8)
            self.assertEqual(defaults["analysis_timeout_seconds"], 30)
            self.assertEqual(defaults["vision_review_timeout_seconds"], 15)
            self.assertEqual(defaults["vision_review_provider"], "")
            self.assertEqual(defaults["vision_review_fallback_provider"], "")

            updated = store.configure({
                "analysis_timeout_seconds": 45,
                "vision_review_timeout_seconds": 20,
                "vision_review_provider": "flash-vision",
                "vision_review_fallback_provider": "backup-vision",
            }, defaults["revision"])
            self.assertEqual(updated["analysis_timeout_seconds"], 45)
            self.assertEqual(updated["vision_review_timeout_seconds"], 20)
            self.assertEqual(updated["vision_review_provider"], "flash-vision")
            self.assertEqual(updated["vision_review_fallback_provider"], "backup-vision")

            for invalid in (
                {"analysis_timeout_seconds": 4},
                {"analysis_timeout_seconds": 121},
                {"vision_review_timeout_seconds": 2},
                {"vision_review_timeout_seconds": 61},
            ):
                with self.assertRaises(ValueError):
                    store.configure(invalid, updated["revision"])

            # Existing DB without reaction_collection_runs.detail upgrades idempotently
            # while keeping PRAGMA user_version == 8.
            with store.db() as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(reaction_collection_runs)")}
                self.assertIn("detail", columns)
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)

    def test_collection_filter_pagination_detail_sanitization_and_failure_reset(self):
        with TemporaryDirectory() as temporary:
            store = SemanticStore(Path(temporary))
            owner = "owner-filter"
            store.put(owner, "reaction", "r-enabled",
                      {"name": "启用", "analysis": "analyzed"}, enabled=True)
            store.put(owner, "reaction", "r-awaiting-1",
                      {"name": "待确认一", "analysis": "auto_reviewed",
                       "collection_state": "awaiting_confirmation"}, enabled=False)
            store.put(owner, "reaction", "r-awaiting-2",
                      {"name": "待确认二", "analysis": "auto_reviewed",
                       "collection_state": "awaiting_confirmation"}, enabled=False)
            store.put(owner, "reaction", "r-disabled",
                      {"name": "手动停用", "analysis": "manual"}, enabled=False)
            store.put(owner, "reaction", "r-pending",
                      {"name": "待识别", "analysis": "pending"}, enabled=True)

            all_items, _ = store.list_recent_page(owner, "reaction", limit=10, collection_filter="all")
            self.assertEqual(len(all_items), 5)

            awaiting, _ = store.list_recent_page(owner, "reaction", limit=10,
                                                 collection_filter="awaiting_confirmation")
            self.assertEqual([item["id"] for item in awaiting], ["r-awaiting-2", "r-awaiting-1"])

            enabled, _ = store.list_recent_page(owner, "reaction", limit=10, collection_filter="enabled")
            self.assertEqual([item["id"] for item in enabled], ["r-pending", "r-enabled"])

            disabled, _ = store.list_recent_page(owner, "reaction", limit=10, collection_filter="disabled")
            self.assertEqual([item["id"] for item in disabled], ["r-disabled"])

            pending, _ = store.list_recent_page(owner, "reaction", limit=10, collection_filter="pending_analysis")
            self.assertEqual([item["id"] for item in pending], ["r-pending"])

            self.assertEqual([item["id"] for item in store.list_awaiting_confirmation_reactions(owner)],
                             ["r-awaiting-2", "r-awaiting-1"])

            # Sanitized detail strips secrets/URLs and reset clears failed runs only.
            store.record_reaction_collection(
                owner, "ev-1", "private_capture", "failed",
                code="http_502",
                detail="Gateway error at https://api.example.com/v1?key=sk-secret1234567890abcd\nBearer token999",
                at=100.0,
            )
            store.record_reaction_collection(
                owner, "ev-2", "private_capture", "added",
                item_id="r-enabled", at=101.0,
            )
            summary = store.reaction_collection_summary(owner)
            failed_run = next(row for row in summary["recent"] if row["status"] == "failed")
            self.assertEqual(failed_run["code"], "http_502")
            self.assertNotIn("sk-secret", failed_run["detail"])
            self.assertNotIn("https://api.example.com", failed_run["detail"])
            self.assertNotIn("token999", failed_run["detail"])
            self.assertEqual(summary["totals"]["private_capture"]["failed"], 1)
            self.assertEqual(summary["totals"]["private_capture"]["added"], 1)

            cleared = store.reset_reaction_collection_failures(owner)
            self.assertEqual(cleared["cleared_runs"], 1)
            self.assertEqual(cleared["cleared_totals"], 1)
            after_reset = store.reaction_collection_summary(owner)
            self.assertEqual(after_reset["totals"]["private_capture"]["failed"], 0)
            self.assertEqual(after_reset["totals"]["private_capture"]["added"], 1)
