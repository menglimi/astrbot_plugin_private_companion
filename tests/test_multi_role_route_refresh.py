"""A role mapped after boot must not keep advertising an unregistered route."""

import unittest

from astrbot_host.multi_api import MultiHostWebAPI


class FakeController:
    def __init__(self):
        self.calls = []

    def unregister_routes(self):
        self.calls.append("unregister")

    def register_routes(self):
        self.calls.append("register")


class RoleRouteRefreshTests(unittest.TestCase):
    def test_missing_role_route_refreshes_registration_once(self):
        api = MultiHostWebAPI.__new__(MultiHostWebAPI)
        api._registered_routes = []
        api.controller = FakeController()
        inventory = [{"roles": [{"config_api": "/astrbot_plugin_private_companion/targets/t/roles/r/config"}]}]
        api._ensure_role_routes(inventory)
        self.assertEqual(api.controller.calls, ["unregister", "register"])

    def test_registered_route_does_not_trigger_a_refresh(self):
        api = MultiHostWebAPI.__new__(MultiHostWebAPI)
        route = "/astrbot_plugin_private_companion/targets/t/roles/r/config"
        api._registered_routes = [(route, object())]
        api.controller = FakeController()
        api._ensure_role_routes([{"roles": [{"config_api": route}]}])
        self.assertEqual(api.controller.calls, [])


if __name__ == "__main__":
    unittest.main()
