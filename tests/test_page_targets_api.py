"""Page discovery and target selection use the real SDK with isolated V2 data."""

import json
import asyncio
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture, multi_config
from astrbot.api.web import PluginRequest, bind_request_context
from astrbot.dashboard.services.plugin_page_service import PluginPageService
from starlette.requests import Request
from astrbot_host.management_identity import dashboard_operator
from astrbot_host.page_api import PREFIX
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.memory.person.management import PersonAcquaintanceDirectory
from companion_v2.relationships.group_network import GroupRelationshipStore, VERSION, metadata_key, route_owner_key
from tools.host_page_render import render_html


class PageRequests:
    def assert_read_only_platform(self):
        self.assertTrue(all(action in {"get_friend_list", "get_stranger_info", "get_group_list", "get_group_info", "get_login_info"}
                            for action, _ in self.native.platform.client._api._wsr_api.calls))

    def register(self):
        self.controller.register_routes()
        self.handler = next(handler for path, handler, _, _ in self.native.registered_web_apis
                            if path == PREFIX + "/page/targets")

    async def request(self, *, username="admin", query=""):
        request = Request({"type": "http", "method": "GET", "path": PREFIX + "/page/targets",
                           "headers": [], "query_string": query.encode()})
        with bind_request_context(PluginRequest(request, username=username)):
            return await self.handler()


