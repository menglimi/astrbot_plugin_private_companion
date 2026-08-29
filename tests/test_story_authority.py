from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import importlib
from pathlib import Path
import re
import stat
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

from story_migration_contract import (
    build_story_migration_snapshot,
    canonical_story_snapshot_payload,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = f"_companion_story_authority_tests_{uuid.uuid4().hex}"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
authority = importlib.import_module(f"{PACKAGE}.story_authority")


def _snapshot(title: str = "雨声") -> dict:
    return build_story_migration_snapshot(
        [
            {
                "id": "work-1",
                "title": title,
                "draft_chunks": [{"text": "雨停了。"}],
            }
        ]
    )


async def _prepare(
    controller,
    generation: str,
    snapshot_factory=None,
) -> dict:
    if snapshot_factory is None:
        async def snapshot_factory() -> dict:
            return _snapshot()
    return await controller.prepare(
        generation=generation,
        target_plugin_id=authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        owner_id=authority.STORY_MIGRATION_OWNER_ID,
        snapshot_factory=snapshot_factory,
    )


def _controller(*, drain: float = 0.2, ttl: float = 1.0):
    controller = authority._StoryAuthorityController(
        drain_timeout_seconds=drain,
        lease_ttl_seconds=ttl,
    )
    controller.stage_generation("generation-1")
    controller.activate_generation("generation-1")
    return controller


def _persona_id_harness_type() -> type:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "PrivateCompanionPlugin"
    )
    methods = [
        copy.deepcopy(node)
        for node in owner.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {
            "_configured_multi_persona_ids",
            "_persona_profile_ids",
        }
    ]
    harness = ast.ClassDef(
        name="PersonaIdHarness",
        bases=[],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Path": Path,
        "PersonaConfigError": ValueError,
        "re": re,
        "stat": stat,
    }
    exec(compile(module, "main.py", "exec"), namespace)
    harness_type = namespace["PersonaIdHarness"]
    harness_type._cfg_raw = lambda self, _config, _key, _default: self.raw_ids
    harness_type._sanitize_persona_id = lambda self, value: "".join(
        character
        for character in str(value or "")
        if ord(character) >= 32
    ).strip()
    harness_type._primary_persona_id = lambda self: "primary"
    harness_type._persona_id_from_profile_path = lambda self, path: (
        self._sanitize_persona_id(path.name[: -len(path.suffix)])
        if path.suffix.lower() in {".db", ".json"}
        else ""
    )
    harness_type._persona_profile_filename = lambda self, pid: f"{pid}.json"
    harness_type._persona_profile_db_filename = lambda self, pid: f"{pid}.db"
    return harness_type


def test_exact_task_reentrancy_does_not_flow_to_child_task() -> None:
    async def scenario() -> None:
        controller = _controller()
        root = controller.enter_legacy_operation("root")
        nested = controller.enter_legacy_operation("nested")
        assert controller.debug_state()["active_roots"] == 1

        async def child() -> None:
            child_root = controller.enter_legacy_operation("child")
            try:
                assert controller.debug_state()["active_roots"] == 2
            finally:
                controller.exit_legacy_operation(child_root)

        await asyncio.create_task(child())
        assert controller.debug_state()["active_roots"] == 1
        controller.exit_legacy_operation(nested)
        controller.exit_legacy_operation(root)
        assert controller.debug_state()["active_roots"] == 0

    asyncio.run(scenario())


def test_prepare_drains_existing_root_rejects_new_root_and_pins_snapshot() -> None:
    async def scenario() -> None:
        controller = _controller()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def existing_root() -> None:
            identity = controller.enter_legacy_operation("existing")
            try:
                entered.set()
                await release.wait()
            finally:
                controller.exit_legacy_operation(identity)

        root_task = asyncio.create_task(existing_root())
        await entered.wait()
        lease_task = asyncio.create_task(_prepare(controller, "generation-1"))
        while controller.debug_state()["state"] != "draining":
            await asyncio.sleep(0)
        with pytest.raises(authority.StoryAuthorityError) as rejected:
            controller.enter_legacy_operation("new")
        assert rejected.value.code == "story_legacy_write_draining"

        release.set()
        lease = await lease_task
        await root_task
        assert controller.debug_state() == {
            "state": "leased",
            "active_generation": "generation-1",
            "active_roots": 0,
            "waiters": 0,
            "preparers": 0,
            "inspectors": 0,
            "has_lease": True,
        }
        assert len(lease["lease_token"]) >= 43

        first = controller.export_lease(
            generation="generation-1",
            lease_token=lease["lease_token"],
        )
        first["projects"][0]["title"] = "caller mutation"
        second = controller.export_lease(
            generation="generation-1",
            lease_token=lease["lease_token"],
        )
        assert second["projects"][0]["title"] == "雨声"
        with pytest.raises(authority.StoryAuthorityError) as leased:
            controller.enter_legacy_operation("new")
        assert leased.value.code == "story_legacy_write_leased"

        assert controller.abort(
            generation="generation-1",
            lease_token=lease["lease_token"],
        ) == {"aborted": True, "already_released": False}
        assert controller.abort(
            generation="generation-1",
            lease_token=lease["lease_token"],
        ) == {"aborted": False, "already_released": True}
        reopened = controller.enter_legacy_operation("reopened")
        controller.exit_legacy_operation(reopened)

    asyncio.run(scenario())


