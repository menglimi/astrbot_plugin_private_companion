# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import threading
import unittest
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.message_pipeline import event_data_save_boundary


class _RecordingManager:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        next_revision: int = 1,
        backend_name: str = "sqlite",
    ) -> None:
        self.loop = loop
        self.backend_name = backend_name
        self.revision_seed = next_revision
        self.section_attempts = 0
        self.section_writes: list[
            tuple[dict[str, tuple[int, Any]], dict[str, int]]
        ] = []
        self.snapshot_writes: list[dict[str, Any]] = []
        self.snapshot_deletions: list[dict[str, int]] = []
        self.snapshot_preserve_tombstones: list[bool] = []
        self.fail_section_attempts = 0
        self.confirm_only_on_attempt: dict[int, set[str]] = {}
        self.blocked_section_attempts: dict[
            int, tuple[asyncio.Event, asyncio.Event]
        ] = {}
        self.blocked_snapshot_attempts: dict[
            int, tuple[asyncio.Event, asyncio.Event]
        ] = {}
        self.snapshot_attempts = 0
        self._active_writers = 0
        self.max_active_writers = 0
        self._lock = threading.Lock()

    def next_revision(self) -> int:
        return self.revision_seed

    def block_section_attempt(
        self, attempt: int
    ) -> tuple[asyncio.Event, asyncio.Event]:
        started = asyncio.Event()
        release = asyncio.Event()
        self.blocked_section_attempts[attempt] = (started, release)
        return started, release

    def block_snapshot_attempt(
        self, attempt: int
    ) -> tuple[asyncio.Event, asyncio.Event]:
        started = asyncio.Event()
        release = asyncio.Event()
        self.blocked_snapshot_attempts[attempt] = (started, release)
        return started, release

    def _enter_writer(self) -> None:
        with self._lock:
            self._active_writers += 1
            self.max_active_writers = max(self.max_active_writers, self._active_writers)

    def _leave_writer(self) -> None:
        with self._lock:
            self._active_writers -= 1

    def _wait_for_gate(
        self,
        gate: tuple[asyncio.Event, asyncio.Event] | None,
    ) -> None:
        if gate is None:
            return
        started, release = gate
        self.loop.call_soon_threadsafe(started.set)
        future = asyncio.run_coroutine_threadsafe(release.wait(), self.loop)
        future.result(timeout=3.0)

    def save_sections(
        self,
        changed_sections: dict[str, tuple[int, Any]],
        deleted_sections: dict[str, int],
    ) -> dict[str, int]:
        self._enter_writer()
        try:
            self.section_attempts += 1
            attempt = self.section_attempts
            self._wait_for_gate(self.blocked_section_attempts.get(attempt))
            if attempt <= self.fail_section_attempts:
                raise OSError("injected section failure")
            changed_copy = {
                name: (revision, deepcopy(payload))
                for name, (revision, payload) in changed_sections.items()
            }
            deleted_copy = dict(deleted_sections)
            self.section_writes.append((changed_copy, deleted_copy))
            confirmed = {
                name: revision for name, (revision, _payload) in changed_copy.items()
            }
            confirmed.update(deleted_copy)
            if attempt in self.confirm_only_on_attempt:
                allowed = self.confirm_only_on_attempt[attempt]
                confirmed = {
                    name: revision
                    for name, revision in confirmed.items()
                    if name in allowed
                }
            if confirmed:
                self.revision_seed = max(
                    self.revision_seed, max(confirmed.values()) + 1
                )
            return confirmed
        finally:
            self._leave_writer()

    def save_snapshot(
        self,
        data: dict[str, Any],
        *,
        minimum_revision: int | None = None,
        deleted_sections: dict[str, int] | None = None,
        preserve_tombstones: bool = False,
    ) -> int:
        self._enter_writer()
        try:
            self.snapshot_attempts += 1
            self._wait_for_gate(
                self.blocked_snapshot_attempts.get(self.snapshot_attempts)
            )
            self.snapshot_writes.append(deepcopy(data))
            self.snapshot_deletions.append(dict(deleted_sections or {}))
            self.snapshot_preserve_tombstones.append(bool(preserve_tombstones))
            persisted_revision = max(
                self.revision_seed,
                int(minimum_revision or self.revision_seed),
            )
            self.revision_seed = persisted_revision + 1
            return persisted_revision
        finally:
            self._leave_writer()

    def save_store(self, data: dict[str, Any]) -> None:
        self.save_snapshot(data)


