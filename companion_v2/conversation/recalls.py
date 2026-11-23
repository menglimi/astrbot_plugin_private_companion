"""Durable platform withdrawal facts, independent of the current chat persona.

Only a trusted host adapter may submit these facts. They withdraw availability;
they do not impersonate a user command or rewrite a completed delivery receipt.
"""

from dataclasses import dataclass
from datetime import timedelta
import json
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.storage.codec import decode_timestamp
from companion_v2.storage.repositories import InboxRepository


CONSUMER = "conversation.platform_recall.v1"


@dataclass(frozen=True)
class MessageRecall:
    installation_id: str
    platform: str
    bot_id: str
    account_id: str
    kind: ScopeKind
    native_scope_id: str
    message_id: str
    sender_id: str

    def __post_init__(self):
        if self.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            raise ValueError("recall requires a private or group native route")
        for name in ("installation_id", "platform", "bot_id", "account_id", "native_scope_id", "message_id", "sender_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip() or len(value) > 1024:
                raise ValueError("recall route identifiers must be bounded and explicit")

    @classmethod
    def from_scope(cls, scope, message_id, sender_id):
        return cls(scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                   scope.kind, scope.native_scope_id, message_id, sender_id)

    @property
    def key(self):
        """The marker belongs to the message and route, never the operator.

        A group administrator may recall another member's message. OneBot then
        puts the moderator in ``user_id``; keying the marker by that id would
        make the original author's withdrawal invisible. V1 keyed its recalled
        id cache by message and scope for the same reason.
        """
        return "recall-" + digest([self.installation_id, self.platform, self.bot_id,
                                    self.account_id, self.kind.value, self.native_scope_id,
                                    self.message_id])

    @property
    def legacy_key(self):
        """Read markers written before the operator id was removed from the key."""
        return "recall-" + digest(self)

    def matches(self, scope):
        return (scope.installation_id, scope.platform, scope.bot_id, scope.account_id, scope.kind,
                scope.native_scope_id) == (self.installation_id, self.platform, self.bot_id,
                                           self.account_id, self.kind, self.native_scope_id)


class ConversationRecalls:
    def __init__(self, messages):
        self.messages, self.runtime = messages, messages.runtime
        self.inbox = InboxRepository(self.runtime)

    def recalled(self, scope, message_id, sender_id):
        if not scope.is_resolved:
            return False
        marker = MessageRecall.from_scope(scope, message_id, sender_id)
        for key in (marker.key, marker.legacy_key):
            if self.runtime.read_one("SELECT 1 FROM inbox WHERE consumer = ? AND event_id = ?",
                                     (CONSUMER, key)) is not None:
                return True
        return False

    def check(self, scope, message_id, sender_id):
        if self.recalled(scope, message_id, sender_id):
            raise TaskError("message_withdrawn", "the original native message has been withdrawn")

    @staticmethod
    def platform_reference(scope, message_id):
        return "onebot-message:" + digest([scope.platform, scope.account_id])[:24] + ":" + message_id

    def transcribe(self, context, operator, settings, *, at=None):
        """Read what this window's own withdrawn inputs said, bounded and read-only.

        V1 needed a separate short-lived cache for this. V2 keeps the admitted input
        row and marks withdrawal in place, so the original text is still here under
        its own scope; this read never revives the withdrawn source, never publishes
        a new one and never returns anything to generation. Only an operator who may
        manage this installation's memory can ask, which is V1's "Bot administrator"
        rule expressed with the V2 tier system.
        """
        if type(settings) is not dict or not settings.get("transcribe_enabled"):
            raise TaskError("recall_transcribe_disabled", "withdrawn-message transcription is not enabled here")
        if not context.scope.is_resolved or context.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            raise TaskError("recall_scope_denied", "transcription requires one resolved conversation window")
        observed_at = at or self.messages.tasks.now()
        require(ObjectAccessPolicy(self.runtime).scope_access(operator, scope=context.scope,
            action="memory.manage", observed_at=observed_at, core_only=True))
        zone = ZoneInfo(settings.get("timezone", "Asia/Shanghai"))
        since = observed_at - timedelta(minutes=settings["transcribe_window_minutes"])
        limit = settings["transcribe_max_items"]
        characters = settings["transcribe_text_chars"]
        rows = self.runtime.read_all("""SELECT i.event_key, i.batch_id, b.created_at, i.event_json,
                s.payload_json, s.content_hash
            FROM conversation_inputs i JOIN conversation_batches b ON b.batch_id = i.batch_id
            JOIN sources s ON s.source_id = json_extract(i.event_json, '$.source.source_id')
                AND s.lineage_id = json_extract(i.event_json, '$.source.version.lineage_id')
                AND s.source_revision = json_extract(i.event_json, '$.source.version.revision')
            WHERE i.withdrawn = 1 AND b.origin_key = ?
              AND json_extract(i.event_json, '$.scope.platform') = ?
              AND json_extract(i.event_json, '$.scope.bot_id') = ?
              AND json_extract(i.event_json, '$.scope.account_id') = ?
              AND json_extract(i.event_json, '$.scope.kind') = ?
              AND json_extract(i.event_json, '$.scope.native_scope_id') = ?
            ORDER BY b.created_at DESC, i.position DESC LIMIT 64""",
            (context.key, context.scope.platform, context.scope.bot_id, context.scope.account_id,
             context.scope.kind.value, context.scope.native_scope_id))
        from .native_observations import observations_available
        if observations_available(self.runtime):
            native = self.runtime.read_all("""SELECT n.event_id, n.context_json, n.observed_at AS created_at,
                    n.content_hash, s.payload_json, s.content_hash AS source_hash
                FROM conversation_native_observations n JOIN sources s
                  ON s.source_id = json_extract(n.source_json, '$.source_id')
                 AND s.lineage_id = json_extract(n.source_json, '$.version.lineage_id')
                 AND s.source_revision = json_extract(n.source_json, '$.version.revision')
                WHERE n.kind = 'input' AND n.context_key = ?
                ORDER BY n.observed_at DESC, n.rowid DESC LIMIT 64""", (context.key,))
            projected = []
            for row in native:
                try:
                    from companion_v2.execution.models import TaskContext
                    from companion_v2.storage.native_dedup import resolve_context_json
                    resolved_ctx = json.loads(resolve_context_json(self.runtime, row["context_json"]))
                    original = TaskContext.restore(resolved_ctx)
                    sender = original.actor.principal.native_account_id
                    if (original.scope != context.scope or original.key != context.key
                            or row["source_hash"] != row["content_hash"]
                            or not self.recalled(original.scope, row["event_id"], sender)):
                        continue
                    data = {"scope": resolved_ctx["audience"]["scope"],
                            "principal": {"native_account_id": sender}}
                    projected.append({"event_key": digest([original.scope, sender, row["event_id"]]),
                                      "event_json": json.dumps(data), **dict(row)})
                except (ValueError, KeyError, TypeError):
                    continue
            rows = (*rows, *projected)
        entries, seen = [], set()
        for row in sorted(rows, key=lambda item: item["created_at"], reverse=True):
            if len(entries) >= limit:
                break
            created = decode_timestamp(row["created_at"])
            if created < since or created > observed_at or row["event_key"] in seen:
                continue
            try:
                data = json.loads(row["event_json"])
                if ScopeRef.restore(data["scope"]) != context.scope:
                    continue
                # The withdrawn original is read exactly as it was admitted: a
                # payload that no longer matches its own hash is never shown.
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, str) or digest(payload) != row["content_hash"]:
                    continue
                text = " ".join(payload.split())[:characters]
            except (ValueError, TypeError, KeyError):
                continue
            seen.add(row["event_key"])
            entries.append({"withdrawn_at": row["created_at"],
                            "local_time": created.astimezone(zone).strftime("%H:%M"),
                            "author": str(data.get("principal", {}).get("native_account_id", "")),
                            "text": text})
        return {"state": "ok" if entries else "empty", "entries": entries,
                "window_minutes": settings["transcribe_window_minutes"]}

    def check_reply(self, scope, platform_ref):
        prefix = self.platform_reference(scope, "")
        if isinstance(platform_ref, str) and platform_ref.startswith(prefix):
            self.check(scope, platform_ref[len(prefix):], scope.account_id)

    def _revoke(self, reference):
        row = self.runtime.read_one("""SELECT state, governance_revision FROM sources
            WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""",
            (reference.source_id, reference.version.lineage_id, reference.version.revision))
        if row is None or row["state"] != "active":
            return 0
        require(self.messages.sources.revoke(reference, expected_governance_revision=row["governance_revision"],
                                             observed_at=self.messages.tasks.now()))
        return 1

    def _invalidate(self, connection, batch_id):
        connection.execute("""UPDATE conversation_batches SET
            state = CASE WHEN state = 'open' THEN 'open' ELSE 'invalidated' END,
            revision = revision + 1 WHERE batch_id = ? AND state NOT IN ('cancelled', 'invalidated')""", (batch_id,))
        connection.execute("UPDATE conversation_parts SET state = 'cancelled' WHERE batch_id = ? AND state = 'pending'", (batch_id,))

    def _cancel_reply(self, batch_id):
        service = getattr(self.messages, "reply_settings", None)
        if service is None:
            return True
        row = self.runtime.read_one("SELECT context_json FROM conversation_batches WHERE batch_id = ?", (batch_id,))
        if row is None:
            return True
        try:
            from companion_v2.execution.models import TaskContext
            context = TaskContext.restore(json.loads(row["context_json"]))
            settings = service.capture(context, self.messages.tasks.now())["settings"]
            return bool(settings.get("recall_silence_enabled", True))
        except Exception:
            return True

    def _inputs(self, notice):
        # Page routing metadata only. Native IDs survive /new and persona changes;
        # reconstructing a key with the currently selected role would miss them.
        after = 0
        while True:
            rows = self.runtime.read_all("""SELECT rowid AS input_sequence, * FROM conversation_inputs
                WHERE rowid > ? AND json_extract(event_json, '$.scope.installation_id') = ?
                AND json_extract(event_json, '$.scope.platform') = ?
                AND json_extract(event_json, '$.scope.bot_id') = ?
                AND json_extract(event_json, '$.scope.account_id') = ?
                AND json_extract(event_json, '$.scope.kind') = ?
                AND json_extract(event_json, '$.scope.native_scope_id') = ?
                AND (json_extract(event_json, '$.event_id') = ? OR EXISTS (
                    SELECT 1 FROM json_each(event_json, '$.quotes') q
                    WHERE json_extract(q.value, '$.message_id') = ?))
                ORDER BY rowid LIMIT 32""",
                (after, notice.installation_id, notice.platform, notice.bot_id, notice.account_id,
                 notice.kind.value, notice.native_scope_id, notice.message_id, notice.message_id))
            if not rows:
                return
            for row in rows:
                after = row["input_sequence"]
                yield row

    @outcome
    def accept(self, notice):
        if type(notice) is not MessageRecall or notice.installation_id != self.runtime.installation_id:
            raise TaskError("recall_route_mismatch", "recall must belong to this original installation")
        from .messages import source_from_document
        from .quotes.service import quote_reference

        with self.runtime.transaction() as connection:
            require(self.messages.tasks.check_generation())
            receipt = require(self.inbox.accept(consumer=CONSUMER, event_id=notice.key,
                payload_hash=digest(notice), observed_at=self.messages.tasks.now()))
            withdrawn, quoted, replies, cancelled = 0, 0, 0, 0
            for row in self._inputs(notice):
                data = json.loads(row["event_json"])
                scope = ScopeRef.restore(data["scope"])
                if not notice.matches(scope):
                    raise TaskError("recall_route_mismatch", "stored native route changed")
                parent = source_from_document(data["source"])
                cancel = self._cancel_reply(row["batch_id"])
                direct = data["event_id"] == notice.message_id
                if direct and not row["withdrawn"]:
                    self._revoke(parent)
                    connection.execute("UPDATE conversation_inputs SET withdrawn = 1 WHERE event_key = ?", (row["event_key"],))
                    if cancel:
                        self._invalidate(connection, row["batch_id"])
                        cancelled += 1
                    withdrawn += 1
                for quote in data.get("quotes", ()):
                    if quote["message_id"] == notice.message_id:
                        changed = self._revoke(quote_reference(parent, quote["position"]))
                        quoted += changed
                        if changed and cancel:
                            self._invalidate(connection, row["batch_id"])
                            cancelled += 1
            from .native_observations import observations_available
            if observations_available(self.runtime):
                from companion_v2.execution.models import TaskContext
                from companion_v2.storage.native_dedup import resolve_context_json
                for row in self.runtime.read_all(
                    """SELECT source_json, context_json
                       FROM conversation_native_observations
                       WHERE kind = 'input' AND event_id = ?""",
                    (notice.message_id,),
                ):
                    original = TaskContext.restore(json.loads(resolve_context_json(self.runtime, row["context_json"])))
                    scope = original.scope
                    if (
                        scope.installation_id != notice.installation_id
                        or scope.platform != notice.platform
                        or scope.bot_id != notice.bot_id
                        or scope.account_id != notice.account_id
                        or scope.kind != notice.kind
                        or scope.native_scope_id != notice.native_scope_id
                    ):
                        continue
                    if not notice.matches(scope):
                        raise TaskError("recall_route_mismatch", "stored native observation route changed")
                    withdrawn += self._revoke(source_from_document(json.loads(row["source_json"])))
            platform_ref = "onebot-message:" + digest([notice.platform, notice.account_id])[:24] + ":" + notice.message_id
            for row in self.runtime.read_all("""SELECT p.*, b.context_json FROM conversation_parts p
                JOIN conversation_batches b ON b.batch_id = p.batch_id WHERE p.platform_ref = ?""", (platform_ref,)):
                from companion_v2.execution.models import TaskContext
                original = TaskContext.restore(json.loads(row["context_json"]))
                if notice.matches(original.scope):
                    if row["delivered_at"] and self.messages.confirmed_reply_sources is not None:
                        self._revoke(self.messages.confirmed_reply_sources.reference(row))
                    replies += 1
            # A missing original is still a committed withdrawal. Late inputs,
            # quotes and delivery acknowledgements must consult this same marker.
            return {"replayed": receipt.replayed, "withdrawn_inputs": withdrawn,
                    "revoked_quotes": quoted, "recalled_replies": replies,
                    "cancelled": cancelled}
