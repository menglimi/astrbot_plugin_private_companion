"""Per-role route refresh must heal after a transient failure, not latch forever.

Saving a persona mapping races the controller rebuild.  A single failed
re-register used to set a permanent flag, so the new role's ``config`` route
never appeared and the page answered "未找到该路由" until the plugin was
reloaded.  These tests pin the backoff behaviour instead.
"""

import unittest
from unittest.mock import patch

from astrbot_host.multi_api import MultiHostWebAPI


ROUTE = "/astrbot_plugin_private_companion/host/targets/t1/roles/r1/config"


class FakeController:
    def __init__(self, failures=0):
        self.failures = failures
        self.registrations = 0
        self.unregistrations = 0

    def unregister_routes(self):
        self.unregistrations += 1

    def register_routes(self):
        self.registrations += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("controller is rebuilding")


def inventory():
    return [{"target_id": "t1", "roles": [{"role_id": "r1", "config_api": ROUTE}]}]


class RoleRouteRefreshTest(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.api = MultiHostWebAPI(self.controller)
        self.api._registered_routes = []

    def test_a_failed_refresh_retries_after_the_backoff(self):
        self.controller.failures = 1
        self.api._ensure_role_routes(inventory())
        self.assertEqual(self.controller.registrations, 1)
        self.assertGreater(self.api._role_routes_retry_at, 0)

        # 退避窗口内不重复注册，避免每个页面请求都触发一次重建。
        self.api._ensure_role_routes(inventory())
        self.assertEqual(self.controller.registrations, 1)

        with patch("astrbot_host.multi_api.time.monotonic",
                   return_value=self.api._role_routes_retry_at + 1):
            self.api._ensure_role_routes(inventory())
        self.assertEqual(self.controller.registrations, 2, "退避结束后应再次尝试")
        self.assertEqual(self.api._role_routes_retry_at, 0, "成功后清掉退避")

    def test_known_routes_do_not_trigger_a_rebuild(self):
        self.api._registered_routes = [(ROUTE, object())]
        self.api._ensure_role_routes(inventory())
        self.assertEqual(self.controller.registrations, 0)

    def test_an_empty_inventory_is_ignored(self):
        self.api._ensure_role_routes([])
        self.assertEqual(self.controller.registrations, 0)


if __name__ == "__main__":
    unittest.main()
