"""Host lifecycle for independently bound private role routines."""

import asyncio
from datetime import time, timedelta
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from .configuration import GROUP
from .runtime import RoleLifeRuntime
from .configuration import values
from .scheduling import next_bedtime, sleep_rhythm
from .physiology import recommended_sleep_minutes


class RoleLifeCoordinator:
    def __init__(self, app):
        self.app = app
        self.routines = {}
        self._generation = None
        self._closed = False

    def for_context(self, context):
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("life_scope_invalid", "private routines require a private context")
        if context.key not in self.routines:
            self.routines[context.key] = RoleLifeRuntime(self.app, context=context)
        return self.routines[context.key]

    @staticmethod
    def route(context):
        s = context.scope
        return (s.installation_id, s.platform, s.bot_id, s.account_id, s.native_scope_id)

    def _can_reuse_binding(self, routine, context, role_instruction):
        if (routine._retired or routine._closed or routine.instruction != role_instruction
                or self.route(routine.context) != self.route(context)
                or routine.context.role_binding.role != context.role_binding.role):
            return False
        binding = self.app.tasks.identity.resolve_role(routine.context.scope, observed_at=self.app.now())
        return binding.is_success and binding.unwrap() == routine.context.role_binding

    def bind(self, context, role_instruction, *, guard=None):
        if self._closed:
            raise TaskError("life_closed", "routine coordinator is closed")
        routine = self.routines.get(context.key)
        if (routine is not None and routine.context.key != context.key
                and not self._can_reuse_binding(routine, context, role_instruction)):
            del self.routines[context.key]
            routine = None
        if routine is None:
            # The Dashboard and native intake can name the same private route
            # with different conversation IDs. Reuse a binding while its authority is current.
            for candidate in self.routines.values():
                if self._can_reuse_binding(candidate, context, role_instruction):
                    routine = candidate
                    self.routines[context.key] = candidate
                    break
        if routine is None:
            routine = self.for_context(context)
        routine.bind(context, role_instruction, guard=guard)
        for other in tuple(self.routines.values()):
            if other is not routine and other.instruction is not None and self.route(other.context) == self.route(context):
                other.retire()
        return routine

    async def rebind(self, context, role_instruction, *, guard=None):
        """Refresh edited native persona text after its old workers finish."""
        routine = self.routines.get(context.key)
        if routine is not None and routine.instruction not in (None, role_instruction):
            routine.retire()
            await routine.close()
            if routine._workers:
                await asyncio.gather(*tuple(routine._workers.values()), return_exceptions=True)
                await routine.close()
            if self.routines.get(context.key) is routine:
                del self.routines[context.key]
        return self.bind(context, role_instruction, guard=guard)

    def consumer_generation(self):
        if self._generation is None:
            row = self.app.configuration.store.read_one("SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (GROUP,))
            self._generation = self.app.configuration.start_consumer(GROUP,
                expected_generation=row["generation"] if row else 0)
        return self._generation

    async def scheduled_diary(self, schedule, day):
        routine = self.routines.get(schedule.context.key)
        if routine is not None and schedule.schedule_id == routine.schedule_id:
            if schedule.role_instruction != routine.instruction:
                raise TaskError("life_binding_changed", "daily writing retains its original persona")
            return await routine.diary(local_date=day)
        return await self.app.journal_triggers.daily(schedule.context, schedule.event, local_date=day,
            timezone=schedule.timezone, role_instruction=schedule.role_instruction)

    def advance(self):
        if not self._closed:
            for routine in tuple(self.routines.values()):
                routine.advance()

    def _in_read_snapshot(self):
        configuration = getattr(self.app, "configuration", None)
        for store in (getattr(self.app, "runtime", None), getattr(configuration, "store", None)):
            diagnostic = getattr(getattr(store, "_read_snapshot_diagnostics", None), "current", None)
            if diagnostic is not None and not diagnostic.get("writer_owned"):
                return True
        return False

    def _select_sleep_executor(self, routine, *, refresh, now=None):
        eligible = []
        for candidate in tuple(self.routines.values()):
            if candidate.owner != routine.owner or candidate._closed or candidate._retired or candidate.instruction is None:
                continue
            try:
                if refresh:
                    candidate.refresh()
                    if candidate.settings and candidate.settings["enabled"] and candidate.settings["sleep_enabled"]:
                        candidate.authorize()
                        eligible.append((candidate, candidate._next_sleep))
                else:
                    projection = candidate.sleep_projection(now=now)
                    if projection is not None:
                        _, next_sleep = projection
                        eligible.append((candidate, next_sleep))
            except (TaskError, ValueError, KeyError):
                continue
        return min(eligible, key=lambda item: item[0].context.key) if eligible else (None, None)

    def owns_sleep_schedule(self, routine):
        return self.sleep_executor(routine) is routine

    def sleep_executor(self, routine):
        executor, _ = self._select_sleep_executor(routine, refresh=not self._in_read_snapshot())
        return executor

    def sleep_schedule_status(self, routine, config, *, body_sleep=None, now=None):
        settings = values(config)
        now = now or self.app.now()
        executor, next_at = (
            self._select_sleep_executor(routine, refresh=False, now=now)
            if settings["sleep_enabled"]
            else (None, None)
        )
        if settings["sleep_enabled"] and next_at is None:
            next_at = next_bedtime(now, routine.owner, settings["sleep_timezone"],
                time.fromisoformat(settings["sleep_time"]), settings["sleep_variation_minutes"])
        active_sleep = (
            body_sleep
            if body_sleep is not None
            else self.app.role_state.body_clock.read(
                routine.context, now=now, sleep_enabled=settings["sleep_enabled"]
            )
        )
        if active_sleep is not None and "sleep_debt_minutes" in active_sleep and "workload_score" in active_sleep:
            profile = {
                "sleep_pressure": active_sleep["sleep_pressure"],
                "sleep_debt_minutes": active_sleep["sleep_debt_minutes"],
                "workload_score": active_sleep["workload_score"],
                "awake_minutes": active_sleep["awake_minutes"],
                "has_sleep_history": active_sleep["has_sleep_history"],
            }
        else:
            profile = self.app.role_state.body_clock.profile(
                routine.context, now=now, sleep_enabled=settings["sleep_enabled"]
            )
        planned_minutes = recommended_sleep_minutes(settings["sleep_minutes"],
            profile["sleep_debt_minutes"], profile["workload_score"])
        next_wake = next_at + timedelta(minutes=planned_minutes) if next_at else None
        rhythm = sleep_rhythm(now, next_at if settings["sleep_enabled"] else None, active_sleep)
        return {"shared": True, "owner": {"installation_id": routine.context.scope.installation_id,
                    "bot_id": routine.context.scope.bot_id, "role_id": routine.context.role_binding.role.subject_id},
            "enabled": settings["sleep_enabled"], "time": settings["sleep_time"],
            "timezone": settings["sleep_timezone"], "duration_minutes": settings["sleep_minutes"],
            "variation_minutes": settings["sleep_variation_minutes"],
            "window_minutes": settings["sleep_window_minutes"],
            "recommended_duration_minutes": planned_minutes, "physiology": profile,
            "executor_is_current": executor is routine,
            "executor_state": "available" if executor is not None else "no_authorized_window" if settings["sleep_enabled"] else "disabled",
            "executor_window": self._window_descriptor(executor),
            "next_sleep_at": next_at.isoformat() if next_at else None,
            "next_wake_at": next_wake.isoformat() if next_wake else None,
            "sleep_window_closes_at": (next_at + timedelta(minutes=settings["sleep_window_minutes"])).isoformat()
                if next_at else None,
            "rhythm": rhythm, "sleep_state": active_sleep["state"] if active_sleep else None,
            "active_wake_at": (active_sleep["planned_wake_at"] if active_sleep and active_sleep["state"] == "sleeping"
                                else active_sleep["wake_until"] if active_sleep and active_sleep["state"] == "interrupted"
                                else None)}

    @staticmethod
    def _window_descriptor(routine):
        if routine is None:
            return None
        scope = routine.context.scope
        account = scope.account_id or "未知账号"
        return {"platform": scope.platform, "account_id": scope.account_id,
                "native_scope_id": scope.native_scope_id, "session_id": scope.session_id,
                "label": f"{scope.platform} · 账号 {account} · 会话 {scope.native_scope_id}"}

    def cancel_activity(self, identifier):
        wake_prefix = "wake:" + identifier + ":"
        for routine in tuple(self.routines.values()):
            for key, worker in tuple(routine._workers.items()):
                if key in (identifier, "wake:" + identifier) or key.startswith(wake_prefix):
                    worker.cancel()

    @property
    def active_handles(self):
        return tuple(handle for routine in self.routines.values() for handle in routine.active_handles)

    def cancel_running(self):
        for routine in tuple(self.routines.values()):
            routine.cancel_running()

    async def close(self):
        self._closed = True
        self.cancel_running()
        await asyncio.gather(*(routine.close() for routine in tuple(self.routines.values())))
