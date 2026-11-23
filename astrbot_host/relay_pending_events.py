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
    settled: set[str] = set()
    in_flight: str | None = None
    in_flight_state = "failed"
    try:
        for task in tasks:
            task_id = task["id"]
            in_flight = task_id
            in_flight_state = "failed"
            try:
                result = await _send_group(controller, event, platform_id=target.platform_id,
                    login=target.account_id, peer=target.group_id,
                    text=_text(task.get("text")), mention_id=str(task.get("mention_id") or ""))
                in_flight_state = "sent" if isinstance(result, dict) and result.get("state") == "delivered" else "failed"
            except Exception:
                in_flight_state = "failed"
            await asyncio.to_thread(finish, controller.runtime, task_id, in_flight_state)
            settled.add(task_id)
            in_flight = None
    finally:
        for task in tasks:
            task_id = task["id"]
            if task_id in settled:
                continue
            try:
                if task_id == in_flight:
                    finish(controller.runtime, task_id, in_flight_state)
                else:
                    requeue(controller.runtime, task_id, state="waiting")
            except Exception:
                pass
    # 带话一直没等到目标成员出现时，告诉发起窗口“已经撤回”，
    # 而不是让那条委托在队列里无声过期。
    expired = await asyncio.to_thread(claim_expired, controller.runtime, str(target.group_id))
    for task in expired:
        source_kind = str(task.get("source_kind") or "private")
        source_id = str(task.get("source_id") or task.get("source_user") or "")
        if not source_id:
            continue
        text = "你让我在群里带的那句话，对方一直没出现，我先撤回了。"
        try:
            if source_kind == "group":
                await _send_group(controller, event, platform_id=target.platform_id,
                    login=target.account_id, peer=source_id, text=text)
            else:
                await _send_private(controller, event, platform_id=target.platform_id,
                    login=target.account_id, peer=source_id, text=text)
        except Exception:
            continue


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
    settled_permission: set[str] = set()
    in_flight_permission: str | None = None
    in_flight_permission_state = "failed"
    try:
        for task in permission_tasks:
            task_id = task["id"]
            if rejected:
                in_flight_permission = task_id
                in_flight_permission_state = "declined"
                await asyncio.to_thread(finish, controller.runtime, task_id, "declined")
            elif confirmed:
                in_flight_permission = task_id
                in_flight_permission_state = "failed"
                try:
                    ok = await _report_receipt(controller, event, target, task,
                                               _text(task.get("reply_text"), 800))
                except Exception:
                    ok = False
                in_flight_permission_state = "reported" if ok else "failed"
                await asyncio.to_thread(finish, controller.runtime, task_id, in_flight_permission_state)
            else:
                await asyncio.to_thread(requeue, controller.runtime, task_id, state="waiting_permission")
            settled_permission.add(task_id)
            in_flight_permission = None
    finally:
        for task in permission_tasks:
            task_id = task["id"]
            if task_id in settled_permission:
                continue
            try:
                if task_id == in_flight_permission:
                    finish(controller.runtime, task_id, in_flight_permission_state)
                else:
                    requeue(controller.runtime, task_id, state="waiting_permission")
            except Exception:
                pass

    reply_tasks = await asyncio.to_thread(claim_private_receipts, controller.runtime, target_user_id)
    settled_reply: set[str] = set()
    in_flight_report: str | None = None
    in_flight_report_state = "failed"
    in_flight_ask: str | None = None
    try:
        for task in reply_tasks:
            task_id = task["id"]
            if task.get("confirm_before_report"):
                try:
                    asked = await _ask_permission(controller, event, target, task)
                except Exception:
                    asked = False
                if asked:
                    in_flight_ask = task_id
                    await asyncio.to_thread(mark_waiting_permission, controller.runtime, task_id, text)
                else:
                    await asyncio.to_thread(requeue, controller.runtime, task_id, state="waiting_reply")
            else:
                in_flight_report = task_id
                in_flight_report_state = "failed"
                try:
                    ok = await _report_receipt(controller, event, target, task, text)
                except Exception:
                    ok = False
                in_flight_report_state = "reported" if ok else "failed"
                await asyncio.to_thread(finish, controller.runtime, task_id, in_flight_report_state)
            settled_reply.add(task_id)
            in_flight_report = None
            in_flight_ask = None
    finally:
        for task in reply_tasks:
            task_id = task["id"]
            if task_id in settled_reply:
                continue
            try:
                if task_id == in_flight_report:
                    finish(controller.runtime, task_id, in_flight_report_state)
                elif task_id == in_flight_ask:
                    mark_waiting_permission(controller.runtime, task_id, text)
                else:
                    requeue(controller.runtime, task_id, state="waiting_reply")
            except Exception:
                pass


__all__ = ["process_group_presence", "process_private_receipts"]
