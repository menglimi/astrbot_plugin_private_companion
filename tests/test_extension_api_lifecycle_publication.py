from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from typing import Any

from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend
from astrbot_plugin_private_companion.storage.path_generation import (
    activate_persistence_owner,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class _LifecycleBase:
    def __init__(self, context: Any) -> None:
        self.context = context
        self.bootstrap_steps: list[str] = []


class _ExtensionAPI:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.story_state = "created"

    def _activate_story_migration_api(self) -> bool:
        if self.story_state != "created":
            return False
        self.story_state = "ready"
        return True

    def _supersede_story_migration_api(self) -> None:
        if self.story_state in {"created", "ready"}:
            self.story_state = "superseded"

    def _close_story_migration_api(self) -> None:
        if self.story_state != "superseded":
            self.story_state = "closed"

    def bridge_lifecycle_status(self) -> dict[str, Any]:
        return {
            "active": self.story_state == "ready",
            "state": self.story_state,
            "instance_generation": "test-generation",
        }


def _record_bootstrap_step(host: Any, name: str) -> None:
    host.bootstrap_steps.append(name)
    if getattr(host.config, "fail_at", "") == name:
        raise RuntimeError(f"bootstrap failed: {name}")


def _initialize_entrypoint(
    host: Any,
    _context: Any,
    config: Any,
    *,
    extension_api_factory: Any,
) -> None:
    host.config = config
    host.extension_api = extension_api_factory(host)
    host._persistence_owner_token = f"lifecycle-{id(host)}"
    host.data_file = ROOT / ".lifecycle-publication-companions.json"
    _record_bootstrap_step(host, "entrypoint")


def _initialize_config(host: Any, _config: Any) -> None:
    _record_bootstrap_step(host, "config")


def _initialize_runtime(host: Any) -> None:
    _record_bootstrap_step(host, "runtime")


def _initialize_post_runtime(host: Any, _config: Any) -> None:
    _record_bootstrap_step(host, "post_runtime")


class _StoryAuthorityError(RuntimeError):
    code = "story_test_error"


async def _resume_story_handoff(_host: Any) -> None:
    return None


def _load_lifecycle_source() -> tuple[dict[str, Any], ast.ClassDef]:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    method_names = {"__init__", "initialize", "terminate"}
    methods = [
        copy.deepcopy(node)
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in method_names
    ]
    lifecycle_class = ast.ClassDef(
        name="PrivateCompanionPlugin",
        bases=[ast.Name(id="_LifecycleBase", ctx=ast.Load())],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    getter = next(
        copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "get_private_companion_api"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            getter,
            lifecycle_class,
        ],
        type_ignores=[],
    )
    namespace: dict[str, Any] = {
        "_private_companion_plugin": None,
        "_private_companion_runtime": types.SimpleNamespace(
            lock=threading.RLock(),
            active_plugin=None,
        ),
        "_LifecycleBase": _LifecycleBase,
        "PrivateCompanionExtensionAPI": _ExtensionAPI,
        "Req041Observability": object,
        "initialize_plugin_entrypoint_state": _initialize_entrypoint,
        "initialize_plugin_config": _initialize_config,
        "initialize_plugin_runtime": _initialize_runtime,
        "initialize_plugin_post_runtime_state": _initialize_post_runtime,
        "resume_story_handoff": _resume_story_handoff,
        "StoryAuthorityError": _StoryAuthorityError,
        "activate_persistence_owner": activate_persistence_owner,
        "asyncio": asyncio,
        "logger": types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        "_single_line": lambda value, limit=240: " ".join(
            str(value or "").split()
        )[:limit],
    }
    ast.fix_missing_locations(module)
    exec(compile(module, str(MAIN_PATH), "exec"), namespace)
    return namespace, owner


LIFECYCLE, SOURCE_CLASS = _load_lifecycle_source()
Plugin = LIFECYCLE["PrivateCompanionPlugin"]
get_private_companion_api = LIFECYCLE["get_private_companion_api"]


async def _publish_after_success(plugin: Any) -> None:
    async def successful_initialization() -> None:
        return None

    plugin._initialize_before_publication = successful_initialization
    await plugin.initialize()


def _prepare_for_termination(plugin: Any) -> None:
    async def no_op() -> None:
        return None

    plugin._stop_event = asyncio.Event()
    plugin._cancel_lifecycle_background_tasks = no_op
    plugin._proactive_chat_runtime_bridge = None
    plugin._task = None
    plugin._passive_input_status_tasks = {}
    plugin._startup_maintenance_task = None
    plugin._req041_replay_task = None
    plugin._req041_scoped_sync_task = None
    plugin._startup_background_tasks = {}
    plugin._group_image_understanding_tasks = {}
    plugin._troubleshooting_proactive_wakeup_tasks = {}
    plugin._flush_scheduled_data_save = no_op
    plugin._save_data_on_terminate = no_op


class ExtensionApiLifecyclePublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        LIFECYCLE["_private_companion_plugin"] = None
        LIFECYCLE["_private_companion_runtime"].active_plugin = None

    async def test_instance_is_hidden_until_initialize_finishes(self) -> None:
        self.assertIsNone(get_private_companion_api())
        plugin = Plugin(object(), types.SimpleNamespace(fail_at=""))
        self.assertEqual(
            ["entrypoint", "config", "runtime", "post_runtime"],
            plugin.bootstrap_steps,
        )
        self.assertIsNone(get_private_companion_api())
        self.assertEqual("created", plugin.extension_api.story_state)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def pending_initialization() -> None:
            entered.set()
            await release.wait()

        plugin._initialize_before_publication = pending_initialization
        initialize_task = asyncio.create_task(plugin.initialize())
        await entered.wait()
        self.assertIsNone(get_private_companion_api())

        release.set()
        await initialize_task
        self.assertIs(plugin.extension_api, get_private_companion_api())
        self.assertEqual("ready", plugin.extension_api.story_state)

    async def test_constructor_and_initialize_failures_keep_old_ready_instance(self) -> None:
        old = Plugin(object(), types.SimpleNamespace(fail_at=""))
        await _publish_after_success(old)
        self.assertIs(old.extension_api, get_private_companion_api())

        with self.assertRaisesRegex(RuntimeError, "bootstrap failed: runtime"):
            Plugin(object(), types.SimpleNamespace(fail_at="runtime"))
        self.assertIs(old.extension_api, get_private_companion_api())

        failed = Plugin(object(), types.SimpleNamespace(fail_at=""))

        async def escaped_failure() -> None:
            raise RuntimeError("initialize escaped")

        failed._initialize_before_publication = escaped_failure
        with self.assertRaisesRegex(RuntimeError, "initialize escaped"):
            await failed.initialize()
        await asyncio.sleep(0)
        self.assertIs(old.extension_api, get_private_companion_api())
        self.assertEqual("ready", old.extension_api.story_state)
        self.assertEqual("created", failed.extension_api.story_state)

    async def test_cancelled_handoff_replay_keeps_old_ready_generation(self) -> None:
        old = Plugin(object(), types.SimpleNamespace(fail_at=""))
        await _publish_after_success(old)
        new = Plugin(object(), types.SimpleNamespace(fail_at=""))
        entered = asyncio.Event()

        async def successful_initialization() -> None:
            return None

        new._initialize_before_publication = successful_initialization

        async def blocked_replay(_plugin: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        original = LIFECYCLE["resume_story_handoff"]
        LIFECYCLE["resume_story_handoff"] = blocked_replay
        try:
            task = asyncio.create_task(new.initialize())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            LIFECYCLE["resume_story_handoff"] = original

        self.assertIs(old.extension_api, get_private_companion_api())
        self.assertEqual("ready", old.extension_api.story_state)
        self.assertEqual("created", new.extension_api.story_state)

    async def test_terminated_pending_initialize_cannot_publish_closed_api(self) -> None:
        old = Plugin(object(), types.SimpleNamespace(fail_at=""))
        await _publish_after_success(old)
        pending = Plugin(object(), types.SimpleNamespace(fail_at=""))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pending_initialization() -> None:
            entered.set()
            await release.wait()

        pending._initialize_before_publication = pending_initialization
        initialize_task = asyncio.create_task(pending.initialize())
        await entered.wait()
        _prepare_for_termination(pending)
        await pending.terminate()

        self.assertEqual("closed", pending.extension_api.story_state)
        self.assertIs(old.extension_api, get_private_companion_api())
        self.assertEqual("ready", old.extension_api.story_state)

        release.set()
        await initialize_task
        self.assertEqual("closed", pending.extension_api.story_state)
        self.assertIs(old.extension_api, get_private_companion_api())
        self.assertEqual("ready", old.extension_api.story_state)

    async def test_hot_reload_and_identity_guarded_termination(self) -> None:
        old = Plugin(object(), types.SimpleNamespace(fail_at=""))
        await _publish_after_success(old)

        new = Plugin(object(), types.SimpleNamespace(fail_at=""))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pending_initialization() -> None:
            entered.set()
            await release.wait()

        new._initialize_before_publication = pending_initialization
        initialize_task = asyncio.create_task(new.initialize())
        await entered.wait()
        self.assertIs(old.extension_api, get_private_companion_api())

        release.set()
        await initialize_task
        self.assertIs(new.extension_api, get_private_companion_api())
        self.assertEqual("superseded", old.extension_api.story_state)
        self.assertEqual("ready", new.extension_api.story_state)

        _prepare_for_termination(old)
        await old.terminate()
        self.assertIs(new.extension_api, get_private_companion_api())
        self.assertEqual("superseded", old.extension_api.story_state)

        _prepare_for_termination(new)
        await new.terminate()
        self.assertIsNone(get_private_companion_api())
        self.assertEqual("closed", new.extension_api.story_state)

    async def test_publication_without_manager_claims_direct_json_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "companions.json"
            old = Plugin(object(), types.SimpleNamespace(fail_at=""))
            old._persistence_owner_token = "direct-old"
            old.data_file = target
            self.assertFalse(hasattr(old, "store_manager"))
            await _publish_after_success(old)

            old_backend = JsonStoreBackend(
                target,
                lambda value: value,
                dict,
                persistence_owner_token="direct-old",
            )
            old_ticket = old_backend.capture_write_ticket()

            new = Plugin(object(), types.SimpleNamespace(fail_at=""))
            new._persistence_owner_token = "direct-new"
            new.data_file = target
            await _publish_after_success(new)

            new_backend = JsonStoreBackend(
                target,
                lambda value: value,
                dict,
                persistence_owner_token="direct-new",
            )
            new_backend.save_store({"writer": "new"})
            old_backend.save_store({"writer": "old"}, write_ticket=old_ticket)

            self.assertEqual(
                {"writer": "new"},
                json.loads(target.read_text(encoding="utf-8")),
            )
            self.assertEqual("saved", new_backend.last_write_status["state"])
            self.assertEqual("superseded", old_backend.last_write_status["state"])

    def test_publication_is_the_initialize_tail_and_not_constructor_work(self) -> None:
        methods = {
            node.name: node
            for node in SOURCE_CLASS.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for method_name in ("__init__", "_initialize_before_publication"):
            premature_writes = [
                node
                for node in ast.walk(methods[method_name])
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and any(
                    isinstance(name, ast.Name)
                    and name.id == "_private_companion_plugin"
                    for name in ast.walk(node)
                )
            ]
            self.assertEqual([], premature_writes, method_name)

        initialize = methods["initialize"]
        self.assertIsInstance(initialize.body[-1], ast.Assign)
        publication = initialize.body[-1]
        self.assertEqual(
            ["_private_companion_plugin"],
            [target.id for target in publication.targets if isinstance(target, ast.Name)],
        )
        self.assertIsInstance(publication.value, ast.Name)
        self.assertEqual("self", publication.value.id)


if __name__ == "__main__":
    unittest.main()
