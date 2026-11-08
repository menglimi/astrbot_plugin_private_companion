"""Original-role planning on the shared background supervisor and calendar owner."""

import asyncio
from datetime import datetime, timedelta
import json
import logging
from zoneinfo import ZoneInfo

from companion_v2.execution.models import encode_prompt
from companion_v2.conversation.foreground import BackgroundPromptTrigger, PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskContext, TaskDefinition,
    TaskError, TaskRegistration, TaskResult, TaskSpec, digest, encode, require)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.identity_policy.interventions import (CALENDAR_INTERVENTION,
    REST_WAKE_INTERVENTION, require_capability)
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.role_work.state.ownership import role_schedule_key
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.versioned_records import owner_key
from .planning_config import SCHEDULE_MODE_ORCHESTRATED, configured_weather_location, planning_values
from .planning_inputs import capture, validate
from . import planning_observation
from .planning_output import INSTRUCTION, INTERVENTION_INSTRUCTION, MODE_INSTRUCTIONS, parse_output
from .models import AgendaError
logger = logging.getLogger(__name__)


KIND = "calendar.plan"
OWNER = "calendar.planning"


def planning_profile(provider, *, max_output_tokens=2048):
    return ModelTaskProfile(KIND, Lane.BACKGROUND, provider,
        "沿原人格安排自己的生活；保留长期目标、当天小步和未完成事项，计划不是完成记录。",
        max_output_tokens=max_output_tokens)


