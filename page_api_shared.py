# -*- coding: utf-8 -*-
"""page_api 域家族共享工具。

宿主延迟代理：域 mixin 模块内通过 `_page_api_host.<name>` 访问宿主模块
(page_api) 的全局名字。直接 `from .page_api import name` 拷贝的是值绑定，
测试对 `astrbot_plugin_private_companion.page_api.<name>` 的 patch 将不生效；
属性式延迟解析保证 patch 始终路由到宿主模块的当前绑定。
"""
from __future__ import annotations


class _PageApiHostRef:
    """延迟引用宿主 page_api 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api as _host_module

        return getattr(_host_module, name)


_page_api_host = _PageApiHostRef()


class _PageApiRequestProxy:
    """`request` 的双向可 patch 代理。

    page_api 家族把方法拆到 30+ 个域模块后，`request` 出现两种 patch 风格：

    - `patch("...page_api.request", fake)`         —— 打宿主模块
    - `patch("...page_api_media.request", fake)`   —— 打域模块自己

    二者要同时成立，必须满足两个条件：
    1. 域模块顶层**真的有** `request` 这个名字（否则 patch 报
       `AttributeError: module does not have the attribute 'request'`）；
    2. 这个名字不能是 `from quart import request` 的值绑定
       （否则打宿主的 patch 对域模块无效，mock 从不触发）。

    所以域模块统一写成
    `from .page_api_shared import _page_api_host_request as request`
    —— 名字在、但解析推迟到调用时，两种 patch 都能生效。
    """

    def __getattr__(self, name: str):
        from . import page_api as _host_module

        return getattr(getattr(_host_module, "request"), name)


_page_api_host_request = _PageApiRequestProxy()
