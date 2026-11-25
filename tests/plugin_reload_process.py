"""Exercise real AstrBot disable, enable and reload with isolated V2 storage."""

import asyncio
from functools import partial
import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

# This must run before importing AstrBot: core.__init__ derives DB_PATH at import time.
from tests.astrbot_data_root import ASTRBOT_ROOT, assert_sdk_database_isolated

# Redirect the SDK's import-time data root before importing its manager.
from tests.multi_host_support import MultiNativeContext, multi_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.test_management_access import NOW
from astrbot.api.star import StarTools
from astrbot.api.web import PluginRequest, bind_request_context
from astrbot.core import db_helper
from astrbot.core.star.star import star_map, star_registry
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.star.star_manager import PluginManager
from starlette.requests import Request


class FixtureManager(PluginManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_next_controller_init = False

    async def _import_plugin_with_dependency_recovery(self, path, module_str, **_):
        # Use the host's exact import semantics, with synthetic native ports and
        # no package installation or external providers in the test process.
        entry = __import__(path, fromlist=[module_str])
        if not getattr(entry, "_reload_fixture", False):
            adapter = importlib.import_module("companion_v2.adapters.astrbot.staging")
            types = adapter.NativeAdapterTypes("4.28.2", FakeEvent, FakeProvider,
                                              SimpleNamespace, SimpleNamespace, FakeReverseApi)
            controller = partial(entry.AstrBotMultiController, native_types=types, now=lambda: NOW)
            if self.fail_next_controller_init:
                self.fail_next_controller_init = False

                def fail_once(*_args, **_kwargs):
                    raise ValueError("synthetic native adapter contract mismatch")

                entry.AstrBotMultiController = fail_once
            else:
                entry.AstrBotMultiController = controller
            entry._reload_fixture = True
        return entry


async def run():
    assert_sdk_database_isolated(db_helper)
    await db_helper.initialize()
    db_helper.inited = True
    source = Path(__file__).resolve().parents[1]
    # AstrBot imports by the plugin directory but registers the public metadata
    # name. These intentionally differ during the v7 migration workspace.
    plugin_dir_name = source.name
    plugin_name = "astrbot_plugin_private_companion"
    entry_name = "data.plugins." + plugin_dir_name + ".main"
    for name, paths in (("data", []), ("data.plugins", [str(source.parent)])):
        package = ModuleType(name)
        package.__path__ = paths
        sys.modules[name] = package
    # The real host imports via its namespace, without the plugin root on the
    # search path. A test launched from the checkout would otherwise hide this bug.
    sys.path[:] = [path for path in sys.path if Path(path or ".").resolve() != source]
    native = MultiNativeContext()
    native.get_registered_star = lambda name: next((item for item in star_registry if item.name == name), None)
    native.get_all_stars = lambda: list(star_registry)
    native.conversation_manager.session_conversations = {
        "qq-main:FriendMessage:200": "conversation-a", "qq-second:FriendMessage:200": "conversation-b"}
    manager = FixtureManager(native, {})
    manager.plugin_store_path = str(source.parent)
    manager._get_plugin_modules = lambda: [{"pname": plugin_dir_name, "module": "main"}]
    foreign = ModuleType("companion_v2.foreign_reload_fixture")
    foreign.__file__ = str(source.parent / "another-plugin" / "foreign.py")
    sys.modules[foreign.__name__] = foreign
    with TemporaryDirectory(prefix="companion-plugin-reload-") as temporary:
        root = Path(temporary)
        manager.plugin_config_path = str(root / "config")
        Path(manager.plugin_config_path).mkdir()
        config_file = Path(manager.plugin_config_path) / (plugin_dir_name + "_config.json")
        config_file.write_text(json.dumps(multi_config(), ensure_ascii=False) + "\n",
                               encoding="utf-8", newline="\n")
        initial_owner = initial_manifest = initial_routes = initial_handlers = saved_config = None
        starts = 0

        async def check_running():
            nonlocal initial_owner, initial_manifest, initial_routes, initial_handlers, saved_config, starts
            metadata = native.get_registered_star(plugin_name)
            assert metadata and metadata.activated and metadata.star_cls
            controller = metadata.star_cls.staging
            for _ in range(200):
                if controller._accepting:
                    break
                await asyncio.sleep(0.025)
            assert controller._accepting, controller.last_failure
            assert controller.session.application.state == "running"
            await controller.session.account_recovery.refresh(wait=True)
            assert metadata.module is sys.modules[entry_name]
            assert metadata.star_cls_type is metadata.module.PrivateCompanionV2StagingPlugin
            refs = importlib.import_module("companion_v2.foundation.refs")
            context = controller.authority.context("first", "role-a")
            assert isinstance(context.scope, refs.ScopeRef)
            assert isinstance(context.actor.subject, refs.SubjectRef)
            assert context.role_binding.role.kind is refs.SubjectKind.BOT
            owner = (context.actor.subject.subject_id, context.scope, context.role_binding.role.subject_id)
            owner = json.dumps([owner[0], vars(owner[1]), owner[2]], default=lambda item: item.value, sort_keys=True)
            manifest_key = importlib.import_module(metadata.module.__package__ + ".astrbot_host.multi_authority").MANIFEST
            manifest = controller.authority.read(manifest_key)
            routes = {(route, tuple(methods)) for route, _, methods, _ in native.registered_web_apis}
            assert len(routes) == len(native.registered_web_apis)
            handlers = [handler for handler in star_handlers_registry if handler.handler_module_path == entry_name]
            names = {handler.handler_full_name for handler in handlers}
            assert len(names) == len(handlers)
            assert all(isinstance(handler.handler, partial) and handler.handler.args[0] is metadata.star_cls
                       for handler in handlers)
            assert len([item for item in star_registry if item.module_path == entry_name]) == 1
            if initial_owner is None:
                initial_owner, initial_manifest, initial_routes, initial_handlers = owner, manifest, routes, names
                saved_config = config_file.read_bytes()
            assert (owner, manifest, routes, names) == (initial_owner, initial_manifest, initial_routes, initial_handlers)
            assert config_file.read_bytes() == saved_config
            state_path = next(role["state_api"] for target in controller.web_api.target_inventory()
                              for role in target["roles"] if role["role_id"] == "role-a")
            handler = next(handler for route, handler, methods, _ in native.registered_web_apis
                           if route == state_path and methods == ["GET"])
            req = Request({"type": "http", "method": "GET", "path": state_path,
                           "headers": [], "query_string": b""})
            with bind_request_context(PluginRequest(req, username="admin")):
                result = await handler()
            assert result["status"] == "ok", result
            assert native.provider.client.state.calls == []
            assert all(action in {"get_login_info", "get_friend_list", "get_stranger_info"}
                       for platform in native.platforms.values() for action, _ in platform.client._api._wsr_api.calls)
            starts += 1
            return metadata.star_cls

        def check_stopped(plugin):
            assert plugin.staging.runtime is None
            assert native.registered_web_apis == []
            assert entry_name not in sys.modules
            assert not any(name == "companion_v2" or (name.startswith("companion_v2.") and name != foreign.__name__)
                           for name in sys.modules)
            assert not any(name.startswith("data.plugins." + plugin_dir_name + ".") for name in sys.modules)
            assert str(source) not in sys.path
            assert sys.modules[foreign.__name__] is foreign
            assert not hasattr(sys.modules["data.plugins"], plugin_dir_name)

        def check_failed_initialization_cleanup():
            assert entry_name not in sys.modules
            assert not any(name == "companion_v2" or name.startswith("companion_v2.")
                           for name in sys.modules if name != foreign.__name__)
            assert not any(name == "astrbot_host" or name.startswith("astrbot_host.")
                           for name in sys.modules)
            assert str(source) not in sys.path
            assert not hasattr(sys.modules["data.plugins"], plugin_dir_name)
            assert native.registered_web_apis == []
            assert sys.modules[foreign.__name__] is foreign

        with patch.object(StarTools, "get_data_dir", return_value=root / "v2"):
            try:
                platforms = native.platforms
                native.platforms = {}
                manager.fail_next_controller_init = True
                success, error = await manager.load(specified_module_path=entry_name)
                assert not success, error
                check_failed_initialization_cleanup()
                success, error = await manager.load(specified_module_path=entry_name)
                assert success, error
                await asyncio.sleep(0.05)
                native.platforms = platforms
                plugin = await check_running()
                for cycle in range(3):
                    previous_plugin = plugin
                    old_entry = star_map[entry_name].module
                    await manager.turn_off_plugin(plugin_name)
                    check_stopped(plugin)
                    if cycle == 1:
                        success, error = await manager.reload(plugin_name)
                        assert success, error
                        assert not star_map[entry_name].activated and star_map[entry_name].star_cls is None
                        assert not native.registered_web_apis
                    await manager.turn_on_plugin(plugin_name)
                    plugin = await check_running()
                    assert star_map[entry_name].module is not old_entry
                    await previous_plugin.terminate()
                    assert star_map[entry_name].module is sys.modules[entry_name]
                    assert plugin.staging._accepting and native.registered_web_apis
                entry = star_map[entry_name].module
                draining = entry.PrivateCompanionV2StagingPlugin.__new__(entry.PrivateCompanionV2StagingPlugin)
                draining.staging = SimpleNamespace(runtime=object(), terminate=AsyncMock())
                await draining.terminate()
                assert sys.modules[entry_name] is entry and "companion_v2" in sys.modules
                assert str(source) not in sys.path
                assert importlib.util.find_spec("companion_v2.model_gateway.replacement_rules") is not None
                draining.staging.runtime = None
                await draining.terminate()
                assert sys.modules[entry_name] is entry and plugin.staging._accepting
                old_entry = star_map[entry_name].module
                success, error = await manager.reload(plugin_name)
                assert success, error
                plugin = await check_running()
                assert star_map[entry_name].module is not old_entry
            finally:
                metadata = native.get_registered_star(plugin_name)
                if metadata and metadata.activated and metadata.star_cls:
                    plugin = metadata.star_cls
                    await manager.turn_off_plugin(plugin_name)
                    check_stopped(plugin)
    print(json.dumps({"startup_cycles": starts, "disable_enable_cycles": 3, "disabled_reload": True,
                      "active_reload": True, "saved_profile_restored": True, "same_role_owner": True,
                      "state_routes_readable": True, "duplicate_handlers": 0, "duplicate_routes": 0,
                      "foreign_modules_preserved": True, "deferred_drain_imports": True,
                      "failed_constructor_reload": True,
                      "model_calls": 0, "platform_sends": 0,
                      "astrbot_root_isolated": str(db_helper.db_path).startswith(str(ASTRBOT_ROOT))}))


async def main():
    try:
        await run()
    finally:
        await db_helper.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
