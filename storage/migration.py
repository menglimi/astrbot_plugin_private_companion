# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any


from .sqlite_backend import SqliteStoreNotInitializedError
from ..logging_util import get_module_logger

logger = get_module_logger(__name__)


def _initialize_backend_from_payload(
    backend: Any,
    payload: dict[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    try:
        backend.initialize_empty_store(deepcopy(payload))
        logger.info(
            "已%s到 %s 后端",
            action,
            backend.backend_name(),
        )
        return backend.load_store()
    except Exception as exc:
        logger.warning(
            "%s到 %s 后端失败,本次继续使用来源数据: %s",
            action,
            backend.backend_name(),
            exc,
        )
        return payload


def migrate_json_to_backend_if_needed(backend: Any, json_backend: Any, default_data: dict[str, Any]) -> dict[str, Any]:
    if backend.exists():
        try:
            return backend.load_store()
        except SqliteStoreNotInitializedError:
            if json_backend.exists():
                payload = json_backend.load_store()
                return _initialize_backend_from_payload(
                    backend,
                    payload,
                    action="从 JSON 恢复未完成迁移的数据",
                )
            # An existing SQLite file is installation evidence, even when its
            # schema is incomplete.  Without a JSON recovery source we cannot
            # distinguish an interrupted migration from deliberate data loss,
            # so never replace it with a valid-looking empty store.
            raise
    if json_backend.exists():
        payload = json_backend.load_store()
        return _initialize_backend_from_payload(
            backend,
            payload,
            action="将 JSON 数据迁移",
        )
    return _initialize_backend_from_payload(
        backend,
        default_data,
        action="初始化空存储",
    )
