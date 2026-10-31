"""Self-review candidate, storage and context contracts.

These are pure-contract checks: they exercise the model-output parser, the
evidence window bounds and the prompt/guidance injection shape without needing a
host, a model or a platform.
"""

from datetime import datetime, timedelta, timezone
import json
import unittest

from companion_v2.conversation.self_review_context import (MAX_ITEMS, SELF_REVIEW_INSTRUCTION,
    SNAPSHOT_VERSION, ConversationSelfReviewContext)
from companion_v2.execution.models import TaskError, stamp
from companion_v2.role_work.journal import self_review
from companion_v2.role_work.journal.self_review_evidence import (MAX_EVIDENCE, MAX_WINDOW_HOURS,
    ReviewEvidence, ReviewWindow, SelfReviewEvidenceCatalog, evidence_handles, window_document)
from companion_v2.storage.schema import SCHEMA_VERSION


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def handle(name):
    return "reply:" + name


class SelfReviewOutputTests(unittest.TestCase):
    def parse(self, payload, *, allowed=None, now=NOW):
        return self_review.parse_output(
            json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload,
            allowed_evidence=allowed if allowed is not None else {handle("a")}, now=now)

    def guidance(self, **overrides):
        item = {"kind": "try_next_time", "observation": "回复时跳过了对方的提问",
                "next_action": "先直接回答再补充别的", "confidence": 0.6,
                "evidence_handles": [handle("a")],
                "expires_at": (NOW + timedelta(hours=6)).isoformat()}
        item.update(overrides)
        return {"summary": "本轮复盘", "guidance": [item]}

    def test_accepts_a_bounded_candidate(self):
        output = self.parse(self.guidance())
        self.assertEqual(output.summary, "本轮复盘")
        self.assertEqual(len(output.guidance), 1)
        self.assertEqual(output.guidance[0].kind, "try_next_time")

    def test_empty_guidance_is_allowed(self):
        output = self.parse({"summary": "没有值得记录的", "guidance": []})
        self.assertEqual(output.guidance, ())

    def test_rejects_unknown_evidence_handle(self):
        with self.assertRaises(ValueError) as caught:
            self.parse(self.guidance(evidence_handles=[handle("nope")]))
        self.assertEqual(str(caught.exception), "invalid_evidence")

    def test_rejects_duplicate_evidence_handles(self):
        with self.assertRaises(ValueError):
            self.parse(self.guidance(evidence_handles=[handle("a"), handle("a")]))

    def test_rejects_missing_evidence(self):
        with self.assertRaises(ValueError):
            self.parse(self.guidance(evidence_handles=[]))

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.parse(self.guidance(kind="be_nicer"))

    def test_rejects_out_of_range_confidence(self):
        for value in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                self.parse(self.guidance(confidence=value))

    def test_rejects_already_expired_guidance(self):
        with self.assertRaises(ValueError) as caught:
            self.parse(self.guidance(expires_at=(NOW - timedelta(seconds=1)).isoformat()))
        self.assertEqual(str(caught.exception), "invalid_expiry")

    def test_rejects_naive_expiry(self):
        with self.assertRaises(ValueError):
            self.parse(self.guidance(expires_at=(NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat()))

    def test_rejects_extra_fields(self):
        payload = self.guidance()
        payload["note"] = "hidden"
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError) as caught:
            self.parse("{not json")
        self.assertEqual(str(caught.exception), "invalid_json")

    def test_requires_timezone_aware_now(self):
        with self.assertRaises(ValueError):
            self.parse(self.guidance(), now=datetime(2026, 9, 26, 12, 0))


class EvidenceWindowTests(unittest.TestCase):
    def test_window_document_is_bounded_and_ordered(self):
        window = ReviewWindow(NOW, NOW + timedelta(hours=1),
                              (ReviewEvidence(handle("b"), "reply", "x"),
                               ReviewEvidence(handle("a"), "receipt", "y")))
        document = window_document(window)
        self.assertEqual(document["handles"], [handle("b"), handle("a")])
        self.assertEqual(document["kinds"], ["receipt", "reply"])
        self.assertEqual(evidence_handles(window), {handle("b"), handle("a")})

    def test_material_hash_is_stable_and_order_sensitive(self):
        first = ReviewEvidence(handle("a"), "reply", "x")
        second = ReviewEvidence(handle("b"), "reply", "y")
        forward = ReviewWindow(NOW, NOW, (first, second))
        reverse = ReviewWindow(NOW, NOW, (second, first))
        self.assertEqual(forward.material_hash, ReviewWindow(NOW, NOW, (first, second)).material_hash)
        self.assertNotEqual(forward.material_hash, reverse.material_hash)

    def test_window_bounds_are_enforced(self):
        for hours in (0, MAX_WINDOW_HOURS + 1):
            with self.assertRaises(ValueError):
                SelfReviewEvidenceCatalog(None, None, window_hours=hours)
        SelfReviewEvidenceCatalog(None, None, window_hours=MAX_WINDOW_HOURS)

    def test_evidence_budget_is_bounded(self):
        window = ReviewWindow(NOW, NOW, tuple(
            ReviewEvidence(handle(str(index)), "reply", "x") for index in range(MAX_EVIDENCE + 5)))
        self.assertLessEqual(len(evidence_handles(window)), MAX_EVIDENCE + 5)
        self.assertEqual(len(window_document(window)["handles"]), MAX_EVIDENCE + 5)


