# -*- coding: utf-8 -*-
"""回归守卫：``_move_timestamp_into_reason_window`` 的产出必须落在窗口内。

修复前两处随机偏移都未收敛到窗口右边界：
- 窗口内分支 ``timestamp + randint(0, 17*60)`` 最多加 17 小时，必然冲出 ``end``；
- 窗口外分支 ``target.timestamp() + randint(0, 59*60)`` 同样不受窗口约束。

``check_in`` 等调用方没有二次边界保护，越界即真的会在窗外发消息。

本测试直接调用真实函数并大量采样，断言结果时间戳对应的「当日分钟数」
始终落在 ``[start, end)`` 内。
"""
from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta

from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


# 窄到 1 小时的窗口，随机偏移最容易越界。
START_MIN = 9 * 60 + 30
END_MIN = 10 * 60 + 30


class _WindowHarness(ProactiveEngineMixin):
    """只覆写本测试关心的依赖，其余走 mixin 真实实现。"""

    def __init__(self) -> None:
        self.calls = 0

    def _normalize_legacy_proactive_text(self, text, limit=40):
        return text

    def _reason_windows(self, reason, user=None):
        self.calls += 1
        return [(START_MIN, END_MIN)]

    def _apply_chronotype_shift_to_windows(self, windows, user=None):
        return windows

    def _environment_fromtimestamp(self, ts: float) -> datetime:
        # 与 tz-aware 构造保持一致的本地时区。
        return datetime.fromtimestamp(ts)


def _minute_of_day(ts: float) -> int:
    dt = datetime.fromtimestamp(ts)
    return dt.hour * 60 + dt.minute


class MoveTimestampIntoWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _WindowHarness()
        random.seed(20260919)

    def _assert_all_inside(self, samples: int, base_ts: float) -> None:
        for _ in range(samples):
            ts = self.h._move_timestamp_into_reason_window(base_ts, "check_in", None)
            minute = _minute_of_day(ts)
            self.assertTrue(
                START_MIN <= minute < END_MIN,
                f"调度时间越出窗口 [{START_MIN}, {END_MIN})："
                f"实际分钟 {minute}，时间戳 {ts}",
            )

    def test_window_inside_branch_never_overshoots(self) -> None:
        """起点在窗口内时，随机偏移不得冲出窗口右边界。"""
        base = datetime(2026, 9, 19, 9, 45).timestamp()  # 09:45，落在窗口内
        self._assert_all_inside(2000, base)

    def test_window_outside_branch_never_overshoots(self) -> None:
        """起点在窗口外时，回推后的目标点同样不得越界。"""
        base = datetime(2026, 9, 19, 3, 0).timestamp()  # 03:00，早于窗口
        self._assert_all_inside(2000, base)

    def test_late_in_window_start_still_clamped(self) -> None:
        """贴着右边界启动（10:29）时，偏移必须被压回 10:29 而非溢出。"""
        base = datetime(2026, 9, 19, 10, 29).timestamp()
        self._assert_all_inside(2000, base)

    def test_window_helpers_still_used(self) -> None:
        """确保测试真的走到了被测函数，而不是被短路。"""
        base = datetime(2026, 9, 19, 9, 45).timestamp()
        self.h._move_timestamp_into_reason_window(base, "check_in", None)
        self.assertGreater(self.h.calls, 0, "_reason_windows 未被调用，测试前提失效")


if __name__ == "__main__":
    unittest.main()
