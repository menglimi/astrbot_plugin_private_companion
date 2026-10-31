"""Exercise the persistent first-run guide against synthetic AstrBot adapters."""

import json
import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import ANY, AsyncMock, Mock, patch

from tests.test_astrbot_staging_host import WebContext, host_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi, completion
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_management_access import NOW
from astrbot_host.binding_store import PendingBinding, write_binding
from astrbot_host.controller import AstrBotStagingController
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.setup_api import (PROACTIVE_TEST_FAILURE_LABELS, PROACTIVE_TEST_STAGE_BY_CODE,
                                    PROACTIVE_TEST_SUGGESTIONS, SetupWebAPI)
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.execution.models import TaskError
from companion_v2.proactive.diagnostic_models import DiagnosticSourceError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind


class AstrBotSetupFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-setup-")
        self.root = Path(self.temporary.name)
        self.native = WebContext()
        self.types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.config = host_config(staging={"profile_id": "setup-flow", "delivery_mode": "onebot_private"})
        self.controller = self.make_single_controller()
        self.api = SetupWebAPI(self.controller)
        self.native.get_all_providers = lambda: [self.native.provider]
        self.native.provider.meta = lambda: NS(id="native-model", name="Fixture model")

    def make_single_controller(self):
        return AstrBotStagingController(self.native, self.config, self.root,
                                        native_types=self.types, now=lambda: NOW)

    def attach_config_writer(self):
        def save(candidate):
            self.config.clear()
            self.config.update(candidate)
            self.controller.refresh_settings()
        self.controller.save_plugin_config = save

    async def asyncTearDown(self):
        await self.controller.terminate()
        self.temporary.cleanup()

    async def call(self, name, body=None, *, username="administrator"):
        payload = (b"" if name in {"model_tasks", "profile"} else
                   json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8"))
        request = NS(username=username, query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request):
            return await getattr(self.api, name)()

    async def test_profile_save_accepts_the_full_field_set(self):
        body = {
            "target": "role_profile",
            "operation_key": "setup-profile-body-limit",
            "edit_token": "0" * 64,
            "changes": {"identity": "身" * 2000, "personality": "格" * 2400,
                        "world": "世" * 4200},
        }
        self.assertGreater(len(json.dumps(body, ensure_ascii=False).encode("utf-8")), 4096)
        reached = []

        async def stop_after_body():
            reached.append(True)
            raise TaskError("test_body_accepted", "profile body was accepted")

        with patch.object(self.api, "_setup_context", stop_after_body):
            result = await self.call("save_profile", body)
        self.assertTrue(reached, result)
        self.assertEqual(result["data"]["code"], "test_body_accepted")

    async def test_setup_api_has_no_runtime_mode_endpoint_or_status_field(self):
        routes = {route for route, *_ in self.api.routes()}
        self.assertNotIn("/astrbot_plugin_private_companion/setup/mode", routes)
        status = await self.call("status")
        self.assertEqual(status["status"], "ok", status)
        self.assertNotIn("mode", status["data"])

    def prepare_contact_model(self, text="想起你说过的航行计划。"):
        async def behavior(_payload, _tools):
            return completion(json.dumps({"decision": "send", "text": text,
                                          "decision_confidence": 0.96}, ensure_ascii=False))
        self.native.provider.client.state.behavior = behavior

    async def wait_for_test(self):
        for _ in range(500):
            receipt = self.api.progress.read()["proactive_test"]
            if receipt and receipt["state"] != "started":
                return receipt
            await asyncio.sleep(0.01)
        self.fail(f"proactive test did not reach a terminal receipt: {self.api.progress.read()['proactive_test']}")

    async def skip_optional_setup_sections(self):
        for step in ("proactive", "life", "group"):
            result = await self.call("complete", {"step": step, "skipped": True})
            self.assertEqual(result["status"], "ok", result)

    def test_proactive_authorization_diagnostics_distinguish_login_and_core_permission(self):
        cases = {
            "dashboard_identity_required": "Dashboard 登录身份不可用",
            "unbound_identity": "Dashboard 身份尚未完成绑定",
            "binding_revoked": "Dashboard 身份绑定已撤销",
            "binding_expired": "Dashboard 身份绑定已过期",
            "binding_not_active": "Dashboard 身份绑定尚未生效",
            "binding_rebound": "Dashboard 身份已重新绑定",
            "scope_installation_mismatch": "Dashboard 身份不属于当前安装",
            "scope_not_granted": "当前 Dashboard 身份未获该配置范围授权",
            "core_access_required": "当前 Dashboard 身份没有核心管理权限",
            "authorization_denied": "当前 Dashboard 身份没有配置管理权限",
            "authorization_revoked": "当前 Dashboard 身份的管理授权已撤销",
            "authorization_expired": "当前 Dashboard 身份的管理授权已过期",
            "authorization_not_active": "当前 Dashboard 身份的管理授权尚未生效",
        }
        for code, title in cases.items():
            with self.subTest(code=code):
                self.assertEqual(PROACTIVE_TEST_STAGE_BY_CODE[code], "authorization")
                self.assertEqual(PROACTIVE_TEST_FAILURE_LABELS[code][1], title)
                self.assertTrue(PROACTIVE_TEST_SUGGESTIONS[code])
                result = SetupWebAPI._diagnostic_failure("screen", None, "authorization", code, code)
                self.assertEqual(result["failure_title"], title)
                self.assertIn(result["suggestion"], PROACTIVE_TEST_SUGGESTIONS.values())

    def message_calls(self):
        calls = self.native.platform.client._api._wsr_api.calls
        self.assertTrue(all(action in {"send_private_msg", "get_friend_list", "get_stranger_info", "get_login_info"}
                            for action, _ in calls))
        return [call for call in calls if call[0] == "send_private_msg"]

    async def prepare_trusted_conversation_source(self):
        started = await self.controller.start()
        self.assertIsNone(started.failure, started.failure)
        batch = (await self.controller.receive_claimed(
            FakeEvent(self.native, event_id="setup-trusted-source"))).unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(len(self.message_calls()), 1)

    async def test_proactive_diagnostic_uses_source_backed_route_and_core_context(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE),
                     role_binding=NS(role=NS(subject_id="core-role")))
        task = NS(task_id="diagnostic-task", state="succeeded", reason=None)
        route = {"id": "weather_event", "label": "天气观测生产器", "variant": "weather"}
        candidate = {"accepted": True, "opportunity": object(), "intents": (NS(purpose="environment",
            thought=NS(summary="角色配置地点的当前天气观测：晴，18℃。")),),
            "role_instruction": "role instructions", "route": route,
            "stages": [{"stage": "weather_fetch", "state": "passed", "detail": "真实天气接口观测"}], "diagnostic_origin": {
                "_diagnostic": {"route_id": "weather_event", "batch_id": "batch-1",
                    "purpose": "environment", "event_key": "event-1", "event_hash": "hash-1", "role_hash": "role-1"},
                "weather_event": {"slot": "slot-1"}}}
        app = NS(state="running", now=lambda: NOW)
        app.proactive = NS(contact=AsyncMock(return_value=Outcome.success(NS(task=task))),
            observe=lambda _context, _task_id: Outcome.success({"skipped": False, "delivery": "delivered",
                "judge": "allowed", "judge_allowed": True, "judge_reason": None, "receipt_ref": "receipt-1"}))
        app.tasks = NS(read=lambda _context, _task_id: Outcome.success(task))
        app.contact_schedule = NS(diagnostic_candidate=AsyncMock(return_value=candidate))
        session = NS(application=app)
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._setup_context = AsyncMock(return_value=(session, context, "role instructions"))
        api._binding = lambda: {"bound": True, "pending": False}

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"purposes": {
                    "environment": {"allowed": True, "reason": None},
                    "chat": {"allowed": True, "reason": None}}})

        payload = json.dumps({"purpose": "environment", "confirm_delivery": True}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            response = await api.proactive_diagnostic()

        result = response["data"]
        self.assertEqual(result["state"], "passed", result)
        self.assertEqual(result["recipient"], "core_administrator")
        self.assertEqual(result["purpose"], "environment")
        self.assertEqual(result["route"], route)
        self.assertIn({"stage": "weather_fetch", "state": "passed", "detail": "真实天气接口观测"}, result["stages"])
        app.contact_schedule.diagnostic_candidate.assert_awaited_once_with(context, "environment", at=NOW, test_id=ANY)
        app.proactive.contact.assert_awaited_once()
        args, kwargs = app.proactive.contact.await_args
        self.assertIs(args[0], context)
        self.assertEqual(args[2][0].purpose, "environment")
        self.assertEqual(args[2][0].thought.summary, "角色配置地点的当前天气观测：晴，18℃。")
        self.assertEqual(kwargs["role_instruction"], "role instructions")
        self.assertTrue(kwargs["administrator_test"])
        self.assertTrue(kwargs["diagnostic_source_backed"])
        self.assertEqual(kwargs["diagnostic_origin"], candidate["diagnostic_origin"])
        self.assertFalse(result["replay_allowed"])

        app.proactive.contact.return_value = Outcome.failed("model_budget_exhausted", "local model budget exhausted")
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            budget_response = await api.proactive_diagnostic()
        budget_failure = budget_response["data"]
        self.assertEqual((budget_failure["state"], budget_failure["code"], budget_failure["stage"]),
                         ("failed", "model_budget_exhausted", "model_generation"))
        self.assertIn("硬额度", budget_failure["failure_title"])
        self.assertTrue(any(item == {"stage": "model_generation", "state": "failed"}
                            for item in budget_failure["stages"]))
        app.proactive.contact.return_value = Outcome.success(NS(task=task))

        app.proactive.observe = lambda _context, _task_id: Outcome.success({"skipped": True,
            "delivery": None, "judge": None, "judge_allowed": None, "judge_reason": None,
            "receipt_ref": None, "skip_reason": "model_decision", "model_skip_reason": "no_natural_opening",
            "dedup_reason": None, "presentation_reason": None, "decision_confidence": 0.91,
            "silence_threshold": 0.65})
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            skipped_response = await api.proactive_diagnostic()
        skipped = skipped_response["data"]
        self.assertEqual((skipped["state"], skipped["skip_reason"], skipped["model_skip_reason"]),
                         ("skipped", "model_decision", "no_natural_opening"))
        self.assertIn({"stage": "model_generation", "state": "passed"}, skipped["stages"])

        image_candidate = {"accepted": True, "opportunity": object(), "intents": (NS(purpose="chat",
            thought=NS(summary="自然的聊天开场。")),), "role_instruction": "role instructions",
            "route": {"id": "conversation_scheduler", "label": "真实私聊来源"}, "stages": [],
            "diagnostic_origin": {"_diagnostic": {"route_id": "conversation_scheduler", "purpose": "chat",
                "batch_id": "batch-2", "event_key": "event-2", "event_hash": "hash-2", "role_hash": "role-1"}}}
        app.contact_schedule.diagnostic_candidate.return_value = image_candidate
        app.proactive.observe = lambda _context, _task_id: Outcome.success({"skipped": False,
            "delivery": "delivered", "judge": "allowed", "judge_allowed": True, "judge_reason": None,
            "receipt_ref": "receipt-2", "stage_metrics": {
                "image_attachment": {"state": "passed", "attached": True}}})
        request.body.return_value = json.dumps({"purpose": "chat", "confirm_delivery": True,
            "diagnostic_image": True}).encode("utf-8")
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            image_response = await api.proactive_diagnostic()
        image_result = image_response["data"]
        self.assertEqual((image_result["state"], image_result["delivery_state"]), ("passed", "delivered"))
        self.assertIn({"stage": "image_attachment", "state": "passed"}, image_result["stages"])
        self.assertTrue(app.proactive.contact.await_args.kwargs["diagnostic_image_intent"])

    async def test_proactive_diagnostic_routes_mobile_location_to_reality_producer(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE, native_scope_id="200"))
        task = NS(task_id="mobile-location-diagnostic", state="succeeded", reason=None)
        receipt = {"_diagnostic": {"route_id": "reality_location_event", "purpose": "environment",
            "batch_id": "batch-1", "event_key": "event-1", "event_hash": "hash-1", "role_hash": "role-1"}}
        candidate = {"accepted": True, "opportunity": object(), "intents": (NS(purpose="environment",
            thought=NS(summary="手机陪伴终端本次上报的地点。")),), "role_instruction": "role instructions",
            "route": {"id": "reality_location_event", "label": "手机陪伴终端地点来源",
                      "variant": "mobile_location"}, "stages": [
                {"stage": "mobile_location_fetch", "state": "passed"}],
            "diagnostic_origin": {**receipt, "reality_location_event": {"test_id": "attempt-1",
                "captured_at": 123, "source_id": "reality-location-1", "place_fingerprint": "place-1"}}}
        app = NS(state="running", now=lambda: NOW,
            contact_schedule=NS(diagnostic_candidate=AsyncMock(),
                diagnostic_receipt=Mock(return_value=(object(), object(), object(), receipt))),
            proactive=NS(contact=AsyncMock(return_value=Outcome.success(NS(task=task))),
                observe=lambda _context, _task_id: Outcome.success({"skipped": False, "delivery": "delivered",
                    "judge": "allowed", "judge_allowed": True, "judge_reason": None,
                    "receipt_ref": "receipt-1", "stage_metrics": {}})),
            tasks=NS(read=lambda _context, _task_id: Outcome.success(task)))
        session = NS(application=app)
        native_api = NS(mobile_location_diagnostic_candidate=AsyncMock(return_value=candidate))
        reality = NS(v2_api=Mock(return_value=native_api))
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})),
            native_extensions=NS(active_service=Mock(return_value=reality)))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._proactive_diagnostic_target = AsyncMock(return_value=object())
        api._proactive_diagnostic_context = AsyncMock(return_value=(session, context, "role instructions"))
        api._binding = lambda: {"bound": True, "pending": False}
        api._diagnostic_usage_summary = lambda _session, _context: {}

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"purposes": {"environment": {"allowed": True, "reason": None}}})

        payload = json.dumps({"purpose": "environment", "confirm_delivery": True,
                              "synthetic_event": False, "variant": "mobile_location"}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            response = await api.proactive_diagnostic()

        result = response["data"]
        self.assertEqual((result.get("state"), result.get("route"), result.get("delivery_state")),
            ("passed", candidate["route"], "delivered"), result)
        controller.native_extensions.active_service.assert_called_once_with("reality")
        app.contact_schedule.diagnostic_receipt.assert_called_once_with(
            context, "environment", "reality_location_event", NOW)
        native_api.mobile_location_diagnostic_candidate.assert_awaited_once_with(
            "200", test_id=result["attempt_id"], diagnostic_origin=receipt)
        app.contact_schedule.diagnostic_candidate.assert_not_awaited()
        self.assertEqual(app.proactive.contact.await_args.kwargs["diagnostic_origin"],
                         candidate["diagnostic_origin"])

    async def test_proactive_diagnostic_uses_synthetic_candidate_and_real_delivery_pipeline(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE))
        task = NS(task_id="synthetic-diagnostic-task", state="succeeded", reason=None)
        route = {"id": "synthetic_event", "label": "合成测试事件", "variant": "news"}
        diagnostic_origin = {"_diagnostic": {"route_id": "synthetic_event", "purpose": "news",
            "batch_id": "batch-1", "event_key": "event-1", "event_hash": "hash-1", "role_hash": "role-1"},
            "synthetic_event": {"test_id": "attempt-1", "scenario_hash": "scenario-1"}}
        candidate = {"opportunity": object(), "intents": (NS(purpose="news",
            thought=NS(summary="合成测试事件：模拟虚构资讯分享。")),),
            "role_instruction": "role instructions", "route": route, "diagnostic_origin": diagnostic_origin}
        app = NS(state="running", now=lambda: NOW)
        app.proactive = NS(contact=AsyncMock(return_value=Outcome.success(NS(task=task))),
            observe=lambda _context, _task_id: Outcome.success({"skipped": False, "delivery": "delivered",
                "judge": "allowed", "judge_allowed": True, "judge_reason": None, "receipt_ref": "receipt-1"}))
        app.tasks = NS(read=lambda _context, _task_id: Outcome.success(task))
        app.contact_schedule = NS(
            diagnostic_candidate=AsyncMock(),
            synthetic_diagnostic_candidate=AsyncMock(return_value=candidate))
        session = NS(application=app)
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._setup_context = AsyncMock(return_value=(session, context, "role instructions"))
        api._binding = lambda: {"bound": True, "pending": False}

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"purposes": {"news": {"allowed": True, "reason": None},
                                                      "screen": {"allowed": True, "reason": None}}})

        payload = json.dumps({"purpose": "news", "confirm_delivery": True,
                              "synthetic_event": True}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            response = await api.proactive_diagnostic()

        result = response["data"]
        self.assertEqual((result["state"], result["event_mode"], result["route"]),
                         ("passed", "synthetic_event", route))
        app.contact_schedule.synthetic_diagnostic_candidate.assert_awaited_once()
        call_args = app.contact_schedule.synthetic_diagnostic_candidate.await_args
        self.assertEqual(call_args.args, (context, "news"))
        self.assertEqual(call_args.kwargs["at"], NOW)
        self.assertTrue(call_args.kwargs["test_id"])
        app.contact_schedule.diagnostic_candidate.assert_not_awaited()
        app.proactive.contact.assert_awaited_once()
        kwargs = app.proactive.contact.await_args.kwargs
        self.assertTrue(kwargs["administrator_test"])
        self.assertFalse(kwargs["diagnostic_source_backed"])
        self.assertEqual(kwargs["diagnostic_origin"], diagnostic_origin)

    async def test_proactive_diagnostic_recovers_recipient_from_current_core_identity(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE))
        task = NS(task_id="diagnostic-task", state="succeeded", reason=None)
        route = {"id": "weather_event", "label": "天气观测生产器", "variant": "weather"}
        candidate = {"opportunity": object(), "intents": (NS(purpose="environment",
            thought=NS(summary="角色配置地点的当前天气观测：晴，18℃。")),),
            "role_instruction": "role instructions", "route": route, "diagnostic_origin": {
                "_diagnostic": {"route_id": "weather_event", "batch_id": "batch-1",
                    "purpose": "environment", "event_key": "event-1", "event_hash": "hash-1", "role_hash": "role-1"},
                "weather_event": {"slot": "slot-1"}}}
        app = NS(state="running", now=lambda: NOW)
        app.proactive = NS(contact=AsyncMock(return_value=Outcome.success(NS(task=task))),
            observe=lambda _context, _task_id: Outcome.success({"skipped": False, "delivery": "delivered",
                "judge": "allowed", "judge_allowed": True, "judge_reason": None, "receipt_ref": "receipt-1"}))
        app.tasks = NS(read=lambda _context, _task_id: Outcome.success(task))
        app.contact_schedule = NS(diagnostic_candidate=AsyncMock(return_value=candidate))
        session = NS(application=app, synchronize=AsyncMock(return_value=(context, "role instructions")))
        target = NS(target_id="private-core", platform_id="qq-main", account_id="100",
                    bot_id="staging-bot", participant_id="200")
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})), _multi_host=True,
            proactive_diagnostic_target=AsyncMock(return_value=target))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._binding = lambda: ({"bound": True, "pending": False,
            "platform_id": target.platform_id, "account_id": target.account_id,
            "participant_id": target.participant_id} if controller.proactive_diagnostic_target.await_count
            else {"bound": False, "pending": False})

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"purposes": {"environment": {"allowed": True, "reason": None}}})

        payload = json.dumps({"purpose": "environment", "confirm_delivery": True}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            response = await api.proactive_diagnostic()

        result = response["data"]
        self.assertEqual(result["state"], "passed", result)
        self.assertEqual(result["recipient"], "core_administrator")
        session.synchronize.assert_awaited_once_with(target)
        self.assertEqual(controller.proactive_diagnostic_target.await_count, 2)
        app.proactive.contact.assert_awaited_once()

    async def test_proactive_diagnostic_only_bypasses_master_and_time_gates(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE))
        task = NS(task_id="diagnostic-task", state="succeeded", reason=None)
        candidate = {"opportunity": object(), "intents": (NS(purpose="care",
            thought=NS(summary="对方刚才明确说最近很累。")),), "role_instruction": "role instructions",
            "route": {"id": "relationship_event", "label": "关系语境候选", "variant": "care"},
            "diagnostic_origin": {"_diagnostic": {"route_id": "relationship_event", "batch_id": "batch-1",
                "purpose": "care", "event_key": "event-1", "event_hash": "hash-1", "role_hash": "role-1"},
                "relationship_event": {"purpose": "care"}}}
        app = NS(state="running", now=lambda: NOW)
        app.proactive = NS(
            contact=AsyncMock(return_value=Outcome.success(NS(task=task))),
            observe=lambda _context, _task_id: Outcome.success({"skipped": False, "delivery": "delivered",
                "judge": None, "judge_allowed": None, "judge_reason": None, "receipt_ref": "receipt-1"}))
        app.tasks = NS(read=lambda _context, _task_id: Outcome.success(task))
        app.contact_schedule = NS(diagnostic_candidate=AsyncMock(return_value=candidate))
        session = NS(application=app)
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._setup_context = AsyncMock(return_value=(session, context, "role instructions"))
        api._binding = lambda: {"bound": True, "pending": False}

        preferences = {"enabled": True, "care.enabled": True}
        blocked_reason = "proactive_quiet_hours"

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"settings": preferences,
                    "purposes": {"care": {"allowed": False, "reason": blocked_reason}}})

        payload = json.dumps({"purpose": "care", "confirm_delivery": True}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        for master, purpose_enabled, blocked_reason, permitted in (
                (True, True, "proactive_quiet_hours", True),
                (True, True, "proactive_paused", True),
                (False, True, "proactive_disabled", True),
                (False, False, "proactive_disabled", False),
                (True, False, "proactive_disabled", False),
                (True, True, "authorization_revoked", False)):
            with self.subTest(master=master, purpose=purpose_enabled, reason=blocked_reason):
                preferences.update(enabled=master, **{"care.enabled": purpose_enabled})
                app.proactive.contact.reset_mock()
                app.contact_schedule.diagnostic_candidate.reset_mock()
                with patch("astrbot_host.setup_api.request", request), \
                     patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
                     patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
                    response = await api.proactive_diagnostic()
                result = response["data"]
                if permitted:
                    self.assertEqual(result["state"], "passed", result)
                    self.assertEqual(result["delivery_state"], "delivered")
                    app.proactive.contact.assert_awaited_once()
                    self.assertTrue(app.proactive.contact.await_args.kwargs["administrator_test"])
                else:
                    self.assertEqual(result["state"], "failed", result)
                    self.assertEqual(result["code"], blocked_reason)
                    app.proactive.contact.assert_not_awaited()
                    app.contact_schedule.diagnostic_candidate.assert_not_awaited()
                self.assertEqual(preferences["enabled"], master)

    async def test_proactive_diagnostic_without_a_real_source_never_calls_contact(self):
        context = NS(scope=NS(kind=ScopeKind.PRIVATE))
        app = NS(state="running", now=lambda: NOW)
        app.proactive = NS(contact=AsyncMock())
        app.contact_schedule = NS(diagnostic_candidate=AsyncMock(side_effect=TaskError(
            "contact_diagnostic_source_required", "no current news source")))
        session = NS(application=app)
        controller = NS(session=session, last_session=None, runtime=object(), now=lambda: NOW,
            start=AsyncMock(return_value=Outcome.success({})))
        api = SetupWebAPI.__new__(SetupWebAPI)
        api.controller = controller
        api._test_lock = asyncio.Lock()
        api._setup_context = AsyncMock(return_value=(session, context, "role instructions"))
        api._binding = lambda: {"bound": True, "pending": False}

        class ContactManager:
            def __init__(self, _app, _context):
                pass

            def status(self, _operator):
                return Outcome.success({"purposes": {"news": {"allowed": True, "reason": None}, "screen": {"allowed": True, "reason": None}}})

        payload = json.dumps({"purpose": "news", "confirm_delivery": True}).encode("utf-8")
        request = NS(username="administrator", query={}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            response = await api.proactive_diagnostic()

        result = response["data"]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["code"], "contact_diagnostic_source_required")
        self.assertEqual(result["stage"], "trusted_candidate")
        self.assertEqual(result["failure_title"], "当前用途没有可核验的测试机会")
        self.assertIn("阶段记录", result["explanation"])
        self.assertIn("关系表达类使用已完成的私聊窗口和用途设置", result["suggestion"])
        self.assertEqual(result["stages"][-1], {"stage": "trusted_candidate", "state": "failed"})
        self.assertTrue(all(stage["state"] == "passed" for stage in result["stages"][:-1]))
        app.proactive.contact.assert_not_awaited()
        app.contact_schedule.diagnostic_candidate.assert_awaited_once_with(context, "news", at=NOW, test_id=ANY)

        app.contact_schedule.diagnostic_candidate.side_effect = DiagnosticSourceError(
            "model_timeout", "视觉模型结果未知", stage="screen_interpretation", state="unknown",
            task_id="vision-task", stages=[{"stage": "screen_capture", "state": "passed"}])
        request.body.return_value = json.dumps({"purpose": "screen", "confirm_delivery": True}).encode("utf-8")
        with patch("astrbot_host.setup_api.request", request), \
             patch("astrbot_host.setup_api.ContactManagement", ContactManager), \
             patch("astrbot_host.setup_api.dashboard_operator", return_value=object()):
            unknown = (await api.proactive_diagnostic())["data"]
        self.assertEqual((unknown["state"], unknown["stage"], unknown["task_id"]),
                         ("unknown", "screen_interpretation", "vision-task"))
        self.assertEqual(unknown["stages"][-2:], [{"stage": "screen_capture", "state": "passed"},
                                                {"stage": "screen_interpretation", "state": "unknown"}])
        app.proactive.contact.assert_not_awaited()

    async def bind_core_window(self, persona_id="original"):
        write_binding(self.controller.binding_root,
                      PendingBinding("qq-main", "100", "200", persona_id))
        result = await self.call("confirm_binding")
        self.assertEqual(result["status"], "ok", result)

    async def enter_model_step(self):
        self.attach_config_writer()
        await self.bind_core_window()
        self.assertIsNone(self.controller.runtime)
        status = await self.call("status")
        self.assertEqual(status["data"]["step"], "model")

    async def test_binding_precedes_model_and_does_not_initialize_the_runtime(self):
        initial = await self.call("status")
        self.assertEqual(initial["data"]["step"], "binding")
        await self.enter_model_step()
        self.assertEqual(self.message_calls(), [])
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_setup_saves_config_data_without_copying_host_locks(self):
        class HostConfig(dict):
            pass

        self.config = HostConfig(self.config)
        self.config.save_lock = threading.Lock()
        self.controller.config = self.config
        await self.enter_model_step()
        result = await self.call("model", {"provider_id": "native-model"})
        self.assertEqual(result["status"], "ok", result)

    async def test_model_selection_persists_and_prepares_before_knowledge(self):
        await self.enter_model_step()

        result = await self.call("model", {"provider_id": "native-model"})

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["model"]["provider_id"], "native-model")
        self.assertIsNotNone(self.controller.runtime)
        self.assertFalse(self.controller.status()["accepting"])
        status = await self.call("status")
        self.assertEqual(status["data"]["step"], "model")
        advanced = await self.call("complete", {"step": "model", "skipped": False})
        self.assertEqual(advanced["status"], "ok", advanced)
        self.assertEqual((await self.call("status"))["data"]["step"], "knowledge")
        self.assertEqual(self.message_calls(), [])
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_model_can_be_saved_while_the_shared_host_is_running(self):
        await self.enter_model_step()
        with patch.object(self.controller, "initialize_profile",
                          AsyncMock(return_value=Outcome.failed("host_must_stop", "host is running"))):
            result = await self.call("model", {"provider_id": "native-model"})
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"].get("restart_required"))

    async def test_model_selection_rejects_unloaded_provider_without_saving(self):
        await self.enter_model_step()

        result = await self.call("model", {"provider_id": "missing-model"})

        self.assertEqual(result["data"]["code"], "host_provider_unavailable")
        self.assertEqual(self.config["model"]["provider_id"], "native-model")
        self.assertIsNone(self.controller.runtime)

    async def test_model_setup_uses_all_loaded_providers_without_registering_them(self):
        secondary = FakeProvider()
        secondary.provider_config["id"] = "secondary-model"
        secondary.meta = lambda: NS(id="secondary-model", name="Second model")
        providers = {"native-model": self.native.provider, "secondary-model": secondary}
        self.native.get_all_providers = lambda: list(providers.values())
        self.native.get_provider_by_id = lambda provider_id: providers.get(provider_id)
        await self.enter_model_step()

        result = await self.call("model", {"provider_id": "native-model"})

        self.assertEqual(result["status"], "ok", result)
        self.assertNotIn("additional_providers", self.config["model"])
        routes = await self.call("model_tasks")
        self.assertEqual(routes["status"], "ok", routes)
        self.assertEqual({item["id"] for item in routes["data"]["providers"]},
                         {"native-model", "secondary-model"})

    async def test_selecting_a_loaded_primary_clears_only_conflicting_legacy_routes(self):
        self.config["model"].update(
            additional_providers=[
                {"provider_id": "secondary-model", "max_output_tokens": 4096},
                {"provider_id": "other-model", "max_output_tokens": 1024},
            ],
            fallback_provider_id="secondary-model",
            replacement_rules='[{"keywords":["use other"],"provider_id":"other-model"}]',
        )
        secondary = FakeProvider()
        secondary.provider_config["id"] = "secondary-model"
        secondary.meta = lambda: NS(id="secondary-model", name="Second model")
        other = FakeProvider()
        other.provider_config["id"] = "other-model"
        other.meta = lambda: NS(id="other-model", name="Other model")
        providers = {"native-model": self.native.provider, "secondary-model": secondary, "other-model": other}
        self.native.get_all_providers = lambda: list(providers.values())
        self.native.get_provider_by_id = lambda provider_id: providers.get(provider_id)
        self.attach_config_writer()
        await self.enter_model_step()

        result = await self.call("model", {"provider_id": "secondary-model"})

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["model"]["provider_id"], "secondary-model")
        self.assertEqual(self.config["model"]["additional_providers"], [
            {"provider_id": "other-model", "max_output_tokens": 1024},
        ])
        self.assertEqual(self.config["model"]["fallback_provider_id"], "")
        self.assertIn('"provider_id":"other-model"', self.config["model"]["replacement_rules"])

    async def test_setup_reads_and_saves_task_routes_and_profiles_without_get_bodies(self):
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")

        routes = await self.call("model_tasks")
        profiles = await self.call("profile")

        self.assertEqual(routes["status"], "ok", routes)
        self.assertTrue(any(item["kind"] == "conversation.reply" for item in routes["data"]["tasks"]))
        self.assertEqual(profiles["status"], "ok", profiles)
        self.assertIn("role_profile", profiles["data"])
        self.assertIn("relationship_profile", profiles["data"])

        task = next(item for item in routes["data"]["tasks"] if item["kind"] == "conversation.reply")
        self.assertEqual(task["configuration_layer"], "role")
        saved = await self.call("configure_model_task", {
            "kind": task["kind"], "operation_key": "setup-route-test", "edit_token": task["edit_token"],
            "changes": {"provider": "native-model", "fallback": ""}, "reset": [],
        })
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["task"]["configuration_layer"], "role")

    async def test_multi_mode_confirmation_seeds_the_first_private_target_and_persona(self):
        await self.controller.terminate()
        self.config["staging"]["enabled"] = False
        self.config["multi_host"] = {"enabled": True, "installation_id": "setup-shared"}
        self.controller = AstrBotMultiController(self.native, self.config, self.root,
                                                 native_types=self.types, now=lambda: NOW)
        self.api = SetupWebAPI(self.controller)
        self.native.persona_manager.get_persona_v3_by_id = lambda persona_id: (
            self.native.persona if persona_id == "original" else None)
        self.attach_config_writer()

        await self.bind_core_window()

        target, persona = self.config["multi_host"]["targets"][0], self.config["multi_host"]["personas"][0]
        self.assertEqual((target["platform_id"], target["account_id"], target["participant_id"]),
                         ("qq-main", "100", "200"))
        self.assertEqual((persona["bot_id"], persona["persona_id"]), (target["bot_id"], "original"))
        self.assertIsNone(self.controller.settings_error)
        self.assertTrue(self.controller.binding_state()["configured_target"])

    async def test_proactive_diagnostic_target_uses_current_core_identity_and_rejects_ambiguity(self):
        await self.controller.terminate()
        self.config["staging"]["enabled"] = False
        self.config["multi_host"] = {"enabled": True, "installation_id": "setup-core-target"}
        self.controller = AstrBotMultiController(self.native, self.config, self.root,
                                                 native_types=self.types, now=lambda: NOW)
        self.api = SetupWebAPI(self.controller)
        self.native.persona_manager.get_persona_v3_by_id = lambda persona_id: (
            self.native.persona if persona_id == "original" else None)
        self.attach_config_writer()

        await self.bind_core_window()
        started = await self.controller.start()
        self.assertIsNone(started.failure, started.failure)
        from astrbot_host.binding_store import clear_binding
        self.assertTrue(clear_binding(self.controller.binding_root))

        target = self.controller.settings.targets[0]
        with self.assertRaises(TaskError) as missing:
            await self.controller.proactive_diagnostic_target()
        self.assertEqual(missing.exception.code, "setup_binding_confirmation_required")

        from companion_v2.identity_policy.models import AuthorizationTier
        from companion_v2.identity_policy.object_access import ObjectAccessPolicy
        policy = ObjectAccessPolicy(self.controller.runtime)
        actor = self.controller.authority.actor(target)
        subject = actor.subject
        policy.set_tier(subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=NOW)
        self.assertEqual(await self.controller.proactive_diagnostic_target(), target)
        binding = self.controller.binding_state()
        self.assertTrue(binding["bound"], binding)
        self.assertTrue(binding["recovered"], binding)
        session, context, _instruction = await self.api._setup_context()
        self.assertIs(session, self.controller.session)
        self.assertEqual(context.scope.native_scope_id, target.participant_id)
        test_session, test_context, _instruction = await self.api._test_context()
        self.assertIs(test_session, session)
        self.assertEqual(test_context.key, context.key)
        role_view = await self.api._role_view()
        self.assertEqual(role_view.target_id, target.target_id)
        self.assertEqual(role_view.role_id, context.role_binding.role.subject_id)

        from dataclasses import replace
        from astrbot_host.multi_settings import PrivateTarget
        second_target = PrivateTarget("second-core-target", target.platform_id, target.account_id,
                                      target.bot_id, "201")
        self.controller.settings = replace(self.controller.settings, targets=(target, second_target))
        second_actor = self.controller.authority.ensure_actor(second_target)
        policy.set_tier(second_actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=NOW)
        with self.assertRaises(TaskError) as ambiguous:
            await self.controller.proactive_diagnostic_target()
        self.assertEqual(ambiguous.exception.code, "setup_core_window_ambiguous")
        with self.assertRaises(TaskError) as setup_ambiguous:
            await self.api._setup_context()
        self.assertEqual(setup_ambiguous.exception.code, "setup_core_window_ambiguous")

    async def test_active_test_is_persisted_and_never_replayed(self):
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "model", "skipped": False}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "knowledge", "skipped": True}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "relationships", "skipped": True}))["status"], "ok")
        await self.skip_optional_setup_sections()
        await self.prepare_trusted_conversation_source()

        generated_text = "想起你说过的航行计划。"
        self.prepare_contact_model(generated_text)
        self.native.platform.client._api._wsr_api.result = {"message_id": -456}
        first = await self.call("proactive_test", {"enable_proactive": True})
        again = await self.call("proactive_test", {"enable_proactive": True})
        receipt = await self.wait_for_test()

        self.assertEqual(first["data"]["state"], "started")
        self.assertEqual(again["data"]["state"], "started")
        self.assertEqual(receipt["state"], "delivered", receipt)
        calls = self.message_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], "send_private_msg")
        self.assertIn(generated_text, json.dumps(calls[1][1], ensure_ascii=False))
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.proactive.management import ContactManagement
        session = self.controller.session or self.controller.last_session
        settings = ContactManagement(session.application, session.profile.target).status(
            dashboard_operator(self.controller.runtime, "administrator", at=NOW)).unwrap()["settings"]
        self.assertFalse(settings["enabled"])
        self.assertFalse(settings["automatic_enabled"])

    async def test_proactive_poll_does_not_load_host_catalog_or_upgrade_plan(self):
        from astrbot_host.setup_state import SETUP_STEPS
        for step in SETUP_STEPS:
            self.api.progress.complete(step)
        self.api.progress.begin_proactive_test(attempt_id="poll-only", started_at=NOW.isoformat())
        self.api._test_task = asyncio.get_running_loop().create_future()
        try:
            with patch.object(self.controller, "status", side_effect=AssertionError("host discovery")), \
                    patch("astrbot_host.upgrade_setup.UpgradeSetup.plan", new=AsyncMock(
                        side_effect=AssertionError("migration discovery"))):
                result = await self.call("proactive_test_status")
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["data"]["proactive_test"]["state"], "started")
            self.assertFalse(result["data"]["ready"])
            denied = await self.call("proactive_test_status", username="api_key:fixture")
            self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        finally:
            self.api._test_task.cancel()

    async def test_proactive_test_stops_when_generation_has_already_failed(self):
        from astrbot_host.setup_state import SETUP_STEPS
        for step in SETUP_STEPS:
            self.api.progress.complete(step)
        manager = NS(status=Mock(return_value=Outcome.success({"settings": {"enabled": True}})))
        candidate = {"opportunity": object(), "intents": (), "role_instruction": "fixture",
                     "route": {"id": "chat", "label": "chat", "variant": "fixture"},
                     "diagnostic_origin": {}}
        for generation in ("failed", "cancelled", "expired", "dead_letter", "uncertain"):
            with self.subTest(generation=generation):
                self.api.progress.path.unlink(missing_ok=True)
                for step in SETUP_STEPS:
                    self.api.progress.complete(step)
                self.api.progress.begin_proactive_test(attempt_id=generation, started_at=NOW.isoformat())
                app = NS(
                    contact_schedule=NS(diagnostic_candidate=AsyncMock(return_value=candidate)),
                    tasks=NS(read=Mock(return_value=Outcome.success(NS(reason="model_generation_failed")))),
                    proactive=NS(task_id=Mock(return_value="task-1"),
                        contact=AsyncMock(return_value=Outcome.success(NS(task=NS(task_id="task-1")))),
                        observe=Mock(return_value=Outcome.success({"generation": generation,
                            "delivery": None, "judge": None, "skipped": False}))))
                with patch.object(self.api, "_test_context", new=AsyncMock(return_value=(NS(application=app), object(), ""))), \
                        patch("astrbot_host.setup_api.dashboard_operator", return_value=object()), \
                        patch("astrbot_host.setup_api.ContactManagement", return_value=manager), \
                        patch.object(self.api, "_restore_test_contact_permission_with_context"), \
                        patch("astrbot_host.setup_api.asyncio.sleep", new=AsyncMock(
                            side_effect=AssertionError("a terminal generation must not keep polling"))):
                    await self.api._execute_proactive_test("administrator", generation)
                receipt = self.api.progress.read()["proactive_test"]
                self.assertEqual(receipt["state"], "unknown" if generation == "uncertain" else "failed")
                self.assertEqual(receipt["error_code"], "setup_contact_generation_failed")
                states = {item["stage"]: item["state"] for item in receipt["stages"]}
                self.assertEqual(states["model_generation"], "failed")
                self.assertEqual(states["delivery"], "skipped")
                app.proactive.observe.assert_called_once()

    async def test_recipient_confirmation_finishes_unknown_test_without_sending_again(self):
        await self.enter_model_step()
        await self.call("model", {"provider_id": "native-model"})
        await self.call("complete", {"step": "model", "skipped": False})
        await self.call("complete", {"step": "knowledge", "skipped": True})
        await self.call("complete", {"step": "relationships", "skipped": True})
        await self.skip_optional_setup_sections()
        await self.prepare_trusted_conversation_source()
        self.prepare_contact_model()
        reverse_api = self.native.platform.client._api._wsr_api
        reverse_api.error = TimeoutError("the message may have been sent")
        self.assertEqual((await self.call("proactive_test", {"enable_proactive": True}))["data"]["state"], "started")
        receipt_after_test = await self.wait_for_test()
        self.assertEqual(receipt_after_test["state"], "unknown", receipt_after_test)
        self.assertEqual((await self.call("proactive_test", {"enable_proactive": True}))["data"]["code"],
                         "setup_test_non_replayable")
        receipt = self.api.progress.read()["proactive_test"]

        stale = await self.call("confirm_proactive_test", {"attempt_id": "another-attempt"})
        self.assertEqual(stale["data"]["code"], "setup_test_confirmation_invalid")
        denied = await self.call("confirm_proactive_test", {"attempt_id": receipt["attempt_id"]},
                                 username="api_key:fixture")
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        self.assertFalse(self.api.progress.read()["ready"])

        sends_before_confirmation = len(self.message_calls())
        confirmed = await self.call("confirm_proactive_test", {"attempt_id": receipt["attempt_id"]})
        self.assertEqual(confirmed["data"]["state"], "received")
        self.assertTrue(confirmed["data"]["ready"])
        saved = self.api.progress.read()["proactive_test"]
        self.assertEqual(saved["finished_at"], receipt["finished_at"])
        self.assertEqual(saved["confirmed_by"], "administrator")
        self.assertEqual((await self.call("proactive_test", {"enable_proactive": True}))["data"]["state"], "received")
        self.assertEqual(len(self.message_calls()), sends_before_confirmation)

        await self.controller.terminate()
        self.controller = self.make_single_controller()
        self.api = SetupWebAPI(self.controller)
        self.assertEqual((await self.call("status"))["data"]["step"], "complete")

    async def test_recipient_confirmation_cannot_complete_setup_without_a_test(self):
        await self.enter_model_step()
        denied = await self.call("proactive_test", {"enable_proactive": False})
        self.assertEqual(denied["data"]["code"], "setup_proactive_opt_in_required")
        result = await self.call("confirm_proactive_test", {"attempt_id": "unstarted"})
        self.assertEqual(result["data"]["code"], "setup_test_confirmation_invalid")
        self.assertFalse(self.api.progress.read()["ready"])
        self.assertEqual(self.message_calls(), [])

    async def test_completed_guide_survives_plugin_restart_without_reopening_setup_steps(self):
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "model", "skipped": False}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "knowledge", "skipped": True}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "relationships", "skipped": True}))["status"], "ok")
        await self.skip_optional_setup_sections()
        await self.prepare_trusted_conversation_source()
        self.prepare_contact_model()
        self.assertEqual((await self.call("proactive_test", {"enable_proactive": True}))["data"]["state"], "started")
        receipt = await self.wait_for_test()
        self.assertEqual(receipt["state"], "delivered", receipt)

        await self.controller.terminate()
        self.controller = self.make_single_controller()
        self.api = SetupWebAPI(self.controller)
        status = await self.call("status")

        self.assertEqual(status["data"]["step"], "complete")
        self.assertTrue(status["data"]["ready"])
        self.assertFalse(status["data"]["host"]["accepting"])

    async def test_interrupted_test_restores_contact_permission_after_plugin_restart(self):
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "model", "skipped": False}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "knowledge", "skipped": True}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "relationships", "skipped": True}))["status"], "ok")
        await self.skip_optional_setup_sections()

        started = await self.controller.start()
        self.assertIsNone(started.failure, started.failure)
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.proactive.management import ContactManagement

        session = self.controller.session or self.controller.last_session
        context = session.profile.target
        operator = dashboard_operator(self.controller.runtime, "administrator", at=NOW)
        manager = ContactManagement(session.application, context)
        from companion_v2.configuration.management import ProactiveContactManagement
        public = ProactiveContactManagement(session.application, context)
        public.configure(operator, {"operation_key": "master-off-before-setup-test",
            "edit_token": public.status(operator).unwrap()["edit_token"], "changes": {"enabled": False}}).unwrap()
        current = manager.status(operator).unwrap()
        attempt_id = "interrupted-setup-test"
        self.api.progress.begin_proactive_test(attempt_id=attempt_id, started_at=NOW.isoformat())
        self.api.progress.record_test_contact_permission(attempt_id=attempt_id, enabled_before=False)
        manager.configure(operator, {"operation_key": "setup-test-" + attempt_id,
            "edit_token": current["edit_token"], "changes": {"enabled": True}}).unwrap()

        await self.controller.terminate()
        self.controller = self.make_single_controller()
        self.api = SetupWebAPI(self.controller)
        status = await self.call("status")

        self.assertEqual(status["status"], "ok", status)
        self.assertEqual(status["data"]["proactive_test"]["state"], "unknown")
        self.assertEqual(status["data"]["proactive_test"]["error_code"], "setup_test_interrupted")
        self.assertTrue(status["data"]["proactive_test"]["contact_permission_restored"])
        recovered_session = self.controller.session or self.controller.last_session
        restored = ContactManagement(recovered_session.application, context).status(
            dashboard_operator(self.controller.runtime, "administrator", at=NOW)).unwrap()["settings"]
        self.assertFalse(restored["enabled"])
        self.assertFalse(restored["automatic_enabled"])
        from companion_v2.proactive.configuration import GROUP, target_for
        recovered_identity = recovered_session.application.tasks.identity
        self.assertTrue(any(grant.capability == GROUP and grant.grant_id.startswith("proactive-managed:")
            and grant.state.value == "active" for grant in recovered_identity.snapshot().grants))
        legacy = recovered_session.application.runtime.read_one(
            "SELECT value_json FROM configuration_values WHERE target_key = ? AND field_id = ?",
            (target_for(context).key, GROUP + ".enabled"))
        self.assertEqual(legacy["value_json"], "false")
        self.assertEqual(self.message_calls(), [])

        started = await self.controller.start()
        self.assertIsNone(started.failure, started.failure)
        active_session = self.controller.session
        active_context = active_session.profile.target
        operator = dashboard_operator(self.controller.runtime, "administrator", at=NOW)
        manager = ContactManagement(active_session.application, active_context)
        current = manager.status(operator).unwrap()
        manager.configure(operator, {"operation_key": "reenable-after-setup-test",
            "edit_token": current["edit_token"], "changes": {"enabled": True}}).unwrap()
        access = active_session.application.tasks.identity.authorize(
            active_context.actor, scope=active_context.scope, capability=GROUP, observed_at=NOW)
        self.assertTrue(access.is_success, access.failure)

    async def test_reconnect_route_starts_only_a_completed_setup(self):
        denied = await self.call("reconnect_host")
        self.assertEqual(denied["data"]["code"], "setup_completion_required")

        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "model", "skipped": False}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "knowledge", "skipped": True}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "relationships", "skipped": True}))["status"], "ok")
        await self.skip_optional_setup_sections()
        await self.prepare_trusted_conversation_source()
        self.prepare_contact_model()
        self.assertEqual((await self.call("proactive_test", {"enable_proactive": True}))["data"]["state"], "started")
        receipt = await self.wait_for_test()
        self.assertEqual(receipt["state"], "delivered", receipt)

        connected = await self.call("reconnect_host")

        self.assertEqual(connected["status"], "ok", connected)
        self.assertTrue(connected["data"]["status"]["host"]["accepting"])

    async def test_unfinished_guide_is_not_reported_as_an_unreplayable_test(self):
        # A fresh install: the guide is still open, and no attempt has ever run.
        # Reporting setup_test_non_replayable here sent the operator looking for a
        # replay problem while proactive_test was still null.
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "model", "skipped": False}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "knowledge", "skipped": True}))["status"], "ok")
        self.assertEqual((await self.call("complete", {"step": "relationships", "skipped": True}))["status"], "ok")
        await self.prepare_trusted_conversation_source()
        self.prepare_contact_model()
        model_calls_before = len(self.native.provider.client.state.calls)

        result = await self.call("proactive_test", {"enable_proactive": True})

        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "setup_steps_incomplete", result)
        self.assertEqual(result["message"], "setup_steps_incomplete")
        self.assertIn("guided setup steps", result["reason"])
        self.assertIsNone(self.api.progress.read()["proactive_test"])
        # Nothing was dispatched and no attempt was recorded: the refusal happened
        # before the test could start, exactly like the null receipt says.
        self.assertEqual(len(self.native.provider.client.state.calls), model_calls_before)

    async def test_a_started_test_still_reports_the_non_replayable_code(self):
        # The genuine replay refusal must keep its original code and message.
        await self.enter_model_step()
        self.assertEqual((await self.call("model", {"provider_id": "native-model"}))["status"], "ok")
        for step, skipped in (("model", False), ("knowledge", True), ("relationships", True),
                              ("proactive", True), ("life", True), ("group", True)):
            self.assertEqual((await self.call("complete", {"step": step, "skipped": skipped}))["status"], "ok")
        await self.prepare_trusted_conversation_source()
        self.prepare_contact_model()
        self.native.platform.client._api._wsr_api.error = TimeoutError("the message may have been sent")

        first = await self.call("proactive_test", {"enable_proactive": True})
        self.assertEqual(first["data"]["state"], "started", first)
        receipt = await self.wait_for_test()
        self.assertEqual(receipt["state"], "unknown", receipt)

        again = await self.call("proactive_test", {"enable_proactive": True})

        self.assertEqual(again["status"], "error", again)
        self.assertEqual(again["data"]["code"], "setup_test_non_replayable", again)
        self.assertIn("cannot be replayed", again["reason"])


if __name__ == "__main__":
    unittest.main()