class _WriterHarness(CoreStoreMixin):
    def __init__(
        self, data: dict[str, Any], *, backend: str = "sqlite", seed: int = 1
    ) -> None:
        self.data = data
        self.storage_backend = backend
        self.store_manager = _RecordingManager(
            asyncio.get_running_loop(),
            next_revision=seed,
            backend_name=backend,
        )
        self.enable_multi_persona_mode = False
        self.enable_store_control_tag_sanitization = True
        self._stop_event = asyncio.Event()
        self._data_save_task = None
        self._data_save_dirty: dict[str, int] = {}
        self._data_save_deleted: dict[str, int] = {}
        self._data_save_full_revision = 0
        self._data_save_revision = seed - 1
        self._persona_data_save_tasks: dict[str, asyncio.Task] = {}
        self._persona_data_save_dirty: dict[str, dict[str, int]] = {}
        self._persona_data_save_deleted: dict[str, dict[str, int]] = {}
        self._persona_data_save_full_revision: dict[str, int] = {}
        self._persona_data_save_revision: dict[str, int] = {}
        self._data_save_max_delay_seconds = 0.05
        self._data_save_retry_base_seconds = 0.01
        self._data_save_retry_max_seconds = 0.03
        self.sanitized_roots: list[tuple[str, ...]] = []

    def _sanitize_store_control_tags_inplace(
        self,
        value: Any,
        _path: tuple[Any, ...] = (),
    ) -> int:
        if not _path and isinstance(value, dict):
            self.sanitized_roots.append(tuple(str(key) for key in value))
        return super()._sanitize_store_control_tags_inplace(value, _path)


