"""多目标模式下的两条安装级能力：备份与 V1 迁移。

对应 `docs/MULTI_TARGET_OPEN_DECISIONS_20260930.md` 的推荐组合：

* 备份：多目标也注册 `BackupWebAPI`，且暂存还原必须在**开库之前**被应用——
  少了这一步会变成「暂存成功、重启不生效」，比入口点不动更糟。
* 迁移：路由按角色挂载（写的是角色设置），目标从**会话管线上下文**推出来；
  单目标下原先传的是宿主上下文，目标推导必然失败、被兜底成 `legacy_apply_failed`。

用 AstrBot 自带 Python 运行（见 AGENTS.md）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_astrbot_staging_host as support
from tests import multi_host_support as multi_support
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request


BACKUP_PREFIX = "/astrbot_plugin_private_companion/backup"
LEGACY_PREFIX = "/astrbot_plugin_private_companion/legacy"


class QueryArgs(dict):
    def getlist(self, key):
        value = self.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


def v1_weather_config() -> dict:
    """一份最小可用的 V1 天气配置——它有一条真实的转换器。"""
    return {"weather_city": "杭州", "weather_lat": 30.25, "weather_lon": 120.16,
            "enable_weather_context": True, "weather_refresh_minutes": 90,
            "enable_weather_alerts": False}


def v1_root(parent: Path) -> Path:
    """在给定目录下造一份 V1 数据根：配置转储 + 一个 SQLite。"""
    root = parent / "astrbot_plugin_private_companion"
    root.mkdir(parents=True, exist_ok=True)
    (root / "companion_config.json").write_text(json.dumps(v1_weather_config(), ensure_ascii=False),
                                                encoding="utf-8")
    return root


class RouteCaller:
    """按宿主注册表直接调用路由，绕开 HTTP —— 与 test_model_provider_api 同一做法。"""

    def __init__(self, native, module):
        self.routes = {path: handler for path, handler, _, _ in native.registered_web_apis}
        self.module = module

    async def call(self, route, *, payload=None, username="admin", query=None):
        request = Request({"type": "http", "method": "POST" if payload is not None else "GET",
                           "path": route, "headers": [], "query_string": b""})
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode("utf-8")
        query_values = QueryArgs(query or {})
        fake = NS(username=username, query=query_values,
                  body=(lambda: self._body(body)) if body is not None else None)
        with patch.object(self.module, "request", fake):
            with bind_request_context(PluginRequest(request, username=username)):
                return await self.routes[route]()

    @staticmethod
    async def _body(raw):
        return raw


class MultiTargetInstallApiTests(multi_support.MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        multi_support.MultiFixture.setUp(self)

    async def test_backup_routes_are_registered_in_multi_mode(self):
        self.controller.register_routes()
        paths = {path for path, _, _, _ in self.native.registered_web_apis}

        # 备份是安装级的：挂一次，不按角色重挂。
        self.assertIn(BACKUP_PREFIX, paths)
        self.assertIn(BACKUP_PREFIX + "/create", paths)
        self.assertIn(BACKUP_PREFIX + "/restore", paths)

    async def test_account_migration_appends_new_routes_and_preserves_v2_persona_roles(self):
        import astrbot_host.legacy_api as legacy_api
        from companion_v2.legacy.migration import ConfigIntent

        self.controller.save_plugin_config = lambda candidate: (
            self.controller.config.clear(), self.controller.config.update(candidate))
        async def apply_bindings(candidate, **_options):
            self.controller.config.clear()
            self.controller.config.update(candidate)
            return {"saved": True, "started": True}
        self.controller.apply_bindings = AsyncMock(side_effect=apply_bindings)
        api = legacy_api.LegacyMigrationWebAPI(self.controller)
        duplicate_route = {"target_id": "v1-copy", "platform_id": "qq-main", "account_id": "100",
                           "bot_id": "bot-a", "participant_id": "200"}
        new_route = {"target_id": "v1-extra", "platform_id": "qq-main", "account_id": "100",
                     "bot_id": "bot-a", "participant_id": "201"}
        intent = ConfigIntent(
            scope="plugin_config", group_id="plugin", source_id="test",
            target_kind="plugin_config",
            changes={
                "multi_host.targets": [duplicate_route, new_route],
                "multi_host.personas": [
                    {"bot_id": "bot-a", "persona_id": "original", "role_id": "legacy-role"},
                    {"bot_id": "bot-a", "persona_id": "third", "role_id": "role-third"},
                ],
            },
            confidence={"multi_host.targets": "high", "multi_host.personas": "high"},
            sources={"multi_host.targets": ("basic_config.target_user_ids",),
                     "multi_host.personas": ("basic_config.plugin_specific_persona_id",)},
        )
        differences, scope_status, edit_tokens = [], [], {}

        prepared = api._prepare_plugin_intent(intent, differences, scope_status, edit_tokens)
        self.assertIsNotNone(prepared, differences)
        written, apply_result = await api._apply_plugin_intent(
            prepared, edit_tokens["plugin_config_state"])

        self.assertIn("multi_host.targets", written)
        self.assertEqual(apply_result, {"saved": True, "started": True})
        self.controller.apply_bindings.assert_awaited_once()
        self.assertEqual(len(self.controller.config["multi_host"]["targets"]), 3)
        self.assertEqual(next(item for item in self.controller.config["multi_host"]["targets"]
                              if item["target_id"] == "first")["participant_id"], "200")
        self.assertEqual(next(item for item in self.controller.config["multi_host"]["personas"]
                              if item["bot_id"] == "bot-a" and item["persona_id"] == "original")["role_id"],
                         "role-a")
        self.assertIn("role-third", {item["role_id"] for item in self.controller.config["multi_host"]["personas"]})
        self.assertIn("preserved_v2", {item["kind"] for item in differences})

    async def test_invalid_runtime_setting_does_not_split_identity_migration_bundle(self):
        import astrbot_host.legacy_api as legacy_api
        from companion_v2.legacy.migration import ConfigIntent

        self.controller.save_plugin_config = lambda candidate: (
            self.controller.config.clear(), self.controller.config.update(candidate))
        api = legacy_api.LegacyMigrationWebAPI(self.controller)
        intent = ConfigIntent(
            scope="plugin_config", group_id="plugin", source_id="test",
            target_kind="plugin_config",
            changes={
                "multi_host.targets": [{"target_id": "v1-private", "platform_id": "qq-main",
                    "account_id": "100", "bot_id": "bot-a", "participant_id": "201"}],
                "multi_host.group_targets": [{"target_id": "v1-group", "platform_id": "qq-main",
                    "account_id": "100", "bot_id": "bot-a", "group_id": "300"}],
                "multi_host.personas": [{"bot_id": "bot-a", "persona_id": "third",
                    "role_id": "role-third"}],
                "runtime.quiet_seconds": -1,
            },
            confidence={"multi_host.targets": "high", "multi_host.group_targets": "high",
                        "multi_host.personas": "high", "runtime.quiet_seconds": "high"},
            sources={"multi_host.targets": ("basic_config.target_user_ids",),
                     "multi_host.group_targets": ("legacy_compat_config.target_group_ids",),
                     "multi_host.personas": ("basic_config.multi_persona_ids",),
                     "runtime.quiet_seconds": ("basic_config.runtime_quiet_seconds",)},
        )
        differences, scope_status, edit_tokens = [], [], {}

        prepared = api._prepare_plugin_intent(intent, differences, scope_status, edit_tokens)

        self.assertIsNotNone(prepared, differences)
        self.assertEqual(prepared.changes["multi_host.targets"][-1]["target_id"], "v1-private")
        self.assertEqual(prepared.changes["multi_host.group_targets"][-1]["target_id"], "v1-group")
        self.assertIn("role-third", {item["role_id"] for item in prepared.changes["multi_host.personas"]})
        self.assertNotIn("runtime.quiet_seconds", prepared.changes)
        self.assertIn(("runtime.quiet_seconds", "invalid_combination"),
                      {(item["field"], item["kind"]) for item in differences})

    async def test_staged_restore_is_applied_before_the_store_opens(self):
        """多目标下必须补上这一步：否则还原永远不生效。"""
        import astrbot_host.multi_controller as multi_controller

        calls = []
        applied = {"applied": True, "reason": "test-restore"}
        state = {"state": "applied", "blocking": False, "startup_allowed": True,
                 "code": None, "detail": None, "journals": [], "pending": None,
                 "recovered_journals": []}

        def recorder(root, *, installation_id, backup_directory):
            # 记录当时运行实例还没有打开——那正是唯一能安全换文件的时刻。
            calls.append((root, installation_id, backup_directory, self.controller.runtime))
            return dict(applied), dict(state)

        with patch.object(multi_controller, "guard_restore_startup", recorder):
            initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)

        self.assertEqual(len(calls), 1, "开库路径必须调用一次启动闸")
        root, installation_id, backup_directory, runtime_at_call = calls[0]
        self.assertEqual(Path(root), self.controller.profile_root / "runtime")
        self.assertEqual(installation_id, self.controller.settings.installation_id)
        self.assertEqual(Path(backup_directory), Path(root) / "backups")
        self.assertIsNone(runtime_at_call, "必须在开库之前应用，否则会留下孤儿连接")
        # 结果要留在运行实例上，「还原到底生效了没有」才有地方读。
        self.assertEqual(self.controller.last_restore, applied)
        self.assertEqual(self.controller.restore_state, state)
        self.assertFalse(self.controller.restore_blocked)

    async def test_rollback_incomplete_blocks_initialize_profile_and_preserves_the_scene(self):
        """A startup gate refusal must leave the journal and request untouched.

        ``StorageRuntime.open`` creates a missing database. When the previous
        restore could not be rolled back, opening would fork the installation and
        hide the only copy of the old bytes; the controller has to fail instead.
        """
        runtime_root = self.controller.profile_root / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        database = runtime_root / "companion-v2.sqlite3"
        database.mkdir()  # the journaled original cannot be moved back onto a directory
        journal = runtime_root / ".restore-aaaaaaaa-12345678"
        (journal / "rollback").mkdir(parents=True)
        (journal / "rollback" / "companion-v2.sqlite3").write_bytes(b"original-bytes")
        (journal / "journal.json").write_text(json.dumps({
            "format": "companion-v2.restore-journal.v1", "phase": "prepared",
            "snapshot_id": "a" * 32, "content_hash": "b" * 64,
            "artifacts": [{"path": "companion-v2.sqlite3", "kind": "sqlite"}],
            "roots": ["companion-v2.sqlite3"]}))
        pending = runtime_root / "restore-pending.json"
        pending.write_text(json.dumps({
            "format": "companion-v2.restore-request.v1", "snapshot_id": "a" * 32,
            "content_hash": "b" * 64}))

        initialized = await self.controller.initialize_profile()

        self.assertIsNotNone(initialized.failure, initialized)
        self.assertEqual(initialized.failure.code, "backup_restore_rollback_incomplete")
        self.assertIsNone(self.controller.runtime, "the controller opened a store anyway")
        self.assertTrue(self.controller.restore_blocked)
        self.assertEqual(self.controller.restore_state["state"], "blocked")
        self.assertTrue(self.controller.restore_state["blocking"])
        self.assertEqual(self.controller.last_failure["code"], "backup_restore_rollback_incomplete")
        self.assertTrue(pending.is_file(), "the staged request was cleared")
        self.assertTrue((journal / "journal.json").is_file(), "the journal was cleared")
        self.assertEqual((journal / "rollback" / "companion-v2.sqlite3").read_bytes(),
                         b"original-bytes")
        self.assertFalse(database.is_file(), "an empty database was created next to the journal")

    async def test_legacy_routes_are_mounted_once_per_role(self):
        self.controller.register_routes()
        paths = {path for path, _, _, _ in self.native.registered_web_apis}

        inventory = self.controller.web_api.target_inventory()
        expected = [role["legacy_api"] for target in inventory for role in target["roles"]]
        self.assertTrue(expected, "多目标夹具里应当有角色")
        for route in expected:
            self.assertIn(route, paths, f"{route} 应当被注册")
            self.assertIn(route + "/plan", paths)
            self.assertIn(route + "/apply", paths)
        # 安装级的老地址（单目标用）不应在多目标里出现，否则会写成没角色的生效范围。
        self.assertNotIn(LEGACY_PREFIX, paths)

    async def test_page_directory_publishes_a_legacy_route_per_role(self):
        await self.controller.initialize_profile()
        self.controller.register_routes()
        caller = RouteCaller(self.native, __import__("astrbot_host.page_api", fromlist=["request"]))

        result = await caller.call("/astrbot_plugin_private_companion/page/targets")

        self.assertEqual(result["status"], "ok", result)
        data = result["data"]
        self.assertNotIn("mode", data)
        for target in data["targets"]:
            for role in target["roles"]:
                self.assertTrue(role["legacy_api"].endswith("/legacy"), role)

    async def test_group_model_routes_support_role_defaults_and_isolated_group_windows(self):
        config = multi_support.multi_config(group_targets=[
            {"target_id": "group-one", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "group_id": "300"},
            {"target_id": "group-two", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "group_id": "301"},
        ])
        self.controller = self.make_controller(config=config)
        await self.controller.initialize_profile()
        self.controller.register_routes()
        from astrbot_host import group_model_api

        caller = RouteCaller(self.native, group_model_api)
        inventory = self.controller.web_api.target_inventory()
        groups = {target["target_id"]: target for target in inventory if target["scope_kind"] == "group"}
        self.assertEqual(set(groups), {"group-one", "group-two"})
        first_route = groups["group-one"]["roles"][0]["model_api"]
        second_route = groups["group-two"]["roles"][0]["model_api"]
        paths = {path for path, _, _, _ in self.native.registered_web_apis}
        self.assertIn(first_route, paths)
        self.assertIn(first_route + "/configure", paths)
        self.assertIn(second_route, paths)

        role_status = await caller.call(first_route, query={"layer": "role"})
        window_status = await caller.call(first_route, query={"layer": "window"})
        self.assertEqual(role_status["status"], "ok", role_status)
        self.assertEqual(window_status["status"], "ok", window_status)
        self.assertEqual(role_status["data"]["scope_kind"], "group")
        self.assertEqual(role_status["data"]["configuration_layer"], "role")
        self.assertEqual(window_status["data"]["configuration_layer"], "window")
        proactive = next(item for item in window_status["data"]["tasks"]
                         if item["kind"] == "proactive.compose")
        relationship_prompts = [item for item in proactive["prompt_sections"]
                                if item.get("configuration_layer") == "role"]
        self.assertTrue(relationship_prompts)
        self.assertTrue(all(not item["editable"] for item in relationship_prompts))

        task = next(item for item in window_status["data"]["tasks"] if item["kind"] == "memory.reflect")
        saved = await caller.call(first_route + "/configure", payload={
            "kind": "memory.reflect", "operation_key": "group-one-window-prompt",
            "edit_token": task["edit_token"], "changes": {"prompt": "只用于群一窗口"},
            "reset": [], "layer": "window",
        })
        self.assertEqual(saved["status"], "ok", saved)

        first_window = await caller.call(first_route, query={"layer": "window"})
        second_window = await caller.call(second_route, query={"layer": "window"})
        first_task = next(item for item in first_window["data"]["tasks"] if item["kind"] == "memory.reflect")
        second_task = next(item for item in second_window["data"]["tasks"] if item["kind"] == "memory.reflect")
        role_task = next(item for item in role_status["data"]["tasks"] if item["kind"] == "memory.reflect")
        self.assertEqual(first_task["fields"]["prompt"]["value"], "只用于群一窗口")
        self.assertNotEqual(second_task["fields"]["prompt"]["value"], "只用于群一窗口")
        self.assertNotEqual(role_task["fields"]["prompt"]["value"], "只用于群一窗口")


class MultiTargetLegacyApplyTests(multi_support.MultiFixture, unittest.IsolatedAsyncioTestCase):
    """多目标下的迁移：先选目标与角色，再对着那个角色的路径执行。"""

    def setUp(self):
        multi_support.MultiFixture.setUp(self)
        temporary = TemporaryDirectory(prefix="companion-legacy-multi-")
        self.addCleanup(temporary.cleanup)
        self.v1 = v1_root(Path(temporary.name))
        config = multi_support.multi_config()
        config["legacy"] = {"v1_root": str(self.v1)}
        self.controller = self.make_controller(config=config)

    async def test_official_supplement_adds_group_persona_to_existing_role_without_changing_data(self):
        await self._supplement_existing_role(auto=False)

    async def test_auto_inheritance_adds_shared_persona_without_locking_or_losing_its_receipt(self):
        await self._supplement_existing_role(auto=True)

    async def _supplement_existing_role(self, *, auto):
        import astrbot_host.legacy_api as legacy_api
        from copy import deepcopy

        section = self.controller.config["multi_host"]
        section["targets"] = section["targets"][:1]
        section["personas"] = [{"bot_id": "bot-a", "persona_id": "original", "role_id": "role-a"}]
        self.window_personas["qq-main:GroupMessage:300"] = "second"
        self.native.persona_manager.personas_v3 = list(self.native.persona_records.values())
        self.native.platforms = {"qq-main": self.native.platform}
        clients = self.native.platform.client._wsr_api_clients
        self.native.platform.client._wsr_api_clients = {"100": clients["100"]}
        self.controller.refresh_settings()
        config_path = self.root / "config" / "astrbot_plugin_private_companion_config.json"
        config_path.parent.mkdir()
        self.controller.default_data_root = self.root / "plugin_data" / "companion-7"
        config_path.write_text(json.dumps({
            "basic_config": {"bot_scope_mode": "allowlist", "bot_scope_ids": ["100"],
                "target_user_ids": ["200"], "plugin_specific_persona_id": "original",
                "enable_multi_persona_mode": False},
            "group_observation_config": {"enable_group_companion": True,
                "group_access_mode": "whitelist", "group_whitelist_ids": ["300"]},
        }), encoding="utf-8")
        source_bytes = config_path.read_bytes()
        (await self.controller.initialize_profile()).unwrap()
        self.controller.save_plugin_config = lambda candidate: self.controller.config.update(candidate)
        self.controller.register_routes()
        caller = RouteCaller(self.native, legacy_api)
        runtime = self.controller.runtime
        route = self.controller.web_api.target_inventory()[0]["roles"][0]["legacy_api"]
        api = caller.routes[route].__self__
        from companion_v2.legacy.migration import ConfigIntent
        request = Request({"type": "http", "method": "POST", "path": route,
                           "headers": [], "query_string": b""})
        with bind_request_context(PluginRequest(request, username="admin")):
            seed = ConfigIntent("role.profile", "role.profile", {"name": "已迁移角色"}, "test")
            target = api._scope_target(api.controller.session, seed)
            snapshot = api.controller.session.application.configuration._snapshot(
                api._operator(), target, seed.group_id, support.NOW)
            api._apply_intent(seed, api.controller.session, snapshot)
        with runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                               ("persona-migration-existing-profile", "保留原角色资料"))
        before_configuration = [dict(row) for row in runtime.read_all("SELECT * FROM configuration_values", ())]
        original_target = deepcopy(section["targets"][0])
        planned = await caller.call(route + "/plan", query={"configuration_scope": "plugin"})
        self.assertEqual(planned["status"], "ok", planned)
        plan = planned["data"]
        self.assertTrue(any(item["target_kind"] == "plugin_config" for item in plan["intents"]), plan)
        plugin = next(item for item in plan["intents"] if item["target_kind"] == "plugin_config")
        pairs = plugin["values"]["multi_host.personas"]
        self.assertEqual({row["persona_id"]: row["role_id"] for row in pairs},
                         {"original": "role-a", "second": "role-a"})
        import asyncio
        applied = await asyncio.wait_for(caller.call(route + ("/auto" if auto else "/apply"), payload={} if auto else {
            "plan_token": plan["plan_token"], "configuration_scope": "plugin"}), timeout=20)
        self.assertEqual(applied["status"], "ok", applied)
        self.assertEqual(applied["data"]["errors"], [], applied)
        if auto:
            self.assertEqual(applied["data"]["state"], "imported", applied)
            self.assertTrue(applied["data"]["configuration_imported"], applied)
            self.assertTrue(applied["data"]["data_import_required"], applied)
        runtime = self.controller.runtime
        self.assertEqual(self.controller.config["multi_host"]["targets"][0], original_target)
        self.assertEqual(self.controller.config["multi_host"]["personas"], pairs)
        self.assertEqual(self.controller.config["multi_host"]["group_targets"][0]["group_id"], "300")
        for row in before_configuration:
            self.assertEqual(dict(runtime.read_one("SELECT * FROM configuration_values "
                "WHERE target_key = ? AND field_id = ?", (row["target_key"], row["field_id"]))), row)
        self.assertEqual(runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
            ("persona-migration-existing-profile",))["value"], "保留原角色资料")
        self.assertEqual(config_path.read_bytes(), source_bytes)

    async def test_migration_writes_the_selected_roles_settings(self):
        import astrbot_host.legacy_api as legacy_api

        await self.controller.initialize_profile()
        self.controller.register_routes()
        caller = RouteCaller(self.native, legacy_api)

        inventory = self.controller.web_api.target_inventory()
        target = inventory[0]
        role = target["roles"][0]
        planned = await caller.call(role["legacy_api"] + "/plan")

        self.assertEqual(planned["status"], "ok", planned)
        self.assertTrue(planned["data"]["scope_status"], "试算要带生效范围可用性")
        applied = await caller.call(role["legacy_api"] + "/apply",
                                    payload={"plan_token": planned["data"]["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertGreater(applied["data"]["written_count"], 0)
        self.assertEqual(applied["data"]["errors"], [])

        # 写到的是**选中角色**的设置：换个角色读同一组，值应当还是默认。
        view = self.controller.web_api.target_inventory()[0]
        other = next((item for item in view["roles"] if item["role_id"] != role["role_id"]), None)
        if other is not None:
            other_plan = await caller.call(other["legacy_api"] + "/plan")
            self.assertNotEqual(other_plan["data"]["plan_token"], "",
                                "另一个角色也应当能读同一份 V1 数据（允许复制，见口径 B2）")


class SingleTargetLegacyApplyTests(unittest.IsolatedAsyncioTestCase):
    """单目标下的迁移执行：目标必须从会话的管线上下文推出来。"""

    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-legacy-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.v1 = v1_root(self.root)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS,
                                           support.FakeReverseApi)
        config = support.host_config()
        config["legacy"] = {"v1_root": str(self.v1)}
        self.controller = support.AstrBotStagingController(self.native, config, self.root,
                                                           native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)

    async def test_scope_targets_come_from_the_session_pipeline_context(self):
        import astrbot_host.legacy_api as legacy_api
        from companion_v2.legacy.migration import ConfigIntent

        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        api = legacy_api.LegacyMigrationWebAPI(self.controller)

        for scope, group in (("role.state", "role.state"), ("proactive.contact", "proactive.contact")):
            intent = ConfigIntent(source_id="test", scope=scope, group_id=group, changes={"x": 1})
            target = api._scope_target(session, intent)
            self.assertIsNotNone(target, f"{scope} 应当能从会话上下文推出目标")
            self.assertEqual(target.installation_id, session.profile.target.scope.installation_id)

        # 宿主上下文（只有注册路由那套接口）推不出目标——这正是原先的错处。
        empty = ConfigIntent(source_id="test", scope="role.state", group_id="role.state", changes={})
        self.assertIsNone(api._scope_target(NS(profile=NS(target=NS())), empty))

    async def test_plan_reports_scope_availability_before_applying(self):
        self.controller.register_routes()
        caller = RouteCaller(self.native, __import__("astrbot_host.legacy_api", fromlist=["request"]))
        (await self.controller.initialize_profile()).unwrap()

        planned = await caller.call(LEGACY_PREFIX + "/plan")

        self.assertEqual(planned["status"], "ok", planned)
        status = planned["data"]["scope_status"]
        self.assertTrue(status, "试算要回答每个生效范围能不能写")
        for item in status:
            self.assertTrue(item["available"], item)
            self.assertEqual(item["reason"], "")

    async def test_apply_writes_the_planned_settings(self):
        self.controller.register_routes()
        caller = RouteCaller(self.native, __import__("astrbot_host.legacy_api", fromlist=["request"]))
        (await self.controller.initialize_profile()).unwrap()
        planned = await caller.call(LEGACY_PREFIX + "/plan")
        token = planned["data"]["plan_token"]

        applied = await caller.call(LEGACY_PREFIX + "/apply", payload={"plan_token": token})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertGreater(applied["data"]["written_count"], 0,
                           "迁移必须真的写进去（原先目标推导失败，一条都写不进去）")
        self.assertEqual(applied["data"]["errors"], [])
        self.assertTrue(applied["data"]["completion_recorded"])

    def _split_migration(self):
        from companion_v2.foundation.outcome import Outcome
        from companion_v2.legacy.migration import ConfigIntent, MigrationPlan, SourceFinding

        plan = MigrationPlan(str(self.v1), findings=(
            SourceFinding("private.host_configuration", "private", "converted", "ready", ()),
            SourceFinding("reality.configuration", "reality", "converted", "absent", ()),
            SourceFinding("image.configuration", "image", "converted", "absent", ()),
            *(SourceFinding(name + ".configuration", name, "converted", "absent", ())
              for name in ("game", "together", "live")),
        ), intents=(ConfigIntent("role.state", "role.state",
            {"enabled": True, "maintenance_minutes": 45, "presence_enabled": True},
            "private.host_configuration"),))
        return NS(plan=lambda: Outcome.success(plan), configuration_context_fingerprint="split-test",
                  v1_root=self.v1, _configuration=lambda: {})

    async def test_split_plan_keeps_existing_role_values_and_matches_the_remaining_fields_token(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        from companion_v2.legacy.migration import ConfigIntent

        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        api = LegacyMigrationWebAPI(self.controller)
        raw = Request({"type": "http", "method": "POST", "path": "/",
                       "headers": [], "query_string": b""})
        with bind_request_context(PluginRequest(raw, username="admin")):
            seed = ConfigIntent("role.state", "role.state", {"enabled": False}, "test")
            service = session.application.configuration
            target = api._scope_target(session, seed)
            baseline = service._snapshot(api._operator(), target, seed.group_id, support.NOW)
            api._apply_intent(seed, session, baseline)
            migration = self._split_migration()
            plan = api._plan(migration, self.v1, session).unwrap()
            state_intents = [item for item in plan["intents"] if item["group_id"] == "role.state"]
            self.assertEqual([item["fields"] for item in state_intents],
                             [["maintenance_minutes", "presence_enabled"]])
            result = (await api._apply(migration, self.v1, session, plan["plan_token"])).unwrap()
            self.assertEqual(result["errors"], [])
            self.assertNotIn("role.state.enabled", result["written"])
            snapshot = service._snapshot(api._operator(), target, seed.group_id, support.NOW)
            self.assertFalse(snapshot.fields["role.state.enabled"].value)
            self.assertEqual(snapshot.fields["role.state.maintenance_minutes"].value, 45)

    async def test_split_plan_rejects_changes_after_preparation_before_any_write(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI
        from companion_v2.configuration.models import ConfigLayer
        from companion_v2.configuration.owners import SparseConfigurationOwner
        from companion_v2.legacy.migration import ConfigIntent

        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        api = LegacyMigrationWebAPI(self.controller)
        migration = self._split_migration()
        raw = Request({"type": "http", "method": "POST", "path": "/",
                       "headers": [], "query_string": b""})
        with bind_request_context(PluginRequest(raw, username="admin")):
            plan = api._plan(migration, self.v1, session).unwrap()
            original = api._prepare_intents
            service = session.application.configuration
            def changed_after_preparation(*args):
                prepared = original(*args)
                intent = ConfigIntent("role.state", "role.state", {"maintenance_minutes": 50}, "test")
                target = api._scope_target(session, intent)
                self.assertIs(target.layer, ConfigLayer.ROLE)
                definitions = service.registry.group(intent.group_id)
                owner = service._owners[definitions[0].owner_id]
                current = owner.read(target, definitions)
                SparseConfigurationOwner.write(owner, target, owner.role_fields(definitions),
                    changes={"role.state.maintenance_minutes": 50}, reset=(),
                    expected_revision=current.revision, observed_at=support.NOW)
                return prepared
            with patch.object(api, "_prepare_intents", side_effect=changed_after_preparation):
                result = await api._apply(migration, self.v1, session, plan["plan_token"])
            self.assertFalse(result.is_success)
            self.assertEqual(result.failure.code, "legacy_plan_stale")
            window = api._scope_target(session, ConfigIntent("role.state", "role.state",
                {"presence_enabled": True}, "test"))
            snapshot = service._snapshot(api._operator(), window, "role.state", support.NOW)
            self.assertFalse(snapshot.fields["role.state.presence_enabled"].value)
            self.assertEqual(snapshot.fields["role.state.maintenance_minutes"].value, 50)

    async def test_upgrade_guide_route_lifecycle_imports_then_confirms_after_restart(self):
        import astrbot_host.legacy_api as legacy_api

        self.controller.register_routes()
        caller = RouteCaller(self.native, legacy_api)
        (await self.controller.initialize_profile()).unwrap()
        source_config = self.v1 / "companion_config.json"
        source_values = json.loads(source_config.read_text(encoding="utf-8"))
        source_values["model_assignment_config"] = {"enable_llm_streaming": True}
        source_config.write_text(json.dumps(source_values, ensure_ascii=False) + "\n",
                                 encoding="utf-8", newline="\n")
        self.controller.save_plugin_config = lambda candidate: self.controller.config.update(candidate)
        original_v1_config = source_config.read_bytes()

        discovered = await caller.call(LEGACY_PREFIX)
        self.assertEqual(discovered["status"], "ok", discovered)
        self.assertEqual(discovered["data"]["install"]["phase"], "upgrade_pending")
        self.assertTrue(discovered["data"]["install"]["pending"])

        inherited = await caller.call(LEGACY_PREFIX + "/auto", payload={})
        self.assertEqual(inherited["status"], "ok", inherited)
        self.assertGreater(inherited["data"]["written_count"], 0, inherited)
        self.assertTrue(inherited["data"]["configuration_imported"], inherited)
        self.assertTrue(inherited["data"]["data_import_required"], inherited)
        self.assertEqual(source_config.read_bytes(), original_v1_config,
                         "upgrade import must leave V1 source files untouched")

        before_restart = await caller.call(LEGACY_PREFIX)
        self.assertEqual(before_restart["data"]["install"]["phase"], "upgrade_restart_check")
        self.assertFalse(before_restart["data"]["install"]["restart_observed"])
        self.assertEqual(before_restart["data"]["install"]["data_migration_status"], "not_inferred")

        premature = await caller.call(LEGACY_PREFIX + "/upgrade-confirm", payload={"confirmed": True})
        self.assertEqual(premature["status"], "error", premature)
        self.assertEqual(premature["data"]["code"], "legacy_restart_required")

        self.controller.lifecycle_token = "simulated-next-astrbot-process"
        self.controller.unregister_routes()
        self.controller.register_routes()
        caller = RouteCaller(self.native, legacy_api)
        after_restart = await caller.call(LEGACY_PREFIX)
        self.assertTrue(after_restart["data"]["install"]["restart_observed"])
        confirmed = await caller.call(LEGACY_PREFIX + "/upgrade-confirm", payload={"confirmed": True})
        self.assertEqual(confirmed["status"], "ok", confirmed)
        self.assertEqual(confirmed["data"]["install"]["phase"], "upgrade_cleanup")
        self.assertTrue(confirmed["data"]["install"]["upgrade_verified"])
        self.assertEqual(confirmed["data"]["install"]["data_migration_status"], "not_inferred")
        self.assertEqual(source_config.read_bytes(), original_v1_config)

    async def test_upgrade_state_requires_restart_and_separates_data_migration(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        first_instance = LegacyMigrationWebAPI(self.controller)
        self.assertTrue(first_instance._mark_imported(2, self.v1))

        before_restart = first_instance._install_state(self.v1, [{"available": True}])
        self.assertEqual(before_restart["phase"], "upgrade_restart_check")
        self.assertFalse(before_restart["restart_observed"])
        self.assertTrue(before_restart["configuration_imported"])
        self.assertEqual(before_restart["data_migration_status"], "not_inferred")

        alternate_root = self.root / "alternate-v1"
        alternate_root.mkdir()
        alternate = first_instance._install_state(alternate_root, [{"available": True}])
        self.assertEqual(alternate["phase"], "upgrade_pending")

        after_restart = LegacyMigrationWebAPI(self.controller)
        after_restart._instance_token = "new-process"
        ready_to_confirm = after_restart._install_state(self.v1, [{"available": True}])
        self.assertTrue(ready_to_confirm["restart_observed"])
        self.assertTrue(after_restart._mark_upgrade_verified(self.v1))

        verified = after_restart._install_state(self.v1, [{"available": True}])
        self.assertEqual(verified["phase"], "upgrade_cleanup")
        self.assertTrue(verified["upgrade_verified"])
        self.assertEqual(after_restart._install_state(alternate_root, [{"available": True}])["phase"],
                         "upgrade_pending")

        self.assertTrue(after_restart._mark_imported(1, self.v1, configuration_scope="proactive"))
        after_additional_write = after_restart._install_state(self.v1, [{"available": True}])
        self.assertEqual(after_additional_write["phase"], "upgrade_restart_check")
        self.assertFalse(after_additional_write["upgrade_verified"])

    async def test_no_change_import_is_verified_without_restart(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        api = LegacyMigrationWebAPI(self.controller)
        self.assertTrue(api._mark_imported(
            0, self.v1, configuration_scope="plugin", restart_required=False,
        ))

        state = api._install_state(self.v1, [{"available": True}])

        self.assertEqual(state["phase"], "upgrade_cleanup")
        self.assertTrue(state["configuration_imported"])
        self.assertEqual(state["configuration_scopes"], ["plugin"])
        self.assertFalse(state["restart_required"])
        self.assertTrue(state["upgrade_verified"])

    async def test_plugin_reload_reopens_configuration_without_a_process_restart(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        original = LegacyMigrationWebAPI(self.controller)
        self.assertTrue(original._mark_imported(2, self.v1, restart_required=True))
        rebuilt_routes = LegacyMigrationWebAPI(self.controller)
        self.assertEqual(original._instance_token, rebuilt_routes._instance_token)
        self.assertFalse(rebuilt_routes._restart_observed(self.v1))

        await self.controller.terminate()
        reopened = support.AstrBotStagingController(self.native, self.controller.config, self.root,
            native_types=self.controller.native_types, now=lambda: support.NOW)
        self.addAsyncCleanup(reopened.terminate)
        (await reopened.load_existing()).unwrap()
        (await reopened.start()).unwrap()
        loaded = LegacyMigrationWebAPI(reopened)
        self.assertNotEqual(original._instance_token, loaded._instance_token)
        self.assertTrue(loaded._restart_observed(self.v1))
        self.assertTrue(loaded._mark_upgrade_verified(self.v1))
        self.assertFalse(loaded._install_state(self.v1, [])["restart_required"])

    async def test_no_change_import_preserves_restart_pending_from_prior_write(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        api = LegacyMigrationWebAPI(self.controller)
        self.assertTrue(api._mark_imported(
            2, self.v1, configuration_scope="plugin", restart_required=True,
        ))
        self.assertTrue(api._mark_imported(
            0, self.v1, configuration_scope="proactive", restart_required=False,
        ))

        state = api._install_state(self.v1, [{"available": True}])

        self.assertEqual(state["phase"], "upgrade_restart_check")
        self.assertEqual(state["configuration_scopes"], ["plugin", "proactive"])
        self.assertTrue(state["restart_required"])
        self.assertFalse(state["upgrade_verified"])

    async def test_legacy_import_marker_without_scope_keeps_its_upgrade_state(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        with self.controller.runtime.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                ("legacy_imported_at", "2026-01-01T00:00:00Z"),
            )

        api = LegacyMigrationWebAPI(self.controller)
        state = api._install_state(self.v1, [{"available": True}])
        self.assertEqual(state["phase"], "upgrade_restart_check")
        self.assertTrue(state["configuration_imported"])
        self.assertTrue(state["configuration_scope_unknown"])
        self.assertEqual(state["configuration_scopes"], [])

        alternate_root = self.root / "alternate-v1"
        alternate_root.mkdir()
        alternate = api._install_state(alternate_root, [{"available": True}])
        self.assertEqual(alternate["phase"], "upgrade_pending")
        self.assertFalse(alternate["configuration_scope_unknown"])

    async def test_partial_scope_marker_does_not_inherit_legacy_completion(self):
        from astrbot_host.legacy_api import LegacyMigrationWebAPI

        (await self.controller.initialize_profile()).unwrap()
        api = LegacyMigrationWebAPI(self.controller)
        self.assertTrue(api._mark_imported(2, self.v1, configuration_scope="proactive"))

        state = api._install_state(self.v1, [{"available": True}])
        self.assertEqual(state["phase"], "upgrade_pending")
        self.assertFalse(state["configuration_imported"])
        self.assertFalse(state["configuration_scope_unknown"])
        self.assertEqual(state["configuration_scopes"], ["proactive"])


class HostConfigDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    """V1 的插件配置在宿主配置目录里，不在 V1 数据根里。

    真实安装中 V1 从不导出配置转储，迁移必须直接读宿主按
    ``<插件目录名>_config.json`` 保存的那份（star_manager 的命名约定，位于 AstrBot
    数据目录的 config 子目录）。没有这条读取，「天气」这条唯一的自动转换在任何
    真实机器上都会报「未发现」。沙盒里按 AstrBot 的标准布局搭一层：
    ``<tmp>/astrbot/{config,plugin_data/<v2 数据根>}``。
    """

    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-legacy-hostcfg-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name) / "astrbot"
        self.data_root = base / "plugin_data" / "astrbot_plugin_private_companion_v2"
        self.data_root.mkdir(parents=True)
        # V1 数据根留空：证明来源的就绪只靠宿主配置文件，不靠任何转储。
        self.v1 = base / "plugin_data" / "astrbot_plugin_private_companion"
        self.v1.mkdir(parents=True)
        config_dir = base / "config"
        config_dir.mkdir()
        self.host_config_path = config_dir / "astrbot_plugin_private_companion_config.json"
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS,
                                           support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), self.data_root,
            native_types=types, now=lambda: support.NOW)
        self.native.persona_manager.get_personas = lambda: [self.native.persona]
        self.controller.save_plugin_config = self._save_plugin_config
        self.addAsyncCleanup(self.controller.terminate)

    def _save_plugin_config(self, candidate):
        self.controller.config.clear()
        self.controller.config.update(candidate)

    def _write_host_config(self, **overrides):
        config = v1_weather_config()
        config["basic_config"] = {"reply_style_prompt": "V1 的回复表达配置"}
        config["reactive_poke_config"] = {
            "enable_reactive_poke": True,
            "reactive_poke_trigger_probability": 0.25,
            "reactive_poke_back_probability": 0.4,
            "reactive_poke_normal_replies": ["V1 戳一戳回复"],
        }
        config.update(overrides)
        self.host_config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    async def _plan(self, *, configuration_scope=None):
        self.controller.register_routes()
        (await self.controller.initialize_profile()).unwrap()
        caller = RouteCaller(self.native, __import__("astrbot_host.legacy_api", fromlist=["request"]))
        query = ({"configuration_scope": configuration_scope}
                 if configuration_scope is not None else None)
        planned = await caller.call(LEGACY_PREFIX + "/plan", query=query)
        self.assertEqual(planned["status"], "ok", planned)
        return planned["data"], caller

    async def test_absent_without_the_host_config_file(self):
        plan, _ = await self._plan()
        ready = [item["source_id"] for item in plan["findings"] if item["state"] == "ready"]
        self.assertEqual(ready, [], "没有宿主配置文件也没有转储时，host-config 来源必须报未发现")

    async def test_host_config_file_makes_the_source_ready_and_applies(self):
        self._write_host_config()
        plan, caller = await self._plan()
        ready = [item["source_id"] for item in plan["findings"] if item["state"] == "ready"]
        self.assertIn("private.host_configuration", ready)
        self.assertGreater(plan["change_count"], 0, "试算要产出可写入的天气设置")
        presentation = next(item for item in plan["intents"]
                            if item["group_id"] == "conversation.presentation")
        self.assertEqual(presentation["values"]["style_prompt"],
                         {"configured": True, "characters": len("V1 的回复表达配置")})
        self.assertEqual(presentation["confidence"]["style_prompt"], "high")

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertGreater(applied["data"]["written_count"], 0)
        self.assertEqual(applied["data"]["errors"], [])
        self.assertTrue(applied["data"]["restart_required"])
        self.assertEqual(self.controller.config["poke"], {
            "enabled": True,
            "trigger_probability": 0.25,
            "back_probability": 0.4,
            "replies": ["V1 戳一戳回复"],
        })


    async def test_auto_inheritance_rejects_unreadable_host_config_without_writing(self):
        import astrbot_host.legacy_api as legacy_api

        self.host_config_path.write_text("{ invalid json", encoding="utf-8")
        self.controller.register_routes()
        caller = RouteCaller(self.native, legacy_api)
        (await self.controller.initialize_profile()).unwrap()
        original_config = json.loads(json.dumps(self.controller.config))

        inherited = await caller.call(LEGACY_PREFIX + "/auto", payload={})

        self.assertEqual(inherited["status"], "error", inherited)
        self.assertEqual(inherited["data"]["code"], "legacy_configuration_unavailable")
        self.assertEqual(self.controller.config, original_config)
        api = legacy_api.LegacyMigrationWebAPI(self.controller)
        self.assertFalse(api._already_imported(self.v1))
        self.assertIsNone(api._runtime_metadata_value("legacy_configuration_scopes"))
        self.assertIsNone(api._runtime_metadata_value("legacy_configuration_imported_root"))

    def test_plugin_config_candidate_falls_back_for_noncopyable_runtime_mapping(self):
        import astrbot_host.legacy_api as legacy_api

        class RuntimeConfig(dict):
            def __deepcopy__(self, memo):
                raise TypeError("runtime mapping cannot be copied")

        weather = {"provider": "auto", "api_host": ""}
        untouched = object()
        config = RuntimeConfig(weather=weather, untouched=untouched)
        candidate = legacy_api.LegacyMigrationWebAPI._plugin_config_candidate(
            config, {"weather.provider": "qweather", "weather.api_host": "api.qweatherapi.com"},
        )

        self.assertEqual(candidate["weather"], {
            "provider": "qweather", "api_host": "api.qweatherapi.com",
        })
        self.assertEqual(weather, {"provider": "auto", "api_host": ""})
        self.assertIs(candidate["untouched"], untouched)

    async def test_qweather_credentials_apply_through_redacted_host_settings(self):
        host = "api.qweatherapi.com"
        credential = "a" * 32
        self._write_host_config(
            reactive_poke_config={},
            weather_config={
                "weather_source": "qweather",
                "weather_api_host": host,
                "weather_token": credential,
                "weather_city": "杭州",
                "weather_lat": 30.25,
                "weather_lon": 120.16,
                "enable_weather_context": True,
                "enable_weather_alerts": False,
            },
        )

        plan, caller = await self._plan()
        weather_intent = next(item for item in plan["intents"]
                              if item["target_kind"] == "weather_host_settings")
        self.assertEqual(set(weather_intent["fields"]), {"api_host", "credential", "provider"})
        self.assertEqual(weather_intent["values"]["api_host"], "（已配置，值不在此显示）")
        self.assertEqual(weather_intent["values"]["credential"], "（已配置，值不在此显示）")
        serialized_plan = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(host, serialized_plan)
        self.assertNotIn(credential, serialized_plan)
        self.assertNotIn("weather_api_host", {
            item.get("field") for item in plan["differences"]
        })

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertIn("weather.api_host", applied["data"]["written"])
        self.assertIn("weather.credential", applied["data"]["written"])
        self.assertNotIn(host, json.dumps(applied, ensure_ascii=False))
        self.assertNotIn(credential, json.dumps(applied, ensure_ascii=False))
        weather = self.controller.config["weather"]
        self.assertEqual(weather["provider"], "qweather")
        self.assertEqual(weather["api_host"], host)
        self.assertEqual(weather["credential"], credential)
        self.assertEqual(self.controller.session.application.calendar_planning.weather_port.provider,
                         "qweather")

    async def test_weather_bundle_isolated_from_an_unrelated_invalid_plugin_setting(self):
        import astrbot_host.legacy_api as legacy_api
        from companion_v2.legacy.migration import ConfigIntent, MigrationPlan

        (await self.controller.initialize_profile()).unwrap()
        api = legacy_api.LegacyMigrationWebAPI(self.controller)
        host = "api.qweatherapi.com"
        credential = "b" * 32
        intent = ConfigIntent(
            scope="plugin_config", group_id="plugin", source_id="test", target_kind="plugin_config",
            changes={"weather.provider": "qweather", "weather.api_host": host,
                     "weather.credential": credential, "runtime.quiet_seconds": -1},
        )

        request = Request({"type": "http", "method": "GET", "path": LEGACY_PREFIX + "/plan",
                           "headers": [], "query_string": b""})
        with bind_request_context(PluginRequest(request, username="admin")):
            intents, differences, scope_status, edit_tokens = api._prepare_intents(
                self.controller.session, MigrationPlan(v1_root="test", intents=(intent,)),
            )

        self.assertEqual([item.target_kind for item in intents], ["weather_host_settings"])
        documents = api._intent_documents(intents)
        encoded = json.dumps(documents, ensure_ascii=False)
        self.assertNotIn(host, encoded)
        self.assertNotIn(credential, encoded)
        self.assertIn(("runtime.quiet_seconds", "invalid_combination"),
                      {(item["field"], item["kind"]) for item in differences})
        self.assertIn("weather_host_settings", edit_tokens)

    async def test_proactive_scope_only_plans_and_applies_message_and_weather_settings(self):
        self._write_host_config(private_followup_config={
            "idle_minutes": 12,
            "min_interval_minutes": 45,
        })

        plan, caller = await self._plan(configuration_scope="proactive")

        self.assertEqual(plan["configuration_scope"], "proactive")
        fields = {
            f"{intent['scope']}.{field}"
            for intent in plan["intents"]
            for field in intent["fields"]
        }
        allowed = {
            "proactive.contact.*",
            "role.state.planning_weather_enabled",
            "role.state.planning_weather_location",
            "role.state.planning_weather_max_age_minutes",
        }
        self.assertTrue(any(field.startswith("proactive.contact.") for field in fields))
        self.assertTrue({
            "role.state.planning_weather_enabled",
            "role.state.planning_weather_location",
            "role.state.planning_weather_max_age_minutes",
        }.issubset(fields))
        self.assertTrue(all(field.startswith("proactive.contact.") or field in allowed for field in fields))
        self.assertTrue(all(intent["target_kind"] == "configuration_registry" for intent in plan["intents"]))
        self.assertNotIn("conversation.presentation", {intent["scope"] for intent in plan["intents"]})
        self.assertNotIn("plugin_config", {intent["target_kind"] for intent in plan["intents"]})
        self.assertNotIn("semantic_config", {intent["target_kind"] for intent in plan["intents"]})

        wrong_scope = await caller.call(LEGACY_PREFIX + "/apply", payload={
            "plan_token": plan["plan_token"],
        })
        self.assertEqual(wrong_scope["status"], "error")
        self.assertEqual(wrong_scope["data"]["code"], "legacy_plan_stale")

        applied = await caller.call(LEGACY_PREFIX + "/apply", payload={
            "plan_token": plan["plan_token"],
            "configuration_scope": "proactive",
        })
        self.assertEqual(applied["status"], "ok", applied)
        self.assertEqual(applied["data"]["configuration_scope"], "proactive")
        self.assertGreater(applied["data"]["written_count"], 0)
        self.assertEqual(applied["data"]["errors"], [])
        self.assertFalse(applied["data"]["restart_required"])
        self.assertNotIn("poke", self.controller.config)

    async def test_account_targets_and_persona_are_previewed_and_applied_without_mode_switch(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock

        self.controller.native_types = replace(self.controller.native_types,
            persona_snapshot_lookup=AsyncMock(return_value="original"),
            persona_selection_lookup=lambda _context, _umo, selected: selected)
        self._write_host_config(
            basic_config={
                "bot_scope_mode": "all",
                "bot_scope_ids": [],
                "target_user_ids": ["200"],
                "plugin_specific_persona_id": "original",
                "enable_multi_persona_mode": False,
                "multi_persona_ids": [],
            },
            group_observation_config={
                "enable_group_companion": False,
                "group_access_mode": "whitelist",
                "group_whitelist_ids": [],
            },
        )

        plan, caller = await self._plan()
        plugin = next(item for item in plan["intents"] if item["target_kind"] == "plugin_config")
        targets = plugin["values"]["multi_host.targets"]
        personas = plugin["values"]["multi_host.personas"]
        self.assertEqual({item["account_id"] for item in targets}, {"100", "101"})
        self.assertEqual({item["participant_id"] for item in targets}, {"200"})
        self.assertEqual({item["persona_id"] for item in personas}, {"original"})
        self.assertNotIn("multi_host.enabled", plugin["values"])
        self.assertIn("mode_preserved", {item["kind"] for item in plan["differences"]})

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertEqual(self.controller.config.get("multi_host", {}).get("enabled", False), False)
        self.assertEqual(len(self.controller.config["multi_host"]["targets"]), 2)
        self.assertEqual(len(self.controller.config["multi_host"]["personas"]), 2)

    async def test_account_inventory_change_expires_the_migration_plan(self):
        self._write_host_config(
            basic_config={"bot_scope_mode": "all", "bot_scope_ids": [], "target_user_ids": ["200"],
                          "plugin_specific_persona_id": "original"},
        )
        plan, caller = await self._plan()
        self.native.platform.client._wsr_api_clients["102"] = object()

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "error")
        self.assertEqual(applied["data"]["code"], "legacy_plan_stale", applied)

    async def test_semantic_settings_are_previewed_and_applied(self):
        self._write_host_config(
            experimental_motivation_config={
                "reaction_expression_trigger_probability": 0.73,
                "reaction_expression_cooldown_seconds": 240,
            },
            relay_bridge_config={"group_slang_summary_minutes": 20},
        )
        plan, caller = await self._plan()

        semantic = next(item for item in plan["intents"]
                        if item["target_kind"] == "semantic_config")
        self.assertEqual(semantic["values"], {
            "reactions_trigger_probability": 0.73,
            "reactions_cooldown_seconds": 240,
            "learning_interval_seconds": 1200,
        })
        self.assertTrue(next(item for item in plan["scope_status"]
                             if item["scope"] == "semantic_catalog")["available"])

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertIn("semantic.reactions_trigger_probability", applied["data"]["written"])
        snapshot = self.controller.session.application.semantics.store.configuration_snapshot()
        self.assertEqual(snapshot["values"]["reactions_trigger_probability"], 0.73)
        self.assertEqual(snapshot["values"]["reactions_cooldown_seconds"], 240)
        self.assertEqual(snapshot["values"]["learning_interval_seconds"], 1200)
        self.assertTrue({"reactions_trigger_probability", "reactions_cooldown_seconds",
                         "learning_interval_seconds"}.issubset(snapshot["explicit"]))

    async def test_semantic_revision_change_expires_the_preview(self):
        self._write_host_config(experimental_motivation_config={
            "reaction_expression_trigger_probability": 0.73,
        })
        plan, caller = await self._plan()
        semantics = self.controller.session.application.semantics
        snapshot = semantics.store.configuration_snapshot()
        semantics.store.configure({"slang_enabled": False}, snapshot["revision"])

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "error", applied)
        self.assertEqual(applied["data"]["code"], "legacy_plan_stale", applied)

    async def test_semantic_preview_preserves_an_existing_v2_value(self):
        self._write_host_config(experimental_motivation_config={
            "reaction_expression_trigger_probability": 0.73,
        })
        _, caller = await self._plan()
        semantics = self.controller.session.application.semantics
        snapshot = semantics.store.configuration_snapshot()
        semantics.store.configure({"reactions_trigger_probability": 0.25}, snapshot["revision"])

        plan = await caller.call(LEGACY_PREFIX + "/plan")

        self.assertEqual(plan["status"], "ok", plan)
        self.assertFalse(any(item["target_kind"] == "semantic_config"
                             for item in plan["data"]["intents"]))
        difference = next(item for item in plan["data"]["differences"]
                          if item["field"] == "semantic.reactions_trigger_probability")
        self.assertEqual(difference["kind"], "preserved_v2")
        self.assertEqual(difference["after"], "0.25")

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["data"]["plan_token"]})

        self.assertEqual(applied["status"], "ok", applied)
        self.assertNotIn("semantic.reactions_trigger_probability", applied["data"]["written"])
        self.assertEqual(semantics.store.settings()["reactions_trigger_probability"], 0.25)

    async def test_plan_token_expires_when_the_host_config_changes(self):
        """指纹必须覆盖宿主配置文件：它在试算与执行之间变了，执行就要被拒。"""
        self._write_host_config()
        plan, caller = await self._plan()
        self._write_host_config(weather_refresh_minutes=119)

        applied = await caller.call(LEGACY_PREFIX + "/apply",
                                    payload={"plan_token": plan["plan_token"]})

        self.assertEqual(applied["status"], "error", applied)
        self.assertEqual(applied["data"]["code"], "legacy_plan_stale", applied)

    async def test_plan_token_expires_when_config_dump_changes_without_stat_change(self):
        self.controller.register_routes()
        (await self.controller.initialize_profile()).unwrap()
        caller = RouteCaller(self.native, __import__("astrbot_host.legacy_api", fromlist=["request"]))
        selected_root = v1_root(self.data_root.parent / "alternate-dump")
        planned = await caller.call(LEGACY_PREFIX + "/plan", query={"v1_root": str(selected_root)})
        self.assertEqual(planned["status"], "ok", planned)

        config_path = selected_root / "companion_config.json"
        original = config_path.read_text(encoding="utf-8")
        original_stat = config_path.stat()
        updated = original.replace("杭州", "合肥", 1)
        self.assertEqual(len(updated.encode("utf-8")), original_stat.st_size)
        config_path.write_text(updated, encoding="utf-8")
        os.utime(config_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(config_path.stat().st_mtime_ns, original_stat.st_mtime_ns)

        applied = await caller.call(LEGACY_PREFIX + "/apply", payload={
            "plan_token": planned["data"]["plan_token"], "v1_root": str(selected_root)})

        self.assertEqual(applied["status"], "error")
        self.assertEqual(applied["data"]["code"], "legacy_plan_stale", applied)

    async def test_explicit_v1_directory_is_bound_to_the_plan_and_apply(self):
        self._write_host_config()
        self.controller.register_routes()
        (await self.controller.initialize_profile()).unwrap()
        caller = RouteCaller(self.native, __import__("astrbot_host.legacy_api", fromlist=["request"]))
        selected_root = v1_root(self.data_root.parent / "alternate")

        discovered = await caller.call(LEGACY_PREFIX, query={"v1_root": str(selected_root)})
        planned = await caller.call(LEGACY_PREFIX + "/plan", query={"v1_root": str(selected_root)})

        self.assertEqual(discovered["status"], "ok", discovered)
        self.assertEqual(discovered["data"]["v1_root"], str(selected_root))
        self.assertEqual(planned["status"], "ok", planned)
        self.assertEqual(planned["data"]["v1_root"], str(selected_root))
        applied = await caller.call(LEGACY_PREFIX + "/apply", payload={
            "plan_token": planned["data"]["plan_token"], "v1_root": str(selected_root)})
        self.assertEqual(applied["status"], "ok", applied)
        self.assertEqual(applied["data"]["v1_root"], str(selected_root))
