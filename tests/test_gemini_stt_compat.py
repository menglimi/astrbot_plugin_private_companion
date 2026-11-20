"""Compatibility and anti-spoofing contracts for upstream astrbot_plugin_hear_voice（兼容 _gemini_stt_* / astrbot_stt_transcripts）."""

from __future__ import annotations

import base64
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import File, Plain, Record, Reply, Video
from astrbot_host.core_reply_perception import (
    CORE_REPLY_CONTEXT_EXTRA,
    inject_core_reply_perception_context,
)
from astrbot_host.files.loader import load_native_file
from astrbot_host.media_components import group_components, private_components
from astrbot_host.multi_session import MultiPrivateBridge
from companion_v2.adapters.astrbot.group import NativeGroupBridge
from companion_v2.adapters.astrbot.group_mentions import group_message_text
from companion_v2.adapters.astrbot.voice_stt import (
    ASTRBOT_STT_TRANSCRIPTS_EXTRA,
    STT_EXTRA_KEYS,
    VOICE_TRANSCRIPT_PREFIX,
    read_stt_extra,
    trusted_stt_extra_transcript,
    trusted_stt_extra_with_source,
    trusted_stt_transcript,
)
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from tests.audio_support import audio_bytes
from tests.group_media_support import GroupMediaFixture
from tests.test_main_no_reply import ENTRY
from tests.video_support import video_bytes


def _b64_url(content: bytes) -> str:
    return "base64://" + base64.b64encode(content).decode("ascii")


