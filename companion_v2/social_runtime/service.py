"""Persistent relationship lines over one set of governed source records."""

from __future__ import annotations

from datetime import datetime, timedelta
import json

from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.conversation.group_observation import topic_signature, topic_signatures_similar
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind, SourceRef
from companion_v2.memory import codec
from companion_v2.memory.models import AtomRef, owner_key as private_owner
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.role_work.journal.models import JournalKey, JournalKind
from companion_v2.sources.repositories import SourceRepository

from .attention import AttentionDecision, decide, salience


INSTRUCTION = (
    "社交状态来自同一角色的真实记录。当前线程的欠回复、话题和已确认回复只用于决定接话节奏，"
    "不等于新事实。跨线程材料只可按当前受众投影引用：私聊细节不带入群或空间；群里内容在私聊"
    "只谈当前对象本人有证据在场的部分；公开生活只说已记录且适合公开的部分。"
    "没有材料时不要假装记得，事实冲突时以当前有证据的记录为准，不编造圆场解释。"
)


def role_key(context) -> str:
    return digest(["social.role.v1", context.scope.installation_id, context.scope.bot_id,
                   context.role_binding.role.subject_id])


def scope_key(context) -> str:
    scope = context.scope
    if scope.kind is ScopeKind.PRIVATE:
        return digest(["social.private.v1", scope.installation_id, scope.bot_id,
                       context.role_binding.role.subject_id, context.actor.subject.subject_id])
    if scope.kind is ScopeKind.GROUP:
        return digest(["social.group.v1", scope.installation_id, scope.platform, scope.bot_id,
                       scope.account_id, scope.native_scope_id])
    return digest(["social.qzone.v1", scope.installation_id, scope.bot_id,
                   context.role_binding.role.subject_id])


def _kind(context) -> str:
    if context.scope.kind is ScopeKind.PRIVATE:
        return "private"
    if context.scope.kind is ScopeKind.GROUP:
        return "group"
    return "qzone"


