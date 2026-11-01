"""Durable temporary-context boundaries for a trusted native window reset.

Receipts and original sources remain available to long-term memory provenance.
Row cutoffs also exclude late replies to batches admitted before the reset.
"""

from dataclasses import dataclass
import json

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.storage.repositories import InboxRepository


PREFIX = "conversation.window_reset.v1:"
CONSUMER = "conversation.window_reset.v1"


@dataclass(frozen=True)
class ConversationWindow:
    installation_id: str
    platform: str
    kind: ScopeKind
    native_scope_id: str
    bot_id: str
    account_id: str
    session_id: str

    def __post_init__(self):
        if self.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            raise ValueError("a reset requires a private or group window")
        scope = ScopeRef(self.installation_id, self.platform, self.kind, self.native_scope_id,
                         self.bot_id, self.account_id, "window-reset", self.session_id)
        if not scope.is_resolved:
            raise ValueError("a reset requires the complete native window")

    @classmethod
    def from_scope(cls, scope):
        return cls(**{key: getattr(scope, key) for key in cls.__dataclass_fields__})

    @property
    def key(self):
        return PREFIX + digest(self)

    def sql(self, column, path="$"):
        """Internal SQL callers supply the JSON column/path, never user input."""
        fields = self.__dataclass_fields__
        where = " AND ".join(f"json_extract({column}, '{path}.{key}') = ?" for key in fields)
        return where, tuple(self.kind.value if key == "kind" else getattr(self, key) for key in fields)


def boundary(runtime, scope):
    if scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP) or not scope.is_resolved:
        return None
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                           (ConversationWindow.from_scope(scope).key,))
    if row is None:
        return None
    try:
        saved = json.loads(row["value"])
        if (type(saved) is not dict or saved.get("version") != 1
                or any(type(saved.get(key)) is not int or saved[key] < 0
                       for key in ("batch_sequence", "background_sequence"))
                or type(saved.get("reset_at")) is not str):
            raise ValueError("invalid reset boundary")
        return saved
    except (ValueError, TypeError) as error:
        raise TaskError("conversation_reset_unavailable", "the window reset boundary is unavailable") from error


def batch_current(runtime, batch_id, scope=None):
    row = runtime.read_one("SELECT rowid AS sequence, context_json FROM conversation_batches WHERE batch_id = ?",
                           (batch_id,))
    if row is None:
        return False
    scope = scope or ScopeRef.restore(json.loads(row["context_json"])["audience"]["scope"])
    saved = boundary(runtime, scope)
    return saved is None or row["sequence"] > saved["batch_sequence"]


def require_current_batch(runtime, batch_id, scope):
    if not batch_current(runtime, batch_id, scope):
        raise TaskError("conversation_context_reset", "the original turn precedes this window's reset")


def background_current(runtime, delivery_id, scope):
    saved = boundary(runtime, scope)
    if saved is None:
        return True
    row = runtime.read_one("SELECT rowid AS sequence FROM background_deliveries WHERE delivery_id = ?", (delivery_id,))
    return row is not None and row["sequence"] > saved["background_sequence"]


def source_current(runtime, reference):
    """Only temporary/raw recall uses this check; durable evidence still reads its source.

    Callers rechecking evidence that was already stored (memory provenance, a
    learned rule's samples) must not gate it on this boundary: see
    :func:`durable_reset_at`.
    """
    saved = boundary(runtime, reference.scope)
    if saved is None:
        return True
    row = runtime.read_one("""SELECT batch_id FROM conversation_inputs
        WHERE json_extract(event_json, '$.source.source_id') = ? LIMIT 1""", (reference.source_id,))
    if row is None and reference.owner == "conversation.confirmed_reply":
        row = runtime.read_one("SELECT batch_id FROM conversation_parts WHERE part_id = ?",
                               (reference.version.lineage_id,))
    return (batch_current(runtime, row["batch_id"], reference.scope) if row is not None
            else stamp(reference.issued_at) > saved["reset_at"])


def durable_reset_at(runtime, scope):
    """The reset moment stored evidence of this window predates, if there is one.

    ``/reset`` ends the temporary conversation context: raw recall, in-flight
    batches, session atoms and pending observations. Evidence that was already
    stored for a durable fact or a learned rule is not revoked by it: that
    evidence keeps reading its governed source (state, expiry, content hash,
    withdrawal) and the audience, role and scope it was captured under, so the
    boundary only dates it. A caller can therefore label such evidence as
    historical instead of hiding it. Returns ``None`` when the window was not
    reset, and also when the boundary itself cannot be read: dating evidence is
    informational, so a damaged boundary must not revoke it.
    """
    try:
        saved = boundary(runtime, scope)
    except TaskError:
        return None
    return None if saved is None else saved["reset_at"]


