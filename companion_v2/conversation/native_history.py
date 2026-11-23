"""Reference-based native dialogue for background work, without fake receipts."""

import json

from companion_v2.execution.models import TaskContext, TaskError, require, stamp
from companion_v2.foundation.refs import normalize_utc
from companion_v2.sources import dependencies as source_dependencies
from companion_v2.sources.models import GovernanceState
from companion_v2.sources.repositories import SourceRepository, _source_from_row
from companion_v2.storage.native_dedup import resolve_context_json, resolve_rows_context
from companion_v2.storage.runtime import table_exists

from .messages import ConversationMessages, source_from_document
from .native_observations import (
    REALITY_ASSISTANT_OWNER,
    TOGETHER_OWNERS,
    observations_available,
    validate_observation,
)
from .recalls import CONSUMER as RECALL_CONSUMER, MessageRecall
from .reset import batch_current, boundary as reset_boundary, source_current


def reference(row):
    return {"kind": "native_input" if row["kind"] == "input" else "native_reply",
            "observation_id": row["observation_id"], "content_hash": row["content_hash"],
            "observed_at": row["observed_at"]}


def reference_key(ref):
    if (type(ref) is not dict or set(ref) != {"kind", "observation_id", "content_hash", "observed_at"}
            or ref["kind"] not in ("native_input", "native_reply")
            or any(type(value) is not str or not value or len(value) > 1024 for value in ref.values())):
        raise TaskError("native_history_invalid", "native history must retain its original source reference")
    return ref["kind"], ref["observation_id"]


def rows(runtime, context, *, start=None, end=None, kind=None, limit=32):
    if not observations_available(runtime):
        return ()
    has_sends = table_exists(runtime, "conversation_native_sends")
    where = ["n.context_key = ?", "n.delivery_state IN ('input_observed', 'send_returned')"]
    values = [context.key]
    if start is not None:
        where.append("n.effective_at >= ?")
        values.append(start)
    if end is not None:
        where.append("n.effective_at <= ?")
        values.append(end)
    if kind is not None:
        where.append("n.kind = ?")
        values.append(kind)
    query = ("SELECT n.*, s.sent_at FROM conversation_native_observations n LEFT JOIN conversation_native_sends s USING(observation_id) "
             if has_sends else "SELECT n.*, NULL AS sent_at FROM conversation_native_observations n ")
    query += "WHERE " + " AND ".join(where) + " ORDER BY n.effective_at DESC, n.rowid DESC LIMIT ?"
    return resolve_rows_context(runtime, runtime.read_all(query, (*values, limit)))


def _format_body(row, source, record, at, *, sent_at, has_sent_row):
    origin = ({"owner": source.owner, "execution_path": "together_room", "source_label": "一起房间文字转写"}
              if source.owner in TOGETHER_OWNERS else {})
    if source.owner == REALITY_ASSISTANT_OWNER:
        local = row["event_id"].startswith("reality-local:")
        origin = {"execution_path": "reality_local_audio" if local else "reality_chat",
                  "source_label": "现实设备已播放的话" if local else "现实提醒聊天副本"}
    if row["kind"] == "input":
        return {"role": "user", "received_at": row["observed_at"], "text": record.payload,
                "owner": source.owner, **origin}
    resolved_sent_at = sent_at if has_sent_row else row["observed_at"]
    if resolved_sent_at > stamp(at):
        raise TaskError("native_history_changed", "the native send is later than this observation")
    return {"role": "assistant", "delivered_at": resolved_sent_at, "text": record.payload, "owner": source.owner,
            "recipient_read": "unknown", "delivery_state": "send_returned", "platform_confirmed": False,
            "send_time_basis": "reality_output_return" if source.owner == REALITY_ASSISTANT_OWNER else
                "together_room_send_return" if source.owner in TOGETHER_OWNERS
                else "native_send_return" if has_sent_row else "source_observation", **origin}


def body(messages, ref, context, at, *, historical=False):
    reference_key(ref)
    if not observations_available(messages.runtime):
        raise TaskError("native_history_unavailable", "native history is unavailable")
    row = messages.runtime.read_one("SELECT * FROM conversation_native_observations WHERE observation_id=?",
                                    (ref["observation_id"],))
    if (row is None or reference(row) != ref or row["context_key"] != context.key
            or row["delivery_state"] not in ("input_observed", "send_returned") or row["observed_at"] > stamp(at)):
        raise TaskError("native_history_changed", "the native history changed or was not sent")
    original = TaskContext.restore(json.loads(resolve_context_json(messages.runtime, row["context_json"])))
    if original.key != context.key or original.scope != context.scope:
        raise TaskError("native_history_context", "native history belongs to another authenticated context")
    source = source_from_document(json.loads(row["source_json"]))
    validate_observation(messages.runtime, row, context, source, original=original)
    if not historical and not source_current(messages.runtime, source):
        raise TaskError("conversation_context_reset", "native history precedes this window's reset")
    record = messages._read_source(source, context, "text", at)
    if record.content_hash != row["content_hash"]:
        raise TaskError("native_history_changed", "the original native source changed")
    sent = (messages.runtime.read_one("SELECT sent_at FROM conversation_native_sends WHERE observation_id=?",
                                     (row["observation_id"],))
            if row["kind"] != "input" and table_exists(messages.runtime, "conversation_native_sends") else None)
    return _format_body(row, source, record, at,
                        sent_at=sent["sent_at"] if sent else None, has_sent_row=sent is not None)


