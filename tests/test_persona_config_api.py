from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.persona_config import load_scope_manifest


def _harness(root: str):
    from tests.test_multi_persona_isolation import _plugin_harness

    plugin = _plugin_harness(root)
    plugin._persona_data_profiles.pop("alt", None)
    plugin._persona_settings_migration_status = {}
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
        raise AssertionError(f"unsupported test route: {route}")


def _persisted_persona_profile(plugin, persona_id: str) -> dict:
    handle = plugin._load_secondary_persona_store_sync(persona_id)
    return handle.manager.backend.load_store()


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
                    "changes": {
                        "quiet_hours": "01:00-09:00",
                        "enable_proactive_burst": False,
                        "segmented_proactive_content_cleanup_words": "。\n<newline>",
                    },
                },
            )
            assert updated["success"]
            assert updated["data"]["raw_settings"]["enable_proactive_burst"] is False
            assert updated["data"]["raw_settings"]["segmented_proactive_content_cleanup_words"] == ["。", "\n"]
            assert plugin._persona_profile_db_path("alt").is_file()
            assert not plugin._persona_profile_path("alt").exists()
            saved_profile = _persisted_persona_profile(plugin, "alt")
            assert saved_profile["persona_settings"]["segmented_proactive_content_cleanup_words"] == ["。", "\n"]
            reloaded = await _call(api, app, "/persona/config-state?persona_id=alt", "GET")
            assert reloaded["data"]["raw_settings"]["segmented_proactive_content_cleanup_words"] == ["。", "\n"]

            preview = await _call(
                api,
                app,
                "/persona/config/detach-preview",
                "POST",
                {"persona_id": "alt"},
            )
            assert preview["success"]
            preview_data = preview["data"]
            existing_keys = set(preview_data["existing_override_keys"])
            follow_keys = set(preview_data["follow_primary_keys"])
            assert {"bot_name", "quiet_hours", "enable_proactive_burst"} <= existing_keys
            assert existing_keys.isdisjoint(follow_keys)
            assert preview_data["existing_override_count"] == len(existing_keys)
            assert preview_data["follow_primary_count"] == len(follow_keys)
            assert preview_data["final_settings_count"] == len(existing_keys | follow_keys)
            assert preview_data["materialized_count"] == preview_data["final_settings_count"]
            assert preview_data["missing_keys"] == preview_data["follow_primary_keys"]
            assert preview_data["follow_primary_count"] > 0
            status = plugin._multi_persona_status()
            assert status["enabled_ids"] == ["main", "alt"]
            assert status["configured_profiles"] == ["alt"]
            assert status["profiles"] == ["main", "alt"]
            assert status["profile_labels"] == {"main": "主人格", "alt": "次人格"}

    asyncio.run(run())


def test_saved_topology_without_config_is_not_editable_detachable_or_schedulable():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            alt_path = plugin._persona_profile_path("alt")

            status = plugin._multi_persona_status()
            assert status["enabled_ids"] == ["main", "alt"]
            assert status["configured_profiles"] == []
            assert status["profiles"] == ["main"]
            assert plugin._scheduler_persona_ids() == ["main"]
            assert plugin._activate_persona_id("alt") is None

            preview = plugin._persona_detach_preview("alt")
            assert preview["ok"] is False
            assert preview["code"] == "persona_config_missing"
            assert alt_path.exists() is False

            try:
                plugin._persona_config_state("alt")
            except ValueError as exc:
                assert "尚未创建独立配置" in str(exc)
            else:
                raise AssertionError("topology-only persona must not expose config state")
            assert alt_path.exists() is False

    asyncio.run(run())


def test_persona_api_preserves_structured_assets_and_spaced_vent_targets():
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
            structured_asset = {
                "id": "identity-1",
                "role": "identity",
                "file": "identity.png",
                "sha256": "a" * 64,
            }
            reaction_asset = {
                "id": "smile-1",
                "file": "smile.png",
                "sha256": "b" * 64,
                "tags": ["开心"],
                "meme_only": True,
            }

            updated = await _call(
                api,
                app,
                "/persona/settings/update",
                "POST",
                {
                    "persona_id": "alt",
                    "expected_revision": created["data"]["revision"],
                    "changes": {
                        "photo_structured_reference_assets": [structured_asset],
                        "owned_reaction_assets": [reaction_asset],
                        "relationship_boundary_vent_targets": "小 林\n姐姐",
                    },
                },
            )

            assert updated["success"]
            raw = updated["data"]["raw_settings"]
            assert raw["photo_structured_reference_assets"] == [structured_asset]
            assert raw["owned_reaction_assets"] == [reaction_asset]
            assert raw["relationship_boundary_vent_targets"] == ["小 林", "姐姐"]
            saved = _persisted_persona_profile(plugin, "alt")
            assert saved["persona_settings"]["photo_structured_reference_assets"] == [structured_asset]
            assert saved["persona_settings"]["owned_reaction_assets"] == [reaction_asset]
            reloaded = await _call(api, app, "/persona/config-state?persona_id=alt", "GET")
            assert reloaded["data"]["raw_settings"]["relationship_boundary_vent_targets"] == ["小 林", "姐姐"]

    asyncio.run(run())


