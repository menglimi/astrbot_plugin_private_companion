"""Current identity tiers, explicit action contracts, and exact object sharing.

Mutation methods are trusted control-plane primitives. They are not model tools or
public management endpoints; a host adapter must establish its own control-plane
authorization before exposing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ReferenceValidationError, ScopeKind, ScopeRef, SubjectRef, normalize_utc
from companion_v2.storage.codec import decode_timestamp, scope_values
from companion_v2.storage.runtime import StorageRuntime

from .models import AuthorizationTier, ResolvedIdentity, _opaque
from .repository import PersistentIdentityDirectory
from .personas import PersonaDirectory


@dataclass(frozen=True)
class ActionDefinition:
    action: str
    domains: tuple[str, ...]
    core_only: bool = False
    shareable: bool = True
    private_owner_without_grant: bool = False

    def __post_init__(self) -> None:
        _opaque(self.action, field="action")
        if any(type(value) is not bool for value in (
                self.core_only, self.shareable, self.private_owner_without_grant)):
            raise ReferenceValidationError("action flags must be boolean")
        if not isinstance(self.domains, tuple) or not self.domains:
            raise ReferenceValidationError("action requires explicit object domains")
        for domain in self.domains:
            _opaque(domain, field="domain")


DEFAULT_ACTIONS = (
    *(ActionDefinition(f"memo.{action}", ("memo",)) for action in (
        "read", "edit", "complete", "reopen", "pin", "unpin", "delete", "delete.commit", "reminder.set",
    )),
    ActionDefinition("memo.create", ("memo",), shareable=False),
    ActionDefinition("memo.list", ("memo",), shareable=False),
    *(ActionDefinition(f"reminder.{action}", ("reminder",)) for action in (
        "read", "cancel", "reschedule", "deliver",
    )),
    ActionDefinition("reminder.create", ("reminder",), shareable=False),
    ActionDefinition("reminder.list", ("reminder",), shareable=False),
    ActionDefinition("operation.lookup", ("operation", "memo", "reminder")),
    ActionDefinition("access.policy.set", ("access-policy",), core_only=True, shareable=False),
    ActionDefinition("configuration.read", ("configuration",), shareable=False),
    ActionDefinition("configuration.write", ("configuration",), shareable=False),
    ActionDefinition("conversation.reply", ("conversation",), shareable=False),
    ActionDefinition("proactive.contact", ("proactive",), shareable=False),
    ActionDefinition("role.journal.write", ("role.journal",), shareable=False),
    ActionDefinition("role.growth.read", ("role.growth",), shareable=False),
    # The scheduled daily review only reads the role's own recorded behaviour in
    # the exact private window it is bound to, so that window's verified owner can
    # run it without a per-window grant. The evidence catalog still rechecks the
    # private scope and exact audience on every read, and no other window opens.
    ActionDefinition("role.self_review.read", ("role.self_review",), shareable=False,
                     private_owner_without_grant=True),
    # A verified private route can read its own expression samples and rules;
    # group tasks still require a grant for the exact speaker and group.
    ActionDefinition("role.expression.read", ("role.expression",), shareable=False,
                     private_owner_without_grant=True),
    ActionDefinition("role.growth.propose", ("role.growth",), shareable=False),
    ActionDefinition("role.growth.manage", ("role.growth",), core_only=True, shareable=False),
    ActionDefinition("role.reading.record", ("role.life",), shareable=False),
    *(ActionDefinition(action, ("library",), shareable=False)
      for action in ("library.catalog", "library.read")),
    ActionDefinition("role.life.run", ("role.life",), shareable=False),
    ActionDefinition("role.life.read", ("role.life",), shareable=False),
    ActionDefinition("role.life.manage", ("role.life",), shareable=False),
    *(ActionDefinition(action, ("role.state",), shareable=False)
      for action in ("role.state.read", "role.state.maintain", "platform.typing", "platform.presence")),
    # The private route owner enters the task runner without a per-window grant;
    # CalendarAccess still checks the role-wide switch at each calendar operation.
    *(ActionDefinition(action, ("calendar",), shareable=False, private_owner_without_grant=True)
      for action in ("calendar.read", "calendar.write")),
    *(ActionDefinition(f"relationship.memory.{action}", ("relationship.memory",), shareable=False)
      for action in ("read", "propose", "revise", "review", "adopt", "retract")),
    *(ActionDefinition(f"memory.{action}", ("memory",), shareable=False)
      for action in ("read", "propose", "correct", "retract", "protect", "forget", "reflect", "subscribe")),
    ActionDefinition("memory.manage", ("memory",), core_only=True, shareable=False),
    ActionDefinition("extension.tool.invoke", ("capability",), shareable=False),
    ActionDefinition("capability.view", ("capability",), shareable=False),
    *(ActionDefinition(f"capability.{action}", ("capability",), core_only=True, shareable=False)
      for action in ("acquire", "update", "pause", "resume", "discard.request", "role.set")),
    ActionDefinition("capability.consent.set", ("capability-consent",), shareable=False),
    ActionDefinition("diagnostics.storage.read", ("diagnostics",), core_only=True, shareable=False),
    ActionDefinition("diagnostics.operation.read", ("diagnostics",), core_only=True, shareable=False),
)


class ActionRegistry:
    """Unknown actions never inherit an implicit non-core classification."""

    def __init__(self, definitions: tuple[ActionDefinition, ...] = DEFAULT_ACTIONS) -> None:
        values = {definition.action: definition for definition in definitions}
        if len(values) != len(definitions):
            raise ReferenceValidationError("duplicate access action")
        self.definitions = MappingProxyType(values)

    def get(self, action: str) -> ActionDefinition | None:
        return self.definitions.get(action)


@dataclass(frozen=True)
class ObjectAccessDecision:
    tier: AuthorizationTier
    policy_revision: int
    identity_policy_revision: int


class ObjectAccessPolicy:
    """Authorization is always recomputed from the current persistent records."""

    def __init__(self, runtime: StorageRuntime, *, registry: ActionRegistry | None = None) -> None:
        self.runtime = runtime
        self.identity = PersistentIdentityDirectory(runtime)
        self.registry = registry or ActionRegistry()

    @property
    def revision(self) -> int:
        row = self.runtime.read_one("SELECT revision FROM object_policy_metadata WHERE singleton = 1", ())
        if row is None:
            raise ReferenceValidationError("object policy metadata is missing")
        return row["revision"]

    def _installation(self, installation_id: str) -> None:
        if installation_id != self.runtime.installation_id:
            raise ReferenceValidationError("object policy crosses installation")

    def _expect(self, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision != self.revision:
            raise ReferenceValidationError("object_policy_revision_conflict")

    @staticmethod
    def _advance(connection) -> int:
        connection.execute("UPDATE object_policy_metadata SET revision = revision + 1 WHERE singleton = 1")
        return connection.execute("SELECT revision FROM object_policy_metadata WHERE singleton = 1").fetchone()["revision"]

    def set_tier(
        self, subject: SubjectRef, tier: AuthorizationTier, *, expected_revision: int, observed_at: datetime,
    ) -> int:
        self._installation(subject.installation_id)
        if not isinstance(tier, AuthorizationTier):
            raise ReferenceValidationError("tier must be an AuthorizationTier")
        timestamp = normalize_utc(observed_at, field="observed_at").isoformat()
        with self.runtime.transaction() as connection:
            self._expect(expected_revision)
            revision = self._advance(connection)
            connection.execute(
                """INSERT INTO subject_access_tiers VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(subject_kind, subject_id) DO UPDATE SET
                   tier = excluded.tier, revision = excluded.revision, updated_at = excluded.updated_at""",
                (subject.kind.value, subject.subject_id, tier.value, revision, timestamp),
            )
            return revision

    def register_object(self, object_key: str, *, owner: SubjectRef, scope: ScopeRef, domain: str) -> int:
        _opaque(object_key, field="object_key")
        _opaque(domain, field="domain")
        self._installation(owner.installation_id)
        self._installation(scope.installation_id)
        if not any(domain in definition.domains for definition in self.registry.definitions.values()):
            raise ReferenceValidationError("unknown object domain")
        values = (object_key, owner.kind.value, owner.subject_id, *scope_values(scope), domain)
        with self.runtime.transaction() as connection:
            existing = connection.execute("SELECT * FROM access_objects WHERE object_key = ?", (object_key,)).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ReferenceValidationError("object_ownership_conflict")
                return self.revision
            connection.execute("INSERT INTO access_objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            return self._advance(connection)

    def share_object(
        self, object_key: str, subject: SubjectRef, actions: tuple[str, ...], *, expected_revision: int,
        observed_at: datetime, expires_at: datetime | None = None,
    ) -> int:
        """Grant the listed actions only; other existing action shares are unchanged."""
        _opaque(object_key, field="object_key")
        self._installation(subject.installation_id)
        observed_at = normalize_utc(observed_at, field="observed_at")
        if expires_at is not None:
            expires_at = normalize_utc(expires_at, field="expires_at")
            if expires_at <= observed_at:
                raise ReferenceValidationError("share expiration must follow observed_at")
        if not isinstance(actions, tuple) or not actions or len(set(actions)) != len(actions):
            raise ReferenceValidationError("share requires unique explicit actions")
        with self.runtime.transaction() as connection:
            self._expect(expected_revision)
            record = connection.execute("SELECT domain FROM access_objects WHERE object_key = ?", (object_key,)).fetchone()
            if record is None:
                raise ReferenceValidationError("object_not_registered")
            for action in actions:
                definition = self.registry.get(action)
                if (definition is None or definition.core_only or not definition.shareable
                        or record["domain"] not in definition.domains):
                    raise ReferenceValidationError("action_cannot_be_shared_for_object")
            revision = self._advance(connection)
            for action in actions:
                connection.execute(
                    """INSERT INTO object_access_shares VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                       ON CONFLICT(object_key, subject_kind, subject_id, action) DO UPDATE SET
                       state = 'active', revision = excluded.revision, valid_from = excluded.valid_from,
                       expires_at = excluded.expires_at, updated_at = excluded.updated_at""",
                    (object_key, subject.kind.value, subject.subject_id, action, revision,
                     observed_at.isoformat(), expires_at.isoformat() if expires_at else None, observed_at.isoformat()),
                )
            return revision

    def revoke_share(
        self, object_key: str, subject: SubjectRef, actions: tuple[str, ...] | None = None, *,
        expected_revision: int, observed_at: datetime,
    ) -> int:
        """Revoke listed actions, or all of this subject's shares on one object."""
        _opaque(object_key, field="object_key")
        self._installation(subject.installation_id)
        timestamp = normalize_utc(observed_at, field="observed_at").isoformat()
        if actions is not None and (not isinstance(actions, tuple) or not actions or len(set(actions)) != len(actions)):
            raise ReferenceValidationError("revocation requires unique explicit actions")
        with self.runtime.transaction() as connection:
            self._expect(expected_revision)
            revision = self._advance(connection)
            parameters = (revision, timestamp, object_key, subject.kind.value, subject.subject_id)
            statement = """UPDATE object_access_shares SET state = 'revoked', revision = ?, updated_at = ?
                           WHERE object_key = ? AND subject_kind = ? AND subject_id = ?"""
            if actions is not None:
                statement += " AND action IN (" + ",".join("?" for _ in actions) + ")"
                parameters += actions
            connection.execute(statement, parameters)
            return revision

    def feature_access(
        self, actor: ResolvedIdentity, *, scope: ScopeRef, action: str, observed_at: datetime,
        allow_private_owner: bool = False,
    ) -> Outcome[ObjectAccessDecision]:
        """Check the feature only; this result never authorizes an arbitrary object."""
        observed_at = normalize_utc(observed_at, field="observed_at")
        with self.runtime.read_snapshot():
            if (scope.installation_id != self.runtime.installation_id
                    or actor.subject.installation_id != self.runtime.installation_id
                    or actor.principal.installation_id != self.runtime.installation_id):
                return Outcome.failed("scope_installation_mismatch", "access belongs to another installation")
            current = self.identity.resolve_authenticated(actor.principal, observed_at=observed_at)
            if not current.is_success:
                return Outcome(failure=current.failure)
            identity = current.unwrap()
            if (identity.subject != actor.subject or identity.binding_id != actor.binding_id
                    or identity.binding_revision != actor.binding_revision):
                return Outcome.failed("binding_rebound", "the resolved account binding is no longer current")
            definition = self.registry.get(action)
            if definition is None:
                return Outcome.failed("unknown_access_action", "action has no explicit authorization contract")
            row = self.runtime.read_one(
                "SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                (actor.subject.kind.value, actor.subject.subject_id),
            )
            tier = AuthorizationTier(row["tier"]) if row else AuthorizationTier.STANDARD
            if definition.core_only and tier is not AuthorizationTier.CORE:
                return Outcome.failed("core_access_required", "action requires installation core membership")
            route = PersonaDirectory(self.runtime).route(scope) if (
                allow_private_owner and definition.private_owner_without_grant
                and scope.kind is ScopeKind.PRIVATE
            ) else None
            private_owner_feature = route is not None and route.user == actor.subject
            from .grants import PERSON_PERMISSION_GRANT_PREFIX
            explicit_private_decision = scope.kind is ScopeKind.PRIVATE and action in (
                "conversation.reply", "proactive.contact") and any(
                    grant.scope == scope and grant.capability == action
                    and grant.grant_id.startswith(PERSON_PERMISSION_GRANT_PREFIX)
                    for grant in self.identity.grants_for_subject(actor.subject))
            if explicit_private_decision or tier is AuthorizationTier.STANDARD and not private_owner_feature:
                granted = self.identity.authorize(actor, scope=scope, capability=action, observed_at=observed_at)
                if not granted.is_success:
                    return Outcome(failure=granted.failure)
            return Outcome.success(ObjectAccessDecision(tier, self.revision, identity.policy_revision))

    def authorize(
        self, actor: ResolvedIdentity, *, scope: ScopeRef, action: str, owner: SubjectRef,
        object_key: str | None = None, observed_at: datetime,
    ) -> Outcome[ObjectAccessDecision]:
        observed_at = normalize_utc(observed_at, field="observed_at")
        with self.runtime.read_snapshot():
            feature = self.feature_access(actor, scope=scope, action=action, observed_at=observed_at)
            if not feature.is_success:
                return feature
            if owner.installation_id != self.runtime.installation_id:
                return Outcome.failed("scope_installation_mismatch", "object owner belongs to another installation")
            decision = feature.unwrap()
            definition = self.registry.get(action)
            if object_key is not None:
                record = self.runtime.read_one("SELECT * FROM access_objects WHERE object_key = ?", (object_key,))
                if record is None:
                    return Outcome.failed("object_not_registered", "object has no trusted ownership registration")
                if (record["owner_kind"] != owner.kind.value or record["owner_id"] != owner.subject_id
                        or tuple(record[column] for column in (
                            "installation_id", "platform", "scope_kind", "native_scope_id",
                            "bot_id", "account_id", "conversation_id", "session_id",
                        )) != scope_values(scope)
                        or record["domain"] not in definition.domains):
                    return Outcome.failed("object_scope_mismatch", "object does not belong to the stated owner, scope, and domain")
            if decision.tier is AuthorizationTier.CORE or owner == actor.subject:
                return feature
            if object_key is None:
                return Outcome.failed("object_access_denied", "another owner's container requires core membership")
            share = self.runtime.read_one(
                """SELECT * FROM object_access_shares WHERE object_key = ? AND subject_kind = ?
                   AND subject_id = ? AND action = ?""",
                (object_key, actor.subject.kind.value, actor.subject.subject_id, action),
            )
            if share is None:
                return Outcome.failed("object_access_denied", "object action is not shared with this subject")
            if share["state"] != "active":
                return Outcome.failed("object_share_revoked", "object action share was revoked")
            if observed_at < decode_timestamp(share["valid_from"]):
                return Outcome.failed("object_share_not_active", "object action share is not active yet")
            if share["expires_at"] is not None and observed_at >= decode_timestamp(share["expires_at"]):
                return Outcome.failed("object_share_expired", "object action share has expired")
            return feature

    def scope_access(
        self, actor: ResolvedIdentity, *, scope: ScopeRef, action: str, observed_at: datetime,
        owner: SubjectRef | None = None, core_only: bool = False,
        allow_private_owner: bool = False,
    ) -> Outcome[ObjectAccessDecision]:
        """Scope containers: core, a verified owner, or an exact explicit delegation.

        The domain supplies ownership from its registry, never from the caller's
        selected target. Feature opening alone gives IMPORTANT no other container.
        """
        with self.runtime.read_snapshot():
            feature = self.feature_access(actor, scope=scope, action=action, observed_at=observed_at,
                                          allow_private_owner=allow_private_owner)
            if feature.failure is not None:
                return feature
            if owner is not None and owner.installation_id != self.runtime.installation_id:
                return Outcome.failed("scope_installation_mismatch", "container owner belongs to another installation")
            if feature.unwrap().tier is AuthorizationTier.CORE:
                return feature
            if core_only:
                return Outcome.failed("core_access_required", "this configuration group requires core membership")
            # STANDARD already proved this exact action/scope in feature_access.
            if owner == actor.subject or feature.unwrap().tier is AuthorizationTier.STANDARD:
                return feature
            if allow_private_owner:
                route = PersonaDirectory(self.runtime).route(scope)
                if route is not None and route.scene_kind is ScopeKind.PRIVATE and route.user == actor.subject:
                    return feature
            delegated = self.identity.authorize(actor, scope=scope, capability=action, observed_at=observed_at)
            return feature if delegated.is_success else Outcome(failure=delegated.failure)

    def permitted_object_keys(
        self, actor: ResolvedIdentity, *, scope: ScopeRef, action: str, observed_at: datetime,
        owner: SubjectRef | None = None,
    ) -> Outcome[tuple[str, ...]]:
        """Return exact currently visible registered objects for an object action.

        List entry points must first check their list feature, then use a read action
        here. A read share never opens creation or the owner's entire container.
        """
        observed_at = normalize_utc(observed_at, field="observed_at")
        with self.runtime.read_snapshot():
            feature = self.feature_access(actor, scope=scope, action=action, observed_at=observed_at)
            if not feature.is_success:
                return Outcome(failure=feature.failure)
            if owner is not None and owner.installation_id != self.runtime.installation_id:
                return Outcome.failed("scope_installation_mismatch", "object owner belongs to another installation")
            definition = self.registry.get(action)
            parameters = (*scope_values(scope), *definition.domains)
            query = """SELECT object_key FROM access_objects WHERE installation_id = ? AND platform = ?
                       AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ? AND domain IN ("""
            query += ",".join("?" for _ in definition.domains) + ")"
            if owner is not None:
                query += " AND owner_kind = ? AND owner_id = ?"
                parameters += (owner.kind.value, owner.subject_id)
            if feature.unwrap().tier is not AuthorizationTier.CORE:
                query += """ AND ((owner_kind = ? AND owner_id = ?) OR object_key IN (
                    SELECT object_key FROM object_access_shares WHERE subject_kind = ? AND subject_id = ?
                    AND action = ? AND state = 'active' AND valid_from <= ?
                    AND (expires_at IS NULL OR expires_at > ?)))"""
                timestamp = observed_at.isoformat()
                parameters += (actor.subject.kind.value, actor.subject.subject_id,
                               actor.subject.kind.value, actor.subject.subject_id, action, timestamp, timestamp)
            query += " ORDER BY object_key"
            return Outcome.success(tuple(row["object_key"] for row in self.runtime.read_all(query, parameters)))