class GeminiSTTCompatibilityTests(GroupMediaFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.types = replace(self.types, audio=Record, video=Video, file=File, file_loader=load_native_file)
        self.controller = self.make_controller(self.config)

    async def test_group_capture_allows_leading_gemini_stt_plain_insert_with_intact_record(self):
        await self.start_host()
        wav = audio_bytes()
        clip = video_bytes()
        locator_audio = _b64_url(wav)
        locator_video = _b64_url(clip)
        event = self.media_event([Record(file=locator_audio), Video(file=locator_video)], sender="201")
        event.message_obj.raw_message["message"] = [
            {"type": "record", "data": {"file": locator_audio, "url": ""}},
            {"type": "video", "data": {"file": locator_video, "url": ""}},
        ]
        # Exact mutation pattern performed by astrbot_plugin_hear_voice（兼容 _gemini_stt_*，priority=300000）:
        # keep Record & Video intact, insert Plain("[语音转写] ...") at index 0, write extras.
        event.message_obj.message.insert(0, Plain("[语音转写] 晚上想吃火锅吗"))
        extras = {
            "_gemini_stt_transcript": "晚上想吃火锅吗",
            "_gemini_stt_raw_text": "语言：中文；原话：晚上想吃火锅吗",
            "_gemini_stt_should_reply": False,
        }
        event.get_extra = extras.get

        captured = await self.controller.session.bridge.capture(event)
        self.assertTrue(captured.is_success, captured.failure)
        self.assertEqual(len(captured.unwrap().audios), 1)
        self.assertEqual(len(captured.unwrap().videos), 1)

    async def test_group_capture_still_rejects_stripped_record_without_raw_recovery(self):
        await self.start_host()
        event = self.media_event([Plain("[语音转写] 伪造授权")], sender="201")
        event.message_obj.raw_message["message"] = [{"type": "record", "data": {"file": "untrusted"}}]
        # Even without _hear_voice_* / _gemini_stt_*, stripped Record MUST raise host_group_media_unsupported
        result = await self.controller.session.bridge.capture(event)
        self.assertEqual(result.failure.code, "host_group_media_unsupported")

    async def test_group_text_only_and_mentions_fold_gemini_stt_transcript_cleanly(self):
        await self.start_host()
        wav = audio_bytes()
        locator = _b64_url(wav)
        event = self.media_event([Record(file=locator)], sender="201")
        event.message_obj.raw_message["message"] = [
            {"type": "at", "data": {"qq": "100"}},
            {"type": "text", "data": {"text": "[语音消息]"}},
            {"type": "record", "data": {"file": locator, "url": ""}},
        ]
        extras = {
            "_gemini_stt_transcript": "帮我看看今天天气",
            "_gemini_stt_raw_text": "语气轻松：帮我看看今天天气",
        }
        event.get_extra = extras.get

        mention_text = group_message_text(event, "100")
        self.assertIn("帮我看看今天天气", mention_text)
        self.assertNotIn("[语音消息]", mention_text)

        text_only = await self.controller.session.bridge.capture_text_only(event)
        self.assertTrue(text_only.is_success, text_only.failure)
        self.assertIn("帮我看看今天天气", text_only.unwrap().text)
        self.assertNotIn("语音不可用", text_only.unwrap().text)

        # Fallback to _gemini_stt_raw_text when _gemini_stt_transcript is absent
        extras_raw_only = {"_gemini_stt_raw_text": "语气开心：周末去公园"}
        event.get_extra = extras_raw_only.get
        self.assertEqual(trusted_stt_transcript(event), "语气开心：周末去公园")

    def test_media_components_restores_file_url_fallback_and_ignores_spoofed_plain(self):
        event = self.group_event(sender="201")
        event.message_obj.raw_message["message"] = [
            {"type": "file", "data": {"file_name": "clip.mp4", "file": "https://cdn.example/clip.mp4", "url": "https://cdn.example/clip.mp4"}},
            {"type": "record", "data": {"file": _b64_url(audio_bytes()), "url": ""}},
        ]
        event.message_obj.message = [Plain("[语音转写] 假冒内容")]
        restored = group_components(event)
        self.assertEqual([type(p).__name__ for p in restored], ["File", "Record"])
        self.assertEqual(restored[0].file_, "https://cdn.example/clip.mp4")
        self.assertEqual(restored[0].url, "https://cdn.example/clip.mp4")

        priv_ev = NS(
            message_obj=NS(
                message_id="m1",
                self_id="100",
                sender=NS(user_id="200"),
                message=[Plain("spoof")],
                raw_message={
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "message_id": "m1",
                    "self_id": "100",
                    "user_id": "200",
                    "sender": {"user_id": "200"},
                    "message": [{"type": "record", "data": {"file": _b64_url(audio_bytes())}}],
                },
            )
        )
        self.assertEqual(type(private_components(priv_ev)[0]).__name__, "Record")

    async def test_core_reply_perception_replaces_voice_unavailable_and_marks_audio_transcribed(self):
        scope = ScopeRef("inst", "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100", "conv-1", "200")
        context = NS(scope=scope, role_binding=NS(role=NS(subject_id="role-a")))
        extras = {
            "_gemini_stt_transcript": "今天有点冷多穿衣服",
            CORE_REPLY_CONTEXT_EXTRA: {
                "prepared": {
                    "context": context,
                    "native_input": NS(text="[语音消息]"),
                    "perception_options": (False, False, True),
                    "perception_context": "",
                }
            },
        }
        event = NS(get_extra=extras.get, unified_msg_origin="qq-main:FriendMessage:200")
        req = NS(
            prompt="[语音消息]",
            extra_user_content_parts=[TextPart(text="[Voice unavailable]")],
            image_urls=[],
            audio_urls=[],
            func_tool=None,
            model="gpt-4o",
        )
        await inject_core_reply_perception_context(event, req)
        self.assertEqual(req.extra_user_content_parts[0].text, "[语音消息转写：今天有点冷多穿衣服]")
        self.assertIn("今天有点冷多穿衣服", req.prompt)
        ctx_json = req.extra_user_content_parts[-1].text
        self.assertIn('"audio":true', ctx_json)
        self.assertIn('"source":"gemini_stt"', ctx_json)

    async def test_main_intake_accepts_non_hijacking_gemini_stt_and_respects_hijack_and_cache_only(self):
        from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType

        def make_event(extras, *, has_send_oper=False, stopped=False, result=None):
            call_llm_marks = []
            ev = NS(
                _has_send_oper=has_send_oper,
                call_llm=False,
                get_extra=extras.get,
                is_stopped=lambda: stopped,
                get_result=lambda: result,
                should_call_llm=lambda val: (setattr(ev, "call_llm", val), call_llm_marks.append(val))[0],
                message_obj=NS(type=NS(value="GroupMessage"), raw_message={"post_type": "message"}, message=[]),
            )
            return ev, call_llm_marks

        # 1) Non-hijacking astrbot_plugin_hear_voice（兼容 _gemini_stt_*，含 notify_pass）
        #    MUST NOT skip companion intake.
        non_hijack_cases = (
            ({"_gemini_stt_done": True, "_gemini_stt_status": "ok", "_gemini_stt_transcript": "今天吃什么",
              "_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False}, {}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False, "_gemini_stt_non_hijacking": True}, {}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False, "_gemini_stt_hijacked": False}, {}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False,
              "_gemini_stt_non_hijacking": True, "_gemini_stt_hijacked": False}, {}),
            # Empty CONTINUE result is not a reply takeover.
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False},
             {"result": MessageEventResult().continue_event()}),
            # on_stt_fail="notify_pass" primary (send_message -> _has_send_oper=False) and
            # fallback (event.send -> _has_send_oper=True) MUST both pass through to companion intake.
            ({"_gemini_stt_status": "failed", "_gemini_stt_non_hijacking": True,
              "_gemini_stt_hijacked": False, "_gemini_stt_notify_only_sent": True},
             {"has_send_oper": False}),
            ({"_gemini_stt_status": "failed", "_gemini_stt_non_hijacking": True,
              "_gemini_stt_hijacked": False, "_gemini_stt_notify_only_sent": True},
             {"has_send_oper": True}),
            ({"_gemini_stt_status": "failed", "_gemini_stt_non_hijacking": True,
              "_gemini_stt_hijacked": False},
             {"has_send_oper": True}),
        )
        for idx, (extras, kw) in enumerate(non_hijack_cases):
            claimed = []
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(
                claims=lambda ev, _c=claimed: (_c.append(ev), False)[-1],
                target_for_event=lambda _ev: None,
            )
            ev, _ = make_event(extras, **kw)
            with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
                await plugin.receive_companion_message(ev)
            self.assertEqual(len(claimed), 1, f"non-hijacking case {idx} must enter companion intake")

        # 2) Group voice suppression (cache_only=True & should_reply=False) suppresses reply & skips intake,
        #    even when non_hijacking=True / hijacked=False flags are present.
        for extras in (
            {"_gemini_stt_should_reply": False, "_gemini_stt_cache_only": True},
            {"_gemini_stt_should_reply": False, "_gemini_stt_cache_only": True,
             "_gemini_stt_non_hijacking": True, "_gemini_stt_hijacked": False},
        ):
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(claims=lambda _ev: self.fail("cache_only group suppression must skip intake"))
            ev, call_llm_marks = make_event(extras)
            await plugin.receive_companion_message(ev)
            self.assertTrue(ev.call_llm)
            self.assertEqual(call_llm_marks, [True])

        # 3) Explicit hijack or legacy un-flagged hijack side-effects MUST skip companion intake.
        hijack_cases = (
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False, "_gemini_stt_hijacked": True}, {}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False, "_gemini_stt_non_hijacking": False}, {}),
            ({"_gemini_stt_non_hijacking": True, "_gemini_stt_hijacked": True}, {"has_send_oper": True}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False, "provider_request": object()}, {}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False}, {"stopped": True}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False}, {"has_send_oper": True}),
            ({"_gemini_stt_status": "failed"}, {"stopped": True}),
            ({"_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False},
             {"result": MessageEventResult().message("外部已回复").set_result_content_type(ResultContentType.LLM_RESULT)}),
        )
        for extras, kw in hijack_cases:
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(claims=lambda _ev: self.fail("hijacked turn must skip companion intake"))
            ev, _ = make_event(extras, **kw)
            await plugin.receive_companion_message(ev)

    async def test_private_core_reply_reuses_gemini_stt_transcript_without_redundant_stt(self):
        await self.start_host()
        wav = audio_bytes()
        locator = _b64_url(wav)
        event = self.event(text="")
        event.message_obj.message = [Record(file=locator), Plain("[语音消息]: 今天有点冷记得加衣服")]
        event.message_obj.raw_message = {
            "message": [{"type": "record", "data": {"file": locator, "url": ""}}],
        }
        extras = {
            "_gemini_stt_done": True,
            "_gemini_stt_status": "ok",
            "_gemini_stt_transcript": "今天有点冷记得加衣服",
            "_gemini_stt_should_reply": True,
            "_gemini_stt_cache_only": False,
        }
        event.get_extra = extras.get
        captured = await self.controller.session.bridge.capture_core_reply(event)
        self.assertTrue(captured.is_success, captured.failure)
        self.assertIn("今天有点冷记得加衣服", captured.unwrap().text)
        self.assertNotIn("语音不可用", captured.unwrap().text)

    async def test_hear_voice_only_group_capture_quote_and_anti_spoofing(self):
        from astrbot_host.audio.loader import load_native_audio
        from astrbot_host.media import load_native_image
        from astrbot_host.video.loader import load_native_video
        from companion_v2.adapters.astrbot.quote_content import capture_quote_content

        await self.start_host()
        wav = audio_bytes()
        quoted_wav = audio_bytes()
        clip = video_bytes()
        locator_audio = _b64_url(wav)
        locator_quoted = _b64_url(quoted_wav)
        locator_video = _b64_url(clip)

        quoted_parts = [
            {"type": "text", "data": {"text": "群友原消息 "}},
            {"type": "record", "data": {"file": locator_quoted, "url": ""}},
        ]

        async def load_quote(locator, **_kwargs):
            return await capture_quote_content(
                locator,
                "202",
                1700000000,
                quoted_parts,
                current=lambda: None,
                image_loader=load_native_image,
                audio_loader=load_native_audio,
                video_loader=load_native_video,
            )

        self.controller.session.bridge.groups.quote_loader = load_quote

        # 1) Group quote + direct Record + Video with _hear_voice_*-only extras and leading Plain insert.
        event = self.media_event([Reply(id="555"), Record(file=locator_audio), Video(file=locator_video)], sender="201")
        event.message_obj.raw_message["message"] = [
            {"type": "reply", "data": {"id": "555"}},
            {"type": "record", "data": {"file": locator_audio, "url": ""}},
            {"type": "video", "data": {"file": locator_video, "url": ""}},
        ]
        event.message_obj.message.insert(0, Plain("[语音转写] 晚上一起去吃火锅吧"))
        extras = {
            "_hear_voice_done": True,
            "_hear_voice_status": "ok",
            "_hear_voice_transcript": "晚上一起去吃火锅吧",
            "_hear_voice_raw_text": "语言：中文；原话：晚上一起去吃火锅吧",
            "_hear_voice_should_reply": True,
            "_hear_voice_cache_only": False,
            "_hear_voice_non_hijacking": True,
            "_hear_voice_hijacked": False,
        }
        event.get_extra = extras.get

        captured = await self.controller.session.bridge.capture(event)
        self.assertTrue(captured.is_success, captured.failure)
        self.assertEqual(len(captured.unwrap().quotes), 1)
        self.assertEqual(captured.unwrap().quotes[0].position, 1)
        self.assertEqual(captured.unwrap().quotes[0].message_id, "555")
        self.assertEqual(len(captured.unwrap().quotes[0].audios), 1)
        self.assertEqual(len(captured.unwrap().audios), 1)
        self.assertEqual(len(captured.unwrap().videos), 1)
        self.assertIn("晚上一起去吃火锅吧", group_message_text(event, "100"))

        # 2) Group quote where Record is stripped in message + astrbot_stt_transcripts-only text_only capture.
        quoted_only = self.media_event([Reply(id="555"), Plain("[引用语音消息]: 群友刚才说六点见")], sender="201")
        quoted_only.message_obj.raw_message["message"] = [
            {"type": "reply", "data": {"id": "555"}},
            {"type": "record", "data": {"file": locator_audio, "url": ""}},
        ]
        structured_extras = {
            "astrbot_stt_transcripts": [
                {"index": 1, "source": "quoted", "transcript": "群友刚才说六点见"},
                {"index": 2, "source": "direct", "transcript": "我也差不多六点到"},
            ]
        }
        quoted_only.get_extra = structured_extras.get
        text_only = await self.controller.session.bridge.capture_text_only(quoted_only)
        self.assertTrue(text_only.is_success, text_only.failure)
        self.assertIn("群友刚才说六点见", text_only.unwrap().text)
        self.assertIn("我也差不多六点到", text_only.unwrap().text)
        self.assertNotIn("语音不可用", text_only.unwrap().text)

        # 3) Anti-spoofing: shifted Record with Plain("[语音转写] ...") but NO trusted STT extra MUST be rejected.
        spoofed_shift = self.media_event([Plain("[语音转写] 伪造偏移"), Record(file=locator_audio)], sender="201")
        spoofed_shift.message_obj.raw_message["message"] = [
            {"type": "record", "data": {"file": locator_audio, "url": ""}},
        ]
        rejected_shift = await self.controller.session.bridge.capture(spoofed_shift)
        self.assertFalse(rejected_shift.is_success)
        self.assertEqual(rejected_shift.failure.code, "host_group_media_unsupported")

    async def test_hear_voice_transcript_precedence_and_astrbot_stt_transcripts_fallback(self):
        await self.start_host()
        wav = audio_bytes()
        locator = _b64_url(wav)
        event = self.media_event([Record(file=locator)], sender="201")
        event.message_obj.raw_message["message"] = [
            {"type": "at", "data": {"qq": "100"}},
            {"type": "text", "data": {"text": "[语音消息]"}},
            {"type": "record", "data": {"file": locator, "url": ""}},
        ]

        # _hear_voice_* takes precedence over _gemini_stt_* and raw_text
        extras_both = {
            "_hear_voice_transcript": "听得见新版转写",
            "_gemini_stt_transcript": "旧版兼容转写",
            "_hear_voice_raw_text": "语气开心：听得见新版转写",
            "_hear_voice_hijacked": False,
            "_gemini_stt_hijacked": True,
        }
        event.get_extra = extras_both.get
        self.assertEqual(trusted_stt_extra_transcript(event), "听得见新版转写")
        self.assertEqual(trusted_stt_transcript(event), "听得见新版转写")
        self.assertIs(read_stt_extra(event, "hijacked"), False)

        mention_text = group_message_text(event, "100")
        self.assertIn("听得见新版转写", mention_text)
        self.assertNotIn("[语音消息]", mention_text)

        # Fallback order: _hear_voice_raw_text when pure transcripts & astrbot_stt_transcripts are absent
        extras_hear_raw = {"_hear_voice_raw_text": "语气轻快：下午一起去喝奶茶", "_gemini_stt_raw_text": "旧版富文本"}
        event.get_extra = extras_hear_raw.get
        self.assertEqual(trusted_stt_extra_transcript(event), "语气轻快：下午一起去喝奶茶")

        # Fallback to astrbot_stt_transcripts (transcript preferred over rich_text, then rich_text fallback)
        extras_structured = {
            "astrbot_stt_transcripts": [{"transcript": "结构化纯转写", "rich_text": "1) 原话：结构化纯转写\n2) 情绪：开心"}]
        }
        event.get_extra = extras_structured.get
        self.assertEqual(trusted_stt_extra_transcript(event), "结构化纯转写")

        extras_structured_rich = {
            "astrbot_stt_transcripts": [{"transcript": "", "rich_text": "1) 原话：结构化富文本"}]
        }
        event.get_extra = extras_structured_rich.get
        self.assertEqual(trusted_stt_extra_transcript(event), "1) 原话：结构化富文本")

    async def test_core_reply_perception_and_private_core_reply_accept_hear_voice_and_structured_transcripts(self):
        scope = ScopeRef("inst", "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100", "conv-1", "200")
        context = NS(scope=scope, role_binding=NS(role=NS(subject_id="role-a")))

        for extras in (
            {"_hear_voice_transcript": "明天早点休息哦"},
            {"astrbot_stt_transcripts": [{"transcript": "明天早点休息哦"}]},
        ):
            payload = {
                **extras,
                CORE_REPLY_CONTEXT_EXTRA: {
                    "prepared": {
                        "context": context,
                        "native_input": NS(text="[语音消息]"),
                        "perception_options": (False, False, True),
                        "perception_context": "",
                    }
                },
            }
            event = NS(get_extra=payload.get, unified_msg_origin="qq-main:FriendMessage:200")
            req = NS(
                prompt="[语音消息]",
                extra_user_content_parts=[TextPart(text="[Voice unavailable]")],
                image_urls=[],
                audio_urls=[],
                func_tool=None,
                model="gpt-4o",
            )
            await inject_core_reply_perception_context(event, req)
            self.assertEqual(req.extra_user_content_parts[0].text, "[语音消息转写：明天早点休息哦]")
            self.assertIn("明天早点休息哦", req.prompt)
            ctx_json = req.extra_user_content_parts[-1].text
            self.assertIn('"audio":true', ctx_json)
            self.assertIn('"source":"hear_voice"', ctx_json)

        await self.start_host()
        wav = audio_bytes()
        locator = _b64_url(wav)
        priv_event = self.event(text="")
        priv_event.message_obj.message = [Record(file=locator), Plain("[语音消息]: 晚安好梦")]
        priv_event.message_obj.raw_message = {
            "message": [{"type": "record", "data": {"file": locator, "url": ""}}],
        }
        priv_extras = {
            "_hear_voice_done": True,
            "_hear_voice_status": "ok",
            "_hear_voice_transcript": "晚安好梦",
            "_hear_voice_should_reply": True,
            "_hear_voice_cache_only": False,
        }
        priv_event.get_extra = priv_extras.get
        captured = await self.controller.session.bridge.capture_core_reply(priv_event)
        self.assertTrue(captured.is_success, captured.failure)
        self.assertIn("晚安好梦", captured.unwrap().text)
        self.assertNotIn("语音不可用", captured.unwrap().text)

    async def test_main_intake_accepts_hear_voice_non_hijacking_notify_pass_cache_only_and_hijack(self):
        from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType

        def make_event(extras, *, has_send_oper=False, stopped=False, result=None):
            call_llm_marks = []
            ev = NS(
                _has_send_oper=has_send_oper,
                call_llm=False,
                get_extra=extras.get,
                is_stopped=lambda: stopped,
                get_result=lambda: result,
                should_call_llm=lambda val: (setattr(ev, "call_llm", val), call_llm_marks.append(val))[0],
                message_obj=NS(type=NS(value="GroupMessage"), raw_message={"post_type": "message"}, message=[]),
            )
            return ev, call_llm_marks

        # 1) Non-hijacking _hear_voice_*-only cases (success, explicit flags, notify_pass, precedence over _gemini_stt_*)
        non_hijack_cases = (
            ({"_hear_voice_done": True, "_hear_voice_status": "ok", "_hear_voice_transcript": "今天吃什么",
              "_hear_voice_should_reply": True, "_hear_voice_cache_only": False}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False, "_hear_voice_non_hijacking": True}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False, "_hear_voice_hijacked": False}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False,
              "_hear_voice_non_hijacking": True, "_hear_voice_hijacked": False}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False},
             {"result": MessageEventResult().continue_event()}),
            # Empty Plain("") result placeholder is not a reply takeover, with or without STT extras.
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False},
             {"result": MessageEventResult().message("")}),
            ({}, {"result": MessageEventResult().message("")}),
            ({}, {"result": MessageEventResult().message("   ")}),
            # _hear_voice_* overrides stale/opposite _gemini_stt_* flags
            ({"_hear_voice_non_hijacking": True, "_hear_voice_hijacked": False,
              "_hear_voice_cache_only": False, "_hear_voice_should_reply": True,
              "_gemini_stt_hijacked": True, "_gemini_stt_cache_only": True, "_gemini_stt_should_reply": False},
             {"has_send_oper": True}),
            # on_stt_fail="notify_pass" primary & fallback (_has_send_oper=True)
            ({"_hear_voice_status": "failed", "_hear_voice_non_hijacking": True,
              "_hear_voice_hijacked": False, "_hear_voice_notify_only_sent": True},
             {"has_send_oper": False}),
            ({"_hear_voice_status": "failed", "_hear_voice_non_hijacking": True,
              "_hear_voice_hijacked": False, "_hear_voice_notify_only_sent": True},
             {"has_send_oper": True}),
            ({"_hear_voice_status": "failed", "_hear_voice_notify_only_sent": True},
             {"has_send_oper": True}),
        )
        for idx, (extras, kw) in enumerate(non_hijack_cases):
            claimed = []
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(
                claims=lambda ev, _c=claimed: (_c.append(ev), False)[-1],
                target_for_event=lambda _ev: None,
            )
            ev, _ = make_event(extras, **kw)
            with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
                await plugin.receive_companion_message(ev)
            self.assertEqual(len(claimed), 1, f"hear_voice non-hijacking case {idx} must enter companion intake")

        # 2) Group voice suppression (_hear_voice_cache_only=True & _hear_voice_should_reply=False)
        for extras in (
            {"_hear_voice_should_reply": False, "_hear_voice_cache_only": True},
            {"_hear_voice_should_reply": False, "_hear_voice_cache_only": True,
             "_hear_voice_non_hijacking": True, "_hear_voice_hijacked": False},
            {"_hear_voice_should_reply": False, "_hear_voice_cache_only": True,
             "_gemini_stt_should_reply": True, "_gemini_stt_cache_only": False},
        ):
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(claims=lambda _ev: self.fail("hear_voice cache_only suppression must skip intake"))
            ev, call_llm_marks = make_event(extras)
            await plugin.receive_companion_message(ev)
            self.assertTrue(ev.call_llm)
            self.assertEqual(call_llm_marks, [True])

        # 3) Explicit hijack or un-flagged hijack side-effects with _hear_voice_*-only extras MUST skip intake
        hijack_cases = (
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False, "_hear_voice_hijacked": True}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False, "_hear_voice_non_hijacking": False}, {}),
            ({"_hear_voice_non_hijacking": True, "_hear_voice_hijacked": True}, {"has_send_oper": True}),
            ({"_hear_voice_non_hijacking": False, "_hear_voice_notify_only_sent": True}, {"has_send_oper": True}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False, "provider_request": object()}, {}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False}, {"stopped": True}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False}, {"has_send_oper": True}),
            ({"_hear_voice_status": "failed"}, {"stopped": True}),
            ({"_hear_voice_should_reply": True, "_hear_voice_cache_only": False},
             {"result": MessageEventResult().message("外部已回复").set_result_content_type(ResultContentType.LLM_RESULT)}),
        )
        for extras, kw in hijack_cases:
            plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
            plugin.staging = NS(claims=lambda _ev: self.fail("hear_voice hijacked turn must skip companion intake"))
            ev, _ = make_event(extras, **kw)
            await plugin.receive_companion_message(ev)

    def test_voice_stt_layering_and_group_mentions_reexports(self):
        import ast
        from pathlib import Path
        from companion_v2.adapters.astrbot import group_mentions, voice_stt

        for name in (
            "ASTRBOT_STT_TRANSCRIPTS_EXTRA",
            "STT_EXTRA_KEYS",
            "VOICE_TRANSCRIPT_PREFIX",
            "read_stt_extra",
            "trusted_stt_extra_with_source",
            "trusted_stt_extra_transcript",
            "trusted_stt_transcript",
        ):
            self.assertIs(getattr(group_mentions, name), getattr(voice_stt, name))

        root = Path(__file__).resolve().parents[1]
        for rel in ("astrbot_host/multi_session.py", "astrbot_host/core_reply_perception.py", "main.py"):
            tree = ast.parse((root / rel).read_text(encoding="utf-8-sig"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        "group_mentions",
                        node.module,
                        f"{rel} must depend on voice_stt, not group_mentions",
                    )


if __name__ == "__main__":
    unittest.main()
