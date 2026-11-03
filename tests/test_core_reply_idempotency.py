"""One turn never delivers the same body twice in a row.

These tests go through the real reply guard: ``prepare_core_reply_event`` installs
it, ``activate_core_reply_delivery_guard`` arms it and ``event.send`` is the one
call the host makes per delivery.  Everything below is therefore the sequence the
platform would see, not a private helper's verdict.

Coverage: only deliveries that re-enter the guarded native loop are compared.
``event.send_streaming`` implementations that never call ``event.send`` (native
streaming bubbles) are deliberately outside this net; the tests below only pin the
guarded path.
"""

from dataclasses import dataclass
import asyncio
import inspect
import unittest

from tests import astrbot_data_root  # noqa: F401  (message components need the SDK)

from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain

from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard, prepare_core_reply_event,
    restore_core_reply_event,
)
from astrbot_host.core_reply_idempotency import (
    begin_core_reply_delivery_turn, should_drop_core_reply_duplicate,
)
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome

BODY = "我先看看这事的来龙去脉。"
OTHER = "其实我刚查了，是服务器在维护。"


@dataclass
class PreparedContext:
    instruction: str = "Speak naturally."
    context: object = None
    target_id: str = "target-1"
    channel: str = "private"
    prompt_instruction: str = ""
    stable_prompt_instruction: str = ""


class DeliveryEvent:
    """The event surface the reply guard and the delivery net actually use."""

    def __init__(self):
        self.extra = {}
        self.sent = []
        self.reject_next = False
        self.explode_next = False
        self.send = self._send
        self.send_streaming = self._send_streaming

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    async def _send(self, message, *args, **kwargs):
        if self.explode_next:
            self.explode_next = False
            raise RuntimeError("platform call failed")
        if self.reject_next:
            self.reject_next = False
            return False
        self.sent.append(message)
        return "sent"

    async def _send_streaming(self, *args, **kwargs):
        self.sent.append(args)
        return "streamed"


async def authorize(_prepared):
    return Outcome.success(True)


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


def chain(text, *parts):
    return MessageChain(chain=[Plain(text), *parts])


def texts(event):
    return [part.get_plain_text() for part in event.sent]


class ReplyDeliveryNetTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, event=None, prepared=None, **kwargs):
        event = event or DeliveryEvent()
        if prepared is None:
            prepared = PreparedContext()
        await resolve(prepare_core_reply_event(event, prepared, authorize=authorize, **kwargs))
        activate_core_reply_delivery_guard(event)
        return event

    def core(self, event):
        return event.get_extra(CORE_REPLY_CONTEXT_EXTRA)

    # -- the repeat that started all of this ---------------------------------
    async def test_the_same_body_right_after_an_accepted_one_is_delivered_once(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await event.send(chain(BODY))
        self.assertEqual(texts(event), [BODY])
        state = self.core(event)
        self.assertEqual((state["send_attempts"], state["send_returns"]), (1, 1))
        self.assertEqual(state["_delivery_net"]["dropped"], 1)

    async def test_only_the_adjacent_second_copy_is_dropped(self):
        event = await self.prepare()
        for _ in range(3):
            await event.send(chain(BODY))
        # A third identical body is a real repeat from somewhere else: keep it
        # visible instead of hiding it behind this net.
        self.assertEqual(texts(event), [BODY, BODY])

    async def test_a_third_identical_body_is_still_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await event.send(chain(BODY))
        await event.send(chain(BODY))
        self.assertEqual(len(event.sent), 2, "the third identical body must still reach the platform")

    async def test_a_different_answer_is_always_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await event.send(chain(OTHER))
        await event.send(chain(BODY))
        self.assertEqual(texts(event), [BODY, OTHER, BODY])

    # -- decorations are part of the identity --------------------------------
    async def test_the_same_body_with_the_same_mention_is_one_delivery(self):
        event = await self.prepare()
        await event.send(chain(BODY, At(qq="10001")))
        await event.send(chain(BODY, At(qq="10001")))
        self.assertEqual(len(event.sent), 1, "an identical mention is the same delivery")

    async def test_a_second_body_that_mentions_another_account_is_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY, At(qq="10001")))
        await event.send(chain(BODY, At(qq="10002")))
        self.assertEqual(len(event.sent), 2, "a different @ target must not be swallowed")

    async def test_a_second_body_that_adds_a_mention_is_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await event.send(chain(BODY, At(qq="10002")))
        self.assertEqual(len(event.sent), 2, "a body that adds a mention is a new delivery")

    async def test_the_same_body_quoting_the_same_message_is_one_delivery(self):
        event = await self.prepare()
        await event.send(chain(BODY, Reply(id="m1", sender_id=1)))
        await event.send(chain(BODY, Reply(id="m1", sender_id=1)))
        self.assertEqual(len(event.sent), 1, "the identical quote is the same delivery")

    async def test_a_second_body_that_quotes_another_message_is_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY, Reply(id="m1", sender_id=1)))
        await event.send(chain(BODY, Reply(id="m2", sender_id=1)))
        self.assertEqual(len(event.sent), 2, "a different quoted message must not be swallowed")

    async def test_a_second_body_that_adds_a_quote_is_delivered(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await event.send(chain(BODY, Reply(id="m2", sender_id=1)))
        self.assertEqual(len(event.sent), 2, "a body that adds a quote is a new delivery")

    # -- the turn owns the net ----------------------------------------------
    async def test_a_new_turn_may_repeat_the_last_turns_words(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await self.prepare(event)
        await event.send(chain(BODY))
        self.assertEqual(texts(event), [BODY, BODY], "the same words in two turns are two replies")
        self.assertEqual(len(event.sent), 2)

    async def test_a_second_turn_still_drops_its_own_adjacent_copy(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        await self.prepare(event)
        await event.send(chain(BODY))
        await event.send(chain(BODY))
        # Cross-turn words are delivered, and the new turn keeps its own dedupe.
        self.assertEqual(texts(event), [BODY, BODY])
        self.assertEqual(self.core(event)["_delivery_net"]["dropped"], 1)

    async def test_re_preparing_the_same_turn_snapshot_keeps_the_net(self):
        snapshot = PreparedContext()
        event = await self.prepare(prepared=snapshot)
        await event.send(chain(BODY))
        # A re-entry with the same reply snapshot is the same turn: it must not
        # silently forget what was already accepted.
        await self.prepare(event, prepared=snapshot)
        await event.send(chain(BODY))
        self.assertEqual(len(event.sent), 1, "re-preparing the same turn must not lose dedupe")

    async def test_a_missed_net_reset_cannot_swallow_the_next_turns_words(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        state = self.core(event)
        # The next turn's snapshot is installed and its identity bound, but the
        # net reset is deliberately skipped.  Identity alone must fence the old net.
        state["prepared"] = PreparedContext()
        self.assertTrue(begin_core_reply_delivery_turn(event))
        await event.send(chain(BODY))
        self.assertEqual(len(event.sent), 2,
                         "a net that lost its reset must not swallow the next turn's words")
        await event.send(chain(BODY))
        self.assertEqual(texts(event), [BODY, BODY])
        self.assertEqual(state["_delivery_net"]["dropped"], 1,
                         "the new turn still drops its own adjacent copy")

    async def test_a_new_delivery_phase_in_the_same_turn_keeps_the_net(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        # AstrBot closes one delivery phase after each result; a tool loop yields
        # another result on the same turn and the guard is re-armed for it.
        restore_core_reply_event(event)
        activate_core_reply_delivery_guard(event)
        await event.send(chain(BODY))
        self.assertEqual(len(event.sent), 1,
                         "a delivery phase boundary inside one turn is not a new turn")

    async def test_the_net_dies_with_the_reply_turn(self):
        event = DeliveryEvent()

        async def turn():
            await self.prepare(event, on_reply_end=lambda: None)
            await event.send(chain(BODY))
            self.assertIn("_delivery_net", self.core(event))
            self.assertIn("_delivery_turn", self.core(event))

        await asyncio.create_task(turn())
        await asyncio.sleep(0)
        state = self.core(event)
        self.assertNotIn("_delivery_net", state, "a finished turn must not keep its net")
        self.assertNotIn("_delivery_turn", state, "a finished turn must not keep its identity")

    # -- never swallow what the turn only said once --------------------------
    async def test_a_picture_is_a_different_delivery_not_a_repeat(self):
        event = await self.prepare()
        await event.send(chain(BODY, Image(file="fixture.png")))
        await event.send(chain(BODY, Image(file="fixture.png")))
        self.assertEqual(len(event.sent), 2)

    async def test_a_rejected_body_is_offered_again(self):
        event = await self.prepare()
        event.reject_next = True
        with self.assertRaises(TaskError):
            await event.send(chain(BODY))
        net = self.core(event).get("_delivery_net") or {}
        self.assertFalse(net.get("confirmed", False),
                         "a rejected delivery never authorises dropping the next copy")
        await event.send(chain(BODY))
        self.assertEqual(texts(event), [BODY])

    async def test_an_unknown_outcome_never_authorises_a_drop(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        self.core(event)["send_unknown"] = 1
        self.assertFalse(should_drop_core_reply_duplicate(event, chain(BODY)))
        self.core(event)["send_unknown"] = 0
        self.core(event)["send_failed"] = 1
        self.assertFalse(should_drop_core_reply_duplicate(event, chain(BODY)))

    async def test_the_net_only_compares_plain_text_deliveries(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        self.assertFalse(should_drop_core_reply_duplicate(event, BODY))
        self.assertFalse(should_drop_core_reply_duplicate(event, chain(BODY, Image(file="x.png"))))

    async def test_dropping_a_repeat_does_not_report_a_send(self):
        event = await self.prepare()
        await event.send(chain(BODY))
        result = await event.send(chain(BODY))
        self.assertIsNone(result, "a fully dropped delivery must not claim a send")
        self.assertFalse(self.core(event)["send_unknown"])
        self.assertFalse(self.core(event)["send_failed"])

    async def test_a_turn_without_a_net_still_delivers(self):
        event = DeliveryEvent()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {"event_ref": "turn-x"}})
        self.assertFalse(should_drop_core_reply_duplicate(event, chain(BODY)))


if __name__ == "__main__":
    unittest.main()
