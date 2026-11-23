"""Ledgered person permissions and management tiers on the existing policy store."""

from __future__ import annotations

from datetime import datetime

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.identity_policy.grants import CONFIGURATION_GRANT_PREFIXES, PERSON_PERMISSION_GRANT_PREFIX
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.relationships.private_access import REVOCABLE_CAPABILITIES
from companion_v2.identity_policy.interventions import INTERVENTION_CAPABILITIES


# This page's lineage. Its presence is what makes a capability retractable here.
GRANT_PREFIX = PERSON_PERMISSION_GRANT_PREFIX
OPERATION_OWNER = "identity.authorization.manage"
# Private delivery is host-managed; dialogue permission defines a companion member.
COMPANION_CAPABILITIES = ("conversation.reply",)
# The capabilities this page can always reach, even when the person holds no grant
# for them: an operator has to be able to switch one back on without first
# writing a row for it somewhere else.
PAGE_MANAGED_CAPABILITIES = ("conversation.reply", "proactive.contact", *INTERVENTION_CAPABILITIES)
MAX_OPERATION_KEY = 128
MANAGED_BY_PAGE = "page"
MANAGED_BY_CONFIGURATION = "configuration"
# Page requests name the registered route; permission writes use its exact scope.
PERSON_ROUTE_KEYS = ("platform", "bot_id", "account_id", "role_id")


