"""Formal Memory Page API compatibility for the installed memory companion."""

from __future__ import annotations

import hashlib
import inspect
import json
import unittest

from astrbot_host.integration_registry import UnifiedCompanionExtensionAPI
from astrbot_host.memory_page_api import (
    MEMORY_PAGE_API_FAMILY,
    MEMORY_PAGE_API_VERSION,
    MEMORY_PAGE_CAPABILITIES,
    MEMORY_PAGE_SNAPSHOT_VERSION,
    MemoryPageAPI,
    MemoryPageUnavailable,
)


class MemoryPageAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = MemoryPageAPI(None)

    def test_descriptor_is_exact_and_declares_every_required_capability(self):
        descriptor = self.adapter.capabilities()
        self.assertEqual(tuple(descriptor), (
            "plugin_id", "instance_generation", "api_family", "api_version",
            "supported_task_versions", "capabilities", "lifecycle_state",
            "degraded_reasons"))
        self.assertEqual(descriptor["plugin_id"], "astrbot_plugin_private_companion")
        self.assertRegex(descriptor["instance_generation"], r"^[0-9a-f]{32}$")
        self.assertEqual(descriptor["api_family"], MEMORY_PAGE_API_FAMILY)
        self.assertEqual(descriptor["api_version"], MEMORY_PAGE_API_VERSION)
        self.assertEqual(descriptor["supported_task_versions"], [
            MEMORY_PAGE_SNAPSHOT_VERSION, "companion.memory-page-photo.v1"])
        self.assertEqual(set(MEMORY_PAGE_CAPABILITIES).issubset(descriptor["capabilities"]), True)
        self.assertEqual((descriptor["lifecycle_state"], descriptor["degraded_reasons"]),
                         ("ready", []))

    async def test_snapshot_is_sealed_path_free_and_matches_the_declared_date(self):
        snapshot = await self.adapter.export_snapshot(
            target_plugin_id="astrbot_plugin_memory_companion",
            selected_date="2026-10-28")
        self.assertEqual(tuple(snapshot), (
            "version", "source_plugin_id", "instance_generation", "selected_date",
            "available_dates", "features", "coordination", "day", "snapshot_id",
            "snapshot_sha256"))
        self.assertEqual(snapshot["version"], MEMORY_PAGE_SNAPSHOT_VERSION)
        self.assertEqual(snapshot["source_plugin_id"], "astrbot_plugin_private_companion")
        self.assertEqual(snapshot["instance_generation"], self.adapter.instance_generation)
        self.assertEqual(snapshot["selected_date"], "2026-10-28")
        self.assertEqual(snapshot["available_dates"], ["2026-10-28"])
        self.assertEqual(snapshot["coordination"], {
            "available": True, "state": "ready", "reason_code": ""})
        self.assertEqual(snapshot["day"]["date"], "2026-10-28")
        self.assertEqual(snapshot["day"]["plan"], {
            "date": "", "source": "none", "items": []})
        self.assertEqual((snapshot["day"]["details"], snapshot["day"]["photos"],
                          snapshot["day"]["diaries"]), ([], [], []))
        hashed = {field: snapshot[field] for field in (
            "version", "source_plugin_id", "instance_generation", "selected_date",
            "available_dates", "features", "coordination", "day")}
        digest = hashlib.sha256(json.dumps(hashed, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(snapshot["snapshot_sha256"], digest)
        self.assertEqual(snapshot["snapshot_id"], "memorypagesnap_" + digest)
        encoded = json.dumps(snapshot, ensure_ascii=False).lower()
        for forbidden in ("/users/", "c:\\\\", "session_id", "provider_id", "token"):
            self.assertNotIn(forbidden, encoded)

    async def test_photo_requests_fail_with_a_stable_code_instead_of_host_data(self):
        with self.assertRaises(MemoryPageUnavailable) as raised:
            await self.adapter.read_photo(
                target_plugin_id="astrbot_plugin_memory_companion",
                photo_ref="mphoto_0123456789ab_abcdefghijklmnopqrstuv")
        self.assertEqual(raised.exception.code, "memory_page_photo_unavailable")

    async def test_public_facade_exposes_the_three_formal_methods(self):
        api = object.__new__(UnifiedCompanionExtensionAPI)
        api._memory_page = self.adapter
        self.assertTrue(callable(api.memory_page_capabilities))
        self.assertTrue(inspect.iscoroutinefunction(api.export_memory_page_snapshot))
        self.assertTrue(inspect.iscoroutinefunction(api.read_memory_page_photo))
        self.assertEqual(api.memory_page_capabilities()["lifecycle_state"], "ready")
        snapshot = await api.export_memory_page_snapshot(
            target_plugin_id="astrbot_plugin_memory_companion",
            selected_date="2026-10-28")
        self.assertEqual(snapshot["selected_date"], "2026-10-28")


class _FakeRuntime:
    def __init__(self, records, states, journals=()):
        self.records = records
        self.states = states
        self.journals = list(journals)

    def read_all(self, statement, parameters):
        if "role_state_records" in statement:
            return self.states
        if "role_journals" in statement:
            return self.journals if "DISTINCT" not in statement else self.journals
        return self.records


class MemoryPageSnapshotDataTests(unittest.IsolatedAsyncioTestCase):
    def test_anchor_date_and_clock_follow_the_schedule_timezone(self):
        from astrbot_host.memory_page_api import _calendar_plans, _record_date
        record = {"kind": "role_plan", "state": "accepted", "title": "夜读",
                  "detail": "读两页就睡。",
                  "schedule": {"anchor": "2026-10-27T16:30:00+00:00",
                               "timezone_name": "Asia/Shanghai"}}
        self.assertEqual(_record_date(record), "2026-10-28")
        plans, _dates = _calendar_plans([{"record_json": json.dumps(record)}], "2026-10-28")
        self.assertEqual([item["time"] for item in plans["2026-10-28"]], ["00:30"])

    def test_weekly_plan_lands_on_its_weekday_and_moved_exception(self):
        from astrbot_host.memory_page_api import _calendar_plans
        record = {
            "kind": "role_plan", "state": "accepted", "title": "周会",
            "detail": "复盘一周。", "duration_minutes": 60,
            "schedule": {"anchor": "2026-10-07T02:00:00+00:00",
                         "timezone_name": "Asia/Shanghai", "frequency": "weekly",
                         "interval": 1, "count": None, "fold": 0, "gap_policy": "reject"},
            # 第 3 次期次（10-28）被改到 13:30。
            "exceptions": {"3": "2026-10-28T05:30:00+00:00"},
        }
        row = {"record_json": json.dumps(record)}
        wednesday, dates = _calendar_plans([row], "2026-10-28")
        self.assertEqual([item["time"] for item in wednesday["2026-10-28"]], ["13:30"])
        self.assertIn("2026-10-07", dates)
        tuesday, _ = _calendar_plans([row], "2026-10-27")
        self.assertEqual(tuesday, {})

    async def test_missing_requested_date_falls_back_to_latest_available_date(self):
        from types import SimpleNamespace
        record = {
            "kind": "role_plan", "state": "accepted", "title": "夜读",
            "detail": "读两页就睡。",
            "schedule": {"anchor": "2026-10-28T02:30:00+00:00", "timezone_name": "Asia/Shanghai"},
        }
        runtime = _FakeRuntime(
            [{"record_json": json.dumps(record, ensure_ascii=False)}],
            [],
            [{"content_json": json.dumps({"body": "昨天读了两页。"}, ensure_ascii=False),
              "kind": "diary", "local_date": "2026-10-28"}])
        adapter = MemoryPageAPI(SimpleNamespace(runtime=runtime))
        snapshot = await adapter.export_snapshot(
            target_plugin_id="astrbot_plugin_memory_companion")
        self.assertEqual(snapshot["available_dates"], ["2026-10-28"])
        self.assertEqual(snapshot["selected_date"], "2026-10-28")
        self.assertEqual(snapshot["day"]["date"], "2026-10-28")

    async def test_empty_history_keeps_selected_date_inside_available_dates(self):
        from types import SimpleNamespace
        adapter = MemoryPageAPI(SimpleNamespace(runtime=_FakeRuntime([], [])))
        snapshot = await adapter.export_snapshot(
            target_plugin_id="astrbot_plugin_memory_companion")
        self.assertEqual(snapshot["available_dates"], [])
        self.assertEqual(snapshot["selected_date"], "")
        self.assertEqual(snapshot["day"]["date"], "")

    async def test_calendar_plans_and_state_are_projected_into_the_snapshot(self):
        from types import SimpleNamespace
        record = {
            "kind": "role_plan", "state": "accepted", "title": "画一幅小画",
            "detail": "找一张空白画纸，慢慢画。",
            "planning": {"slot": "daily:Asia/Shanghai:2026-10-28:settings:abc"},
            "schedule": {"anchor": "2026-10-28T02:30:00+00:00", "timezone_name": "Asia/Shanghai"},
        }
        state = {"values": {"energy": 77, "sleep": "已休息", "presence": "休息中",
                            "mood": {"baseline": 6}}}
        runtime = _FakeRuntime(
            [{"record_json": json.dumps(record, ensure_ascii=False)}],
            [{"record_json": json.dumps(state, ensure_ascii=False)}],
            [{"content_json": json.dumps({"body": "今天画了一幅画。", "summary": "画画",
                                          "afterglow": "心里很安静。"}, ensure_ascii=False),
              "kind": "diary", "local_date": "2026-10-28"}])
        adapter = MemoryPageAPI(SimpleNamespace(runtime=runtime))
        snapshot = await adapter.export_snapshot(
            target_plugin_id="astrbot_plugin_memory_companion", selected_date="2026-10-28")
        self.assertEqual(snapshot["available_dates"], ["2026-10-28"])
        plan = snapshot["day"]["plan"]
        self.assertEqual((plan["date"], plan["source"]), ("2026-10-28", "history"))
        self.assertEqual(plan["items"][0]["index"], 0)
        self.assertEqual(plan["items"][0]["time"], "10:30")
        self.assertEqual(plan["items"][0]["activity"], "画一幅小画")
        self.assertEqual(snapshot["day"]["daily_state"]["energy"], 77)
        self.assertEqual(snapshot["day"]["daily_state"]["sleep"], "已休息")
        self.assertTrue(snapshot["features"]["daily_plan_enabled"])
        diaries = snapshot["day"]["diaries"]
        self.assertEqual(len(diaries), 1)
        self.assertEqual(diaries[0]["date"], "2026-10-28")
        self.assertEqual(diaries[0]["summary"], "画画")
        self.assertEqual(diaries[0]["share_seed"], "心里很安静。")


if __name__ == "__main__":
    unittest.main()
