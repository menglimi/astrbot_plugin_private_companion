from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import ModuleType
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = f"_companion_story_handoff_tests_{uuid.uuid4().hex}"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
authority = importlib.import_module(f"{PACKAGE}.story_authority")
handoff = importlib.import_module(f"{PACKAGE}.story_handoff")
core_store = importlib.import_module(f"{PACKAGE}.core_store")
contract = importlib.import_module(f"{PACKAGE}.story_migration_contract")


GENERATION = "1" * 32
NEXT_GENERATION = "2" * 32
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _snapshot(title: str = "雨声") -> dict:
    return contract.build_story_migration_snapshot(
        [{"id": "work-1", "title": title, "draft_chunks": []}]
    )


def _marker(snapshot: dict, *, committed_at: float = 1.0) -> dict:
    return {
        "version": handoff.STORY_MIGRATION_COMMIT_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "target_plugin_id": authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        "owner_id": contract.STORY_MIGRATION_OWNER_ID,
        "committed_at": committed_at,
    }


def _backup() -> dict:
    return {"sha256": EMPTY_SHA256, "size": 0, "existed": False}


def _absent_status() -> dict:
    return {
        "version": "content.story-migration-ledger.v1",
        "status": "absent",
        "target_plugin_id": authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        "owner_id": contract.STORY_MIGRATION_OWNER_ID,
    }


def _prepared_status(snapshot: dict, *, generation: str = GENERATION) -> dict:
    return {
        "version": "content.story-migration-ledger.v1",
        "status": "prepared",
        "source_plugin_id": contract.STORY_MIGRATION_OWNER_ID,
        "source_instance_generation": generation,
        "target_plugin_id": authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        "owner_id": contract.STORY_MIGRATION_OWNER_ID,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "prepared_at": 1.0,
        "baseline_sha256": "0" * 64,
        "backup": _backup(),
    }


def _committed_status(marker: dict, *, generation: str = GENERATION) -> dict:
    return {
        "version": "content.story-migration-ledger.v1",
        "status": "committed",
        "source_plugin_id": contract.STORY_MIGRATION_OWNER_ID,
        "source_instance_generation": generation,
        "marker": copy.deepcopy(marker),
        "backup": _backup(),
    }


def _aborted_status(snapshot: dict, *, generation: str = GENERATION) -> dict:
    return {
        "version": "content.story-migration-ledger.v1",
        "status": "aborted",
        "source_plugin_id": contract.STORY_MIGRATION_OWNER_ID,
        "source_instance_generation": generation,
        "target_plugin_id": authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        "owner_id": contract.STORY_MIGRATION_OWNER_ID,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "aborted_at": 2.0,
        "backup": _backup(),
    }


