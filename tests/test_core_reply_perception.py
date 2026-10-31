import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from astrbot.core.agent.message import Message, TextPart, dump_messages_with_checkpoints
from astrbot.core.provider.sources.fishaudio_tts_api_source import ProviderFishAudioTTSAPI
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, inject_core_reply_prompt
from astrbot_host.core_reply_perception import (
    _group_participation_hint,
    inject_core_reply_perception_context,
    inject_core_reply_perception_policy,
)
from companion_v2.conversation.voice_reply import VoiceReplyPolicy


class Event:
    def __init__(self):
        self.extra = {}
        self.unified_msg_origin = "qq-main:FriendMessage:200"

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)


def prepared(*, options=(True, True, True), route="chat-route", guidance=""):
    return {
        "context": NS(scope=NS(platform="qq-main")),
        "perception_options": options,
        "perception_context": json.dumps({"current_local_time": {"period": "晚上"}},
                                          ensure_ascii=False),
        "perception_instructions": ("时间只在相关时自然使用。",),
        "perception_guidance": guidance,
        "prompt_instruction": "【相处背景】今天刚聊过星星。",
        "route": route,
    }


def perception_payload(request):
    value = request.extra_user_content_parts[-1].text
    return json.loads(value.removeprefix("<companion_v2_context>\n")
                      .removesuffix("\n</companion_v2_context>"))


class CoreReplyPerceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_wake_name_restores_primary_provider_prompt(self):
        event = Event()
        event.is_at_or_wake_command = True
        turn = prepared(options=(False, False, False))
        turn["native_input"] = NS(text="星缘黑先吧，开五子棋房间给我")
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
        native_context = NS(get_config=lambda **_: {"wake_prefix": ["/", "星缘"]})
        request = NS(prompt="黑先吧，开五子棋房间给我", extra_user_content_parts=[],
                     system_prompt="你是星缘。")
        await inject_core_reply_perception_context(event, request, native_context=native_context)
        provider = object.__new__(ProviderOpenAIOfficial)
        assembled = await provider.assemble_context(request.prompt,
            extra_user_content_parts=request.extra_user_content_parts)
        self.assertEqual(assembled["content"][0]["text"], turn["native_input"].text)
        await inject_core_reply_perception_context(event, request, native_context=native_context)
        self.assertEqual(request.prompt, turn["native_input"].text)
        self.assertEqual(len(request.extra_user_content_parts), 1)
        request.prompt = "开五子棋房间给我"
        await inject_core_reply_perception_context(event, request, native_context=native_context)
        self.assertEqual(request.prompt, "开五子棋房间给我")

    async def test_removed_wake_name_remains_in_actual_provider_input(self):
        event = Event()
        turn = prepared(options=(False, False, False))
        turn["native_input"] = NS(text="星缘黑先吧")
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
        request = NS(prompt="黑先吧", extra_user_content_parts=[], system_prompt="你是星缘。")
        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request)
        self.assertEqual(perception_payload(request)["original_current_message"]["text"], "星缘黑先吧")
        provider = object.__new__(ProviderOpenAIOfficial)
        assembled = await provider.assemble_context(request.prompt,
            extra_user_content_parts=request.extra_user_content_parts)
        self.assertIn("星缘黑先吧", json.dumps(assembled, ensure_ascii=False))
        self.assertEqual(request.prompt, "黑先吧")
        request.prompt = "另一个插件提供的完整请求"
        await inject_core_reply_perception_context(event, request)
        self.assertNotIn("original_current_message", perception_payload(request))

    async def test_dynamic_facts_are_user_context_and_configured_route_is_explicit(self):
        event = Event()
        event.extra["selected_provider"] = "chat-route"
        provider = NS(
            provider_config={"id": "chat-route", "modalities": ["text", "image", "audio"]},
            meta=lambda: NS(id="chat-route"),
            get_model=lambda: "qwen-chat-alias",
        )
        native_context = NS(get_provider_by_id=Mock(return_value=provider),
                            get_using_provider_async=AsyncMock())
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": prepared(guidance="只在自然时顺带提起时间。")}
        request = NS(system_prompt="AstrBot 人格", model=None, image_urls=["image"], audio_urls=[],
                     func_tool=NS(tools=[object()]), extra_user_content_parts=[])

        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request, native_context=native_context)
        payload = perception_payload(request)

        self.assertIn("若包含角色当前状态", request.system_prompt)
        self.assertNotIn("只在自然时顺带提起时间", request.system_prompt)
        self.assertNotIn("chat-route", request.system_prompt)
        self.assertNotIn("qwen-chat-alias", request.system_prompt)
        self.assertEqual(payload["conversation"]["current_local_time"]["period"], "晚上")
        self.assertEqual(payload["companion_background"], "【相处背景】今天刚聊过星星。")
        self.assertIn("只在自然时顺带提起时间", payload["expression_guidance"])
        self.assertEqual(payload["environment"]["platform"], "qq-main")
        self.assertEqual(payload["environment"]["chat_route"]["provider_id"], "chat-route")
        self.assertEqual(payload["environment"]["chat_route"]["model"], "qwen-chat-alias")
        self.assertFalse(payload["environment"]["chat_route"]["verified_server_model"])
        self.assertEqual(payload["environment"]["configured_input_modalities"]["values"],
                         ["text", "image", "audio"])
        self.assertTrue(payload["environment"]["request_contents"]["image"])
        self.assertTrue(payload["environment"]["request_contents"]["structured_tools"])
        self.assertIsInstance(request.extra_user_content_parts[-1], TextPart)
        self.assertTrue(request.extra_user_content_parts[-1]._no_save)

        provider = object.__new__(ProviderOpenAIOfficial)
        assembled = await provider.assemble_context(
            "星缘现在用的是什么模型？", extra_user_content_parts=request.extra_user_content_parts)
        assembled_text = [part["text"] for part in assembled["content"] if part["type"] == "text"]
        self.assertIn(request.extra_user_content_parts[-1].text, assembled_text)

        user_message = Message.model_validate({"role": "user", "content": request.extra_user_content_parts})
        self.assertTrue(user_message.content[-1]._no_save)
        self.assertEqual(dump_messages_with_checkpoints([user_message])[0]["content"], [])
        native_context.get_using_provider_async.assert_not_awaited()

    async def test_dynamic_changes_do_not_change_system_prompt_and_injection_is_idempotent(self):
        provider_a = NS(provider_config={"id": "route-a", "modalities": ["text"]},
                        meta=lambda: NS(id="route-a"), get_model=lambda: "model-a")
        provider_b = NS(provider_config={"id": "route-b", "modalities": ["text"]},
                        meta=lambda: NS(id="route-b"), get_model=lambda: "model-b")
        prompts, payloads = [], []

        for route, provider in (("route-a", provider_a), ("route-b", provider_b)):
            event = Event()
            event.extra["selected_provider"] = route
            turn = prepared(route=route, guidance="只在适合时提到" + route)
            turn["stable_prompt_instruction"] = "固定角色档案与表达规则"
            turn["perception_instructions"] = ("按本轮时区接话：" + route,)
            turn["perception_context"] = json.dumps({"current_local_time": {"period": route}},
                                                    ensure_ascii=False)
            turn["prompt_instruction"] = "本轮背景：" + route
            event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
            request = NS(system_prompt="AstrBot 人格", model=None, image_urls=[], audio_urls=[],
                         func_tool=NS(tools=[]), extra_user_content_parts=[])
            native_context = NS(get_provider_by_id=Mock(return_value=provider),
                                get_using_provider_async=AsyncMock())

            inject_core_reply_perception_policy(event, request)
            inject_core_reply_perception_policy(event, request)
            inject_core_reply_prompt(event, request)
            await inject_core_reply_perception_context(event, request, native_context=native_context)
            await inject_core_reply_perception_context(event, request, native_context=native_context)
            prompts.append(request.system_prompt)
            payloads.append(perception_payload(request))
            self.assertEqual(len(request.extra_user_content_parts), 1)
            self.assertTrue(request.extra_user_content_parts[0]._no_save)
            self.assertEqual(request.system_prompt.count("【陪伴感知与自然表达】"), 1)

        self.assertEqual(prompts[0], prompts[1])
        self.assertIn("固定角色档案与表达规则", prompts[0])
        self.assertNotEqual(payloads[0]["environment"]["chat_route"],
                            payloads[1]["environment"]["chat_route"])
        self.assertNotEqual(payloads[0]["conversation"], payloads[1]["conversation"])
        self.assertNotEqual(payloads[0]["expression_guidance"], payloads[1]["expression_guidance"])
        self.assertNotEqual(payloads[0]["companion_background"], payloads[1]["companion_background"])

    async def test_language_voice_route_reaches_provider_input_and_tracks_live_drama_model(self):
        event = Event()
        event.extra["selected_provider"] = "chat-route"
        chat = NS(provider_config={"id": "chat-route", "model": "chat-config"})
        voice = ProviderFishAudioTTSAPI({
            "id": "fish-ja", "name": "FishAudio", "type": "fishaudio_tts_api",
            "api_key": "synthetic-voice-secret", "api_base": "https://tts.example.invalid/v1",
            "model": "s2.1-pro-free", "fishaudio-tts-reference-id": "synthetic-reference",
        }, {})
        voice.set_model("drama-3")
        voice.get_audio = AsyncMock()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja",
                                  provider_id="default-voice", ja_provider_id="fish-ja",
                                  foreign_text_mode="translation")
        turn = prepared()
        turn.update(voice_reply_policy=policy, configured_voice_reply_policy=policy)
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
        native = NS(get_provider_by_id=Mock(side_effect=lambda key: voice if key == "fish-ja" else chat),
                    get_all_tts_providers=lambda: [voice], get_using_tts_provider_async=AsyncMock(),
                    get_config=lambda **_: {"provider_tts_settings": {"enable": False}})
        request = NS(system_prompt="AstrBot 人格", model="gpt-4.1", extra_user_content_parts=[])

        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request, native_context=native)
        route = perception_payload(request)["environment"]["tts_route"]
        self.assertEqual(route["status"], "available")
        self.assertEqual(route["provider_id"], "fish-ja")
        self.assertEqual(route["provider_name"], "FishAudio")
        self.assertEqual(route["model"], "drama-3")
        self.assertEqual(route["language"], "ja")
        self.assertEqual(route["generation_mode"], "model_tag")
        self.assertEqual(route["delivery_mode"], "voice_and_text")
        self.assertEqual(route["foreign_text_mode"], "translation")
        self.assertTrue(route["companion_voice_active_this_turn"])
        self.assertTrue(route["drama_3_directions"])
        self.assertFalse(route["verified_server_model"])
        self.assertFalse(route["host_tts_enabled"])
        self.assertEqual(perception_payload(request)["environment"]["chat_route"]["model"], "gpt-4.1")

        assembled = await object.__new__(ProviderOpenAIOfficial).assemble_context(
            "现在用哪些聊天和朗读模型？", extra_user_content_parts=request.extra_user_content_parts)
        wire = json.dumps(assembled, ensure_ascii=False)
        self.assertIn("drama-3", wire)
        for sensitive in ("synthetic-voice-secret", "tts.example.invalid", "synthetic-reference", "api_key"):
            self.assertNotIn(sensitive, wire)
        stable_prompt = request.system_prompt
        for dynamic in ("fish-ja", "drama-3", "gpt-4.1"):
            self.assertNotIn(dynamic, stable_prompt)

        voice.set_model("s2.1-pro-free")
        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request, native_context=native)
        self.assertEqual(request.system_prompt, stable_prompt)
        self.assertEqual(len(request.extra_user_content_parts), 1)
        route = perception_payload(request)["environment"]["tts_route"]
        self.assertEqual(route["model"], "s2.1-pro-free")
        self.assertFalse(route["drama_3_directions"])
        self.assertTrue(request.extra_user_content_parts[0]._no_save)
        native.get_using_tts_provider_async.assert_not_awaited()
        voice.get_audio.assert_not_awaited()

    async def test_host_voice_route_uses_session_selection_in_private_and_group(self):
        for kind in ("FriendMessage", "GroupMessage"):
            with self.subTest(kind=kind):
                event = Event()
                event.unified_msg_origin = f"qq-main:{kind}:200"
                turn = prepared()
                turn["configured_voice_reply_policy"] = None
                event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
                voice = NS(provider_config={"id": "session-voice", "model": "host-speech-model"})
                native = NS(get_using_tts_provider_async=AsyncMock(return_value=voice),
                            get_config=Mock(return_value={"provider_tts_settings": {
                                "enable": True, "provider_id": "global-voice", "dual_output": False}}))
                request = NS(extra_user_content_parts=[])
                await inject_core_reply_perception_context(event, request, native_context=native)
                route = perception_payload(request)["environment"]["tts_route"]
                self.assertEqual(route["provider_id"], "session-voice")
                self.assertEqual(route["configured_provider_id"], "global-voice")
                self.assertEqual(route["model"], "host-speech-model")
                self.assertEqual(route["delivery_mode"], "voice_only")
                self.assertTrue(route["host_tts_enabled"])
                self.assertFalse(route["companion_voice_enabled"])
                self.assertFalse(route["companion_voice_active_this_turn"])
                native.get_using_tts_provider_async.assert_awaited_once_with(umo=event.unified_msg_origin)
                native.get_config.assert_called_once_with(umo=event.unified_msg_origin)

    async def test_unavailable_voice_reports_disabled_missing_and_wrong_provider_without_guessing(self):
        for case in ("disabled", "not_selected", "provider_not_loaded", "lookup_unavailable"):
            with self.subTest(case=case):
                event = Event()
                turn = prepared()
                turn["configured_voice_reply_policy"] = None
                native = NS(get_using_tts_provider_async=AsyncMock(return_value=None),
                            get_using_tts_provider=Mock(),
                            get_config=lambda **_: {"provider_tts_settings": {"enable": case != "disabled"}})
                if case == "provider_not_loaded":
                    turn["configured_voice_reply_policy"] = VoiceReplyPolicy(provider_id="chat-not-tts")
                    native.get_provider_by_id = Mock(return_value=NS(provider_config={"model": "chat-only"}))
                    native.get_all_tts_providers = lambda: []
                elif case == "lookup_unavailable":
                    native.get_using_tts_provider_async.side_effect = RuntimeError("synthetic-lookup-error")
                event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": turn}
                request = NS(extra_user_content_parts=[])
                await inject_core_reply_perception_context(event, request, native_context=native)
                route = perception_payload(request)["environment"]["tts_route"]
                self.assertEqual(route["status"], "unavailable")
                self.assertEqual(route["reason"], case)
                self.assertNotIn("model", route)
                self.assertFalse(route["drama_3_directions"])
                native.get_using_tts_provider.assert_not_called()
                if case in ("disabled", "provider_not_loaded"):
                    native.get_using_tts_provider_async.assert_not_awaited()

    async def test_older_host_voice_resolver_remains_supported(self):
        event = Event()
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": prepared()}
        voice = NS(provider_config={"id": "legacy-host-voice", "model": "legacy-speech"})
        native = NS(get_using_tts_provider=Mock(return_value=voice))
        request = NS(extra_user_content_parts=[])
        await inject_core_reply_perception_context(event, request, native_context=native)
        self.assertEqual(perception_payload(request)["environment"]["tts_route"]["model"], "legacy-speech")
        native.get_using_tts_provider.assert_called_once_with(umo=event.unified_msg_origin)

    async def test_disabled_fields_keep_the_stable_policy_without_dynamic_context(self):
        event = Event()
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": {
            "context": NS(scope=NS(platform="qq-main")),
            "perception_options": (False, False, False),
            "perception_context": "", "perception_instructions": (), "perception_guidance": "",
        }}
        native_context = NS(get_provider_by_id=Mock(), get_using_provider_async=AsyncMock())
        request = NS(system_prompt="AstrBot 人格", model=None, image_urls=[], audio_urls=[], func_tool=None,
                     extra_user_content_parts=[])

        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request, native_context=native_context)

        self.assertIn("【陪伴感知与自然表达】", request.system_prompt)
        self.assertIn("承接中的短话保留原请求的对象与进度", request.system_prompt)
        self.assertIn("不要每轮都用问题收尾", request.system_prompt)
        self.assertTrue(request.system_prompt.startswith("AstrBot 人格"))
        self.assertEqual(request.extra_user_content_parts, [])
        native_context.get_provider_by_id.assert_not_called()
        native_context.get_using_provider_async.assert_not_awaited()

    async def test_context_only_perception_still_gets_stable_interpretation_policy(self):
        event = Event()
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": {
            "context": NS(scope=NS(platform="qq-main")),
            "perception_options": (False, False, False),
            "perception_context": json.dumps({"current_local_time": {"period": "晚上"}},
                                              ensure_ascii=False),
            "perception_instructions": (), "perception_guidance": "", "prompt_instruction": "",
        }}
        request = NS(system_prompt="AstrBot 人格", model=None, image_urls=[], audio_urls=[], func_tool=None,
                     extra_user_content_parts=[])

        inject_core_reply_perception_policy(event, request)
        await inject_core_reply_perception_context(event, request)

        self.assertIn("感知背景帮助理解当下", request.system_prompt)
        self.assertEqual(perception_payload(request)["conversation"]["current_local_time"]["period"], "晚上")

    async def test_unavailable_route_and_undeclared_modalities_are_not_guessed(self):
        event = Event()
        event.extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": prepared()}
        native_context = NS(get_provider_by_id=Mock(return_value=None),
                            get_using_provider_async=AsyncMock(return_value=None))
        request = NS(system_prompt="AstrBot 人格", model=None, image_urls=[], audio_urls=[], func_tool=None,
                     extra_user_content_parts=[])

        await inject_core_reply_perception_context(event, request, native_context=native_context)
        payload = perception_payload(request)

        self.assertEqual(payload["environment"]["chat_route"]["status"], "unavailable")
        self.assertEqual(payload["environment"]["configured_input_modalities"]["status"],
                         "not_explicitly_declared")


class GroupParticipationHintTests(unittest.TestCase):
    def hint(self, admission):
        value = prepared()
        value["group_admission"] = admission
        return _group_participation_hint(value)

    def test_addressed_turn_states_the_reply_decision_without_naming_a_missing_tool(self):
        # The explicit @ already decided this turn replies and the participation
        # tool is not mounted, so the hint must not invite a choose call it cannot
        # serve; it has to point at the reply itself.
        hint = self.hint({"admitted": True, "mentioned": True, "reason": "mention", "reasons": ["mention"]})
        self.assertIn("明确 @ 了你", hint)
        self.assertIn("直接写出要说的话", hint)
        self.assertNotIn("group.participation.choose", hint)

    def test_candidate_turn_keeps_the_original_participation_instruction(self):
        hint = self.hint({"admitted": True, "mentioned": False, "reason": "question", "reasons": ["question"]})
        self.assertIn("本轮没有明确 @", hint)
        self.assertIn("group.participation.choose", hint)

    def test_turn_without_an_admission_gets_no_hint(self):
        self.assertEqual(self.hint(None), "")


if __name__ == "__main__":
    unittest.main()
