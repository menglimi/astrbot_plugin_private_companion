"""QQ payload grouping: a quote/@ header must ride with the body it decorates."""

import asyncio
import base64
import io
import unittest
import wave

from tests import astrbot_data_root  # Isolate the SDK before importing the host.
from astrbot.core.message.components import At, Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

from astrbot_host.core_reply_media_delivery import native_reply_messages


class _Event:
    def __init__(self, platform="aiocqhttp"):
        self._platform = platform

    def get_platform_name(self):
        return self._platform


def silent_wav():
    """A real 0.1 s silent WAV; the adapter base64-encodes Record segments."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 800)
    return "base64://" + base64.b64encode(buffer.getvalue()).decode()


def wire(group):
    """The OneBot array an aiocqhttp send turns this payload into."""
    return asyncio.run(AiocqhttpMessageEvent._parse_onebot_json(group))


def kinds(groups):
    return [[type(part) for part in group.chain] for group in groups]


class NativeReplyMessagesTests(unittest.TestCase):
    def test_record_is_split_but_the_quote_stays_with_its_text(self):
        chain = MessageChain(chain=[
            Reply(id="4601"), Record(file=silent_wav(), text="またテスト？"),
            Plain("又来test？从刚才起就反复测个没完"),
        ])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Record], [Reply, Plain]])

    def test_a_dangling_header_is_never_sent_as_its_own_message(self):
        chain = MessageChain(chain=[Reply(id="4601"), At(qq="1538054427"),
                                    Record(file=silent_wav())])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Reply, At, Record]])

    def test_an_at_header_rides_with_the_text_it_decorates(self):
        chain = MessageChain(chain=[At(qq="1538054427"), Record(file=silent_wav()),
                                    Plain("你好")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Record], [At, Plain]])

    def test_the_quote_lands_on_the_text_behind_several_voice_chunks(self):
        chain = MessageChain(chain=[Reply(id="4601"), Record(file=silent_wav(), text="第一句。"),
                                    Record(file=silent_wav(), text="第二句。"), Plain("第一句。第二句。")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Record], [Record], [Reply, Plain]])

    def test_non_qq_platforms_keep_one_payload(self):
        chain = MessageChain(chain=[Reply(id="4601"), Record(file=silent_wav()), Plain("你好")])
        groups = native_reply_messages(_Event("telegram"), chain)

        self.assertEqual(kinds(groups), [[Reply, Record, Plain]])

    def test_qq_official_text_and_voice_are_separate_messages(self):
        chain = MessageChain(chain=[Plain("你好"), Record(file=silent_wav())])
        groups = native_reply_messages(_Event("qq_official"), chain)

        self.assertEqual(kinds(groups), [[Plain], [Record]])

    def test_image_attachment_still_splits_from_the_caption(self):
        chain = MessageChain(chain=[Plain("你好"), Image.fromURL("https://example.invalid/a.png")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Plain, Image]])

    def test_blank_text_payloads_are_dropped_instead_of_sent_empty(self):
        chain = MessageChain(chain=[Reply(id="4601"), Record(file=silent_wav()), Plain("  ")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Reply, Record]])

    def test_the_quote_reaches_the_wire_inside_the_text_message(self):
        chain = MessageChain(chain=[Record(file=silent_wav()), Reply(id="4601"), Plain("你好")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual(kinds(groups), [[Record], [Reply, Plain]])
        message = wire(groups[-1])
        self.assertEqual([item["type"] for item in message], ["reply", "text"])
        self.assertEqual(message[0]["data"]["id"], "4601")
        self.assertEqual(message[1]["data"]["text"], "你好")

    def test_no_payload_reaches_the_wire_as_an_empty_message(self):
        chain = MessageChain(chain=[Reply(id="4601"), Record(file=silent_wav(), text="またテスト？"),
                                    Plain("又来test？从刚才起就反复测个没完")])
        groups = native_reply_messages(_Event(), chain)

        self.assertTrue(groups)
        for group in groups:
            body = [item for item in wire(group) if item["type"] not in {"reply", "at"}]
            self.assertTrue(body, f"{kinds([group])} would arrive as an empty message")

    def test_expression_markers_never_reach_the_wire(self):
        chain = MessageChain(chain=[Plain("&&angry&&我没事&&sad&&")])
        groups = native_reply_messages(_Event(), chain)

        self.assertEqual([item["data"]["text"] for item in wire(groups[0]) if item["type"] == "text"], ["我没事"])

    def test_a_message_with_nothing_to_render_is_not_sent_at_all(self):
        for chain in ([Reply(id="4601")], [Reply(id="4601"), At(qq="1538054427")], [Plain("   ")]):
            with self.subTest(chain=[type(part).__name__ for part in chain]):
                self.assertEqual(native_reply_messages(_Event(), MessageChain(chain=chain)), ())


class QqVoiceTextSendPacingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_event(platform="aiocqhttp"):
        from astrbot_host.core_reply import (
            activate_core_reply_delivery_guard,
            prepare_core_reply_event,
        )
        from companion_v2.foundation.outcome import Outcome

        sent = []

        class FakeQqEvent:
            def __init__(self):
                self._platform = platform
                self._extra = {}

            def get_platform_name(self):
                return self._platform

            def get_extra(self, key, default=None):
                return self._extra.get(key, default)

            def set_extra(self, key, value):
                self._extra[key] = value

            async def send(self, message):
                sent.append(message)
                return True

        async def _authorize(_prepared):
            return Outcome.success(True)

        event = FakeQqEvent()
        prepare_core_reply_event(event, {"event_id": "evt-pace-1"}, authorize=_authorize)
        activate_core_reply_delivery_guard(event)
        return event, sent

    async def test_qq_split_record_and_plain_inserts_brief_inter_part_pace(self):
        from unittest.mock import AsyncMock, patch
        from astrbot_host.core_reply import QQ_RECORD_PART_PACE_SECONDS

        self.assertGreaterEqual(QQ_RECORD_PART_PACE_SECONDS, 0.6)
        self.assertLessEqual(QQ_RECORD_PART_PACE_SECONDS, 1.0)

        event, sent = self._make_event("aiocqhttp")
        chain = MessageChain(chain=[Record(file=silent_wav(), text="你好"), Plain("你好")])
        now = [100.0]

        with patch("astrbot_host.core_reply.time.monotonic", side_effect=lambda: now[0]), \
                patch("astrbot_host.core_reply.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await event.send(chain)

        self.assertEqual(len(sent), 2)
        sleep_mock.assert_awaited_once()
        self.assertAlmostEqual(sleep_mock.await_args.args[0], QQ_RECORD_PART_PACE_SECONDS, places=6)

    async def test_qq_separate_event_send_calls_pace_record_to_plain_and_skip_plain_to_plain(self):
        from unittest.mock import AsyncMock, patch
        from astrbot_host.core_reply import QQ_RECORD_PART_PACE_SECONDS

        # Two separate event.send() calls (AstrBot RespondStage path: [Record] then [Plain]).
        event, sent = self._make_event("aiocqhttp")
        now = [200.0]
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with patch("astrbot_host.core_reply.time.monotonic", side_effect=lambda: now[0]), \
                patch("astrbot_host.core_reply.asyncio.sleep", side_effect=fake_sleep):
            await event.send(MessageChain(chain=[Record(file=silent_wav(), text="第一句")]))
            # 0.25s elapsed before second send -> only remaining pace is awaited.
            now[0] += 0.25
            await event.send(MessageChain(chain=[Plain("第一句")]))
            # Subsequent Plain -> Plain has 0ms delay (no sleep).
            await event.send(MessageChain(chain=[Plain("第二句")]))
            # If >= pace already elapsed before a later Record, no redundant sleep occurs.
            now[0] += QQ_RECORD_PART_PACE_SECONDS + 0.1
            await event.send(MessageChain(chain=[Record(file=silent_wav(), text="尾声")]))
            # Immediate Plain right after Record waits the full pace.
            await event.send(MessageChain(chain=[Plain("尾声")]))

        self.assertEqual(len(sent), 5)
        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(sleeps[0], QQ_RECORD_PART_PACE_SECONDS - 0.25, places=6)
        self.assertAlmostEqual(sleeps[1], QQ_RECORD_PART_PACE_SECONDS, places=6)

        # Pure Plain -> Plain on QQ never sleeps.
        event_plain, sent_plain = self._make_event("aiocqhttp")
        with patch("astrbot_host.core_reply.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await event_plain.send(MessageChain(chain=[Plain("纯文字一")]))
            await event_plain.send(MessageChain(chain=[Plain("纯文字二")]))
        self.assertEqual(len(sent_plain), 2)
        sleep_mock.assert_not_awaited()

        # Non-QQ platform never sleeps across Record -> Plain.
        event_tg, sent_tg = self._make_event("telegram")
        with patch("astrbot_host.core_reply.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await event_tg.send(MessageChain(chain=[Record(file=silent_wav(), text="hi")]))
            await event_tg.send(MessageChain(chain=[Plain("hi")]))
        self.assertEqual(len(sent_tg), 2)
        sleep_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