class _TargetApi:
    def __init__(
        self,
        ledger: dict,
        order: list[str],
        *,
        generation: str = "a" * 32,
    ) -> None:
        self.ledger = ledger
        self.order = order
        self.generation = generation
        self.plugin = None
        self.swap_after_prepare = None
        self.swap_after_commit = None
        self.prepare_started = asyncio.Event()
        self.prepare_release: asyncio.Event | None = None
        self.commit_started = asyncio.Event()
        self.commit_release: asyncio.Event | None = None
        self.prepare_fail = False
        self.commit_fail = False
        self.prepare_calls = 0
        self.commit_calls = 0
        self.abort_calls = 0
        self.descriptor_override: dict | None = None

    def capabilities(self) -> dict:
        descriptor = {
            "plugin_id": authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
            "instance_generation": self.generation,
            "api_family": "content.story",
            "api_version": "content.story-api.v1",
            "supported_task_versions": [
                "content.story-task.v1",
                "content.story-task.v2",
            ],
            "capabilities": [
                "story.build-task",
                "story.migration.abort",
                "story.migration.commit",
                "story.migration.prepare",
                "story.migration.status",
            ],
            "lifecycle_state": "ready",
            "degraded_reasons": [],
        }
        if self.descriptor_override is not None:
            descriptor = copy.deepcopy(self.descriptor_override)
        return descriptor

    def story_migration_status(self) -> dict:
        self.order.append("target-status")
        return copy.deepcopy(self.ledger.get("status", _absent_status()))

    async def prepare_story_migration(
        self,
        snapshot: dict,
        *,
        source_plugin_id: str,
        source_instance_generation: str,
    ) -> dict:
        self.prepare_calls += 1
        self.order.append("target-prepare")
        self.prepare_started.set()
        if self.prepare_release is not None:
            await self.prepare_release.wait()
        if self.prepare_fail:
            raise RuntimeError("target prepare crash body must not escape")
        assert source_plugin_id == contract.STORY_MIGRATION_OWNER_ID
        self.ledger["status"] = _prepared_status(
            snapshot,
            generation=source_instance_generation,
        )
        if self.swap_after_prepare is not None:
            self.plugin.api = self.swap_after_prepare
        return self.story_migration_status()

    async def commit_story_migration(self, marker: dict) -> dict:
        self.commit_calls += 1
        self.order.append("target-commit")
        self.commit_started.set()
        if self.commit_release is not None:
            await self.commit_release.wait()
        if self.commit_fail:
            raise RuntimeError("target commit crash body must not escape")
        self.ledger["status"] = _committed_status(marker)
        if self.swap_after_commit is not None:
            self.plugin.api = self.swap_after_commit
        return self.story_migration_status()

    async def abort_story_migration(
        self,
        *,
        snapshot_id: str,
        snapshot_sha256: str,
    ) -> dict:
        self.abort_calls += 1
        self.order.append("target-abort")
        status = self.ledger.get("status", _absent_status())
        assert status.get("snapshot_id") == snapshot_id
        assert status.get("snapshot_sha256") == snapshot_sha256
        snapshot = {
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_sha256,
        }
        self.ledger["status"] = _aborted_status(snapshot)
        return self.story_migration_status()


class _Plugin:
    def __init__(self, api: _TargetApi | None) -> None:
        self.api = api
        if api is not None:
            api.plugin = self
        self._data_lock = asyncio.Lock()
        self.data = {
            "creative_projects": [{"id": "work-1", "title": "雨声"}],
            "unrelated": {"nested": [1, 2, 3]},
        }
        self._data_default = self.data
        self.persisted: dict = {}
        self.save_mode = "ok"
        self.fresh_calls = 0
        self.order: list[str] = api.order if api is not None else []

    def _content_companion_api_fresh(self):
        self.fresh_calls += 1
        if self.api is not None:
            self.api.plugin = self
        return self.api

    def _save_story_migration_commit_confirmed_sync(self, marker: dict) -> None:
        self.order.append("source-save")
        if self.save_mode == "fail":
            raise RuntimeError("save failed")
        if self.save_mode == "mismatch":
            self.persisted = {"unexpected": True}
            raise RuntimeError("readback mismatch")
        self.persisted = copy.deepcopy(marker)

    def _read_story_migration_commit_persisted_sync(self):
        if not self.persisted:
            return False, None
        return True, copy.deepcopy(self.persisted)


def _controller(*, ttl: float = 60.0):
    controller = authority._StoryAuthorityController(
        drain_timeout_seconds=0.2,
        lease_ttl_seconds=ttl,
    )
    controller.stage_generation(GENERATION)
    controller.activate_generation(GENERATION)
    return controller


async def _lease(controller, snapshot: dict) -> dict:
    async def snapshot_factory() -> dict:
        return copy.deepcopy(snapshot)

    return await controller.prepare(
        generation=GENERATION,
        target_plugin_id=authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
        owner_id=contract.STORY_MIGRATION_OWNER_ID,
        snapshot_factory=snapshot_factory,
    )


@pytest.fixture
def local_controller(monkeypatch):
    controller = _controller()
    monkeypatch.setattr(handoff, "story_authority_controller", lambda: controller)
    monkeypatch.setattr(core_store, "story_authority_controller", lambda: controller)
    return controller


