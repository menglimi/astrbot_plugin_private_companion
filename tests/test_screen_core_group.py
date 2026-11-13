"""Core administrators may capture the desktop from a group, observation only."""

from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.native_extensions.screens import ScreenAdopter
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AssetRef, ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef, VersionRef,
)
from companion_v2.screen.models import ScreenCaptureRef
from tests.g6_support import INSTALLATION, NOW


GROUP_SCOPE = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "group-a",
                       "bot-a", "login-a", "conversation-a", "session-a")
ROLE = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
CORE = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "core-a")
RESOLVED = SimpleNamespace(principal=object(), subject=CORE, binding_id="binding-a",
                           binding_revision=1, policy_revision=1)


def reference():
    return ScreenCaptureRef(
        "capture-core-group",
        SourceRef("source-core-group", "screen", GROUP_SCOPE, VersionRef("line-core-group", 1),
                  NOW - timedelta(seconds=1), NOW + timedelta(hours=1)),
        AssetRef("asset-core-group", "screen", GROUP_SCOPE, VersionRef("asset-core-group", 1),
                 "0" * 64),
    )


class ScreenCoreGroupAdmissionTests(unittest.TestCase):
    def test_core_admin_group_capture_skips_group_consent_and_checks_binding(self):
        from companion_v2.screen.repositories import ScreenRepository

        repository = ScreenRepository.__new__(ScreenRepository)
        repository._runtime = object()
        repository._capabilities = SimpleNamespace(
            require_role_enabled=lambda **_: Outcome.success(SimpleNamespace(revision=7)),
            installation=lambda **_: Outcome.success(SimpleNamespace(revision=3)),
            consent=lambda **_: Outcome.failed("capability_consent_missing", "no window consent"),
        )
        repository._authorizer = SimpleNamespace(
            resolve_authenticated=lambda principal, *, observed_at: Outcome.success(RESOLVED),
            authorize=lambda *args, **kwargs: Outcome.failed("authorization_denied", "no window grant"),
        )
        with patch("companion_v2.screen.repositories.is_core", return_value=True):
            admitted = repository._admission(reference(), ROLE, RESOLVED, NOW)
        self.assertTrue(admitted.is_success)

    def test_non_core_group_capture_still_needs_its_own_consent(self):
        from companion_v2.screen.repositories import ScreenRepository

        repository = ScreenRepository.__new__(ScreenRepository)
        repository._runtime = object()
        repository._capabilities = SimpleNamespace(
            require_role_enabled=lambda **_: Outcome.success(SimpleNamespace(revision=7)),
            authorize=lambda **_: Outcome.failed("capability_not_authorized", "no group consent"),
        )
        repository._authorizer = SimpleNamespace()
        with patch("companion_v2.screen.repositories.is_core", return_value=False):
            admitted = repository._admission(reference(), ROLE, RESOLVED, NOW)
        self.assertFalse(admitted.is_success)
        self.assertEqual(admitted.failure.code, "capability_not_authorized")

    def test_preflight_allows_only_a_core_administrator_in_a_group(self):
        adopter = ScreenAdopter.__new__(ScreenAdopter)
        adopter.application = SimpleNamespace(runtime=object())
        adopter.registry = SimpleNamespace(now=lambda: NOW)
        adopter.screens = SimpleNamespace(
            admit_capture=lambda *args, **kwargs: Outcome.success("admission-stamp"))
        invocation = SimpleNamespace(
            invocation_id="invocation-core-group",
            context=SimpleNamespace(scope=GROUP_SCOPE, role_binding=SimpleNamespace(role=ROLE),
                                    actor=SimpleNamespace(subject=CORE)),
        )
        with patch("astrbot_host.native_extensions.screens.is_core", return_value=True):
            adopter.preflight(invocation)
        with patch("astrbot_host.native_extensions.screens.is_core", return_value=False):
            with self.assertRaises(TaskError) as caught:
                adopter.preflight(invocation)
        self.assertEqual(caught.exception.code, "screen_scope_denied")


if __name__ == "__main__":
    unittest.main()
