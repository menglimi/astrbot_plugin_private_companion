"""The AstrBot entry records only the replies that were actually expected."""

import asyncio
import importlib.util
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_host.logging_bridge import PLUGIN_NAME as ASTRBOT_PLUGIN
from tests import test_astrbot_staging_host as _astrbot_test_runtime
from tests.test_management_access import INSTALLATION, NOW
from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.runtime import StorageRuntime


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_companion_v2_no_reply_entry_test"
PACKAGE = ModuleType(PACKAGE_NAME)
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE_NAME] = PACKAGE
SPEC = importlib.util.spec_from_file_location(PACKAGE_NAME + ".main", ROOT / "main.py")
ENTRY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENTRY
SPEC.loader.exec_module(ENTRY)


def private_event():
    event = NS(platform_meta=NS(name="aiocqhttp"),
                message_obj=NS(type=NS(value="FriendMessage"), raw_message={"post_type": "message"}),
                stopped=False)
    event.stop_event = lambda: setattr(event, "stopped", True)
    return event


def group_event():
    event = NS(platform_meta=NS(name="aiocqhttp"),
                message_obj=NS(type=NS(value="GroupMessage"), raw_message={"post_type": "message"}),
                stopped=False)
    event.stop_event = lambda: setattr(event, "stopped", True)
    return event


class MainNoReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-entry-no-reply-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.target = NS(key="configured-role")
        self.staging = NS(
            runtime=self.runtime,
            now=lambda: NOW,
            claims=lambda _event: False,
            target_for_event=lambda _event: None,
            settings=NS(enabled=True),
            settings_error=None,
            _accepting=False,
            _terminated=False,
            session=NS(profile=NS(target=self.target)),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=lambda event: None),
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="registered-role")),
            prepare_core_reply=AsyncMock(return_value=Outcome.failed(
                "host_core_reply_prepare_failed", "optional reply preparation unavailable")),
            group_reply_candidate=AsyncMock(),
            receive_claimed=AsyncMock(side_effect=RuntimeError("synthetic processing failure")),
            terminate=AsyncMock(),
        )
        self.plugin.staging = self.staging

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def test_unmatched_private_target_is_neither_logged_nor_recorded(self):
        # A private message this installation never configured is not a failure
        # of this plugin: it produced no expectation, so it produces no outcome.
        event = private_event()

        with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
            await self.plugin.receive_companion_message(event)

        self.assertEqual(self.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())
        self.assertFalse(event.stopped)

    async def test_configured_private_target_not_accepting_is_recorded(self):
        self.staging.target_for_event = lambda _event: self.target
        event = private_event()

        with self.assertLogs("companion_v2.diagnostics.inbound", level="WARNING") as captured:
            await self.plugin.receive_companion_message(event)

        row = self.runtime.read_one("SELECT * FROM inbound_no_replies", ())
        self.assertEqual(row["reason_code"], "staging_not_accepting")
        self.assertEqual(row["channel"], "private")
        self.assertEqual(row["origin_key"], self.target.key)
        self.assertIn("stage=admission", captured.output[0])
        self.assertIn("expected=true reason=staging_not_accepting", captured.output[0])
        self.assertFalse(event.stopped)

    async def test_optional_preparation_failure_leaves_astrbot_core_reply_available(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        event = private_event()

        await self.plugin.receive_companion_message(event)

        self.assertEqual(self.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())
        self.assertFalse(event.stopped)
        self.assertFalse(getattr(event, "call_llm", False))

    async def test_unregistered_persona_suppresses_astrbot_default_reply(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.prepare_core_reply = AsyncMock(return_value=Outcome.failed(
            "host_persona_unmapped", "persona not registered"))
        event = private_event()

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.assertTrue(event.call_llm)
        self.staging.prepare_core_reply.assert_awaited_once()
        self.staging.receive_claimed.assert_not_awaited()
        row = self.runtime.read_one("SELECT reason_code FROM inbound_no_replies", ())
        self.assertEqual(row["reason_code"], "host_persona_unmapped")

    async def test_persona_lookup_timeout_suppresses_unverified_default_reply(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.prepare_core_reply = AsyncMock(return_value=Outcome.failed(
            "host_native_lookup_timeout__conversation", "conversation lookup timed out"))
        event = private_event()

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.staging.prepare_core_reply.assert_awaited_once()
        self.assertTrue(getattr(event, "call_llm", False))
        row = self.runtime.read_one("SELECT reason_code FROM inbound_no_replies", ())
        self.assertEqual(row["reason_code"], "host_native_lookup_timeout__conversation")

    async def test_unmapped_group_persona_suppresses_astrbot_default_reply(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.group_reply_candidate = AsyncMock(return_value=NS(
            failure=NS(code="host_persona_unmapped")))
        event = group_event()

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.assertTrue(getattr(event, "call_llm", False))
        self.assertEqual(self.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())

    async def test_competing_astrbot_command_passes_through_a_claimed_route(self):
        from astrbot.core.star.filter.command import CommandFilter

        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        event = group_event()
        command_handler = NS(
            handler_module_path="other_plugin.commands",
            handler_full_name="other_plugin.commands.handle",
            event_filters=[CommandFilter("other")],
        )
        event.get_extra = lambda key=None, default=None: (
            {"activated_handlers": [command_handler]}.get(key, default)
            if key is not None else {"activated_handlers": [command_handler]}
        )

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.staging.persona_mapping_for_event.assert_not_awaited()
        self.staging.receive_claimed.assert_not_awaited()

    async def test_unsupported_group_component_passes_through_to_astrbot(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.group_reply_candidate.return_value = NS(
            failure=NS(code="host_group_media_unsupported"))
        event = group_event()

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.staging.receive_claimed.assert_not_awaited()
        self.assertEqual(self.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())

    async def test_group_silent_choice_suppresses_only_astrbot_fallback(self):
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.group_reply_candidate.return_value = NS(
            failure=None, unwrap=lambda: {"admitted": False})
        event = group_event()

        await self.plugin.receive_companion_message(event)

        self.assertFalse(event.stopped)
        self.assertTrue(event.call_llm)
        self.staging.receive_claimed.assert_not_awaited()

    async def test_group_silent_choice_is_logged_without_a_ledger_row(self):
        # A silent group choice is a decision, not a missing reply, so it stays out
        # of the inbound ledger; the log is the only place an operator can see that
        # the turn was left to AstrBot on purpose instead of failing unobserved.
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.group_reply_candidate.return_value = NS(
            failure=None,
            unwrap=lambda: {"admitted": False, "reason": "", "mentioned": False})
        event = group_event()
        # astrbot.api.logger routes this entry module to the plugin's own logger,
        # which AstrBot deliberately isolates from the global one: let this single
        # capture travel up so the assertion still targets the AstrBot hierarchy.
        sink = logging.getLogger("astrbot.plugin." + ASTRBOT_PLUGIN)

        with patch.object(sink, "propagate", True), \
                self.assertLogs("astrbot", level="INFO") as captured:
            await self.plugin.receive_companion_message(event)

        lines = [line for line in captured.output if "群聊本轮保持沉默" in line]
        self.assertEqual(len(lines), 1, captured.output)
        self.assertIn("event_ref=", lines[0])
        self.assertIn("reason=role_choice", lines[0])
        self.assertIn("mentioned=False", lines[0])
        self.assertEqual(self.runtime.read_all("SELECT * FROM inbound_no_replies", ()), ())
        self.assertTrue(event.call_llm)


class StartupRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_retries_startup_until_onebot_account_is_ready(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._host_start_task = None
        plugin._session_settings_warm_task = None
        plugin.staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(side_effect=(
                "host_platform_unavailable", "host_account_unavailable", None)),
            load_existing=AsyncMock(return_value=Outcome.success({"loaded": True})),
            start=AsyncMock(return_value=Outcome.success({"state": "running"})),
        )

        with patch.object(ENTRY, "warm_session_settings", new=AsyncMock()):
            await plugin.initialize()
            task = plugin._host_start_task
            self.assertIsNotNone(task)
            await asyncio.wait_for(task, timeout=3)

        self.assertEqual(plugin.staging.startup_readiness.call_count, 3)
        plugin.staging.load_existing.assert_awaited_once()
        plugin.staging.start.assert_awaited_once()

    async def test_plugin_retries_a_failed_startup_until_the_shared_host_accepts(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._host_start_task = None
        plugin._session_settings_warm_task = None
        plugin._startup_failure_key = None
        plugin._startup_failure_at = 0.0
        staging = NS(
            settings=NS(enabled=True),
            _terminated=False,
            _accepting=False,
            session=None,
            startup_readiness=Mock(return_value=None),
            load_existing=AsyncMock(return_value=Outcome.success({"loaded": True})),
            start=AsyncMock(side_effect=(
                Outcome.failed("host_persona_window_unbound", "window persona is not configured"),
                Outcome.success({"state": "running"}),
            )),
        )
        plugin.staging = staging

        with patch.object(ENTRY, "warm_session_settings", new=AsyncMock()):
            await plugin.initialize()
            await asyncio.wait_for(plugin._host_start_task, timeout=3)

        self.assertEqual(staging.start.await_count, 2)
        self.assertIsNone(plugin._startup_failure_key)


if __name__ == "__main__":
    unittest.main()
