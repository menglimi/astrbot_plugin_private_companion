# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.proactive_message import (
    ProactiveMessageMixin,
    _ProactiveSendOutcome,
)


UMO = "default:FriendMessage:10001"


class _GenerationHarness(ProactiveMessageMixin, LlmToolActionsMixin):
    enable_llm_proactive_message = True
    enable_reaction_expression_experiment = True
    reaction_expression_private_enabled = True
    reaction_expression_proactive_enabled = True
    reaction_expression_candidate_limit = 6

    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text

    @staticmethod
    def _reaction_image_provider_available() -> bool:
        return True

    async def _generate_proactive_message_via_framework(self, *_args, **_kwargs) -> str:
        return self.raw_text

    async def _generate_proactive_message_direct_fallback(self, *_args, **_kwargs) -> str:
        return ""

    async def _finalize_proactive_generated_text(self, _user, raw_text: str, **_kwargs):
        return raw_text.strip(), ""


class _AttachmentHarness(ProactiveMessageMixin):
    enable_reaction_expression_experiment = True
    reaction_expression_private_enabled = True
    reaction_expression_proactive_enabled = True

    def __init__(self, image_path: str) -> None:
        self.image_path = image_path
        self.prepared_event = None
        self.settled: list[tuple[bool, str]] = []

    @staticmethod
    def _reaction_image_provider_available() -> bool:
        return True

    @staticmethod
    def _reaction_expression_has_visible_text(text: str) -> bool:
        return bool(text.strip())

    async def _preauthorize_reaction_expression_prompt(self, event) -> bool:
        self.prepared_event = event
        return True

    async def _pc_reaction_expression_impl(self, event, **kwargs) -> str:
        pending = {
            "user_id": event.get_sender_id(),
            "scope": "private",
            "scope_key": event.unified_msg_origin,
            "image_id": "pc-local:test",
            "match_basis": "tags_emotions_intents",
            "settled": False,
        }
        event._private_companion_reaction_expression_pending_attachment = pending
        self.prepare_kwargs = kwargs
        return json.dumps(
            {
                "decision": "attach",
                "path": self.image_path,
                "image_id": "pc-local:test",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )

    async def _settle_reaction_expression_attachment_data(
        self,
        _pending,
        *,
        sent: bool,
        reason: str,
    ) -> bool:
        self.settled.append((sent, reason))
        return True

    @staticmethod
    def _log_reaction_expression_event(*_args, **_kwargs) -> None:
        return None


class _DeliveryHarness(ProactiveMessageMixin):
    def __init__(self, *, settlement_error: bool = False) -> None:
        self.pending = {"settled": False}
        self.settled: list[tuple[bool, str]] = []
        self.prepare_calls = 0
        self.cleared: list[str] = []
        self.settlement_error = settlement_error

    async def _prepare_proactive_reaction_attachment(self, _umo: str, _text: str):
        self.prepare_calls += 1
        return SimpleNamespace(kind="image"), self.pending

    async def _send_media_proactive_chain(self, *_args, **_kwargs):
        return _ProactiveSendOutcome(
            delivered=True,
            complete=True,
            delivered_text="正文",
            extra_components_delivered=1,
        )

    async def _settle_reaction_expression_attachment_data(
        self,
        _pending,
        *,
        sent: bool,
        reason: str,
    ) -> bool:
        self.settled.append((sent, reason))
        if self.settlement_error:
            raise RuntimeError("bookkeeping unavailable")
        return True

    def _clear_proactive_reaction_intent(self, umo: str) -> None:
        self.cleared.append(umo)


class _ModeDeliveryHarness(ProactiveMessageMixin):
    def __init__(self, mode: str = "separate_after", send_results=None) -> None:
        self.reaction_expression_delivery_mode = mode
        self.reaction_component = SimpleNamespace(kind="reaction_image")
        self.pending = {"settled": False}
        self.send_results = list(send_results or [True, True])
        self.sent_chains: list[list[object]] = []
        self.settled: list[tuple[bool, str]] = []

    async def _prepare_proactive_reaction_attachment(self, _umo: str, _text: str):
        return self.reaction_component, self.pending

    async def _settle_reaction_expression_attachment_data(
        self,
        _pending,
        *,
        sent: bool,
        reason: str,
    ) -> bool:
        self.settled.append((sent, reason))
        return True

    async def _send_chain_components(self, _umo: str, chain: list[object], **_kwargs):
        self.sent_chains.append(list(chain))
        result = self.send_results.pop(0) if self.send_results else True
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    @staticmethod
    async def _maybe_send_input_status(_umo: str, _text: str) -> None:
        return None

    @staticmethod
    def _split_proactive_text(text: str, **_kwargs) -> list[str]:
        return [text] if text else []

    @staticmethod
    def _segmented_scope_allows_umo(_umo: str) -> bool:
        return True

    @staticmethod
    def _platform_supports(*_args, **_kwargs) -> bool:
        return True

    @staticmethod
    def _with_optional_reply(chain: list[object], _message_id: str) -> list[object]:
        return list(chain)

    @staticmethod
    def _quote_skip_reason_for_short_reply(_text: str) -> str:
        return ""

    @staticmethod
    def _should_cancel_reply_for_recalled_message_ids(_message_id: str) -> str:
        return ""

    @staticmethod
    def _clear_proactive_reaction_intent(_umo: str) -> None:
        return None


class ProactiveReactionExpressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_strips_hidden_intent_and_caches_it_for_delivery(self) -> None:
        raw = (
            "刚看到一个很有意思的小东西，顺手来和你分享一下。"
            '<pc_reaction_expression>{"purpose":"分享开心","emotion":"开心",'
            '"intensity":2,"candidate_queries":["开心分享"]}</pc_reaction_expression>'
        )
        harness = _GenerationHarness(raw)
        user = {"user_id": "10001", "umo": UMO}

        text = await harness._generate_proactive_message_with_llm(
            user,
            "用户",
            "check_in",
        )

        self.assertEqual("刚看到一个很有意思的小东西，顺手来和你分享一下。", text)
        self.assertNotIn("pc_reaction_expression", text)
        cached = harness._proactive_reaction_intent_cache()[UMO]
        self.assertEqual("分享开心", cached["intent"]["purpose"])
        self.assertEqual("10001", cached["user_id"])

    async def test_generation_without_hidden_intent_keeps_plain_text_only(self) -> None:
        harness = _GenerationHarness("今天路过时闻到一点桂花香，忽然想起你。")
        user = {"user_id": "10001", "umo": UMO}

        text = await harness._generate_proactive_message_with_llm(
            user,
            "用户",
            "check_in",
        )

        self.assertEqual("今天路过时闻到一点桂花香，忽然想起你。", text)
        self.assertNotIn(UMO, harness._proactive_reaction_intent_cache())

    async def test_high_frequency_generation_recovers_when_model_omits_hidden_intent(self) -> None:
        harness = _GenerationHarness("今天路过时闻到一点桂花香，忽然想起你。")
        harness.reaction_expression_trigger_probability = 1.0
        user = {"user_id": "10001", "umo": UMO}

        text = await harness._generate_proactive_message_with_llm(
            user,
            "用户",
            "check_in",
        )

        self.assertEqual("今天路过时闻到一点桂花香，忽然想起你。", text)
        cached = harness._proactive_reaction_intent_cache()[UMO]
        self.assertEqual("日常分享", cached["intent"]["purpose"])
        self.assertEqual("开心", cached["intent"]["emotion"])

    async def test_prepared_attachment_uses_canonical_user_and_visible_text_context(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "reaction.png"
            image_path.write_bytes(b"fake-image")
            harness = _AttachmentHarness(str(image_path))
            harness._proactive_reaction_intent_cache()[UMO] = {
                "user_id": "10001",
                "expires_at": 4_000_000_000,
                "intent": {
                    "purpose": "轻吐槽",
                    "emotion": "无语",
                    "intensity": 2,
                    "candidate_queries": ["无语"],
                },
            }
            fake_component = SimpleNamespace(path=str(image_path))
            fake_image = SimpleNamespace(
                fromFileSystem=lambda _path: fake_component,
                from_file_system=lambda _path: fake_component,
            )

            with patch(
                "astrbot_plugin_private_companion.proactive_message.Image",
                fake_image,
            ):
                component, pending = await harness._prepare_proactive_reaction_attachment(
                    UMO,
                    "这件事确实有点让人无语。",
                )

        self.assertIs(component, fake_component)
        self.assertIsInstance(pending, dict)
        self.assertEqual("10001", harness.prepared_event.get_sender_id())
        self.assertEqual(UMO, harness.prepared_event.unified_msg_origin)
        self.assertEqual("这件事确实有点让人无语。", harness.prepare_kwargs["context"])
        self.assertTrue(pending["attached"])

    async def test_send_chain_settles_reaction_only_after_media_delivery(self) -> None:
        harness = _DeliveryHarness()

        outcome = await harness._send_proactive_message_chain(UMO, "正文")

        self.assertTrue(outcome.delivered)
        self.assertEqual(1, harness.prepare_calls)
        self.assertEqual([(True, "delivered")], harness.settled)

    async def test_multi_persona_delivery_gate_blocks_before_platform_send(self) -> None:
        harness = _ModeDeliveryHarness()
        harness.enable_multi_persona_mode = True
        harness._active_persona_scope = lambda: "scheduled-persona"
        harness._validate_proactive_persona_delivery = AsyncMock(
            return_value={"ok": False, "action": "blocked", "reason_code": "persona_mismatch"}
        )

        outcome = await harness._send_proactive_message_chain(UMO, "不应发送")

        self.assertFalse(outcome.delivered)
        self.assertEqual([], harness.sent_chains)
        harness._validate_proactive_persona_delivery.assert_awaited_once_with(
            UMO,
            "scheduled-persona",
        )

    async def test_single_persona_delivery_gate_mismatch_keeps_compatibility_send(self) -> None:
        harness = _ModeDeliveryHarness()
        harness.enable_multi_persona_mode = False
        harness.plugin_specific_persona_id = "single-persona"
        harness._validate_proactive_persona_delivery = AsyncMock(
            return_value={"ok": True, "action": "sent_with_warning", "reason_code": "single_mode_mismatch"}
        )

        outcome = await harness._send_proactive_message_chain(UMO, "继续发送")

        self.assertTrue(outcome.delivered)
        self.assertEqual(2, len(harness.sent_chains))
        harness._validate_proactive_persona_delivery.assert_awaited_once_with(
            UMO,
            "single-persona",
        )

    async def test_multi_persona_delivery_gate_exception_fails_closed(self) -> None:
        harness = _ModeDeliveryHarness()
        harness.enable_multi_persona_mode = True
        harness._active_persona_scope = lambda: "scheduled-persona"
        harness._validate_proactive_persona_delivery = AsyncMock(
            side_effect=RuntimeError("conversation unavailable")
        )

        outcome = await harness._send_proactive_message_chain(UMO, "不应发送")

        self.assertFalse(outcome.delivered)
        self.assertEqual([], harness.sent_chains)

    async def test_default_separate_after_sends_complete_text_before_image(self) -> None:
        harness = _ModeDeliveryHarness()

        outcome = await harness._send_proactive_message_chain(UMO, "完整正文")

        self.assertTrue(outcome.complete)
        self.assertEqual(2, len(harness.sent_chains))
        self.assertEqual("完整正文", harness.sent_chains[0][0].text)
        self.assertIs(harness.sent_chains[1][0], harness.reaction_component)
        self.assertEqual([(True, "delivered")], harness.settled)

    async def test_separate_after_skips_image_when_primary_is_not_delivered(self) -> None:
        harness = _ModeDeliveryHarness(send_results=[False])

        outcome = await harness._send_proactive_message_chain(UMO, "完整正文")

        self.assertFalse(outcome.delivered)
        self.assertEqual(1, len(harness.sent_chains))
        self.assertEqual("完整正文", harness.sent_chains[0][0].text)
        self.assertEqual([(False, "primary_not_delivered")], harness.settled)

    async def test_same_message_keeps_text_and_image_in_one_chain(self) -> None:
        harness = _ModeDeliveryHarness(mode="same_message", send_results=[True])

        outcome = await harness._send_proactive_message_chain(UMO, "完整正文")

        self.assertTrue(outcome.complete)
        self.assertEqual(1, len(harness.sent_chains))
        self.assertEqual("完整正文", harness.sent_chains[0][0].text)
        self.assertIs(harness.sent_chains[0][1], harness.reaction_component)
        self.assertEqual([(True, "delivered")], harness.settled)

    async def test_separate_before_sends_image_first_and_keeps_text_on_image_failure(self) -> None:
        harness = _ModeDeliveryHarness(
            mode="separate_before",
            send_results=[False, True],
        )

        outcome = await harness._send_proactive_message_chain(UMO, "完整正文")

        self.assertTrue(outcome.complete)
        self.assertIs(harness.sent_chains[0][0], harness.reaction_component)
        self.assertEqual("完整正文", harness.sent_chains[1][0].text)
        self.assertEqual([(False, "delivery_failed")], harness.settled)

    def test_delivery_mode_normalization_is_backward_compatible(self) -> None:
        normalize = ProactiveMessageMixin._normalize_reaction_expression_delivery_mode

        self.assertEqual("same_message", normalize("inline"))
        self.assertEqual("same_message", normalize("current-chain"))
        self.assertEqual("separate_before", normalize("before"))
        self.assertEqual("separate_after", normalize("invalid"))

    async def test_existing_media_skips_reaction_attachment(self) -> None:
        harness = _DeliveryHarness()

        outcome = await harness._send_proactive_message_chain(
            UMO,
            "正文",
            extra_components=[SimpleNamespace(kind="voice")],
        )

        self.assertTrue(outcome.delivered)
        self.assertEqual(0, harness.prepare_calls)
        self.assertEqual([UMO], harness.cleared)
        self.assertEqual([], harness.settled)

    async def test_inline_image_skips_reaction_attachment(self) -> None:
        harness = _DeliveryHarness()

        outcome = await harness._send_proactive_message_chain(
            UMO,
            '正文<img src="https://example.com/photo.png">',
        )

        self.assertTrue(outcome.delivered)
        self.assertEqual(0, harness.prepare_calls)
        self.assertEqual([UMO], harness.cleared)
        self.assertEqual([], harness.settled)

    async def test_settlement_failure_does_not_turn_delivery_into_failure(self) -> None:
        harness = _DeliveryHarness(settlement_error=True)

        outcome = await harness._send_proactive_message_chain(UMO, "正文")

        self.assertTrue(outcome.delivered)
        self.assertTrue(outcome.complete)
        self.assertEqual([(True, "delivered")], harness.settled)


if __name__ == "__main__":
    unittest.main()
