"""Screen source configuration uses the existing Dashboard core identity boundary."""

import asyncio
import json
import secrets
from types import SimpleNamespace as NS
import unittest

from tests import astrbot_data_root  # Isolate the SDK before importing the host.
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.management_identity import dashboard_operator
from astrbot_host.native_extensions.screen_source import ScreenSourceSettings
from astrbot_host.screen_access_api import ScreenAccessWebAPI, PREFIX
from companion_v2.identity_policy.models import AuthorizationTier
from tests.test_management_access import ManagementFixture, NOW


class RemoteScreenSourceAPITests(ManagementFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.source = ScreenSourceSettings(self.root / "screen")
        self.controller = NS(runtime=self.runtime, now=lambda: NOW, _terminated=False,
            _control_lock=asyncio.Lock(), native_extensions=NS(api=lambda _: NS(source=self.source)))
        self.api = ScreenAccessWebAPI(self.controller)
        self.command = {"revision": self.source.snapshot()["revision"], "updates": {
            "mode": "remote", "remote_url": "http://127.0.0.1:6191/capture",
            "remote_token": secrets.token_urlsafe(48), "device_id": "linux-desktop"}}

    async def call(self, *, username="admin", command=None, query=b""):
        payload = json.dumps(self.command if command is None else command).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        native = Request({"type": "http", "method": "POST", "path": PREFIX + "/capture-source",
                          "headers": [], "query_string": query}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.api.capture_source()

    async def test_core_can_save_source_without_device_call_or_other_configuration_write(self):
        dashboard_operator(self.runtime, "admin", at=NOW)
        identity_revision = self.identity.revision
        policy_revision = self.policy.revision
        result = await self.call()
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["capture_source"]["settings"]["device_id"], "linux-desktop")
        self.assertNotIn(self.command["updates"]["remote_token"], json.dumps(result))
        self.assertEqual(self.identity.revision, identity_revision)
        self.assertEqual(self.policy.revision, policy_revision)

    async def test_noncore_cannot_change_source(self):
        operator = dashboard_operator(self.runtime, "admin", at=NOW)
        self.policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                             expected_revision=self.policy.revision, observed_at=NOW)
        result = await self.call()
        self.assertEqual(result["data"]["code"], "core_access_required", result)
        self.assertFalse(self.source.path.exists())

    async def test_service_keys_and_identity_overrides_are_rejected(self):
        for username in (None, "", "api_key:synthetic"):
            result = await self.call(username=username)
            self.assertEqual(result["data"]["code"], "dashboard_identity_required", result)
        result = await self.call(query=b"target_id=another-window")
        self.assertEqual(result["data"]["code"], "screen_source_invalid", result)
        self.assertFalse(self.source.path.exists())

    async def test_stale_revision_cannot_replace_the_device(self):
        self.assertEqual((await self.call())["status"], "ok")
        result = await self.call()
        self.assertEqual(result["data"]["code"], "screen_source_invalid", result)
        self.assertEqual(self.source.read()["device_id"], "linux-desktop")
