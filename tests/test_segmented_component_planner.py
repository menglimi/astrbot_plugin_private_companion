# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot.api.message_components import At, Image, Plain, Record, Reply

from astrbot_plugin_private_companion.segmented_message import (
    bind_reply_components_to_first_text,
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    normalize_component_order,
    normalize_component_strategy,
    plan_component_chunks,
)


DEFAULT_STRATEGIES = {
    "voice": "separate",
    "image": "separate",
    "at": "inline",
    "face": "inline",
    "other": "separate",
}


class SegmentedComponentPlannerTests(unittest.TestCase):
    @staticmethod
    def _plan(chain, **overrides):
        strategies = {**DEFAULT_STRATEGIES, **overrides}
        chunks, changed, split_changed, text = plan_component_chunks(
            chain,
            plain_type=Plain,
            split_text=lambda value: [part for part in value.split("|") if part],
            strategies=strategies,
        )
        return chunks, changed, split_changed, text

    def test_voice_is_separate_while_reply_and_at_follow_text(self):
        reply = Reply(id="message-1")
        chunks, changed, _split_changed, text = self._plan(
            [reply, At(qq="10001"), Record(file="voice.wav"), Plain("对应正文")]
        )

        self.assertTrue(changed)
        self.assertEqual("对应正文", text)
        self.assertEqual(
            [["Record"], ["Reply", "At", "Plain"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )
        self.assertIs(reply, chunks[1][0])

    def test_component_strategy_matrix_preserves_relative_text(self):
        expectations = {
            "separate": [["Plain"], ["Image"], ["Plain"]],
            "inline": [["Plain", "Image"], ["Plain"]],
            "previous": [["Plain", "Image"], ["Plain"]],
            "next": [["Plain"], ["Image", "Plain"]],
        }
        for strategy, expected in expectations.items():
            with self.subTest(strategy=strategy):
                chunks, _changed, _split_changed, _text = self._plan(
                    [Plain("第一段|"), Image(file="image.png"), Plain("第二段")],
                    image=strategy,
                )
                self.assertEqual(
                    expected,
                    [[type(item).__name__ for item in chunk] for chunk in chunks],
                )

    def test_inline_leading_image_joins_following_text(self):
        chunks, _changed, _split_changed, _text = self._plan(
            [Image(file="image.png"), Plain("正文")],
            image="inline",
        )

        self.assertEqual(
            [["Image", "Plain"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_next_without_following_text_remains_standalone(self):
        chunks, _changed, _split_changed, _text = self._plan(
            [Plain("正文"), Image(file="image.png")],
            image="next",
        )

        self.assertEqual(
            [["Plain"], ["Image"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_reply_binding_moves_quote_from_voice_to_pending_text(self):
        reply = Reply(id="message-2")
        chunks, changed = bind_reply_components_to_first_text(
            [[reply, Record(file="voice.wav")], [At(qq="10001"), Plain("正文")]],
            plain_type=Plain,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [["Record"], ["Reply", "At", "Plain"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_reply_binding_stays_after_leading_voice_in_mixed_chunk(self):
        chunks, changed = bind_reply_components_to_first_text(
            [[Reply(id="message-2"), Record(file="voice.wav"), At(qq="10001"), Plain("正文")]],
            plain_type=Plain,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [["Record", "Reply", "At", "Plain"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_reply_binding_drops_quote_when_voice_has_no_text_companion(self):
        chunks, changed = bind_reply_components_to_first_text(
            [[Reply(id="message-voice"), Record(file="voice.wav")]],
            plain_type=Plain,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [["Record"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_component_plan_drops_quote_from_voice_only_result(self):
        chunks, changed, _split_changed, full_text = self._plan(
            [Reply(id="message-voice"), Record(file="voice.wav")],
            voice="separate",
        )

        self.assertTrue(changed)
        self.assertEqual("", full_text)
        self.assertEqual(
            [["Record"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    def test_component_kind_and_strategy_aliases_are_version_tolerant(self):
        class Face:
            pass

        class File:
            pass

        self.assertEqual("face", component_kind(Face()))
        self.assertEqual("other", component_kind(File()))
        self.assertEqual("inline", normalize_component_strategy("嵌入", "separate"))
        self.assertEqual("previous", normalize_component_strategy("跟随上段", "separate"))
        self.assertEqual("next", normalize_component_strategy("follow_next", "separate"))
        self.assertEqual("separate", normalize_component_strategy("invalid", "separate"))

    def test_component_order_groups_types_stably_and_fills_missing_kinds(self):
        chain = [Plain("正文"), Image(file="image.png"), At(qq="10001"), Record(file="voice.wav")]
        chunks, _changed, _split_changed, _text = plan_component_chunks(
            chain,
            plain_type=Plain,
            split_text=lambda value: [value],
            strategies={**DEFAULT_STRATEGIES, "image": "separate", "at": "separate", "voice": "separate"},
            component_order=["image", "text", "at", "voice"],
        )
        self.assertEqual(
            [["Image"], ["Plain"], ["At"], ["Record"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )
        self.assertEqual(
            ["image", "text", "at", "voice", "face", "other", "reaction"],
            normalize_component_order(["image", "image", "text", "unknown", "at", "voice"]),
        )

    def test_component_policy_uses_active_persona_settings(self):
        class Owner:
            segmented_proactive_voice_strategy = "separate"
            segmented_proactive_image_strategy = "separate"
            segmented_proactive_at_strategy = "inline"
            segmented_proactive_face_strategy = "inline"
            segmented_proactive_other_strategy = "separate"
            segmented_proactive_component_order = ["voice", "text", "image"]
            reaction_expression_delivery_mode = "separate_after"

            values = {
                "segmented_proactive_voice_strategy": "inline",
                "segmented_proactive_image_strategy": "next",
                "segmented_proactive_at_strategy": "separate",
                "segmented_proactive_face_strategy": "previous",
                "segmented_proactive_other_strategy": "inline",
                "segmented_proactive_component_order": ["image", "text", "voice"],
                "reaction_expression_delivery_mode": "same_message",
            }

            def persona_setting(self, key, default=None):
                return self.values.get(key, getattr(self, key, default))

        owner = Owner()
        strategies = component_strategies_from_owner(owner)

        self.assertEqual("inline", strategies["voice"])
        self.assertEqual("next", strategies["image"])
        self.assertEqual("separate", strategies["at"])
        self.assertEqual("previous", strategies["face"])
        self.assertEqual("inline", strategies["other"])
        self.assertEqual("inline", strategies["reaction"])
        self.assertEqual(
            ["image", "text", "voice", "at", "face", "other", "reaction"],
            component_order_from_owner(owner),
        )


class SegmentedQuoteBindingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_reply_keeps_quote_for_pending_text_chunk(self):
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_proactive_quote_trigger_message = True
        plugin.enable_quote_group_reply = True
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._group_current_reply_quote_message_id = (
            lambda _event, *, text_or_chain: "message-3"
        )
        plugin._make_reply_component = (
            lambda message_id, event=None: Reply(id=message_id)
        )
        result = SimpleNamespace(
            chain=[Record(file="voice.wav")],
            is_llm_result=lambda: True,
        )

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            def __init__(self):
                self._private_companion_tts_reply_remainder = {
                    "chunks": [[At(qq="10001"), Plain("对应正文")]]
                }

            @staticmethod
            def get_result():
                return result

        event = Event()
        await PrivateCompanionPlugin.attach_group_reply_quote(plugin, event)

        self.assertEqual(
            ["Record"],
            [type(component).__name__ for component in result.chain],
        )
        self.assertEqual(
            [["Reply", "At", "Plain"]],
            [
                [type(component).__name__ for component in chunk]
                for chunk in event._private_companion_tts_reply_remainder["chunks"]
            ],
        )

    async def test_voice_and_text_keep_inbound_quote_for_downstream_consumers(self):
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        result = SimpleNamespace(
            chain=[Reply(id="quoted-image"), Record(file="voice.wav"), Plain("看起来不错。")],
            is_llm_result=lambda: True,
        )

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            @staticmethod
            def get_result():
                return result

        await PrivateCompanionPlugin.attach_group_reply_quote(plugin, Event())

        self.assertEqual(
            ["Record", "Reply", "Plain"],
            [type(component).__name__ for component in result.chain],
        )

    async def test_voice_only_reply_drops_orphan_quote_component(self):
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        result = SimpleNamespace(
            chain=[Reply(id="message-voice"), Record(file="voice.wav")],
            is_llm_result=lambda: True,
        )

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            @staticmethod
            def get_result():
                return result

        await PrivateCompanionPlugin.attach_group_reply_quote(plugin, Event())

        self.assertEqual(
            ["Record"],
            [type(component).__name__ for component in result.chain],
        )


if __name__ == "__main__":
    unittest.main()
