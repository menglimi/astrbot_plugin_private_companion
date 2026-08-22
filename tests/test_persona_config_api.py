from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


def _harness(root: str):
    from tests.test_multi_persona_isolation import _plugin_harness

    plugin = _plugin_harness(root)
    plugin._persona_settings_migration_status = {}
    plugin.multi_persona_primary_id = "main"
    plugin.multi_persona_ids = ["main", "alt"]
    plugin.config["multi_persona_ids"] = ["main", "alt"]
    plugin.config["basic_config"] = {
        "bot_name": "主人格",
        "quiet_hours": "23:00-08:30",
        "enable_proactive_burst": True,
    }
    plugin.bot_name = "主人格"
    plugin.quiet_hours = "23:00-08:30"
    plugin.enable_proactive_burst = True
    return plugin


async def _call(api, app, path, method, payload=None):
    async with app.test_request_context(path, method=method, json=payload):
        route = path.split("?", 1)[0]
        if route.endswith("/create"):
            return await api.create_persona_config()
        if route == "/settings/update":
            return await api.update_settings()
        if route.endswith("/config-state"):
            return await api.get_persona_config_state()
        if route.endswith("/settings/update"):
            return await api.update_persona_settings()
        if route.endswith("detach-preview"):
            return await api.preview_persona_config_detach()
        if route.endswith("detach-apply"):
            return await api.apply_persona_config_detach()
        if route.endswith("/window-bindings"):
            return await api.persona_window_bindings()
        return await api.delete_persona_window_binding()


def test_persona_config_api_lifecycle_and_sparse_following():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            api = PrivateCompanionPageApi(plugin)
            app = Quart(__name__)

            created = await _call(
                api,
                app,
                "/persona/config/create",
                "POST",
                {"persona_id": "alt", "bot_name": "次人格", "mode": "follow_primary"},
            )
            assert created["success"]
            state = await _call(api, app, "/persona/config-state?persona_id=alt", "GET")
            assert state["data"]["raw_settings"] == {"bot_name": "次人格"}
            revision = state["data"]["revision"]

            updated = await _call(
                api,
                app,
                "/persona/settings/update",
                "POST",
                {
                    "persona_id": "alt",
                    "expected_revision": revision,
                    "changes": {"quiet_hours": "01:00-09:00", "enable_proactive_burst": False},
                },
            )
            assert updated["success"]
            assert updated["data"]["raw_settings"]["enable_proactive_burst"] is False

            preview = await _call(
                api,
                app,
                "/persona/config/detach-preview",
                "POST",
                {"persona_id": "alt"},
            )
            assert preview["success"]
            assert preview["data"]["materialized_count"] > 2

    asyncio.run(run())


def test_multi_persona_enable_applies_primary_before_toggle_even_when_payload_toggle_is_first():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.multi_persona_primary_id = ""
            plugin.multi_persona_ids = []
            plugin.plugin_specific_persona_id = ""
            plugin._single_mode_plugin_specific_persona_id = ""
            plugin.config["multi_persona_ids"] = []
            api = PrivateCompanionPageApi(plugin)
            api._save_config_if_possible = AsyncMock(return_value=True)
            app = Quart(__name__)
            response = await _call(
                api,
                app,
                "/settings/update",
                "POST",
                {
                    "settings": {
                        "enable_multi_persona_mode": True,
                        "multi_persona_primary_id": "main",
                        "multi_persona_ids": ["main", "alt"],
                    },
                },
            )
            assert response["success"] is True
            assert plugin.enable_multi_persona_mode is True
            assert plugin.multi_persona_primary_id == "main"
            assert plugin.multi_persona_ids == ["main", "alt"]

    asyncio.run(run())


def test_window_binding_api_delete_restores_auto_recognition():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            api = PrivateCompanionPageApi(plugin)
            app = Quart(__name__)
            created = await _call(
                api,
                app,
                "/persona/window-bindings",
                "POST",
                {"window_key": "QBot4012710235:GroupMessage:group-1", "persona_id": "alt"},
            )
            assert created["success"]
            revision = created["data"]["revision"]
            deleted = await _call(
                api,
                app,
                "/persona/window-bindings/delete",
                "POST",
                {"window_key": "QBot4012710235:GroupMessage:group-1", "expected_revision": revision},
            )
            assert deleted["success"]
            assert deleted["data"]["auto_recognition_restored"] is True
            assert deleted["data"]["bindings"] == {}

    asyncio.run(run())


