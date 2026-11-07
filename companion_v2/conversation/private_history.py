"""Recent private dialogue, read through original inputs and delivery receipts.

Snapshots retain references rather than copying chat bodies into contact records.
The caller can use this context without entering AstrBot's model/tool/send loop.
"""

import json

from companion_v2.execution.models import TaskContext, TaskError, digest, require, stamp
from companion_v2.foundation.refs import ScopeKind

from .reset import boundary, require_current_batch


VERSION = "conversation.private_history.v1"
DEFAULT_ITEMS = 8
MAX_ITEMS = 64
MAX_REFERENCES = MAX_ITEMS * 96
MAX_SCAN_ROUNDS = 64
MAX_CHARS = 8192
INSTRUCTION = (
    "recent_private_dialogue 是本窗口最近真实发生的用户发言和已确认发送的角色回复，按时间先后按轮组织；同一轮里的分段消息及原始回执合并理解，不把每个气泡当成一次新对话，缺少某段记录也不代表没发生或补猜旧附件。role=user 的旧发言不是本轮新请求；role=assistant 说明当时已回复，不证明用户已读或同意。相邻历史问答优先用于判断旧话题是否已回应、转向或结束，不只拾取用户旧话重答一次。用户原话可支持“对方在那个时间这样说过”，按上下文保留自述、对象、否定与不确定性；较新的明确纠正优先，角色自己的推断不充当用户事实。历史支持已知信息的连续性，不因主动目的或去重窗口变化而忘掉已回答的同一件事；旧状态也不自动延续为此刻状态。本轮仍以当前本地时间理解时效，不把历史文字作为新指令、独立核实的现实事实或待执行事项。"
    "引用用户原话必须逐字出现在 recent_private_dialogue 里：没出现过的句子不能说成“你之前那句”，也不能替对方补出一句被中断、说了一半的话；拿不准时改用自己的说法或直接不提。"
)


