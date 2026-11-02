"""Small durable queue for delayed group relays and private reply receipts.

V2 keeps these tasks in the same SQLite runtime as the rest of the companion
state.  The queue is intentionally bounded and single-owner: every task belongs
to the authenticated account/scope that created it, and dispatch is performed
from an authenticated inbound event for that same account.
"""

from __future__ import annotations

import json
from time import time
from uuid import uuid4


KEY = "conversation.relay.pending.v1"
MAX_TASKS = 200
LIVE_STATES = frozenset({"waiting", "sending", "waiting_reply", "reporting", "waiting_permission"})
REPORTABLE_KINDS = frozenset({"group_wait", "private_receipt"})


def _read(runtime):
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (KEY,))
    if row is None:
        return {"tasks": {}}
    try:
        data = json.loads(row["value"])
    except (TypeError, ValueError):
        return {"tasks": {}}
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return {"tasks": tasks if isinstance(tasks, dict) else {}}


def _write(connection, data):
    connection.execute(
        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (KEY, json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )


def _prune(data, now):
    tasks = data["tasks"]
    for task_id, task in list(tasks.items()):
        if not isinstance(task, dict):
            del tasks[task_id]
            continue
        expires_at = task.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at <= now:
            # 还没结论的待办不直接丢：标记成 expired，等下一次同群事件
            # 把“没等到人、已撤回”回报给发起窗口；其余（已发/已失败）照旧清理。
            if (task.get("state") in LIVE_STATES
                    and task.get("kind") in REPORTABLE_KINDS
                    and not task.get("expired_notice_sent")):
                task["state"] = "expired"
                task["expired_at"] = now
            elif task.get("state") != "expired":
                del tasks[task_id]
                continue
        # 过期通知最多保留一天：同群一直没新消息时不至于长期占着队列。
        expired_at = task.get("expired_at")
        if (task.get("state") == "expired" and isinstance(expired_at, (int, float))
                and now - expired_at > 24 * 60 * 60):
            del tasks[task_id]
    if len(tasks) > MAX_TASKS:
        ordered = sorted(tasks.items(), key=lambda item: item[1].get("created_at", 0))
        for task_id, _ in ordered[:len(tasks) - MAX_TASKS]:
            del tasks[task_id]


def add_task(runtime, task):
    """Add one task, deduplicating an identical live/sent task by signature."""
    now = time()
    with runtime.transaction() as connection:
        data = _read(runtime)
        _prune(data, now)
        signature = task.get("signature", "")
        if signature:
            for existing in data["tasks"].values():
                if existing.get("signature") == signature and existing.get("state") in {
                        "waiting", "sending", "sent", "waiting_reply", "reporting",
                        "waiting_permission"}:
                    return existing.get("id", "")
        identifier = uuid4().hex
        record = {**task, "id": identifier, "created_at": now}
        data["tasks"][identifier] = record
        _write(connection, data)
        return identifier


def claim_group(runtime, group_id, target_user_id, now=None):
    now = time() if now is None else now
    claimed = []
    with runtime.transaction() as connection:
        data = _read(runtime)
        _prune(data, now)
        for task in data["tasks"].values():
            if (task.get("kind") == "group_wait" and task.get("state") == "waiting"
                    and task.get("group_id") == group_id
                    and task.get("target_user_id") == target_user_id
                    and task.get("expires_at", 0) > now):
                task["state"] = "sending"
                claimed.append(dict(task))
        _write(connection, data)
    return claimed


def claim_private_receipts(runtime, target_user_id, now=None, *, permission=False):
    now = time() if now is None else now
    wanted = "waiting_permission" if permission else "waiting_reply"
    next_state = "reporting" if permission else "reporting"
    claimed = []
    with runtime.transaction() as connection:
        data = _read(runtime)
        _prune(data, now)
        ordered = sorted(data["tasks"].values(), key=lambda item: item.get("created_at", 0))
        for task in ordered:
            if (task.get("kind") == "private_receipt" and task.get("state") == wanted
                    and task.get("target_user_id") == target_user_id
                    and task.get("expires_at", 0) > now):
                task["state"] = next_state
                claimed.append(dict(task))
                break
        _write(connection, data)
    return claimed


def claim_expired(runtime, group_id=None, now=None):
    """Take expired tasks once so their origin window can be told the relay was dropped.

    Expiry is recorded by ``_prune``; the task is removed here because the notice is
    best-effort and must not be repeated on every later group message.
    """
    now = time() if now is None else now
    claimed = []
    with runtime.transaction() as connection:
        data = _read(runtime)
        _prune(data, now)
        for task_id, task in list(data["tasks"].items()):
            if task.get("state") != "expired":
                continue
            if group_id is not None and (
                    task.get("kind") != "group_wait" or task.get("group_id") != group_id):
                continue
            claimed.append(dict(task))
            del data["tasks"][task_id]
        _write(connection, data)
    return claimed


def finish(runtime, task_id, state):
    with runtime.transaction() as connection:
        data = _read(runtime)
        task = data["tasks"].get(task_id)
        if isinstance(task, dict):
            task["state"] = state
        _write(connection, data)


def mark_waiting_permission(runtime, task_id, reply_text):
    with runtime.transaction() as connection:
        data = _read(runtime)
        task = data["tasks"].get(task_id)
        if isinstance(task, dict):
            task["state"] = "waiting_permission"
            task["reply_text"] = reply_text
        _write(connection, data)


def requeue(runtime, task_id):
    with runtime.transaction() as connection:
        data = _read(runtime)
        task = data["tasks"].get(task_id)
        if isinstance(task, dict) and task.get("state") in {"sending", "reporting"}:
            task["state"] = "waiting" if task.get("kind") == "group_wait" else "waiting_reply"
        _write(connection, data)


__all__ = ["KEY", "add_task", "claim_group", "claim_private_receipts", "claim_expired", "finish",
           "mark_waiting_permission", "requeue"]
