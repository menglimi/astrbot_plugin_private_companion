"""A local asynchronous gateway that never retries an unknown model invocation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from logging import getLogger
from time import perf_counter
from uuid import uuid4

from companion_v2.conversation.foreground import ModelDraft, PromptRequest
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.execution.runner import TaskRun
from companion_v2.execution.service import TaskSupervisor, outcome
from companion_v2.role_profile.task_reference import TaskRoleReference
from companion_v2.sources.repositories import SourceRepository

from .budget import BudgetDeferral, BudgetLedger
from .failures import ModelFailure
from .models import MAX_TOOL_CATALOG, Amount, ModelCompletion, ProviderResponse, ToolSpec
from .refusal import matches_refusal_text
from .routing import ModelRouter
from .response_diagnostics import checked_response_diagnostics


logger = getLogger(__name__)
PROACTIVE_PROMPT_SNAPSHOT_LIMIT = 10
# Background tasks use the self-page role profile as their role reference.
# Conversation and image safety review keep their own captured instructions.
TASK_ROLE_REFERENCE_KINDS = frozenset({
    "conversation.poke_reply",
    "proactive.compose", "proactive.judge",
    "role.journal.diary", "role.journal.dream", "role.journal.derive", "role.journal.select",
    "role.self_review", "role.growth.progress",
    "role.expression.learn", "role.expression.review", "role.expression.contact_review",
    "role.state.maintain", "role.reading.consume",
    "calendar.plan", "memory.reflect",
    "screen.interpretation",
    "content.authoring.draft",
    "conversation.image_narration", "conversation.video_narration",
    "conversation.context_image_caption",
})
# Output formats whose owner checks the parts that arrived whole, so a body cut off
# by the model's output ceiling is still worth handing to it. Every other format
# needs a whole result and hears the ceiling's own code instead. The private memory
# owner is not listed: it has always received its raw body through the capture path.
CEILING_BODY_FORMATS = frozenset({"calendar_planning"})


def _redact_prompt_media(value):
    if isinstance(value, list):
        return [_redact_prompt_media(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "image_url" and isinstance(item, dict):
            image = dict(item)
            if isinstance(image.get("url"), str) and image["url"].startswith("data:"):
                image["url"] = "[inline image payload omitted]"
            result[key] = _redact_prompt_media(image)
        elif key in ("input_audio", "video_data") and isinstance(item, dict):
            media = dict(item)
            if "data" in media:
                media["data"] = f"[{key} payload omitted]"
            result[key] = _redact_prompt_media(media)
        else:
            result[key] = _redact_prompt_media(item)
    return result


class LocalModelGateway:
    def __init__(self, router: ModelRouter, ledger: BudgetLedger, tasks: TaskSupervisor):
        if router.configuration.runtime is not tasks.runtime or ledger.runtime is not tasks.runtime:
            raise ValueError("gateway components must share the original runtime")
        self.router = router
        self.ledger = ledger
        self.tasks = tasks
        self.runtime = tasks.runtime
        self.role_reference = TaskRoleReference(self.runtime)

    async def generate(self, run: TaskRun, request: PromptRequest) -> ModelCompletion:
        session = self.open_session(run, request)
        # open_session may replace the captured persona with the task role
        # reference. Continue with the exact request frozen into the plan so
        # ModelSession's first-round context check compares like with like.
        return await session.generate(session.plan.envelope.original)

    def _task_role_request(self, run: TaskRun, request: PromptRequest) -> PromptRequest:
        """Use the self page as the task role, with the captured persona as fallback."""
        if run.task.spec.kind not in TASK_ROLE_REFERENCE_KINDS:
            return request
        reference = self.role_reference.instruction(run.task.spec.context, self.tasks.now())
        if not reference:
            return request
        # The model role layer becomes the self page, but the native role guard
        # must keep checking the captured AstrBot persona behind the window.
        guard = request.role_guard_instruction if request.role_guard_instruction is not None else request.role_instruction
        return replace(request, role_instruction=reference, role_guard_instruction=guard)

    def open_session(self, run: TaskRun, request: PromptRequest, *, tools: tuple[ToolSpec, ...] = (),
                     tool_filters: tuple[dict, ...] = (), independent_providers=None):
        """Open the one model session and decide this round's effective tools.

        This is the **single** authority for the tool-capability gate.  Callers
        pass the full catalog; this method compares it with the authoritative
        ``plan.providers`` from ``router.resolve()``, drops it for a round whose
        chain cannot use tools, records the reason in ``plan.audit`` and returns
        the effective catalog in the session.  Do not add a caller-side
        prediction (for example with ``router.selected_route()``): it reads the
        configuration snapshot a second time and can see a different provider
        chain than the plan that actually executes.
        """
        if run.supervisor is not self.tasks:
            raise ValueError("gateway must execute against its original supervisor")
        if len(tools) > MAX_TOOL_CATALOG or len({tool.action for tool in tools}) != len(tools):
            raise ValueError("tool registration requires a bounded unique catalog")
        if independent_providers is not None and (
                type(independent_providers) is not tuple or not 1 <= len(independent_providers) <= 2
                or any(type(value) is not str for value in independent_providers) or tools):
            raise ValueError("independent checks require one or two captured providers and no tools")
        with self.runtime.transaction() as connection:
            run.validate()
            if connection.execute("SELECT 1 FROM model_requests WHERE task_id = ?", (run.task.task_id,)).fetchone():
                raise TaskError("model_request_already_started", "inspect the original model calls instead of regenerating")
            request = self._task_role_request(run, request)
            plans = (tuple(self.router.resolve(run, request, provider_id=value or None)
                           for value in independent_providers) if independent_providers is not None else ())
            plan = plans[0] if plans else self.router.resolve(run, request)
            if tools and any("tools" not in provider.capabilities for provider in plan.providers):
                # 与宿主同语义（tool_loop_agent_runner：``modalities`` 不含
                # ``tool_use`` 时只清空本轮 tools，不判整轮失败）。这里丢弃目录并
                # 记录原因，而不是让一条本来能发的文本回复失败；整条 provider 链
                # 都有 tools 能力时，下面的行为与改动前完全一致。
                # 这是唯一判定点：generation 原样传入目录，不再用
                # router.selected_route() 另做一次预测（那会多读一次配置，且可能
                # 与这里真正执行的 plan 分叉）。
                logger.warning(
                    "[CompanionV2] 本轮降级为无工具对话 task=%s providers=%s actions=%d",
                    run.task.task_id, [provider.binding.provider for provider in plan.providers], len(tools))
                tool_filters = (*tool_filters, {"action": "*", "reason": "provider_without_tool_use"})
                tools = ()
            if tools or tool_filters:
                plan = replace(plan, audit={**plan.audit, "tool_catalog_hash": digest(tools),
                                          "tool_actions": [tool.action for tool in tools],
                                          "tool_filters": list(tool_filters[:128])})
            self.router.adopt(run, plan)
            if plans:
                plan = replace(plan, audit={**plan.audit,
                    "independent_checks": [item.audit for item in plans]})
            connection.execute("INSERT INTO model_requests VALUES (?, ?, ?, ?)",
                               (run.task.task_id, digest(request), encode(plan.audit), stamp(self.tasks.now())))
        return ModelSession(self, run, plan, tools, independent_plans=plans)

    async def _generate(self, run, plan, envelope, round_no, on_reply_prefix=None, reply_prefix_ready=None):
        def before_dispatch():
            run.validate()
            self.router.validate_plan(plan)
            # Recheck explicit original references in the cached envelope, too:
            # task input validation need not include later tool materials.
            sources = SourceRepository(self.runtime)
            references = {material.source for material in envelope.original.context.materials
                          if material.source is not None}
            reference = getattr(envelope.original.event, "source", None)
            if reference is not None:
                references.add(reference)
            for reference in references:
                require(sources.validate_reference(reference, observed_at=self.tasks.now()))

        contract_fallback_used = False
        for provider_position, provider in enumerate(plan.providers):
            provider_envelope = replace(envelope,
                model_override=envelope.model_override if provider_position == 0 else "",
                provider_capabilities=provider.capabilities)
            position = round_no * 2 + provider_position
            before_dispatch()
            logger.debug("[CompanionV2][开发者追踪] 模型网关回合开始 task=%s round=%d provider=%s position=%d output_format=%s",
                         run.task.task_id, round_no, provider.binding, position, envelope.original.output_format)
            try:
                amount = provider.reserve(provider_envelope)
            except Exception as error:
                logger.debug("[CompanionV2][开发者追踪] 模型网关预估失败 task=%s round=%d provider=%s error_type=%s",
                             run.task.task_id, round_no, provider.binding, type(error).__name__, exc_info=True)
                raise TaskError("model_estimation_failed", "local reservation estimation failed; details omitted") from error
            if not isinstance(amount, Amount):
                raise TaskError("model_estimation_failed", "adapter did not provide a reservation ceiling")
            if amount.tokens < envelope.max_output_tokens:
                raise TaskError("model_estimation_failed", "reservation must cover the declared output ceiling")
            call_id = "model-call-" + uuid4().hex
            try:
                with self.runtime.transaction():
                    self.router.validate_plan(plan)
                    self.ledger.reserve(run, call_id=call_id, position=position, provider=provider.binding, amount=amount)
                    self.ledger.dispatch(run, call_id)
            except BudgetDeferral as error:
                self.ledger.record_deferral(run, error)
                raise

            def record_cache_signature(signature):
                self.ledger.record_cache_signature(run, call_id, provider.binding, signature)

            prefix_committed = False

            async def publish_prefix(draft):
                nonlocal prefix_committed
                before_dispatch()
                committed = await on_reply_prefix(call_id, draft)
                prefix_committed = prefix_committed or committed
                return committed

            def record_prompt_snapshot(messages):
                if envelope.original.output_format != "proactive_contact":
                    return
                try:
                    self._record_proactive_prompt_snapshot(
                        run.task.task_id, call_id, provider.binding.provider,
                        envelope.model_override or provider.model, messages)
                except Exception as error:
                    logger.debug("[CompanionV2] proactive prompt snapshot failed task=%s call=%s error_type=%s",
                                 run.task.task_id, call_id, type(error).__name__, exc_info=True)

            started = perf_counter()
            try:
                response = await asyncio.wait_for(
                    provider.generate(replace(provider_envelope, before_dispatch=before_dispatch,
                                              record_cache_signature=record_cache_signature,
                                              record_prompt_snapshot=(record_prompt_snapshot
                                                  if envelope.original.output_format == "proactive_contact" else None),
                                              on_reply_prefix=publish_prefix if on_reply_prefix is not None else None,
                                              reply_prefix_ready=reply_prefix_ready)),
                    timeout=plan.audit["timeout_seconds"])
                if not isinstance(response, ProviderResponse):
                    raise TypeError("provider returned no trusted outcome")
                logger.debug("[CompanionV2][开发者追踪] 模型网关回合结果 task=%s round=%d provider=%s status=%s failure=%s draft=%r claims=%r tool_calls=%r diagnostics=%r",
                             run.task.task_id, round_no, provider.binding, response.status,
                             response.failure.value if response.failure is not None else None,
                             response.draft.text if response.draft is not None else None,
                             response.draft.claims if response.draft is not None else None,
                             response.tool_calls, response.diagnostics.to_record() if response.diagnostics is not None else None)
            except TimeoutError as error:
                logger.debug("[CompanionV2][开发者追踪] 模型网关超时 task=%s round=%d provider=%s",
                             run.task.task_id, round_no, provider.binding, exc_info=True)
                self.ledger.settle(call_id, provider.binding, ProviderResponse("unknown", failure=ModelFailure.TIMEOUT),
                                   observed_at=self.tasks.now(), latency_ms=(perf_counter() - started) * 1000)
                raise TaskError("model_timeout", "provider wait timed out; original result is unknown") from error
            except asyncio.CancelledError:
                logger.debug("[CompanionV2][开发者追踪] 模型网关取消 task=%s round=%d provider=%s",
                             run.task.task_id, round_no, provider.binding, exc_info=True)
                self.ledger.settle(call_id, provider.binding, ProviderResponse("unknown", failure=ModelFailure.INTERRUPTED),
                                   observed_at=self.tasks.now(), latency_ms=(perf_counter() - started) * 1000)
                raise
            except TaskError:
                # A trusted provider may return a structured product error (for
                # example, a model budget refusal). Keep its safe code so the
                # owning workflow can explain the actual cause instead of
                # collapsing it into a generic transport failure. The request
                # still has an unknown provider outcome, so record the call as
                # an adapter failure and never expose the private reason text.
                logger.debug("[CompanionV2][开发者追踪] 模型网关任务错误 task=%s round=%d provider=%s",
                             run.task.task_id, round_no, provider.binding, exc_info=True)
                self.ledger.settle(call_id, provider.binding, ProviderResponse("unknown", failure=ModelFailure.ADAPTER),
                                   observed_at=self.tasks.now(), latency_ms=(perf_counter() - started) * 1000)
                raise
            except Exception as error:
                logger.debug("[CompanionV2][开发者追踪] 模型网关传输异常 task=%s round=%d provider=%s error_type=%s",
                             run.task.task_id, round_no, provider.binding, type(error).__name__, exc_info=True)
                self.ledger.settle(call_id, provider.binding, ProviderResponse("unknown", failure=ModelFailure.ADAPTER),
                                   observed_at=self.tasks.now(), latency_ms=(perf_counter() - started) * 1000)
                raise TaskError("model_transport_failed", "local provider failed; private details omitted") from error
            usage_known = self.ledger.settle(call_id, provider.binding, response, observed_at=self.tasks.now(),
                                            latency_ms=(perf_counter() - started) * 1000)
            try:
                with self.runtime.read_snapshot():
                    before_dispatch()
            except TaskError:
                self.ledger.discard(call_id, failure=ModelFailure.GATEWAY_RECHECK, observed_at=self.tasks.now())
                raise
            if response.status == "generated":
                provider_refusal = (response.captured_output is not None
                                    and response.captured_output.finish_reason == "refusal")
                text_refusal = (plan.audit["fallback_on_refusal"] and response.draft is not None
                                and matches_refusal_text(response.draft.text, envelope.refusal_terms))
                if ((provider_refusal and (envelope.original.output_format != "memory_reflection"
                                           or plan.audit["fallback_on_refusal"])) or text_refusal):
                    self.ledger.discard(call_id, failure=ModelFailure.REFUSAL, observed_at=self.tasks.now())
                    if not prefix_committed and plan.audit["fallback_on_refusal"] and provider_position + 1 < len(plan.providers):
                        continue
                    raise TaskError("model_refused", "provider explicitly refused the request")
                captured = response.captured_output
                draft = response.draft
                if captured is not None and envelope.original.output_format != "memory_reflection":
                    if captured.finish_reason != "length":
                        self.ledger.discard(call_id)
                        raise TaskError("model_capture_unavailable", "unvalidated output requires its private memory owner")
                    if envelope.original.output_format not in CEILING_BODY_FORMATS:
                        # The ceiling cut this body and the format needs a whole
                        # result. Naming the ceiling keeps one glance enough to tell
                        # it from a refused or malformed answer.
                        logger.warning("[CompanionV2] 模型输出被上限截断，任务拒绝不完整结果 task=%s kind=%s "
                                       "output_format=%s max_output_tokens=%d provider=%s raw_chars=%d",
                                       run.task.task_id, plan.audit["profile"]["kind"],
                                       envelope.original.output_format, envelope.max_output_tokens,
                                       provider.binding.provider, len(captured.text))
                        self.ledger.discard(call_id, failure=ModelFailure.WIRE_DECODE, observed_at=self.tasks.now())
                        raise TaskError("model_output_truncated",
                                        "the output ceiling cut this result before its contract was complete")
                    # The owner validates the parts that arrived whole, so the body
                    # becomes its draft unchanged: the gateway does not judge it.
                    logger.warning("[CompanionV2] 模型正文被输出上限截断，交由任务取舍 task=%s kind=%s "
                                   "output_format=%s raw_chars=%d", run.task.task_id,
                                   plan.audit["profile"]["kind"], envelope.original.output_format,
                                   len(captured.text))
                    draft = ModelDraft(captured.text, ())
                if response.tool_calls and not envelope.tools:
                    self.ledger.discard(call_id)
                    raise TaskError("model_tools_unavailable", "no tool dispatch is allowed for this model round")
                if prefix_committed and response.tool_calls:
                    self.ledger.discard(call_id)
                    raise TaskError("model_stream_changed", "a published reply cannot become a tool round")
                with self.runtime.transaction() as connection:
                    connection.execute("""UPDATE model_rounds SET state = ?, call_id = ?, tool_calls_json = ?
                        WHERE task_id = ? AND round_no = ?""", (
                        "tools" if response.tool_calls else "final", call_id, encode(response.tool_calls),
                        run.task.task_id, round_no))
                return ModelCompletion(call_id, draft, usage_known, response.tool_calls, captured)
            if prefix_committed or response.status != "not_started":
                diagnostics = response.diagnostics
                failure = response.failure or (diagnostics.failure if diagnostics is not None else None)
                phase = failure.document()["phase"] if failure is not None else "unclassified"
                logger.warning(
                    "[CompanionV2] 模型结果无法确认 task=%s kind=%s round=%d provider=%s "
                    "phase=%s failure=%s parse_state=%s finish_reason=%s completion_present=%s "
                    "raw_chars=%s parsed_chars=%s visible_chars=%s prefix_committed=%s",
                    run.task.task_id, plan.audit["profile"]["kind"], round_no + 1,
                    provider.binding.provider, phase,
                    failure.value if failure is not None else "unclassified",
                    diagnostics.parse_state if diagnostics is not None else "unavailable",
                    diagnostics.finish_reason if diagnostics is not None else "unavailable",
                    diagnostics.completion_present if diagnostics is not None else "unavailable",
                    diagnostics.raw.chars if diagnostics is not None and diagnostics.raw is not None else "unavailable",
                    diagnostics.parsed.chars if diagnostics is not None and diagnostics.parsed is not None else "unavailable",
                    diagnostics.visible.chars if diagnostics is not None and diagnostics.visible is not None else "unavailable",
                    prefix_committed)
                if (not prefix_committed and not contract_fallback_used
                        and failure is ModelFailure.WIRE_DECODE and usage_known
                        and provider_position + 1 < len(plan.providers)):
                    # 结构化任务拿不到合法 JSON，但这次调用的用量已由 Provider 确认：
                    # 没有已发出的正文前缀，也没有执行过工具，换备用模型重试一次不会
                    # 造成重复投递；这一次的用量照常入账，不计入"未知不可重试"。
                    contract_fallback_used = True
                    logger.warning(
                        "[CompanionV2] 结构化输出不符合契约，用量已确认，切换备用模型重试 "
                        "task=%s provider=%s phase=%s parse_state=%s",
                        run.task.task_id, provider.binding.provider, phase,
                        diagnostics.parse_state if diagnostics is not None else "unavailable")
                    continue
                raise TaskError("model_usage_unknown", "provider processing is unknown; no fallback was dispatched")
        raise TaskError("model_not_started", "configured providers confirmed they did not process the request")

    def _record_proactive_prompt_snapshot(self, task_id, call_id, provider_id, model_name, messages):
        snapshot = _redact_prompt_media(messages)
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO proactive_prompt_snapshots
                (task_id, call_id, provider_id, model_name, prompt_hash, messages_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET provider_id=excluded.provider_id,
                    model_name=excluded.model_name, prompt_hash=excluded.prompt_hash,
                    messages_json=excluded.messages_json, created_at=excluded.created_at""",
                (task_id, call_id, provider_id, model_name, digest(snapshot), encode(snapshot), stamp(self.tasks.now())))
            connection.execute("""DELETE FROM proactive_prompt_snapshots
                WHERE snapshot_id NOT IN (
                    SELECT snapshot_id FROM proactive_prompt_snapshots
                    ORDER BY snapshot_id DESC LIMIT ?
                )""", (PROACTIVE_PROMPT_SNAPSHOT_LIMIT,))
    @outcome
    def observe(self, context: TaskContext, task_id: str) -> dict:
        with self.runtime.read_snapshot():
            require(self.tasks.read(context, task_id))
            plan = self.runtime.read_one("SELECT plan_json FROM model_requests WHERE task_id = ?", (task_id,))
            if plan is None:
                return {"state": "not_observed", "transport": "local_test_adapter", "calls": []}
            audit = json.loads(plan["plan_json"])
            rows = self.runtime.read_all("""SELECT c.*, f.code AS failure_code, f.observed_at AS failure_observed_at,
                    d.summary_json AS response_summary_json
                FROM model_calls c LEFT JOIN model_call_failures f USING(call_id)
                LEFT JOIN model_call_diagnostics d USING(call_id)
                WHERE c.task_id = ? ORDER BY c.position""", (task_id,))
            return {"state": "observed", "transport": audit["transport"], "plan": audit,
                    "calls": [{"call_id": row["call_id"], "position": row["position"], "round": row["position"] // 2,
                               "provider_position": row["position"] % 2, "state": row["state"],
                               "provider": json.loads(row["provider_json"]),
                               "reserved_tokens": row["reserved_tokens"], "reserved_cost_microusd": row["reserved_cost"],
                               "actual_tokens": row["actual_tokens"], "actual_cost_microusd": row["actual_cost"],
                               "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                               "cache_creation_tokens": row["cache_creation_tokens"], "output_tokens": row["output_tokens"],
                               "usage_state": "unknown" if row["actual_tokens"] is None else "settled",
                               "over_reservation": None if row["actual_tokens"] is None else (
                                   row["actual_tokens"] > row["reserved_tokens"] or row["actual_cost"] > row["reserved_cost"]),
                               "provider_latency_ms": row["latency_ms"],
                               "response_diagnostics": checked_response_diagnostics(row["response_summary_json"]),
                               "failure": None if row["failure_code"] is None else {
                                   **ModelFailure(row["failure_code"]).document(),
                                   "observed_at": row["failure_observed_at"]},
                               "latency_basis": "local_wait_until_receipt_or_interruption"} for row in rows]}


