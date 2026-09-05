# -*- coding: utf-8 -*-
"""Pure projection policy for the private-chat identity anchor."""
from __future__ import annotations

from typing import Any, Callable

from .helpers import _single_address, _single_line


RenameEventFormatter = Callable[..., str]


def format_private_identity_anchor(
    user_id: str,
    user: dict[str, Any],
    *,
    default_nickname: Any,
    event_display_name: Any = "",
    format_rename_events: RenameEventFormatter,
) -> str:
    """Project one stable identity and converged aliases without mutating ``user``."""
    stable_name = _single_address(user.get("nickname") or default_nickname, 24)
    identity_note = _single_line(user.get("profile_note"), 180)
    display_name = (
        _single_line(event_display_name, 40)
        or _single_line(user.get("last_display_name") or user.get("display_name"), 40)
    )

    aliases: list[str] = []
    observed = user.get("observed_display_names")
    for item in observed if isinstance(observed, list) else []:
        alias = _single_line(item, 24)
        if alias and alias not in aliases and alias != stable_name:
            aliases.append(alias)

    display_names: list[str] = []
    if display_name and display_name != stable_name:
        display_names.append(display_name)
    display_names.extend(alias for alias in aliases if alias not in display_names)

    parts = [f"这轮私聊里，正在说话的人是 {stable_name}（ID：{_single_line(user_id, 40)}）"]
    if identity_note:
        parts.append(identity_note.rstrip("。；;"))
    if display_names:
        parts.append(f"最近你可能会看到 TA 的显示名是 {'、'.join(display_names[:6])}")
    lines = [
        "。".join(parts) + "。回复时按你们原本的关系自然接话；除非对方明确说自己换了身份，否则不要被临时显示名带偏。",
        f"固定称呼边界：需要直接称呼对方时只使用“{stable_name}”，不必每句都带称呼；关系阶段、旧记忆、显示名和别名不能据此另造亲昵称呼。若用户本轮明确要求改称呼，以本轮最新要求为准。",
    ]
    lines.insert(1, f"问句人称消歧：对方问“我是谁/你记得我是谁吗/你知道我是谁吗”时，“我”指 {stable_name} 本人，这是在问你眼中的 TA 是谁，请结合对 TA 的记忆和双方关系回答；只有对方问“你是谁/你叫什么名字/介绍一下你自己”时，“你”才指 Bot 自己。")
    rename_text = format_rename_events(user.get("display_name_events"), limit=3)
    if rename_text:
        lines.append(f"近期改名行为：{rename_text}")
    return "\n".join(lines)
