# -*- coding: utf-8 -*-
"""passive_state_pipeline 阶段化拆分的共享件。

- `_psp_host`：延迟引用宿主模块，保证 patch 宿主全局名对全部阶段模块生效。
- `_PassiveStageContext`：阶段之间传递局部变量的轻量命名空间。
- `_PASSIVE_STAGE_STOP`：阶段函数早退哨兵，宿主收到后整体 return。
"""
from __future__ import annotations


_PASSIVE_STAGE_STOP = object()


class _PassiveHostRef:
    """延迟引用 passive_state_pipeline 宿主模块。"""

    def __getattr__(self, name: str):
        from . import passive_state_pipeline as _host_module

        return getattr(_host_module, name)


_psp_host = _PassiveHostRef()


class _PassiveStageContext:
    """阶段函数之间传递局部变量的命名空间。"""

    def __init__(self, **values):
        self.__dict__.update(values)
