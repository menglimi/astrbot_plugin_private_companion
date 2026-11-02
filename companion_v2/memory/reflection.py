"""Incremental, durable memory extraction through the shared task/model services."""

import asyncio
from datetime import timedelta
import json
import logging

from companion_v2.conversation.foreground import PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import (Lane, ProviderBinding, QUEUED, SideEffect, TaskContext, TaskDefinition,
    TaskError, TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.sources.models import SourceRecord
from companion_v2.relationships import anniversaries

from . import codec
from .configuration import target_for
from .models import AtomRef, owner_key, text, timestamp
from .prompts import REFLECTION_INSTRUCTION
from .reflection_output import REFLECTION_SCHEMA, proposal_for_repair
from . import reflection_recovery as recovery
from .reflection_selection import ReflectionSelection
from .reflection_jobs import describe_job
from .group.access import owner_key as group_memory_owner_key
from companion_v2.conversation.addressing import ADDRESSING_REFLECTION_INSTRUCTION
from .group.hits import DEFAULT_GROUP_SAFETY_POLICY
logger = logging.getLogger(__name__)


KIND = "memory.reflect"


def reflection_profile(provider, *, max_output_tokens=4096):
    return ModelTaskProfile(KIND, Lane.BACKGROUND, provider,
        "按原人格整理已授权资料，有来源地更新认识，允许不修改。", max_output_tokens=max_output_tokens)


class MemoryReflection:
    def __init__(self, runtime):
        self.runtime = runtime
        self.memory = self.gateway = self.runner = None
        self._require_running = None
        self._closed, self._invocations = False, set()
        self.selection = ReflectionSelection(self)
        binding = ProviderBinding("memory.extract", "local", "1", "memory-extract-1")
        # Model routing allows 600 seconds per provider, with one fallback.
        # The task must leave room for both attempts and its local checkpoint.
        self.registrations = (TaskRegistration(TaskDefinition(KIND, "memory", "memory.reflect", Lane.BACKGROUND,
            side_effect=SideEffect.LOCAL, binding=binding, timeout_seconds=1230, max_attempts=1, concurrency=1), self.validate),)

    def attach(self, memory, gateway, *, require_running=None):
        if self.gateway is not None or gateway.runtime is not self.runtime or memory.runtime is not self.runtime:
            raise ValueError("memory reflection must attach once to its original runtime")
        self.memory, self.gateway, self.tasks = memory, gateway, gateway.tasks
        self._require_running = require_running
        # Retain capacity refusals: MemoryReflectionJobs own this row's
        # convergence by ID (they re-queue on the refusal code and retry), so a
        # momentarily full lane must not retire the reservation.
        # `runner.CAPACITY_REFUSALS` is the execution-layer notion of "capacity";
        # `reflection_jobs.RETRYABLE` is this owner's retry policy. They answer
        # different questions and are deliberately not the same constant.
        self.runner = ExactTaskRunner(self.tasks, frozenset({KIND}), self._generate, retain_refusals=True)

    def _sequence(self, context):
        return self.runtime.read_one("SELECT COALESCE(MAX(sequence), 0) n FROM memory_changes WHERE owner_key = ?", (owner_key(context),))["n"]

    def _row(self, context, batch_id):
        self.memory.writer.access.check(context, "memory.reflect")
        row = self.runtime.read_one("SELECT * FROM memory_reflections WHERE batch_id = ? AND owner_key = ?", (batch_id, owner_key(context)))
        if row is None or TaskContext.restore(json.loads(row["context_json"])).key != context.key:
            raise TaskError("memory_reflection_unavailable", "reflection belongs to its original authenticated context")
        return row

    def _task_timeout(self, context):
        group = self.gateway.router.profiles[KIND].group
        snapshot = require(self.gateway.router.configuration.read(context.actor,
            target=target_for(context), group_id=group, observed_at=self.tasks.now()))
        timeout = snapshot.fields[group + ".timeout_seconds"].value
        return max(180, 2 * timeout + 30)

    @outcome
    def prepare(self, context, *, request_id, start, end, role_instruction, maximum_sources=32, maximum_chars=24000, schedule_ref=None,
                retry_of=None):
        try:
            return self._prepare(context, request_id=request_id, start=start, end=end, role_instruction=role_instruction,
                maximum_sources=maximum_sources, maximum_chars=maximum_chars, schedule_ref=schedule_ref, retry_of=retry_of)
        except TaskError as error:
            # Finish the failed selection transaction before marking an existing
            # batch stale. A newly rejected batch leaves no reservation behind.
            recovery.quarantine_stale(self, context, "memory-reflection-" + digest([owner_key(context), request_id]), error.code)
            raise

    def _prepare(self, context, *, request_id, start, end, role_instruction, maximum_sources, maximum_chars, schedule_ref,
                 retry_of):
        """Read captured original messages incrementally. Uncaptured history is unknown."""
        self.memory.writer.access.check(context, "memory.reflect")
        text(request_id, "request_id", 256)
        text(role_instruction, "role_instruction", 65536)
        start, end = timestamp(start), timestamp(end)
        if not start or not end or start >= end or end > self.tasks.now():
            raise ValueError("reflection needs a completed aware time window")
        if type(maximum_sources) is not int or not 1 <= maximum_sources <= 64 or type(maximum_chars) is not int or not 2000 <= maximum_chars <= 48000:
            raise ValueError("reflection input budget is invalid")
        batch_id = "memory-reflection-" + digest([owner_key(context), request_id])
        request_hash = digest([context, start, end, role_instruction, maximum_sources, maximum_chars, schedule_ref] + ([retry_of] if retry_of else []))
        with self.runtime.transaction() as connection:
            existing = connection.execute("SELECT * FROM memory_reflections WHERE batch_id = ?", (batch_id,)).fetchone()
            if existing:
                original_context = TaskContext.restore(json.loads(existing["context_json"]))
                original_hash = digest([original_context, start, end, role_instruction, maximum_sources, maximum_chars, schedule_ref]
                                       + ([retry_of] if retry_of else []))
                if original_context.key != context.key or existing["request_hash"] != original_hash:
                    raise TaskError("memory_reflection_conflict", "the original reflection window changed")
                if existing["state"] == "selecting":
                    existing = self.selection.advance(context, existing)
                return {"batch_id": batch_id, "state": existing["state"]}
            parent = self._row(context, retry_of) if retry_of else None
            if parent and parent["state"] not in ("failed", "partial"):
                raise TaskError("memory_reflection_retry_denied", "only a known failed or partially applied result can be repaired")
            selected = []
            if parent:
                original = json.loads(parent["manifest_json"])
                selected = [codec.evidence(e) for e in original["evidence"]]
                for evidence in selected:
                    self.memory.writer.access.evidence(context, evidence, action="memory.reflect")
            manifest = {"start": stamp(start), "end": stamp(end), "evidence": json.loads(encode(selected)),
                "facts": [], "memory_sequence": self._sequence(context), "role_instruction": role_instruction,
                "remaining_sources": original["remaining_sources"] if parent else None,
                "unavailable_sources": original.get("unavailable_sources", False) if parent else False,
                "deferred_sources": original.get("deferred_sources", 0) if parent else 0,
                "unexamined_fact_count": None, "coverage": "captured_authorized_sources_only",
                "selection": {"phase": "facts" if parent else "source_index", "maximum_sources": maximum_sources,
                    "maximum_chars": maximum_chars, "ids": [], "after": ["", "", -1, ""], "examined": 0}}
            manifest["schedule_ref"] = schedule_ref
            manifest["settings_token"] = self.memory.settings.read(context)[1] if self.memory.settings else None
            manifest["pending"] = require(self.memory.writer.pending(context,
                only_operation_ids=[v["operation_id"] for v in original.get("pending", [])] if parent else None,
                exclude_reflection_claims=parent is None))
            if parent:
                manifest["selection"]["source_anchor"] = original.get("selection", {}).get("source_anchor")
                manifest["retry_of"] = retry_of
                manifest["repair_depth"] = original.get("repair_depth", 0) + 1
                manifest["repair_material"] = recovery.repair_material(parent)
                manifest["inherited_fact_handles"] = original.get("fact_handles", {})
                manifest["inherited_fact_records"] = original.get("fact_records", {})
                rejected = json.loads(parent["result_json"] or "{}").get("rejected", [])
                if rejected:
                    allowed = original.get("repair_item_ids")
                    manifest["repair_item_ids"] = sorted({v["item_id"] for v in rejected
                        if allowed is None or v["item_id"] in allowed})
                    manifest["repair_material"]["allowed_item_ids"] = manifest["repair_item_ids"]
                    # Keep only pending decisions from this repair's input set.
                    pending_ids = {identifier[8:] for identifier in manifest["repair_item_ids"] if identifier.startswith("pending:")}
                    manifest["pending"] = [v for v in manifest["pending"] if v["operation_id"] in pending_ids]
            now = stamp(self.tasks.now())
            connection.execute("INSERT INTO memory_reflections VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)",
                (batch_id, owner_key(context), request_hash, encode(context), encode(manifest), "selecting",
                 encode({"items": {}, "sequence": manifest["memory_sequence"]}), now, now))
            if parent:
                receipts = json.loads(parent["receipts_json"])
                if receipts.get("repair_batch") and receipts["repair_batch"] != batch_id:
                    raise TaskError("memory_repair_conflict", "resolve the existing linked repair before requesting another")
                receipts["repair_batch"] = batch_id
                connection.execute("UPDATE memory_reflections SET receipts_json = ? WHERE batch_id = ?", (encode(receipts), retry_of))
            row = self.selection.advance(context, self._row(context, batch_id))
            return {"batch_id": batch_id, "state": row["state"],
                    "remaining_sources": json.loads(row["manifest_json"])["remaining_sources"]}

    def _validate_selection(self, context, manifest):
        """A saved partial selection never bypasses current settings or sources."""
        self.memory.writer.access.check(context, "memory.reflect")
        schedule = manifest.get("schedule_ref")
        if schedule:
            recovery.current_schedule(self, context, schedule)
        if self.memory.settings and manifest.get("settings_token") != self.memory.settings.read(context)[1]:
            raise TaskError("memory_settings_changed", "memory settings changed after selection began")
        for raw in manifest["evidence"]:
            self.memory.writer.access.evidence(context, codec.evidence(raw), action="memory.reflect")
        for pending in manifest.get("pending", []):
            if pending["state"] == "pending":
                current = self.runtime.read_one("SELECT state FROM memory_operations WHERE operation_id = ?", (pending["operation_id"],))
                if current is None or current["state"] != "pending":
                    raise TaskError("memory_pending_changed", "the original pending proposal changed")
                self.memory.writer._validate_proposal(context, codec.proposal(pending["proposal"]), "memory.propose")

    def _validate_inputs(self, context, row):
        manifest = json.loads(row["manifest_json"])
        schedule = manifest.get("schedule_ref")
        if schedule:
            recovery.current_schedule(self, context, schedule)
        if self.memory.settings and manifest.get("settings_token") != self.memory.settings.read(context)[1]:
            raise TaskError("memory_settings_changed", "memory settings changed after the reflection was prepared")
        for raw in manifest["evidence"]:
            self.memory.writer.access.evidence(context, codec.evidence(raw), action="memory.reflect")
        receipts = json.loads(row["receipts_json"])
        if self._sequence(context) != receipts["sequence"]:
            raise TaskError("memory_reflection_stale", "new memory changes require a fresh reflection before applying old analysis")
        if row["state"] in ("prepared", "running", "received"):
            for ref in manifest["facts"]:
                require(self.memory.writer.read(context, AtomRef(**ref)))
            for pending in manifest.get("pending", []):
                if pending["state"] != "pending":
                    continue
                current = self.runtime.read_one("SELECT state FROM memory_operations WHERE operation_id = ?", (pending["operation_id"],))
                if current is None or current["state"] != "pending":
                    raise TaskError("memory_pending_changed", "the original pending proposal changed")
                self.memory.writer._validate_proposal(context, codec.proposal(pending["proposal"]), "memory.propose")
        return manifest

    @outcome
    def validate(self, spec, at):
        if self._closed:
            raise TaskError("memory_reflection_closed", "memory reflection is stopped")
        if self._require_running:
            self._require_running()
        row = self._row(spec.context, spec.request_ref)
        if row["task_id"] != spec.task_id:
            raise TaskError("memory_reflection_conflict", "task is not this reflection's original attempt")
        self._validate_inputs(spec.context, row)
        return True

    async def run(self, context, batch_id):
        try:
            if self._closed or self.runner is None:
                raise TaskError("memory_reflection_closed", "reflection is not available")
            if self._require_running:
                self._require_running()
            row = recovery.reconcile_prepared(self, context, batch_id)
            if row["state"] == "selecting":
                row = self.selection.advance(context, row)
                if row["state"] == "selecting":
                    return self.status(context, batch_id)
            if row["state"] in ("received", "modelled"):
                recovery.ensure_checkpoint_stopped(self, row)
            if row["state"] in ("complete", "empty"):
                recovery.finish_parent(self, context, batch_id)
                return Outcome.success(self.status(context, batch_id).unwrap())
            if row["state"] == "received":
                recovery.consume(self, context, batch_id)
                return await self._after_run(context, batch_id)
            if row["state"] == "modelled":
                result = self.apply(context, batch_id)
                if not result.is_success:
                    recovery.quarantine_stale(self, context, batch_id, result.failure.code)
                return result
            if row["state"] == "running":
                return Outcome.failed("memory_reflection_uncertain", "the original model attempt must be reconciled; it is not replayed")
            if row["state"] in ("failed", "partial"):
                chain = self._repair_chain(context, batch_id)
                linked = chain[-1] if len(chain) > 1 else None
                if linked and self._row(context, linked)["state"] in ("selecting", "prepared", "received", "modelled", "complete", "empty"):
                    await self.run(context, linked)
                    return self.status(context, batch_id)
                return Outcome.failed("memory_reflection_failed", "the known failed attempt needs an explicit retry request")
            if KIND not in self.gateway.router.profiles:
                raise TaskError("model_task_unregistered", "host has not configured memory reflection")
            timeout_seconds = self._task_timeout(context) if row["task_id"] is None else 0
            with self.runtime.transaction() as connection:
                self._validate_inputs(context, row)
                task_id = row["task_id"] or "memory-extract-task-" + digest(batch_id)
                connection.execute("UPDATE memory_reflections SET task_id = ? WHERE batch_id = ?", (task_id, batch_id))
                now = self.tasks.now()
                if row["task_id"] is None:
                    require(self.tasks.submit(TaskSpec(task_id, context, KIND, batch_id, batch_id,
                        now, now + timedelta(seconds=timeout_seconds))))
            invocation = asyncio.current_task()
            self._invocations.add(invocation)
            try:
                result = await self.runner.run(context, task_id)
            finally:
                self._invocations.discard(invocation)
            if not result.is_success:
                return result
            attempt = result.unwrap()
            if attempt.task is not None and attempt.task.state in QUEUED:
                # Nothing ran and the reservation is still queued: the supervisor
                # refused this attempt transiently. Continuing the batch here would
                # report unexecuted work as an owner result.
                return Outcome.failed(attempt.refused or attempt.failure_code or "task_not_admitted",
                                      "the reflection attempt was not admitted")
            return await self._after_run(context, batch_id)
        except TaskError as error:
            recovery.quarantine_stale(self, context, batch_id, error.code)
            return Outcome.failed(error.code, str(error))

    def _request(self, context, row, manifest):
        batch_id, materials = row["batch_id"], []
        handles = {}
        fact_handles = dict(manifest.get("inherited_fact_handles", {}))
        fact_records = dict(manifest.get("inherited_fact_records", {}))
        for index, raw in enumerate(manifest["evidence"]):
            evidence = codec.evidence(raw)
            handle = "s" + str(index + 1)
            handles[handle] = raw
            delivery = {}
            from companion_v2.conversation.native_observations import source_row
            observation = source_row(self.runtime, evidence.source)
            if observation is not None:
                delivery = {"delivery_state": observation["delivery_state"],
                            "delivery_basis": "native_observation_without_platform_delivery_receipt"}
            materials.append(PromptMaterial("memory", "reflection_source", encode({"handle": handle,
                "text": self.memory.writer.access.evidence(context, evidence), "speaker_id": evidence.speaker_id,
                "source_kind": evidence.source_kind, "message_time": stamp(evidence.source.issued_at),
                "start": evidence.start, "end": evidence.end, **delivery}), evidence.source))
        for ref in manifest["facts"]:
            value = require(self.memory.writer.read(context, AtomRef(**ref)))
            handle = next((alias for alias, existing in fact_handles.items() if existing == ref), "f" + str(len(fact_handles) + 1))
            fact_handles[handle] = ref
            stored = self.runtime.read_one("SELECT fact_key FROM memory_atoms WHERE atom_id = ?", (ref["atom_id"],))
            fact_records[handle] = {**ref, "fact_key": stored["fact_key"]}
            materials.append(PromptMaterial("memory", "existing_fact", encode({"handle": handle,
                **{k: v for k, v in value.items() if k not in ("evidence", "source")}})))
        manifest.update(handles=handles, fact_handles=fact_handles, fact_records=fact_records)
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET manifest_json = ? WHERE batch_id = ?", (encode(manifest), batch_id))
        changes = require(self.memory.writer.changes(context, limit=100))
        materials.append(PromptMaterial("memory", "correction_receipts", encode(changes)))
        materials.append(PromptMaterial("memory", "proposal_format", encode(REFLECTION_SCHEMA)))
        materials.append(PromptMaterial("memory", "pending_proposals", encode(manifest.get("pending", []))))
        if context.scope.kind is ScopeKind.GROUP:
            # Safety hits are bounded evidence for review. They are never facts,
            # member profiles, or instructions to change permissions.
            safety_rows = self.runtime.read_all(
                """SELECT hit_id, member_id, message_id, rule_id, direction, severity,
                   evidence_json, occurred_at, review_state, review_json
                   FROM group_member_safety_hits
                   WHERE owner_key = ? AND occurred_at >= ? AND occurred_at < ?
                   ORDER BY occurred_at, hit_id LIMIT ?""",
                (group_memory_owner_key(context), manifest["start"], manifest["end"],
                 DEFAULT_GROUP_SAFETY_POLICY.reflection_hit_limit))
            hits = []
            for safety in safety_rows:
                try:
                    evidence = json.loads(safety["evidence_json"])
                except (TypeError, ValueError):
                    evidence = {"unavailable": "invalid_evidence"}
                hits.append({"hit_id": safety["hit_id"], "member_id": safety["member_id"],
                    "message_id": safety["message_id"], "rule_id": safety["rule_id"],
                    "direction": safety["direction"], "severity": safety["severity"],
                    "evidence": evidence, "occurred_at": safety["occurred_at"],
                    "review_state": safety["review_state"]})
            materials.append(PromptMaterial("group.member_safety", "daily_hits", encode({
                "start": manifest["start"], "end": manifest["end"], "hits": hits,
                "guidance": [
                    "These deterministic rule hits are evidence, not established facts.",
                    "Only produce a review suggestion when the evidence supports it and cite hit_id.",
                    "Do not create a member profile or infer identity from a rule hit.",
                    "Do not mute, ban, change permissions, or take any other enforcement action.",
                    ADDRESSING_REFLECTION_INSTRUCTION,
                ],
            })))
            # A periodic "本群最近发生" description is part of the same memory chain,
            # never a second store: it is offered as one more bounded material and
            # recorded like any other proposal. See memory/episode_settings.py.
            from .episode_settings import episode_material
            episode = episode_material(self.runtime, context, manifest)
            if episode is not None:
                materials.append(PromptMaterial("group.episode", "recent_group_activity", encode(episode)))
        elif context.scope.kind is ScopeKind.PRIVATE:
            # The private dialogue episode is the same abstract for this
            # conversation's own owner: a periodic, bounded material, recorded
            # like any other proposal. See memory/episodes.py.
            from .episodes import dialogue_episode_material
            episode = dialogue_episode_material(self.runtime, self.memory.settings, self.memory.writer.access,
                                               context, manifest)
            if episode is not None:
                materials.append(PromptMaterial("dialogue.episode", "recent_private_conversation", encode(episode)))
        if manifest.get("repair_material"):
            materials.append(PromptMaterial("memory", "format_repair", encode(manifest["repair_material"])))
        info = {k: manifest[k] for k in ("start", "end", "memory_sequence", "remaining_sources", "unavailable_sources", "unexamined_fact_count", "coverage")}
        info.update({key: manifest[key] for key in ("source_count_basis", "fact_count_basis", "fact_selection_complete") if key in manifest})
        info["continuation_needed"] = self.selection.continuation_needed(context, manifest)
        primary = SourceRef(batch_id, "memory.reflection.input", context.scope, VersionRef(batch_id, 1), self.tasks.now())
        require(self.memory.writer.sources.publish(SourceRecord(primary, context.audience, digest(info), encode(info)), observed_at=self.tasks.now()))
        event = InboundConversationEvent(batch_id, context.actor.principal, context.scope, primary, "memory.reflect", digest(info), self.tasks.now())
        materials.append(PromptMaterial("memory", "coverage", encode(info)))
        mode = self.memory.settings.read(context)[0]["memory.reflection_response_format"] if self.memory.settings else "text"
        return PromptRequest(event, manifest["role_instruction"], SealedForegroundContext(tuple(materials), (), ()),
                             REFLECTION_INSTRUCTION, context.role_binding, output_format="memory_reflection",
                             response_format=mode, response_schema_json=encode(REFLECTION_SCHEMA))

    async def _generate(self, run):
        context, batch_id = run.task.spec.context, run.task.spec.request_ref
        row = self._row(context, batch_id)
        manifest = self._validate_inputs(context, row)
        completion = None
        try:
            with self.runtime.transaction() as connection:
                request = self._request(context, row, manifest)
                connection.execute("UPDATE memory_reflections SET state = 'running' WHERE batch_id = ? AND state = 'prepared'", (batch_id,))
            completion = await self.gateway.generate(run, request)
            recovery.capture(self, run, completion)
            recovery.consume(self, context, batch_id)
            return TaskResult("succeeded", effect="committed", result_ref=batch_id, receipt_ref=batch_id, operation_id=batch_id)
        except BaseException as error:
            if completion is not None:
                self.gateway.ledger.discard(completion.call_id)
                # A captured return is recoverable without calling the model.
                # An interrupted provider with no return remains unknown.
                with self.runtime.transaction() as connection:
                    connection.execute("UPDATE memory_reflections SET state = 'failed', updated_at = ? WHERE batch_id = ? AND state = 'running'",
                                       (stamp(self.tasks.now()), batch_id))
            elif isinstance(error, (Exception, asyncio.CancelledError)):
                # A preparation failure or confirmed non-dispatch is known. A
                # dispatched call without a return must keep its unknown state.
                calls = self.runtime.read_all("SELECT state FROM model_calls WHERE task_id = ?", (run.task.task_id,))
                if not calls or all(call["state"] in ("not_started", "discarded") for call in calls):
                    with self.runtime.transaction() as connection:
                        code = error.code if isinstance(error, TaskError) else "memory_request_preparation_failed"
                        connection.execute("UPDATE memory_reflections SET state = 'failed', result_json = ?, updated_at = ? WHERE batch_id = ? AND state IN ('prepared', 'running')",
                            (encode({"summary": None, "issues": [{"code": code, "path": "$"}]}), stamp(self.tasks.now()), batch_id))
            raise

    async def _after_run(self, context, batch_id):
        row = recovery.reconcile_prepared(self, context, batch_id)
        manifest = json.loads(row["manifest_json"])
        enabled = self.memory.settings.read(context)[0]["memory.reflection_auto_repair"] if self.memory.settings else True
        if enabled and manifest.get("repair_depth", 0) == 0 and recovery.repair_allowed(row):
            receipts = json.loads(row["receipts_json"])
            child_id = receipts.get("repair_batch")
            if child_id is None:
                prepared = self.retry(context, batch_id, request_id="auto-repair:" + batch_id)
                if prepared.is_success:
                    child_id = prepared.unwrap()["batch_id"]
            if child_id is not None:
                child = self._row(context, child_id)
                if child["state"] in ("selecting", "prepared", "received", "modelled"):
                    await self.run(context, child_id)
        recovery.finish_parent(self, context, batch_id)
        return self.status(context, batch_id)

    @outcome
    def retry(self, context, batch_id, *, request_id):
        row = self._row(context, batch_id)
        expected_id = "memory-reflection-" + digest([owner_key(context), request_id])
        existing = self.runtime.read_one("SELECT * FROM memory_reflections WHERE batch_id = ? AND owner_key = ?", (expected_id, owner_key(context)))
        if existing:
            require(self.status(context, expected_id))
            if json.loads(existing["manifest_json"]).get("retry_of") not in self._repair_chain(context, batch_id):
                raise TaskError("memory_repair_conflict", "request ID belongs to a different repair")
            return {"batch_id": expected_id, "state": existing["state"],
                    "remaining_sources": json.loads(existing["manifest_json"])["remaining_sources"]}
        for linked in self._repair_chain(context, batch_id)[1:]:
            row = self._row(context, linked)
            batch_id = linked
        if row["state"] not in ("failed", "partial"):
            raise TaskError("memory_reflection_retry_denied", "only a known failed attempt can be retried")
        prior = json.loads(row["manifest_json"])
        with self.runtime.transaction():
            schedule, role_instruction = prior.get("schedule_ref"), prior["role_instruction"]
            if schedule:
                current = recovery.current_schedule(self, context, schedule, exact=False)
                schedule = {"schedule_id": current["schedule_id"], "revision": current["revision"]}
                role_instruction = json.loads(current["settings_json"])["role_instruction"]
            return require(self.prepare(context, request_id=request_id, start=prior["start"], end=prior["end"],
                role_instruction=role_instruction, schedule_ref=schedule, retry_of=batch_id))

    def _repair_chain(self, context, batch_id):
        chain = []
        while batch_id:
            if batch_id in chain or len(chain) >= 64:
                raise TaskError("memory_repair_conflict", "invalid or excessive linked repair chain")
            chain.append(batch_id)
            row = self._row(context, batch_id)
            batch_id = json.loads(row["receipts_json"]).get("repair_batch")
        return chain

    @outcome
    def apply(self, context, batch_id):
        """Resume persisted model output, never generate it again to recover a receipt."""
        row = self._row(context, batch_id)
        if row["state"] == "complete":
            return require(self.status(context, batch_id))
        if row["state"] != "modelled":
            raise TaskError("memory_reflection_not_modelled", "no durable model output exists")
        output = json.loads(row["result_json"])
        for decision in output.get("pending_decisions", []):
            with self.runtime.transaction() as connection:
                current = self._row(context, batch_id)
                self._validate_inputs(context, current)
                receipts = json.loads(current["receipts_json"])
                key = "pending:" + decision["operation_id"]
                if key in receipts["items"]:
                    continue
                method = self.memory.writer.commit_pending if decision["decision"] == "accept" else self.memory.writer.discard_pending
                result = method(context, decision["operation_id"])
                receipts["items"][key] = result.unwrap() if result.is_success else {"state": "rejected", "code": result.failure.code}
                if not result.is_success:
                    output.setdefault("rejected", []).append({"item_id": key, "raw": decision, "kind": "decision",
                        "problem": {"code": result.failure.code, "path": "pending_decisions"}})
                receipts["sequence"] = self._sequence(context)
                connection.execute("UPDATE memory_reflections SET receipts_json = ?, result_json = ? WHERE batch_id = ?",
                                   (encode(receipts), encode(output), batch_id))
        for entry in output["proposals"]:
            with self.runtime.transaction() as connection:
                row = self._row(context, batch_id)
                self._validate_inputs(context, row)
                receipts = json.loads(row["receipts_json"])
                if entry["item_id"] in receipts["items"]:
                    continue
                proposal = codec.proposal(entry["proposal"])
                result = self.memory.writer.submit(context, proposal, operation_key=batch_id + ":" + entry["item_id"])
                receipts["items"][entry["item_id"]] = ({"state": "rejected", "code": result.failure.code}
                    if not result.is_success else result.unwrap())
                if not result.is_success:
                    manifest = json.loads(row["manifest_json"])
                    output.setdefault("rejected", []).append({"item_id": entry["item_id"], "raw": proposal_for_repair(entry["proposal"], manifest), "kind": "proposal",
                        "problem": {"code": result.failure.code, "path": "proposal"}})
                receipts["sequence"] = self._sequence(context)
                connection.execute("UPDATE memory_reflections SET receipts_json = ?, result_json = ?, updated_at = ? WHERE batch_id = ?",
                                   (encode(receipts), encode(output), stamp(self.tasks.now()), batch_id))
        for entry in output.get("anniversaries", []):
            with self.runtime.transaction() as connection:
                row = self._row(context, batch_id)
                self._validate_inputs(context, row)
                receipts = json.loads(row["receipts_json"])
                key = entry["item_id"]
                if key in receipts["items"]:
                    continue
                try:
                    result = anniversaries.remember(
                        self.runtime, context, entry, source_run_id=batch_id, at=self.tasks.now())
                except (TypeError, ValueError, KeyError):
                    result = {"state": "rejected", "code": "anniversary_invalid"}
                receipts["items"][key] = result
                if result.get("state") in {"limit", "rejected"}:
                    output.setdefault("rejected", []).append({
                        "item_id": key, "raw": entry, "kind": "anniversary",
                        "problem": {"code": result.get("code", "anniversary_limit"),
                                    "path": "anniversaries"},
                    })
                receipts["sequence"] = self._sequence(context)
                connection.execute("UPDATE memory_reflections SET receipts_json = ?, result_json = ?, updated_at = ? WHERE batch_id = ?",
                                   (encode(receipts), encode(output), stamp(self.tasks.now()), batch_id))
        with self.runtime.transaction() as connection:
            self._validate_inputs(context, self._row(context, batch_id))
            self.memory.writer._complete(batch_id)
            state = "partial" if output.get("rejected") else "complete"
            connection.execute("UPDATE memory_reflections SET state = ?, updated_at = ? WHERE batch_id = ?", (state, stamp(self.tasks.now()), batch_id))
        self._cover_dialogue_episode(context, batch_id)
        recovery.finish_parent(self, context, batch_id)
        return require(self.status(context, batch_id))

    def _cover_dialogue_episode(self, context, batch_id):
        """A private dialogue window is covered only once its batch actually landed.

        The durable cursor keeps a restart, a repeated window or a repair pass from
        summarising the same span twice. This is derived bookkeeping: losing it can
        only offer the window again, so it never blocks memory application.
        """
        try:
            from .episodes import mark_covered
            mark_covered(self.runtime, context, json.loads(self._row(context, batch_id)["manifest_json"]))
        except Exception as error:
            logger.warning("V2 dialogue episode coverage mark failed: %s", type(error).__name__)

    @outcome
    def status(self, context, batch_id):
        row = self._row(context, batch_id)
        return self.describe(context, row, self.memory.writer.access)

    def describe(self, context, row, access):
        """Caller authorizes the stored row; only currently readable text is shown."""
        batch_id = row["batch_id"]
        manifest = json.loads(row["manifest_json"])
        # Metadata/receipts survive withdrawal. The human summary is shown only
        # while all of its original inputs remain readable.
        summary = None
        try:
            for raw in manifest["evidence"]:
                access.evidence(context, codec.evidence(raw))
            for pending in manifest.get("pending", []):
                for raw in pending.get("proposal", {}).get("evidence", []):
                    access.evidence(context, codec.evidence(raw))
            for ref in manifest["facts"]:
                # Old fact references must not expose a withdrawn summary.
                original = self.runtime.read_one("SELECT record_json FROM memory_revisions WHERE atom_id = ? AND revision = ?",
                                                 (ref["atom_id"], ref["revision"]))
                if original:
                    saved = json.loads(original["record_json"])
                    if saved.get("forgotten"):
                        raise TaskError("memory_forgotten", "the reflection depended on forgotten memory")
                    for evidence in saved.get("proposal", {}).get("evidence", []):
                        access.evidence(context, codec.evidence(evidence))
            if row["result_json"]:
                summary = json.loads(row["result_json"])["summary"]
        except TaskError:
            pass
        result = json.loads(row["result_json"] or "{}")
        receipts = json.loads(row["receipts_json"])
        job = self.runtime.read_one("SELECT * FROM memory_reflection_jobs WHERE batch_id = ? ORDER BY created_at DESC, job_id DESC LIMIT 1",
                                    (batch_id,))
        return {"batch_id": batch_id, "state": row["state"], "summary": summary,
                "state_label": {"selecting": "正在分批准备材料", "prepared": "待整理", "running": "调用结果待核对", "received": "已收到，待解析",
                    "modelled": "待写入", "failed": "整理失败，待处理", "partial": "部分已保存，仍有待处理项",
                    "complete": "整理完成", "empty": "本批次无可整理材料"}.get(row["state"], "待核对"),
                "receipts": json.loads(row["receipts_json"])["items"], "remaining_sources": manifest["remaining_sources"],
                "coverage": manifest["coverage"], "unavailable_sources": manifest["unavailable_sources"],
                "deferred_sources": manifest.get("deferred_sources", 0),
                "continuation_needed": self.selection.continuation_needed(context, manifest),
                "captured": "raw_output" in result, "finish_reason": result.get("raw_output", {}).get("finish_reason"),
                "issues": result.get("issues", []) if row["state"] != "complete" else [],
                "unresolved_items": [{"item_id": e["item_id"], **e["problem"]} for e in result.get("rejected", [])]
                    if row["state"] != "complete" else [],
                "repair_batch": receipts.get("repair_batch"), "retry_of": manifest.get("retry_of"),
                "continuation": describe_job(job) if job else None,
                "selection": {key: manifest["selection"][key] for key in ("phase", "examined", "blocked_code")
                              if key in manifest.get("selection", {})},
                "resumable": row["state"] in ("selecting", "prepared", "received", "modelled"),
                "retryable": row["state"] in ("failed", "partial") and not receipts.get("repair_batch")}

    @property
    def active_handles(self):
        return (self.runner.active_handles if self.runner else ()) + tuple("memory-reflection:" + str(id(t)) for t in self._invocations)

    async def close(self):
        self._closed = True
        if self.runner:
            await self.runner.close()
        if self._invocations:
            await asyncio.wait(tuple(self._invocations), timeout=0.25)
