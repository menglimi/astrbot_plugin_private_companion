"""Dispatch durable relay tasks from authenticated inbound events."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from companion_v2.foundation.refs import ScopeKind

from .private_notice import prepare_relay_transport, send_group_notice, send_private_notice
from .relay_pending import (claim_expired, claim_group, claim_private_receipts, finish,
                            mark_waiting_permission, requeue)


def _text(value, limit=800):
    return " ".join(str(value or "").split())[:limit]


def _platform_for(controller, platform_id):
    bridge = getattr(getattr(controller, "session", None), "bridge", None)
    pinned = getattr(bridge, "platforms", None)
    if isinstance(pinned, dict):
        item = pinned.get(platform_id)
        if isinstance(item, tuple) and item:
            return item[0]
    return None


def _route(event, controller, *, kind, platform_id, login, peer):
    return SimpleNamespace(
        kind=kind,
        platform_id=platform_id,
        login=login,
        peer=str(peer),
        client=getattr(event, "bot", None),
        platform=_platform_for(controller, platform_id),
        platform_name=str(getattr(getattr(event, "platform_meta", None), "name", "") or ""),
    )


def _native_controller(controller):
    return SimpleNamespace(native_types=controller.native_types)


async def _send_private(controller, event, *, platform_id, login, peer, text):
    route = _route(event, controller, kind=ScopeKind.PRIVATE, platform_id=platform_id, login=login, peer=peer)
    route.platform = _platform_for(controller, platform_id)
    transport = prepare_relay_transport(_native_controller(controller), route)
    return await send_private_notice(_native_controller(controller), None, text=text,
                                     route=route, transport=transport)


async def _send_group(controller, event, *, platform_id, login, peer, text, mention_id=""):
    route = _route(event, controller, kind=ScopeKind.GROUP, platform_id=platform_id, login=login, peer=peer)
    route.platform = _platform_for(controller, platform_id)
    transport = prepare_relay_transport(_native_controller(controller), route)
    return await send_group_notice(_native_controller(controller), route, text=text,
                                   mention_id=mention_id, transport=transport)


async def process_group_presence(controller, event, target, context):
    """Deliver delayed relays when their target member speaks in the group."""
    target_user_id = str(context.actor.principal.native_account_id)
    tasks = await asyncio.to_thread(claim_group, controller.runtime, str(target.group_id), target_user_id)
    for task in tasks:
        result = await _send_group(controller, event, platform_id=target.platform_id,
            login=target.account_id, peer=target.group_id,
            text=_text(task.get("text")), mention_id=str(task.get("mention_id") or ""))
        await asyncio.to_thread(finish, controller.runtime, task["id"],
                                "sent" if result.get("state") == "delivered" else "failed")
    # 带话一直没等到目标成员出现时，告诉发起窗口“已经撤回”，
    # 而不是让那条委托在队列里无声过期。
    expired = await asyncio.to_thread(claim_expired, controller.runtime, str(target.group_id))
    for task in expired:
        source_kind = str(task.get("source_kind") or "private")
        source_id = str(task.get("source_id") or task.get("source_user") or "")
        if not source_id:
            continue
        text = "你让我在群里带的那句话，对方一直没出现，我先撤回了。"
        if source_kind == "group":
            await _send_group(controller, event, platform_id=target.platform_id,
                login=target.account_id, peer=source_id, text=text)
        else:
            await _send_private(controller, event, platform_id=target.platform_id,
                login=target.account_id, peer=source_id, text=text)


async def _report_receipt(controller, event, target, task, reply_text):
    source_kind = str(task.get("source_kind") or "private")
    source_id = str(task.get("source_id") or task.get("source_user") or "")
    if not source_id:
        return False
    report = "你让我问的「%s」有回复了：%s" % (
        _text(task.get("question"), 120), _text(reply_text))
    if source_kind == "group":
        result = await _send_group(controller, event, platform_id=target.platform_id,
            login=target.account_id, peer=source_id, text=report)
    else:
        result = await _send_private(controller, event, platform_id=target.platform_id,
            login=target.account_id, peer=source_id, text=report)
    return result.get("state") == "delivered"


async def _ask_permission(controller, event, target, task):
    result = await _send_private(controller, event, platform_id=target.platform_id,
        login=target.account_id, peer=target.participant_id,
        text="可以把这条回复带回给发起人吗？回复「可以」或「不要」。")
    return result.get("state") == "delivered"


async def process_private_receipts(controller, event, target):
    """Handle a target member's reply and optionally ask before reporting it."""
    target_user_id = str(target.participant_id)
    text = _text(getattr(event, "message_str", ""), 800)
    confirmed = any(token in text for token in ("可以", "同意", "好", "行", "转告", "带回去", "转发"))
    rejected = any(token in text for token in ("不要", "不用", "不行", "不可以", "拒绝", "别转", "算了"))

    permission_tasks = await asyncio.to_thread(claim_private_receipts, controller.runtime,
                                               target_user_id, permission=True)
    for task in permission_tasks:
        if rejected:
            await asyncio.to_thread(finish, controller.runtime, task["id"], "declined")
        elif confirmed:
            ok = await _report_receipt(controller, event, target, task,
                                       _text(task.get("reply_text"), 800))
            await asyncio.to_thread(finish, controller.runtime, task["id"],
                                    "reported" if ok else "failed")
        else:
            await asyncio.to_thread(requeue, controller.runtime, task["id"])

    reply_tasks = await asyncio.to_thread(claim_private_receipts, controller.runtime, target_user_id)
    for task in reply_tasks:
        if task.get("confirm_before_report"):
            if await _ask_permission(controller, event, target, task):
                await asyncio.to_thread(mark_waiting_permission, controller.runtime, task["id"], text)
            else:
                await asyncio.to_thread(requeue, controller.runtime, task["id"])
        else:
            ok = await _report_receipt(controller, event, target, task, text)
            await asyncio.to_thread(finish, controller.runtime, task["id"],
                                    "reported" if ok else "failed")


__all__ = ["process_group_presence", "process_private_receipts"]
