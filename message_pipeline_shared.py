# -*- coding: utf-8 -*-
"""message_pipeline 域家族共享工具。

拆分出域模块后，宿主与全部域模块必须绑定**同一个** logger 实例，否则
``patch("...message_pipeline.logger.<level>")`` 只对宿主单侧生效。

本模块不 import 族内任何模块，是族内唯一的无环叶子，因此族内每个模块都可以
安全地 ``from .message_pipeline_shared import logger``（不会成环）。
"""
from __future__ import annotations

from .logging_util import get_module_logger

# 全族共享同一 logger 实例（与 private_image_shared.py 同一约定；模块名保持
# "message_pipeline"，日志标签仍是「消息管线」，与拆分前一致）。
logger = get_module_logger("message_pipeline")
