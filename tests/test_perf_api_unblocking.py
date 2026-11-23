"""Concurrency, lock-release, cache-invalidation, and native store tests for Dashboard API unblocking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.api.web import PluginMultiDict
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef
from companion_v2.execution.models import TaskContext
from companion_v2.storage.runtime import StorageRuntime
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE

from astrbot_host.configuration_api import ConfigurationWebAPI
from astrbot_host.image_material_api import ImageMaterialsWebAPI
from astrbot_host.life_api import LifeWebAPI
from astrbot_host.memory_api import MemoryWebAPI
from astrbot_host.model_api import ModelWebAPI
from astrbot_host.page_api import PageWebAPI
from astrbot_host.persona_workshop_api import PersonaWorkshopWebAPI
from astrbot_host.qzone_api import QzoneWebAPI
from astrbot_host.reply_api import ReplyWebAPI
from astrbot_host.state_api import StateWebAPI


class _Query(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


def _fake_request(*, username="admin", query=None, body=b""):
    async def read():
        return body

    return NS(
        username=username,
        query=_Query(query or {}),
        body=read,
        plugin_name="astrbot_plugin_private_companion",
    )


def _fake_controller(*, target=None, application=None, runtime=None, settings=None, web_api=None):
    rt = runtime if runtime is not None else NS(installation_id="install-perf")
    ctx = target if target is not None else NS(
        key="ctx-key-perf",
        scope=ScopeRef("install-perf", "onebot", ScopeKind.PRIVATE, "200", "100", "100", "c1", "s1"),
        role_binding=NS(role=NS(subject_id="role-a")),
    )
    app = application if application is not None else NS(runtime=rt, now=lambda: NOW)
    session = NS(application=app, profile=NS(target=ctx))
    return NS(
        _control_lock=asyncio.Lock(),
        _lifecycle_lock=asyncio.Lock(),
        _terminated=False,
        session=session,
        last_session=None,
        runtime=rt,
        settings=settings if settings is not None else NS(profile_id="profile-perf"),
        target_id="target-perf",
        web_api=web_api if web_api is not None else NS(),
        context=NS(),
        native_extensions=None,
        now=lambda: NOW,
    )


async def _assert_off_loop_and_lock_free(testcase, controller, coro, entered, release):
    task = asyncio.create_task(coro)
    try:
        testcase.assertTrue(await asyncio.to_thread(entered.wait, 5), "worker thread was never entered")
        testcase.assertFalse(task.done(), "route must stay off the event loop while worker is blocked")
        await asyncio.wait_for(controller._control_lock.acquire(), timeout=1.0)
        controller._control_lock.release()
    finally:
        release.set()
    return await task


class ControlLockReleaseAndConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_life_and_state_run_concurrently_without_serializing_on_control_lock(self):
        controller = _fake_controller()
        life_api = LifeWebAPI(controller)
        state_api = StateWebAPI(controller)

        life_entered, state_entered = threading.Event(), threading.Event()
        release_both = threading.Event()
        overlap_confirmed = threading.Event()

        def slow_life_status(operator, **kwargs):
            life_entered.set()
            if state_entered.wait(5):
                overlap_confirmed.set()
            release_both.wait(5)
            return {"journals": [], "activities": []}

        def slow_state_status(operator, **kwargs):
            state_entered.set()
            if life_entered.wait(5):
                overlap_confirmed.set()
            release_both.wait(5)
            return Outcome.success({"state": {"values": {}}, "calendar": {"items": []}})

        with patch("astrbot_host.life_api.request", _fake_request()), \
                patch("astrbot_host.state_api.request", _fake_request()), \
                patch("astrbot_host.life_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.state_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.life_api.RoleLifeManagement", return_value=NS(status=slow_life_status)), \
                patch("astrbot_host.state_api.StateManagement", return_value=NS(status=slow_state_status)):
            t_life = asyncio.create_task(life_api.status())
            t_state = asyncio.create_task(state_api.status())
            try:
                self.assertTrue(
                    await asyncio.to_thread(overlap_confirmed.wait, 5),
                    "GET /life and GET /state must overlap concurrently without blocking on _control_lock",
                )
                await asyncio.wait_for(controller._control_lock.acquire(), timeout=1.0)
                controller._control_lock.release()
            finally:
                release_both.set()
            res_life, res_state = await asyncio.gather(t_life, t_state)

        self.assertEqual(res_life["status"], "ok", res_life)
        self.assertEqual(res_state["status"], "ok", res_state)

    async def test_image_material_reads_run_off_loop_without_holding_control_lock(self):
        controller = _fake_controller()
        api = ImageMaterialsWebAPI(controller)
        ok_policy = NS(feature_access=lambda *a, **kw: Outcome.success(True))

        for route_name, method_name, query in (
            ("inventory", "inventory", ()),
            ("album", "album", ()),
            ("album_image", "album_image", (("image_id", "generated-image:test:0"),)),
        ):
            entered, release = threading.Event(), threading.Event()

            def make_blocking():
                def blocking(*args, **kwargs):
                    entered.set()
                    release.wait(5)
                    return {"items": [], "references": []}
                return blocking

            req = NS(username="admin", query=PluginMultiDict(list(query)))
            with patch("astrbot_host.image_material_api.request", req), \
                    patch("astrbot_host.image_material_api.dashboard_operator", return_value=object()), \
                    patch("astrbot_host.image_material_api.ObjectAccessPolicy", return_value=ok_policy), \
                    patch(
                        "astrbot_host.image_material_api.ReferenceMaterialManagement",
                        return_value=NS(**{method_name: make_blocking()}),
                    ):
                res = await _assert_off_loop_and_lock_free(
                    self, controller, getattr(api, route_name)(), entered, release
                )
            self.assertEqual(res["status"], "ok", (route_name, res))

    async def test_model_image_observability_runs_off_loop_without_holding_control_lock(self):
        entered, release = threading.Event(), threading.Event()

        def slow_observability(context, at):
            entered.set()
            release.wait(5)
            return {"items": [], "summary": {}}

        rt = NS(installation_id="install-perf")
        app = NS(
            runtime=rt,
            messages=NS(generated_images=NS(observability_page=slow_observability)),
        )
        controller = _fake_controller(application=app, runtime=rt)
        api = ModelWebAPI(controller)
        with patch("astrbot_host.model_api.request", _fake_request()), \
                patch("astrbot_host.model_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.model_api.ModelManagement", return_value=NS(authorize=lambda op: Outcome.success(True))):
            res = await _assert_off_loop_and_lock_free(
                self, controller, api.image_observability(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

    async def test_memory_reply_qzone_persona_page_and_behavior_reads_release_control_lock(self):
        # 1. MemoryWebAPI.list
        controller = _fake_controller(application=NS(runtime=NS(installation_id="i"), memory=object()))
        entered, release = threading.Event(), threading.Event()

        def slow_memory_list(target, **kwargs):
            entered.set()
            release.wait(5)
            return Outcome.success({"items": []})

        with patch("astrbot_host.memory_api.request", _fake_request()), \
                patch("astrbot_host.memory_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.memory_api.MemoryManagement", return_value=NS(list=slow_memory_list)):
            res = await _assert_off_loop_and_lock_free(
                self, controller, MemoryWebAPI(controller).list(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

        # 2. ReplyWebAPI.status
        controller = _fake_controller()
        entered, release = threading.Event(), threading.Event()

        def slow_reply_status(operator):
            entered.set()
            release.wait(5)
            return Outcome.success({"settings": {}})

        with patch("astrbot_host.reply_api.request", _fake_request()), \
                patch("astrbot_host.reply_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.reply_api.ReplyManagement", return_value=NS(status=slow_reply_status)), \
                patch.object(ReplyWebAPI, "_tts_catalog", return_value={"state": "unavailable", "providers": []}):
            res = await _assert_off_loop_and_lock_free(
                self, controller, ReplyWebAPI(controller).status(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

        # 3. QzoneWebAPI.status
        controller = _fake_controller(application=NS(runtime=NS(installation_id="i"), qzone=object()))
        entered, release = threading.Event(), threading.Event()

        def slow_qzone_status(operator, at):
            entered.set()
            release.wait(5)
            return {"installed": False}

        with patch("astrbot_host.qzone_api.request", _fake_request()), \
                patch("astrbot_host.qzone_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.qzone_api.QzoneAccess", return_value=NS(status=slow_qzone_status)):
            res = await _assert_off_loop_and_lock_free(
                self, controller, QzoneWebAPI(controller).status(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

        # 4. PersonaWorkshopWebAPI.profile_export
        controller = _fake_controller()
        entered, release = threading.Event(), threading.Event()

        def slow_profile_snapshot(runtime, session):
            entered.set()
            release.wait(5)
            return None, None, None, {"name": "test"}

        ok_policy = NS(feature_access=lambda *a, **kw: Outcome.success(True))
        with patch("astrbot_host.persona_workshop_api.request", _fake_request()), \
                patch("astrbot_host.persona_workshop_api.dashboard_operator", return_value=object()), \
                patch("astrbot_host.persona_workshop_api.ObjectAccessPolicy", return_value=ok_policy), \
                patch.object(PersonaWorkshopWebAPI, "_role_profile_snapshot_for", side_effect=slow_profile_snapshot):
            res = await _assert_off_loop_and_lock_free(
                self, controller, PersonaWorkshopWebAPI(controller).profile_export(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

        # 5. PageWebAPI.targets
        entered, release = threading.Event(), threading.Event()

        def slow_inventory():
            entered.set()
            release.wait(5)
            return []

        controller = _fake_controller(web_api=NS(target_inventory=slow_inventory))
        fake_operator = NS(subject=NS(subject_id="op-1"))
        with patch("astrbot_host.page_api.request", _fake_request()), \
                patch("astrbot_host.page_api.dashboard_operator", return_value=fake_operator), \
                patch("astrbot_host.page_api.ObjectAccessPolicy", return_value=ok_policy), \
                patch("astrbot_host.page_api.GroupRemarks", return_value=NS(apply=lambda rows: rows)), \
                patch("astrbot_host.page_api.display_names", return_value=NS(targets=AsyncMock(return_value=[]))):
            res = await _assert_off_loop_and_lock_free(
                self, controller, PageWebAPI(controller).targets(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)

        # 6. ConfigurationWebAPI.behavior_status
        controller = _fake_controller()
        entered, release = threading.Event(), threading.Event()

        def slow_behavior_status(operator):
            entered.set()
            release.wait(5)
            return {"groups": []}

        with patch("astrbot_host.configuration_api.request", _fake_request()), \
                patch("astrbot_host.configuration_api.dashboard_operator", return_value=object()), \
                patch("companion_v2.conversation.behavior.management.BehaviorManagement",
                      return_value=NS(status=slow_behavior_status)):
            res = await _assert_off_loop_and_lock_free(
                self, controller, ConfigurationWebAPI(controller).behavior_status(), entered, release
            )
        self.assertEqual(res["status"], "ok", res)


class ConfigurationCacheAndNativeImageStoreTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_configuration_api_batches_sections_caches_hits_and_invalidates_on_write(self):
        host = self.host()
        await self.start(host)
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        controller = _fake_controller(target=context, application=app, runtime=app.runtime)
        api = ConfigurationWebAPI(controller, allowed_sections=("quota", "busy_gate", "recall"))

        call_counts = {"quota": 0, "busy_gate": 0, "recall": 0}
        wrapped_sections = {}
        for name, cls in api.sections.items():
            def make_wrapper(section_name, editor_cls):
                class CountingEditor(editor_cls):
                    def status(self, operator):
                        call_counts[section_name] += 1
                        return super().status(operator)
                return CountingEditor
            wrapped_sections[name] = make_wrapper(name, cls)
        api.sections = wrapped_sections

        with patch("astrbot_host.configuration_api.dashboard_operator", return_value=self.core):
            with patch("astrbot_host.configuration_api.request", _fake_request(query={"sections": "quota,busy_gate,recall"})):
                first = await api.status()
            self.assertEqual(first["status"], "ok", first)
            self.assertEqual(call_counts, {"quota": 1, "busy_gate": 1, "recall": 1})

            # Second identical read hits the DB-token-validated cache with zero extra section status calls!
            with patch("astrbot_host.configuration_api.request", _fake_request(query={"sections": "quota,busy_gate,recall"})):
                second = await api.status()
            self.assertEqual(second["status"], "ok", second)
            self.assertEqual(call_counts, {"quota": 1, "busy_gate": 1, "recall": 1})
            self.assertEqual(second["data"]["sections"], first["data"]["sections"])

            # Mutating via POST /config/configure invalidates cache immediately and returns updated state.
            token = first["data"]["sections"]["busy_gate"]["edit_token"]
            old_enabled = first["data"]["sections"]["busy_gate"]["settings"]["busy_reply_gate_enabled"]
            body = json.dumps({
                "section": "busy_gate",
                "operation_key": "cache-invalidation-op-1",
                "edit_token": token,
                "changes": {"busy_reply_gate_enabled": not old_enabled},
            }).encode("utf-8")
            with patch("astrbot_host.configuration_api.request", _fake_request(body=body)):
                saved = await api.configure()
            self.assertEqual(saved["status"], "ok", saved)

            with patch("astrbot_host.configuration_api.request", _fake_request(query={"sections": "busy_gate"})):
                third = await api.status()
            self.assertEqual(third["status"], "ok", third)
            self.assertEqual(call_counts["busy_gate"], 2)
            self.assertEqual(
                third["data"]["sections"]["busy_gate"]["settings"]["busy_reply_gate_enabled"],
                not old_enabled,
            )

            # An external configuration write bypassing ConfigurationWebAPI invalidates via revision token.
            config_store = app.configuration.store
            with config_store.transaction() as connection:
                connection.execute(
                    "UPDATE configuration_targets SET revision = revision + 1 WHERE target_key IN "
                    "(SELECT target_key FROM configuration_targets LIMIT 1)"
                )
            with patch("astrbot_host.configuration_api.request", _fake_request(query={"sections": "busy_gate"})):
                fourth = await api.status()
            self.assertEqual(fourth["status"], "ok", fourth)
            self.assertEqual(call_counts["busy_gate"], 3)

    async def test_state_weather_and_model_storage_test_release_control_lock(self):
        # StateWebAPI.weather releases _control_lock before reading weather context off-loop.
        controller = _fake_controller()
        api = StateWebAPI(controller)
        entered, release = threading.Event(), threading.Event()

        def slow_weather_ctx(session, runtime, username, observed_at):
            entered.set()
            release.wait(5)
            return session.application, session.profile.target, {
                "planning_weather_enabled": False,
                "planning_weather_location": "",
                "planning_weather_max_age_minutes": 30,
                "planning_timezone": "Asia/Shanghai",
            }

        with patch("astrbot_host.state_api.request", _fake_request()), \
                patch.object(StateWebAPI, "_read_weather_context", side_effect=slow_weather_ctx):
            res = await _assert_off_loop_and_lock_free(self, controller, api.weather(), entered, release)
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(res["data"]["state"], "disabled")

        # ModelWebAPI.storage_test releases _control_lock before running SQLite storage check off-loop.
        entered2, release2 = threading.Event(), threading.Event()

        def slow_check_storage(operator):
            entered2.set()
            release2.wait(5)
            return Outcome.success({"ok": True})

        model_api = ModelWebAPI(controller)
        body = json.dumps({"check": "sqlite"}).encode("utf-8")
        with patch("astrbot_host.model_api.request", _fake_request(body=body)), \
                patch("astrbot_host.model_api.dashboard_operator", return_value=object()), \
                patch(
                    "astrbot_host.model_api.ModelManagement",
                    return_value=NS(authorize=lambda op: Outcome.success(True), check_storage=slow_check_storage),
                ):
            res2 = await _assert_off_loop_and_lock_free(self, controller, model_api.storage_test(), entered2, release2)
        self.assertEqual(res2["status"], "ok", res2)

    async def test_page_targets_calls_inventory_without_route_refresh_off_loop_and_refreshes_on_loop(self):
        main_thread_id = threading.get_ident()
        inventory_calls = []
        refresh_calls = []
        ok_policy = NS(feature_access=lambda *a, **kw: Outcome.success(True))
        fake_operator = NS(subject=NS(subject_id="op-1"))

        def target_inventory(*, refresh_routes=True):
            inventory_calls.append((threading.get_ident(), refresh_routes))
            return [{
                "target_id": "t-1",
                "scope_kind": "private",
                "roles": [{"role_id": "role-a", "persona_ids": ["p-a"], "config_api": "/cfg"}],
            }]

        controller = None

        def ensure_role_routes(items):
            refresh_calls.append((threading.get_ident(), controller._control_lock.locked(), len(items)))

        controller = _fake_controller(
            web_api=NS(target_inventory=target_inventory, _ensure_role_routes=ensure_role_routes)
        )
        with patch("astrbot_host.page_api.request", _fake_request()), \
                patch("astrbot_host.page_api.dashboard_operator", return_value=fake_operator), \
                patch("astrbot_host.page_api.ObjectAccessPolicy", return_value=ok_policy), \
                patch("astrbot_host.page_api.GroupRemarks", return_value=NS(apply=lambda rows: rows)), \
                patch("astrbot_host.page_api.display_names", return_value=NS(targets=AsyncMock(side_effect=lambda rows: rows))):
            res = await PageWebAPI(controller).targets()

        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(len(inventory_calls), 1)
        self.assertNotEqual(inventory_calls[0][0], main_thread_id, "target_inventory must run off-loop")
        self.assertFalse(inventory_calls[0][1], "off-loop target_inventory must pass refresh_routes=False")
        self.assertEqual(refresh_calls, [(main_thread_id, True, 1)])

        # If controller is terminated before re-acquiring _control_lock, route refresh must not run.
        refresh_calls.clear()

        async def terminate_during_display_names(rows):
            controller._terminated = True
            return rows

        with patch("astrbot_host.page_api.request", _fake_request()), \
                patch("astrbot_host.page_api.dashboard_operator", return_value=fake_operator), \
                patch("astrbot_host.page_api.ObjectAccessPolicy", return_value=ok_policy), \
                patch("astrbot_host.page_api.GroupRemarks", return_value=NS(apply=lambda rows: rows)), \
                patch("astrbot_host.page_api.display_names",
                      return_value=NS(targets=AsyncMock(side_effect=terminate_during_display_names))):
            res_stopped = await PageWebAPI(controller).targets()
        self.assertEqual(res_stopped["status"], "error")
        self.assertEqual(refresh_calls, [])


if __name__ == "__main__":
    unittest.main()
