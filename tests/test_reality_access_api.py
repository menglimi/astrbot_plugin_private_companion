"""Reality data reaches a V2 turn only after its own scoped consent."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture, MultiNativeContext, multi_config

from astrbot.api.web import PluginRequest, bind_request_context
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.provider.entities import ProviderRequest
from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import inject_core_reply_tools, native_tool_name
from astrbot_host.management_identity import dashboard_operator
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.reality_access_api import CAPABILITY_PATHS, PREFIX
from astrbot_host.native_perception_api import PREFIX as PERCEPTION_PREFIX
from companion_v2.capabilities.reality_access import ACTION
from companion_v2.capabilities.reality_camera import ACTION as CAMERA_ACTION
from companion_v2.conversation.native_tool_results import operation_row, snapshot as native_snapshot
from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from starlette.requests import Request


class MobileSnapshot:
    def __init__(self):
        self.users = []
        self.camera_calls = []
        self.device_calls = []
        self.mihome_web_api = self

    def extension_control_preflight(self, alias, *, native=False):
        if not native or alias != "desk-lamp":
            raise ValueError("device alias or native policy mismatch")
        return "private-device-id"

    async def extension_control_power(self, alias, is_on, *, native=False):
        if not native:
            raise ValueError("native policy required")
        self.device_calls.append((alias, is_on))
        return {"alias": alias, "is_on": is_on, "confirmed": True}

    def mobile_context(self, user_id):
        self.users.append(user_id)
        return {"available": True, "user_id": user_id,
                "location": {"available": True, "latitude": 31.23, "longitude": 121.474,
                             "place": {"matched": True, "name": "合成测试地点", "area_label": "测试市·测试区"},
                             "captured_at": 1790848200, "age_seconds": 1},
                "device": {"available": True, "app_state": "foreground", "battery_percent": 82,
                           "charging": True, "last_report_at": 1790848200, "age_seconds": 1},
                "activity": {"available": True, "display_title": "代码编辑器", "category": "work",
                             "last_report_at": 1790848200, "age_seconds": 1, "stale": False},
                "telemetry": {"available": True, "captured_at": 1790848200, "age_seconds": 1,
                              "measurements": [{"type": "heart_rate", "label": "心率", "value": 72,
                                                "unit": "次/分", "captured_at": 1790848200}],
                               "activity": {"state": "resting", "label": "休息"}}}

    def capture_camera_frame(self):
        self.camera_calls.append("frame")
        return {"jpeg_bytes": b"must-never-enter-V2"}

    def install_io(self, runtime):
        # Keep the bundled provider, camera policy and lifecycle intact. Only
        # unavailable mobile/device transports and the physical frame are fake.
        runtime.mobile_context = Mock(side_effect=self.mobile_context)
        runtime.mihome_web_api = self
        runtime._capture_reality_touch_camera_frame = Mock(side_effect=self.capture_camera_frame)
        runtime._analyze_reality_touch_camera_frame = AsyncMock(return_value={
            "summary": "桌面上有打开的笔记本电脑。", "scene_description": "桌面上有打开的笔记本电脑。",
            "purpose_answer": "桌面上有打开的笔记本电脑。", "confidence": 0.9,
            "visible_evidence": ["打开的笔记本电脑"], "answer_available": True,
            "answer_status": "answered", "analyzed": True,
            "image_base64": "must-never-enter-V2"})

    async def terminate(self):
        return None


async def request_api(native, path=PREFIX, *, body=None, username="admin", query=b"", suffix="/configure"):
    for original, scoped in getattr(native, "reality_test_paths", {}).items():
        if path == original or path.startswith(original + "/"):
            path = scoped + path.removeprefix(original)
            break
    method = "GET" if body is None else "POST"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    route = path if method == "GET" else path + suffix
    request = Request({"type":"http", "method":method, "path":route,
                       "headers":[], "query_string":query}, receive=receive)
    handler = next(handler for registered, handler, methods, _ in native.registered_web_apis
                   if registered == route and method in methods)
    with bind_request_context(PluginRequest(request, username=username)):
        return await handler()


def command(status, enabled, key):
    return {"edit_token":status["edit_token"], "enabled":enabled, "operation_key":key}


class RealityAccessSingleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-reality-access-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.native = support.WebContext()
        self.mobile = MobileSnapshot()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                           NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(self.native, support.host_config(staging={
            "profile_id": "reality-native-access", "delivery_mode": "onebot_private"}), self.root,
            native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()

    async def start(self, *, core=False):
        if core:
            await self.controller.terminate()
            self.native = MultiNativeContext()
            types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                              NS, NS, support.FakeReverseApi)
            config = multi_config(targets=[dict(target_id="first", platform_id="qq-main",
                account_id="100", bot_id="bot-a", participant_id="200")],
                personas=[dict(bot_id="bot-a", persona_id="original", role_id="role-a")])
            self.controller = AstrBotMultiController(self.native, config, self.root / "core",
                native_types=types, now=lambda: support.NOW)
            self.addAsyncCleanup(self.controller.terminate)
            self.controller.register_routes()
            role = self.controller.web_api.target_inventory()[0]["roles"][0]
            self.native.reality_test_paths = {PREFIX: role["reality_access_api"],
                                            PERCEPTION_PREFIX: role["perception_native_api"]}
        (await self.controller.initialize_profile()).unwrap()
        if not core:
            (await self.controller.authorize_delivery()).unwrap()
        (await self.controller.start()).unwrap()
        if core:
            self.app = self.controller.session.application
            self.context = self.controller.authority.context("first", "role-a")
            self.reality = self.controller.native_extensions.service("reality")
            self.reality.config["enabled"] = True
            self.reality.config["camera"] = {"enabled": True}
            self.reality._sync_runtime_config()
            self.reality.authorized_user_ids = {"200"}
            self.reality._user("200")
            self.mobile.install_io(self.reality)

    async def core_request(self, text, event_id):
        registry = self.controller.native_extensions
        # The actual configure route schedules this start; await its readiness
        # only in the fixture, never in normal chat admission.
        await registry._lease("reality").start()
        self.assertEqual(registry.status("reality")["state"], "running")
        event = support.FakeEvent(NS(platform=self.native.platform), event_id=event_id,
                                  sender="200", login="100", text=text)
        prepared = require(await self.controller.prepare_core_reply(event))
        await prepared["native_source_future"]

        async def authorize(value):
            return await self.controller.authorize_core_reply(event, value)

        prepare_core_reply_event(event, prepared, authorize=authorize)
        req = ProviderRequest(system_prompt=self.native.persona["prompt"])
        inject_core_reply_tools(event, req, application=self.app)
        return event, req

    async def call_core(self, event, req, action, arguments, call_id):
        tool = req.func_tool.get_tool(native_tool_name(action))
        self.assertIsNotNone(tool, action)
        call = ToolCall(id=call_id, function=ToolCall.FunctionBody(name=tool.name,
            arguments=json.dumps(arguments, ensure_ascii=False)))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        return json.loads(await tool.call(wrapper, **arguments))

    def assert_core_only(self):
        for table in ("conversation_batches", "model_rounds", "conversation_delivery_receipts"):
            self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM " + table, ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_enable_read_model_context_revoke_and_replay(self):
        await self.start(core=True)
        initial = (await request_api(self.native))["data"]
        self.assertFalse(initial["invokable"])
        self.assertEqual(initial["runtime"]["state"], "inactive")
        enabled_command = command(initial, True, "enable-first")
        enabled = await request_api(self.native, body=enabled_command)
        self.assertEqual(enabled["status"], "ok", enabled)
        self.assertTrue(enabled["data"]["status"]["invokable"])
        self.assertIn(enabled["data"]["runtime"]["state"], ("starting", "running"))
        again = await request_api(self.native, body=enabled_command)
        self.assertTrue(again["data"]["replayed"], again)

        event, req = await self.core_request("读一下我手机上报的位置", "location-event")
        result = await self.call_core(event, req, ACTION, {}, "location-read")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertIn("31.23", result["payload"])
        self.assertEqual(self.mobile.users, ["200"])
        row = operation_row(self.app.runtime, result["invocation_id"])
        self.assertEqual((row["state"], row["action"], row["execution_path"]),
                         ("succeeded", ACTION, "astrbot_core"))
        receipt, material = native_snapshot(self.app.external, row, self.context, support.NOW)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertIn("31.23", material.payload)
        runtime = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]
        self.assertEqual(runtime["reality"]["state"], "available", runtime["reality"])
        mobile = runtime["reality"]["snapshot"]
        self.assertEqual(mobile["location"]["label"], "测试市·测试区")
        self.assertNotIn("latitude", mobile["location"])
        self.assertEqual(mobile["health"]["measurements"][0]["label"], "心率")
        self.assertEqual(mobile["device"]["battery_percent"], 82)

        # A dashboard read must show the newest settled core result even after
        # the first eight receipts have filled its usual display limit.
        original_read = self.reality.mobile_context
        for index in range(9):
            def changed(user_id, index=index):
                value = original_read(user_id)
                value["device"]["battery_percent"] = 83 + index
                return value
            self.reality.mobile_context = changed
            fresh_result = await self.call_core(event, req, ACTION, {}, "location-read-" + str(index))
            self.assertEqual(fresh_result["state"], "succeeded", fresh_result)
        newest = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]["reality"]
        self.assertEqual(newest["snapshot"]["device"]["battery_percent"], 91)
        read_count = len(self.mobile.users)
        self.assertEqual(read_count, 10)

        latest = (await request_api(self.native))["data"]
        revoked = await request_api(self.native, body=command(latest, False, "revoke-first"))
        self.assertFalse(revoked["data"]["status"]["invokable"], revoked)
        self.assertTrue(revoked["data"]["status"]["role_enabled"])
        hidden = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]
        self.assertEqual(hidden["reality"]["state"], "blocked")
        self.assertNotIn("snapshot", hidden["reality"])
        with self.assertRaises(TaskError):
            native_snapshot(self.app.external, row, self.context, support.NOW)
        denied = await self.call_core(event, req, ACTION, {}, "revoked-location-read")
        self.assertEqual(denied["state"], "failed", denied)
        self.assertEqual(len(self.mobile.users), read_count)
        old_enable = await request_api(self.native, body=enabled_command)
        self.assertTrue(old_enable["data"]["replayed"])
        self.assertFalse(old_enable["data"]["status"]["invokable"])
        stale = await request_api(self.native, body=command(latest, True, "stale-enable"))
        self.assertEqual(stale["data"]["code"], "reality_access_changed")
        fresh = (await request_api(self.native))["data"]
        restored = await request_api(self.native, body=command(fresh, True, "restore-first"))
        self.assertTrue(restored["data"]["status"]["invokable"], restored)
        self.assert_core_only()
        (await self.controller.stop()).unwrap()
        (await self.controller.start()).unwrap()
        self.assertTrue((await request_api(self.native))["data"]["invokable"])

    async def test_authentication_identity_and_body_cannot_grant_device_control(self):
        await self.start()
        initial = (await request_api(self.native))["data"]
        body = command(initial, True, "enable-safe")
        for username in (None, "", "api_key:synthetic"):
            denied = await request_api(self.native, body=body, username=username)
            self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        denied = await request_api(self.native, query=b"target_id=someone-else")
        self.assertEqual(denied["data"]["code"], "reality_request_invalid")
        denied = await request_api(self.native, body={**body, "capability_id":"reality.device.control"})
        self.assertEqual(denied["data"]["code"], "reality_request_invalid")
        enabled = await request_api(self.native, body=body)
        self.assertEqual(enabled["status"], "ok", enabled)
        app = self.controller.session.application
        self.assertEqual([tool.action for tool in app.external.catalog_for(self.controller.session.profile.target)],
                         [ACTION])
        await self.controller.terminate()
        self.assertFalse(any(path.startswith(PREFIX) for path, *_ in self.native.registered_web_apis))

    async def test_revoke_without_assembly_is_an_error_instead_of_silent_completion(self):
        await self.start()
        initial = (await request_api(self.native))["data"]
        self.assertFalse(initial["installed"])
        revoked = await request_api(self.native, body=command(initial, False, "revoke-missing"))
        self.assertEqual(revoked["status"], "error", revoked)
        self.assertEqual(revoked["data"]["code"], "capability_not_installed")

    async def test_role_selection_route_requires_assembly_and_is_idempotent(self):
        await self.start()
        initial = (await request_api(self.native))["data"]
        missing = await request_api(self.native, suffix="/role-selection", body={
            "operation_key": "reality-role-missing", "expected_revision": 0, "enabled": True})
        self.assertEqual(missing["data"]["code"], "capability_not_installed", missing)
        enabled = await request_api(self.native, body=command(initial, True, "reality-enable-for-role"))
        self.assertEqual(enabled["status"], "ok", enabled)
        current = (await request_api(self.native))["data"]
        self.assertTrue(current["role_enabled"])
        self.assertEqual(current["role_revision"], 1)
        command_body = {"operation_key": "reality-role-disable",
            "expected_revision": current["role_revision"], "enabled": False}
        disabled = await request_api(self.native, suffix="/role-selection", body=command_body)
        self.assertEqual(disabled["status"], "ok", disabled)
        self.assertFalse(disabled["data"]["replayed"])
        self.assertFalse(disabled["data"]["status"]["role_enabled"])
        replay = await request_api(self.native, suffix="/role-selection", body=command_body)
        self.assertEqual(replay["status"], "ok", replay)
        self.assertTrue(replay["data"]["replayed"])
        self.assertFalse(replay["data"]["status"]["role_enabled"])
        self.assertEqual((await request_api(self.native))["data"]["role_revision"], 2)

    async def test_read_only_status_does_not_acquire_or_read_location(self):
        await self.start()
        for _ in range(2):
            status = await request_api(self.native)
            self.assertFalse(status["data"]["installed"], status)
        self.assertEqual(self.mobile.users, [])

    async def test_view_only_operator_gets_read_only_status_and_cannot_submit_consent(self):
        await self.start()
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        original = self.controller.session.profile.target
        target = TaskContext(operator, original.role_binding, AudienceRef(original.scope, (operator.subject,)))
        self.controller.session.profile = NS(target=target)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
            expected_revision=policy.revision, observed_at=support.NOW)

        with patch.object(self.controller.session.application.tasks, "authorize_context",
                          return_value=Outcome.success(True)):
            readonly = await request_api(self.native)
            self.assertEqual(readonly["status"], "ok", readonly)
            self.assertFalse(readonly["data"]["can_manage"])
            for suffix in CAPABILITY_PATHS.values():
                status = await request_api(self.native, PREFIX + suffix)
                self.assertEqual(status["status"], "ok", status)
                self.assertFalse(status["data"]["can_manage"], status)
            denied = await request_api(self.native, body=command(
                readonly["data"], True, "read-only-enable"))
        self.assertEqual(denied["data"]["code"], "core_access_required")

    async def test_all_native_actions_have_fixed_scoped_access_routes(self):
        await self.start()
        paths = {action: PREFIX + suffix for action, suffix in CAPABILITY_PATHS.items()}
        self.assertEqual(len(paths), 8)
        enabled_actions = set()
        for index, (action, path) in enumerate(paths.items()):
            with self.subTest(action=action):
                response = await request_api(self.native, path)
                self.assertEqual(response["status"], "ok", response)
                self.assertEqual(response["data"]["action"], action)
                self.assertFalse(response["data"]["invokable"])
                self.assertTrue(response["data"]["can_manage"])
                granted = await request_api(self.native, path, body=command(
                    response["data"], True, f"enable-action-{index}"))
                self.assertTrue(granted["data"]["status"]["invokable"], granted)
                enabled_actions.add(action)
                catalog = {tool.action for tool in self.controller.session.application.external.catalog_for(
                    self.controller.session.profile.target)}
                self.assertEqual(catalog, enabled_actions)

                current = (await request_api(self.native, path))["data"]
                revoked = await request_api(self.native, path, body=command(
                    current, False, f"revoke-action-{index}"))
                self.assertFalse(revoked["data"]["status"]["invokable"], revoked)
                enabled_actions.remove(action)
                catalog = {tool.action for tool in self.controller.session.application.external.catalog_for(
                    self.controller.session.profile.target)}
                self.assertEqual(catalog, enabled_actions)

    async def test_device_control_requires_its_own_consent_and_reaches_the_native_tool(self):
        await self.start(core=True)
        action = "reality.device.control"
        path = PREFIX + CAPABILITY_PATHS[action]
        initial = (await request_api(self.native, path))["data"]
        self.assertFalse(initial["invokable"])
        enabled = await request_api(self.native, path, body=command(initial, True, "enable-device-control"))
        self.assertTrue(enabled["data"]["status"]["invokable"], enabled)
        self.assertFalse((await request_api(self.native, PREFIX))["data"]["consented"])
        self.assertFalse((await request_api(self.native, PREFIX + "/camera"))["data"]["consented"])
        self.assertEqual([tool.action for tool in self.app.external.catalog_for(self.context)], [action])

        event, req = await self.core_request("打开书桌灯", "device-control-event")
        arguments = {"alias": "desk-lamp", "is_on": True}
        result = await self.call_core(event, req, action, arguments, "device-switch")
        self.assertEqual((result["state"], result["effect"]), ("succeeded", "committed"), result)
        self.assertIn("confirmed", result["payload"])
        replay = await self.call_core(event, req, action, arguments, "device-switch")
        self.assertEqual(replay["operation_id"], result["operation_id"])
        self.assertEqual(replay, result)
        self.assertEqual(self.mobile.device_calls, [("desk-lamp", True)])
        row = operation_row(self.app.runtime, result["invocation_id"])
        self.assertEqual((row["state"], row["action"], row["execution_path"]),
                         ("succeeded", action, "astrbot_core"))

        current = (await request_api(self.native, path))["data"]
        revoked = await request_api(self.native, path, body=command(current, False, "revoke-device-control"))
        self.assertFalse(revoked["data"]["status"]["invokable"])
        self.assertEqual(self.app.external.catalog_for(self.context), ())
        denied = await self.call_core(event, req, action, arguments, "revoked-device-switch")
        self.assertEqual(denied["state"], "failed", denied)
        self.assertEqual(self.mobile.device_calls, [("desk-lamp", True)])
        self.assertEqual(self.mobile.users, [])
        self.assertEqual(self.mobile.camera_calls, [])
        self.assert_core_only()

    async def test_camera_has_independent_consent_and_only_recent_sanitized_private_result(self):
        await self.start(core=True)
        camera_path = PREFIX + "/camera"
        initial = (await request_api(self.native, camera_path))["data"]
        self.assertEqual(initial["action"], CAMERA_ACTION)
        self.assertFalse(initial["invokable"])
        enabled = await request_api(self.native, camera_path,
                                    body=command(initial, True, "enable-camera"))
        self.assertTrue(enabled["data"]["status"]["invokable"], enabled)
        self.assertFalse((await request_api(self.native))["data"]["consented"])
        self.assertFalse((await request_api(self.native, PREFIX + "/device-control"))["data"]["consented"])
        event, req = await self.core_request("请看看摄像头现在拍到什么", "camera-event")
        arguments = {"purpose": "看看我现在在做什么"}
        denied = await self.call_core(event, req, CAMERA_ACTION, arguments, "camera-local-consent-missing")
        self.assertEqual((denied["state"], denied["effect"]), ("failed", "not_committed"), denied)
        self.assertEqual(self.mobile.camera_calls, [])
        local_consent = await self.reality._perform_page_action({
            "action": "consent_camera", "user_id": "200", "confirmed": True})
        self.assertTrue(local_consent["ok"], local_consent)
        mobile_reads = list(self.mobile.users)

        result = await self.call_core(event, req, CAMERA_ACTION, arguments, "camera-read")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertNotIn("must-never-enter-V2", result["payload"])
        observed = json.loads(result["payload"])["native"]["result"]
        self.assertEqual(observed["observation"]["summary"], "桌面上有打开的笔记本电脑。")
        replay = await self.call_core(event, req, CAMERA_ACTION, arguments, "camera-read")
        self.assertEqual(replay, result)
        self.assertEqual(self.mobile.users, mobile_reads)
        self.assertEqual(self.mobile.camera_calls, ["frame"])
        self.reality._analyze_reality_touch_camera_frame.assert_awaited_once()
        user = self.reality._user("200", create=False)
        saved = self.reality._reality_touch_camera_policy(user)["last_observation"]
        self.assertEqual(saved["source"], "assistant_tool_private")
        self.assertEqual(saved["purpose"], arguments["purpose"])

        runtime = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]
        camera = runtime["camera"]
        self.assertEqual(camera["state"], "available", camera)
        self.assertEqual(camera["observation"]["summary"], "桌面上有打开的笔记本电脑。")
        self.assertNotIn("image_base64", json.dumps(camera, ensure_ascii=False))

        current = (await request_api(self.native, camera_path))["data"]
        revoked = await request_api(self.native, camera_path,
                                    body=command(current, False, "revoke-camera"))
        self.assertFalse(revoked["data"]["status"]["invokable"])
        hidden = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]["camera"]
        self.assertEqual(hidden["state"], "blocked")
        self.assertNotIn("observation", hidden)
        denied = await self.call_core(event, req, CAMERA_ACTION, arguments, "revoked-camera-read")
        self.assertEqual(denied["state"], "failed", denied)
        self.assertEqual(self.mobile.camera_calls, ["frame"])

        mobile = (await request_api(self.native))["data"]
        mobile_enabled = await request_api(self.native, body=command(mobile, True, "enable-mobile"))
        self.assertTrue(mobile_enabled["data"]["status"]["invokable"])
        self.assertFalse((await request_api(self.native, camera_path))["data"]["consented"])
        self.assert_core_only()

    async def test_camera_is_not_dispatched_without_an_explicit_current_request(self):
        await self.start(core=True)
        camera_path = PREFIX + "/camera"
        initial = (await request_api(self.native, camera_path))["data"]
        enabled = await request_api(self.native, camera_path,
                                    body=command(initial, True, "enable-camera-without-request"))
        self.assertTrue(enabled["data"]["status"]["invokable"], enabled)
        local_consent = await self.reality._perform_page_action({
            "action": "consent_camera", "user_id": "200", "confirmed": True})
        self.assertTrue(local_consent["ok"], local_consent)
        mobile_reads = list(self.mobile.users)
        # An older explicit request cannot authorize a capture for a new event.
        await self.core_request("请看看摄像头现在拍到什么", "camera-older-request")
        event, req = await self.core_request("你知道我现在在做什么吗", "camera-no-request")
        result = await self.call_core(event, req, CAMERA_ACTION,
            {"purpose": "看看我现在在做什么"}, "camera-without-request")
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(self.mobile.camera_calls, [])
        self.assertEqual(self.mobile.users, mobile_reads)
        denial = self.app.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                          (result["receipt_ref"],))
        self.assertEqual(json.loads(denial["value"])["code"], "reality_camera_explicit_request_required")
        runtime = (await request_api(self.native, PERCEPTION_PREFIX + "/runtime"))["data"]
        self.assertEqual(runtime["camera"]["state"], "ready")
        self.assertNotIn("observation", runtime["camera"])
        event, req = await self.core_request("现在请看看摄像头拍到什么", "camera-current-request")
        success = await self.call_core(event, req, CAMERA_ACTION,
            {"purpose": "看看我现在在做什么"}, "camera-current-read")
        self.assertEqual(success["state"], "succeeded", success)
        self.assertEqual(self.mobile.camera_calls, ["frame"])
        self.assert_core_only()

    async def test_device_unknown_result_cannot_dispatch_again(self):
        await self.start(core=True)
        action = "reality.device.control"
        path = PREFIX + CAPABILITY_PATHS[action]
        initial = (await request_api(self.native, path))["data"]
        enabled = await request_api(self.native, path,
            body=command(initial, True, "enable-device-unknown"))
        self.assertTrue(enabled["data"]["status"]["invokable"], enabled)
        transport = AsyncMock(side_effect=TimeoutError("synthetic device result unknown"))
        self.mobile.extension_control_power = transport
        event, req = await self.core_request("打开书桌灯", "device-unknown-event")
        arguments = {"alias": "desk-lamp", "is_on": True}
        unknown = await self.call_core(event, req, action, arguments, "device-unknown")
        self.assertEqual((unknown["state"], unknown["effect"]), ("unknown", "unknown"), unknown)
        same = await self.call_core(event, req, action, arguments, "device-unknown")
        self.assertEqual(same, unknown)
        another = await self.call_core(event, req, action, arguments, "device-unknown-new-id")
        self.assertEqual(another["code"], "external_effect_unresolved", another)
        event, req = await self.core_request("再打开书桌灯", "device-unknown-new-event")
        another = await self.call_core(event, req, action, arguments, "device-unknown-new-event-id")
        self.assertEqual(another["code"], "external_effect_unresolved", another)
        transport.assert_awaited_once()
        self.assert_core_only()


class RealityAccessMultiTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_bot_and_persona_scopes_remain_independent_after_revocation(self):
        mobile = MobileSnapshot()
        self.controller.register_routes()
        await self.start_host()
        mobile.install_io(self.controller.native_extensions.service("reality"))
        roles = self.controller.web_api.target_inventory()
        paths = { (target["target_id"], role["role_id"]): role["reality_access_api"]
                 for target in roles for role in target["roles"] }
        first = paths[("first", "role-a")]
        initial = (await request_api(self.native, first))["data"]
        enabled = await request_api(self.native, first, body=command(initial, True, "enable-first"))
        self.assertTrue(enabled["data"]["status"]["invokable"], enabled)
        for key in (("first", "role-b"), ("second", "role-a"), ("second", "role-b")):
            self.assertFalse((await request_api(self.native, paths[key]))["data"]["invokable"])
        second = paths[("second", "role-a")]
        second_status = (await request_api(self.native, second))["data"]
        enabled_second = await request_api(self.native, second,
            body=command(second_status, True, "enable-second"))
        self.assertTrue(enabled_second["data"]["status"]["invokable"], enabled_second)
        # A token from one Bot/role cannot grant the other path.
        wrong = await request_api(self.native, first,
            body=command(enabled_second["data"]["status"], False, "wrong-scope"))
        self.assertEqual(wrong["data"]["code"], "reality_access_changed")
        latest = (await request_api(self.native, first))["data"]
        revoked = await request_api(self.native, first, body=command(latest, False, "revoke-first"))
        self.assertFalse(revoked["data"]["status"]["invokable"])
        self.assertTrue((await request_api(self.native, second))["data"]["invokable"])
        self.assertEqual(mobile.users, [])