class CalendarPlanning:
    def __init__(self, runtime, *, weather_port=None):
        self.runtime = runtime
        self.weather_port = weather_port
        self.operations = OperationRepository(runtime)
        self.pending, self.workers, self.results, self.next_checks = {}, {}, {}, {}
        self.committed = set()
        self.invocations = set()
        self.closed, self.cursor = False, 0
        self.legacy_contexts = {}
        self.registrations = (TaskRegistration(TaskDefinition(KIND, OWNER, "calendar.write", Lane.BACKGROUND,
            side_effect=SideEffect.LOCAL, binding=ProviderBinding(OWNER, "local", "1", "planning-1"),
            timeout_seconds=180, max_attempts=1, concurrency=1), self.validate),)

    def attach(self, app):
        self.app, self.tasks, self.gateway = app, app.tasks, app.gateway
        self.legacy_contexts = self._load_legacy_contexts()
        self.runner = ExactTaskRunner(self.tasks, frozenset({KIND}), self.generate, waiting=app.background_waiting)

    def _load_legacy_contexts(self):
        contexts = {}
        for row in self.runtime.read_all("SELECT record_json FROM execution_tasks WHERE kind = ?", (KIND,)):
            try:
                context = TaskContext.restore(json.loads(row["record_json"])["spec"]["context"])
                role_key = self.app.calendar.records.role_key(context)
            except (KeyError, TypeError, ValueError):
                continue
            contexts.setdefault(role_key, []).append(context)
        return {key: tuple(values) for key, values in contexts.items()}

    def _base_identifier(self, context, slot):
        return "calendar-plan-" + digest([self.app.calendar.records.role_key(context), slot])

    def _legacy_identifier(self, context, slot, *, connection=None):
        base = "calendar-plan-" + digest([owner_key(context), slot])
        key = "calendar.admission.v1:" + base
        if connection is None:
            row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
        else:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
        count = json.loads(row["value"])["count"] if row else 0
        return base + (":admission:" + str(count) if count else "")

    def identifier(self, context, slot, *, connection=None):
        base = self._base_identifier(context, slot)
        if connection is None:
            row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                        ("calendar.admission.v1:" + base,))
        else:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                                     ("calendar.admission.v1:" + base,)).fetchone()
        count = json.loads(row["value"])["count"] if row else 0
        return base + (":admission:" + str(count) if count else "")

    def _role_bindings(self):
        groups = {}
        for context, instruction, guard in self.app.state_maintenance.bindings.values():
            role_key = self.app.calendar.records.role_key(context)
            groups.setdefault(role_key, []).append((context, instruction, guard))
        return groups

    def _role_anchor(self, bindings):
        by_context = {item[0].key: item for item in bindings}
        if bindings:
            row = self.runtime.read_one("SELECT context_json FROM role_state_schedules WHERE context_key = ?",
                                        (role_schedule_key(bindings[0][0]),))
            if row is not None:
                try:
                    anchor = TaskContext.restore(json.loads(row["context_json"]))
                    if anchor.key in by_context:
                        return by_context[anchor.key]
                except (KeyError, TypeError, ValueError):
                    pass
        return min(bindings, key=lambda item: item[0].key)

    def _set_result(self, context, result):
        role_key = self.app.calendar.records.role_key(context)
        result = planning_observation.save_result(self, context, result)
        self.results[role_key] = result
        for bound, _, _ in self._role_bindings().get(role_key, ()):
            self.results[bound.key] = result

    def invalidate(self, context):
        role_key = self.app.calendar.records.role_key(context)
        self.next_checks.pop(role_key, None)
        for bound, _, _ in self._role_bindings().get(role_key, ()):
            self.next_checks.pop(bound.key, None)
        self.next_checks.pop(context.key, None)

    def _next_check(self, role_key, bindings):
        candidates = [self.next_checks.get(role_key)]
        candidates.extend(self.next_checks.get(context.key) for context, _, _ in bindings)
        return max((item for item in candidates if item is not None), default=None)

    def _set_next_check(self, role_key, bindings, at):
        deadline = at + timedelta(seconds=30)
        self.next_checks[role_key] = deadline
        for context, _, _ in bindings:
            self.next_checks[context.key] = deadline

    def _check_intervention(self, context, at):
        require_capability(self.tasks, context, CALENDAR_INTERVENTION, at=at)
        from companion_v2.role_work.life import rest_gate
        settings = rest_gate.read_settings(self.runtime, context)
        if rest_gate.sleep_context(self.runtime, context, now=at, settings=settings)["resting"]:
            require_capability(self.tasks, context, REST_WAKE_INTERVENTION, at=at)

    def _automatic_marker_key(self, context, slot):
        role_key = self.app.calendar.records.role_key(context)
        return "calendar.automatic-slot.v1:" + digest([role_key, slot])

    def _automatic_contexts(self, context):
        role_key = self.app.calendar.records.role_key(context)
        candidates = [context]
        candidates.extend(binding[0] for binding in self.app.state_maintenance.bindings.values())
        candidates.extend(self.legacy_contexts.get(role_key, ()))
        seen = set()
        for candidate in candidates:
            if candidate.key in seen or self.app.calendar.records.role_key(candidate) != role_key:
                continue
            seen.add(candidate.key)
            yield candidate

    def _legacy_automatic_attempt(self, context, slot, *, connection=None):
        current = self.identifier(context, slot, connection=connection)
        if connection is not None:
            exists = connection.execute("SELECT 1 FROM operations WHERE operation_id = ?", (current,)).fetchone()
            if exists is not None:
                return current
        elif self.operations.get(current).is_success:
            return current
        for candidate in self._automatic_contexts(context):
            identifier = self._legacy_identifier(candidate, slot, connection=connection)
            if connection is not None:
                exists = connection.execute("SELECT 1 FROM operations WHERE operation_id = ?",
                                            (identifier,)).fetchone()
                if exists is not None:
                    return identifier
            elif self.operations.get(identifier).is_success:
                return identifier
        return None

    def _automatic_attempted(self, context, slot):
        return self._automatic_task_id(context, slot) is not None

    def _automatic_task_id(self, context, slot):
        key = self._automatic_marker_key(context, slot)
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
        if row is not None:
            return json.loads(row["value"]).get("task_id") or self.identifier(context, slot)
        legacy = self._legacy_automatic_attempt(context, slot)
        if legacy is None:
            return None
        with self.runtime.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO runtime_metadata(key, value) VALUES (?, ?)",
                               (key, encode({"task_id": legacy, "status": "legacy_attempt"})))
        return legacy

    def _claim_automatic(self, connection, context, slot, identifier):
        key = self._automatic_marker_key(context, slot)
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return False, json.loads(row["value"]).get("task_id")
        legacy = self._legacy_automatic_attempt(context, slot, connection=connection)
        if legacy is not None:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (key, encode({"task_id": legacy, "status": "legacy_attempt"})))
            return False, legacy
        connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
            (key, encode({"task_id": identifier, "context_key": context.key, "status": "claimed"})))
        return True, identifier

    def _deferred_admission(self, context, slot, identifier, *, automatic=False):
        # Only a refusal with no provider dispatch permits a fresh planning task.
        # Unknown outcomes keep their original operation and are never retried.
        base = self._base_identifier(context, slot)
        with self.runtime.transaction() as connection:
            calls = connection.execute("SELECT 1 FROM model_calls WHERE task_id = ? LIMIT 1",
                                       (identifier,)).fetchone()
            if calls is not None:
                return
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                                     ("calendar.admission.v1:" + base,)).fetchone()
            count = json.loads(row["value"])["count"] if row else 0
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                ("calendar.admission.v1:" + base, encode({"count": count + 1})))
            if automatic:
                marker_key = self._automatic_marker_key(context, slot)
                marker = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                                            (marker_key,)).fetchone()
                if marker is not None and json.loads(marker["value"]).get("task_id") == identifier:
                    connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (marker_key,))

    def attempted(self, context, slot, *, automatic=False):
        if automatic:
            return self._automatic_attempted(context, slot)
        return self.operations.get(self.identifier(context, slot)).is_success

    async def guard(self, context, instruction):
        binding = self.app.state_maintenance.bindings.get(context.key)
        if binding is None or binding[:2] != (context, instruction):
            raise TaskError("planning_role_changed", "original role binding is unavailable")
        if binding[2] is not None:
            await binding[2](context.scope, context.role_binding, instruction)

    @outcome
    def validate(self, spec, at):
        self.app.require_running()
        job = self.pending.get(spec.task_id)
        if self.closed or job is None or job["context"] != spec.context or digest(job) != spec.request_ref:
            raise TaskError("planning_job_unavailable", "original planning input is unavailable")
        validate(self, job, at, committed=spec.task_id in self.committed)
        return True

    async def run(self, context, instruction, *, mode, slot, occurrence=None, manual=False, refresh=False,
                  automatic=False, intervention=False, expected_input_epoch=None):
        invocation = asyncio.current_task()
        self.invocations.add(invocation)
        try:
            return await self._run(context, instruction, mode=mode, slot=slot, occurrence=occurrence,
                                   manual=manual, refresh=refresh, automatic=automatic,
                                   intervention=intervention, expected_input_epoch=expected_input_epoch)
        finally:
            self.invocations.discard(invocation)

    async def run_intervention(self, context, instruction, request_id, *, expected_input_epoch=None):
        slot = "intervention:" + digest([context.key, request_id])
        return await self.run(context, instruction, mode="intervention", slot=slot,
                              manual=True, intervention=True, expected_input_epoch=expected_input_epoch)

    async def _run(self, context, instruction, *, mode, slot, occurrence=None, manual=False, refresh=False,
                   automatic=False, intervention=False, expected_input_epoch=None):
        if (mode not in ("daily", "horizon", "refine", "intervention")
                or mode == "refine" and occurrence is None
                or (mode == "intervention") != bool(intervention)):
            raise ValueError("select an explicit planning mode and original period")
        if self.closed:
            raise TaskError("planning_closed", "planning worker is closed")
        self.app.require_running()
        if intervention:
            self._check_intervention(context, self.tasks.now())
            if (expected_input_epoch is not None
                    and self.app.state_maintenance._input_epochs.get(context.key, 0) != expected_input_epoch):
                raise TaskError("planning_inputs_changed", "new conversation input superseded this schedule intervention")
        self.app.calendar.access.check(context, "calendar.write")
        identifier = self.identifier(context, slot)
        prior_task_id = (self._automatic_task_id(context, slot) if automatic else
                         identifier if self.attempted(context, slot) else None)
        if prior_task_id is not None:
            return {"state": "already_attempted", "mode": mode, "task_id": prior_task_id, "replayed": True}
        if KIND not in self.gateway.router.profiles:
            raise TaskError("model_task_unregistered", "planning needs its background profile")
        deferred = self.gateway.ledger.background_deferral(context, KIND, at=self.tasks.now())
        if deferred:
            report = {**deferred, "mode": mode}
            self._set_result(context, report)
            return report
        await self.guard(context, instruction)
        settings = planning_values(self.app.role_state.settings(context)[0])
        location = (configured_weather_location(settings["planning_weather_location"])
                    if not intervention and (manual or settings["planning_enabled"]) and settings["calendar_enabled"]
                    and settings["planning_weather_enabled"] else None)
        weather = None
        if location is not None and self.weather_port is not None:
            try:
                weather = await asyncio.wait_for(self.weather_port.fetch(location), timeout=6)
            except Exception as error:
                logger.warning("V2 planning weather fetch failed: %s", type(error).__name__)
        with self.runtime.transaction() as connection:
            if automatic:
                claimed, prior_task_id = self._claim_automatic(connection, context, slot, identifier)
                if not claimed:
                    return {"state": "already_attempted", "mode": mode,
                            "task_id": prior_task_id or identifier, "replayed": True}
            job = capture(self, context, instruction, mode, slot, occurrence,
                          weather=weather, weather_location=location, manual=manual, refresh=refresh,
                          role_global=automatic)
            if intervention:
                job["intervention"] = True
                job["input_epoch"] = (expected_input_epoch if expected_input_epoch is not None
                                       else self.app.state_maintenance._input_epochs.get(context.key, 0))
            role_key = self.app.calendar.records.role_key(context)
            reservation = require(self.operations.reserve(OperationRef(identifier, OWNER, context.scope,
                VersionRef(identifier, 1), identifier), parameter_hash=digest([role_key, mode, slot]),
                observed_at=job["at"]))
            if reservation.replayed:
                return {"state": "already_attempted", "mode": mode, "task_id": identifier, "replayed": True}
            self.pending[identifier] = job
            try:
                require(self.tasks.submit(TaskSpec(identifier, context, KIND, digest(job), identifier,
                    job["at"], min(job["end"], job["at"] + timedelta(minutes=3)))))
            except BaseException:
                self.pending.pop(identifier, None)
                raise
        try:
            result = require(await self.runner.run(context, identifier))
            task = result.task
            report = {"state": task.state, "mode": mode, "task_id": identifier, "code": task.reason}
            if result.failure_code:
                report["code"] = result.failure_code
            if result.retry_at is not None:
                report.update(state="deferred", retry_at=result.retry_at.isoformat())
                self._deferred_admission(context, slot, identifier, automatic=automatic)
            if identifier in self.committed:
                saved_result = self.results.get(self.app.calendar.records.role_key(context),
                                                self.results.get(context.key, {}))
                report.update(saved=saved_result.get("saved", 0),
                              rejected=saved_result.get("rejected", []),
                              replaced=saved_result.get("replaced", 0))
            self._set_result(context, report)
            return report
        finally:
            self.pending.pop(identifier, None)
            self.committed.discard(identifier)

    async def generate(self, run):
        job = self.pending[run.task.task_id]
        context, at = job["context"], self.tasks.now()
        await self.guard(context, job["instruction"])
        run.validate()
        trigger = BackgroundPromptTrigger(job["slot"], context.actor.principal, context.scope, at)
        mode_note = MODE_INSTRUCTIONS.get(job["mode"], "")
        prompt_instruction = (INTERVENTION_INSTRUCTION if job.get("intervention")
                              else INSTRUCTION + ("\n" + mode_note if mode_note else ""))
        if ":review:" in job["slot"]:
            prompt_instruction += ("\n当前是新时段对剩余一天的重新观察：按 now 之后的最新日历与角色状态重新考虑，"
                "跳过已经过去的时段和已存在安排，不补写旧请求、不补造完成记录。")
        materials = [PromptMaterial(OWNER, "planning_material", encode_prompt(job["material"]))]
        if job.get("intervention"):
            materials.append(PromptMaterial("conversation.native", "intervention_dialogue", encode_prompt({
                "conversation": job.get("intervention_dialogue", ()),
                "basis": "validated_private_native_dialogue",
                "assistant_reply_returned": any(
                    item["speaker"] == "assistant" for item in job.get("intervention_dialogue", ())),
            })))
        request = PromptRequest(trigger, job["instruction"], SealedForegroundContext(
            tuple(materials), (), ()),
            prompt_instruction, context.role_binding, output_format="calendar_planning", response_format="json_object")
        completion = await self.gateway.generate(run, request)
        try:
            if completion.draft is None:
                raise TaskError("planning_output_invalid", "planning needs a complete result")
            items, rejected = parse_output(completion.draft.text, job)
            await self.guard(context, job["instruction"])
            receipts = []
            replaced = 0
            with self.runtime.transaction():
                run.validate()
                run.begin_effect()
                if job.get("intervention"):
                    for index, change in enumerate(items):
                        if change["action"] == "create":
                            identifier = "planned-intervention-" + digest([run.task.task_id, index])
                            self.app.calendar.command(context, identifier=identifier, action="create",
                                expected_revision=0, operation_key=run.task.task_id + ":" + str(index),
                                data=change["data"], sources=job["sources"],
                                planning={"mode": "intervention", "slot": job["slot"]})
                        else:
                            self.app.calendar.command(context, identifier=change["id"],
                                action={"move": "exception", "refine": "refine", "cancel": "cancel"}[change["action"]],
                                expected_revision=change["expected_revision"],
                                operation_key=run.task.task_id + ":" + str(index),
                                data=change["data"], sources=job["sources"])
                elif job["mode"] == "refine":
                    selected = job["occurrence"]
                    receipts.append(self.app.calendar.command(context, identifier=selected["id"], action="refine",
                        expected_revision=selected["revision"], operation_key=run.task.task_id + ":detail",
                        data={"sequence": selected["sequence"], "detail": items}, sources=job["sources"]))
                else:
                    if job["refresh_plans"] and items:
                        for selected in job["refresh_plans"]:
                            receipts.append(self.app.calendar.command(context, identifier=selected["id"],
                                action="cancel", expected_revision=selected["revision"],
                                operation_key=run.task.task_id + ":replace:" + selected["id"],
                                sources=job["sources"]))
                            replaced += 1
                    for index, (event, basis) in enumerate(items):
                        identifier = "planned-" + digest([run.task.task_id, index])
                        receipts.append(self.app.calendar.command(context, identifier=identifier, action="create",
                            expected_revision=0, operation_key=run.task.task_id + ":" + str(index),
                            data=event, sources=job["sources"], planning=basis))
                require(self.operations.transition(run.task.task_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=self.tasks.now()))
                self.committed.add(run.task.task_id)
                run.record_operation(run.task.task_id)
            self._set_result(context, {"state": "succeeded", "mode": job["mode"], "task_id": run.task.task_id,
                                       "saved": len(items), "replaced": replaced, "rejected": list(rejected)})
            self.app.notify()
            return TaskResult("succeeded", effect="committed", result_ref=run.task.task_id,
                              operation_id=run.task.task_id, receipt_ref=run.task.task_id)
        except BaseException:
            if run.task.task_id not in self.committed:
                self.gateway.ledger.discard(completion.call_id)
            raise

    def opportunity(self, context, settings, at):
        if settings.get("schedule_simulation_mode", SCHEDULE_MODE_ORCHESTRATED) != SCHEDULE_MODE_ORCHESTRATED:
            return None
        local = at.astimezone(ZoneInfo(settings["planning_timezone"]))
        settings_token = self.app.role_state.planning_settings_token(context)
        for mode, period in (("daily", local.date().isoformat()),
                             ("horizon", "%04d-W%02d" % local.isocalendar()[:2])):
            slot = mode + ":" + settings["planning_timezone"] + ":" + period + ":settings:" + settings_token
            if not self.attempted(context, slot, automatic=True):
                return mode, slot, None
            if mode == "daily":
                _, review_slot, _ = planning_observation.daily_observation(self, context, slot, local)
                if review_slot is not None:
                    return mode, review_slot, None
        page = self.app.calendar.occurrences(context, start=at,
            end=at + timedelta(minutes=settings["planning_refine_minutes"]))
        for item in page["items"]:
            if item["kind"] == "role_plan" and item["state"] == "accepted":
                series = self.app.calendar.read(context, item["id"])
                if series["sources"]:
                    continue
                slot = "refine:" + digest([item["id"], item["sequence"], series["schedule"]["anchor"]])
                if not self.attempted(context, slot, automatic=True):
                    return "refine", slot, item
        return None

    async def _automatic(self, context, instruction, opportunity):
        mode, slot, occurrence = opportunity
        try:
            return await self.run(context, instruction, mode=mode, slot=slot, occurrence=occurrence,
                                  automatic=True)
        except (TaskError, ValueError) as error:
            self._set_result(context, {"state": "blocked", "mode": mode,
                "code": getattr(error, "code", "planning_input_invalid")})

    def advance(self):
        if self.closed:
            return
        for key, worker in tuple(self.workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.result()
                del self.workers[key]
        if self.workers or KIND not in self.gateway.router.profiles:
            return
        groups = self._role_bindings()
        if not groups:
            return
        roles = sorted(groups.items())
        # Rotate across roles; private windows sharing one role form a single scheduler entry.
        for offset in range(min(16, len(roles))):
            index = (self.cursor + offset) % len(roles)
            role_key, role_bindings = roles[index]
            context, instruction, _ = self._role_anchor(role_bindings)
            at = self.tasks.now()
            if (self._next_check(role_key, role_bindings) or at) > at:
                continue
            self._set_next_check(role_key, role_bindings, at)
            self.cursor = index + 1
            try:
                settings = planning_values(self.app.role_state.settings(context)[0])
                if (settings["schedule_simulation_mode"] != SCHEDULE_MODE_ORCHESTRATED
                        or not settings["planning_enabled"] or not settings["calendar_enabled"]
                        or self.app.state_maintenance.foreground_busy(context)):
                    continue
                deferred = self.gateway.ledger.background_deferral(context, KIND, at=at)
                if deferred:
                    self._set_result(context, deferred)
                    continue
                opportunity = self.opportunity(context, settings, at)
                if opportunity:
                    worker = asyncio.create_task(self._automatic(context, instruction, opportunity), name="companion:calendar-planning")
                    self.workers[role_key] = worker
                    worker.add_done_callback(lambda done: self.app.notify())
                    break
            except (TaskError, AgendaError, ValueError) as error:
                code = getattr(error, "code", "planning_input_invalid")
                if code in ("supervisor_fenced", "supervisor_generation_conflict"):
                    raise
                self._set_result(context, {"state": "blocked", "code": code})

    def status(self, context):
        settings = self.app.role_state.settings(context)[0]
        role_key = self.app.calendar.records.role_key(context)
        next_check = self.next_checks.get(role_key, self.next_checks.get(context.key))
        local = self.tasks.now().astimezone(ZoneInfo(settings["planning_timezone"]))
        slot = "daily:" + settings["planning_timezone"] + ":" + local.date().isoformat() + ":settings:" + self.app.role_state.planning_settings_token(context)
        daily_result, _, next_review = planning_observation.daily_observation(self, context, slot, local)
        return {"enabled": settings["planning_enabled"], "calendar_enabled": settings["calendar_enabled"],
            "schedule_simulation_mode": settings["schedule_simulation_mode"],
            "running": role_key in self.workers,
            "last_result": self.results.get(role_key, self.results.get(context.key)) or planning_observation.stored_result(self, context),
            "daily_result": daily_result, "next_review_at": next_review.isoformat() if next_review else None,
            "next_check": next_check.isoformat() if next_check is not None else None}

    @property
    def active_handles(self):
        return (self.runner.active_handles + tuple("calendar-planning:" + key for key in self.workers)
            + tuple("planning-invocation:" + str(id(t)) for t in self.invocations))

    def cancel_running(self):
        self.closed = True
        for worker in self.workers.values():
            worker.cancel()
        for invocation in tuple(self.invocations):
            invocation.cancel()

    async def close(self):
        self.cancel_running()
        await self.runner.close()
        if self.invocations:
            await asyncio.wait(tuple(self.invocations), timeout=0.25)
        if self.workers:
            await asyncio.wait(tuple(self.workers.values()), timeout=0.25)
        for key, worker in tuple(self.workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                del self.workers[key]