def test_persona_complex_setting_defaults_keep_manifest_shapes_after_page_normalization():
    with tempfile.TemporaryDirectory() as root:
        api = PrivateCompanionPageApi(_harness(root))
        entries = {
            key: entry
            for key, entry in load_scope_manifest().items()
            if entry.get("scope") == "persona"
            and entry.get("type") in {"list", "template_list", "object"}
        }

        assert entries
        for key, entry in entries.items():
            value = json.loads(json.dumps(entry.get("default"), ensure_ascii=False))
            normalized = api._normalize_setting_value(key, value)
            expected_type = dict if entry.get("type") == "object" else list
            assert isinstance(normalized, expected_type), key


def test_persona_data_migration_requires_enabled_profiles_with_configuration():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            result = await plugin._migrate_persona_profile_async("main", "alt", [])

            assert result["ok"] is False
            assert result["code"] == "persona_migration_target_config_missing"
            assert not plugin._persona_profile_path("alt").exists()

            plugin.config["multi_persona_ids"] = ["main"]
            plugin.multi_persona_ids = ["main"]
            result = await plugin._migrate_persona_profile_async("main", "alt", [])
            assert result["ok"] is False
            assert result["code"] == "persona_migration_target_not_enabled"
            assert not plugin._persona_profile_path("alt").exists()

    asyncio.run(run())


def test_revision_zero_empty_shell_from_old_preview_is_not_a_created_config():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        profile = plugin._new_store()
        profile["persona_settings"] = {"bot_name": "alt"}
        profile["persona_settings_schema_version"] = 1
        profile["persona_settings_revision"] = 0
        plugin._save_persona_profile_sync("alt", profile)
        plugin._persona_data_profiles.clear()

        assert plugin._persona_config_exists("alt") is False
        assert plugin._multi_persona_status()["configured_profiles"] == []
        assert plugin._persona_detach_preview("alt")["code"] == "persona_config_missing"


def test_revision_zero_legacy_profile_with_life_data_remains_compatible():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        profile = plugin._new_store()
        profile["users"] = {"legacy": {"name": "旧用户"}}
        profile["persona_settings"] = {"bot_name": "alt"}
        profile["persona_settings_schema_version"] = 1
        profile["persona_settings_revision"] = 0
        plugin._save_persona_profile_sync("alt", profile)
        plugin._persona_data_profiles.clear()

        assert plugin._persona_config_exists("alt") is True
        assert plugin._multi_persona_status()["configured_profiles"] == ["alt"]


def test_create_requires_saved_topology_and_copy_source_must_be_distinct_and_configured():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin.config["multi_persona_ids"] = ["main", "alt", "work"]
            plugin.multi_persona_ids = ["main", "alt", "work"]

            outside = await plugin._create_persona_config_async(
                "outside", bot_name="Outside", mode="follow_primary"
            )
            assert outside["ok"] is False
            assert outside["code"] == "persona_config_target_not_enabled"
            assert plugin._persona_profile_path("outside").exists() is False

            same = await plugin._create_persona_config_async(
                "alt", bot_name="Alt", mode="copy", source_persona_id="alt"
            )
            assert same["ok"] is False
            assert "不同的来源人格" in same["message"]
            assert plugin._persona_profile_path("alt").exists() is False

            missing_source = await plugin._create_persona_config_async(
                "work", bot_name="Work", mode="copy", source_persona_id="alt"
            )
            assert missing_source["ok"] is False
            assert "尚未创建" in missing_source["message"]
            assert plugin._persona_profile_path("work").exists() is False

            created = await plugin._create_persona_config_async(
                "alt", bot_name="Alt", mode="follow_primary"
            )
            assert created["ok"] is True
            copied = await plugin._create_persona_config_async(
                "work", bot_name="Work", mode="copy", source_persona_id="alt"
            )
            assert copied["ok"] is True

    asyncio.run(run())


