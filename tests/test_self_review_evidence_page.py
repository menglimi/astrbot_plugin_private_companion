"""Focused contracts for evidence display and the daily review cadence."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from companion_v2.execution.models import TaskError
from companion_v2.role_work.journal.self_review_evidence import ReviewEvidence, SelfReviewEvidenceCatalog
from companion_v2.role_work.journal.self_review_service import SelfReviewService


class SelfReviewEvidenceDisplayTests(unittest.TestCase):
    def setUp(self):
        self.catalog = object.__new__(SelfReviewEvidenceCatalog)
        self.catalog.runtime = SimpleNamespace(read_snapshot=nullcontext)
        self.owner = SimpleNamespace(authorize=Mock())
        self.catalog.messages = self.owner
        self.context = SimpleNamespace(key="private-context")
        self.opened = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.closed = self.opened + timedelta(hours=24)

    def test_only_cited_evidence_is_materialized(self):
        reply_rows = [{"part_id": "unrelated"}, {"part_id": "cited"}]

        def reply_handle(_context, part_id):
            return "reply:" + part_id

        def reply(_context, row, _at):
            handle = "reply:" + row["part_id"]
            return ReviewEvidence(handle, "reply", "已送达回复：" + row["part_id"])

        with patch.object(self.catalog, "_authorize"), \
                patch.object(self.catalog, "_owner", return_value=self.owner), \
                patch.object(self.catalog, "_reply_rows", return_value=reply_rows), \
                patch.object(self.catalog, "reply_handle", side_effect=reply_handle), \
                patch.object(self.catalog, "_reply", side_effect=reply) as read_reply, \
                patch.object(self.catalog, "_receipt_references", return_value=[("receipt:unrelated", "activity", "x")]), \
                patch.object(self.catalog, "_receipt", side_effect=AssertionError("uncited receipt was read")):
            result = self.catalog.describe_handles(
                ["reply:cited"], self.opened, self.closed, self.context, self.closed)

        self.assertEqual(result, ["已送达回复：cited"])
        self.assertEqual(read_reply.call_count, 1)

    def test_withdrawn_cited_evidence_is_not_returned(self):
        with patch.object(self.catalog, "_authorize"), \
                patch.object(self.catalog, "_owner", return_value=self.owner), \
                patch.object(self.catalog, "_reply_rows", return_value=[]), \
                patch.object(self.catalog, "_receipt_references", return_value=[]):
            with self.assertRaises(TaskError) as raised:
                self.catalog.describe_handles(
                    ["reply:withdrawn"], self.opened, self.closed, self.context, self.closed)
        self.assertEqual(raised.exception.code, "self_review_evidence_changed")


class SelfReviewCadenceTests(unittest.IsolatedAsyncioTestCase):
    def test_task_identity_tracks_the_review_window(self):
        context = SimpleNamespace(scope="private-scope", role_binding=SimpleNamespace(role="role-one"))
        first = SelfReviewService.identifier(context, "window-one")
        second = SelfReviewService.identifier(context, "window-two")
        self.assertNotEqual(first, second)

    async def test_daily_cursor_advances_only_after_a_completed_attempt(self):
        service = object.__new__(SelfReviewService)
        service.next_checks = {}
        service.tasks = SimpleNamespace(now=lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
        service.run = AsyncMock(return_value={"state": "succeeded"})
        service._advance_cursor = Mock()
        context = SimpleNamespace(key="role-one")
        at = datetime(2026, 10, 2, tzinfo=timezone.utc)

        await service._run_scheduled(context, "instruction", "cursor", at)
        service._advance_cursor.assert_called_once_with("cursor", at)
        self.assertEqual(SelfReviewService.scan_seconds, 86400)

    async def test_failed_attempt_keeps_cursor_and_sets_retry_delay(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        service = object.__new__(SelfReviewService)
        service.next_checks = {}
        service.tasks = SimpleNamespace(now=lambda: now)
        service.run = AsyncMock(return_value={"state": "failed"})
        service._advance_cursor = Mock()
        service._record_failure = Mock()
        context = SimpleNamespace(key="role-one")

        await service._run_scheduled(context, "instruction", "cursor", now)
        service._advance_cursor.assert_not_called()
        self.assertEqual(service.next_checks[context.key], now + timedelta(minutes=5))
        service._record_failure.assert_called_once_with(
            context, "failed", "self_review_failed", now)