def _moment(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class SocialRuntime:
    def __init__(self, runtime, tasks, *, messages=None, memory=None):
        self.runtime, self.tasks = runtime, tasks
        self.messages, self.memory = messages, memory
        self.sources = SourceRepository(runtime)
        self.life = None
        self.journal = None

    def attach(self, *, messages, memory):
        if messages.runtime is not self.runtime or memory.runtime is not self.runtime:
            raise ValueError("social runtime must share the original storage")
        self.messages, self.memory = messages, memory

    def decision(self, context, event, *, interruption_seconds=0.0) -> AttentionDecision:
        self.messages.authorize(context)
        record = self.messages._read_source(event.source, context, event.capability, self.tasks.now())
        if record.content_hash != event.input_hash:
            raise TaskError("social_input_changed", "social attention requires the original input")
        owner, key = role_key(context), scope_key(context)
        last = self.runtime.read_one("""SELECT topic, last_reply_at FROM social_threads
            WHERE owner_key = ? AND kind = ? AND scope_key = ? AND state != 'archived'
            ORDER BY last_event_at DESC LIMIT 1""", (owner, _kind(context), key))
        count = self.runtime.read_one("""SELECT COUNT(*) AS n FROM social_attention_receipts
            WHERE owner_key = ? AND action IN ('respond_now', 'defer') AND recorded_at >= ?""",
            (owner, stamp(self.tasks.now() - timedelta(hours=1))))["n"]
        media = any(getattr(event, name, ()) for name in ("images", "audios", "videos", "forwards", "quotes"))
        return decide(record.payload, private=context.scope.kind is ScopeKind.PRIVATE,
            now=self.tasks.now(), previous_topic=last["topic"] if last else "",
            last_reply_at=_moment(last["last_reply_at"]) if last else None,
            recent_load=count, interruption_seconds=interruption_seconds,
            reciprocity=bool(last and last["last_reply_at"]), media=media)

    def _thread(self, connection, context, event, text):
        owner, key, kind = role_key(context), scope_key(context), _kind(context)
        signature = topic_signature(text) if kind == "group" else ""
        rows = connection.execute("""SELECT * FROM social_threads WHERE owner_key = ? AND kind = ?
            AND scope_key = ? AND state != 'archived' ORDER BY last_event_at DESC LIMIT 16""",
            (owner, kind, key)).fetchall()
        selected = None
        if kind == "group":
            for row in rows:
                at = _moment(row["last_event_at"])
                if at is None or event.received_at - at > timedelta(hours=6):
                    continue
                if (signature and topic_signatures_similar(signature, row["topic_signature"])) or (not signature and row is rows[0]):
                    selected = row
                    break
        elif rows:
            selected = rows[0]
        thread_id = (selected["thread_id"] if selected else
                     "social-thread-" + digest([owner, key, event.scoped_key if kind == "group" else kind]))
        actor = context.actor.subject.subject_id
        participants = list(json.loads(selected["participants_json"])) if selected else []
        if actor not in participants:
            participants = (participants + [actor])[-32:]
        now = stamp(self.tasks.now())
        if selected is None:
            connection.execute("""INSERT INTO social_threads VALUES
                (?, ?, ?, ?, ?, ?, ?, 'active', ?, '', 0, '{}', ?, ?)""",
                (thread_id, owner, kind, key, encode(participants),
                 text[:160] if kind == "group" else "", signature, stamp(event.received_at), now, now))
        else:
            connection.execute("""UPDATE social_threads SET participants_json = ?, topic = ?,
                topic_signature = ?, state = 'active', last_event_at = ?, updated_at = ? WHERE thread_id = ?""",
                (encode(participants), text[:160] if kind == "group" else selected["topic"],
                 signature if kind == "group" and signature else selected["topic_signature"],
                 max(selected["last_event_at"], stamp(event.received_at)), now, thread_id))
        return thread_id

    def record_intake(self, context, event, batch_id, decision: AttentionDecision):
        owner = role_key(context)
        event_id = "social-event-" + digest([owner, event.scoped_key])
        with self.runtime.transaction() as connection:
            prior = connection.execute("SELECT * FROM social_thread_events WHERE event_id = ?", (event_id,)).fetchone()
            if prior is not None:
                receipt = connection.execute("SELECT * FROM social_attention_receipts WHERE event_id = ?", (event_id,)).fetchone()
                if (prior["owner_key"] != owner or prior["source_id"] != event.source.source_id
                        or receipt is None or receipt["action"] != decision.action):
                    raise TaskError("social_event_conflict", "original social event cannot be restamped")
                return prior["thread_id"]
            record = self.messages._read_source(event.source, context, event.capability, self.tasks.now())
            if record.content_hash != event.input_hash:
                raise TaskError("social_input_changed", "social event requires its original source")
            thread_id = self._thread(connection, context, event, record.payload)
            metadata = {"batch_id": batch_id, "event_key": event.scoped_key}
            connection.execute("""INSERT INTO social_thread_events VALUES
                (?, ?, ?, 'inbound', ?, ?, ?, ?, ?, ?)""",
                (event_id, owner, thread_id, event.source.owner, event.source.source_id,
                 event.source.version.revision, context.actor.subject.subject_id,
                 stamp(event.received_at), encode(metadata)))
            connection.execute("INSERT INTO social_attention_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, owner, thread_id, decision.action, decision.score,
                 stamp(decision.due_at) if decision.due_at else None, decision.reason, stamp(self.tasks.now())))
            thread = connection.execute("SELECT attention_json FROM social_threads WHERE thread_id = ?", (thread_id,)).fetchone()
            attention = json.loads(thread["attention_json"])
            attention.update(last_action=decision.action, last_score=decision.score,
                             last_event_id=event_id, last_input_at=stamp(event.received_at))
            _, signals = salience(record.payload, private=context.scope.kind is ScopeKind.PRIVATE)
            if signals["question"] and decision.action != "ignore":
                attention["unresolved"] = (list(attention.get("unresolved", [])) + [event_id])[-4:]
            acknowledged = tuple(attention.get("acknowledged_through", ("", "")))
            owed = connection.execute("""SELECT COUNT(*) AS n FROM social_thread_events e
                JOIN social_attention_receipts r ON r.event_id = e.event_id
                JOIN conversation_inputs i ON i.event_key = json_extract(e.metadata_json, '$.event_key')
                WHERE e.thread_id = ? AND e.kind = 'inbound' AND i.withdrawn = 0
                AND r.action IN ('respond_now', 'defer')
                AND (e.occurred_at, e.event_id) > (?, ?)""",
                (thread_id, *acknowledged)).fetchone()["n"]
            connection.execute("""UPDATE social_threads SET owed_reply = ?,
                attention_json = ?, updated_at = ? WHERE thread_id = ?""",
                (owed, encode(attention),
                 stamp(self.tasks.now()), thread_id))
            return thread_id

    def record_delivery(self, connection, context, batch_id, part_id, delivered_at):
        owner = role_key(context)
        inputs = connection.execute("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch_id,)).fetchall()
        thread_ids = set()
        for item in inputs:
            event_id = "social-event-" + digest([owner, item["event_key"]])
            row = connection.execute("SELECT thread_id FROM social_thread_events WHERE event_id = ?", (event_id,)).fetchone()
            if row:
                thread_ids.add(row["thread_id"])
        for thread_id in thread_ids:
            reply_id = "social-reply-" + digest([owner, part_id, thread_id])
            connection.execute("""INSERT OR IGNORE INTO social_thread_events VALUES
                (?, ?, ?, 'reply', 'conversation_part', ?, 1, ?, ?, ?)""",
                (reply_id, owner, thread_id, part_id, context.role_binding.role.subject_id,
                 stamp(delivered_at), encode({"batch_id": batch_id})))
            replied = {"social-event-" + digest([owner, item["event_key"]]) for item in inputs}
            markers = ",".join("?" for _ in replied)
            latest = connection.execute(f"""SELECT occurred_at, event_id FROM social_thread_events
                WHERE thread_id = ? AND event_id IN ({markers})
                ORDER BY occurred_at DESC, event_id DESC LIMIT 1""",
                (thread_id, *replied)).fetchone()
            if latest is None:
                continue
            current = connection.execute("SELECT attention_json FROM social_threads WHERE thread_id = ?",
                                         (thread_id,)).fetchone()
            attention = json.loads(current["attention_json"])
            acknowledged = max((latest["occurred_at"], latest["event_id"]),
                               tuple(attention.get("acknowledged_through", ("", ""))))
            pending = connection.execute("""SELECT COUNT(*) AS n FROM social_thread_events e
                JOIN social_attention_receipts r ON r.event_id = e.event_id
                JOIN conversation_inputs i ON i.event_key = json_extract(e.metadata_json, '$.event_key')
                WHERE e.thread_id = ? AND e.kind = 'inbound' AND i.withdrawn = 0
                AND r.action IN ('respond_now', 'defer')
                AND (e.occurred_at, e.event_id) > (?, ?)""",
                (thread_id, *acknowledged)).fetchone()["n"]
            attention["acknowledged_through"] = acknowledged
            attention["unresolved"] = [value for value in attention.get("unresolved", []) if value not in replied]
            connection.execute("""UPDATE social_threads SET attention_json = ?, last_reply_at = ?,
                owed_reply = ?, updated_at = ? WHERE thread_id = ?""",
                (encode(attention), stamp(delivered_at), pending, stamp(self.tasks.now()), thread_id))

    def record_withdrawal(self, connection, context, event_key):
        event_id = "social-event-" + digest([role_key(context), event_key])
        row = connection.execute("SELECT thread_id FROM social_thread_events WHERE event_id = ? AND kind = 'inbound'",
                                 (event_id,)).fetchone()
        if row is None:
            return
        thread = connection.execute("SELECT attention_json FROM social_threads WHERE thread_id = ?",
                                    (row["thread_id"],)).fetchone()
        attention = json.loads(thread["attention_json"])
        acknowledged = tuple(attention.get("acknowledged_through", ("", "")))
        pending = connection.execute("""SELECT COUNT(*) AS n FROM social_thread_events e
            JOIN social_attention_receipts r ON r.event_id = e.event_id
            JOIN conversation_inputs i ON i.event_key = json_extract(e.metadata_json, '$.event_key')
            WHERE e.thread_id = ? AND e.kind = 'inbound' AND i.withdrawn = 0
            AND r.action IN ('respond_now', 'defer') AND (e.occurred_at, e.event_id) > (?, ?)""",
            (row["thread_id"], *acknowledged)).fetchone()["n"]
        attention["unresolved"] = [value for value in attention.get("unresolved", []) if value != event_id]
        connection.execute("""UPDATE social_threads SET attention_json = ?, owed_reply = ?, updated_at = ?
            WHERE thread_id = ?""", (encode(attention), pending, stamp(self.tasks.now()), row["thread_id"]))

    def record_fact(self, connection, context, item, atom_id, revision, source, *, state="active"):
        owner = role_key(context)
        semantic_id = "social-semantic-" + digest([owner, "fact", atom_id])
        if state != "active":
            connection.execute("UPDATE social_consent_grants SET state = 'revoked', updated_at = ? WHERE semantic_id = ?",
                               (stamp(self.tasks.now()), semantic_id))
            return
        prior = connection.execute("SELECT origin_revision FROM social_semantic_refs WHERE semantic_id = ?", (semantic_id,)).fetchone()
        if prior and prior["origin_revision"] != revision:
            connection.execute("UPDATE social_consent_grants SET state = 'revoked', updated_at = ? WHERE semantic_id = ?",
                               (stamp(self.tasks.now()), semantic_id))
        evidence_source = item.evidence[0].source
        linked = connection.execute("""SELECT thread_id FROM social_thread_events
            WHERE owner_key = ? AND source_id = ? AND kind = 'inbound' ORDER BY rowid DESC LIMIT 1""",
            (owner, evidence_source.source_id)).fetchone()
        visibility = "private_pair" if item.visibility == "private" else item.visibility
        connection.execute("""INSERT INTO social_semantic_refs VALUES
            (?, ?, 'fact', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(semantic_id) DO UPDATE SET origin_revision=excluded.origin_revision,
            origin_thread=excluded.origin_thread, visibility=excluded.visibility,
            occurred_at=excluded.occurred_at, confidence=excluded.confidence,
            source_json=excluded.source_json, source_hash=excluded.source_hash,
            metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (semantic_id, owner, atom_id, revision, private_owner(context),
             linked["thread_id"] if linked else None, context.actor.subject.subject_id,
             visibility, stamp(evidence_source.issued_at), item.confidence, encode(source),
             digest(item.representation), encode({"namespace": item.namespace,
                 "context_mode": item.context_mode, "predicate": item.predicate,
                 "object": item.object, "single_value": item.single_value,
                 "valid_from": stamp(item.valid_from) if item.valid_from else None,
                 "valid_to": stamp(item.valid_to) if item.valid_to else None}), stamp(self.tasks.now())))
        if linked:
            connection.execute("""INSERT OR IGNORE INTO social_thread_events VALUES
                (?, ?, ?, 'fact', 'memory.atom', ?, ?, ?, ?, '{}')""",
                ("social-fact-" + digest([semantic_id, revision]), owner, linked["thread_id"], atom_id,
                 revision, context.actor.subject.subject_id, stamp(evidence_source.issued_at)))

    def record_group_fact(self, connection, context, memory_id, revision, source, *, state, document):
        owner = role_key(context)
        semantic_id = "social-semantic-" + digest([owner, "group_fact", memory_id])
        if state != "active":
            return
        evidence = document["evidence"]
        event_key = evidence.get("event_key") if isinstance(evidence, dict) else None
        linked = connection.execute("SELECT thread_id FROM social_thread_events WHERE owner_key = ? AND event_id = ?",
            (owner, "social-event-" + digest([owner, event_key]))).fetchone() if event_key else None
        connection.execute("""INSERT INTO social_semantic_refs VALUES
            (?, ?, 'group_fact', ?, ?, ?, ?, ?, 'group_public', ?, 1, ?, ?, ?, ?)
            ON CONFLICT(semantic_id) DO UPDATE SET origin_revision=excluded.origin_revision,
            origin_thread=excluded.origin_thread, occurred_at=excluded.occurred_at,
            source_json=excluded.source_json, source_hash=excluded.source_hash,
            metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (semantic_id, owner, memory_id, revision, scope_key(context),
             linked["thread_id"] if linked else None, document["author"], document["said_at"],
             encode(source), digest(document["statement"]),
             encode({"group_scope": context.scope.key}), stamp(self.tasks.now())))
        if linked:
            connection.execute("""INSERT OR IGNORE INTO social_thread_events VALUES
                (?, ?, ?, 'group_fact', 'memory.group', ?, ?, ?, ?, '{}')""",
                ("social-fact-" + digest([semantic_id, revision]), owner, linked["thread_id"],
                 memory_id, revision, document["author"], document["said_at"]))

    @staticmethod
    def owner_from_role(owner: RoleOwnerRef):
        return digest(["social.role.v1", owner.installation_id, owner.bot_id, owner.persona_id])

    def _context_for_scope(self, connection, owner, scope):
        if scope.kind is not ScopeKind.PRIVATE:
            return None
        rows = connection.execute("""SELECT context_json FROM conversation_batches
            WHERE json_extract(context_json, '$.audience.scope') = json(?) ORDER BY created_at DESC LIMIT 8""",
            (encode(scope),)).fetchall()
        for row in rows:
            try:
                context = TaskContext.restore(json.loads(row["context_json"]))
            except (TypeError, ValueError, KeyError):
                continue
            if role_key(context) == owner and context.scope == scope:
                return context
        return None

    def _thread_for_known_context(self, connection, context):
        return connection.execute("""SELECT * FROM social_threads WHERE owner_key = ? AND kind = ?
            AND scope_key = ? AND state != 'archived' ORDER BY last_event_at DESC LIMIT 1""",
            (role_key(context), _kind(context), scope_key(context))).fetchone()

    def record_life_activity(self, connection, activity):
        owner = self.owner_from_role(activity.owner)
        context = self._context_for_scope(connection, owner, activity.scope)
        thread = self._thread_for_known_context(connection, context) if context is not None else None
        semantic_id = "social-semantic-" + digest([owner, "life", activity.activity_id])
        occurred_at = stamp(activity.updated_at)
        connection.execute("""INSERT INTO social_semantic_refs VALUES
            (?, ?, 'life', ?, ?, ?, ?, ?, 'private_pair', ?, 1, 'null', ?, '{}', ?)
            ON CONFLICT(semantic_id) DO UPDATE SET origin_revision=excluded.origin_revision,
            origin_thread=excluded.origin_thread, subject_id=excluded.subject_id,
            visibility=CASE WHEN social_semantic_refs.origin_revision = excluded.origin_revision
                THEN social_semantic_refs.visibility ELSE 'private_pair' END,
            occurred_at=excluded.occurred_at,
            source_hash=excluded.source_hash, updated_at=excluded.updated_at""",
            (semantic_id, owner, activity.activity_id, activity.revision, digest(activity.owner),
             thread["thread_id"] if thread else None,
             context.actor.subject.subject_id if context else "", occurred_at,
             digest(activity), stamp(self.tasks.now())))
        if thread:
            connection.execute("""INSERT OR IGNORE INTO social_thread_events VALUES
                (?, ?, ?, 'life', 'role_activity', ?, ?, ?, ?, '{}')""",
                ("social-life-" + digest([semantic_id, activity.revision]), owner, thread["thread_id"],
                 activity.activity_id, activity.revision, context.actor.subject.subject_id, occurred_at))

    def record_journal(self, connection, journal_context, head, draft):
        owner = self.owner_from_role(journal_context.key.owner)
        context = self._context_for_scope(connection, owner, journal_context.scope)
        thread = self._thread_for_known_context(connection, context) if context is not None else None
        identifier = digest(journal_context.key)
        semantic_id = "social-semantic-" + digest([owner, "journal", identifier])
        source = draft.sources[0] if draft is not None and draft.sources else None
        connection.execute("""INSERT INTO social_semantic_refs VALUES
            (?, ?, 'journal', ?, ?, ?, ?, ?, 'private_pair', ?, 1, ?, ?, ?, ?)
            ON CONFLICT(semantic_id) DO UPDATE SET origin_revision=excluded.origin_revision,
            origin_thread=excluded.origin_thread, subject_id=excluded.subject_id,
            occurred_at=excluded.occurred_at,
            source_json=excluded.source_json, source_hash=excluded.source_hash,
            metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (semantic_id, owner, identifier, head.revision, digest(journal_context.key.owner),
             thread["thread_id"] if thread else None,
             context.actor.subject.subject_id if context else "", stamp(head.updated_at),
             encode(source), digest(draft.body) if draft else "deleted",
             encode({"kind": journal_context.key.kind.value, "local_date": journal_context.key.local_date,
                     "scope": journal_context.scope.document(), "state": head.state.value}), stamp(self.tasks.now())))
        if thread and draft is not None:
            connection.execute("""INSERT OR IGNORE INTO social_thread_events VALUES
                (?, ?, ?, 'journal', 'role_journal', ?, ?, ?, ?, '{}')""",
                ("social-journal-" + digest([semantic_id, head.revision]), owner, thread["thread_id"],
                 identifier, head.revision, context.actor.subject.subject_id, stamp(head.updated_at)))

    def publish_life(self, context, activity_id, *, expected_revision=None):
        """Explicitly select a source-free role activity for public projection."""
        self.messages.authorize(context)
        row = self.runtime.read_one("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND origin_kind = 'life' AND origin_id = ?""", (role_key(context), activity_id))
        if row is None or row["subject_id"] != context.actor.subject.subject_id:
            raise TaskError("social_life_unavailable", "activity is not indexed for this role")
        activity = self.life.activity(activity_id, owner=RoleOwnerRef(context.scope.installation_id,
            context.scope.bot_id, context.role_binding.role.subject_id))
        if (activity.scope != context.scope or activity.revision != row["origin_revision"]
                or expected_revision is not None and activity.revision != expected_revision
                or digest(activity) != row["source_hash"]
                or activity.work is not None and activity.work.source is not None or activity.reading is not None):
            raise TaskError("social_life_not_public", "activity has changed or depends on private source material")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE social_semantic_refs SET visibility = 'public', updated_at = ? WHERE semantic_id = ?",
                               (stamp(self.tasks.now()), row["semantic_id"]))
        return {"semantic_id": row["semantic_id"], "visibility": "public"}

    def sync_life(self, context):
        """Page old role records into the reference index after a thread first appears."""
        if context.scope.kind is not ScopeKind.PRIVATE or self.life is None or self.journal is None:
            return
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        marker = "social.life.backfill:" + role_key(context) + ":" + context.scope.key
        with self.runtime.transaction() as connection:
            saved = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (marker,)).fetchone()
            if saved and saved["value"] == "complete":
                return
            state = json.loads(saved["value"]) if saved else {"activity": "", "journal": ""}
            from companion_v2.role_work.codec import activity_from_json
            from companion_v2.role_work.journal import codec as journal_codec
            activities = connection.execute("""SELECT activity_id, record_json FROM role_activities
                WHERE owner_key = ? AND activity_id > ? AND json_extract(record_json, '$.scope') = json(?)
                ORDER BY activity_id LIMIT 33""",
                (digest(owner), state["activity"], encode(context.scope))).fetchall()
            for row in activities[:32]:
                self.record_life_activity(connection, activity_from_json(row["record_json"]))
            state["activity"] = activities[31]["activity_id"] if len(activities) > 32 else None
            journals = connection.execute("""SELECT * FROM role_journals WHERE owner_key = ? AND entry_key > ?
                AND json_extract(context_json, '$.scope') = json(?) ORDER BY entry_key LIMIT 33""",
                (digest(owner), state["journal"], encode(context.scope))).fetchall()
            for row in journals[:32]:
                head = journal_codec.head_from_row(row)
                draft = journal_codec.entry_from_row(row).draft if row["content_json"] else None
                self.record_journal(connection, journal_codec.context(json.loads(row["context_json"])), head, draft)
            state["journal"] = journals[31]["entry_key"] if len(journals) > 32 else None
            value = "complete" if state["activity"] is None and state["journal"] is None else encode(state)
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (marker, value))

    def thread_for(self, context):
        self.messages.authorize(context)
        row = self.runtime.read_one("""SELECT * FROM social_threads WHERE owner_key = ? AND kind = ? AND scope_key = ?
            AND state != 'archived' ORDER BY last_event_at DESC LIMIT 1""",
            (role_key(context), _kind(context), scope_key(context)))
        return dict(row) if row else None

    def _timeline_item(self, event, context):
        item = {"event_id": event["event_id"], "kind": event["kind"],
                "occurred_at": event["occurred_at"], "actor_id": event["actor_id"]}
        if event["kind"] == "inbound":
            receipt = self.runtime.read_one("SELECT action, score FROM social_attention_receipts WHERE event_id = ?",
                                            (event["event_id"],))
            if receipt is not None:
                item["attention"] = {"action": receipt["action"], "score": receipt["score"]}
            if context.scope.kind is ScopeKind.PRIVATE:
                from companion_v2.conversation.messages import event_from_document
                saved = self.runtime.read_one("""SELECT i.*, b.context_json, b.role_instruction
                    FROM conversation_inputs i JOIN conversation_batches b ON b.batch_id = i.batch_id
                    WHERE i.event_key = ? AND i.withdrawn = 0""",
                    (json.loads(event["metadata_json"])["event_key"],))
                if saved is not None:
                    try:
                        original = TaskContext.restore(json.loads(saved["context_json"]))
                        incoming = event_from_document(json.loads(saved["event_json"]), original)
                        if (original.actor.subject == context.actor.subject and original.scope == context.scope
                                and incoming.source.source_id == event["source_id"]
                                and incoming.source.version.revision == event["source_revision"]
                                and digest([incoming, original.key, saved["role_instruction"]]) == saved["event_hash"]):
                            source = self.messages._read_source(incoming.source, original, incoming.capability,
                                                                self.tasks.now())
                            if source.content_hash == incoming.input_hash:
                                item["text"] = source.payload[:500]
                    except (TaskError, ValueError, KeyError, TypeError):
                        pass
        elif event["kind"] in ("fact", "group_fact", "life", "journal"):
            origin_kind = "group_fact" if event["kind"] == "group_fact" else event["kind"]
            row = self.runtime.read_one("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
                AND origin_kind = ? AND origin_id = ? AND origin_revision = ?""",
                (role_key(context), origin_kind, event["source_id"], event["source_revision"]))
            if row is not None and not self._live_conflict(row, context):
                try:
                    projected = self._project(row, context)
                except (TaskError, ValueError, KeyError):
                    projected = None
                if projected is not None:
                    item["text"] = projected["text"]
                    item["visibility"] = projected["visibility"]
                    if event["kind"] == "life":
                        item["source_id"] = event["source_id"]
                        item["source_revision"] = event["source_revision"]
        return item

    def order_due_batches(self, batch_ids, at):
        """Rank due work across scopes; waiting time eventually outweighs salience."""
        if not batch_ids:
            return ()
        slots = ",".join("?" for _ in batch_ids)
        rows = self.runtime.read_all(f"""SELECT i.batch_id, MIN(e.occurred_at) AS first_at,
            MAX(r.score) AS score, MAX(t.owed_reply) AS owed
            FROM conversation_inputs i
            LEFT JOIN social_thread_events e ON e.source_id = json_extract(i.event_json, '$.source.source_id')
                AND json_extract(e.metadata_json, '$.event_key') = i.event_key AND e.kind = 'inbound'
            LEFT JOIN social_attention_receipts r ON r.event_id = e.event_id
            LEFT JOIN social_threads t ON t.thread_id = e.thread_id
            WHERE i.batch_id IN ({slots}) GROUP BY i.batch_id""", tuple(batch_ids))
        values = {row["batch_id"]: row for row in rows}
        def priority(batch_id):
            row = values.get(batch_id)
            first = _moment(row["first_at"]) if row else None
            waiting = max(0.0, (at - first).total_seconds() / 60) if first else 0.0
            # A steadily growing wait term prevents a busy private line from starving a group.
            return waiting / 5 + float(row["score"] or 0) + min(1.0, int(row["owed"] or 0) * .15) if row else 0.0
        return tuple(sorted(batch_ids, key=priority, reverse=True))

    def proactive_hold(self, context, at):
        """Discretionary contact yields while this relationship is owed a reply."""
        thread = self.thread_for(context)
        if thread is not None and thread["owed_reply"]:
            return "social_reply_owed", at + timedelta(minutes=5)
        return None

    @staticmethod
    def _intervals_overlap(left, right):
        floor = "0000"
        ceiling = "9999"
        return ((left.get("valid_from") or floor) < (right.get("valid_to") or ceiling)
                and (right.get("valid_from") or floor) < (left.get("valid_to") or ceiling))

    def _conflicting_rows(self, row):
        metadata = json.loads(row["metadata_json"])
        if row["origin_kind"] != "fact" or not metadata.get("single_value") or not metadata.get("predicate"):
            return ()
        candidates = self.runtime.read_all("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND subject_id = ? AND origin_kind = 'fact' AND semantic_id != ?
            AND json_extract(metadata_json, '$.single_value') = 1
            AND json_extract(metadata_json, '$.namespace') = ?
            AND json_extract(metadata_json, '$.predicate') = ? LIMIT 32""",
            (row["owner_key"], row["subject_id"], row["semantic_id"],
             metadata["namespace"], metadata["predicate"]))
        return tuple(other for other in candidates if
                     json.loads(other["metadata_json"]).get("object") != metadata.get("object")
                     and self._intervals_overlap(metadata, json.loads(other["metadata_json"])))

    def _live_conflict(self, row, context):
        for other in self._conflicting_rows(row):
            if not self._allowed(other, context):
                continue
            try:
                if self._project(other, context) is not None:
                    return True
            except (TaskError, ValueError, KeyError):
                continue
        return False

    def conflicts(self, context, *, limit=20):
        self.messages.authorize(context)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("social conflict limit must be 1..50")
        rows = self.runtime.read_all("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND origin_kind = 'fact' ORDER BY occurred_at DESC LIMIT 256""", (role_key(context),))
        conflicts, seen = [], set()
        for row in rows:
            if not self._allowed(row, context):
                continue
            for other in self._conflicting_rows(row):
                pair = tuple(sorted((row["semantic_id"], other["semantic_id"])))
                if pair not in seen and self._allowed(other, context):
                    try:
                        if self._project(row, context) is None or self._project(other, context) is None:
                            continue
                    except (TaskError, ValueError, KeyError):
                        continue
                    seen.add(pair)
                    conflicts.append({"semantic_ids": pair,
                        "predicate": json.loads(row["metadata_json"])["predicate"],
                        "occurred_at": max(row["occurred_at"], other["occurred_at"])})
                    if len(conflicts) >= limit:
                        return conflicts
        return conflicts

    def broadcast(self, context, *, limit=4):
        """A public-only, revalidated view for QQ Zone composition."""
        self.messages.authorize(context)
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("social broadcast limit must be 1..8")
        rows = self.runtime.read_all("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND visibility = 'public' ORDER BY occurred_at DESC LIMIT 32""", (role_key(context),))
        items = []
        for row in rows:
            try:
                item = self._project(row, context)
            except (TaskError, ValueError, KeyError):
                continue
            if item is not None:
                items.append({"semantic_id": row["semantic_id"], **item})
            if len(items) >= limit:
                break
        return items

    def broadcast_reference(self, context, semantic_id):
        self.messages.authorize(context)
        row = self.runtime.read_one("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND semantic_id = ? AND visibility = 'public'""", (role_key(context), semantic_id))
        if row is None:
            raise TaskError("social_broadcast_denied", "the selected source is not public")
        projected = self._project(row, context)
        if projected is None:
            raise TaskError("social_broadcast_denied", "the selected public source is unavailable")
        return projected

    def timeline(self, context, thread_id, *, cursor=None, limit=20):
        self.messages.authorize(context)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("social timeline page size must be 1..50")
        row = self.runtime.read_one("SELECT * FROM social_threads WHERE thread_id = ? AND owner_key = ?",
                                    (thread_id, role_key(context)))
        if row is None or row["scope_key"] != scope_key(context) or row["kind"] != _kind(context):
            raise TaskError("social_thread_unavailable", "thread is outside the current audience")
        if cursor is not None and (type(cursor) is not str or len(cursor) > 1024):
            raise ValueError("social timeline cursor is invalid")
        try:
            before = json.loads(cursor) if cursor else None
        except (ValueError, TypeError):
            raise ValueError("social timeline cursor is invalid") from None
        if before is not None and (type(before) is not list or len(before) != 2 or
                                   any(type(value) is not str for value in before)):
            raise ValueError("social timeline cursor is invalid")
        sql = "SELECT * FROM social_thread_events WHERE thread_id = ?"
        args = [thread_id]
        if before:
            sql += " AND (occurred_at, event_id) < (?, ?)"
            args.extend(before)
        sql += " ORDER BY occurred_at DESC, event_id DESC LIMIT ?"
        rows = self.runtime.read_all(sql, (*args, limit + 1))
        items = [self._timeline_item(value, context) for value in rows[:limit]]
        attention = json.loads(row["attention_json"])
        pinned_rows = self.runtime.read_all("""SELECT * FROM social_semantic_refs
            WHERE owner_key = ? AND subject_id = ? AND origin_kind = 'fact'
            AND json_extract(metadata_json, '$.context_mode') = 'pinned'
            ORDER BY occurred_at DESC LIMIT 12""", (role_key(context), context.actor.subject.subject_id))
        pinned = []
        for fact in pinned_rows:
            if self._live_conflict(fact, context):
                continue
            try:
                projected = self._project(fact, context)
            except (TaskError, ValueError, KeyError):
                continue
            if projected is not None:
                pinned.append({"semantic_id": fact["semantic_id"], **projected})
            if len(pinned) == 4:
                break
        open_items = []
        if context.scope.kind is ScopeKind.PRIVATE and getattr(self, "open_items", None) is not None:
            try:
                memos = self.open_items._memos(context, self.tasks.now())
                open_items = [{"memo_id": item.memo_id, "title": item.title,
                               "revision": item.revision} for item in memos[:4]]
            except (TaskError, ValueError):
                pass
        next_cursor = encode([rows[limit - 1]["occurred_at"], rows[limit - 1]["event_id"]]) if len(rows) > limit else None
        cross_thread = []
        for source in self.runtime.read_all("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            AND origin_thread IS NOT NULL AND origin_thread != ?
            ORDER BY occurred_at DESC LIMIT 48""", (role_key(context), thread_id)):
            if self._live_conflict(source, context):
                continue
            try:
                projected = self._project(source, context)
            except (TaskError, ValueError, KeyError):
                continue
            if projected is not None:
                cross_thread.append({"semantic_id": source["semantic_id"], **projected})
            if len(cross_thread) >= 4:
                break
        return {"thread_id": thread_id, "kind": row["kind"], "topic": row["topic"],
                "owed_reply": row["owed_reply"], "last_reply_at": row["last_reply_at"],
                "unresolved": attention.get("unresolved", []), "pinned": pinned,
                "open_items": open_items, "cross_thread": cross_thread,
                "items": items, "next_cursor": next_cursor}

    def grant(self, origin_context, reference: AtomRef, target_context, *, operation_key: str):
        """A direct authenticated user action; this method is never a model tool."""
        if (origin_context.scope.kind is not ScopeKind.PRIVATE
                or target_context.scope.kind is not ScopeKind.GROUP
                or origin_context.actor.subject != target_context.actor.subject
                or role_key(origin_context) != role_key(target_context)):
            raise TaskError("social_consent_actor", "only the fact's subject can grant a group projection")
        if type(operation_key) is not str or not 1 <= len(operation_key) <= 256:
            raise ValueError("consent operation key must be bounded")
        self.messages.group_context.authorize(target_context, self.tasks.now())
        fact = require(self.memory.writer.read(origin_context, reference))
        if fact["retention"] == "session":
            raise TaskError("social_consent_temporary", "session memory cannot be shared across audiences")
        if fact["subject"] != "self" or fact["attribution"] != "user":
            raise TaskError("social_consent_subject", "only the subject's own statement can be shared")
        owner, target_key = role_key(origin_context), scope_key(target_context)
        semantic_id = "social-semantic-" + digest([owner, "fact", reference.atom_id])
        grant_id = "social-consent-" + digest([semantic_id, target_key, operation_key])
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM social_semantic_refs WHERE semantic_id = ? AND owner_key = ?",
                                     (semantic_id, owner)).fetchone()
            if row is None:
                item = codec.proposal(json.loads(self.runtime.read_one(
                    "SELECT record_json FROM memory_atoms WHERE atom_id = ? AND owner_key = ?",
                    (reference.atom_id, private_owner(origin_context)))["record_json"])["proposal"])
                self.record_fact(connection, origin_context, item, reference.atom_id, reference.revision,
                                 codec.source(fact["source"]))
                row = connection.execute("SELECT * FROM social_semantic_refs WHERE semantic_id = ?", (semantic_id,)).fetchone()
            if (row["origin_revision"] != reference.revision or row["subject_id"] != origin_context.actor.subject.subject_id
                    or row["origin_owner"] != private_owner(origin_context)):
                raise TaskError("social_consent_stale", "consent must name the current fact and subject")
            current = connection.execute("SELECT * FROM social_consent_grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if current:
                if (current["semantic_id"] != semantic_id or current["audience_key"] != target_key
                        or current["grantor_id"] != row["subject_id"]):
                    raise TaskError("social_consent_conflict", "consent operation changed")
                return {"grant_id": grant_id, "state": current["state"]}
            at = stamp(self.tasks.now())
            consent = {"kind": "direct_authenticated_action", "operation_key": operation_key,
                       "actor_id": origin_context.actor.subject.subject_id, "fact_revision": reference.revision}
            connection.execute("""INSERT INTO social_consent_grants VALUES
                (?, ?, ?, ?, 'group', ?, ?, 'active', ?, ?)""",
                (grant_id, semantic_id, owner, row["subject_id"], target_key, encode(consent), at, at))
            connection.execute("UPDATE social_semantic_refs SET visibility = 'shareable', updated_at = ? WHERE semantic_id = ?",
                               (at, semantic_id))
            return {"grant_id": grant_id, "state": "active"}

    def revoke(self, origin_context, grant_id: str):
        self.messages.authorize(origin_context)
        if type(grant_id) is not str or not grant_id.startswith("social-consent-"):
            raise ValueError("original consent grant is required")
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM social_consent_grants WHERE grant_id = ? AND owner_key = ?",
                                     (grant_id, role_key(origin_context))).fetchone()
            if row is None or row["grantor_id"] != origin_context.actor.subject.subject_id:
                raise TaskError("social_consent_unavailable", "only the grantor may revoke this projection")
            if row["state"] != "revoked":
                connection.execute("UPDATE social_consent_grants SET state = 'revoked', updated_at = ? WHERE grant_id = ?",
                                   (stamp(self.tasks.now()), grant_id))
            return {"grant_id": grant_id, "state": "revoked"}

    def _allowed(self, row, context):
        kind, actor = _kind(context), context.actor.subject.subject_id
        visibility = row["visibility"]
        if row["origin_kind"] == "fact":
            if visibility == "private_pair":
                return kind == "private" and row["subject_id"] == actor and row["origin_owner"] == private_owner(context)
            if visibility == "shareable":
                if kind == "private" and row["subject_id"] == actor and row["origin_owner"] == private_owner(context):
                    return True
                return bool(kind == "group" and self.runtime.read_one("""SELECT 1 FROM social_consent_grants
                    WHERE semantic_id = ? AND owner_key = ? AND audience_kind = 'group' AND audience_key = ?
                    AND state = 'active' AND grantor_id = ? LIMIT 1""",
                    (row["semantic_id"], role_key(context), scope_key(context), row["subject_id"])))
            return False
        if row["origin_kind"] == "group_fact":
            if visibility != "group_public":
                return False
            if kind == "group":
                return row["origin_owner"] == scope_key(context)
            return kind == "private"  # Exact presence and source checks follow in _project.
        if row["origin_kind"] in ("life", "journal"):
            return (visibility == "public" or kind == "private" and row["subject_id"] == actor
                    and row["subject_id"] != "")
        return visibility == "public"

    def _present_in_group(self, context, group_key, occurred_at):
        """A nearby authenticated group input proves this recipient was present."""
        moment = _moment(occurred_at)
        if moment is None:
            return False
        start, end = stamp(moment - timedelta(minutes=30)), stamp(moment + timedelta(minutes=30))
        rows = self.runtime.read_all("""SELECT e.*, i.event_json, i.event_hash, i.withdrawn,
            b.context_json, b.role_instruction FROM social_thread_events e
            JOIN social_threads t ON t.thread_id = e.thread_id
            JOIN conversation_inputs i ON i.event_key = json_extract(e.metadata_json, '$.event_key')
            JOIN conversation_batches b ON b.batch_id = i.batch_id
            WHERE e.owner_key = ? AND t.kind = 'group' AND t.scope_key = ? AND e.kind = 'inbound'
            AND e.actor_id = ? AND e.occurred_at BETWEEN ? AND ?
            ORDER BY e.occurred_at DESC LIMIT 8""",
            (role_key(context), group_key, context.actor.subject.subject_id, start, end))
        from companion_v2.conversation.messages import event_from_document
        for row in rows:
            if row["withdrawn"]:
                continue
            try:
                original = TaskContext.restore(json.loads(row["context_json"]))
                event = event_from_document(json.loads(row["event_json"]), original)
                if (original.role_binding.role != context.role_binding.role or scope_key(original) != group_key
                        or original.actor.subject != context.actor.subject or event.source.source_id != row["source_id"]
                        or event.source.version.revision != row["source_revision"]
                        or digest([event, original.key, row["role_instruction"]]) != row["event_hash"]):
                    continue
                source = self.messages._read_source(event.source, original, event.capability, self.tasks.now())
                if source.content_hash == event.input_hash:
                    return True
            except (TaskError, ValueError, KeyError, TypeError):
                continue
        return False

    def _project(self, row, context):
        if not self._allowed(row, context):
            return None
        at = self.tasks.now()
        source = codec.source(json.loads(row["source_json"])) if row["source_json"] != "null" else None
        if row["origin_kind"] == "fact":
            original = context
            if context.scope.kind is not ScopeKind.PRIVATE:
                saved = self.runtime.read_one("""SELECT context_json FROM memory_operations
                    WHERE owner_key = ? AND json_extract(result_json, '$.atom_id') = ?
                    ORDER BY created_at DESC LIMIT 1""", (row["origin_owner"], row["origin_id"]))
                if saved is None:
                    raise TaskError("social_origin_unavailable", "original fact context is unavailable")
                original = TaskContext.restore(json.loads(saved["context_json"]))
            fact = require(self.memory.writer.read(original, AtomRef(row["origin_id"], row["origin_revision"])))
            if (original.actor.subject.subject_id != row["subject_id"] or fact["source"] != json.loads(row["source_json"])
                    or digest(fact["representation"]) != row["source_hash"]):
                raise TaskError("social_origin_changed", "fact projection no longer matches its original")
            return {"kind": "fact", "text": fact["representation"], "occurred_at": row["occurred_at"],
                    "confidence": row["confidence"], "source_id": source.source_id,
                    "visibility": row["visibility"]}
        if row["origin_kind"] == "group_fact":
            original = self.runtime.read_one("SELECT * FROM memory_views WHERE view_id = ?",
                                             (row["origin_id"],))
            if original is None or original["revision"] != row["origin_revision"] or original["state"] != "active":
                raise TaskError("social_origin_changed", "group statement changed")
            if context.scope.kind is ScopeKind.GROUP:
                item, actual = self.messages.group_memory.read(context, row["origin_id"], row["origin_revision"])
            else:
                item, actual = self.messages.group_memory._read_row(original, at=at)
                document = json.loads(original["document_json"])
                _, speaker, _ = self.messages.group_memory.access._input_for_owner(
                    original["owner_key"], document["evidence"], at=at)
                from companion_v2.identity_policy.group_audience import group_audience
                collective = group_audience(speaker.scope, speaker.actor.subject).recipients[1]
                require(self.sources.validate_reference_for_audience(actual,
                    recipient=collective, observed_at=at))
                if (scope_key(speaker) != row["origin_owner"]
                        or not self._present_in_group(context, row["origin_owner"], row["occurred_at"])):
                    raise TaskError("social_group_presence_required", "recipient was not present for this group event")
            if (actual != source or item["speaker_id"] == "" or
                    digest(item["statement"]) != row["source_hash"]):
                raise TaskError("social_origin_changed", "group statement lost its original evidence")
            return {"kind": "group_fact", "text": item["statement"], "occurred_at": row["occurred_at"],
                    "confidence": row["confidence"], "source_id": source.source_id,
                    "visibility": row["visibility"]}
        if row["origin_kind"] == "life":
            owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                 context.role_binding.role.subject_id)
            try:
                activity = self.life.activity(row["origin_id"], owner=owner)
            except KeyError:
                raise TaskError("social_origin_unavailable", "role activity was removed") from None
            if activity.revision != row["origin_revision"] or digest(activity) != row["source_hash"]:
                raise TaskError("social_origin_changed", "role activity changed")
            if row["visibility"] == "public" and (activity.reading is not None or
                    activity.work is not None and activity.work.source is not None):
                raise TaskError("social_life_not_public", "public activity acquired a private dependency")
            return {"kind": "life", "text": activity.description, "state": activity.state.value,
                    "occurred_at": row["occurred_at"], "visibility": row["visibility"]}
        if row["origin_kind"] == "journal":
            metadata = json.loads(row["metadata_json"])
            if metadata["state"] != "active" or row["visibility"] == "public":
                return None
            owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                 context.role_binding.role.subject_id)
            key = JournalKey(owner, JournalKind(metadata["kind"]), metadata["local_date"])
            from companion_v2.foundation.refs import ScopeRef
            entry = self.journal.get_for_audience(key, scope=ScopeRef.restore(metadata["scope"]),
                                                  recipient=context.actor.subject, observed_at=at)
            if entry.reference.revision != row["origin_revision"] or digest(entry.draft.body) != row["source_hash"]:
                raise TaskError("social_origin_changed", "journal episode changed")
            return {"kind": "journal", "text": entry.draft.summary or entry.draft.body[:240],
                    "occurred_at": row["occurred_at"], "visibility": "private_pair"}
        return None

    def capture(self, context, batch_id):
        self.messages.authorize(context)
        owner = role_key(context)
        thread = self.thread_for(context)
        refs = []
        rows = self.runtime.read_all("""SELECT * FROM social_semantic_refs WHERE owner_key = ?
            ORDER BY occurred_at DESC, semantic_id DESC LIMIT 48""", (owner,))
        for row in rows:
            if row["origin_kind"] == "fact" and context.scope.kind is ScopeKind.PRIVATE:
                continue  # Existing private memory context already owns this working set.
            if row["origin_kind"] == "group_fact" and context.scope.kind is ScopeKind.GROUP:
                continue  # Existing group memory context already owns this working set.
            try:
                projected = self._project(row, context)
            except (TaskError, ValueError, KeyError):
                continue
            if projected is not None and not self._live_conflict(row, context):
                refs.append({"semantic_id": row["semantic_id"], "revision": row["origin_revision"]})
            if len(refs) == 4:
                break
        return {"version": 1, "context_key": context.key, "owner_key": owner,
                "batch_id": batch_id, "thread_id": thread["thread_id"] if thread else None,
                "thread_state": ({"thread_id": thread["thread_id"], "owed_reply": thread["owed_reply"],
                                  "last_reply_at": thread["last_reply_at"],
                                  "topic": thread["topic"] if thread["kind"] == "group" else ""}
                                 if thread else None), "refs": refs}

    def contributions(self, snapshot, context, batch_id):
        if (type(snapshot) is not dict or snapshot.get("version") != 1
                or snapshot.get("context_key") != context.key or snapshot.get("owner_key") != role_key(context)
                or snapshot.get("batch_id") != batch_id or type(snapshot.get("refs")) is not list
                or len(snapshot["refs"]) > 4):
            raise TaskError("social_snapshot_invalid", "social projection belongs to another audience")
        self.messages.authorize(context)
        materials = []
        thread_id = snapshot.get("thread_id")
        if thread_id is not None:
            thread = self.runtime.read_one("SELECT * FROM social_threads WHERE thread_id = ? AND owner_key = ?",
                                           (thread_id, role_key(context)))
            if thread is None or thread["scope_key"] != scope_key(context) or thread["kind"] != _kind(context):
                raise TaskError("social_snapshot_invalid", "thread moved outside its original audience")
            state = snapshot.get("thread_state")
            if (type(state) is not dict or set(state) != {"thread_id", "owed_reply", "last_reply_at", "topic"}
                    or state["thread_id"] != thread_id or type(state["owed_reply"]) is not int
                    or type(state["last_reply_at"]) is not str or type(state["topic"]) is not str):
                raise TaskError("social_snapshot_invalid", "sealed thread state is invalid")
            materials.append(ContextContribution("social-thread:" + thread_id, "social.runtime",
                "social_thread", ContributionState.READY, encode(state)))
        for ref in snapshot["refs"]:
            if (type(ref) is not dict or set(ref) != {"semantic_id", "revision"}
                    or type(ref["semantic_id"]) is not str or type(ref["revision"]) is not int):
                raise TaskError("social_snapshot_invalid", "social reference is not exact")
            row = self.runtime.read_one("SELECT * FROM social_semantic_refs WHERE semantic_id = ? AND owner_key = ?",
                                        (ref["semantic_id"], role_key(context)))
            if row is None or row["origin_revision"] != ref["revision"]:
                raise TaskError("social_origin_changed", "social reference changed after capture")
            projected = self._project(row, context)
            if projected is None:
                raise TaskError("social_audience_denied", "cross-window fact is no longer allowed here")
            materials.append(ContextContribution("social-semantic:" + row["semantic_id"], "social.runtime",
                "social_projection", ContributionState.READY, encode(projected)))
        return tuple(materials)
