from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from pathlib import Path

from astrbot_plugin_private_companion.constants import (
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
)
from astrbot_plugin_private_companion.main import PrivateCompanionExtensionAPI
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _PluginHarness:
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.data = {
            "worldbook_member_profiles": {
                "u1": {
                    "user_id": "u1",
                    "name": "用户",
                    "pending_observations": [
                        {
                            "id": "ordinary",
                            "content": "普通待确认观察",
                            "source": "group_observation",
                        }
                    ],
                }
            }
        }

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


def _historical(batch_id: str, index: int, *, content: str = "") -> dict[str, object]:
    value = content or f"历史关系观察 {index}"
    return {
        "id": f"{batch_id}-{index}",
        "content": value,
        "source": "memory_companion_historical_chat",
        "import_batch_id": batch_id,
    }


class HistoricalRelationshipImportTests(unittest.IsolatedAsyncioTestCase):
    def test_worldbook_capacity_contracts_remain_stable(self) -> None:
        self.assertEqual(24, WORLDBOOK_PENDING_OBSERVATION_CAPACITY)
        self.assertEqual(8, WORLDBOOK_IMPORTANT_MEMORY_CAPACITY)

        api = PrivateCompanionPageApi(_PluginHarness())
        normalized = api._normalize_important_memories(
            [
                {
                    "content": f"重要记忆 {index}",
                    "weight": index,
                    "updated_at": index + 1,
                }
                for index in range(12)
            ]
        )
        self.assertEqual(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY, len(normalized))

    def test_accepting_historical_observation_preserves_batch_provenance(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "page_api.py").read_text(encoding="utf-8")
        self.assertIn('"import_batch_id": self._single_line(accepted.get("import_batch_id"), 120)', source)
        self.assertIn('import_batch_id = self._single_line(raw.get("import_batch_id"), 120)', source)
        self.assertIn('memory["import_batch_id"] = import_batch_id', source)

    async def test_history_is_grounded_deduplicated_and_does_not_evict_ordinary_pending(self) -> None:
        plugin = _PluginHarness()
        api = PrivateCompanionExtensionAPI(plugin)
        observations = [
            {
                "title": f"候选 {index}",
                "content": f"历史关系观察 {index}",
                "source_message_ids": [f"tl-{index}"],
                "observed_at": "2026-01-01T00:00:00+08:00",
                "confidence": 0.8,
            }
            for index in range(30)
        ]
        first = await api.stage_historical_relationship_observations(
            user_id="u1", user_name="用户", batch_id="batch-1", observations=observations
        )
        second = await api.stage_historical_relationship_observations(
            user_id="u1", user_name="用户", batch_id="batch-1", observations=observations
        )
        pending = plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"]
        historical = [item for item in pending if item.get("source") == "memory_companion_historical_chat"]
        self.assertEqual(24, first["staged"])
        self.assertEqual(0, second["staged"])
        self.assertEqual(24, len(historical))
        self.assertTrue(any(item.get("id") == "ordinary" for item in pending))
        self.assertTrue(all(item.get("source_event_ids") for item in historical))

        rollback = await api.rollback_historical_relationship_observations("batch-1")
        pending = plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"]
        self.assertEqual(24, rollback["removed"])
        self.assertEqual(["ordinary"], [item.get("id") for item in pending])

    async def test_rebind_keeps_capacity_blocked_items_at_source_and_preserves_target(self) -> None:
        plugin = _PluginHarness()
        plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"].extend(
            [
                _historical("batch-1", 1),
                _historical("batch-1", 2),
                _historical("batch-2", 3),
                {
                    "id": "ordinary-with-batch",
                    "content": "普通观察仍然保留",
                    "source": "group_observation",
                    "import_batch_id": "batch-1",
                },
            ]
        )
        plugin.data["worldbook_member_profiles"]["u2"] = {
            "user_id": "u2",
            "name": "正确用户",
            "pending_observations": [
                {"id": "target-ordinary", "content": "目标普通观察", "source": "group_observation"},
                _historical("batch-1", 99, content="历史关系观察 1"),
                *[_historical("older", index) for index in range(30)],
            ],
        }
        target_before = deepcopy(plugin.data["worldbook_member_profiles"]["u2"])
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-1",
            old_user_id="u1",
            user_id="u2",
            user_name="正确用户",
        )

        source_pending = plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"]
        target_pending = plugin.data["worldbook_member_profiles"]["u2"]["pending_observations"]
        self.assertEqual(2, result["matched"])
        self.assertEqual(0, result["moved"])
        self.assertEqual(1, result["deduplicated"])
        self.assertEqual(1, result["trimmed"])
        self.assertEqual(1, result["target_batch_count"])
        self.assertEqual(
            result["matched"],
            result["moved"] + result["deduplicated"] + result["trimmed"],
        )
        self.assertEqual(1, plugin.saved)
        self.assertTrue(any(item.get("id") == "ordinary" for item in source_pending))
        self.assertTrue(any(item.get("id") == "ordinary-with-batch" for item in source_pending))
        self.assertTrue(any(item.get("import_batch_id") == "batch-2" for item in source_pending))
        self.assertEqual(
            ["batch-1-2"],
            [
                item.get("id")
                for item in source_pending
                if item.get("source") == "memory_companion_historical_chat"
                and item.get("import_batch_id") == "batch-1"
            ],
        )
        self.assertEqual(target_before, plugin.data["worldbook_member_profiles"]["u2"])

    async def test_rebind_limits_new_items_and_leaves_source_batch_overflow_in_place(self) -> None:
        plugin = _PluginHarness()
        pending = [
            _historical("batch-overflow", index)
            for index in range(WORLDBOOK_PENDING_OBSERVATION_CAPACITY + 3)
        ]
        confirmed = [
            _historical("batch-overflow", index + 100)
            for index in range(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY + 3)
        ]
        plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"].extend(pending)
        plugin.data["worldbook_member_profiles"]["u1"]["important_memories"] = confirmed
        plugin.data["worldbook_member_profiles"]["u2"] = {
            "user_id": "u2",
            "name": "正确用户",
            "pending_observations": [],
            "important_memories": [],
        }
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-overflow",
            old_user_id="u1",
            user_id="u2",
            user_name="正确用户",
        )

        source = plugin.data["worldbook_member_profiles"]["u1"]
        target = plugin.data["worldbook_member_profiles"]["u2"]
        self.assertEqual(WORLDBOOK_PENDING_OBSERVATION_CAPACITY + 3, result["matched"])
        self.assertEqual(WORLDBOOK_PENDING_OBSERVATION_CAPACITY, result["moved"])
        self.assertEqual(0, result["deduplicated"])
        self.assertEqual(3, result["trimmed"])
        self.assertEqual(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY + 3, result["confirmed_matched"])
        self.assertEqual(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY, result["confirmed_moved"])
        self.assertEqual(0, result["confirmed_deduplicated"])
        self.assertEqual(3, result["confirmed_trimmed"])
        self.assertEqual(pending[-3:], source["pending_observations"][-3:])
        self.assertEqual(confirmed[-3:], source["important_memories"])
        self.assertEqual(pending[:WORLDBOOK_PENDING_OBSERVATION_CAPACITY], target["pending_observations"])
        self.assertEqual(confirmed[:WORLDBOOK_IMPORTANT_MEMORY_CAPACITY], target["important_memories"])

    async def test_rebind_never_rewrites_over_capacity_target_records(self) -> None:
        plugin = _PluginHarness()
        duplicate_pending = _historical("batch-overfull-target", 1)
        deferred_pending = _historical("batch-overfull-target", 2)
        duplicate_confirmed = _historical("batch-overfull-target", 3)
        deferred_confirmed = _historical("batch-overfull-target", 4)
        plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"].extend(
            [duplicate_pending, deferred_pending]
        )
        plugin.data["worldbook_member_profiles"]["u1"]["important_memories"] = [
            duplicate_confirmed,
            deferred_confirmed,
        ]
        target_pending = [
            {"id": f"ordinary-{index}", "content": f"普通观察 {index}", "source": "manual"}
            for index in range(WORLDBOOK_PENDING_OBSERVATION_CAPACITY + 2)
        ] + [
            duplicate_pending,
            *[
                _historical("another-batch", index)
                for index in range(WORLDBOOK_PENDING_OBSERVATION_CAPACITY)
            ],
        ]
        target_important = [
            {"content": f"普通正式记忆 {index}", "source": "manual"}
            for index in range(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY + 2)
        ] + [
            duplicate_confirmed,
            *[
                _historical("another-confirmed-batch", index)
                for index in range(WORLDBOOK_IMPORTANT_MEMORY_CAPACITY)
            ],
        ]
        plugin.data["worldbook_member_profiles"]["u2"] = {
            "user_id": "u2",
            "name": "已有超额资料",
            "pending_observations": deepcopy(target_pending),
            "important_memories": deepcopy(target_important),
        }
        target_before = deepcopy(plugin.data["worldbook_member_profiles"]["u2"])
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-overfull-target",
            old_user_id="u1",
            user_id="u2",
            user_name="已有超额资料",
        )

        source = plugin.data["worldbook_member_profiles"]["u1"]
        self.assertEqual(target_before, plugin.data["worldbook_member_profiles"]["u2"])
        self.assertEqual(
            (2, 0, 1, 1),
            tuple(result[key] for key in ("matched", "moved", "deduplicated", "trimmed")),
        )
        self.assertEqual(
            (2, 0, 1, 1),
            tuple(
                result[key]
                for key in (
                    "confirmed_matched",
                    "confirmed_moved",
                    "confirmed_deduplicated",
                    "confirmed_trimmed",
                )
            ),
        )
        self.assertEqual(
            [deferred_pending],
            [
                item
                for item in source["pending_observations"]
                if item.get("import_batch_id") == "batch-overfull-target"
            ],
        )
        self.assertEqual([deferred_confirmed], source["important_memories"])

    async def test_rebind_initializes_missing_target_like_stage(self) -> None:
        plugin = _PluginHarness()
        plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"].append(
            _historical("batch-new", 1)
        )
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-new",
            old_user_id="u1",
            user_id="123456",
            user_name="新用户",
        )

        target = plugin.data["worldbook_member_profiles"]["123456"]
        self.assertEqual(1, result["moved"])
        self.assertEqual("qq", target["identity_type"])
        self.assertEqual("新用户", target["name"])
        self.assertEqual(["MemoryCompanion 历史对话导入"], target["source_entries"])
        self.assertEqual(1, len(target["pending_observations"]))

    async def test_rebind_moves_traceable_confirmed_and_reports_legacy_untraceable(self) -> None:
        plugin = _PluginHarness()
        plugin.data["worldbook_member_profiles"]["u1"]["important_memories"] = [
            {"content": "普通正式记忆", "source": "manual"},
            _historical("batch-confirmed", 1),
            _historical("other-batch", 2),
            {
                "content": "旧版已确认但没有批次标识",
                "source": "memory_companion_historical_chat",
            },
        ]
        plugin.data["worldbook_member_profiles"]["u2"] = {
            "user_id": "u2",
            "name": "正确用户",
            "pending_observations": [],
            "important_memories": [{"content": "目标普通记忆", "source": "manual"}],
        }
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-confirmed",
            old_user_id="u1",
            user_id="u2",
            user_name="正确用户",
        )

        source_memories = plugin.data["worldbook_member_profiles"]["u1"]["important_memories"]
        target_memories = plugin.data["worldbook_member_profiles"]["u2"]["important_memories"]
        self.assertEqual(1, result["confirmed_matched"])
        self.assertEqual(1, result["confirmed_moved"])
        self.assertEqual(1, result["target_confirmed_batch_count"])
        self.assertEqual(1, result["untraceable_confirmed"])
        self.assertFalse(any(item.get("import_batch_id") == "batch-confirmed" for item in source_memories))
        self.assertTrue(any(item.get("import_batch_id") == "other-batch" for item in source_memories))
        self.assertTrue(any(not item.get("import_batch_id") and item.get("source") == "memory_companion_historical_chat" for item in source_memories))
        self.assertTrue(any(item.get("import_batch_id") == "batch-confirmed" for item in target_memories))

    async def test_rebind_keeps_confirmed_at_source_when_target_is_full(self) -> None:
        plugin = _PluginHarness()
        plugin.data["worldbook_member_profiles"]["u1"]["important_memories"] = [
            _historical("batch-full", 1)
        ]
        plugin.data["worldbook_member_profiles"]["u2"] = {
            "user_id": "u2",
            "name": "已满用户",
            "pending_observations": [],
            "important_memories": [
                {"content": f"目标正式记忆 {index}", "source": "manual"}
                for index in range(8)
            ],
        }
        api = PrivateCompanionExtensionAPI(plugin)

        result = await api.rebind_historical_relationship_observations(
            batch_id="batch-full",
            old_user_id="u1",
            user_id="u2",
            user_name="已满用户",
        )

        source_memories = plugin.data["worldbook_member_profiles"]["u1"]["important_memories"]
        target_memories = plugin.data["worldbook_member_profiles"]["u2"]["important_memories"]
        self.assertEqual(1, result["confirmed_matched"])
        self.assertEqual(0, result["confirmed_moved"])
        self.assertEqual(1, result["confirmed_trimmed"])
        self.assertTrue(any(item.get("import_batch_id") == "batch-full" for item in source_memories))
        self.assertEqual(8, len(target_memories))
        self.assertFalse(any(item.get("import_batch_id") == "batch-full" for item in target_memories))

    async def test_rebind_restores_profiles_when_persistence_fails(self) -> None:
        plugin = _PluginHarness()
        plugin.data["worldbook_member_profiles"]["u1"]["pending_observations"].append(
            _historical("batch-fail", 1)
        )
        plugin.data["worldbook_member_profiles"]["u2"] = "invalid profile"
        before = deepcopy(plugin.data["worldbook_member_profiles"])

        def fail_save(**_kwargs) -> None:
            raise OSError("save failed")

        plugin._save_data_sync = fail_save
        api = PrivateCompanionExtensionAPI(plugin)

        with self.assertRaises(OSError):
            await api.rebind_historical_relationship_observations(
                batch_id="batch-fail",
                old_user_id="u1",
                user_id="u2",
                user_name="目标用户",
            )

        self.assertEqual(before, plugin.data["worldbook_member_profiles"])


if __name__ == "__main__":
    unittest.main()
