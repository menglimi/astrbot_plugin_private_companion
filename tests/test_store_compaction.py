# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from astrbot_plugin_private_companion import core_store
from astrbot_plugin_private_companion.core_store import CoreStoreMixin


class _StoreHarness(CoreStoreMixin):
    def __init__(self, data=None) -> None:
        self.data = data or {}
        self._data_save_task = None
        self._data_save_dirty = False
        self.writes: list[dict] = []

    def _sanitize_store_control_tags_inplace(self, _data) -> int:
        return 0

    def _sanitize_proactive_candidate_repeat_counts_inplace(self, _data) -> int:
        return 0

    def _write_data_snapshot_sync(self, data) -> None:
        self.writes.append(deepcopy(data))


class _ControlTagSanitizerHarness(CoreStoreMixin):
    def __init__(self, enabled: bool) -> None:
        self.enable_store_control_tag_sanitization = enabled


class _CanonicalSanitizeHarness(CoreStoreMixin):
    def __init__(self, data) -> None:
        self.enable_store_control_tag_sanitization = True
        self.data = data
        self._data_save_task = None
        self._data_save_dirty = False
        self.writes: list[dict] = []

    def _sanitize_proactive_candidate_repeat_counts_inplace(self, _data) -> int:
        return 0

    def _write_data_snapshot_sync(self, data) -> int:
        changed = self._sanitize_store_control_tags_inplace(data)
        self.writes.append(deepcopy(data))
        return changed


