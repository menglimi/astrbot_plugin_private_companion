"""Regression tests for state-board maintenance, offload retry parity, failure metadata, and cycle prompt pruning."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import unittest

from companion_v2.agenda import planning_observation
from companion_v2.execution.models import encode, stamp
from companion_v2.host.tick_offload import advance_state_maintenance_async
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, ProviderResponse
from companion_v2.model_gateway.response_diagnostics import ResponseDiagnostics
from companion_v2.model_gateway.task_failure_summary import read_task_failure_summary
from companion_v2.role_work.state import cycle
from companion_v2.role_work.state.cycle_prompts import (
    INSTRUCTION as CYCLE_INSTRUCTION,
    MAINTENANCE as CYCLE_MAINTENANCE,
    PHASE_HINTS,
    maintenance_instruction as cycle_maintenance_instruction,
)
from companion_v2.role_work.state.maintenance import _maintenance_settings
from companion_v2.role_work.state.ownership import role_schedule_key
from tests.test_management_access import NOW
from tests.test_role_cycle import assertion
from tests.test_role_state import StateFixture, state_data
from tests.test_role_tool_loop import answered


class CyclePromptPruningTests(unittest.TestCase):
    def test_maintenance_instruction_injects_only_identified_phase(self):
        disabled = cycle_maintenance_instruction({"applicability": "applicable", "phase": "luteal"}, enabled=False)
        self.assertEqual(disabled, "cycle 使用 null，conditions 不添加 cycle 项。")

        luteal = cycle_maintenance_instruction({"applicability": "applicable", "phase": "luteal"}, enabled=True)
        self.assertIn(CYCLE_INSTRUCTION, luteal)
        self.assertIn(CYCLE_MAINTENANCE, luteal)
        self.assertIn(PHASE_HINTS["luteal"], luteal)
        for other_phase, text in PHASE_HINTS.items():
            if other_phase != "luteal":
                self.assertNotIn(text, luteal)

        not_applicable = cycle_maintenance_instruction({"applicability": "not_applicable"}, enabled=True)
        self.assertIn(CYCLE_INSTRUCTION, not_applicable)
        self.assertIn(CYCLE_MAINTENANCE, not_applicable)
        for text in PHASE_HINTS.values():
            self.assertNotIn(text, not_applicable)

        unknown = cycle_maintenance_instruction({"applicability": "unknown"}, enabled=True)
        self.assertIn("当前阶段未知，不自行补日期。", unknown)
        for text in PHASE_HINTS.values():
            self.assertNotIn(text, unknown)

    def test_maintenance_settings_strips_six_phase_preference_duplicates(self):
        full = dict(cycle.DEFAULTS, cycle_enabled=True, cycle_expression_prompt="保持自然", enabled=True)
        pruned = _maintenance_settings(full)
        self.assertEqual(pruned["cycle_expression_prompt"], "保持自然")
        self.assertTrue(pruned["cycle_enabled"])
        for phase in cycle.PHASES:
            for suffix in ("energy", "mood", "prompt"):
                self.assertNotIn(f"cycle_{phase}_{suffix}", pruned)

        disabled = _maintenance_settings(dict(full, cycle_enabled=False))
        self.assertFalse(disabled["cycle_enabled"])
        self.assertEqual([k for k in disabled if k.startswith("cycle_")], ["cycle_enabled"])


class StateBoardAndOffloadTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def _ready_state(self, **changes):
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, **changes)
        app.state_maintenance.bind(context, "原角色人格提示")
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE role_state_schedules SET next_at = ?",
                ((NOW + timedelta(days=1)).isoformat(),),
            )
        await self.start(host)
        return host, app, context, manager

    async def test_cycle_maintenance_prompt_sends_single_phase_and_no_duplicate_six_phase_settings(self):
        _, app, context, _ = await self._ready_state(
            cycle_enabled=True,
            cycle_mode="six_phase",
            cycle_initialization="first_day",
            cycle_expression_prompt="保持安静陪伴",
        )
        self.model_behavior = lambda env: asyncio.sleep(0, result=answered(encode(state_data(cycle=assertion()))))
        first = await app.state_maintenance.run(context, "原角色人格提示", "init-cycle")
        self.assertEqual(first["state"], "succeeded", first)

        # Advance into follicular phase (day 7 of 28) and run maintenance again.
        self.clock.advance(timedelta(days=6))
        second = await app.state_maintenance.run(context, "原角色人格提示", "follicular-cycle")
        self.assertEqual(second["state"], "succeeded", second)

        envelope = self.called[-1].original
        self.assertIn(PHASE_HINTS["follicular"], envelope.instruction)
        for other_phase, text in PHASE_HINTS.items():
            if other_phase != "follicular":
                self.assertNotIn(text, envelope.instruction, f"unexpected hint for {other_phase}")

        basis = json.loads(next(m.payload for m in envelope.context.materials if m.purpose == "maintenance_basis"))
        self.assertEqual(basis["cycle_timeline"]["phase"], "follicular")
        self.assertIn("phase_expression", basis["cycle_timeline"])
        self.assertEqual(basis["settings"]["cycle_expression_prompt"], "保持安静陪伴")
        for phase in cycle.PHASES:
            for suffix in ("energy", "mood", "prompt"):
                self.assertNotIn(f"cycle_{phase}_{suffix}", basis["settings"])

    async def test_offloaded_state_maintenance_reaps_failure_with_5min_retry_backoff(self):
        _, app, context, _ = await self._ready_state()
        schedule_key = role_schedule_key(context)

        async def fail_run(*_args, **_kwargs):
            return {
                "task_id": "state-task-synthetic",
                "state": "failed",
                "reason": "worker_error",
                "code": "provider_request_failed",
                "http_status": 429,
                "provider": "sta1n/test-primary",
                "fallback_attempted": False,
            }

        worker = asyncio.create_task(fail_run())
        await worker
        app.state_maintenance.workers[context.key] = worker
        app.state_maintenance.worker_schedules[context.key] = schedule_key

        await advance_state_maintenance_async(app.state_maintenance)

        row = self.runtime.read_one(
            "SELECT next_at, result_json FROM role_state_schedules WHERE context_key = ?",
            (schedule_key,),
        )
        self.assertIsNotNone(row)
        saved = json.loads(row["result_json"])
        self.assertEqual(saved["state"], "failed")
        self.assertTrue(saved["retryable"])
        self.assertEqual(saved["code"], "provider_request_failed")
        self.assertEqual(saved["http_status"], 429)
        self.assertEqual(saved["provider"], "sta1n/test-primary")
        self.assertFalse(saved["fallback_attempted"])
        expected_retry = self.clock.now() + timedelta(minutes=5)
        actual_next = datetime.fromisoformat(row["next_at"])
        self.assertEqual(actual_next, expected_retry)

    async def test_maintain_command_survives_unrelated_policy_revision_churn_and_marks_queued(self):
        _, app, context, manager = await self._ready_state()
        status_before = manager.status(self.core).unwrap()
        state = status_before["state"]
        self.assertIn("maintain_token", state)

        # Simulate unrelated policy_revision churn (e.g. another subject's tier updated).
        from companion_v2.identity_policy.models import AuthorizationTier
        self.policy.set_tier(
            self.standard.subject,
            AuthorizationTier.IMPORTANT,
            expected_revision=self.policy.revision,
            observed_at=self.clock.now(),
        )
        fresh_status = manager.status(self.core).unwrap()
        self.assertNotEqual(fresh_status["state"]["view_token"], state["view_token"])
        self.assertEqual(fresh_status["state"]["maintain_token"], state["maintain_token"])

        result = manager.command(
            self.core,
            {
                "action": "maintain",
                "operation_key": "manual-after-policy-bump",
                "expected_revision": state["revision"],
                "view_token": state["maintain_token"],
            },
        )
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(result.unwrap()["state"], "scheduled")

        row = self.runtime.read_one(
            "SELECT next_at, result_json FROM role_state_schedules WHERE context_key = ?",
            (role_schedule_key(context),),
        )
        saved = json.loads(row["result_json"])
        self.assertEqual(saved["state"], "queued")
        self.assertEqual(saved["operation_id"], result.unwrap()["operation_id"])
        self.assertLessEqual(datetime.fromisoformat(row["next_at"]), self.clock.now())

        # Foreign operator must still be rejected with state_view_stale.
        other = self.person("other-admin", AuthorizationTier.CORE)
        denied = manager.command(
            other,
            {
                "action": "maintain",
                "operation_key": "foreign-maintain",
                "expected_revision": state["revision"],
                "view_token": state["maintain_token"],
            },
        )
        self.assertIsNotNone(denied.failure)
        self.assertEqual(denied.failure.code, "state_view_stale")

    async def test_provider_failure_metadata_propagates_to_state_and_planning_results(self):
        _, app, context, _ = await self._ready_state()
        diagnostics = ResponseDiagnostics(
            failure=ModelFailure.TRANSPORT,
            error_type="ClientError",
            status_code=429,
            completion_present=False,
        )
        self.model_behavior = lambda env: asyncio.sleep(
            0,
            result=ProviderResponse(
                "unknown",
                failure=ModelFailure.TRANSPORT,
                diagnostics=diagnostics,
            ),
        )
        outcome = await app.state_maintenance.run(context, "原角色人格提示", "fail-429")
        self.assertEqual(outcome["state"], "failed")
        self.assertEqual(outcome["code"], "provider_request_failed")
        self.assertEqual(outcome["http_status"], 429)
        self.assertIn("provider", outcome)
        self.assertIs(outcome["fallback_attempted"], False)

        summary = read_task_failure_summary(self.runtime, outcome["task_id"])
        self.assertEqual(summary["code"], "provider_request_failed")
        self.assertEqual(summary["http_status"], 429)
        self.assertIs(summary["fallback_attempted"], False)

        # Verify planning_observation.task_result propagates the same safe fields.
        report = planning_observation.task_result(app.calendar_planning, context, outcome["task_id"])
        self.assertIsNotNone(report)
        self.assertEqual(report["code"], "provider_request_failed")
        self.assertEqual(report["http_status"], 429)
        self.assertEqual(report["provider"], outcome["provider"])
        self.assertIs(report["fallback_attempted"], False)


if __name__ == "__main__":
    unittest.main()
