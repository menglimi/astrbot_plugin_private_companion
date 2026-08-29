# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.integration_status import IntegrationStatusMixin


class _Harness(IntegrationStatusMixin):
    enable_livingmemory_integration = True
    livingmemory_tool_name = "recall_long_term_memory"

    def __init__(self, manager=None) -> None:
        self.context = SimpleNamespace()
        if manager is not None:
            self.context.get_llm_tool_manager = lambda: manager


class LivingMemoryIntegrationStatusTests(unittest.TestCase):
    def test_installed_directory_without_runtime_tool_is_not_available(self) -> None:
        manager = SimpleNamespace(func_list=[])
        harness = _Harness(manager)

        self.assertFalse(harness._livingmemory_available())
        self.assertEqual("", harness._format_livingmemory_guidance())

    def test_installed_but_disabled_plugin_is_reported_as_inactive(self) -> None:
        harness = _Harness(SimpleNamespace(func_list=[]))
        harness._memory_companion_bridge = lambda: None
        with tempfile.TemporaryDirectory() as root:
            harness._livingmemory_plugin_dir = lambda: Path(root)
            status = harness._format_livingmemory_status()

        self.assertIn("插件未加载或召回工具未启用", status)
        self.assertIn("当前不会注入 LivingMemory 召回提示", status)

    def test_detected_memory_companion_incompatibility_is_not_reported_as_missing(self) -> None:
        harness = _Harness(SimpleNamespace(func_list=[]))
        harness._memory_companion_bridge = lambda: None
        harness._memory_companion_presence = lambda: {
            "detected": True,
            "installed": True,
            "loaded": True,
            "activated": True,
            "display_name": "我会牢牢记住你",
            "version": "1.6.0",
            "plugin_dir": "C:/plugins/astrbot_plugin_memory_companion",
            "reason": "capability_contract_mismatch",
        }

        status = harness._format_livingmemory_status()

        self.assertIn("已检测到我会牢牢记住你", status)
        self.assertIn("版本不兼容", status)
        self.assertNotIn("未检测到", status)

    def test_active_livingmemory_tool_is_available_and_prompt_is_guarded(self) -> None:
        tool = SimpleNamespace(
            name="recall_long_term_memory",
            active=True,
            handler_module_path="data.plugins.astrbot_plugin_livingmemory.main",
        )
        harness = _Harness(SimpleNamespace(func_list=[tool]))

        self.assertTrue(harness._livingmemory_available())
        guidance = harness._format_livingmemory_guidance()
        self.assertIn("recall_long_term_memory", guidance)
        self.assertIn("当前工具列表确实提供该工具", guidance)
        self.assertIn("群聊玩笑边界", guidance)
        self.assertIn("不进入核心人物画像", guidance)

    def test_inactive_or_foreign_tool_is_not_available(self) -> None:
        inactive = SimpleNamespace(
            name="recall_long_term_memory",
            active=False,
            handler_module_path="data.plugins.astrbot_plugin_livingmemory.main",
        )
        foreign = SimpleNamespace(
            name="recall_long_term_memory",
            active=True,
            handler_module_path="data.plugins.some_other_plugin.main",
        )

        self.assertFalse(_Harness(SimpleNamespace(func_list=[inactive]))._livingmemory_available())
        self.assertFalse(_Harness(SimpleNamespace(func_list=[foreign]))._livingmemory_available())

    def test_old_host_falls_back_to_loaded_module_only(self) -> None:
        harness = _Harness()
        module = ModuleType("astrbot_plugin_livingmemory.main")
        with patch.dict(sys.modules, {module.__name__: module}):
            self.assertTrue(harness._livingmemory_available())


if __name__ == "__main__":
    unittest.main()
