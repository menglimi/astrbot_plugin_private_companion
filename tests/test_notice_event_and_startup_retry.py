"""Regression tests for Issue #811: notice filter pollution and host_provider_required retry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import importlib.util
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_host.bootstrap_provider import resolve_bootstrap_provider
from astrbot_host.multi_settings import MultiHostSettings
from astrbot_host.recalls import is_notice_event
from astrbot_host.settings import HostControlError
from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.runtime import StorageRuntime


ROOT = Path(__file__).resolve().parents[1]


class _FakeComponentType(Enum):
    Plain = "Plain"
    Poke = "Poke"
    Image = "Image"
    Record = "Record"


class NoticeEventFilterTests(unittest.TestCase):
    def test_pure_input_status_notice_is_classified_as_notice(self):
        event = NS(
            message_str="",
            message_obj=NS(
                message_str="",
                message=[],
                raw_message={
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "input_status",
                    "user_id": 200,
                },
            ),
        )
        self.assertTrue(is_notice_event(event))

    def test_pure_recall_and_poke_notices_remain_notices(self):
        cases = (
            NS(
                message_str="",
                message_obj=NS(
                    message_str="",
                    message=[],
                    raw_message={"post_type": "notice", "notice_type": "friend_recall", "message_id": 1},
                ),
            ),
            NS(
                message_str="   \n ",
                message_obj=NS(
                    message_str="  ",
                    message=[NS(type=_FakeComponentType.Plain, text="   ")],
                    raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"},
                ),
            ),
            NS(
                message_str="",
                message_obj=NS(
                    message_str="",
                    message=[NS(type=_FakeComponentType.Poke)],
                    raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "poke"},
                ),
            ),
        )
        for event in cases:
            with self.subTest(raw=event.message_obj.raw_message):
                self.assertTrue(is_notice_event(event))

    def test_polluted_notice_post_type_with_real_text_is_not_classified_as_notice(self):
        raw = {"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"}
        cases = (
            NS(message_str="真实私聊正文", message_obj=NS(message_str="", message=[], raw_message=dict(raw))),
            NS(message_str="", message_obj=NS(message_str="来自 message_obj 的正文", message=[], raw_message=dict(raw))),
            NS(
                message_str="",
                message_obj=NS(
                    message_str="",
                    message=[NS(type=_FakeComponentType.Plain, text="组件正文")],
                    raw_message=dict(raw),
                ),
            ),
        )
        for event in cases:
            with self.subTest(event=event):
                self.assertFalse(is_notice_event(event))

    def test_polluted_notice_post_type_with_media_component_is_not_classified_as_notice(self):
        raw = {"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"}
        for component in (
            NS(type=_FakeComponentType.Image, file="photo.jpg"),
            NS(type=_FakeComponentType.Record, file="voice.amr"),
            NS(type="Video", file="clip.mp4"),
            NS(type="File", file="notes.txt"),
            NS(type="Forward", id="fwd-1"),
            NS(type="Face", id="14"),
        ):
            event = NS(
                message_str="",
                message_obj=NS(message_str="", message=[component], raw_message=dict(raw)),
            )
            with self.subTest(component=component):
                self.assertFalse(is_notice_event(event))

    def test_polluted_real_message_is_not_treated_as_poke_or_recall_notice(self):
        from astrbot_host.pokes import is_poke_notice
        from astrbot_host.recalls import capture_recall

        pure_poke = NS(
            message_str="",
            message_obj=NS(
                message_str="",
                message=[],
                raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "poke"},
            ),
        )
        self.assertTrue(is_poke_notice(pure_poke))

        polluted_poke = NS(
            message_str="刚发来的真消息",
            message_obj=NS(
                message_str="刚发来的真消息",
                message=[NS(type=_FakeComponentType.Plain, text="刚发来的真消息")],
                raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "poke"},
            ),
        )
        self.assertFalse(is_poke_notice(polluted_poke))

        polluted_recall = NS(
            message_str="刚发来的真消息",
            message_obj=NS(
                message_str="刚发来的真消息",
                message=[NS(type=_FakeComponentType.Plain, text="刚发来的真消息")],
                raw_message={"post_type": "notice", "notice_type": "friend_recall", "message_id": 123},
            ),
        )
        self.assertIsNone(capture_recall(NS(), polluted_recall))

    def test_normal_message_and_missing_raw_return_false(self):
        self.assertFalse(is_notice_event(NS(message_obj=NS(raw_message={"post_type": "message"}))))
        self.assertFalse(is_notice_event(NS(message_obj=NS(raw_message=None))))
        self.assertFalse(is_notice_event(NS(message_obj=None)))


def _build_main_import_stubs():
    """Minimal module stubs so ``main.py`` can be imported without the AstrBot SDK."""
    astrbot = ModuleType("astrbot")
    astrbot.__version__ = "4.29.1"
    astrbot_api = ModuleType("astrbot.api")
    plugin_logger = logging.getLogger("astrbot.plugin.test_issue_811")
    astrbot_api.logger = plugin_logger
    astrbot_api.AstrBotConfig = dict
    astrbot_api.llm_tool = lambda *args, **kwargs: (lambda fn: fn)

    astrbot_event = ModuleType("astrbot.api.event")

    class _Filter:
        class EventMessageType:
            ALL = 1
            PRIVATE_MESSAGE = 2
            GROUP_MESSAGE = 4

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: (lambda fn: fn)

    astrbot_event.AstrMessageEvent = object
    astrbot_event.MessageChain = list
    astrbot_event.MessageEventResult = object
    astrbot_event.filter = _Filter()

    astrbot_star = ModuleType("astrbot.api.star")

    class _Star:
        def __init__(self, context=None, config=None):
            self.context = context
            self.config = config

    class _StarTools:
        @staticmethod
        def get_data_dir():
            return ROOT

    astrbot_star.Context = object
    astrbot_star.Star = _Star
    astrbot_star.StarTools = _StarTools
    astrbot_star.register = lambda *args, **kwargs: (lambda cls: cls)

    astrbot_provider = ModuleType("astrbot.api.provider")
    astrbot_provider.ProviderRequest = object

    astrbot_core = ModuleType("astrbot.core")
    astrbot_core_log = ModuleType("astrbot.core.log")
    astrbot_core_log.LogManager = NS(
        GetLogger=lambda name="astrbot": plugin_logger,
        get_plugin_logger=lambda name="astrbot": plugin_logger,
    )
    astrbot_core_star = ModuleType("astrbot.core.star")
    astrbot_core_star_handler = ModuleType("astrbot.core.star.star_handler")
    class _HandlerRegistry(list):
        def get_handlers_by_event_type(self, *_a, **_kw):
            return ()

    astrbot_core_star_handler.EventType = NS(AdapterMessageEvent="adapter")
    astrbot_core_star_handler.star_handlers_registry = _HandlerRegistry()
    astrbot_core_provider = ModuleType("astrbot.core.provider")
    astrbot_core_provider_entities = ModuleType("astrbot.core.provider.entities")
    astrbot_core_provider_entities.LLMResponse = object

    pkg_name = "_companion_v2_issue_811_test_pkg"
    package = ModuleType(pkg_name)
    package.__path__ = [str(ROOT)]

    stubs = {
        "astrbot": astrbot,
        "astrbot.api": astrbot_api,
        "astrbot.api.event": astrbot_event,
        "astrbot.api.star": astrbot_star,
        "astrbot.api.provider": astrbot_provider,
        "astrbot.core": astrbot_core,
        "astrbot.core.log": astrbot_core_log,
        "astrbot.core.star": astrbot_core_star,
        "astrbot.core.star.star_handler": astrbot_core_star_handler,
        "astrbot.core.provider": astrbot_core_provider,
        "astrbot.core.provider.entities": astrbot_core_provider_entities,
        pkg_name: package,
    }
    dummy_modules = (
        "astrbot_host.automatic_backup",
        "astrbot_host.calendar_command",
        "astrbot_host.conversation_reset",
        "astrbot_host.core_reply",
        "astrbot_host.core_reply_closing",
        "astrbot_host.core_reply_decision",
        "astrbot_host.core_reply_native_send",
        "astrbot_host.core_reply_perception",
        "astrbot_host.core_reply_preparation",
        "astrbot_host.core_reply_segmentation",
        "astrbot_host.core_reply_telemetry",
        "astrbot_host.core_reply_tool_dispatch",
        "astrbot_host.core_reply_tool_guard",
        "astrbot_host.core_reply_tools",
        "astrbot_host.core_reply_tracking",
        "astrbot_host.core_reply_turn_gate",
        "astrbot_host.event_routing",
        "astrbot_host.functional_commands",
        "astrbot_host.group_command",
        "astrbot_host.group_debounce",
        "astrbot_host.import_lifecycle",
        "astrbot_host.integration_registry",
        "astrbot_host.log_buffer",
        "astrbot_host.multi_controller",
        "astrbot_host.native",
        "astrbot_host.native_extensions",
        "astrbot_host.native_extensions.host_events",
        "astrbot_host.phone_command",
        "astrbot_host.private_debounce",
        "astrbot_host.private_reply_followup",
        "astrbot_host.qzone_life_publish",
        "astrbot_host.reset_command",
        "astrbot_host.shared_host_migration",
        "astrbot_host.storage_location",
        "astrbot_host.stranger_notice",
        "astrbot_host.upgrade_source",
    )

    class _WildcardModule(ModuleType):
        def __getattr__(self, item):
            if item == "COMPANION_INTAKE_PRIORITY":
                return 100
            if item == "CORE_REPLY_CONTEXT_EXTRA":
                return "_companion_core_reply_context"
            if item == "SILENT_SEND_DENIAL_CODES":
                return frozenset()
            if item == "competing_command_handler":
                return lambda *_a, **_kw: None
            if item == "suppress_default_reply":
                return lambda event: setattr(event, "call_llm", True)
            if item == "warm_session_settings":
                return AsyncMock()
            if item == "AutomaticBackup":
                return lambda _staging: NS(start=lambda: NS())
            if item == "QzoneLifePublisher":
                return lambda _staging: NS(start=lambda: NS())
            if item == "PrivateDebounce":
                return NS(private_plain_text=lambda _event: None)
            if item in ("refuse_stranger", "receive_phone_confirmation",
                        "receive_reset_command", "receive_group_llm_command"):
                return AsyncMock(return_value=None)
            if item == "measure_core_preparation":
                async def _measure(staging, event, target, **_kwargs):
                    return await staging.prepare_core_reply(event, target)
                return _measure
            return lambda *_a, **_kw: None

    for rel in dummy_modules:
        full = pkg_name + "." + rel
        stubs[full] = _WildcardModule(full)
    # Use the real recalls module under the test package so main.py tests the real filter.
    import astrbot_host.recalls as real_recalls
    stubs[pkg_name + ".astrbot_host.recalls"] = real_recalls
    return pkg_name, plugin_logger, stubs


class MainNoticeAndHostProviderRetryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        pkg_name, plugin_logger, stubs = _build_main_import_stubs()
        cls.plugin_logger = plugin_logger
        cls.enterClassContext(patch.dict(sys.modules, stubs))
        spec = importlib.util.spec_from_file_location(pkg_name + ".main", ROOT / "main.py")
        entry = importlib.util.module_from_spec(spec)
        cls.enterClassContext(patch.dict(sys.modules, {spec.name: entry}))
        spec.loader.exec_module(entry)
        cls.ENTRY = entry

    def _new_plugin(self, staging):
        plugin = self.ENTRY.PrivateCompanionV2StagingPlugin.__new__(self.ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._host_start_task = None
        plugin._session_settings_warm_task = None
        plugin._automatic_backup = NS()
        plugin._qzone_life_publisher = NS()
        plugin._startup_failure_key = None
        plugin._startup_failure_at = 0.0
        plugin.staging = staging
        return plugin

    async def test_receive_companion_message_drops_pure_notice_but_processes_polluted_real_message(self):
        now = datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory(prefix="companion-v2-issue-811-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp), installation_id="inst-issue-811")
            try:
                target = NS(key="configured-role")
                claims = Mock(return_value=True)
                prepare = AsyncMock(return_value=Outcome.failed(
                    "host_core_reply_prepare_failed", "optional reply preparation unavailable"))
                staging = NS(
                    runtime=runtime,
                    now=lambda: now,
                    claims=claims,
                    target_for_event=lambda _event: target,
                    settings=NS(enabled=True),
                    settings_error=None,
                    _accepting=True,
                    _terminated=False,
                    session=NS(profile=NS(target=target)),
                    session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=lambda _event: None),
                    persona_mapping_for_event=AsyncMock(return_value=NS(role_id="registered-role")),
                    prepare_core_reply=prepare,
                    receive_claimed=AsyncMock(),
                    terminate=AsyncMock(),
                )
                plugin = self._new_plugin(staging)

                pure_notice = NS(
                    message_str="",
                    platform_meta=NS(name="aiocqhttp"),
                    message_obj=NS(
                        type=NS(value="FriendMessage"),
                        message_str="",
                        message=[],
                        raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"},
                    ),
                    stopped=False,
                )
                await plugin.receive_companion_message(pure_notice)
                claims.assert_not_called()
                prepare.assert_not_awaited()

                polluted_message = NS(
                    message_str="你好呀，还在吗？",
                    platform_meta=NS(name="aiocqhttp"),
                    message_obj=NS(
                        type=NS(value="FriendMessage"),
                        message_str="你好呀，还在吗？",
                        message=[NS(type=_FakeComponentType.Plain, text="你好呀，还在吗？")],
                        raw_message={"post_type": "notice", "notice_type": "notify", "sub_type": "input_status"},
                    ),
                    stopped=False,
                )
                await plugin.receive_companion_message(polluted_message)
                claims.assert_called_once_with(polluted_message)
                prepare.assert_awaited_once()
            finally:
                runtime.close()

    async def test_host_provider_required_in_readiness_retries_warns_and_recovers(self):
        self.assertIn("host_provider_required", self.ENTRY.RETRYABLE_HOST_STARTUP_CODES)
        self.assertTrue(self.ENTRY.PrivateCompanionV2StagingPlugin._retry_startup_failure("host_provider_required"))

        staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(side_effect=(
                HostControlError("host_provider_required", "select the shared model provider"),
                HostControlError("host_provider_required", "select the shared model provider"),
                None,
            )),
            load_existing=AsyncMock(return_value=Outcome.success({"loaded": True})),
            start=AsyncMock(return_value=Outcome.success({"state": "running"})),
        )
        plugin = self._new_plugin(staging)

        with patch.object(self.ENTRY, "warm_session_settings", new=AsyncMock()), \
                patch.object(asyncio, "sleep", new=AsyncMock()), \
                self.assertLogs(self.plugin_logger.name, level="WARNING") as captured:
            await plugin.initialize()
            self.assertIsNotNone(plugin._host_start_task)
            await asyncio.wait_for(plugin._host_start_task, timeout=3)

        self.assertEqual(staging.startup_readiness.call_count, 3)
        staging.load_existing.assert_awaited_once()
        staging.start.assert_awaited_once()
        self.assertIsNone(plugin._startup_failure_key)
        self.assertTrue(
            any("model.provider_id" in line and "host_provider_required" in line and "将自动重试" in line
                for line in captured.output),
            captured.output,
        )

    async def test_host_provider_required_returned_from_readiness_retries_and_recovers(self):
        staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(side_effect=("host_provider_required", None)),
            load_existing=AsyncMock(return_value=Outcome.success({"loaded": True})),
            start=AsyncMock(return_value=Outcome.success({"state": "running"})),
        )
        plugin = self._new_plugin(staging)

        with patch.object(self.ENTRY, "warm_session_settings", new=AsyncMock()), \
                patch.object(asyncio, "sleep", new=AsyncMock()), \
                self.assertLogs(self.plugin_logger.name, level="WARNING") as captured:
            await plugin.initialize()
            self.assertIsNotNone(plugin._host_start_task)
            await asyncio.wait_for(plugin._host_start_task, timeout=3)

        self.assertEqual(staging.startup_readiness.call_count, 2)
        staging.load_existing.assert_awaited_once()
        staging.start.assert_awaited_once()
        self.assertIsNone(plugin._startup_failure_key)
        self.assertTrue(any("host_provider_required" in line and "将自动重试" in line for line in captured.output))

    async def test_host_provider_required_in_start_retries_and_recovers(self):
        staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(return_value=None),
            load_existing=AsyncMock(return_value=Outcome.success({"loaded": True})),
            start=AsyncMock(side_effect=(
                Outcome.failed("host_provider_required", "select a loaded AstrBot text provider"),
                Outcome.success({"state": "running"}),
            )),
        )
        plugin = self._new_plugin(staging)

        with patch.object(self.ENTRY, "warm_session_settings", new=AsyncMock()), \
                patch.object(asyncio, "sleep", new=AsyncMock()), \
                self.assertLogs(self.plugin_logger.name, level="WARNING") as captured:
            await plugin.initialize()
            self.assertIsNotNone(plugin._host_start_task)
            await asyncio.wait_for(plugin._host_start_task, timeout=3)

        self.assertEqual(staging.start.await_count, 2)
        self.assertIsNone(plugin._startup_failure_key)
        self.assertTrue(any("host_provider_required" in line and "将自动重试" in line for line in captured.output))

    async def test_non_retryable_readiness_error_does_not_schedule_retry(self):
        staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(side_effect=HostControlError("host_disabled", "host is disabled")),
            load_existing=AsyncMock(),
            start=AsyncMock(),
        )
        plugin = self._new_plugin(staging)

        with patch.object(self.ENTRY, "warm_session_settings", new=AsyncMock()), \
                self.assertLogs(self.plugin_logger.name, level="WARNING") as captured:
            await plugin.initialize()

        self.assertIsNone(plugin._host_start_task)
        staging.load_existing.assert_not_awaited()
        self.assertTrue(any("host_disabled" in line and "请修正配置或重新加载插件" in line for line in captured.output))


class EmptyProviderAutoFollowTests(unittest.TestCase):
    def test_empty_provider_id_raises_host_provider_required_until_provider_becomes_available(self):
        config = {
            "multi_host": {
                "enabled": True,
                "installation_id": "shared-main",
                "targets": [{
                    "target_id": "t1",
                    "platform_id": "qq-main",
                    "account_id": "100",
                    "bot_id": "bot-a",
                    "participant_id": "200",
                }],
                "personas": [{
                    "bot_id": "bot-a",
                    "persona_id": "original",
                    "role_id": "role-a",
                }],
            },
            "model": {"provider_id": ""},
        }
        settings = MultiHostSettings.parse(config)
        self.assertEqual(settings.common.provider_id, "")
        with self.assertRaises(HostControlError) as ctx:
            settings.require_ready()
        self.assertEqual(ctx.exception.code, "host_provider_required")

        empty_context = NS(get_all_providers=lambda: [], get_using_provider=lambda: None)
        unavailable = resolve_bootstrap_provider(empty_context, None, "")
        self.assertEqual((unavailable.source, unavailable.provider_id), ("unavailable", ""))

        dynamic_provider = NS(meta=lambda: NS(id="astrbot-default-llm", name="Default LLM"))
        ready_context = NS(
            get_all_providers=lambda: [dynamic_provider],
            get_using_provider=lambda: dynamic_provider,
            get_provider_by_id=lambda pid: dynamic_provider if pid == "astrbot-default-llm" else None,
        )
        resolved = resolve_bootstrap_provider(ready_context, None, "")
        self.assertEqual((resolved.source, resolved.provider_id), ("auto", "astrbot-default-llm"))


if __name__ == "__main__":
    unittest.main()
