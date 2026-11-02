"""Background self-review runs that propose reviewable guidance for one role.

A run inspects only the role's own observable behaviour inside one exact private
conversation -- confirmed replies and completed life/journal receipts -- and asks
the original role model what it might do differently. The output is a candidate:
it lands in `role_self_review_items` as `pending` and changes nothing until an
authenticated operator accepts an individual item. Accepted guidance always
carries a short expiry and is rechecked against its original evidence before it
may be injected into a later reply.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json

from companion_v2.conversation.foreground import (BackgroundPromptTrigger, PromptMaterial, PromptRequest,
    SealedForegroundContext)
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskDefinition, TaskError,
    TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.memory.activity_receipts import RoleReceiptReader
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from . import self_review
from .self_review_evidence import SelfReviewEvidenceCatalog, evidence_handles, window_document

KIND = "role.self_review"
OWNER = "role.self_review"
# The host selects the prompt output format; every owner that builds a
# PromptRequest must use a format registered by that boundary.
OUTPUT_FORMAT = "role_self_review"
CURSOR_PREFIX = "role.self_review.cursor.v1:"
# The status view may only report states the dashboard already labels. A restart
# keeps the cursor but loses the in-memory report, so a settled window has to be
# described as `succeeded` ("最近一次已产出") and never as `not_run`.
SETTLED_STATE = "succeeded"
# The newest non-success outcome is kept on disk together with its stable code:
# after a restart the dashboard can still say why the daily review is not moving.
OUTCOME_PREFIX = "role.self_review.outcome.v1:"
AUTO_APPLY_KINDS = frozenset({"try_next_time", "keep"})
AUTO_ACTOR = "system:self-review"
MIN_EVIDENCE = 2
# A deterministic output-contract failure will not fix itself by asking again a
# few minutes later: the same model with the same prompt returns the same shape.
# Back off for hours instead of burning a full request every five minutes.
OUTPUT_FAILURE_RETRY_SECONDS = 6 * 3600
TRANSIENT_FAILURE_RETRY_SECONDS = 5 * 60


def retry_delay(code):
    """Seconds to wait before retrying one failed self-review run."""
    return (OUTPUT_FAILURE_RETRY_SECONDS
            if code in {"self_review_output_invalid", "self_review_material_changed"}
            else TRANSIENT_FAILURE_RETRY_SECONDS)


def failure_code(value):
    """Stable diagnostic code for a failed run: an error or a stored reason."""
    code = getattr(value, "code", value)
    return code if type(code) is str and code else "self_review_failed"


TERMINAL_AVAILABLE = frozenset({"self_review_private_audience_required"})
PROMPT = (
    "只依据提供的证据，复盘最近一天这段私聊中有记录的角色工作、产出与回复：检查完成回执与送达回复是否支持结论、是否回应请求、表达不确定性、遵守来源和授权、是否把计划或提案说成完成，并回味相处时的语气温度、共情分寸与接话呼吸感（如哪里说得太生硬啰嗦、哪处贴心默契值得保持）。材料只覆盖本窗口留痕部分，不代表全天情况；不评价用户或第三方，不贴人格标签，不以一次失误推断长期问题，也不以材料缺失断言事情未发生。证据有限时减少建议或使用 no_change；每条指导引用实际 evidence_handle，next_action 具体、短而可执行（像角色私下对下次相处分寸的留心），expires_at 用带时区的短期 ISO 8601。输出仅是待审候选，不表示已修改提示、配置或记忆。"
)


def self_review_profile(provider, *, max_output_tokens=1024):
    return ModelTaskProfile(KIND, Lane.BACKGROUND, provider,
        "依据角色自己的近期行为证据，提出待审阅的自我复盘指导候选。",
        max_output_tokens=max_output_tokens)


def parse_output(raw, *, allowed_evidence, now):
    """Bridge the candidate contract onto this service's error vocabulary."""
    try:
        return self_review.parse_output(raw, allowed_evidence=allowed_evidence, now=now)
    except ValueError as error:
        raise TaskError("self_review_output_invalid", str(error) or "self-review output is invalid") from error


