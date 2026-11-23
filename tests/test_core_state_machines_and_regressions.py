"""Focused regression tests for v7.3.0 fixes and core backend state machines."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.functional_commands import FunctionalCommand, parse_functional_command
from astrbot_host.multi_api import MultiHostWebAPI
from astrbot_host.native_extensions.image_native.output_routing import (
    OnlineImageBackend,
    OutputRoutingBackend,
)
from astrbot_host.relay_pending import (
    _read as relay_read,
    add_task as relay_add_task,
    claim_group as relay_claim_group,
    claim_private_receipts as relay_claim_private_receipts,
    mark_waiting_permission as relay_mark_waiting_permission,
    requeue as relay_requeue,
)
from astrbot_host.relay_pending_events import process_group_presence, process_private_receipts
from astrbot_host.voice import NativeVoiceReply
from astrbot_host.web_read_material import read_page
from companion_v2.agenda.models import Frequency, Schedule
from companion_v2.agenda.planning_config import configured_annual_calendars
from companion_v2.agenda.planning_inputs import planning_calendar_context
from companion_v2.agenda.weather_reading import WeatherReading
from companion_v2.capabilities.native import parse_result
from companion_v2.conversation import busy_gate
from companion_v2.conversation.participation_prompts import compact_observation_facts
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.device import adb
from companion_v2.execution.models import Outcome, SideEffect, encode, stamp
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.memory.search import SearchIndex
from companion_v2.proactive.exploration import ExplorationEvents
from companion_v2.proactive.external_materials import WeatherSnapshot
from companion_v2.proactive.greetings import GreetingContactEvents
from companion_v2.proactive.news_events import NewsContactEvents, ParseBatch
from companion_v2.proactive.prompts import local_time_fields
from companion_v2.proactive.scheduling import ContactSchedule
from companion_v2.product import PRODUCT_VERSION
from companion_v2.reality import provider as REALITY
from companion_v2.relationships.profile import ConversationRelationshipProfileContext, FIELD_LIMITS
from companion_v2.role_work.life import rest_gate
from companion_v2.role_work.state.maintenance import StateMaintenance
from companion_v2.screen.interpretation import ScreenInterpretationService
from companion_v2.screen.peek import ScreenPeekService
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.sqlite_path import file_uri, readonly_uri
from companion_v2.system_prompts.configuration import (
    GROUP as SYSTEM_PROMPT_GROUP,
    RoleSystemPromptConfigurationOwner,
    configuration_fields as system_prompt_fields,
    target_for as system_prompt_role_target,
)
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW, ROLE, SCOPE
from tests.test_reality_together_native import FakeReality, request as reality_request


ROOT = Path(__file__).resolve().parents[1]


class VoiceAndCommandRegressionTests(unittest.TestCase):
    def test_voice_timeout_defaults_unified_to_120s(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["voice_reply"]["items"]["timeout_seconds"]["default"], 120)
        self.assertEqual(VoiceReplyPolicy().timeout_seconds, 120)
        self.assertEqual(
            inspect.signature(NativeVoiceReply.render).parameters["timeout_seconds"].default,
            120,
        )

    def test_compact_diagnostics_confirm_and_cancel_commands_refine_intent(self):
        cases = (
            ("陪伴答疑确认 diag-1", FunctionalCommand("private", "diagnostics_confirm", "diag-1")),
            ("陪伴应用答疑建议 diag-2", FunctionalCommand("private", "diagnostics_confirm", "diag-2")),
            ("陪伴答疑取消 diag-3", FunctionalCommand("private", "diagnostics_cancel", "diag-3")),
            ("陪伴取消答疑建议 diag-4", FunctionalCommand("private", "diagnostics_cancel", "diag-4")),
            ("陪伴答疑确认", FunctionalCommand("private", "manage_diagnostics", "")),
            ("陪伴答疑取消", FunctionalCommand("private", "manage_diagnostics", "")),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_functional_command(text, channel="private"), expected)

    def test_participation_prompts_partial_settings_no_key_error(self):
        facts = {
            "mood": {"top_label": "轻松", "top_score": 0.9, "tension": 0.1, "tension_level": "low"},
            "topic_threads": [],
        }
        self.assertEqual(compact_observation_facts({}, facts), {})
        self.assertEqual(
            compact_observation_facts({"enable_group_persona_denoise": True}, facts),
            {"denoise": True},
        )

    def test_python_sources_have_no_utf8_bom(self):
        checked = (
            "astrbot_host/core_reply_context.py",
            "companion_v2/agenda/planning_inputs.py",
            "companion_v2/agenda/repository.py",
            "companion_v2/conversation/voice_reply.py",
            "companion_v2/delivery/plan.py",
        )
        for rel in checked:
            raw = (ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"unexpected UTF-8 BOM in {rel}")


class DateAndSolarTermRegressionTests(unittest.TestCase):
    def test_maintenance_today_uses_solar_term_port_and_configured_holiday_country(self):
        port = NS(lookup=lambda year: {f"{year}-09-23": "秋分"})
        captured_settings = {
            "holiday_enabled": True,
            "holiday_country": "WEEKEND_ONLY",
            "automatic_solar_terms_enabled": True,
        }
        fake_maintainer = NS(
            app=NS(
                messages=NS(solar_term_port=port),
                perception_settings=NS(capture=lambda _ctx, _at: {"settings": captured_settings}),
            )
        )
        at = datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc)
        today = StateMaintenance._today(
            fake_maintainer,
            {"planning_timezone": "Asia/Shanghai", "planning_annual_calendars": ""},
            at,
            context=object(),
        )
        self.assertIsNotNone(today)
        self.assertEqual(today["automatic_solar_terms"]["status"], "available")
        self.assertEqual(today["automatic_solar_terms"]["today"], ["秋分"])
        self.assertEqual(today["holiday"]["source"], "weekday_fallback")

    def test_planning_calendar_context_propagates_solar_term_port_and_holiday_country(self):
        port = NS(lookup=lambda year: {f"{year}-10-01": "测试节气"})
        at = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
        result = planning_calendar_context(
            at,
            at + timedelta(days=1),
            ZoneInfo("Asia/Shanghai"),
            holiday_country="WEEKEND_ONLY",
            solar_term_port=port,
        )
        self.assertEqual(result["today"]["automatic_solar_terms"]["status"], "available")
        self.assertEqual(result["today"]["automatic_solar_terms"]["today"], ["测试节气"])
        self.assertEqual(result["today"]["holiday"]["source"], "weekday_fallback")

    def test_local_time_fields_uses_builtin_calendar_without_polluting_workday_holiday(self):
        provider = NS(
            is_holiday=lambda day: day.isoformat() == "2026-10-01",
            get_holiday_detail=lambda _day: (True, "国庆节"),
            is_workday=lambda day: day.isoformat() != "2026-10-01",
        )
        with patch("companion_v2.conversation.date_context._chinese_calendar", provider):
            # Holiday without manual annual calendar falls back to chinese_calendar.
            holiday_fields = local_time_fields(
                datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc), "Asia/Shanghai"
            )
            self.assertEqual(holiday_fields["holiday"], "国庆节")
            self.assertFalse(holiday_fields["is_workday"])
            self.assertTrue(holiday_fields["is_weekend"])

            # Normal workday must NOT set fields["holiday"] = "工作日".
            workday_fields = local_time_fields(
                datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc), "Asia/Shanghai"
            )
            self.assertNotIn("holiday", workday_fields)
            self.assertTrue(workday_fields["is_workday"])
            self.assertFalse(workday_fields["is_weekend"])

        # Configured makeup workday sets holiday label and flips is_workday=True.
        calendars = configured_annual_calendars(json.dumps([{
            "schema": "companion.annual-calendar.v1",
            "year": 2026,
            "holidays": [],
            "makeup_workdays": [{"date": "2026-10-10", "name": "国庆调休"}],
            "solar_terms": [],
        }], ensure_ascii=False))
        makeup_fields = local_time_fields(
            datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc), "Asia/Shanghai", calendars
        )
        self.assertEqual(makeup_fields["holiday"], "国庆调休")
        self.assertTrue(makeup_fields["is_workday"])
        self.assertFalse(makeup_fields["is_weekend"])


class HostLayerRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_tool_repeat_hook_priority_runs_after_delivery_guard(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        guard_idx = source.index("def guard_companion_core_delivery")
        repeat_idx = source.index("def suppress_companion_native_tool_repeat")
        guard_header = source[max(0, guard_idx - 120):guard_idx]
        repeat_header = source[max(0, repeat_idx - 120):repeat_idx]
        self.assertIn("priority=-1000000", guard_header)
        self.assertIn("priority=-1000001", repeat_header)

    async def test_relay_requeue_preserves_waiting_permission_and_recovers_send_exceptions(self):
        runtime = StorageRuntime.open(data_root=self.root / "relay", installation_id="install-relay")
        self.addCleanup(runtime.close)
        future = 9_999_999_999.0

        # 1) Unrelated message while waiting_permission keeps waiting_permission & reply_text.
        perm_id = relay_add_task(runtime, {
            "kind": "private_receipt",
            "state": "waiting_reply",
            "target_user_id": "u-1",
            "source_kind": "private",
            "source_id": "owner-1",
            "question": "晚上来吗",
            "confirm_before_report": True,
            "expires_at": future,
        })
        relay_mark_waiting_permission(runtime, perm_id, "我七点到")
        controller = NS(runtime=runtime, native_types=NS(), session=None)
        target = NS(platform_id="qq", account_id="100", participant_id="u-1", group_id="g-1")
        unrelated = NS(message_str="今天天气不错", bot=None, platform_meta=NS(name="aiocqhttp"))
        with patch("astrbot_host.relay_pending_events._ask_permission", AsyncMock(return_value=True)) as ask_mock:
            await process_private_receipts(controller, unrelated, target)
        ask_mock.assert_not_awaited()
        saved = relay_read(runtime)["tasks"][perm_id]
        self.assertEqual(saved["state"], "waiting_permission")
        self.assertEqual(saved["reply_text"], "我七点到")

        # 2) Exception while sending group relay settles failed instead of leaving state='sending'.
        group_id = relay_add_task(runtime, {
            "kind": "group_wait",
            "state": "waiting",
            "group_id": "g-1",
            "target_user_id": "u-1",
            "text": "帮我带句话",
            "expires_at": future,
        })
        ctx = NS(actor=NS(principal=NS(native_account_id="u-1")))
        with patch("astrbot_host.relay_pending_events._send_group", AsyncMock(side_effect=RuntimeError("boom"))):
            await process_group_presence(controller, unrelated, target, ctx)
        self.assertEqual(relay_read(runtime)["tasks"][group_id]["state"], "failed")

        # 3) Explicit requeue directly after permission claim returns waiting_permission.
        claimed = relay_claim_private_receipts(runtime, "u-1", permission=True)
        self.assertEqual(len(claimed), 1)
        relay_requeue(runtime, perm_id)
        self.assertEqual(relay_read(runtime)["tasks"][perm_id]["state"], "waiting_permission")

        # 4) Cancellation during finish() after group delivery settles as "sent" (never requeued).
        sent_group_id = relay_add_task(runtime, {
            "kind": "group_wait",
            "state": "waiting",
            "group_id": "g-1",
            "target_user_id": "u-1",
            "text": "只发一次",
            "expires_at": future,
        })
        orig_to_thread = asyncio.to_thread

        async def cancel_on_finish(fn, *args, **kwargs):
            if fn.__name__ == "finish":
                raise asyncio.CancelledError()
            return await orig_to_thread(fn, *args, **kwargs)

        with patch("astrbot_host.relay_pending_events._send_group", AsyncMock(return_value={"state": "delivered"})), \
                patch("astrbot_host.relay_pending_events.asyncio.to_thread", side_effect=cancel_on_finish):
            with self.assertRaises(asyncio.CancelledError):
                await process_group_presence(controller, unrelated, target, ctx)
        self.assertEqual(relay_read(runtime)["tasks"][sent_group_id]["state"], "sent")

        # 5) Cancellation during finish() after private report settles as "reported" (never requeued).
        report_id = relay_add_task(runtime, {
            "kind": "private_receipt",
            "state": "waiting_reply",
            "target_user_id": "u-2",
            "source_user": "admin",
            "question": "到了吗",
            "confirm_before_report": False,
            "expires_at": future,
        })
        target_u2 = NS(platform_id="qq-main", account_id="100", group_id="g-1", participant_id="u-2")
        with patch("astrbot_host.relay_pending_events._report_receipt", AsyncMock(return_value=True)), \
                patch("astrbot_host.relay_pending_events.asyncio.to_thread", side_effect=cancel_on_finish):
            with self.assertRaises(asyncio.CancelledError):
                await process_private_receipts(controller, NS(message_str="刚到"), target_u2)
        self.assertEqual(relay_read(runtime)["tasks"][report_id]["state"], "reported")

        # 6) Cancellation during finish() after confirmed permission report settles as "reported".
        with patch("astrbot_host.relay_pending_events._report_receipt", AsyncMock(return_value=True)), \
                patch("astrbot_host.relay_pending_events.asyncio.to_thread", side_effect=cancel_on_finish):
            with self.assertRaises(asyncio.CancelledError):
                await process_private_receipts(controller, NS(message_str="可以"), target)
        self.assertEqual(relay_read(runtime)["tasks"][perm_id]["state"], "reported")

    async def test_multi_host_web_api_wraps_routes_with_http_handler(self):
        registered = []
        context = NS(registered_web_apis=registered)
        controller = NS(context=context)
        api = MultiHostWebAPI(controller)

        class BoundOwner:
            def __init__(self):
                self.controller = NS(target_id="first", role_id="role-a")

            async def broken(self):
                raise RuntimeError("unexpected failure")

        owner = BoundOwner()
        api._register_routes(
            lambda route, handler, methods, desc: registered.append((route, handler, methods, desc)),
            [("/test/broken", owner.broken, ["GET"], "broken")],
        )
        _, wrapped, _, _ = registered[0]
        self.assertIs(wrapped.__self__, owner)
        self.assertEqual(wrapped.__name__, "broken")
        response = await wrapped()
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["message"], "plugin_web_internal_error")

    def test_account_session_api_view_handles_controller_without_settings_attr(self):
        controller = NS(config={"multi_host": {"enabled": False}}, context=None, native_types=None)
        api = AccountSessionWebAPI(controller)
        view = api._view()
        self.assertEqual(view["mode"], "multi")
        self.assertFalse(view["enabled"])

    async def test_web_read_material_offloads_pdf_extraction_to_worker_thread(self):
        main_thread = threading.current_thread()
        seen_threads = []

        def fake_extract(raw, index, name, *, maximum_chars):
            seen_threads.append(threading.current_thread())
            return NS(text="pdf body")

        class FakeContent:
            def __init__(self, payload: bytes):
                self._payload = payload

            async def iter_chunked(self, _size):
                yield self._payload

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/pdf"}
            content = FakeContent(b"%PDF-1.4")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def get(self, *_args, **_kwargs):
                return FakeResponse()

        with patch("astrbot_host.web_read_material.address_allowed", return_value=True), \
                patch("astrbot_host.files.parsing.extract_document", side_effect=fake_extract):
            result = await read_page(FakeSession(), "https://example.com/doc.pdf")
        self.assertEqual(result["status"], "full")
        self.assertEqual(result["text"], "pdf body")
        self.assertEqual(len(seen_threads), 1)
        self.assertIsNot(seen_threads[0], main_thread)

    def test_image_native_legacy_route_forwards_network_proxy_config(self):
        router = OutputRoutingBackend(
            {
                "enabled": True,
                "backend": "online_api",
                "network": {"proxy": "http://127.0.0.1:7890"},
                "endpoints": [{
                    "id": "ep-1",
                    "enabled": True,
                    "platform": "openai",
                    "base_url": "https://api.example.invalid/v1",
                    "api_key": "sk-test",
                    "model": "gpt-image-1",
                }],
            },
            self.root / "img",
        )
        backends = list(router._routes("generate"))
        self.assertEqual(len(backends), 1)
        self.assertIsInstance(backends[0], OnlineImageBackend)
        self.assertEqual(backends[0].network.get("proxy"), "http://127.0.0.1:7890")

    def test_search_index_handles_hash_and_percent_in_path(self):
        special = self.root / "dir#1%2"
        special.mkdir(parents=True)
        index = SearchIndex(special)
        self.assertTrue((special / "memory-search.sqlite3").is_file())
        self.assertIn("%23", file_uri(special / "memory-search.sqlite3"))
        self.assertTrue(readonly_uri(special / "memory-search.sqlite3").endswith("?mode=ro"))


class CoreStateMachineRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_relationship_profile_context_stores_runtime_and_applies_prompt_override(self):
        runtime = StorageRuntime.open(data_root=self.root / "rel", installation_id=SCOPE.installation_id)
        self.addCleanup(runtime.close)
        ctx = NS(
            key="ctx-rel",
            scope=SCOPE,
            role_binding=NS(role=ROLE),
            actor=NS(subject=SubjectRef(SCOPE.installation_id, SubjectKind.HUMAN, "user-1")),
        )
        owner = RoleSystemPromptConfigurationOwner(runtime)
        owner.write(
            system_prompt_role_target(ctx),
            system_prompt_fields(),
            changes={f"{SYSTEM_PROMPT_GROUP}.relationship": "自定义关系指导"},
            reset=(),
            expected_revision=0,
            observed_at=NOW,
        )
        profile_ctx = ConversationRelationshipProfileContext(runtime)
        self.assertIs(profile_ctx.runtime, runtime)
        snapshot = {
            "role_id": ROLE.subject_id,
            "subject_id": "user-1",
            "context_key": "ctx-rel",
            "revision": 1,
            "fields": {**{name: "" for name in FIELD_LIMITS}, "default_nickname": "阿明"},
        }
        rendered = profile_ctx.instruction(snapshot, ctx)
        self.assertIn("自定义关系指导", rendered)

    def test_adb_schedule_ime_restore_cancels_previous_timer_on_consecutive_input(self):
        restored = []
        client = NS(executable="adb", serial="emulator-5554", ime_set=lambda ime: restored.append(ime))
        key = adb._ime_key(client)
        try:
            adb.schedule_ime_restore(client, "com.example/.Ime", 60.0)
            first_timer = adb._IME_RESTORE_TIMERS.get(key)
            self.assertIsInstance(first_timer, threading.Timer)
            adb.schedule_ime_restore(client, "com.example/.Ime", 60.0)
            second_timer = adb._IME_RESTORE_TIMERS.get(key)
            self.assertIsInstance(second_timer, threading.Timer)
            self.assertIsNot(first_timer, second_timer)
            self.assertTrue(first_timer.finished.is_set())
        finally:
            with adb._IME_RESTORE_LOCK:
                timer = adb._IME_RESTORE_TIMERS.pop(key, None)
                if timer is not None:
                    timer.cancel()
                adb._IME_RESTORE_TOKENS.pop(key, None)
                adb._IME_PENDING_RESTORE.pop(key, None)
        self.assertEqual(restored, [])

    def test_screen_services_evict_terminal_tasks_from_pending(self):
        runtime = StorageRuntime.open(data_root=self.root / "screen", installation_id="install-screen")
        self.addCleanup(runtime.close)
        for cls in (ScreenInterpretationService, ScreenPeekService):
            service = cls(runtime)
            service.app = object()
            service.runner = object()
            service.tasks = NS(read=lambda _ctx, _tid: Outcome.success(NS(state="succeeded")))
            service.pending["task-done"] = {"context": object(), "role_instruction": "x" * 100}
            service.advance()
            self.assertNotIn("task-done", service.pending)

    def test_insomnia_same_day_early_morning_window_does_not_inflate_by_24h(self):
        events = GreetingContactEvents.__new__(GreetingContactEvents)
        prefs = NS(values={
            "insomnia_night_daily_limit": 3,
            "insomnia_night_start": "00:30",
            "insomnia_night_end": "04:00",
            "quiet_timezone": "Asia/Shanghai",
        })
        zone = ZoneInfo("Asia/Shanghai")
        # 00:45 -> slot 0; 02:00 -> slot 1; 03:30 -> slot 2 (span must be 3.5h, not 27.5h).
        s0 = events._night(prefs, datetime(2026, 9, 25, 0, 45, tzinfo=zone).astimezone(timezone.utc))
        s1 = events._night(prefs, datetime(2026, 9, 25, 2, 0, tzinfo=zone).astimezone(timezone.utc))
        s2 = events._night(prefs, datetime(2026, 9, 25, 3, 30, tzinfo=zone).astimezone(timezone.utc))
        self.assertEqual([s0["slot"], s1["slot"], s2["slot"]], [0, 1, 2])
        self.assertAlmostEqual((s0["expires"] - s0["opened"]).total_seconds(), 3.5 * 3600 / 3, places=1)
        # Cross-midnight 23:30-03:00 still works across midnight.
        cross = NS(values={**prefs.values, "insomnia_night_start": "23:30", "insomnia_night_end": "03:00"})
        c0 = events._night(cross, datetime(2026, 9, 24, 23, 45, tzinfo=zone).astimezone(timezone.utc))
        c2 = events._night(cross, datetime(2026, 9, 25, 2, 30, tzinfo=zone).astimezone(timezone.utc))
        self.assertEqual((c0["night_key"], c0["slot"]), ("2026-09-24", 0))
        self.assertEqual((c2["night_key"], c2["slot"]), ("2026-09-24", 2))

    def test_rest_gate_explicit_window_not_bypassed_by_daytime_message_and_backlog_aligns(self):
        runtime = StorageRuntime.open(data_root=self.root / "rest", installation_id=SCOPE.installation_id)
        self.addCleanup(runtime.close)
        ctx = NS(key="ctx-rest", scope=SCOPE, role_binding=NS(role=ROLE))
        settings = rest_gate.Settings.from_values({
            **rest_gate.DEFAULTS,
            "enable_rest_reply_simulation": True,
            "rest_reply_active_windows": "23:00-07:00",
            "rest_reply_probability": 0,
            "rest_reply_awake_grace_minutes": 30,
            "sleep_timezone": "Asia/Shanghai",
        })
        zone = ZoneInfo("Asia/Shanghai")
        # Daytime message at 22:50 must NOT write awake_sleep or bypass 23:05 rest window.
        day_now = datetime(2026, 9, 25, 22, 50, tzinfo=zone).astimezone(timezone.utc)
        self.assertEqual(rest_gate.deferral(runtime, ctx, text="你好", now=day_now, settings=settings), 0.0)
        doc = rest_gate.document(runtime, ctx)
        self.assertIsNone(doc["awake_until"])
        self.assertIsNone(doc["awake_sleep"])

        night_now = datetime(2026, 9, 25, 23, 5, 12, 123456, tzinfo=zone).astimezone(timezone.utc)
        self.assertGreater(rest_gate.deferral(runtime, ctx, text="在干嘛呀", now=night_now, settings=settings), 0.0)

        # Waking inside explicit rest window activates stable awake_grace across seconds and local midnight.
        self.assertEqual(rest_gate.deferral(runtime, ctx, text="快醒醒！", now=night_now, settings=settings), 0.0)
        five_sec_later = datetime(2026, 9, 25, 23, 5, 17, 654321, tzinfo=zone).astimezone(timezone.utc)
        self.assertEqual(rest_gate.deferral(runtime, ctx, text="在干嘛呀", now=five_sec_later, settings=settings), 0.0)

        before_midnight = datetime(2026, 9, 25, 23, 55, 10, tzinfo=zone).astimezone(timezone.utc)
        after_midnight = datetime(2026, 9, 26, 0, 10, 45, tzinfo=zone).astimezone(timezone.utc)
        self.assertEqual(rest_gate.deferral(runtime, ctx, text="快醒醒！", now=before_midnight, settings=settings), 0.0)
        self.assertEqual(rest_gate.deferral(runtime, ctx, text="还没睡吗", now=after_midnight, settings=settings), 0.0)
        key_before = rest_gate.sleep_context(runtime, ctx, now=before_midnight, settings=settings)["window_key"]
        key_after = rest_gate.sleep_context(runtime, ctx, now=after_midnight, settings=settings)["window_key"]
        key_next_night = rest_gate.sleep_context(
            runtime, ctx,
            now=datetime(2026, 9, 26, 23, 5, 12, tzinfo=zone).astimezone(timezone.utc),
            settings=settings,
        )["window_key"]
        self.assertEqual(key_before, key_after)
        self.assertNotEqual(key_before, key_next_night)

        # backlog_prompt pairs the LAST limit timestamps with the LAST limit texts.
        entries = [
            {"at": datetime(2026, 9, 25, 1, i, tzinfo=zone).astimezone(timezone.utc).isoformat()}
            for i in range(6)
        ]
        texts = [f"msg-{i}" for i in range(6)]
        prompt = rest_gate.backlog_prompt(entries, texts, timezone="Asia/Shanghai", max_messages=3)
        self.assertIn("01:03｜msg-3", prompt)
        self.assertIn("01:04｜msg-4", prompt)
        self.assertIn("01:05｜msg-5", prompt)
        self.assertNotIn("msg-0", prompt)

    async def test_reality_lookup_and_cancel_do_not_leak_prepared_rows(self):
        plugin = FakeReality()
        provider = REALITY.NativeRealityProvider(plugin, self.root / "reality", version=PRODUCT_VERSION)
        req_control = reality_request(
            provider, "reality.device.control", invocation="inv-prep", arguments={"alias": "desk", "is_on": True}
        )
        scope = REALITY.scope_key(req_control["context"])
        prepared_op, _ = provider.store.begin(scope, req_control)

        # Argless lookup must NOT create a row or return itself.
        argless = reality_request(provider, "reality.operation.lookup", invocation="inv-argless", arguments={})
        with self.assertRaises(REALITY.NativeRealityError) as raised:
            await provider.execute(argless, before_dispatch=lambda: True)
        self.assertEqual(raised.exception.code, "native_operation_not_found")
        self.assertEqual(len(provider.store.latest(scope)), 1)

        # Explicit lookup returns original row without adding any ledger entry.
        explicit_lookup = reality_request(
            provider, "reality.operation.lookup", invocation="inv-lookup-1",
            arguments={"operation_id": prepared_op["id"]},
        )
        await provider.execute(explicit_lookup, before_dispatch=lambda: True)
        self.assertEqual(len(provider.store.latest(scope)), 1)

        # Cancel settles BOTH target (cancelled) and cancel invocation itself (succeeded/committed).
        cancel_req = reality_request(
            provider, "reality.operation.cancel", invocation="inv-cancel-1",
            arguments={"operation_id": prepared_op["id"]},
        )
        cancelled = await provider.execute(cancel_req, before_dispatch=lambda: True)
        parse_result(cancelled, request=cancel_req, side_effect=SideEffect.LOCAL)
        self.assertEqual((cancelled["state"], cancelled["effect"]), ("succeeded", "committed"))
        self.assertEqual(cancelled["result"]["cancelled_operation_id"], prepared_op["id"])
        # Replay of same cancel invocation returns identical settled receipt (not leaked prepared).
        replayed = await provider.execute(cancel_req, before_dispatch=lambda: True)
        self.assertEqual(replayed, cancelled)
        # No prepared rows remain in the ledger.
        self.assertFalse(any(row["state"] == "prepared" for row in provider.store.latest(scope)))
        # Looking up cancelled target reports failed / not_committed instead of unknown.
        after_cancel = await provider.lookup(req_control, before_dispatch=lambda: True)
        self.assertEqual((after_cancel["state"], after_cancel["effect"]), ("failed", "not_committed"))

    def test_busy_gate_respects_rescheduled_and_cancelled_exceptions(self):
        settings = busy_gate.Settings.from_values({**busy_gate.DEFAULTS, "busy_reply_gate_enabled": True})
        original_start = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
        moved_start = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
        schedule = json.loads(encode(Schedule(original_start, "UTC", Frequency.ONCE)))
        rescheduled_item = {
            "kind": "role_plan",
            "state": "accepted",
            "title": "开会讨论方案",
            "detail": "",
            "presence": "",
            "duration_minutes": 60,
            "schedule": schedule,
            "exceptions": {"0": moved_start.isoformat()},
        }
        # Not busy at old time, busy at moved time.
        self.assertEqual(busy_gate.busy_items([rescheduled_item], now=original_start + timedelta(minutes=15), settings=settings), ())
        found = busy_gate.busy_items([rescheduled_item], now=moved_start + timedelta(minutes=15), settings=settings)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], moved_start + timedelta(minutes=60))

        # Cancelled occurrence via None exception is not busy.
        cancelled_item = {**rescheduled_item, "exceptions": {"0": None}}
        self.assertEqual(busy_gate.busy_items([cancelled_item], now=original_start + timedelta(minutes=15), settings=settings), ())

    async def test_weather_reading_shields_shared_fetch_and_cancels_replaced_task(self):
        gate = asyncio.Event()
        cancelled_flags = []

        class SlowPort:
            refresh_minutes = 5

            async def fetch(self, location):
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    cancelled_flags.append(location.name)
                    raise
                return WeatherSnapshot(
                    provider="open-meteo", location=location.name, observed_at=NOW,
                    condition="晴", temperature_c=22.0, humidity_pct=50.0, wind_kph=5.0, alerts=(),
                )

        reading = WeatherReading()
        loc_a = NS(name="上海", latitude=31.2, longitude=121.5, timezone="Asia/Shanghai")
        loc_b = NS(name="北京", latitude=39.9, longitude=116.4, timezone="Asia/Shanghai")
        port = SlowPort()

        task1 = asyncio.create_task(reading.read(port, loc_a, max_age=timedelta(minutes=30), now=lambda: NOW))
        await asyncio.sleep(0)
        task2 = asyncio.create_task(reading.read(port, loc_a, max_age=timedelta(minutes=30), now=lambda: NOW))
        await asyncio.sleep(0)
        # Cancelling task1 must NOT cancel the shared fetch for task2.
        task1.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task1
        gate.set()
        res2 = await task2
        self.assertEqual(res2["state"], "live")
        self.assertEqual(res2["location"], "上海")

        # Switching location cancels old pending task.
        gate.clear()
        task3 = asyncio.create_task(reading.read(port, loc_b, max_age=timedelta(minutes=30), now=lambda: NOW, force=True))
        await asyncio.sleep(0)
        old_pending = reading.pending
        self.assertIsNotNone(old_pending)
        gate.set()
        await reading.read(port, loc_a, max_age=timedelta(minutes=30), now=lambda: NOW)
        await asyncio.sleep(0)
        self.assertTrue(old_pending.cancelled() or old_pending.done())
        await task3

    async def test_news_and_exploration_locking_closed_gate_and_aclose(self):
        runtime = StorageRuntime.open(data_root=self.root / "news", installation_id="install-news")
        self.addCleanup(runtime.close)
        contacts = NS(runtime=runtime, tasks=NS(now=lambda: NOW))
        active = 0
        max_active = 0
        started = asyncio.Event()
        release = asyncio.Event()

        class LockedPort:
            fingerprint = "fp-1"

            async def fetch(self):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                started.set()
                try:
                    await release.wait()
                finally:
                    active -= 1
                return ParseBatch(items=(), provider="test", fetched_at=NOW)

        port = LockedPort()
        news = NewsContactEvents(contacts, port)
        t1 = asyncio.create_task(news._maintain_role_feed(port))
        t2 = asyncio.create_task(news._maintain_role_feed(port))
        await started.wait()
        await asyncio.sleep(0.02)
        self.assertEqual(max_active, 1)
        release.set()
        await asyncio.gather(t1, t2)

        # close() + aclose() stops in-flight workers and blocks new spawns.
        release.clear()
        started.clear()
        news._maintenance_workers["k1"] = asyncio.create_task(news._maintain_role_feed(port))
        await started.wait()
        await news.aclose()
        self.assertTrue(news._closed)
        news.maintain_role({"news_source_kind": "host"})
        self.assertEqual(news._maintenance_workers, {})

        exp = ExplorationEvents(contacts, None, port)
        stuck = asyncio.create_task(asyncio.sleep(60))
        exp._workers["k"] = stuck
        await exp.aclose()
        self.assertTrue(exp._closed)
        self.assertTrue(stuck.cancelled())

    async def test_proactive_schedule_advance_and_open_loop_claim_semantics(self):
        # 1) advance() and advance_async() do NOT reset cursor on early break (< 16 rows).
        for is_async in (False, True):
            sched = ContactSchedule.__new__(ContactSchedule)
            sched._closed = False
            sched._advancing = False
            sched._planning = None
            sched._cursor = 0
            sched._native_cursor = 0
            sched._workers = {}
            sched.tasks = NS(now=lambda: NOW)
            sched._note_coordinator = lambda *_args, **_kwargs: None
            sched._row_available = lambda *_args, **_kwargs: True
            rows = [
                {"sequence": 1, "execution_path": "astrbot_core", "origin_key": "k1"},
                {"sequence": 2, "execution_path": "astrbot_core", "origin_key": "k2"},
                {"sequence": 3, "execution_path": "astrbot_core", "origin_key": "k3"},
            ]
            sched._begin_tick = lambda: (NOW, rows, 1, 0, len(rows))
            sched._plan_one = lambda row, _now: ("plan", row["sequence"])
            sched._activate_plan = lambda _row, _plan, avail, _now: avail - 1
            if is_async:
                await sched.advance_async()
            else:
                sched.advance()
            self.assertEqual(sched._native_cursor, 1)

        # 2) Open-loop claim happens in _activate_plan only after gates pass, never while sleeping.
        claimed = []
        fake_repo = NS(claim_open_loop_followup=lambda memo_id, *, at: claimed.append(memo_id) or True)
        sched = ContactSchedule.__new__(ContactSchedule)
        sched.runtime = object()
        sched._workers = {}
        sched.contacts = NS(platform_circuit=NS(cooldown_until=lambda _ctx, *, at: None))
        sched._account_available = lambda _ctx: True
        sched._busy_hold = lambda _ctx, _origin, _at: None
        sched._note_window = lambda *_args, **_kwargs: None
        sched.notify = lambda: None
        plan = (
            NS(key="ctx-ol"),
            NS(opportunity_id="opp-1"),
            (NS(purpose="followup"),),
            "instr",
            {"open_loop_event": {"memo_id": "memo-1"}},
        )
        row = {"origin_key": "ctx-ol"}
        # Held by sleep gate -> must NOT claim memo-1.
        sched._sleep_hold = lambda _ctx, _origin, _at: NOW + timedelta(hours=1)
        with patch("companion_v2.proactive.scheduling.AgendaService", return_value=NS(repository=fake_repo)):
            avail = sched._activate_plan(row, plan, 2, NOW)
        self.assertEqual(avail, 2)
        self.assertEqual(claimed, [])

        # Gate open -> claims memo-1 and launches worker.
        sched._sleep_hold = lambda _ctx, _origin, _at: None
        sched._run = AsyncMock(return_value=None)
        with patch("companion_v2.proactive.scheduling.AgendaService", return_value=NS(repository=fake_repo)):
            avail = sched._activate_plan(row, plan, 2, NOW)
            worker = sched._workers.get("ctx-ol")
            if worker is not None:
                await worker
        self.assertEqual(avail, 1)
        self.assertEqual(claimed, ["memo-1"])

    async def test_role_state_maintenance_failure_marks_retryable_and_pulls_next_at_forward(self):
        runtime = StorageRuntime.open(data_root=self.root / "maint", installation_id=SCOPE.installation_id)
        self.addCleanup(runtime.close)
        future_next = stamp(NOW + timedelta(hours=2))
        with runtime.transaction() as conn:
            conn.execute(
                """INSERT INTO role_state_schedules
                   (context_key, context_json, settings_json, next_at, revision, enabled, result_json)
                   VALUES (?, '{}', '{}', ?, 1, 1, NULL)""",
                ("sched-1", future_next),
            )
        maint = StateMaintenance.__new__(StateMaintenance)
        maint.closed = False
        maint.runtime = runtime
        maint.tasks = NS(now=lambda: NOW)
        maint.patrol = lambda: None
        maint.results = {}
        maint._scan_after = None
        failing_task = asyncio.create_task(asyncio.sleep(0))
        await failing_task
        bad_future = asyncio.get_running_loop().create_future()
        bad_future.set_exception(RuntimeError("model boom"))
        maint.workers = {"ctx-1": bad_future}
        maint.worker_schedules = {"ctx-1": "sched-1"}
        # Force return right after collecting completed workers by seeding a second pending sentinel.
        sentinel = asyncio.get_running_loop().create_future()
        maint.workers["ctx-hold"] = sentinel
        try:
            maint.advance()
        finally:
            sentinel.cancel()
        self.assertTrue(maint.results["ctx-1"]["retryable"])
        self.assertEqual(maint.results["ctx-1"]["state"], "failed")
        row = runtime.read_one("SELECT next_at, result_json FROM role_state_schedules WHERE context_key = ?", ("sched-1",))
        self.assertEqual(row["next_at"], stamp(NOW + timedelta(minutes=5)))
        self.assertTrue(json.loads(row["result_json"])["retryable"])

    def test_clean_reply_text_preserves_repeated_segment_split_markers(self):
        from astrbot_host.core_reply_cleanup import clean_reply_text
        from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER

        body = f"第一段正常内容。\n{LLM_SEGMENT_MARKER}\n第二段正常内容。\n{LLM_SEGMENT_MARKER}\n第三段正常内容。"
        cleaned, removed = clean_reply_text(body)
        self.assertEqual(removed, 0)
        self.assertEqual(cleaned.count(LLM_SEGMENT_MARKER), 2)

        repeated_line = "这是一段超过二十四个字符的完全重复的正文句子用于测试去重。"
        with_dup = f"{repeated_line}\n{LLM_SEGMENT_MARKER}\n{repeated_line}\n{LLM_SEGMENT_MARKER}\n结尾。"
        cleaned_dup, removed_dup = clean_reply_text(with_dup)
        self.assertEqual(removed_dup, 1)
        self.assertEqual(cleaned_dup.count(LLM_SEGMENT_MARKER), 2)


class GenerationVoiceSilentFallbackTests(MessageFixture, unittest.IsolatedAsyncioTestCase):
    async def test_generation_silent_fallback_with_voice_enabled_does_not_crash(self):
        from companion_v2.conversation.foreground import ModelDraft
        from companion_v2.conversation.generation import LocalReplyGenerator
        from companion_v2.conversation.segmentation import SegmentationPolicy
        from companion_v2.model_gateway.models import Amount, ProviderResponse

        self.responses["primary"] = ProviderResponse(
            "generated", ModelDraft("visible-before-cleanup", ()), Amount(10, 10), "r-silent",
        )
        self.generator = LocalReplyGenerator(
            self.messages,
            self.gateway,
            segmentation=SegmentationPolicy(enabled=False),
            voice_policy=VoiceReplyPolicy(probability=1.0),
            split=lambda text: tuple(text.splitlines(keepends=True)),
        )
        with patch("companion_v2.conversation.generation.prepare_reply", side_effect=ValueError("empty")):
            batch = await self.generate()
        row = self.messages.batch(batch)
        self.assertEqual(row["state"], "ready")
        self.assertEqual(row["presentation_action"], "silent")
        self.assertEqual(row["part_count"], 0)


if __name__ == "__main__":
    unittest.main()
