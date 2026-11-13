"""Original WeChat events and public transports, with synthetic data only.

Set WECHAT_NATIVE_SOURCE to a current source checkout. Integration tests run
against that adapter; no Native service, model endpoint or real account is used.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
import wave

from tests import astrbot_data_root  # noqa: F401
from companion_v2.adapters.astrbot.native import event_account_id, native_user_subject_id, supported_platform_name
from astrbot_host.binding import event_identity

SOURCE = os.environ.get("WECHAT_NATIVE_SOURCE")


class PlatformProjectionTests(unittest.TestCase):
    def test_follows_existing_weixin_account_mapping_without_relabeling_it(self):
        for name in ("wechat_native", "weixin_oc"):
            event = NS(platform_meta=NS(id="wx-main", name=name),
                       message_obj=NS(self_id="bot-wx", sender=NS(user_id="real-user")))
            self.assertTrue(supported_platform_name(name))
            self.assertEqual(event_account_id(event), "wx-main")
            self.assertEqual(event_identity(event), {"platform_id": "wx-main", "account_id": "wx-main",
                                                    "participant_id": "real-user"})
        self.assertEqual(native_user_subject_id("weixin_oc", "real-user"), "weixin-user:real-user")
        self.assertEqual(native_user_subject_id("wechat_native", "real-user"), "wechat-native-user:real-user")


@unittest.skipUnless(SOURCE and Path(SOURCE, "adapter.py").is_file(), "set WECHAT_NATIVE_SOURCE to the current adapter checkout")
class WeChatNativeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        package = ModuleType("companion_wechat_test_adapter")
        package.__path__ = [SOURCE]
        sys.modules[package.__name__] = package
        cls.adapter_module = importlib.import_module(package.__name__ + ".adapter")
        cls.event_module = importlib.import_module(package.__name__ + ".event")
        cls.Adapter = cls.adapter_module.WeChatNativeAdapter
        cls.Event = cls.event_module.WeChatNativeEvent

    async def asyncSetUp(self):
        from aiohttp import web
        from PIL import Image
        self.tmp = TemporaryDirectory(prefix="companion-wechat-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.adapters = []
        self.controllers = []
        self.submissions, self.uploads, self.messages = [], {}, {}
        self.metric = patch("astrbot.core.utils.metrics.Metric.upload", new=AsyncMock())
        self.metric.start()
        self.addCleanup(self.metric.stop)

        async def handler(request):
            if request.method == "GET" and "/messages/" in request.path:
                key = request.path.rsplit("/", 1)[-1]
                return web.json_response(self.messages[key])
            if request.method == "GET" and "/groups/" in request.path and "/members/" in request.path:
                username = request.path.rsplit("/", 1)[-1]
                return web.json_response({"groupId": "room@chatroom", "username": username,
                    "state": "present", "member": {"username": username, "displayName": username}})
            if request.path.endswith("/uploads"):
                reader = await request.multipart()
                part = await reader.next()
                data = bytes(await part.read())
                key = f"upload-{len(self.uploads)}"
                self.uploads[key] = data
                return web.json_response({"id": key, "byteLength": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest()})
            if request.path.endswith("/sends") and request.method == "POST":
                body = await request.json()
                self.submissions.append(body)
                if "kind" in body:
                    result = {"messageIds": ["synthetic-outbound"]}
                    if body["kind"] == "voice":
                        result["chunks"] = [{"index": 0, "state": "accepted", "durationMs": 50,
                                             "serverMessageId": "synthetic-server-message"}]
                    return web.json_response({"id": f"job-{len(self.submissions)}", "state": "succeeded",
                        "accountId": "a", "threadId": body["threadId"], "kind": "send",
                        "sourceId": body["sourceId"], "serverMessageId": "synthetic-server-message", "result": result})
                return web.json_response({"id": f"send-{len(self.submissions)}", "state": "accepted",
                                          "accountId": "a", "destination": body["threadId"]})
            return web.json_response({"message": "synthetic unsupported endpoint"}, status=404)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.server = web.AppRunner(app)
        await self.server.setup()
        site = web.TCPSite(self.server, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.addAsyncCleanup(self.close_all)
        png = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(png, format="PNG")
        self.png = png.getvalue()
        wav = io.BytesIO()
        with wave.open(wav, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"\0\0" * 800)
        self.wav = wav.getvalue()

    async def close_all(self):
        for controller in reversed(self.controllers):
            await controller.terminate()
        for adapter in reversed(self.adapters):
            await adapter.terminate()
        await self.server.cleanup()

    async def adapter(self, *, unique=True, identifier="wx-main", account="a", **options):
        adapter = self.Adapter({"id": identifier, "server_url": f"http://127.0.0.1:{self.port}",
            "account_id": account, "token": "synthetic", "unique_session": unique, **options}, {},
            asyncio.Queue(), state_dir=self.root / f"adapter-{len(self.adapters)}")
        await adapter.client.open()
        adapter.bot_self_id = "synthetic-bot"
        self.adapters.append(adapter)
        return adapter

    def event(self, adapter, *, thread="peer/x", sender="real-user", identifier=None,
              text="synthetic question", kind=1, **media):
        from tests.test_management_access import NOW
        identifier = identifier or f"message-{len(self.messages)}"
        raw = {"id": identifier, "accountId": adapter.native_config.account_id, "threadId": thread,
               "senderId": sender, "direction": "incoming", "type": kind, "body": text,
               "createdAt": datetime.now(timezone.utc).isoformat(), "serverId": str(100 + len(self.messages)),
               "version": "1", "media": [], "mentions": {"state": "known", "self": thread.endswith("@chatroom"),
               "all": False, "userIds": []}}
        self.messages[identifier] = raw
        adapter.state.accept(str(len(self.messages)), identifier, thread=thread, server_id=raw["serverId"], status="queued")
        event = self.Event(self.event_module.convert_message(raw, adapter.native_config, adapter.bot_self_id, **media), adapter)
        event.created_at = NOW.timestamp()
        return event

    async def controller(self, adapter, *, observe=None, root=None, extra_adapters=(),
                         role_id="role-w", voice_config=None):
        from astrbot.core.message.components import Image, Record, Reply
        from astrbot_host.audio.loader import load_native_audio
        from astrbot_host.media import load_native_image
        from astrbot_host.media_components import group_components, private_components
        from astrbot_host.multi_controller import AstrBotMultiController
        from astrbot_host.quotes import load_native_group_quote, load_native_quote
        from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
        from companion_v2.foundation.clock import DeterministicClock
        from tests.multi_host_support import MultiNativeContext, multi_config
        from tests.native_adapter_support import FakeProvider, FakeReverseApi
        from tests.test_management_access import NOW
        native = MultiNativeContext()
        native.platforms[adapter.meta().id] = adapter
        native.selections[adapter.meta().id] = "original"
        native.conversations[adapter.meta().id] = "synthetic-wx-conversation"
        async def override(_umo):
            return "original"
        types = NativeAdapterTypes("4.29.4", self.Event, FakeProvider, NS, NS, FakeReverseApi,
            image=Image, image_loader=load_native_image, audio=Record, audio_loader=load_native_audio,
            reply=Reply, quote_loader=load_native_quote, group_quote_loader=load_native_group_quote,
            component_reader=private_components, group_component_reader=group_components,
            session_persona_override_lookup=override)
        config = multi_config()
        if voice_config is not None:
            config["voice_reply"] = voice_config
        config["multi_host"]["targets"].append({"target_id": "wx", "platform_id": adapter.meta().id,
            "account_id": adapter.meta().id, "bot_id": "bot-w", "participant_id": "real-user"})
        config["multi_host"]["personas"].append({"bot_id": "bot-w", "persona_id": "original", "role_id": role_id})
        config["multi_host"]["group_targets"] = [{"target_id": "wx-group", "platform_id": adapter.meta().id,
            "account_id": adapter.meta().id, "bot_id": "bot-w", "group_id": "room@chatroom"}]
        for extra in extra_adapters:
            identifier = extra.meta().id
            native.platforms[identifier] = extra
            native.selections[identifier] = "original"
            native.conversations[identifier] = "synthetic-" + identifier
            config["multi_host"]["targets"].append({"target_id": identifier, "platform_id": identifier,
                "account_id": identifier, "bot_id": "bot-w", "participant_id": "real-user"})
        controller = AstrBotMultiController(native, config, root or self.root / "companion-data",
            native_types=types, now=DeterministicClock.at(NOW).now)
        self.controllers.append(controller)
        if observe is not None:
            await controller.session_topology.observe(observe)
        result = await controller.initialize_profile()
        self.assertTrue(result.is_success, result.failure)
        result = await controller.start()
        self.assertTrue(result.is_success, result.failure)
        return controller

    async def test_startup_without_native_session_does_not_fabricate_a_route(self):
        adapter = await self.adapter()
        controller = await self.controller(adapter)
        from companion_v2.execution.models import TaskError
        with self.assertRaises(TaskError) as failure:
            await controller.session.synchronize(controller.settings.target("wx"))
        self.assertEqual(failure.exception.code, "host_native_session_unavailable")
        self.assertEqual(self.submissions, [])

    async def test_private_capture_keeps_person_and_native_session_separate(self):
        adapter = await self.adapter()
        event = self.event(adapter)
        controller = await self.controller(adapter, observe=event)
        self.assertEqual(controller.target_for_event(event).participant_id, "real-user")
        result = await controller.session.bridge.capture_core_reply(event)
        self.assertTrue(result.is_success, result.failure)
        captured = result.unwrap()
        self.assertEqual(captured.principal.native_account_id, "real-user")
        self.assertEqual(captured.route.scope.native_scope_id, "real-user")
        self.assertEqual(captured.route.scope.session_id, event.session.session_id)
        self.assertEqual(event.message_obj.raw_message["threadId"], "peer/x")
        self.assertNotEqual(event.session.session_id, "real-user")
        self.assertEqual(event.adapter, adapter)
        self.assertEqual(self.submissions, [])

    async def test_private_route_survives_restart_in_existing_metadata(self):
        adapter = await self.adapter()
        event = self.event(adapter)
        first = await self.controller(adapter, observe=event)
        capture = await first.session.bridge.capture_core_reply(event)
        self.assertTrue(capture.is_success, capture.failure)
        context, _ = await first.session.synchronize(first.settings.target("wx"))
        await first.terminate()
        second = await self.controller(adapter)
        resumed, _ = await second.session.synchronize(second.settings.target("wx"))
        self.assertEqual(resumed.scope, context.scope)
        self.assertEqual(resumed.scope.session_id, event.session.session_id)
        self.assertEqual(second.session_topology.route(event.unified_msg_origin), ("wx-main", "private", "real-user"))

    async def test_shared_and_member_group_sessions_keep_real_group_and_speaker(self):
        for unique in (False, True):
            with self.subTest(unique=unique):
                adapter = await self.adapter(unique=unique)
                event = self.event(adapter, thread="room@chatroom")
                controller = await self.controller(adapter, observe=event, root=self.root / f"group-{unique}")
                route = await controller.session.bridge.capture_text_only(event)
                self.assertTrue(route.is_success, route.failure)
                captured = route.unwrap()
                self.assertEqual(captured.route.scope.native_scope_id, "room@chatroom")
                self.assertEqual(captured.route.scope.session_id, event.session.session_id)
                self.assertEqual(captured.principal.native_account_id, "real-user")
                self.assertIn(":v2-role:", captured.route.scope.conversation_id)
                another = self.event(adapter, thread="room@chatroom", sender="another-user")
                second = (await controller.session.bridge.capture_text_only(another)).unwrap()
                self.assertEqual(second.route.scope.session_id == captured.route.scope.session_id, not unique)
                self.assertNotEqual(second.principal.native_account_id, captured.principal.native_account_id)
                self.assertIsNone(controller.target_for_event(self.event(adapter, thread="unconfigured@chatroom")))
                self.assertEqual(controller.target_for_event(event).target_id, "wx-group")
                self.assertEqual(controller.session_topology.route(event.unified_msg_origin), ("wx-main", "group", "room@chatroom"))

    async def test_changed_adapter_account_sender_or_session_is_not_admitted(self):
        adapter = await self.adapter()
        event = self.event(adapter)
        controller = await self.controller(adapter, observe=event)
        for key, value in (("accountId", "other"), ("senderId", "other-user"), ("threadId", "other-chat")):
            original = event.message_obj.raw_message[key]
            event.message_obj.raw_message[key] = value
            self.assertIsNone(controller.target_for_event(event), key)
            event.message_obj.raw_message[key] = original
        event.adapter = await self.adapter(identifier="other")
        self.assertIsNone(controller.target_for_event(event))
        self.assertEqual(self.submissions, [])

    async def test_long_encoded_private_session_is_not_truncated_or_used_as_user(self):
        adapter = await self.adapter()
        event = self.event(adapter, thread="微信会话/" * 12)
        self.assertGreater(len(event.session.session_id), 128)
        controller = await self.controller(adapter, observe=event)
        result = await controller.session.bridge.capture_core_reply(event)
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(result.unwrap().route.scope.session_id, event.session.session_id)
        self.assertEqual(result.unwrap().principal.native_account_id, "real-user")

    async def test_private_and_group_original_media_use_existing_loaders(self):
        adapter = await self.adapter()
        initial = self.event(adapter)
        controller = await self.controller(adapter, observe=initial)
        for thread in ("peer/x", "room@chatroom"):
            for kind, attribute, kwargs, data in ((3, "images", {"image_bytes": self.png}, self.png),
                                                (34, "audios", {"voice_bytes": self.wav}, self.wav)):
                with self.subTest(thread=thread, kind=kind):
                    event = self.event(adapter, thread=thread, kind=kind, text="", **kwargs)
                    captured = await controller.session.bridge.capture(event)
                    self.assertTrue(captured.is_success, captured.failure)
                    self.assertEqual(getattr(captured.unwrap(), attribute)[0].content, data)

    async def test_background_sender_uses_public_session_entry_for_text_images_and_voice(self):
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain, Image, Record
        from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
        adapter = await self.adapter()
        path = self.root / "synthetic.wav"
        path.write_bytes(self.wav)
        sender = AstrBotPlatformSender(NS(), binding=NS(), version="4.29.4", reverse_api_type=None, authorize=lambda: None)
        from companion_v2.foundation.refs import ScopeKind
        for thread in ("peer/x", "room@chatroom"):
            group = thread.endswith("@chatroom")
            native_session = self.event_module.session_id("a", thread, "real-user" if group else None)
            scope = NS(kind=ScopeKind.GROUP if group else ScopeKind.PRIVATE, platform="wx-main",
                       native_scope_id=thread if group else "real-user", session_id=native_session)
            session = sender._session(scope, platform_name="wechat_native")
            self.assertEqual(session.session_id, native_session)
            with patch.object(adapter, "send_by_session", wraps=adapter.send_by_session) as public:
                await adapter.send_by_session(session, MessageChain([Plain("synthetic reply"), Image.fromBytes(self.png), Record(file=str(path))]))
            public.assert_awaited_once()
            self.assertEqual([row["threadId"] for row in self.submissions[-3:]], [thread] * 3)
            self.assertEqual(self.uploads[self.submissions[-2]["sourceId"]], self.png)
            self.assertEqual(self.uploads[self.submissions[-1]["sourceId"]], self.wav)

    async def test_original_event_send_retains_recall_and_loop_guards(self):
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain
        adapter = await self.adapter()
        recalled = self.event(adapter)
        adapter.state.mark_message(recalled.message_obj.message_id, "recalled")
        await recalled.send(MessageChain([Plain("must not send")]))
        self.assertEqual(self.submissions, [])
        event = self.event(adapter, identifier="guarded-message")
        with patch.object(adapter.loop_guard, "blocked", return_value=True):
            await event.send(MessageChain([Plain("must not send")]))
        self.assertEqual(self.submissions, [])

    async def test_first_event_resolves_route_before_optional_observation(self):
        adapter = await self.adapter()
        controller = await self.controller(adapter)
        event = self.event(adapter)
        captured = await controller.session.bridge.capture_core_reply(event)
        self.assertTrue(captured.is_success, captured.failure)
        context, _ = await controller.session.synchronize(controller.settings.target("wx"))
        self.assertEqual(context.scope.session_id, event.session.session_id)
        self.assertEqual(self.submissions, [])

    async def test_restart_can_recover_scope_without_directory_observation(self):
        adapter = await self.adapter()
        first = await self.controller(adapter)
        event = self.event(adapter)
        self.assertTrue((await first.session.bridge.capture_core_reply(event)).is_success)
        context, _ = await first.session.synchronize(first.settings.target("wx"))
        await first.terminate()
        second = await self.controller(adapter)
        resumed, _ = await second.session.synchronize(second.settings.target("wx"))
        self.assertEqual(resumed.scope, context.scope)

    async def test_account_replacement_invalidates_saved_session(self):
        adapter = await self.adapter()
        event = self.event(adapter)
        first = await self.controller(adapter, observe=event)
        self.assertTrue((await first.session.bridge.capture_core_reply(event)).is_success)
        await first.terminate()
        replacement = await self.adapter(account="different-account")
        second = await self.controller(replacement)
        self.assertIsNone(second.session_topology.private_session_id("wx-main", "wx-main", "real-user"))
        self.assertIsNone(second.target_for_event(event))

    async def test_opaque_quote_and_mention_stay_on_original_event(self):
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain, At, Reply
        from astrbot_host.core_reply_outbound import prepare_core_reply_outbound_message
        from tests.test_core_reply_outbound import prepared
        adapter = await self.adapter()
        event = self.event(adapter, thread="room@chatroom", identifier="opaque-message")
        captured = prepared(event_id=event.message_obj.message_id, speaker="real-user",
            settings={"reply_quote_enabled": True, "reply_quote_group_enabled": True,
                      "reply_mention_speaker_enabled": True, "reply_quote_first_part_only": True})
        message = MessageChain([Plain("synthetic reply")])
        decorated = prepare_core_reply_outbound_message(event, captured, message, segment_index=0)
        self.assertEqual([part.id for part in decorated.chain if isinstance(part, Reply)], ["opaque-message"])
        self.assertEqual([part.qq for part in decorated.chain if isinstance(part, At)], ["real-user"])
        later = prepare_core_reply_outbound_message(event, captured, message, segment_index=1)
        self.assertFalse(any(isinstance(part, (Reply, At)) for part in later.chain))
        captured["outbound_settings"]["reply_quote_skip_chars"] = 100
        short = prepare_core_reply_outbound_message(event, captured, message, segment_index=0)
        self.assertFalse(any(isinstance(part, Reply) for part in short.chain))
        captured["outbound_settings"]["reply_quote_skip_chars"] = 0
        await event.send(decorated)
        self.assertEqual(self.submissions[-1]["threadId"], "room@chatroom")
        self.assertIn("real-user", self.submissions[-1]["mentions"]["userIds"])

    async def test_complete_sender_checks_policy_and_does_not_retry_unknown(self):
        from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
        from companion_v2.delivery.models import DeliveryEnvelope
        from companion_v2.execution.models import ProviderBinding, digest
        adapter = await self.adapter()
        event = self.event(adapter)
        controller = await self.controller(adapter, observe=event)
        context, _ = await controller.session.synchronize(controller.settings.target("wx"))
        binding = ProviderBinding("message.delivery", "synthetic-sender", "1", digest("synthetic"))
        sender = AstrBotPlatformSender(controller.session.bridge, binding=binding, version="4.29.4",
            reverse_api_type=object, authorize=lambda: None)
        def envelope(guard=lambda: True):
            return DeliveryEnvelope("synthetic-part", "synthetic-operation", context, 1, "synthetic proactive",
                digest("synthetic proactive"), binding, before_dispatch=guard)
        with patch.object(adapter, "send_by_session", wraps=adapter.send_by_session) as public:
            receipt = await sender.send(envelope())
        self.assertEqual(receipt.status, "delivered", receipt)
        self.assertTrue(receipt.platform_ref.startswith("astrbot-session:"))  # local reference, not phone ack
        public.assert_awaited_once()
        self.assertEqual(public.await_args.args[0].session_id, event.session.session_id)
        self.assertEqual(self.submissions[-1]["threadId"], "peer/x")
        count = len(self.submissions)
        def denied_by_policy():
            from companion_v2.execution.models import TaskError
            raise TaskError("synthetic_policy_denial", "synthetic do-not-disturb")
        denied = await sender.send(envelope(denied_by_policy))
        self.assertEqual(denied.status, "not_sent")
        self.assertEqual(len(self.submissions), count)
        with patch.object(adapter, "send_by_session", new=AsyncMock(side_effect=TimeoutError)) as public:
            unknown = await sender.send(envelope())
        self.assertEqual(unknown.status, "unknown")
        public.assert_awaited_once()
        self.assertEqual(len(self.submissions), count)
        voice_file = self.root / "synthetic-tts.wav"
        voice_file.write_bytes(self.wav)
        tts = AsyncMock(return_value={"type": "record", "data": {"file": str(voice_file)}})
        voiced = AstrBotPlatformSender(controller.session.bridge, binding=binding, version="4.29.4",
            reverse_api_type=object, authorize=lambda: None, voice=tts)
        receipt = await voiced.send(envelope())
        self.assertEqual(receipt.status, "delivered", receipt)
        tts.assert_awaited_once_with(context, "synthetic proactive")
        self.assertEqual(self.uploads[self.submissions[-1]["sourceId"]], self.wav)

    async def test_pdf_hook_and_native_tools_keep_the_original_event(self):
        main = importlib.import_module("companion_wechat_test_adapter.main")
        plugin = main.WeChatNativePlugin.__new__(main.WeChatNativePlugin)
        event = self.event(await self.adapter())
        event.set_extra("wechat_native_pdf", {"name": "synthetic.pdf", "text": "synthetic PDF body"})
        request = NS(extra_user_content_parts=[])
        await plugin.attach_pdf_text(event, request)
        await plugin.attach_pdf_text(event, request)
        self.assertEqual(len(request.extra_user_content_parts), 1)
        self.assertIn("synthetic PDF body", request.extra_user_content_parts[0].text)
        with patch.object(main, "run_read", new=AsyncMock(return_value="synthetic result")) as read:
            result = await plugin.wechat_get_group_info(event)
        self.assertEqual(result, "synthetic result")
        self.assertIs(read.await_args.args[0], event)
        self.assertIs(event.adapter, self.adapters[0])

    async def test_core_hooks_keep_native_generation_and_original_guarded_sender(self):
        from tests.test_main_no_reply import ENTRY
        from companion_v2.foundation.outcome import Outcome
        from unittest.mock import Mock
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain
        adapter = await self.adapter()
        event = self.event(adapter)
        original = event.send.__func__
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(key="synthetic-target", target_id="synthetic-target")
        prepared = {"context": object(), "instruction": "companion", "prompt_instruction": "companion context",
            "stable_prompt_instruction": "companion voice", "target_id": target.target_id,
            "channel": "private", "event_id": event.message_obj.message_id}
        staging = NS(runtime=None, now=lambda: None, claims=lambda _: True, target_for_event=lambda _: target,
            settings=NS(enabled=True), session=NS(profile=NS(target=target),
                core_reply_observations=NS(phase=Mock())),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)))
        plugin.staging = staging
        await plugin.receive_companion_message(event)
        self.assertEqual(self.submissions, [])
        request = NS(system_prompt="original persona", func_tool=object())
        original_tools = request.func_tool
        await plugin.prepare_companion_core_request(event, request)
        self.assertIs(request.func_tool, original_tools)
        self.assertIn("original persona", request.system_prompt)
        event.set_result(NS(result_content_type=NS(name="LLM_RESULT")))
        await plugin.guard_companion_core_delivery(event)
        await event.send(MessageChain([Plain("synthetic core result")]))
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(self.submissions[0]["threadId"], "peer/x")
        await plugin.observe_companion_core_reply_sent(event)
        self.assertIs(event.send.__func__, original)

    async def test_native_face_fallback_preserves_other_media(self):
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Face, Plain, Image
        from astrbot_host.core_reply_media_delivery import native_wire_message
        event = self.event(await self.adapter())
        message = MessageChain([Plain("hello"), Face(id=14), Image.fromBytes(self.png)])
        result = await native_wire_message(event, message)
        self.assertTrue(any(isinstance(part, Image) for part in result.chain))
        self.assertFalse(any(isinstance(part, Face) for part in result.chain))
        self.assertIn("[QQ表情 14]", "".join(part.text for part in result.chain if isinstance(part, Plain)))

    async def test_same_named_peers_on_two_accounts_keep_distinct_routes(self):
        first = await self.adapter()
        second = await self.adapter(identifier="wx-second", account="b")
        controller = await self.controller(first, extra_adapters=(second,))
        captured = []
        for adapter in (first, second):
            event = self.event(adapter, thread="same-thread", sender="real-user")
            captured.append((await controller.session.bridge.capture_core_reply(event)).unwrap())
        left, right = captured
        self.assertEqual(left.principal.native_account_id, right.principal.native_account_id)
        self.assertNotEqual(left.principal.account_id, right.principal.account_id)
        self.assertNotEqual(left.route.scope.session_id, right.route.scope.session_id)
        self.assertNotEqual(left.route.scope.conversation_id, right.route.scope.conversation_id)
        self.assertEqual(self.submissions, [])

    async def test_game_context_matches_exact_native_room_and_poke_is_not_offered(self):
        from astrbot_host.native_extensions.game.native_context import _room_route, _scope_matches
        from astrbot_host.core_reply_tools import CoreReplyToolSession
        adapter = await self.adapter()
        event = self.event(adapter)
        controller = await self.controller(adapter, observe=event)
        context, _ = await controller.session.synchronize(controller.settings.target("wx"))
        room = NS(source="private", creator_qq="real-user", platform="wx-main", session_id=event.unified_msg_origin)
        route = _room_route(room, controller.session_topology)
        self.assertEqual(route, ("wx-main", "private", "real-user"))
        self.assertTrue(_scope_matches(context.scope, room, route))
        room.session_id = "wx-main:FriendMessage:other-session"
        self.assertFalse(_scope_matches(context.scope, room, route))
        tool_session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        tool_session.event, tool_session.context = event, context
        tool_session.application = NS(tools=NS(catalog=()), tasks=NS(), require_running=lambda: None)
        tool_session.state, tool_session.prepared = {}, {}
        tool_session.group_tool_profile = "full"
        offered = tool_session.catalog()
        self.assertNotIn("interaction.poke", {item.spec.action for item in offered})


    async def test_core_voice_only_quote_uses_original_text_carrier_and_native_send(self):
        import base64
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain, Reply
        from astrbot_host.core_reply import prepare_core_reply_event, activate_core_reply_delivery_guard
        from astrbot_host.core_reply_voice import prepare_core_reply_voice_message
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy
        from companion_v2.foundation.outcome import Outcome
        adapter = await self.adapter()
        for thread in ("peer/x", "room@chatroom"):
            with self.subTest(thread=thread):
                event = self.event(adapter, thread=thread)
                raw = event.message_obj.raw_message
                prepared = {"context": object(), "event_id": raw["id"],
                            "voice_reply_policy": VoiceReplyPolicy(delivery_mode="voice_only")}
                async def allow(*_):
                    return Outcome.success(True)
                async def render(*_, **__):
                    return {"type": "record", "data": {
                        "file": "base64://" + base64.b64encode(self.wav).decode()}}
                async def prepare(current, message, check, index, count):
                    return await prepare_core_reply_voice_message(
                        event, current, message, renderer=render, delivery=None, authorize=check)
                prepare_core_reply_event(event, prepared, authorize=allow, prepare_send=prepare)
                event.set_result(NS(result_content_type=NS(name="LLM_RESULT")))
                activate_core_reply_delivery_guard(event)
                before = len(self.submissions)
                await event.send(MessageChain([Reply(id=raw["id"]), Plain("晚安，睡吧")]))
                sent = self.submissions[before:]
                self.assertEqual(len(sent), 2)
                text = next(item for item in sent if "text" in item)
                voice = next(item for item in sent if item.get("kind") == "voice")
                self.assertEqual(text["text"], "晚安，睡吧")
                self.assertEqual(text["quote"]["serverId"], raw["serverId"])
                self.assertEqual(voice["threadId"], thread)
                self.assertTrue(self.uploads)


    async def test_live_adapter_event_remains_original_after_event_module_reimport(self):
        from companion_v2.adapters.astrbot.wechat_native import event_session
        module_name = self.event_module.__name__
        spec = importlib.util.spec_from_file_location(module_name, self.event_module.__file__)
        refreshed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(refreshed)
        self.assertIsNot(refreshed.WeChatNativeEvent, self.Event)
        support = importlib.import_module(module_name.rsplit(".", 1)[0] + ".tool_support")
        adapter = await self.adapter()
        controller = await self.controller(adapter)
        for thread in ("peer/x", "room@chatroom"):
            with self.subTest(thread=thread):
                original = self.event(adapter, thread=thread)
                # Mirror Native's retention of accepted incoming events.
                adapter._events[original.message_obj.message_id] = original
                with patch.dict(sys.modules, {module_name: refreshed}), patch.object(
                        support, "WeChatNativeEvent", refreshed.WeChatNativeEvent):
                    self.assertEqual(event_session(original), original.session.session_id)
                    await controller.session_topology.observe(original)
                    self.assertIsNotNone(controller.target_for_event(original))
                    captured = await (controller.session.bridge.capture_text_only(original)
                        if original.message_obj.group_id else
                        controller.session.bridge.capture_core_reply(original))
                    self.assertTrue(captured.is_success, captured.failure)
                    # A class name plus copied metadata cannot replace an event
                    # retained by the adapter during class-identity drift.
                    other = self.Event(original.message_obj, adapter)
                    with self.assertRaisesRegex(ValueError, "original event"):
                        event_session(other)
                original.message_obj.raw_message["accountId"] = "other-account"
                with self.assertRaises(ValueError):
                    event_session(original)

    async def test_native_voice_inherits_author_common_settings_without_role_writes(self):
        import base64
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain
        from astrbot_host.core_reply_voice import prepare_core_reply_voice_message
        from companion_v2.conversation.reply_settings import voice_policy
        from companion_v2.foundation.outcome import Outcome
        adapter = await self.adapter()
        event = self.event(adapter)
        controller = await self.controller(adapter, observe=event,
            voice_config={"enabled": True, "probability": 1.0, "trigger_mode": "always",
                          "generation_mode": "plain", "conversion_scope": "full"})
        # Both existing QQ and Native targets resolve the author's defaults.
        for target_id in ("first", "wx"):
            context, _ = await controller.session.synchronize(controller.settings.target(target_id))
            snapshot = controller.session.application.reply_settings.snapshot(context.actor, context, controller.now())
            values = {key.removeprefix("conversation.presentation."): value for key, value in snapshot.values.items()}
            for name, expected in (("voice_enabled", True), ("voice_probability", 1.0),
                                   ("voice_trigger_mode", "always"), ("voice_generation_mode", "plain")):
                self.assertEqual(values[name], expected)
                self.assertIsNone(snapshot.fields["conversation.presentation." + name].source)
        render = AsyncMock(return_value={"type": "record", "data": {
            "file": "base64://" + base64.b64encode(self.wav).decode()}})
        result = await prepare_core_reply_voice_message(event,
            {"context": context, "voice_reply_policy": voice_policy(values)},
            MessageChain([Plain("synthetic voice")]), renderer=render, delivery=None,
            authorize=lambda *_: asyncio.sleep(0, result=Outcome.success(True)))
        render.assert_awaited_once()
        await event.send(result)
        self.assertTrue(any(item.get("kind") == "voice" for item in self.submissions))
