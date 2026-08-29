# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.message_components import Plain, Record, Reply
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _OfficialTtsHarness(TtsEnhancementMixin):
    def __init__(self, *, enabled: bool = True, probability: object = 1) -> None:
        self.context = SimpleNamespace(
            get_config=lambda _umo: {
                "provider_tts_settings": {
                    "enable": enabled,
                    "trigger_probability": probability,
                }
            },
            get_using_tts_provider=lambda _umo: object(),
        )


def _llm_result(*components: object) -> MessageEventResult:
    result = MessageEventResult(chain=list(components))
    result.set_result_content_type(ResultContentType.LLM_RESULT)
    return result


class OfficialTtsSegmentingCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_tts_plain_fallback_still_segments_for_owner(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        plugin._private_plain_result_allows_segmenting = lambda *_args: False
        chunks = [[Plain("第一段。")], [Plain("第二段。")]]
        plugin._segment_llm_reply_chain = Mock(
            return_value=(chunks, True, "第一段。第二段。")
        )
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(
            chain=list(chain)
        )
        plugin._segmented_chunk_log_text = lambda chunk: chunk[0].text
        plugin._create_lifecycle_background_task = Mock()
        result = MessageEventResult(chain=[Plain("第一段。第二段。")])

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = "想听听声音"
            _private_companion_tts_request_applied = True
            _private_companion_reaction_expression_intent = {"query": "回应"}

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertEqual(["第一段。"], [item.text for item in event.result.chain])
        self.assertEqual(
            [chunks[1]],
            event._private_companion_reaction_expression_segmented_remainder["chunks"],
        )
        plugin._create_lifecycle_background_task.assert_not_called()

    async def test_plugin_private_plain_reply_segments_without_role_gate(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        chunks = [
            [Plain("嗯...")],
            [Plain("比折大人盯着代码那么专注，都不理我呢……")],
            [Plain("那，早饭到底有没有乖乖吃呀，大笨蛋？")],
        ]
        plugin._segment_llm_reply_chain = Mock(
            return_value=(chunks, True, "".join(item[0].text for item in chunks))
        )
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(
            chain=list(chain)
        )
        plugin._segmented_chunk_log_text = lambda chunk: chunk[0].text
        plugin._create_lifecycle_background_task = Mock(
            side_effect=lambda coroutine, **_kwargs: coroutine.close()
        )
        result = MessageEventResult(
            chain=[Plain("\n".join(item[0].text for item in chunks))]
        )

        class Event:
            unified_msg_origin = "default:FriendMessage:995051631"
            message_str = ""

            def __init__(self) -> None:
                self.result = result

            @staticmethod
            def is_private_chat() -> bool:
                return True

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        self.assertTrue(
            PrivateCompanionPlugin._private_plain_result_allows_segmenting(
                plugin,
                event,
                list(result.chain),
            )
        )

        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertEqual(["嗯..."], [item.text for item in event.result.chain])
        plugin._create_lifecycle_background_task.assert_called_once()

    async def test_plugin_group_quoted_plain_reply_segments_without_llm_marker(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        quote = Reply(id="quoted-message")
        source = (
            "刚才被戳了宇宙。"
            "正看着群里庄见秋发的那个哔哩哔哩高标的链接呢，顺便刷刷消息摸鱼。"
        )
        chunks = [
            [quote, Plain("刚才被戳了宇宙。")],
            [Plain("正看着群里庄见秋发的那个哔哩哔哩高标的链接呢，顺便刷刷消息摸鱼。")],
        ]
        plugin._segment_llm_reply_chain = Mock(return_value=(chunks, True, source))
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(
            chain=list(chain)
        )
        plugin._segmented_chunk_log_text = lambda chunk: "".join(
            item.text for item in chunk if isinstance(item, Plain)
        )
        plugin._create_lifecycle_background_task = Mock(
            side_effect=lambda coroutine, **_kwargs: coroutine.close()
        )
        result = MessageEventResult(chain=[quote, Plain(source)])

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"
            message_str = "芙蕾雅 你在干嘛"

            def __init__(self) -> None:
                self.result = result

            @staticmethod
            def is_private_chat() -> bool:
                return False

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        self.assertTrue(
            PrivateCompanionPlugin._private_plain_result_allows_segmenting(
                plugin,
                event,
                list(result.chain),
            )
        )

        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertIs(quote, event.result.chain[0])
        self.assertEqual("刚才被戳了宇宙。", event.result.chain[1].text)
        plugin._create_lifecycle_background_task.assert_called_once()

    async def test_plugin_tts_group_quote_is_still_a_plain_fallback(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        plugin._private_plain_result_allows_segmenting = lambda *_args: False
        quote = Reply(id="quoted-message")
        chunks = [
            [quote, Plain("第一段。")],
            [Plain("第二段。")],
        ]
        plugin._segment_llm_reply_chain = Mock(
            return_value=(chunks, True, "第一段。第二段。")
        )
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(
            chain=list(chain)
        )
        plugin._segmented_chunk_log_text = lambda chunk: "".join(
            item.text for item in chunk if isinstance(item, Plain)
        )
        plugin._create_lifecycle_background_task = Mock(
            side_effect=lambda coroutine, **_kwargs: coroutine.close()
        )
        result = MessageEventResult(chain=[quote, Plain("第一段。第二段。")])

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"
            message_str = "普通聊天"
            _private_companion_tts_request_applied = True

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertIs(quote, event.result.chain[0])
        self.assertEqual("第一段。", event.result.chain[1].text)
        plugin._create_lifecycle_background_task.assert_called_once()

    def test_plugin_command_plain_reply_stays_whole(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        event = SimpleNamespace(
            is_command=True,
            message_str="/陪伴 状态",
            unified_msg_origin="default:GroupMessage:10001",
        )

        self.assertFalse(
            PrivateCompanionPlugin._private_plain_result_allows_segmenting(
                plugin,
                event,
                [Reply(id="quoted-command"), Plain("配置已保存。当前状态正常。")],
            )
        )

    async def test_reaction_reply_defers_remaining_bubbles_until_first_send(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = lambda key: key == "enable_segmented_proactive_reply"
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._should_defer_segmenting_to_astrbot_tts = AsyncMock(return_value=False)
        plugin._platform_supports = lambda *_args, **_kwargs: True
        chunks = [[Plain("第一段。")], [Plain("第二段。")]]
        plugin._segment_llm_reply_chain = Mock(
            return_value=(chunks, True, "第一段。第二段。")
        )
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(chain=list(chain))
        plugin._segmented_chunk_log_text = lambda chunk: chunk[0].text
        plugin._event_inbound_activity_ts = lambda _event: 10.0
        plugin._create_lifecycle_background_task = Mock()
        result = _llm_result(Plain("第一段。第二段。"))

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = "普通聊天"
            _private_companion_reaction_expression_intent = {"query": "开心"}

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertEqual(["第一段。"], [item.text for item in event.result.chain])
        self.assertEqual(
            chunks,
            event._private_companion_reaction_expression_expected_primary_chunks,
        )
        self.assertEqual(
            [chunks[1]],
            event._private_companion_reaction_expression_segmented_remainder["chunks"],
        )
        plugin._create_lifecycle_background_task.assert_not_called()

    async def test_deferred_reaction_tts_text_still_enters_segmenting(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        chunks = [[Plain("第一段。")], [Plain("第二段。")]]
        plugin._segment_llm_reply_chain = Mock(
            return_value=(chunks, True, "第一段。第二段。")
        )
        plugin._limit_private_routine_check_segments = lambda _text, value: value
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._build_result_from_chain = lambda chain: MessageEventResult(
            chain=list(chain)
        )
        plugin._segmented_chunk_log_text = lambda chunk: chunk[0].text
        plugin._create_lifecycle_background_task = Mock()
        result = MessageEventResult(chain=[Plain("第一段。第二段。")])

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = "普通聊天"
            _private_companion_reaction_expression_intent = {"query": "开心"}
            _private_companion_deferred_reaction_tts = {
                "normalized": "第一段。第二段。",
                "fallback_plain": "第一段。第二段。",
                "started_at": 10.0,
            }

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        self.assertEqual(["第一段。"], [item.text for item in event.result.chain])
        self.assertEqual(
            chunks,
            event._private_companion_reaction_expression_expected_primary_chunks,
        )
        self.assertEqual(
            [chunks[1]],
            event._private_companion_reaction_expression_segmented_remainder[
                "chunks"
            ],
        )
        plugin._create_lifecycle_background_task.assert_not_called()

    async def test_logged_reaction_reply_uses_active_segment_rules_after_tts(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_segmented_proactive_reply = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin.enable_proactive_quote_trigger_message = False
        plugin.enable_segmented_proactive_content_replacement = False
        plugin.segmented_proactive_content_replacements = []
        plugin.segmented_proactive_threshold = 500
        plugin.segmented_proactive_min_segment_chars = 5
        plugin.segmented_proactive_max_segments = 5
        plugin.segmented_proactive_split_mode = "words"
        plugin.segmented_proactive_split_words = [
            "。",
            "？",
            "！",
            "~",
            "?",
            ".",
            "!",
            ";",
            "；",
            "……",
            "（",
            "“",
            "，",
            "…",
        ]
        plugin.segmented_proactive_regex = r"(?<=[。！？!?…~～])\s*|\n+"
        plugin.enable_segmented_proactive_content_cleanup = True
        plugin.segmented_proactive_content_cleanup_rule = r"[\n。？！]"
        plugin.segmented_proactive_content_cleanup_scope = "all"
        plugin.segmented_proactive_content_cleanup_words = ["。", "，"]
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._platform_supports = lambda *_args, **_kwargs: True
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._create_lifecycle_background_task = Mock()
        source = (
            "唔，我们群主大人确实是能躺着绝不坐着的类型啦～"
            "不过能指挥大家干活也算一种本事喵"
        )
        result = MessageEventResult(chain=[Plain(source)])

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = "例行检查"
            _private_companion_reaction_expression_intent = {"query": "回应"}
            _private_companion_deferred_reaction_tts = {
                "normalized": source,
                "fallback_plain": source,
                "started_at": 10.0,
            }

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)

        expected = event._private_companion_reaction_expression_expected_primary_chunks
        self.assertEqual(2, len(expected))
        self.assertEqual(expected[0], event.result.chain)
        self.assertEqual(
            [expected[1]],
            event._private_companion_reaction_expression_segmented_remainder[
                "chunks"
            ],
        )
        self.assertTrue("啦～" in "".join(item.text for item in expected[0]))
        self.assertTrue("不过" in "".join(item.text for item in expected[1]))
        self.assertNotEqual(source, "".join(item.text for item in expected[0]))
        plugin._create_lifecycle_background_task.assert_not_called()

    async def test_segmenting_hook_leaves_official_tts_llm_result_untouched(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.segmented_proactive_scope = "all_llm"
        plugin.context = _OfficialTtsHarness().context
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = lambda key: key == "enable_segmented_proactive_reply"
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._segment_llm_reply_chain = Mock(
            side_effect=AssertionError("official TTS result must not enter plugin segmenting")
        )
        result = _llm_result(Plain("第一段。第二段。"))

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"

            @staticmethod
            def get_result() -> MessageEventResult:
                return result

        with patch(
            "astrbot.core.star.session_llm_manager.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ):
            await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, Event())

        plugin._segment_llm_reply_chain.assert_not_called()
        self.assertTrue(result.is_llm_result())

    async def test_enabled_official_tts_owns_unmodified_llm_text(self) -> None:
        harness = _OfficialTtsHarness()
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("第一段。第二段。"))

        with patch(
            "astrbot.core.star.session_llm_manager.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ):
            should_defer = await harness._should_defer_segmenting_to_astrbot_tts(
                event,
                result,
                list(result.chain),
            )

        self.assertTrue(should_defer)
        self.assertTrue(result.is_llm_result())

    async def test_official_tts_preflight_removes_reply_quote(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.context = _OfficialTtsHarness().context
        result = _llm_result(Reply(id="quoted-message"), Plain("交给官方 TTS。"))

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            @staticmethod
            def get_result() -> MessageEventResult:
                return result

        with patch(
            "astrbot.core.star.session_llm_manager.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=True),
        ):
            await PrivateCompanionPlugin.attach_group_reply_quote(plugin, Event())

        self.assertEqual(["Plain"], [type(component).__name__ for component in result.chain])

    async def test_session_tts_override_is_respected(self) -> None:
        harness = _OfficialTtsHarness()
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("保持文字。"))

        with patch(
            "astrbot.core.star.session_llm_manager.SessionServiceManager.should_process_tts_request",
            new=AsyncMock(return_value=False),
        ):
            should_defer = await harness._should_defer_segmenting_to_astrbot_tts(
                event,
                result,
                list(result.chain),
            )

        self.assertFalse(should_defer)

    async def test_disabled_or_zero_probability_official_tts_does_not_take_over(self) -> None:
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("保持插件分段。"))

        for harness in (
            _OfficialTtsHarness(enabled=False),
            _OfficialTtsHarness(probability=0),
            _OfficialTtsHarness(probability="0"),
        ):
            with self.subTest(harness=harness):
                should_defer = await harness._should_defer_segmenting_to_astrbot_tts(
                    event,
                    result,
                    list(result.chain),
                )
                self.assertFalse(should_defer)

    async def test_empty_or_invalid_probability_matches_astrbot_default(self) -> None:
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("交给官方 TTS。"))

        for probability in (None, "", "invalid"):
            with self.subTest(probability=probability):
                harness = _OfficialTtsHarness(probability=probability)
                with patch(
                    "astrbot.core.star.session_llm_manager.SessionServiceManager.should_process_tts_request",
                    new=AsyncMock(return_value=True),
                ):
                    should_defer = await harness._should_defer_segmenting_to_astrbot_tts(
                        event,
                        result,
                        list(result.chain),
                    )
                self.assertTrue(should_defer)

    async def test_plugin_owned_or_media_result_never_reenters_official_tts(self) -> None:
        harness = _OfficialTtsHarness()
        plugin_event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            _private_companion_tts_request_applied=True,
        )
        plain_result = _llm_result(Plain("插件负责本轮语音。"))
        media_event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        media_result = _llm_result(Record(file="voice.wav"), Plain("语音对应正文。"))

        self.assertFalse(
            await harness._should_defer_segmenting_to_astrbot_tts(
                plugin_event,
                plain_result,
                list(plain_result.chain),
            )
        )
        self.assertFalse(
            await harness._should_defer_segmenting_to_astrbot_tts(
                media_event,
                media_result,
                list(media_result.chain),
            )
        )

    async def test_plugin_owned_probability_miss_cannot_fall_through_to_official_tts(self) -> None:
        harness = _OfficialTtsHarness()
        harness.enabled = True
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            _private_companion_tts_request_applied=True,
        )
        result = _llm_result(Plain("插件本轮决定保持文字。"))
        event.get_result = lambda: result

        await harness.finalize_outbound_tts_markup_guard(event)

        self.assertFalse(result.is_llm_result())
        self.assertEqual("插件本轮决定保持文字。", result.chain[0].text)

    async def test_unowned_llm_result_remains_available_to_official_tts(self) -> None:
        harness = _OfficialTtsHarness()
        harness.enabled = True
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("交给 AstrBot 官方 TTS。"))
        event.get_result = lambda: result

        await harness.finalize_outbound_tts_markup_guard(event)

        self.assertTrue(result.is_llm_result())

    async def test_missing_official_provider_keeps_plugin_segmenting(self) -> None:
        harness = _OfficialTtsHarness()
        harness.context.get_using_tts_provider = lambda _umo: None
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        result = _llm_result(Plain("保持插件分段。"))

        should_defer = await harness._should_defer_segmenting_to_astrbot_tts(
            event,
            result,
            list(result.chain),
        )

        self.assertFalse(should_defer)

    async def test_plugin_strips_cross_plain_thinking_before_scope_early_returns(self) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        cases = {
            "proactive_only_passive_block": lambda plugin: setattr(
                plugin, "_proactive_only_blocks_passive_event", lambda *_a: True
            ),
            "segmented_scope_passive_early_return": lambda plugin: setattr(
                plugin, "_proactive_only_blocks_passive_event", lambda *_a: False
            ),
        }
        for case_name, configure in cases.items():
            with self.subTest(case=case_name):
                plugin = object.__new__(PrivateCompanionPlugin)
                plugin.enabled = True
                plugin._feature_enabled_or_temp_unlocked = (
                    lambda key: key == "enable_segmented_proactive_reply"
                )
                plugin._segmented_scope_allows_event = lambda _event: True
                plugin._segmented_setting = lambda *_a, **_k: "proactive_only"
                configure(plugin)
                result = _llm_result(
                    Plain("  thinking"),
                    Plain("推理内容被拆分\n  /response"),
                    Plain("正文可见"),
                )

                class Event:
                    unified_msg_origin = "default:FriendMessage:10001"
                    message_str = "普通被动回复"

                    def __init__(self) -> None:
                        self.result = result

                    def get_result(self) -> MessageEventResult:
                        return self.result

                await PrivateCompanionPlugin.apply_segmented_llm_reply_scope(
                    plugin, Event()
                )

                plain_texts = [
                    str(getattr(component, "text", "") or "").strip()
                    for component in result.chain
                    if isinstance(component, Plain)
                    and str(getattr(component, "text", "") or "").strip()
                ]
                self.assertEqual(["正文可见"], plain_texts)


if __name__ == "__main__":
    unittest.main()
