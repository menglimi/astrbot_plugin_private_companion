# -*- coding: utf-8 -*-
"""proactive_message_photo_generation 域家族共享件。

被拆分出的 10 个域 mixin 与宿主 `proactive_message_photo_generation.py` 共同依赖的
模块级名字放这里，保证全族共享同一份绑定：

* ``logger`` —— 全族共享**同一 logger 实例**（宿主 re-export，各域子模块也从本
  模块取），使既有 ``patch("...proactive_message_photo_generation.logger.info")``
  对已搬走的方法依然生效。
* ``_now_ts`` —— 转发宿主 `proactive_message._now_ts` 的 shim，保留可 patch 性。
* ``_PHOTO_GENERATION_TRACE_FILE_LOCK`` —— trace 文件写入互斥锁。
* ``PhotoGenerationResult`` / ``_ExternalPhotoGenerationOutcome`` —— 出图结果
  dataclass；前者是公开契约类型，宿主与 `proactive_message` 均需可导入。

本模块处于导入链最上游（不 import 任何同族模块），零导入环。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .helpers import _path_text
from .logging_util import get_module_logger

# 全族共享的 logger 实例：宿主从本模块 re-export，各域子模块也从本模块取，
# 保证既有 patch("...proactive_message_photo_generation.logger.xxx") 依然生效。
logger = get_module_logger(
    "astrbot_plugin_private_companion.proactive_message_photo_generation"
)

_PHOTO_GENERATION_TRACE_FILE_LOCK = threading.Lock()


def _now_ts(*args, **kwargs):
    from . import proactive_message as _host

    return getattr(_host, "_now_ts")(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class PhotoGenerationResult:
    backend: str = ""
    image_path: str = ""
    note: str = ""
    trace_id: str = ""
    reference_selected_path: str = ""
    reference_used: bool = False
    reference_id: str = ""
    reference_kind: str = ""
    reference_roles: tuple[str, ...] = ()
    wardrobe_mode: str = ""
    wardrobe_category: str = ""
    outfit_locked: bool = False
    daily_outfit_removed: bool = False
    preset_names: tuple[str, ...] = ()
    preset_hint: str = ""
    preset_source: str = ""
    suggestion_status: str = ""
    prompt_hash: str = ""
    prompt_path: str = ""
    reference_requested_roles: tuple[str, ...] = ()
    reference_excluded_roles: tuple[str, ...] = ()
    continuity_mode: str = "ambiguous"
    reference_confidence: float = 0.0
    reference_plan: tuple[dict[str, Any], ...] = ()
    reference_fulfilled_roles: tuple[str, ...] = ()
    reference_missing_roles: tuple[str, ...] = ()
    reference_fallback_message: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    @property
    def success(self) -> bool:
        path = _path_text(self.image_path, 1000)
        if not path:
            return False
        try:
            return Path(path).is_file()
        except (OSError, ValueError):
            return False

    def as_legacy_tuple(self) -> tuple[str, str, str]:
        return self.backend, self.image_path, self.note


@dataclass(frozen=True, slots=True, eq=False)
class _ExternalPhotoGenerationOutcome:
    """Internal result state that preserves the legacy ``(path, note)`` API."""

    image_path: str = ""
    note: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    def as_legacy_tuple(self) -> tuple[str, str]:
        return self.image_path, self.note

    def __iter__(self):
        yield self.image_path
        yield self.note

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return self.as_legacy_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ExternalPhotoGenerationOutcome):
            return (
                self.image_path,
                self.note,
                self.generation_completed,
                self.failure_stage,
            ) == (
                other.image_path,
                other.note,
                other.generation_completed,
                other.failure_stage,
            )
        if isinstance(other, (tuple, list)) and len(other) == 2:
            return self.as_legacy_tuple() == (other[0], other[1])
        return NotImplemented
