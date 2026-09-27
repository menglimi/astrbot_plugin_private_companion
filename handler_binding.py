# -*- coding: utf-8 -*-
"""把本插件子模块注册的 handler 重新绑定到插件主模块。

## 问题

AstrBot 的 ``StarHandlerRegistry.get_handlers_by_event_type`` 默认
``only_activated=True``，会按 ``handler_module_path`` 去 ``star_map`` 里反查
插件元数据：

```python
if only_activated:
    plugin = star_map.get(handler.handler_module_path)
    if not (plugin and plugin.activated):
        continue
```

而 ``star_map`` 的 key **只有插件主模块路径**（``StarManager`` 在加载时执行
``star_map[path] = metadata``，``path`` 即 ``<pkg>.main``）。于是任何
``handler_module_path`` 不等于主模块路径的 handler，都会被判定为「插件未激活」
并在运行时被静默跳过——装饰器明明执行了、注册表里也有记录，但 hook 永不触发。

## 触发条件

只要 ``@filter.*`` 装饰器写在**非主模块**里就会中招。本仓库有两类：

1. 历史遗留：``atrelay.py``（1 个 handler）
2. 巨型模块拆分引入：``main_outbound_guard.py``（28 个）、
   ``main_prompt.py``（1 个）

## 处理

本模块提供 :func:`bind_submodule_handlers`，在宿主模块加载完成后调用一次，
把本包内所有 handler 的 ``handler_module_path`` 重写为宿主主模块路径，
使其通过 ``only_activated`` 反查。

``handler_full_name`` **不修改**——它由 ``<模块>_<函数名>`` 构成，是注册表
内部字典的 key，保持原值可避免与其他插件 / 重复注册互相覆盖。
"""
from __future__ import annotations

import sys
from typing import Any

from astrbot.core.star.star_handler import star_handlers_registry

__all__ = ["bind_submodule_handlers"]


def bind_submodule_handlers(package_name: str, host_module_path: str) -> int:
    """把 ``package_name`` 包内子模块注册的 handler 重绑到 ``host_module_path``。

    参数：
        package_name: 插件包名，例如 ``astrbot_plugin_private_companion``。
        host_module_path: 插件主模块路径，例如
            ``astrbot_plugin_private_companion.main``。

    返回：被重绑的 handler 数量（0 表示无需处理）。
    """
    prefix = f"{package_name}."
    bound = 0
    for handler in star_handlers_registry:
        module_path = str(getattr(handler, "handler_module_path", "") or "")
        if not module_path.startswith(prefix):
            continue
        if module_path == host_module_path:
            continue
        # 仅当该模块确实属于本包时才改写，避免误伤同名前缀的其他包。
        if module_path.split(".")[0] != package_name:
            continue
        handler.handler_module_path = host_module_path
        bound += 1
    return bound


def rebind_registry_map(package_name: str, host_module_path: str) -> None:
    """兼容钩子：把 ``star_handlers_map`` 里指向子模块的条目规范化。

    目前注册表以 ``handler_full_name`` 为 key，重绑 ``handler_module_path``
    不会破坏映射，因此这里只做一致性校验，不重建字典。
    """
    registry_map: dict[str, Any] = getattr(
        star_handlers_registry, "star_handlers_map", {}
    )
    for full_name, handler in list(registry_map.items()):
        module_path = str(getattr(handler, "handler_module_path", "") or "")
        if module_path.startswith(f"{package_name}.") and module_path != host_module_path:
            # 保持 key 不变，仅确保 value 已重绑。
            handler.handler_module_path = host_module_path


def host_module_path_for(package_name: str) -> str:
    """推导插件主模块路径（``<pkg>.main``，缺失时回退 ``<pkg>``）。"""
    main_name = f"{package_name}.main"
    if main_name in sys.modules:
        return main_name
    return package_name
