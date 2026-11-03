"""Dashboard managers read a private role without the participant's reply grant."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.identity_policy.private_domain import PrivateDomainAccess


NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


class _Tasks:
    def __init__(self):
        self.context_checks = []
        self.binding_checks = 0
        self.identity_checks = []
        self.operator_checks = []
        self.policy = SimpleNamespace(scope_access=self._scope_access)

    def now(self):
        return NOW

    def check_context_access(self, context, *, action, observed_at):
        self.context_checks.append(action)
        return Outcome.success(True)

    def check_context_binding(self, context, *, observed_at):
        self.binding_checks += 1
        return Outcome.success(True)

    def _scope_access(self, operator, *, scope, action, observed_at, core_only):
        self.operator_checks.append((action, core_only))
        return Outcome.success(SimpleNamespace(policy_revision=1))

    @property
    def identity(self):
        return SimpleNamespace(authorize=self._authorize)

    def _authorize(self, actor, *, scope, capability, observed_at):
        self.identity_checks.append(capability)
        return Outcome.success(True)


def _context():
    participant = SimpleNamespace(kind=SubjectKind.HUMAN, subject_id="onebot-user:1")
    scope = SimpleNamespace(kind=ScopeKind.PRIVATE, installation_id="i", bot_id="b")
    return SimpleNamespace(scope=scope, actor=SimpleNamespace(subject=participant),
                           audience=SimpleNamespace(recipients=(participant,)))


class PrivateDomainManagerTests(unittest.TestCase):
    def _access(self, tasks):
        access = PrivateDomainAccess.__new__(PrivateDomainAccess)
        access.tasks = tasks
        return access

    def test_manager_does_not_need_the_participants_conversation_reply_grant(self):
        tasks = _Tasks()
        self._access(tasks).check(_context(), "calendar.read", operator=SimpleNamespace(subject="dashboard"))
        self.assertEqual(tasks.context_checks, [])
        self.assertEqual(tasks.binding_checks, 1)
        self.assertEqual(tasks.identity_checks, [])
        self.assertEqual(tasks.operator_checks, [("role.life.manage", True)])

    def test_chat_still_requires_the_context_action_and_grant(self):
        tasks = _Tasks()
        self._access(tasks).check(_context(), "calendar.read")
        self.assertEqual(tasks.context_checks, ["calendar.read"])
        self.assertEqual(tasks.identity_checks, ["calendar.read"])
        self.assertEqual(tasks.operator_checks, [])


if __name__ == "__main__":
    unittest.main()
