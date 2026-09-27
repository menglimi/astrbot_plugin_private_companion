# -*- coding: utf-8 -*-
"""private_image 域家族共享工具。

宿主延迟代理：域 mixin 模块内通过 `_private_image_host.<name>` 访问宿主模块
(private_image) 的全局名字。直接 `from .private_image import name` 拷贝的是值绑定，
测试对 `astrbot_plugin_private_companion.private_image.<name>` 的 patch 将不生效；
属性式延迟解析保证 patch 始终路由到宿主模块的当前绑定。

同时承载全族共享的 logger 实例与模块级常量，保证：
- 测试 `patch("...private_image.logger.warning")` 对全部域模块生效；
- `private_image.PREPARED_IMAGE_MAX_AGE_SECONDS` 等常量经宿主 re-export 仍可访问。
"""
from __future__ import annotations

from .logging_util import get_module_logger

# 全族共享同一 logger 实例（拆到域模块后 patch 宿主 logger 仍需命中）。
logger = get_module_logger("private_image")

# 模块级常量：拆分时留在宿主族（经 private_image 模块 re-export 访问）。
PREPARED_IMAGE_MAX_AGE_SECONDS = 30 * 60
CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS = 5 * 60


class _PrivateImageHostRef:
    """延迟引用宿主 private_image 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import private_image as _host_module

        return getattr(_host_module, name)


_private_image_host = _PrivateImageHostRef()
