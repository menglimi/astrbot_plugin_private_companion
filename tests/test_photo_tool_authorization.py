from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_photo_tool_rechecks_companion_user_authorization() -> None:
    source = (ROOT / "llm_tool_actions.py").read_text(encoding="utf-8")
    assert 'target_checker = getattr(self, "_is_target_private_user", None)' in source
    assert '"status": "unauthorized"' in source
    assert "这个生图工具只对已启用的陪伴对象开放。" in source
