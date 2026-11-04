"""Ordering and paging contracts for the dashboard self-review list.

The dashboard reads this bucket one page at a time, and it explains "生效中的
指导" from the first page alone. Page members therefore have to be the newest
rows: ordering the list by the hashed item id produced an arbitrary subset, so
once a bucket held more rows than one page the newest guidance could be missing
from page 1 entirely (and the page still reported "暂无生效中的指导").

Degradation is per row: re-checking a row's evidence can fail long after the
item was accepted (a native observation is reset away, a stored window no
longer parses).  Such a row is projected as unavailable and keeps its place;
it may never turn the page — or the other rows — into a failed read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.foundation.refs import (RoleOwnerRef, ScopeKind, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.role_work.journal.self_review_management import SelfReviewManagement
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 11, 1, 22, 0, tzinfo=timezone(timedelta(hours=8)))
OWNER = RoleOwnerRef("install-1", "bot-1", "kazusa")
SCOPE = ScopeRef("install-1", "qq", ScopeKind.PRIVATE, "private-kazusa", bot_id="bot-1",
                 account_id="acc-1", conversation_id="conv-1", session_id="session-1")
CONTEXT = SimpleNamespace(key="companion:role:kazusa", scope=SCOPE)
OPERATOR = SimpleNamespace(subject=SubjectRef("install-1", SubjectKind.HUMAN, "owner-1"),
                           binding_id="binding:1", binding_revision=3)

ITEM_FIELDS = {"item_id", "run_id", "kind", "observation", "next_action", "confidence",
               "expires_at", "created_at", "state", "summary", "evidence", "automatic",
               "unavailable_reason", "actions", "view_token"}
PAGE_FIELDS = {"items", "expired_in_page", "policy", "next_cursor", "context_key", "role_id"}


def order_key(item):
    """Newest first, ties broken by descending item id (the paging contract)."""

    return (item["created_at"], item["item_id"])


def moment(value):
    return datetime.fromisoformat(value)


class FakeEvidence:
    """Only the cited handles matter here; the list projects them as opaque rows."""

    def describe_handles(self, handles, opened_at, closed_at, context, at):
        return [{"handle": handle, "kind": "reply", "text": "已送达回复：" + handle}
                for handle in handles]


class SelectiveEvidence:
    """Withdrawal is per handle: one row can fail while its neighbours stay whole."""

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.seen = []

    def describe_handles(self, handles, opened_at, closed_at, context, at):
        self.seen.append(tuple(handles))
        if any(handle in self.broken for handle in handles):
            # What a reset-away native observation raises while the item is rechecked.
            raise TaskError("conversation_context_reset",
                            "native history precedes this window's reset")
        return [{"handle": handle, "kind": "reply", "text": "已送达回复：" + handle}
                for handle in handles]


class BrokenEvidence:
    """A projection failure that is not a ``TaskError`` at all."""

    def __init__(self, error):
        self.error = error

    def describe_handles(self, handles, opened_at, closed_at, context, at):
        raise self.error


class SelfReviewListTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-self-review-order-")
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name) / "v2-data",
                                          installation_id="self-review-order")
        self.management = self.build_management()

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def build_management(self):
        app = SimpleNamespace(runtime=self.runtime, role_self_review_evidence=FakeEvidence(),
                              role_self_review=None, notify=lambda: None,
                              role_growth=SimpleNamespace(runtime=self.runtime))
        management = SelfReviewManagement(app)
        # Authorization and the clock are not what these contracts cover.
        management.management._authorize = lambda operator, context, at: (OWNER, 4)
        management.management.ledger = SimpleNamespace(now=lambda: NOW)
        return management

    def insert_run(self, *, day, items=1, state="accepted", live_hours=24, label="nightly"):
        """One review run ``day`` days before NOW, with realistic hash-derived ids."""

        created = NOW - timedelta(days=day)
        request_key = f"{label}:window:{created.date().isoformat()}"
        run_id = "self-review:" + digest([CONTEXT.key, request_key])
        window = {"opened_at": (created - timedelta(days=1)).isoformat(),
                  "closed_at": created.isoformat(), "handles": [f"h-{label}-{day}"],
                  "kinds": ["conversation"]}
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO role_self_review_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, digest(OWNER), digest(SCOPE), json.dumps(window), f"material-{label}-{day}",
                 f"summary {label} {created.date().isoformat()}", "decided", stamp(created),
                 stamp(created)))
            for ordinal in range(items):
                connection.execute("INSERT INTO role_self_review_items VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id + ":item:" + str(ordinal), run_id, digest(OWNER), digest(SCOPE), "keep",
                     f"observation {label} {day}/{ordinal}", "next action", 0.8,
                     json.dumps([f"h-{label}-{day}"]),
                     stamp(created + timedelta(hours=live_hours)), state, stamp(created),
                     None, None, None, ordinal))
        return run_id

    def page(self, *, state="active", limit=50, after_id=None):
        result = self.management.list(operator=OPERATOR, context=CONTEXT, state=state,
                                     limit=limit, after_id=after_id)
        self.assertTrue(result.is_success, result.failure)
        return result.unwrap()

    def use_evidence(self, evidence):
        self.management.evidence = evidence

    def drain(self, *, state="active", limit=7):
        collected, cursor, pages = [], None, 0
        while True:
            data = self.page(state=state, limit=limit, after_id=cursor)
            pages += 1
            self.assertLessEqual(pages, 40, "paging did not terminate")
            collected.extend(data["items"])
            cursor = data["next_cursor"]
            if not cursor:
                return collected

    def test_first_page_of_a_full_bucket_is_the_newest_rows_not_a_hash_subset(self):
        # 40 daily runs x 3 accepted items; only the newest two runs are still live.
        for day in range(40):
            self.insert_run(day=day, items=3, live_hours=72 if day < 2 else 24)
        everything = self.drain(state="active", limit=50)
        self.assertEqual(len(everything), 120)
        expected = sorted(everything, key=order_key, reverse=True)[:50]

        first = self.page(state="active", limit=50)
        self.assertEqual(len(first["items"]), 50)
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual([item["item_id"] for item in first["items"]],
                         [item["item_id"] for item in expected])
        # In this fixture the live rows are the newest ones, so all six land on the
        # first page.  That is a property of the fixture, not of the ordering: an
        # older row can stay live longer (covered by the long-lived-row test below).
        live = [item for item in first["items"] if moment(item["expires_at"]) > NOW]
        self.assertEqual(len(live), 6)

    def test_paging_walks_every_row_once_in_time_order(self):
        for day in range(40):
            self.insert_run(day=day, items=3)
        walked = self.drain(state="active", limit=7)
        self.assertEqual(len(walked), 120)
        self.assertEqual(len({item["item_id"] for item in walked}), 120)
        keys = [order_key(item) for item in walked]
        self.assertEqual(keys, sorted(keys, reverse=True))
        # The cursor is "<created_at>|<item_id>" and travels in the 512-byte after_id
        # field; it also has to round-trip through the next page call.
        cursor = self.page(state="active", limit=7)["next_cursor"]
        self.assertIn("|", cursor)
        self.assertLessEqual(len(cursor), 512)
        self.assertEqual(len(self.page(state="active", limit=7, after_id=cursor)["items"]), 7)

    def test_an_older_row_that_stays_live_longer_can_page_behind_newer_expired_ones(self):
        # expires_at is chosen per suggestion, so ordering by created_at cannot also
        # order by "still live".  What this list guarantees is that a full bucket is
        # read newest first instead of being truncated into an arbitrary hash subset;
        # a long-lived older row simply lands on a later page.
        for day in range(1, 51):
            self.insert_run(day=day, items=1, live_hours=1, label="short")
        self.insert_run(day=60, items=1, live_hours=24 * 365, label="long")
        first = self.page(state="active", limit=50)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual([item for item in first["items"] if moment(item["expires_at"]) > NOW], [])
        rest = self.page(state="active", limit=50, after_id=first["next_cursor"])
        self.assertEqual(len(rest["items"]), 1)
        self.assertGreater(moment(rest["items"][0]["expires_at"]), NOW)

    def test_items_from_one_run_page_by_descending_item_id(self):
        # A single run gives every row the same created_at, so item_id alone has to
        # make the order stable across page boundaries.
        self.insert_run(day=0, items=5)
        expected = sorted(self.drain(state="active", limit=2), key=order_key, reverse=True)
        self.assertEqual([item["item_id"].rsplit(":", 1)[-1] for item in expected],
                         ["4", "3", "2", "1", "0"])
        first = self.page(state="active", limit=2)
        self.assertEqual([item["item_id"] for item in first["items"]],
                         [item["item_id"] for item in expected[:2]])
        rest = self.page(state="active", limit=2, after_id=first["next_cursor"])
        self.assertEqual([item["item_id"] for item in rest["items"]],
                         [item["item_id"] for item in expected[2:4]])

    def test_state_filters_keep_their_meaning(self):
        for day in range(4):
            self.insert_run(day=day, items=2, state="rejected", live_hours=24, label="rejected")
        for day in range(3):
            self.insert_run(day=day, items=2, state="pending", live_hours=72, label="pending")
        for day in range(4):
            self.insert_run(day=day, items=2, state="accepted",
                            live_hours=72 if day < 2 else 24, label="accepted")

        active = self.drain(state="active", limit=5)
        pending = self.drain(state="pending", limit=5)
        decided = self.drain(state="decided", limit=5)
        self.assertEqual(len(active), 8)
        self.assertEqual(len(pending), 6)
        # "已停止注入" explains rejected rows plus accepted rows that have expired.
        self.assertEqual(len(decided), 8 + 4)
        self.assertEqual({item["state"] for item in pending}, {"pending"})
        self.assertEqual({item["state"] for item in active}, {"accepted"})
        self.assertEqual(sum(1 for item in active if moment(item["expires_at"]) > NOW), 4)
        for walked in (active, pending, decided):
            keys = [order_key(item) for item in walked]
            self.assertEqual(keys, sorted(keys, reverse=True))

    def test_the_last_page_ends_without_a_cursor(self):
        for day in range(3):
            self.insert_run(day=day, items=2)
        first = self.page(state="active", limit=4)
        self.assertIsNotNone(first["next_cursor"])
        last = self.page(state="active", limit=4, after_id=first["next_cursor"])
        self.assertEqual(len(last["items"]), 2)
        self.assertIsNone(last["next_cursor"])

    def test_a_cursor_from_the_hash_ordered_list_is_rejected_instead_of_repeating_rows(self):
        # One review run gives five rows the same created_at.  The previous list paged
        # by item_id ASCENDING, so its first page (limit=2) showed item:0 and item:1
        # with "item:1" as the cursor.  Resuming that bare id under the time order
        # would replay item:0 and skip item:2..4, so the cursor is rejected and the
        # page re-reads from the beginning instead.
        run_id = self.insert_run(day=0, items=5)
        legacy = run_id + ":item:1"
        result = self.management.list(operator=OPERATOR, context=CONTEXT, state="active",
                                     limit=5, after_id=legacy)
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "self_review_query_invalid")

    def test_an_unusable_cursor_is_reported_as_an_invalid_page(self):
        self.insert_run(day=0, items=2)
        for cursor in ("missing-item", "not-a-timestamp|self-review:x:item:0",
                       "2026-11-01T22:00:00+00:00|"):
            result = self.management.list(operator=OPERATOR, context=CONTEXT, state="active",
                                         limit=5, after_id=cursor)
            self.assertFalse(result.is_success, cursor)
            self.assertEqual(result.failure.code, "self_review_query_invalid", cursor)

    def test_the_projected_page_shape_is_unchanged(self):
        self.insert_run(day=0, items=2)
        data = self.page(state="active", limit=1)
        self.assertEqual(set(data), PAGE_FIELDS)
        self.assertEqual(set(data["items"][0]), ITEM_FIELDS)
        self.assertEqual(set(data["policy"]), {"window_hours", "scan_seconds",
                                              "empty_retry_seconds", "minimum_evidence"})
        self.assertEqual(data["context_key"], CONTEXT.key)
        self.assertEqual(data["role_id"], OWNER.persona_id)

    def test_a_withdrawn_evidence_row_degrades_instead_of_failing_the_page(self):
        # The cited native observation was reset away, so describing the evidence
        # raises a TaskError.  Its reason only lives in ``args[0]``: the normalization
        # used to read a ``.reason`` attribute TaskError does not have, and that
        # AttributeError escaped this list *and* the route layer, so one withdrawn
        # evidence turned the whole dashboard into a failed read.
        self.use_evidence(SelectiveEvidence(broken={"h-reset-0"}))
        self.insert_run(day=0, items=1, label="reset")
        for state in ("active", "decided"):
            self.page(state=state)  # no bucket may fail because one row is broken
        item = self.page(state="active")["items"][0]
        self.assertEqual(item["unavailable_reason"], "self_review_evidence_changed")
        self.assertIsNone(item["observation"])
        self.assertIsNone(item["next_action"])
        self.assertEqual(item["evidence"], [])
        self.assertIsNone(item["summary"])

    def test_one_broken_row_leaves_the_other_rows_complete(self):
        self.use_evidence(SelectiveEvidence(broken={"h-broken-0"}))
        broken = self.insert_run(day=0, items=1, label="broken")
        self.insert_run(day=1, items=1, label="whole-one")
        self.insert_run(day=2, items=1, label="whole-two")
        items = self.page(state="active", limit=50)["items"]
        self.assertEqual(len(items), 3)
        degraded = [item for item in items if item["unavailable_reason"]]
        self.assertEqual([item["item_id"] for item in degraded], [broken + ":item:0"])
        for item in items:
            if item["unavailable_reason"]:
                continue
            self.assertEqual(len(item["evidence"]), 1)
            self.assertEqual(item["next_action"], "next action")
            self.assertTrue(item["observation"].startswith("observation "))
            self.assertTrue(item["summary"].startswith("summary "))

    def test_an_unexpected_projection_failure_never_escapes_the_list(self):
        self.insert_run(day=0, items=2, label="unexpected")
        for error in (AttributeError("'TaskError' object has no attribute 'reason'"),
                      RuntimeError("projection exploded")):
            with self.subTest(error=type(error).__name__):
                self.use_evidence(BrokenEvidence(error))
                with self.assertLogs(
                        "companion_v2.role_work.journal.self_review_management", level="ERROR"):
                    result = self.management.list(operator=OPERATOR, context=CONTEXT,
                                                  state="active", limit=5)
                self.assertTrue(result.is_success, result.failure)
                items = result.unwrap()["items"]
                self.assertEqual(len(items), 2)
                for item in items:
                    self.assertEqual(item["unavailable_reason"], "self_review_evidence_changed")
                    self.assertIsNone(item["observation"])
                    self.assertIsNone(item["next_action"])


if __name__ == "__main__":
    unittest.main()
