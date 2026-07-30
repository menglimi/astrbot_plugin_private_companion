# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from astrbot.api.message_components import Image, Plain

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


UMO = "default:FriendMessage:10001"


class _SendContext:
    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.calls = 0
        self.platform_manager = None

    async def send_message(self, _session: Any, _result: Any) -> Any:
        self.calls += 1
        return self.result


class _ComponentSendHarness(ProactiveMessageMixin):
    def __init__(self, result: Any = None) -> None:
        self.context = _SendContext(result)
        self.enable_precise_platform_send = False
        self.blocked_word = ""

    def _forbidden_recall_hit(self, _text: str) -> str:
        return self.blocked_word

    @staticmethod
    def _chain_text_for_forbidden_recall(chain: list[Any]) -> str:
        return "".join(str(getattr(item, "text", item)) for item in chain)

    @staticmethod
    async def _trigger_proactive_decorating_hooks(_umo: str, chain: list[Any]) -> list[Any]:
        return list(chain)

    @staticmethod
    def _build_result_from_chain(chain: list[Any]) -> list[Any]:
        return list(chain)


class _ChainHarness(ProactiveMessageMixin):
    def __init__(self) -> None:
        self.segments = ["测试"]
        self.recall_results: list[str] = []
        self.send_results: list[Any] = [True]
        self.sent: list[list[Any]] = []

    async def _maybe_send_input_status(self, _umo: str, _text: str) -> None:
        return None

    def _split_proactive_text(self, _text: str, **_kwargs: Any) -> list[str]:
        return list(self.segments)

    @staticmethod
    def _segmented_scope_allows_umo(_umo: str) -> bool:
        return True

    @staticmethod
    def _quote_skip_reason_for_short_reply(_text: str) -> str:
        return ""

    def _should_cancel_reply_for_recalled_message_ids(self, _message_id: str) -> str:
        return self.recall_results.pop(0) if self.recall_results else ""

    @staticmethod
    async def _send_segmented_proactive_forward_message(
        _umo: str,
        _segments: list[str],
        *,
        source: str = "proactive",
    ) -> bool:
        return False

    @staticmethod
    def _proactive_plain_segment_component(text: str, **_kwargs: Any) -> Plain:
        return Plain(text)

    @staticmethod
    def _with_optional_reply(chain: list[Any], _message_id: str) -> list[Any]:
        return list(chain)

    async def _send_chain_components(self, _umo: str, chain: list[Any], **_kwargs: Any) -> bool:
        self.sent.append(list(chain))
        result = self.send_results.pop(0) if self.send_results else False
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    @staticmethod
    async def _calc_segmented_proactive_interval(_segment: str) -> float:
        return 0.0


class _AmbiguousOneBotClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_action(self, _action: str, **_params: Any) -> Any:
        self.calls.append("call_action")
        raise TimeoutError("waiting for OneBot response timed out")

    async def call_api(self, _action: str, **_params: Any) -> Any:
        self.calls.append("call_api")
        return {"status": "ok", "retcode": 0}


class _SnowLumaPresenceClient:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(self, action: str, **params: Any) -> Any:
        self.calls.append((action, params))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def call_api(self, action: str, **params: Any) -> Any:
        self.calls.append((action, params))
        return {"status": "ok", "retcode": 0}


class OverallDebugRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_component_send_reports_core_acceptance(self) -> None:
        harness = _ComponentSendHarness(result=None)

        delivered = await harness._send_chain_components(UMO, [Plain("已送达")])

        self.assertTrue(delivered)
        self.assertEqual(1, harness.context.calls)

    async def test_component_send_reports_existing_content_block(self) -> None:
        harness = _ComponentSendHarness(result=None)
        harness.blocked_word = "测试禁词"

        delivered = await harness._send_chain_components(UMO, [Plain("不应发出")])

        self.assertFalse(delivered)
        self.assertEqual(0, harness.context.calls)

    async def test_component_send_reports_tts_chain_cancellation(self) -> None:
        harness = _ComponentSendHarness(result=None)

        async def clear_chain(_chain: list[Any], *, umo: str) -> list[Any]:
            return []

        harness._sanitize_outbound_tts_chain_without_event = clear_chain

        delivered = await harness._send_chain_components(UMO, [Plain("会被 TTS 校验清空")])

        self.assertFalse(delivered)
        self.assertEqual(0, harness.context.calls)

    async def test_recalled_trigger_is_not_reported_as_sent(self) -> None:
        harness = _ChainHarness()
        harness.recall_results = ["message-42"]

        delivered = await harness._send_proactive_message_chain(
            UMO,
            "测试",
            quote_message_id="message-42",
        )

        self.assertFalse(delivered)
        self.assertEqual([], harness.sent)

    async def test_partially_sent_segments_are_not_reported_as_fully_cancelled(self) -> None:
        harness = _ChainHarness()
        harness.segments = ["第一段", "第二段"]
        harness.recall_results = ["", "", "message-42"]
        harness.send_results = [True]

        delivered = await harness._send_proactive_message_chain(
            UMO,
            "第一段\n第二段",
            quote_message_id="message-42",
        )

        self.assertTrue(delivered)
        self.assertFalse(delivered.complete)
        self.assertEqual("第一段", delivered.delivered_text)
        self.assertEqual(1, len(harness.sent))

    async def test_partial_segment_failure_does_not_retry_delivered_prefix(self) -> None:
        harness = _ChainHarness()
        harness.segments = ["第一段", "第二段"]
        harness.send_results = [True, RuntimeError("第二段网络异常")]

        delivered = await harness._send_proactive_message_chain(UMO, "第一段\n第二段")

        self.assertTrue(delivered)
        self.assertFalse(delivered.complete)
        self.assertEqual("第一段", delivered.delivered_text)
        self.assertEqual(2, len(harness.sent))

    async def test_media_failure_after_text_keeps_only_real_delivery(self) -> None:
        harness = _ChainHarness()
        harness.segments = ["先发出的正文"]
        harness.send_results = [True, RuntimeError("媒体发送异常")]

        delivered = await harness._send_media_proactive_chain(
            UMO,
            "先发出的正文",
            extra_components=[Plain("模拟媒体组件")],
        )

        self.assertTrue(delivered)
        self.assertFalse(delivered.complete)
        self.assertEqual("先发出的正文", delivered.delivered_text)
        self.assertEqual(0, delivered.extra_components_delivered)

    async def test_empty_text_after_placeholder_cleanup_is_not_sent(self) -> None:
        harness = _ChainHarness()
        harness.segments = []
        harness._sanitize_orphan_tts_placeholders = lambda _text: ""

        delivered = await harness._send_proactive_message_chain(UMO, "<tts></tts>")

        self.assertFalse(delivered)
        self.assertEqual([], harness.sent)

    def test_decorating_hook_empty_or_receipt_only_result_stays_cancelled(self) -> None:
        harness = _ComponentSendHarness()

        self.assertEqual([], harness._filter_decorated_proactive_chain([Plain("原文")], []))
        self.assertEqual(
            [],
            harness._filter_decorated_proactive_chain([Plain("原文")], [Plain("消息已送达")]),
        )

    def test_onebot_forward_none_result_matches_normal_action_success_contract(self) -> None:
        harness = _ComponentSendHarness()

        self.assertTrue(harness._onebot_forward_action_result_ok(None))

    def test_real_plugin_mro_keeps_memory_and_affinity_trackers_separate(self) -> None:
        self.assertIs(PrivateCompanionPlugin._note_action_sent, UserMemoryMixin._note_action_sent)
        self.assertIs(
            PrivateCompanionPlugin._note_action_affinity_sent,
            ProactiveEngineMixin._note_action_affinity_sent,
        )
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin._reset_daily_counter_if_needed = lambda _user: None
        plugin._private_user_role = lambda _user, _user_id="": "owner"
        plugin._note_proactive_afterglow_sent = lambda *_args, **_kwargs: None
        plugin._note_proactive_afterglow_reply = lambda *_args, **_kwargs: None
        user = {"action_reply_affinity": {"photo_text": 2}}

        plugin._note_action_sent(user, "photo_text", text="发了一张照片")
        plugin._note_action_reply_feedback(user, "photo_text", "收到了")

        self.assertEqual({"sent": 3, "replied": 3}, user["action_reply_affinity"]["photo_text"])
        self.assertEqual(1, user["photo_sent_today"])

    def test_canonical_receipt_detector_includes_previous_event_patterns(self) -> None:
        detector = ProactiveMessageMixin._is_proactive_delivery_receipt_text

        for text in ("我主动开口了。", "图片生成好了啦", "正在排队", "消息已送达，请等待"):
            with self.subTest(text=text):
                self.assertTrue(detector(text))
        self.assertFalse(detector("今晚看到一盏很好看的灯，忽然想起你。"))

    def test_daily_summary_filters_legacy_proactive_archive_marker(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.bot_name = "测试 Bot"
        legacy = "【主动消息】触发原因：关心；行为结果：已发送；内部动机：想问候"

        rendered = plugin._format_history_item_for_summary({"role": "user", "content": legacy})

        self.assertEqual("", rendered)

    def test_partial_media_delivery_metadata_uses_only_delivered_content(self) -> None:
        harness = _ComponentSendHarness()

        action, summary, delivered_photo = harness._reconcile_proactive_delivery_metadata(
            text="图片没发出来，不过这句已经送达",
            extra_components=[],
            action="photo_text+voice",
            action_summary="发图：窗边的自拍",
            delivery_complete=False,
        )

        self.assertEqual("message", action)
        self.assertFalse(delivered_photo)
        self.assertIn("图片没发出来，不过这句已经送达", summary)
        self.assertNotIn("窗边的自拍", summary)

    def test_url_image_component_is_archived_as_photo(self) -> None:
        harness = _ComponentSendHarness()
        image = Image.fromURL("https://example.com/photo.jpg")

        action, summary, delivered_photo = harness._reconcile_proactive_delivery_metadata(
            text="给你看看",
            extra_components=[image],
            action="photo_text",
            action_summary="发图：窗边的自拍",
            delivery_complete=True,
        )
        archived = harness._build_proactive_archive_assistant_text(
            text="给你看看",
            extra_components=[image],
            action_summary=summary,
            photo_subject_owner="self",
        )

        self.assertEqual("photo_text", action)
        self.assertTrue(delivered_photo)
        self.assertIn("随消息发送了一张图片", archived)
        self.assertIn("图片画面：窗边的自拍", archived)

    async def test_ambiguous_onebot_send_does_not_retry_api_alias(self) -> None:
        harness = _ComponentSendHarness()
        client = _AmbiguousOneBotClient()

        ok, note = await harness._call_onebot_action_with_error(
            client,
            "send_private_msg",
            at_most_once=True,
            user_id=10001,
            message=[{"type": "text", "data": {"text": "只发一次"}}],
        )

        self.assertTrue(ok)
        self.assertIn("回执不确定", note)
        self.assertEqual(["call_action"], client.calls)

    async def test_ambiguous_forward_send_does_not_retry_api_alias(self) -> None:
        harness = _ComponentSendHarness()
        client = _AmbiguousOneBotClient()

        ok = await harness._call_onebot_forward_action(
            client,
            "send_private_forward_msg",
            user_id=10001,
            messages=[],
        )

        self.assertTrue(ok)
        self.assertEqual(["call_action"], client.calls)

    async def test_snowluma_presence_success_exception_is_not_retried(self) -> None:
        harness = _ComponentSendHarness()
        client = _SnowLumaPresenceClient(RuntimeError("set status success"))
        harness._resolve_aiocqhttp_client = lambda: client

        ok, note = await harness._set_qq_online_presence("busy")

        self.assertTrue(ok)
        self.assertEqual("忙碌", note)
        self.assertEqual(1, len(client.calls))
        self.assertEqual("set_online_status", client.calls[0][0])

    async def test_snowluma_failed_envelope_with_success_message_is_not_retried(self) -> None:
        harness = _ComponentSendHarness()
        client = _SnowLumaPresenceClient(
            {"status": "failed", "retcode": 100, "message": "set status success"}
        )
        harness._resolve_aiocqhttp_client = lambda: client

        ok, _note = await harness._set_qq_online_presence("online")

        self.assertTrue(ok)
        self.assertEqual(1, len(client.calls))

    async def test_presence_failure_does_not_cycle_api_aliases(self) -> None:
        harness = _ComponentSendHarness()
        client = _SnowLumaPresenceClient(
            {"status": "failed", "retcode": 1404, "message": "unsupported action"}
        )
        harness._resolve_aiocqhttp_client = lambda: client

        ok, note = await harness._set_qq_online_presence("online")

        self.assertFalse(ok)
        self.assertIn("unsupported action", note)
        self.assertEqual(1, len(client.calls))


if __name__ == "__main__":
    unittest.main()
