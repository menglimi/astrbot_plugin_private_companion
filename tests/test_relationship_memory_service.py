from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion_v2.foundation.refs import (
    AudienceRef,
    ScopeKind,
    ScopeRef,
    SourceRef,
    SubjectKind,
    SubjectRef,
    VersionRef,
)
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaDirectory, SelectorKind
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.relationships import (
    RelationshipMemory,
    RelationshipMemoryKind,
    RelationshipMemoryService,
    RelationshipMemoryState as State,
)
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc)
INSTALLATION = "relationship-service-installation"
HUMAN = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "human-a")
OTHER_HUMAN = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "human-b")
ROLE = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-a")
OTHER_ROLE = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b")
SCOPE = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", "bot-a", "account-a", "conversation-a", "session-a")
PRINCIPAL = AuthenticatedHostPrincipal(INSTALLATION, "host-a", "onebot", "native-human-a", "bot-a", "account-a")
OTHER_PRINCIPAL = AuthenticatedHostPrincipal(INSTALLATION, "host-a", "onebot", "native-human-b", "bot-a", "account-a")
ACTIONS = tuple(f"relationship.memory.{action}" for action in ("read", "propose", "revise", "review", "adopt", "retract"))


class RelationshipMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-v2-relationship-service-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name) / "v2-data", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.current_time = NOW
        self.identity = PersistentIdentityDirectory(self.runtime)
        self.personas = PersonaDirectory(self.runtime)
        self.access = ObjectAccessPolicy(self.runtime)
        self.sources = SourceRepository(self.runtime)
        self.service = RelationshipMemoryService(self.runtime, now=lambda: self.current_time)
        self.identity.bind(binding_id="identity-a", principal=PRINCIPAL, subject=HUMAN,
            valid_from=NOW - timedelta(minutes=1), expected_revision=self.identity.revision)
        self.actor = self.identity.resolve_authenticated(PRINCIPAL, observed_at=NOW).unwrap()
        self.route = ConversationRoute(SCOPE, user=HUMAN)
        self.personas.register_route(self.route, observed_at=NOW)
        selector = next(item for item, _ in self.route.selectors() if item.kind is SelectorKind.SESSION)
        self.persona_binding = PersonaBinding("persona-binding-a", selector, ROLE, 0,
            NOW - timedelta(minutes=1), "test-fixture", "test-authority")
        self.personas.set_binding(self.persona_binding, expected_revision=0, observed_at=NOW)
        for action in ACTIONS:
            self.grant(action)

    def grant(self, action, *, subject=HUMAN, principal=PRINCIPAL, scope=SCOPE):
        self.identity.issue_grant(grant_id=f"grant:{subject.subject_id}:{action}", subject=subject,
            scope=scope, capability=action, tier=AuthorizationTier.STANDARD, valid_from=NOW - timedelta(minutes=1),
            expected_revision=self.identity.revision)

    def publish_source(self, source_id="source-a", *, audience=(HUMAN,), expires_at=None):
        reference = SourceRef(source_id, "conversation", SCOPE, VersionRef(source_id, 1),
            NOW - timedelta(minutes=1), expires_at)
        record = SourceRecord(reference, AudienceRef(SCOPE, tuple(audience)), f"hash:{source_id}", "source payload")
        self.sources.publish(record, observed_at=NOW).unwrap()
        return reference

    def proposal(self, source=None, memory_id="memory-a"):
        source = source or self.publish_source()
        return RelationshipMemory(memory_id, self.owner(), HUMAN, SCOPE, RelationshipMemoryKind.CONTEXT,
            "我们近来开始自在地聊一些生活小事。", (source,), NOW, NOW)

    @staticmethod
    def owner():
        from companion_v2.foundation.refs import RoleOwnerRef
        return RoleOwnerRef(INSTALLATION, SCOPE.bot_id, ROLE.subject_id)

    def test_review_adoption_revision_and_retraction_record_the_real_reviewer(self):
        proposed = self.service.propose(self.proposal(), actor=self.actor, observed_at=NOW).unwrap()
        reviewed = self.service.transition("memory-a", actor=self.actor, scope=SCOPE,
            expected_revision=proposed.revision, operation_id="review-a", state=State.REVIEWED,
            observed_at=NOW + timedelta(seconds=1)).unwrap()
        self.assertEqual(reviewed.reviewed_by, HUMAN)
        adopted = self.service.transition("memory-a", actor=self.actor, scope=SCOPE,
            expected_revision=reviewed.revision, operation_id="adopt-a", state=State.ADOPTED,
            observed_at=NOW + timedelta(seconds=2)).unwrap()
        self.assertEqual(adopted.adopted_by, HUMAN)
        self.assertEqual(self.service.read("memory-a", actor=self.actor, scope=SCOPE,
            observed_at=NOW + timedelta(seconds=3)).unwrap(), adopted)
        self.assertEqual(self.service.for_relationship(actor=self.actor, scope=SCOPE,
            state=State.ADOPTED, observed_at=NOW + timedelta(seconds=3)).unwrap(), (adopted,))
        self.assertEqual(self.service.adopted_for_relationship(actor=self.actor, scope=SCOPE,
            observed_at=NOW + timedelta(seconds=3)).unwrap(), (adopted,))

        revised = self.service.revise("memory-a", actor=self.actor, scope=SCOPE,
            expected_revision=adopted.revision, operation_id="revise-a",
            interpretation="更正：我们最近才开始聊这些。", sources=adopted.sources,
            observed_at=NOW + timedelta(seconds=4)).unwrap()
        self.assertEqual(revised.state, State.PROPOSED)
        self.assertIsNone(revised.reviewed_by)
        self.assertIsNone(revised.adopted_by)
        retracted = self.service.transition("memory-a", actor=self.actor, scope=SCOPE,
            expected_revision=revised.revision, operation_id="retract-a", state=State.RETRACTED,
            observed_at=NOW + timedelta(seconds=5)).unwrap()
        self.assertEqual(retracted.retracted_by, HUMAN)

    def test_reads_and_adoption_recheck_source_governance_and_audience(self):
        source = self.publish_source("revoked-source")
        proposed = self.service.propose(self.proposal(source, "revoked-memory"), actor=self.actor).unwrap()
        self.sources.revoke(source, expected_governance_revision=1, observed_at=NOW + timedelta(seconds=1)).unwrap()
        self.assertEqual(self.service.read("revoked-memory", actor=self.actor, scope=SCOPE,
            observed_at=NOW + timedelta(seconds=2)).failure.code, "source_revoked")
        self.assertEqual(self.service.transition("revoked-memory", actor=self.actor, scope=SCOPE,
            expected_revision=proposed.revision, operation_id="review-revoked", state=State.REVIEWED,
            observed_at=NOW + timedelta(seconds=2)).failure.code, "source_revoked")
        retracted = self.service.transition("revoked-memory", actor=self.actor, scope=SCOPE,
            expected_revision=proposed.revision, operation_id="retract-revoked", state=State.RETRACTED,
            observed_at=NOW + timedelta(seconds=2)).unwrap()
        self.assertEqual(retracted.retracted_by, HUMAN)

        hidden = self.publish_source("private-to-other", audience=(OTHER_HUMAN,))
        outcome = self.service.propose(self.proposal(hidden, "hidden-memory"), actor=self.actor)
        self.assertEqual(outcome.failure.code, "source_audience_denied")

    def test_expiry_and_current_action_revocation_are_rechecked(self):
        source = self.publish_source("short-source", expires_at=NOW + timedelta(seconds=2))
        proposed = self.service.propose(self.proposal(source, "expiring-memory"), actor=self.actor).unwrap()
        self.current_time = NOW + timedelta(seconds=2)
        self.assertEqual(self.service.read("expiring-memory", actor=self.actor, scope=SCOPE).failure.code,
            "source_expired")
        self.assertEqual(self.service.transition("expiring-memory", actor=self.actor, scope=SCOPE,
            expected_revision=proposed.revision, operation_id="review-expired", state=State.REVIEWED).failure.code,
            "source_expired")

        self.current_time = NOW + timedelta(seconds=3)
        self.identity.revoke_grant(f"grant:{HUMAN.subject_id}:relationship.memory.read",
            revoked_at=self.current_time, expected_revision=self.identity.revision)
        self.assertEqual(self.service.for_relationship(actor=self.actor, scope=SCOPE).failure.code,
                         "source_expired")

    def test_private_owner_reads_without_read_grant_but_cannot_borrow_another_route(self):
        self.identity.revoke_grant(f"grant:{HUMAN.subject_id}:relationship.memory.read",
            revoked_at=NOW, expected_revision=self.identity.revision)
        self.assertTrue(self.service.for_relationship(actor=self.actor, scope=SCOPE).is_success)
        self.identity.bind(binding_id="identity-b", principal=OTHER_PRINCIPAL, subject=OTHER_HUMAN,
            valid_from=NOW - timedelta(minutes=1), expected_revision=self.identity.revision)
        other = self.identity.resolve_authenticated(OTHER_PRINCIPAL, observed_at=NOW).unwrap()
        self.assertFalse(self.service.for_relationship(actor=other, scope=SCOPE).is_success)
        self.assertFalse(self.service.propose(self.proposal(), actor=other).is_success)

    def test_role_switch_and_another_private_participant_cannot_read_this_pair(self):
        proposed = self.service.propose(self.proposal(), actor=self.actor).unwrap()
        changed = replace(self.persona_binding, role=OTHER_ROLE, revision=self.persona_binding.revision + 1)
        self.personas.set_binding(changed, expected_revision=self.persona_binding.revision,
            observed_at=NOW + timedelta(seconds=1))
        self.assertEqual(self.service.read("memory-a", actor=self.actor, scope=SCOPE,
            observed_at=NOW + timedelta(seconds=1)).failure.code, "relationship_memory_not_found")
        self.assertEqual(proposed.owner.persona_id, ROLE.subject_id)

        second_scope = replace(SCOPE, native_scope_id="private-b", conversation_id="conversation-b", session_id="session-b")
        second_route = ConversationRoute(second_scope, user=OTHER_HUMAN)
        self.personas.register_route(second_route, observed_at=NOW)
        selector = next(item for item, _ in second_route.selectors() if item.kind is SelectorKind.SESSION)
        self.personas.set_binding(PersonaBinding("persona-binding-b", selector, ROLE, 0,
            NOW - timedelta(minutes=1), "test-fixture", "test-authority"), expected_revision=0,
            observed_at=NOW + timedelta(seconds=2))
        self.identity.bind(binding_id="identity-b", principal=OTHER_PRINCIPAL, subject=OTHER_HUMAN,
            valid_from=NOW - timedelta(minutes=1), expected_revision=self.identity.revision)
        other_actor = self.identity.resolve_authenticated(OTHER_PRINCIPAL,
            observed_at=NOW + timedelta(seconds=2)).unwrap()
        for action in ACTIONS:
            self.grant(action, subject=OTHER_HUMAN, principal=OTHER_PRINCIPAL, scope=second_scope)
        self.assertEqual(self.service.read("memory-a", actor=other_actor, scope=second_scope,
            observed_at=NOW + timedelta(seconds=2)).failure.code, "relationship_memory_not_found")


if __name__ == "__main__":
    unittest.main()
