# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

from astrbot_plugin_private_companion.passive_state_pipeline import inject_humanized_state


class PassiveGroupContextDecouplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_context_still_injects_when_passive_states_are_disabled(self) -> None:
        captured_fragment = {}

        def append_fragment(_req, _marker, text, **kwargs):
            captured_fragment["text"] = text
            captured_fragment.update(kwargs)
            return False

        plugin = SimpleNamespace(
            enabled=True,
            data={"users": {}},
            _record_photo_reference_feedback_from_event=lambda _event: None,
            _stop_group_llm_reply_if_blocked=lambda *_args, **_kwargs: False,
            _sanitize_request_context_new_conversation_boundary=lambda *_args: None,
            _repair_incomplete_tool_context_groups=lambda *_args: None,
            _sanitize_private_companion_prompt_artifacts_in_request=lambda *_args: None,
            _append_deepseek_tool_protocol_guard=lambda *_args: None,
            _append_passive_reply_tool_boundary=lambda *_args: None,
            _remember_external_llm_request_for_token_stats=lambda *_args: None,
            _proactive_only_limited_passive_event=lambda _event: False,
            _proactive_only_blocks_passive_event=lambda *_args: False,
            _should_reply_during_rest=AsyncMock(return_value=(True, "disabled")),
            _apply_busy_reply_gate_delay=AsyncMock(return_value=(0.0, "disabled")),
            _trim_passive_request_context_if_needed=lambda *_args, **_kwargs: None,
            _enrich_request_context_image_placeholders=AsyncMock(),
            _append_group_image_understanding_to_request=AsyncMock(),
            apply_tts_enhancement_request=AsyncMock(),
            _append_forward_message_context_to_request=AsyncMock(),
            _mark_group_conversation_from_llm_request=AsyncMock(),
            _append_group_injection_guard_to_request=AsyncMock(),
            _append_group_persona_denoise_to_request=AsyncMock(),
            _append_group_high_intensity_reply_guard_to_request=AsyncMock(),
            _append_group_member_safety_hidden_marker_to_request=AsyncMock(),
            _append_daily_review_guidance_to_request=AsyncMock(),
            _append_weather_query_context_to_request=AsyncMock(),
            _feature_enabled_or_temp_unlocked=lambda key: key == "enable_group_companion",
            _extract_group_id_from_event=lambda _event: "group-1",
            _group_enabled_for_event=lambda _group_id: True,
            _get_group=lambda _group_id: {"recent_messages": []},
            _expression_voice_selection=lambda **_kwargs: {},
            _consume_semantic_message_buffer_for_event=AsyncMock(return_value=""),
            _user_asks_recalled_messages=lambda _text: False,
            _format_group_passive_reply_context_for_prompt=lambda *_args: "【群聊回复补充】\n真实最近群聊：\n- 群友: 你好",
            _group_slang_embedding_context=AsyncMock(return_value="本群黑话释义"),
            _format_recent_atrelay_context_for_prompt=lambda **_kwargs: "刚刚的转述",
            _append_turn_prompt_fragment_by_position=append_fragment,
            _record_request_prompt_fragment=AsyncMock(),
            _append_group_active_period_boundary_to_request=AsyncMock(),
            _memory_companion_should_defer_prompt_section=lambda *_args: True,
            _append_reply_style_to_request=AsyncMock(),
            _append_conditional_tool_instructions_to_request=AsyncMock(),
            _append_environment_perception_to_request=AsyncMock(),
            _log_bookshelf_secret_skip=lambda *_args, **_kwargs: None,
        )
        event = SimpleNamespace(
            unified_msg_origin="qq_official:GroupMessage:group-1",
            message_str="你好",
            private_companion_group_text="你好",
            get_sender_id=lambda: "user-1",
            is_private_chat=lambda: False,
        )
        request = SimpleNamespace(
            system_prompt="群聊人格",
            prompt="你好",
            contexts=[],
            extra_user_content_parts=[],
        )

        def persona_setting(_plugin, key, default=None):
            values = {
                "enable_group_reality_promise_guard": False,
                "enable_group_context_injection": True,
            }
            return values.get(key, default)

        with patch(
            "astrbot_plugin_private_companion.passive_state_pipeline.runtime_persona_setting",
            side_effect=persona_setting,
        ):
            await inject_humanized_state(plugin, event, request)

        self.assertIn("private_companion_group_context_v1", request.system_prompt)
        self.assertIn("真实最近群聊", request.system_prompt)
        payload = ET.fromstring(captured_fragment["text"])
        self.assertEqual(
            ["群内黑话语义近似（仅作软参考）", "刚刚的转述动作", "群聊上下文"],
            [item.attrib["title"] for item in payload.findall("./section")],
        )
        self.assertEqual(10_000, captured_fragment["priority"])
        plugin._record_request_prompt_fragment.assert_awaited_once()
        plugin._append_group_active_period_boundary_to_request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
