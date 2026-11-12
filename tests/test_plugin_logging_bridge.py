"""Plugin logs reach the host sink once without hijacking other plugin logs."""

import logging
import unittest
from unittest.mock import patch

from astrbot_host.log_buffer import install_log_buffer, snapshot

from astrbot_host.logging_bridge import (
    PLUGIN_NAME, PluginLogBridge, install_logging_bridge, retire_logging_bridge,
)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class PluginLoggingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.entry = "data.plugins.astrbot_plugin_private_companion.main"
        # A previous real entry test may have installed a bridge in this process.
        # Establish the clean lifecycle state whose restoration is asserted here.
        retire_logging_bridge(self.entry)
        self.capture = Capture()
        self.sink = logging.Logger("astrbot.plugin." + PLUGIN_NAME, logging.INFO)
        self.sink.addHandler(self.capture)
        self.get_sink = patch("astrbot_host.logging_bridge.LogManager.get_plugin_logger", return_value=self.sink)
        self.get_sink_mock = self.get_sink.start()
        self.addCleanup(self.get_sink.stop)
        self.addCleanup(retire_logging_bridge, self.entry)

    def test_source_and_relative_host_logs_use_formal_plugin_identity_once(self):
        install_logging_bridge(self.entry)
        install_logging_bridge(self.entry)
        logging.getLogger("companion_v2.conversation.generation").info("正文 %r", ("甲", "乙"))
        logging.getLogger(self.entry.rsplit(".", 1)[0] + ".astrbot_host.controller").warning("宿主状态")
        self.assertEqual(len(self.capture.records), 2)
        self.assertEqual(self.capture.records[0].getMessage(), "正文 ('甲', '乙')")
        self.assertTrue(all(record.name == self.sink.name for record in self.capture.records))
        self.get_sink_mock.assert_called_with(PLUGIN_NAME)

    def test_live_level_changes_are_respected_and_other_logs_stay_on_original_route(self):
        source = logging.getLogger("companion_v2")
        prior = getattr(source, "_private_companion_v2_log_bridge", None)
        level, propagate = prior[1:3] if prior is not None else (source.level, source.propagate)
        root_capture = Capture()
        root = logging.getLogger()
        root.addHandler(root_capture)
        self.addCleanup(root.removeHandler, root_capture)
        install_logging_bridge(self.entry)
        self.sink.setLevel(logging.ERROR)
        logging.getLogger("companion_v2.conversation.flow").warning("隐藏警告")
        logging.getLogger("companion_v2.conversation.flow").error("显示错误")
        logging.getLogger("another_plugin.example").warning("其他插件")
        self.assertEqual([record.getMessage() for record in self.capture.records], ["显示错误"])
        self.assertEqual([record.getMessage() for record in root_capture.records], ["其他插件"])
        self.sink.setLevel(logging.DEBUG)
        logging.getLogger("companion_v2.conversation.flow").debug("显示调试")
        self.assertEqual(self.capture.records[-1].getMessage(), "显示调试")
        retire_logging_bridge(self.entry)
        self.assertEqual((source.level, source.propagate), (level, propagate))
        self.assertFalse(any(isinstance(handler, PluginLogBridge) for handler in source.handlers))

    def test_bridged_and_direct_plugin_logs_reach_the_builtin_buffer_once(self):
        install_log_buffer()
        install_logging_bridge(self.entry)
        install_logging_bridge(self.entry)
        logging.getLogger("companion_v2.tests.bridge_buffer").warning("bridge-buffer-domain")
        self.sink.warning("bridge-buffer-direct")
        self.assertEqual([row["message"] for row in snapshot(query="bridge-buffer-", limit=20)["records"]],
                         ["bridge-buffer-domain", "bridge-buffer-direct"])
        self.assertEqual(len(self.capture.records), 2)

    def test_builtin_buffer_respects_live_plugin_level_and_excludes_other_plugins(self):
        install_logging_bridge(self.entry)
        self.sink.setLevel(logging.ERROR)
        source = logging.getLogger("astrbot_host.tests.bridge_buffer")
        source.warning("bridge-level-hidden")
        source.error("bridge-level-shown")
        logging.getLogger("astrbot.plugin.other_plugin").error("bridge-level-other")
        self.assertEqual([row["message"] for row in snapshot(query="bridge-level-", limit=20)["records"]],
                         ["bridge-level-shown"])
        self.sink.setLevel(logging.DEBUG)
        source.debug("bridge-level-debug")
        self.assertEqual(snapshot(query="bridge-level-debug")["records"][-1]["level"], "DEBUG")

    def test_reinstall_retires_only_stale_buffer_handlers(self):
        root = logging.getLogger()
        stale = logging.NullHandler()
        setattr(stale, "_private_companion_log_buffer", True)
        self.sink.addHandler(stale)
        root.addHandler(stale)
        self.addCleanup(root.removeHandler, stale)
        install_logging_bridge(self.entry)
        self.assertNotIn(stale, self.sink.handlers)
        self.assertNotIn(stale, root.handlers)
        self.assertIn(self.capture, self.sink.handlers)
        for logger in (root, self.sink):
            self.assertEqual(sum(bool(getattr(handler, "_private_companion_log_buffer", False))
                                 for handler in logger.handlers), 1)