def test_timeout_cancellation_expiry_and_invalid_snapshot_all_reopen_without_residue() -> None:
    async def scenario() -> None:
        timeout_controller = _controller(drain=0.01)
        root = timeout_controller.enter_legacy_operation("held")
        with pytest.raises(authority.StoryAuthorityError) as timed_out:
            await _prepare(timeout_controller, "generation-1")
        assert timed_out.value.code == "story_handoff_drain_timeout"
        assert timeout_controller.debug_state()["state"] == "open"
        assert timeout_controller.debug_state()["waiters"] == 0
        timeout_controller.exit_legacy_operation(root)

        cancel_controller = _controller()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_factory() -> dict:
            entered.set()
            await release.wait()
            return _snapshot()

        preparing = asyncio.create_task(
            _prepare(cancel_controller, "generation-1", blocked_factory)
        )
        await entered.wait()
        preparing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await preparing
        state = cancel_controller.debug_state()
        assert state["state"] == "open"
        assert state["waiters"] == state["preparers"] == state["inspectors"] == 0

        invalid_controller = _controller()

        async def invalid_factory() -> dict:
            return {
                "version": authority.STORY_MIGRATION_SNAPSHOT_VERSION,
                "owner_id": authority.STORY_MIGRATION_OWNER_ID,
                "snapshot_id": "storysnap_not-a-hash",
                "snapshot_sha256": "",
                "projects": [],
            }

        with pytest.raises(authority.StoryAuthorityError) as invalid:
            await _prepare(invalid_controller, "generation-1", invalid_factory)
        assert invalid.value.code == "story_handoff_snapshot_identity_invalid"
        assert invalid_controller.debug_state()["state"] == "open"

        mismatched_controller = _controller()

        async def mismatched_factory() -> dict:
            snapshot = _snapshot()
            snapshot["projects"][0]["title"] = "changed after hashing"
            return snapshot

        with pytest.raises(authority.StoryAuthorityError) as mismatched:
            await _prepare(
                mismatched_controller,
                "generation-1",
                mismatched_factory,
            )
        assert mismatched.value.code == "story_handoff_snapshot_identity_invalid"

        extra_key_controller = _controller()

        async def extra_key_factory() -> dict:
            snapshot = _snapshot()
            snapshot["unexpected"] = True
            return snapshot

        with pytest.raises(authority.StoryAuthorityError) as extra_key:
            await _prepare(
                extra_key_controller,
                "generation-1",
                extra_key_factory,
            )
        assert extra_key.value.code == "story_handoff_snapshot_identity_invalid"

        noncanonical_controller = _controller()

        async def noncanonical_factory() -> dict:
            snapshot = _snapshot()
            snapshot["projects"][0]["unexpected"] = True
            digest = hashlib.sha256(
                canonical_story_snapshot_payload(snapshot)
            ).hexdigest()
            snapshot["snapshot_sha256"] = digest
            snapshot["snapshot_id"] = f"storysnap_{digest}"
            return snapshot

        with pytest.raises(authority.StoryAuthorityError) as noncanonical:
            await _prepare(
                noncanonical_controller,
                "generation-1",
                noncanonical_factory,
            )
        assert noncanonical.value.code == "story_handoff_snapshot_identity_invalid"

        expiry_controller = _controller(ttl=0.001)
        lease = await _prepare(expiry_controller, "generation-1")
        await asyncio.sleep(0.01)
        with pytest.raises(authority.StoryAuthorityError) as expired:
            expiry_controller.export_lease(
                generation="generation-1",
                lease_token=lease["lease_token"],
            )
        assert expired.value.code == "story_handoff_lease_expired"
        assert expiry_controller.debug_state()["state"] == "open"

    asyncio.run(scenario())


