"""Reproduce short replies and the dispatch order that sends them."""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from companion_v2.conversation.reply_preparation import prepare_reply
from companion_v2.conversation.reply_settings import configuration_fields
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.text_processing import ReplyTextPolicy
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class ReplyPreparationTests(unittest.TestCase):
    def test_live_short_reply_splits_and_has_no_edge_blank_lines(self):
        reply = prepare_reply("\n上课呢…偷偷瞄一眼。怎么啦，这个点找我？\n\n",
                              SegmentationPolicy(), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("上课呢…偷偷瞄一眼。", "怎么啦，这个点找我？"))
        self.assertTrue(reply.source_parts[0].startswith("\n"))

    def test_explicit_split_discards_blank_lines_around_message_boundaries(self):
        marker = "<<PRIVATE_COMPANION_SPLIT>>"
        reply = prepare_reply("\n早安。\n\n" + marker + "\n\n下午好。\n",
                              SegmentationPolicy(enabled=True, llm_controlled=True), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("早安。", "下午好。"))

    def test_blank_edge_cleanup_preserves_indentation_and_internal_paragraphs(self):
        reply = prepare_reply(" \n\n    内容\n\n第二行\n \n", SegmentationPolicy(enabled=False),
                              ReplyTextPolicy(content_cleanup_enabled=False))
        self.assertEqual(reply.parts, ("    内容\n\n第二行",))

    def test_short_sentences_merged_in_one_part_keep_internal_punctuation(self):
        reply = prepare_reply("好。嗯，明白了。", SegmentationPolicy(), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("好。嗯，明白了。",))

    def test_explicit_cleanup_only_removes_the_end_of_each_sent_part(self):
        reply = prepare_reply("好。嗯，明白了。", SegmentationPolicy(),
                              ReplyTextPolicy(content_cleanup_enabled=True,
                                              content_cleanup_scope="trailing",
                                              content_cleanup_words=json.dumps(["。", "，"])))
        self.assertEqual(reply.parts, ("好。嗯，明白了",))

    def test_v1_regex_keeps_zero_width_punctuation_boundaries(self):
        policy = SegmentationPolicy(rule_mode="regex")
        source = "这是第一句话。这是第二句话！"
        self.assertEqual(policy.parts(source), ("这是第一句话。", "这是第二句话！"))
        self.assertEqual("".join(policy.parts(source)), source)


class ReplyDispatchOrderTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rule_split_short_reply_sends_trimmed_text_without_format_prompt(self):
        async def model(envelope):
            return answered("哦？被拆穿了就骂我笨蛋…烛雨大人好过分呢。\n才不生气，反正我说的是真的嘛。")

        self.model_behavior = model
        segmentation = SegmentationPolicy()
        registry = tuple(replace(field, default="fixed")
                         if field.field_id == "conversation.presentation.part_gap_mode" else field
                         for field in configuration_fields(segmentation, True, 0))
        self.registry = type(self.registry)(version=self.registry.version, fields=(
            *self.registry.fields.values(),
            *(field for field in registry if field.field_id not in self.registry.fields),
        ))
        host = await self.start(self.host(segmentation=segmentation))
        batch = host.receive(self.event("short-reply-blank-edge")).unwrap()
        app = host.application
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual([envelope.text for envelope in self.sent], [
            "哦？被拆穿了就骂我笨蛋…", "烛雨大人好过分呢。", "才不生气，反正我说的是真的嘛。",
        ])
        stored = app.runtime.read_all(
            "SELECT text FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch,))
        self.assertEqual([part["text"] for part in stored], [envelope.text for envelope in self.sent])
        self.assertNotIn("消息正文组织", self.called[0].original.instruction)
        self.assertNotIn("日常短消息正文内部不插入空行", self.called[0].original.instruction)

    async def test_new_foreground_work_starts_before_periodic_maintenance(self):
        host = await self.start()
        app = host.application
        events = []
        run, advance = app.runner.run, app.memory_schedule.advance

        async def recording_run(context, task_id):
            events.append("dispatch")
            return await run(context, task_id)

        def recording_advance():
            if app._workers:
                events.append("maintenance")
            return advance()

        with patch.object(app.runner, "run", recording_run), patch.object(app.memory_schedule, "advance", recording_advance):
            batch = host.receive(self.event("foreground-first")).unwrap()
            self.advance(app)
            self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertIn("maintenance", events)
        self.assertEqual(events[0], "dispatch")
