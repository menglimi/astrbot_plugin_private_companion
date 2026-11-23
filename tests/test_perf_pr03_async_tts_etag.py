"""Tests for async tick offloading, management API unblocking, startup coalescing, concurrent TTS, and HTTP ETag/304."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import timedelta
import io
import json
import os
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image as PILImage
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from astrbot.api.web import PluginMultiDict, PluginRequest, bind_request_context
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.host.tick_offload import (
    advance_background_async,
    advance_state_maintenance_async,
)
from companion_v2.qzone.capability import CAPABILITIES as QZONE_CAPABILITIES

from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.acquaintance_api import AcquaintanceWebAPI
from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA,
    activate_core_reply_delivery_guard,
    prepare_core_reply_event,
)
from astrbot_host.core_reply_segmentation import prepare_core_reply_segmentation
from astrbot_host.core_reply_voice import (
    MAX_CONCURRENT_TTS,
    prepare_core_reply_voice_message,
)
from astrbot_host.http_etag import (
    compute_etag,
    conditional_response,
    etag_matches,
)
from astrbot_host.memory_page_api import MEMORY_PLUGIN_ID, MemoryPageAPI
from astrbot_host.model_api import ModelWebAPI
from astrbot_host.multi_api import MultiHostWebAPI
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.multi_session import MultiHostSession
from astrbot_host.native_perception_api import NativePerceptionWebAPI
from astrbot_host.semantic_api import SemanticWebAPI
from astrbot_host.setup_api import SetupWebAPI

from tests.group_host_support import GroupFixture
from tests.native_adapter_support import FakePlatform
from tests.test_core_reply_voice import FakeDelivery, FakeEvent, _allow
from tests.test_host_application import HostFixture
from tests.test_main_no_reply import ENTRY
from tests.test_management_access import NOW


class _Query(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


def _fake_request(*, username="admin", query=None, body=b"", headers=None, live_asgi=False):
    async def read():
        return body

    hdrs = dict(headers or {})
    if live_asgi:
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hdrs.items()]
        raw = Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": raw_headers,
            "app": object(),
        })
        return PluginRequest(raw, username=username, plugin_name="astrbot_plugin_private_companion")

    return NS(
        username=username,
        query=_Query(query or {}),
        body=read,
        headers=hdrs,
        plugin_name="astrbot_plugin_private_companion",
    )


def _fake_controller(*, target=None, application=None, runtime=None, settings=None, config=None):
    rt = runtime if runtime is not None else NS(installation_id="install-rt")
    ctx = target if target is not None else NS(
        key="ctx-key-rt",
        scope=ScopeRef("install-rt", "onebot", ScopeKind.PRIVATE, "200", "100", "100", "c1", "s1"),
        role_binding=NS(role=SubjectRef("install-rt", SubjectKind.BOT, "role-a")),
    )
    app = application if application is not None else NS(runtime=rt, now=lambda: NOW)
    session = NS(application=app, profile=NS(target=ctx))
    return NS(
        _control_lock=asyncio.Lock(),
        _lifecycle_lock=asyncio.Lock(),
        _terminated=False,
        _accepting=False,
        _multi_host=False,
        session=session,
        last_session=None,
        runtime=rt,
        settings=settings if settings is not None else NS(
            profile_id="profile-rt",
            platform_id="onebot",
            bot_id="100",
            account_id="100",
            participant_id="200",
            role_id="role-a",
            persona_id="persona-a",
        ),
        config=config if config is not None else {},
        data_root=None,
        target_id="target-rt",
        context=NS(),
        native_extensions=None,
        now=lambda: NOW,
        status=lambda: {"mode": "multi", "state": "running", "targets": []},
    )


async def _assert_off_loop_and_lock_free(testcase, controller, coro, entered, release):
    task = asyncio.create_task(coro)
    try:
        testcase.assertTrue(await asyncio.to_thread(entered.wait, 5), "worker thread was never entered")
        testcase.assertFalse(task.done(), "route must not block the event loop")
        await asyncio.wait_for(controller._control_lock.acquire(), timeout=1.0)
        controller._control_lock.release()
    finally:
        release.set()
    return await task


class AsyncTickUnitOffloadTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_maintenance_tick_units_run_off_loop_without_blocking_coroutines(self):
        host = self.host()
        app = host.application
        # Start task supervisor generation without launching the full background drive task.
        app.tasks.start(expected_generation=0)
        app.state = "running"
        app.background._closed = False
        app.state_maintenance.closed = False
        loop = asyncio.get_running_loop()
        scheduler = app._maintenance_rotation(loop)

        expected_units = (
            "background-planners",
            "memory-schedule",
            "life-runtime",
            "phone-actions",
            "state-maintenance",
            "memory-activity",
            "memory-game",
            "calendar-planning",
            "daily-outfit",
            "growth-progress",
            "self-review",
            "group-participation",
            "expression-style",
            "presence",
            "proactive-deliveries",
            "journal-schedule",
            "background-deliveries",
        )
        self.assertEqual(scheduler.names, expected_units)

        main_thread = threading.current_thread()
        entered = threading.Event()
        release = threading.Event()
        worker_threads = {}

        orig_settle = app.background.delivery.settle
        orig_patrol = app.state_maintenance.patrol
        orig_proactive = app.proactive.advance_deliveries

        def traced_settle():
            worker_threads["background"] = threading.current_thread()
            entered.set()
            release.wait(5)
            return orig_settle()

        def traced_patrol():
            worker_threads["state-maintenance"] = threading.current_thread()
            return orig_patrol()

        def traced_proactive():
            worker_threads["proactive-deliveries"] = threading.current_thread()
            return orig_proactive()

        heartbeat_ticks = 0

        async def heartbeat():
            nonlocal heartbeat_ticks
            while not release.is_set():
                heartbeat_ticks += 1
                await asyncio.sleep(0.005)

        with patch.object(app.background.delivery, "settle", side_effect=traced_settle), \
                patch.object(app.state_maintenance, "patrol", side_effect=traced_patrol), \
                patch.object(app.proactive, "advance_deliveries", side_effect=traced_proactive):
            app._next_contact_delivery_check = 0.0
            tick_task = asyncio.create_task(scheduler.advance())
            beat_task = asyncio.create_task(heartbeat())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                await asyncio.sleep(0.03)
                self.assertGreater(heartbeat_ticks, 0, "event loop stalled while tick unit blocked")
            finally:
                release.set()
            report = await tick_task
            await beat_task

        self.assertTrue(report.completed)
        for name in ("background", "state-maintenance", "proactive-deliveries"):
            self.assertIn(name, worker_threads)
            self.assertIsNot(worker_threads[name], main_thread, f"{name} ran on event loop thread")

    async def test_tick_offload_honors_patched_advance_methods(self):
        host = self.host()
        app = host.application
        calls = []

        with patch.object(app.background, "advance", side_effect=lambda **kw: calls.append(("bg", kw)) or False), \
                patch.object(app.state_maintenance, "advance", side_effect=lambda: calls.append(("sm", {}))):
            await advance_background_async(app.background, planners=False)
            await advance_background_async(app.background, planners=True)
            await advance_state_maintenance_async(app.state_maintenance)

        self.assertEqual(calls, [("bg", {"planners": False}), ("bg", {}), ("sm", {})])

    async def test_startup_rotation_coalesces_offload_and_rolls_back_on_sqlite_lock(self):
        import sqlite3
        from companion_v2.host.tick_budget import StagedOffload, TickScheduler, TickUnit

        state = {"gate": None, "scanned": [], "applied": [], "cleaned": 0}
        fail_first = {"active": True}

        def stage_a():
            def scan():
                state["scanned"].append(("a", threading.current_thread()))
                return "a-val"
            return StagedOffload("offload", scan=scan, apply=lambda v: (state["applied"].append("a"), v)[1])

        def stage_b():
            def scan():
                state["scanned"].append(("b", threading.current_thread()))
                if fail_first["active"]:
                    fail_first["active"] = False
                    raise sqlite3.OperationalError("database is locked")
                return "b-val"
            return StagedOffload("offload", scan=scan, apply=lambda v: (state["applied"].append("b"), v)[1])

        def stage_c():
            prev = state["gate"]
            state["gate"] = "opened"
            def cleanup():
                state["cleaned"] += 1
                state["gate"] = prev
            return StagedOffload(
                "offload",
                scan=lambda: (state["scanned"].append(("c", threading.current_thread())), "c-val")[1],
                apply=lambda v: (state["applied"].append("c"), v)[1],
                cleanup=cleanup,
            )

        scheduler = TickScheduler((
            TickUnit("a", lambda: None, stage=stage_a),
            TickUnit("b", lambda: None, stage=stage_b),
            TickUnit("c", lambda: None, stage=stage_c),
        ), budget_seconds=1.0)

        first = await scheduler.advance()
        self.assertFalse(first.completed)
        self.assertEqual(first.ran, ("a",))
        self.assertEqual(first.results["b"]["deferred"], "sqlite_operational_error")
        self.assertEqual(scheduler.cursor, 1)
        self.assertIsNone(state["gate"], "unapplied staged unit gate must roll back on transient SQLite lock")
        self.assertEqual(state["cleaned"], 1)

        second = await scheduler.advance()
        self.assertTrue(second.completed)
        self.assertEqual(second.ran, ("b", "c"))
        self.assertEqual(state["gate"], "opened")
        self.assertEqual(state["applied"], ["a", "b", "c"])
        main_thread = threading.current_thread()
        self.assertTrue(all(t is not main_thread for _, t in state["scanned"]))
        # b and c coalesced into the same worker thread pass on Rotation 1 retry
        self.assertEqual(state["scanned"][-2][1], state["scanned"][-1][1])


class ManagementApiUnblockingTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_page_export_snapshot_runs_off_loop(self):
        main_thread = threading.current_thread()
        entered, release = threading.Event(), threading.Event()
        seen_threads = []

        def slow_read_all(sql, params):
            seen_threads.append(threading.current_thread())
            entered.set()
            release.wait(5)
            return ()

        controller = _fake_controller(runtime=NS(installation_id="i", read_all=slow_read_all))
        api = MemoryPageAPI(controller)
        task = asyncio.create_task(api.export_snapshot(target_plugin_id=MEMORY_PLUGIN_ID, selected_date="2026-05-01"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            self.assertFalse(task.done())
        finally:
            release.set()
        snapshot = await task
        self.assertEqual(snapshot["selected_date"], "2026-05-01")
        self.assertTrue(seen_threads)
        self.assertTrue(all(t is not main_thread for t in seen_threads))

    async def test_semantic_api_get_and_reindex_offload_and_schedule_on_loop(self):
        main_thread = threading.current_thread()
        entered, release = threading.Event(), threading.Event()
        seen_threads = {}

        class _FakeDB:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=()):
                seen_threads["db"] = threading.current_thread()
                entered.set()
                release.wait(5)
                return NS(fetchone=lambda: (0,))

        def schedule_memory(ctx):
            seen_threads["schedule_memory"] = threading.current_thread()

        service = NS(
            closed=False,
            status=lambda: {"model_binding": "emb-1"},
            ensure_reaction_catalog=lambda ctx: "owner-r",
            owner=lambda ctx: "owner-s",
            check=lambda ctx: None,
            store=NS(
                db=lambda: _FakeDB(),
                reaction_collection_summary=lambda owner: {"totals": {}, "recent": []},
                list_recent_page=lambda owner, kind, after="", limit=24: ([], None),
            ),
            gateway=NS(clear=lambda: seen_threads.setdefault("clear", threading.current_thread())),
            schedule_memory=schedule_memory,
        )
        rt = NS(installation_id="install-sem")
        ctx = NS(
            key="ctx-sem",
            scope=ScopeRef("install-sem", "onebot", ScopeKind.PRIVATE, "200", "100", "100", "c1", "s1"),
            role_binding=NS(role=NS(subject_id="role-a")),
        )
        app = NS(runtime=rt, semantics=service)
        controller = _fake_controller(target=ctx, application=app, runtime=rt)
        api = SemanticWebAPI(controller)
        ok_policy = NS(feature_access=lambda *a, **kw: Outcome.success(True))

        body = json.dumps({
            "action": "reindex",
            "target_id": "profile-rt",
            "role_id": "role-a",
        }).encode("utf-8")
        with patch("astrbot_host.semantic_api.request", _fake_request(body=body)), \
                patch("astrbot_host.semantic_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.semantic_api.ObjectAccessPolicy", return_value=ok_policy):
            task = asyncio.create_task(api.post())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.assertFalse(task.done())
            finally:
                release.set()
            res = await task

        self.assertEqual(res["status"], "ok", res)
        self.assertIsNot(seen_threads["db"], main_thread)
        self.assertIsNot(seen_threads["clear"], main_thread)
        self.assertIs(seen_threads["schedule_memory"], main_thread)

        # Verify thread-safe concurrent thumbnail cache access.
        buf = io.BytesIO()
        PILImage.new("RGB", (16, 16), color=(10, 20, 30)).save(buf, format="PNG")
        png_bytes = buf.getvalue()
        thumb_service = NS(reactions=NS(captured=lambda item: NS(content=png_bytes, frames=())))
        item = {"owner": "owner-r", "id": "img-1"}
        results = await asyncio.gather(*(asyncio.to_thread(api.reaction_thumbnail, thumb_service, item) for _ in range(8)))
        self.assertTrue(all(val.startswith("data:image/jpeg;base64,") for val in results))

    async def test_acquaintance_strangers_and_member_settings_release_control_lock_and_keep_auth_order(self):
        entered, release = threading.Event(), threading.Event()
        ok_policy = NS(feature_access=lambda *a, **kw: Outcome.success(True))

        def slow_read_all(sql, params=()):
            entered.set()
            release.wait(5)
            return ()

        rt = NS(installation_id="install-acq", read_all=slow_read_all)
        controller = _fake_controller(runtime=rt)
        controller.resolved_settings = lambda: controller.settings
        api = AcquaintanceWebAPI(controller)

        with patch("astrbot_host.acquaintance_api.request", _fake_request()), \
                patch("astrbot_host.acquaintance_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.acquaintance_api.ObjectAccessPolicy", return_value=ok_policy):
            res = await _assert_off_loop_and_lock_free(self, controller, api.strangers(), entered, release)
        self.assertEqual(res["status"], "ok", res)

        # Unauthenticated caller must get dashboard_identity_required before query/body validation.
        with patch("astrbot_host.acquaintance_api.request", _fake_request(username="", query={"bad": "1"})):
            unauth = await api.member_settings()
        self.assertEqual(unauth["status"], "error")
        self.assertEqual(unauth["data"]["code"], "dashboard_identity_required")

    async def test_setup_status_and_account_session_save_session_are_non_blocking_and_lock_free(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            entered, release = threading.Event(), threading.Event()
            controller = _fake_controller()
            controller.data_root = Path(directory)

            def slow_status():
                entered.set()
                release.wait(5)
                return {"mode": "multi", "state": "running"}

            controller.status = slow_status
            setup_api = SetupWebAPI(controller)
            with patch("astrbot_host.setup_api.request", _fake_request()), \
                    patch("astrbot_host.upgrade_setup.UpgradeSetup.plan", new=AsyncMock(return_value=({"present": False}, None))):
                res = await _assert_off_loop_and_lock_free(self, controller, setup_api.status(), entered, release)
            self.assertEqual(res["status"], "ok", res)

            # AccountSessionWebAPI.save_session must not hold _control_lock while awaiting topology/sp.
            select_entered = asyncio.Event()
            select_release = asyncio.Event()

            async def slow_select(_key):
                select_entered.set()
                await select_release.wait()
                return {"native": True, "managed": False, "unified_msg_origin": "qq:FriendMessage:200"}

            controller.session_topology = NS(select=slow_select)
            acct_api = AccountSessionWebAPI(controller)
            body = json.dumps({"session_key": "k1", "llm_enabled": True}).encode("utf-8")
            with patch("astrbot_host.account_session_api.request", _fake_request(body=body)), \
                    patch("astrbot_host.management_identity.require_management_operator", return_value=object()), \
                    patch("astrbot_host.account_session_api.sp.get_async", new=AsyncMock(return_value={})), \
                    patch("astrbot_host.account_session_api.sp.put_async", new=AsyncMock()):
                task = asyncio.create_task(acct_api.save_session())
                try:
                    await asyncio.wait_for(select_entered.wait(), timeout=2.0)
                    await asyncio.wait_for(controller._control_lock.acquire(), timeout=1.0)
                    controller._control_lock.release()
                finally:
                    select_release.set()
                saved = await task
            self.assertEqual(saved["status"], "ok", saved)

    async def test_native_perception_runtime_snapshot_releases_control_lock(self):
        entered, release = threading.Event(), threading.Event()
        controller = _fake_controller()
        api = NativePerceptionWebAPI(controller)

        def slow_op(*args, **kwargs):
            entered.set()
            release.wait(5)
            return object()

        cfg_read = Outcome.success(NS(values={
            "role.state.planning_timezone": "Asia/Shanghai",
            "role.state.planning_annual_calendars": "",
            "role.state.planning_weather_enabled": False,
            "role.state.planning_weather_location": "",
            "role.state.planning_weather_max_age_minutes": 30,
        }))
        controller.session.application.configuration = NS(read=lambda *a, **kw: cfg_read)
        with patch("astrbot_host.native_perception_api.request", _fake_request(query={"scope": "role"})), \
                patch("astrbot_host.native_perception_api.dashboard_operator", side_effect=slow_op), \
                patch("astrbot_host.native_perception_api.StateManagement", return_value=NS(life=NS(authorize=lambda op: None))), \
                patch("astrbot_host.native_perception_api.PerceptionManagement", return_value=NS(
                    status=lambda op: Outcome.success({"settings": {"calendar_enabled": False}}))):
            res = await _assert_off_loop_and_lock_free(self, controller, api.runtime_snapshot(), entered, release)
        self.assertEqual(res["status"], "ok", res)


class InitialPlatformCoalesceTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def _plugin_stub(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None
        plugin._session_settings_warm_task = None
        plugin._qzone_life_publisher = NS(close=AsyncMock())
        plugin._automatic_backup = NS(close=AsyncMock())
        return plugin

    async def test_initialize_followed_by_platform_loaded_performs_single_cold_start_with_qzone(self):
        class _DynamicPlatformManager:
            def __init__(self, mapping):
                self._mapping = mapping

            @property
            def platform_insts(self):
                return tuple(self._mapping.values())

        self.native.platform_manager = _DynamicPlatformManager(self.native.platforms)
        saved_platforms = self.native.platforms.copy()
        self.native.platforms.clear()

        plugin = self._plugin_stub()
        plugin.PLATFORM_STARTUP_COALESCE_SECONDS = 1.5
        plugin.PLATFORM_BATCH_COALESCE_SECONDS = 0.03

        assemble_count = 0
        close_count = 0
        orig_assemble = AstrBotMultiController._assemble
        orig_close = MultiHostSession.close

        async def count_assemble(ctrl, manifest):
            nonlocal assemble_count
            assemble_count += 1
            return await orig_assemble(ctrl, manifest)

        async def count_close(session, **kwargs):
            nonlocal close_count
            close_count += 1
            return await orig_close(session, **kwargs)

        with patch.dict(os.environ, {"QZONE_COOKIE": ""}), \
                patch.object(ENTRY, "warm_session_settings", new=AsyncMock()), \
                patch.object(AstrBotMultiController, "_assemble", count_assemble), \
                patch.object(MultiHostSession, "close", count_close):
            await plugin.initialize()
            self.assertEqual(assemble_count, 0, "initialize() must defer assembly while zero platforms are loaded")
            self.assertIsNotNone(plugin._host_start_task)
            self.assertFalse(plugin._host_start_task.done())

            # Simulate AstrBot loading two platform adapters ~40ms later with rapid hooks.
            await asyncio.sleep(0.04)
            self.native.platforms["qq-main"] = saved_platforms["qq-main"]
            await plugin.on_companion_platform_loaded()
            await asyncio.sleep(0.005)
            self.native.platforms["qq-second"] = saved_platforms["qq-second"]
            await plugin.on_companion_platform_loaded()

            await asyncio.wait_for(plugin._host_start_task, timeout=10)

            self.assertEqual(assemble_count, 1, "expected exactly 1 cold-start assembly")
            self.assertEqual(close_count, 0, "expected 0 session teardowns during initial boot")
            self.assertIsNotNone(self.controller.session)
            self.assertIsNotNone(self.controller.session.application.qzone)
            self.assertTrue(
                set(QZONE_CAPABILITIES).issubset(set(self.controller.session.application.external.providers))
            )
            self.assertTrue(self.controller._accepting)
            self.assertTrue(self.controller.native_bridges_current())

            # Cancel warmup task cleanly before tearDown.
            if plugin._session_settings_warm_task is not None and not plugin._session_settings_warm_task.done():
                plugin._session_settings_warm_task.cancel()
                await asyncio.gather(plugin._session_settings_warm_task, return_exceptions=True)

    async def test_initialize_offline_fallback_starts_after_coalesce_timeout(self):
        self.native.platforms.clear()
        plugin = self._plugin_stub()
        plugin.PLATFORM_STARTUP_COALESCE_SECONDS = 0.04
        plugin.PLATFORM_BATCH_COALESCE_SECONDS = 0.01

        with patch.dict(os.environ, {"QZONE_COOKIE": ""}), \
                patch.object(ENTRY, "warm_session_settings", new=AsyncMock()):
            await plugin.initialize()
            self.assertIsNotNone(plugin._host_start_task)
            await asyncio.wait_for(plugin._host_start_task, timeout=10)
            self.assertIsNotNone(self.controller.session)
            self.assertEqual(self.controller.session.application.state, "running")
            if plugin._session_settings_warm_task is not None and not plugin._session_settings_warm_task.done():
                plugin._session_settings_warm_task.cancel()
                await asyncio.gather(plugin._session_settings_warm_task, return_exceptions=True)

    async def test_direct_start_hot_latches_unlatched_online_platform_without_teardown(self):
        saved_platforms = self.native.platforms.copy()
        self.native.platforms.clear()
        with patch.dict(os.environ, {"QZONE_COOKIE": "uin=o100; p_skey=synthetic"}):
            await self.start_host()
            initial_session = self.controller.session
            self.assertEqual(initial_session.native_bridge_pairs, ({}, {}))

            # Bring platforms online without calling recover_accounts() first.
            self.native.platforms.update(saved_platforms)
            self.assertFalse(self.controller.native_bridges_current())

            replayed = await self.controller.start()
            self.assertTrue(replayed.is_success, replayed.failure)
            self.assertTrue(replayed.unwrap().get("replayed"))
            self.assertIs(self.controller.session, initial_session)
            self.assertEqual(initial_session.application.state, "running")
            self.assertTrue(self.controller.native_bridges_current())
            self.assertIn("qq-main", initial_session.native_bridge_pairs[0])


class ConcurrentTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_chunk_tts_is_bounded_concurrent_and_preserves_order(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="zh", max_speech_chars=6)
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "conc-chunks"}
        inflight = 0
        peak_inflight = 0
        # 5 chunks of 6 chars each: "第一句测试。" ... "第五句测试。"
        chunk_delays = {
            "第一句测试。": 0.08,
            "第二句测试。": 0.06,
            "第三句测试。": 0.02,
            "第四句测试。": 0.04,
            "第五句测试。": 0.01,
        }

        async def renderer(_, text, **__):
            nonlocal inflight, peak_inflight
            inflight += 1
            peak_inflight = max(peak_inflight, inflight)
            try:
                await asyncio.sleep(chunk_delays.get(text, 0.02))
                return {"type": "record", "data": {"file": f"base64://{text}"}}
            finally:
                inflight -= 1

        text = "<pc_tts>" + "".join(chunk_delays.keys()) + "</pc_tts>"
        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain(text)]),
            renderer=renderer, delivery=None, authorize=_allow,
        )
        records = [part for part in output.chain if isinstance(part, Record)]
        self.assertEqual(len(records), 5)
        self.assertGreater(peak_inflight, 1)
        self.assertLessEqual(peak_inflight, MAX_CONCURRENT_TTS)
        self.assertEqual([r.text for r in records], list(chunk_delays.keys()))

    async def test_multi_chunk_tts_cancels_siblings_and_releases_cooldown_on_failure(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="zh", max_speech_chars=6,
                                  cooldown_seconds=60)
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "fail-chunks"}
        delivery = FakeDelivery()
        cancelled_chunks = []
        started = asyncio.Event()

        async def renderer(_, text, **__):
            if text == "第二句测试。":
                await asyncio.sleep(0.01)
                raise RuntimeError("boom")
            started.set()
            try:
                await asyncio.sleep(10)
                return {"type": "record", "data": {"file": "base64://YQ=="}}
            except asyncio.CancelledError:
                cancelled_chunks.append(text)
                raise

        text = "<pc_tts>第一句测试。第二句测试。第三句测试。</pc_tts>"
        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain(text)]),
            renderer=renderer, delivery=delivery, authorize=_allow,
        )
        self.assertEqual([type(part) for part in output.chain], [Plain])
        self.assertEqual(delivery.released, [("claim-1", "core:event")])
        self.assertIn("第一句测试。", cancelled_chunks)

    async def test_multi_segment_each_mode_prefetches_concurrently_and_sends_strictly_in_order(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="plain", segment_mode="each", position="after_text")
        seg_policy = SegmentationPolicy(enabled=True, llm_controlled=True)
        prepared = {
            "context": object(),
            "event_id": "multi-seg",
            "event_ref": "multi-seg",
            "channel": "private",
            "voice_reply_policy": policy,
            "segmentation_settings": {**asdict(seg_policy), "part_gap_mode": "fixed", "part_gap_ms": 15},
        }
        inflight = 0
        peak_inflight = 0
        delays = {"第一段。": 0.08, "第二段。": 0.05, "第三段。": 0.02, "第四段。": 0.01}

        async def renderer(_, text, **__):
            nonlocal inflight, peak_inflight
            inflight += 1
            peak_inflight = max(peak_inflight, inflight)
            try:
                await asyncio.sleep(delays.get(text, 0.02))
                return {"type": "record", "data": {"file": f"base64://{text}"}}
            finally:
                inflight -= 1

        async def prepare_send(current, message, check, segment_index, segment_count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=check,
                segment_index=segment_index, segment_count=segment_count,
            )

        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare_send)
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)

        # Force 4 explicit segments via LLM segment marker.
        from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER
        body = f"\n{LLM_SEGMENT_MARKER}\n".join(delays.keys())
        await event.send(MessageChain(chain=[Plain(body)]))

        self.assertGreater(peak_inflight, 1, "expected concurrent multi-segment TTS prefetch")
        self.assertLessEqual(peak_inflight, MAX_CONCURRENT_TTS)
        self.assertEqual(len(event.sent), 4)
        sent_texts = [msg.get_plain_text() for msg in event.sent]
        sent_records = [[p.text for p in msg.chain if isinstance(p, Record)] for msg in event.sent]
        self.assertEqual(sent_texts, list(delays.keys()))
        self.assertEqual(sent_records, [[k] for k in delays.keys()])

    async def test_multi_segment_prefetch_cancels_unconsumed_tasks_on_early_stop(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="plain", segment_mode="each", position="after_text")
        seg_policy = SegmentationPolicy(enabled=True, llm_controlled=True)
        prepared = {
            "context": object(),
            "event_id": "stop-seg",
            "event_ref": "stop-seg",
            "channel": "private",
            "voice_reply_policy": policy,
            "segmentation_settings": {**asdict(seg_policy), "part_gap_mode": "fixed", "part_gap_ms": 0},
        }
        cancelled = []
        prefetch_started = asyncio.Event()

        async def renderer(_, text, **__):
            if text == "第一段。":
                await asyncio.sleep(0.02)
                return {"type": "record", "data": {"file": "base64://first"}}
            prefetch_started.set()
            try:
                await asyncio.sleep(10)
                return {"type": "record", "data": {"file": f"base64://{text}"}}
            except asyncio.CancelledError:
                cancelled.append(text)
                raise

        orig_send = event.send

        async def failing_send(message):
            await orig_send(message)
            raise RuntimeError("platform send failed")

        event.send = failing_send

        async def prepare_send(current, message, check, segment_index, segment_count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=check,
                segment_index=segment_index, segment_count=segment_count,
            )

        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare_send)
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)

        from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER
        body = f"\n{LLM_SEGMENT_MARKER}\n".join(("第一段。", "第二段。", "第三段。"))
        with self.assertRaises(RuntimeError):
            await event.send(MessageChain(chain=[Plain(body)]))

        self.assertTrue(prefetch_started.is_set())
        self.assertEqual(set(cancelled), {"第二段。", "第三段。"})
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertNotIn("_voice_segment_prefetch", core)

    async def test_multi_segment_prefetch_cancels_and_reaps_task_when_authorize_fails(self):
        from companion_v2.execution.models import TaskError

        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="plain", segment_mode="each", position="after_text")
        seg_policy = SegmentationPolicy(enabled=True, llm_controlled=True)
        prepared = {
            "context": object(),
            "event_id": "auth-fail-seg",
            "event_ref": "auth-fail-seg",
            "channel": "private",
            "voice_reply_policy": policy,
            "segmentation_settings": {**asdict(seg_policy), "part_gap_mode": "fixed", "part_gap_ms": 0},
        }
        cancelled = []
        prefetch_started = asyncio.Event()
        auth_calls = 0

        async def renderer(_, text, **__):
            if text == "第一段。":
                await asyncio.sleep(0.02)
                return {"type": "record", "data": {"file": "base64://first"}}
            prefetch_started.set()
            try:
                await asyncio.sleep(10)
                return {"type": "record", "data": {"file": f"base64://{text}"}}
            except asyncio.CancelledError:
                cancelled.append(text)
                raise

        async def authorize_then_fail():
            nonlocal auth_calls
            auth_calls += 1
            if auth_calls == 2:
                await asyncio.wait_for(prefetch_started.wait(), timeout=2.0)
                raise TaskError("voice_unauthorized", "authorization denied")

        async def prepare_send(current, message, check, segment_index, segment_count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=authorize_then_fail,
                segment_index=segment_index, segment_count=segment_count,
            )

        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare_send)
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)

        from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER
        body = f"\n{LLM_SEGMENT_MARKER}\n".join(("第一段。", "第二段。"))
        await event.send(MessageChain(chain=[Plain(body)]))

        self.assertEqual(len(event.sent), 2)
        self.assertTrue(any(isinstance(part, Record) for part in event.sent[0].chain))
        self.assertFalse(any(isinstance(part, Record) for part in event.sent[1].chain))
        self.assertEqual(event.sent[1].get_plain_text(), "第二段。")
        self.assertEqual(cancelled, ["第二段。"])
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertNotIn("_voice_segment_prefetch", core)


class HttpEtagPollingTests(unittest.IsolatedAsyncioTestCase):
    def test_etag_matching_and_ignore_keys(self):
        tag1 = compute_etag({"a": 1, "observed_at": "2026-01-01T00:00:00Z"}, ignore_keys=("observed_at",))
        tag2 = compute_etag({"a": 1, "observed_at": "2026-01-01T00:05:00Z"}, ignore_keys=("observed_at",))
        tag3 = compute_etag({"a": 2, "observed_at": "2026-01-01T00:05:00Z"}, ignore_keys=("observed_at",))
        self.assertEqual(tag1, tag2)
        self.assertNotEqual(tag1, tag3)
        self.assertTrue(tag1.startswith('W/"'))
        self.assertTrue(etag_matches(tag1, tag1))
        self.assertTrue(etag_matches(tag1.removeprefix("W/"), tag1))
        self.assertTrue(etag_matches(f'W/"other", {tag1}', tag1))
        self.assertTrue(etag_matches("*", tag1))
        self.assertFalse(etag_matches('W/"other"', tag1))
        self.assertFalse(etag_matches("", tag1))

        # Live ASGI request returns JSONResponse with ETag header and jsonable-encoded content.
        from pathlib import Path

        live_req = _fake_request(live_asgi=True)
        resp = conditional_response(
            {"status": "ok", "data": {"x": 1, "at": NOW, "path": Path("sample/file.txt")}},
            req=live_req,
        )
        self.assertIsInstance(resp, JSONResponse)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("etag"),
            compute_etag({"x": 1, "at": NOW, "path": Path("sample/file.txt")}),
        )

    async def test_multi_api_status_and_performance_etag_304(self):
        state = {"mode": "multi", "state": "running", "targets": [], "counter": 1}
        controller = _fake_controller(config={"performance_mode": {"enabled": False}})
        controller.status = lambda: dict(state)
        api = MultiHostWebAPI(controller)

        with patch("astrbot_host.management_identity.require_management_operator",
                   return_value=object()) as authorize, \
                patch("astrbot_host.multi_api.display_names", return_value=NS(targets=AsyncMock(return_value=[]))):
            with patch("astrbot_host.multi_api.request", _fake_request()):
                first = await api.api_status()
            self.assertIsInstance(first, dict)
            self.assertEqual(first["status"], "ok")
            etag = first["etag"]
            self.assertTrue(etag.startswith('W/"'))

            with patch("astrbot_host.multi_api.request", _fake_request(headers={"If-None-Match": etag})):
                second = await api.api_status()
            self.assertIsInstance(second, Response)
            self.assertEqual(second.status_code, 304)
            self.assertEqual(second.headers.get("etag"), etag)
            self.assertEqual(authorize.call_count, 4)

            state["counter"] = 2
            with patch("astrbot_host.multi_api.request", _fake_request(headers={"If-None-Match": etag})):
                third = await api.api_status()
            self.assertIsInstance(third, dict)
            self.assertEqual(third["data"]["counter"], 2)
            self.assertNotEqual(third["etag"], etag)

        # api_performance ETag / 304
        with patch("astrbot_host.management_identity.require_management_operator", return_value=object()):
            with patch("astrbot_host.multi_api.request", _fake_request()):
                perf1 = await api.api_performance()
            self.assertIsInstance(perf1, dict)
            perf_etag = perf1["etag"]
            with patch("astrbot_host.multi_api.request", _fake_request(headers={"If-None-Match": perf_etag})):
                perf2 = await api.api_performance()
        self.assertIsInstance(perf2, Response)
        self.assertEqual(perf2.status_code, 304)

    async def test_model_maintenance_and_native_perception_etag_304(self):
        # ModelWebAPI.maintenance (including real generated_at / reply_efficiency.generated_at fields)
        controller = _fake_controller()
        model_api = ModelWebAPI(controller)
        maint_data = {
            "state": "ok",
            "pending": 0,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "observed_at": "2026-01-01T00:00:00Z",
            "reply_efficiency": {"generated_at": "2026-01-01T00:00:00+00:00", "p50_ms": 120},
        }

        async def fake_run(action):
            self.assertEqual(action, "maintenance")
            return {
                "status": "ok",
                "data": {**maint_data, "reply_efficiency": dict(maint_data["reply_efficiency"])},
            }

        with patch.object(model_api, "_run", side_effect=fake_run):
            with patch("astrbot_host.model_api.request", _fake_request()):
                m1 = await model_api.maintenance()
            self.assertIsInstance(m1, dict)
            m_etag = m1["etag"]
            maint_data["generated_at"] = "2026-01-01T00:01:00+00:00"
            maint_data["observed_at"] = "2026-01-01T00:01:00Z"
            maint_data["reply_efficiency"]["generated_at"] = "2026-01-01T00:01:00+00:00"
            with patch("astrbot_host.model_api.request", _fake_request(headers={"If-None-Match": m_etag})):
                m2 = await model_api.maintenance()
            self.assertIsInstance(m2, Response)
            self.assertEqual(m2.status_code, 304)
            maint_data["pending"] = 3
            with patch("astrbot_host.model_api.request", _fake_request(headers={"If-None-Match": m_etag})):
                m3 = await model_api.maintenance()
            self.assertIsInstance(m3, dict)
            self.assertEqual(m3["data"]["pending"], 3)

        # NativePerceptionWebAPI.runtime_snapshot(scope=role) & status
        clock = [NOW]
        controller.now = lambda: clock[0]
        cfg_read = Outcome.success(NS(values={
            "role.state.planning_timezone": "Asia/Shanghai",
            "role.state.planning_annual_calendars": "",
            "role.state.planning_weather_enabled": False,
            "role.state.planning_weather_location": "",
            "role.state.planning_weather_max_age_minutes": 30,
        }))
        controller.session.application.configuration = NS(read=lambda *a, **kw: cfg_read)
        perc_api = NativePerceptionWebAPI(controller)

        with patch("astrbot_host.native_perception_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.native_perception_api.StateManagement", return_value=NS(life=NS(authorize=lambda op: None))), \
                patch("astrbot_host.native_perception_api.PerceptionManagement", return_value=NS(
                    status=lambda op: Outcome.success({"settings": {"calendar_enabled": False}}))):
            with patch("astrbot_host.native_perception_api.request", _fake_request(query={"scope": "role"})):
                r1 = await perc_api.runtime_snapshot()
            self.assertIsInstance(r1, dict)
            self.assertEqual(set(r1["data"]), {"weather", "calendar", "observed_at"})
            r_etag = r1["etag"]

            # Clock advances, underlying data unchanged -> 304!
            clock[0] = NOW + timedelta(seconds=30)
            with patch("astrbot_host.native_perception_api.request",
                       _fake_request(query={"scope": "role"}, headers={"If-None-Match": r_etag})):
                r2 = await perc_api.runtime_snapshot()
            self.assertIsInstance(r2, Response)
            self.assertEqual(r2.status_code, 304)


if __name__ == "__main__":
    unittest.main()
