from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_group_member_safety_defaults_to_reply_only() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["group_member_safety_hidden_marker_mode"]["default"] == "reply_only"
    assert (
        schema["group_observation_config"]["items"]["group_member_safety_hidden_marker_mode"]["default"]
        == "reply_only"
    )

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    safety_source = (ROOT / "group_member_safety.py").read_text(encoding="utf-8")
    panel_source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

    assert '"group_member_safety_hidden_marker_mode", "reply_only", "reply_only"' in main_source
    assert 'getattr(self, "group_member_safety_hidden_marker_mode", "reply_only")' in safety_source
    assert '"reply_only", "仅使用回复模型标签（推荐）"' in panel_source