class SinglePageTests(PageRequests, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-page-api-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                          NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(self.native, support.host_config(), Path(temporary.name),
                                                         native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.register()

    async def test_directory_requires_loaded_host_login_and_current_core_tier(self):
        for username in (None, "", "api_key:synthetic"):
            self.assertEqual((await self.request(username=username))["data"]["code"], "dashboard_identity_required")
        self.assertEqual((await self.request())["data"]["code"], "staging_not_initialized")
        (await self.controller.initialize_profile()).unwrap()
        self.assertEqual((await self.request(query="subject_id=another"))["data"]["code"], "page_request_invalid")
        listed = (await self.request())["data"]
        self.assertNotIn("mode", listed)
        self.assertEqual(listed["operator_name"], "admin")
        self.assertEqual(listed["acquaintance_api"], PREFIX + "/acquaintance")
        self.assertEqual(listed["accounts_api"], PREFIX + "/accounts")
        target = listed["targets"][0]
        self.assertEqual(target["participant_id"], "200")
        self.assertEqual(target["roles"], [{"role_id":self.controller.resolved_settings().role_id,
            "role_name": "original",
            **{name + "_api": PREFIX + "/" + name for name in
               ("memory", "state", "life", "relationships", "growth", "reply", "model", "config",
                # V1 迁移写的是角色设置，所以它的地址也在角色这一层：
                # 单目标是固定路径，多目标由 target_inventory 每角色各生成一份。
                "legacy", "persona")},
            "perception_native_api": PREFIX + "/perception-native",
            "reality_access_api": PREFIX + "/reality-access",
            "screen_access_api": PREFIX + "/screen-access",
            # QQ 空间拓展与 Reality/Screen 同层：按角色安装与授权，所以地址跟角色走。
            "qzone_extension_api": PREFIX + "/qzone-extension",
            "game_access_api": PREFIX + "/game-access",
            "image_material_api": PREFIX + "/image-materials"}])
        self.assertNotIn("provider", json.dumps(listed))
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                        expected_revision=policy.revision, observed_at=support.NOW)
        self.assertEqual((await self.request())["data"]["code"], "core_access_required")
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assert_read_only_platform()

    async def test_page_directory_unregisters_and_retained_handler_refuses_stopped_host(self):
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        self.assertEqual((await self.request())["data"]["code"], "staging_host_terminated")

    async def test_single_target_acquaintance_directory_uses_the_configured_role(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        directory = routes[PREFIX + "/acquaintance"]
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(PersonAcquaintanceDirectory, "directory",
                             return_value={"persons": [], "counts": {"total": 0}}) as listing:
            response = await directory()
        self.assertEqual(response["status"], "ok", response)
        settings = self.controller.resolved_settings()
        listing.assert_called_once_with([{
            "platform": settings.platform_id,
            "bot_id": settings.bot_id,
            "account_id": settings.account_id,
            "role_id": settings.role_id,
        }])
        self.assertEqual(response["data"]["groups"], [])
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assert_read_only_platform()

    async def test_stored_relationship_directory_returns_before_native_discovery_or_name_queries(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        directory = routes[PREFIX + "/acquaintance"]
        native_query = self.native.platform.client._api._wsr_api
        native_query.call_action = AsyncMock(side_effect=AssertionError("first paint must not query OneBot"))
        request = Request({"type": "http", "method": "GET", "path": PREFIX + "/acquaintance",
                           "headers": [], "query_string": b"read_mode=stored"})
        before_grants = self.controller.runtime.read_all("SELECT grant_id FROM identity_grants ORDER BY grant_id", ())
        with bind_request_context(PluginRequest(request, username="admin")), \
                patch.object(self.controller.session_topology, "inventory", AsyncMock(
                    side_effect=AssertionError("first paint must not discover sessions"))) as inventory:
            response = await asyncio.wait_for(directory(), 1)
        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(response["data"]["directory_refresh_pending"])
        self.assertTrue(response["data"]["persons"])
        inventory.assert_not_awaited()
        native_query.call_action.assert_not_awaited()
        self.assertEqual(self.controller.runtime.read_all("SELECT grant_id FROM identity_grants ORDER BY grant_id", ()),
                         before_grants)

    async def test_relationship_directory_only_discovers_group_sessions(self):
        (await self.controller.initialize_profile()).unwrap()
        directory = next(handler for path, handler, _, _ in self.native.registered_web_apis
                         if path == PREFIX + "/acquaintance")
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(self.controller.session_topology, "inventory", AsyncMock(return_value=[])) as inventory:
            response = await directory()
        self.assertEqual(response["status"], "ok", response)
        self.assertFalse(response["data"]["directory_refresh_pending"])
        inventory.assert_awaited_once_with(scope_kind="group")

    async def test_stored_directory_keeps_login_and_strict_query_validation(self):
        (await self.controller.initialize_profile()).unwrap()
        directory = next(handler for path, handler, _, _ in self.native.registered_web_apis
                         if path == PREFIX + "/acquaintance")
        for query, username, code in (
                (b"read_mode=stored", "api_key:synthetic", "dashboard_identity_required"),
                (b"read_mode=other", "admin", "acquaintance_request_invalid"),
                (b"read_mode=stored&read_mode=stored", "admin", "acquaintance_request_invalid"),
                (b"read_mode=stored&subject_id=another", "admin", "acquaintance_request_invalid")):
            request = Request({"type": "http", "method": "GET", "path": PREFIX + "/acquaintance",
                               "headers": [], "query_string": query})
            with bind_request_context(PluginRequest(request, username=username)):
                response = await directory()
            self.assertEqual(response["data"]["code"], code, response)

    async def test_group_used_for_model_replies_appears_from_relationship_network(self):
        (await self.controller.initialize_profile()).unwrap()
        settings = self.controller.resolved_settings()
        runtime = self.controller.runtime
        owner = route_owner_key(runtime.installation_id, settings.platform_id, settings.bot_id,
                                settings.account_id, "450", settings.role_id)
        document = {"version": VERSION,
                    "group": {"installation_id": runtime.installation_id,
                              "platform_id": settings.platform_id, "bot_id": settings.bot_id,
                              "account_id": settings.account_id, "group_id": "450",
                              "role_id": settings.role_id},
                    "members": {"700": {"account_id": "700", "aliases": [], "message_count": 3,
                                          "active_days": 2, "first_seen": "2026-10-01T00:00:00+00:00",
                                          "last_seen": "2026-10-01T01:00:00+00:00", "recent": []}},
                    "edges": {}, "updated_at": "2026-10-01T01:00:00+00:00"}
        with runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                               (metadata_key(owner), json.dumps(document)))
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        directory = routes["/astrbot_plugin_private_companion/acquaintance"]
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(PersonAcquaintanceDirectory, "directory",
                             return_value={"persons": [], "counts": {"total": 0}}):
            response = await directory()
        self.assertEqual(response["status"], "ok", response)
        group = response["data"]["groups"][0]
        self.assertEqual(group["group_id"], "450")
        self.assertFalse(group["configured"])
        self.assertEqual(response["data"]["group_networks"][0]["members"][0]["native_account_id"], "700")

    async def test_native_group_session_is_listed_without_importing_v2_permissions(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        directory = routes["/astrbot_plugin_private_companion/acquaintance"]
        topology = self.controller.session_topology
        self.native.platform.client._wsr_api_clients = {"100": object()}
        umo = "qq-main:GroupMessage:450"
        self.native.conversation_manager.session_conversations = {umo: "conversation-1"}
        before_grants = self.controller.runtime.read_all("SELECT grant_id FROM identity_grants ORDER BY grant_id", ())
        discovered = await topology.inventory()
        discovered_groups = [row for row in discovered if row["scope_kind"] == "group"]
        self.assertEqual(len(discovered_groups), 1)
        self.assertEqual(discovered_groups[0]["unified_msg_origin"], umo)
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(PersonAcquaintanceDirectory, "directory",
                             return_value={"persons": [], "counts": {"total": 0}}):
            response = await directory()
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(len(response["data"]["groups"]), 1)
        group = response["data"]["groups"][0]
        self.assertEqual((group["platform_id"], group["account_id"], group["group_id"]),
                         ("qq-main", "100", "450"))
        self.assertTrue(group["bot_id"])
        self.assertTrue(group["discovered"])
        self.assertFalse(group["configured"])
        self.assertEqual((group["member_count"], group["msg_count"]), (0, 0))
        self.assertNotIn("unified_msg_origin", group)
        self.assertNotIn("conversation_id", group)
        self.assertNotIn("session_key", group)
        self.assertNotIn("target_id", group)
        self.assertEqual(response["data"]["group_networks"], [])
        self.assertEqual(self.controller.runtime.read_all("SELECT grant_id FROM identity_grants ORDER BY grant_id", ()),
                         before_grants)
        self.assert_read_only_platform()

    async def test_native_group_directory_failure_is_reported_with_its_cause(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        directory = routes["/astrbot_plugin_private_companion/acquaintance"]
        topology = self.controller.session_topology
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(PersonAcquaintanceDirectory, "directory",
                             return_value={"persons": [], "counts": {"total": 0}}), \
                patch.object(topology, "inventory", new=AsyncMock(
                    side_effect=RuntimeError("simulated session catalog failure"))):
            response = await directory()

        self.assertEqual(response["status"], "error", response)
        self.assertEqual(response["data"]["code"], "acquaintance_native_directory_unavailable")
        self.assertIn("RuntimeError: simulated session catalog failure", response["reason"])

    async def test_model_route_requires_dashboard_identity_and_valid_request(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        status = routes[PREFIX + "/model"]
        configure = routes[PREFIX + "/model/configure"]
        with patch("astrbot_host.model_api.request", NS(username="admin", query={})):
            response = await status()
        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(response["data"]["tasks"])
        self.assertEqual(len(response["data"]["tasks"][0]["edit_token"]), 64)
        with patch("astrbot_host.model_api.request", NS(username="api_key:synthetic", query={})):
            self.assertEqual((await status())["data"]["code"], "dashboard_identity_required")

        async def malformed():
            return b'{"kind":"conversation.reply","kind":"duplicate"}'

        with patch("astrbot_host.model_api.request", NS(username="admin", query={}, body=malformed)):
            self.assertEqual((await configure())["data"]["code"], "model_request_invalid")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_model_status_projection_runs_off_the_event_loop(self):
        (await self.controller.initialize_profile()).unwrap()
        import threading
        import astrbot_host.model_api as model_api
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        status = routes[PREFIX + "/model"]
        main_thread = threading.get_ident()
        workers = []
        original = model_api.ModelManagement.status

        def tracked(manager, *args, **kwargs):
            workers.append(threading.get_ident())
            return original(manager, *args, **kwargs)

        with patch.object(model_api.ModelManagement, "status", new=tracked), \
                patch("astrbot_host.model_api.request", NS(username="admin", query={})):
            response = await status()
        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(workers)
        self.assertNotIn(main_thread, workers)
    async def test_setup_reads_pinned_identity_without_starting_the_host(self):
        (await self.controller.initialize_profile()).unwrap()
        before = self.controller.status()["session_state"]
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        with patch("astrbot_host.setup_api.request", NS(username="admin", query={})):
            progress = await routes[PREFIX + "/setup/status"]()
            choices = await routes[PREFIX + "/setup/targets"]()
        self.assertEqual(progress["status"], "ok", progress)
        self.assertEqual(choices["status"], "ok", choices)
        self.assertTrue(progress["data"]["binding"]["pinned"])
        pinned = self.controller._read_intent()
        self.assertEqual(progress["data"]["binding"]["role_id"], pinned.persona.role.subject_id)
        self.assertEqual(set(choices["data"]), {"accounts", "personas", "targets", "directory"})
        from astrbot_host.legacy_api import PREFIX as LEGACY_PREFIX
        self.assertTrue(any(role.get("legacy_api") == LEGACY_PREFIX
                            for target in choices["data"]["targets"] for role in target["roles"]))
        self.assertEqual(set(choices["data"]["directory"]), {"complete", "issues"})
        self.assertIsInstance(choices["data"]["directory"]["complete"], bool)
        self.assertIsInstance(choices["data"]["directory"]["issues"], list)
        self.assertEqual(self.controller.resolved_settings().role_id, pinned.persona.role.subject_id)
        self.assertEqual(self.controller.status()["session_state"], before)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assert_read_only_platform()

    async def test_directory_resolves_platform_names_without_changing_the_target_or_api(self):
        (await self.controller.initialize_profile()).unwrap()
        async def metadata(action, **params):
            self.assertEqual(params["self_id"], "100")
            return {"get_friend_list": [{"user_id": 200, "remark": "朋友备注", "nickname": "朋友昵称"}],
                    "get_login_info": {"user_id": 100, "nickname": "小伴"}}[action]
        self.native.platform.client._api._wsr_api.call_action = AsyncMock(side_effect=metadata)
        target = (await self.request())["data"]["targets"][0]
        self.assertEqual(target["display_name"], "朋友备注")
        self.assertEqual(target["account_name"], "小伴")
        self.assertEqual(target["participant_id"], "200")
        self.assertEqual(target["roles"][0]["memory_api"], PREFIX + "/memory")

    async def test_name_lookup_uses_the_pinned_window_after_target_config_changes(self):
        (await self.controller.initialize_profile()).unwrap()
        self.controller.config["target"].update(account_id="101", participant_id="201")
        self.controller.refresh_settings()
        self.assertEqual(self.controller.settings.account_id, "101")

        async def metadata(action, **params):
            self.assertEqual(params["self_id"], "100")
            return {"get_friend_list": [{"user_id": 200, "nickname": "已绑定的朋友"}],
                    "get_login_info": {"user_id": 100, "nickname": "已绑定的 Bot"}}[action]

        self.native.platform.client._api._wsr_api.call_action = AsyncMock(side_effect=metadata)
        target = (await self.request())["data"]["targets"][0]
        self.assertEqual((target["account_id"], target["participant_id"]), ("100", "200"))
        self.assertEqual(target["display_name"], "已绑定的朋友")
        self.assertEqual(target["account_name"], "已绑定的 Bot")

    async def test_unreadable_pinned_window_returns_an_explicit_directory_error(self):
        (await self.controller.initialize_profile()).unwrap()
        self.controller.intent_path.write_text("{broken pin\n", encoding="utf-8", newline="\n")
        result = await self.request()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"]["code"], "staging_intent_invalid__invalid")

    async def test_revoked_access_during_name_lookup_cannot_return_directory(self):
        (await self.controller.initialize_profile()).unwrap()
        started, release = asyncio.Event(), asyncio.Event()
        async def metadata(action, **params):
            started.set()
            await release.wait()
            return []
        self.native.platform.client._api._wsr_api.call_action = AsyncMock(side_effect=metadata)
        pending = asyncio.create_task(self.request())
        await asyncio.wait_for(started.wait(), 1)
        operator = dashboard_operator(self.controller.runtime, "admin", at=support.NOW)
        policy = ObjectAccessPolicy(self.controller.runtime)
        async with self.controller._control_lock:
            policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                            expected_revision=policy.revision, observed_at=support.NOW)
        release.set()
        self.assertEqual((await pending)["data"]["code"], "core_access_required")

    async def test_acquaintance_resolves_historical_group_names_and_keeps_admin_names(self):
        (await self.controller.initialize_profile()).unwrap()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        settings = self.controller.resolved_settings()
        person = {"subject_id": "onebot-user:200", "role_id": settings.role_id,
                  "platform": settings.platform_id, "account_id": settings.account_id,
                  "bot_id": settings.bot_id, "native_account_id": "200",
                  "display_name": "管理员称呼", "admin_nickname": "管理员称呼", "name_basis": "administrator",
                  "private_chat": False, "group_member": True,
                  "groups": {"300": {"msg_count": 2}}}
        async def metadata(action, **params):
            return {"get_group_list": [{"group_id": 300, "group_name": "历史读书群"}],
                    "get_login_info": {"user_id": 100, "nickname": "小伴"}}[action]
        api = self.native.platform.client._api._wsr_api
        api.call_action = AsyncMock(side_effect=metadata)
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(PersonAcquaintanceDirectory, "directory",
                             return_value={"persons": [person], "counts": {"total": 1}}):
            response = await routes[PREFIX + "/acquaintance"]()
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["persons"][0]["display_name"], "管理员称呼")
        group = response["data"]["groups"][0]
        self.assertEqual(group["display_name"], "历史读书群")
        self.assertFalse(group["configured"])
        self.assertEqual((group["member_count"], group["msg_count"]), (1, 2))
        self.assertNotIn("get_friend_list", [call.args[0] for call in api.call_action.await_args_list])

    async def test_sdk_discovers_same_voxel_shell_and_injects_scoped_bridge(self):
        plugin_root = Path(__file__).resolve().parents[1]
        service = PluginPageService(NS(plugin_store_path=str(plugin_root.parent)))
        plugin = NS(root_dir_name=plugin_root.name, reserved=False)
        pages = await service.discover_plugin_pages(plugin)
        self.assertEqual([page.name for page in pages], ["陪伴小屋"])
        page = next(page for page in pages if page.name == "陪伴小屋")
        entry = await service.resolve_plugin_page_file(plugin, page.name, "")
        html = render_html(service, entry.read_text(encoding="utf-8"), plugin_root.name, page.name,
                           "index.html", theme="light")
        self.assertIn("assets/voxel-room.js", html)
        self.assertIn("memory-page.js", html)
        self.assertIn("models-page.js", html)
        self.assertIn("perception-native-page.js", html)
        self.assertIn("/api/plugin/page/bridge-sdk.js", html)
        self.assertIn('name="companion-api-host"', html)
        # GIF 地址必须来自静态 HTML 属性；内联脚本只负责从 template 中随机克隆，
        # 不再把裸相对路径交给浏览器。
        if callable(getattr(service, "process_plugin_page_html", None)):
            # 4.29 起视图由路径令牌路由提供，相对地址按自身解析即可，宿主不再改写，
            # 因此这里钉住两件事：地址仍是静态属性的相对路径，且引用的文件确实存在。
            page_root = await service.resolve_plugin_page_root(plugin, page.name)
            for asset in ("room-loading-tea-coffee.gif", "room-loading-mug.gif"):
                self.assertIn(f'src="assets/{asset}?', html)
                self.assertTrue((page_root / "assets" / asset).is_file())
        else:
            # 4.28 以前的宿主把相对资源改写成带资源的绝对路由，这里钉住改写结果。
            for asset in ("room-loading-tea-coffee.gif", "room-loading-mug.gif"):
                self.assertRegex(
                    html,
                    rf'src="/api/plugin/page/content/{re.escape(plugin_root.name)}/[^" ]+/assets/{asset}\?'
                )
                self.assertNotIn(f'src="assets/{asset}', html)
        host_page = await service.resolve_plugin_page_file(plugin, "陪伴小屋", "workspaces/host/index.html")
        host_html = host_page.read_text(encoding="utf-8")
        self.assertRegex(host_html, r'<script src="app\.js(?:\?[^\"]*)?" defer></script>')
        self.assertIn('id="topology-canvas"', host_html)
        self.assertIn("embedded-bridge.js", host_html)
        content = NS(root_dir_name="astrbot_plugin_content_companion", reserved=False)
        self.assertNotIn("framework-preview-v1", [p.name for p in await service.discover_plugin_pages(content)])


class MultiPageTests(PageRequests, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_setup_targets_publish_migration_paths_before_runtime_starts(self):
        from astrbot_host.role_paths import HOST_PREFIX, role_legacy_paths
        from astrbot_host.setup_api import PREFIX as SETUP_PREFIX

        self.controller = self.make_controller(multi_config(group_targets=[dict(
            target_id="groups", platform_id="qq-main", account_id="100",
            bot_id="bot-a", group_id="300")]))
        self.controller.register_routes()
        routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        with patch("astrbot_host.setup_api.request", NS(username="admin", query={})):
            response = await routes[SETUP_PREFIX + "/targets"]()
        self.assertEqual(response["status"], "ok", response)
        targets = response["data"]["targets"]
        private_roles = [role for target in targets if target["scope_kind"] == "private"
                         for role in target["roles"]]
        group_roles = [role for target in targets if target["scope_kind"] == "group"
                       for role in target["roles"]]
        self.assertTrue(private_roles)
        self.assertTrue(group_roles)
        for source_target in targets:
            for role in source_target["roles"]:
                expected = role_legacy_paths(source_target["target_id"], role["role_id"],
                                             source_target["scope_kind"])
                self.assertEqual(role["legacy_api"], expected["legacy_api"])
                self.assertEqual(role["legacy_group_data_api"], expected["legacy_group_data_api"])
                if expected["legacy_api"]:
                    self.assertTrue(role["legacy_api"].startswith(HOST_PREFIX + "/targets/"))
        self.assertEqual(self.controller.runtime, None)

    async def test_historical_group_for_removed_role_is_not_marked_configured(self):
        from astrbot_host.acquaintance_api import _group_relationship_networks

        self.controller = self.make_controller(multi_config(group_targets=[dict(target_id="groups",
            platform_id="qq-main", account_id="100", bot_id="bot-a", group_id="*")]))
        (await self.controller.initialize_profile()).unwrap()
        people = [{"platform": "qq-main", "account_id": "100", "bot_id": "bot-a",
                   "role_id": "removed-role", "native_account_id": "200", "groups": {"450": {"msg_count": 2}}},
                  {"platform": "qq-main", "account_id": "100", "bot_id": "bot-a",
                   "role_id": "role-a", "native_account_id": "200", "groups": {"450": {"msg_count": 3}}}]
        networks = _group_relationship_networks(self.controller.runtime, self.controller.settings, people, [])
        self.assertEqual({network["role_id"]: network["configured"] for network in networks},
                         {"role-a": True, "removed-role": False})
        self.assertEqual({network["role_id"]: network["members"][0]["acquaintance"]["group"]["message_count"]
                          for network in networks}, {"role-a": 3, "removed-role": 2})

    async def test_automatic_native_group_projection_includes_only_the_mapped_role(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock
        from astrbot_host.acquaintance_api import AcquaintanceWebAPI
        self.controller = self.make_controller(multi_config(group_targets=[dict(target_id="groups",
            platform_id="qq-main", account_id="100", bot_id="bot-a", group_id="*")]))
        (await self.controller.initialize_profile()).unwrap()
        api = AcquaintanceWebAPI(self.controller)
        native = [{"native": True, "managed": True, "scope_kind": "group", "platform_id": "qq-main",
                   "account_id": "100", "bot_id": "bot-a", "group_id": "450",
                   "roles": [{"role_id": "role-a", "persona_ids": ["original"], "role_name": "original"}]}]
        with patch.object(self.controller.session_topology, "inventory", AsyncMock(return_value=native)), \
                patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch("astrbot_host.acquaintance_api.display_names", return_value=NS(
                    persons=AsyncMock(side_effect=lambda rows: rows), targets=AsyncMock(side_effect=lambda rows: rows))):
            result = await api.directory()
            self.assertEqual(result["status"], "ok", result)
            networks = result["data"]["group_networks"]
            self.assertEqual([(row["group_id"], row["role_id"], row["configured"]) for row in networks],
                             [("450", "role-a", True)])
            self.assertEqual(result["data"]["groups"][0]["roles"], native[0]["roles"])
            target = self.controller.settings.group_targets[0]
            self.controller.settings = replace(self.controller.settings, group_targets=(replace(target, bot_id="bot-b"),))
            result = await api.directory()
            self.assertTrue(all(not row["configured"] for row in result["data"]["group_networks"]))

    async def test_same_native_user_on_different_accounts_roles_and_group_have_distinct_paths(self):
        config = multi_config(group_targets=[dict(target_id="reading", platform_id="qq-main", account_id="100",
                                                  bot_id="bot-a", group_id="300")])
        self.controller = self.make_controller(config)
        self.register()
        (await self.controller.initialize_profile()).unwrap()
        result = await self.request()
        self.assertEqual(result["status"], "ok", result)
        targets = result["data"]["targets"]
        private = [target for target in targets if target["scope_kind"] == "private"]
        self.assertEqual([target["participant_id"] for target in private], ["200", "200"])
        paths = [role["memory_api"] for target in private for role in target["roles"]]
        self.assertEqual(len(set(paths)), 4)
        routes = {path for path, *_ in self.native.registered_web_apis}
        self.assertTrue(all(path in routes and path + "/reflections/queue" in routes for path in paths))
        state_paths = [role["state_api"] for target in private for role in target["roles"]]
        self.assertEqual(len(set(state_paths)), 4)
        self.assertTrue(all(path in routes for path in state_paths))
        for name in ("life", "relationships", "growth", "model"):
            paths = [role[name + "_api"] for target in private for role in target["roles"]]
            self.assertEqual(len(set(paths)), 4)
            suffix = "/configure" if name == "model" else "/command"
            self.assertTrue(all(path in routes and path + suffix in routes for path in paths))
        paths = [role["perception_native_api"] for target in private for role in target["roles"]]
        self.assertEqual(len(set(paths)), 4)
        self.assertTrue(all(path in routes and path + "/configure" in routes for path in paths))
        persona_paths = [role["persona_api"] for target in private for role in target["roles"]]
        self.assertEqual(len(set(persona_paths)), 4)
        self.assertTrue(all(path in routes and path + "/command" in routes for path in persona_paths))
        screen_paths = [role["screen_access_api"] for target in private for role in target["roles"]]
        self.assertEqual(len(set(screen_paths)), 4)
        self.assertTrue(all(path in routes and path + "/configure" in routes for path in screen_paths))
        group = next(target for target in targets if target["scope_kind"] == "group")
        self.assertTrue(all(role["memory_api"] is None for role in group["roles"]))
        group_model_paths = [role["model_api"] for role in group["roles"]]
        self.assertTrue(all(path and path in routes and path + "/configure" in routes
                            for path in group_model_paths))
        self.assertTrue(all(role["perception_native_api"] is None for role in group["roles"]))
        self.assertTrue(all(role["persona_api"] is None for role in group["roles"]))
        self.assertTrue(all(role["screen_access_api"] is None for role in group["roles"]))
        # 群目标没有「角色设置」这回事，所以迁移地址同样是 None，
        # 而不是一个读不出东西的地址。
        self.assertTrue(all(role["legacy_api"] is None for role in group["roles"]))
        migration_paths = [role["legacy_group_data_api"] for role in group["roles"]]
        self.assertTrue(all(path + "/plan" in routes and path + "/apply" in routes
                            for path in migration_paths))
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
