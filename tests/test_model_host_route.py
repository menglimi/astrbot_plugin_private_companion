"""The core-admin card must name the model from that UMO's AstrBot config."""

import unittest
from types import SimpleNamespace as NS

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from astrbot_host.model_api import ModelWebAPI
from companion_v2.foundation.refs import ScopeKind


class FakeProvider:
    def meta(self):
        return NS(id="火山引擎/deepseek-v4-flash-ga-260731",
                  model="deepseek-v4-flash-ga-260731")


class ModelHostRouteTests(unittest.TestCase):
    def api(self, *, config_id="abc", name="星缘-私聊", provider=None):
        api = object.__new__(ModelWebAPI)
        api.controller = NS(context=NS(
            astrbot_config_mgr=NS(get_conf_info=lambda umo: {
                "id": config_id, "name": name, "path": f"abconf_{config_id}.json"}),
            get_using_provider=lambda umo: provider or FakeProvider()))
        return api

    def session(self):
        scope = NS(kind=ScopeKind.PRIVATE, platform="qq", native_scope_id="200")
        return NS(profile=NS(target=NS(scope=scope)))

    def test_route_reports_the_config_name_and_effective_model(self):
        route = self.api()._host_route(self.session())
        self.assertEqual(route["umo"], "qq:FriendMessage:200")
        self.assertEqual(route["config"]["name"], "星缘-私聊")
        self.assertEqual(route["provider"], "火山引擎/deepseek-v4-flash-ga-260731")
        self.assertEqual(route["model"], "deepseek-v4-flash-ga-260731")

    def test_default_config_is_named_clearly(self):
        route = self.api(config_id="default", name="default")._host_route(self.session())
        self.assertEqual(route["config"]["name"], "AstrBot 默认配置")

    def test_missing_host_context_degrades_to_empty(self):
        api = object.__new__(ModelWebAPI)
        api.controller = NS(context=NS())
        self.assertEqual(api._host_route(self.session()), {})


if __name__ == "__main__":
    unittest.main()