def test_controller_committing_does_not_expire_and_committed_hot_reload_never_reopens() -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        controller = _controller(ttl=0.001)
        receipt = await _lease(controller, snapshot)
        controller.begin_commit(
            generation=GENERATION,
            lease_token=receipt["lease_token"],
            snapshot_id=snapshot["snapshot_id"],
            snapshot_sha256=snapshot["snapshot_sha256"],
            target_plugin_id=authority.STORY_HANDOFF_TARGET_PLUGIN_ID,
            owner_id=contract.STORY_MIGRATION_OWNER_ID,
        )
        await asyncio.sleep(0.01)
        assert controller.authority_state() == "committing"
        with pytest.raises(authority.StoryAuthorityError) as writing:
            controller.enter_legacy_operation("late-writer")
        assert writing.value.code == "story_legacy_write_committing"

        marker = _marker(snapshot)
        controller.finish_commit(
            generation=GENERATION,
            lease_token=receipt["lease_token"],
            marker=marker,
        )
        assert controller.committed_marker_source_verified(marker) is True
        controller.stage_generation(NEXT_GENERATION)
        assert controller.debug_state()["active_generation"] == GENERATION
        controller.activate_generation(NEXT_GENERATION)
        controller.close_generation(GENERATION)
        assert controller.authority_state() == "committed"
        assert controller.debug_state()["active_generation"] == NEXT_GENERATION
        controller.close_generation(NEXT_GENERATION)
        assert controller.authority_state() == "committed"

    asyncio.run(scenario())


def test_s2_controller_is_upgraded_in_place_with_active_state_preserved() -> None:
    class LegacyController:
        pass

    old = LegacyController()
    old._lock = threading.RLock()
    old._state = "leased"
    old._active_generation = GENERATION
    old._depths = {"root": 2}
    old._prepare_bindings = {"prepare": (GENERATION, "drain")}
    old._inspection_bindings = {}
    old._waiters = {"drain": (None, object())}
    old._drain_id = "drain"
    lease = {"generation": GENERATION, "token": "secret", "snapshot": _snapshot()}
    old._lease = lease
    old._last_token_digest = ""
    old._last_token_generation = ""
    old._last_token_reason = ""
    old._drain_timeout_seconds = 5.0
    old._lease_ttl_seconds = 60.0
    runtime = ModuleType("legacy-story-authority")
    runtime.controller = old

    upgraded = authority._upgrade_story_authority_runtime(runtime)

    assert upgraded.controller is old
    assert old.__class__ is authority._StoryAuthorityController
    assert old._state == "leased"
    assert old._depths == {"root": 2}
    assert old._waiters["drain"][1] is not None
    assert old._lease is lease
    assert old._commit_marker is None
    assert old._commit_marker_source_verified is False


def test_source_verification_proof_is_exact_and_monotonic() -> None:
    controller = _controller()
    marker = _marker(_snapshot())
    controller.recover_committed_marker(marker)
    assert controller.committed_marker_source_verified(marker) is False
    controller.recover_committed_marker(marker, source_verified=True)
    assert controller.committed_marker_source_verified(marker) is True
    controller.recover_committed_marker(marker)
    assert controller.committed_marker_source_verified(marker) is True


def test_startup_root_never_grants_nested_story_write_after_marker_recovery() -> None:
    controller = _controller()
    startup = controller.enter_startup_operation("startup")
    try:
        controller.recover_committed_marker(_marker(_snapshot()))
        with pytest.raises(authority.StoryAuthorityError) as writer:
            controller.enter_legacy_operation("startup.story-writer")
        assert writer.value.code == "story_legacy_write_committed"
        assert controller.debug_state()["active_roots"] == 1
    finally:
        controller.exit_startup_operation(startup)
    assert controller.debug_state()["active_roots"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda marker: marker.pop("owner_id"),
        lambda marker: marker.__setitem__("future", True),
        lambda marker: marker.__setitem__("version", "companion.story-migration-commit.v2"),
        lambda marker: marker.__setitem__("snapshot_sha256", "A" * 64),
        lambda marker: marker.__setitem__("committed_at", True),
    ],
)
def test_marker_is_exact_and_future_or_partial_values_block(local_controller, mutation) -> None:
    marker = _marker(_snapshot())
    mutation(marker)
    with pytest.raises(authority.StoryAuthorityError):
        handoff.preflight_story_handoff_sections(
            {handoff.STORY_MIGRATION_COMMIT_KEY: marker}
        )
    assert local_controller.authority_state() == "blocked"


