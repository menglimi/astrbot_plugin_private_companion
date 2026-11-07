"""Wildcard group reply routes resolve and isolate concrete groups."""

import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.multi_api import PREFIX
from companion_v2.execution.models import digest
from tests.group_host_support import GroupFixture, Query
from tests.multi_host_support import multi_config


class WildcardGroupReplyTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_concrete_group_settings_from_wildcard_route(self):
        config = multi_config(group_targets=[dict(target_id="all-groups", platform_id="qq-main",
            account_id="100", bot_id="bot-a", group_id="*")])
        self.controller = self.make_controller(config)
        await self.start_host()
        self.controller.register_routes()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        base = PREFIX + "/targets/all-groups/roles/" + digest("role-a")
        route = base + "/groups/<path:group_id>/reply"
        self.assertIn(route, routes)

        async def call(handler, group_id, body=None):
            async def read_body():
                return json.dumps(body, ensure_ascii=False).encode("utf-8")
            with patch("astrbot_host.group_reply_api.request", NS(
                    username="admin", query=Query(), body=read_body)):
                return await handler(group_id=group_id)

        first = await call(routes[route], "300")
        second = await call(routes[route], "301")
        self.assertEqual((first["status"], second["status"]), ("ok", "ok"))
        value = not first["data"]["settings"]["enabled"]
        saved = await call(routes[route + "/configure"], "300", {
            "operation_key": "group-300-segmentation", "edit_token": first["data"]["edit_token"],
            "changes": {"enabled": value}})
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual((await call(routes[route], "300"))["data"]["settings"]["enabled"], value)
        self.assertNotEqual((await call(routes[route], "301"))["data"]["settings"]["enabled"], value)
        self.assertEqual((await call(routes[route], "*"))["data"]["code"], "group_reply_target_required")


if __name__ == "__main__":
    unittest.main()
