from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api_event = types.ModuleType("astrbot.api.event")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    quart = types.ModuleType("quart")
    api.logger = _Logger()
    # page_api.py 顶层 `from astrbot.api.event import MessageChain` 需要此桩，
    # 否则收集期即 ImportError。api 标为包以便按子模块名解析。
    api.__path__ = []
    api.event = api_event
    api_event.MessageChain = type("MessageChain", (), {"__init__": lambda self, *a, **k: None})
    astrbot_path.get_astrbot_data_path = lambda: tempfile.gettempdir()
    quart.request = types.SimpleNamespace()

    async def send_file(*_args, **_kwargs):
        return None

    quart.send_file = send_file
    astrbot.api = api
    astrbot.core = core
    core.utils = utils
    utils.astrbot_path = astrbot_path
    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": api_event,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "quart": quart,
    }


with mock.patch.dict(sys.modules, _astrbot_stubs()):
    plugin_root = Path(__file__).resolve().parents[1]
    package_name = "astrbot_plugin_private_companion"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(plugin_root)]
        package.__package__ = package_name
        sys.modules[package_name] = package
    from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _Plugin:
    def __init__(self, *, enabled: bool, installed: bool, status=None) -> None:
        self.enable_body_monitor_integration = enabled
        self._installed = installed
        self._status = status
        self.config = {}

    def _integrated_plugin_installed(self, name: str) -> bool:
        return name == "astrbot_plugin_body_monitor" and self._installed

    def _body_monitor_integration_status_view(self):
        if isinstance(self._status, Exception):
            raise self._status
        return self._status

    @staticmethod
    def _format_timestamp_elapsed(value):
        return f"time:{int(value)}"


class BodyMonitorPageApiTests(unittest.TestCase):
    def test_disabled_state_wins_over_runtime_and_does_not_leak_context(self) -> None:
        api = PrivateCompanionPageApi(
            _Plugin(
                enabled=False,
                installed=True,
                status={
                    "state": "connected",
                    "targets": ["qq:FriendMessage:123456"],
                    "context": {"metric": "heart_rate", "value": 112},
                },
            )
        )

        result = api._body_monitor_integration_summary()

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["state_text"], "联动已关闭")
        self.assertNotIn("targets", result)
        self.assertNotIn("context", result)

    def test_missing_and_incompatible_states_are_distinct(self) -> None:
        missing = PrivateCompanionPageApi(
            _Plugin(enabled=True, installed=False, status={})
        )._body_monitor_integration_summary()
        incompatible = PrivateCompanionPageApi(
            _Plugin(
                enabled=True,
                installed=True,
                status={"api_compatible": False, "api_version": 2},
            )
        )._body_monitor_integration_summary()

        self.assertEqual(missing["state"], "not_installed")
        self.assertEqual(incompatible["state"], "incompatible")
        self.assertEqual(incompatible["api_version"], 2)
        self.assertEqual(incompatible["supported_api_version"], 1)

    def test_enabled_with_stale_disabled_runtime_is_initializing(self) -> None:
        result = PrivateCompanionPageApi(
            _Plugin(enabled=True, installed=True, status={"state": "disabled"})
        )._body_monitor_integration_summary()

        self.assertEqual(result["state"], "initializing")
        self.assertEqual(result["state_text"], "正在初始化")

    def test_connected_summary_only_exposes_aggregate_batch_counts(self) -> None:
        result = PrivateCompanionPageApi(
            _Plugin(
                enabled=True,
                installed=True,
                status={
                    "state": "ready",
                    "api_version": 1,
                    "last_pull_at": 1234,
                    "last_batch": {
                        "received": 5,
                        "queued": 2,
                        "rejected": 1,
                        "duplicates": 1,
                        "expired": 1,
                        "events": [{"context": {"value": 112}}],
                    },
                },
            )
        )._body_monitor_integration_summary()

        self.assertEqual(result["state"], "connected")
        self.assertEqual(result["last_pull_text"], "time:1234")
        self.assertEqual(
            result["last_batch"],
            {"received": 5, "accepted": 2, "skipped": 1, "duplicate": 1, "expired": 1},
        )
        self.assertNotIn("events", result["last_batch"])

    def test_error_is_short_and_redacts_complete_unified_message_origin(self) -> None:
        result = PrivateCompanionPageApi(
            _Plugin(
                enabled=True,
                installed=True,
                status={"state": "error", "last_error": "route qq:FriendMessage:123456 metric=heart_rate value=108 baseline=76 failed"},
            )
        )._body_monitor_integration_summary()

        self.assertEqual(result["state"], "error")
        self.assertNotIn("123456", result["error"])
        self.assertNotIn("heart_rate", result["error"])
        self.assertNotIn("108", result["error"])
        self.assertEqual(result["error"], "Body Monitor 事件读取失败，请查看服务端日志")

    def test_status_getter_failure_is_reported_without_breaking_overview(self) -> None:
        result = PrivateCompanionPageApi(
            _Plugin(enabled=True, installed=True, status=RuntimeError("probe failed"))
        )._body_monitor_integration_summary()

        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"], "Body Monitor 事件读取失败，请查看服务端日志")

    def test_setting_is_allowed_and_normalized_as_boolean(self) -> None:
        api = PrivateCompanionPageApi(_Plugin(enabled=False, installed=False, status={}))

        self.assertIn("enable_body_monitor_integration", api._allowed_setting_keys())
        self.assertTrue(api._normalize_setting_value("enable_body_monitor_integration", "true"))
        self.assertFalse(api._normalize_setting_value("enable_body_monitor_integration", "false"))


class BodyMonitorPageRuntimeToggleTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabling_resets_runtime_before_kicking_proactive_loop(self) -> None:
        calls = []

        class _Integration:
            def __init__(self):
                self.cursor = 42

            async def set_enabled(self, enabled):
                self.cursor = None
                calls.append(("set_enabled", enabled, self.cursor))

        plugin = _Plugin(enabled=False, installed=True, status={})
        plugin._body_monitor_integration = _Integration()

        async def kick():
            calls.append(("kick", True, plugin._body_monitor_integration.cursor))

        plugin._kick_proactive_loop_once = kick
        api = PrivateCompanionPageApi(plugin)

        api._apply_config_value("enable_body_monitor_integration", True)
        await plugin._body_monitor_integration_toggle_task
        await plugin._body_monitor_integration_kick_task

        self.assertTrue(plugin.enable_body_monitor_integration)
        self.assertEqual(
            calls,
            [("set_enabled", True, None), ("kick", True, None)],
        )

    async def test_disabling_syncs_runtime_without_triggering_pull(self) -> None:
        calls = []

        class _Integration:
            async def set_enabled(self, enabled):
                calls.append(("set_enabled", enabled))

        plugin = _Plugin(enabled=True, installed=True, status={})
        plugin._body_monitor_integration = _Integration()

        async def kick():
            calls.append(("kick", True))

        plugin._kick_proactive_loop_once = kick
        api = PrivateCompanionPageApi(plugin)

        api._apply_config_value("enable_body_monitor_integration", False)
        await plugin._body_monitor_integration_toggle_task

        self.assertEqual(calls, [("set_enabled", False)])


if __name__ == "__main__":
    unittest.main()