def test_hot_generation_invalidates_old_lease_and_old_close_cannot_close_new() -> None:
    async def scenario() -> None:
        controller = _controller()
        old = await _prepare(controller, "generation-1")
        controller.activate_generation("generation-2")
        with pytest.raises(authority.StoryAuthorityError) as stale:
            controller.export_lease(
                generation="generation-1",
                lease_token=old["lease_token"],
            )
        assert stale.value.code == "story_handoff_generation_stale"
        controller.supersede_generation("generation-1")
        assert controller.debug_state()["state"] == "open"
        new = await _prepare(controller, "generation-2")
        assert controller.export_lease(
            generation="generation-2",
            lease_token=new["lease_token"],
        )["snapshot_id"] == new["snapshot_id"]

    asyncio.run(scenario())


def test_staged_reload_does_not_inherit_or_revoke_an_old_lease() -> None:
    async def scenario() -> None:
        controller = _controller()
        old = await _prepare(controller, "generation-1")

        controller.stage_generation("generation-2")
        assert controller.debug_state()["active_generation"] == "generation-1"
        assert controller.debug_state()["state"] == "leased"
        with pytest.raises(authority.StoryAuthorityError) as bootstrap:
            controller.enter_legacy_operation("reload.bootstrap")
        assert bootstrap.value.code == "story_legacy_write_leased"

        controller.close_generation("generation-2")
        assert controller.debug_state()["state"] == "leased"
        controller.abort(
            generation="generation-1",
            lease_token=old["lease_token"],
        )
        bootstrap_identity = controller.enter_legacy_operation("reload.bootstrap")
        controller.exit_legacy_operation(bootstrap_identity)
        controller.activate_generation("generation-2")
        assert controller.debug_state()["active_generation"] == "generation-2"
        assert controller.debug_state()["state"] == "open"

    asyncio.run(scenario())


def test_profile_inspection_bypass_is_exact_task_and_exact_scope_only() -> None:
    async def scenario() -> None:
        controller = _controller()

        async def snapshot_factory() -> dict:
            with controller.strict_profile_inspection():
                bypass = controller.enter_legacy_operation(
                    "persona.store.strict-read"
                )
                controller.exit_legacy_operation(bypass)
                assert controller.debug_state()["active_roots"] == 0

                async def child_attempt() -> str:
                    try:
                        controller.enter_legacy_operation("child")
                    except authority.StoryAuthorityError as exc:
                        return exc.code
                    raise AssertionError("child Task inherited inspection bypass")

                assert await asyncio.create_task(child_attempt()) == (
                    "story_legacy_write_draining"
                )

                def thread_attempt() -> str:
                    try:
                        controller.enter_legacy_operation("thread")
                    except authority.StoryAuthorityError as exc:
                        return exc.code
                    raise AssertionError("worker thread inherited inspection bypass")

                assert await asyncio.to_thread(thread_attempt) == (
                    "story_legacy_write_draining"
                )
            with pytest.raises(authority.StoryAuthorityError) as outside:
                controller.enter_legacy_operation("outside-profile-read")
            assert outside.value.code == "story_legacy_write_draining"
            return _snapshot()

        lease = await _prepare(
            controller,
            "generation-1",
            snapshot_factory,
        )
        assert controller.debug_state()["preparers"] == 0
        assert controller.debug_state()["inspectors"] == 0
        controller.abort(
            generation="generation-1",
            lease_token=lease["lease_token"],
        )

    asyncio.run(scenario())


def test_module_aliases_share_exact_process_controller_and_error_type() -> None:
    alias_name = f"_companion_story_authority_alias_{uuid.uuid4().hex}"
    alias = ModuleType(alias_name)
    alias.__path__ = [str(ROOT)]
    sys.modules[alias_name] = alias
    alias_authority = importlib.import_module(f"{alias_name}.story_authority")

    assert alias_authority.story_authority_controller() is (
        authority.story_authority_controller()
    )
    assert alias_authority.StoryAuthorityError is authority.StoryAuthorityError


