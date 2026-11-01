"""A missing required cookie keeps its specific reason in the panel response."""

import unittest

from astrbot_host.huawei_health.api import HuaweiHealthWebAPI
from astrbot_host.huawei_health.vendor.hw_auth import HuaweiAuthError


class FakeService:
    async def import_cookie(self, text, fields):
        raise HuaweiAuthError("缺少必要的 cookie：CASTGC、sid。请把六项都填上，或改用整段 JSON 导出。")


class HuaweiCookieImportResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cookie_reason_is_not_collapsed_into_a_generic_failure(self):
        api = HuaweiHealthWebAPI(controller=object())

        async def json_body():
            return {"json": '[{"name":"CASLOGINSITE","value":"1"}]', "fields": {}}

        async def service():
            return FakeService()

        api._json_body = json_body
        api._selected_scope = lambda body: ("target-a", "role-a")
        api._service = service

        response = await api.import_cookie()
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["message"], "huawei_health_cookie_invalid")
        self.assertIn("CASTGC", response["reason"])
        self.assertEqual(response["data"]["code"], "huawei_health_cookie_invalid")


if __name__ == "__main__":
    unittest.main()
