"""Run separately: real SDK plugin entry and isolated multi-target life routes."""

import asyncio
from functools import partial
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock, patch

# Establish a disposable AstrBot root before imports that load the SDK.
from tests.astrbot_data_root import ASTRBOT_ROOT, assert_sdk_database_isolated

# This fixture establishes a temporary AstrBot import root before SDK imports.
from tests.multi_host_support import MultiNativeContext, multi_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.test_management_access import NOW
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.foundation.clock import DeterministicClock
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request
from astrbot.core import db_helper


async def run():
    assert_sdk_database_isolated(db_helper)
    root = Path(__file__).resolve().parents[1]
    package = ModuleType("companion_life_entry_fixture")
    package.__path__ = [str(root)]
    sys.modules[package.__name__] = package
    entry = importlib.import_module(package.__name__ + ".main")
    native = MultiNativeContext()
    clock = DeterministicClock.at(NOW)
    async def session_persona_override(umo):
        return native.selections[umo.split(":", 1)[0]]

    types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, SimpleNamespace, SimpleNamespace,
                               FakeReverseApi, session_persona_override_lookup=session_persona_override)
    factory = partial(entry.AstrBotMultiController, native_types=types, now=clock.now)
    with TemporaryDirectory(prefix="companion-life-entry-") as temporary:
        with patch.object(entry.StarTools, "get_data_dir", return_value=Path(temporary)), \
                patch.object(entry, "AstrBotMultiController", factory):
            plugin = entry.PrivateCompanionV2StagingPlugin(native, multi_config())
        controller = plugin.staging
        try:
            await plugin.initialize()
            assert controller.runtime is not None
            assert controller.session.application.state == "running" and controller._accepting
            # Dashboard initialization resolves routes for all configured personas.
            (await controller.initialize_profile()).unwrap()
            running_session = controller.session
            group = FakeEvent(native)
            group.message_obj.type = group.session.message_type = SimpleNamespace(value="GroupMessage")
            group.message_obj.group_id = group.message_obj.session_id = group.session.session_id = "300"
            group.message_obj.raw_message = dict(post_type="message", message_type="group", self_id=100,
                user_id=200, sender={"user_id": 200}, group_id=300, message_id="event-1")
            group.unified_msg_origin = "qq-main:GroupMessage:300"
            await plugin.observe_companion_model_session(group, SimpleNamespace(role="err"))
            assert not controller.session_topology.path.exists()
            await plugin.observe_companion_model_session(group, SimpleNamespace(role="assistant"))
            await asyncio.wait_for(asyncio.gather(*tuple(controller.session_topology._pending)), timeout=5)
            observed = json.loads(controller.session_topology.path.read_text(encoding="utf-8"))
            assert len(observed) == 1 and next(iter(observed.values()))["account_id"] == "100"
            routes = {path: handler for path, handler, _, _ in native.registered_web_apis}
            paths = [role["life_api"] for target in controller.web_api.target_inventory() for role in target["roles"]]
            assert len(paths) == 4 and all(path in routes and path + "/command" in routes for path in paths)
            weather_path = "/astrbot_plugin_private_companion/host-options/weather"
            weather_routes = [item for item in native.registered_web_apis if item[0] == weather_path]
            assert len(weather_routes) == 2 and {method for item in weather_routes for method in item[2]} == {"GET", "POST"}

            async def request(path, data=None):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else b""
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                req = Request({"type": "http", "method": "POST" if data else "GET", "path": path,
                    "headers": [(b"content-type", b"application/json")], "query_string": b""}, receive=receive)
                with bind_request_context(PluginRequest(req, username="admin")):
                    result = await routes[path]()
                assert result["status"] == "ok", result
                return result["data"]

            for path in paths:
                status = await request(path)
                await request(path + "/command", dict(action="configure", operation_key="enable",
                    view_token=status["view_token"], edit_token=status["edit_token"],
                    changes={"enabled": True, "diary_enabled": False, "derive_enabled": False}))
            with bind_request_context(PluginRequest(Request({"type": "http", "method": "GET", "path": weather_path,
                    "headers": [], "query_string": b""}), username="admin")):
                weather_status = await next(item[1] for item in weather_routes if item[2] == ["GET"])()
            assert weather_status["status"] == "ok" and "credential" not in weather_status["data"]["settings"]
            (await controller.start()).unwrap()
            assert controller.session is running_session
            event = FakeEvent(native)
            event.stop_event = lambda: setattr(event, "stopped", True)
            with patch.object(controller.session_topology, "services", AsyncMock(return_value={"llm_enabled": False})):
                await asyncio.create_task(plugin.receive_companion_message(event))
            # The wake flags select AstrBot's automatic core reply path;
            # call_llm stays false because the plugin did not call a model itself.
            assert not getattr(event, "stopped", False)
            assert event.is_wake and event.is_at_or_wake_command
            assert not getattr(event, "call_llm", False)
            assert native.provider.client.state.calls == []
            app = controller.session.application
            assert len([r for r in app.life_runtime.routines.values() if r.instruction is not None]) == 2
            await request(paths[0] + "/command", dict(action="sleep", operation_key="sleep", minutes=1,
                view_token=(await request(paths[0]))["view_token"]))
            report = (await controller.stop()).unwrap()
            assert report.state == "stopped" and report.pending_handles == ()
            assert (await request(paths[0]))["activities"][0]["state"] == "active"
            assert app.life_runtime.active_handles == () and app.journal_schedule.active_handles == ()
            assert native.provider.client.state.calls == []
            assert all(not p.client._api._wsr_api.calls for p in native.platforms.values())
        finally:
            try:
                await plugin.terminate()
            finally:
                await db_helper.engine.dispose()
        assert controller.runtime is None and native.registered_web_apis == []
        print(json.dumps({"life_routes": len(paths), "state": report.state, "route_cleanup": True,
                          "automatic_startup": True,
                          "model_session_discovery": True, "native_reply_switch": True,
                          "weather_source_routes": True,
                          "model_calls": 0, "platform_calls": 0,
                          "astrbot_root_isolated": str(db_helper.db_path).startswith(str(ASTRBOT_ROOT))}))


if __name__ == "__main__":
    asyncio.run(run())
