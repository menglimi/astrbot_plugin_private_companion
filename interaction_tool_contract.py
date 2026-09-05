# -*- coding: utf-8 -*-
"""Parameter contract and response serialization for interaction-query tools."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .helpers import _safe_int, _single_line


_PRIVATE_SCOPES = frozenset({"私聊", "好友", "friend", "private_message", "user"})
_GROUP_SCOPES = frozenset({"群", "群聊", "group_message"})


def first_value(arguments: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    """Read the first truthy legacy alias from tool arguments."""
    for name in names:
        value = arguments.get(name)
        if value:
            return value
    return default


@dataclass(frozen=True, slots=True)
class InteractionQuery:
    scope: str
    user_hint: str
    group_hint: str
    hint: str
    hours: int
    limit: int

    @property
    def target_hint(self) -> str:
        return self.user_hint or self.group_hint or self.hint

    @classmethod
    def parse(cls, arguments: Mapping[str, Any]) -> "InteractionQuery":
        scope = _single_line(first_value(arguments, "scope", "type", default="auto"), 20).lower()
        user_hint = _single_line(
            first_value(arguments, "user_hint", "user", "user_id", "target_user"),
            128,
        )
        group_hint = _single_line(
            first_value(arguments, "group_hint", "group", "group_id", "target_group"),
            80,
        )
        hint = _single_line(first_value(arguments, "hint", "target", "name"), 80)
        hours = max(1, min(24 * 30, _safe_int(arguments.get("hours"), 72, 1)))
        limit = max(5, min(80, _safe_int(arguments.get("limit"), 36, 5)))

        if scope in _GROUP_SCOPES:
            scope = "group"
        elif scope in _PRIVATE_SCOPES:
            scope = "private"
        elif scope not in {"auto", "private", "group"}:
            scope = "auto"
        if scope == "auto":
            if group_hint:
                scope = "group"
            elif user_hint:
                scope = "private"
            elif hint and "群" in hint:
                scope = "group"
            else:
                scope = "private"
        return cls(scope, user_hint, group_hint, hint, hours, limit)


def tool_response(**payload: Any) -> str:
    """Serialize with the legacy Unicode-visible JSON settings."""
    return json.dumps(payload, ensure_ascii=False)