def test_existing_sparse_profile_is_repaired_without_materializing_old_keys():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        profile_path = Path(root) / "persona_profiles" / "alt.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            '{"users": {"legacy": {"name": "保留"}}, "daily_plan": {"old": true}}',
            encoding="utf-8",
        )
        status = plugin._migrate_persona_profiles_sync()
        assert status["ok"] is True
        profile = plugin._ensure_persona_profile("alt")
        assert profile["users"]["legacy"]["name"] == "保留"
        assert profile["persona_settings"] == {"bot_name": "alt"}
        assert "quiet_hours" not in profile["persona_settings"]


def test_runtime_resolver_reads_sparse_and_explicit_falsy_values():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        plugin.max_daily_messages = 8
        profile = plugin._ensure_persona_profile("alt")
        profile["persona_settings"].update(
            {"quiet_hours": "01:00-09:00", "enable_proactive_burst": False}
        )
        assert plugin.get_persona_setting("quiet_hours", "alt") == "01:00-09:00"
        assert plugin.get_persona_setting("enable_proactive_burst", "alt") is False
        assert plugin.get_persona_setting("max_daily_messages", "alt") == plugin.max_daily_messages


def test_canonical_provider_setting_uses_runtime_attribute_as_primary_fallback():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        plugin.fast_response_provider_id = "primary-fast"
        profile = plugin._ensure_persona_profile("alt")
        assert plugin.get_persona_setting("FAST_RESPONSE_PROVIDER_ID", "alt") == "primary-fast"
        profile["persona_settings"]["FAST_RESPONSE_PROVIDER_ID"] = "persona-fast"
        assert plugin.get_persona_setting("FAST_RESPONSE_PROVIDER_ID", "alt") == "persona-fast"


def test_stale_window_upsert_is_rejected():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            first = await plugin._mutate_persona_window_binding_async(
                action="upsert", window_key="QBot:GroupMessage:one", persona_id="alt"
            )
            assert first["ok"] and first["revision"] == 1
            stale = await plugin._mutate_persona_window_binding_async(
                action="upsert",
                window_key="QBot:GroupMessage:one",
                persona_id="main",
                expected_revision=0,
            )
            assert stale["ok"] is False
            assert stale["status_code"] == 409
            assert plugin._persona_window_bindings()["QBot:GroupMessage:one"] == "alt"

    asyncio.run(run())


def test_startup_migration_backs_up_invalid_profile_and_uses_persona_label():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        plugin.context = SimpleNamespace(
            persona_manager=SimpleNamespace(
                personas=[SimpleNamespace(persona_id="alt", name="次人格显示名")]
            )
        )
        profiles = Path(root) / "persona_profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "alt.json").write_text(
            json.dumps({"users": {"u": {}}, "persona_settings": {}}), encoding="utf-8"
        )
        (profiles / "broken.json").write_text(
            json.dumps({"users": {"u": {}}, "persona_settings": []}), encoding="utf-8"
        )
        status = plugin._migrate_persona_profiles_sync()
        assert status["ok"] is False
        assert Path(status["backups"]["broken"]).is_file()
        migrated = json.loads((profiles / "alt.json").read_text(encoding="utf-8"))
        assert migrated["persona_settings"]["bot_name"] == "次人格显示名"


def test_disabling_multi_persona_rolls_back_default_store_on_write_failure():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        plugin.data_dir = root
        plugin.data_file = str(Path(root) / "companions.json")
        previous = {"users": {"legacy": {"name": "旧数据"}}}
        primary = {"users": {"primary": {"name": "主人格数据"}}}
        plugin._data_default = previous
        plugin._persona_data_profiles["main"] = primary
        writes = 0

        def writer(snapshot):
            nonlocal writes
            writes += 1
            if writes == 1:
                raise OSError("simulated_write_failure")
            Path(plugin.data_file).write_text(json.dumps(snapshot), encoding="utf-8")

        plugin._write_data_snapshot_sync = writer
        try:
            plugin._prepare_multi_persona_transition(False)
        except OSError as exc:
            assert "simulated_write_failure" in str(exc)
        else:
            raise AssertionError("expected transition failure")
        assert plugin._data_default == previous
        assert json.loads(Path(plugin.data_file).read_text(encoding="utf-8")) == previous


