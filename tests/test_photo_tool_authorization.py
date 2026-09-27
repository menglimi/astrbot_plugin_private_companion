from __future__ import annotations

from pathlib import Path

from tests.module_source_index import llm_tool_actions_source_text


ROOT = Path(__file__).resolve().parents[1]


def test_photo_tool_uses_scope_without_legacy_private_target_permission() -> None:
    source = llm_tool_actions_source_text(ROOT)
    commands = (ROOT / "command_handlers.py").read_text(encoding="utf-8")
    assert 'target_checker = getattr(self, "_is_target_private_user", None)' not in source
    assert '"status": "unauthorized"' in source
    assert "requester_authorized = (group_enabled if request_scope == \"group\" else isinstance(requester, dict))" in source
    assert "这个生图入口只对已启用的陪伴对象开放。" not in commands
    assert "这个规则快判生图/改图入口只对主要用户开放。" not in commands
