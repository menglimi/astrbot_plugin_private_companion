"""Core-only, versioned management of one original private role and participant."""

from dataclasses import replace
from datetime import date
import json

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, RoleOwnerRef, ScopeKind, SourceRef, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.sources.models import SourceRecord
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.role_work.models import (RoleActivity, RoleActivityState as State, RoleArtifact,
    RoleArtifactKind, RoleArtifactJournalMeaning)
from companion_v2.role_work.work_models import RoleWorkSpec
from companion_v2.role_work.journal import codec
from companion_v2.role_work.journal.models import (DreamRecall, JournalContext, JournalDraft, JournalKey,
    JournalKind, JournalOrigin, JournalState)

from .configuration import DEFAULTS, GROUP, OWNER as CONFIGURATION_OWNER, target_for, values


class RoleLifeManagement:
    def __init__(self, app, context):
        self.app, self.context, self.runtime = app, context, app.runtime
        self.life = app.life_runtime.for_context(context)
        self.policy = ObjectAccessPolicy(self.runtime)
        self.operations = OperationRepository(self.runtime)
        self.owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id, context.role_binding.role.subject_id)

    def authorize(self, operator):
        c, now = self.context, self.app.now()
        if c.scope.kind is not ScopeKind.PRIVATE or c.audience.recipients != (c.actor.subject,):
            raise TaskError("life_scope_invalid", "select one private participant")
        current = require(self.app.tasks.identity.resolve_authenticated(c.actor.principal, observed_at=now))
        binding = require(self.app.tasks.identity.resolve_role(c.scope, observed_at=now))
        if (current.subject != c.actor.subject or current.binding_id != c.actor.binding_id
                or current.binding_revision != c.actor.binding_revision or binding != c.role_binding):
            raise TaskError("life_target_changed", "selected participant or role binding changed")
        return require(self.policy.scope_access(operator, scope=c.scope, action="role.life.manage",
                                               observed_at=now, core_only=True))

    def token(self, operator, access, identifier="life", revision=0):
        return digest(["life-management.v1", operator.subject, operator.binding_id, operator.binding_revision,
                       self.context.key, access.policy_revision, identifier, revision])

    def _activity(self, identifier, *, current=True):
        try:
            a = self.app.role_life.activity(identifier, owner=self.owner)
        except KeyError:
            raise TaskError("life_activity_unavailable", "activity is unavailable") from None
        if a.scope != self.context.scope or a.work is None or current and a.work.context_key != self.context.key:
            raise TaskError("life_context_changed", "activity belongs to another authority")
        return a

    def _activities(self):
        cursor = None
        while True:
            page = self.app.role_life.activities_for_role(self.owner, limit=100, after_id=cursor)
            for activity in page:
                if activity.scope == self.context.scope and activity.work:
                    yield activity
            if len(page) < 100:
                return
            cursor = page[-1].activity_id

    def _project_activity(self, a, operator, access):
        unavailable = None
        if a.work.source:
            check = self.app.role_journal.sources.validate_reference_for_audience(a.work.source,
                recipient=self.context.actor.subject, observed_at=self.app.now())
            unavailable = check.failure.code if check.failure else None
        task = None
        if a.work.kind == "reading":
            task_id = "reading-task-" + digest([a.work.context_key, a.activity_id, a.work.request_id])
            row = self.runtime.read_one("SELECT state, record_json FROM execution_tasks WHERE task_id = ?", (task_id,))
            unknown = self.runtime.read_one("SELECT 1 FROM model_calls WHERE task_id = ? AND state IN ('reserved','dispatched','unknown')", (task_id,))
            if row:
                task = {"task_id": task_id, "state": "unknown" if unknown else row["state"],
                        "reason": json.loads(row["record_json"])["reason"]}
        return {"activity_id": a.activity_id, "revision": a.revision, "kind": a.work.kind,
            "current_context": a.work.context_key == self.context.key,
            "state": a.state.value, "title": a.description, "checkpoint": a.checkpoint,
            "started_at": a.started_at.isoformat(), "updated_at": a.updated_at.isoformat(),
            "due_at": a.work.due_at.isoformat() if a.work.due_at else None,
            "start_offset": a.work.start_offset, "end_offset": a.work.end_offset,
            "sleep": ({"kind": a.sleep.kind, "target_minutes": a.sleep.target_minutes,
                "slept_seconds": a.sleep.slept_seconds, "interruptions": a.sleep.interruptions,
                "early_interruptions": a.sleep.early_interruptions,
                "quality_range": [a.sleep.quality_min, a.sleep.quality_max],
                "wake_until": a.sleep.wake_until.isoformat() if a.sleep.wake_until else None,
                "oversleep_minutes": a.sleep.oversleep_minutes} if a.sleep else None),
            "reflection": a.reading.reflection if a.reading and unavailable is None else None,
            "unavailable_reason": unavailable, "task": task,
            "view_token": self.token(operator, access, a.activity_id, a.revision)}

    def _journal(self, key, operator, access):
        journal = self.app.role_journal
        head = journal.head(key)
        if head.state is JournalState.ABSENT:
            return None
        row = self.runtime.read_one("SELECT context_json FROM role_journals WHERE entry_key = ?", (digest(key),))
        target = codec.context(json.loads(row["context_json"]))
        if target.scope != self.context.scope:
            return None
        data = {"kind": key.kind.value, "local_date": key.local_date, "revision": head.revision,
                "state": head.state.value, "body": None, "afterglow": None, "recall": None,
                "summary": None, "dream_fragments": [], "unavailable_reason": None,
                "view_token": self.token(operator, access, digest(key), head.revision)}
        if head.state is JournalState.ACTIVE:
            try:
                entry = journal.get(key)
                for source in entry.draft.sources:
                    require(journal.sources.validate_reference_for_audience(source,
                        recipient=self.context.actor.subject, observed_at=self.app.now()))
                data.update(body=entry.draft.body, afterglow=entry.draft.afterglow,
                            recall=entry.draft.recall.value if entry.draft.recall else None)
                derived = journal.derivatives(key)
                if derived:
                    data.update(summary=derived.material.summary, dream_fragments=derived.material.dream_fragments)
            except TaskError as error:
                data["unavailable_reason"] = error.code
        return data

    def status(self, operator, *, before=None, after_activity=None):
        access = self.authorize(operator)
        config = require(self.app.configuration.read(operator, target=target_for(self.context), group_id=GROUP,
                                                      observed_at=self.app.now()))
        # Cursor pages include unavailable/deleted records; a damaged source never hides the rest of the archive.
        if before is not None:
            date.fromisoformat(before)
        if after_activity is not None and (not isinstance(after_activity, str) or len(after_activity) > 512):
            raise ValueError("invalid activity cursor")
        rows = self.runtime.read_all("""SELECT context_json FROM role_journals WHERE owner_key = ?
            AND local_date IN (SELECT DISTINCT local_date FROM role_journals WHERE owner_key = ?
                AND (? IS NULL OR local_date < ?) ORDER BY local_date DESC LIMIT 50)
            ORDER BY local_date DESC, kind""", (digest(self.owner), digest(self.owner), before, before))
        journals = []
        for row in rows:
            target = codec.context(json.loads(row["context_json"]))
            if target.scope == self.context.scope:
                journals.append(self._journal(target.key, operator, access))
        activities, cursor = [], after_activity
        while len(activities) < 30:
            page = self.app.role_life.activities_for_role(self.owner, limit=30, after_id=cursor)
            activities.extend(a for a in page if a.scope == self.context.scope and a.work)
            if len(page) < 30:
                cursor = None
                break
            cursor = page[-1].activity_id
        receipts = []
        for key, result in self.life.results.items():
            if result is None:
                continue
            if result.failure:
                receipts.append({"key": key, "state": "failed", "code": result.failure.code})
            else:
                value = result.unwrap()
                task = getattr(value, "task", None)
                receipts.append({"key": key, "state": getattr(value, "state", None) or (task.state if task else "complete"),
                    "operation_id": getattr(value, "operation_id", None),
                    "code": getattr(value, "failure_code", None)})
        # Trigger receipts survive restarts and contain no journal body.
        persisted = self.runtime.read_all("""SELECT operation_id, state FROM operations
            WHERE owner = 'role.journal.trigger' AND idempotency_key = operation_id || ':' || ?
            ORDER BY created_at DESC LIMIT 40""", (self.context.key,))
        for row in persisted:
            operation = require(self.operations.get(row["operation_id"]))
            if (operation.operation.scope == self.context.scope
                    and operation.operation.idempotency_key == row["operation_id"] + ":" + self.context.key):
                receipt = self.app.journal_triggers._receipt(self.context, operation)
                receipts.append({"key": row["operation_id"], "state": receipt.state,
                    "code": receipt.failure_code, "operation_id": row["operation_id"],
                    "retryable": receipt.retryable})
        # 只要还有一次书写没有结论（unknown/处理中/已受理），就不该再让人从页面
        # 选“失败的书写”重试：那会把同一天的第二次提交送进同一条链。
        journal_unresolved = any(
            item.get("retryable") is not None and item.get("state") in ("unknown", "uncertain", "accepted")
            for item in receipts)
        daily = self.app.journal_schedule._schedules.get(self.life.schedule_id)
        sleep_schedule = self.app.life_runtime.sleep_schedule_status(self.life, config)
        return {"role_id": self.owner.persona_id, "participant": self.context.scope.native_scope_id,
            "host_state": self.app.state, "config": values(config), "edit_token": config.edit_token,
            "view_token": self.token(operator, access), "failure_code": self.life.failure_code,
            "next_diary_at": daily.next_at.isoformat() if daily else None,
            "next_sleep_at": sleep_schedule["next_sleep_at"], "sleep_schedule": sleep_schedule,
            "body_sleep": self.app.role_state.body_clock.read(self.context),
            "activities": [self._project_activity(a, operator, access) for a in activities],
            "next_activity": cursor, "journals": journals,
            "next_date": codec.context(json.loads(rows[-1]["context_json"])).key.local_date
                if len({codec.context(json.loads(r["context_json"])).key.local_date for r in rows}) == 50 else None,
            "receipts": receipts[-60:], "running": list(self.life._workers),
            "journal_unresolved": journal_unresolved}

    def sleep_status(self, operator, *, access=None):
        """Small shared-schedule projection for role-state management views."""
        if access is None:
            self.authorize(operator)
        config = require(self.app.configuration.read(operator, target=target_for(self.context), group_id=GROUP,
                                                      observed_at=self.app.now()))
        return {"sleep_schedule": self.app.life_runtime.sleep_schedule_status(self.life, config),
                "body_sleep": self.app.role_state.body_clock.read(self.context)}

    def operation_id(self, prefix, operator, key):
        # Preserve pre-partitioning retries only in their original private scope.
        legacy = prefix + digest([operator.subject, key])
        receipt = self.operations.get(legacy)
        if prefix != "life-config-" and receipt.is_success and receipt.unwrap().operation.scope == self.context.scope:
            return legacy
        allowed_owners = (CONFIGURATION_OWNER,
                          *getattr(self.app.configuration._owners.get(CONFIGURATION_OWNER),
                                   "legacy_owner_ids", ()))
        if (receipt.is_success and receipt.unwrap().operation.scope == self.context.scope
                and receipt.unwrap().operation.owner in allowed_owners):
            return legacy
        return prefix + digest([operator.subject, self.context.key, key])

    def configuration_operation_owner(self, operation_id):
        """Use a retired owner only to replay its exact persisted receipt."""
        owner = self.app.configuration._owners.get(CONFIGURATION_OWNER)
        legacy_ids = tuple(getattr(owner, "legacy_owner_ids", ()))
        receipt = self.operations.get(operation_id)
        if (receipt.is_success and receipt.unwrap().operation.scope == self.context.scope
                and receipt.unwrap().operation.owner in legacy_ids):
            return receipt.unwrap().operation.owner
        return CONFIGURATION_OWNER

    def configure(self, operator, data):
        self.authorize(operator)
        changes = data["changes"]
        if type(changes) is not dict or set(changes) - set(DEFAULTS):
            raise ValueError("unknown routine fields")
        target = target_for(self.context)
        current = require(self.app.configuration.read(operator, target=target, group_id=GROUP, observed_at=self.app.now()))
        normalized = {GROUP + "." + k: v for k, v in changes.items()}
        preview = require(self.app.configuration.preview_changes(operator, target=target, group_id=GROUP,
            changes=normalized, observed_at=self.app.now()))
        values(replace(current, fields=preview.after))
        op_id = self.operation_id("life-config-", operator, data["operation_key"])
        operation = OperationRef(op_id, self.configuration_operation_owner(op_id), self.context.scope,
                                 VersionRef(op_id, 1), op_id)
        with self.runtime.transaction():
            receipt = require(self.app.configuration.patch(operator, target=target, group_id=GROUP,
                operation=operation, expected_edit_token=data["edit_token"], changes=normalized, observed_at=self.app.now()))
            if not receipt.replayed and "enabled" in changes:
                self._grants(changes["enabled"], op_id)
        self.app.notify()
        return {"operation_id": op_id, "replayed": receipt.replayed}

    def _grants(self, enabled, op_id):
        identity = self.app.tasks.identity
        prefix = "life-grant-" + digest(self.context.key) + ":"
        for grant in identity.snapshot().grants:
            if (grant.grant_id.startswith("life-grant-") and grant.scope == self.context.scope
                    and grant.subject == self.context.actor.subject and grant.state is GrantState.ACTIVE):
                identity.revoke_grant(grant.grant_id, revoked_at=self.app.now(), expected_revision=identity.revision)
        if enabled:
            for action in ("role.life.run", "role.life.read", "role.journal.write", "role.reading.record"):
                identity.issue_grant(grant_id=prefix + digest([op_id, action]), subject=self.context.actor.subject,
                    scope=self.context.scope, capability=action, tier=AuthorizationTier.STANDARD,
                    valid_from=self.app.now(), expected_revision=identity.revision)

    def sync_configuration_grant(self, operator, enabled, receipt):
        """Keep the generic settings editor on the same grant path as this manager."""
        now = self.app.now()
        require(self.policy.scope_access(operator, scope=self.context.scope, action="configuration.write",
                                         observed_at=now, core_only=True))
        self.app.tasks._context(self.context, now)
        current = require(self.app.configuration.read(operator, target=target_for(self.context), group_id=GROUP,
                                                      observed_at=now))
        if current.values[GROUP + ".enabled"] is not enabled:
            return

        identity, context = self.app.tasks.identity, self.context
        prefix = "life-grant-" + digest(context.key) + ":"
        actions = ("role.life.run", "role.life.read", "role.journal.write", "role.reading.record")
        if not receipt["replayed"]:
            self._grants(enabled, receipt["operation_id"])
            return

        if not enabled:
            for grant in identity.snapshot().grants:
                if (grant.grant_id.startswith(prefix) and grant.scope == context.scope
                        and grant.subject == context.actor.subject and grant.state is GrantState.ACTIVE):
                    identity.revoke_grant(grant.grant_id, revoked_at=now, expected_revision=identity.revision)
            return

        grants = identity.snapshot().grants
        for action in actions:
            grant_id = prefix + digest([receipt["operation_id"], action])
            if any(grant.grant_id == grant_id for grant in grants):
                continue
            if any(grant.grant_id.startswith(prefix) and grant.scope == context.scope
                   and grant.subject == context.actor.subject and grant.capability == action
                   and grant.state is GrantState.ACTIVE for grant in grants):
                continue
            identity.issue_grant(grant_id=grant_id, subject=context.actor.subject, scope=context.scope,
                capability=action, tier=AuthorizationTier.STANDARD, valid_from=now,
                expected_revision=identity.revision)

    def _add_text(self, data, op_id):
        text, title = data["text"], data["title"]
        if (type(text) is not str or not text.strip() or len(text) > 262144
                or type(title) is not str or not title.strip() or len(title) > 160):
            raise ValueError("provide a title and 1..262144 characters of original text")
        now = self.app.now()
        source = SourceRef("book-" + digest(op_id), GROUP, self.context.scope, VersionRef(op_id, 1), now)
        require(self.app.role_journal.sources.publish(SourceRecord(source, self.context.audience, digest(text), text), observed_at=now))
        identifiers = []
        # Every character belongs to one actual input span. No passage is silently omitted.
        for start in range(0, len(text), 6000):
            end = min(start + 6000, len(text))
            if not text[start:end].strip():
                continue
            identifier = "book-part-" + digest([op_id, start])
            work = RoleWorkSpec("reading", self.context.key, identifier, source, digest(text), start, end)
            self.app.role_life.add_activity(RoleActivity(identifier, self.owner.persona_id, self.context.scope,
                now, State.PLANNED, description=f"{title} · {start + 1}–{end} / {len(text)} 字符", work=work))
            identifiers.append(identifier)
        self.app.role_life.add_artifact(RoleArtifact("book-" + digest(op_id), identifiers[0], RoleArtifactKind.TEXT,
            source, now, journal_meaning=RoleArtifactJournalMeaning.READING), owner=self.owner)
        return {"activity_ids": identifiers}

    async def command(self, operator, data):
        try:
            access = self.authorize(operator)
            action, key = data["action"], data["operation_key"]
            if type(key) is not str or not key.strip() or len(key) > 128:
                raise ValueError("stable operation key required")
            if action == "configure":
                return Outcome.success(self.configure(operator, data))
            identifier, revision = data.get("activity_id", "life"), data.get("expected_revision", 0)
            if type(revision) is not int or revision < 0:
                raise ValueError("original revision required")
            journal_target = None
            if action in ("edit", "delete", "restore", "derive"):
                journal_target = JournalContext(JournalKey(self.owner, JournalKind(data["kind"]), data["local_date"]),
                    self.context.scope, data.get("timezone", "Asia/Shanghai"))
                identifier = digest(journal_target.key)
            if data.get("view_token") != self.token(operator, access, identifier, revision):
                raise TaskError("life_view_stale", "refresh before applying this change")
            op_id = self.operation_id("life-command-", operator, key)
            if action in ("diary", "dream", "derive"):
                await self.life.preflight()
                if action == "diary":
                    result = await self.life.diary(local_date=data.get("local_date"), retry_of=data.get("retry_of"), request_id=key)
                elif action == "dream":
                    activity = self._activity(identifier)
                    if activity.work.kind != "sleep" or activity.state is not State.COMPLETED or activity.revision != revision:
                        raise TaskError("life_sleep_invalid", "select the unchanged completed sleep")
                    result = await self.life.dream(activity, retry_of=data.get("retry_of"), request_id=key)
                else:
                    entry = self.app.role_journal.get(journal_target.key)
                    if entry.draft.context.scope != self.context.scope or entry.reference.revision != revision:
                        raise TaskError("journal_revision_conflict", "journal changed")
                    result = await self.life.derive(entry.draft.context, self.life.event("derive", "独立提取私人书写索引。"), automatic=False)
                    result = result or Outcome.success({"state": "already_derived"})
                self.life.results[op_id] = result
                return result
            if action in ("sleep", "read", "wake"):
                await self.life.preflight()
                self.authorize(operator)
            operation = OperationRef(op_id, "role.life.management", self.context.scope, VersionRef(op_id, 1), op_id)
            with self.runtime.transaction():
                reserved = require(self.operations.reserve(operation, parameter_hash=digest(data), observed_at=self.app.now()))
                if reserved.replayed:
                    return Outcome.success({"operation_id": op_id, "state": reserved.state.value, "replayed": True})
                result = {}
                if action == "add_text":
                    result = self._add_text(data, op_id)
                elif action == "sleep":
                    self.life.refresh()
                    a = self.life.start_sleep(op_id, data["minutes"])
                    result = {"activity_id": a.activity_id}
                elif action == "read":
                    a = self.life.start_reading(identifier, expected_revision=revision, request_id=op_id)
                    result = {"activity_id": a.activity_id}
                elif action == "wake":
                    a = self.life.wake(identifier, expected_revision=revision, operation_id=op_id + ":wake")
                    result = {"activity_id": a.activity_id}
                elif action in ("cancel", "withdraw"):
                    a = self._activity(identifier, current=False)
                    if a.revision != revision:
                        raise TaskError("life_revision_conflict", "activity changed")
                    if action == "withdraw":
                        if a.work.source is None:
                            raise ValueError("only original reading text can be withdrawn")
                        row = self.runtime.read_one("SELECT governance_revision FROM sources WHERE source_id = ?", (a.work.source.source_id,))
                        require(self.app.role_journal.sources.revoke(a.work.source,
                            expected_governance_revision=row["governance_revision"], observed_at=self.app.now()))
                        for related in self._activities():
                            if (related.activity_id != identifier and related.work.source == a.work.source
                                    and related.state not in (State.COMPLETED, State.CANCELLED)):
                                self.app.role_life.transition_activity(related.activity_id, owner=self.owner,
                                    expected_revision=related.revision, state=State.CANCELLED,
                                    operation_id=op_id + ":" + digest(related.activity_id), observed_at=self.app.now())
                                self.app.life_runtime.cancel_activity(related.activity_id)
                    if a.state not in (State.COMPLETED, State.CANCELLED):
                        if action == "cancel" and a.work.kind == "sleep":
                            self.app.role_state.body_clock.finish(self.context, a, operation_id=op_id + ":cancel",
                                observed_at=self.app.now(), checkpoint="睡眠被手动结束并保留实际睡眠时长",
                                state=State.CANCELLED)
                        else:
                            self.app.role_life.transition_activity(identifier, owner=self.owner, expected_revision=revision,
                                state=State.CANCELLED, operation_id=op_id + ":cancel", observed_at=self.app.now())
                    self.app.life_runtime.cancel_activity(identifier)
                elif action in ("edit", "delete", "restore"):
                    old = self._journal(journal_target.key, operator, access)
                    if old is None or old["revision"] != revision:
                        raise TaskError("journal_revision_conflict", "journal changed or belongs to another scope")
                    if action == "delete":
                        self.app.role_journal.delete(journal_target, expected_revision=revision, operation_id=op_id + ":journal")
                    else:
                        if action == "edit":
                            original = self.app.role_journal.get(journal_target.key).draft
                            draft = replace(original, body=data["body"], afterglow=data.get("afterglow", original.afterglow),
                                            origin=JournalOrigin.MANUAL)
                        else:
                            draft = JournalDraft(journal_target, body=data["body"], afterglow=data.get("afterglow", ""),
                                origin=JournalOrigin.MANUAL,
                                recall=DreamRecall(data.get("recall", "fragments")) if journal_target.key.kind is JournalKind.DREAM else None)
                        method = self.app.role_journal.restore if action == "restore" else self.app.role_journal.save
                        method(draft, expected_revision=revision, operation_id=op_id + ":journal")
                else:
                    raise ValueError("unknown life action")
                require(self.operations.transition(op_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=self.app.now()))
            self.app.notify()
            return Outcome.success({"operation_id": op_id, **result})
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except (KeyError, ValueError, TypeError):
            return Outcome.failed("life_command_invalid", "invalid command or stale activity revision; refresh and check the selected record")
