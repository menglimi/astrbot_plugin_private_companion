"""Queued confirmations retain the reply that had actually been sent at arrival."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest

from tests.multi_host_support import MultiFixture
from astrbot.core.agent.message import Message, dump_messages_with_checkpoints
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_perception import (
    _compact_private_reply_context, inject_core_reply_perception_context,
    inject_core_reply_perception_policy,
)
from companion_v2.conversation.private_reply_context import (
    capture_private_reply_context, merged_input_sequence,
)
from companion_v2.execution.models import TaskError, stamp


class PrivateReplyContextTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.observer = self.controller.session.core_reply_observations

    async def flush(self):
        while pending := self.observer._pending - self.observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))

    async def prepare(self, text, identifier, **kwargs):
        event = self.event(text=text, identifier=identifier, **kwargs)
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        await self.flush()
        return event, prepared

    async def sent(self, prepared, text):
        await self.observer.output(prepared, text, state="send_returned")
        await self.flush()

    def capture(self, prepared, **kwargs):
        return capture_private_reply_context(self.app.messages, prepared["context"],
            prepared["native_input"], at=self.clock.now(), **kwargs)

    async def test_two_confirmations_both_follow_the_first_reply(self):
        _, first = await self.prepare("来张自拍看看", "selfie-first")
        self.clock.advance(timedelta(seconds=10))
        await self.sent(first, "我头发还乱着，现在只穿着家居服哦。")
        self.clock.advance(timedelta(seconds=10))
        second_event, second = await self.prepare("看看", "selfie-second")
        self.clock.advance(timedelta(seconds=5))
        await self.sent(first, "你确定要看？")
        self.clock.advance(timedelta(seconds=17))
        third_event, third = await self.prepare("当然", "selfie-third")

        second_context = self.capture(second)
        third_context = self.capture(third)
        self.assertEqual(second_context["last_sent_reply_turn_id"], first["turn_id"])
        self.assertEqual(third_context["last_sent_reply_turn_id"], first["turn_id"])
        self.assertNotIn("你确定要看？", json.dumps(second_context["before_input"], ensure_ascii=False))
        self.assertIn("你确定要看？", json.dumps(second_context["after_input"], ensure_ascii=False))
        self.assertIn("你确定要看？", json.dumps(third_context["before_input"], ensure_ascii=False))
        self.assertEqual([item["text"] for item in third_context["before_input"]
                          if item["speaker"] == "user"], ["来张自拍看看", "看看"])
        # A later-arriving user input belongs to its own request, not an earlier
        # turn's temporary progress material.
        self.assertNotIn("当然", json.dumps(second_context, ensure_ascii=False))

        self.clock.advance(timedelta(seconds=11))
        await self.sent(second, "与其看照片，你自己投影过来看看我本人嘛。")
        queued_context = self.capture(third)
        self.assertEqual(queued_context["last_sent_reply_turn_id"], first["turn_id"])
        self.assertNotIn("投影", json.dumps(queued_context["before_input"], ensure_ascii=False))
        self.assertIn("投影", json.dumps(queued_context["after_input"], ensure_ascii=False))
        self.assertEqual(queued_context["current_inputs"][0]["text"], "当然")
        self.assertEqual(queued_context["input_received_at"], stamp(third["native_input"].at))
        merged = self.capture(third, merged_inputs=merged_input_sequence([
            (third_event, "当然"), (second_event, "看看")]))
        self.assertEqual([item["text"] for item in merged["current_inputs"]], ["看看", "当然"])
        self.assertEqual(merged["input_received_at"], stamp(second["native_input"].at))

    async def test_one_segmented_turn_uses_one_history_slot(self):
        _, first = await self.prepare("开始", "segmented-first")
        self.clock.advance(timedelta(seconds=1))
        for index in range(25):
            await self.sent(first, f"气泡{index}")
        self.clock.advance(timedelta(seconds=1))
        _, second = await self.prepare("继续", "segmented-second")

        before = self.capture(second)["before_input"]
        self.assertEqual([item["text"] for item in before if item["speaker"] == "user"], ["开始"])
        assistant = [item for item in before if item["speaker"] == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["text"].splitlines(), [f"气泡{index}" for index in range(25)])

        await self.sent(second, "气泡24")
        self.clock.advance(timedelta(seconds=1))
        _, third = await self.prepare("还有呢", "segmented-third")
        assistant = [item for item in self.capture(third)["before_input"]
                     if item["speaker"] == "assistant"]
        self.assertEqual(len(assistant), 2)
        self.assertNotEqual(assistant[0]["turn_id"], assistant[1]["turn_id"])
        self.assertEqual(assistant[1]["text"], "气泡24")

    async def test_drafts_stay_out_and_provider_context_is_temporary_and_refreshed(self):
        _, first = await self.prepare("来张自拍看看", "draft-first")
        self.clock.advance(timedelta(seconds=5))
        await self.sent(first, "头发还有点乱。")
        self.clock.advance(timedelta(seconds=5))
        event, current = await self.prepare("看看", "draft-current")
        await self.observer.output(first, "这是未发出的投影邀请。", state="output_observed")
        await self.flush()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": current})
        request = NS(prompt="看看", system_prompt="原人格", extra_user_content_parts=[])
        inject_core_reply_perception_policy(event, request)
        system = request.system_prompt
        await inject_core_reply_perception_context(event, request, application=self.app)
        first_payload = request.extra_user_content_parts[-1].text
        self.assertIn("private_reply_context", first_payload)
        self.assertNotIn("未发出的投影", first_payload)
        self.assertIn("头发还有点乱", first_payload)
        self.clock.advance(timedelta(seconds=5))
        await self.sent(first, "你确定要看？")
        await inject_core_reply_perception_context(event, request, application=self.app)
        self.assertEqual(len(request.extra_user_content_parts), 1)
        payload = json.loads(request.extra_user_content_parts[-1].text.split("\n", 1)[1]
                             .removesuffix("\n</companion_v2_context>"))
        self.assertEqual(payload["private_reply_context"]["after_input"][0]["text"], "你确定要看？")
        self.assertEqual(request.system_prompt, system)
        self.assertEqual(request.prompt, "看看")
        message = Message(role="user", content=request.extra_user_content_parts)
        self.assertEqual(dump_messages_with_checkpoints([message])[0]["content"], [])
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(event.sent, [])

    async def test_confirmed_contact_bubbles_follow_older_native_replies_in_provider_context(self):
        _, old = await self.prepare("下次聊", "before-contact")
        self.clock.advance(timedelta(seconds=1))
        await self.sent(old, "先去上课吧。")
        self.clock.advance(timedelta(seconds=1))
        first_at = stamp(self.clock.now())
        self.clock.advance(timedelta(seconds=1))
        second_at = stamp(self.clock.now())
        self.clock.advance(timedelta(seconds=1))
        event, current = await self.prepare("这让我有点担心。", "after-contact")
        bubbles = [(first_at, "看到窗台上的杯子了。"), (second_at, "它快被风吹落了。")]
        materials = [{"kind": "confirmed_background_message", "content": {
            "owner": "proactive.contact", "ref": "sent_1", "delivered_at": at, "text": text,
            "part": {"sequence": sequence, "part_count": 2},
        }} for sequence, (at, text) in enumerate(bubbles, 1)]
        current.update(prompt_instruction="主动消息", prompt_context_instruction="主动消息",
                       prompt_context_blocks=({"title": "主动消息", "materials": materials},))
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": current})
        request = NS(prompt="这让我有点担心。", extra_user_content_parts=[],
                     contexts=[{"role": "assistant", "content": "先去上课吧。"}])
        await inject_core_reply_perception_context(event, request, application=self.app)

        payload = json.loads(request.extra_user_content_parts[-1].text.split("\n", 1)[1]
                             .removesuffix("\n</companion_v2_context>"))
        timeline = payload["private_reply_context"]
        native, first, second = [item for item in timeline["before_input"] if item["speaker"] == "assistant"]
        self.assertTrue(native["text_in_host_history"])
        self.assertEqual([(item["at"], item["text"]) for item in (first, second)], bubbles)
        self.assertEqual([item["part"]["sequence"] for item in (first, second)], [1, 2])
        self.assertEqual(first["turn_id"], second["turn_id"])
        self.assertEqual(timeline["last_sent_reply_turn_id"], second["turn_id"])
        for material in payload["companion_background"]["sections"][0]["materials"]:
            self.assertTrue(material["content"]["text_in_private_reply_context"])
            self.assertNotIn("text", material["content"])

    async def test_other_window_and_unauthorized_history_are_not_injected(self):
        _, other = await self.prepare("另一个窗口的请求", "other-window", target="second")
        self.clock.advance(timedelta(seconds=1))
        await self.sent(other, "另一个窗口的私密回复。")
        self.clock.advance(timedelta(seconds=1))
        _, current = await self.prepare("当然", "own-window")
        material = self.capture(current)
        self.assertEqual(material["before_input"], [])
        self.assertEqual(material["after_input"], [])
        self.assertIsNone(material["last_sent_reply_turn_id"])
        from unittest.mock import Mock, patch
        from companion_v2.foundation.outcome import Outcome
        with patch.object(self.app.messages.tasks, "authorize_context", return_value=Outcome.failed(
                "access_denied", "test access revoked")), patch(
                "companion_v2.conversation.private_reply_context.dialogue", Mock()) as history:
            with self.assertRaises(TaskError):
                self.capture(current)
            history.assert_not_called()


class MergedInputSequenceTests(unittest.TestCase):
    def test_original_arrivals_survive_merge_in_order(self):
        entries = [(NS(created_at=20.0), "当然"), (NS(created_at=10.0), "看看")]
        result = merged_input_sequence(entries)
        self.assertEqual([item["text"] for item in result], ["看看", "当然"])
        self.assertLess(result[0]["received_at"], result[1]["received_at"])
        self.assertEqual(merged_input_sequence([(NS(created_at=float("nan")), "bad")]), [])

    def test_history_compaction_preserves_distinct_equal_turns(self):
        context = {"before_input": [
            {"speaker": "assistant", "text": "好啊", "at": "first", "turn_id": "long-turn-1"},
            {"speaker": "assistant", "text": "好啊", "at": "second", "turn_id": "long-turn-2"},
        ], "after_input": [], "last_sent_reply_turn_id": "long-turn-2"}
        partial = _compact_private_reply_context(context, NS(contexts=[
            {"role": "assistant", "content": "好啊"},
        ]))
        self.assertEqual([entry["text"] for entry in partial["before_input"]], ["好啊", "好啊"])
        self.assertNotEqual(partial["before_input"][0]["turn_id"],
                            partial["before_input"][1]["turn_id"])
        self.assertNotIn("long-turn", json.dumps(partial))

        complete = _compact_private_reply_context(context, NS(contexts=[
            {"role": "assistant", "content": "好啊"},
            {"role": "assistant", "content": "好啊"},
        ]))
        self.assertTrue(all(entry["text_in_host_history"] for entry in complete["before_input"]))
        self.assertTrue(all("text" not in entry for entry in complete["before_input"]))
        self.assertEqual(complete["last_sent_reply_turn_id"], complete["before_input"][1]["turn_id"])

    def test_current_input_uses_exact_current_prompt_once(self):
        context = {"before_input": [], "after_input": [], "current_inputs": [
            {"text": "好啊", "received_at": "today"}], "last_sent_reply_turn_id": None}
        exact = _compact_private_reply_context(context, NS(contexts=[], prompt="好啊"))
        self.assertEqual(exact["current_inputs"], [
            {"received_at": "today", "text_in_current_prompt": True}])
        changed = _compact_private_reply_context(context, NS(contexts=[], prompt="好"))
        self.assertEqual(changed["current_inputs"][0]["text"], "好啊")


if __name__ == "__main__":
    unittest.main()
