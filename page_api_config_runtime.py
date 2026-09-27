# -*- coding: utf-8 -*-
"""config_runtime 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 82 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
from .runtime_config_dispatcher import dispatch_runtime_config_effects
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiConfigRuntimeMixin:
    """config_runtime 域（从 PrivateCompanionPageApi 拆出）。"""


    def _req041_config_runtime_snapshot(self, changed: dict[str, Any]) -> dict[str, Any]:
        """Snapshot only identity/relationship isolation controls before hot apply."""
        critical = {
            "enable_auto_user_profile_creation",
            "portrait_global_mode",
            "auto_profile_platforms",
            "owner_group_relationship_projection",
            "owner_group_interaction_projection",
            "enable_group_relationship_affinity",
            "group_relationship_affinity_allowlist",
            "group_relationship_daily_net_cap",
            "group_relationship_window_minutes",
            "group_relationship_window_absolute_cap",
            "group_relationship_person_daily_absolute_cap",
            "group_relationship_scope_daily_absolute_cap",
            "relationship_event_window_minutes",
            "relationship_positive_event_cap",
            "relationship_negative_event_cap",
            "relationship_positive_daily_cap",
        }
        snapshot: dict[str, Any] = {}
        getter = getattr(self, "_config_get_raw", None)
        for key in sorted(critical & set(changed)):
            if hasattr(self.plugin, key):
                snapshot[key] = deepcopy(getattr(self.plugin, key))
            elif callable(getter):
                snapshot[key] = deepcopy(getter(key, None))
        return snapshot

    async def _rollback_req041_config_runtime(self, snapshot: dict[str, Any]) -> bool:
        """Restore runtime and config object, then durably save the old values."""
        for key, value in snapshot.items():
            self._apply_config_value(key, deepcopy(value))
        try:
            return bool(await self._save_config_if_possible())
        except Exception as exc:
            logger.error(
                "REQ-041 配置回滚持久化失败: %s",
                self._single_line(exc, 160),
            )
            return False

    async def get_extension_control_plane_status(self) -> dict[str, Any]:
        """Expose extension metadata and invariant checks without provider data."""
        api = getattr(self.plugin, "extension_api", None)
        getter = getattr(api, "extension_control_plane_status", None)
        if not callable(getter):
            return self._ok(
                {
                    "protocol_version": "0.1",
                    "extensions": [],
                    "issues": ["control_plane_unavailable"],
                }
            )
        try:
            payload = getter()
            return self._ok(payload if isinstance(payload, dict) else {})
        except Exception as exc:
            logger.error("读取扩展控制面状态失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error("读取扩展状态失败")

    def _forward_runtime_config_effects(
        self,
        key: str,
        value: Any,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        dispatch_runtime_config_effects(
            self.plugin,
            {key: value},
            source="page",
            adapter=self,
            overrides=overrides,
        )

    def _schedule_body_monitor_integration_toggle(self, enabled: bool) -> asyncio.Task[Any] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(
            self._sync_body_monitor_integration_toggle(enabled),
            name="private_companion_body_monitor_toggle",
        )
        self.plugin._body_monitor_integration_toggle_task = task
        return task