def test_enabling_multi_persona_rolls_back_runtime_profile_and_config_on_save_failure():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.multi_persona_primary_id = "main"
            plugin.multi_persona_ids = ["main", "alt"]
            plugin.plugin_specific_persona_id = "main"
            plugin._single_mode_plugin_specific_persona_id = "main"
            plugin.config["enable_multi_persona_mode"] = False
            plugin.config["multi_persona_primary_id"] = "main"
            plugin.config["multi_persona_ids"] = ["main", "alt"]
            plugin.data_file = str(Path(root) / "companions.json")
            plugin._data_default = {"users": {"single": {"name": "单人格"}}}
            Path(plugin.data_file).write_text(
                json.dumps(plugin._data_default), encoding="utf-8"
            )
            config_before = json.loads(json.dumps(plugin.config, ensure_ascii=False))
            api = PrivateCompanionPageApi(plugin)
            api._save_config_if_possible = lambda: asyncio.sleep(0, result=False)
            app = Quart(__name__)

            async with app.test_request_context(
                "/settings/update",
                method="POST",
                json={"features": {"enable_multi_persona_mode": True}},
            ):
                response = await api.update_settings()

            assert response[0]["success"] is False if isinstance(response, tuple) else response["success"] is False
            assert plugin.enable_multi_persona_mode is False
            assert plugin.plugin_specific_persona_id == "main"
            assert plugin.config == config_before
            assert plugin._persona_data_profiles == {}
            assert not plugin._persona_profile_path("main").exists()
            assert json.loads(Path(plugin.data_file).read_text(encoding="utf-8")) == plugin._data_default

    asyncio.run(run())


def test_disabling_multi_persona_rolls_back_written_legacy_data_on_config_save_failure():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin.data_file = str(Path(root) / "companions.json")
            legacy = {"users": {"legacy": {"name": "旧单人格"}}}
            primary = {"users": {"primary": {"name": "主人格"}}}
            plugin._data_default = legacy
            plugin._persona_data_profiles["main"] = primary
            plugin._save_persona_profile_sync("main", primary)
            Path(plugin.data_file).write_text(json.dumps(legacy), encoding="utf-8")
            config_before = json.loads(json.dumps(plugin.config, ensure_ascii=False))
            api = PrivateCompanionPageApi(plugin)
            api._save_config_if_possible = lambda: asyncio.sleep(0, result=False)
            app = Quart(__name__)

            async with app.test_request_context(
                "/settings/update",
                method="POST",
                json={"features": {"enable_multi_persona_mode": False}},
            ):
                response = await api.update_settings()

            assert response[0]["success"] is False if isinstance(response, tuple) else response["success"] is False
            assert plugin.enable_multi_persona_mode is True
            assert plugin.config == config_before
            assert plugin._data_default == legacy
            assert plugin._persona_data_profiles["main"] == primary
            assert json.loads(Path(plugin.data_file).read_text(encoding="utf-8")) == legacy

    asyncio.run(run())


def test_persona_setting_hot_apply_is_target_scoped():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            profile = plugin._ensure_persona_profile("alt")
            profile["runtime_cache"] = {"stale": True}
            profile["persona_settings"]["bot_name"] = "次人格"
            seen: list[tuple[str, str]] = []

            plugin._refresh_expression_voice_profile = lambda: seen.append(
                ("expression", plugin._active_persona_scope())
            )
            plugin._import_worldbook_entries_from_sources = lambda: seen.append(
                ("worldbook", plugin._active_persona_scope())
            ) or False

            async def kick():
                seen.append(("scheduler", plugin._active_persona_scope()))

            plugin._kick_proactive_loop_once = kick
            revision = int(profile.get("persona_settings_revision") or 0)
            result = await plugin._update_persona_settings_async(
                "alt",
                changes={
                    "FAST_RESPONSE_PROVIDER_ID": "persona-fast",
                    "default_style": "独立风格",
                    "worldbook_config_paths": "worldbook.json",
                    "max_daily_messages": 2,
                },
                follow_primary_keys=[],
                expected_revision=revision,
            )
            await asyncio.sleep(0)

            assert result["ok"] is True
            assert "runtime_cache" not in plugin._ensure_persona_profile("alt")
            assert ("expression", "alt") in seen
            assert ("worldbook", "alt") in seen
            assert ("scheduler", "alt") in seen
            assert plugin._active_persona_scope() == ""

    asyncio.run(run())