class StoreCompactionTests(unittest.IsolatedAsyncioTestCase):
    def test_primary_json_projection_keeps_twelve_member_and_bot_messages(self) -> None:
        data = {
            "groups": {
                "group-a": {
                    "recent_messages": [
                        {"sender_id": "user", "text": f"member-{index}"}
                        for index in range(15)
                    ],
                    "recent_bot_replies": [
                        {"reply_to_id": "user", "text": f"bot-{index}"}
                        for index in range(15)
                    ],
                }
            }
        }

        changed = CoreStoreMixin._strip_ephemeral_group_transcripts_inplace(data)

        group = data["groups"]["group-a"]
        self.assertEqual(12, len(group["recent_messages"]))
        self.assertEqual(12, len(group["recent_bot_replies"]))
        self.assertEqual("member-3", group["recent_messages"][0]["text"])
        self.assertEqual("bot-3", group["recent_bot_replies"][0]["text"])
        self.assertEqual(3, changed["group_recent_messages"])
        self.assertEqual(3, changed["group_recent_bot_replies"])

    def test_store_control_tag_sanitization_can_be_disabled(self) -> None:
        data = {"memory": {"text": "先这样 <bubble/> 再继续"}}

        changed = _ControlTagSanitizerHarness(False)._sanitize_store_control_tags_inplace(data)

        self.assertEqual(changed, 0)
        self.assertEqual(data["memory"]["text"], "先这样 <bubble/> 再继续")

    def test_store_control_tag_sanitization_remains_enabled_by_default(self) -> None:
        data = {"memory": {"text": "先这样 <bubble/> 再继续"}}

        changed = _ControlTagSanitizerHarness(True)._sanitize_store_control_tags_inplace(data)

        self.assertEqual(changed, 1)
        self.assertEqual(data["memory"]["text"], "先这样 再继续")

    def test_store_sanitization_preserves_raw_user_evidence_and_code(self) -> None:
        raw = "那就让 bot 删掉所有的 <bubble/>？"
        data = {
            "groups": {
                "10001": {
                    "recent_messages": [{"sender_id": "20002", "text": raw}],
                    "members": {"20002": {"recent_phrases": [raw]}},
                    "summary": "模型输出泄漏 <bubble/> 需要清掉",
                }
            },
            "memory": {"code": "示例是 `<widget/>`，泄漏是 <bubble/>。"},
        }

        changed = _ControlTagSanitizerHarness(True)._sanitize_store_control_tags_inplace(data)

        self.assertEqual(changed, 2)
        self.assertEqual(data["groups"]["10001"]["recent_messages"][0]["text"], raw)
        self.assertEqual(data["groups"]["10001"]["members"]["20002"]["recent_phrases"][0], raw)
        self.assertNotIn("<bubble/>", data["groups"]["10001"]["summary"])
        self.assertEqual(data["memory"]["code"], "示例是 `<widget/>`，泄漏是。")

    async def test_snapshot_cleanup_updates_live_data_without_full_followup_write(self) -> None:
        harness = _CanonicalSanitizeHarness({"memory": {"text": "残留 <bubble/> 内容"}})

        harness._save_data_sync(full_scope="admin_import_export")
        await harness._flush_scheduled_data_save()

        self.assertEqual(harness.data["memory"]["text"], "残留 内容")
        self.assertEqual(len(harness.writes), 1)
        self.assertEqual(harness.writes[0], harness.data)

    def test_cleanup_logs_are_aggregated_within_cooldown(self) -> None:
        harness = _ControlTagSanitizerHarness(True)
        with (
            patch.object(core_store.time, "monotonic", side_effect=[100.0, 101.0, 800.0]),
            patch.object(core_store.logger, "info") as info,
        ):
            self.assertTrue(harness._log_store_control_cleanup("snapshot", 9))
            self.assertFalse(harness._log_store_control_cleanup("snapshot", 9))
            self.assertTrue(harness._log_store_control_cleanup("snapshot", 2))

        self.assertEqual(info.call_count, 2)
        self.assertIn("suppressed_events=1", info.call_args.args[-1])
        self.assertIn("suppressed_fields=9", info.call_args.args[-1])

    def test_compaction_preserves_planned_and_pending_candidates(self) -> None:
        now = time.time()
        candidates = [
            {
                "id": f"blocked-{index}",
                "user_id": "10001",
                "status": "blocked",
                "created_ts": now - 2000 + index,
            }
            for index in range(900)
        ]
        candidates.extend(
            {
                "id": f"pending-{index}",
                "user_id": "10001",
                "status": "deferred",
                "created_ts": now + index,
            }
            for index in range(20)
        )
        candidates.append(
            {
                "id": "planned-old",
                "user_id": "10001",
                "status": "blocked",
                "created_ts": 1,
            }
        )
        data = {
            "users": {"10001": {"planned_candidate_id": "planned-old"}},
            "proactive_candidate_pool": candidates,
        }
        harness = _StoreHarness(data)
        changed = harness._compact_store_history_inplace(data)
        kept = data["proactive_candidate_pool"]
        kept_ids = {item["id"] for item in kept}

        self.assertEqual(len(kept), 600)
        self.assertIn("planned-old", kept_ids)
        self.assertTrue({f"pending-{index}" for index in range(20)}.issubset(kept_ids))
        self.assertEqual(changed["proactive_candidate_pool"], 321)

    def test_external_history_drops_repeated_raw_payloads(self) -> None:
        data = {
            "news_integration": {
                "digests": [
                    {"headline": f"news-{index}", "items": [{"title": "x", "article_body": "z" * 5000}]}
                    for index in range(50)
                ],
                "last_digest": {"headline": "latest", "items": [{"title": "kept", "article_body": "z" * 5000}]},
                "latest_items": [{"title": "kept", "summary": "summary", "article_body": "z" * 5000}],
            },
            "web_exploration": {
                "notes": [
                    {"topic": f"topic-{index}", "results": [{"title": "x", "raw_html": "z" * 5000}]}
                    for index in range(60)
                ],
                "last_digest": {"topic": "latest", "results": [{"title": "kept", "raw_html": "z" * 5000}]},
                "latest_results": [{"title": "kept", "summary": "summary", "raw_html": "z" * 5000}],
            },
        }
        harness = _StoreHarness(data)
        harness._compact_store_history_inplace(data)

        self.assertEqual(len(data["news_integration"]["digests"]), 32)
        self.assertEqual(data["news_integration"]["digests"][-1]["headline"], "news-49")
        self.assertNotIn("items", data["news_integration"]["digests"][-1])
        self.assertNotIn("article_body", data["news_integration"]["last_digest"]["items"][0])
        self.assertEqual(len(data["web_exploration"]["notes"]), 40)
        self.assertEqual(data["web_exploration"]["notes"][-1]["topic"], "topic-59")
        self.assertNotIn("results", data["web_exploration"]["notes"][-1])
        self.assertNotIn("raw_html", data["web_exploration"]["last_digest"]["results"][0])

    async def test_repeated_sync_save_calls_are_coalesced_off_loop(self) -> None:
        harness = _StoreHarness({"users": {}})
        harness._save_data_sync(full_scope="admin_import_export")
        harness._save_data_sync(full_scope="admin_import_export")
        harness._save_data_sync(full_scope="admin_import_export")

        self.assertEqual(harness.writes, [])
        await asyncio.sleep(0.55)
        self.assertEqual(len(harness.writes), 1)

    async def test_flush_waits_for_pending_coalesced_save(self) -> None:
        harness = _StoreHarness({"users": {"10001": {"nickname": "before switch"}}})
        harness._save_data_sync(full_scope="admin_import_export")

        await harness._flush_scheduled_data_save()

        self.assertEqual(len(harness.writes), 1)
        self.assertEqual(harness.writes[0]["users"]["10001"]["nickname"], "before switch")
        self.assertFalse(harness._data_save_dirty)
        self.assertIsNone(harness._data_save_task)


if __name__ == "__main__":
    unittest.main()
