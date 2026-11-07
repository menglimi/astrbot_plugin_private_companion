"""QQ 空间凭据探测的预算边界。

现场（2026-11-05）：页面 15 秒就报「读取超时」，凭据一直停在「尚未检查」。
原因是探测按 2 个 action × 8 个 domain × 2 种参数形状逐个试、每次最多 8 秒——
客户端在 get_cookies 上挂住时，整轮能拖到几分钟，页面永远等不到结论。
"""
from __future__ import annotations

import asyncio
from time import monotonic
import unittest
from unittest.mock import patch

from astrbot_host.qzone_client import QzoneHttpClient
from companion_v2.execution.models import TaskError


class HangingBot:
    """每一个 OneBot 动作都不返回；模拟 NTQQ 客户端不响应 QQ 空间 Cookie 接口。"""

    async def get_cookies(self, **_params):
        await asyncio.sleep(30)

    async def get_credentials(self, **_params):
        await asyncio.sleep(30)

    async def get_login_info(self, **_params):
        await asyncio.sleep(30)

    async def call_action(self, _action, **_params):
        await asyncio.sleep(30)


class ResponsiveBot:
    async def get_cookies(self, **_params):
        return {"cookies": "uin=o123456; skey=SK; p_skey=PSK; qzonetoken=T"}

    async def call_action(self, _action, **_params):
        return None


class QzoneCookieProbeBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_hanging_onebot_returns_a_conclusion_within_the_page_timeout(self):
        client = QzoneHttpClient(bot_provider=lambda: HangingBot())
        started = monotonic()
        with patch("astrbot_host.qzone_client.COOKIE_PROBE_BUDGET_SECONDS", 0.2):
            with self.assertRaises(TaskError) as caught:
                await client.credential()
        elapsed = monotonic() - started
        self.assertEqual(caught.exception.code, "qzone_credential_missing")
        # 预算 0.2 秒：允许调度开销，但必须远小于页面 15 秒的等待。
        self.assertLess(elapsed, 2.0, f"探测没有按预算收口：{elapsed:.2f}s")
        self.assertEqual(client.last_credential_state["state"], "failed")

    async def test_responsive_onebot_still_yields_a_credential(self):
        client = QzoneHttpClient(bot_provider=lambda: ResponsiveBot())
        credential = await client.credential()
        self.assertEqual(int(credential.uin), 123456)
        self.assertEqual(client.last_credential_state["state"], "ready")


if __name__ == "__main__":
    unittest.main()
