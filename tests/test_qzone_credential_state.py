"""The page's credential row must describe the session as it is *now*.

The host route used to capture the transport's credential state when the route
started and hand that copy to ``QzoneAccess``. ``POST /refresh-credentials``
then acquired a session and returned the same access object, so its successful
response always carried the conclusion from *before* the acquisition -- the
first click on the page's refresh button could never show ``ready``.

These tests drive the real host route against a transport that re-binds its
state on every acquisition, which makes a construction-time copy observable as
a stale answer. They also pin the other half of the defect: a process that has
never needed a credential has simply not looked yet (``unknown``), not failed.
"""

from __future__ import annotations

import asyncio
import atexit
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

# The host modules resolve an AstrBot data root at import time; isolate it.
_previous_astrbot_root = os.environ.get("ASTRBOT_ROOT")
_astrbot_test_root = TemporaryDirectory(prefix="companion-v2-qzone-credential-")


def _cleanup_astrbot_test_root():
    _astrbot_test_root.cleanup()
    if _previous_astrbot_root is None:
        os.environ.pop("ASTRBOT_ROOT", None)
    else:
        os.environ["ASTRBOT_ROOT"] = _previous_astrbot_root


os.environ["ASTRBOT_ROOT"] = _astrbot_test_root.name
atexit.register(_cleanup_astrbot_test_root)

from astrbot_host.qzone_api import QzoneWebAPI
from companion_v2.capabilities.qzone_access import QzoneAccess
from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef)
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier, RoleBinding
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import (ConversationRoute, PersonaBinding, PersonaDirectory,
                                                   PersonaSelector, SelectorKind)
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.qzone import protocol as P
from companion_v2.qzone.service import QzoneService
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
INSTALLATION = "installation-qzone-credential"
COOKIE = "uin=o123456789; p_skey=SECRET"
UNTOUCHED = {"source": "none", "state": "unknown", "uin_masked": "", "reason": ""}
ACQUIRED = {"source": "runtime", "state": "ready", "uin_masked": "1******89", "reason": ""}


class CredentialTransport:
    """A transport whose conclusion is re-bound when a session is acquired.

    Nothing here touches the network: ``credential`` is the acquisition the host
    route asks for, and ``last_credential_state`` is the page-facing copy.
    """

    def __init__(self, *, fails=False):
        self.fails = fails
        self.acquired = 0
        self.sent = []
        self._state = dict(UNTOUCHED)

    @property
    def last_credential_state(self):
        return dict(self._state)

    async def credential(self, *, refresh=False):
        self.acquired += 1
        if self.fails:
            self._state = {"source": "none", "state": "failed", "uin_masked": "",
                           "reason": "没有可用的 OneBot 连接"}
            raise TaskError("qzone_credential_missing", "没有可用的 OneBot 连接")
        self._state = dict(ACQUIRED)
        return P.credential_from_cookies(P.parse_cookie_text(COOKIE), source="runtime")

    async def send(self, request, *, timeout):
        raise AssertionError("reading the credential status must not touch the network")

    async def qzonetoken(self, credential):
        return credential.qzonetoken


class CredentialSettings:
    def __init__(self):
        self.values = {"enabled": True}

    def read(self, context, at):
        return dict(self.values)


class QzoneCredentialStatusTests(unittest.IsolatedAsyncioTestCase):
    """Refresh and status through the host route, not a hand-rolled snapshot."""

    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / f"companion-v2-qzone-credential-{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = StorageRuntime.open(data_root=self.root / "v2", installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", bot_id="bot-a",
                              account_id="account-a", conversation_id="private-a", session_id="private-a")
        self.identities = PersistentIdentityDirectory(self.runtime)
        self.policy = ObjectAccessPolicy(self.runtime)
        target = self.person("target", AuthorizationTier.IMPORTANT)
        self.operator = self.person("operator", AuthorizationTier.CORE)
        self.role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-a")
        personas = PersonaDirectory(self.runtime)
        personas.register_route(ConversationRoute(self.scope, user=target.subject), observed_at=NOW)
        personas.set_binding(PersonaBinding("base", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-a"),
                                            self.role, 0, NOW, "fixture", "admin"),
                             expected_revision=0, observed_at=NOW)
        self.context = TaskContext(target, RoleBinding(self.scope, self.role, 1),
                                   AudienceRef(self.scope, (target.subject,)))
        self.transport = CredentialTransport()
        self.service = QzoneService(self.runtime, CredentialSettings(), self.transport, now=lambda: NOW,
                                    verify_delays=(0.0,), publish_verify_delays=(0.0,))
        self.application = SimpleNamespace(runtime=self.runtime, tasks=SimpleNamespace(
            authorize_context=lambda context, action=None, observed_at=None: Outcome.success(True)),
            qzone=self.service)
        self.controller = SimpleNamespace(
            _control_lock=asyncio.Lock(), _terminated=False, runtime=self.runtime, now=lambda: NOW,
            session=SimpleNamespace(profile=SimpleNamespace(target=self.context), application=self.application),
            last_session=None)
        self.api = QzoneWebAPI(self.controller)

    def person(self, name, tier):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "onebot", name, "bot-a", "login-a")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, name)
        self.identities.bind(binding_id=name, principal=principal, subject=subject, valid_from=NOW,
                             expected_revision=self.identities.revision)
        self.policy.set_tier(subject, tier, expected_revision=self.policy.revision, observed_at=NOW)
        return self.identities.resolve_authenticated(principal, observed_at=NOW).unwrap()

    async def call(self, name):
        request = SimpleNamespace(username="administrator")
        with patch("astrbot_host.qzone_api.request", request), \
             patch("astrbot_host.qzone_api.dashboard_operator", return_value=self.operator):
            return await getattr(self.api, name)()

    async def test_status_before_any_acquisition_reports_unknown_not_failed(self):
        response = await self.call("status")

        self.assertEqual(response["status"], "ok", response)
        credential = response["data"]["credential"]
        self.assertEqual(credential, UNTOUCHED)
        self.assertEqual(credential["state"], "unknown")
        self.assertNotEqual(credential["state"], "failed")
        self.assertEqual(self.transport.acquired, 0, "status 不应该探测凭据")
        self.assertNotIn("credential", response["data"]["runtime"],
                         "凭据行只在顶层出现一次，不在运行状态里重复")

    async def test_refresh_answers_with_the_state_it_just_acquired(self):
        response = await self.call("refresh")

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(self.transport.acquired, 1)
        self.assertEqual(response["data"]["credential"], ACQUIRED)
        self.assertEqual(response["data"]["credential"]["state"], "ready")
        self.assertEqual(response["data"]["credential"]["source"], "runtime")

    async def test_status_after_acquisition_reports_ready(self):
        await self.call("refresh")
        response = await self.call("status")

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["credential"], ACQUIRED)
        self.assertEqual(self.transport.acquired, 1)

    async def test_a_failed_refresh_is_reported_as_a_failure_and_marks_the_source(self):
        self.transport.fails = True

        response = await self.call("refresh")

        self.assertEqual(response["status"], "error", response)
        self.assertEqual(response["message"], "qzone_credential_missing")
        self.assertEqual(self.transport.last_credential_state["state"], "failed")
        self.assertEqual(self.transport.last_credential_state["source"], "none")

    def test_a_caller_supplied_getter_is_still_read_at_status_time(self):
        """Callers without a service keep the older constructor contract."""
        access = QzoneAccess(self.application, self.context,
                             credential_state=lambda: dict(ACQUIRED))

        status = access.status(self.operator, NOW)

        self.assertEqual(status["credential"], ACQUIRED)


if __name__ == "__main__":
    unittest.main()
