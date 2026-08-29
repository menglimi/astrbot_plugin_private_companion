# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _Config:
    def __init__(self, data: dict[str, Any], *, fail_calls: set[int]) -> None:
        self.data = data
        self.fail_calls = set(fail_calls)
        self.calls = 0
        self.persisted = deepcopy(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save_config(self) -> None:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise OSError("fault-injected config save")
        self.persisted = deepcopy(self.data)


class _Plugin:
    def __init__(
        self,
        *,
        fail_config_calls: set[int],
        fail_data_save: bool = False,
    ) -> None:
        self.config = _Config(
            {"basic_config": {"group_access_mode": "whitelist"}},
            fail_calls=fail_config_calls,
        )
        self.group_access_mode = "whitelist"
        self.data = {"can_do": {"old": True}, "sentinel": {"value": 1}}
        self.persisted_data = deepcopy(self.data)
        self._data_lock = asyncio.Lock()
        self.fail_data_save = fail_data_save

    @staticmethod
    def _validate_save_request(
        _sections: set[str],
        _deleted: tuple[Any, ...],
        _full_scope: Any,
    ) -> None:
        return None

    def _save_data_sync(self, *, sections: set[str]) -> None:
        assert sections
        if self.fail_data_save:
            self.fail_data_save = False
            raise OSError("fault-injected data save")
        self.persisted_data = deepcopy(self.data)

    def _write_data_snapshot_sync(self, snapshot: dict[str, Any]) -> None:
        self.persisted_data = deepcopy(snapshot)


def _api(plugin: _Plugin) -> PrivateCompanionPageApi:
    api = PrivateCompanionPageApi(plugin)

    async def build_backup(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "kind": "private_companion_config_backup",
            "plugin": "astrbot_plugin_private_companion",
            "settings": {"group_access_mode": plugin.group_access_mode},
            "data": deepcopy(plugin.data),
        }

    api._build_migration_package = build_backup  # type: ignore[method-assign]
    api._write_migration_backup = lambda _package: "backup.json"  # type: ignore[method-assign]
    return api


def _normalized(*, with_data: bool) -> dict[str, Any]:
    return {
        "settings": {"group_access_mode": "blacklist"},
        "features": {},
        "providers": {},
        "data": {"can_do": {"new": True}} if with_data else {},
    }


def test_config_save_fault_rolls_back_runtime_config_and_data_snapshot() -> None:
    async def scenario() -> None:
        plugin = _Plugin(fail_config_calls={1})
        api = _api(plugin)
        before_config = deepcopy(plugin.config.data)
        before_data = deepcopy(plugin.data)

        with pytest.raises(RuntimeError, match="配置导入持久化失败"):
            await api._apply_migration_normalized(
                _normalized(with_data=False),
                mode="merge",
                conflict="use_backup",
            )

        assert plugin.config.data == before_config
        assert plugin.config.persisted == before_config
        assert plugin.group_access_mode == "whitelist"
        assert plugin.data == before_data
        assert plugin.persisted_data == before_data
        assert plugin.config.calls == 2

    asyncio.run(scenario())


def test_data_save_fault_rolls_back_already_saved_config_and_live_data() -> None:
    async def scenario() -> None:
        plugin = _Plugin(fail_config_calls=set(), fail_data_save=True)
        api = _api(plugin)
        before_config = deepcopy(plugin.config.data)
        before_data = deepcopy(plugin.data)

        with pytest.raises(OSError, match="fault-injected data save"):
            await api._apply_migration_normalized(
                _normalized(with_data=True),
                mode="replace",
                conflict="use_backup",
            )

        assert plugin.config.data == before_config
        assert plugin.config.persisted == before_config
        assert plugin.group_access_mode == "whitelist"
        assert plugin.data == before_data
        assert plugin.persisted_data == before_data
        assert plugin.config.calls == 2

    asyncio.run(scenario())
