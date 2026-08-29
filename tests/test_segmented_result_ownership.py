# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageEventResult


def test_unmarked_general_result_is_not_resegmented():
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    plugin = object.__new__(PrivateCompanionPlugin)
    plugin.enabled = True
    plugin.segmented_proactive_scope = "all_llm"
    plugin.enable_framework_error_leak_guard = False
    plugin.enable_daily_case_review_experiment = False
    plugin._proactive_only_blocks_passive_event = lambda *_args: False
    plugin._feature_enabled_or_temp_unlocked = lambda _key: True
    plugin._segmented_scope_allows_event = lambda _event: True
    plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
    plugin._segmented_platform_allows = lambda **_kwargs: True
    plugin._segment_llm_reply_chain = Mock(
        return_value=([[Plain("第一段。")], [Plain("第二段。")]], True, "第一段。第二段。")
    )

    result = MessageEventResult(chain=[Plain("第一段。第二段。")])
    event = SimpleNamespace(
        unified_msg_origin="default:GroupMessage:10001",
        message_str="普通插件回复",
        get_result=lambda: result,
        set_result=lambda value: None,
    )

    import asyncio

    asyncio.run(PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event))

    plugin._segment_llm_reply_chain.assert_not_called()
    assert result.chain[0].text == "第一段。第二段。"


def test_plugin_built_result_carries_ownership_marker():
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    plugin = object.__new__(PrivateCompanionPlugin)
    result = plugin._build_result_from_chain([Plain("本插件回复。")])

    assert getattr(result, "_private_companion_owned_result", False) is True
