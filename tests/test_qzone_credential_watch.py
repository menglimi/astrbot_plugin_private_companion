"""QQ 空间凭据的自动重取节奏、去重与"告诉管理员"这句话。

这一层不需要 AstrBot、不需要网络：把 ``credential_ttl`` 与 OneBot 都换成替身，
只钉住三件容易写错的事——

1. **梯子**：1 分钟起、24 小时封顶，且普通操作永远只等短档位（≤5 分钟）；
2. **去重**：同一档位只发一条通知，跨档才再发；一次成功就清零；
3. **措辞**：发给管理员的那句话里不允许出现任何 cookie / skey 片段。
"""

from __future__ import annotations

import unittest

from astrbot_host.qzone_credential_watch import QzoneCredentialWatch
from companion_v2.execution.models import TaskError
from companion_v2.qzone.credential import (PASSIVE_RETRY_CAP_SECONDS, RETRY_STEPS_SECONDS,
                                           CredentialWatch, disconnect_message, format_delay,
                                           passive_retry_delay_seconds, recovered_message,
                                           retry_delay_seconds, retry_step_index)


class RetryLadderTests(unittest.TestCase):
    def test_ladder_is_the_requested_one_minute_to_one_day(self):
        self.assertEqual(RETRY_STEPS_SECONDS,
                         (60, 300, 600, 1200, 1800, 3600, 7200, 10800, 21600, 43200, 86400))
        self.assertEqual([retry_delay_seconds(n) for n in range(1, 12)], list(RETRY_STEPS_SECONDS))

    def test_ladder_stops_growing_after_one_day(self):
        self.assertEqual(retry_delay_seconds(1), 60)
        self.assertEqual(retry_delay_seconds(11), 86400)
        self.assertEqual(retry_delay_seconds(12), 86400)
        self.assertEqual(retry_delay_seconds(999), 86400)
        self.assertEqual(retry_step_index(99), len(RETRY_STEPS_SECONDS) - 1)

    def test_ordinary_callers_never_wait_longer_than_five_minutes(self):
        self.assertEqual(PASSIVE_RETRY_CAP_SECONDS, 300)
        self.assertEqual(passive_retry_delay_seconds(1), 60)
        self.assertEqual(passive_retry_delay_seconds(11), 300)

    def test_durations_read_naturally_in_chinese(self):
        self.assertEqual(format_delay(60), "1 分钟")
        self.assertEqual(format_delay(3600), "1 小时")
        self.assertEqual(format_delay(7200), "2 小时")
        self.assertEqual(format_delay(86400), "24 小时")
        self.assertEqual(format_delay(5400), "1 小时 30 分钟")


class CredentialWatchStateTests(unittest.TestCase):
    def test_success_clears_the_ladder(self):
        watch = CredentialWatch().failed().failed().failed()
        self.assertEqual(watch.failures, 3)
        self.assertEqual(watch.next_delay_seconds(), 600)
        self.assertEqual(watch.succeeded(), CredentialWatch())

    def test_one_message_per_rung(self):
        watch = CredentialWatch()
        self.assertIsNone(watch.pending_step())
        watch = watch.failed()                     # 1 分钟
        self.assertEqual(watch.pending_step(), 0)
        watch = watch.noticed(0)
        self.assertIsNone(watch.pending_step())    # 同一档不重复
        watch = watch.failed()                     # 5 分钟，新档
        self.assertEqual(watch.pending_step(), 1)
        watch = watch.noticed(1)
        self.assertIsNone(watch.pending_step())

    def test_disconnect_message_carries_no_secret(self):
        watch = CredentialWatch().failed().failed()
        text = disconnect_message(watch=watch, reason="qzone_credential_missing: uin=有",
                                  uin_masked="2******07", delay_seconds=600)
        self.assertIn("QQ 空间凭据未连接", text)
        self.assertIn("2******07", text)
        self.assertIn("连续失败：2 次", text)
        self.assertIn("10 分钟后", text)
        for secret in ("p_skey=", "skey=", "qzonetoken="):
            self.assertNotIn(secret, text)

    def test_recovered_message_mentions_the_streak_only_when_there_was_one(self):
        self.assertIn("已恢复", recovered_message(failures=0))
        self.assertIn("连续失败 4 次", recovered_message(failures=4))


class _Transport:
    def __init__(self, failures, *, state=None):
        self.failures = list(failures)
        self.calls = 0
        self.last_credential_state = state or {"source": "runtime", "state": "failed",
                                               "uin_masked": "2******07", "reason": "无会话"}

    async def credential(self, *, refresh=False):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return object()


class _Settings:
    def __init__(self, values):
        self.values = values

    def read(self, context, at):
        return dict(self.values)


class _Controller:
    """只用到的三个字段：session / now / 无 session 的空转。"""

    def __init__(self, service=None, settings=None, with_session=True):
        application = type("App", (), {})()
        application.state = "running"
        application.qzone = service
        application.qzone_settings = _Settings(settings if settings is not None else {})
        profile = type("Profile", (), {})()
        profile.target = object()
        session = type("Session", (), {})()
        session.application = application
        session.profile = profile
        session.bridge = None
        self.session = session if with_session else None

    def now(self):
        return None


class WatchLoopTests(unittest.TestCase):
    def _service(self, transport):
        return type("Service", (), {"transport": transport})()

    def _watch(self, transport, *, settings=None, with_session=True):
        return QzoneCredentialWatch(_Controller(self._service(transport), settings, with_session))

    def test_failures_walk_the_ladder_and_do_not_repeat_a_rung(self):
        transport = _Transport([TaskError("qzone_credential_missing", "no session")] * 3)
        watch = self._watch(transport, settings={"credential_admin_notice": False})

        async def run():
            return [await watch._cycle() for _ in range(3)]

        import asyncio
        delays = asyncio.run(run())
        self.assertEqual(delays, [60.0, 300.0, 600.0])
        # 通知关掉时 failures 仍照常累加，只是不发消息（route 为 None 也会失败）
        self.assertEqual(watch.state.failures, 3)

    def test_success_returns_to_the_five_minute_health_check(self):
        transport = _Transport([TaskError("qzone_credential_missing", "no session")])
        watch = self._watch(transport, settings={"credential_admin_notice": False})

        async def run():
            first = await watch._cycle()
            second = await watch._cycle()
            return first, second

        import asyncio
        first, second = asyncio.run(run())
        self.assertEqual(first, 60.0)
        self.assertEqual(second, 300.0)
        self.assertEqual(watch.state, CredentialWatch())

    def test_no_session_is_idle_not_a_failure(self):
        watch = self._watch(_Transport([]), with_session=False)

        import asyncio
        delay = asyncio.run(watch._cycle())
        self.assertEqual(delay, 60.0)
        self.assertEqual(watch.state, CredentialWatch())


if __name__ == "__main__":
    unittest.main()
