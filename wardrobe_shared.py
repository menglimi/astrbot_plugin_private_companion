# -*- coding: utf-8 -*-
"""wardrobe 域家族共享叶子模块。

拆分后宿主与三个域模块之间原本是**双向模块级 import**：

    wardrobe.py            -> wardrobe_part01/02/03   （门面重导出）
    wardrobe_part01/02/03  -> wardrobe.py             （vars(host) 引导拷贝宿主命名空间）

后一条边在 import 期执行，构成模块级环。ci_static_checks 的 check_architecture 只
扫描 ast.Module.body 的直接语句，环上两条边都在 try 块内，因此该守卫看不到它，但
Tarjan 全量扫描会报 4 节点环。

本模块把「引用宿主」改为**惰性代理**：代理自身不 import 族内任何模块，是族内唯一
的无环叶子，因此族内任意模块都能安全 ``from .wardrobe_shared import _wardrobe_host``；
真正 import 宿主只发生在属性读取时（那时宿主已完成初始化）。

约定与 final_response_persistence_shared.py 的 ``_final_response_persistence_host``
一致，保证 ``patch("...wardrobe.<name>")`` 对全部域模块生效。
"""
from __future__ import annotations


class _WardrobeHostRef:
    """延迟引用宿主 wardrobe 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        try:
            from . import wardrobe as _host_module
        except ImportError:  # scripts/ 下的离线工具把本模块当顶层模块加载
            import wardrobe as _host_module  # type: ignore[no-redef]

        return getattr(_host_module, name)


_wardrobe_host = _WardrobeHostRef()
