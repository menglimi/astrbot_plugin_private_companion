# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from astrbot.api import logger


def migrate_json_to_backend_if_needed(backend: Any, json_backend: Any, default_data: dict[str, Any]) -> dict[str, Any]:
    if backend.exists():
        return backend.load_store()
    if json_backend.exists():
        payload = json_backend.load_store()
        try:
            backend.initialize_empty_store(deepcopy(payload))
            logger.info("[PrivateCompanion] 已将 JSON 数据迁移到 %s 后端", backend.backend_name())
            return backend.load_store()
        except Exception as exc:
            logger.warning("[PrivateCompanion] 迁移 JSON 到 %s 失败: %s", backend.backend_name(), exc)
            return payload
    backend.initialize_empty_store(deepcopy(default_data))
    return backend.load_store()
