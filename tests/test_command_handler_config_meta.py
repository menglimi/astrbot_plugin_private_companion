# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from tests.module_source_index import class_body_defs_for_file, file_family_source_text


ROOT = Path(__file__).resolve().parents[1]


def test_manual_config_display_meta_has_unique_keys() -> None:
    # 拆分后 _companion_manual_config_display_meta 落在 command_handlers_cm_snapshot_config.py 的
    # CommandHandlersCmSnapshotConfigMixin 中，只读 command_handlers.py 宿主会漏掉。
    body = class_body_defs_for_file(
        ROOT, "command_handlers.py", "CommandHandlersMixin"
    )
    function = next(
        node
        for node in body
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
    """``command_handlers`` must bind the module-tagged logger it uses.

    The module logs through the shared ``logging_util`` proxy so its lines are
    prefixed with the ``[指令处理]`` tag (see ``logging_util._MODULE_LABELS``).
    Assert both halves of that contract: the proxy factory is imported exactly
    once, and every ``logger`` global is bound from it -- a bare
    ``from astrbot.api import logger`` would silently drop the module tag.
    """
    # 拆分后 logger 绑定在 command_handlers_shared.py 中，用 family 全文搜索。
    source = file_family_source_text(ROOT, "command_handlers.py")
    tree = ast.parse(source)

    proxy_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module is not None and "logging_util" in node.module
        and any(alias.name == "get_module_logger" for alias in node.names)
    ]
    assert len(proxy_imports) == 1

    bare_logger_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "astrbot.api"
        and any(alias.name == "logger" for alias in node.names)
    ]
    assert bare_logger_imports == []

    logger_bindings = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "logger" for target in node.targets)
    ]
    assert len(logger_bindings) == 1
    assert "get_module_logger" in ast.unparse(logger_bindings[0].value)
