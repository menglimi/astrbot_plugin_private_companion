# -*- coding: utf-8 -*-
"""event_dispatch 域家族共享工具。

宿主延迟代理：域 mixin 模块内通过 `_event_dispatch_host.<name>` 访问宿主模块
(event_dispatch) 的全局名字。直接 `from .event_dispatch import name` 拷贝的是值绑定，
测试对 `astrbot_plugin_private_companion.event_dispatch.<name>` 的 patch 将不生效；
属性式延迟解析保证 patch 始终路由到宿主模块的当前绑定。

同时承载全族共享的 logger 实例与模块级函数，保证：
- 测试 `patch("...event_dispatch.logger.warning")` 对全部域模块生效；
- 多桶共用的 `_persona_value` / `_persona_feature_enabled` 只有一份实现。
"""
from __future__ import annotations

from typing import Any

from .logging_util import get_module_logger

# 全族共享同一 logger 实例（拆到域模块后 patch 宿主 logger 仍需命中）。
# 名字与宿主模块 __name__ 保持一致，日志标签与拆分前完全相同。
logger = get_module_logger("astrbot_plugin_private_companion.event_dispatch")


def _persona_value(owner: Any, key: str, default: Any = None) -> Any:
    """Read an active-persona setting without changing shared plugin state."""
    getter = getattr(owner, "persona_setting", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            pass
    return getattr(owner, key, default)


def _persona_feature_enabled(owner: Any, key: str, default: bool = False) -> bool:
    if not hasattr(owner, "enable_multi_persona_mode"):
        checker = getattr(owner, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            try:
                return bool(checker(key, default))
            except Exception:
                pass
    if bool(_persona_value(owner, key, default)):
        return True
    unlocker = getattr(owner, "_proactive_only_temp_unlock_allows", None)
    return bool(
        _persona_value(owner, "enable_proactive_only_mode", False)
        and callable(unlocker)
        and unlocker(key)
    )


class _EventDispatchHostRef:
    """延迟引用宿主 event_dispatch 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import event_dispatch as _host_module

        return getattr(_host_module, name)


_event_dispatch_host = _EventDispatchHostRef()
