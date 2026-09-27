# -*- coding: utf-8 -*-
"""每日回顾域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 47 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
import time
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiDailyReviewMixin:
    """每日回顾域（从 PrivateCompanionPageApi 拆出）。"""


    async def get_daily_review(self) -> dict[str, Any]:
        try:
            payload_getter = getattr(self.plugin, "_daily_review_status_payload", None)
            if not callable(payload_getter):
                return self._error("当前插件版本未加载每日终盘巡视模块")
            async with self.plugin._data_lock:
                payload = payload_getter()
            return self._ok(payload)
        except Exception as exc:
            logger.error("获取每日巡视报告失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._error(str(exc))

    async def run_daily_review(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        target_date = self._single_line(payload.get("date"), 16)
        if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
            return self._error("巡视日期格式必须为 YYYY-MM-DD")
        runner = getattr(self.plugin, "_ensure_daily_review", None)
        if not callable(runner):
            return self._error("当前插件版本未加载每日终盘巡视模块")
        try:
            report = await runner(force=True, target_date=target_date)
            if not isinstance(report, dict):
                return self._error("巡视未生成有效报告")
            async with self.plugin._data_lock:
                status = self.plugin._daily_review_status_payload()
            return self._ok({"report": report, **status})
        except Exception as exc:
            logger.warning("手动执行每日巡视失败: %s", self._single_line(exc, 180))
            return self._error(str(exc))

    async def update_daily_review_guidance(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        active = bool(payload.get("active", False))
        try:
            async with self.plugin._data_lock:
                guidance = self.plugin.data.get("daily_review_active_guidance")
                if not isinstance(guidance, dict) or not isinstance(guidance.get("items"), list) or not guidance.get("items"):
                    return self._error("当前没有可启用的低风险巡视指导")
                if active and self._float(guidance.get("active_until")) <= time.time():
                    return self._error("这份巡视指导已经过期，请重新执行巡视")
                guidance["active"] = active
                guidance["manual_paused"] = not active
                self.plugin._save_data_sync(sections={"daily_review_active_guidance"})
                status = self.plugin._daily_review_status_payload()
            return self._ok(status)
        except Exception as exc:
            logger.error("更新每日巡视指导失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._error(str(exc))