def test_valid_marker_recovery_survives_missing_content_without_reopening(local_controller) -> None:
    async def scenario() -> None:
        marker = _marker(_snapshot())
        handoff.preflight_story_handoff_sections(
            {handoff.STORY_MIGRATION_COMMIT_KEY: marker}
        )
        plugin = _Plugin(None)
        with pytest.raises(authority.StoryAuthorityError) as unavailable:
            await handoff.resume_story_handoff(plugin)
        assert unavailable.value.code == "story_handoff_target_unavailable"
        assert local_controller.authority_state() == "committed"
        with pytest.raises(authority.StoryAuthorityError) as writer:
            local_controller.enter_legacy_operation("legacy")
        assert writer.value.code == "story_legacy_write_committed"

    asyncio.run(scenario())


def test_missing_source_marker_cannot_be_unblocked_by_target_replay(
    local_controller,
) -> None:
    async def scenario() -> None:
        marker = _marker(_snapshot())
        local_controller.recover_committed_marker(
            marker,
            source_verified=True,
        )
        with pytest.raises(authority.StoryAuthorityError) as missing:
            local_controller.assert_marker_absent()
        assert missing.value.code == "story_handoff_marker_missing"

        target = _TargetApi({"status": _committed_status(marker)}, [])
        plugin = _Plugin(target)
        with pytest.raises(authority.StoryAuthorityError) as blocked:
            await handoff.resume_story_handoff(plugin)
        assert blocked.value.code == "story_handoff_blocked"
        assert target.order == []

        handoff.preflight_story_handoff_sections(
            {handoff.STORY_MIGRATION_COMMIT_KEY: marker}
        )
        assert local_controller.authority_state() == "committed"

    asyncio.run(scenario())


def test_memory_marker_without_exact_persisted_readback_blocks_before_replay(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        marker = _marker(snapshot)
        target = _TargetApi({"status": _prepared_status(snapshot)}, [])
        plugin = _Plugin(target)
        plugin.data[handoff.STORY_MIGRATION_COMMIT_KEY] = copy.deepcopy(marker)

        with pytest.raises(authority.StoryAuthorityError) as blocked:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
            )

        assert blocked.value.code == "story_handoff_marker_persistence_unconfirmed"
        assert local_controller.authority_state() == "blocked"
        assert target.commit_calls == target.abort_calls == 0

    asyncio.run(scenario())


def test_memory_marker_replays_only_after_exact_persisted_readback(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        marker = _marker(snapshot)
        ledger = {"status": _prepared_status(snapshot)}
        target = _TargetApi(ledger, [])
        plugin = _Plugin(target)
        plugin.data[handoff.STORY_MIGRATION_COMMIT_KEY] = copy.deepcopy(marker)
        plugin.persisted = copy.deepcopy(marker)

        result = await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
        )

        assert result["status"] == "committed"
        assert local_controller.authority_state() == "committed"
        assert target.commit_calls == 1
        assert target.abort_calls == 0

    asyncio.run(scenario())


def test_end_to_end_order_is_prepare_then_source_marker_then_target_commit(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        order: list[str] = []
        ledger: dict = {}
        target = _TargetApi(ledger, order)
        plugin = _Plugin(target)

        result = await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
            lease_token=receipt["lease_token"],
        )

        assert result["status"] == "committed"
        assert local_controller.authority_state() == "committed"
        assert set(plugin.data[handoff.STORY_MIGRATION_COMMIT_KEY]) == {
            "version",
            "snapshot_id",
            "snapshot_sha256",
            "target_plugin_id",
            "owner_id",
            "committed_at",
        }
        assert order.index("target-prepare") < order.index("source-save")
        assert order.index("source-save") < order.index("target-commit")
        assert ledger["status"]["status"] == "committed"

    asyncio.run(scenario())


