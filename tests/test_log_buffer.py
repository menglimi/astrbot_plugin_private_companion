import logging
import unittest

from astrbot_host.log_buffer import install_log_buffer, snapshot


class LogBufferTests(unittest.TestCase):
    def setUp(self):
        install_log_buffer()
        self.logger = logging.getLogger("companion_v2.tests.log-buffer")
        self.logger.setLevel(logging.DEBUG)

    def test_recent_records_filter_by_level_and_query(self):
        self.logger.warning("needle-warning-%s", "alpha")
        self.logger.info("needle-info-%s", "beta")
        found = snapshot(query="needle-", level="WARNING", limit=50)
        self.assertTrue(found["records"])
        self.assertTrue(all(row["level"] in ("WARNING", "ERROR", "CRITICAL")
                            for row in found["records"]))
        self.assertTrue(any("needle-warning" in row["message"] for row in found["records"]))
        self.assertFalse(any("needle-info" in row["message"] for row in found["records"]))

    def test_unowned_loggers_are_not_captured(self):
        logging.getLogger("astrbot.core.other").info("not-companion-log")
        found = snapshot(query="not-companion-log", limit=50)
        self.assertEqual(found["records"], [])

    def test_snapshot_is_bounded_and_ordered(self):
        for index in range(12):
            self.logger.info("bounded-%02d", index)
        found = snapshot(query="bounded-", limit=5)
        self.assertEqual(len(found["records"]), 5)
        self.assertLess(found["records"][0]["id"], found["records"][-1]["id"])
        self.assertEqual(found["records"][-1]["message"], "bounded-11")


if __name__ == "__main__":
    unittest.main()
