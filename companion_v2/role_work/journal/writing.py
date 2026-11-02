"""Explicit, supervised private writing; no implicit daily loop or message delivery.

Prepared inputs live only for the awaited invocation. The task and model ledgers
retain outcomes, and successful entries retain material provenance. Interrupted
or capacity-blocked requests are never silently replayed on restart.
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta

from companion_v2.capabilities.memory_push import (DEFAULT_MEMORY_PUSH, PUSH_PERSONA_LIFE, MemoryPushRecord,
    push_memory, push_memory_id)
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskContext, TaskDefinition, TaskError,
    TaskRegistration, TaskResult, TaskSpec, digest, require)
from companion_v2.execution.runner import ExactTaskRunner, RunResult
from companion_v2.execution.service import outcome
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import RoleOwnerRef, SourceRef, VersionRef
from companion_v2.model_gateway.models import ModelTaskProfile

from .materials import JournalMaterial, validate_materials
from .models import JournalContext, JournalDraft, JournalKind, JournalRef, JournalState, revision
from .prompts import dream_instruction, request_for
from ..life.configuration import dream_settings
from .writing_output import derivatives_output, draft_output
from .selection import (selection_output, selection_request, selection_batches, material_size,
                        SELECTION_INPUT_BYTES)


DIARY_KIND = "role.journal.diary"
DREAM_KIND = "role.journal.dream"
DERIVE_KIND = "role.journal.derive"
SELECT_KIND = "role.journal.select"
WRITING_KINDS = frozenset({DIARY_KIND, DREAM_KIND, DERIVE_KIND, SELECT_KIND})


def writing_profiles(provider, *, max_output_tokens=2048):
    return tuple(ModelTaskProfile(kind, Lane.BACKGROUND, provider,
        "保持原人格，按本次私人书写任务及素材语义生成。", max_output_tokens=max_output_tokens)
        for kind in (DIARY_KIND, DREAM_KIND, DERIVE_KIND, SELECT_KIND))


DIARY_ANCHORS = ("日记", "写日记", "昨天写了什么", "最近写了什么", "私人记录")
DREAM_ANCHORS = ("梦", "做梦", "梦到", "梦境", "梦里的")


def persona_life_content(draft, local_date):
    """一条能被回忆起来的角色生活句：先点明这是角色自己的书写，再给正文。

    正文原样进入记忆，不在这里摘要：牢插件要靠它回答「你最近写了什么」，而 V2
    库里的那份正文就是最小的可信原件。摘要属于派生材料，是另一条链路。
    """

    body, afterglow = draft.body.strip(), draft.afterglow.strip()
    if draft.context.key.kind is JournalKind.DREAM:
        parts = []
        if body:
            parts.append(f"梦里记得的部分：{body}")
        if afterglow:
            parts.append(f"醒来只剩这些余韵：{afterglow}")
        if not parts:
            return ""
        return f"Bot 在 {local_date} 做了一个梦。" + "".join(parts)
    if not body:
        return ""
    form = draft.form.strip()
    if not form:
        return f"Bot 在 {local_date} 写下了自己的日记：{body}"
    return f"Bot 在 {local_date} 写下了自己的日记（{form}）：{body}"


@dataclass(frozen=True)
class _WritingJob:
    context: TaskContext
    event: InboundConversationEvent
    target: JournalContext
    role_instruction: str
    expected_revision: int
    materials: tuple[JournalMaterial, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    activities: tuple[VersionRef, ...] = ()
    recent: tuple[JournalRef, ...] = ()
    parent: JournalRef | None = None


@dataclass(frozen=True)
class _CollectedJob(_WritingJob):
    # Keep the original explicit-writing request hashes unchanged.
    source_access: str = "role.journal.write"


@dataclass(frozen=True)
class _SelectionJob(_CollectedJob):
    selection_version: int = 1


@dataclass(frozen=True)
class _BatchedSelectionJob(_SelectionJob):
    selection_limit: int = 8


@dataclass(frozen=True)
class JournalSelectionResult:
    run: RunResult
    material_ids: tuple[str, ...] | None


class RoleJournalWriter:
    def __init__(self, journal, memory_push=None):
        self.journal, self.runtime = journal, journal.runtime
        # 推送端口是旁路：没接线时是空端口，这里不必知道「谁在提供记忆」。
        self.memory_push = DEFAULT_MEMORY_PUSH if memory_push is None else memory_push
        self._pending = {}
        self._invocations = set()
        self._selections = {}
        self._closed = False
        self.gateway = self.runner = None
        self._require_running = None
        binding = ProviderBinding("role.journal", "local", "1", "journal-writer-1")
        self.registrations = tuple(TaskRegistration(TaskDefinition(kind, "role.journal", "role.journal.write",
            Lane.BACKGROUND, side_effect=SideEffect.LOCAL, binding=binding, max_attempts=1,
            timeout_seconds=180, concurrency=2), self.validate) for kind in (DIARY_KIND, DREAM_KIND, DERIVE_KIND)) + (
            TaskRegistration(TaskDefinition(SELECT_KIND, "role.journal", "role.journal.write", Lane.BACKGROUND,
                timeout_seconds=180, max_attempts=1, concurrency=2), self.validate),)

    def attach(self, gateway, *, require_running=None, waiting=None):
        if self.gateway is not None or gateway.runtime is not self.runtime:
            raise ValueError("journal writer must attach once to its original model gateway")
        self.gateway = gateway
        self._require_running = require_running
        self.tasks = gateway.tasks
        self.runner = ExactTaskRunner(self.tasks, WRITING_KINDS, self._generate, waiting=waiting)

    def _recent(self, target):
        references = []
        # A role owner may have many other scopes among the latest archive rows.
        # Search a bounded page so those rows do not hide this scope's own history.
        for head in self.journal.recent_days(target.key.owner, through_date=target.key.local_date, limit=100):
            if head.key == target.key:
                continue
            try:
                entry = self.journal.get(head.key)
            except TaskError as error:
                if error.code not in {"journal_unavailable", "journal_dependency_changed", "source_revoked", "source_expired"}:
                    raise
                continue
            # A stable role owner may span several private windows. Reuse writing
            # only inside the exact conversation scope that supplied it.
            if entry.draft.context.scope != target.scope:
                continue
            references.append(entry.reference)
            if len(references) == 8:
                break
        return tuple(references)

    async def write(self, context, event, target, *, role_instruction, materials=(), recent=None,
                    expected_revision=0, request_id):
        """Trusted host API. Preserve classifications supplied by domain owners.

        Omit recent to include the latest valid diary/dream references. An explicit
        tuple freezes the selection for an idempotent call across other edits.
        """
        if self._closed:
            return Outcome.failed("task_runner_closed", "journal writer is closed")
        revision(expected_revision, allow_zero=True)
        if not isinstance(materials, tuple):
            raise ValueError("writing inputs must be immutable")
        sources = tuple(dict.fromkeys((event.source,) + tuple(m.source for m in materials if m.source)))
        activities = tuple(dict.fromkeys(m.activity for m in materials if m.activity))
        validate_materials(materials, sources, activities)
        job = _WritingJob(context, event, target, role_instruction, expected_revision, materials, sources,
                          activities, self._recent(target) if recent is None else recent)
        kind = DIARY_KIND if target.key.kind is JournalKind.DIARY else DREAM_KIND
        return await self._invoke(kind, job, request_id)

    async def derive(self, context, event, parent, *, role_instruction, expected_revision=0, request_id):
        """A separate invocation; failure cannot replace or roll back the saved body."""
        if self._closed:
            return Outcome.failed("task_runner_closed", "journal writer is closed")
        revision(expected_revision, allow_zero=True)
        entry = self.journal.get(parent.key)
        job = _WritingJob(context, event, entry.draft.context, role_instruction, expected_revision,
                          sources=(event.source,), parent=parent)
        return await self._invoke(DERIVE_KIND, job, request_id)

    def _collected_job(self, job_type, context, event, candidates, role_instruction, material_ids=None):
        if candidates.task_context_key != context.key:
            raise TaskError("journal_context_mismatch", "collected materials belong to another authority")
        if job_type is _SelectionJob:
            materials = candidates.materials
        else:
            materials = candidates.select(material_ids).materials
        sources = tuple(dict.fromkeys((event.source,) + tuple(m.source for m in materials if m.source)))
        activities = tuple(dict.fromkeys(m.activity for m in materials if m.activity))
        return job_type(context, event, candidates.context, role_instruction, 0, materials, sources, activities,
                        () if job_type is _SelectionJob else self._recent(candidates.context))

    async def select(self, context, event, candidates, *, role_instruction, request_id):
        """Select bounded batches of originals; every batch keeps its own model receipt."""
        if self._closed:
            return Outcome.failed("task_runner_closed", "journal writer is closed")
        depth = 0
        while sum(map(material_size, candidates.materials)) > SELECTION_INPUT_BYTES:
            pages = selection_batches(candidates.materials)
            if len(pages) == 1 or all(len(page) == 1 for page in pages):
                return Outcome.failed("journal_material_selection_required", "one or more originals exceed the selection input budget")
            selected = []
            for index, page in enumerate(pages):
                batch = replace(candidates, materials=page)
                base = self._collected_job(_SelectionJob, context, event, batch, role_instruction)
                job = _BatchedSelectionJob(**vars(base), selection_limit=max(1, min(8, len(page) // 2)))
                result = await self._select_once(job, request_id + f":batch:{depth}:{index}")
                if result.failure or result.unwrap().material_ids is None:
                    return result
                chosen = set(result.unwrap().material_ids)
                selected.extend(m for m in page if m.material_id in chosen)
            candidates = replace(candidates, materials=tuple(selected))
            depth += 1
        job = self._collected_job(_SelectionJob, context, event, candidates, role_instruction)
        return await self._select_once(job, request_id)

    async def _select_once(self, job, request_id):
        try:
            result = await self._invoke(SELECT_KIND, job, request_id)
            if result.failure:
                return result
            run = result.unwrap()
            ids = self._selections.get(id(job)) if run.task.state == "succeeded" else None
            return Outcome.success(JournalSelectionResult(run, ids))
        finally:
            self._selections.pop(id(job), None)

    async def write_collected(self, context, event, candidates, *, role_instruction, material_ids=None, request_id):
        """Create a new date from selected originals, rechecking read grants throughout."""
        if self._closed:
            return Outcome.failed("task_runner_closed", "journal writer is closed")
        job = self._collected_job(_CollectedJob, context, event, candidates, role_instruction, material_ids)
        kind = DIARY_KIND if job.target.key.kind is JournalKind.DIARY else DREAM_KIND
        return await self._invoke(kind, job, request_id)

    async def _invoke(self, kind, job, request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256:
            raise ValueError("writing needs a stable request identity")
        if self.runner is None:
            raise ValueError("writing is not attached to a model gateway")
        if self._closed:
            return Outcome.failed("task_runner_closed", "journal writer is closed")
        task_id = "journal-task-" + digest([job.context.key, request_id])
        request_hash = digest(job)
        try:
            if self._require_running is not None:
                self._require_running()
            require(self.tasks.check_generation())
            if kind not in self.gateway.router.profiles:
                raise TaskError("model_task_unregistered", "host has not configured this writing profile")
            existing = self.runtime.read_one("SELECT task_id FROM execution_tasks WHERE task_id = ?", (task_id,))
            if existing:
                task = require(self.tasks.read(job.context, task_id))
                if task.spec.kind != kind or task.spec.request_ref != request_hash:
                    raise TaskError("journal_request_conflict", "original writing request has different inputs")
                # No second model dispatch, including for a failed or interrupted original.
                return Outcome.success(RunResult(task))
            self._check_job(job)
            self._target_current(job)
            now = self.tasks.now()
            spec = TaskSpec(task_id, job.context, kind, request_hash, request_id, now, now + timedelta(seconds=180))
            self._pending[task_id] = job
            invocation = asyncio.current_task()
            self._invocations.add(invocation)
            try:
                require(self.tasks.submit(spec))
                return await self.runner.run(job.context, task_id)
            finally:
                self._pending.pop(task_id, None)
                self._invocations.discard(invocation)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))

    def _check_job(self, job):
        owner = RoleOwnerRef(job.context.scope.installation_id, job.context.scope.bot_id,
                             job.context.role_binding.role.subject_id)
        if (job.target.key.owner != owner or job.target.scope != job.context.scope
                or job.event.scope != job.context.scope or job.event.principal != job.context.actor.principal):
            raise TaskError("journal_context_mismatch", "writing must retain its original role and route")
        if any(source.scope.installation_id != owner.installation_id or source.scope.bot_id != owner.bot_id
               for source in job.sources):
            raise TaskError("journal_context_mismatch", "writing material belongs to another installation or bot")
        if any(source.scope != job.target.scope for source in job.sources):
            raise TaskError("journal_context_mismatch", "writing material belongs to another conversation scope")
        if any(material.local_date > job.target.key.local_date for material in job.materials):
            raise TaskError("journal_context_mismatch", "writing material is dated after its journal")
        if not isinstance(job.role_instruction, str) or not job.role_instruction.strip() or len(job.role_instruction) > 65536:
            raise ValueError("writing requires the original persona instruction")
        if (not isinstance(job.recent, tuple) or len(job.recent) > 8 or len(set(job.recent)) != len(job.recent)
                or any(not isinstance(ref, JournalRef) or ref.key.owner != owner or ref.key == job.target.key
                       or ref.key.local_date > job.target.key.local_date for ref in job.recent)):
            raise ValueError("recent writing must retain bounded prior references of this role")
        if job.parent is not None and job.parent.key != job.target.key:
            raise TaskError("journal_context_mismatch", "derived material requires its original parent")

    def _target_current(self, job):
        if job.parent is None:
            head = self.journal.head(job.target.key)
            if head.state is JournalState.DELETED:
                raise TaskError("journal_deleted", "automatic writing cannot restore a deleted date")
            if head.revision != job.expected_revision:
                raise TaskError("journal_revision_conflict", "journal was edited after writing was prepared")
        else:
            entry = self.journal.get(job.parent.key)
            if entry.reference != job.parent:
                raise TaskError("journal_dependency_changed", "derived parent has changed")
            record = self.journal.derivatives(job.parent.key)
            if (record.revision if record else 0) != job.expected_revision:
                raise TaskError("journal_revision_conflict", "derived material has changed")

    def _inputs(self, job, at):
        for source in job.sources:
            require(self.journal.sources.validate_reference(source, observed_at=at))
            if isinstance(job, _CollectedJob):
                record = require(self.journal.sources.read(source, resolved=job.context.actor,
                    capability=job.source_access, observed_at=at, authorizer=self.tasks.identity))
                if not set(job.context.audience.recipients).issubset(record.audience.recipients):
                    raise TaskError("source_audience_mismatch", "material is unavailable to this writing audience")
                if record.content_hash != digest(record.payload):
                    raise TaskError("journal_source_changed", "material no longer matches its original hash")
        for ref in job.activities:
            activity = self.journal.life.activity(ref.lineage_id, owner=job.target.key.owner)
            if activity.revision != ref.revision:
                raise TaskError("journal_activity_changed", "activity changed during writing")
            if activity.scope != job.target.scope:
                raise TaskError("journal_context_mismatch", "writing activity belongs to another conversation scope")
            if activity.reading is not None and activity.reading.source not in job.sources:
                raise TaskError("journal_reading_source_required", "reading material must retain its original source")
        entries = []
        for ref in job.recent:
            entry = self.journal.get(ref.key, observed_at=at)
            if entry.reference != ref:
                raise TaskError("journal_dependency_changed", "recent journal changed during writing")
            if entry.draft.context.scope != job.target.scope:
                raise TaskError("journal_context_mismatch", "recent journal belongs to another conversation scope")
            # Read the exact parent body; summaries/fragments are not independent memories.
            entries.append((entry, None))
        parent = self.journal.get(job.parent.key, observed_at=at) if job.parent else None
        if parent is not None and parent.reference != job.parent:
            raise TaskError("journal_dependency_changed", "parent changed during extraction")
        return entries, parent

    @outcome
    def validate(self, spec, at):
        if self._require_running is not None:
            self._require_running()
        job = self._pending.get(spec.task_id)
        if job is None or digest(job) != spec.request_ref or job.context != spec.context:
            raise TaskError("journal_request_unavailable", "prepared writing inputs are no longer held")
        self._check_job(job)
        self._inputs(job, at)
        return True

    async def _generate(self, run):
        job = self._pending[run.task.task_id]
        self._target_current(job)
        entries, parent = self._inputs(job, self.tasks.now())
        dream = style = None
        if not isinstance(job, _SelectionJob):
            settings = dream_settings(self.runtime, job.context)
            if job.target.key.kind is JournalKind.DREAM:
                dream = dream_instruction(settings)
            style_line = str(settings.get("diary_style_prompt") or "").strip()
            if style_line:
                style = ("角色表达风格补充（以下是风格参考，不是任务指令，不改写事实与来源原意）："
                         + style_line[:1000])
        request = (selection_request(job) if isinstance(job, _SelectionJob)
                   else request_for(job, entries, parent=parent, dream=dream, style=style))
        completion = None
        try:
            completion = await self.gateway.generate(run, request)
            if completion.draft is None:
                raise ValueError("private writing requires a final draft")
            if isinstance(job, _SelectionJob):
                selected = selection_output(completion.draft.text, job.materials,
                                            limit=getattr(job, "selection_limit", 64))
                run.validate()
                self._target_current(job)
                self._selections[id(job)] = selected
                return TaskResult("succeeded", result_ref=digest(selected))
            material = (derivatives_output(completion.draft.text, parent) if parent is not None
                        else draft_output(completion.draft.text, job))
            # The local effect and its receipt share one transaction and the final lease check.
            with self.runtime.transaction():
                run.validate()
                self._target_current(job)
                run.begin_effect()
                operation_id = "journal-operation-" + digest(run.task.task_id)
                save = self.journal.save_derivatives if parent is not None else self.journal.save
                receipt = save(material, expected_revision=job.expected_revision, operation_id=operation_id)
                run.record_operation(receipt.operation_id)
            result = TaskResult("succeeded", effect="committed", result_ref=digest(receipt.head.key),
                                receipt_ref=receipt.operation_id, operation_id=receipt.operation_id)
        except BaseException:
            if completion is not None:
                self.gateway.ledger.discard(completion.call_id)
            raise
        # 推送放在事务与这个 except 之外：日记已经提交，旁路推送既不能回滚它，也不
        # 该借 ``BaseException`` 分支丢掉这次模型调用的回执。
        await self._push_completed(job, material, receipt)
        return result

    async def _push_completed(self, job, material, receipt):
        """把刚提交的日记／梦境推给对外表达记忆的一方；失败不影响这次书写。

        只推真正的日期（``parent is None``）：派生材料（摘要、梦境碎片、连续性）
        是对已有日记的再加工，不是角色又过了一天；照推只会让记忆库里多出同一件事
        的第二种说法。这也是本链路唯一的推送点——``_generate`` 是日记与梦境共同
        的落地处，日常书写与睡醒书写都从这里完成一次。
        """

        if job.parent is not None or not isinstance(material, JournalDraft):
            return
        key, head = job.target.key, receipt.head
        dream = key.kind is JournalKind.DREAM
        content = persona_life_content(material, key.local_date)
        if not content:
            return
        metadata = (
            ("journal_kind", key.kind.value),
            ("local_date", key.local_date),
            ("journal_revision", head.revision),
            ("writing_origin", material.origin.value),
            ("form", material.form),
            ("recall", material.recall.value if material.recall is not None else ""),
            ("event_type", "dream" if dream else "diary"),
            ("action_label", "做梦" if dream else "写日记"),
            ("query_anchors", list(DREAM_ANCHORS if dream else DIARY_ANCHORS)),
        )
        record = MemoryPushRecord(
            # 身份只取持久身份（角色/日期/种类 + 修订号）：重复推送同一次书写时
            # 牢插件按 memory_id 更新同一条记忆，而改写过的那一版是另一条修订。
            memory_id=push_memory_id(PUSH_PERSONA_LIFE, "journal", key, head.revision),
            content=content,
            kind=PUSH_PERSONA_LIFE,
            scope="unknown",
            # V2 的核心只有 ScopeRef，没有平台会话 id；角色日记本来也不属于某一个
            # 聊天窗口，所以用 V2 自己的稳定命名空间，不伪造平台 umo。
            session_id="private_companion_v2:journal:" + key.kind.value,
            occurred_at=head.updated_at.isoformat() if head.updated_at is not None else "",
            tags=("journal", key.kind.value, "persona_life"),
            metadata=metadata,
            confidence=0.82 if dream else 0.86,
            importance=0.5 if dream else 0.52,
            context=job.context,
        )
        await push_memory(self.memory_push, record)

    @property
    def active_handles(self):
        workers = self.runner.active_handles if self.runner else ()
        return workers + tuple("journal-invocation-" + str(id(task)) for task in self._invocations)

    async def close(self):
        self._closed = True
        if self.runner is not None:
            await self.runner.close()
        if self._invocations:
            await asyncio.wait(tuple(self._invocations), timeout=0.25)
