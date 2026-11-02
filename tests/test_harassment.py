import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from astrbot_host.harassment import HarassmentGuard
from companion_v2.relationships import harassment
from companion_v2.storage.runtime import StorageRuntime


class HarassmentStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-harassment-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name), installation_id="harassment")
        self.addCleanup(self.runtime.close)

    def test_settings_default_to_off_and_validate_bounds(self):
        settings = harassment.read_settings(self.runtime)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["window_seconds"], 300)
        self.assertEqual(settings["max_messages"], 20)
        saved = harassment.save_settings(self.runtime, {"enabled": True, "max_messages": 5})
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["max_messages"], 5)
        # 只改一项时其余字段保持原值。
        kept = harassment.save_settings(self.runtime, {"window_seconds": 600})
        self.assertTrue(kept["enabled"])
        self.assertEqual(kept["window_seconds"], 600)
        with self.assertRaises(ValueError):
            harassment.save_settings(self.runtime, {"max_messages": 1})
        with self.assertRaises(ValueError):
            harassment.save_settings(self.runtime, {"unknown": True})

    def test_blacklist_entries_round_trip(self):
        entry = harassment.add_entry(self.runtime, scope="umo:g1", user_id="1001", reason="rate", count=9)
        self.assertEqual(entry["reason_text"], "短时间消息过多")
        found = harassment.entry_for(self.runtime, "umo:g1", "1001")
        self.assertIsNotNone(found)
        self.assertEqual(harassment.entries(self.runtime)[0]["user_id"], "1001")
        self.assertTrue(harassment.remove_entry(self.runtime, scope="umo:g1", user_id="1001"))
        self.assertIsNone(harassment.entry_for(self.runtime, "umo:g1", "1001"))
        self.assertFalse(harassment.remove_entry(self.runtime, scope="umo:g1", user_id="1001"))

    def test_keyword_and_rate_triggers(self):
        settings = harassment.save_settings(self.runtime, {"enabled": True, "max_messages": 3,
                                                          "keywords": "广告，刷屏"})
        self.assertEqual(harassment.reason_for(settings, 1, "这是正常聊天"), "")
        self.assertEqual(harassment.reason_for(settings, 1, "发个广告"), "keyword")
        self.assertEqual(harassment.reason_for(settings, 4, "正常"), "rate")
        off = harassment.save_settings(self.runtime, {"enabled": False})
        self.assertEqual(harassment.reason_for(off, 99, "广告"), "")

    def test_guard_blocks_after_threshold_and_stays_until_removed(self):
        harassment.save_settings(self.runtime, {"enabled": True, "max_messages": 3, "window_seconds": 60})
        guard = HarassmentGuard()
        verdicts = [guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1000 + index)
                    for index in range(4)]
        self.assertFalse(verdicts[0]["blocked"])
        self.assertFalse(verdicts[2]["blocked"])
        self.assertTrue(verdicts[3]["blocked"])
        self.assertEqual(verdicts[3]["triggered"], "rate")
        later = guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=5000)
        self.assertTrue(later["blocked"])
        # 其他人不受影响。
        other = guard.check(self.runtime, scope="umo:g1", user_id="2002", text="嗨", now=5001)
        self.assertFalse(other["blocked"])
        harassment.remove_entry(self.runtime, scope="umo:g1", user_id="1001")
        guard.reset("umo:g1", "1001")
        self.assertFalse(guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨",
                                     now=5002)["blocked"])

    def test_keyword_blocks_immediately(self):
        harassment.save_settings(self.runtime, {"enabled": True, "keywords": "刷屏"})
        guard = HarassmentGuard()
        verdict = guard.check(self.runtime, scope="umo:g1", user_id="1001", text="开始刷屏", now=1000)
        self.assertTrue(verdict["blocked"])
        self.assertEqual(verdict["triggered"], "keyword")


if __name__ == "__main__":
    unittest.main()
