"""Direct and quoted forwards share operator intake card, node, depth, and text budgets."""

import unittest

from astrbot_host.audio.loader import load_native_audio
from astrbot_host.media import load_native_image
from astrbot_host.video.loader import load_native_video
from companion_v2.adapters.astrbot.native import NativePrivateBridge
from companion_v2.adapters.astrbot.quote_content import capture_quote_content
from companion_v2.conversation.forwards.models import (
    CapturedForward, CapturedForwardNode, ForwardIntakePolicy, MAX_FORWARDS, MAX_NODES, fits_forwards,
)
from companion_v2.conversation.quotes.forward_models import CapturedForwardQuote
from companion_v2.execution.models import TaskError, digest
from tests.forward_support import ForwardFixture, node_raw, quoted_forward_raw
from tests.group_quote_media_support import Forward, GroupQuoteMediaFixture, Reply
from tests.test_conversation_quotes import FakeReply


def _captured_forward(position, *, node_count=1, text="节点正文", paths=None):
    resource_hash = digest(f"forward-{position}")
    node_paths = paths if paths is not None else tuple((index,) for index in range(node_count))
    nodes = tuple(
        CapturedForwardNode(path, resource_hash, "300", "记录中的昵称", 1700000000, text)
        for path in node_paths
    )
    return CapturedForward(position, resource_hash, nodes)


class FitsForwardsIntakeTests(unittest.TestCase):
    def test_structural_mode_allows_persisted_range_while_intake_enforces_policy_ceilings(self):
        cards = (_captured_forward(0, node_count=20), _captured_forward(1, node_count=20))
        self.assertTrue(fits_forwards(cards))
        self.assertFalse(fits_forwards(cards, intake=ForwardIntakePolicy()))
        self.assertTrue(fits_forwards(cards, intake=ForwardIntakePolicy(max_nodes=300)))

        boundary = (_captured_forward(0, node_count=16), _captured_forward(1, node_count=16))
        self.assertTrue(fits_forwards(boundary, intake=ForwardIntakePolicy()))

        exact_structural = (_captured_forward(0, node_count=150), _captured_forward(1, node_count=150))
        self.assertTrue(fits_forwards(exact_structural, intake=ForwardIntakePolicy(max_nodes=MAX_NODES)))
        over_structural = (_captured_forward(0, node_count=151), _captured_forward(1, node_count=150))
        self.assertFalse(fits_forwards(over_structural, intake=ForwardIntakePolicy(max_nodes=MAX_NODES)))

    def test_intake_enforces_narrowed_cards_nodes_depth_and_shared_text(self):
        narrow = ForwardIntakePolicy(max_forwards=2, max_nodes=8, max_depth=2)
        self.assertTrue(fits_forwards((_captured_forward(0, node_count=4), _captured_forward(1, node_count=4)), intake=narrow))
        self.assertFalse(fits_forwards((_captured_forward(0, node_count=5), _captured_forward(1, node_count=4)), intake=narrow))
        self.assertFalse(fits_forwards(
            (_captured_forward(0), _captured_forward(1), _captured_forward(2)), intake=narrow))

        deep_card = (_captured_forward(0, paths=((0,), (0, 0, 0), (0, 0, 0, 0, 0))),)
        self.assertFalse(fits_forwards(deep_card, intake=narrow))
        self.assertTrue(fits_forwards(deep_card, intake=ForwardIntakePolicy(max_depth=3)))

        text_cards = (_captured_forward(0, text="x" * 33000), _captured_forward(1, text="y" * 33000))
        self.assertFalse(fits_forwards(text_cards, intake=ForwardIntakePolicy()))

        too_many_cards = tuple(_captured_forward(i) for i in range(MAX_FORWARDS + 1))
        self.assertFalse(fits_forwards(too_many_cards, intake=ForwardIntakePolicy()))

    def test_invalid_intake_type_is_rejected(self):
        with self.assertRaises(TypeError):
            fits_forwards((), intake={"max_nodes": 32})

    def test_bridge_shared_forward_helper_checks_direct_plus_quoted_against_intake(self):
        direct = (_captured_forward(0, node_count=20),)
        quote = CapturedForwardQuote(1, "345", "201", 1700000000, "\n[合并转发 1]\n", (), (_captured_forward(0, node_count=20),))
        NativePrivateBridge._check_shared_forwards(
            direct, (quote,), ForwardIntakePolicy(max_nodes=40), "allowed at 40")
        with self.assertRaises(TaskError) as caught:
            NativePrivateBridge._check_shared_forwards(
                direct, (quote,), ForwardIntakePolicy(), "direct and quoted forwards share one budget")
        self.assertEqual(caught.exception.code, "host_forward_limit")


