"""Role routines driven by the host wake loop, with durable activity intent."""

import asyncio
from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import TERMINAL, TaskError, digest, require, stamp
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import RoleOwnerRef, SourceRef, VersionRef
from companion_v2.sources.models import SourceRecord
from companion_v2.role_work.models import RoleActivity, RoleActivityState as State
from companion_v2.role_work.life.physiology import (nap_ready, night_sleep_ready,
    recommended_sleep_minutes, sleep_receipt)
from companion_v2.role_work.reader import READ_KIND
from companion_v2.role_work.work_models import RoleWorkSpec
from companion_v2.role_work.journal.models import JournalKind

from .calendar_work import require_window, reserve_routine_slot, routine_for
from .configuration import GROUP, target_for, values
from .scheduling import next_bedtime


class RoleLifeRuntime:
    def __init__(self, app, *, context=None):
        self.app, self.runtime = app, app.runtime
        self.context, self.instruction, self.guard = context, None, None
        self.settings = None
        self._revision = None
        self._workers, self.results = {}, {}
        self._dreams = set()
        self._next_sleep = None
        self._next_wake = None
        self._next_calendar_work = None
        self._next_writing_plan_check = None
        self.next_reading_at = None
        self.failure_code = None
        self._closed = False
        self._retired = False

    @staticmethod
    def _route(context):
        scope = context.scope
        return (scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                scope.native_scope_id, context.role_binding.role.subject_id)

    def bind(self, context, role_instruction, *, guard=None):
        # Unrelated grant changes advance policy_revision without changing the
        # original principal, binding, role or audience represented by this key.
        if self.context is not None:
            if self.instruction not in (None, role_instruction):
                raise TaskError("life_binding_changed", "a live routine cannot change its authority")
            if self.context.key != context.key and self._route(self.context) != self._route(context):
                raise TaskError("life_binding_changed", "a live routine cannot change its authority")
            if self.context.key == context.key:
                # Refresh the same context's policy/audience snapshot.
                self.context = context
        else:
            self.context = context
        # The Dashboard target and native intake can name the same private route
        # with different conversation IDs.  Keep the first context (and the
        # activities/grants created under it) so a later native bind does not
        # turn that live routine into a different, unbound window.
        self.instruction, self.guard = role_instruction, guard
        self._retired = False

    @property
    def schedule_id(self):
        return "role.life.daily:" + self.context.key

    def retire(self):
        self._retired = True
        self.failure_code = "life_binding_changed"
        self._next_sleep = self._next_wake = None
        if self.schedule_id in self.app.journal_schedule._schedules:
            self.app.journal_schedule.remove(self.schedule_id)
        self.cancel_running()

    @property
    def owner(self):
        c = self.context
        return RoleOwnerRef(c.scope.installation_id, c.scope.bot_id, c.role_binding.role.subject_id)

    def authorize(self):
        self.app.require_running()
        if self.context is None or self.instruction is None:
            raise TaskError("life_not_bound", "the host must bind the original persona first")
        if self._retired:
            raise TaskError("life_binding_changed", "the host has selected another persona")
        require(self.app.tasks.identity.authorize(self.context.actor, scope=self.context.scope,
            capability="role.life.run", observed_at=self.app.now()))
        require(self.app.tasks.authorize_context(self.context, action="role.life.run", observed_at=self.app.now()))

    async def preflight(self):
        self.authorize()
        if self.guard:
            await self.guard(self.context.scope, self.context.role_binding, self.instruction)
        self.authorize()

    def event(self, label, text, *, at=None):
        at = at or self.app.now()
        identifier = "life-source-" + digest([self.context.key, label, at])
        source = SourceRef(identifier, GROUP, self.context.scope, VersionRef(identifier, 1), at)
        require(self.app.role_journal.sources.publish(SourceRecord(source, self.context.audience, digest(text), text),
                                                     observed_at=self.app.now()))
        return InboundConversationEvent(identifier, self.context.actor.principal, self.context.scope,
            source, "role.journal.write", digest(text), at)

    def activities(self, *, state=None):
        if self.context is None:
            return ()
        result, cursor = [], None
        while True:
            page = self.app.role_life.activities_for_role(self.owner, state=state, limit=100, after_id=cursor)
            result.extend(a for a in page if a.scope == self.context.scope and a.work is not None
                          and a.work.context_key == self.context.key)
            if len(page) < 100:
                return tuple(result)
            cursor = page[-1].activity_id

    def activity(self, identifier):
        try:
            a = self.app.role_life.activity(identifier, owner=self.owner)
        except KeyError:
            raise TaskError("life_activity_unavailable", "activity is unavailable in this role") from None
        if a.scope != self.context.scope or a.work is None or a.work.context_key != self.context.key:
            raise TaskError("life_context_changed", "activity belongs to another authority")
        return a

    def refresh(self):
        if self.instruction is None or self._closed or self._retired:
            return
        snapshot = require(self.app.configuration.read(self.context.actor, target=target_for(self.context),
            group_id=GROUP, observed_at=self.app.now()))
        if snapshot.resolution_token == self._revision:
            return
        settings = values(snapshot)
        generation = self.app.life_runtime.consumer_generation()
        if self._revision is not None:
            self.app.journal_schedule.remove(self.schedule_id)
        self.settings, self._revision = settings, snapshot.resolution_token
        self._next_sleep = self._next_wake = self._next_calendar_work = self.next_reading_at = None
        self.failure_code = None
        if not settings["enabled"]:
            self.cancel_running()
            require(self.app.configuration.report_application(self.context.actor, snapshot=snapshot,
                generation=generation, succeeded=True, observed_at=self.app.now()))
            return
        self.authorize()
        if settings["diary_enabled"]:
            event = self.event("routine-registration", "管理员启用了本窗口的私人日记日程；这不是用户发来的聊天。")
            require(self.app.journal_schedule.register_daily(self.schedule_id, self.context, event,
                role_instruction=self.instruction, timezone=settings["timezone"], local_time=settings["diary_time"]))
        if settings["sleep_enabled"]:
            self._next_sleep = next_bedtime(self.app.now(), self.owner, settings["sleep_timezone"],
                time.fromisoformat(settings["sleep_time"]), settings["sleep_variation_minutes"])
            self._next_wake = self._next_sleep + timedelta(minutes=settings["sleep_minutes"])
        require(self.app.configuration.report_application(self.context.actor, snapshot=snapshot,
            generation=generation, succeeded=True, observed_at=self.app.now()))

    def start_sleep(self, request_id, minutes, *, label="角色开始一段睡眠", sleep_kind="manual"):
        with self.runtime.transaction():
            return self._start_sleep(request_id, minutes, label=label, sleep_kind=sleep_kind)

    def _start_sleep(self, request_id, minutes, *, label, sleep_kind):
        self.authorize()
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError("sleep duration must be 1..1440 minutes")
        if sleep_kind not in ("night", "nap", "manual"):
            raise ValueError("unknown sleep kind")
        identifier = "sleep-" + digest([self.context.key, request_id])
        try:
            return self.activity(identifier)
        except TaskError as error:
            if error.code != "life_activity_unavailable":
                raise
        if self.app.role_state.body_clock.pending_sleep(self.context):
            raise TaskError("life_already_sleeping", "there is already an active sleep")
        if self.app.role_state.body_clock.active(self.context, "reading"):
            raise TaskError("life_reading_active", "wait for the current reading or cancel it before sleeping")
        now = self.app.now()
        base_need = self.settings["sleep_minutes"] if self.settings else minutes
        profile = self.app.role_state.body_clock.profile(self.context, now=now)
        target = (25 if sleep_kind == "nap" else recommended_sleep_minutes(base_need,
            profile["sleep_debt_minutes"], profile["workload_score"])) if sleep_kind != "manual" else minutes
        receipt = sleep_receipt(kind=sleep_kind, base_need_minutes=base_need, target_minutes=target,
            workload_score=profile["workload_score"], sleep_debt_minutes=profile["sleep_debt_minutes"])
        work = RoleWorkSpec("sleep", self.context.key, request_id, due_at=now + timedelta(minutes=target))
        saved = self.app.role_life.add_activity(RoleActivity(identifier, self.owner.persona_id,
            self.context.scope, now, State.ACTIVE, description=label, work=work, sleep=receipt))
        self.app.notify()
        return saved

    def wake(self, identifier, *, expected_revision, operation_id):
        self.authorize()
        a = self.activity(identifier)
        if a.state in (State.COMPLETED, State.CANCELLED):
            # A retry after a successful/failed transition must not turn into a
            # generic "command invalid"; the sleep is already over.
            return a
        if a.work.kind != "sleep" or self.app.now() <= a.started_at:
            raise TaskError("life_sleep_invalid", "sleep must actually have started before waking")
        saved = self.app.role_state.body_clock.finish(self.context, a, operation_id=operation_id,
            observed_at=self.app.now(), checkpoint="已醒来，梦境由独立任务记录")
        self.app.notify()
        return saved

    def reading_unsettled(self, activity, *, scheduled=False):
        task_id = self.app.role_reader.task_id(self.context, activity)
        row = self.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?", (task_id,))
        if scheduled:
            # A terminal reading can no longer commit against the old activity
            # revision. The next slot can retry under a new identity.
            return row is not None and (row["state"] not in TERMINAL or row["state"] == "succeeded")
        return (row is not None and row["state"] not in ("failed", "cancelled", "expired", "dead_letter")
            or self.runtime.read_one("""SELECT 1 FROM model_calls WHERE task_id = ?
                AND state IN ('reserved','dispatched','unknown')""", (task_id,)) is not None)

    def start_reading(self, identifier, *, expected_revision, request_id, window=None):
        self.authorize()
        if window is not None:
            require_window(self.app.calendar, self.context, window, self.app.now())
        a = self.activity(identifier)
        if a.work.kind != "reading":
            raise TaskError("life_reading_invalid", "select a reading activity")
        if a.state is State.PAUSED and self.reading_unsettled(a, scheduled=window is not None):
            raise TaskError("life_reading_unresolved", "the original reading must settle before another attempt")
        if self.app.role_state.body_clock.active(self.context, "sleep"):
            raise TaskError("life_sleeping", "wake the role before reading")
        if self.app.role_state.body_clock.active(self.context, "reading"):
            raise TaskError("life_reading_active", "another reading is already running")
        record = require(self.app.role_journal.sources.read(a.work.source, resolved=self.context.actor,
            capability="role.reading.record", observed_at=self.app.now(), authorizer=self.app.tasks.identity))
        if record.content_hash != a.work.content_hash or digest(record.payload) != a.work.content_hash:
            raise TaskError("reading_source_changed", "reading must retain its original text")
        work = replace(a.work, request_id=request_id)
        saved = self.app.role_life.transition_activity(identifier, owner=self.owner, expected_revision=expected_revision,
            state=State.ACTIVE, operation_id="read-start-" + digest([self.context.key, request_id]),
            observed_at=self.app.now(), work=work, checkpoint="正在阅读实际提供的片段")
        self.launch(identifier, lambda: self._read(saved, window=window))
        return saved

    async def _read(self, activity, *, window=None):
        result = None
        try:
            await self.preflight()
            result = await self.app.role_reader.consume(self.context, activity,
                role_instruction=self.instruction, window=window)
            return result
        finally:
            current = self.activity(activity.activity_id)
            if current.state is State.ACTIVE:
                code = (result.failure.code if result and result.failure else
                        result.unwrap().task.state if result else "interrupted")
                self.app.role_life.transition_activity(current.activity_id, owner=self.owner,
                    expected_revision=current.revision, state=State.PAUSED,
                    operation_id="read-pause-" + digest([current.activity_id, current.work.request_id]),
                    observed_at=self.app.now(), checkpoint=code)

    async def derive(self, target, event, *, automatic=True):
        if automatic and (not self.settings or not self.settings["derive_enabled"]):
            return None
        await self.preflight()
        entry = self.app.role_journal.get(target.key)
        existing = self.app.role_journal.derivatives(target.key)
        if existing:
            return None
        event = self.event("derive-" + digest(entry.reference), "独立提取已保存的私人书写索引。", at=entry.updated_at)
        result = await self.app.journal_writer.derive(self.context, event, entry.reference,
            role_instruction=self.instruction, request_id="life-derive-" + digest(entry.reference))
        self.results["derive"] = result
        return result

    async def diary(self, *, local_date=None, retry_of=None, request_id=None):
        await self.preflight()
        zone = self.settings["timezone"]
        day = local_date or self.app.now().astimezone(ZoneInfo(zone)).date().isoformat()
        event = self.event("diary-" + day, "生活执行端请求保存本日私人日记；这不是用户聊天内容。")
        result = await self.app.journal_triggers.daily(self.context, event, local_date=day, timezone=zone,
            role_instruction=self.instruction, retry_of=retry_of, retry_id=request_id if retry_of else None)
        if result.is_success and result.unwrap().state == "written":
            target = self.app.journal_triggers.target(self.context, JournalKind.DIARY, day, zone)
            await self.derive(target, event)
        return result

    async def dream(self, activity, *, retry_of=None, request_id=None):
        await self.preflight()
        zone = self.settings["timezone"]
        event = self.event("sleep-completed-" + activity.activity_id,
            "生活执行端确认本段睡眠已结束，没有执行其他醒后动作。", at=activity.updated_at)
        result = await self.app.journal_triggers.sleep_completed(self.context, event,
            started_at=activity.started_at, completed_at=activity.updated_at, timezone=zone,
            role_instruction=self.instruction, retry_of=retry_of, retry_id=request_id if retry_of else None)
        if result.is_success and result.unwrap().state == "written":
            target = self.app.journal_triggers.target(self.context, JournalKind.DREAM,
                activity.updated_at.astimezone(ZoneInfo(zone)).date().isoformat(), zone)
            await self.derive(target, event)
        return result

    def launch(self, key, factory, *, timeout_seconds=None):
        if self._closed or key in self._workers:
            return False
        async def execute():
            try:
                work = factory()
                return (await asyncio.wait_for(work, timeout=timeout_seconds)
                        if timeout_seconds is not None else await work)
            except asyncio.TimeoutError:
                return Outcome.failed("life_wake_timeout", "scheduled wake did not finish before its deadline")
            except TaskError as error:
                return Outcome.failed(error.code, str(error))
            except Exception:
                return Outcome.failed("life_execution_failed", "routine stopped; inspect its recorded outcome")
        self._workers[key] = asyncio.create_task(execute(), name="companion:life:" + key)
        def collect(done):
            if timeout_seconds is not None and self._workers.get(key) is done:
                if not done.cancelled():
                    self.results[key] = done.result()
                del self._workers[key]
            self.app.notify()
        self._workers[key].add_done_callback(collect)
        return True

    def advance(self):
        if self.context is None or self._closed:
            return
        for key, worker in tuple(self._workers.items()):
            if worker.done():
                if not worker.cancelled():
                    self.results[key] = worker.result()
                del self._workers[key]
        if self.instruction is None or self._retired:
            return
        try:
            self.refresh()
            if not self.settings or not self.settings["enabled"]:
                return
            self.authorize()
            now = self.app.now()
            self.app.role_state.body_clock.resume_due(self.context, observed_at=now)
            for sleeping in self.app.role_state.body_clock.active(self.context, "sleep"):
                wake_key = "wake:" + sleeping.activity_id
                if (sleeping.work.due_at <= now and wake_key not in self._workers
                        and wake_key not in self.results):
                    self.launch(wake_key, self._wake_shared_due, timeout_seconds=30)
            records = self.activities()
            for a in records:
                if (a.work.kind == "reading" and a.state is State.ACTIVE
                        and a.activity_id not in self._workers):
                    # A restart has no in-memory reading call. Never turn elapsed time into a completion.
                    self.app.role_life.transition_activity(a.activity_id, owner=self.owner, expected_revision=a.revision,
                        state=State.PAUSED, operation_id="recover-reading-" + digest([a.activity_id, a.revision]),
                        observed_at=now, checkpoint="interrupted; inspect the original model result before retrying")
                elif a.work.kind == "sleep" and a.state is State.COMPLETED and a.activity_id not in self._dreams:
                    self._dreams.add(a.activity_id)
                    self.launch(a.activity_id, lambda a=a: self.dream(a))
            pending_sleep = self.app.role_state.body_clock.pending_sleep(self.context)
            busy = self._workers or any(a.state is State.ACTIVE for a in records) or pending_sleep
            if (self._next_sleep is not None and self._next_sleep <= now and not busy
                    and self.app.life_runtime.owns_sleep_schedule(self)):
                zone = self.settings["sleep_timezone"]
                local_now = now.astimezone(ZoneInfo(zone))
                due_day = self._next_sleep.astimezone(ZoneInfo(zone)).date()
                if not (due_day != local_now.date()
                        and local_now.time().replace(tzinfo=None) < time.fromisoformat(self.settings["sleep_time"])):
                    profile = self.app.role_state.body_clock.profile(self.context, now=now)
                    window_closed = now >= self._next_sleep + timedelta(minutes=self.settings["sleep_window_minutes"])
                    if night_sleep_ready(profile) or window_closed:
                        self._next_sleep = next_bedtime(now + timedelta(microseconds=1), self.owner, zone,
                            time.fromisoformat(self.settings["sleep_time"]), self.settings["sleep_variation_minutes"])
                        self._next_wake = self._next_sleep + timedelta(minutes=self.settings["sleep_minutes"])
                        self.start_sleep("daily-sleep-" + due_day.isoformat(),
                            self.settings["sleep_minutes"], sleep_kind="night")
                        return
            local_now = now.astimezone(ZoneInfo(self.settings["sleep_timezone"]))
            profile = self.app.role_state.body_clock.profile(self.context, now=now)
            if (self.settings["sleep_enabled"] and not busy
                    and nap_ready(profile, local_minutes=local_now.hour * 60 + local_now.minute)):
                today = local_now.date().isoformat()
                if not any(item.sleep and item.sleep.kind == "nap" and item.started_at.astimezone(
                        ZoneInfo(self.settings["sleep_timezone"])).date().isoformat() == today
                        for item in self.app.role_state.body_clock.episodes(self.context)):
                    self.start_sleep("daily-nap-" + today, 25, label="角色短暂午休", sleep_kind="nap")
                    return
            if not busy:
                self.advance_calendar_work(records, now)
        except (TaskError, ValueError, KeyError) as error:
            self.failure_code = error.code if isinstance(error, TaskError) else "life_configuration_invalid"
            self.cancel_running()

    def advance_calendar_work(self, records, now):
        if self._next_calendar_work is not None and now < self._next_calendar_work:
            return
        self._next_calendar_work = now + timedelta(seconds=20)
        context, calendar = self.context, self.app.calendar
        if self.app.state_maintenance.foreground_busy(context):
            return
        settings, _ = self.app.role_state.settings(context)
        if not settings["calendar_enabled"]:
            self.next_reading_at = None
            return
        pending = {}
        if self.settings["reading_enabled"] and READ_KIND in self.app.gateway.router.profiles:
            for activity in sorted(records, key=lambda a: (a.started_at, a.activity_id)):
                if activity.work.kind != "reading" or activity.state not in (State.PLANNED, State.PAUSED):
                    continue
                source = activity.work.source
                if source not in pending or activity.work.start_offset < pending[source].work.start_offset:
                    pending[source] = activity
        writer = self.app.scheduled_authoring
        if not pending:
            self.next_reading_at = None
            if writer is None:
                return
        page = calendar.occurrences(context, start=now, end=now + timedelta(days=7, hours=1),
                                    kinds=("role_plan", "commitment"), diagnostics=True)
        self.next_reading_at = next((item["start"] for item in page["items"]
            if pending and item["start"] > stamp(now) and routine_for(item) == "reading"), None)
        current = [item for item in page["items"]
                   if item["state"] == "accepted" and item["start"] <= stamp(now)]
        # Leave an overlapping commitment or other planned activity its time.
        if page["scan_truncated"] or any(routine_for(item) is None for item in current):
            current = []
        writing = any(key.startswith("writing:") for other in self.app.life_runtime.routines.values()
                      if other.owner == self.owner for key in other._workers)
        if writing or self.app.role_state.body_clock.active(context, "reading"):
            return
        if writer is not None:
            for item in current:
                if routine_for(item) != "writing":
                    continue
                key = "writing:" + item["id"] + ":" + str(item["sequence"])
                if (key not in self.results and datetime.fromisoformat(item["end"]) - now >= timedelta(minutes=1)
                        and writer.ready(context, item)):
                    self.launch(key, lambda: writer.run(self, item))
                    return
        if (writer is not None and (self._next_writing_plan_check is None or now >= self._next_writing_plan_check)
                and not any(routine_for(item) == "writing" and item["start"] > stamp(now)
                            for item in page["items"])
                and not any(routine_for(item) == "writing" for item in current)):
            self._next_writing_plan_check = now + timedelta(minutes=10)
            planned = writer.plan_continuation(context, now, settings, page,
                                                sleep_plan=(self._next_sleep, self._next_wake))
            # A new role plan can occupy a gap the reading scan just saw.
            if planned and pending:
                page = calendar.occurrences(context, start=now, end=now + timedelta(days=7, hours=1),
                                            kinds=("role_plan", "commitment"), diagnostics=True)
        if not pending:
            return
        ready = [a for a in pending.values() if a.state is State.PLANNED or not self.reading_unsettled(a, scheduled=True)]
        if not ready:
            return
        for item in current:
            if routine_for(item) != "reading":
                continue
            start = datetime.fromisoformat(item["start"])
            queued = next((a for a in ready if a.started_at <= start
                           and (a.state is State.PLANNED or a.updated_at < start)), None)
            if (queued is None or datetime.fromisoformat(item["end"]) - now < timedelta(seconds=30)
                    or self.app.gateway.ledger.background_deferral(context, READ_KIND, at=now)):
                continue
            try:
                self.start_reading(queued.activity_id, expected_revision=queued.revision,
                    request_id="scheduled-read-" + digest([queued.activity_id, queued.revision]),
                    window=item)
            except TaskError as error:
                if error.code not in ("source_revoked", "source_expired", "source_audience_denied", "source_audience_mismatch"):
                    raise
                self.app.role_life.transition_activity(queued.activity_id, owner=self.owner,
                    expected_revision=queued.revision, state=State.CANCELLED,
                    operation_id="unavailable-reading-" + digest([queued.activity_id, queued.revision]),
                    observed_at=now, checkpoint=error.code)
            return
        if (self.next_reading_at is None and not page["scan_truncated"] and not page["occurrences_truncated"]):
            self.next_reading_at = reserve_routine_slot(calendar, context, routine="reading",
                timezone=settings["planning_timezone"], now=now, items=page["items"],
                sleep_plan=(self._next_sleep, self._next_wake))

    async def _wake_shared_due(self):
        await self.preflight()
        self.app.role_state.body_clock.finish_due(self.context)
        self.app.notify()
        return Outcome.success({"state": "sleep_timer_checked"})

    @property
    def active_handles(self):
        return tuple("life-" + key for key in self._workers)

    def cancel_running(self):
        for worker in tuple(self._workers.values()):
            worker.cancel()

    async def close(self):
        self._closed = True
        self.cancel_running()
        if self._workers:
            await asyncio.wait(tuple(self._workers.values()), timeout=0.25)
        for key, worker in tuple(self._workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                del self._workers[key]