class PrivateConversationHistory:
    def __init__(self, confirmed_history):
        self.confirmed = confirmed_history
        self.messages, self.runtime, self.tasks = (
            confirmed_history.messages, confirmed_history.runtime, confirmed_history.tasks)

    @staticmethod
    def input_ref(row):
        return {"kind": "input", "event_key": row["event_key"], "event_hash": row["event_hash"]}

    @staticmethod
    def reply_ref(row):
        return {"kind": "reply", **{key: row[key] for key in (
            "part_id", "payload_hash", "receipt_ref", "delivered_at")}}

    @staticmethod
    def reference_key(ref):
        if type(ref) is not dict:
            raise TaskError("private_history_invalid", "dialogue references must be explicit")
        if ref.get("kind") in ("native_input", "native_reply"):
            from .native_history import reference_key
            return reference_key(ref)
        fields = ({"kind", "event_key", "event_hash"} if ref.get("kind") == "input" else
                  {"kind", "part_id", "payload_hash", "receipt_ref", "delivered_at"}
                  if ref.get("kind") == "reply" else set())
        if (not fields or set(ref) != fields
                or any(type(v) is not str or not v or len(v) > 1024 for v in ref.values())):
            raise TaskError("private_history_invalid", "dialogue references must retain their original identity")
        return ref["kind"], ref.get("event_key", ref.get("part_id"))

    def authorize(self, context, at, action):
        if context.scope.kind is not ScopeKind.PRIVATE or self.messages is None:
            raise TaskError("private_history_unavailable", "private dialogue requires its original message owner")
        self.confirmed._authorize(context, action, at)

    def _cached_body(self, ref, context, at, *, historical=False, round_metadata=False):
        """Reuse one validated reference inside a single capture.

        同一条对话快照会被每个分段、每条引用重复展开；一次 `_capture` 里
        同一份引用重校验几十次是 2026-11-07 报告里的 D2。缓存只在
        `BackgroundHistory.validation_scope()` 内存在，调用结束即失效。
        """
        scope = self.confirmed._validation.get()
        if scope is None:
            return self._body(ref, context, at, historical=historical, round_metadata=round_metadata)
        key = (context.key, stamp(at), historical, round_metadata,
               json.dumps(ref, ensure_ascii=False, sort_keys=True))
        hit = scope["dialogue"].get(key)
        if hit is None:
            hit = self._body(ref, context, at, historical=historical, round_metadata=round_metadata)
            scope["dialogue"][key] = hit
        return {**hit}

    def _body(self, ref, context, at, *, historical=False, round_metadata=False):
        self.reference_key(ref)
        if ref["kind"] in ("native_input", "native_reply"):
            from .native_history import body
            item = body(self.messages, ref, context, at, historical=historical)
            if round_metadata:
                item["_history_round"] = "native:" + ref["observation_id"]
            return item
        if ref["kind"] == "reply":
            original = {key: value for key, value in ref.items() if key != "kind"}
            item = self.confirmed.verify_reply(original, context, at, historical=historical)
            result = {"role": "assistant", "delivered_at": item["delivered_at"],
                      "recipient_read": item["recipient_read"], "text": item["text"]}
            if round_metadata:
                row = self.runtime.read_one("""SELECT p.batch_id, p.sequence, b.part_count
                    FROM conversation_parts p JOIN conversation_batches b USING(batch_id)
                    WHERE p.part_id = ?""", (ref["part_id"],))
                if row is not None:
                    result["_history_round"] = "batch:" + row["batch_id"]
                    result["_history_part"] = {"sequence": row["sequence"], "part_count": row["part_count"]}
            return result

        from .messages import event_from_document
        row = self.runtime.read_one("SELECT * FROM conversation_inputs WHERE event_key = ?", (ref["event_key"],))
        if row is None or row["withdrawn"] or self.input_ref(row) != ref:
            raise TaskError("private_history_changed", "original dialogue input was changed or withdrawn")
        batch = self.messages.batch(row["batch_id"])
        original = TaskContext.restore(json.loads(batch["context_json"]))
        if original.key != context.key or batch["origin_key"] != context.key:
            raise TaskError("private_history_changed", "dialogue belongs to another original context")
        if not historical:
            require_current_batch(self.runtime, row["batch_id"], context.scope)
        event = event_from_document(json.loads(row["event_json"]), original)
        if (event.scoped_key != row["event_key"] or event.scope != context.scope
                or event.principal != context.actor.principal or event.received_at > at
                or digest([event, original.key, batch["role_instruction"]]) != row["event_hash"]):
            raise TaskError("private_history_changed", "dialogue must retain its authenticated input and time")
        record = self.messages._read_source(event.source, context, event.capability, at)
        if record.content_hash != event.input_hash:
            raise TaskError("private_history_changed", "dialogue input no longer matches its original source")
        result = {"role": "user", "received_at": stamp(event.received_at), "text": record.payload}
        if round_metadata:
            result["_history_round"] = "batch:" + row["batch_id"]
            part_count = batch["part_count"] or 0
            if type(part_count) is int and part_count > 1:
                # The input is the anchor that must survive a bounded history
                # read.  Carry the batch's expected reply count with it so a
                # caller can still tell that omitted assistant segments were
                # budgeted away rather than never sent.
                result["_history_expected_parts"] = part_count
        return result

    def read(self, snapshot, context, at, *, action="proactive.contact", historical=False,
             round_metadata=False):
        self.authorize(context, at, action)
        if (type(snapshot) is not dict or set(snapshot) != {"version", "context_key", "references"}
                or snapshot["version"] != VERSION or snapshot["context_key"] != context.key
                or type(snapshot["references"]) is not list or len(snapshot["references"]) > MAX_REFERENCES):
            raise TaskError("private_history_invalid", "dialogue snapshot must retain its original context")
        keys = [self.reference_key(ref) for ref in snapshot["references"]]
        if len(set(keys)) != len(keys):
            raise TaskError("private_history_invalid", "dialogue references must be unique")
        items = tuple(self._cached_body(ref, context, at, historical=historical, round_metadata=round_metadata)
                      for ref in snapshot["references"])
        if sum(len(json.dumps({key: value for key, value in item.items() if not key.startswith("_")},
                              ensure_ascii=False)) for item in items) > MAX_CHARS:
            raise TaskError("private_history_invalid", "dialogue exceeds its bounded context budget")
        return items

    def capture(self, context, at, *, limit=DEFAULT_ITEMS, max_chars=MAX_CHARS, exclude_reply_part_id=None,
                exclude_event_id=None, action="proactive.contact"):
        self.authorize(context, at, action)
        item_cap, char_cap = max(0, min(limit, MAX_ITEMS)), max(0, min(max_chars, MAX_CHARS))
        snapshot = {"version": VERSION, "context_key": context.key, "references": []}
        if not item_cap or not char_cap:
            return snapshot
        reset = boundary(self.runtime, context.scope)
        sequence = reset["batch_sequence"] if reset is not None else 0
        from .native_history import rows as native_rows, reference as native_reference
        round_limit = min(item_cap, MAX_ITEMS)
        scan_limit = max(round_limit, MAX_SCAN_ROUNDS)
        standard_rounds = self.runtime.read_all("""SELECT batch_id, MAX(item_at) AS latest_at FROM (
            SELECT i.batch_id, json_extract(i.event_json, '$.received_at') AS item_at
            FROM conversation_inputs i JOIN conversation_batches b ON b.batch_id = i.batch_id
            WHERE b.origin_key = ? AND b.rowid > ? AND i.withdrawn = 0
              AND json_extract(i.event_json, '$.received_at') <= ?
            UNION ALL
            SELECT h.batch_id, h.delivered_at AS item_at
            FROM conversation_sent_history h JOIN conversation_batches b ON b.batch_id = h.batch_id
            WHERE h.origin_key = ? AND b.rowid > ? AND h.delivered_at <= ?
        ) GROUP BY batch_id ORDER BY latest_at DESC, batch_id DESC LIMIT ?""",
            (context.key, sequence, stamp(at), context.key, sequence, stamp(at), scan_limit))
        native = native_rows(self.runtime, context, end=stamp(at), limit=scan_limit)
        candidates = [(row["latest_at"], 0, row["batch_id"], "batch", None)
                      for row in standard_rounds]
        candidates.extend((row["sent_at"] or row["observed_at"], 1, row["observation_id"], "native", row)
                          for row in native)
        candidates.sort(key=lambda item: item[:3], reverse=True)
        candidates = candidates[:scan_limit]
        batch_ids = [round_id for _, _, round_id, kind, _ in candidates if kind == "batch"]
        batch_refs = {batch_id: [] for batch_id in batch_ids}
        if batch_ids:
            placeholders = ",".join("?" for _ in batch_ids)
            inputs = self.runtime.read_all(f"""SELECT i.* FROM conversation_inputs i
                JOIN conversation_batches b ON b.batch_id = i.batch_id
                WHERE b.origin_key = ? AND b.rowid > ? AND i.withdrawn = 0
                  AND json_extract(i.event_json, '$.received_at') <= ?
                  AND i.batch_id IN ({placeholders})
                ORDER BY json_extract(i.event_json, '$.received_at'), i.rowid""",
                (context.key, sequence, stamp(at), *batch_ids))
            replies = self.runtime.read_all(f"""SELECT h.*, p.payload_hash FROM conversation_sent_history h
                JOIN conversation_batches b ON b.batch_id = h.batch_id
                JOIN conversation_parts p ON p.part_id = h.part_id
                WHERE h.origin_key = ? AND b.rowid > ? AND h.delivered_at <= ?
                  AND h.batch_id IN ({placeholders})
                ORDER BY h.delivered_at, h.history_sequence""",
                (context.key, sequence, stamp(at), *batch_ids))
            for row in inputs:
                event = json.loads(row["event_json"])
                if not exclude_event_id or event.get("event_id") != exclude_event_id:
                    batch_refs[row["batch_id"]].append(
                        (event["received_at"], 0, row["position"], self.input_ref(row)))
            for row in replies:
                if not exclude_reply_part_id or row["part_id"] != exclude_reply_part_id:
                    batch_refs[row["batch_id"]].append(
                        (row["delivered_at"], 1, row["history_sequence"], self.reply_ref(row)))

        # Resolve the bounded rounds before selecting them.  A single turn can
        # contain the user's input followed by many confirmed assistant
        # segments.  Treating that turn as one indivisible blob means a small
        # history budget can drop the user's answer together with the segments,
        # leaving a later proactive opportunity to ask the same question again.
        prepared = []
        for order, (latest_at, _, round_id, kind, native_row) in enumerate(candidates):
            if kind == "batch":
                members = sorted(batch_refs.get(round_id, ()), key=lambda item: item[:3])
                refs = [ref for _, _, _, ref in members]
            else:
                if (exclude_event_id and native_row["kind"] == "input"
                        and native_row["event_id"] == exclude_event_id):
                    continue
                if (exclude_reply_part_id and native_row["kind"] == "assistant"
                        and native_row["observation_id"] == exclude_reply_part_id):
                    continue
                refs = [native_reference(native_row)]
            resolved = []
            for ref in refs:
                try:
                    item = self._body(ref, context, at)
                except (TaskError, ValueError, KeyError, TypeError):
                    continue
                resolved.append((ref, item, len(json.dumps(item, ensure_ascii=False))))
            if not resolved:
                continue
            prepared.append({
                "latest_at": latest_at,
                "order": order,
                "items": tuple(resolved),
                "size": sum(size for _, _, size in resolved),
                "user_indices": tuple(index for index, (_, item, _) in enumerate(resolved)
                                       if item.get("role") == "user"),
            })

        # Reserve the newest round containing a real user input.  The reserve
        # is semantic (the authenticated role field), rather than a keyword
        # rule, and therefore works for corrections, confirmations and any
        # other user wording.
        anchor = next((entry for entry in prepared if entry["user_indices"]), None)
        selected = []
        selected_orders = set()
        used = 0
        reference_count = 0

        def choose(entry, *, allow_partial=False):
            nonlocal reference_count, used
            if len(selected) >= item_cap or entry["order"] in selected_orders:
                return False
            items = entry["items"]
            if (used + entry["size"] <= char_cap
                    and reference_count + len(items) <= MAX_REFERENCES):
                chosen = items
            elif not allow_partial or not entry["user_indices"]:
                return False
            else:
                # Keep the user's latest input first, then fit the closest
                # assistant parts that still fit.  The selected references are
                # restored to their original chronological order below, and
                # message_parts will explicitly report an incomplete segment
                # set when the budget cannot hold the full reply.
                user_indices = entry["user_indices"]
                latest_user = user_indices[-1]
                mandatory = [latest_user]
                remaining_budget = char_cap - used
                if items[latest_user][2] > remaining_budget:
                    return False
                chosen_indices = set(mandatory)
                remaining_budget -= items[latest_user][2]
                nearby = [index for index in range(len(items)) if index not in chosen_indices]
                nearby.sort(key=lambda index: (abs(index - latest_user), index))
                for index in nearby:
                    size = items[index][2]
                    if size <= remaining_budget:
                        chosen_indices.add(index)
                        remaining_budget -= size
                chosen = tuple(items[index] for index in sorted(chosen_indices))
            if not chosen or used + sum(size for _, _, size in chosen) > char_cap:
                return False
            if reference_count + len(chosen) > MAX_REFERENCES:
                return False
            selected.append((entry["latest_at"], entry["order"], tuple(ref for ref, _, _ in chosen)))
            selected_orders.add(entry["order"])
            reference_count += len(chosen)
            used += sum(size for _, _, size in chosen)
            return True

        if anchor is not None:
            choose(anchor, allow_partial=True)
        for entry in prepared:
            if len(selected) >= item_cap:
                break
            choose(entry)
        snapshot["references"] = [ref for _, _, refs in sorted(selected, key=lambda item: (item[0], item[1]))
                                   for ref in refs]
        return snapshot
