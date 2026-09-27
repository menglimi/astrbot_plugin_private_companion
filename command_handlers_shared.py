# -*- coding: utf-8 -*-
"""command_handlers 域家族共享件。

被拆分出的域 mixin 与宿主 command_handlers.py 共同依赖的模块级名字放这里，
保证全族共享同一份绑定：

* ``logger`` —— 全族共享同一 logger 实例（宿主 re-export，各域子模块也从
  本模块取），使既有 ``patch("...command_handlers.logger.xxx")`` 对已搬走的
  方法依然生效。
* ``_PHOTO_REFERENCE_SUFFIXES`` —— 参考图文件后缀集合，跨域共用时以本模块为
  唯一定义处，宿主 re-export 后仍可经 ``command_handlers`` 访问。
* ``_CommandHandlersHostRef`` —— 宿主延迟代理。域模块内若需要引用宿主模块的
  全局名字，用 ``_command_handlers_host.<name>`` 而非 ``from .command_handlers
  import <name>``：后者是 import 期值绑定，测试对宿主全局的 patch 会失效；
  属性式延迟解析把宿主导入推迟到首次属性访问（运行期），既保证 patch 生效，
  也避免「宿主 → 域 → 宿主」的导入环。

本模块处于导入链最上游（不 import 任何同族模块），零导入环。
"""
from __future__ import annotations

from .logging_util import get_module_logger

# 全族共享的 logger 实例: 宿主从本模块 re-export, 各域子模块也从本模块取,
# 保证既有 patch("...command_handlers.logger.xxx") 对已搬走的方法依然生效。
logger = get_module_logger("astrbot_plugin_private_companion.command_handlers")

# 参考图文件后缀集合（原 command_handlers.py 模块级常量）。
_PHOTO_REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class _CommandHandlersHostRef:
    """延迟引用宿主 command_handlers 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import command_handlers as _host_module

        return getattr(_host_module, name)


_command_handlers_host = _CommandHandlersHostRef()
