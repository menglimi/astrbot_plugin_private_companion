"""Reality belongs to the active V2 context, actual host providers and Cron lifecycle."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiNativeContext, multi_config
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot_host.integration_registry import UnifiedCompanionExtensionAPI
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.multi_settings import PrivateTarget
from companion_v2.adapters.astrbot.native import HostAccount
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from astrbot_host.native_extensions.reality.host_events import RealityHostEvents
from astrbot_host.native_extensions.reality import mobile_gateway
from astrbot_host.native_extensions.reality.mobile_gateway import _MobileRequestState, _mobile_request_state
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityRepository
from companion_v2.conversation.native_history import body, reference
from companion_v2.conversation.native_observations import REALITY_ASSISTANT_OWNER
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.identity_policy.models import AuthorizationTier


class Event:
    def __init__(self, reminder_id, job_id="job-a"):
        self.unified_msg_origin = "qq-main:FriendMessage:200"
        self.extra = {"cron_payload": {"origin": "companion_v2.reality", "sender_id": "200",
            "reality_v2": {"reminder_id": reminder_id}}, "cron_job": {"id": job_id}}

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)


class RealityHostGlueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="v2-reality-host-")
        self.addCleanup(self.temporary.cleanup)
        self.native = MultiNativeContext()
        self.native.get_current_chat_provider_id = AsyncMock(return_value="native-model")
        self.native.send_message = AsyncMock(return_value=True)
        self.native.cron_manager = NS(add_active_job=AsyncMock(return_value=NS(job_id="job-a")),
            delete_job=AsyncMock(), update_job=AsyncMock(), db=NS(get_cron_job=AsyncMock(return_value=None)))
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                          NS, NS, support.FakeReverseApi)
        config = multi_config(targets=[dict(target_id="first", platform_id="qq-main",
            account_id="100", bot_id="bot-a", participant_id="200")],
            personas=[dict(bot_id="bot-a", persona_id=persona, role_id=role)
                      for persona, role in (("original", "role-a"), ("second", "role-b"))])
        self.controller = AstrBotMultiController(self.native, config,
            Path(self.temporary.name), native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        require(await self.controller.initialize_profile())
        require(await self.controller.start())
        self.registry = self.controller.native_extensions
        self.runtime = self.registry.service("reality")
        self.runtime.config["enabled"] = True
        self.runtime._sync_runtime_config()
        package = self.registry._lease("reality").manifest.package
        require(CapabilityRepository(self.controller.runtime).acquire(extension_id="reality",
            package_id=package.package_id, package_version=package.package_version,
            expected_revision=0, observed_at=support.NOW))
        await self.registry.reconcile_service("reality")
        await self.registry._lease("reality").start()
        prepared = require(await self.controller.prepare_core_reply(
            support.FakeEvent(self.native, event_id="reality-seed", text="昨天一起聊到那本天文书。")))
        await prepared["native_source_future"]
        self.context = prepared["context"]
        self.app = self.controller.session.application
        self.api = UnifiedCompanionExtensionAPI(self.controller)
        self.user = self.runtime._user("200")
        self.user["umo"] = self.api._reality_context.origin(self.context)
        self.user["reality_touch_consent"] = {"confirmed": True, "version": 1,
                                             "granted_capabilities": ["local_audio"]}

    def counts(self):
        return {table: self.app.runtime.read_one("SELECT COUNT(*) AS n FROM " + table, ())["n"]
            for table in ("conversation_batches", "conversation_delivery_receipts",
                          "conversation_native_turns", "conversation_native_sends")}

    async def test_primary_camera_user_is_not_ambiguous_with_a_wildcard_test_account(self):
        policy = ObjectAccessPolicy(self.app.runtime)
        policy.set_tier(self.context.actor.subject, AuthorizationTier.CORE,
                        expected_revision=policy.revision, observed_at=support.NOW)
        settings = self.controller.settings
        wildcard = PrivateTarget("test-wildcard", "test", "test-account", "bot-test", "*")
        test_account = HostAccount("test", "test-account", "bot-test")
        self.controller.settings = replace(
            settings,
            targets=settings.targets + (wildcard,),
            account_bindings=settings.accounts + (test_account,),
        )

        self.assertTrue(self.runtime._is_configured_admin_user_id("200"))
        self.assertTrue(self.runtime._reality_touch_camera_user_eligible("200"))

    async def test_current_private_persona_history_and_actual_model_usage(self):
        background = await self.api.get_realtime_context("200", purpose="提醒喝水")
        self.assertIn(self.native.persona["prompt"], background["prompt"])
        self.assertIn("天文书", background["recent_dialogue"])
        self.native.provider.text_chat = AsyncMock(return_value=LLMResponse(role="assistant",
            completion_text="喝口水再继续看吧。", usage=TokenUsage(80, 20, 12)))
        before = self.counts()
        self.assertEqual(await self.api.generate_reality_touch_text("提醒喝水", user_id="200"), "喝口水再继续看吧。")
        self.native.get_current_chat_provider_id.assert_awaited_once_with(self.user["umo"])
        kwargs = self.native.provider.text_chat.await_args.kwargs
        self.assertIn(self.native.persona["prompt"], kwargs["system_prompt"])
        self.assertIn("天文书", kwargs["system_prompt"])
        recent = self.runtime.token_usage.summary()["recent"][-1]
        self.assertEqual((recent["prompt_tokens"], recent["completion_tokens"], recent["total_tokens"]), (100, 12, 112))
        self.assertEqual(self.counts(), before)

    async def test_other_user_paused_service_and_identity_changed_after_await_are_rejected(self):
        with self.assertRaises(TaskError):
            await self.api.get_realtime_context("999")
        with patch.object(self.registry, "active_service", return_value=None):
            with self.assertRaises(TaskError):
                await self.api.get_realtime_context("200")
        guard = self.registry.persona_guard

        async def change(scope, role):
            persona = await guard(scope, role)
            # The installed window mapping owns persona selection; changing
            # AstrBot's global default alone does not switch this V2 window.
            umo = self.api._reality_context.origin(self.context)
            conversation_id = await self.native.conversation_manager.get_curr_conversation_id(umo)
            self.controller.session_topology.update_persona_selection_snapshot(
                umo, "100", "bot-a", "second", "role-b", conversation_id)
            return persona

        with patch.object(self.registry, "persona_guard", side_effect=change):
            with self.assertRaises(TaskError):
                await self.api.get_realtime_context("200")

    async def test_real_chat_is_sent_once_and_history_is_not_a_core_reply_receipt(self):
        before = self.counts()
        for _ in range(2):
            self.assertTrue(await self.api.send_reality_touch_chat(self.user["umo"], "该起来喝点水啦。",
                user_id="200", operation_key="same-reminder"))
        self.native.send_message.assert_awaited_once()
        rows = self.app.runtime.read_all("SELECT * FROM conversation_native_observations WHERE event_id LIKE 'reality-chat:%'", ())
        self.assertEqual(len(rows), 1)
        item = body(self.app.messages, reference(rows[0]), self.context, support.NOW)
        self.assertEqual(item["owner"], REALITY_ASSISTANT_OWNER)
        self.assertEqual(item["execution_path"], "reality_chat")
        self.assertFalse(item["platform_confirmed"])
        self.assertEqual(self.counts(), before)

    async def test_unknown_chat_delivery_is_not_replayed(self):
        self.native.send_message.side_effect = RuntimeError("synthetic unknown send")
        with self.assertRaises(RuntimeError):
            await self.api.send_reality_touch_chat(self.user["umo"], "喝水提醒", user_id="200", operation_key="unknown-reminder")
        self.native.send_message.side_effect = None
        self.assertFalse(await self.api.send_reality_touch_chat(self.user["umo"], "喝水提醒",
            user_id="200", operation_key="unknown-reminder"))
        self.native.send_message.assert_awaited_once()
        row = self.app.runtime.read_one("SELECT state FROM operations WHERE owner='reality.chat'", ())
        self.assertEqual(row["state"], "unknown")

    async def test_v2_tts_requires_own_consent_and_reclaims_its_audio_file(self):
        source = Path(get_astrbot_temp_path()) / "reality-test-source.wav"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"RIFFsynthetic-audio")
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        tts = NS(get_audio=AsyncMock(return_value=str(source)))
        self.native.get_using_tts_provider = Mock(return_value=tts)
        self.runtime._open_reality_touch_audio_file = Mock(return_value={"id": "default", "name": "system"})
        self.assertTrue(await self.runtime._play_reality_touch_text("该起床啦。", user_id="200"))
        played = Path(self.runtime._open_reality_touch_audio_file.call_args.args[0])
        self.assertFalse(played.exists())
        self.native.get_using_tts_provider.assert_called_once_with(self.user["umo"])
        self.user["reality_touch_consent"]["confirmed"] = False
        with self.assertRaises(TaskError):
            await self.api.synthesize_realtime_voice("该起床啦。", user_id="200")
        tts.get_audio.assert_awaited_once()

    async def test_cancelled_playback_waits_for_open_file_and_is_not_repeated(self):
        entered, release = threading.Event(), threading.Event()
        audio = Path(self.temporary.name) / "cancelled.wav"
        audio.write_bytes(b"RIFFsynthetic")
        self.runtime._synthesize_realtime_voice = AsyncMock(return_value={"audio_path": str(audio), "temporary": True})

        def play(path, **kwargs):
            entered.set()
            release.wait(2)
            self.assertTrue(Path(path).exists())
            return {"id": "default"}

        self.runtime._open_reality_touch_audio_file = Mock(side_effect=play)
        task = asyncio.create_task(self.runtime._play_reality_touch_text("起床啦", user_id="200", repeat=2))
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(audio.exists())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(audio.exists())
        self.runtime._open_reality_touch_audio_file.assert_called_once()
        self.assertEqual(self.runtime._reality_touch_store()["last_playback"]["state"], "delivery_unknown")

    async def schedule(self):
        self.assertTrue(await self.runtime._schedule_reality_touch_official_reminder("200",
            {"scheduled_ts": int(datetime.now(timezone.utc).timestamp()) + 3600, "topic": "喝水",
             "delivery_mode": "audio_and_chat"}, source_text="一小时后提醒喝水", trigger_umo=self.user["umo"]))
        return next(iter(self.user["reality_touch_reminders"].values()))

    async def drain(self):
        jobs = [job for job in self.runtime._background_tasks if job is not self.runtime._alarm_task]
        if jobs:
            await asyncio.gather(*jobs)

    def private_event(self, text, *, event_id="reality-command", sender="200", private=True):
        event = support.FakeEvent(self.native, event_id=event_id, sender=sender, text=text)
        event.message_str = text
        event.get_sender_id = Mock(return_value=sender)
        event.is_private_chat = Mock(return_value=private)
        event.stop_event = Mock()
        return event

    async def private_tool(self, text, *, event_id="reality-command"):
        event = self.private_event(text, event_id=event_id)
        owner = RealityHostEvents(self.runtime, lambda: True)
        req = ProviderRequest(system_prompt="原人格")
        owner.request(event, req)
        await self.drain()
        self.assertEqual(req.system_prompt, "原人格")
        self.assertIn(req.func_tool.tools[-1].name,
                      "\n".join(part.text for part in req.extra_user_content_parts))
        return event, owner, req.func_tool.tools[-1], NS(context=NS(event=event))

    async def test_private_command_uses_received_confirmation_and_preserves_core_reply(self):
        self.user.pop("reality_touch_consent")
        event, owner, tool, wrapper = await self.private_tool("我理解风险并确认授权", event_id="unrequested")
        self.assertIn("请先", json.loads(await tool.call(wrapper))["text"])
        self.assertFalse(self.runtime._reality_touch_audio_consented(self.user))
        event, owner, tool, wrapper = await self.private_tool("陪伴 现实触及 授权", event_id="request-audio")
        result = json.loads(await tool.call(wrapper))
        self.assertIn("风险说明", result["text"])
        self.assertEqual(self.user["reality_touch_pending_consent"]["capability"], "local_audio")
        self.assertFalse(self.runtime._reality_touch_audio_consented(self.user))
        event, owner, tool, wrapper = await self.private_tool("我理解风险并确认授权", event_id="confirm-audio")
        first = await tool.call(wrapper)
        self.assertEqual(await tool.call(wrapper), first)
        self.assertTrue(self.runtime._reality_touch_audio_consented(self.user))
        with self.assertRaises(TaskError):
            await tool.call(NS(context=NS(event=self.private_event("我理解风险并确认授权"))))
        response = LLMResponse(role="assistant", completion_text="嗯，那就按你确认的来。")
        owner.response(event, response)
        self.assertEqual(response.completion_text, "嗯，那就按你确认的来。")
        event.stop_event.assert_not_called()
        self.native.send_message.assert_not_awaited()
        for other in (self.private_event("现实触及 授权", sender="999"),
                      self.private_event("现实触及 授权", private=False)):
            req = ProviderRequest(system_prompt="原人格")
            owner.request(other, req)
            self.assertFalse(getattr(getattr(req, "func_tool", None), "tools", ()))

    async def test_private_manager_saves_alarm_and_deduplicates_reminder_registration(self):
        event, owner, tool, wrapper = await self.private_tool("明早用音箱叫醒我", event_id="set-wakeup")
        result = json.loads(await tool.call(wrapper, operation="wakeup", time="07:30", days=[0, 1, 2, 3, 4]))
        self.assertTrue(result["saved"])
        self.assertEqual(result["alarm"]["time"], "07:30")
        event, owner, tool, wrapper = await self.private_tool("一小时后用音箱提醒我喝水", event_id="schedule-reminder")
        arguments = {"operation": "reminder", "delay_minutes": 60, "topic": "喝水", "delivery_mode": "audio_only"}
        first = await tool.call(wrapper, **arguments)
        self.assertEqual(await tool.call(wrapper, **arguments), first)
        result = json.loads(first)
        self.assertTrue(result["accepted"])
        record = self.user["reality_touch_reminders"][result["reminder_id"]]
        self.assertTrue(record["request_key"].startswith("reality-reminder-"))
        self.native.cron_manager.add_active_job.assert_awaited_once()
        event, owner, tool, wrapper = await self.private_tool("一小时后用音箱提醒我喝水", event_id="unknown-registration")
        self.native.cron_manager.add_active_job.side_effect = RuntimeError("synthetic registration unknown")
        result = json.loads(await tool.call(wrapper, **arguments))
        self.assertFalse(result["accepted"])
        replay = json.loads(await tool.call(wrapper, **arguments))
        self.assertEqual(replay["reason"], "registration_unknown")
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.native.cron_manager.add_active_job.await_count, 2)
        row = self.app.runtime.read_one("SELECT state FROM operations WHERE operation_id=?", (record["request_key"],))
        self.assertEqual(row["state"], "completed")

    async def test_wakeup_feedback_updates_state_without_sending_or_stopping_core_reply(self):
        self.user["wakeup_alarm"] = {"enabled": True, "time": "07:30", "days": list(range(7)),
            "contact_session": {"id": "active-wakeup", "status": "pending", "next_attempt_at": 0}}
        event = self.private_event("我醒了", event_id="awake-feedback")
        self.runtime._reply = AsyncMock()
        self.assertTrue(await self.runtime._maybe_handle_wakeup_feedback(event, "200", self.user, event.message_str))
        self.assertEqual(self.user["wakeup_alarm"]["contact_session"]["status"], "acknowledged")
        self.assertEqual(event.get_extra("companion_v2_reality_wakeup_feedback")["intent"], "awake")
        event.stop_event.assert_not_called()
        self.runtime._reply.assert_not_awaited()
        self.native.send_message.assert_not_awaited()

    async def test_page_audio_scan_and_consent_actions_do_not_confirm_for_user(self):
        self.user.pop("reality_touch_consent")
        catalog = {"backend_available": True, "devices": [{"id": "default", "name": "系统默认"}]}
        self.runtime._reality_touch_query_audio_devices = Mock(return_value=catalog)
        await self.runtime.page_status()
        self.runtime._reality_touch_query_audio_devices.assert_not_called()
        scan = await self.runtime._perform_page_action({"action": "scan_audio"})
        self.assertTrue(scan["ok"])
        self.assertEqual(scan["result"], catalog)
        self.runtime._reality_touch_query_audio_devices.assert_called_once()
        requested = await self.runtime._perform_page_action({"action": "request_audio_consent", "user_id": "200"})
        self.assertTrue(requested["ok"])
        self.assertTrue(requested["result"]["confirmation_required"])
        self.assertFalse(self.runtime._reality_touch_audio_consented(self.user))
        revoked = await self.runtime._perform_page_action({"action": "revoke_audio_consent", "user_id": "200"})
        self.assertTrue(revoked["ok"])
        self.assertFalse(revoked["result"]["confirmed"])
        self.assertNotIn("reality_touch_pending_consent", self.user)
        rejected = await self.runtime._perform_page_action({"action": "request_audio_consent", "user_id": "999"})
        self.assertFalse(rejected["ok"])

    async def test_official_cron_tool_and_fallback_deliver_once_then_complete(self):
        reminder = await self.schedule()
        payload = self.native.cron_manager.add_active_job.await_args.kwargs["payload"]
        self.assertEqual(payload["origin"], "companion_v2.reality")
        self.assertEqual(reminder["context_key"], self.context.key)
        event, req = Event(reminder["id"]), ProviderRequest(system_prompt="原人格")
        owner = RealityHostEvents(self.runtime, lambda: True)
        owner.request(event, req)
        self.assertEqual([tool.name for tool in req.func_tool.tools], ["pc_reality_touch_reminder"])
        self.runtime._play_reality_touch_text = AsyncMock(return_value=True)
        self.runtime._record_reality_touch_delivery = AsyncMock(return_value=True)
        context = NS(context=NS(event=event))
        for _ in range(2):
            await req.func_tool.tools[0].call(context, text="喝口水吧。")
        response = LLMResponse(role="assistant", completion_text="不应重复交付")
        owner.response(event, response)
        await self.drain()
        self.assertEqual(response.completion_text, "")
        self.runtime._play_reality_touch_text.assert_awaited_once()
        self.native.send_message.assert_awaited_once()
        self.assertEqual(reminder["status"], "completed")

    async def test_cancelled_reminder_and_other_cron_job_cannot_be_delivered_or_removed(self):
        reminder = await self.schedule()
        self.assertTrue(await self.runtime._cancel_reality_touch_official_reminder("200", reminder_id=reminder["id"]))
        self.native.cron_manager.delete_job.assert_awaited_once_with("job-a")
        self.assertFalse((await self.api.delete_reality_touch_cron_job("unrelated-job"))[0])
        event = Event(reminder["id"])
        req = ProviderRequest(system_prompt="旧 Cron 提示")
        owner = RealityHostEvents(self.runtime, lambda: True)
        owner.request(event, req)
        self.assertEqual(req.func_tool.tools, [])
        delivered, _ = await self.runtime._execute_official_reality_touch_reminder(event, "不能发送")
        self.assertFalse(delivered)
        self.native.send_message.assert_not_awaited()
        response = LLMResponse(role="assistant", completion_text="不能普通发送")
        owner.response(event, response)
        self.assertEqual(response.completion_text, "")

    async def test_disable_pauses_owned_cron_and_stops_alarm_loop_then_resume(self):
        reminder = await self.schedule()
        alarm = self.runtime._alarm_task
        self.assertIsNotNone(alarm)
        await self.runtime.terminate()
        self.assertTrue(alarm.done())
        self.assertTrue(reminder["paused_by_v2"])
        self.native.cron_manager.update_job.assert_awaited_once_with("job-a", enabled=False)
        await self.runtime.initialize()
        self.assertFalse(reminder["paused_by_v2"])
        self.assertEqual(self.native.cron_manager.update_job.await_args.kwargs, {"enabled": True})
        self.assertFalse(self.runtime._alarm_task.done())

    def allow_snapshot(self):
        repository = CapabilityRepository(self.app.runtime)
        require(repository.set_role_enabled(extension_id="reality", role=self.context.role_binding.role,
            bot_id=self.context.scope.bot_id, enabled=True, expected_revision=0, observed_at=support.NOW))
        require(repository.set_authorization(extension_id="reality", capability_id="reality.snapshot.read",
            subject=self.context.actor.subject, scope=self.context.scope,
            state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=support.NOW))
        identities = self.controller.authority.identities
        for capability in ("extension.tool.invoke", "proactive.contact"):
            if not identities.authorize(self.context.actor, scope=self.context.scope,
                    capability=capability, observed_at=support.NOW).is_success:
                identities.issue_grant(grant_id="reality-test:" + capability,
                    subject=self.context.actor.subject, scope=self.context.scope, capability=capability,
                    tier=AuthorizationTier.STANDARD, valid_from=support.NOW, expected_revision=identities.revision)
        self.context = self.api.mobile_target_context("200")[2]

    def mobile_session(self, token="test-paired-native-phone", *, ttl=60, name="test phone"):
        self.runtime.config["mobile"]["enabled"] = True
        self.runtime.config["mobile"]["allowed_user_id"] = "200"
        now = time.time()
        self.runtime._mobile_store_session(token, {"user_id": "200", "device_name": name,
            "created_at": now, "last_seen_at": now, "expires_at": now + ttl})
        return token

    async def mobile_call(self, token, handler, payload=None):
        request = _MobileRequestState(headers={"authorization": "Bearer " + token}, payload=payload or {})
        marker = _mobile_request_state.set(request)
        try:
            return await handler()
        finally:
            _mobile_request_state.reset(marker)

    async def test_reality_page_snapshot_exposes_active_mobile_pairing(self):
        self.mobile_session(name="测试手机")

        snapshot = self.runtime._reality_touch_page_snapshot()

        observation = next(row for row in snapshot["mobile_observations"] if row["user_id"] == "200")
        self.assertEqual(len(observation["connections"]), 1)
        self.assertEqual(observation["connections"][0]["device_name"], "测试手机")
        self.assertTrue(observation["connections"][0]["online"])

    async def upload_mobile_samples(self, token):
        self.runtime.config["mobile"]["activity_enabled"] = True
        self.runtime.health_enabled = True
        self.runtime._schedule_mobile_amap_enrichment = Mock()
        self.runtime._schedule_v2_location_notification = Mock()
        samples = ((self.runtime.mobile_location, {"latitude": 30, "longitude": 120}),
                   (self.runtime.mobile_device_status, {"app_state": "foreground", "battery_percent": 68}),
                   (self.runtime.mobile_device_activity, {"consent": True, "app_label": "读书", "category": "reading"}),
                   (self.runtime.mobile_telemetry, {"measurements": [{"type": "steps", "value": 200, "unit": "steps"}]}),
                   (self.runtime.mobile_screen_heartbeat, {"streaming": False}))
        for handler, payload in samples:
            body, status = await self.mobile_call(token, handler, payload)
            self.assertEqual((status, body["ok"]), (200, True), handler.__name__)

    async def test_observer_pair_and_close_preserve_phone_uploads_and_native_snapshot(self):
        self.allow_snapshot()
        phone = self.mobile_session(ttl=600)
        await self.upload_mobile_samples(phone)
        retention = self.runtime._mobile_device_status["200"]["_rv1"]["retain_until"]
        observer = self.mobile_session("test-observer-paired-native-phone", ttl=60, name="observer")
        before_close = await self.api.companion_v2_mobile_reality_snapshot(observer)
        for kind in ("location", "device", "activity", "telemetry"):
            self.assertTrue(before_close["snapshot"][kind]["available"], kind)
        self.assertTrue((await self.mobile_call(observer, self.runtime.mobile_close_session))[0]["ok"])
        after_close = await self.api.companion_v2_mobile_reality_snapshot(phone)
        self.assertEqual(after_close["snapshot"]["device"]["battery_percent"], 68)
        self.assertTrue(self.runtime._mobile_screen_status("200")["online"])
        self.assertEqual(self.runtime._mobile_device_status["200"]["_rv1"]["retain_until"], retention)
        self.assertTrue(all(after_close["snapshot"][kind]["available"]
                            for kind in ("location", "device", "activity", "telemetry")))
        self.native.send_message.assert_not_awaited()

    async def test_upload_retention_uses_its_source_session_and_survives_observer_expiry(self):
        phone = self.mobile_session(ttl=600)
        observer = self.mobile_session("test-expiring-observer", ttl=5)
        await self.upload_mobile_samples(phone)
        phone_session = self.runtime._mobile_sessions[self.runtime._mobile_token_key(phone)]
        stamp = self.runtime._mobile_device_status["200"]["_rv1"]
        self.assertEqual(stamp["retain_until"], phone_session["expires_at"])
        self.runtime._mobile_sessions[self.runtime._mobile_token_key(observer)]["expires_at"] = time.time() - 1
        self.runtime._mobile_cleanup_sessions()
        snapshot = self.runtime.mobile_context("200")
        self.assertTrue(all(snapshot[kind]["available"] for kind in ("location", "device", "activity", "telemetry")))
        self.assertTrue(self.runtime._mobile_screen_status("200")["online"])
        self.assertEqual(self.runtime._mobile_device_status["200"]["_rv1"]["retain_until"], stamp["retain_until"])

    async def test_closing_source_withdraws_its_samples_and_preserves_other_phone(self):
        phone = self.mobile_session(ttl=600)
        await self.upload_mobile_samples(phone)
        second = self.mobile_session("test-second-phone", ttl=600)
        await self.mobile_call(second, self.runtime.mobile_device_activity,
                               {"consent": True, "app_label": "第二台手机", "category": "reading"})
        await self.mobile_call(second, self.runtime.mobile_telemetry,
                               {"measurements": [{"type": "heart_rate", "value": 70, "unit": "bpm"}]})
        await self.mobile_call(phone, self.runtime.mobile_close_session)
        snapshot = self.runtime.mobile_context("200")
        self.assertFalse(snapshot["location"]["available"])
        self.assertFalse(snapshot["device"]["available"])
        self.assertFalse(snapshot["telemetry"]["available"])
        self.assertFalse(self.runtime._mobile_screen_status("200")["online"])
        self.assertEqual(snapshot["activity"]["app_label"], "第二台手机")

    async def test_new_private_context_cannot_inherit_old_phone_uploads(self):
        phone = self.mobile_session(ttl=600)
        await self.upload_mobile_samples(phone)
        host = self.runtime.v2_host_api()
        identity = host.reality_mobile_context("200")
        with patch.object(host, "reality_mobile_context", return_value=dict(identity, context_key="new-private-context")):
            self.mobile_session("test-new-context", ttl=600)
            self.assertFalse(self.runtime.mobile_context("200")["device"]["available"])
            self.assertFalse(self.runtime._mobile_screen_status("200")["online"])
            self.assertEqual((await self.mobile_call(phone, self.runtime.mobile_device_status,
                             {"app_state": "foreground"}))[1], 401)

    async def test_report_dispatch_records_receipt_and_rejection_without_payload_or_token(self):
        phone = self.mobile_session(ttl=600)
        request = NS(can_read_body=True, content_length=100, transport=None, remote="127.0.0.1",
                     headers={"Authorization": "Bearer " + phone},
                     json=AsyncMock(return_value={"app_state": "foreground", "battery_percent": 68}))
        with patch("astrbot_host.native_extensions.reality.mobile_gateway.logger") as logger:
            response = await self.runtime._mobile_aiohttp_dispatch(request, self.runtime.mobile_device_status)
            self.assertEqual(response.status, 200)
            logger.info.assert_called_once()
            request.json.return_value = {"app_state": "invalid"}
            rejected = await self.runtime._mobile_aiohttp_dispatch(request, self.runtime.mobile_device_status)
            self.assertEqual(rejected.status, 400)
            logger.warning.assert_called_once()
            self.assertNotIn(phone, repr(logger.mock_calls))
            self.assertNotIn("battery_percent", repr(logger.mock_calls))
        reports = self.runtime._mobile_connection_status("200")["devices"][0]["reports"]
        self.assertEqual(set(reports), {"device"})
        self.assertEqual(set(reports["device"]), {"accepted_at", "age_seconds"})
        self.assertGreater(reports["device"]["accepted_at"], 0)

    async def test_screen_heartbeat_rechecks_session_after_reading_request(self):
        phone = self.mobile_session(ttl=600)
        async def revoke():
            self.runtime._mobile_sessions.clear()
            return {"streaming": True}
        with patch.object(self.runtime, "_mobile_request_payload", side_effect=revoke):
            body, status = await self.mobile_call(phone, self.runtime.mobile_screen_heartbeat)
        self.assertEqual((status, body["ok"]), (401, False))
        self.assertFalse(self.runtime._mobile_screen_status("200")["online"])

    async def test_mobile_room_status_and_create_explain_the_actual_blocker(self):
        phone = self.mobile_session(ttl=600)
        cases = ((False, False, 'mobile_together_disabled', '尚未启用'),
                 (True, True, 'mobile_together_not_authorized', '建房授权'),
                 (True, False, 'mobile_v2_together_start_failed', '启动失败'))
        for enabled, running, reason, message in cases:
            status = {'matched': True, 'available': enabled, 'enabled': enabled,
                      'running': running, 'ready': False, 'reason': reason,
                      'capabilities': {'create': reason == 'mobile_v2_together_start_failed'}, 'modes': ['call']}
            with patch.object(self.runtime, '_mobile_v2_together_status', new=AsyncMock(return_value=status)):
                body, code = await self.mobile_call(phone, self.runtime.mobile_status)
                self.assertEqual(code, 200)
                self.assertEqual(body['data']['together']['enabled'], enabled)
                self.assertEqual(body['data']['together']['reason'], reason)
                self.assertIn(message, body['data']['together']['blockers'][0])
                rejected, code = await self.mobile_call(phone, self.runtime.mobile_create_room,
                    {'mode': 'call', 'client': 'android_native', 'request_id': 'room-blocked'})
                self.assertEqual(code, 409)
                self.assertEqual(rejected['code'], reason)
                self.assertIn(message, rejected['message'])

    async def test_mobile_gateway_registers_legacy_native_routes(self):
        self.runtime.config['mobile'].update({
            'enabled': True, 'pairing_token': 'synthetic-long-pairing-token', 'proxy_rooms': False})
        runner = NS(setup=AsyncMock(), cleanup=AsyncMock())
        site = NS(start=AsyncMock(), stop=AsyncMock())
        with (patch.object(mobile_gateway.web, 'AppRunner', return_value=runner) as make_runner,
              patch.object(mobile_gateway.web, 'TCPSite', return_value=site)):
            self.assertTrue(await self.runtime._start_mobile_server())
            app = make_runner.call_args.args[0]
            paths = {route.resource.canonical for route in app.router.routes()}
            self.assertIn('/pair', paths)
            self.assertIn('/status', paths)
            self.assertIn('/astrbot_plugin_reality_companion/mobile/pair', paths)
            self.assertIn('/astrbot_plugin_reality_companion/mobile/status', paths)
            await self.runtime._stop_mobile_server()

    async def test_mobile_room_failure_preserves_code_and_unknown_result_without_replay(self):
        phone = self.mobile_session(ttl=600)
        ready = {'matched': True, 'available': True, 'enabled': True,
                 'running': True, 'ready': True, 'capabilities': {'create': True}, 'modes': ['call']}
        host_api = self.runtime.v2_host_api()
        for error_code, uncertain in (('mobile_together_request_conflict', False),
                                      ('mobile_together_result_unknown', True)):
            creator = AsyncMock(side_effect=TaskError(error_code, 'credential: ' + phone))
            with (patch.object(self.runtime, '_mobile_v2_together_status', new=AsyncMock(return_value=ready)),
                  patch.object(self.runtime, 'native_extension', return_value=NS()),
                  patch.object(self.runtime, '_mobile_prepare_together_access', new=AsyncMock(return_value={})),
                  patch.object(host_api, 'companion_v2_mobile_together_create', new=creator),
                  patch('astrbot_host.native_extensions.reality.mobile_gateway.logger') as logger):
                body, status = await self.mobile_call(phone, self.runtime.mobile_create_room,
                    {'mode': 'call', 'client': 'android_native', 'request_id': 'room-original'})
                self.assertEqual(status, 409)
                self.assertEqual(body['code'], error_code)
                self.assertEqual(body.get('uncertain', False), uncertain)
                self.assertEqual(creator.await_count, 1)
                self.assertNotIn(phone, repr(body))
                self.assertNotIn(phone, repr(logger.mock_calls))

    async def prepare_native_mobile_rooms(self):
        self.allow_snapshot()
        together = self.registry.service('together')
        together.config['enabled'] = True
        together.server_enabled = True
        together.public_base_url = 'https://room.example.invalid'
        patches = (
            patch.object(together, '_start_room_server', new=AsyncMock()),
            patch.object(type(together.room_server), 'running', new_callable=PropertyMock, return_value=True),
            patch.object(self.runtime, '_mobile_prepare_together_access',
                         new=AsyncMock(return_value={'url': 'https://room.example.invalid'})),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        repository = CapabilityRepository(self.app.runtime)
        package = self.registry._lease('together').manifest.package
        require(repository.acquire(extension_id='together', package_id=package.package_id,
            package_version=package.package_version, expected_revision=0, observed_at=support.NOW))
        require(repository.set_role_enabled(extension_id='together', role=self.context.role_binding.role,
            bot_id=self.context.scope.bot_id, enabled=True, expected_revision=0, observed_at=support.NOW))
        require(repository.set_authorization(extension_id='together', capability_id='together.room.create',
            subject=self.context.actor.subject, scope=self.context.scope,
            state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=support.NOW))
        await self.registry.reconcile_service('together')
        await self.registry._lease('together').start()
        return together

    async def test_android_no_id_concurrent_requests_create_one_native_room_and_reconnect_reuses_it(self):
        together = await self.prepare_native_mobile_rooms()
        phone = self.mobile_session('test-paired-together-phone-123', ttl=600)
        payload = {'mode': 'call', 'client': 'android_native'}
        with patch.object(together, 'issue_room_ticket', wraps=together.issue_room_ticket) as issue:
            responses = await asyncio.gather(*(
                self.mobile_call(phone, self.runtime.mobile_create_room, payload) for _ in range(3)))
            for body, status in responses:
                self.assertEqual((status, body['ok']), (200, True), body)
                self.assertEqual(body['data']['execution_path'], 'v2_native')
                self.assertTrue(body['data']['request_id'].startswith('mobile-room-'))
            self.assertEqual(len({body['data']['operation_id'] for body, _ in responses}), 1)
            await self.mobile_call(phone, self.runtime.mobile_close_session)
            repaired = self.mobile_session('test-repaired-together-phone-123', ttl=600)
            body, status = await self.mobile_call(repaired, self.runtime.mobile_create_room, payload)
            self.assertEqual(status, 200, body)
            self.assertEqual(body['data']['url'], responses[0][0]['data']['url'])
            self.assertEqual(body['data']['request_id'], responses[0][0]['data']['request_id'])
            issue.assert_called_once()
        self.native.send_message.assert_not_awaited()

    async def test_android_no_id_native_unknown_outcome_is_not_executed_again(self):
        together = await self.prepare_native_mobile_rooms()
        phone = self.mobile_session('test-paired-unknown-phone-123', ttl=600)
        payload = {'mode': 'call', 'client': 'android_native'}
        with patch.object(together, 'issue_room_ticket', side_effect=TimeoutError('unconfirmed')) as issue:
            first, status = await self.mobile_call(phone, self.runtime.mobile_create_room, payload)
            self.assertEqual((status, first['code'], first['uncertain']),
                             (409, 'mobile_together_result_unknown', True))
            repaired = self.mobile_session('test-repaired-unknown-phone-123', ttl=600)
            repeated, status = await self.mobile_call(repaired, self.runtime.mobile_create_room, payload)
            self.assertEqual((status, repeated['uncertain']), (409, True))
            self.assertEqual(repeated['data']['request_id'], first['data']['request_id'])
            self.assertEqual(repeated['data']['operation_id'], first['data']['operation_id'])
            self.assertIn('再点一次', repeated['message'])
            issue.assert_called_once()
        self.assertNotIn(phone, repr(repeated))

    async def test_android_no_id_can_create_again_when_confirmed_room_ticket_is_lost(self):
        together = await self.prepare_native_mobile_rooms()
        phone = self.mobile_session('test-paired-lost-room-phone-123', ttl=600)
        payload = {'mode': 'call', 'client': 'android_native'}
        with patch.object(together, 'issue_room_ticket', wraps=together.issue_room_ticket) as issue:
            first, status = await self.mobile_call(phone, self.runtime.mobile_create_room, payload)
            self.assertEqual(status, 200, first)
            # The host owns in-memory tickets; reload or a confirmed close
            # removes them even while a persisted success receipt remains.
            for token in tuple(together.ticket_store._tickets):
                together.ticket_store.revoke(token)
            second, status = await self.mobile_call(phone, self.runtime.mobile_create_room, payload)
            self.assertEqual(status, 200, second)
            self.assertNotEqual(second['data']['request_id'], first['data']['request_id'])
            self.assertNotEqual(second['data']['operation_id'], first['data']['operation_id'])
            self.assertEqual(issue.call_count, 2)

    async def test_paired_mobile_snapshot_executes_native_provider_without_model_or_delivery(self):
        self.allow_snapshot()
        token = self.mobile_session()
        snapshot = {"available": False, "user_id": "200", "location": {"available": False},
                    "device": {"available": True, "battery_percent": 68}}
        self.runtime.mobile_context = Mock(return_value=snapshot)
        before = self.counts()
        result = await self.api.companion_v2_mobile_reality_snapshot(token)
        self.assertEqual((result["state"], result["effect"]), ("succeeded", "none"))
        self.assertEqual(result["snapshot"], snapshot)
        self.assertTrue(result["receipt_ref"].startswith("reality.native.operation:"))
        self.runtime.mobile_context.assert_called_once_with("200")
        self.native.send_message.assert_not_awaited()
        self.assertEqual(self.counts(), before)

    async def test_paired_mobile_snapshot_requires_separate_native_consent(self):
        token = self.mobile_session()
        self.runtime.mobile_context = Mock()
        with self.assertRaises(TaskError) as error:
            await self.api.companion_v2_mobile_reality_snapshot(token)
        self.assertEqual(error.exception.code, "mobile_reality_not_authorized")
        self.runtime.mobile_context.assert_not_called()

    async def test_paired_mobile_snapshot_rejects_session_revoked_during_read(self):
        self.allow_snapshot()
        token = self.mobile_session()
        def revoke(user_id):
            self.runtime._mobile_sessions.clear()
            return {"available": False, "user_id": user_id}
        self.runtime.mobile_context = Mock(side_effect=revoke)
        with self.assertRaises(TaskError):
            await self.api.companion_v2_mobile_reality_snapshot(token)
        self.native.send_message.assert_not_awaited()

    def test_supported_screen_upload_does_not_imply_an_online_phone(self):
        self.runtime.config["mobile"]["screen_upload_enabled"] = True
        missing = self.runtime._mobile_screen_status("200")
        self.assertTrue(missing["available"])
        self.assertFalse(missing["online"])
        self.assertIsNone(missing["last_report_at"])
        self.runtime._mobile_screen_clients["200"] = {"last_seen_at": time.time(), "streaming": False}
        self.assertTrue(self.runtime._mobile_screen_status("200")["online"])
        self.runtime._mobile_screen_clients["200"]["last_seen_at"] -= 100
        stale = self.runtime._mobile_screen_status("200")
        self.assertTrue(stale["available"])
        self.assertFalse(stale["online"])

    async def test_shared_host_exposes_native_settings_and_unregisters_them(self):
        registered = self.native.registered_web_apis
        self.controller.web_api.register()
        expected = {"/astrbot_plugin_private_companion/extensions" + suffix
                    for suffix in ("", "/configure", "/action")}
        self.assertTrue(expected <= {entry[0] for entry in registered})
        await self.controller.terminate()
        self.assertFalse(expected & {entry[0] for entry in registered})

    async def test_location_notification_uses_per_user_snapshot_consent_and_contact_policy(self):
        mobile = {"available": True, "location": {"available": True, "captured_at": 123,
            "latitude": 30, "longitude": 120, "place": {"matched": True, "name": "家"}}}
        self.runtime.mobile_context = Mock(return_value=mobile)
        self.assertEqual((await self.api.notify_mobile_location_update("200"))["reason"], "reality_location_not_authorized")
        self.allow_snapshot()
        self.assertIn("reality.snapshot.read", {tool.action for tool in self.app.external.catalog_for(self.context)})
        allowed = False
        preferences = NS(reason=lambda _purpose: None if allowed else "proactive_disabled")
        self.app.proactive.contact = AsyncMock(return_value=Outcome.success(True))
        with patch.object(self.app.proactive.preferences, "read", return_value=Outcome.success(preferences)):
            self.assertEqual((await self.api.notify_mobile_location_update("200"))["reason"], "proactive_disabled")
            allowed = True
            self.assertTrue((await self.api.notify_mobile_location_update("200"))["accepted"])
            self.assertEqual((await self.api.notify_mobile_location_update("200"))["reason"], "reality_location_unchanged")
        self.app.proactive.contact.assert_awaited_once()
        context, opportunity, intents = self.app.proactive.contact.await_args.args
        self.assertEqual(context, self.context)
        self.assertEqual(intents[0].purpose, "environment")
        source = require(self.app.proactive.sources.read(intents[0].thought.evidence.sources[0],
            resolved=context.actor, capability="text", observed_at=support.NOW,
            authorizer=self.app.tasks.identity))
        payload = json.loads(source.payload)
        self.assertEqual(payload["place"], "家")
        self.assertIn("不是用户行为", payload["meaning"])

    async def test_location_diagnostic_uses_area_label_without_consuming_production_dedup(self):
        self.allow_snapshot()
        mobile = {"available": True, "location": {"available": True, "captured_at": 123,
            "latitude": 30, "longitude": 120, "label": "", "place": {"matched": False,
                "name": "", "area_label": "西湖区"}}}
        self.runtime.mobile_context = Mock(return_value=mobile)
        diagnostic = {"_diagnostic": {"route_id": "reality_location_event", "purpose": "environment",
            "batch_id": "confirmed-batch", "event_key": "confirmed-event", "event_hash": "confirmed-hash",
            "role_hash": "confirmed-role"}}
        key = "reality.location.last:" + self.context.key
        before = self.app.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))

        candidate = await self.api.mobile_location_diagnostic_candidate(
            "200", test_id="location-test-1", diagnostic_origin=diagnostic)

        self.assertTrue(candidate["accepted"], candidate)
        self.assertEqual(candidate["route"], {"id": "reality_location_event",
            "label": "手机陪伴终端地点来源", "variant": "mobile_location"})
        self.assertEqual(candidate["stages"], [{"stage": "mobile_location_fetch", "state": "passed"}])
        self.assertEqual(candidate["diagnostic_origin"]["reality_location_event"]["test_id"],
                         "location-test-1")
        source = require(self.app.proactive.sources.read(candidate["intents"][0].thought.evidence.sources[0],
            resolved=self.context.actor, capability="text", observed_at=support.NOW,
            authorizer=self.app.tasks.identity))
        self.assertEqual(json.loads(source.payload)["place"], "西湖区")
        after = self.app.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
        self.assertEqual(after, before, "a test must not consume production location-change deduplication")

    async def test_audio_consent_revoked_during_tts_cannot_play(self):
        async def revoke(context, text, **kwargs):
            self.user["reality_touch_consent"]["confirmed"] = False
            return {"type": "record", "data": {"file": "base64://UklGRg=="}}
        self.runtime._open_reality_touch_audio_file = Mock()
        with patch("astrbot_host.voice.NativeVoiceReply.render", side_effect=revoke):
            self.assertFalse(await self.runtime._play_reality_touch_text("起床啦", user_id="200"))
        self.runtime._open_reality_touch_audio_file.assert_not_called()