def test_multi_persona_check_enumerates_persisted_profiles_and_fails_closed() -> None:
    empty = {"creative_projects": []}
    nonempty = {"creative_projects": [{"id": "secondary-work"}]}

    def plugin(snapshot, *, unreadable: bool = False, cached=None):
        read_calls: list[str] = []

        def read_only_snapshot(pid):
            read_calls.append(pid)
            if unreadable:
                raise OSError("unreadable profile")
            return snapshot if pid == "secondary" else None

        return SimpleNamespace(
            enable_multi_persona_mode=True,
            _data_default=empty,
            _persona_data_profiles={} if cached is None else {"secondary": cached},
            _read_only_calls=read_calls,
            _primary_persona_id=lambda: "primary",
            _persona_profile_ids=lambda *, strict=False: [
                "primary",
                "secondary",
            ],
            _persona_profile_snapshot_read_only=read_only_snapshot,
        )

    disk_shadow = plugin(nonempty, cached=empty)
    with pytest.raises(authority.StoryAuthorityError) as unsupported:
        authority.assert_single_persona_story_shelf(disk_shadow)
    assert unsupported.value.code == "story_handoff_multi_persona_unsupported"
    assert disk_shadow._read_only_calls == ["primary", "secondary"]

    with pytest.raises(authority.StoryAuthorityError) as unverifiable:
        authority.assert_single_persona_story_shelf(plugin(None, unreadable=True))
    assert unverifiable.value.code == "story_handoff_multi_persona_unverifiable"

    with pytest.raises(authority.StoryAuthorityError) as malformed_cache:
        authority.assert_single_persona_story_shelf(plugin(None, cached=[]))
    assert malformed_cache.value.code == "story_handoff_multi_persona_unverifiable"

    authority.assert_single_persona_story_shelf(plugin(None))


def test_strict_persona_id_enumeration_rejects_ambiguous_config_and_symlinks(
    tmp_path: Path,
) -> None:
    harness_type = _persona_id_harness_type()
    host = harness_type()
    host.config = {}
    host._persona_data_profiles = {}
    host._persona_profiles_dir = str(tmp_path / "missing")

    for raw_ids in (
        {},
        ("secondary",),
        [123],
        ["\x00"],
        ["secondary\x01"],
    ):
        host.raw_ids = raw_ids
        with pytest.raises(ValueError):
            host._persona_profile_ids(strict=True)

    host.raw_ids = ("secondary",)
    assert host._configured_multi_persona_ids() == [
        "primary",
        "secondary",
    ]

    host.raw_ids = []
    host._persona_data_profiles = {"secondary\x01": {}}
    with pytest.raises(ValueError):
        host._persona_profile_ids(strict=True)
    host._persona_data_profiles = {}

    regular_root = tmp_path / "regular"
    regular_root.mkdir()
    (regular_root / "secondary.db").write_bytes(b"sqlite")
    host.raw_ids = []
    host._persona_profiles_dir = str(regular_root)
    assert host._persona_profile_ids(strict=True) == [
        "primary",
        "secondary",
    ]

    symlink_root = tmp_path / "symlink"
    symlink_root.mkdir()
    target = tmp_path / "target.db"
    target.write_bytes(b"sqlite")
    (symlink_root / "secondary.db").symlink_to(target)
    host._persona_profiles_dir = str(symlink_root)
    with pytest.raises(ValueError):
        host._persona_profile_ids(strict=True)

    non_file_root = tmp_path / "non-file"
    non_file_root.mkdir()
    (non_file_root / "secondary.json").mkdir()
    host._persona_profiles_dir = str(non_file_root)
    with pytest.raises(ValueError):
        host._persona_profile_ids(strict=True)

    noncanonical_root = tmp_path / "noncanonical"
    noncanonical_root.mkdir()
    (noncanonical_root / "secondary.DB").write_bytes(b"sqlite")
    host._persona_profiles_dir = str(noncanonical_root)
    with pytest.raises(ValueError):
        host._persona_profile_ids(strict=True)


def _decorators(filename: str, class_name: str) -> dict[str, set[str]]:
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    result: dict[str, set[str]] = {}
    for method in owner.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        result[method.name] = {
            decorator.func.id
            for decorator in method.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
        }
    return result


