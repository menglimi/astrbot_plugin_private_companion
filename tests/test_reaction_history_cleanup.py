# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.passive_state_pipeline import inject_humanized_state


def test_neutralize_stale_reaction_feedback_preserves_surrounding_feedback() -> None:
    plugin = object.__new__(PrivateCompanionPlugin)
    request = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": "收到你的反馈\n<pc-reaction-expression kind='smile'>\n内部标签\n</pc-reaction-expression>\n谢谢",
            },
            {
                "role": "user",
                "content": [{"text": "&lt;PC_REACTION_EXPRESSION&gt;hidden&lt;/PC_REACTION_EXPRESSION&gt;"}],
            },
        ]
    )

    plugin._neutralize_stale_reaction_feedback_in_history(
        SimpleNamespace(unified_msg_origin="test"), request
    )

    assert request.contexts[0]["content"] == "收到你的反馈\n\n谢谢"
    assert request.contexts[1]["content"][0]["text"] == ""


def test_hot_loaded_instance_without_member_uses_pipeline_compat_cleanup() -> None:
    async def append_unlocked(*_args, **_kwargs) -> None:
        return None

    plugin = SimpleNamespace(
        enabled=True,
        _record_photo_reference_feedback_from_event=lambda _event: None,
        _stop_group_llm_reply_if_blocked=lambda *_args, **_kwargs: False,
        _sanitize_request_context_new_conversation_boundary=lambda *_args: None,
        _repair_incomplete_tool_context_groups=lambda *_args: None,
        _sanitize_private_companion_prompt_artifacts_in_request=lambda *_args: None,
        _append_deepseek_tool_protocol_guard=lambda *_args: None,
        _append_passive_reply_tool_boundary=lambda *_args: None,
        _remember_external_llm_request_for_token_stats=lambda *_args: None,
        _proactive_only_limited_passive_event=lambda _event: True,
        _proactive_only_blocks_passive_event=lambda *_args: False,
        _proactive_only_llm_request_needs_full_path=lambda: False,
        _append_proactive_only_unlocked_llm_request_fragments=append_unlocked,
        _log_bookshelf_secret_skip=lambda *_args, **_kwargs: None,
    )
    request = SimpleNamespace(
        system_prompt="test",
        contexts=[
            {
                "role": "assistant",
                "content": [
                    {"text": "保留"},
                    {
                        "value": "&lt;PC_REACTION_EXPRESSION&gt;hidden"
                        "&lt;/PC_REACTION_EXPRESSION&gt;"
                    },
                ],
            }
        ],
    )

    assert not hasattr(plugin, "_neutralize_stale_reaction_feedback_in_history")
    asyncio.run(
        inject_humanized_state(
            plugin,
            SimpleNamespace(message_str="", private_companion_group_text=""),
            request,
        )
    )

    assert request.contexts[0]["content"] == [
        {"text": "保留"},
        {"value": ""},
    ]
