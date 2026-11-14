"""Role-owned physical sleep receipts and their derived physiological profile."""

from dataclasses import replace
from datetime import timedelta

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.role_work.codec import activity_from_json
from companion_v2.role_work.models import RoleActivityState as State
from companion_v2.role_work.models import RoleSleepReceipt
from companion_v2.role_work.state.ownership import role_owner

from .physiology import (actual_sleep_seconds, oversleep_extension, sleep_profile,
                         sleep_quality_range)


class RoleBodyClock:
    def __init__(self, tasks, life):
        self.tasks, self.life, self.runtime = tasks, life, tasks.runtime

    def _records(self, context, *, states=None, kind=None, limit=200):
        owner_key = digest(role_owner(context))
        state_clause = "" if not states else " AND state IN (" + ",".join("?" for _ in states) + ")"
        kind_clause = "" if kind is None else " AND json_extract(record_json, '$.work.kind') = ?"
        rows = self.runtime.read_all("""SELECT record_json FROM role_activities
            WHERE owner_key = ? AND json_extract(record_json, '$.scope.kind') = 'private'"""
            + state_clause + kind_clause + " ORDER BY updated_at DESC, activity_id DESC LIMIT ?",
            (owner_key, *(state.value for state in (states or ())), *((kind,) if kind else ()), limit))
        records = []
        for row in rows:
            try:
                activity = activity_from_json(row["record_json"])
            except (ValueError, TypeError, KeyError):
                continue
            if activity.work:
                records.append(activity)
        return tuple(records)

    def active(self, context, kind):
        return self._records(context, states=(State.ACTIVE,), kind=kind)

    def pending_sleep(self, context):
        return self._records(context, states=(State.ACTIVE, State.PAUSED), kind="sleep")

    def episodes(self, context):
        return tuple(reversed(self._records(context, kind="sleep")))

    def workload(self, context, *, now=None):
        now = now or self.tasks.now()
        cutoff = stamp(now - timedelta(hours=24))
        readings = self._records(context, kind="reading")
        reading_minutes = 0.0
        for activity in readings:
            if activity.started_at > now or activity.updated_at < now - timedelta(hours=24):
                continue
            if activity.state is State.ACTIVE:
                started, end = activity.updated_at, now
            elif activity.reading is not None:
                try:
                    active = self.life.activity_revision(activity.activity_id, activity.revision - 1,
                                                         owner=activity.owner)
                except KeyError:
                    continue
                if active.state is not State.ACTIVE:
                    continue
                started, end = active.updated_at, activity.reading.completed_at
            else:
                continue
            reading_minutes += min(360.0, max(0.0, (end - started).total_seconds() / 60))
        scope = context.scope
        row = self.runtime.read_one("""SELECT COUNT(*) AS n FROM conversation_inputs i
            JOIN conversation_batches b ON b.batch_id = i.batch_id
            WHERE b.created_at >= ? AND b.created_at <= ? AND i.withdrawn = 0
              AND json_extract(b.context_json, '$.role_binding.scope.kind') = 'private'
              AND json_extract(b.context_json, '$.role_binding.scope.installation_id') = ?
              AND json_extract(b.context_json, '$.role_binding.scope.bot_id') = ?
              AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?""",
            (cutoff, stamp(now), scope.installation_id, scope.bot_id, context.role_binding.role.subject_id))
        inbound_count = int(row["n"] if row else 0)
        return max(0, min(100, round(inbound_count * 1.0 + reading_minutes * 0.25)))

    def profile(self, context, *, now=None):
        now = now or self.tasks.now()
        return sleep_profile(self.episodes(context), workload_score=self.workload(context, now=now), now=now)

    def latest(self, context):
        records = self._records(context, kind="sleep")
        if not records:
            return None
        active = next((item for item in records if item.state is State.ACTIVE), None)
        if active is not None:
            return active
        return records[0]

    def reference(self, context):
        activity = self.latest(context)
        return {"id": activity.activity_id, "revision": activity.revision} if activity else None

    def read(self, context, *, reference=None, historical=False, now=None):
        now = now or self.tasks.now()
        if historical:
            if reference is None:
                return None
            if type(reference) is not dict or set(reference) != {"id", "revision"}:
                raise TaskError("body_sleep_reference", "invalid physical sleep reference")
            try:
                activity = self.life.activity_revision(reference["id"], reference["revision"], owner=role_owner(context))
            except (KeyError, ValueError, TypeError) as error:
                raise TaskError("body_sleep_reference", "original physical sleep is unavailable") from error
            if not activity.work or activity.work.kind != "sleep" or activity.scope.kind is not ScopeKind.PRIVATE:
                raise TaskError("body_sleep_reference", "reference is not a private role sleep")
            episodes = (activity,)
        else:
            activity = self.latest(context)
            if activity is None:
                return None
            episodes = self.episodes(context)
        receipt = activity.sleep
        actual = actual_sleep_seconds(activity, now)
        profile = sleep_profile(episodes, workload_score=self.workload(context, now=now), now=now)
        return {"basis": "execution_receipt", "state": "sleeping" if activity.state is State.ACTIVE else
            "interrupted" if activity.state is State.PAUSED else
            "awake" if activity.state is State.COMPLETED else "interrupted",
            "kind": receipt.kind if receipt else "night",
            "started_at": stamp(activity.started_at),
            "planned_wake_at": stamp(activity.work.due_at),
            "ended_at": stamp(activity.updated_at) if activity.state in (State.COMPLETED, State.CANCELLED) else None,
            "slept_minutes": round(actual / 60),
            "target_minutes": receipt.target_minutes if receipt else max(1, round((activity.work.due_at - activity.started_at).total_seconds() / 60)),
            "interruptions": receipt.interruptions if receipt else 0,
            "early_interruptions": receipt.early_interruptions if receipt else 0,
            "wake_until": stamp(receipt.wake_until) if receipt and receipt.wake_until else None,
            "oversleep_minutes": receipt.oversleep_minutes if receipt else 0,
            "sleep_quality_range": [receipt.quality_min, receipt.quality_max] if receipt else None,
            **profile}

    def interrupt(self, context, activity_id, *, observed_at, grace_minutes):
        """Record one real wake-up; repeated messages while awake do not add interruptions."""
        owner = role_owner(context)
        try:
            activity = self.life.activity(activity_id, owner=owner)
        except KeyError:
            return None
        if (activity.state is not State.ACTIVE or not activity.work or activity.work.kind != "sleep"
                or activity.scope.kind is not ScopeKind.PRIVATE):
            return None
        at = observed_at
        receipt = activity.sleep or self._legacy_receipt(activity)
        segment_seconds = max(0, round((at - activity.updated_at).total_seconds()))
        early = segment_seconds < 20 * 60
        interruptions = receipt.interruptions + 1
        early_interruptions = receipt.early_interruptions + int(early)
        debt = self.profile(context, now=at)["sleep_debt_minutes"]
        quality_min, quality_max = sleep_quality_range(receipt.workload_score, debt,
                                                        interruptions, early_interruptions)
        updated = replace(receipt, slept_seconds=min(172800, receipt.slept_seconds + segment_seconds),
                          interruptions=interruptions, early_interruptions=early_interruptions,
                          quality_min=quality_min, quality_max=quality_max,
                          wake_until=at + timedelta(minutes=max(0, grace_minutes)))
        return self.life.transition_activity(activity_id, owner=owner, expected_revision=activity.revision,
            state=State.PAUSED, operation_id="sleep-interrupt-" + digest([activity_id, activity.revision]),
            observed_at=at, checkpoint="用户消息打断睡眠；进入清醒宽限后再评估是否续睡", sleep=updated)

    def _legacy_receipt(self, activity):
        target = max(1, min(1440, round((activity.work.due_at - activity.started_at).total_seconds() / 60)))
        low, high = sleep_quality_range(0, 0)
        return RoleSleepReceipt("night", target, target, quality_min=low, quality_max=high)

    def finish(self, context, activity, *, operation_id, observed_at, checkpoint, state=State.COMPLETED):
        owner = role_owner(context)
        receipt = activity.sleep or self._legacy_receipt(activity)
        if activity.state is not State.ACTIVE:
            elapsed = 0
        else:
            elapsed = max(0, round((observed_at - activity.updated_at).total_seconds()))
        # A sleep left active across restarts can be days old.  The receipt
        # schema bounds one episode at 48h; without this clamp, waking it fails
        # validation and the UI can never end the sleep.
        total = min(172800, receipt.slept_seconds + elapsed)
        debt = self.profile(context, now=observed_at)["sleep_debt_minutes"]
        low, high = sleep_quality_range(receipt.workload_score, debt,
                                         receipt.interruptions, receipt.early_interruptions)
        receipt = replace(receipt, slept_seconds=total, wake_until=None,
                          quality_min=low, quality_max=high)
        return self.life.transition_activity(activity.activity_id, owner=owner,
            expected_revision=activity.revision, state=state,
            operation_id=operation_id, observed_at=observed_at, checkpoint=checkpoint, sleep=receipt)

    def resume_due(self, context, *, observed_at=None):
        at = observed_at or self.tasks.now()
        resumed = []
        with self.runtime.transaction():
            for activity in self.pending_sleep(context):
                receipt = activity.sleep
                if (activity.state is not State.PAUSED or receipt is None or receipt.wake_until is None
                        or receipt.wake_until > at):
                    continue
                remaining = receipt.target_minutes * 60 - receipt.slept_seconds
                cleared = replace(receipt, wake_until=None)
                if remaining <= 0:
                    changed = self.life.transition_activity(activity.activity_id, owner=role_owner(context),
                        expected_revision=activity.revision, state=State.COMPLETED,
                        operation_id="sleep-finish-short-" + digest([activity.activity_id, activity.revision]),
                        observed_at=at, checkpoint="中断后已达到本段睡眠目标", sleep=cleared)
                else:
                    work = replace(activity.work, due_at=at + timedelta(seconds=remaining))
                    changed = self.life.transition_activity(activity.activity_id, owner=role_owner(context),
                        expected_revision=activity.revision, state=State.ACTIVE,
                        operation_id="sleep-resume-" + digest([activity.activity_id, activity.revision]),
                        observed_at=at, checkpoint="清醒间隔结束，继续补足本段睡眠", work=work, sleep=cleared)
                resumed.append(changed)
        return tuple(resumed)

    def finish_due(self, context):
        # The caller has checked current role.life.run authority. Dream writing remains window-owned.
        at = self.tasks.now()
        with self.runtime.transaction():
            for activity in self.active(context, "sleep"):
                if activity.work.due_at > at:
                    continue
                receipt = activity.sleep or self._legacy_receipt(activity)
                elapsed = max(0, round((at - activity.updated_at).total_seconds()))
                total = min(172800, receipt.slept_seconds + elapsed)
                debt = self.profile(context, now=at)["sleep_debt_minutes"]
                extension = oversleep_extension(activity, sleep_debt_minutes=debt)
                if extension:
                    slept = replace(receipt, slept_seconds=total, oversleep_minutes=extension)
                    work = replace(activity.work, due_at=activity.work.due_at + timedelta(minutes=extension))
                    self.life.transition_activity(activity.activity_id, owner=role_owner(context),
                        expected_revision=activity.revision, state=State.ACTIVE,
                        operation_id="sleep-oversleep-" + digest([activity.activity_id, activity.revision]),
                        observed_at=at, checkpoint="睡眠延长片刻，稍后自然醒来", work=work, sleep=slept)
                    continue
                low, high = sleep_quality_range(receipt.workload_score, debt,
                                                 receipt.interruptions, receipt.early_interruptions)
                completed = replace(receipt, slept_seconds=total, wake_until=None,
                                     quality_min=low, quality_max=high)
                self.life.transition_activity(activity.activity_id, owner=role_owner(context),
                    expected_revision=activity.revision, state=State.COMPLETED,
                    operation_id="body-wake-" + digest([activity.activity_id, activity.revision]),
                    observed_at=at, checkpoint="睡眠结束；记录实际恢复与睡眠质量范围", sleep=completed)
