"""Native reaction attachments use owned originals and actual SDK wire formats."""

import json
import unittest

from tests import astrbot_data_root  # Isolate the SDK before importing the host.
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from types import SimpleNamespace as NS

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard, prepare_core_reply_event
from astrbot_host.core_reply_reaction_delivery import (
    capture_core_reaction_intent, observe_core_reaction_response, prepare_core_reaction_message,
)
from astrbot_host.core_reply_tools import inject_core_reply_tools
from companion_v2.execution.models import TaskError, require
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.expression.reply_intent import authored_reply_decision, extract_reply_intent
from companion_v2.foundation.outcome import Outcome
from tests.multi_host_support import MultiFixture
from tests.test_semantic_integration import SemanticPort, animation


class ReplyIntentTests(unittest.TestCase):
    def test_empty_decision_means_no_attachment_without_exposing_metadata(self):
        text = '先把事情说清楚。<pc_reaction_expression>{"intent":""}</pc_reaction_expression>'
        self.assertEqual(extract_reply_intent(text), ("先把事情说清楚。", ""))
        self.assertTrue(authored_reply_decision(text))
        self.assertFalse(authored_reply_decision("普通文字"))
        self.assertFalse(authored_reply_decision('<pc_reaction_expression>{bad}</pc_reaction_expression>'))

    def test_v1_intent_and_escaped_metadata_are_read_without_exposing_protocol(self):
        text = '你好！&lt;pc_reaction_expression&gt;{"purpose":"友好问候","query":"挥手"}&lt;/pc_reaction_expression&gt;'
        self.assertEqual(extract_reply_intent(text), ("你好！", "友好问候；挥手"))
        self.assertEqual(extract_reply_intent('你好<pc_reaction_expression>{bad}</pc_reaction_expression>'), ("你好", ""))
        self.assertEqual(extract_reply_intent('你好<pc_reaction_expression>{"intent":"挥手"'), ("你好", ""))

    def test_reaction_metadata_tolerates_case_spacing_and_typo_variants(self):
        text = '晚安。<PC-reaction-expression>{"intent":"温柔道晚安"}</pcc reaction_expression>'
        self.assertEqual(extract_reply_intent(text), ("晚安。", "温柔道晚安"))


class CoreReplyReactionDeliveryTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.semantic = self.app.semantics
        self.semantic.gateway.port = SemanticPort()
        settings = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_trigger_probability": 1.0, "reactions_cooldown_seconds": 0},
                                      settings["revision"])
        self.queued = []
        self.event_number = 0

    async def prepare(self, *, mode="separate_after", text="你好", enabled=True, has_image=True):
        cfg = self.semantic.store.settings()
        self.semantic.store.configure({"reactions_delivery_mode": mode, "reactions_image_format": "qq_emoji",
                                      "reactions_enabled": enabled}, cfg["revision"])
        self.event_number += 1
        event = self.event(identifier=f"reaction-{mode}-{self.event_number}", text=text)
        event.get_platform_name = lambda: "aiocqhttp"
        prepared = require(await self.controller.prepare_core_reply(event))
        context = prepared["context"]
        self.original = animation()
        if has_image:
            self.semantic.reactions.import_image(self.semantic.ensure_reaction_catalog(context), self.original,
                {"name": "挥手", "description": "友好挥手", "intents": "回应问候"})
        async def prepare_send(current, message, authorize, index, count):
            return await prepare_core_reaction_message(event, message, authorize=authorize,
                                                       segment_index=index, segment_count=count)
        prepare_core_reply_event(event, prepared,
            authorize=lambda current: self.controller.authorize_core_reply(event, current),
            prepare_send=prepare_send, media_submit=lambda work: self.queued.append(work) or True)
        request = NS(system_prompt="当前角色", func_tool=None)
        inject_core_reply_tools(event, request, application=self.app)
        self.request = request
        response = NS(role="assistant", is_chunk=False, tools_call_name=[],
            completion_text='你好！<pc_reaction_expression>{"intent":"挥手回应问候"}</pc_reaction_expression>')
        observe_core_reaction_response(event, response)
        activate_core_reply_delivery_guard(event)
        return event, MessageChain(chain=[Plain(response.completion_text)])

    async def test_original_qq_sticker_is_sent_after_primary_and_usage_tracks_real_return(self):
        event, message = await self.prepare()
        self.assertFalse(event.get_extra("enable_streaming"))
        self.assertIn("<pc_reaction_expression>", self.request.system_prompt)
        retrieval_started_after = []
        search_native = self.semantic.reactions.search_native

        async def observe_search(*args, **kwargs):
            retrieval_started_after.append(len(event.sent))
            return await search_native(*args, **kwargs)

        self.semantic.reactions.search_native = observe_search
        await event.send(message)
        self.assertEqual(len(event.sent), 2)
        self.assertEqual(retrieval_started_after, [1])
        self.assertEqual(event.sent[0].get_plain_text(), "你好！")
        wire = await AiocqhttpMessageEvent._parse_onebot_json(event.sent[1])
        self.assertEqual(wire[0]["type"], "image")
        self.assertEqual(wire[0]["data"]["sub_type"], 1)
        import base64
        self.assertEqual(base64.b64decode(wire[0]["data"]["file"].removeprefix("base64://")), self.original.content)
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 0)
        for work in self.queued:
            work()
        with self.semantic.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uses").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reaction_deliveries").fetchone()[0], 1)
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) n FROM conversation_batches", ())["n"], 0)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_separate_after_reaction_does_not_prepare_before_last_text_segment(self):
        from astrbot_host.core_reply_media_delivery import native_reply_messages

        event, _ = await self.prepare()
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepare_send = core["prepare_send"]
        segments = ("第一句。", "第二句。", "第三句。")
        output = []
        for index, text in enumerate(segments):
            message = MessageChain(chain=[Plain(text)])
            prepared = await prepare_send(core["prepared"], message, self._allow,
                                          index, len(segments))
            output.extend(native_reply_messages(event, prepared))

        self.assertEqual([item.get_plain_text() for item in output], list(segments))
        self.assertEqual(len(output), len(segments))
        self.assertTrue(core["_reaction_reply"]["deferred_after_text"])
        self.assertEqual(core["_reaction_reply"]["reason"], "intent_authored")
        self.assertFalse(core["_reaction_reply"].get("attempted", False))

    async def test_before_and_same_message_delivery_modes_keep_caption_once(self):
        for mode in ("separate_before", "same_message"):
            event, message = await self.prepare(mode=mode)
            await event.send(message)
            self.assertEqual(len(event.sent), 1 if mode == "same_message" else 2)
            self.assertEqual("".join(item.get_plain_text() for item in event.sent), "你好！")
            first_wire = await AiocqhttpMessageEvent._parse_onebot_json(event.sent[0])
            self.assertEqual(first_wire[0]["type"], "image" if mode == "separate_before" else "text")

    async def test_opt_out_and_disabled_feature_preserve_plain_reply(self):
        for enabled, text in ((False, "你好"), (True, "不要发表情包")):
            event, message = await self.prepare(enabled=enabled, text=text)
            await event.send(message)
            self.assertEqual([item.get_plain_text() for item in event.sent], ["你好！"])
            self.assertEqual(self.queued, [])

    async def test_missing_catalog_and_existing_image_preserve_primary_reply(self):
        event, message = await self.prepare(has_image=False)
        await event.send(message)
        self.assertEqual([item.get_plain_text() for item in event.sent], ["你好！"])
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_reaction_reply"]["reason"], "no_matching_image")
        event, message = await self.prepare()
        message.chain.append(Image.fromURL("https://example.invalid/another.png"))
        await event.send(message)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(len(event.sent[0].chain), 2)
        self.assertEqual(self.queued, [])

    async def test_unknown_attachment_send_is_not_counted_or_replayed(self):
        event, message = await self.prepare()
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        sent = []
        async def send(chain):
            sent.append(chain)
            if len(sent) == 2:
                raise RuntimeError("platform outcome unknown")
        core["_original_send"] = send
        with self.assertRaises(RuntimeError):
            await event.send(message)
        self.assertEqual(len(sent), 2)
        self.assertEqual(core["send_unknown"], 1)
        self.assertEqual(self.queued, [])
        again = await prepare_core_reaction_message(event, message, authorize=lambda: self._allow())
        self.assertIs(again, message)
        with self.assertRaises(TaskError):
            await event.send(message)
        self.assertEqual(len(sent), 2)

    async def test_voice_and_sticker_share_the_native_reply_without_duplicate_text(self):
        from astrbot_host.core_reply_voice import prepare_core_reply_voice_message
        event, message = await self.prepare()
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["prepared"]["voice_reply_policy"] = VoiceReplyPolicy(position="after_text")
        async def prepare(current, message, authorize, index, count):
            message = await prepare_core_reaction_message(event, message, authorize=authorize)
            async def render(*_, **__):
                return {"type": "record", "data": {"file": "base64://YQ=="}}
            return await prepare_core_reply_voice_message(event, current, message,
                renderer=render, delivery=None, authorize=authorize)
        core["prepare_send"] = prepare
        await event.send(message)
        self.assertEqual(len(event.sent), 3)
        self.assertEqual([type(chain.chain[0]) for chain in event.sent[:2]], [Plain, Record])
        self.assertEqual("".join(chain.get_plain_text() for chain in event.sent), "你好！")
        self.assertEqual(len(self.queued), 1)
        wire = await AiocqhttpMessageEvent._parse_onebot_json(event.sent[-1])
        self.assertEqual(wire[0]["data"]["sub_type"], 1)

    async def test_an_already_attempted_reaction_keeps_authored_intent_without_reason(self):
        event, _ = await self.prepare()
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["_reaction_reply"]["attempted"] = True
        response = NS(role="assistant", is_chunk=False, tools_call_name=[],
            completion_text='先不要表情。<pc_reaction_expression>{"intent":"这次已经查过了"}</pc_reaction_expression>')
        observe_core_reaction_response(event, response)
        self.assertEqual(response.completion_text, "先不要表情。")

    async def test_ordinary_reply_without_protocol_uses_role_reply_context_and_reviews_original(self):
        event, _ = await self.prepare(text="很难受")
        response = NS(role="assistant", is_chunk=False, tools_call_name=[], completion_text="你好呀，挥手！")
        observe_core_reaction_response(event, response)
        reaction = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_reaction_reply"]
        self.assertEqual(reaction["reason"], "reply_context")
        self.assertIn("角色本轮实际回应：你好呀，挥手！", reaction["intent"])
        self.assertIn("用户原话：很难受", reaction["intent"])
        await event.send(MessageChain(chain=[Plain(response.completion_text)]))
        self.assertEqual(event.sent[0].get_plain_text(), "你好呀，挥手！")
        self.assertEqual(len(event.sent), 2)
        self.assertTrue(self.semantic.gateway.port.analysis_images)

    async def test_explicit_no_image_decision_is_respected_without_context_fallback(self):
        event, _ = await self.prepare()
        response = NS(role="assistant", is_chunk=False, tools_call_name=[],
            completion_text='先说正事。<pc_reaction_expression>{"intent":""}</pc_reaction_expression>')
        observe_core_reaction_response(event, response)
        capture_core_reaction_intent(event, response.completion_text)
        await event.send(MessageChain(chain=[Plain(response.completion_text)]))
        self.assertEqual([part.get_plain_text() for part in event.sent], ["先说正事。"])
        reaction = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_reaction_reply"]
        self.assertEqual(reaction["reason"], "model_declined")
        self.assertFalse(self.semantic.gateway.port.calls)

    async def test_context_fallback_queries_visible_translation_without_voice_protocol(self):
        event, _ = await self.prepare(text="早上好")
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["prepared"]["voice_reply_policy"] = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        response = NS(role="assistant", is_chunk=False, tools_call_name=[],
            completion_text="<pc_tts>おはよう。手を振って。</pc_tts>早上好呀，挥挥手！")
        observe_core_reaction_response(event, response)
        intent = core["_reaction_reply"]["intent"]
        self.assertIn("角色本轮实际回应：早上好呀，挥挥手！", intent)
        self.assertIn("用户原话：早上好", intent)
        self.assertNotIn("pc_tts", intent)
        self.assertNotIn("おはよう", intent)

    async def _allow(self):
        return Outcome.success(True)

    async def test_revoked_permission_never_reaches_platform(self):
        event, message = await self.prepare()
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        async def revoked(_):
            return Outcome.failed("private_access_dialogue_denied", "revoked")
        core["authorize"] = revoked
        with self.assertRaises(TaskError):
            await event.send(message)
        self.assertEqual(event.sent, [])
        self.assertEqual(self.queued, [])