_DEFAULT_BODY = body


def _can_use_batched_dialogue(messages):
    if body is not _DEFAULT_BODY:
        return False
    if type(messages)._read_source is not ConversationMessages._read_source:
        return False
    sources = getattr(messages, "sources", None)
    if sources is None:
        return False
    if (type(sources).read is not SourceRepository.read
            or type(sources).validate_reference is not SourceRepository.validate_reference):
        return False
    return callable(getattr(messages.runtime, "read_snapshot", None))


def _dialogue_batched(messages, context, at, *, start=None, end=None, limit=32, max_chars=8000,
                      include_turns=False):
    runtime = messages.runtime
    observed_at = normalize_utc(at, field="observed_at")
    at_stamp = stamp(observed_at)
    with source_dependencies.validation_batch(runtime, observed_at):
        obs_rows = rows(runtime, context, start=start, end=end, limit=limit)
        if not obs_rows:
            return ()
        if context.scope.installation_id != runtime.installation_id:
            return ()
        access = messages.tasks.identity.authorize(
            context.actor,
            scope=context.scope,
            capability="text",
            observed_at=observed_at,
        )
        if not access.is_success:
            return ()

        # Parse and pre-validate observation metadata once before batch SQL loads.
        parsed = []
        recall_keys = []
        source_ids = []
        for row in obs_rows:
            if (row["context_key"] != context.key
                    or row["delivery_state"] not in ("input_observed", "send_returned")
                    or row["observed_at"] > at_stamp):
                continue
            try:
                original = TaskContext.restore(json.loads(resolve_context_json(messages.runtime, row["context_json"])))
                if original.key != context.key or original.scope != context.scope:
                    continue
                source = source_from_document(json.loads(row["source_json"]))
                if source.scope != context.scope:
                    continue
            except (TaskError, ValueError, KeyError, TypeError):
                continue
            key = (source.source_id, source.version.lineage_id, source.version.revision)
            parsed.append((row, original, source, key))
            source_ids.append(source.source_id)
            if row["kind"] == "input":
                marker = MessageRecall.from_scope(
                    original.scope, row["event_id"], original.actor.principal.native_account_id
                )
                recall_keys.append(marker.key)

        if not parsed:
            return ()

        # 1. Batch recall lookup for all input rows in chunked SQL queries.
        in_chunk = 500
        recalled_set: set[str] = set()
        if recall_keys:
            unique_recalls = tuple(dict.fromkeys(recall_keys))
            for start in range(0, len(unique_recalls), in_chunk):
                chunk = unique_recalls[start:start + in_chunk]
                marks = ",".join("?" for _ in chunk)
                for r in runtime.read_all(
                    f"SELECT event_id FROM inbox WHERE consumer = ? AND event_id IN ({marks})",
                    (RECALL_CONSUMER, *chunk),
                ):
                    recalled_set.add(r["event_id"])

        # 2. Single window reset boundary check (plus optional batch input check if reset exists).
        saved_reset = reset_boundary(runtime, context.scope)
        reset_batch_by_source: dict[str, str] = {}
        if saved_reset is not None:
            unique_ids = tuple(dict.fromkeys(source_ids))
            for start in range(0, len(unique_ids), in_chunk):
                chunk = unique_ids[start:start + in_chunk]
                marks = ",".join("?" for _ in chunk)
                for input_row in runtime.read_all(
                    f"SELECT json_extract(event_json, '$.source.source_id') AS sid, batch_id "
                    f"FROM conversation_inputs "
                    f"WHERE json_extract(event_json, '$.source.source_id') IN ({marks})",
                    chunk,
                ):
                    sid = input_row["sid"]
                    if sid is not None and sid not in reset_batch_by_source:
                        reset_batch_by_source[sid] = input_row["batch_id"]

        # 3. Batch-load sources and leaf dependency edges into the active validation batch.
        unique_source_ids = tuple(dict.fromkeys(source_ids))
        source_rows_by_key: dict[tuple[str, str, int], object] = {}
        source_records_by_key: dict[tuple[str, str, int], object] = {}
        for start in range(0, len(unique_source_ids), in_chunk):
            chunk = unique_source_ids[start:start + in_chunk]
            marks = ",".join("?" for _ in chunk)
            for src_row in runtime.read_all(
                f"SELECT * FROM sources WHERE source_id IN ({marks})",
                chunk,
            ):
                src_key = (src_row["source_id"], src_row["lineage_id"], src_row["source_revision"])
                source_rows_by_key[src_key] = src_row
                source_records_by_key[src_key] = _source_from_row(src_row)

        batch = source_dependencies.active_validation_batch(runtime, observed_at)
        if batch is not None:
            has_deps: set[tuple[str, str, int]] = set()
            for start in range(0, len(unique_source_ids), in_chunk):
                chunk = unique_source_ids[start:start + in_chunk]
                marks = ",".join("?" for _ in chunk)
                for dep_row in runtime.read_all(
                    f"SELECT DISTINCT dependent_id, dependent_lineage, dependent_revision "
                    f"FROM source_dependencies WHERE dependent_id IN ({marks})",
                    chunk,
                ):
                    has_deps.add((dep_row["dependent_id"], dep_row["dependent_lineage"], dep_row["dependent_revision"]))
            for _, _, _, src_key in parsed:
                if src_key in source_rows_by_key:
                    batch.rows[src_key] = source_rows_by_key[src_key]
                    batch.full_records[src_key] = source_records_by_key[src_key]
                    if src_key not in has_deps:
                        batch.edges[src_key] = []

        # 4. Validate and assemble dialogue entries in reverse chronological order.
        result = []
        remaining = max_chars
        required_recipients = set(context.audience.recipients)
        actor_subject = context.actor.subject
        for row, original, source, src_key in parsed:
            if remaining <= 0:
                break
            try:
                validate_observation(runtime, row, context, source,
                                     original=original, recalled_keys=recalled_set)
                if saved_reset is not None:
                    matched_batch = reset_batch_by_source.get(source.source_id)
                    if matched_batch is not None:
                        if not batch_current(runtime, matched_batch, source.scope):
                            raise TaskError("conversation_context_reset",
                                            "native history precedes this window's reset")
                    elif stamp(source.issued_at) <= saved_reset["reset_at"]:
                        raise TaskError("conversation_context_reset",
                                        "native history precedes this window's reset")
                record = source_records_by_key.get(src_key)
                if record is None or record.reference != source:
                    raise TaskError("source_not_found", "exact evidence version does not exist")
                if record.state is not GovernanceState.ACTIVE:
                    raise TaskError("source_revoked", "evidence was revoked")
                if record.reference.is_expired_at(observed_at):
                    raise TaskError("source_expired", "evidence expired")
                if observed_at < record.reference.issued_at:
                    raise TaskError("source_not_active", "evidence is not available at this time")
                source_dependencies.validate(runtime, source, observed_at)
                if actor_subject not in record.audience.recipients:
                    raise TaskError("source_audience_denied", "source is not visible to the current subject")
                if not required_recipients.issubset(record.audience.recipients):
                    raise TaskError("source_audience_mismatch", "material is not available to the original audience")
                if record.content_hash != row["content_hash"]:
                    raise TaskError("native_history_changed", "the original native source changed")
                sent_at = row["sent_at"]
                item = _format_body(row, source, record, observed_at,
                                    sent_at=sent_at, has_sent_row=sent_at is not None)
            except (TaskError, ValueError, KeyError, TypeError):
                continue
            text = str(item.get("text", "")).strip()
            if not text or remaining <= 0:
                continue
            text = text[:min(1600, remaining)]
            if not text:
                continue
            entry = {
                "speaker": item["role"],
                "text": text,
                "at": item.get("received_at") or item.get("delivered_at") or row["observed_at"],
                "observation_id": row["observation_id"],
                "content_hash": row["content_hash"],
            }
            if include_turns:
                entry.update(turn_id=row["turn_id"], event_id=row["event_id"])
            result.append(entry)
            remaining -= len(text)
        return tuple(reversed(result))