def test_multi_persona_enable_applies_primary_before_toggle_even_when_payload_toggle_is_first():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.multi_persona_ids = []
            plugin.plugin_specific_persona_id = ""
            plugin.config["multi_persona_ids"] = []
            plugin.context = SimpleNamespace(
                persona_manager=SimpleNamespace(
                    personas_v3=[{"name": "main", "prompt": "MAIN"}],
                    get_persona_v3_by_id=lambda persona_id: (
                        {"name": "main", "prompt": "MAIN"}
                        if persona_id == "main"
                        else None
                    ),
                )
            )
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
                        "plugin_specific_persona_id": "main",
                        "multi_persona_ids": ["main", "alt"],
                    },
                },
            )
            assert response["success"] is True
            assert plugin.enable_multi_persona_mode is True
            assert plugin.plugin_specific_persona_id == "main"
            assert not hasattr(plugin, "multi_persona_primary_id")
            assert plugin.multi_persona_ids == ["main", "alt"]

    asyncio.run(run())


def test_window_binding_api_is_retired():
    with tempfile.TemporaryDirectory() as root:
        api = PrivateCompanionPageApi(_harness(root))
        assert not hasattr(api, "persona_window_bindings")
        assert not hasattr(api, "delete_persona_window_binding")
        assert not hasattr(api, "switch_persona")


def test_plugin_persona_routing_endpoints_are_not_registered():
    with tempfile.TemporaryDirectory() as root:
        api = PrivateCompanionPageApi(_harness(root))
        paths = {path for path, _handler, _methods, _description in api.route_bindings()}
        assert "/persona/switch" not in paths
        assert "/persona/window-bindings" not in paths
        assert "/persona/window-bindings/delete" not in paths


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
        assert not profile_path.exists()
        assert plugin._persona_profile_db_path("alt").is_file()
        profile = plugin._ensure_persona_profile("alt")
        assert profile["users"]["legacy"]["name"] == "保留"
        assert profile["persona_settings"] == {
            "bot_name": "alt",
            "enable_group_bot_name_wakeup": True,
            "enable_qq_official_segmented_reply": False,
            "intercept_astrbot_group_context": True,
            "group_scene_recent_max_chars": 4000,
            "enable_llm_controlled_segmenting": False,
            "enable_segmented_plugin_rules": True,
            "enable_user_requested_photo_generation": True,
        }
        assert "quiet_hours" not in profile["persona_settings"]


def test_schema_migration_failure_keeps_legacy_persona_json():
    with tempfile.TemporaryDirectory() as root:
        plugin = _harness(root)
        profiles = Path(root) / "persona_profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        legacy = profiles / "alt.json"
        legacy.write_text(
            json.dumps(
                {
                    "users": {"u": {"name": "保留"}},
                    "persona_settings": {
                        "bot_name": "alt",
                        "persona_settings_schema_version": 999,
                    },
                    "persona_settings_schema_version": 999,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        status = plugin._migrate_persona_profiles_sync()

        assert status["ok"] is False
        assert "alt" in status["degraded"]
        assert legacy.exists()
        assert not plugin._persona_profile_db_path("alt").exists()


def test_multi_persona_transition_rollback_restores_sqlite_profiles():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            plugin._ensure_persona_profile("alt")["rollback_marker"] = "before"
            plugin._save_persona_profile_sync("alt")
            api = PrivateCompanionPageApi(plugin)
            snapshot = api._multi_persona_transition_snapshot()

            plugin._ensure_persona_profile("alt")["rollback_marker"] = "after"
            plugin._save_persona_profile_sync("alt")
            plugin._ensure_persona_profile("work")["created_during_transition"] = True
            plugin._save_persona_profile_sync("work")
            assert plugin._persona_profile_db_path("work").is_file()

            await api._rollback_multi_persona_transition(snapshot)
            plugin._persona_data_profiles.clear()

            assert plugin._ensure_persona_profile("alt")["rollback_marker"] == "before"
            assert not plugin._persona_profile_db_path("work").exists()

    asyncio.run(run())


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


def test_runtime_window_binding_mutation_is_retired():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            plugin = _harness(root)
            first = await plugin._mutate_persona_window_binding_async(
                action="upsert", window_key="QBot:GroupMessage:one", persona_id="alt"
            )
            assert first["ok"] is False
            assert first["status_code"] == 410
            assert first["code"] == "plugin_persona_routing_removed"
            assert plugin._persona_window_bindings() == {}

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
        migrated = plugin._load_secondary_persona_store_sync(
            "alt"
        ).manager.backend.load_store()
        assert not (profiles / "alt.json").exists()
        assert (profiles / "alt.db").is_file()
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
        Path(plugin.data_file).write_text(json.dumps(previous), encoding="utf-8")
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
            plugin.multi_persona_ids = ["main", "alt"]
            plugin.plugin_specific_persona_id = "main"
            plugin.config["enable_multi_persona_mode"] = False
            plugin.config["multi_persona_ids"] = ["main", "alt"]
            plugin.context = SimpleNamespace(
                persona_manager=SimpleNamespace(
                    get_persona_v3_by_id=lambda persona_id: (
                        {"name": "main"} if persona_id == "main" else None
                    )
                )
            )
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
