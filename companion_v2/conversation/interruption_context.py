"""Receipt-backed context for a private reply superseded between segments."""

from __future__ import annotations

from companion_v2.execution.models import TaskError, encode_prompt
from companion_v2.foundation.refs import ScopeKind

from .foreground import ContextContribution, ContributionState


INSTRUCTION = (
    "interrupted_reply_context 只描述上一轮真实收到的输入和已确认发出的分段；"
    "cancelled_parts 未派发，not_sent_parts 收到明确的未发送回执；"
    "unresolved_parts 的投递结果尚不确定，不要把它们说成已发或未发。"
    "结合本轮用户原话判断是在纠正、补充还是转向；若用户明确指出刚才已发内容有误或与新信息冲突，"
    "自然承认并修正自己已经说出口的部分，再接着回应。不要续发被取消的草稿，"
    "材料可能经过长度裁剪；不要补猜被裁掉的原话。也不要无故道歉、解释内部打断机制或假定对方已读。"
)

INPUT_CHARS = 2000
SENT_CHARS = 3000


def _bounded(items, limit):
    result = []
    remaining = limit
    for item in items:
        if remaining <= 0:
            break
        text = item["text"]
        kept = text[:remaining]
        entry = {**item, "text": kept}
        if kept != text:
            entry["text_truncated"] = True
        result.append(entry)
        remaining -= len(kept)
    return result


def capture(messages, context, batch_id):
    if context.scope.kind is not ScopeKind.PRIVATE or messages.background_history is None:
        return None
    current = messages.runtime.read_one(
        "SELECT route_key, sequence FROM conversation_flows WHERE batch_id = ?", (batch_id,))
    if current is None:
        return None
    previous = messages.runtime.read_one("""SELECT f.batch_id, f.state, f.reason,
        b.origin_key, b.state AS batch_state FROM conversation_flows f
        JOIN conversation_batches b ON b.batch_id = f.batch_id
        WHERE f.route_key = ? AND f.sequence < ? ORDER BY f.sequence DESC LIMIT 1""",
        (current["route_key"], current["sequence"]))
    if (previous is None or previous["state"] != "closed"
            or previous["reason"] != "newer_input_received"
            or previous["batch_state"] != "cancelled"
            or previous["origin_key"] != context.key):
        return None
    inputs = messages.runtime.read_all("""SELECT event_key, event_hash FROM conversation_inputs
        WHERE batch_id = ? AND withdrawn = 0 ORDER BY position LIMIT 32""", (previous["batch_id"],))
    sent = messages.runtime.read_all("""SELECT h.part_id, p.payload_hash, h.receipt_ref, h.delivered_at
        FROM conversation_sent_history h JOIN conversation_parts p
          ON p.part_id = h.part_id AND p.batch_id = h.batch_id
        WHERE h.batch_id = ? AND h.origin_key = ? AND p.state = 'delivered'
        ORDER BY p.sequence LIMIT 16""", (previous["batch_id"], context.key))
    if not sent:
        return None
    return {"version": 1, "context_key": context.key, "batch_id": previous["batch_id"],
            "inputs": [{"kind": "input", **dict(row)} for row in inputs],
            "sent": [dict(row) for row in sent]}


def contributions(messages, snapshot, context, batch_id, at):
    if (type(snapshot) is not dict or set(snapshot) != {"version", "context_key", "batch_id", "inputs", "sent"}
            or snapshot["version"] != 1 or snapshot["context_key"] != context.key
            or type(snapshot["batch_id"]) is not str or type(snapshot["inputs"]) is not list
            or type(snapshot["sent"]) is not list or len(snapshot["inputs"]) > 32
            or not 1 <= len(snapshot["sent"]) <= 16 or context.scope.kind is not ScopeKind.PRIVATE):
        raise TaskError("interrupted_reply_invalid", "interrupted context is outside its original private window")
    current = messages.runtime.read_one(
        "SELECT route_key, sequence FROM conversation_flows WHERE batch_id = ?", (batch_id,))
    previous = messages.runtime.read_one("""SELECT f.route_key, f.sequence, f.state, f.reason,
        b.origin_key, b.state AS batch_state FROM conversation_flows f
        JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.batch_id = ?""",
        (snapshot["batch_id"],))
    if (current is None or previous is None or previous["route_key"] != current["route_key"]
            or previous["sequence"] >= current["sequence"] or previous["origin_key"] != context.key
            or previous["state"] != "closed" or previous["reason"] != "newer_input_received"
            or previous["batch_state"] != "cancelled"):
        raise TaskError("interrupted_reply_changed", "superseded reply no longer matches this window")

    history = messages.background_history
    original_inputs = []
    for ref in snapshot["inputs"]:
        if (type(ref) is not dict or set(ref) != {"kind", "event_key", "event_hash"}
                or any(type(value) is not str or not value or len(value) > 1024 for value in ref.values())):
            raise TaskError("interrupted_reply_invalid", "original input reference is invalid")
        row = messages.runtime.read_one("SELECT batch_id FROM conversation_inputs WHERE event_key = ?", (ref["event_key"],))
        if row is None or row["batch_id"] != snapshot["batch_id"]:
            raise TaskError("interrupted_reply_changed", "original input moved outside this reply")
        try:
            item = history.conversation._body(ref, context, at)
        except (TaskError, ValueError, KeyError, TypeError):
            continue
        original_inputs.append({"text": item["text"], "received_at": item["received_at"]})

    confirmed = []
    for ref in snapshot["sent"]:
        if (type(ref) is not dict or set(ref) != {"part_id", "payload_hash", "receipt_ref", "delivered_at"}
                or any(type(value) is not str or not value or len(value) > 1024 for value in ref.values())):
            raise TaskError("interrupted_reply_invalid", "confirmed part reference is invalid")
        row = messages.runtime.read_one("SELECT batch_id, sequence FROM conversation_parts WHERE part_id = ?",
                                        (ref["part_id"],))
        if row is None or row["batch_id"] != snapshot["batch_id"]:
            raise TaskError("interrupted_reply_changed", "confirmed part moved outside this reply")
        try:
            item = history.latest_reply(context, at, within_minutes=0,
                                        action="conversation.reply", part_id=ref["part_id"])
        except (TaskError, ValueError, KeyError, TypeError):
            continue
        if item is not None and item["reference"] == ref:
            confirmed.append({"sequence": row["sequence"], "text": item["text"],
                              "delivered_at": item["delivered_at"]})

    states = messages.runtime.read_all("""SELECT state, COUNT(*) AS count FROM conversation_parts
        WHERE batch_id = ? GROUP BY state""", (snapshot["batch_id"],))
    counts = {row["state"]: row["count"] for row in states}
    payload = {"original_inputs": _bounded(original_inputs, INPUT_CHARS),
               "confirmed_sent": _bounded(confirmed, SENT_CHARS),
               "cancelled_parts": counts.get("cancelled", 0),
               "not_sent_parts": counts.get("failed", 0),
               "unresolved_parts": sum(counts.get(state, 0) for state in ("dispatched", "accepted", "unknown")),
               "recipient_read": "unknown", "history_is_bounded": True}
    if len(payload["original_inputs"]) < len(original_inputs):
        payload["omitted_input_count"] = len(original_inputs) - len(payload["original_inputs"])
    if len(payload["confirmed_sent"]) < len(confirmed):
        payload["omitted_sent_count"] = len(confirmed) - len(payload["confirmed_sent"])
    return (ContextContribution("interrupted:" + snapshot["batch_id"], "conversation.interruption",
                "interrupted_reply_context", ContributionState.READY, encode_prompt(payload)),)
