"""Regression tests for gateway 429/5xx fallback, #901 startup recovery, diagnostics, startup & schema."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from astrbot_host.bootstrap_provider import resolve_bootstrap_provider
from astrbot_host.core_reply_tool_execution import CoreReplyToolExecutor
from astrbot_host.multi_controller import AstrBotMultiController
from companion_v2.adapters.astrbot.provider_transport import (
    TRANSIENT_UPSTREAM_FALLBACK_STATUSES,
    rejected_before_generation,
    transient_upstream_fallback_status,
)
from companion_v2.conversation.external_tools import core_tool_kind
from companion_v2.execution.models import (
    Lane,
    SideEffect,
    TaskDeferred,
    TaskError,
    TaskResult,
    encode,
    stamp,
)
from companion_v2.execution.runner import LocalTaskRunner
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, BudgetPolicy, ProviderResponse
from companion_v2.model_gateway.response_diagnostics import ResponseDiagnostics
from companion_v2.model_gateway.service import LocalModelGateway
from tests.test_execution import ExecutionFixture, NOW
from tests.test_external_tools import ExternalFixture
from tests.test_model_gateway import ModelFixture


class _RecordingGuard:
    def __init__(self):
        self.finished_calls = []

    def capture(self, spec, definition, *, submitting=False):
        return None

    def assert_current(self, spec, lease):
        return None

    def finished(self, spec, definition, state):
        self.finished_calls.append((spec.task_id, definition.kind, state))


class Issue901StartupRecoveryTests(ExecutionFixture):
    """Unregistered dynamic task kinds and corrupt rows must never deadlock startup recovery."""

    def test_recover_survives_unregistered_legacy_core_tool_kind_with_execution_guard(self):
        # Submit and start a healthy task on generation 1, plus inject an orphaned
        # pre-upgrade dynamic kind ``astrbot.core-tool.<sha256>`` in both running
        # and admitted states.
        running_healthy = self.running("healthy-running")
        self.assertEqual(running_healthy.task_id, "healthy-running")

        legacy_running_kind = "astrbot.core-tool." + "a" * 64
        legacy_admitted_kind = "astrbot.core-tool." + "b" * 64
        healthy_row = self.runtime.read_one(
            "SELECT * FROM execution_tasks WHERE task_id = ?", ("healthy-running",)
        )
        base_data = json.loads(healthy_row["record_json"])

        with self.runtime.transaction() as connection:
            for tid, kind, state, started_at in (
                ("orphan-running", legacy_running_kind, "running", stamp(NOW)),
                ("orphan-admitted", legacy_admitted_kind, "admitted", None),
            ):
                data = json.loads(json.dumps(base_data))
                data["spec"]["task_id"] = tid
                data["spec"]["kind"] = kind
                data["definition"]["kind"] = kind
                data["definition"]["side_effect"] = SideEffect.NONE.value
                attempt_id = "att-" + tid
                connection.execute(
                    "INSERT INTO execution_tasks VALUES (?, ?, ?, ?, ?, ?, 1, ?, 1, 1, ?, ?, ?, ?, ?, ?)",
                    (
                        tid,
                        healthy_row["origin_key"],
                        kind,
                        Lane.FOREGROUND.value,
                        "dedupe-" + tid,
                        "req-" + tid,
                        state,
                        attempt_id,
                        stamp(NOW),
                        stamp(NOW + timedelta(minutes=5)),
                        stamp(NOW),
                        stamp(NOW),
                        encode(data),
                    ),
                )
                attempt_json = encode({
                    "claimed_at": stamp(NOW),
                    "started_at": started_at,
                    "finished_at": None,
                    "effect_started": False,
                    "queue_ms": 0.0,
                    "execution_ms": None,
                    "stopped_proof": None,
                })
                connection.execute(
                    "INSERT INTO execution_attempts VALUES (?, ?, 1, 1, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        attempt_id,
                        tid,
                        Lane.FOREGROUND.value,
                        kind,
                        healthy_row["origin_key"],
                        data["scope_key"],
                        stamp(NOW + timedelta(seconds=30)),
                        attempt_json,
                    ),
                )

        guard = _RecordingGuard()
        restarted = self.supervisor(self.open_runtime())
        restarted.bind_execution_guard(guard)
        restarted.start(expected_generation=1).unwrap()

        with self.assertLogs("companion_v2.execution.service", level="WARNING") as captured:
            recovered = restarted.recover().unwrap()

        self.assertEqual(set(recovered), {"healthy-running", "orphan-running", "orphan-admitted"})
        # Second sweep sees zero remaining stuck tasks.
        self.assertEqual(restarted.recover().unwrap(), ())
        self.assertEqual(
            restarted.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?", ("orphan-running",))["state"],
            "uncertain",
        )
        self.assertEqual(
            restarted.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?", ("orphan-admitted",))["state"],
            "failed",
        )
        self.assertEqual(
            restarted.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?", ("healthy-running",))["state"],
            "uncertain",
        )
        # Execution guard callback ran for the healthy registered task and was skipped for unregistered kinds.
        self.assertEqual(guard.finished_calls, [("healthy-running", "fixture.read", "uncertain")])
        self.assertIn("未注册类型", "\n".join(captured.output))

    def test_recover_isolates_corrupt_record_without_blocking_healthy_tasks(self):
        self.running("healthy-1")
        self.running("corrupt-1", kind="fixture.background")
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE execution_tasks SET record_json = '{not-valid-json' WHERE task_id = 'corrupt-1'"
            )

        restarted = self.supervisor(self.open_runtime())
        restarted.start(expected_generation=1).unwrap()
        with self.assertLogs("companion_v2.execution.service", level="WARNING") as captured:
            recovered = restarted.recover().unwrap()

        self.assertEqual(set(recovered), {"healthy-1", "corrupt-1"})
        self.assertEqual(restarted.recover().unwrap(), ())
        corrupt_row = restarted.runtime.read_one(
            "SELECT state, record_json FROM execution_tasks WHERE task_id = 'corrupt-1'", ()
        )
        self.assertEqual(corrupt_row["state"], "uncertain")
        self.assertEqual(json.loads(corrupt_row["record_json"])["reason"], "recovery_corrupt_record")
        self.assertEqual(
            restarted.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = 'healthy-1'", ())["state"],
            "uncertain",
        )
        self.assertIn("corrupt-1", "\n".join(captured.output))


class StaticCoreToolKindTests(ExternalFixture, unittest.IsolatedAsyncioTestCase):
    def test_core_reply_tool_executor_uses_stable_action_kind_matching_external_registrations(self):
        app = NS(runtime=self.runtime, tasks=self.tasks, messages=self.messages, tools=self.tools,
                 require_running=lambda: None)
        executor = CoreReplyToolExecutor(app)
        self.assertEqual(executor.kinds["device.play"], core_tool_kind("device.play"))
        self.assertEqual(executor.kinds["device.play"], "astrbot.core-tool.device.play")
        expected_kinds = {reg.definition.kind for reg in self.tools.external.core_tool_registrations}
        self.assertEqual(set(executor.kinds.values()), expected_kinds)

        # Even if provider binding generation changes on reload, the task kind stays stable.
        self.bindings["device.play"] = replace(self.bindings["device.play"], generation="reloaded-gen")
        reloaded = CoreReplyToolExecutor(app)
        self.assertEqual(reloaded.kinds["device.play"], "astrbot.core-tool.device.play")


class GatewayTransientFallbackAndDiagnosticsTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def test_transient_status_helper_keeps_rejection_contract_separate(self):
        for code in (429, 500, 502, 503, 504):
            self.assertIn(code, TRANSIENT_UPSTREAM_FALLBACK_STATUSES)
            self.assertTrue(transient_upstream_fallback_status(code))
            self.assertFalse(rejected_before_generation(NS(status_code=code)))
        for code in (400, 401, 403, 404, 422, 200, None, True, "500"):
            self.assertFalse(transient_upstream_fallback_status(code))

    async def test_transient_429_and_5xx_fallback_for_background_and_general_tasks_without_budget_leak(self):
        for status_code in (429, 500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                self.called.clear()
                self.responses["primary"] = ProviderResponse(
                    "unknown",
                    failure=ModelFailure.TRANSPORT,
                    diagnostics=ResponseDiagnostics(
                        completion_present=False,
                        failure=ModelFailure.TRANSPORT,
                        error_type="ServerError",
                        status_code=status_code,
                    ),
                )
                task_id = f"task-transient-{status_code}"
                # Tight budget: only room for 1 reservation (60 tokens, ceiling=100).
                # Releasing the failed primary hold lets the fallback succeed instead of
                # failing with model_budget_exhausted.
                tight = Amount(100, 1000)
                self.ledger = BudgetLedger(self.runtime, BudgetPolicy(tight, tight, tight, tight, tight))
                self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)
                run = self.model_run(task_id)
                with self.assertLogs("companion_v2.model_gateway.service", level="WARNING") as captured:
                    completion = await self.gateway.generate(run, self.request())
                self.tasks.finish(run.claim, TaskResult("succeeded")).unwrap()
                self.assertEqual(completion.draft.text, "private draft")
                self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
                calls = self.gateway.observe(self.context, task_id).unwrap()["calls"]
                self.assertEqual([(c["state"], c["actual_tokens"]) for c in calls], [("unknown", None), ("generated", 15)])
                # No leaked held_tokens left behind after fallback.
                self.assertTrue(all(a["held_tokens"] == 0 for a in self.accounts()))
                self.assertIn(f"status_code={status_code}", "\n".join(captured.output))
                # Clean accounts for next subtest.
                with self.runtime.transaction() as conn:
                    conn.execute("DELETE FROM model_call_accounts")
                    conn.execute("DELETE FROM model_budget_accounts")

    async def test_unknown_with_completion_present_or_no_status_does_not_fallback(self):
        for label, diag in (
            ("completion_present", ResponseDiagnostics(True, "stop", failure=ModelFailure.TRANSPORT, status_code=500)),
            ("read_timeout_no_status", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ReadTimeout")),
        ):
            with self.subTest(case=label):
                self.called.clear()
                self.responses["primary"] = ProviderResponse("unknown", failure=ModelFailure.TRANSPORT, diagnostics=diag)
                run = self.model_run("task-" + label)
                with self.assertRaises(TaskError) as raised:
                    await self.gateway.generate(run, self.request())
                self.tasks.stop(run.claim, reason="worker_error").unwrap()
                self.assertEqual(raised.exception.code, "model_usage_unknown")
                self.assertEqual([name for name, _ in self.called], ["primary"])

    async def test_phone_429_without_fallback_preserves_phone_model_rate_limited_code(self):
        self.patch_model(fallback="")
        self.responses["primary"] = ProviderResponse(
            "unknown",
            failure=ModelFailure.TRANSPORT,
            diagnostics=ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, status_code=429),
        )
        run = self.model_run("phone-429")
        with self.assertRaises(TaskError) as raised:
            await self.gateway.generate(
                run, replace(self.request(), output_format="phone_action")
            )
        self.tasks.stop(run.claim, reason="worker_error").unwrap()
        self.assertEqual(raised.exception.code, "phone_model_rate_limited")

    async def test_not_started_context_blocker_fail_fast_and_classifies_cause_and_hint(self):
        self.responses["primary"] = ProviderResponse(
            "not_started",
            usage=Amount(0, 0),
            receipt_ref="r-primary",
            failure=ModelFailure.PREPARATION,
            diagnostics=ResponseDiagnostics(
                False,
                failure=ModelFailure.PREPARATION,
                error_type="TaskError",
                error_code="host_persona_window_unbound",
            ),
        )
        with self.assertLogs("companion_v2.model_gateway.service", level="WARNING") as captured:
            with self.assertRaises(TaskError) as raised:
                await self.gateway.generate(self.model_run("task-persona"), self.request())
        self.assertEqual(raised.exception.code, "model_not_started")
        # Fail-fast: fallback provider must NOT be called for a persona/binding mismatch.
        self.assertEqual([name for name, _ in self.called], ["primary"])
        details = raised.exception.details
        self.assertEqual(details["cause"], "persona_binding_mismatch")
        self.assertEqual(details["task_error_code"], "host_persona_window_unbound")
        self.assertEqual(details["provider"], "primary")
        self.assertIn("账号与会话", details["hint"])
        self.assertIn("cause=persona_binding_mismatch", "\n".join(captured.output))

    async def test_not_started_classifies_connect_timeout_404_auth_and_400(self):
        cases = [
            ("connect_timeout", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ConnectTimeout"), None),
            ("connect_failed", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ConnectError"), None),
            ("model_or_endpoint_not_found", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ClientError", status_code=404), 404),
            ("provider_auth_rejected", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ClientError", status_code=401), 401),
            ("provider_request_rejected", ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT, error_type="ClientError", status_code=422), 422),
            ("provider_config_changed", ResponseDiagnostics(False, failure=ModelFailure.PREPARATION, error_type="TaskError", error_code="host_provider_changed"), None),
        ]
        for expected_cause, diag, expected_status in cases:
            with self.subTest(expected_cause=expected_cause):
                self.called.clear()
                fail_resp = ProviderResponse(
                    "not_started", usage=Amount(0, 0), receipt_ref="r", failure=diag.failure, diagnostics=diag
                )
                self.responses["primary"] = fail_resp
                self.responses["fallback"] = fail_resp
                run = self.model_run("task-" + expected_cause)
                with self.assertRaises(TaskError) as raised:
                    await self.gateway.generate(run, self.request())
                self.tasks.stop(run.claim, reason="worker_error").unwrap()
                self.assertEqual(raised.exception.code, "model_not_started")
                self.assertEqual(raised.exception.details["cause"], expected_cause)
                self.assertEqual(raised.exception.details.get("status_code"), expected_status)
                self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])

    async def test_background_host_account_offline_defers_softly_and_runner_logs_info(self):
        self.responses["primary"] = ProviderResponse(
            "not_started",
            usage=Amount(0, 0),
            receipt_ref="r-offline",
            failure=ModelFailure.PREPARATION,
            diagnostics=ResponseDiagnostics(
                False, failure=ModelFailure.PREPARATION, error_type="TaskError", error_code="host_account_offline"
            ),
        )
        # Verify foreground raises model_not_started with cause=host_account_offline and Fail-Fast.
        fg_run = self.model_run("fg-offline")
        with self.assertRaises(TaskError) as fg_err:
            await self.gateway.generate(fg_run, self.request())
        self.tasks.stop(fg_run.claim, reason="worker_error").unwrap()
        self.assertNotIsInstance(fg_err.exception, TaskDeferred)
        self.assertEqual(fg_err.exception.code, "model_not_started")
        self.assertEqual(fg_err.exception.details["cause"], "host_account_offline")
        self.assertEqual([name for name, _ in self.called], ["primary"])

        # Verify background task converts host_account_offline to TaskDeferred(+15s) and runner logs INFO.
        self.called.clear()
        bg_run = self.model_run("bg-offline", kind="fixture.background")
        with self.assertRaises(TaskDeferred) as bg_err:
            await self.gateway.generate(bg_run, self.request())
        self.tasks.stop(bg_run.claim, reason="worker_error").unwrap()
        self.assertEqual(bg_err.exception.code, "host_account_offline")
        self.assertEqual(bg_err.exception.retry_at, self.clock.now() + timedelta(seconds=15))
        self.assertEqual([name for name, _ in self.called], ["primary"])

        async def bg_handler(_run):
            raise TaskDeferred("host_account_offline", "private offline reason", retry_at=self.clock.now() + timedelta(seconds=15))

        runner = LocalTaskRunner(
            self.tasks,
            {entry.definition.kind: bg_handler for entry in self.tasks.registrations.values()},
            check_interval=0.005,
        )
        self.submit("runner-bg-offline", kind="fixture.background")
        with self.assertLogs("companion_v2.execution.runner", level="INFO") as captured:
            run_result = (await runner.run_once(Lane.BACKGROUND)).unwrap()
        self.assertEqual(run_result.failure_code, "host_account_offline")
        self.assertTrue(all(rec.levelname == "INFO" for rec in captured.records))
        self.assertIn("后台任务等待账号重连", "\n".join(captured.output))
        self.assertNotIn("private offline reason", "\n".join(captured.output))

    async def test_runner_logs_safe_task_error_details_without_leaking_private_reason(self):
        async def failing_handler(_run):
            raise TaskError(
                "model_not_started",
                "PRIVATE_PROMPT_OR_SECRET",
                details={
                    "cause": "persona_binding_mismatch",
                    "provider": "provider-a/main",
                    "model": "example-model",
                    "task_error_code": "host_persona_changed",
                    "hint": "请检查账号与会话映射",
                    "secret_field": "MUST_NOT_APPEAR",
                },
            )

        runner = LocalTaskRunner(
            self.tasks,
            {entry.definition.kind: failing_handler for entry in self.tasks.registrations.values()},
            check_interval=0.005,
        )
        self.submit("runner-diag")
        with self.assertLogs("companion_v2.execution.runner", level="ERROR") as captured:
            run_result = (await runner.run_once(Lane.FOREGROUND)).unwrap()
        self.assertEqual(run_result.failure_code, "model_not_started")
        out = "\n".join(captured.output)
        self.assertIn("cause=persona_binding_mismatch", out)
        self.assertIn("provider=provider-a/main", out)
        self.assertIn("model=example-model", out)
        self.assertIn("task_error_code=host_persona_changed", out)
        self.assertIn("hint=请检查账号与会话映射", out)
        self.assertNotIn("PRIVATE_PROMPT_OR_SECRET", out)
        self.assertNotIn("MUST_NOT_APPEAR", out)


class StartupBootstrapAndSchemaTests(unittest.TestCase):
    def test_empty_catalog_does_not_probe_get_provider_by_id_or_get_using_provider(self):
        get_by_id = Mock(side_effect=AssertionError("must not probe when catalog is empty"))
        get_using = Mock(side_effect=AssertionError("must not probe active provider when catalog is empty"))
        empty_ctx = NS(
            get_all_providers=lambda: [],
            get_provider_by_id=get_by_id,
            get_using_provider=get_using,
        )
        result = resolve_bootstrap_provider(empty_ctx, None, "provider-a/example-model")
        self.assertEqual((result.provider_id, result.source), ("", "unavailable"))
        get_by_id.assert_not_called()
        get_using.assert_not_called()

    def test_multi_controller_resolve_settings_skips_bootstrap_when_provider_id_configured(self):
        config = {
            "multi_host": {
                "enabled": True,
                "installation_id": "shared-main",
                "targets": [{
                    "target_id": "t1",
                    "platform_id": "qq-main",
                    "account_id": "100",
                    "bot_id": "bot-a",
                    "participant_id": "200",
                }],
                "personas": [{
                    "bot_id": "bot-a",
                    "persona_id": "original",
                    "role_id": "role-a",
                }],
            },
            "model": {"provider_id": "provider-a/example-model"},
        }
        empty_ctx = NS(
            get_all_providers=lambda: [],
            get_provider_by_id=Mock(side_effect=AssertionError("must not call get_provider_by_id")),
            get_using_provider=Mock(side_effect=AssertionError("must not call get_using_provider")),
        )
        controller = AstrBotMultiController.__new__(AstrBotMultiController)
        controller.context = empty_ctx
        controller.native_types = NS(is_text_provider=None)
        controller.data_root = Path(".")
        settings, _root, _legacy = controller._resolve_settings(config)
        self.assertEqual(settings.common.provider_id, "provider-a/example-model")
        empty_ctx.get_provider_by_id.assert_not_called()
        empty_ctx.get_using_provider.assert_not_called()

    def test_conf_schema_has_no_duplicate_keys_and_preserves_semantic_debounce_hints(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        duplicates = []

        def reject_dups(pairs):
            seen = {}
            for key, value in pairs:
                if key in seen:
                    duplicates.append(key)
                seen[key] = value
            return seen

        parsed = json.loads(schema_path.read_text(encoding="utf-8"), object_pairs_hook=reject_dups)
        self.assertEqual(duplicates, [])
        self.assertIn("private_debounce_semantic", parsed)
        self.assertIn("onnxruntime", parsed["private_debounce_semantic"].get("hint", ""))
        self.assertIn("threshold", parsed["private_debounce_semantic"]["items"])


if __name__ == "__main__":
    unittest.main()