class SelfReviewService:
    page_size = 16
    scan_seconds = 86400
    empty_retry_seconds = 3600
    minimum_evidence = 1

    def __init__(self, runtime, *, window_hours=24):
        self.runtime = runtime
        self.operations = OperationRepository(runtime)
        self.window_hours = window_hours
        self.pending, self.committed, self.workers, self.results = {}, set(), {}, {}
        self.next_checks = {}
        self.cursor = 0
        self.closed = False
        self.registrations = (TaskRegistration(TaskDefinition(KIND, OWNER, "role.self_review.read",
            Lane.BACKGROUND, side_effect=SideEffect.LOCAL, binding=ProviderBinding(OWNER, "local", "1", "review-1"),
            timeout_seconds=180, max_attempts=1, concurrency=1), self.validate),)

    def attach(self, app):
        self.app, self.tasks, self.gateway = app, app.tasks, app.gateway
        if app.role_self_review_evidence is None:
            raise TaskError("self_review_owner_unavailable", "the conversation owner is not attached")
        self.evidence = app.role_self_review_evidence
        self.runner = ExactTaskRunner(self.tasks, frozenset({KIND}), self.generate,
                                      waiting=app.background_waiting)

    @staticmethod
    def identifier(context, request_key):
        return "self-review:" + digest([context.scope, context.role_binding.role, request_key])

    def _bound(self, context, instruction):
        bound = self.app.state_maintenance.bindings.get(context.key)
        if bound is None or bound[:2] != (context, instruction):
            raise TaskError("self_review_binding_changed", "the original private role is no longer bound")
        return bound[2]

    async def guard(self, context, instruction):
        check = self._bound(context, instruction)
        if check is not None:
            await check(context.scope, context.role_binding, instruction)

    def prepare(self, context, instruction, at):
        self.app.require_running()
        self._bound(context, instruction)
        window = self.evidence.collect(context, at)
        if len(window.evidence) < MIN_EVIDENCE:
            return None
        return {"context": context, "instruction": instruction, "at": at,
                "window": window, "material_hash": window.material_hash,
                "catalogue": self.evidence.catalogue(window),
                "window_document": window_document(window)}

    @outcome
    def validate(self, spec, at):
        self.app.require_running()
        job = self.pending.get(spec.task_id)
        if job is None or job["context"] != spec.context or job["request_key"] != spec.request_ref:
            raise TaskError("self_review_job_unavailable", "original self-review inputs are unavailable")
        self._bound(spec.context, job["instruction"])
        window = self.evidence.collect(spec.context, at)
        if window.material_hash != job["material_hash"]:
            raise TaskError("self_review_material_changed", "reviewed behaviour changed or was withdrawn")
        return True

    async def run(self, context, instruction):
        if self.closed:
            raise TaskError("self_review_closed", "self-review worker is stopped")
        if KIND not in self.gateway.router.profiles:
            raise TaskError("model_task_unregistered", "self-review needs its background profile")
        await self.guard(context, instruction)
        job = self.prepare(context, instruction, self.tasks.now())
        if job is None:
            return {"state": "no_reviewable_window"}
        request_key = digest([job["material_hash"], job["window_document"]])
        identifier = self.identifier(context, request_key)
        if self.operations.get(identifier).is_success:
            return {"state": "already_attempted", "task_id": identifier}
        with self.runtime.transaction():
            reservation = require(self.operations.reserve(OperationRef(identifier, OWNER, context.scope,
                VersionRef(identifier, 1), identifier),
                parameter_hash=digest([context.key, request_key]), observed_at=job["at"]))
            if reservation.replayed:
                return {"state": "already_attempted", "task_id": identifier}
            self.pending[identifier] = {**job, "request_key": request_key}
            try:
                require(self.tasks.submit(TaskSpec(identifier, context, KIND, request_key, identifier,
                    job["at"], job["at"] + timedelta(minutes=3))))
            except BaseException:
                self.pending.pop(identifier, None)
                raise
        try:
            result = require(await self.runner.run(context, identifier))
            report = {"state": result.task.state, "task_id": identifier, "code": result.task.reason}
            self.results[context.key] = report
            return report
        finally:
            self.pending.pop(identifier, None)
            self.committed.discard(identifier)

    async def _run_scheduled(self, context, instruction, cursor_key, at):
        try:
            result = await self.run(context, instruction)
        except Exception as error:
            # Remember the failure before re-raising it: the retry deadline and,
            # later, a restarted process both explain the window from this record.
            self._record_failure(context, "failed", failure_code(error), at)
            raise
        if result["state"] in {"succeeded", "completed", "already_attempted"}:
            self._advance_cursor(cursor_key, at)
        elif result["state"] == "no_reviewable_window":
            self.next_checks[context.key] = self.tasks.now() + timedelta(seconds=self.empty_retry_seconds)
        else:
            code = failure_code(result.get("code"))
            self._record_failure(context, "failed", code, at)
            self.next_checks[context.key] = self.tasks.now() + timedelta(seconds=retry_delay(code))
        return result

    async def generate(self, run):
        job = self.pending[run.task.task_id]
        context = job["context"]
        await self.guard(context, job["instruction"])
        run.validate()
        material = {"review_window": job["window_document"], "evidence": job["catalogue"]}
        request = PromptRequest(BackgroundPromptTrigger(job["request_key"], context.actor.principal,
            context.scope, job["at"]), job["instruction"], SealedForegroundContext(
                (PromptMaterial(OWNER, "role_self_review_evidence", encode(material)),), (), ()),
            PROMPT, context.role_binding, output_format=OUTPUT_FORMAT,
            response_format="json_object")
        completion = await self.gateway.generate(run, request)
        try:
            if completion.draft is None:
                raise TaskError("self_review_output_invalid", "self-review needs a complete response")
            output = parse_output(completion.draft.text,
                allowed_evidence=evidence_handles(job["window"]), now=self.tasks.now())
            await self.guard(context, job["instruction"])
            with self.runtime.transaction() as connection:
                run.validate()
                run.begin_effect()
                run_id = "self-review:" + digest([context.key, job["request_key"]])
                at = stamp(self.tasks.now())
                has_questions = any(item.kind == "ask_user" for item in output.guidance)
                connection.execute("""INSERT INTO role_self_review_runs VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, digest(self.evidence.receipts.life.owner(context)), digest(context.scope),
                     encode(job["window_document"]), job["material_hash"], output.summary,
                     "pending" if has_questions else "decided", at, None if has_questions else at))
                for ordinal, item in enumerate(output.guidance):
                    if item.kind == "no_change":
                        continue
                    automatic = item.kind in AUTO_APPLY_KINDS
                    state = "accepted" if automatic else "pending"
                    decided_at = at if automatic else None
                    decided_by = AUTO_ACTOR if automatic else None
                    decision_key = digest([run_id, ordinal, "automatic"]) if automatic else None
                    connection.execute("""INSERT INTO role_self_review_items VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id + ":item:" + str(ordinal), run_id, digest(self.evidence.receipts.life.owner(context)),
                         digest(context.scope), item.kind, item.observation, item.next_action,
                         item.confidence, encode(sorted(item.evidence_handles)), stamp(item.expires_at),
                         state, at, decided_at, decided_by, decision_key, ordinal))
                require(self.operations.transition(run.task.task_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=self.tasks.now()))
                self.committed.add(run.task.task_id)
                run.record_operation(run.task.task_id)
            self.app.notify()
            return TaskResult("succeeded", effect="committed", result_ref=run_id,
                receipt_ref=run.task.task_id, operation_id=run.task.task_id)
        except BaseException:
            if run.task.task_id not in self.committed:
                self.gateway.ledger.discard(completion.call_id)
            raise

    def _metadata_key(self, prefix, context):
        return prefix + digest([self.evidence.receipts.life.owner(context), context.scope])

    def _cursor(self, context):
        """Persisted review cursor: the last closed window this role was reviewed over."""
        key = self._metadata_key(CURSOR_PREFIX, context)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            return key, None
        value = json.loads(row["value"])
        if type(value) is not dict or set(value) != {"closed_at"} or type(value["closed_at"]) is not str:
            raise TaskError("self_review_cursor_invalid", "self-review cursor is invalid")
        try:
            closed_at = datetime.fromisoformat(value["closed_at"])
        except ValueError:
            raise TaskError("self_review_cursor_invalid", "self-review cursor is invalid") from None
        return key, closed_at

    def _last_failure(self, context):
        """Newest failed or blocked run recorded before this process started.

        The check covers the required fields only, so a record written by a later
        version (extra keys) still explains the window; otherwise one added field
        would silently degrade every older record back to the bare cursor view.
        """
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (self._metadata_key(OUTCOME_PREFIX, context),))
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
            if type(value) is not dict:
                raise ValueError
            state, code, at_raw = value.get("state"), value.get("code"), value.get("at")
            if (state not in ("failed", "blocked") or type(code) is not str or not code
                    or type(at_raw) is not str):
                raise ValueError
            at = datetime.fromisoformat(at_raw)
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise TaskError("self_review_outcome_invalid", "self-review outcome is invalid") from None
        return at, state, code

    def _record_failure(self, context, state, code, at):
        """Keep the newest non-success outcome so a restart can still explain it."""
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (self._metadata_key(OUTCOME_PREFIX, context),
                 encode({"state": state, "code": code, "at": stamp(at)})))

    def _advance_cursor(self, key, closed_at):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, encode({"closed_at": stamp(closed_at)})))

    def due(self, context, at):
        """True when this role's window has moved on since its last review."""
        _, closed_at = self._cursor(context)
        return closed_at is None or at - closed_at >= timedelta(seconds=self.scan_seconds)

    def status(self, context):
        """Bounded, read-only view of this role's daily review state.

        Only this process' reports are in memory; the cursor and the last
        recorded failure outlive it. A restarted process therefore restores the
        newest of the two instead of pairing a persisted ``last_run_at`` with a
        fresh ``not_run``.
        """
        at = self.tasks.now()
        report = dict(self.results.get(context.key) or {})
        try:
            _, closed_at = self._cursor(context)
        except TaskError:
            closed_at = None
        state, code = report.get("state", "not_run"), report.get("code")
        if not report:
            try:
                failure = self._last_failure(context)
            except TaskError:
                failure = None
            if failure is not None and (closed_at is None or failure[0] > closed_at):
                _, state, code = failure
            elif closed_at is not None:
                state = SETTLED_STATE
        worker = self.workers.get(context.key)
        try:
            due = self.due(context, at)
        except TaskError:
            due = False
        return {
            "state": state,
            "code": code,
            "last_run_at": stamp(closed_at) if closed_at is not None else None,
            "due": due,
            "worker": bool(worker is not None and not worker.done()),
            "next_check_at": stamp(self.next_checks[context.key])
                if context.key in self.next_checks else None,
        }

    def advance(self):
        if self.closed:
            return
        for key, worker in tuple(self.workers.items()):
            if worker.done():
                del self.workers[key]
                if not worker.cancelled():
                    error = worker.exception()
                    if error:
                        code = failure_code(error)
                        self.results[key] = {"state": "failed", "code": code}
                        self.next_checks[key] = self.tasks.now() + timedelta(seconds=retry_delay(code))
        if self.workers or KIND not in self.gateway.router.profiles:
            return
        bindings = tuple(self.app.state_maintenance.bindings.values())
        if not bindings:
            self.next_checks.clear()
            return
        for _ in range(min(16, len(bindings))):
            context, instruction, _ = bindings[self.cursor % len(bindings)]
            self.cursor += 1
            at = self.tasks.now()
            if context.scope.kind is not ScopeKind.PRIVATE or self.next_checks.get(context.key, at) > at:
                continue
            self.next_checks[context.key] = at + timedelta(seconds=30)
            try:
                if not self.due(context, at):
                    continue
                job = self.prepare(context, instruction, at)
                if job is None:
                    self.next_checks[context.key] = at + timedelta(hours=1)
                    continue
                key, _ = self._cursor(context)
                self.workers[context.key] = asyncio.create_task(
                    self._run_scheduled(context, instruction, key, at), name="companion:self-review")
                self.workers[context.key].add_done_callback(lambda done: self.app.notify())
                return
            except TaskError as error:
                self.results[context.key] = {"state": "blocked", "code": error.code}
                self._record_failure(context, "blocked", error.code, at)

    def cancel_running(self):
        for worker in self.workers.values():
            worker.cancel()

    @property
    def active_handles(self):
        return self.runner.active_handles + tuple("self-review:" + key for key, worker in
            self.workers.items() if not worker.done())

    async def close(self):
        self.closed = True
        self.cancel_running()
        await self.runner.close()
        if self.workers:
            await asyncio.wait(tuple(self.workers.values()), timeout=0.25)
