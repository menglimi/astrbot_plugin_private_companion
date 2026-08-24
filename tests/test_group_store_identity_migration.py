# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.core_store import CoreStoreMixin


GROUP_ID = "8EC9DA9653F094D2D9CC640B6EC225C0"
GROUP_UMO = f"QBot4012710235:GroupMessage:{GROUP_ID}"


class _StoreHarness(CoreStoreMixin):
    def __init__(self) -> None:
        self.current_profile = "main"
        self.profiles = {
            "main": {"groups": {}},
            "other": {"groups": {}},
        }

    @property
    def data(self) -> dict:
        return self.profiles[self.current_profile]

    @data.setter
    def data(self, value: dict) -> None:
        self.profiles[self.current_profile] = value


class GroupStoreIdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _StoreHarness()

    def test_get_group_canonicalizes_numeric_opaque_and_umo_ids(self) -> None:
        numeric = self.store._get_group("default:GroupMessage:123456")
        self.assertIs(numeric, self.store._get_group("123456"))
        opaque = self.store._get_group(GROUP_UMO)
        self.assertIs(opaque, self.store._get_group(GROUP_ID))
        self.assertEqual({"123456", GROUP_ID}, set(self.store.data["groups"]))

    def test_equivalent_legacy_keys_merge_without_losing_observations(self) -> None:
        self.store.data["groups"] = {
            GROUP_ID: {
                "group_id": GROUP_ID,
                "manual_group_name": "手动群名",
                "manual_group_name_updated_at": 20,
                "message_count": 2,
                "last_seen": 10,
                "recent_messages": [{"message_id": "m1", "text": "第一条", "ts": 1}],
                "recent_bot_replies": [{"delivery_id": "d1", "text": "Bot 第一条", "ts": 1}],
                "members": {
                    "u1": {"name": "甲", "count": 2, "recent_phrases": ["早"]},
                },
                "topic_threads": [{
                    "signature": "共同话题",
                    "message_count": 2,
                    "participants": ["u1"],
                    "recent_examples": [{"id": "e1", "text": "例一"}],
                    "last_ts": 10,
                }],
                "group_episodes": [{"created_ts": 10, "summary": "旧摘要"}],
                "relationship_edges": {"u1|u2": {"count": 1, "tone": {"普通": 1}}},
                "enabled": False,
            },
            GROUP_UMO: {
                "group_id": GROUP_UMO,
                "umo": GROUP_UMO,
                "name": "自动群名",
                "message_count": 3,
                "last_seen": 30,
                "recent_messages": [{"message_id": "m2", "text": "第二条", "ts": 2}],
                "recent_bot_replies": [
                    {"delivery_id": "d1", "text": "重复副本", "ts": 9},
                    {"ts": 2, "sender_id": "u2", "kind": "interjection", "text": "Bot 第二条"},
                ],
                "members": {
                    "u1": {"name": "甲", "count": 3, "recent_phrases": ["晚"]},
                    "u2": {"name": "乙", "count": 1, "recent_phrases": []},
                },
                "topic_threads": [{
                    "signature": "共同话题",
                    "message_count": 3,
                    "participants": ["u2"],
                    "recent_examples": [{"id": "e2", "text": "例二"}],
                    "last_ts": 30,
                }],
                "group_episodes": [{"created_ts": 30, "summary": "新摘要"}],
                "relationship_edges": {"u1|u2": {"count": 2, "tone": {"普通": 2, "玩笑": 1}}},
                "slang_terms": ["新梗"],
                "enabled": True,
            },
        }

        group = self.store._get_group(GROUP_UMO)

        self.assertEqual([GROUP_ID], list(self.store.data["groups"]))
        self.assertEqual(GROUP_ID, group["group_id"])
        self.assertEqual(5, group["message_count"])
        self.assertEqual(30, group["last_seen"])
        self.assertFalse(group["enabled"])
        self.assertEqual("手动群名", group["manual_group_name"])
        self.assertEqual("手动群名", group["name"])
        self.assertEqual({"m1", "m2"}, {item["message_id"] for item in group["recent_messages"]})
        self.assertEqual({"d1", ""}, {item.get("delivery_id", "") for item in group["recent_bot_replies"]})
        self.assertEqual(2, len(group["recent_bot_replies"]))
        self.assertEqual(5, group["members"]["u1"]["count"])
        self.assertEqual({"早", "晚"}, set(group["members"]["u1"]["recent_phrases"]))
        self.assertIn("u2", group["members"])
        self.assertEqual(5, group["topic_threads"][0]["message_count"])
        self.assertEqual({"u1", "u2"}, set(group["topic_threads"][0]["participants"]))
        self.assertEqual(2, len(group["topic_threads"][0]["recent_examples"]))
        self.assertEqual(2, len(group["group_episodes"]))
        self.assertEqual(3, group["relationship_edges"]["u1|u2"]["count"])
        self.assertEqual(3, group["relationship_edges"]["u1|u2"]["tone"]["普通"])
        self.assertEqual(["新梗"], group["slang_terms"])
        self.assertIn(GROUP_UMO, group["alias_group_ids"])

        snapshot = repr(group)
        self.assertIs(group, self.store._get_group(GROUP_UMO))
        self.assertEqual(snapshot, repr(group))

    def test_copied_alias_does_not_double_counters(self) -> None:
        record = {
            "message_count": 7,
            "recent_messages": [{"message_id": "same", "text": "同一条"}],
            "members": {"u1": {"count": 7}},
        }
        self.store.data["groups"] = {
            GROUP_ID: {"group_id": GROUP_ID, **record},
            GROUP_UMO: {"group_id": GROUP_UMO, "umo": GROUP_UMO, **record},
        }

        group = self.store._get_group(GROUP_ID)

        self.assertEqual(7, group["message_count"])
        self.assertEqual(7, group["members"]["u1"]["count"])
        self.assertEqual(1, len(group["recent_messages"]))

    def test_newer_manual_name_from_alias_wins_without_erasing_old_name(self) -> None:
        self.store.data["groups"] = {
            GROUP_ID: {
                "group_id": GROUP_ID,
                "manual_group_name": "旧手动名",
                "manual_group_name_updated_at": 10,
            },
            GROUP_UMO: {
                "group_id": GROUP_UMO,
                "manual_group_name": "新手动名",
                "manual_group_name_updated_at": 20,
            },
        }

        group = self.store._get_group(GROUP_ID)

        self.assertEqual("新手动名", group["manual_group_name"])
        self.assertEqual("新手动名", group["name"])
        self.assertEqual({"旧手动名", "新手动名"}, set(group["group_name_aliases"]))

    def test_migration_only_touches_the_active_persona_profile(self) -> None:
        for profile in self.store.profiles.values():
            profile["groups"] = {
                GROUP_UMO: {"group_id": GROUP_UMO, "message_count": 1},
            }

        self.store.current_profile = "main"
        self.store._get_group(GROUP_ID)

        self.assertIn(GROUP_ID, self.store.profiles["main"]["groups"])
        self.assertNotIn(GROUP_UMO, self.store.profiles["main"]["groups"])
        self.assertNotIn(GROUP_ID, self.store.profiles["other"]["groups"])
        self.assertIn(GROUP_UMO, self.store.profiles["other"]["groups"])


if __name__ == "__main__":
    unittest.main()