class PersonAuthorizationManagement:
    """Authorization rows for one subject, and the ledgered switch that changes them.

    ``participants`` is built by the host from the same authorized routes the
    acquaintance directory is built from, so a request can only name a person
    this operator was already allowed to see. Each entry carries the subject and
    the *resolved* window the host registered for them; nothing here mints an
    identity or invents a window, and an entry without one is reported as such
    rather than guessed at.
    """

    def __init__(self, runtime, *, participants, configuration_grant_prefixes=CONFIGURATION_GRANT_PREFIXES):
        self.runtime = runtime
        self.prefixes = tuple(configuration_grant_prefixes)
        self.policy = ObjectAccessPolicy(runtime)
        self.identity = PersistentIdentityDirectory(runtime)
        self.operations = OperationRepository(runtime)
        self.subjects, self.participants = {}, []
        for entry in participants:
            subject, scope = entry.get("subject"), entry.get("scope")
            if subject is None or subject.installation_id != runtime.installation_id:
                continue
            # A known person is remembered even without a window: that is what lets
            # the refusal say "no window yet" instead of "not in the directory".
            self.subjects.setdefault(subject.subject_id, subject)
            if scope is not None and scope.installation_id == runtime.installation_id and scope.is_resolved:
                self.participants.append(entry)

    def _authorize(self, operator, at: datetime) -> None:
        # Same gate as the directory itself: an operator who may not read the
        # acquaintance directory may not read or change anyone's authorization.
        require(self.policy.feature_access(operator,
            scope=ScopeRef(self.runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
            action="memory.manage", observed_at=at))

    def _person(self, subject_id, route=None):
        if not isinstance(subject_id, str) or not subject_id:
            raise TaskError("acquaintance_subject_required", "name one person from the acquaintance directory")
        subject = self.subjects.get(subject_id)
        if subject is None:
            raise TaskError("acquaintance_subject_unknown", "this person is not in the authorized acquaintance directory")
        candidates = [entry for entry in self.participants if entry["subject"] == subject]
        if route is not None:
            if (type(route) is not dict or set(route) != set(PERSON_ROUTE_KEYS)
                    or any(type(value) is not str or not value for value in route.values())):
                raise TaskError("acquaintance_route_invalid", "select one complete Bot, account and role route")
            candidates = [entry for entry in candidates if
                (entry["scope"].platform, entry["scope"].bot_id, entry["scope"].account_id,
                 entry.get("role_id")) == tuple(route[key] for key in PERSON_ROUTE_KEYS)]
            if not candidates:
                raise TaskError("acquaintance_route_unknown", "this person is not registered on the selected route")
        scopes = {entry["scope"] for entry in candidates}
        if len(scopes) > 1:
            raise TaskError("acquaintance_route_required", "select this person's Bot, account and role")
        scope = next(iter(scopes), None)
        if scope is None:
            # Refusing beats inventing a window: a grant written against a guessed
            # scope would not be the one the reply path checks.
            raise TaskError("acquaintance_window_unknown",
                            "this person has no registered private window yet, so there is nothing to authorize")
        return subject, scope

    def _classification(self, grant):
        if grant.capability in REVOCABLE_CAPABILITIES:
            return MANAGED_BY_PAGE
        for prefix in self.prefixes:
            if grant.grant_id.startswith(prefix):
                return MANAGED_BY_CONFIGURATION
        return MANAGED_BY_PAGE

    @staticmethod
    def _state(grant, at: datetime) -> str:
        """The real state of the newest record, not the one the page wishes for."""
        if grant.state is GrantState.REVOKED:
            return "revoked"
        if grant.expires_at is not None and at >= grant.expires_at:
            return "expired"
        if at < grant.valid_from:
            return "pending"
        return "active"

    def _relevant(self, subject, scope=None):
        """This person's own current rows.

        The installation check keeps a foreign row out of the projection: subject
        and scope ids are only unique inside one installation. The scope check is
        what makes the row and the write control tell the same story -- a grant
        kept for a window this person does not use would render a switch that the
        write path cannot reach.
        """
        installation = self.runtime.installation_id
        return [grant for grant in self.identity.snapshot().grants
                if grant.subject == subject and grant.scope.installation_id == installation
                and (scope is None or self._same_window(grant.scope, scope))]

    @staticmethod
    def _same_window(grant_scope, scope) -> bool:
        return grant_scope == scope

    def _row(self, capability, grants, at: datetime):
        """One row per capability, spoken from the newest record of that capability.

        The snapshot's own order is **not** usable here: a revoked grant is kept as
        evidence and its id has nothing to do with when it was written, so reading
        the last row would report "revoked" for a capability that has since been
        switched back on. The newest record decides, which is also what the
        authorization rule does -- ``authorize`` only needs *one* active grant.
        """
        active = [item for item in grants if self._state(item, at) == "active"]
        grant = max(active or grants, key=lambda item: (item.valid_from, item.grant_id))
        derived = self._classification(grant) == MANAGED_BY_CONFIGURATION
        return {"capability": capability, "state": self._state(grant, at),
                "managed_by": MANAGED_BY_CONFIGURATION if derived else MANAGED_BY_PAGE,
                "revocable": not derived,
                "tier": grant.tier.value, "expires_at": grant.expires_at, "valid_from": grant.valid_from,
                "grant_id": grant.grant_id, "grant_revision": grant.revision,
                "scope": grant.scope.document(), "policy_revision": self.identity.revision}

    def _status(self, subject, at: datetime, *, scope=None):
        grants = self._relevant(subject, scope)
        by_capability = {}
        for grant in grants:
            by_capability.setdefault(grant.capability, []).append(grant)
        # Missing permissions remain configurable, with their actual absence explicit.
        for capability in PAGE_MANAGED_CAPABILITIES:
            by_capability.setdefault(capability, [])
        items = [self._row(capability, by_capability[capability], at) if by_capability[capability]
                 else {"capability": capability, "state": "off", "managed_by": MANAGED_BY_PAGE,
                       "revocable": True, "tier": None, "expires_at": None, "valid_from": None,
                       "grant_id": None, "grant_revision": 0, "scope": scope.document() if scope else None,
                       "policy_revision": self.identity.revision}
                 for capability in sorted(by_capability)]
        active = {item["capability"] for item in items if item["state"] == "active"}
        row = self.runtime.read_one("SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                                    (subject.kind.value, subject.subject_id))
        tier = row["tier"] if row else AuthorizationTier.STANDARD.value
        for item in items:
            core_intervention = tier == AuthorizationTier.CORE.value and item["capability"] in INTERVENTION_CAPABILITIES
            explicit = any(grant.capability == item["capability"]
                           and grant.grant_id.startswith(PERSON_PERMISSION_GRANT_PREFIX) for grant in grants)
            override = core_intervention or (tier != AuthorizationTier.STANDARD.value
                and item["capability"] in REVOCABLE_CAPABILITIES and not explicit)
            item["effective_state"] = "active" if override else item["state"]
            item["identity_override"] = override
        result = {"subject_id": subject.subject_id, "items": items,
                "identity_tier": tier, "identity_revision": self.policy.revision,
                # 管理员权限不是一条可单独签发的成员 grant，而是 CORE 身份的明确投影。
                # Dashboard 可以直接呈现这项权限，实际生效仍只由 set_tier /
                # ObjectAccessPolicy 决定，避免页面另造一套旁路授权。
                "administrator": {"enabled": tier == AuthorizationTier.CORE.value,
                                  "state": ("active" if tier == AuthorizationTier.CORE.value else "off"),
                                  "label": ("核心管理员" if tier == AuthorizationTier.CORE.value else "未开启")},
                "policy_revision": self.identity.revision,
                "companion": all(any(item["capability"] == name and item["effective_state"] == "active"
                                     for item in items) for name in COMPANION_CAPABILITIES),
                "counts": {state: sum(1 for item in items if item["state"] == state)
                           for state in ("active", "off", "revoked", "expired", "pending")}}
        from companion_v2.relationships.account_link_identity import project_status
        return project_status(self.runtime, subject, scope, at, result)

    @outcome
    def status(self, operator, subject_id, *, observed_at: datetime, route=None):
        self._authorize(operator, observed_at)
        subject, scope = self._person(subject_id, route)
        with self.runtime.read_snapshot():
            return self._status(subject, observed_at, scope=scope)

    def _retract(self, subject, scope, capability, at: datetime):
        """Revoke this person's active grant for one capability, in this window.

        It deliberately retracts *every* generation it finds rather than only the
        id it just minted: a re-issued capability must not leave two active grants
        behind, because ``authorize`` is satisfied by any one of them.
        """
        for grant in self._relevant(subject, scope):
            if (grant.capability == capability and self._same_window(grant.scope, scope)
                    and grant.state is GrantState.ACTIVE):
                self.identity.revoke_grant(grant.grant_id, revoked_at=at,
                                           expected_revision=self.identity.revision)

    @outcome
    def configure(self, operator, command, *, observed_at: datetime):
        """Turn one capability on or off as a single ledgered operation.

        Revoke-then-issue happens inside one transaction on purpose: a failure
        between the two halves would otherwise leave the capability revoked while
        the operator was told the save failed. The operation key is what makes a
        retry return the original result instead of a second grant.
        """
        if (type(command) is not dict
                or set(command) not in ({"operation_key", "subject_id", "capability", "enabled", "expected_revision"},
                                       {"operation_key", "subject_id", "route", "capability", "enabled", "expected_revision"})
                or type(command["operation_key"]) is not str or not command["operation_key"].strip()
                or len(command["operation_key"]) > MAX_OPERATION_KEY
                or type(command["enabled"]) is not bool
                or type(command["capability"]) is not str or not command["capability"]
                or len(command["capability"]) > 200
                or type(command["expected_revision"]) is not int or command["expected_revision"] < 0):
            raise TaskError("authorization_command_invalid",
                            "provide a person, a capability, a switch, the revision it was read at "
                            "and a stable operation key")
        self._authorize(operator, observed_at)
        subject, scope = self._person(command["subject_id"], command.get("route"))
        capability, enabled = command["capability"], command["enabled"]
        from companion_v2.relationships.account_link_identity import edit
        shared = edit(self.runtime, operator, subject, scope, command, at=observed_at)
        if shared is not None:
            return {**shared, "status": self._status(subject, observed_at, scope=scope)}
        # The idempotency key names the *decision* being taken (this person, this
        # capability, this click), never the value it is set to: that is what makes
        # reusing one key for the opposite change a conflict instead of a second
        # authority. The operation id does carry the value, so an inverted retry
        # cannot collide with the original operation's identifier.
        token = digest([self.runtime.installation_id, subject.subject_id, scope.document(), capability, command["operation_key"]])
        operation = OperationRef(operation_id=GRANT_PREFIX + digest([token, str(enabled)]),
                                 owner=OPERATION_OWNER, scope=scope,
                                 version=VersionRef(f"identity-authorization:{token}", 1),
                                 idempotency_key=f"identity-authorization:{token}")
        with self.runtime.transaction():
            receipt = require(self.operations.reserve(operation,
                parameter_hash=digest([encode(operator.principal), encode(operator.subject),
                    operator.binding_id, operator.binding_revision, subject.subject_id, scope.document(),
                    capability, enabled, command["operation_key"]]), observed_at=observed_at))
            if receipt.replayed:
                if receipt.state is not OperationState.COMPLETED:
                    raise TaskError("authorization_operation_unresolved",
                                    "the original authorization change is unresolved")
                return {"replayed": True, "operation_id": operation.operation_id,
                        "status": self._status(subject, observed_at, scope=scope)}
            self._apply(subject, scope, capability, enabled, observed_at,
                        expected_revision=command["expected_revision"], operation_id=operation.operation_id)
            require(self.operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                                               next_state=OperationState.COMPLETED, observed_at=observed_at))
            return {"replayed": False, "operation_id": operation.operation_id,
                    "status": self._status(subject, observed_at, scope=scope)}

    def _apply(self, subject, scope, capability, enabled, at: datetime, *, expected_revision, operation_id):
        grants = self._relevant(subject, scope)
        for grant in grants:
            if self._classification(grant) == MANAGED_BY_CONFIGURATION and grant.capability == capability:
                raise TaskError("authorization_capability_configured",
                                "this capability is issued from the profile configuration, "
                                "so it is changed there and not on this page")
        before = self.identity.revision
        # The revision the caller read is checked against the rows it was read from.
        # Without it two operators editing different capabilities of one person
        # would each overwrite the other's view of a page they both still show.
        if type(expected_revision) is not int or expected_revision != before:
            raise TaskError("authorization_revision_conflict",
                            "authorization changed since it was read; read it again before saving")
        if enabled:
            # Revoke-then-issue is one change, in one transaction: a failure between
            # the halves would otherwise revoke a capability while reporting failure.
            self._retract(subject, scope, capability, at)
            self.identity.issue_grant(grant_id=GRANT_PREFIX + digest([subject.subject_id, capability, operation_id]),
                subject=subject, scope=scope, capability=capability, tier=AuthorizationTier.STANDARD,
                valid_from=at, expected_revision=self.identity.revision)
        else:
            mine = [grant for grant in grants if grant.capability == capability
                    and self._same_window(grant.scope, scope) and grant.state is GrantState.ACTIVE
                    and self._classification(grant) == MANAGED_BY_PAGE]
            if not mine and capability in INTERVENTION_CAPABILITIES:
                return
            if not mine and capability not in REVOCABLE_CAPABILITIES:
                raise TaskError("authorization_capability_not_managed",
                                "this capability has no active grant this page owns")
            self._retract(subject, scope, capability, at)
            if capability in REVOCABLE_CAPABILITIES:
                identifier = GRANT_PREFIX + digest([subject.subject_id, capability, operation_id])
                self.identity.issue_grant(grant_id=identifier, subject=subject, scope=scope,
                    capability=capability, tier=AuthorizationTier.STANDARD, valid_from=at,
                    expected_revision=self.identity.revision)
                self.identity.revoke_grant(identifier, revoked_at=at, expected_revision=self.identity.revision)
        if self.identity.revision == before:
            # A mutation that moved nothing is a bug, not a save. Fail loudly rather
            # than report a change the next read will not show.
            raise TaskError("authorization_change_not_applied",
                            "the authorization change did not reach the grant record")

    @outcome
    def set_identity(self, operator, command, *, observed_at: datetime):
        self._authorize(operator, observed_at)
        if (type(command) is not dict or set(command) != {
                "operation_key", "subject_id", "route", "tier", "expected_revision"}
                or type(command["operation_key"]) is not str or not command["operation_key"].strip()
                or len(command["operation_key"]) > MAX_OPERATION_KEY
                or type(command["tier"]) is not str
                or command["tier"] not in {item.value for item in AuthorizationTier}
                or type(command["expected_revision"]) is not int):
            raise TaskError("identity_command_invalid", "provide a tier, current policy revision and stable operation key")
        subject, scope = self._person(command["subject_id"], command["route"])
        from companion_v2.relationships.account_link_identity import edit
        shared = edit(self.runtime, operator, subject, scope, command, at=observed_at, identity_tier=True)
        if shared is not None:
            return {**shared, "status": self._status(subject, observed_at, scope=scope)}
        token = digest([self.runtime.installation_id, subject, command["operation_key"]])
        op_id = "person-identity:" + token
        operation = OperationRef(op_id, OPERATION_OWNER, scope, VersionRef(op_id, 1), op_id)
        with self.runtime.transaction():
            receipt = require(self.operations.reserve(operation,
                parameter_hash=digest([operator.principal, operator.subject, operator.binding_id,
                    operator.binding_revision, subject, scope.document(), command["tier"]]), observed_at=observed_at))
            if receipt.replayed:
                if receipt.state is not OperationState.COMPLETED:
                    raise TaskError("authorization_operation_unresolved", "the identity change is unresolved")
            else:
                if command["expected_revision"] != self.policy.revision:
                    raise TaskError("identity_revision_conflict", "identity policy changed; read it again")
                self.policy.set_tier(subject, AuthorizationTier(command["tier"]),
                    expected_revision=self.policy.revision, observed_at=observed_at)
                require(self.operations.transition(op_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=observed_at))
            return {"operation_id": op_id, "replayed": receipt.replayed,
                    "status": self._status(subject, observed_at, scope=scope)}
