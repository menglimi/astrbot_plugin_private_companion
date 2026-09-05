# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manual_config_display_meta_has_unique_keys() -> None:
    source = (ROOT / "command_handlers.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_companion_manual_config_display_meta"
    )
    returned = next(
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    )
    keys = [key.value for key in returned.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)

    assert duplicates == []
    metadata = ast.literal_eval(returned)
    assert metadata["proactive_review_strength"] == {
        "label": "主动消息终审强度",
        "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情",
    }


def test_command_handlers_imports_the_logger_it_uses() -> None:
    tree = ast.parse((ROOT / "command_handlers.py").read_text(encoding="utf-8"))
    logger_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "astrbot.api"
        and any(alias.name == "logger" for alias in node.names)
    ]

    assert len(logger_imports) == 1
