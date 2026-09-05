# -*- coding: utf-8 -*-
"""Orchestration for the cross-user interaction query.

I/O remains behind the host's narrow history/target methods; this module only
chooses a query path and builds the existing response DTO.
"""
from __future__ import annotations

import json
from typing import Any

from .interaction_tool_contract import InteractionQuery, tool_response


async def execute_interaction_query(host: Any, event: Any, query: InteractionQuery) -> str:
    platform = host._interaction_query_platform(event)
    target_hint = query.target_hint
    if query.scope == "private":
        targets = host._interaction_query_private_targets(query.user_hint or query.hint)
        if not targets:
            return tool_response(status="not_found", message="没有找到匹配的私聊对象", hint=target_hint)
        if len(targets) > 1 and not (query.user_hint or query.hint).isdigit():
            return json.dumps({"status": "ambiguous", "message": "匹配到多个私聊对象，需要补充用户 ID 或更明确称呼", "matches": targets[:8]}, ensure_ascii=False)
        target = targets[0]
        user_id = target.get("user_id", "")
        umo = f"{platform}:FriendMessage:{user_id}"
        history = await host._interaction_query_read_history(umo, limit=query.limit, hours=query.hours)
        lines = host._interaction_query_lines(history, limit=min(query.limit, 28))
        return json.dumps(
            {
                "status": "success" if lines else "empty",
                "scope": "private",
                "target": target,
                "session": umo,
                "hours": query.hours,
                "message_count": len(lines),
                "recent_lines": lines,
                "reply_hint": "请用自然口吻向主要用户概括最近互动；可以提到对象和大致话题，不要大段复述原文。",
            },
            ensure_ascii=False,
        )

    if query.user_hint and not (query.group_hint or query.hint):
        lines = host._interaction_query_group_user_recent_lines(
            query.user_hint, limit=min(query.limit, 36), hours=query.hours
        )
        return json.dumps(
            {
                "status": "success" if lines else "empty",
                "scope": "group_user",
                "target": {"user_hint": query.user_hint},
                "hours": query.hours,
                "message_count": len(lines),
                "recent_lines": lines,
                "reply_hint": "请概括这个人最近在群里的发言和互动；如果线索不足，就说明目前只看到这些近期群聊记录。",
            },
            ensure_ascii=False,
        )

    targets = await host._interaction_query_group_targets(event, query.group_hint or query.hint)
    if not targets:
        return tool_response(status="not_found", message="没有找到匹配的群聊", hint=target_hint)
    if len(targets) > 1 and not (query.group_hint or query.hint).isdigit():
        return json.dumps({"status": "ambiguous", "message": "匹配到多个群聊，需要补充群号或更明确群名", "matches": targets[:8]}, ensure_ascii=False)
    target = targets[0]
    group_id = target.get("group_id", "")
    umo = f"{platform}:GroupMessage:{group_id}"
    if query.user_hint:
        lines = host._interaction_query_group_recent_lines(
            group_id,
            limit=min(query.limit, 28),
            user_hint=query.user_hint,
            hours=query.hours,
        )
    else:
        history = await host._interaction_query_read_history(umo, limit=query.limit, hours=query.hours)
        lines = host._interaction_query_lines(history, limit=min(query.limit, 28))
        if not lines:
            lines = host._interaction_query_group_recent_lines(
                group_id, limit=min(query.limit, 28), hours=query.hours
            )
    return json.dumps(
        {
            "status": "success" if lines else "empty",
            "scope": "group",
            "target": target,
            "user_hint": query.user_hint,
            "session": umo,
            "hours": query.hours,
            "message_count": len(lines),
            "recent_lines": lines,
            "reply_hint": "请用自然口吻向主要用户概括 Bot 最近在这个群里的互动；不要把群聊原文整段搬出来。",
        },
        ensure_ascii=False,
    )
