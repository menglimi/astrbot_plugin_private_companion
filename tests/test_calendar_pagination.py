"""Calendar occurrence paging boundaries."""

from datetime import timedelta
import json
import unittest

from companion_v2.agenda.models import Schedule
from companion_v2.execution.models import encode
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture


class CalendarPaginationTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_occurrences_only_reports_scan_truncation_when_another_record_exists(self):
        _host, app, context, _manager = self.setup_state()
        for count, hidden_last, expected_partial, expected_skipped, expected_scan_limit in (
            (200, False, False, 0, False),
            (201, False, True, 0, True),
            (201, True, True, 1, True),
        ):
            with self.subTest(count=count, hidden_last=hidden_last):
                rows = [{"id": f"event-{index:03d}", "state": "cancelled"}
                        for index in range(count)]

                def inventory(_context, *, after_id="", limit=30, operator=None, kinds=None, active_since=None):
                    self.assertEqual(active_since, NOW - timedelta(days=1))
                    raw = [row for row in rows if row["id"] > after_id][:limit]
                    skipped = int(hidden_last and raw and raw[-1]["id"] == "event-200")
                    visible = [row for row in raw if not (hidden_last and row["id"] == "event-200")]
                    return {"items": visible, "skipped": skipped,
                            "next_cursor": raw[-1]["id"] if len(raw) == limit else None}

                app.calendar.inventory = inventory
                result = app.calendar.occurrences(context, start=NOW, end=NOW + timedelta(days=1), diagnostics=True)
                self.assertEqual(result["items"], [])
                self.assertEqual(result["partial"], expected_partial)
                self.assertEqual(result["skipped"], expected_skipped)
                self.assertEqual(result["scan_truncated"], expected_scan_limit)
                self.assertEqual(result["occurrences_truncated"], 0)

    async def test_occurrences_report_the_exact_count_over_the_display_limit(self):
        _host, app, context, _manager = self.setup_state()
        schedule = json.loads(encode(Schedule(NOW + timedelta(hours=1), "Asia/Shanghai")))
        rows = [{"id": f"event-{index:03d}", "revision": 1, "kind": "role_plan", "state": "accepted",
                 "title": f"安排 {index}", "detail": "", "presence": "", "sources": [],
                 "schedule": schedule, "duration_minutes": 30, "exceptions": {}}
                for index in range(65)]

        def inventory(_context, *, after_id="", limit=50, operator=None, kinds=None, active_since=None):
            self.assertEqual(active_since, NOW - timedelta(days=1))
            page = [row for row in rows if row["id"] > after_id][:limit]
            return {"items": page, "skipped": 0,
                    "next_cursor": page[-1]["id"] if len(page) == limit else None}

        app.calendar.inventory = inventory
        result = app.calendar.occurrences(context, start=NOW, end=NOW + timedelta(days=1), diagnostics=True)
        self.assertEqual(len(result["items"]), 64)
        self.assertTrue(result["partial"])
        self.assertFalse(result["scan_truncated"])
        self.assertEqual(result["occurrences_truncated"], 1)