class GroupForwardIntakeBudgetTests(GroupQuoteMediaFixture, unittest.IsolatedAsyncioTestCase):
    async def _load_quote_without_history_wrapper(self, locator, *, client, scope, intake=ForwardIntakePolicy()):
        del client, scope
        raw = self.records[locator.message_id]
        return await capture_quote_content(
            locator,
            str(raw["sender"]["user_id"]),
            raw.get("time"),
            raw["message"],
            current=lambda: None,
            image_loader=load_native_image,
            audio_loader=load_native_audio,
            video_loader=load_native_video,
            intake=intake,
        )

    async def _capture_direct_and_quoted(self, *, direct_cards=1, direct_nodes=None, quoted_cards=1, quoted_nodes=None):
        q_nodes = quoted_nodes if quoted_nodes is not None else [node_raw()]
        d_nodes = direct_nodes if direct_nodes is not None else [node_raw()]
        self.store("345", sender="201", parts=[Forward(id="group-quoted-forward")] * quoted_cards, nodes=q_nodes)
        parts = [Forward(id="group-quoted-forward")] * direct_cards + [Reply(id="345")]
        self.store("678", sender="200", parts=parts, nodes=d_nodes)
        return await self.controller.session.bridge.groups.capture(self.media_event(parts, identifier="678"))

    async def test_group_direct_and_quoted_forwards_enforce_intake_node_and_card_budgets(self):
        await self.start_host()
        bridge = self.controller.session.bridge.groups
        bridge.quote_loader = self._load_quote_without_history_wrapper

        over_nodes = await self._capture_direct_and_quoted(direct_nodes=[node_raw()] * 20, quoted_nodes=[node_raw()] * 20)
        self.assertEqual(over_nodes.failure.code, "host_forward_limit")

        exact_nodes = await self._capture_direct_and_quoted(direct_nodes=[node_raw()] * 16, quoted_nodes=[node_raw()] * 16)
        self.assertTrue(exact_nodes.is_success, exact_nodes.failure)

        over_text = await self._capture_direct_and_quoted(
            direct_nodes=[node_raw("x" * 33000)], quoted_nodes=[node_raw("y" * 33000)])
        self.assertEqual(over_text.failure.code, "host_forward_limit")

        bridge.forward_intake = ForwardIntakePolicy(max_nodes=300)
        widened = await self._capture_direct_and_quoted(direct_nodes=[node_raw()] * 20, quoted_nodes=[node_raw()] * 20)
        self.assertTrue(widened.is_success, widened.failure)

        bridge.forward_intake = ForwardIntakePolicy(max_forwards=2, max_nodes=10)
        over_cards = await self._capture_direct_and_quoted(direct_cards=1, quoted_cards=2)
        self.assertEqual(over_cards.failure.code, "host_forward_limit")
        over_narrow_nodes = await self._capture_direct_and_quoted(
            direct_nodes=[node_raw()] * 5, quoted_nodes=[node_raw()] * 6)
        self.assertEqual(over_narrow_nodes.failure.code, "host_forward_limit")
        self.assertEqual(self.app.runtime.read_one("SELECT COUNT(*) FROM sources", ())[0], 0)


class PrivateForwardIntakeBudgetTests(ForwardFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_direct_and_quoted_forwards_respect_intake_policy(self):
        self.raw = quoted_forward_raw(nodes=[node_raw()] * 16, cards=1)
        self.forward_raw = {"messages": [node_raw()] * 16}
        event = self.forward_event()
        event.message_obj.message.append(FakeReply())
        self.assertTrue((await self.bridge.capture(event)).is_success)

        self.raw = quoted_forward_raw(nodes=[node_raw()] * 20, cards=1)
        self.forward_raw = {"messages": [node_raw()] * 20}
        self.assertEqual((await self.bridge.capture(event)).failure.code, "host_forward_limit")

        self.bridge.forward_intake = ForwardIntakePolicy(max_nodes=300)
        self.assertTrue((await self.bridge.capture(event)).is_success)

        self.bridge.forward_intake = ForwardIntakePolicy(max_forwards=2, max_nodes=10)
        self.raw = quoted_forward_raw(nodes=[node_raw()], cards=2)
        self.forward_raw = {"messages": [node_raw()]}
        self.assertEqual((await self.bridge.capture(event)).failure.code, "host_forward_limit")