def _clear_group_observations(connection, window):
    from companion_v2.identity_policy.personas import ConversationRoute
    from companion_v2.memory.group.access import group_owner_key
    from .group_observation import GroupPresenceStore, MOOD_KIND, MOMENTS_KIND, JOKE_KIND, TOPIC_KIND

    where, values = window.sql("context_json", "$.audience.scope")
    roles = {row[0] for row in connection.execute("""SELECT DISTINCT
        json_extract(context_json, '$.role_binding.role.subject_id') FROM conversation_batches WHERE """ + where,
        values)}
    roles.update(row[0] for row in connection.execute("""SELECT DISTINCT role_id FROM role_bindings
        WHERE installation_id = ? AND platform = ? AND scope_kind = 'group'
        AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND session_id = ?""",
        (window.installation_id, window.platform, window.native_scope_id,
         window.bot_id, window.account_id, window.session_id)))
    # Early observations can exist without a reply batch or a legacy exact
    # binding. Include every persona registered for this window's routes.
    where, values = window.sql("record_json", "$.scope")
    for row in connection.execute("SELECT record_json FROM conversation_routes WHERE " + where, values).fetchall():
        route = ConversationRoute.restore(json.loads(row[0]))
        selectors = tuple(selector.key for selector, _ in route.selectors())
        roles.update(row[0] for row in connection.execute("""SELECT DISTINCT
            json_extract(record_json, '$.role.subject_id') FROM persona_binding_events
            WHERE selector_key IN (""" + ",".join("?" for _ in selectors) + ")", selectors))
    for role_id in roles:
        role = SubjectRef(window.installation_id, SubjectKind.BOT, role_id)
        owner = group_owner_key(window.installation_id, window.platform, window.bot_id, window.account_id,
                                window.native_scope_id, window.session_id, role)
        for kind in (MOOD_KIND, MOMENTS_KIND, JOKE_KIND, TOPIC_KIND):
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (GroupPresenceStore.key(owner, kind),))


def reset_window(runtime, window, *, reset_id, at):
    """Trusted host lifecycle operation, outside the role/model command surface."""
    if type(window) is not ConversationWindow or window.installation_id != runtime.installation_id:
        raise TaskError("conversation_reset_route", "the reset must belong to this installation's native window")
    with runtime.transaction() as connection:
        receipt = require(InboxRepository(runtime).accept(consumer=CONSUMER, event_id=reset_id,
            payload_hash=digest(window), observed_at=at))
        if receipt.replayed:
            where, values = window.sql("b.context_json", "$.audience.scope")
            rows = connection.execute("""SELECT b.batch_id FROM conversation_batches b
                JOIN conversation_flows f ON f.batch_id = b.batch_id
                WHERE b.state = 'cancelled' AND f.reason = 'context_reset' AND """ + where, values).fetchall()
            return {"handled": True, "replayed": True, "batches": [row[0] for row in rows], "temporary_atoms": 0}
        saved = {"version": 1, "reset_at": stamp(at),
                 "batch_sequence": connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM conversation_batches").fetchone()[0],
                 "background_sequence": connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM background_deliveries").fetchone()[0]}
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (window.key, encode(saved)))
        where, values = window.sql("b.context_json", "$.audience.scope")
        rows = connection.execute("""SELECT b.batch_id, b.origin_key FROM conversation_batches b
            LEFT JOIN conversation_flows f ON f.batch_id = b.batch_id WHERE """ + where + """
            AND (b.state IN ('open', 'sealed') OR f.state IN ('collecting', 'generating', 'delivering'))""",
            values).fetchall()
        for row in rows:
            connection.execute("UPDATE conversation_batches SET state = 'cancelled', revision = revision + 1 WHERE batch_id = ?",
                               (row["batch_id"],))
            connection.execute("UPDATE conversation_parts SET state = 'cancelled' WHERE batch_id = ? AND state = 'pending'",
                               (row["batch_id"],))
            connection.execute("UPDATE conversation_flows SET state = 'closed', reason = 'context_reset' WHERE batch_id = ?",
                               (row["batch_id"],))
        # Query handles from old turns must not remain usable after /reset keeps its cid.
        connection.execute("""UPDATE memory_sessions SET expires_at = ?, state_json = ? WHERE context_key IN
            (SELECT b.origin_key FROM conversation_batches b WHERE """ + where + ")",
            (stamp(at), encode({"reads": [], "notes": [], "pages": {}}), *values))
        from .native_observations import observations_available
        if observations_available(runtime):
            native_where, native_values = window.sql("context_json", "$.audience.scope")
            connection.execute("""UPDATE memory_sessions SET expires_at = ?, state_json = ? WHERE context_key IN
                (SELECT context_key FROM conversation_native_observations WHERE """ + native_where + ")",
                (stamp(at), encode({"reads": [], "notes": [], "pages": {}}), *native_values))
            from .native_turns import turns_available
            if turns_available(runtime):
                connection.execute("""UPDATE conversation_native_turns SET generation_state = 'failed', phase_at = ?
                    WHERE generation_state IN ('prepared', 'running') AND """ + native_where,
                    (stamp(at), *native_values))
        from companion_v2.memory.reset import expire_temporary
        expired = expire_temporary(runtime, window, at=at)
        if window.kind is ScopeKind.GROUP:
            _clear_group_observations(connection, window)
        return {"handled": True, "replayed": False, "batches": [row["batch_id"] for row in rows],
                "temporary_atoms": expired}
