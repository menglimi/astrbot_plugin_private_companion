import logging
from pathlib import Path
import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion import log_buffer
from astrbot_plugin_private_companion.log_buffer import LogRingBuffer, snapshot


class LogBufferTests(unittest.TestCase):
    def test_ring_buffer_filters_by_level_and_query(self):
        buffer = LogRingBuffer(capacity=10)
        logger = logging.getLogger("test.companion.logs")
        logger.handlers = [buffer]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.info("群聊 主动联系 已跳过")
        logger.warning("模型调用超时")
        rows, _ = buffer.snapshot(level="WARNING")
        self.assertEqual([row["message"] for row in rows], ["模型调用超时"])
        rows, _ = buffer.snapshot(query="主动")
        self.assertEqual(len(rows), 1)

    def test_install_is_idempotent_and_filters_shared_host_logs(self):
        host = logging.Logger("astrbot")
        with patch.object(log_buffer, "logger", host), patch.object(log_buffer, "_BUFFER", None):
            first = log_buffer.install_log_buffer()
            self.assertIs(first, log_buffer.install_log_buffer())
            self.assertEqual(host.handlers, [first])

            root = Path(log_buffer.__file__).parent
            for path, message in (
                (root / "main.py", "current plugin"),
                (root.parent / "other_plugin" / "main.py", "another plugin"),
            ):
                host.handle(host.makeRecord(host.name, logging.INFO, str(path), 1, message, (), None))

            rows, _ = first.snapshot()
            self.assertEqual([row["message"] for row in rows], ["current plugin"])

    def test_empty_before_install(self):
        rows, cursor = snapshot(limit=10)
        self.assertIsInstance(rows, list)
        self.assertIsNone(cursor)


if __name__ == "__main__":
    unittest.main()
