# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ACCESS_TOKEN = "standalone-private-token-0123456789"


def _api() -> PrivateCompanionPageApi:
    plugin = SimpleNamespace(
        config={
            "basic_config": {
                "enable_standalone_webui": True,
                "standalone_webui_access_token": ACCESS_TOKEN,
            }
        },
        enable_standalone_webui=True,
        standalone_webui_access_token=ACCESS_TOKEN,
        data={},
        _data_lock=asyncio.Lock(),
    )
    return PrivateCompanionPageApi(plugin)


def test_access_token_is_not_projected_to_panel_runtime_settings() -> None:
    api = _api()

    settings = api._runtime_settings()

    assert "standalone_webui_access_token" not in settings
    assert ACCESS_TOKEN not in repr(settings)
    assert settings["enable_standalone_webui"] is True


def test_access_token_is_not_exportable_or_page_writable() -> None:
    api = _api()

    assert "standalone_webui_access_token" not in api._allowed_setting_keys()
    package = asyncio.run(
        api._build_migration_package({"basic", "sensitive"})
    )
    assert "standalone_webui_access_token" not in package["settings"]
    assert "standalone_webui_access_token" not in repr(package)
    assert ACCESS_TOKEN not in repr(package)


def test_access_token_from_import_package_is_ignored() -> None:
    api = _api()
    package = {
        "kind": "private_companion_config_backup",
        "plugin": "astrbot_plugin_private_companion",
        "settings": {"standalone_webui_access_token": "attacker-controlled-token"},
    }

    normalized = api._normalize_migration_package(package)

    assert "standalone_webui_access_token" not in normalized["settings"]
    assert "standalone_webui_access_token" in normalized["ignored"]
