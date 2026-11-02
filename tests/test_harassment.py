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

    def test_defaults_are_conservative_and_legacy_fields_map(self):
        settings = harassment.read_settings(self.runtime)
        self.assertFalse(settings["enabled"])
        self.assertTrue(settings["dry_run"])
        self.assertTrue(settings["rate_enabled"])
        self.assertEqual(settings["rate_window_seconds"], 300)
        self.assertEqual(settings["rate_max_messages"], 20)
        self.assertEqual(settings["auto_block_hours"], 168)

        # 旧版只有 window_seconds/max_messages/keywords/notice，读取时映射到新字段。
        harassment._write(self.runtime, harassment.SETTINGS_KEY, {
            "enabled": True, "window_seconds": 60, "max_messages": 5,
            "keywords": "广告", "notice": "别刷了",
        })
        legacy = harassment.read_settings(self.runtime)
        self.assertTrue(legacy["enabled"])
        self.assertEqual(legacy["rate_window_seconds"], 60)
        self.assertEqual(legacy["rate_max_messages"], 5)
        self.assertEqual(legacy["keywords_weak"], "广告")
        self.assertEqual(legacy["notice_text"], "别刷了")

    def test_settings_validate_and_keep_unspecified_fields(self):
        saved = harassment.save_settings(self.runtime, {"enabled": True, "rate_max_messages": 5})
        self.assertTrue(saved["enabled"])
        kept = harassment.save_settings(self.runtime, {"auto_block_hours": 24})
        self.assertTrue(kept["enabled"])
        self.assertEqual(kept["auto_block_hours"], 24)
        with self.assertRaises(ValueError):
            harassment.save_settings(self.runtime, {"rate_max_messages": 1})
        with self.assertRaises(ValueError):
            harassment.save_settings(self.runtime, {"unknown": True})
        with self.assertRaises(ValueError):
            harassment.save_settings(self.runtime, {"notice_mode": "loud"})

    def test_entries_support_kind_expiry_and_extend(self):
        entry = harassment.add_entry(self.runtime, scope="umo:g1", user_id="1001", kind="block",
                                     reasons=("rate", "burst"), evidence="10 秒内 9 条",
                                     strike=2, hours=1, at=1000)
        self.assertEqual(entry["kind"], "block")
        self.assertEqual(entry["until"], 1000 + 3600)
        self.assertIsNotNone(harassment.entry_for(self.runtime, "umo:g1", "1001", at=1001))
        self.assertIsNone(harassment.entry_for(self.runtime, "umo:g1", "1001", at=1000 + 3601))
        harassment.add_entry(self.runtime, scope="umo:g1", user_id="1001", kind="block",
                             reasons=("rate",), hours=1, at=2000)
        extended = harassment.extend_entry(self.runtime, scope="umo:g1", user_id="1001", hours=0, at=2000)
        self.assertEqual(extended["until"], 0)
        self.assertTrue(harassment.remove_entry(self.runtime, scope="umo:g1", user_id="1001"))

    def test_audit_is_bounded_and_newest_first(self):
        for index in range(3):
            harassment.record_audit(self.runtime, scope="umo:g1", user_id="1001", action="cooldown",
                                    reasons=("rate",), evidence=str(index), at=1000 + index)
        items = harassment.audit(self.runtime)
        self.assertEqual([item["evidence"] for item in items], ["2", "1", "0"])


class HarassmentGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-harassment-guard-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name), installation_id="guard")
        self.addCleanup(self.runtime.close)
        harassment.save_settings(self.runtime, {"enabled": True, "dry_run": False,
                                                "rate_enabled": True, "rate_window_seconds": 60,
                                                "rate_max_messages": 2,
                                                "burst_enabled": False, "duplicate_enabled": False,
                                                "mention_enabled": False, "link_enabled": False,
                                                "cooldown_seconds": 60, "auto_block_hours": 24,
                                                "strike_block_at": 2, "strike_permanent_at": 3,
                                                "strike_decay_hours": 24,
                                                "notice_mode": "text", "notice_text": "先停一下"})
        self.guard = HarassmentGuard()

    def test_rate_escalates_cooldown_then_block(self):
        first = self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1000)
        self.assertFalse(first["blocked"])
        second = self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1001)
        self.assertFalse(second["blocked"])
        third = self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1002)
        self.assertEqual(third["action"], "cooldown")
        self.assertTrue(third["blocked"])
        self.assertEqual(third["notice"], "先停一下")
        later = self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1050)
        self.assertTrue(later["blocked"])
        # 冷却过期后再犯升级为拉黑 24 小时。
        after = None
        for moment in (2000, 2001, 2002):
            after = self.guard.check(self.runtime, scope="umo:g1", user_id="1001",
                                     text="嗨", now=moment)
        self.assertTrue(after["blocked"])
        self.assertEqual(after["entry"]["kind"], "block")
        self.assertEqual(after["entry"]["until"], 2002 + 24 * 3600)

    def test_another_user_is_not_affected(self):
        self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1000)
        self.guard.check(self.runtime, scope="umo:g1", user_id="1001", text="嗨", now=1001)
        other = self.guard.check(self.runtime, scope="umo:g1", user_id="2002", text="嗨", now=1002)
        self.assertFalse(other["blocked"])

    def test_dry_run_records_without_blocking(self):
        harassment.save_settings(self.runtime, {"dry_run": True, "rate_max_messages": 2})
        guard = HarassmentGuard()
        guard.check(self.runtime, scope="umo:g1", user_id="1001", text="一", now=1000)
        guard.check(self.runtime, scope="umo:g1", user_id="1001", text="二", now=1001)
        verdict = guard.check(self.runtime, scope="umo:g1", user_id="1001", text="三", now=1002)
        self.assertFalse(verdict["blocked"])
        self.assertTrue(verdict["dry_run"])
        self.assertIsNone(harassment.entry_for(self.runtime, "umo:g1", "1001"))
        actions = [item["action"] for item in harassment.audit(self.runtime)]
        self.assertIn("would_cooldown", actions)

    def test_strong_keyword_blocks_immediately(self):
        harassment.save_settings(self.runtime, {"keywords_strong": "广告"})
        verdict = self.guard.check(self.runtime, scope="umo:g1", user_id="1001",
                                   text="这是广告", now=1000)
        self.assertTrue(verdict["blocked"])
        self.assertEqual(verdict["entry"]["kind"], "block")
        self.assertIn("keyword", verdict["entry"]["reasons"])

    def test_duplicate_and_mention_signals(self):
        harassment.save_settings(self.runtime, {"duplicate_enabled": True, "duplicate_max_repeats": 3,
                                                "mention_enabled": True, "mention_max": 2,
                                                "burst_enabled": False, "rate_enabled": False,
                                                "link_enabled": False})
        guard = HarassmentGuard()
        for index in range(3):
            verdict = guard.check(self.runtime, scope="umo:g1", user_id="1001",
                                  text="在吗", mentions=0, now=1000 + index)
        self.assertTrue(verdict["blocked"])
        self.assertIn("duplicate", verdict["entry"]["reasons"])

        harassment.remove_entry(self.runtime, scope="umo:g1", user_id="1001")
        guard.reset("umo:g1", "1001")
        for index in range(2):
            verdict = guard.check(self.runtime, scope="umo:g1", user_id="2002",
                                  text="看", mentions=2, now=2000 + index)
        self.assertTrue(verdict["blocked"])
        self.assertIn("mention", verdict["entry"]["reasons"])


if __name__ == "__main__":
    unittest.main()
