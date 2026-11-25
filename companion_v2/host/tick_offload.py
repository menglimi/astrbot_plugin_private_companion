"""Async offloading helpers for host maintenance tick units.

Each helper keeps synchronous SQLite scans and record preparation on a worker
thread via ``asyncio.to_thread`` while retaining asyncio task creation,
cancellation, and event-loop clock checks on the owning loop. Direct patches or
subclass overrides of a component's ``advance`` method are honored unchanged.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone as utc_timezone
import inspect
import json
from zoneinfo import ZoneInfo

from companion_v2.host.tick_budget import StagedOffload

from companion_v2.agenda.background import AgendaBackground
from companion_v2.agenda.models import AgendaError
from companion_v2.agenda.planning import CalendarPlanning, KIND as PLANNING_KIND
from companion_v2.agenda.planning_config import SCHEDULE_MODE_ORCHESTRATED, planning_values
from companion_v2.conversation.participation import GroupParticipation
from companion_v2.conversation.media.review_service import (
    CONCURRENCY as IMAGE_REVIEW_CONCURRENCY,
    GroupImageReviewService,
    KIND as IMAGE_REVIEW_KIND,
    TIMEOUT_SECONDS as IMAGE_REVIEW_TIMEOUT_SECONDS,
)
from companion_v2.delivery.presence import PresenceCoordinator
from companion_v2.device.action_service import PhoneActionService
from companion_v2.execution.models import (
    TERMINAL,
    TaskContext,
    TaskError,
    TaskSpec,
    digest,
    encode,
    require,
    stamp,
)
from companion_v2.execution.runner import CAPACITY_REFUSALS
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.host.background import BackgroundCoordinator
from companion_v2.memory.checkpoints import MemoryCheckpointRecovery
from companion_v2.memory.reflection_jobs import MemoryReflectionJobs
from companion_v2.memory.scheduling import MemorySchedule, next_due as memory_next_due
from companion_v2.references.daily_outfit import (
    DailyOutfitPhotos,
    KIND as DAILY_OUTFIT_KIND,
)
from companion_v2.role_work.growth.progress import (
    GrowthProgressService,
    KIND as GROWTH_PROGRESS_KIND,
)
from companion_v2.role_work.journal.scheduling import JournalScheduleDriver, next_daily
from companion_v2.role_work.journal.self_review_service import (
    KIND as SELF_REVIEW_KIND,
    SelfReviewService,
    failure_code as self_review_failure_code,
    retry_delay as self_review_retry_delay,
)
from companion_v2.role_work.life.calendar_work import (
    require_window as life_require_window,
    reserve_routine_slot as life_reserve_routine_slot,
    routine_for as life_routine_for,
)
from companion_v2.role_work.life.configuration import GROUP as LIFE_GROUP, target_for as life_target_for
from companion_v2.role_work.life.coordinator import RoleLifeCoordinator
from companion_v2.role_work.life.physiology import nap_ready
from companion_v2.role_work.life.runtime import RoleLifeRuntime
from companion_v2.role_work.models import RoleActivityState as LifeState
from companion_v2.role_work.reader import READ_KIND
from companion_v2.role_work.state.maintenance import KIND as STATE_KIND, StateMaintenance
from companion_v2.role_work.state.ownership import role_schedule_key
from companion_v2.screen.interpretation import (
    MAX_CONCURRENCY as SCREEN_INTERP_CONCURRENCY,
    ScreenInterpretationService,
)
from companion_v2.screen.peek import (
    MAX_CONCURRENCY as SCREEN_PEEK_CONCURRENCY,
    ScreenPeekService,
)


_DEFAULT_METHODS = {
    (AgendaBackground, "advance"): AgendaBackground.advance,
    (BackgroundCoordinator, "advance"): BackgroundCoordinator.advance,
    (CalendarPlanning, "advance"): CalendarPlanning.advance,
    (DailyOutfitPhotos, "advance"): DailyOutfitPhotos.advance,
    (DailyOutfitPhotos, "request"): DailyOutfitPhotos.request,
    (GroupImageReviewService, "advance"): GroupImageReviewService.advance,
    (GroupImageReviewService, "submit"): GroupImageReviewService.submit,
    (GroupParticipation, "advance_reflection"): GroupParticipation.advance_reflection,
    (GrowthProgressService, "advance"): GrowthProgressService.advance,
    (JournalScheduleDriver, "advance"): JournalScheduleDriver.advance,
    (MemoryCheckpointRecovery, "advance"): MemoryCheckpointRecovery.advance,
    (MemoryReflectionJobs, "advance"): MemoryReflectionJobs.advance,
    (MemorySchedule, "advance"): MemorySchedule.advance,
    (PhoneActionService, "advance"): PhoneActionService.advance,
    (PresenceCoordinator, "advance"): PresenceCoordinator.advance,
    (RoleLifeCoordinator, "advance"): RoleLifeCoordinator.advance,
    (RoleLifeRuntime, "advance"): RoleLifeRuntime.advance,
    (RoleLifeRuntime, "refresh"): RoleLifeRuntime.refresh,
    (RoleLifeRuntime, "advance_calendar_work"): RoleLifeRuntime.advance_calendar_work,
    (RoleLifeRuntime, "start_sleep"): RoleLifeRuntime.start_sleep,
    (RoleLifeRuntime, "start_reading"): RoleLifeRuntime.start_reading,
    (ScreenInterpretationService, "advance"): ScreenInterpretationService.advance,
    (ScreenPeekService, "advance"): ScreenPeekService.advance,
    (SelfReviewService, "advance"): SelfReviewService.advance,
    (StateMaintenance, "advance"): StateMaintenance.advance,
}


def uses_default_method(obj, cls, method_name: str = "advance") -> bool:
    """Return True when ``obj`` still uses ``cls``'s unpatched method implementation."""
    default = _DEFAULT_METHODS.get((cls, method_name))
    if default is None:
        default = getattr(cls, method_name)
    return (
        isinstance(obj, cls)
        and method_name not in getattr(obj, "__dict__", {})
        and getattr(type(obj), method_name, None) is default
    )


def _prepare_image_review_sync(planner: GroupImageReviewService):
    capacity = max(0, IMAGE_REVIEW_CONCURRENCY - len(planner._workers))
    if not capacity:
        return (), False
    active_workers = frozenset(planner._workers)
    rows = planner.review.pending_requests(limit=capacity)
    to_launch = []
    submitted = 0
    for row in rows:
        review_key = row["review_key"]
        try:
            current = planner.review.row(review_key)
            if current is None or current["state"] != "pending":
                raise TaskError("image_review_unavailable", "the original review request is unavailable")
            batch = planner.runtime.read_one(
                "SELECT * FROM conversation_batches WHERE batch_id = ?", (current["batch_id"],)
            )
            if batch is None:
                raise TaskError("image_review_unavailable", "the original review batch is unavailable")
            context = TaskContext.restore(json.loads(batch["context_json"]))
            if context.key != current["origin_key"] or context.scope.kind is not ScopeKind.GROUP:
                raise TaskError("image_review_context", "review must retain its original group context")
            if review_key in active_workers:
                submitted += 1
                continue
            planner.pending[review_key] = {
                "context": context,
                "role_instruction": batch["role_instruction"],
            }
            try:
                with planner.runtime.transaction():
                    require(
                        planner.tasks.submit(
                            TaskSpec(
                                review_key,
                                context,
                                IMAGE_REVIEW_KIND,
                                review_key,
                                review_key,
                                planner.tasks.now(),
                                planner.tasks.now() + timedelta(seconds=IMAGE_REVIEW_TIMEOUT_SECONDS),
                            )
                        )
                    )
            except BaseException:
                planner.pending.pop(review_key, None)
                raise
            to_launch.append((context, review_key))
            submitted += 1
        except TaskError as error:
            planner.results[review_key] = {"state": "blocked", "code": error.code}
            continue
    return to_launch, submitted == capacity


def _prepare_screen_planner_sync(planner, max_concurrency: int):
    capacity = max_concurrency - len(planner._workers)
    if capacity <= 0 or not planner.pending:
        return (), ()
    candidates = tuple(
        (task_id, job) for task_id, job in tuple(planner.pending.items()) if task_id not in planner._workers
    )
    if not candidates:
        return (), ()
    to_launch = []
    drop_ids = []
    remaining = capacity
    for task_id, job in candidates:
        if remaining <= 0:
            break
        task = planner.tasks.read(job["context"], task_id)
        if task.is_success and task.unwrap().state in (
            "succeeded", "failed", "cancelled", "expired", "uncertain", "dead_letter",
        ):
            continue
        if task.failure is not None:
            drop_ids.append(task_id)
            continue
        to_launch.append((job["context"], task_id))
        remaining -= 1
    return to_launch, drop_ids


