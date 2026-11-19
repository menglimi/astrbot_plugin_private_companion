"""Real SDK request identity and native adapter shape, with isolated model doubles."""

import asyncio
import json
import unittest

from tests import test_relationship_management_host as support
from tests.native_adapter_support import completion
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request
from astrbot_host.life_api import PREFIX
from companion_v2.storage.runtime import StorageOpenError


class LifeAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.RelationshipManagementHostTests.asyncSetUp

    async def request(self, *, username="admin", data=None, raw=None, query=""):
        raw = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode("utf-8") if data else b""
        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}
        mutation = data is not None or bool(raw)
        path = PREFIX + ("/command" if mutation else "")
        req = Request({"type": "http", "method": "POST" if mutation else "GET", "path": path,
            "headers": [(b"content-type", b"application/json")], "query_string": query.encode()}, receive=receive)
        with bind_request_context(PluginRequest(req, username=username)):
            return await self.routes[path]()

    async def test_authentication_and_strict_command_schema(self):
        self.assertEqual((await self.request(username=None))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request())["data"]["code"], "staging_not_initialized")
        (await self.controller.initialize_profile()).unwrap()
        status = (await self.request())["data"]
        for extra in ({"operator":"admin"}, {"participant":"someone"}, {"source":{}}):
            bad = await self.request(data={"action":"sleep", "minutes":1, "operation_key":"a", "view_token":status["view_token"], **extra})
            self.assertEqual(bad["data"]["code"], "life_command_invalid")
        self.assertEqual((await self.request(raw=b'{"action":"sleep","action":"cancel"}'))["data"]["code"], "life_command_invalid")
        self.assertEqual((await self.request(query="before=invalid"))["data"]["code"], "life_query_invalid")
        self.assertEqual((await self.request(username="api_key:42"))["data"]["code"], "dashboard_identity_required")

    async def test_native_read_diary_and_stopped_archive(self):
        (await self.controller.initialize_profile()).unwrap()
        status = (await self.request())["data"]
        config = {"action":"configure", "operation_key":"enable", "changes":{"enabled":True,"diary_enabled":False}, "edit_token":status["edit_token"]}
        self.assertEqual((await self.request(data=config))["status"], "ok")
        (await self.controller.start()).unwrap()
        async def model(payload, client):
            text = json.dumps(payload, ensure_ascii=False)
            if "original_reading_span" in text:
                return completion(json.dumps({"reflection":"资料里的观点让我想到一个问题。"}, ensure_ascii=False))
            if "parent_journal" in text:
                return completion(json.dumps({"summary":"读到一段资料后记下想法。","dream_fragments":[],"continuity":""}, ensure_ascii=False))
            return completion(json.dumps({"body":"今天读到一段资料。","form":"随记","recall":None,"afterglow":""}, ensure_ascii=False))
        self.native.provider.client.state.behavior = model
        status = (await self.request())["data"]
        added = await self.request(data={"action":"add_text", "operation_key":"book", "view_token":status["view_token"], "title":"原文", "text":"这里是一段真实提供的测试原文。"})
        self.assertEqual(added["status"], "ok", added)
        item = (await self.request())["data"]["activities"][0]
        read = await self.request(data={"action":"read", "operation_key":"read", "activity_id":item["activity_id"], "expected_revision":item["revision"], "view_token":item["view_token"]})
        self.assertEqual(read["status"], "ok", read)
        async def wait():
            while self.controller.session.application.life_runtime.active_handles:
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 3)
        status = (await self.request())["data"]
        self.assertEqual(status["activities"][0]["state"], "completed", status["activities"])
        result = await self.request(data={"action":"diary", "operation_key":"diary", "view_token":status["view_token"]})
        self.assertEqual(result["data"]["state"], "written", result)
        application = self.controller.session.application
        store = application.configuration.store
        (await self.controller.stop()).unwrap()
        model_calls = len(self.native.provider.client.state.calls)
        for _ in range(3):
            response = await self.request()
            self.assertEqual(response["status"], "ok", response)
            status = response["data"]
            self.assertTrue(status["journals"][0]["body"])
            self.assertTrue(status["journals"][0]["summary"])
            self.assertEqual(status["host_state"], "stopped")
            self.assertTrue(status["config"]["enabled"])
            self.assertFalse(status["config"]["diary_enabled"])
            self.assertEqual(status["running"], [])
            self.assertEqual(application.state, "stopped")
            self.assertEqual(application.life_runtime.active_handles, ())
            self.assertIs(application.runtime.configuration_store, store)
        self.assertEqual(len(self.native.provider.client.state.calls), model_calls)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        await self.controller.terminate()
        with self.assertRaises(StorageOpenError):
            store.read_one("SELECT COUNT(*) AS n FROM configuration_values", ())
        self.assertEqual(self.native.registered_web_apis, [])
