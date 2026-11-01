"""The panel HTTP boundary keeps returning the host's JSON envelope."""

import unittest

from astrbot_host.web_api import http_handler


class PanelResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_envelope_is_returned_unchanged(self):
        payload = {"status": "error", "message": "acquaintance_route_required",
                   "reason": "请选择一个具体的 Bot、账号和角色。",
                   "data": {"code": "acquaintance_route_required"}}

        async def handler():
            return payload

        self.assertEqual(await http_handler(handler)(), payload)

    async def test_unhandled_exception_becomes_an_error_envelope(self):
        async def handler():
            raise RuntimeError("boom")

        response = await http_handler(handler)()
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["data"]["code"], "plugin_web_internal_error")
        self.assertEqual(response["message"], "plugin_web_internal_error")


if __name__ == "__main__":
    unittest.main()