def test_source_marker_uses_primary_store_not_scoped_persona_view(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        target = _TargetApi({}, [])
        plugin = _Plugin(target)
        primary = plugin._data_default
        scoped = {"creative_projects": [], "persona": "secondary"}
        plugin.data = scoped

        await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
            lease_token=receipt["lease_token"],
        )

        assert handoff.STORY_MIGRATION_COMMIT_KEY in primary
        assert handoff.STORY_MIGRATION_COMMIT_KEY not in scoped

    asyncio.run(scenario())


def test_prepared_from_prior_generation_is_reused_only_for_same_snapshot(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        order: list[str] = []
        ledger = {"status": _prepared_status(snapshot, generation="9" * 32)}
        target = _TargetApi(ledger, order)
        plugin = _Plugin(target)

        await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
            lease_token=receipt["lease_token"],
        )

        assert target.prepare_calls == 0
        assert target.commit_calls == 1
        assert ledger["status"]["status"] == "committed"

    asyncio.run(scenario())


def test_content_committed_without_source_marker_is_split_brain(local_controller) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        marker = _marker(snapshot)
        ledger = {"status": _committed_status(marker)}
        target = _TargetApi(ledger, [])
        plugin = _Plugin(target)

        with pytest.raises(authority.StoryAuthorityError) as split:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        assert split.value.code == "story_handoff_split_brain"
        assert local_controller.authority_state() == "blocked"
        assert handoff.STORY_MIGRATION_COMMIT_KEY not in plugin.data

    asyncio.run(scenario())