def test_story_mutation_root_allowlist_is_gated_and_read_tool_stays_read_only() -> None:
    creative = _decorators("creative.py", "CreativeMixin")
    for name in {
        "_cleanup_legacy_creative_fallback_chunks",
        "_get_or_create_story_bible",
        "_get_or_create_memory_pool",
        "_add_memory_entry",
        "_generate_outline_for_chunk",
        "_post_generation_extract",
        "_apply_creative_manual_edit",
        "_rebuild_creative_memory_from_project",
        "_generate_creative_chunk",
        "_maybe_start_creative_project",
        "_defer_creative_project_advance",
        "_store_creative_cover_image",
        "_maybe_generate_creative_cover",
        "_maybe_advance_creative_projects",
        "_latest_creative_share_candidate",
        "_mark_creative_milestone_disclosed",
        "_maybe_schedule_creative_share",
    }:
        assert creative[name] & {
            "story_legacy_operation",
            "story_legacy_sync_operation",
        }

    page = _decorators("page_api.py", "PrivateCompanionPageApi")
    for name in {
        "update_creative_project",
        "update_creative_chunk",
        "update_creative_outline",
        "update_creative_characters",
        "reanalyze_creative_project",
        "rebuild_creative_memory",
        "delete_creative_project",
    }:
        assert page[name] == {"story_legacy_operation"}
    assert page["_apply_migration_normalized"] == {"story_legacy_operation_if"}

    bridge = _decorators(
        "content_companion_bridge.py",
        "ContentCompanionBridgeMixin",
    )
    bridge_gates = {
        "_maybe_advance_creative_projects": "story_authority_controller",
        "_maybe_start_creative_project": "_content_story_maybe_start_current",
        "_generate_creative_project": "_content_story_execute",
        "_generate_creative_chunk": "_content_story_execute",
        "_review_creative_chunk": "_content_story_execute",
        "_apply_creative_manual_edit": "_content_story_execute",
        "_rebuild_creative_memory_from_project": "_content_story_execute",
        "_maybe_generate_creative_cover": "story_authority_controller",
    }
    bridge_tree = ast.parse(
        (ROOT / "content_companion_bridge.py").read_text(encoding="utf-8")
    )
    bridge_owner = next(
        node
        for node in bridge_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ContentCompanionBridgeMixin"
    )
    bridge_methods = {
        node.name: node
        for node in bridge_owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, gate in bridge_gates.items():
        # Routing methods remain callable after handoff and gate only their
        # legacy fallback, while the current Content generation stays usable.
        assert bridge[name] == set()
        assert gate in ast.unparse(bridge_methods[name])

    tool_tree = ast.parse((ROOT / "llm_tool_actions.py").read_text(encoding="utf-8"))
    tool_owner = next(
        node
        for node in tool_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LlmToolActionsMixin"
    )
    view = next(
        node
        for node in tool_owner.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_pc_view_creative_work_impl"
    )
    assert not view.decorator_list
    state_write_targets: list[ast.AST] = []
    for node in ast.walk(view):
        if isinstance(node, ast.Assign):
            state_write_targets.extend(
                target
                for target in node.targets
                if isinstance(target, (ast.Attribute, ast.Subscript))
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target,
            (ast.Attribute, ast.Subscript),
        ):
            state_write_targets.append(node.target)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, (ast.Attribute, ast.Subscript)):
                state_write_targets.append(node.target)
        elif isinstance(node, ast.Delete):
            state_write_targets.extend(node.targets)
    assert not state_write_targets
    view_source = ast.unparse(view)
    for forbidden_write in (
        "_save_data_sync",
        "_schedule_data_save",
        "_apply_creative_manual_edit",
        "_mark_creative_milestone_disclosed",
    ):
        assert forbidden_write not in view_source


def test_s4_surface_enforces_unique_content_routing_after_commit() -> None:
    authority_source = (ROOT / "story_authority.py").read_text(encoding="utf-8")
    content_source = (ROOT / "extension_api_content.py").read_text(encoding="utf-8")
    handoff_source = (ROOT / "story_handoff.py").read_text(encoding="utf-8")
    bridge_source = (ROOT / "content_companion_bridge.py").read_text(
        encoding="utf-8"
    )
    descriptor_method = next(
        node
        for node in ast.walk(ast.parse(content_source))
        if isinstance(node, ast.FunctionDef)
        and node.name == "story_migration_capabilities"
    )
    descriptor_text = ast.unparse(descriptor_method)
    assert "ids_getter(strict=True)" in authority_source
    assert "story.handoff.prepare" in descriptor_text
    assert "story.handoff.export-lease" in descriptor_text
    assert "story.handoff.abort" in descriptor_text
    assert "story.handoff.commit" in descriptor_text
    assert "companion.story-migration-commit.v1" in handoff_source
    assert '"story.handoff.enforced"' in handoff_source
    assert "resolve_enforced_story_target" in bridge_source
    assert "call_enforced_story_target" in bridge_source
    for forbidden in (
        "switch_story_owner",
        "_content_companion_call",
    ):
        assert forbidden not in authority_source
        assert forbidden not in descriptor_text
        assert forbidden not in handoff_source
