# -*- coding: utf-8 -*-
"""Parsing and response contracts for the ``陪伴群`` command family.

This module is deliberately framework-free: parsing, permission classification and
response formatting can be tested without constructing an AstrBot event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class GroupLLMAction(str, Enum):
    BLOCK = "block"
    RESTORE = "restore"
    STATUS = "status"


_LLM_BLOCK_ALIASES = frozenset(
    {
        "关闭llm", "关闭llm回复", "关闭所有llm回复", "禁用llm", "禁用llm回复",
        "停用llm", "停用llm回复", "禁止llm", "禁止llm回复", "关闭主链", "关闭主链回复",
    }
)
_LLM_RESTORE_ALIASES = frozenset(
    {
        "开启llm", "开启llm回复", "开启所有llm回复", "启用llm", "启用llm回复",
        "打开llm", "打开llm回复", "恢复llm", "恢复llm回复", "恢复主链", "恢复主链回复",
    }
)
_LLM_STATUS_ALIASES = frozenset({"llm状态", "主链状态", "llm回复状态"})
_MANAGEMENT_ACTIONS = frozenset(
    {"开启", "启用", "打开", "关闭", "停用", "关掉", "撤回消息", "防撤回", "转述撤回", "撤回转述"}
)


@dataclass(frozen=True, slots=True)
class GroupCommandRequest:
    action: str
    value: str
    compact: str
    llm_action: GroupLLMAction | None

    @property
    def requires_management(self) -> bool:
        return self.llm_action is not None or self.action in _MANAGEMENT_ACTIONS


def parse_group_command(message: object) -> GroupCommandRequest:
    """Parse command arguments while retaining the historical split semantics."""
    parts = str(message or "").strip().split(maxsplit=2)
    action = parts[1].strip() if len(parts) >= 2 else ""
    value = parts[2].strip() if len(parts) >= 3 else ""
    compact = re.sub(r"\s+", "", f"{action}{value}").lower()
    if compact in _LLM_BLOCK_ALIASES:
        llm_action = GroupLLMAction.BLOCK
    elif compact in _LLM_RESTORE_ALIASES:
        llm_action = GroupLLMAction.RESTORE
    elif compact in _LLM_STATUS_ALIASES:
        llm_action = GroupLLMAction.STATUS
    else:
        llm_action = None
    return GroupCommandRequest(action=action, value=value, compact=compact, llm_action=llm_action)


def format_llm_blocked(group_id: str, elapsed: str) -> str:
    return (
        "已关闭本群所有 LLM 回复。\n"
        f"群号：{group_id}\n"
        f"状态：拦截中（{elapsed}）\n"
        "恢复：陪伴群 开启LLM"
    )


def format_llm_status(*, blocked: bool, elapsed: str = "") -> str:
    if blocked:
        return f"本群 LLM 回复当前关闭中，开启时间：{elapsed}。\n恢复：陪伴群 开启LLM"
    return "本群 LLM 回复当前未被单独关闭。"


GROUP_COMMAND_HELP = (
    "群聊陪伴命令：\n"
    "陪伴群 状态\n"
    "陪伴群 黑话\n"
    "陪伴群 群友\n"
    "陪伴群 话题\n"
    "陪伴群 片段\n"
    "陪伴群 插话反馈\n"
    "陪伴群 关系网\n"
    "陪伴群 撤回消息\n"
    "陪伴群 LLM状态\n"
    "陪伴群 关闭LLM\n"
    "陪伴群 开启LLM\n"
    "陪伴群 开启\n"
    "陪伴群 关闭"
)