def test_target_swap_before_marker_aborts_exact_target_and_local_lease(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        order: list[str] = []
        old = _TargetApi(ledger, order, generation="a" * 32)
        new = _TargetApi(ledger, order, generation="b" * 32)
        plugin = _Plugin(old)
        old.swap_after_prepare = new

        with pytest.raises(authority.StoryAuthorityError) as changed:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        assert changed.value.code == "story_handoff_target_generation_changed"
        assert ledger["status"]["status"] == "aborted"
        assert new.abort_calls == 1
        assert local_controller.authority_state() == "open"
        assert handoff.STORY_MIGRATION_COMMIT_KEY not in plugin.data

    asyncio.run(scenario())


def test_failed_prepare_after_older_aborted_ledger_releases_local_lease(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        older = _snapshot("旧稿")
        ledger = {"status": _aborted_status(older, generation="9" * 32)}
        target = _TargetApi(ledger, [])
        target.prepare_fail = True
        plugin = _Plugin(target)

        with pytest.raises(authority.StoryAuthorityError) as failed:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )

        assert failed.value.code == "story_handoff_target_call_failed"
        assert ledger["status"] == _aborted_status(older, generation="9" * 32)
        assert local_controller.authority_state() == "open"

    asyncio.run(scenario())


def test_target_swap_after_marker_replays_new_current_generation_without_abort(
    local_controller,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        order: list[str] = []
        old = _TargetApi(ledger, order, generation="a" * 32)
        new = _TargetApi(ledger, order, generation="b" * 32)
        plugin = _Plugin(old)
        old.swap_after_commit = new

        result = await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
            lease_token=receipt["lease_token"],
        )

        assert result["status"] == "committed"
        assert local_controller.authority_state() == "committed"
        assert old.abort_calls == new.abort_calls == 0
        assert old.commit_calls == 1
        assert new.commit_calls == 0
        assert ledger["status"]["status"] == "committed"

    asyncio.run(scenario())


@pytest.mark.parametrize("save_mode", ["fail", "mismatch"])
def test_marker_save_or_readback_failure_restores_full_memory_and_aborts(
    local_controller,
    save_mode: str,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        target = _TargetApi(ledger, [])
        plugin = _Plugin(target)
        plugin.save_mode = save_mode
        baseline = copy.deepcopy(plugin.data)

        with pytest.raises(RuntimeError):
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        assert plugin.data == baseline
        assert ledger["status"]["status"] == "aborted"
        assert local_controller.authority_state() == "open"

    asyncio.run(scenario())


def test_durable_save_then_controller_receipt_failure_recovers_without_abort(
    local_controller,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        target = _TargetApi(ledger, [])
        plugin = _Plugin(target)
        monkeypatch.setattr(
            local_controller,
            "finish_commit",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("receipt crash")),
        )

        result = await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
            lease_token=receipt["lease_token"],
        )

        assert result["status"] == "committed"
        assert local_controller.authority_state() == "committed"
        assert target.abort_calls == 0
        assert plugin.persisted == plugin.data[handoff.STORY_MIGRATION_COMMIT_KEY]

    asyncio.run(scenario())


def test_cancel_before_marker_harvests_prepare_then_aborts(local_controller) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        target = _TargetApi(ledger, [])
        target.prepare_release = asyncio.Event()
        plugin = _Plugin(target)
        operation = asyncio.create_task(
            handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        )
        await target.prepare_started.wait()
        operation.cancel()
        target.prepare_release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert ledger["status"]["status"] == "aborted"
        assert local_controller.authority_state() == "open"
        assert handoff.STORY_MIGRATION_COMMIT_KEY not in plugin.data

    asyncio.run(scenario())


def test_cancel_after_marker_harvests_commit_and_never_reopens(local_controller) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        target = _TargetApi(ledger, [])
        target.commit_release = asyncio.Event()
        plugin = _Plugin(target)
        operation = asyncio.create_task(
            handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        )
        await target.commit_started.wait()
        assert handoff.STORY_MIGRATION_COMMIT_KEY in plugin.data
        operation.cancel()
        target.commit_release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert ledger["status"]["status"] == "committed"
        assert local_controller.authority_state() == "committed"
        assert target.abort_calls == 0

    asyncio.run(scenario())


def test_target_commit_crash_leaves_exact_durable_replay(local_controller) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        ledger: dict = {}
        target = _TargetApi(ledger, [])
        target.commit_fail = True
        plugin = _Plugin(target)
        with pytest.raises(authority.StoryAuthorityError) as failed:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        assert failed.value.code == "story_handoff_target_call_failed"
        assert local_controller.authority_state() == "committed"
        assert ledger["status"]["status"] == "prepared"
        assert target.abort_calls == 0

        target.commit_fail = False
        result = await handoff.commit_story_handoff(
            plugin,
            generation=GENERATION,
        )
        assert result["status"] == "committed"
        assert ledger["status"]["status"] == "committed"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda descriptor: descriptor.__setitem__(
                "api_version", "content.story-api.v99"
            ),
            "story_handoff_target_version_unsupported",
        ),
        (
            lambda descriptor: descriptor.__setitem__(
                "capabilities", ["story.build-task"]
            ),
            "story_handoff_target_capability_missing",
        ),
        (
            lambda descriptor: descriptor.pop("degraded_reasons"),
            "story_handoff_target_descriptor_invalid",
        ),
    ],
)
def test_unknown_n_minus_one_or_partial_target_never_crosses_local_lease(
    local_controller,
    mutation,
    code: str,
) -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        receipt = await _lease(local_controller, snapshot)
        target = _TargetApi({}, [])
        descriptor = target.capabilities()
        mutation(descriptor)
        target.descriptor_override = descriptor
        plugin = _Plugin(target)
        with pytest.raises(authority.StoryAuthorityError) as rejected:
            await handoff.commit_story_handoff(
                plugin,
                generation=GENERATION,
                lease_token=receipt["lease_token"],
            )
        assert rejected.value.code == code
        assert local_controller.authority_state() == "leased"
        assert target.prepare_calls == target.commit_calls == 0

    asyncio.run(scenario())


class _StartupManager:
    backend_name = "json"
    data_file = Path("/tmp/story-handoff-test.json")
    sqlite_path = Path("/tmp/story-handoff-test.db")

    def __init__(self, sections: dict, data: dict) -> None:
        self.sections = copy.deepcopy(sections)
        self.data = copy.deepcopy(data)
        self.load_initial_calls = 0

    def load_sections(self, _names, *, backend_name=None, read_only=False):
        del backend_name, read_only
        return copy.deepcopy(self.sections)

    def load_initial_store(self):
        self.load_initial_calls += 1
        return copy.deepcopy(self.data)


