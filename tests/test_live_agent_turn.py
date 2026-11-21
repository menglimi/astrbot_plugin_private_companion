"""Contracts for Bilibili live replies through AstrBot's programmatic agent API."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests import test_astrbot_staging_host as _host_import_root
from astrbot.api.message_components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot_host.native_extensions.live.agent_turn import (
    is_bili_live_agent_turn, prepare_bili_live_agent_event, run_bili_live_agent_turn,
)
from astrbot_host.native_extensions.live.bilibili_live import LiveDanmakuEvent
from astrbot_host.native_extensions.live.model_methods import LiveModelMethods


class FakeEvent:
    def __init__(self, metadata=None):
        self.extras = {"agent_metadata": metadata or {}}
        self.message_str = "完整的直播提示"
        self.message_obj = NS(message_str=self.message_str, raw_message=self.message_str,
                              message=[Plain(self.message_str)])

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value


class FakeRun:
    def __init__(self, outputs, status):
        self.outputs = outputs
        self.status = status

    async def events(self):
        for event in self.outputs:
            yield event

    async def result(self):
        return NS(status=self.status, text="结果文本不应再次发送")


def output(target, source, text):
    return NS(type="output.ready", payload=NS(target_umo=target, source=source, content=(text,)))


class LiveAgentTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agents_api = ModuleType("astrbot.api.agents")
        self.agents_api.PlainInput = lambda *, text: NS(text=text)
        self.agents_api.TurnStatus = NS(COMPLETED="completed")
        self.agents_api.chain_from_content = lambda content: MessageChain([Plain(content[0])])
        self.module_patch = patch.dict(sys.modules, {"astrbot.api.agents": self.agents_api})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    async def test_run_turn_captures_tool_and_reply_without_automatic_delivery(self):
        run = FakeRun([output("bot:FriendMessage:other", "tool", "工具内容"),
                       NS(type="tool.started", payload=NS()),
                       output("bot:FriendMessage:200", "reply", "最终回复")], "completed")
        context = NS(agent=NS(run_turn=AsyncMock(return_value=run)))
        event = LiveDanmakuEvent("danmaku", "观众", "你好", event_id="event-1")
        source_event = object()

        outputs = await run_bili_live_agent_turn(
            context, umo="bot:FriendMessage:200", conversation_id="conv-1",
            prompt="完整的直播提示", recall_query="B站直播 你好", events=[event],
            source_event=source_event)

        kwargs = context.agent.run_turn.await_args.kwargs
        self.assertEqual(kwargs["umo"], "bot:FriendMessage:200")
        self.assertEqual(kwargs["session_id"], "conv-1")
        self.assertFalse(kwargs["send_to_user"])
        self.assertIs(kwargs["source_event"], source_event)
        self.assertEqual(kwargs["input_message"][0].text, "完整的直播提示")
        self.assertEqual(kwargs["metadata"]["companion"]["recall_query"], "B站直播 你好")
        self.assertEqual(kwargs["metadata"]["companion"]["events"][0]["event_id"], "event-1")
        self.assertEqual([(target, source, chain.chain[0].text) for target, source, chain in outputs], [
            ("bot:FriendMessage:other", "tool", "工具内容"),
            ("bot:FriendMessage:200", "reply", "最终回复"),
        ])

    async def test_failed_turn_does_not_release_prepared_outputs(self):
        context = NS(agent=NS(run_turn=AsyncMock(return_value=FakeRun([
            output("bot:FriendMessage:200", "reply", "未完成")], "failed"))))
        result = await run_bili_live_agent_turn(
            context, umo="bot:FriendMessage:200", conversation_id="conv-1",
            prompt="直播提示", recall_query="弹幕", events=[], source_event=object())
        self.assertIsNone(result)

    def test_metadata_marks_wakeup_and_separates_recall_from_model_input(self):
        event = FakeEvent({"companion": {"kind": "bili_live_auto_reply",
                                         "recall_query": "B站直播 观众甲",
                                         "events": [{"type": "danmaku"}]}})
        self.assertTrue(is_bili_live_agent_turn(event))
        prepare_bili_live_agent_event(event)
        self.assertEqual(event.message_str, "B站直播 观众甲")
        self.assertEqual(event.message_obj.message[0].text, "B站直播 观众甲")
        self.assertTrue(event.get_extra("bili_live_auto_reply"))
        self.assertEqual(event.get_extra("bili_live_events"), [{"type": "danmaku"}])
        ordinary = FakeEvent()
        prepare_bili_live_agent_event(ordinary)
        self.assertEqual(ordinary.message_str, "完整的直播提示")

    async def test_live_delivery_sends_each_output_once(self):
        session = "bot:FriendMessage:200"
        other = "bot:FriendMessage:other"
        context = NS(agent=NS(run_turn=Mock()),
                     conversation_manager=NS(get_curr_conversation_id=AsyncMock(return_value="conv-1"),
                                             new_conversation=AsyncMock(), get_conversation=AsyncMock()),
                     send_message=AsyncMock())
        live = NS(
            context=context, config={"bili_live_auto_reply_force_full_tts": False},
            _bili_reply_event_template=None,
            _safe_parse_int=lambda value, default: default if value is None else int(value),
            _select_bili_reply_events=lambda events, limit: events[-limit:],
            _format_bili_events=lambda events: "观众：你好",
            _bili_live_reply_identity_prompt_line=lambda: "我是主播",
            _build_bili_support_reply_hint=lambda events: "",
            _build_bili_live_auxiliary_context=AsyncMock(return_value=""),
            _build_bili_live_memory_recall_query=lambda events: "B站直播 你好",
            _plain_chain_text=lambda chain: "".join(part.text for part in chain if isinstance(part, Plain)),
            _clean_auto_reply_text=lambda text: text.strip(),
            _ensure_bili_support_acknowledgement=lambda text, events: text,
            _decorate_bili_live_reply_chain=AsyncMock(side_effect=lambda target, chain, **kwargs: chain),
            _strip_tts_blocks_from_plain_chain=lambda chain: chain,
            _ensure_visible_text_after_voice=lambda chain, text: chain,
            _bili_live_auto_reply_sync_tts_subtitle=lambda: False,
            _push_subtitle=AsyncMock(),
            _record_bili_auto_reply_sent=Mock(),
            _record_bili_auto_reply_rate_mark=Mock(),
        )
        event = LiveDanmakuEvent("danmaku", "观众", "你好")
        with patch("astrbot_host.native_extensions.live.model_methods.run_bili_live_agent_turn",
                   new=AsyncMock(return_value=[
                       (other, "tool", MessageChain([Plain("工具内容")])),
                       (session, "reply", MessageChain([Plain("最终回复")])),
                   ])) as run_turn, patch(
                       "astrbot_host.native_extensions.live.model_methods.live_private_persona_prompt",
                       new=AsyncMock(return_value="")):
            handled = await LiveModelMethods._reply_to_bili_live_events_via_framework(live, [event], session)

        self.assertTrue(handled)
        self.assertEqual(context.send_message.await_count, 2)
        self.assertEqual([(call.args[0], call.args[1].chain[0].text)
                          for call in context.send_message.await_args_list],
                         [(other, "工具内容"), (session, "最终回复")])
        context.conversation_manager.get_conversation.assert_not_awaited()
        self.assertEqual(run_turn.await_args.kwargs["conversation_id"], "conv-1")
        self.assertEqual(run_turn.await_args.kwargs["source_event"].unified_msg_origin, session)
        live._record_bili_auto_reply_sent.assert_called_once_with([event], "最终回复")
        live._push_subtitle.assert_awaited_once_with("最终回复", source="bili_live")

        context.send_message.reset_mock()
        with patch("astrbot_host.native_extensions.live.model_methods.run_bili_live_agent_turn",
                   new=AsyncMock(return_value=[(other, "tool", MessageChain([Plain("仅其他会话")]))])), patch(
                       "astrbot_host.native_extensions.live.model_methods.live_private_persona_prompt",
                       new=AsyncMock(return_value="")):
            handled = await LiveModelMethods._reply_to_bili_live_events_via_framework(live, [event], session)
        self.assertFalse(handled)
        context.send_message.assert_awaited_once()

        context.send_message.reset_mock()
        with patch("astrbot_host.native_extensions.live.model_methods.run_bili_live_agent_turn",
                   new=AsyncMock(return_value=[
                       (session, "reply", MessageChain([
                           Image.fromURL("https://example.invalid/live.png"), Plain("图片之后的回复")]))
                   ])), patch(
                       "astrbot_host.native_extensions.live.model_methods.live_private_persona_prompt",
                       new=AsyncMock(return_value="")):
            handled = await LiveModelMethods._reply_to_bili_live_events_via_framework(live, [event], session)
        self.assertTrue(handled)
        delivered = context.send_message.await_args.args[1].chain
        self.assertIsInstance(delivered[0], Image)
        self.assertEqual(delivered[1].text, "图片之后的回复")

    async def test_older_host_uses_legacy_builder(self):
        session = "bot:FriendMessage:200"
        context = NS(
            conversation_manager=NS(get_curr_conversation_id=AsyncMock(return_value="conv-1"),
                                    new_conversation=AsyncMock(),
                                    get_conversation=AsyncMock(return_value=NS(cid="conv-1"))),
            get_config=Mock(return_value={"provider_settings": {}}),
            send_message=AsyncMock(),
        )
        live = NS(
            context=context, config={"bili_live_auto_reply_force_full_tts": False},
            _bili_reply_event_template=None,
            _safe_parse_int=lambda value, default: default if value is None else int(value),
            _select_bili_reply_events=lambda events, limit: events[-limit:],
            _format_bili_events=lambda events: "观众：你好",
            _bili_live_reply_identity_prompt_line=lambda: "我是主播",
            _build_bili_support_reply_hint=lambda events: "",
            _build_bili_live_auxiliary_context=AsyncMock(return_value=""),
            _build_bili_live_memory_recall_query=lambda events: "B站直播 你好",
            _plain_chain_text=lambda chain: "".join(part.text for part in chain if isinstance(part, Plain)),
            _clean_auto_reply_text=lambda text: text.strip(),
            _ensure_bili_support_acknowledgement=lambda text, events: text,
            _decorate_bili_live_reply_chain=AsyncMock(side_effect=lambda target, chain, **kwargs: chain),
            _strip_tts_blocks_from_plain_chain=lambda chain: chain,
            _ensure_visible_text_after_voice=lambda chain, text: chain,
            _bili_live_auto_reply_sync_tts_subtitle=lambda: False,
            _push_subtitle=AsyncMock(),
            _record_bili_auto_reply_sent=Mock(),
        )
        class Runner:
            async def step_until_done(self, limit):
                self.limit = limit
                yield None

            def get_final_llm_resp(self):
                return NS(role="assistant", completion_text="旧接口回复")

        runner = Runner()
        event = LiveDanmakuEvent("danmaku", "观众", "你好")
        with patch("astrbot_host.native_extensions.live.model_methods.build_main_agent",
                   new=AsyncMock(return_value=NS(agent_runner=runner))) as builder, patch(
                       "astrbot_host.native_extensions.live.model_methods.live_private_persona_prompt",
                       new=AsyncMock(return_value="")):
            handled = await LiveModelMethods._reply_to_bili_live_events_via_framework(live, [event], session)
        self.assertTrue(handled)
        self.assertEqual(runner.limit, 8)
        self.assertEqual(builder.await_args.kwargs["req"].conversation.cid, "conv-1")
        self.assertEqual(context.send_message.await_args.args[1].chain[0].text, "旧接口回复")


if __name__ == "__main__":
    unittest.main()