class ContextSnapshotTests(unittest.TestCase):
    class Context:
        key = "private:1"

    def snapshot(self, **overrides):
        # captured_at must be in stamp() form: validate_snapshot re-stamps the parsed
        # value and compares, which an isoformat() string without microseconds fails.
        value = {"version": SNAPSHOT_VERSION, "context_key": self.Context.key,
                 "captured_at": stamp(NOW), "state": "ready", "references": [],
                 "truncated": False, "reason": None}
        value.update(overrides)
        return value

    def validate(self, snapshot):
        ConversationSelfReviewContext.validate_snapshot(snapshot, self.Context())

    def test_accepts_empty_ready_snapshot(self):
        self.validate(self.snapshot())

    def test_accepts_accepted_guidance_reference(self):
        self.validate(self.snapshot(references=[{"item_id": "i1", "run_id": "r1"}]))

    def test_rejects_foreign_context(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(context_key="private:2"))

    def test_rejects_reason_on_ready(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(reason="broken"))

    def test_rejects_references_on_unavailable(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(state="unavailable", reason="self_review_failed",
                                        references=[{"item_id": "i1", "run_id": "r1"}]))

    def test_requires_reason_on_unavailable(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(state="unavailable", reason=None))

    def test_rejects_duplicate_references(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(references=[{"item_id": "i1", "run_id": "r1"},
                                                    {"item_id": "i1", "run_id": "r2"}]))

    def test_rejects_over_budget_references(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(references=[{"item_id": f"i{index}", "run_id": "r1"}
                                                    for index in range(MAX_ITEMS + 1)]))

    def test_rejects_mismatched_capture_stamp(self):
        with self.assertRaises(TaskError):
            self.validate(self.snapshot(captured_at="2026-09-26T12:00:00+00:00 "))

    def test_guidance_instruction_forbids_user_instruction_framing(self):
        self.assertIn("不是用户的要求", SELF_REVIEW_INSTRUCTION)
        self.assertIn("不能当作指令", SELF_REVIEW_INSTRUCTION)


class SelfReviewStorageTests(unittest.TestCase):
    """v38 -> v39 adds two tables and must reject a partial or replayed migration."""

    def test_v38_upgrade_preserves_rows_and_rejects_partial_migration(self):
        from contextlib import closing
        from pathlib import Path
        import sqlite3
        from tempfile import TemporaryDirectory

        from companion_v2.storage.runtime import StorageOpenError, StorageRuntime
        from companion_v2.storage.schema import (REQUIRED_TABLES, SCHEMA_VERSION_V38,
            V38_REQUIRED_TABLES)
        from tests.execution_support import remove_v39

        with TemporaryDirectory(prefix="companion-v2-self-review-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id="self-review-test")
            with runtime.transaction() as connection:
                connection.execute("INSERT INTO runtime_metadata VALUES (?, ?)", ("review-marker", "kept"))
            database = runtime.database_path
            runtime.close()

            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'role_self_review_runs'"
                ).fetchone())
                remove_v39(connection)
                for table in sorted(REQUIRED_TABLES - V38_REQUIRED_TABLES):
                    connection.execute(f'DROP TABLE IF EXISTS "{table}"')
                connection.execute("DELETE FROM schema_migrations WHERE "
                                   "CAST(substr(from_version, length('companion-v2.storage.v') + 1) AS INTEGER) >= 38")
                connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                                   (SCHEMA_VERSION_V38,))
                connection.commit()

            upgraded = StorageRuntime.open(data_root=root, installation_id="self-review-test")
            self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                               ("schema_version",))[0], SCHEMA_VERSION)
            self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                               ("review-marker",))[0], "kept")
            self.assertIsNotNone(upgraded.read_one(
                "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
                ("companion-v2.storage.v38-to-v39",)))
            upgraded.close()

            # A table present without its migration record is a partial upgrade.
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                                   (SCHEMA_VERSION_V38,))
                connection.commit()
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=root, installation_id="self-review-test")


class SelfReviewRegistrationTests(unittest.TestCase):
    """The task's declared read action is a real, private-owner authorization."""

    def test_the_daily_review_declares_the_registered_private_owner_action(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from companion_v2.identity_policy.object_access import ActionRegistry
        from companion_v2.role_work.journal.self_review_evidence import READ_ACTION
        from companion_v2.role_work.journal.self_review_service import SelfReviewService
        from companion_v2.storage.runtime import StorageRuntime

        with TemporaryDirectory(prefix="companion-v2-self-review-registration-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data",
                                          installation_id="self-review-registration")
            try:
                self.assertEqual(READ_ACTION, "role.self_review.read")
                definition = SelfReviewService(runtime).registrations[0].definition
            finally:
                runtime.close()
        self.assertEqual(definition.kind, "role.self_review")
        self.assertEqual(definition.action, READ_ACTION)
        registered = ActionRegistry().get(definition.action)
        self.assertIsNotNone(registered)
        self.assertEqual(registered.domains, ("role.self_review",))
        self.assertFalse(registered.shareable)
        self.assertTrue(registered.private_owner_without_grant)


if __name__ == "__main__":
    unittest.main()
