# -*- coding: utf-8 -*-
"""task_prompt 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 201 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
from .helpers import _MISSING
from .task_prompt_registry import (
    TASK_PROMPT_CONFIG_KEY,
    TASK_PROMPT_GROUPS,
    catalog_task_prompts,
    normalize_task_prompt_overrides,
    validate_task_prompt_override,
)
from copy import deepcopy
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any, Mapping

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiTaskPromptMixin:
    """task_prompt 域（从 PrivateCompanionPageApi 拆出）。"""


    def _task_prompt_overrides(self) -> dict[str, str]:
        """Return only normalized overrides owned by this plugin.

        The runtime attribute is authoritative after a hot update.  Before
        plugin initialization creates that attribute, fall back to the
        persisted configuration value.  The registry filters unknown and
        ``astrbot_*`` conversation keys in either case.
        """
        runtime_value = getattr(self.plugin, TASK_PROMPT_CONFIG_KEY, _MISSING)
        raw = runtime_value
        if raw is _MISSING:
            raw = self._config_get_raw(TASK_PROMPT_CONFIG_KEY, {})
        try:
            return normalize_task_prompt_overrides(raw)
        except Exception:
            return {}

    def _task_prompt_payload(
        self,
        overrides: Any = None,
        *,
        config_saved: bool | None = None,
        changed: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_task_prompt_overrides(
            self._task_prompt_overrides() if overrides is None else overrides
        )
        rows = catalog_task_prompts(normalized)
        customized_count = sum(1 for row in rows if bool(row.get("customized")))
        payload: dict[str, Any] = {
            "config_key": TASK_PROMPT_CONFIG_KEY,
            "groups": list(TASK_PROMPT_GROUPS),
            "tasks": rows,
            "overrides": normalized,
            "configured_count": len(normalized),
            "customized_count": customized_count,
        }
        if config_saved is not None:
            payload["config_saved"] = bool(config_saved)
        if changed is not None:
            payload["changed"] = list(changed)
        return payload

    async def get_task_prompts(self) -> dict[str, Any]:
        """List every editable prompt used by plugin-internal task models."""
        try:
            return self._ok(self._task_prompt_payload())
        except Exception as exc:
            logger.warning("读取任务模型提示词目录失败: %s", self._single_line(exc, 180))
            return self._exception_error("读取任务模型提示词失败")

    @staticmethod
    def _task_prompt_payload_value(raw_value: Any, *, reset: bool = False) -> Any:
        """Extract the accepted editor value from a single update entry."""
        if reset or raw_value is None:
            return ""
        if isinstance(raw_value, Mapping):
            if raw_value.get("reset") is True:
                return ""
            for key in ("custom_prompt", "prompt", "value"):
                if key in raw_value:
                    raw_value = raw_value.get(key)
                    break
        return raw_value

    def _task_prompt_update_entries(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Parse single/batch/reset forms into ``{key: value}`` updates."""
        entries: dict[str, Any] = {}
        reset_all = payload.get("reset_all") is True

        def add(key: Any, value: Any, *, reset: bool = False) -> None:
            clean_key = self._single_line(key, 120).strip().lower()
            if not clean_key:
                raise ValueError("缺少任务模型提示词标识")
            entries[clean_key] = self._task_prompt_payload_value(value, reset=reset)

        for source_name in ("overrides", "updates", "items"):
            source = payload.get(source_name)
            if isinstance(source, Mapping):
                for key, value in source.items():
                    if isinstance(value, Mapping):
                        add(key, value, reset=value.get("reset") is True)
                    else:
                        add(key, value)
            elif isinstance(source, list):
                for item in source:
                    if not isinstance(item, Mapping):
                        raise ValueError("批量任务提示词条目格式无效")
                    key = item.get("task_key") or item.get("override_key") or item.get("key")
                    add(key, item, reset=item.get("reset") is True)

        if payload.get("task_key") is not None or payload.get("key") is not None:
            key = payload.get("task_key") or payload.get("key")
            add(key, payload.get("prompt", payload.get("custom_prompt", payload.get("value"))), reset=payload.get("reset") is True)

        reset_keys = payload.get("reset_keys") or payload.get("remove_keys") or []
        if isinstance(reset_keys, (str, bytes)):
            reset_keys = [reset_keys]
        if not isinstance(reset_keys, list):
            raise ValueError("reset_keys 必须是任务标识列表")
        for key in reset_keys:
            add(key, "", reset=True)
        if payload.get("reset") is True and not entries:
            key = payload.get("override_key")
            if key is not None:
                add(key, "", reset=True)
        return entries, reset_all

    def _task_prompt_update_lock(self) -> asyncio.Lock:
        """Serialize prompt persistence and rollback for one plugin instance."""
        lock = getattr(self.plugin, "_task_prompt_update_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self.plugin, "_task_prompt_update_lock", lock)
        return lock

    async def update_task_prompts(self) -> dict[str, Any]:
        """Save plugin-internal task prompt additions without touching chat prompts."""
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, Mapping):
            return self._error("任务提示词请求格式无效")
        async with self._task_prompt_update_lock():
            return await self._update_task_prompts_unlocked(payload)

    async def _update_task_prompts_unlocked(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one validated update while the instance update lock is held."""
        try:
            entries, reset_all = self._task_prompt_update_entries(payload)
            current = self._task_prompt_overrides()
            replace_all = payload.get("replace") is True or str(payload.get("mode") or "").strip().lower() == "replace"
            updated = {} if replace_all or reset_all else dict(current)
            changed: set[str] = set()
            if reset_all:
                changed.update(current)
            for key, raw_value in entries.items():
                value = self._task_prompt_payload_value(raw_value)
                try:
                    normalized_value = validate_task_prompt_override(key, value)
                except ValueError as exc:
                    raise ValueError(f"{key}：{exc}") from exc
                if normalized_value:
                    if updated.get(key) != normalized_value:
                        changed.add(key)
                    updated[key] = normalized_value
                elif key in updated:
                    updated.pop(key, None)
                    changed.add(key)
            if replace_all:
                # In replacement mode, entries omitted by the client are
                # intentionally removed; report those keys as changed too.
                changed.update(set(current) - set(updated))
            if not changed and updated == current:
                return self._ok(self._task_prompt_payload(current, config_saved=True, changed=[]))

            before_runtime = getattr(self.plugin, TASK_PROMPT_CONFIG_KEY, _MISSING)
            before_config = self._config_get_raw(TASK_PROMPT_CONFIG_KEY, _MISSING)

            def rollback_task_prompt_state() -> None:
                """Restore both persisted and runtime values after a failed save."""
                if before_config is _MISSING:
                    config = getattr(self.plugin, "config", None)
                    try:
                        if isinstance(config, dict):
                            config.pop(TASK_PROMPT_CONFIG_KEY, None)
                        elif config is not None:
                            del config[TASK_PROMPT_CONFIG_KEY]
                    except Exception:
                        pass
                else:
                    try:
                        self._set_config_value(TASK_PROMPT_CONFIG_KEY, deepcopy(before_config))
                    except Exception:
                        pass
                try:
                    if before_runtime is _MISSING:
                        delattr(self.plugin, TASK_PROMPT_CONFIG_KEY)
                    else:
                        setattr(self.plugin, TASK_PROMPT_CONFIG_KEY, before_runtime)
                except Exception:
                    pass

            # The schema intentionally declares this hidden compatibility
            # field as ``text``. Persist JSON text so AstrBot's config
            # validator accepts the value across reloads; the runtime
            # attribute remains a normalized mapping for fast lookups.
            persisted_overrides = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
            self._set_config_value(TASK_PROMPT_CONFIG_KEY, persisted_overrides)
            setattr(self.plugin, TASK_PROMPT_CONFIG_KEY, deepcopy(updated))
            try:
                config_saved = await self._save_config_if_possible()
            except Exception:
                rollback_task_prompt_state()
                raise
            if not config_saved:
                rollback_task_prompt_state()
                return self._error("保存任务模型提示词失败", status_code=500)
            return self._ok(
                self._task_prompt_payload(
                    updated,
                    config_saved=config_saved,
                    changed=sorted(changed),
                )
            )
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.warning("保存任务模型提示词失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("保存任务模型提示词失败")