class _PersonaWriterHarness(_WriterHarness):
    def __init__(self) -> None:
        self._active_persona_id = ""
        self._data_default = {"users": {}}
        self._persona_data_profiles = {
            "main": {"users": {}, "groups": {}},
            "alt": {"users": {}, "groups": {}},
        }
        super().__init__(self._data_default, backend="json")
        self.enable_multi_persona_mode = True
        self.persona_attempts: dict[str, int] = {}
        self.persona_failures: dict[str, int] = {}
        self.persona_writes: list[tuple[str, dict[str, Any]]] = []
        self.persona_gates: dict[
            tuple[str, int], tuple[asyncio.Event, asyncio.Event]
        ] = {}

    @property
    def data(self) -> dict[str, Any]:
        if self._active_persona_id:
            return self._persona_data_profiles[self._active_persona_id]
        return self._data_default

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._data_default = value

    def _active_persona_scope(self) -> str:
        return self._active_persona_id

    def activate(self, persona_id: str) -> None:
        self._active_persona_id = persona_id

    def block_persona_attempt(
        self,
        persona_id: str,
        attempt: int,
    ) -> tuple[asyncio.Event, asyncio.Event]:
        started = asyncio.Event()
        release = asyncio.Event()
        self.persona_gates[(persona_id, attempt)] = (started, release)
        return started, release

    def _save_persona_profile_sync(
        self,
        persona_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        attempt = self.persona_attempts.get(persona_id, 0) + 1
        self.persona_attempts[persona_id] = attempt
        self.store_manager._wait_for_gate(self.persona_gates.get((persona_id, attempt)))
        if attempt <= self.persona_failures.get(persona_id, 0):
            raise OSError(f"injected persona failure: {persona_id}")
        self.persona_writes.append((persona_id, deepcopy(data or {})))


class _BatchEvent:
    def __init__(self) -> None:
        self.stopped = False

    def stop_event(self) -> None:
        self.stopped = True

    def is_stopped(self) -> bool:
        return self.stopped


class _BoundaryHarness(_WriterHarness):
    @event_data_save_boundary
    async def early_mutation(self, event: _BatchEvent) -> None:
        self._schedule_data_save(sections={"users"})

    @event_data_save_boundary
    async def stopped_early_mutation(self, event: _BatchEvent) -> None:
        self._schedule_data_save(sections={"users"})
        event.stop_event()

    @event_data_save_boundary(flush=True)
    async def final_mutation(self, event: _BatchEvent) -> None:
        self._schedule_data_save(sections={"groups"})

    @event_data_save_boundary
    async def failed_mutation(self, event: _BatchEvent) -> None:
        self._schedule_data_save(sections={"users"})
        raise RuntimeError("injected event hook failure")


class IncrementalPersistenceWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_owned_batch_resumes_across_filter_tasks(self) -> None:
        harness = _BoundaryHarness({"users": {}, "groups": {}})
        harness._stop_event.set()
        event = _BatchEvent()

        await harness.early_mutation(event)
        self.assertTrue(hasattr(event, "_private_companion_event_data_save_batch"))
        await harness.final_mutation(event)

        self.assertFalse(hasattr(event, "_private_companion_event_data_save_batch"))
        self.assertEqual({"users", "groups"}, set(harness._data_save_dirty))
        self.assertEqual(1, len(set(harness._data_save_dirty.values())))
        self.assertEqual(1, harness._data_save_revision)

    async def test_stopped_early_filter_flushes_pending_sections(self) -> None:
        harness = _BoundaryHarness({"users": {}})
        harness._stop_event.set()
        event = _BatchEvent()

        await harness.stopped_early_mutation(event)

        self.assertTrue(event.is_stopped())
        self.assertFalse(hasattr(event, "_private_companion_event_data_save_batch"))
        self.assertEqual({"users"}, set(harness._data_save_dirty))
        self.assertEqual(1, harness._data_save_revision)

    async def test_failed_filter_flushes_pending_sections(self) -> None:
        harness = _BoundaryHarness({"users": {}})
        harness._stop_event.set()
        event = _BatchEvent()

        with self.assertRaisesRegex(RuntimeError, "injected event hook failure"):
            await harness.failed_mutation(event)

        self.assertFalse(hasattr(event, "_private_companion_event_data_save_batch"))
        self.assertEqual({"users"}, set(harness._data_save_dirty))
        self.assertEqual(1, harness._data_save_revision)

    async def test_event_batch_submits_one_union_revision(self) -> None:
        harness = _WriterHarness({"users": {}, "groups": {}})
        harness._stop_event.set()
        event = SimpleNamespace()
        handle = harness._begin_event_data_save_batch(event)

        harness._schedule_data_save(sections={"users"}, delay=0.8)
        harness._save_data_sync(sections={"groups"})

        self.assertEqual(0, harness._data_save_revision)
        self.assertEqual(
            {"users", "groups"},
            event._private_companion_pending_save_sections,
        )

        harness._finish_event_data_save_batch(handle)

        self.assertFalse(
            hasattr(event, "_private_companion_pending_save_sections")
        )
        self.assertEqual({"users", "groups"}, set(harness._data_save_dirty))
        self.assertEqual(1, len(set(harness._data_save_dirty.values())))
        self.assertEqual(1, harness._data_save_revision)

    async def test_empty_event_batch_does_not_allocate_revision(self) -> None:
        harness = _WriterHarness({"users": {}})
        event = SimpleNamespace()

        handle = harness._begin_event_data_save_batch(event)
        harness._finish_event_data_save_batch(handle)

        self.assertEqual(0, harness._data_save_revision)
        self.assertFalse(harness._default_data_save_is_dirty())

    async def test_child_task_does_not_write_into_closed_event_batch(self) -> None:
        harness = _WriterHarness({"users": {}})
        harness._stop_event.set()
        event = SimpleNamespace()
        release = asyncio.Event()
        handle = harness._begin_event_data_save_batch(event)

        async def save_after_event() -> None:
            await release.wait()
            harness._schedule_data_save(sections={"users"})

        task = asyncio.create_task(save_after_event())
        harness._finish_event_data_save_batch(handle)
        release.set()
        await task

        self.assertEqual({"users"}, set(harness._data_save_dirty))
        self.assertEqual(1, harness._data_save_revision)

    async def test_save_contract_requires_explicit_sections_or_allowlisted_scope(self) -> None:
        harness = _WriterHarness({"users": {}})

        with self.assertRaisesRegex(ValueError, "sections must be explicit"):
            harness._schedule_data_save()
        with self.assertRaisesRegex(ValueError, "unknown full save scope"):
            harness._schedule_data_save(full_scope="not-allowed")
        with self.assertRaisesRegex(ValueError, "unknown durable sections"):
            harness._schedule_data_save(sections={"not-a-section"})
        with self.assertRaisesRegex(ValueError, "disjoint"):
            harness._schedule_data_save(
                sections={"users"},
                deleted_sections={"users"},
            )

    def test_runtime_sections_are_initialized_in_new_store(self) -> None:
        store = CoreStoreMixin._new_store(object())

        self.assertEqual({}, store["proactive_review_runtime"])
        self.assertEqual({}, store["proactive_runtime"])
        self.assertEqual([], store["proactive_audit_log"])
        self.assertEqual({}, store["passive_no_reply_records"])

    async def test_section_marks_share_revision_and_empty_mark_is_noop(self) -> None:
        harness = _WriterHarness({"users": {}, "groups": {}})
        harness._stop_event.set()

        harness._schedule_data_save(sections={"users", "groups"})
        revision = harness._data_save_dirty["users"]

        self.assertGreaterEqual(revision, 1)
        self.assertEqual(revision, harness._data_save_dirty["groups"])
        before = harness._data_save_revision
        harness._schedule_data_save(sections=set())
        self.assertEqual(before, harness._data_save_revision)
        self.assertIsNone(harness._data_save_task)

    async def test_full_fallback_marks_every_live_root_with_one_revision(self) -> None:
        harness = _WriterHarness({"users": {}, "groups": {}, "daily_state": {}})
        harness._stop_event.set()

        harness._schedule_data_save(full_scope="admin_import_export")

        self.assertEqual(
            {"users", "groups", "daily_state"}, set(harness._data_save_dirty)
        )
        self.assertEqual(1, len(set(harness._data_save_dirty.values())))
        self.assertEqual(
            next(iter(harness._data_save_dirty.values())),
            harness._data_save_full_revision,
        )

    async def test_writer_uses_store_manager_backend_over_config_string(self) -> None:
        harness = _WriterHarness({"users": {}}, backend="sqlite")
        harness.storage_backend = "json"

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(1, len(harness.store_manager.section_writes))
        self.assertFalse(harness.store_manager.snapshot_writes)

    async def test_primary_json_delayed_snapshot_keeps_restart_tail_only(self) -> None:
        harness = _WriterHarness(
            {
                "groups": {
                    "room": {
                        "recent_messages": [
                            {"text": str(index)} for index in range(15)
                        ],
                        "recent_bot_replies": [
                            {"text": f"bot-{index}"} for index in range(15)
                        ],
                    }
                }
            },
            backend="json",
        )

        harness._schedule_data_save(full_scope="startup_maintenance", delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        saved = harness.store_manager.snapshot_writes[-1]
        self.assertEqual(12, len(saved["groups"]["room"]["recent_messages"]))
        self.assertEqual(12, len(saved["groups"]["room"]["recent_bot_replies"]))
        self.assertEqual(
            15,
            len(harness.data["groups"]["room"]["recent_messages"]),
        )

    async def test_sync_save_with_explicit_sections_only_writes_target_section(self) -> None:
        harness = _WriterHarness(
            {
                "users": {"owner": {"name": "changed"}},
                "groups": {"room": {"name": "untouched"}},
                "daily_state": {"mood": "steady"},
            }
        )

        await asyncio.to_thread(harness._save_data_sync, sections={"users"})

        self.assertEqual(1, len(harness.store_manager.section_writes))
        changed, deleted = harness.store_manager.section_writes[0]
        self.assertEqual({"users"}, set(changed))
        self.assertFalse(deleted)
        self.assertFalse(harness._default_data_save_is_dirty())

    async def test_sync_save_requires_an_explicit_full_compatibility_scope(self) -> None:
        harness = _WriterHarness(
            {
                "users": {},
                "groups": {},
                "daily_state": {},
            }
        )

        with self.assertRaisesRegex(ValueError, "sections must be explicit"):
            await asyncio.to_thread(harness._save_data_sync)

        await asyncio.to_thread(
            harness._save_data_sync,
            full_scope="admin_import_export",
        )

        self.assertEqual(1, len(harness.store_manager.section_writes))
        changed, deleted = harness.store_manager.section_writes[0]
        self.assertEqual({"users", "groups", "daily_state"}, set(changed))
        self.assertFalse(deleted)

    async def test_sync_json_save_keeps_full_file_replacement_but_dirty_scope(self) -> None:
        harness = _WriterHarness(
            {
                "users": {"owner": {"summary": "clean <bubble/> me"}},
                "groups": {"room": {"summary": "keep <bubble/> raw"}},
            },
            backend="json",
        )

        await asyncio.to_thread(harness._save_data_sync, sections={"users"})

        self.assertFalse(harness.store_manager.section_writes)
        self.assertEqual(1, len(harness.store_manager.snapshot_writes))
        snapshot = harness.store_manager.snapshot_writes[0]
        self.assertNotIn("<bubble/>", snapshot["users"]["owner"]["summary"])
        self.assertIn("<bubble/>", snapshot["groups"]["room"]["summary"])
        self.assertNotIn("<bubble/>", harness.data["users"]["owner"]["summary"])
        self.assertIn("<bubble/>", harness.data["groups"]["room"]["summary"])

    async def test_writer_keeps_newer_same_section_revision_for_second_batch(
        self,
    ) -> None:
        harness = _WriterHarness({"users": {"owner": {"name": "old"}}, "groups": {}})
        started, release = harness.store_manager.block_section_attempt(1)

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(started.wait(), timeout=0.5)
        harness.data["users"]["owner"]["name"] = "latest"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        release.set()
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(2, len(harness.store_manager.section_writes))
        first = harness.store_manager.section_writes[0][0]["users"][1]
        second = harness.store_manager.section_writes[1][0]["users"][1]
        self.assertEqual("old", first["owner"]["name"])
        self.assertEqual("latest", second["owner"]["name"])
        self.assertFalse(harness._data_save_dirty)

    async def test_section_added_during_write_is_not_cleared_by_first_batch(
        self,
    ) -> None:
        harness = _WriterHarness({"users": {"owner": {}}, "groups": {}})
        started, release = harness.store_manager.block_section_attempt(1)

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(started.wait(), timeout=0.5)
        harness.data["groups"]["room"] = {"name": "new"}
        harness._schedule_data_save(sections={"groups"}, delay=0.0)
        release.set()
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual({"users"}, set(harness.store_manager.section_writes[0][0]))
        self.assertEqual({"groups"}, set(harness.store_manager.section_writes[1][0]))

    async def test_failed_write_retries_in_one_writer_and_retains_dirty_revision(
        self,
    ) -> None:
        harness = _WriterHarness({"users": {"owner": {"name": "kept"}}})
        harness.store_manager.fail_section_attempts = 1

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(2, harness.store_manager.section_attempts)
        self.assertEqual(1, len(harness.store_manager.section_writes))
        self.assertEqual(1, harness.store_manager.max_active_writers)
        self.assertFalse(harness._data_save_dirty)

    async def test_only_explicit_delete_becomes_tombstone(self) -> None:
        harness = _WriterHarness({"users": {}})

        harness._schedule_data_save(
            sections={"users"},
            deleted_sections={"memo_notes"},
            delay=0.0,
        )
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        changed, deleted = harness.store_manager.section_writes[0]
        self.assertEqual({"users"}, set(changed))
        self.assertEqual({"memo_notes"}, set(deleted))
        with self.assertRaises(ValueError):
            harness._schedule_data_save(
                sections={"users"},
                deleted_sections={"users"},
            )
        with self.assertRaisesRegex(ValueError, "full_scope cannot be combined"):
            harness._schedule_data_save(
                full_scope="admin_import_export",
                deleted_sections={"memo_notes"},
            )

    async def test_bookshelf_group_is_captured_with_one_revision(self) -> None:
        harness = _WriterHarness(
            {
                "bookshelf_items": [{"id": "one"}],
                "bookshelf_secret": {"token": "secret"},
                "bookshelf_store_revision": 7,
                "reading_archive_integration": {"enabled": True},
                "users": {},
            }
        )

        harness._schedule_data_save(sections={"bookshelf_items"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        changed, deleted = harness.store_manager.section_writes[0]
        expected = {
            "bookshelf_items",
            "bookshelf_secret",
            "bookshelf_store_revision",
            "reading_archive_integration",
        }
        self.assertEqual(expected, set(changed))
        self.assertFalse(deleted)
        self.assertEqual(1, len({revision for revision, _payload in changed.values()}))

    async def test_partial_backend_confirmation_keeps_unconfirmed_section_dirty(
        self,
    ) -> None:
        harness = _WriterHarness({"users": {}, "groups": {}})
        harness.store_manager.confirm_only_on_attempt[1] = {"users"}

        harness._schedule_data_save(sections={"users", "groups"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(2, harness.store_manager.section_attempts)
        self.assertEqual(
            {"users", "groups"}, set(harness.store_manager.section_writes[0][0])
        )
        self.assertEqual({"groups"}, set(harness.store_manager.section_writes[1][0]))
        self.assertFalse(harness._data_save_dirty)

    async def test_sqlite_full_fallback_upsert_precedes_partial_with_fresh_revision(
        self,
    ) -> None:
        harness = _WriterHarness({"users": {"owner": {"name": "full"}}}, seed=20)

        harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)
        harness.data["users"]["owner"]["name"] = "partial"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertFalse(harness.store_manager.snapshot_writes)
        self.assertEqual(2, len(harness.store_manager.section_writes))
        full_revision = harness.store_manager.section_writes[0][0]["users"][0]
        partial_revision = harness.store_manager.section_writes[1][0]["users"][0]
        self.assertGreater(partial_revision, full_revision)

    async def test_full_compatibility_upsert_keeps_mutation_for_second_batch(self) -> None:
        harness = _WriterHarness(
            {"users": {"owner": {"name": "old"}}, "groups": {}}, seed=20
        )
        started, release = harness.store_manager.block_section_attempt(1)

        harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
        await asyncio.wait_for(started.wait(), timeout=0.5)
        harness.data["users"]["owner"]["name"] = "latest"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        release.set()

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertFalse(harness.store_manager.snapshot_writes)
        self.assertEqual(2, len(harness.store_manager.section_writes))
        self.assertEqual(
            "latest",
            harness.store_manager.section_writes[1][0]["users"][1]["owner"]["name"],
        )
        self.assertFalse(harness._data_save_dirty)

    async def test_full_compatibility_upsert_keeps_newer_revision_before_capture(
        self,
    ) -> None:
        harness = _WriterHarness(
            {"users": {"owner": {"name": "old"}}, "groups": {}}, seed=20
        )

        harness._schedule_data_save(full_scope="admin_import_export", delay=0.05)
        harness.data["users"]["owner"]["name"] = "latest"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertFalse(harness.store_manager.snapshot_writes)
        self.assertEqual(1, len(harness.store_manager.section_writes))
        changed = harness.store_manager.section_writes[0][0]
        self.assertGreater(changed["users"][0], changed["groups"][0])
        self.assertEqual(22, harness.store_manager.next_revision())
        self.assertFalse(harness._data_save_dirty)
        self.assertEqual(0, harness._data_save_full_revision)

    async def test_full_compatibility_upsert_preserves_explicit_tombstone(self) -> None:
        harness = _WriterHarness(
            {"users": {}, "memo_notes": {"value": "legacy"}}, seed=20
        )
        harness._stop_event.set()
        harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
        harness.data.pop("memo_notes")
        harness._schedule_data_save(
            sections=set(),
            deleted_sections={"memo_notes"},
            delay=0.0,
        )
        harness._stop_event.clear()
        harness._start_default_data_save_writer(0.0)

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertFalse(harness.store_manager.snapshot_writes)
        changed, deleted = harness.store_manager.section_writes[0]
        self.assertEqual({"users"}, set(changed))
        self.assertEqual({"memo_notes": 21}, deleted)

    async def test_terminate_full_dirty_uses_snapshot_for_missing_section(
        self,
    ) -> None:
        harness = _WriterHarness(
            {"users": {}, "memo_notes": {"value": "legacy"}}, seed=20
        )
        harness._stop_event.set()
        harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
        harness.data.pop("memo_notes")

        await harness._flush_default_data_save_on_terminate()

        self.assertEqual(1, len(harness.store_manager.snapshot_writes))
        self.assertEqual({"users": {}}, harness.store_manager.snapshot_writes[0])
        self.assertEqual([{}], harness.store_manager.snapshot_deletions)
        self.assertEqual([True], harness.store_manager.snapshot_preserve_tombstones)
        self.assertFalse(harness.store_manager.section_writes)
        self.assertFalse(harness._default_data_save_is_dirty())

    async def test_terminate_partial_dirty_does_not_infer_missing_section_delete(
        self,
    ) -> None:
        harness = _WriterHarness(
            {"users": {}, "memo_notes": {"value": "legacy"}}, seed=20
        )
        harness._stop_event.set()
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        harness.data.pop("memo_notes")

        await harness._flush_default_data_save_on_terminate()

        self.assertFalse(harness.store_manager.snapshot_writes)
        self.assertEqual(1, len(harness.store_manager.section_writes))
        changed, deleted = harness.store_manager.section_writes[0]
        self.assertEqual({"users"}, set(changed))
        self.assertEqual({}, deleted)
        self.assertFalse(harness._default_data_save_is_dirty())

    async def test_partial_dirty_missing_section_requires_explicit_tombstone(self) -> None:
        harness = _WriterHarness(
            {"users": {}, "memo_notes": {"value": "legacy"}}, seed=20
        )
        harness._stop_event.set()
        harness._schedule_data_save(sections={"memo_notes"}, delay=0.0)
        revision = harness._data_save_dirty["memo_notes"]
        harness.data.pop("memo_notes")

        with self.assertRaisesRegex(RuntimeError, "explicit tombstones"):
            await harness._flush_default_data_save_on_terminate()

        self.assertFalse(harness.store_manager.section_writes)
        self.assertEqual(revision, harness._data_save_dirty["memo_notes"])
        self.assertTrue(harness._default_data_save_is_dirty())

    async def test_reset_remains_an_explicit_full_snapshot(self) -> None:
        harness = _WriterHarness(
            {"users": {"owner": {}}, "obsolete": {"value": "legacy"}},
            seed=20,
        )
        harness._data_lock = asyncio.Lock()
        harness.default_enable_configured_targets = False
        harness._new_store = lambda: {"users": {"reset": True}}

        await harness._reset_plugin_store()

        self.assertEqual({"users": {"reset": True}}, harness.data)
        self.assertEqual([{"users": {"reset": True}}], harness.store_manager.snapshot_writes)
        self.assertFalse(harness.store_manager.section_writes)
        self.assertFalse(harness._default_data_save_is_dirty())

    async def test_max_delay_bounds_first_write(self) -> None:
        harness = _WriterHarness({"users": {}})
        started, release = harness.store_manager.block_section_attempt(1)

        harness._schedule_data_save(sections={"users"}, delay=60.0)
        await asyncio.wait_for(started.wait(), timeout=0.3)
        release.set()
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(1, len(harness.store_manager.section_writes))

    async def test_json_writes_full_file_but_sanitizes_only_dirty_roots(self) -> None:
        harness = _WriterHarness(
            {
                "users": {"owner": {"summary": "clean <bubble/> me"}},
                "groups": {"room": {"summary": "leave <bubble/> alone"}},
            },
            backend="json",
        )

        users_identity = harness.data["users"]
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual([("users",)], harness.sanitized_roots)
        snapshot = harness.store_manager.snapshot_writes[0]
        self.assertNotIn("<bubble/>", snapshot["users"]["owner"]["summary"])
        self.assertIn("<bubble/>", snapshot["groups"]["room"]["summary"])
        self.assertNotIn("<bubble/>", harness.data["users"]["owner"]["summary"])
        self.assertIn("<bubble/>", harness.data["groups"]["room"]["summary"])
        self.assertIs(users_identity, harness.data["users"])

    async def test_candidate_compaction_uses_users_as_unsanitized_readonly_context(
        self,
    ) -> None:
        candidates = [
            {"id": f"candidate-{index}", "status": "blocked", "created_ts": index}
            for index in range(601)
        ]
        harness = _WriterHarness(
            {
                "users": {
                    "owner": {
                        "planned_candidate_id": "candidate-0",
                        "summary": "raw <bubble/> user",
                    }
                },
                "proactive_candidate_pool": candidates,
            }
        )
        pool_identity = harness.data["proactive_candidate_pool"]

        harness._schedule_data_save(sections={"proactive_candidate_pool"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        changed = harness.store_manager.section_writes[0][0]
        self.assertEqual({"proactive_candidate_pool"}, set(changed))
        self.assertEqual([("proactive_candidate_pool",)], harness.sanitized_roots)
        self.assertEqual(
            "raw <bubble/> user", harness.data["users"]["owner"]["summary"]
        )
        self.assertIs(pool_identity, harness.data["proactive_candidate_pool"])
        self.assertEqual(600, len(pool_identity))
        self.assertIn("candidate-0", {item["id"] for item in pool_identity})

    async def test_repeat_count_sanitizer_persists_derived_timestamp_at_same_revision(
        self,
    ) -> None:
        harness = _WriterHarness(
            {
                "users": {},
                "proactive_candidate_pool": [
                    {"id": "candidate", "status": "blocked", "repeat_count": 99}
                ],
            }
        )

        harness._schedule_data_save(sections={"proactive_candidate_pool"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        changed = harness.store_manager.section_writes[0][0]
        self.assertEqual(
            {"proactive_candidate_pool", "proactive_candidate_repeat_sanitized_at"},
            set(changed),
        )
        self.assertEqual(1, len({revision for revision, _payload in changed.values()}))
        self.assertIn("proactive_candidate_repeat_sanitized_at", harness.data)
        self.assertEqual(6, harness.data["proactive_candidate_pool"][0]["repeat_count"])

    async def test_stale_sanitizer_result_does_not_overwrite_newer_live_value(
        self,
    ) -> None:
        harness = _WriterHarness(
            {"users": {"owner": {"summary": "old<bubble/>"}}},
            backend="json",
        )
        first_started, first_release = harness.store_manager.block_snapshot_attempt(1)
        second_started, second_release = harness.store_manager.block_snapshot_attempt(2)

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(first_started.wait(), timeout=0.5)
        harness.data["users"]["owner"]["summary"] = "latest<bubble/>"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        first_release.set()
        await asyncio.wait_for(second_started.wait(), timeout=0.5)

        self.assertEqual("latest<bubble/>", harness.data["users"]["owner"]["summary"])
        second_release.set()
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)
        self.assertEqual("latest", harness.data["users"]["owner"]["summary"])

    async def test_sync_snapshot_supersedes_captured_default_writer_batch(
        self,
    ) -> None:
        harness = _WriterHarness(
            {"users": {"owner": {"name": "old"}}},
            backend="json",
        )
        harness._stop_event.set()
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        batch = harness._capture_default_data_save_batch()
        harness.data["users"]["owner"]["name"] = "sync-latest"

        lock = harness._data_save_io_lock()
        lock.acquire()
        try:
            stale_writer = asyncio.create_task(
                asyncio.to_thread(harness._write_default_data_save_batch_sync, batch)
            )
            await asyncio.sleep(0)
            # Re-entering the lock is intentional: the event-loop thread owns
            # it while the captured writer is queued behind it.
            harness._save_data_now_sync(full_scope="admin_import_export")
        finally:
            lock.release()
        result = await asyncio.wait_for(stale_writer, timeout=1.0)
        harness._finish_default_data_save_batch(batch, result)

        self.assertTrue(result["superseded"])
        self.assertEqual(1, len(harness.store_manager.snapshot_writes))
        self.assertEqual(
            "sync-latest",
            harness.store_manager.snapshot_writes[0]["users"]["owner"]["name"],
        )
        self.assertTrue(harness._data_save_dirty)

    async def test_stopping_keeps_dirty_without_starting_writer(self) -> None:
        harness = _WriterHarness({"users": {}})
        harness._stop_event.set()

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=0.2)

        self.assertTrue(harness._data_save_dirty)
        self.assertIsNone(harness._data_save_task)

    async def test_persona_writers_are_isolated_and_failure_does_not_clear_peer(
        self,
    ) -> None:
        harness = _PersonaWriterHarness()
        harness.persona_failures["main"] = 1
        harness.activate("main")
        harness.data["users"]["owner"] = {"name": "main"}
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        harness.activate("alt")
        harness.data["groups"]["room"] = {"name": "alt"}
        harness._schedule_data_save(sections={"groups"}, delay=0.0)
        harness.activate("")

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        self.assertEqual(2, harness.persona_attempts["main"])
        self.assertEqual(1, harness.persona_attempts["alt"])
        self.assertEqual(
            {"main", "alt"}, {persona for persona, _data in harness.persona_writes}
        )
        self.assertFalse(harness._persona_data_save_dirty)
        self.assertFalse(harness._persona_data_save_tasks)

    async def test_persona_full_file_sanitizes_only_dirty_root(self) -> None:
        harness = _PersonaWriterHarness()
        harness._persona_data_profiles["main"] = {
            "users": {"owner": {"summary": "clean <bubble/> me"}},
            "groups": {"room": {"summary": "keep <bubble/> raw"}},
        }
        harness.activate("main")

        harness._schedule_data_save(sections={"users"}, delay=0.0)
        harness.activate("")
        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        saved = next(
            data for persona, data in harness.persona_writes if persona == "main"
        )
        self.assertNotIn("<bubble/>", saved["users"]["owner"]["summary"])
        self.assertIn("<bubble/>", saved["groups"]["room"]["summary"])
        self.assertNotIn(
            "<bubble/>",
            harness._persona_data_profiles["main"]["users"]["owner"]["summary"],
        )

    async def test_persona_mutation_during_write_is_saved_in_second_batch(self) -> None:
        harness = _PersonaWriterHarness()
        started, release = harness.block_persona_attempt("main", 1)
        harness.activate("main")
        harness.data["users"]["owner"] = {"name": "old"}
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        await asyncio.wait_for(started.wait(), timeout=0.5)
        harness.data["users"]["owner"]["name"] = "latest"
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        harness.activate("")
        release.set()

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=1.0)

        writes = [data for persona, data in harness.persona_writes if persona == "main"]
        self.assertEqual(2, len(writes))
        self.assertEqual("old", writes[0]["users"]["owner"]["name"])
        self.assertEqual("latest", writes[1]["users"]["owner"]["name"])

    async def test_sync_persona_snapshot_supersedes_captured_writer_batch(self) -> None:
        harness = _PersonaWriterHarness()
        harness._stop_event.set()
        harness.activate("main")
        harness.data["users"]["owner"] = {"name": "old"}
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        batch = harness._capture_persona_data_save_batch("main")

        harness.data["users"]["owner"]["name"] = "sync-latest"
        lock = harness._data_save_io_lock("main")
        lock.acquire()
        try:
            stale_writer = asyncio.create_task(
                asyncio.to_thread(
                    harness._write_persona_data_save_batch_sync,
                    "main",
                    batch,
                )
            )
            await asyncio.sleep(0)
            harness._write_persona_data_snapshot_sync("main", deepcopy(harness.data))
        finally:
            lock.release()
        harness.activate("")
        result = await asyncio.wait_for(stale_writer, timeout=1.0)
        harness._finish_persona_data_save_batch("main", batch, result)

        writes = [data for persona, data in harness.persona_writes if persona == "main"]
        self.assertTrue(result["superseded"])
        self.assertEqual(1, len(writes))
        self.assertEqual("sync-latest", writes[0]["users"]["owner"]["name"])
        self.assertTrue(harness._persona_data_save_dirty)

    async def test_flush_waits_for_default_and_all_persona_writers(self) -> None:
        harness = _PersonaWriterHarness()
        default_started, default_release = harness.store_manager.block_snapshot_attempt(
            1
        )
        persona_started, persona_release = harness.block_persona_attempt("main", 1)
        harness._data_default["users"]["default"] = {"name": "default"}
        harness._schedule_default_data_save(0.0, sections={"users"})
        harness.activate("main")
        harness.data["users"]["owner"] = {"name": "main"}
        harness._schedule_data_save(sections={"users"}, delay=0.0)
        harness.activate("")
        await asyncio.wait_for(default_started.wait(), timeout=0.5)
        await asyncio.wait_for(persona_started.wait(), timeout=0.5)

        flushing = asyncio.create_task(harness._flush_scheduled_data_save())
        await asyncio.sleep(0)
        self.assertFalse(flushing.done())
        default_release.set()
        await asyncio.sleep(0.01)
        self.assertFalse(flushing.done())
        persona_release.set()
        await asyncio.wait_for(flushing, timeout=1.0)


if __name__ == "__main__":
    unittest.main()
