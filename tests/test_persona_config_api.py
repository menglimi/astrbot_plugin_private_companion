from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
