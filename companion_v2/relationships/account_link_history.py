"""Read-only, paged linked-private history. Never moves or relabels records."""
import base64
from datetime import datetime
import json

from companion_v2.execution.models import TaskContext, TaskError, require, stamp
from companion_v2.conversation.native_observations import observations_available, validate_observation
from companion_v2.sources.repositories import SourceRepository
from companion_v2.memory.codec import source
from .account_links import PrivateAccountLinks, member_for


def history(application, context, *, at, cursor=None):
    runtime = application.runtime
    group = PrivateAccountLinks(runtime).group_for(context)
    check = getattr(application, "private_account_link_persona", None)
    if not group or not check or check(member_for(context)) != group["persona_id"]:
        return {"records": [], "next_cursor": None}
    boundary = (stamp(at), "~")
    if cursor:
        try:
            if type(cursor) is not str or len(cursor) > 512:
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if (type(decoded) is not list or len(decoded) != 2 or
                    any(type(value) is not str or not value for value in decoded)):
                raise ValueError()
            parsed = datetime.fromisoformat(decoded[0])
            if parsed.tzinfo is None or stamp(parsed) != decoded[0] or parsed > at:
                raise ValueError()
            boundary = tuple(decoded)
        except (ValueError, TypeError, UnicodeError):
            raise TaskError("account_link_cursor_invalid", "read a valid original history page")
    members = [m for m in group["members"] if check(m) == group["persona_id"]]
    def predicate(context_column):
        clauses, params = [], []
        for member in members:
            clauses.append("(" + " AND ".join(
                "json_extract(" + context_column + ", '$." + path + "') = ?"
                for path in ("actor.subject.subject_id", "audience.scope.platform", "audience.scope.bot_id",
                             "audience.scope.account_id", "audience.scope.native_scope_id", "role_binding.role.subject_id")) + ")")
            params.extend(member[k] for k in ("subject_id", "platform", "bot_id", "account_id", "native_account_id", "role_id"))
        return " OR ".join(clauses), params

    if not members:
        return {"records": [], "next_cursor": None}
    queries, params = [], []
    from companion_v2.storage.native_dedup import matching_context_hashes, resolve_context_json
    from companion_v2.storage.runtime import table_exists
    if observations_available(runtime):
        clause, values = predicate("n.context_json")
        ctx_clause, ctx_values = predicate("context_json")
        matched_hashes = matching_context_hashes(runtime, ctx_clause, tuple(ctx_values))
        if matched_hashes:
            marks = ",".join("?" for _ in matched_hashes)
            clause = f"{clause} OR n.context_hash IN ({marks})"
            values.extend(matched_hashes)
        has_sends = table_exists(runtime, "conversation_native_sends")
        projected = "COALESCE(s.sent_at,n.observed_at)" if has_sends else "n.observed_at"
        joins = " LEFT JOIN conversation_native_sends s USING(observation_id)" if has_sends else ""
        sent = "(n.kind='input' OR s.sent_at IS NOT NULL)" if has_sends else "n.kind='input'"
        queries.append("SELECT 'native' AS kind, 'native:' || n.observation_id AS record_id, " + projected +
            " AS actual_at, n.observation_id AS original_id FROM conversation_native_observations n" + joins +
            " WHERE (" + clause + ") AND " + sent + " AND n.delivery_state IN ('input_observed','send_returned')")
        params.extend(values)
    if table_exists(runtime, "background_deliveries"):
        clause, values = predicate("json_extract(d.request_json, '$.context')")
        queries.append("SELECT 'background' AS kind, 'background:' || d.delivery_id AS record_id, "
            "d.delivered_at AS actual_at, d.delivery_id AS original_id FROM background_deliveries d "
            "WHERE d.state='delivered' AND (" + clause + ")")
        params.extend(values)
    if not queries:
        return {"records": [], "next_cursor": None}
    rows = runtime.read_all("SELECT * FROM (" + " UNION ALL ".join(queries) +
        ") WHERE (actual_at,record_id) < (?,?) ORDER BY actual_at DESC,record_id DESC LIMIT 101", (*params, *boundary))
    records = []
    for item in rows[:100]:
        try:
            actual_scope = None
            if item["kind"] == "native":
                row = runtime.read_one("SELECT * FROM conversation_native_observations WHERE observation_id=?", (item["original_id"],))
                original = TaskContext.restore(json.loads(resolve_context_json(runtime, row["context_json"])))
                if member_for(original) not in members:
                    continue
                require(application.tasks.check_context_access(original, action="conversation.reply", observed_at=at))
                reference = source(json.loads(row["source_json"]))
                validate_observation(runtime, row, original, reference)
                record = require(SourceRepository(runtime).read(reference, resolved=original.actor,
                    capability="conversation.reply", observed_at=at, authorizer=application.tasks.identity))
                if record.content_hash != row["content_hash"]:
                    continue
                text, speaker = record.payload, "user" if row["kind"] == "input" else "assistant"
            else:
                from companion_v2.delivery.service import DeliveryRequest
                row = application.outbound._row(item["original_id"])
                request = DeliveryRequest.restore(row["request_json"])
                original = request.context
                if member_for(original) not in members:
                    continue
                # Use the existing owner, receipt, source-governance and platform
                # proof validation, not a raw historical payload/database copy.
                body = application.background_history._body(application.background_history.reference(row),
                    original, at, historical=True)
                text, speaker = body["text"], "assistant"
                router = getattr(application.outbound, "private_account_router", None)
                actual = router.actual_context(item["original_id"]) if router else None
                if actual is not None:
                    # Never disclose a destination that has since been unlinked,
                    # remapped to another persona, or explicitly lost access.
                    if member_for(actual) not in members:
                        continue
                    require(application.tasks.check_context_access(actual, action="conversation.reply", observed_at=at))
                    actual_scope = actual.scope.document()
        except (TaskError, ValueError, KeyError, TypeError):
            continue
        records.append({"id": item["record_id"], "subject_id": original.actor.subject.subject_id,
            "scope": original.scope.document(), "speaker": speaker,
            "at": item["actual_at"], "text": text[:10000], "record_type": item["kind"],
            **({"delivery_scope": actual_scope} if actual_scope is not None else {})})
    next_cursor = None
    if len(rows) > 100:
        last = rows[99]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last["actual_at"],last["record_id"]]).encode()).decode()
    return {"records": records, "next_cursor": next_cursor}
