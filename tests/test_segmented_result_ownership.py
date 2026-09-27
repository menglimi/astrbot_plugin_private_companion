# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType

# PyO3 environment guard: python_ripgrep (PyO3 C extension) can only be
# initialized once per interpreter process. If importing the plugin main
# module triggers this restriction, skip all tests in this file.
try:
    from astrbot_plugin_private_companion import main as _pcompanion_main
    _PYO3_IMPORT_OK = True
except ImportError:
    _PYO3_IMPORT_OK = False
    _pcompanion_main = None


def test_unmarked_general_result_is_not_resegmented():
    if not _PYO3_IMPORT_OK:
        pytest.skip("PyO3 env: python_ripgrep can only be initialized once per process")
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    plugin = object.__new__(PrivateCompanionPlugin)
    plugin.enabled = True
    plugin.segmented_proactive_scope = "all_llm"
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
    if not _PYO3_IMPORT_OK:
        pytest.skip("PyO3 env: python_ripgrep can only be initialized once per process")
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    plugin = object.__new__(PrivateCompanionPlugin)
    result = plugin._build_result_from_chain([Plain("本插件回复。")])

    assert getattr(result, "_private_companion_owned_result", False) is True


def test_segmented_rebuild_preserves_llm_metadata_without_marking_independent_reply():
    if not _PYO3_IMPORT_OK:
        pytest.skip("PyO3 env: python_ripgrep can only be initialized once per process")
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    plugin = object.__new__(PrivateCompanionPlugin)
    source = MessageEventResult(chain=[Plain("模型原始回复")])
    source.set_result_content_type(ResultContentType.LLM_RESULT)

    rebuilt = plugin._build_segmented_result_from_chain(
        [Plain("模型分段回复")],
        source,
    )
    assert rebuilt.is_llm_result() is True

    independent = plugin._build_result_from_chain([Plain("插件自身回复")])
    assert independent.is_llm_result() is False
