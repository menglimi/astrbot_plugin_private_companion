"""Receipt-based accounting for native image providers, separate from text routing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType

from companion_v2.execution.models import ProviderBinding, TaskError, digest, encode, stamp

from .failures import ModelFailure
from .models import Amount, CapturedModelOutput, ProviderResponse
from .response_diagnostics import ResponseDiagnostics, checked_response_diagnostics


@dataclass(frozen=True)
class ImageProvider:
    provider_id: str
    operations: tuple[str, ...]


# Native image tools retain image.native ownership; their paid backend has its
# own gateway identity and never becomes a selectable text completion provider.
IMAGE_PROVIDERS = MappingProxyType({
    "nai": ImageProvider("image.nai", ("generate", "edit")),
})
IMAGE_FAILURES = MappingProxyType({
    "nai_plugin_unavailable": ModelFailure.PROVIDER_RECHECK,
    "nai_generation_unconfirmed": ModelFailure.NATIVE_PARSE,
    "nai_output_unavailable": ModelFailure.NATIVE_PARSE,
    "nai_generation_timed_out": ModelFailure.TIMEOUT,
    "nai_generation_failed": ModelFailure.TRANSPORT,
    "unsupported_image_parameter": ModelFailure.PREPARATION,
    "invalid_image_parameter": ModelFailure.PREPARATION,
    "image_authority_revoked": ModelFailure.GATEWAY_RECHECK,
    "image_provider_stopped": ModelFailure.GATEWAY_RECHECK,
    "image_usage_unavailable": ModelFailure.PREPARATION,
})


def image_failure(runtime, task_id):
    row = runtime.read_one("""SELECT d.summary_json FROM model_calls c
        JOIN model_call_diagnostics d USING(call_id)
        WHERE c.task_id = ? AND json_extract(c.provider_json, '$.provider') = 'image.nai'
        ORDER BY c.position LIMIT 1""", (task_id,))
    if row is None:
        return None
    diagnostic = checked_response_diagnostics(row["summary_json"])
    code = diagnostic.get("error_code") if diagnostic else None
    return code if code in IMAGE_FAILURES or code == "nai_generation_interrupted" else None


class ImageModelSession:
    """One trusted child task; neither a model argument nor a persisted callback."""

    def __init__(self, gateway, run, invocation):
        if (run is None or run.supervisor.runtime is not gateway.runtime
                or run.supervisor.generation != gateway.tasks.generation
                or run.task.spec.context != invocation.context
                or run.task.spec.request_ref != invocation.invocation_id):
            raise TaskError("image_model_task_mismatch", "image accounting requires the original child task")
        self.gateway, self.run, self.invocation = gateway, run, invocation
        self.active = True

    def close(self):
        self.active = False

    async def execute(self, name, operation, execute, before_dispatch):
        gateway, run, invocation = self.gateway, self.run, self.invocation
        provider = IMAGE_PROVIDERS.get(name)
        if (not self.active or provider is None or operation not in provider.operations
                or invocation.descriptor.tool.action != "image." + operation):
            raise TaskError("image_model_provider_unavailable", "the image backend is not registered for this task")
        run.validate()
        binding = ProviderBinding("image." + operation, provider.provider_id, "1",
                                  invocation.descriptor.binding.generation)
        call_id = "image-model-" + digest([run.task.task_id, binding])
        audit = {"transport": "native_image", "providers": [binding],
                 "parent_task_id": invocation.parent_task_id, "action": invocation.descriptor.tool.action,
                 "usage_basis": "provider_receipt", "image_quota_basis": "confirmed_successful_operation",
                 "cost_availability": "unavailable", "token_availability": "not_applicable"}
        with gateway.runtime.transaction() as connection:
            run.validate()
            if connection.execute("SELECT 1 FROM model_requests WHERE task_id = ?",
                                  (run.task.task_id,)).fetchone():
                raise TaskError("model_request_already_started", "inspect the original image call instead of resubmitting")
            connection.execute("INSERT INTO model_requests VALUES (?, ?, ?, ?)",
                (run.task.task_id, invocation.arguments_hash, encode(audit), stamp(gateway.tasks.now())))
            # Zero is a reservation only. Actual usage stays NULL unless there
            # is positive evidence no execution occurred; no prices are guessed.
            gateway.ledger.reserve(run, call_id=call_id, position=0, provider=binding, amount=Amount(0, 0))
        dispatched = False
        started = perf_counter()

        async def dispatch():
            nonlocal dispatched
            if not self.active:
                raise TaskError("image_model_session_closed", "the original image dispatch boundary has closed")
            run.validate()
            await before_dispatch()
            gateway.ledger.dispatch(run, call_id)
            dispatched = True

        try:
            # The current NAI port has neither a cost receipt nor a declared
            # charge ceiling. A hard monetary cap cannot be enforced by a zero
            # reservation, so reject before I/O rather than bypass that cap.
            if gateway.ledger.policy.enabled:
                raise TaskError("image_usage_unavailable",
                                "NAI does not report a charge ceiling; a hard monetary budget cannot admit it")
            result = await execute(dispatch)
        except BaseException as error:
            code = getattr(error, "code", "")
            if isinstance(error, asyncio.CancelledError):
                failure, code = ModelFailure.INTERRUPTED, "nai_generation_interrupted"
            else:
                failure = IMAGE_FAILURES.get(code, ModelFailure.TRANSPORT)
                code = code if code in IMAGE_FAILURES else "nai_generation_failed"
            not_started = not dispatched or getattr(error, "unconfirmed", True) is False
            response = ProviderResponse("not_started" if not_started else "unknown",
                usage=Amount(0, 0) if not_started else None, failure=failure,
                receipt_ref=call_id + ":not-started" if not_started else None,
                diagnostics=ResponseDiagnostics(parse_state="not_received", failure=failure, error_code=code))
            gateway.ledger.settle(call_id, binding, response, observed_at=gateway.tasks.now(),
                                  latency_ms=(perf_counter() - started) * 1000)
            raise
        gateway.ledger.settle(call_id, binding, ProviderResponse("generated",
            captured_output=CapturedModelOutput(""),
            receipt_ref=call_id + ":generated",
            diagnostics=ResponseDiagnostics(completion_present=True, finish_reason="stop", parse_state="validated")),
            observed_at=gateway.tasks.now(), latency_ms=(perf_counter() - started) * 1000)
        return result
