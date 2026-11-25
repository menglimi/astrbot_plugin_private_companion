"""开房工具的入口边界：公网访问要先准备好，群聊里不许贴链接。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from astrbot_host.native_extensions import together_room_execution
from companion_v2.conversation.external_tools import ExternalToolInvocation
from companion_v2.execution.models import TaskContext, encode
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.together.mobile.origin import MOBILE_ROOM_CALL
from companion_v2.together.provider import NativeTogetherError, NativeTogetherProvider
from tests.native_internal_support import internal_registry


class FakeStore:
    def __init__(self):
        self.events = []

    def update(self, scope, operation_id, **fields):
        self.events.append(fields)
        return {"id": operation_id, **fields}


class FakePlugin:
    def __init__(self, *, access=None, access_fails=False, running=True):
        self.room_server = SimpleNamespace(running=running)
        self._access = access if access is not None else {
            "url": "https://room.example", "tunnel_started": True,
            "tunnel_ready": True, "fixed_public_url": False}
        self._access_fails = access_fails
        self.prepared = 0
        self.via_mobile_gateway = []
        self.tickets = []

    async def _ensure_mobile_room_access(self, *, via_mobile_gateway=False):
        self.prepared += 1
        self.via_mobile_gateway.append(via_mobile_gateway)
        if self._access_fails:
            raise RuntimeError("tunnel unavailable")
        return dict(self._access)

    def _get_provider_by_id(self, provider_id):
        return SimpleNamespace(text_chat=lambda *args, **kwargs: None)

    def issue_room_ticket(self, *, mode, user_id, native_v2):
        self.tickets.append({"mode": mode, "user_id": user_id, "native_v2": native_v2})
        return SimpleNamespace(mode=mode, token="tok-1", created_at=1.0, expires_at=2.0)

    def _ticket_url(self, ticket):
        return f"https://room.example/join/{ticket.token}?mode={ticket.mode}"


def provider_with(plugin):
    provider = object.__new__(NativeTogetherProvider)
    provider.plugin = plugin
    provider.store = FakeStore()
    provider._room_lock = asyncio.Lock()
    provider._closed = False
    return provider


def request(kind="private", mode="call"):
    return {
        "context": {"audience": {"scope": {"kind": kind, "platform": "aiocqhttp", "session_id": "995051631"}}},
        "host_execution": {"model_provider_id": "p1", "tts_provider_id": "", "tts_language": "zh",
                           "tts_timeout_seconds": 30},
        "host_persona": {"bot_id": "b1", "account_id": "a1", "persona_id": "xiaoling", "prompt": "你是小凌。"},
        "arguments": {"mode": mode},
    }


class RoomCreationTests(unittest.TestCase):
    def test_private_room_prepares_public_access_before_signing_the_link(self):
        plugin = FakePlugin()
        provider = provider_with(plugin)
        operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(plugin.prepared, 1)
        self.assertEqual(operation["state"], "succeeded")
        room = operation["result"]["room"]
        self.assertIn("/join/tok-1", room["url"])
        self.assertTrue(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["tunnel_ready"])
        self.assertEqual(plugin.tickets[0]["user_id"], "u1")

    def test_mobile_gateway_room_uses_the_already_prepared_local_access(self):
        from companion_v2.together.mobile.origin import MOBILE_ROOM_CALL
        plugin = FakePlugin(access={"url": "http://127.0.0.1:6321", "tunnel_started": False,
                                    "tunnel_ready": True, "fixed_public_url": False,
                                    "mobile_gateway": True})
        provider = provider_with(plugin)
        token = MOBILE_ROOM_CALL.set(True)
        try:
            operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        finally:
            MOBILE_ROOM_CALL.reset(token)
        self.assertEqual(plugin.via_mobile_gateway, [True])
        self.assertEqual(operation["state"], "succeeded")
        self.assertFalse(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["tunnel_ready"])

    def test_fixed_public_url_is_not_an_auto_tunnel(self):
        plugin = FakePlugin(access={"url": "https://together.example.com", "tunnel_started": False,
                                    "tunnel_ready": True, "fixed_public_url": True})
        provider = provider_with(plugin)
        operation = asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertFalse(operation["result"]["access"]["tunnel_started"])
        self.assertTrue(operation["result"]["access"]["fixed_public_url"])

    def test_public_access_failure_is_reported_instead_of_handing_out_a_localhost_link(self):
        provider = provider_with(FakePlugin(access_fails=True))
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_public_access_unavailable")

    def test_group_chat_never_gets_a_room_credential(self):
        plugin = FakePlugin()
        provider = provider_with(plugin)
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(kind="group"), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_private_room_required")
        self.assertEqual(plugin.tickets, [])
        self.assertEqual(plugin.prepared, 0)

    def test_room_server_must_be_running(self):
        provider = provider_with(FakePlugin(running=False))
        with self.assertRaises(NativeTogetherError) as caught:
            asyncio.run(provider._create_room(request(), "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(caught.exception.code, "together_room_server_unavailable")


class TogetherRoomExecutionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.base_binding = {
            "model_provider_id": "main/chat-model",
            "tts_provider_id": "main/tts-model",
            "tts_language": "zh",
            "tts_timeout_seconds": 30,
        }

    def test_stale_chat_provider_warns_and_falls_back_to_main_model_and_creates_room(self):
        valid_chat = SimpleNamespace(text_chat=lambda *args, **kwargs: None)
        context = SimpleNamespace(
            get_provider_by_id=lambda provider_id: valid_chat if provider_id == "main/chat-model" else None
        )
        service = SimpleNamespace(
            chat_provider_id="deepseek/deepseek-v4-flash",
            tts_provider_id="",
            tts_timeout_seconds=45,
            context=context,
        )
        with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured:
            execution = together_room_execution(self.base_binding, service, context=context)

        self.assertEqual(execution["model_provider_id"], "main/chat-model")
        self.assertEqual(execution["tts_provider_id"], "main/tts-model")
        self.assertEqual(execution["tts_timeout_seconds"], 45)
        log_output = "\n".join(captured.output)
        self.assertIn("chat_provider_id", log_output)
        self.assertIn("deepseek/deepseek-v4-flash", log_output)
        self.assertIn("main/chat-model", log_output)

        plugin = FakePlugin()
        plugin._get_provider_by_id = lambda pid: valid_chat if pid == "main/chat-model" else None
        provider = provider_with(plugin)
        req = request()
        req["host_execution"] = execution
        operation = asyncio.run(provider._create_room(req, "scope-1", {"id": "op"}, user_id="u1"))
        self.assertEqual(operation["state"], "succeeded")
        self.assertEqual(plugin.tickets[0]["native_v2"]["model_provider_id"], "main/chat-model")

    def test_non_chat_provider_in_chat_provider_id_warns_and_falls_back(self):
        providers = {
            "main/chat-model": SimpleNamespace(text_chat=lambda *args, **kwargs: None),
            "voice/only-tts": SimpleNamespace(get_audio=lambda *args, **kwargs: None),
        }
        context = SimpleNamespace(get_provider_by_id=providers.get)
        service = SimpleNamespace(chat_provider_id="voice/only-tts", tts_provider_id="")
        with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured:
            execution = together_room_execution(self.base_binding, service, context=context)

        self.assertEqual(execution["model_provider_id"], "main/chat-model")
        self.assertIn("voice/only-tts", "\n".join(captured.output))

    def test_valid_chat_and_tts_overrides_are_kept_without_warning(self):
        providers = {
            "room/chat-model": SimpleNamespace(text_chat=lambda *args, **kwargs: None),
            "room/tts-model": SimpleNamespace(get_audio=lambda *args, **kwargs: None),
        }
        context = SimpleNamespace(get_provider_by_id=providers.get)
        service = SimpleNamespace(chat_provider_id="room/chat-model", tts_provider_id="room/tts-model")
        with self.assertNoLogs("astrbot_host.native_extensions", level="WARNING"):
            execution = together_room_execution(self.base_binding, service, context=context)
        self.assertEqual(execution["model_provider_id"], "room/chat-model")
        self.assertEqual(execution["tts_provider_id"], "room/tts-model")

        # TTS provider registered only via get_all_tts_providers() is also recognized.
        tts_only = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="tts/from-list"),
            get_audio=lambda *args, **kwargs: None,
        )
        list_context = SimpleNamespace(
            get_provider_by_id=lambda pid: providers.get(pid),
            get_all_tts_providers=lambda: [tts_only],
        )
        service_tts = SimpleNamespace(chat_provider_id="room/chat-model", tts_provider_id="tts/from-list")
        with self.assertNoLogs("astrbot_host.native_extensions", level="WARNING"):
            execution_list = together_room_execution(self.base_binding, service_tts, context=list_context)
        self.assertEqual(execution_list["tts_provider_id"], "tts/from-list")

    def test_stale_tts_provider_warns_and_falls_back_to_binding_or_default(self):
        context = SimpleNamespace(get_provider_by_id=lambda pid: None, get_all_tts_providers=lambda: [])
        service = SimpleNamespace(chat_provider_id="", tts_provider_id="deleted/tts")
        with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured:
            execution = together_room_execution(self.base_binding, service, context=context)
        self.assertEqual(execution["tts_provider_id"], "main/tts-model")
        self.assertIn("deleted/tts", "\n".join(captured.output))
        self.assertIn("main/tts-model", "\n".join(captured.output))

        empty_tts_binding = {**self.base_binding, "tts_provider_id": ""}
        with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured_default:
            execution_default = together_room_execution(empty_tts_binding, service, context=context)
        self.assertEqual(execution_default["tts_provider_id"], "")
        self.assertIn("deleted/tts", "\n".join(captured_default.output))

    def test_without_host_resolver_preserves_explicit_overrides(self):
        service = SimpleNamespace(chat_provider_id="custom/chat", tts_provider_id="custom/tts")
        execution = together_room_execution(self.base_binding, service)
        self.assertEqual(execution["model_provider_id"], "custom/chat")
        self.assertEqual(execution["tts_provider_id"], "custom/tts")

    def test_stale_chat_provider_with_empty_main_fallback_keeps_model_unavailable_code(self):
        context = SimpleNamespace(get_provider_by_id=lambda pid: None)
        service = SimpleNamespace(chat_provider_id="deleted/chat", tts_provider_id="")
        empty_main = {**self.base_binding, "model_provider_id": ""}
        with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured:
            execution = together_room_execution(empty_main, service, context=context)
        self.assertEqual(execution["model_provider_id"], "deleted/chat")
        self.assertIn("deleted/chat", "\n".join(captured.output))

    def test_native_registry_invoke_falls_back_from_stale_chat_provider_in_mobile_lan_mode(self):
        async def scenario():
            valid_chat = SimpleNamespace(text_chat=lambda *args, **kwargs: None)
            context = SimpleNamespace(
                get_provider_by_id=lambda pid: valid_chat if pid == "main/chat-model" else None
            )
            plugin = FakePlugin(access={
                "url": "http://127.0.0.1:6321",
                "tunnel_started": False,
                "tunnel_ready": True,
                "fixed_public_url": False,
                "mobile_gateway": True,
            })
            plugin.chat_provider_id = "deepseek/deepseek-v4-flash"
            plugin.tts_provider_id = ""
            plugin.tts_timeout_seconds = 30
            plugin.context = context
            plugin._get_provider_by_id = lambda pid: valid_chat if pid == "main/chat-model" else None

            with TemporaryDirectory(prefix="v2-together-fallback-") as temp_dir:
                from companion_v2.product import PRODUCT_VERSION
                provider = NativeTogetherProvider(plugin, Path(temp_dir), version=PRODUCT_VERSION)
                registry = internal_registry(provider)
                registry.context = context
                registry.together_execution = dict(self.base_binding)
                registry._bundled_extensions[0].instance = plugin

                async def persona_guard(scope, role_binding):
                    return {
                        "persona_id": "role-a",
                        "prompt": "你是陪伴者。",
                        "bot_id": scope.bot_id,
                        "account_id": scope.account_id,
                    }

                registry.bind_role_guard(
                    _noop_role_guard,
                    persona_guard=persona_guard,
                )
                declaration = next(
                    item for item in registry.declarations()
                    if item.descriptor.tool.action == "together.room.create"
                )
                scope = ScopeRef(
                    "install-a", "aiocqhttp", ScopeKind.PRIVATE, "12345",
                    "bot-a", "bot-a-account", "12345", "12345",
                )
                subject = SubjectRef("install-a", SubjectKind.HUMAN, "human-12345")
                role = SubjectRef("install-a", SubjectKind.BOT, "role-a")
                principal = AuthenticatedHostPrincipal(
                    "install-a", "astrbot", "aiocqhttp", "12345", "bot-a", "bot-a-account",
                )
                actor = ResolvedIdentity(principal, subject, "binding-a", 1, 1)
                inv_context = TaskContext(
                    actor=actor,
                    role_binding=RoleBinding(scope, role, 1),
                    audience=AudienceRef(scope, (subject,)),
                )
                invocation = ExternalToolInvocation(
                    "inv-lan-fallback",
                    "parent-lan-fallback",
                    "call-lan-fallback",
                    inv_context,
                    declaration.descriptor,
                    encode({"mode": "watch"}),
                    datetime.now(timezone.utc) + timedelta(seconds=30),
                    before_dispatch=lambda: None,
                )
                marker = MOBILE_ROOM_CALL.set(True)
                try:
                    with self.assertLogs("astrbot_host.native_extensions", level="WARNING") as captured:
                        response = await registry.invoke(declaration.host_tool_id, invocation)
                finally:
                    MOBILE_ROOM_CALL.reset(marker)
                    await registry.aclose()

                self.assertEqual(response.state, "succeeded")
                self.assertEqual(plugin.via_mobile_gateway, [True])
                self.assertEqual(plugin.tickets[0]["native_v2"]["model_provider_id"], "main/chat-model")
                self.assertIn("deepseek/deepseek-v4-flash", "\n".join(captured.output))
                self.assertIn("main/chat-model", "\n".join(captured.output))

        asyncio.run(scenario())

    def test_together_context_execution_uses_live_companion_voice_policy(self):
        from astrbot_host.native_extensions import together_context_execution

        app = SimpleNamespace(
            reply_settings=SimpleNamespace(
                capture=lambda ctx, at: {
                    "settings": {
                        "voice_language": "ja",
                        "voice_provider_id": "default_tts",
                        "voice_zh_provider_id": "fishaudio_tts",
                        "voice_ja_provider_id": "SyuanDrama3",
                        "voice_en_provider_id": "fishaudio_tts",
                        "voice_timeout_seconds": 55,
                    }
                }
            )
        )
        ctx = SimpleNamespace(scope=SimpleNamespace(kind=SimpleNamespace(value="private")))
        base = {
            "model_provider_id": "main/chat",
            "tts_provider_id": "",
            "tts_language": "zh",
            "tts_timeout_seconds": 30,
        }
        resolved = together_context_execution(base, app, ctx)
        self.assertEqual(set(resolved), {"model_provider_id", "tts_provider_id", "tts_language", "tts_timeout_seconds"})
        self.assertEqual(resolved["tts_provider_id"], "SyuanDrama3")
        self.assertEqual(resolved["tts_language"], "ja")
        self.assertEqual(resolved["tts_timeout_seconds"], 55)

    def test_together_room_tts_follows_companion_per_language_bindings_and_honors_override(self):
        from astrbot_host.native_extensions.together.speech_methods import TogetherSpeechMethods
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy
        from companion_v2.together.models import NativeRoomBinding, RoomSession

        zh_tts = SimpleNamespace(meta=lambda: SimpleNamespace(id="fishaudio_tts"), get_audio=lambda text: text)
        ja_tts = SimpleNamespace(meta=lambda: SimpleNamespace(id="SyuanDrama3"), get_audio=lambda text: text)
        en_tts = SimpleNamespace(meta=lambda: SimpleNamespace(id="en_tts"), get_audio=lambda text: text)
        room_tts = SimpleNamespace(meta=lambda: SimpleNamespace(id="room_override"), get_audio=lambda text: text)

        class FakeTogether(TogetherSpeechMethods):
            def __init__(self, policy):
                self._policy = policy
                self.tts_provider_id = ""
                self.direct_multilingual_tts = True
                self.browser_language = "zh-CN"
                self.context = SimpleNamespace(
                    get_all_tts_providers=lambda: [zh_tts, ja_tts, en_tts, room_tts],
                    get_provider_by_id=lambda pid: None,
                )

            def _spoken_text(self, value):
                return str(value or "").strip()

            def _get_provider_by_id(self, provider_id):
                return None

            def _companion_voice_policy(self, room):
                return self._policy

            def _companion_realtime_voice_config(self):
                return {"browser_language": self.browser_language}

        policy = VoiceReplyPolicy(
            language="ja",
            zh_provider_id="fishaudio_tts",
            ja_provider_id="SyuanDrama3",
            en_provider_id="en_tts",
        )
        service = FakeTogether(policy)
        room = RoomSession(
            room_id="r1",
            ticket_token="t1",
            mode="call",
            user_id="u1",
            websocket=None,
            native_v2=NativeRoomBinding(
                scope_key="k1", bot_id="b1", account_id="a1", persona_id="p1",
                prompt="你是冬马和纱。", model_provider_id="m1",
                tts_provider_id="", tts_language="zh", tts_timeout_seconds=60,
                platform="aiocqhttp", session_id="u1", scope_kind="private",
            ),
        )
        room.call_active = True

        # Default ("") follows Companion's per-language bindings dynamically by spoken script.
        self.assertIs(service._room_tts_provider(room, spoken_text="おはよう、今日も寒いね"), ja_tts)
        self.assertIs(service._room_tts_provider(room, spoken_text="早安，今天天气有点冷"), zh_tts)
        self.assertIs(service._room_tts_provider(room, spoken_text="Good morning, let us play piano"), en_tts)
        # Undetermined text / capability probe falls back to Companion's configured default language (ja).
        self.assertIs(service._room_tts_provider(room, spoken_text=""), ja_tts)
        # Direct multilingual prompt picks up live Companion language (ja) even if room binding had zh.
        self.assertIn("ja-JP", service._call_direct_speech_prompt(room))

        # Explicit room-level tts_provider_id override always takes precedence.
        service.tts_provider_id = "room_override"
        self.assertIs(service._room_tts_provider(room, spoken_text="おはよう"), room_tts)
        self.assertIs(service._room_tts_provider(room, spoken_text="早安"), room_tts)

        # Stale explicit override gracefully falls back to Companion per-language bindings.
        service.tts_provider_id = "deleted_tts"
        self.assertIs(service._room_tts_provider(room, spoken_text="おはよう"), ja_tts)
        self.assertIs(service._room_tts_provider(room, spoken_text="早安"), zh_tts)

    def test_synthesize_and_send_resolves_companion_voice_policy_once_per_utterance(self):
        from astrbot_host.native_extensions.together.activity_methods import TogetherActivityMethods
        from astrbot_host.native_extensions.together.speech_methods import TogetherSpeechMethods
        from companion_v2.conversation.voice_reply import VoiceReplyPolicy
        from companion_v2.together.models import NativeRoomBinding, RoomSession

        calls = []
        sent = []

        class FakeTogether(TogetherActivityMethods, TogetherSpeechMethods):
            def __init__(self, policy):
                self._policy = policy
                self.tts_provider_id = ""
                self.browser_language = "zh-CN"
                self.record_visible_turns = False
                self.sync_astrbot_conversation = False
                self.context = SimpleNamespace(
                    get_all_tts_providers=lambda: [],
                    get_provider_by_id=lambda pid: None,
                )

            def _spoken_text(self, value):
                return str(value or "").strip()

            def _get_provider_by_id(self, provider_id):
                return None

            def _companion_voice_policy(self, room):
                calls.append(room.room_id)
                return self._policy

            def _companion_realtime_voice_config(self):
                return {"browser_language": self.browser_language}

            async def send_room_payload(self, room, payload):
                sent.append(payload)
                return True

        policy = VoiceReplyPolicy(language="ja", ja_provider_id="missing_tts")
        service = FakeTogether(policy)
        room = RoomSession(
            room_id="r-once",
            ticket_token="t-once",
            mode="call",
            user_id="u1",
            websocket=None,
            native_v2=NativeRoomBinding(
                scope_key="k1", bot_id="b1", account_id="a1", persona_id="p1",
                prompt="你是冬马和纱。", model_provider_id="m1",
                tts_provider_id="", tts_language="zh", tts_timeout_seconds=60,
                platform="aiocqhttp", session_id="u1", scope_kind="private",
            ),
        )
        room.call_active = True
        room.call_tts_enabled = True

        # Neutral spoken text ("123") forces both provider selection and browser language
        # to rely on the policy, proving a single policy lookup serves both steps.
        asyncio.run(service._synthesize_and_send(room, "123"))
        self.assertEqual(calls, ["r-once"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "tts_fallback")
        self.assertEqual(sent[0]["language"], "ja-JP")


async def _noop_role_guard(scope, role_binding):
    return None


if __name__ == "__main__":
    unittest.main()