def dialogue(messages, context, at, *, start=None, end=None, limit=32, max_chars=8000,
             include_turns=False):
    """Read a bounded, validated private native dialogue in chronological order.

    Only input observations and actual assistant send returns are exposed.  The
    returned rows intentionally contain no mutable source object so callers can
    safely place the material in an asynchronous model request.
    """
    if max_chars <= 0:
        return ()
    if _can_use_batched_dialogue(messages):
        return _dialogue_batched(messages, context, at, start=start, end=end,
                                 limit=limit, max_chars=max_chars, include_turns=include_turns)
    result = []
    remaining = max_chars
    # Apply the text budget from the latest messages backwards. Reading in
    # chronological order first could spend the whole budget on older inputs
    # and silently omit the assistant's latest care/recovery feedback.
    for row in rows(messages.runtime, context, start=start, end=end, limit=limit):
        if remaining <= 0:
            break
        try:
            item = body(messages, reference(row), context, at)
        except (TaskError, ValueError, KeyError, TypeError):
            continue
        text = str(item.get("text", "")).strip()
        if not text or remaining <= 0:
            continue
        text = text[:min(1600, remaining)]
        if not text:
            continue
        entry = {
            "speaker": item["role"],
            "text": text,
            "at": item.get("received_at") or item.get("delivered_at") or row["observed_at"],
            "observation_id": row["observation_id"],
            "content_hash": row["content_hash"],
        }
        if include_turns:
            entry.update(turn_id=row["turn_id"], event_id=row["event_id"])
        result.append(entry)
        remaining -= len(text)
    return tuple(reversed(result))


def latest_input(messages, context, at):
    for row in rows(messages.runtime, context, end=stamp(at), kind="input", limit=32):
        try:
            item = body(messages, reference(row), context, at)
        except (TaskError, ValueError, KeyError, TypeError):
            continue
        return row, item
    return None
