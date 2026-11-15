"""The AstrBot core path applies the captured group outbound policy."""

from types import SimpleNamespace as NS
import unittest

from astrbot.core.message.components import At, Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain

from astrbot_host.core_reply_outbound import prepare_core_reply_outbound_message
from astrbot_host.core_reply_idempotency import remember_native_tool_message, should_drop_native_tool_response
from companion_v2.adapters.astrbot.native import NativeConversationInput


class FakeEvent:
    def __init__(self, first_text_segment=0):
        self.extra = {"_companion_v2_core_reply_context": {
            "outbound_first_text_segment": first_text_segment}}

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)


def prepared(*, channel="group", event_id="7001", speaker="200", settings=None):
    captured = NativeConversationInput(
        event_id, NS(native_account_id=speaker), None, None, "trigger", None, None, "",
    )
    return {"channel": channel, "event_id": event_id, "native_input": captured,
            "outbound_settings": settings or {}}


class CoreReplyOutboundTests(unittest.TestCase):
    def test_native_tool_dedupe_keeps_new_outbound_quote_and_mention(self):
        event = FakeEvent()
        state = event.extra["_companion_v2_core_reply_context"]
        state.update(prepared=prepared(settings={"reply_quote_enabled": True,
                                                 "reply_mention_speaker_enabled": True}),
                     send_unknown=0, send_failed=0)
        body = MessageChain(chain=[Plain("我也来说一句。")])
        remember_native_tool_message(event, body)

        self.assertFalse(should_drop_native_tool_response(event, body))
        decorated = MessageChain(chain=[Reply(id="7001"), At(qq="200"), Plain("我也来说一句。")])
        remember_native_tool_message(event, decorated)
        self.assertTrue(should_drop_native_tool_response(event, body))

    def test_group_quote_and_mention_use_the_captured_native_message(self):
        event = FakeEvent()
        message = MessageChain(chain=[Plain("我也来说一句。")])
        result = prepare_core_reply_outbound_message(
            event, prepared(settings={"reply_quote_enabled": True,
                                      "reply_mention_speaker_enabled": True}), message)
        self.assertEqual([type(part) for part in result.chain], [Reply, At, Plain])
        self.assertEqual(result.chain[0].id, "7001")
        self.assertEqual(str(result.chain[1].qq), "200")
        self.assertEqual(result.chain[2].text, "我也来说一句。")

    def test_existing_reply_and_mention_are_not_duplicated(self):
        message = MessageChain(chain=[Reply(id="6999"), At(qq="200"), Plain("接着说。")])
        result = prepare_core_reply_outbound_message(
            FakeEvent(), prepared(settings={"reply_quote_enabled": True,
                                            "reply_mention_speaker_enabled": True}), message)
        self.assertIs(result, message)

    def test_short_reply_first_part_and_private_scope_rules_are_respected(self):
        settings = {"reply_quote_enabled": True, "reply_quote_skip_chars": 8}
        short = MessageChain(chain=[Plain("好呀")])
        self.assertEqual(prepare_core_reply_outbound_message(
            FakeEvent(), prepared(settings=settings), short).chain, short.chain)

        later = MessageChain(chain=[Plain("后面补充。")])
        self.assertEqual(prepare_core_reply_outbound_message(
            FakeEvent(first_text_segment=0), prepared(settings={"reply_quote_enabled": True}),
            later, segment_index=1).chain, later.chain)
        every = prepare_core_reply_outbound_message(
            FakeEvent(first_text_segment=0), prepared(settings={"reply_quote_enabled": True,
                                                                  "reply_quote_first_part_only": False}),
            later, segment_index=1)
        self.assertIsInstance(every.chain[0], Reply)

        private = MessageChain(chain=[Plain("私聊不应附群引用。")])
        self.assertIs(prepare_core_reply_outbound_message(
            FakeEvent(), prepared(channel="private", settings=settings), private), private)

    def test_component_order_reorders_native_voice_and_mentions(self):
        message = MessageChain(chain=[Image(file="base64://image"), Plain("你好。"),
                                      Record(file="base64://voice")])
        result = prepare_core_reply_outbound_message(
            FakeEvent(), prepared(settings={"reply_mention_speaker_enabled": True,
                                            "component_order": '["voice", "at", "text", "image"]'}),
            message)
        self.assertEqual([type(part) for part in result.chain], [Record, At, Plain, Image])

    def test_image_position_applies_without_an_explicit_component_order(self):
        message = MessageChain(chain=[Plain("图片在文字前。"), Image(file="base64://image")])
        result = prepare_core_reply_outbound_message(
            FakeEvent(), prepared(channel="private", settings={"image_position": "before_text"}),
            message)
        self.assertEqual([type(part) for part in result.chain], [Image, Plain])

        explicit = prepare_core_reply_outbound_message(
            FakeEvent(), prepared(channel="private", settings={
                "image_position": "before_text", "component_order": '["text", "image"]'}),
            message)
        self.assertEqual([type(part) for part in explicit.chain], [Plain, Image])


if __name__ == "__main__":
    unittest.main()