class _StartupHarness(core_store.CoreStoreMixin):
    def __init__(self, manager: _StartupManager) -> None:
        self.store_manager = manager
        self.data_file = str(manager.data_file)
        self.enable_store_control_tag_sanitization = True
        self.maintenance_writes = 0

    def _sanitize_store_control_tags_inplace(self, data, _path=()):
        del _path
        data["creative_projects"] = [{"id": "mutated-by-maintenance"}]
        return 1

    def _sanitize_proactive_candidate_repeat_counts_inplace(self, _data):
        return 0

    def _compact_store_history_inplace(self, _data):
        return {}

    def _recover_bookshelf_after_load(self, _data):
        return 0

    def _persist_startup_maintenance_sync(self, _manager, before, after, _tombstones):
        if before != after:
            self.maintenance_writes += 1

    def _write_storage_backend_state(self, _backend, _path):
        return None


def test_malformed_startup_marker_blocks_before_load_or_maintenance_write(
    local_controller,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_store, "preflight_story_handoff_sections", handoff.preflight_story_handoff_sections)
    bad = _marker(_snapshot())
    bad["version"] = "companion.story-migration-commit.v2"
    manager = _StartupManager(
        {handoff.STORY_MIGRATION_COMMIT_KEY: bad},
        {"creative_projects": [], "creative_memory_pool": []},
    )
    host = _StartupHarness(manager)

    with pytest.raises(authority.StoryAuthorityError):
        host._load_data_sync()

    assert manager.load_initial_calls == 0
    assert host.maintenance_writes == 0
    assert local_controller.authority_state() == "blocked"


def test_valid_startup_marker_loads_but_restores_story_roots_before_maintenance(
    local_controller,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_store, "preflight_story_handoff_sections", handoff.preflight_story_handoff_sections)
    marker = _marker(_snapshot())
    data = {
        handoff.STORY_MIGRATION_COMMIT_KEY: marker,
        "creative_projects": [{"id": "legacy-source"}],
        "creative_memory_pool": [{"id": "legacy-memory"}],
    }
    manager = _StartupManager(
        {handoff.STORY_MIGRATION_COMMIT_KEY: marker},
        data,
    )
    host = _StartupHarness(manager)

    loaded = host._load_data_sync()

    assert loaded["creative_projects"] == [{"id": "legacy-source"}]
    assert loaded["creative_memory_pool"] == [{"id": "legacy-memory"}]
    assert host.maintenance_writes == 0
    assert local_controller.authority_state() == "committed"


def test_v1_sqlite_marker_preflight_is_read_only_and_preserves_legacy_schema(
    local_controller,
    tmp_path,
) -> None:
    marker = _marker(_snapshot())
    database = tmp_path / "companions.db"
    payload = json.dumps(marker, ensure_ascii=False)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE store_sections ("
            "section_name TEXT PRIMARY KEY,"
            "payload_json TEXT NOT NULL,"
            "updated_at REAL NOT NULL,"
            "checksum TEXT DEFAULT '',"
            "schema_version INTEGER DEFAULT 1)"
        )
        connection.execute(
            "INSERT INTO store_sections VALUES(?,?,?,?,1)",
            (
                handoff.STORY_MIGRATION_COMMIT_KEY,
                payload,
                1.0,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            ),
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()

    manager = core_store.StoreManager(
        backend_name="sqlite",
        data_file=tmp_path / "companions.json",
        sqlite_path=database,
        ensure_defaults=lambda value: value,
        new_store=dict,
    )
    sections = manager.load_sections(
        (handoff.STORY_MIGRATION_COMMIT_KEY,),
        read_only=True,
    )
    handoff.preflight_story_handoff_sections(sections)

    verification = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert verification.execute("PRAGMA user_version").fetchone() == (1,)
        tables = {
            row[0]
            for row in verification.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        verification.close()
    assert tables == {"store_sections"}
    assert not list(tmp_path.glob("companions.db.pre-v2-*.bak"))
    assert local_controller.authority_state() == "committed"