async def _run_staged(staged: StagedOffload | None, fallback):
    if staged is None:
        return fallback()
    if staged.kind == "skip":
        return staged.value
    try:
        scanned = await asyncio.to_thread(staged.scan)
    except BaseException:
        if staged.cleanup is not None:
            staged.cleanup()
        raise
    result = staged.apply(scanned) if staged.apply is not None else scanned
    if inspect.isawaitable(result):
        return await result
    return result


def stage_background(bg: BackgroundCoordinator, *, planners: bool = True) -> StagedOffload | None:
    """Prepare background settlement, planner scans, and pending lookup for off-loop execution."""
    if not uses_default_method(bg, BackgroundCoordinator, "advance"):
        return None
    if bg._closed:
        return StagedOffload("skip", value=False, barrier_before=planners)
    if planners:
        for planner in bg.planners:
            if not (
                uses_default_method(planner, AgendaBackground, "advance")
                or (
                    uses_default_method(planner, GroupImageReviewService, "advance")
                    and uses_default_method(planner, GroupImageReviewService, "submit")
                )
                or uses_default_method(planner, ScreenInterpretationService, "advance")
                or uses_default_method(planner, ScreenPeekService, "advance")
            ):
                return None
    failed_cancels = []
    for task_id, worker in tuple(bg._workers.items()):
        if worker.done():
            del bg._workers[task_id]
            if not worker.cancelled():
                result = worker.result()
                if result.failure:
                    failed_cancels.append((task_id, result.failure.code))

    offload_planners = []
    if planners:
        for planner in bg.planners:
            if uses_default_method(planner, AgendaBackground, "advance"):
                offload_planners.append(("agenda", planner))
            elif (
                uses_default_method(planner, GroupImageReviewService, "advance")
                and uses_default_method(planner, GroupImageReviewService, "submit")
            ):
                if not (planner.closed or planner.app is None or planner.review is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                            planner.pending.pop(task_id, None)
                    offload_planners.append(("image_review", planner))
            elif uses_default_method(planner, ScreenInterpretationService, "advance"):
                if not (planner._closed or planner.app is None or planner.runner is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                    offload_planners.append(("screen_interp", planner))
            elif uses_default_method(planner, ScreenPeekService, "advance"):
                if not (planner._closed or planner.app is None or planner.runner is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                    offload_planners.append(("screen_peek", planner))

    available = bg.tasks.limits.background_slots - len(bg._workers)
    excluded = tuple(bg._workers)
    fetch_pending_in_batch = available > 0

    def batched_scan():
        require(bg.tasks.check_generation())
        for task_id, reason in failed_cancels:
            bg.delivery.cancel_unstarted(task_id, reason=reason)
        more = bg.delivery.settle() == 32
        image_launches = []
        screen_launches = []
        for kind, planner in offload_planners:
            if kind == "agenda":
                more = require(planner.advance()) or more
            elif kind == "image_review":
                to_launch, review_more = _prepare_image_review_sync(planner)
                if to_launch:
                    image_launches.append((planner, to_launch))
                more = review_more or more
            elif kind == "screen_interp":
                to_launch, drop_ids = _prepare_screen_planner_sync(planner, SCREEN_INTERP_CONCURRENCY)
                if to_launch or drop_ids:
                    screen_launches.append((planner, "companion:screen-interpretation:", to_launch, drop_ids))
                more = bool(to_launch) or more
            elif kind == "screen_peek":
                to_launch, drop_ids = _prepare_screen_planner_sync(planner, SCREEN_PEEK_CONCURRENCY)
                if to_launch or drop_ids:
                    screen_launches.append((planner, "companion:screen-peek:", to_launch, drop_ids))
                more = bool(to_launch) or more
        pending = (
            tuple(bg.delivery.pending(limit=available, excluded=excluded))
            if fetch_pending_in_batch
            else None
        )
        return more, image_launches, screen_launches, pending

    def apply_bg(scanned):
        more, image_launches, screen_launches, pending = scanned
        for planner, to_launch in image_launches:
            if planner.closed:
                continue
            for context, review_key in to_launch:
                if review_key in planner._workers:
                    continue
                worker = asyncio.create_task(
                    planner._run(context, review_key), name="companion:image-review:" + review_key
                )
                planner._workers[review_key] = worker
                worker.add_done_callback(lambda done, p=planner: p.notify())
        for planner, name_prefix, to_launch, drop_ids in screen_launches:
            for task_id in drop_ids:
                planner.pending.pop(task_id, None)
            if planner._closed:
                continue
            for context, task_id in to_launch:
                if task_id in planner._workers:
                    continue
                worker = asyncio.create_task(planner._run(context, task_id), name=name_prefix + task_id)
                planner._workers[task_id] = worker
                worker.add_done_callback(lambda done, p=planner: p.notify())
        if pending and not bg._closed:
            for context, task_id in pending:
                if task_id in bg._workers:
                    continue
                worker = asyncio.create_task(
                    bg.runner.run(context, task_id), name="companion:background:" + task_id
                )
                bg._workers[task_id] = worker
                worker.add_done_callback(bg._done)
        return more

    return StagedOffload("offload", scan=batched_scan, apply=apply_bg, barrier_before=planners)


async def advance_background_async(bg: BackgroundCoordinator, *, planners: bool = True) -> bool:
    """Offload background coordinator SQLite settlement, planner scans, and pending lookup."""
    if not uses_default_method(bg, BackgroundCoordinator, "advance"):
        return bg.advance(planners=False) if not planners else bg.advance()
    staged = stage_background(bg, planners=planners)
    if staged is not None:
        return await _run_staged(staged, lambda: False)
    if bg._closed:
        return False
    failed_cancels = []
    for task_id, worker in tuple(bg._workers.items()):
        if worker.done():
            del bg._workers[task_id]
            if not worker.cancelled():
                result = worker.result()
                if result.failure:
                    failed_cancels.append((task_id, result.failure.code))

    # Reap completed planner workers on the event loop before the batched SQLite scan.
    custom_planners = []
    offload_planners = []
    if planners:
        for planner in bg.planners:
            if uses_default_method(planner, AgendaBackground, "advance"):
                offload_planners.append(("agenda", planner))
            elif (
                uses_default_method(planner, GroupImageReviewService, "advance")
                and uses_default_method(planner, GroupImageReviewService, "submit")
            ):
                if not (planner.closed or planner.app is None or planner.review is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                            planner.pending.pop(task_id, None)
                    offload_planners.append(("image_review", planner))
            elif uses_default_method(planner, ScreenInterpretationService, "advance"):
                if not (planner._closed or planner.app is None or planner.runner is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                    offload_planners.append(("screen_interp", planner))
            elif uses_default_method(planner, ScreenPeekService, "advance"):
                if not (planner._closed or planner.app is None or planner.runner is None):
                    for task_id, worker in tuple(planner._workers.items()):
                        if worker.done():
                            if not worker.cancelled():
                                worker.exception()
                            del planner._workers[task_id]
                    offload_planners.append(("screen_peek", planner))
            else:
                custom_planners.append(planner)

    available = bg.tasks.limits.background_slots - len(bg._workers)
    excluded = tuple(bg._workers)
    fetch_pending_in_batch = available > 0 and not custom_planners

    def batched_scan():
        require(bg.tasks.check_generation())
        for task_id, reason in failed_cancels:
            bg.delivery.cancel_unstarted(task_id, reason=reason)
        more = bg.delivery.settle() == 32
        image_launches = []
        screen_launches = []
        for kind, planner in offload_planners:
            if kind == "agenda":
                more = require(planner.advance()) or more
            elif kind == "image_review":
                to_launch, review_more = _prepare_image_review_sync(planner)
                if to_launch:
                    image_launches.append((planner, to_launch))
                more = review_more or more
            elif kind == "screen_interp":
                to_launch, drop_ids = _prepare_screen_planner_sync(planner, SCREEN_INTERP_CONCURRENCY)
                if to_launch or drop_ids:
                    screen_launches.append((planner, "companion:screen-interpretation:", to_launch, drop_ids))
                more = bool(to_launch) or more
            elif kind == "screen_peek":
                to_launch, drop_ids = _prepare_screen_planner_sync(planner, SCREEN_PEEK_CONCURRENCY)
                if to_launch or drop_ids:
                    screen_launches.append((planner, "companion:screen-peek:", to_launch, drop_ids))
                more = bool(to_launch) or more
        pending = (
            tuple(bg.delivery.pending(limit=available, excluded=excluded))
            if fetch_pending_in_batch
            else None
        )
        return more, image_launches, screen_launches, pending

    more, image_launches, screen_launches, pending = await asyncio.to_thread(batched_scan)
    for planner, to_launch in image_launches:
        if planner.closed:
            continue
        for context, review_key in to_launch:
            if review_key in planner._workers:
                continue
            worker = asyncio.create_task(
                planner._run(context, review_key), name="companion:image-review:" + review_key
            )
            planner._workers[review_key] = worker
            worker.add_done_callback(lambda done, p=planner: p.notify())
    for planner, name_prefix, to_launch, drop_ids in screen_launches:
        for task_id in drop_ids:
            planner.pending.pop(task_id, None)
        if planner._closed:
            continue
        for context, task_id in to_launch:
            if task_id in planner._workers:
                continue
            worker = asyncio.create_task(planner._run(context, task_id), name=name_prefix + task_id)
            planner._workers[task_id] = worker
            worker.add_done_callback(lambda done, p=planner: p.notify())
    if custom_planners:
        for planner in custom_planners:
            more = require(planner.advance()) or more
        available = bg.tasks.limits.background_slots - len(bg._workers)
        if available > 0:
            excluded = tuple(bg._workers)
            pending = await asyncio.to_thread(
                lambda: tuple(bg.delivery.pending(limit=available, excluded=excluded))
            )
    if pending and not bg._closed:
        for context, task_id in pending:
            if task_id in bg._workers:
                continue
            worker = asyncio.create_task(
                bg.runner.run(context, task_id), name="companion:background:" + task_id
            )
            bg._workers[task_id] = worker
            worker.add_done_callback(bg._done)
    return more


def stage_memory_schedule(ms: MemorySchedule) -> StagedOffload | None:
    """Prepare memory checkpoint recovery, continuation scan, and due daily schedules off-loop."""
    if not uses_default_method(ms, MemorySchedule, "advance"):
        return None
    if ms.closed:
        return StagedOffload("skip")
    recovery_running = ms.recovery.state == "running"
    if recovery_running and not uses_default_method(ms.recovery, MemoryCheckpointRecovery, "advance"):
        return None
    cont = ms.continuations
    if not uses_default_method(cont, MemoryReflectionJobs, "advance"):
        return None
    if not recovery_running:
        ms._schedule_configuration_refresh()
    for key, worker in tuple(ms.workers.items()):
        if worker.done():
            del ms.workers[key]
            if not worker.cancelled():
                error = worker.exception()
                ms.results[key] = (
                    {"state": "failed", "code": "memory_schedule_failed"} if error else worker.result()
                )
    if not cont.closed:
        for job_id, (_, worker) in tuple(cont.workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                del cont.workers[job_id]
    capacity = ms.reflection.tasks.limits.background_slots - len(ms.workers)
    cont_active_batches = {batch_id for batch_id, _ in cont.workers.values()}
    cont_workers_count = len(cont.workers)
    active_schedule_keys = frozenset(ms.workers)

    def scan_schedules():
        ran_recovery = False
        if recovery_running:
            ms.recovery.advance()
            ran_recovery = True
            if ms.recovery.state == "running":
                return True, (), ()
        cont_launch = []
        if not cont.closed:
            cont._active()
            cont_cap = max(0, capacity - cont_workers_count)
            if cont_cap > 0:
                batches_tuple = tuple(cont_active_batches)
                excluded_sql = (
                    " AND batch_id NOT IN (" + ",".join("?" for _ in batches_tuple) + ")"
                    if batches_tuple
                    else ""
                )
                rows = cont.runtime.read_all(
                    "SELECT * FROM memory_reflection_jobs WHERE state = 'queued' AND next_at <= ?"
                    + excluded_sql
                    + " ORDER BY next_at, job_id LIMIT ?",
                    (stamp(cont.reflection.tasks.now()), *batches_tuple, cont_cap),
                )
                seen_batches = set(cont_active_batches)
                for row in rows:
                    if row["batch_id"] in seen_batches:
                        continue
                    seen_batches.add(row["batch_id"])
                    cont_launch.append(dict(row))

        daily_cap = max(0, capacity - cont_workers_count - len(cont_launch))
        daily_launch = []
        if daily_cap > 0:
            now = ms.reflection.tasks.now()
            rows = ms.runtime.read_all(
                "SELECT * FROM memory_schedules WHERE enabled = 1 AND next_at <= ? "
                "ORDER BY next_at, schedule_id LIMIT ?",
                (stamp(now), daily_cap),
            )
            for row in rows:
                key = row["schedule_id"]
                if key in active_schedule_keys:
                    continue
                settings = json.loads(row["settings_json"])
                with ms.runtime.transaction() as connection:
                    changed = connection.execute(
                        "UPDATE memory_schedules SET next_at = ? "
                        "WHERE schedule_id = ? AND next_at = ? AND enabled = 1",
                        (stamp(memory_next_due(now, settings["zone"], settings["local_time"])), key, row["next_at"]),
                    )
                    if not changed.rowcount:
                        continue
                daily_launch.append((key, dict(row), now))
        return False, cont_launch, daily_launch if not ran_recovery else daily_launch

    def apply_ms(scanned):
        still_recovering, cont_launch, daily_launch = scanned
        if ms.closed or still_recovering:
            return
        if recovery_running:
            ms._schedule_configuration_refresh()
        if not cont.closed:
            live_batches = {batch_id for batch_id, _ in cont.workers.values()}
            for row_dict in cont_launch:
                job_id = row_dict["job_id"]
                batch_id = row_dict["batch_id"]
                if job_id in cont.workers or batch_id in live_batches:
                    continue
                live_batches.add(batch_id)
                worker = asyncio.create_task(
                    cont._run(row_dict), name="companion:memory-continuation:" + job_id
                )
                cont.workers[job_id] = batch_id, worker
                worker.add_done_callback(lambda done: cont.notify())
        for key, row_dict, now in daily_launch:
            if key in ms.workers:
                continue
            worker = asyncio.create_task(ms._run(row_dict, now), name="companion:memory-daily:" + key)
            ms.workers[key] = worker
            worker.add_done_callback(lambda done: ms.notify())

    return StagedOffload("offload", scan=scan_schedules, apply=apply_ms)


async def advance_memory_schedule_async(ms: MemorySchedule) -> None:
    """Offload checkpoint recovery, continuation scan, and due daily schedules off-loop."""
    if not uses_default_method(ms, MemorySchedule, "advance"):
        ms.advance()
        return
    staged = stage_memory_schedule(ms)
    if staged is not None:
        await _run_staged(staged, lambda: None)
        return
    if ms.closed:
        return
    if ms.recovery.state == "running":
        if uses_default_method(ms.recovery, MemoryCheckpointRecovery, "advance"):
            await asyncio.to_thread(ms.recovery.advance)
        else:
            ms.recovery.advance()
        if ms.recovery.state == "running":
            return
    ms._schedule_configuration_refresh()
    for key, worker in tuple(ms.workers.items()):
        if worker.done():
            del ms.workers[key]
            if not worker.cancelled():
                error = worker.exception()
                ms.results[key] = (
                    {"state": "failed", "code": "memory_schedule_failed"} if error else worker.result()
                )
    capacity = ms.reflection.tasks.limits.background_slots - len(ms.workers)
    cont = ms.continuations
    offload_continuations = uses_default_method(cont, MemoryReflectionJobs, "advance")
    if offload_continuations:
        if not cont.closed:
            for job_id, (_, worker) in tuple(cont.workers.items()):
                if worker.done():
                    if not worker.cancelled():
                        worker.exception()
                    del cont.workers[job_id]
    else:
        cont.advance(capacity)

    cont_active_batches = {batch_id for batch_id, _ in cont.workers.values()}
    cont_workers_count = len(cont.workers)
    active_schedule_keys = frozenset(ms.workers)

    def scan_schedules():
        cont_launch = []
        if offload_continuations and not cont.closed:
            cont._active()
            cont_cap = max(0, capacity - cont_workers_count)
            if cont_cap > 0:
                batches_tuple = tuple(cont_active_batches)
                excluded_sql = (
                    " AND batch_id NOT IN (" + ",".join("?" for _ in batches_tuple) + ")"
                    if batches_tuple
                    else ""
                )
                rows = cont.runtime.read_all(
                    "SELECT * FROM memory_reflection_jobs WHERE state = 'queued' AND next_at <= ?"
                    + excluded_sql
                    + " ORDER BY next_at, job_id LIMIT ?",
                    (stamp(cont.reflection.tasks.now()), *batches_tuple, cont_cap),
                )
                seen_batches = set(cont_active_batches)
                for row in rows:
                    if row["batch_id"] in seen_batches:
                        continue
                    seen_batches.add(row["batch_id"])
                    cont_launch.append(dict(row))

        daily_cap = max(0, capacity - cont_workers_count - len(cont_launch))
        daily_launch = []
        if daily_cap > 0:
            now = ms.reflection.tasks.now()
            rows = ms.runtime.read_all(
                "SELECT * FROM memory_schedules WHERE enabled = 1 AND next_at <= ? "
                "ORDER BY next_at, schedule_id LIMIT ?",
                (stamp(now), daily_cap),
            )
            for row in rows:
                key = row["schedule_id"]
                if key in active_schedule_keys:
                    continue
                settings = json.loads(row["settings_json"])
                with ms.runtime.transaction() as connection:
                    changed = connection.execute(
                        "UPDATE memory_schedules SET next_at = ? "
                        "WHERE schedule_id = ? AND next_at = ? AND enabled = 1",
                        (stamp(memory_next_due(now, settings["zone"], settings["local_time"])), key, row["next_at"]),
                    )
                    if not changed.rowcount:
                        continue
                daily_launch.append((key, dict(row), now))
        return cont_launch, daily_launch

    cont_launch, daily_launch = await asyncio.to_thread(scan_schedules)
    if ms.closed:
        return
    if not cont.closed:
        live_batches = {batch_id for batch_id, _ in cont.workers.values()}
        for row_dict in cont_launch:
            job_id = row_dict["job_id"]
            batch_id = row_dict["batch_id"]
            if job_id in cont.workers or batch_id in live_batches:
                continue
            live_batches.add(batch_id)
            worker = asyncio.create_task(
                cont._run(row_dict), name="companion:memory-continuation:" + job_id
            )
            cont.workers[job_id] = batch_id, worker
            worker.add_done_callback(lambda done: cont.notify())
    for key, row_dict, now in daily_launch:
        if key in ms.workers:
            continue
        worker = asyncio.create_task(ms._run(row_dict, now), name="companion:memory-daily:" + key)
        ms.workers[key] = worker
        worker.add_done_callback(lambda done: ms.notify())


def _routine_uses_defaults(routine: RoleLifeRuntime) -> bool:
    return all(
        uses_default_method(routine, RoleLifeRuntime, name)
        for name in ("advance", "refresh", "advance_calendar_work", "start_sleep", "start_reading")
    )


def _scan_life_routine_sync(
    routine: RoleLifeRuntime,
    active_workers: frozenset[str],
    writing_active: bool,
    *,
    check_config: bool,
):
    to_launch = []
    try:
        if check_config:
            snapshot = require(
                routine.app.configuration.read(
                    routine.context.actor,
                    target=life_target_for(routine.context),
                    group_id=LIFE_GROUP,
                    observed_at=routine.app.now(),
                )
            )
            if snapshot.resolution_token != routine._revision:
                return "needs_refresh", (), False
        if not routine.settings or not routine.settings["enabled"]:
            return "ok", (), False
        routine.authorize()
        now = routine.app.now()
        routine.app.role_state.body_clock.resume_due(routine.context, observed_at=now)
        for sleeping in routine.app.role_state.body_clock.active(routine.context, "sleep"):
            wake_key = routine.wake_key(sleeping)
            if (
                sleeping.work.due_at <= now
                and wake_key not in active_workers
                and wake_key not in routine.results
            ):
                to_launch.append((wake_key, routine._wake_shared_due, 30))
        records = routine.activities()
        for activity in records:
            if (
                activity.work.kind == "reading"
                and activity.state is LifeState.ACTIVE
                and activity.activity_id not in active_workers
            ):
                routine.app.role_life.transition_activity(
                    activity.activity_id,
                    owner=routine.owner,
                    expected_revision=activity.revision,
                    state=LifeState.PAUSED,
                    operation_id="recover-reading-" + digest([activity.activity_id, activity.revision]),
                    observed_at=now,
                    checkpoint="interrupted; inspect the original model result before retrying",
                )
            elif (
                activity.work.kind == "sleep"
                and activity.state is LifeState.COMPLETED
                and activity.activity_id not in routine._dreams
            ):
                routine._dreams.add(activity.activity_id)
                to_launch.append((activity.activity_id, lambda a=activity: routine.dream(a), None))
        pending_sleep = routine.app.role_state.body_clock.pending_sleep(routine.context)
        busy = (
            bool(active_workers)
            or bool(to_launch)
            or any(a.state is LifeState.ACTIVE for a in records)
            or pending_sleep
        )
        if not busy and routine._start_due_night_sleep(now):
            return "ok", to_launch, False
        local_now = now.astimezone(ZoneInfo(routine.settings["sleep_timezone"]))
        profile = routine.app.role_state.body_clock.profile(routine.context, now=now)
        if routine.settings["sleep_enabled"] and not busy and nap_ready(
            profile, local_minutes=local_now.hour * 60 + local_now.minute
        ):
            today = local_now.date().isoformat()
            if not any(
                item.sleep
                and item.sleep.kind == "nap"
                and item.started_at.astimezone(ZoneInfo(routine.settings["sleep_timezone"])).date().isoformat()
                == today
                for item in routine.app.role_state.body_clock.episodes(routine.context)
            ):
                routine.start_sleep("daily-nap-" + today, 25, label="角色短暂午休", sleep_kind="nap")
                return "ok", to_launch, False
        if not busy:
            _scan_calendar_work(routine, records, now, writing_active, to_launch)
        return "ok", to_launch, False
    except (TaskError, ValueError, KeyError) as error:
        routine.failure_code = error.code if isinstance(error, TaskError) else "life_configuration_invalid"
        return "cancel", (), True


def _reap_and_snapshot_life_routine(routine: RoleLifeRuntime):
    if routine.context is None or routine._closed:
        return None
    for key, worker in tuple(routine._workers.items()):
        if worker.done():
            if not worker.cancelled():
                routine.results[key] = worker.result()
            del routine._workers[key]
    if routine.instruction is None or routine._retired:
        return None
    active_workers = frozenset(routine._workers)
    writing_active = any(
        key.startswith("writing:")
        for other in routine.app.life_runtime.routines.values()
        if other.owner == routine.owner
        for key in other._workers
    )
    return (routine, active_workers, writing_active)


async def _advance_life_routine_batch_async(batch: list[tuple[RoleLifeRuntime, frozenset[str], bool]]) -> None:
    if not batch:
        return

    def scan_batch(items, *, check_config: bool):
        return [
            (routine, *_scan_life_routine_sync(routine, active_workers, writing_active, check_config=check_config))
            for routine, active_workers, writing_active in items
        ]

    outcomes = await asyncio.to_thread(scan_batch, batch, check_config=True)
    refreshed_items = []
    final_actions = []
    for routine, status, to_launch, cancel_needed in outcomes:
        if status == "needs_refresh":
            try:
                routine.refresh()
            except (TaskError, ValueError, KeyError) as error:
                routine.failure_code = error.code if isinstance(error, TaskError) else "life_configuration_invalid"
                routine.cancel_running()
                continue
            if routine._closed or routine._retired or not routine.settings or not routine.settings["enabled"]:
                continue
            snap = _reap_and_snapshot_life_routine(routine)
            if snap is not None:
                refreshed_items.append(snap)
        else:
            final_actions.append((routine, to_launch, cancel_needed))

    if refreshed_items:
        refreshed_outcomes = await asyncio.to_thread(scan_batch, refreshed_items, check_config=False)
        for routine, _status, to_launch, cancel_needed in refreshed_outcomes:
            final_actions.append((routine, to_launch, cancel_needed))

    for routine, to_launch, cancel_needed in final_actions:
        if cancel_needed:
            routine.cancel_running()
        elif not routine._closed:
            for key, factory, timeout_seconds in to_launch:
                routine.launch(key, factory, timeout_seconds=timeout_seconds)


def _scan_calendar_work(routine: RoleLifeRuntime, records, now, writing_active: bool, to_launch: list) -> None:
    if routine._next_calendar_work is not None and now < routine._next_calendar_work:
        return
    routine._next_calendar_work = now + timedelta(seconds=20)
    context, calendar = routine.context, routine.app.calendar
    if routine.app.state_maintenance.foreground_busy(context):
        return
    settings, _ = routine.app.role_state.settings(context)
    if not settings["calendar_enabled"]:
        routine.next_reading_at = None
        return
    pending = {}
    if routine.settings["reading_enabled"] and READ_KIND in routine.app.gateway.router.profiles:
        for activity in sorted(records, key=lambda a: (a.started_at, a.activity_id)):
            if activity.work.kind != "reading" or activity.state not in (LifeState.PLANNED, LifeState.PAUSED):
                continue
            source = activity.work.source
            if source not in pending or activity.work.start_offset < pending[source].work.start_offset:
                pending[source] = activity
    writer = routine.app.scheduled_authoring
    if not pending:
        routine.next_reading_at = None
        if writer is None:
            return
    page = calendar.occurrences(
        context, start=now, end=now + timedelta(days=7, hours=1),
        kinds=("role_plan", "commitment"), diagnostics=True,
    )
    routine.next_reading_at = next(
        (
            item["start"]
            for item in page["items"]
            if pending and item["start"] > stamp(now) and life_routine_for(item) == "reading"
        ),
        None,
    )
    current = [item for item in page["items"] if item["state"] == "accepted" and item["start"] <= stamp(now)]
    if page["scan_truncated"] or any(item.get("kind") == "commitment" for item in current):
        current = []
    else:
        current = [item for item in current if life_routine_for(item) is not None]
    if writing_active or routine.app.role_state.body_clock.active(context, "reading"):
        return
    if writer is not None:
        for item in current:
            if life_routine_for(item) != "writing":
                continue
            key = "writing:" + item["id"] + ":" + str(item["sequence"])
            prior = routine.results.get(key)
            retryable_prior = (
                prior is not None
                and getattr(prior, "failure", None) is not None
                and getattr(writer, "is_retryable_failure", lambda _: False)(prior.failure.code)
            )
            if (
                (prior is None or retryable_prior)
                and datetime.fromisoformat(item["end"]) - now >= timedelta(minutes=1)
                and writer.ready(context, item)
            ):
                routine.results.pop(key, None)
                to_launch.append((key, lambda w=writer, r=routine, it=item: w.run(r, it), None))
                return
    if (
        writer is not None
        and (routine._next_writing_plan_check is None or now >= routine._next_writing_plan_check)
        and not any(life_routine_for(item) == "writing" and item["start"] > stamp(now) for item in page["items"])
        and not any(life_routine_for(item) == "writing" for item in current)
    ):
        routine._next_writing_plan_check = now + timedelta(minutes=10)
        planned = writer.plan_continuation(
            context, now, settings, page, sleep_plan=(routine._next_sleep, routine._next_wake)
        )
        if planned and pending:
            page = calendar.occurrences(
                context, start=now, end=now + timedelta(days=7, hours=1),
                kinds=("role_plan", "commitment"), diagnostics=True,
            )
    if not pending:
        return
    ready = [
        a for a in pending.values()
        if a.state is LifeState.PLANNED or not routine.reading_unsettled(a, scheduled=True)
    ]
    if not ready:
        return
    for item in current:
        if life_routine_for(item) != "reading":
            continue
        start = datetime.fromisoformat(item["start"])
        queued = next(
            (
                a for a in ready
                if a.started_at <= start and (a.state is LifeState.PLANNED or a.updated_at < start)
            ),
            None,
        )
        if (
            queued is None
            or datetime.fromisoformat(item["end"]) - now < timedelta(seconds=30)
            or routine.app.gateway.ledger.background_deferral(context, READ_KIND, at=now)
        ):
            continue
        try:
            saved = _prepare_scheduled_reading(
                routine,
                queued.activity_id,
                expected_revision=queued.revision,
                request_id="scheduled-read-" + digest([queued.activity_id, queued.revision]),
                window=item,
            )
            to_launch.append(
                (queued.activity_id, lambda r=routine, s=saved, w=item: r._read(s, window=w), None)
            )
        except TaskError as error:
            if error.code not in (
                "source_revoked", "source_expired", "source_audience_denied", "source_audience_mismatch",
            ):
                raise
            routine.app.role_life.transition_activity(
                queued.activity_id,
                owner=routine.owner,
                expected_revision=queued.revision,
                state=LifeState.CANCELLED,
                operation_id="unavailable-reading-" + digest([queued.activity_id, queued.revision]),
                observed_at=now,
                checkpoint=error.code,
            )
        return
    if routine.next_reading_at is None and not page["scan_truncated"] and not page["occurrences_truncated"]:
        routine.next_reading_at = life_reserve_routine_slot(
            calendar, context, routine="reading", timezone=settings["planning_timezone"],
            now=now, items=page["items"], sleep_plan=(routine._next_sleep, routine._next_wake),
        )


def _prepare_scheduled_reading(routine: RoleLifeRuntime, identifier, *, expected_revision, request_id, window):
    routine.authorize()
    if window is not None:
        life_require_window(routine.app.calendar, routine.context, window, routine.app.now())
    activity = routine.activity(identifier)
    if activity.work.kind != "reading":
        raise TaskError("life_reading_invalid", "select a reading activity")
    if activity.state is LifeState.PAUSED and routine.reading_unsettled(activity, scheduled=window is not None):
        raise TaskError("life_reading_unresolved", "the original reading must settle before another attempt")
    if routine.app.role_state.body_clock.active(routine.context, "sleep"):
        raise TaskError("life_sleeping", "wake the role before reading")
    if routine.app.role_state.body_clock.active(routine.context, "reading"):
        raise TaskError("life_reading_active", "another reading is already running")
    record = require(
        routine.app.role_journal.sources.read(
            activity.work.source,
            resolved=routine.context.actor,
            capability="role.reading.record",
            observed_at=routine.app.now(),
            authorizer=routine.app.tasks.identity,
        )
    )
    if record.content_hash != activity.work.content_hash or digest(record.payload) != activity.work.content_hash:
        raise TaskError("reading_source_changed", "reading must retain its original text")
    work = replace(activity.work, request_id=request_id)
    return routine.app.role_life.transition_activity(
        identifier,
        owner=routine.owner,
        expected_revision=expected_revision,
        state=LifeState.ACTIVE,
        operation_id="read-start-" + digest([routine.context.key, request_id]),
        observed_at=routine.app.now(),
        work=work,
        checkpoint="正在阅读实际提供的片段",
    )


def stage_life_runtime(life_runtime: RoleLifeCoordinator) -> StagedOffload | None:
    """Prepare batched role-life SQLite scans for off-loop execution."""
    if not uses_default_method(life_runtime, RoleLifeCoordinator, "advance"):
        return None
    if life_runtime._closed:
        return StagedOffload("skip")
    routines = tuple(life_runtime.routines.values())
    if any(not _routine_uses_defaults(routine) for routine in routines):
        return None
    batch: list[tuple[RoleLifeRuntime, frozenset[str], bool]] = []
    for routine in routines:
        snap = _reap_and_snapshot_life_routine(routine)
        if snap is not None:
            batch.append(snap)
    if not batch:
        return StagedOffload("skip")

    def scan_batch():
        return [
            (routine, *_scan_life_routine_sync(routine, active_workers, writing_active, check_config=True))
            for routine, active_workers, writing_active in batch
        ]

    async def apply_batch(outcomes):
        refreshed_items = []
        final_actions = []
        for routine, status, to_launch, cancel_needed in outcomes:
            if status == "needs_refresh":
                try:
                    routine.refresh()
                except (TaskError, ValueError, KeyError) as error:
                    routine.failure_code = error.code if isinstance(error, TaskError) else "life_configuration_invalid"
                    routine.cancel_running()
                    continue
                if routine._closed or routine._retired or not routine.settings or not routine.settings["enabled"]:
                    continue
                snap = _reap_and_snapshot_life_routine(routine)
                if snap is not None:
                    refreshed_items.append(snap)
            else:
                final_actions.append((routine, to_launch, cancel_needed))
        if refreshed_items:
            refreshed_outcomes = await asyncio.to_thread(
                lambda: [
                    (routine, *_scan_life_routine_sync(routine, aw, wa, check_config=False))
                    for routine, aw, wa in refreshed_items
                ]
            )
            for routine, _status, to_launch, cancel_needed in refreshed_outcomes:
                final_actions.append((routine, to_launch, cancel_needed))
        for routine, to_launch, cancel_needed in final_actions:
            if cancel_needed:
                routine.cancel_running()
            elif not routine._closed:
                for key, factory, timeout_seconds in to_launch:
                    routine.launch(key, factory, timeout_seconds=timeout_seconds)

    return StagedOffload("offload", scan=scan_batch, apply=apply_batch)


async def advance_life_runtime_async(life_runtime: RoleLifeCoordinator) -> None:
    """Offload role life SQLite polling in batched worker calls while keeping task launch/cancel on-loop."""
    if not uses_default_method(life_runtime, RoleLifeCoordinator, "advance"):
        life_runtime.advance()
        return
    staged = stage_life_runtime(life_runtime)
    if staged is not None:
        await _run_staged(staged, lambda: None)
        return
    if life_runtime._closed:
        return
    batch: list[tuple[RoleLifeRuntime, frozenset[str], bool]] = []
    for routine in tuple(life_runtime.routines.values()):
        if not _routine_uses_defaults(routine):
            if batch:
                await _advance_life_routine_batch_async(batch)
                batch.clear()
            routine.advance()
            continue
        snap = _reap_and_snapshot_life_routine(routine)
        if snap is not None:
            batch.append(snap)
    if batch:
        await _advance_life_routine_batch_async(batch)


def stage_phone_actions(pa: PhoneActionService) -> StagedOffload | None:
    """Prepare phone task queue scan and submission for off-loop execution."""
    if not uses_default_method(pa, PhoneActionService, "advance"):
        return None
    if pa._closed or pa.runner is None or pa.tasks is None or pa._workers:
        return StagedOffload("skip", value=Outcome.success(0))

    def scan_phone():
        try:
            return pa.prepare_due_step(), None
        except TaskError as error:
            return None, Outcome.failed(error.code, str(error))

    def apply_phone(prepared):
        spec, failure = prepared
        return failure if failure is not None else Outcome.success(pa.launch_prepared_step(spec))

    return StagedOffload("offload", scan=scan_phone, apply=apply_phone)


async def advance_phone_actions_async(pa: PhoneActionService):
    """Offload phone task queue scanning and submission off-loop."""
    return await _run_staged(stage_phone_actions(pa), pa.advance)


def stage_state_maintenance(sm: StateMaintenance) -> StagedOffload | None:
    """Prepare state patrol, schedule sweep, and preflight SQLite IO for off-loop execution."""
    if not uses_default_method(sm, StateMaintenance, "advance"):
        return None
    if sm.closed:
        return StagedOffload("skip")
    reaped_updates = []
    for key, worker in tuple(sm.workers.items()):
        if worker.done():
            del sm.workers[key]
            if not worker.cancelled():
                error = worker.exception()
                sm.results[key] = (
                    {"state": "failed", "code": getattr(error, "code", "state_worker_failed")}
                    if error
                    else worker.result()
                )
                schedule_key = sm.worker_schedules.pop(key, None)
                if schedule_key is not None:
                    reaped_updates.append((encode(sm.results[key]), schedule_key))
    has_active_workers = bool(sm.workers)
    active_worker_keys = frozenset(sm.workers)

    def scan_state():
        from companion_v2.external_ownership import registry_for

        try:
            if registry_for(sm.runtime.installation_id).active("state") is not None:
                return None, False
        except Exception:
            pass
        if reaped_updates:
            with sm.runtime.transaction() as connection:
                for encoded_result, schedule_key in reaped_updates:
                    connection.execute(
                        "UPDATE role_state_schedules SET result_json = ? WHERE context_key = ?",
                        (encoded_result, schedule_key),
                    )
        sm.patrol()
        if has_active_workers:
            return None, False
        after = sm._scan_after
        rows = sm.runtime.read_all(
            """SELECT * FROM role_state_schedules
            WHERE (? IS NULL OR next_at > ? OR (next_at = ? AND context_key > ?))
            ORDER BY next_at, context_key LIMIT 16""",
            (after[0], after[0], after[0], after[1]) if after else (None, None, None, None),
        )
        for row in rows:
            sm._scan_after = (row["next_at"], row["context_key"])
            now = sm.tasks.now()
            if sm._preflight_checks.get(row["context_key"], now) > now:
                continue
            context = TaskContext.restore(json.loads(row["context_json"]))
            active_binding = sm._current_role_binding(context)
            if active_binding is None:
                sm._record_preflight(
                    row, context, state="waiting", code="state_role_context_unavailable", stage="binding"
                )
                continue
            context, instruction, guard = active_binding
            if sm._budget_checks.get(context.key, sm.tasks.now()) > sm.tasks.now():
                continue
            stage = "schedule_refresh"
            try:
                sm.bind(context, instruction, guard=guard)
                row = sm.runtime.read_one(
                    "SELECT * FROM role_state_schedules WHERE context_key = ?",
                    (role_schedule_key(context),),
                )
                if row is None:
                    continue
                context = TaskContext.restore(json.loads(row["context_json"]))
                if context.key not in sm.bindings:
                    sm._record_preflight(
                        row, context, state="waiting", code="state_role_context_unavailable", stage="binding"
                    )
                    continue
                saved = json.loads(row["settings_json"])
                stage = "authorization"
                sm.service.access.check(context, STATE_KIND)
                stage = "settings"
                settings, token = sm.service.settings(context)
                if (
                    not settings["enabled"]
                    or token != saved["token"]
                    or row["next_at"] > stamp(sm.tasks.now())
                    or sm.foreground_busy(context)
                    or context.key in active_worker_keys
                ):
                    continue
                now = sm.tasks.now()
                deferred_until = sm._sleep_wake_at(context, now)
                if deferred_until is not None:
                    with sm.runtime.transaction() as connection:
                        connection.execute(
                            """UPDATE role_state_schedules SET next_at = ?
                            WHERE context_key = ? AND next_at = ? AND revision = ? AND enabled = 1""",
                            (stamp(deferred_until), row["context_key"], row["next_at"], row["revision"]),
                        )
                    continue
                context, instruction, guard = sm._conversation_anchor((context, instruction, guard), now)
                sm._clear_preflight(context)
                idle_reason = sm._idle_skip_reason(context, settings, token, now)
                if idle_reason is not None:
                    skipped = {"state": "skipped", "reason": idle_reason, "checked_at": stamp(now)}
                    with sm.runtime.transaction() as connection:
                        connection.execute(
                            """UPDATE role_state_schedules
                            SET next_at = ?, result_json = ?
                            WHERE context_key = ? AND next_at = ? AND revision = ? AND enabled = 1""",
                            (
                                stamp(now + timedelta(minutes=settings["maintenance_minutes"])),
                                encode(skipped),
                                row["context_key"],
                                row["next_at"],
                                row["revision"],
                            ),
                        )
                    sm.results[context.key] = skipped
                    continue
                stage = "budget"
                deferred = sm.gateway.ledger.background_deferral(context, STATE_KIND, at=now)
                if deferred:
                    sm.results[context.key] = deferred
                    sm._budget_checks[context.key] = now + timedelta(seconds=30)
                    encoded = encode(deferred)
                    if row["result_json"] != encoded:
                        with sm.runtime.transaction() as connection:
                            connection.execute(
                                "UPDATE role_state_schedules SET result_json = ? WHERE context_key = ?",
                                (encoded, row["context_key"]),
                            )
                    continue
                sm._budget_checks.pop(context.key, None)
                with sm.runtime.transaction() as connection:
                    updated = connection.execute(
                        "UPDATE role_state_schedules SET next_at = ? "
                        "WHERE context_key = ? AND next_at = ? AND revision = ? AND enabled = 1",
                        (
                            stamp(now + timedelta(minutes=settings["maintenance_minutes"])),
                            row["context_key"],
                            row["next_at"],
                            row["revision"],
                        ),
                    )
                    if not updated.rowcount:
                        continue
                request_id = "state-period-" + digest([context.key, row["revision"], row["next_at"]])
                return (context, instruction, request_id, row["context_key"]), False
            except TaskError as error:
                sm._record_preflight(row, context, state="blocked", code=error.code, stage=stage)
                continue
        if len(rows) < 16:
            sm._scan_after = None
            return None, False
        return None, True

    def apply_state(scanned):
        launch_spec, notify_more = scanned
        if launch_spec is not None and not sm.closed and not sm.workers:
            context, instruction, request_id, schedule_key = launch_spec
            worker = asyncio.create_task(sm.run(context, instruction, request_id))
            sm.workers[context.key] = worker
            sm.worker_schedules[context.key] = schedule_key
            worker.add_done_callback(lambda done: sm.app.notify())
        elif notify_more:
            sm.app.notify()

    return StagedOffload("offload", scan=scan_state, apply=apply_state)


async def advance_state_maintenance_async(sm: StateMaintenance) -> None:
    """Offload state patrol, schedule sweep, and preflight SQLite IO off-loop."""
    await _run_staged(stage_state_maintenance(sm), sm.advance)


def stage_calendar_planning(cp: CalendarPlanning) -> StagedOffload | None:
    """Prepare calendar planning opportunity checks for off-loop execution."""
    if not uses_default_method(cp, CalendarPlanning, "advance"):
        return None
    if cp.closed:
        return StagedOffload("skip")
    for key, worker in tuple(cp.workers.items()):
        if worker.done():
            if not worker.cancelled():
                worker.result()
            del cp.workers[key]
    if cp.workers or PLANNING_KIND not in cp.gateway.router.profiles or not cp.app.state_maintenance.bindings:
        return StagedOffload("skip")
    groups = cp._role_bindings()
    if not groups:
        return StagedOffload("skip")
    roles = sorted(groups.items())
    at_check = cp.tasks.now()
    if not any(
        (cp._next_check(roles[(cp.cursor + offset) % len(roles)][0], roles[(cp.cursor + offset) % len(roles)][1]) or at_check)
        <= at_check
        for offset in range(min(16, len(roles)))
    ):
        return StagedOffload("skip")

    def scan_planning():
        for offset in range(min(16, len(roles))):
            index = (cp.cursor + offset) % len(roles)
            role_key, role_bindings = roles[index]
            context, instruction, _ = cp._role_anchor(role_bindings)
            at = cp.tasks.now()
            if (cp._next_check(role_key, role_bindings) or at) > at:
                continue
            cp._set_next_check(role_key, role_bindings, at)
            cp.cursor = index + 1
            try:
                settings = planning_values(cp.app.role_state.settings(context)[0])
                if (
                    settings["schedule_simulation_mode"] != SCHEDULE_MODE_ORCHESTRATED
                    or not settings["planning_enabled"]
                    or not settings["calendar_enabled"]
                    or cp.app.state_maintenance.foreground_busy(context)
                ):
                    continue
                deferred = cp.gateway.ledger.background_deferral(context, PLANNING_KIND, at=at)
                if deferred:
                    cp._set_result(context, deferred)
                    continue
                opportunity = cp.opportunity(context, settings, at)
                if opportunity:
                    return role_key, context, instruction, opportunity
            except (TaskError, AgendaError, ValueError) as error:
                code = getattr(error, "code", "planning_input_invalid")
                if code in ("supervisor_fenced", "supervisor_generation_conflict"):
                    raise
                cp._set_result(context, {"state": "blocked", "code": code})
        return None

    def apply_planning(selected):
        if selected is not None and not cp.closed and not cp.workers:
            role_key, context, instruction, opportunity = selected
            worker = asyncio.create_task(
                cp._automatic(context, instruction, opportunity), name="companion:calendar-planning"
            )
            cp.workers[role_key] = worker
            worker.add_done_callback(lambda done: cp.app.notify())

    return StagedOffload("offload", scan=scan_planning, apply=apply_planning)


async def advance_calendar_planning_async(cp: CalendarPlanning) -> None:
    """Offload calendar planning opportunity checks off-loop."""
    await _run_staged(stage_calendar_planning(cp), cp.advance)


def stage_daily_outfit(do: DailyOutfitPhotos) -> StagedOffload | None:
    """Prepare daily outfit readiness and SQLite task submission for off-loop execution."""
    if not (
        uses_default_method(do, DailyOutfitPhotos, "advance")
        and uses_default_method(do, DailyOutfitPhotos, "request")
    ):
        return None
    if do.closed or do.port is None or do.workers:
        return StagedOffload("skip")
    at = do.app.now()
    if do.next_check and at < do.next_check:
        return StagedOffload("skip")
    prev_next_check = do.next_check
    do.next_check = at + timedelta(seconds=60)
    bindings = tuple(do.app.state_maintenance.bindings.values())
    if not bindings:
        return StagedOffload("skip")

    def scan_outfit():
        for context, _, _ in bindings:
            try:
                do._allowed(context, at)
                day, zone, settings = do._day(context, at)
                key = do._key(context, day)
                document = do._read(key)
                if document:
                    task = do._task(document.get("task_id"))
                    if task and task.state in ("created", "retry_scheduled"):
                        return key, task
                    continue
                if (
                    settings["calendar_enabled"]
                    and settings["planning_enabled"]
                    and settings["schedule_simulation_mode"] == SCHEDULE_MODE_ORCHESTRATED
                    and PLANNING_KIND in do.app.gateway.router.profiles
                ):
                    planning = do.app.calendar_planning.status(context)
                    daily = planning["daily_result"]
                    last = planning["last_result"] or {}
                    manual_today = (
                        last.get("mode") == "daily"
                        and last.get("state") == "succeeded"
                        and datetime.fromisoformat(last["observed_at"]).astimezone(zone).date() == day
                    )
                    if not manual_today and (
                        daily is None or daily["state"] not in TERMINAL or planning["next_review_at"]
                    ):
                        continue
                if do.app.state_maintenance.foreground_busy(context):
                    continue
                now = do.app.now()
                do._allowed(context, now)
                if do.port is None or not do.port.outfit_image_route(context, do._identity(context, now)):
                    raise TaskError("daily_outfit_image_unavailable", "the role has no active Image route")
                req_day, _, _ = do._day(context, now)
                req_key = do._key(context, req_day)
                task_id = "outfit-" + digest([req_key, "automatic"])
                with do.runtime.transaction() as connection:
                    doc = do._read(req_key)
                    active = do._task(doc.get("task_id"))
                    existing = do._task(task_id)
                    if active and active.state not in TERMINAL:
                        task = active
                    elif existing or doc:
                        continue
                    else:
                        spec = TaskSpec(
                            task_id, context, DAILY_OUTFIT_KIND, task_id, task_id,
                            now, now + timedelta(minutes=15),
                        )
                        do._write(connection, req_key, {"task_id": task_id, "photo": doc.get("photo")})
                        task = require(do.app.tasks.submit(spec))
                if task.state in ("created", "retry_scheduled"):
                    return req_key, task
            except TaskError:
                continue
        return None

    def apply_outfit(selected):
        if selected is not None and not do.closed:
            key, task = selected
            do._launch(key, task)

    def cleanup_outfit():
        do.next_check = prev_next_check

    return StagedOffload("offload", scan=scan_outfit, apply=apply_outfit, cleanup=cleanup_outfit)


async def advance_daily_outfit_async(do: DailyOutfitPhotos) -> None:
    """Offload daily outfit readiness and SQLite task submission off-loop."""
    await _run_staged(stage_daily_outfit(do), do.advance)


def stage_growth_progress(gp: GrowthProgressService) -> StagedOffload | None:
    """Prepare growth candidate and receipt scanning for off-loop execution."""
    if not uses_default_method(gp, GrowthProgressService, "advance"):
        return None
    if gp.closed:
        return StagedOffload("skip")
    for key, worker in tuple(gp.workers.items()):
        if worker.done():
            del gp.workers[key]
            if not worker.cancelled():
                error = worker.exception()
                if error:
                    gp.results[key] = {
                        "state": "failed",
                        "code": getattr(error, "code", "growth_progress_failed"),
                    }
    if gp.workers or GROWTH_PROGRESS_KIND not in gp.gateway.router.profiles:
        return StagedOffload("skip")
    bindings = tuple(gp.app.state_maintenance.bindings.values())
    if not bindings:
        gp.next_checks.clear()
        return StagedOffload("skip")
    at_check = gp.tasks.now()
    step_count = min(16, len(bindings))
    if not any(
        bindings[(gp.cursor + offset) % len(bindings)][0].scope.kind is ScopeKind.PRIVATE
        and gp.next_checks.get(bindings[(gp.cursor + offset) % len(bindings)][0].key, at_check) <= at_check
        for offset in range(step_count)
    ):
        gp.cursor += step_count
        return StagedOffload("skip")

    def scan_growth():
        for _ in range(min(16, len(bindings))):
            context, instruction, _ = bindings[gp.cursor % len(bindings)]
            gp.cursor += 1
            at = gp.tasks.now()
            if context.scope.kind is not ScopeKind.PRIVATE or gp.next_checks.get(context.key, at) > at:
                continue
            gp.next_checks[context.key] = at + timedelta(seconds=30)
            try:
                if not gp.growth.candidates(context, at).items:
                    continue
                selected = gp._next_receipt(context)
                if selected:
                    return context, instruction, selected
            except TaskError as error:
                gp.results[context.key] = {"state": "blocked", "code": error.code}
        return None

    def apply_growth(chosen):
        if chosen is not None and not gp.closed and not gp.workers:
            context, instruction, selected = chosen
            worker = asyncio.create_task(
                gp.run(context, instruction, *selected), name="companion:growth-progress"
            )
            gp.workers[context.key] = worker
            worker.add_done_callback(lambda done: gp.app.notify())

    return StagedOffload("offload", scan=scan_growth, apply=apply_growth)


async def advance_growth_progress_async(gp: GrowthProgressService) -> None:
    """Offload growth candidate and receipt scanning off-loop."""
    await _run_staged(stage_growth_progress(gp), gp.advance)


def stage_self_review(sr: SelfReviewService) -> StagedOffload | None:
    """Prepare self-review due checks and evidence preparation for off-loop execution."""
    if not uses_default_method(sr, SelfReviewService, "advance"):
        return None
    if sr.closed:
        return StagedOffload("skip")
    for key, worker in tuple(sr.workers.items()):
        if worker.done():
            del sr.workers[key]
            if not worker.cancelled():
                error = worker.exception()
                if error:
                    code = self_review_failure_code(error)
                    sr.results[key] = {"state": "failed", "code": code}
                    sr.next_checks[key] = sr.tasks.now() + timedelta(seconds=self_review_retry_delay(code))
    if sr.workers or SELF_REVIEW_KIND not in sr.gateway.router.profiles:
        return StagedOffload("skip")
    bindings = tuple(sr.app.state_maintenance.bindings.values())
    if not bindings:
        sr.next_checks.clear()
        return StagedOffload("skip")
    at_check = sr.tasks.now()
    step_count = min(16, len(bindings))
    if not any(
        bindings[(sr.cursor + offset) % len(bindings)][0].scope.kind is ScopeKind.PRIVATE
        and sr.next_checks.get(bindings[(sr.cursor + offset) % len(bindings)][0].key, at_check) <= at_check
        for offset in range(step_count)
    ):
        sr.cursor += step_count
        return StagedOffload("skip")

    def scan_self_review():
        for _ in range(min(16, len(bindings))):
            context, instruction, _ = bindings[sr.cursor % len(bindings)]
            sr.cursor += 1
            at = sr.tasks.now()
            if context.scope.kind is not ScopeKind.PRIVATE or sr.next_checks.get(context.key, at) > at:
                continue
            sr.next_checks[context.key] = at + timedelta(seconds=30)
            try:
                if not sr.due(context, at):
                    continue
                job = sr.prepare(context, instruction, at)
                if job is None:
                    sr.next_checks[context.key] = at + timedelta(hours=1)
                    continue
                key, _ = sr._cursor(context)
                return context, instruction, key, at
            except TaskError as error:
                sr.results[context.key] = {"state": "blocked", "code": error.code}
                sr._record_failure(context, "blocked", error.code, at)
        return None

    def apply_self_review(chosen):
        if chosen is not None and not sr.closed and not sr.workers:
            context, instruction, key, at = chosen
            sr.workers[context.key] = asyncio.create_task(
                sr._run_scheduled(context, instruction, key, at), name="companion:self-review"
            )
            sr.workers[context.key].add_done_callback(lambda done: sr.app.notify())

    return StagedOffload("offload", scan=scan_self_review, apply=apply_self_review)


async def advance_self_review_async(sr: SelfReviewService) -> None:
    """Offload self-review due checks and evidence preparation off-loop."""
    await _run_staged(stage_self_review(sr), sr.advance)


def stage_presence(presence: PresenceCoordinator | None) -> StagedOffload | None:
    """Prepare typing/status SQLite queries for off-loop execution."""
    if presence is None:
        return StagedOffload("skip")
    if not uses_default_method(presence, PresenceCoordinator, "advance"):
        return None
    if presence.closed:
        return StagedOffload("skip")
    if presence.status_worker and presence.status_worker.done():
        if not presence.status_worker.cancelled():
            error = presence.status_worker.exception()
            if error:
                presence.results["presence"] = {
                    "state": "unknown",
                    "code": getattr(error, "code", "presence_failed"),
                }
        presence.status_worker = None
    need_status = presence.status_worker is None
    native_reply_rows = tuple(
        {"context_json": encode(lease["context"]), "batch_id": turn["batch_id"]}
        for lease in tuple(presence.native_replies.values())
        for turn in tuple(lease["turns"].values())
    )

    def scan_presence():
        from companion_v2.conversation.native_turns import context_rows

        rows = presence.runtime.read_all(
            """SELECT b.context_json, f.batch_id, f.sequence FROM conversation_flows f
            JOIN conversation_batches b USING(batch_id)
            WHERE f.state IN ('collecting','generating','delivering') ORDER BY f.sequence LIMIT 64""",
            (),
        )
        native = context_rows(presence.runtime, kind="private", open_only=True)
        combined = (
            *rows,
            *({"context_json": row["context_json"], "batch_id": row["turn_id"]} for row in native),
            *native_reply_rows,
        )
        active = {}
        for row in combined:
            context = TaskContext.restore(json.loads(row["context_json"]))
            presence.bind(context)
            try:
                presence._allowed(context, "typing_enabled", "platform.typing")
                turn = presence.active_turn(context)
                if turn is None:
                    continue
            except TaskError:
                continue
            route = presence.route(context)
            if route not in active:
                active[route] = (context, turn["batch_id"])
        has_candidates = bool(presence._candidates()) if need_status else False
        return active, has_candidates

    def apply_presence(scanned):
        active, has_candidates = scanned
        if presence.closed:
            return
        for key, (context, batch, worker) in tuple(presence.typing.items()):
            current = active.get(key)
            if not current or current[0].key != context.key:
                worker.cancel()
                if worker.done():
                    if not worker.cancelled():
                        worker.exception()
                    del presence.typing[key]
                continue
            if worker.done():
                if not worker.cancelled():
                    error = worker.exception()
                    if error:
                        presence._record_result(
                            "typing:" + key,
                            {
                                "state": "unknown",
                                "code": getattr(error, "code", "typing_failed"),
                                "batch_id": batch,
                            },
                            context.key,
                        )
                    presence.suppressed[key] = current[1]
                del presence.typing[key]
        for key, (context, batch) in active.items():
            if key not in presence.typing and presence.suppressed.get(key) != batch:
                worker = asyncio.create_task(presence._pulse(context), name="companion:typing:" + key)
                presence.typing[key] = (context, batch, worker)
                worker.add_done_callback(lambda done: presence.app.notify())
        presence.suppressed = {k: v for k, v in presence.suppressed.items() if k in active}
        if presence.status_worker is None and has_candidates:
            presence.status_worker = asyncio.create_task(presence._sync_status(), name="companion:presence")

    return StagedOffload("offload", scan=scan_presence, apply=apply_presence)


async def advance_presence_async(presence: PresenceCoordinator | None) -> None:
    """Offload typing/status SQLite queries while managing pulse tasks on-loop."""
    if presence is None:
        return
    await _run_staged(stage_presence(presence), presence.advance)


def stage_journal_schedule(js: JournalScheduleDriver) -> StagedOffload | None:
    """Prepare due daily journal schedule calculations for off-loop execution."""
    if not uses_default_method(js, JournalScheduleDriver, "advance"):
        return None
    for key, worker in tuple(js._workers.items()):
        if worker.done():
            del js._workers[key]
            if not worker.cancelled():
                js.results[key] = worker.result()
    capacity = js.triggers.tasks.limits.background_slots - len(js._workers)
    if capacity <= 0 or not js._schedules:
        return StagedOffload("skip", barrier_before=True)
    now = js.triggers.tasks.now()
    due_items = [
        (key, schedule)
        for key, schedule in sorted(tuple(js._schedules.items()), key=lambda pair: pair[1].next_at)
        if schedule.next_at <= now and key not in js._workers
    ]
    if not due_items:
        return StagedOffload("skip", barrier_before=True)

    def prepare_due():
        planned = []
        remaining = capacity
        for key, schedule in due_items:
            if remaining <= 0:
                break
            zone = ZoneInfo(schedule.timezone)
            local_now = now.astimezone(zone)
            due_day = schedule.next_at.astimezone(zone).date()
            updated = replace(
                schedule,
                next_at=next_daily(now + timedelta(microseconds=1), schedule.timezone, schedule.local_time),
            )
            run_day = local_now.date().isoformat()
            if due_day != local_now.date():
                today = datetime.combine(local_now.date(), schedule.local_time, zone)
                if today.astimezone(utc_timezone.utc) > now:
                    planned.append((key, schedule, updated, None))
                    continue
            planned.append((key, schedule, updated, run_day))
            remaining -= 1
        return planned

    def apply_journal(planned):
        for key, original, updated, run_day in planned:
            if js._schedules.get(key) == original:
                js._schedules[key] = updated
            if run_day is not None and key not in js._workers:
                worker = asyncio.create_task(
                    js._run(original, run_day), name="companion:journal-daily:" + key
                )
                js._workers[key] = worker
                worker.add_done_callback(lambda done: js.notify())

    return StagedOffload("offload", scan=prepare_due, apply=apply_journal, barrier_before=True)


async def advance_journal_schedule_async(js: JournalScheduleDriver) -> None:
    """Offload due timezone calculations when firing daily journal schedules."""
    await _run_staged(stage_journal_schedule(js), js.advance)
