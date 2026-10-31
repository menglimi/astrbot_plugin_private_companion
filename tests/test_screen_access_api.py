"""The native Screen consent route controls one private target and nothing wider."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.screen_access_api import PREFIX
from companion_v2.capabilities import CapabilityAuthorizationState, CapabilityRepository
from companion_v2.execution.models import TaskContext
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.screen import SCREEN_EXTENSION, SCREEN_PROCESS, SCREEN_READ
from astrbot_host.management_identity import dashboard_operator
from tests import test_astrbot_staging_host as support


class ScreenAccessAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-screen-access-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.native = support.WebContext()
        # The host assembles its bundled Screen owner. An optional legacy star
        # is excluded by discovery and must not replace that provider in a test.
        self.native.get_all_stars = lambda: ()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                          NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), self.root, native_types=types,
            now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {(path, methods[0]): handler
                       for path, handler, methods, _ in self.native.registered_web_apis}

    async def call(self, *, body=None, username="admin", route=PREFIX, query=b""):
        method = "POST" if body is not None else "GET"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        request_route = route if method == "GET" else route + "/configure"
        native = Request({"type": "http", "method": method, "path": request_route,
                          "headers": [], "query_string": query}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[(request_route, method)]()

    async def start(self):
        self.assertTrue((await self.controller.initialize_profile()).is_success)
        self.assertTrue((await self.controller.start()).is_success)

    async def test_enable_revoke_and_replay_are_scoped_to_the_current_private_window(self):
        await self.start()
        initial = (await self.call())["data"]
        self.assertFalse(initial["installed"])
        self.assertFalse(initial["invokable"])
        self.assertTrue(initial["can_manage"])
        self.assertIsNone(initial["installed_package_version"])
        self.assertIsNone(initial["installed_artifact_hash"])
        self.assertIsNone(initial["installed_revision"])
        command = {"operation_key": "screen-enable-1", "edit_token": initial["edit_token"], "enabled": True}
        enabled = await self.call(body=command)
        self.assertEqual(enabled["status"], "ok", enabled)
        self.assertTrue(enabled["data"]["status"]["invokable"])
        self.assertTrue(enabled["data"]["status"]["consented"])
        self.assertFalse(enabled["data"]["status"]["upgrade_available"])
        self.assertEqual(enabled["data"]["status"]["installed_package_version"], initial["package_version"])
        self.assertEqual(enabled["data"]["status"]["installed_revision"], 1)
        replay = await self.call(body=command)
        self.assertTrue(replay["data"]["replayed"])
        repository = CapabilityRepository(self.controller.runtime)
        config = self.controller.configuration
        for capability in (SCREEN_READ, SCREEN_PROCESS):
            self.assertTrue(repository.authorize(extension_id=SCREEN_EXTENSION, capability_id=capability,
                subject=config.subject, scope=config.scope).is_success)
        current = (await self.call())["data"]
        revoked = await self.call(body={"operation_key": "screen-revoke-1",
            "edit_token": current["edit_token"], "enabled": False})
        self.assertFalse(revoked["data"]["status"]["invokable"])
        self.assertFalse(revoked["data"]["status"]["consented"])
        for capability in (SCREEN_READ, SCREEN_PROCESS):
            row = self.controller.runtime.read_one(
                "SELECT state FROM capability_authorizations WHERE extension_id = ? AND capability_id = ?",
                (SCREEN_EXTENSION, capability))
            self.assertEqual(row["state"], CapabilityAuthorizationState.REVOKED.value)
        grants = PersistentIdentityDirectory(self.controller.runtime).snapshot().grants
        owned = [grant for grant in grants if grant.grant_id.startswith("screen-access:")]
        self.assertTrue(owned)
        self.assertTrue(all(grant.state is GrantState.REVOKED for grant in owned))
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_view_only_operator_gets_read_only_status_and_cannot_submit_consent(self):
        await self.start()
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        original = self.controller.session.profile.target
        target = TaskContext(operator, original.role_binding, AudienceRef(original.scope, (operator.subject,)))
        self.controller.session.profile = NS(target=target)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
            expected_revision=policy.revision, observed_at=support.NOW)

        with patch.object(self.controller.session.application.tasks, "authorize_context",
                          return_value=Outcome.success(True)):
            readonly = await self.call()
            self.assertEqual(readonly["status"], "ok", readonly)
            self.assertFalse(readonly["data"]["can_manage"])
            denied = await self.call(body={"operation_key": "read-only-enable",
                "edit_token": readonly["data"]["edit_token"], "enabled": True})
        self.assertEqual(denied["data"]["code"], "core_access_required")

    async def test_route_requires_dashboard_identity_and_rejects_query_overrides(self):
        await self.start()
        for username in (None, "", "api_key:synthetic"):
            self.assertEqual((await self.call(username=username))["data"]["code"],
                             "dashboard_identity_required")
        self.assertEqual((await self.call(query=b"target_id=other"))["data"]["code"],
                         "screen_access_request_invalid")

    async def test_review_routes_reject_api_keys_and_identity_or_capture_overrides(self):
        await self.start()
        for suffix in ("/last-observation", "/last-original"):
            self.assertEqual((await self.call(route=PREFIX + suffix, username="api_key:synthetic"))["data"]["code"],
                "dashboard_identity_required")
        for suffix, query in (("/last-observation", b"target_id=other"),
                              ("/last-original", b"capture_id=a&capture_id=b")):
            self.assertEqual((await self.call(route=PREFIX + suffix, query=query))["data"]["code"],
                "screen_access_request_invalid")

    async def test_unassembled_review_reports_a_reason_instead_of_an_authorization_failure(self):
        await self.start()
        with patch.object(self.controller.session.application.tasks, "authorize_context",
                          return_value=Outcome.success(True)):
            result = await self.call(route=PREFIX + "/last-observation")
        self.assertEqual(result["status"], "ok", result)
        self.assertIsNone(result["data"]["observation"])
        self.assertEqual(result["data"]["reason"], "capability_not_installed")
        self.assertIn("latest_attempt", result["data"])

    async def test_review_still_raises_real_authorization_failures(self):
        await self.start()
        with patch.object(self.controller.session.application.tasks, "authorize_context",
                          return_value=Outcome.failed("authorization_denied", "denied")):
            result = await self.call(route=PREFIX + "/last-observation")
        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "authorization_denied")


if __name__ == "__main__":
    unittest.main()
