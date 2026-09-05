# -*- coding: utf-8 -*-
"""Characterize the read-only giant-mixin dependency inventory artifact."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "giant_mixin_dependency_report.json"
FOCUS = {
    "daily_state",
    "proactive_message",
    "proactive_engine",
    "user_memory",
    "command_handlers",
    "core_store",
    "event_dispatch",
    "page_api",
    "llm_tool_actions",
}


def test_dependency_report_covers_every_gate_giant_and_priority_module() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    modules = {item["module"]: item for item in payload["modules"]}

    assert payload["scope"]["gate_giant_mixins"] == 41
    assert sum(item["is_gate_giant"] for item in modules.values()) == 41
    assert FOCUS <= modules.keys()
    assert payload["scope"]["modules_reported"] == len(modules)


def test_pure_candidates_include_traceable_boundaries_and_direction() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))

    assert payload["summary"]["candidate_methods"] > 0
    for module in payload["modules"]:
        direction = module["dependency_direction"]
        assert "runtime/mixin -> extracted pure policy" in direction["recommended"]
        for candidate in module["pure_rule_candidates"]:
            assert candidate["line_start"] <= candidate["line_end"]
            assert candidate["confidence"] in {"high", "medium"}
            # Existing instance methods may retain an unused ``self`` parameter;
            # static methods may legitimately have no parameters.
            assert isinstance(candidate["parameters"], list)
            assert candidate["independent_test_boundary"]