class ModelSession:
    """One in-memory continuation controller; a restart cannot reopen its plan."""

    def __init__(self, gateway, run, plan, tools, *, independent_plans=()):
        self.gateway = gateway
        self.run = run
        self.plan = plan
        self.independent_plans = independent_plans
        self.tools = tools
        self.round_no = 0
        self.history = ()
        self.pending = ()
        self.closed = False
        self.busy = False

    def _context_mismatch(self, *fields):
        logger.warning("[CompanionV2] 模型续答上下文不一致 task=%s round=%d fields=%s",
                       self.run.task.task_id, self.round_no + 1, ",".join(fields))
        raise TaskError("model_context_mismatch", "continuation must preserve its original authorized context")

    async def generate(self, request, *, history=(), owner_refresh=None, media_refresh=None, on_reply_prefix=None,
                       reply_prefix_ready=None):
        if self.closed or self.busy or self.round_no > 8:
            raise TaskError("model_session_closed", "original model session cannot generate again")
        original = self.plan.envelope.original
        if self.independent_plans and (request != original or history):
            self._context_mismatch("independent_check_input")
        reasons = [name for name, pair in (("event", (request.event, original.event)),
                ("role_binding", (request.role_binding, original.role_binding)),
                ("audios", (request.audios, original.audios)), ("videos", (request.videos, original.videos)),
                ("native_video_input", (request.native_video_input, original.native_video_input)),
                ("role_instruction", (request.role_instruction, original.role_instruction)),
                ("host_role_instruction", (request.host_role_instruction, original.host_role_instruction)),
                ("context.deferred", (request.context.deferred, original.context.deferred)),
                ("context.unavailable", (request.context.unavailable, original.context.unavailable)),
                ("output_format", (request.output_format, original.output_format)),
                ("response_format", (request.response_format, original.response_format)),
                ("streaming", (request.streaming, original.streaming)),
                ("streaming_mode", (request.streaming_mode, original.streaming_mode)),
                ("response_schema_json", (request.response_schema_json, original.response_schema_json)))
                if pair[0] != pair[1]]
        style_instruction_removed = False
        if not request.instruction.startswith(original.instruction):
            from companion_v2.conversation.style_context import EXPRESSION_STYLE_INSTRUCTION
            style_suffix = "\n" + EXPRESSION_STYLE_INSTRUCTION
            style_instruction_removed = (style_suffix in original.instruction
                and request.instruction == original.instruction.replace(style_suffix, "", 1))
            if not style_instruction_removed:
                reasons.append("instruction")
        if reasons:
            self._context_mismatch(*reasons)
        if (request.images != original.images or request.reply_images != original.reply_images):
            if media_refresh is None or not media_refresh(original, request):
                self._context_mismatch("images")
            if request.images and any("image" not in provider.capabilities for provider in self.plan.providers):
                raise TaskError("model_capability_mismatch", "generated images require image-capable continuation providers")
        if not isinstance(history, tuple) or len(history) < len(self.history) or tuple(item.call for item in history[len(self.history):]) != self.pending:
            raise TaskError("model_tool_results_missing", "every requested tool needs its ordered original result")
        changed = []
        if request.context.materials[:len(original.context.materials)] != original.context.materials:
            changed.append("context.materials")
        if history[:len(self.history)] != self.history:
            changed.append("tool_history")
        if request.instruction != original.instruction and not style_instruction_removed:
            changed.append("instruction")
        if changed and (owner_refresh is None or not owner_refresh(original, request, self.history, history)):
            self._context_mismatch(*changed)
        self.busy = True
        try:
            envelope = replace(self.plan.envelope, original=request, tools=self.tools if self.round_no < 8 else (),
                               tool_history=history)
            with self.gateway.runtime.transaction() as connection:
                self.run.validate()
                self.gateway.router.validate_plan(self.plan)
                connection.execute("""INSERT INTO model_rounds
                    (task_id, round_no, attempt_id, request_hash, state) VALUES (?, ?, ?, ?, 'started')""",
                    (self.run.task.task_id, self.round_no, self.run.claim.attempt_id, digest(envelope)))
            completion = await self.gateway._generate(self.run, self.plan, envelope, self.round_no,
                                                      on_reply_prefix, reply_prefix_ready)
            self.history = history
            self.pending = completion.tool_calls
            self.round_no += 1
            self.closed = completion.draft is not None or completion.captured_output is not None
            if self.independent_plans:
                # These are separate, predeclared classifications of the same
                # sealed input. A failed or unknown call closes the session in
                # the exception path; it can never be replayed as another check.
                self.closed = completion.draft is None or self.round_no >= len(self.independent_plans)
                if not self.closed:
                    self.plan = self.independent_plans[self.round_no]
            return completion
        except BaseException:
            self.closed = True
            raise
        finally:
            self.busy = False
