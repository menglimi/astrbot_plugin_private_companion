# -*- coding: utf-8 -*-
"""CoreStoreWriteScheduleMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 607 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import Collection
from .core_store_shared import _single_line
from .core_store_shared import asyncio
from .core_store_shared import deepcopy



class CoreStoreWriteScheduleMixin:
    """CoreStoreWriteScheduleMixin（从 CoreStoreMixin 拆出）。"""


    def _write_default_data_save_batch_sync(
        self,
        batch: dict[str, Any],
        *,
        advance_generation: bool = False,
    ) -> dict[str, Any]:
        if batch["incremental"] and batch["missing_revisions"]:
            missing = ", ".join(sorted(batch["missing_revisions"]))
            raise RuntimeError(
                "SQLite incremental save found missing dirty sections without "
                f"explicit tombstones: {missing}"
            )
        prepared, control_changed, repeat_changed, compacted, cleaned_sections = self._prepare_dirty_save_payloads_sync(
            batch["payloads"],
            batch["readonly_context"],
        )
        changed_revisions = dict(batch["changed_revisions"])
        if (
            "proactive_candidate_repeat_sanitized_at" in prepared
            and "proactive_candidate_repeat_sanitized_at" not in changed_revisions
            and "proactive_candidate_pool" in changed_revisions
        ):
            changed_revisions["proactive_candidate_repeat_sanitized_at"] = changed_revisions[
                "proactive_candidate_pool"
            ]
        manager = getattr(self, "store_manager", None)
        confirmed: dict[str, int] = {}
        superseded = False
        if batch["incremental"]:
            with self._data_save_io_lock():
                if batch["write_generation"] != self._current_data_save_write_generation():
                    superseded = True
                else:
                    confirmed = manager.save_sections(
                        {
                            section: (revision, prepared[section])
                            for section, revision in changed_revisions.items()
                            if section in prepared
                        },
                        batch["deleted_revisions"],
                    )
                    if advance_generation:
                        self._advance_data_save_write_generation()
        else:
            snapshot = batch["full_snapshot"]
            for section, value in prepared.items():
                snapshot[section] = value
            for section in batch["deleted_revisions"]:
                snapshot.pop(section, None)
            # SQLite full replacement assigns one backend revision to the whole
            # snapshot.  Raise that baseline to the newest revision captured in
            # this batch so its confirmation never trails the payload it stores.
            sqlite_snapshot = (
                manager is not None
                and str(getattr(manager, "backend_name", "") or "").lower()
                == "sqlite"
            )
            expected_revisions = {
                **changed_revisions,
                **batch["deleted_revisions"],
                **batch["missing_revisions"],
            }
            if sqlite_snapshot:
                minimum_revision = max(
                    int(batch["full_revision"] or 0),
                    *(int(revision) for revision in expected_revisions.values()),
                )
                with self._data_save_io_lock():
                    if batch["write_generation"] != self._current_data_save_write_generation():
                        superseded = True
                    else:
                        persisted_revision = manager.save_snapshot(
                            snapshot,
                            minimum_revision=max(1, minimum_revision),
                            deleted_sections=batch["deleted_revisions"],
                            preserve_tombstones=bool(
                                batch.get("preserve_tombstones", False)
                            ),
                        )
                        if (
                            isinstance(persisted_revision, bool)
                            or not isinstance(persisted_revision, int)
                            or persisted_revision < minimum_revision
                        ):
                            raise RuntimeError(
                                "SQLite full snapshot did not confirm its minimum revision"
                            )
                        confirmed = {
                            section: persisted_revision for section in expected_revisions
                        }
                        if advance_generation:
                            self._advance_data_save_write_generation()
            else:
                with self._data_save_io_lock():
                    if batch["write_generation"] != self._current_data_save_write_generation():
                        superseded = True
                    elif manager is not None:
                        primary_snapshot = deepcopy(snapshot)
                        self._strip_ephemeral_group_transcripts_inplace(primary_snapshot)
                        manager.save_snapshot(primary_snapshot)
                        status_getter = getattr(manager, "persistence_status", None)
                        status = status_getter() if callable(status_getter) else {}
                        self._last_persistence_write_status = dict(status or {})
                        if status.get("accepted") is False:
                            superseded = True
                    else:
                        # Keep the compatibility path behind the overridable
                        # snapshot writer used by JSON/test harnesses.
                        self._last_persistence_write_status = {"accepted": None, "state": "writing"}
                        self._invoke_data_snapshot_writer_sync(
                            snapshot,
                            advance_generation=False,
                        )
                        if getattr(self, "_last_persistence_write_status", {}).get("accepted") is False:
                            superseded = True
                    if not superseded:
                        if advance_generation:
                            self._advance_data_save_write_generation()
                        confirmed = dict(expected_revisions)
        return {
            "confirmed": dict(confirmed or {}),
            "superseded": superseded,
            "prepared": prepared,
            "changed_revisions": changed_revisions,
            "control_changed": control_changed,
            "repeat_changed": repeat_changed,
            "compacted": compacted,
            "cleaned_sections": cleaned_sections,
        }

    def _write_persona_data_save_batch_sync(
        self,
        persona_id: str,
        batch: dict[str, Any],
        *,
        advance_generation: bool = False,
    ) -> dict[str, Any]:
        prepared, control_changed, repeat_changed, compacted, cleaned_sections = self._prepare_dirty_save_payloads_sync(
            batch["payloads"],
            batch["readonly_context"],
        )
        changed_revisions = dict(batch["changed_revisions"])
        if (
            "proactive_candidate_repeat_sanitized_at" in prepared
            and "proactive_candidate_repeat_sanitized_at" not in changed_revisions
            and "proactive_candidate_pool" in changed_revisions
        ):
            changed_revisions["proactive_candidate_repeat_sanitized_at"] = changed_revisions[
                "proactive_candidate_pool"
            ]
        snapshot = batch["full_snapshot"]
        for section, value in prepared.items():
            snapshot[section] = value
        for section in batch["deleted_revisions"]:
            snapshot.pop(section, None)
        saver = getattr(self, "_save_persona_profile_sync", None)
        if not callable(saver):
            raise RuntimeError("persona profile saver is unavailable")
        superseded = False
        confirmed: dict[str, int] = {}
        with self._data_save_io_lock(persona_id):
            if batch["write_generation"] != self._current_data_save_write_generation(
                persona_id
            ):
                superseded = True
            else:
                saver(persona_id, snapshot)
                if advance_generation:
                    self._advance_data_save_write_generation(persona_id)
                confirmed = dict(changed_revisions)
                confirmed.update(batch["deleted_revisions"])
                confirmed.update(batch["missing_revisions"])
        return {
            "confirmed": confirmed,
            "superseded": superseded,
            "prepared": prepared,
            "changed_revisions": changed_revisions,
            "control_changed": control_changed,
            "repeat_changed": repeat_changed,
            "compacted": compacted,
            "cleaned_sections": cleaned_sections,
        }

    @classmethod
    def _apply_cleaned_store_value_inplace(cls, target: Any, source: Any) -> bool:
        if isinstance(target, dict) and isinstance(source, dict):
            for key in tuple(target):
                if key not in source:
                    target.pop(key, None)
            for key, value in source.items():
                if key in target and cls._apply_cleaned_store_value_inplace(target[key], value):
                    continue
                target[key] = deepcopy(value)
            return True
        if isinstance(target, list) and isinstance(source, list):
            target[:] = deepcopy(source)
            return True
        return target == source

    def _apply_prepared_save_section(
        self,
        live_data: dict[str, Any],
        section: str,
        value: Any,
    ) -> None:
        if section in live_data and self._apply_cleaned_store_value_inplace(live_data[section], value):
            return
        live_data[section] = deepcopy(value)

    def _finish_data_save_batch(
        self,
        live_data: dict[str, Any],
        batch: dict[str, Any],
        result: dict[str, Any],
        dirty: dict[str, int],
        deleted: dict[str, int],
        dirty_since: dict[str, float],
        section_revisions: dict[str, int],
        *,
        persona_id: str = "",
    ) -> bool:
        confirmed = result["confirmed"]
        prepared = result["prepared"]
        expected = {
            **batch["changed_revisions"],
            **batch["deleted_revisions"],
            **batch["missing_revisions"],
        }
        derived = "proactive_candidate_repeat_sanitized_at"
        derived_revision = result["changed_revisions"].get(derived)
        if derived_revision is not None and derived not in batch["changed_revisions"]:
            expected[derived] = derived_revision
        dependency_stale = False
        if (
            "proactive_candidate_pool" in batch["changed_revisions"]
            and "proactive_candidate_pool" in result["cleaned_sections"]
        ):
            dependency_stale = any(
                int(section_revisions.get(section, 0) or 0) != int(revision or 0)
                for section, revision in batch["dependency_revisions"].items()
            )
            if dependency_stale and dirty.get("proactive_candidate_pool") == batch["changed_revisions"].get(
                "proactive_candidate_pool"
            ):
                revision = self._next_data_save_revision(persona_id)
                dirty["proactive_candidate_pool"] = revision
                section_revisions["proactive_candidate_pool"] = revision
        for section, revision in batch["changed_revisions"].items():
            if dirty.get(section) != revision or int(confirmed.get(section, -1)) < revision:
                continue
            if (
                section == "proactive_candidate_pool"
                and derived_revision is not None
                and int(confirmed.get(derived, -1)) < derived_revision
            ):
                continue
            if section == "proactive_candidate_pool" and dependency_stale:
                continue
            if section in prepared and section in result["cleaned_sections"]:
                self._apply_prepared_save_section(live_data, section, prepared[section])
            dirty.pop(section, None)
            dirty_since.pop(section, None)
        for section, revision in batch["deleted_revisions"].items():
            if deleted.get(section) == revision and int(confirmed.get(section, -1)) >= revision:
                deleted.pop(section, None)
                dirty_since.pop(section, None)
        for section, revision in batch["missing_revisions"].items():
            if (
                dirty.get(section) == revision
                and int(confirmed.get(section, -1)) >= revision
            ):
                dirty.pop(section, None)
                dirty_since.pop(section, None)
        if derived in prepared and derived not in batch["changed_revisions"]:
            source_revision = batch["changed_revisions"].get("proactive_candidate_pool")
            if (
                source_revision is not None
                and not dependency_stale
                and dirty.get("proactive_candidate_pool") in {None, source_revision}
                and int(confirmed.get(derived, -1)) >= source_revision
                and int(section_revisions.get(derived, 0) or 0) <= source_revision
            ):
                self._apply_prepared_save_section(live_data, derived, prepared[derived])
                section_revisions[derived] = source_revision
        complete = all(int(confirmed.get(section, -1)) >= revision for section, revision in expected.items())
        return complete and not dependency_stale

    def _finish_default_data_save_batch(self, batch: dict[str, Any], result: dict[str, Any]) -> None:
        if result.get("superseded"):
            return
        complete = self._finish_data_save_batch(
            self._default_data_for_save(),
            batch,
            result,
            self._data_save_dirty,
            self._data_save_deleted,
            self._data_save_dirty_since,
            self._data_save_section_revisions,
        )
        if complete and self._data_save_full_revision == batch["full_revision"]:
            self._data_save_full_revision = 0
            self._data_save_full_since = 0.0
            self._data_save_full_scope = ""
        if not batch["incremental"]:
            self._refresh_data_save_revision_from_manager()
            self._rebase_default_pending_revisions()
        if result["control_changed"]:
            self._log_store_control_cleanup("delayed_save", result["control_changed"])

    def _rebase_default_pending_revisions(self) -> None:
        """Move mutations made during a full write above its persisted revision."""
        self._ensure_default_save_state()
        floor = int(self._data_save_revision or 0)
        pending_revisions = sorted(
            {
                int(revision)
                for source in (self._data_save_dirty, self._data_save_deleted)
                for revision in source.values()
                if int(revision) <= floor
            }
            | (
                {int(self._data_save_full_revision)}
                if 0 < int(self._data_save_full_revision or 0) <= floor
                else set()
            )
        )
        for previous in pending_revisions:
            revision = self._next_data_save_revision()
            for source in (self._data_save_dirty, self._data_save_deleted):
                for section, current in tuple(source.items()):
                    if current == previous:
                        source[section] = revision
                        self._data_save_section_revisions[section] = revision
            if self._data_save_full_revision == previous:
                self._data_save_full_revision = revision

    def _finish_persona_data_save_batch(
        self,
        persona_id: str,
        batch: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if result.get("superseded"):
            return
        complete = self._finish_data_save_batch(
            self._persona_data_for_save(persona_id),
            batch,
            result,
            self._persona_data_save_dirty.setdefault(persona_id, {}),
            self._persona_data_save_deleted.setdefault(persona_id, {}),
            self._persona_data_save_dirty_since.setdefault(persona_id, {}),
            self._persona_data_save_section_revisions.setdefault(persona_id, {}),
            persona_id=persona_id,
        )
        if complete and self._persona_data_save_full_revision.get(persona_id, 0) == batch["full_revision"]:
            self._persona_data_save_full_revision.pop(persona_id, None)
            self._persona_data_save_full_scope.pop(persona_id, None)
        if result["control_changed"]:
            self._log_store_control_cleanup("delayed_persona_save", result["control_changed"])
        if not self._persona_data_save_dirty.get(persona_id):
            self._persona_data_save_dirty.pop(persona_id, None)
        if not self._persona_data_save_deleted.get(persona_id):
            self._persona_data_save_deleted.pop(persona_id, None)
        if not self._persona_data_save_dirty_since.get(persona_id):
            self._persona_data_save_dirty_since.pop(persona_id, None)

    def _bounded_data_save_delay(self, delay: float) -> float:
        maximum = max(0.01, float(getattr(self, "_data_save_max_delay_seconds", 2.0) or 2.0))
        return max(0.0, min(maximum, float(delay)))

    def _retry_data_save_delay(self, failures: int) -> float:
        base = max(0.0, float(getattr(self, "_data_save_retry_base_seconds", 1.0) or 1.0))
        maximum = max(base, float(getattr(self, "_data_save_retry_max_seconds", 30.0) or 30.0))
        return min(maximum, base * (2 ** max(0, failures - 1)))

    def _start_default_data_save_writer(self, delay: float) -> None:
        if self._save_is_stopping():
            return
        task = getattr(self, "_data_save_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            failures = 0
            try:
                await asyncio.sleep(self._bounded_data_save_delay(delay))
                while self._default_data_save_is_dirty() and not self._save_is_stopping():
                    batch = self._capture_default_data_save_batch()
                    try:
                        result = await asyncio.to_thread(self._write_default_data_save_batch_sync, batch)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures += 1
                        logger.warning(
                            "Delayed data save failed: %s",
                            _single_line(exc, 160),
                        )
                        if self._save_is_stopping():
                            break
                        await asyncio.sleep(self._retry_data_save_delay(failures))
                        continue
                    failures = 0
                    self._finish_default_data_save_batch(batch, result)
                    if self._default_data_save_is_dirty():
                        await asyncio.sleep(0)
            finally:
                current = asyncio.current_task()
                if getattr(self, "_data_save_task", None) is current:
                    self._data_save_task = None

        try:
            self._data_save_task = asyncio.create_task(_runner())
        except RuntimeError:
            snapshot = deepcopy(self._default_data_for_save())
            self._write_data_snapshot_sync(snapshot)
            self._clear_default_data_save_dirty()

    def _start_persona_data_save_writer(self, persona_id: str, delay: float) -> None:
        self._ensure_persona_save_state()
        if self._save_is_stopping():
            return
        task = self._persona_data_save_tasks.get(persona_id)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            failures = 0
            try:
                await asyncio.sleep(self._bounded_data_save_delay(delay))
                while self._persona_data_save_is_dirty(persona_id) and not self._save_is_stopping():
                    batch = self._capture_persona_data_save_batch(persona_id)
                    try:
                        result = await asyncio.to_thread(
                            self._write_persona_data_save_batch_sync,
                            persona_id,
                            batch,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures += 1
                        logger.warning(
                            "Delayed persona data save failed: "
                            "persona=%s error=%s",
                            persona_id,
                            _single_line(exc, 160),
                        )
                        if self._save_is_stopping():
                            break
                        await asyncio.sleep(self._retry_data_save_delay(failures))
                        continue
                    failures = 0
                    self._finish_persona_data_save_batch(persona_id, batch, result)
                    if self._persona_data_save_is_dirty(persona_id):
                        await asyncio.sleep(0)
            finally:
                current = asyncio.current_task()
                if self._persona_data_save_tasks.get(persona_id) is current:
                    self._persona_data_save_tasks.pop(persona_id, None)

        try:
            self._persona_data_save_tasks[persona_id] = asyncio.create_task(_runner())
        except RuntimeError:
            snapshot = deepcopy(self._persona_data_for_save(persona_id))
            self._write_persona_data_snapshot_sync(persona_id, snapshot)
            self._clear_persona_data_save_dirty(persona_id)

    def _schedule_persona_data_save(
        self,
        persona_id: str,
        delay: float = 1.5,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        if not self._mark_persona_data_dirty(
            persona_id,
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
        ):
            return
        self._start_persona_data_save_writer(persona_id, delay)

    def _schedule_default_data_save(
        self,
        delay: float = 1.5,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        if not self._mark_default_data_dirty(
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
        ):
            return
        self._start_default_data_save_writer(delay)

    def _schedule_data_save(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
        delay: float = 1.5,
    ) -> None:
        sections, deleted_sections, full_scope = self._validate_save_request(
            sections, deleted_sections, full_scope
        )
        if self._collect_event_data_save_request(
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
            delay=delay,
        ):
            return
        scoped_scheduler = getattr(self, "_req041_schedule_scoped_sync", None)
        if callable(scoped_scheduler):
            scoped_scheduler()
        if bool(getattr(self, "_group_observation_dirty", False)):
            delay = max(float(delay), 15.0)
            self._group_observation_dirty = False
        active_getter = getattr(self, "_active_persona_scope", None)
        persona_id = str(active_getter() if callable(active_getter) else "").strip()
        primary_getter = getattr(self, "_primary_persona_id", None)
        primary = str(primary_getter() if callable(primary_getter) else "").strip()
        if (
            bool(getattr(self, "enable_multi_persona_mode", False))
            and persona_id
            and persona_id != primary
        ):
            self._schedule_persona_data_save(
                persona_id,
                delay,
                sections=sections,
                deleted_sections=deleted_sections,
                full_scope=full_scope,
            )
            return
        self._schedule_default_data_save(
            delay,
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
        )

    def _clear_default_data_save_dirty(self, through_revision: int | None = None) -> None:
        self._ensure_default_save_state()
        for source in (self._data_save_dirty, self._data_save_deleted):
            for section, revision in tuple(source.items()):
                if through_revision is None or revision <= through_revision:
                    source.pop(section, None)
                    self._data_save_dirty_since.pop(section, None)
        if through_revision is None or self._data_save_full_revision <= through_revision:
            self._data_save_full_revision = 0
            self._data_save_full_since = 0.0
            self._data_save_full_scope = ""

    def _clear_persona_data_save_dirty(
        self,
        persona_id: str,
        through_revision: int | None = None,
    ) -> None:
        self._ensure_persona_save_state()
        for source in (self._persona_data_save_dirty, self._persona_data_save_deleted):
            values = source.get(persona_id, {})
            for section, revision in tuple(values.items()):
                if through_revision is None or revision <= through_revision:
                    values.pop(section, None)
                    self._persona_data_save_dirty_since.get(persona_id, {}).pop(section, None)
            if not values:
                source.pop(persona_id, None)
        full_revision = int(self._persona_data_save_full_revision.get(persona_id, 0) or 0)
        if through_revision is None or full_revision <= through_revision:
            self._persona_data_save_full_revision.pop(persona_id, None)
            self._persona_data_save_full_scope.pop(persona_id, None)
        if not self._persona_data_save_dirty_since.get(persona_id):
            self._persona_data_save_dirty_since.pop(persona_id, None)

    def _clear_scheduled_data_save_dirty(
        self,
        *,
        persona_id: str = "",
        through_revision: int | None = None,
    ) -> None:
        if persona_id:
            self._clear_persona_data_save_dirty(persona_id, through_revision)
        else:
            self._clear_default_data_save_dirty(through_revision)

    def _schedule_group_observation_save(self, delay: float = 15.0) -> None:
        """Coalesce high-frequency group observations into a bounded save window."""
        self._schedule_data_save(
            sections={"groups"},
            delay=max(5.0, float(delay)),
        )

    async def _flush_scheduled_data_save(self) -> None:
        """Wait until default and persona writers drain all revisions visible while flushing."""
        while True:
            pending: list[asyncio.Task] = []
            task = getattr(self, "_data_save_task", None)
            if isinstance(task, asyncio.Task) and not task.done():
                pending.append(task)
            persona_tasks = getattr(self, "_persona_data_save_tasks", {})
            if isinstance(persona_tasks, dict):
                pending.extend(
                    item
                    for item in persona_tasks.values()
                    if isinstance(item, asyncio.Task) and not item.done()
                )
            if pending:
                await asyncio.gather(*(asyncio.shield(item) for item in pending))
                continue
            if not self._default_data_save_is_dirty() and not self._dirty_persona_ids():
                return
            if self._save_is_stopping():
                return
            for persona_id in self._dirty_persona_ids():
                self._start_persona_data_save_writer(persona_id, 0.0)
            if self._default_data_save_is_dirty():
                self._start_default_data_save_writer(0.0)
