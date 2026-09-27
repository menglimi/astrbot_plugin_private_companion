# -*- coding: utf-8 -*-
"""DailyStateTickPostSendSidecarMixin。

由 tools/split_mixin_domain.py 从 daily_state_tick.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 16 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

import asyncio
from .daily_state_tick_shared import logger
from .helpers import _single_line



class DailyStateTickPostSendSidecarMixin:
    """DailyStateTickPostSendSidecarMixin（从 DailyStateTickMixin 拆出）。"""


    async def _run_hdsi_life_tick_sidecar(self) -> None:
        """Advance the opt-in HDSI life sidecar without disturbing the main tick.

        HDSI life progression is independent from proactive message generation:
        it only advances actor records already created by an HDSI route and
        never schedules a send or changes the legacy user/proactive decisions.
        Kept in one place so every tick path shares the same fail-open policy.
        """
        try:
            from .hdsi_experiment import run_hdsi_life_tick

            await run_hdsi_life_tick(self)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("HDSI life tick skipped: %s", _single_line(exc, 160))
