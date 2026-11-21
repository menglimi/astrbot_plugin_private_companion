"""Model page management uses the registered V2 router and scoped configuration."""

import unittest
import json
from dataclasses import asdict, replace
from datetime import timedelta
from types import MappingProxyType

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.media.context_caption import context_caption_profile
from companion_v2.conversation.media.image_narration import image_narration_profile
from companion_v2.diagnostics.inbound import InboundNoReplyLedger
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.response_diagnostics import ResponseDiagnostics, ResponseTextSummary
from companion_v2.model_gateway.routing import configuration_fields
from tests.test_host_application import HostFixture


class ModelManagementTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles = (*self.profiles,
            context_caption_profile("primary", max_output_tokens=100),
            image_narration_profile("primary", max_output_tokens=100))
        self.registry = FieldRegistry(version=self.registry.version, fields=configuration_fields(self.profiles))
        self.providers = tuple(replace(provider, capabilities=(*provider.capabilities, "image"))
                               for provider in self.providers)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        batch = self.host_instance.receive(self.event("model-settings-context")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        self.role_context = self.context(self.app, batch)
        self.manager = ModelManagement(self.app, self.role_context)

    def request(self, status, *, changes=None, reset=None, key="model-settings"):
        task = status["tasks"][0]
        return {"kind": task["kind"], "layer": status["configuration_layer"],
                "operation_key": key, "edit_token": task["edit_token"],
                "changes": changes or {}, "reset": reset or []}

    async def test_pending_plugin_reservations_are_visible_without_becoming_actual_tokens(self):
        call = self.app.runtime.read_one("SELECT * FROM model_calls LIMIT 1", ())
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET kind = 'calendar.plan' WHERE task_id = ?",
                               (call["task_id"],))
            connection.execute("UPDATE model_calls SET state='unknown',actual_tokens=NULL,actual_cost=NULL WHERE call_id=?",
                               (call["call_id"],))
        report = self.manager.maintenance(self.core).unwrap()
        budget = report["budget"]
        self.assertEqual((budget["spent_tokens"], budget["pending_usage_calls"]), (0, 1))
        self.assertEqual(budget["pending_usage_tokens"], call["reserved_tokens"])
        self.assertEqual(budget["reservation_sources"][0]["kind"], "calendar.plan")
        recent = report["usage_sources"]["plugin"]["recent"][0]
        self.assertIsNone(recent["tokens"])
        self.assertEqual(recent["held_tokens"], call["reserved_tokens"])
        self.assertEqual(recent["usage_state"], "awaiting_receipt")

    async def test_released_reservation_leaves_pending_usage_and_is_reported(self):
        call = self.app.runtime.read_one("SELECT * FROM model_calls LIMIT 1", ())
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET kind = 'calendar.plan' WHERE task_id = ?",
                               (call["task_id"],))
            connection.execute("""UPDATE model_calls SET state = 'unknown', actual_tokens = NULL,
                actual_cost = NULL, receipt_ref = NULL WHERE call_id = ?""", (call["call_id"],))
        released = self.app.gateway.ledger.release(call["call_id"], observed_at=self.app.now())
        self.assertEqual(released.tokens, call["reserved_tokens"])

        report = self.manager.maintenance(self.core).unwrap()
        budget = report["budget"]
        self.assertEqual((budget["pending_usage_calls"], budget["pending_usage_tokens"]), (0, 0))
        self.assertEqual((budget["released_calls"], budget["released_tokens"]), (1, call["reserved_tokens"]))
        self.assertEqual(budget["held_tokens"], 0)
        recent = next(item for item in report["usage_sources"]["plugin"]["recent"]
                      if item["usage_state"] == "released")
        self.assertEqual((recent["held_tokens"], recent["reserved_tokens"]),
                         (0, call["reserved_tokens"]))

    async def test_disabled_hard_budget_is_reported_without_a_fictitious_remaining_quota(self):
        ledger = self.app.gateway.ledger
        ledger.policy = replace(ledger.policy, enabled=False)
        report = self.manager.maintenance(self.core).unwrap()
        self.assertFalse(report["budget"]["hard_limit_enabled"])
        self.assertIsNone(report["budget"]["remaining_tokens"])
        self.assertIn("limit_tokens", report["budget"])
        self.assertIn("held_tokens", report["budget"])
        self.assertGreater(report["usage_sources"]["conversation"]["totals"]["calls"], 0)

    async def test_maintenance_includes_body_free_response_stage_summaries(self):
        summary = ResponseDiagnostics(True, "stop", "native_parsed",
            raw=ResponseTextSummary.from_text("private raw model content"),
            parsed=ResponseTextSummary.from_text("[]"))
        call = self.app.runtime.read_one("SELECT call_id FROM model_calls LIMIT 1", ())
        with self.app.runtime.transaction() as connection:
            connection.execute("INSERT INTO model_call_diagnostics VALUES (?, ?, ?)",
                (call["call_id"], encode(summary.to_record()), self.app.now().isoformat()))
        data = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(data["recent"][0]["response_diagnostics"], summary.to_record())
        self.assertEqual(data["usage_sources"]["conversation"]["recent"][0]["response_diagnostics"], summary.to_record())
        self.assertNotIn("private raw model content", json.dumps(data))

    async def test_reply_efficiency_exposes_safe_failure_and_response_metadata_per_call(self):
        call = self.app.runtime.read_one("SELECT call_id, task_id FROM model_calls LIMIT 1", ())
        summary = ResponseDiagnostics(False, failure=ModelFailure.TRANSPORT,
            error_type="RateLimitError", error_code="provider_rate_limited", status_code=429)
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE model_calls SET state = 'unknown', actual_tokens = NULL, actual_cost = NULL "
                               "WHERE call_id = ?", (call["call_id"],))
            connection.execute("INSERT INTO model_call_failures(call_id, code, observed_at) VALUES (?, ?, ?)",
                (call["call_id"], ModelFailure.TRANSPORT.value, self.app.now().isoformat()))
            connection.execute("INSERT INTO model_call_diagnostics(call_id, summary_json, observed_at) VALUES (?, ?, ?)",
                (call["call_id"], encode(summary.to_record()), self.app.now().isoformat()))

        sample = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]["recent"][0]
        model_call = sample["trace"]["model_calls"][0]
        self.assertEqual(model_call["failure_code"], "provider_request_failed")
        self.assertEqual(model_call["response_diagnostics"], summary.to_record())
        self.assertIn("RateLimitError", json.dumps(sample))
        self.assertNotIn("private", json.dumps(sample))

    async def test_reply_efficiency_uses_platform_receipts_for_end_to_end_latency(self):
        batch = self.app.runtime.read_one("SELECT batch_id, generation_task_id FROM conversation_batches LIMIT 1", ())
        parts = self.app.runtime.read_all("SELECT part_id FROM conversation_parts WHERE batch_id = ?",
                                          (batch["batch_id"],))
        self.assertTrue(parts)
        now = self.app.now()
        received = (now - timedelta(seconds=10)).isoformat()
        model_started = (now - timedelta(seconds=8)).isoformat()
        model_finished = (now - timedelta(seconds=7)).isoformat()
        dispatched = (now - timedelta(seconds=6)).isoformat()
        delivered = (now - timedelta(seconds=5)).isoformat()
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET created_at = ?, part_count = ? WHERE batch_id = ?",
                               (received, len(parts), batch["batch_id"]))
            connection.execute("UPDATE conversation_inputs SET event_json = json_set(event_json, '$.received_at', ?) "
                               "WHERE batch_id = ?", (received, batch["batch_id"]))
            connection.execute("UPDATE model_calls SET started_at = ?, finished_at = ? WHERE task_id = ?",
                               (model_started, model_finished, batch["generation_task_id"]))
            connection.execute("""UPDATE conversation_parts SET state = 'delivered', dispatched_at = ?, delivered_at = ?
                WHERE batch_id = ?""", (dispatched, delivered, batch["batch_id"]))

        report = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]
        sample = report["recent"][0]
        self.assertEqual(report["window_days"], 7)
        self.assertEqual(report["summary"]["samples"], 1)
        self.assertEqual(report["coverage"]["received"], 1)
        self.assertEqual(sample["outcome"], "completed")
        self.assertEqual(sample["stages"], {"pre_model_ms": 2000, "last_input_to_model_ms": 2000, "model_ms": 1000,
            "post_model_ms": 1000, "first_reply_ms": 5000, "delivery_ms": 1000, "end_to_end_ms": 5000})
        self.assertEqual(report["coverage"]["final_delivered"], 1)

        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_parts SET state = 'unknown', delivered_at = NULL WHERE batch_id = ?",
                               (batch["batch_id"],))
        unresolved = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]["recent"][0]
        self.assertEqual(unresolved["outcome"], "unknown")
        self.assertIsNone(unresolved["stages"]["first_reply_ms"])
        self.assertIsNone(unresolved["stages"]["end_to_end_ms"])
        self.assertTrue(unresolved["observed"]["model_finished"])
        self.assertFalse(unresolved["observed"]["first_delivered"])
        self.assertEqual(self.manager.maintenance(self.core).unwrap()["reply_efficiency"]["coverage"]["final_delivered"], 0)

        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE model_calls SET state = 'dispatched', finished_at = NULL WHERE task_id = ?",
                               (batch["generation_task_id"],))
        active = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]["recent"][0]
        self.assertEqual(active["active_model_calls"], 1)
        self.assertFalse(active["observed"]["model_finished"])
        self.assertIsNone(active["stages"]["model_ms"])

    async def test_reply_efficiency_projects_laya_calls_separately(self):
        from companion_v2.execution.models import encode

        batch = self.app.runtime.read_one("SELECT batch_id FROM conversation_batches LIMIT 1", ())
        observed_at = self.app.now().isoformat()
        decision = {"turn_state": "complete", "action": "reply", "reply_intent": "answer",
                    "reply_style": "normal", "reply_form": "text", "segment_mode": "natural",
                    "tool_need": "none", "reason_codes": []}
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, batch_id, source, status, mode, reason_code, reason, elapsed_ms,
                 decision_json, enabled_fields_json, observed_at)
                VALUES (?, ?, ?, 'conversation', 'ok', 'local', NULL, NULL, 137, ?, ?, ?)""",
                ("e" * 32, self.role_context.key, batch["batch_id"], encode(decision), encode({}), observed_at))

        report = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]
        sample = next(item for item in report["recent"] if item["batch_id"] == batch["batch_id"])
        self.assertEqual(report["summary"]["laya_calls"], 1)
        self.assertEqual(report["summary"]["laya_succeeded"], 1)
        self.assertEqual(report["summary"]["median_laya_ms"], 137)
        self.assertEqual(sample["trace"]["laya"]["elapsed_ms"], 137)
        self.assertEqual(sample["trace"]["laya"]["events"][0]["decision"], decision)

    async def test_reply_efficiency_includes_current_role_private_sessions_only(self):
        async def complete_session(identifier, session_id):
            scope = replace(self.role_context.scope, native_scope_id=session_id,
                            conversation_id=session_id, session_id=session_id)
            self.grant(self.important, scope, "text")
            batch = self.host_instance.receive(self.event(identifier, scope=scope)).unwrap()
            self.advance(self.app)
            await self.done(self.app, batch)
            return batch

        included_batch = await complete_session("same-role-session", "same-role-session")
        excluded_batch = await complete_session("other-role-session", "other-role-session")
        context = json.loads(self.app.messages.batch(excluded_batch)["context_json"])
        context["role_binding"]["role"]["subject_id"] = "another-role"
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET context_json = ? WHERE batch_id = ?",
                               (encode(context), excluded_batch))

        report = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]
        batch_ids = {item["batch_id"] for item in report["recent"]}
        original = self.app.runtime.read_one("SELECT batch_id FROM conversation_batches "
                                             "WHERE origin_key = ? ORDER BY created_at LIMIT 1",
                                             (self.role_context.key,))["batch_id"]
        self.assertEqual(report["summary"]["samples"], 2)
        self.assertIn(original, batch_ids)
        self.assertIn(included_batch, batch_ids)
        self.assertNotIn(excluded_batch, batch_ids)

    async def test_reply_efficiency_projects_injections_and_extension_timing_without_payloads(self):
        batch = self.app.runtime.read_one("SELECT batch_id, generation_task_id, contributions_json "
                                           "FROM conversation_batches LIMIT 1", ())
        call = self.app.runtime.read_one("SELECT call_id, attempt_id FROM model_calls WHERE task_id = ? LIMIT 1",
                                         (batch["generation_task_id"],))
        self.assertIsNotNone(call)
        parts = self.app.runtime.read_all("SELECT part_id FROM conversation_parts WHERE batch_id = ?",
                                          (batch["batch_id"],))
        now = self.app.now()
        received = (now - timedelta(seconds=10)).isoformat()
        flow_due = (now - timedelta(seconds=8500 / 1000)).isoformat()
        worker_started = (now - timedelta(seconds=8)).isoformat()
        model_started = (now - timedelta(seconds=7500 / 1000)).isoformat()
        model_finished = (now - timedelta(seconds=6500 / 1000)).isoformat()
        plugin_started = (now - timedelta(seconds=7)).isoformat()
        plugin_finished = (now - timedelta(seconds=6800 / 1000)).isoformat()
        tool_started = (now - timedelta(seconds=7100 / 1000)).isoformat()
        tool_finished = (now - timedelta(seconds=6700 / 1000)).isoformat()
        dispatched = (now - timedelta(seconds=6)).isoformat()
        delivered = (now - timedelta(seconds=5)).isoformat()
        contributions = json.loads(batch["contributions_json"] or "{}")
        contributions["materials"] = [{"owner": "calendar.extension", "purpose": "daily_agenda",
                                        "state": "ready", "payload": "private injected content"}]
        plan = {"tool_actions": ["calendar.query", "memory.search"], "prompt": "private model prompt"}
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET created_at = ?, part_count = ?, contributions_json = ? "
                               "WHERE batch_id = ?",
                               (received, len(parts), encode(contributions), batch["batch_id"]))
            connection.execute("UPDATE conversation_inputs SET event_json = json_set(event_json, '$.received_at', ?) "
                               "WHERE batch_id = ?", (received, batch["batch_id"]))
            connection.execute("UPDATE conversation_flows SET due_at = ? WHERE batch_id = ?",
                               (flow_due, batch["batch_id"]))
            connection.execute("UPDATE execution_events SET observed_at = ? WHERE task_id = ? AND state = 'running'",
                               (worker_started, batch["generation_task_id"]))
            connection.execute("UPDATE model_requests SET plan_json = ? WHERE task_id = ?",
                               (encode(plan), batch["generation_task_id"]))
            connection.execute("UPDATE model_calls SET started_at = ?, finished_at = ?, latency_ms = NULL "
                               "WHERE task_id = ?", (model_started, model_finished, batch["generation_task_id"]))
            connection.execute("""UPDATE conversation_parts SET state = 'delivered', dispatched_at = ?, delivered_at = ?
                WHERE batch_id = ?""", (dispatched, delivered, batch["batch_id"]))
            connection.execute("""INSERT INTO conversation_tool_calls
                (task_id, tool_call_id, batch_id, sequence, model_call_id, attempt_id, action, arguments_json,
                 state, effect, created_at, finished_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'succeeded', 'none', ?, ?)""",
                (batch["generation_task_id"], "trace-tool-call", batch["batch_id"], call["call_id"],
                 call["attempt_id"], "calendar.query", encode({"secret": "private tool arguments"}),
                 tool_started, tool_finished))
            connection.execute("""INSERT INTO extension_tool_invocations
                (invocation_id, parent_task_id, tool_call_id, batch_id, task_id, action, descriptor_json,
                 provider_json, arguments_json, state, effect, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'succeeded', 'none', ?, ?)""",
                ("trace-extension", batch["generation_task_id"], "trace-tool-call", batch["batch_id"],
                 "trace-extension-task", "calendar.query", encode({"action": "calendar.query"}),
                 encode({"provider": "calendar"}), encode({"secret": "private tool arguments"}),
                 plugin_started, plugin_finished))

        report = self.manager.maintenance(self.core).unwrap()["reply_efficiency"]
        sample = report["recent"][0]
        trace = sample["trace"]
        self.assertEqual(trace["materials"], [{"owner": "calendar.extension", "purpose": "daily_agenda",
                                               "state": "ready"}])
        self.assertEqual(trace["offered_tool_count"], 2)
        self.assertEqual(trace["offered_tools"], ["calendar.query", "memory.search"])
        self.assertEqual(len(trace["model_calls"]), 1)
        self.assertEqual(trace["tool_calls"][0]["kind"], "extension")
        self.assertEqual(trace["tool_calls"][0]["state"], "succeeded")
        self.assertEqual(trace["tool_calls"][0]["elapsed_ms"], 400)
        self.assertEqual(trace["tool_calls"][0]["extension_elapsed_ms"], 200)
        body_free_report = json.dumps(report, ensure_ascii=False)
        for private_value in ("private injected content", "private model prompt", "private tool arguments"):
            self.assertNotIn(private_value, body_free_report)

    async def test_maintenance_exposes_current_role_and_unmatched_no_reply_reasons_only(self):
        ledger = InboundNoReplyLedger(self.app.runtime)
        at = self.app.now()
        ledger.record(reason_code="staging_not_accepting", observed_at=at,
                      channel="private", origin_key=None, event_ref="a" * 32)
        ledger.record(reason_code="session_reply_disabled", observed_at=at,
                      channel="private", origin_key=self.role_context.key, event_ref="b" * 32,
                      code="model_budget_exhausted")
        ledger.record(reason_code="provider_request_failed", observed_at=at,
                      channel="private", origin_key="another-role", event_ref="c" * 32)

        data = self.manager.maintenance(self.core).unwrap()

        records = data["runtime"]["inbound_no_replies"]
        self.assertEqual({row["event_ref"] for row in records}, {"a" * 32, "b" * 32})
        self.assertEqual({row["scope"] for row in records}, {"unmatched", "current_role"})
        self.assertEqual(next(row["code"] for row in records if row["event_ref"] == "b" * 32),
                         "model_budget_exhausted")
        self.assertTrue(all(set(row) == {"event_ref", "channel", "reason_code", "code", "observed_at", "scope"}
                            for row in records))

    async def test_status_projects_the_provider_model_name(self):
        """管理面列出 Provider 时必须带上模型名。

        只列 id 的话，同一个 AstrBot 上挂两个同类型 Provider（一个便宜一个强）
        在页面上是两行一样的字，选错只能靠试——V1 面板一直在显示 `id · model`，
        这个字段就是那次对齐补的。

        夹具里的本地 provider 没有模型概念，所以这里验的是**默认降级为空串**
        而不是编一个名字出来（空串合法，非空时才做不透明性校验）。
        """
        before = self.manager.status(self.core).unwrap()
        self.assertEqual(before["providers"][0]["model"], "")

    async def test_status_explains_the_prompt_layers_and_editable_field(self):
        task = self.manager.status(self.core).unwrap()["tasks"][0]
        layers = task["prompt_layers"]
        self.assertEqual([layer["key"] for layer in layers], [
            "role_instruction", "request_instruction", "task_prompt", "output_contract", "capability_instruction",
            "terminal_prompt_guard"])
        self.assertEqual([layer["key"] for layer in layers if layer["editable"]], ["task_prompt"])
        self.assertIn("单次请求正文", layers[1]["description"])

    async def test_storage_check_reports_integrity_without_mutating_runtime(self):
        page_count = self.app.runtime.read_one("PRAGMA page_count", ())[0]
        result = self.manager.check_storage(self.core).unwrap()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["integrity"], ["ok"])
        self.assertEqual(result["foreign_key_issues"], 0)
        self.assertEqual(self.app.runtime.read_one("PRAGMA page_count", ())[0], page_count)

    async def test_maintenance_aggregates_cache_receipts_and_handles_non_object_provider_json(self):
        call = self.app.runtime.read_one("SELECT call_id FROM model_calls LIMIT 1", ())
        self.assertIsNotNone(call)
        with self.app.runtime.transaction() as connection:
            connection.execute("""UPDATE model_calls SET actual_tokens = 120, input_tokens = 100,
                cached_input_tokens = 40, cache_creation_tokens = 10, output_tokens = 20, provider_json = ?
                WHERE call_id = ?""", ("[]", call["call_id"]))

        first = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(first["ledger_scope"], "current_role")
        self.assertEqual(first["runtime"]["identity_scope"], {
            "dashboard_operator": "verified", "role_access": "verified", "token_ledger": "current_role"})
        trace = first["runtime"]["debounce_trace"]
        self.assertTrue(trace)
        self.assertGreaterEqual(trace[0]["input_count"], 1)
        self.assertIsInstance(trace[0]["quiet_window_ms"], int)
        self.assertFalse({"batch_id", "route_key", "merge_key", "context"} & set(trace[0]))
        self.assertEqual(first["summary"]["tokens"], 120)
        self.assertEqual(first["summary"]["cache_input_tokens"], 100)
        self.assertEqual(first["summary"]["cache_hit_rate"], 40)
        self.assertEqual(first["summary"]["cache_coverage_pct"], 100)
        self.assertEqual(first["recent"][0]["provider"], "")
        self.assertEqual(first["recent"][0]["cache_hit_rate"], 40)
        self.assertEqual(first["totals"]["tokens"], 120)
        self.assertEqual(first["totals"]["calls"], 1)
        self.assertEqual(first["windows"]["7d"]["totals"]["tokens"], 120)
        self.assertEqual(first["windows"]["30d"]["totals"]["calls"], 1)
        self.assertEqual(first["by_provider"][0]["key"], "未知 Provider")
        self.assertEqual(first["windows"]["7d"]["providers"][0]["key"], "未知 Provider")
        self.assertEqual(first["history"][-1]["tokens"], 120)
        self.assertEqual(first["history"][-1]["providers"][0]["key"], "未知 Provider")
        self.assertEqual(sum(row["tokens"] for row in first["hourly"]), 120)

        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE model_calls SET provider_json = ? WHERE call_id = ?",
                               (json.dumps({"provider": "primary"}), call["call_id"]))
        ranked = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(ranked["by_provider"][0]["key"], "primary")
        self.assertEqual(ranked["by_task"][0]["key"], "conversation.reply")
        self.assertEqual(ranked["history"][-1]["tasks"][0]["key"], "conversation.reply")

        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE model_calls SET input_tokens = NULL WHERE call_id = ?", (call["call_id"],))
        incomplete = self.manager.maintenance(self.core).unwrap()
        self.assertIsNone(incomplete["summary"]["cache_hit_rate"])
        self.assertEqual(incomplete["summary"]["cache_creation_tokens"], 10)
        self.assertIsNone(incomplete["recent"][0]["cache_hit_rate"])

    async def test_maintenance_windows_keep_old_role_calls_in_30d_but_not_7d(self):
        call = self.app.runtime.read_one("SELECT call_id FROM model_calls LIMIT 1", ())
        self.assertIsNotNone(call)
        today = self.app.now().date()
        old_day = today - timedelta(days=10)
        scope = self.role_context.scope
        role_address = [scope.installation_id, scope.bot_id, asdict(self.role_context.role_binding.role)]
        current_key = digest(["role", role_address, today.isoformat()])
        old_key = digest(["role", role_address, old_day.isoformat()])
        with self.app.runtime.transaction() as connection:
            account = connection.execute("SELECT * FROM model_budget_accounts WHERE account_key = ?",
                                         (current_key,)).fetchone()
            self.assertIsNotNone(account)
            connection.execute("INSERT INTO model_budget_accounts VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
                (old_key, account["policy_hash"], account["token_limit"], account["cost_limit"],
                 account["spent_tokens"], account["spent_cost"]))
            connection.execute("UPDATE model_calls SET started_at = ?, finished_at = ? WHERE call_id = ?",
                (old_day.isoformat() + "T12:00:00+00:00", old_day.isoformat() + "T12:00:01+00:00", call["call_id"]))
            connection.execute("UPDATE model_call_accounts SET account_key = ? WHERE call_id = ? AND account_key = ?",
                (old_key, call["call_id"], current_key))

        data = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(data["totals"]["calls"], 1)
        self.assertEqual(data["windows"]["7d"]["totals"]["calls"], 0)
        self.assertEqual(data["windows"]["30d"]["totals"]["calls"], 1)
        self.assertEqual(data["history"][0]["key"], old_day.isoformat())

    async def test_cache_diagnostics_use_exact_period_counts_and_safe_signature_records(self):
        call = self.app.runtime.read_one("SELECT call_id FROM model_calls LIMIT 1", ())
        signature = {"version": 1, "model_hash": "a" * 64, "system_hash": "b" * 64,
            "system_parts": ["b" * 64], "tools_hash": "c" * 64, "tool_parts": [],
            "context_hashes": [], "context_bytes": 0, "context_observed": True}
        signature_json = json.dumps(signature, sort_keys=True)
        with self.app.runtime.transaction() as connection:
            connection.execute("""UPDATE model_calls SET input_tokens = 1200, cached_input_tokens = 100,
                cache_creation_tokens = 0, provider_json = ? WHERE call_id = ?""",
                (json.dumps({"provider": "primary"}), call["call_id"]))
            connection.execute("INSERT INTO model_call_cache_signatures(call_id, signature_json) VALUES (?, ?)",
                               (call["call_id"], signature_json))

        data = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(data["summary"]["cache_low_calls"], 1)
        self.assertEqual(data["history"][-1]["cache_low_calls"], 1)
        daily = data["cache_diagnostics"]["daily"][-1]
        self.assertEqual((daily["signature_calls"], daily["baseline_calls"], daily["compared_calls"]), (1, 1, 0))
        self.assertEqual(data["cache_diagnostics"]["recent_events"], [])
        self.assertNotIn("prompt", data["cache_diagnostics"])

    async def test_maintenance_splits_dialogue_and_plugin_usage_without_changing_receipts(self):
        calls = [dict(self.app.runtime.read_one("SELECT * FROM model_calls LIMIT 1", ()))]
        for identifier in ("plugin-usage", "unknown-dialogue-usage"):
            batch = self.host_instance.receive(self.event(identifier)).unwrap()
            self.advance(self.app)
            await self.done(self.app, batch)
            calls.append(dict(self.app.runtime.read_one("SELECT * FROM model_calls ORDER BY finished_at DESC LIMIT 1", ())))
        signature = {"version": 1, "model_hash": "a" * 64, "system_hash": "b" * 64,
            "system_parts": ["b" * 64], "tools_hash": "c" * 64, "tool_parts": [],
            "context_hashes": [], "context_bytes": 0, "context_observed": True}
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET kind = 'conversation.image_review' WHERE task_id = ?",
                               (calls[1]["task_id"],))
            for row, tokens, input_tokens, cached, cost in ((calls[0], 120, 100, 40, 12),
                                                          (calls[1], 220, 200, 10, 22),
                                                          (calls[2], None, None, None, None)):
                connection.execute("""UPDATE model_calls SET actual_tokens = ?, actual_cost = ?,
                    input_tokens = ?, cached_input_tokens = ?, output_tokens = ?, cache_creation_tokens = 0,
                    provider_json = ?, state = ? WHERE call_id = ?""",
                    (tokens, cost, input_tokens, cached, 20 if tokens is not None else None,
                     json.dumps({"provider": "primary"}), "unknown" if tokens is None else "generated", row["call_id"]))
                record = signature if row != calls[2] else {**signature, "system_hash": "d" * 64, "system_parts": ["d" * 64]}
                connection.execute("INSERT INTO model_call_cache_signatures(call_id, signature_json) VALUES (?, ?)",
                                   (row["call_id"], json.dumps(record)))
        before = [dict(row) for row in self.app.runtime.read_all("SELECT * FROM model_calls ORDER BY call_id", ())]
        budget_before = [dict(row) for row in self.app.runtime.read_all("SELECT * FROM model_budget_accounts", ())]

        data = self.manager.maintenance(self.core).unwrap()
        dialogue, plugin = (data["usage_sources"][key] for key in ("conversation", "plugin"))
        self.assertEqual((dialogue["totals"]["tokens"], plugin["totals"]["tokens"], data["totals"]["tokens"]),
                         (120, 220, 340))
        self.assertEqual((dialogue["totals"]["cost_microusd"], plugin["totals"]["cost_microusd"]), (12, 22))
        self.assertEqual((dialogue["totals"]["calls"], plugin["totals"]["calls"]), (2, 1))
        self.assertEqual((dialogue["totals"]["unconfirmed_calls"], plugin["totals"]["unconfirmed_calls"]), (1, 0))
        self.assertEqual((dialogue["totals"]["unknown_calls"], plugin["totals"]["unknown_calls"]), (1, 0))
        self.assertEqual((dialogue["summary"]["cache_hit_rate"], plugin["summary"]["cache_hit_rate"]), (40, 5))
        self.assertEqual(dialogue["by_task"][0]["key"], "conversation.reply")
        self.assertEqual(plugin["by_task"][0]["key"], "conversation.image_review")
        for report, tokens in ((dialogue, 120), (plugin, 220)):
            self.assertEqual(report["by_provider"][0]["tokens"], tokens)
            self.assertEqual(report["history"][-1]["tokens"], tokens)
            self.assertEqual(report["daily"][-1]["tokens"], tokens)
            self.assertEqual(sum(row["tokens"] for row in report["hourly"]), tokens)
            for period in ("7d", "30d"):
                self.assertEqual(report["windows"][period]["totals"]["tokens"], tokens)
        self.assertTrue(all(row["kind"] == "conversation.reply" for row in dialogue["recent"]))
        self.assertTrue(all(row["kind"] == "conversation.image_review" for row in plugin["recent"]))
        self.assertEqual(dialogue["cache_diagnostics"]["daily"][-1]["signature_calls"], 2)
        self.assertEqual(dialogue["cache_diagnostics"]["daily"][-1]["changed_calls"], 1)
        self.assertEqual(plugin["cache_diagnostics"]["daily"][-1]["signature_calls"], 1)
        self.assertEqual(plugin["cache_diagnostics"]["recent_events"], [])
        self.assertEqual(before, [dict(row) for row in self.app.runtime.read_all("SELECT * FROM model_calls ORDER BY call_id", ())])
        self.assertEqual(budget_before, [dict(row) for row in self.app.runtime.read_all("SELECT * FROM model_budget_accounts", ())])
        self.assertEqual(self.manager.maintenance(self.important).failure.code, "core_access_required")

    async def test_recent_samples_are_bounded_per_source_and_exclude_other_role_accounts(self):
        call = dict(self.app.runtime.read_one("SELECT * FROM model_calls LIMIT 1", ()))
        task = dict(self.app.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id = ?", (call["task_id"],)))
        attempt = dict(self.app.runtime.read_one("SELECT * FROM execution_attempts WHERE attempt_id = ?", (call["attempt_id"],)))
        request = dict(self.app.runtime.read_one("SELECT * FROM model_requests WHERE task_id = ?", (call["task_id"],)))
        scope = self.role_context.scope
        role_address = [scope.installation_id, scope.bot_id, asdict(self.role_context.role_binding.role)]
        role_account = digest(["role", role_address, self.app.now().date().isoformat()])
        with self.app.runtime.transaction() as connection:
            def insert(table, row):
                connection.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                                   tuple(row.values()))

            other_account = dict(connection.execute("SELECT * FROM model_budget_accounts WHERE account_key = ?",
                                                    (role_account,)).fetchone())
            insert("model_budget_accounts", {**other_account, "account_key": "another-role"})
            connection.execute("UPDATE execution_tasks SET kind = 'proactive.compose' WHERE task_id = ?", (task["task_id"],))
            for index in range(86):
                task_id, attempt_id, call_id = (f"usage-{label}-{index}" for label in ("task", "attempt", "call"))
                at = (self.app.now() + timedelta(seconds=index + 1)).isoformat()
                origin_key = "another-role" if index == 85 else task["origin_key"]
                insert("execution_tasks", {**task, "task_id": task_id, "attempt_id": attempt_id,
                                            "origin_key": origin_key, "dedupe_key": task_id})
                insert("execution_attempts", {**attempt, "attempt_id": attempt_id, "task_id": task_id,
                                               "origin_key": origin_key})
                insert("model_requests", {**request, "task_id": task_id})
                insert("model_calls", {**call, "call_id": call_id, "task_id": task_id, "attempt_id": attempt_id,
                                       "started_at": at, "finished_at": at})
                insert("model_call_accounts", {"call_id": call_id, "account_key": "another-role" if index == 85 else role_account})

        data = self.manager.maintenance(self.core).unwrap()
        dialogue, plugin = (data["usage_sources"][key] for key in ("conversation", "plugin"))
        self.assertEqual((data["totals"]["calls"], dialogue["totals"]["calls"], plugin["totals"]["calls"]),
                         (86, 85, 1))
        self.assertEqual(len(data["recent"]), 80)
        self.assertEqual(len(dialogue["recent"]), 80)
        self.assertEqual(len(plugin["recent"]), 1)
        self.assertEqual(plugin["recent"][0]["kind"], "proactive.compose")
        other_at = (self.app.now() + timedelta(seconds=86)).isoformat()
        self.assertTrue(all(row["at"] != other_at for row in dialogue["recent"]))

    async def test_save_replay_inherit_and_upstream_source(self):
        before = self.manager.status(self.core, layer="window").unwrap()
        task = before["tasks"][0]
        self.assertEqual(task["fields"]["prompt"]["inherited_source"], "default")
        self.assertEqual(task["fields"]["fallback"]["value"], "primary")
        self.assertEqual(task["fields"]["fallback"]["inherited_source"], "default")
        self.assertEqual([provider["id"] for provider in before["providers"]], ["primary"])

        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        parent = self.app.configuration.read(self.core, target=role, group_id="model.conversation.reply",
                                             observed_at=self.app.now()).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=parent.group_id,
            operation=self.operation(role.authorization_scope), expected_edit_token=parent.edit_token,
            changes={"model.conversation.reply.prompt": "角色级任务补充"}, observed_at=self.app.now()).unwrap()
        inherited = self.manager.status(self.core, layer="window").unwrap()
        field = inherited["tasks"][0]["fields"]["prompt"]
        self.assertEqual((field["value"], field["source"], field["inherited_source"]),
                         ("角色级任务补充", "role", "role"))

        payload = self.request(inherited, changes={"prompt": ""}, key="clear-prompt")
        saved = self.manager.configure(self.core, payload).unwrap()
        self.assertFalse(saved["replayed"])
        self.assertEqual(saved["task"]["fields"]["prompt"]["value"], "")
        self.assertEqual(saved["task"]["fields"]["prompt"]["inherited_source"], "role")
        replay = self.manager.configure(self.core, payload).unwrap()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["operation_id"], saved["operation_id"])

        current = self.manager.status(self.core, layer="window").unwrap()
        restored = self.manager.configure(self.core,
            self.request(current, reset=["prompt"], key="inherit-prompt")).unwrap()["task"]["fields"]["prompt"]
        self.assertEqual((restored["value"], restored["source"], restored["explicit"]),
                         ("角色级任务补充", "role", False))

    async def test_sequential_task_saves_can_share_one_page_snapshot(self):
        before = self.manager.status(self.core, layer="role").unwrap()
        tasks = [task for task in before["tasks"] if task["kind"] in (
            "conversation.context_image_caption", "conversation.image_narration")]
        self.assertEqual(len(tasks), 2)
        for index, task in enumerate(tasks):
            saved = self.manager.configure(self.core, {
                "kind": task["kind"], "layer": "role", "operation_key": f"quick-model-{index}",
                "edit_token": task["edit_token"], "changes": {"provider": "primary"}, "reset": [],
            }).unwrap()
            self.assertEqual(saved["task"]["fields"]["provider"]["value"], "primary")
        current = self.manager.status(self.core, layer="role").unwrap()
        saved_tasks = [task for task in current["tasks"] if task["kind"] in {task["kind"] for task in tasks}]
        self.assertTrue(all(task["fields"]["provider"]["explicit"] for task in saved_tasks))

    async def test_invalid_patch_stale_version_and_non_core_access(self):
        status = self.manager.status(self.core).unwrap()
        for invalid in (None, {}, {"kind": "conversation.reply"},
                        self.request(status, changes={"provider": "primary"}, reset=["provider"]),
                        self.request(status, changes={"fallback": "primary"}, key="duplicate-fallback"),
                        self.request(status, changes={"provider": "unregistered"})):
            result = self.manager.configure(self.core, invalid)
            self.assertIsNotNone(result.failure)
        self.assertEqual(self.manager.status(self.important).failure.code, "core_access_required")
        self.assertEqual(self.manager.configure(self.important,
            self.request(status, changes={"prompt": "private"})).failure.code, "core_access_required")
        saved = self.manager.configure(self.core, self.request(status, changes={"prompt": "new"})).unwrap()
        self.assertFalse(saved["replayed"])
        stale = self.manager.configure(self.core,
            self.request(status, changes={"prompt": "stale"}, key="stale-model-settings"))
        self.assertEqual(stale.failure.code, "configuration_revision_conflict")

    async def test_replacement_rules_save_inherit_and_reject_unsupported_targets(self):
        before = self.manager.status(self.core, layer="window").unwrap()
        self.assertFalse(before["providers"][0]["supports_model_override"])
        rules = json.dumps([{"id": "ordinary", "provider_id": "primary", "keywords": ["private"]}])
        saved = self.manager.configure(self.core, self.request(before,
            changes={"replacement_rules": rules}, key="save-routes")).unwrap()
        field = saved["task"]["fields"]["replacement_rules"]
        self.assertEqual((field["value"], field["source"], field["explicit"]), (rules, "window", True))
        for value in (
            '[{"provider_id":"missing","keywords":["private"]}]',
            '[{"provider_id":"primary","model":"creative","keywords":["private"]}]',
            '[{"provider_id":"primary","keywords":7,"time_window":{"timezone":"Asia/Shanghai","start":"07:00","end":"09:00"}}]',
        ):
            with self.subTest(value=value):
                current = self.manager.status(self.core, layer="window").unwrap()
                result = self.manager.configure(self.core, self.request(current,
                    changes={"replacement_rules": value}, key="invalid-" + str(len(value))))
                self.assertEqual(result.failure.code, "model_route_invalid")
        current = self.manager.status(self.core, layer="window").unwrap()
        inherited = self.manager.configure(self.core, self.request(current,
            reset=["replacement_rules"], key="inherit-routes")).unwrap()
        field = inherited["task"]["fields"]["replacement_rules"]
        self.assertEqual((field["value"], field["source"], field["explicit"]), ("[]", "default", False))

    async def test_refusal_terms_save_canonicalize_and_inherit(self):
        before = self.manager.status(self.core, layer="window").unwrap()
        saved = self.manager.configure(self.core, self.request(before, changes={
            "fallback_on_refusal": True,
            "refusal_terms": "policy denied; request blocked\nPOLICY DENIED",
        }, key="save-refusal-terms")).unwrap()
        field = saved["task"]["fields"]["refusal_terms"]
        self.assertEqual(field["value"], "policy denied\nrequest blocked")
        self.assertEqual((field["source"], field["explicit"]), ("window", True))

        current = self.manager.status(self.core, layer="window").unwrap()
        invalid = self.manager.configure(self.core, self.request(current, changes={
            "refusal_terms": "\n".join(f"term-{index}" for index in range(65)),
        }, key="too-many-refusal-terms"))
        self.assertEqual(invalid.failure.code, "model_configuration_invalid")

        current = self.manager.status(self.core, layer="window").unwrap()
        inherited = self.manager.configure(self.core, self.request(current,
            reset=["refusal_terms"], key="inherit-refusal-terms")).unwrap()
        field = inherited["task"]["fields"]["refusal_terms"]
        self.assertEqual(field["value"], "\n".join((
            "很抱歉，我无法", "很抱歉,我无法", "我有我自己的底线", "我们可以聊聊别的", "我无法满足",
            "露骨性行为", "没办法提交这个请求", "这个请求没办法提交",
            "The prompt could not be submitted", "request could not be submitted")))
        self.assertFalse(field["explicit"])

    async def test_output_limit_refusal_names_the_field_the_provider_and_its_ceiling(self):
        # 拒绝只来自 Provider 的显式上限，报文必须能看出是哪一档上限；被拒的配置值
        # 本身不进报文。
        before = self.manager.status(self.core, layer="window").unwrap()
        refused = self.manager.configure(self.core, self.request(before, changes={
            "max_output_tokens": 101}, key="above-provider-ceiling"))

        self.assertEqual(refused.failure.code, "model_output_limit")
        self.assertIn("max_output_tokens", refused.failure.reason)
        self.assertIn("primary", refused.failure.reason)
        self.assertIn("100", refused.failure.reason)
        self.assertNotIn("101", refused.failure.reason)

    async def test_a_retired_inherited_provider_falls_back_to_the_host_model(self):
        # 旧版迁移可能把已下线的 Provider 写进安装级默认。这个值不能再充当路由：
        # 读取时按未设置处理，回退到 AstrBot 当前会话模型，页面的“跟随上级”
        # 也才会指向一个真实可用的模型。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        self.app.router.default_provider_resolver = lambda context, profile: "host-alt"
        # Use a host route that differs from the task default("primary"), so the
        # assertion distinguishes the host conversation model from the fallback.
        primary_provider = self.providers[0]
        host_provider = replace(primary_provider,
                                binding=replace(primary_provider.binding, provider="host-alt"))
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers, "host-alt": host_provider})

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["provider"]
        self.assertEqual((field["value"], field["source"]), ("host-alt", "host_conversation"))
        self.assertEqual((field["inherited_value"], field["inherited_source"]),
                         ("host-alt", "host_conversation"))

    async def test_follow_parent_saves_even_when_the_inherited_provider_is_gone(self):
        # 恢复「跟随上级」不能被继承值阻塞：reset 表示删除本层值，失效的继承 id
        # 由路由回退处理，保存本身必须成功。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        self.app.router.default_provider_resolver = lambda context, profile: "primary"

        status = self.manager.status(self.core, layer="role").unwrap()
        task = next(item for item in status["tasks"] if item["kind"] == kind)
        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "follow-parent",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["provider"],
        }).unwrap()

        self.assertFalse(saved["replayed"])
        self.assertEqual(saved["task"]["fields"]["provider"]["value"], "primary")

    async def test_a_retired_inherited_provider_falls_back_to_the_task_default(self):
        # 后台任务没有可跟随的会话模型（解析器返回 None），失效的继承值同样
        # 必须落到任务档默认模型，而不是继续冒充“上级默认”。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        self.app.router.default_provider_resolver = lambda context, profile: None

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["provider"]
        self.assertEqual((field["value"], field["source"]), ("primary", "default"))
        self.assertEqual((field["inherited_value"], field["inherited_source"]),
                         ("primary", "default"))

    async def test_the_parent_view_is_answered_by_the_parent_route(self):
        # 角色层自己指定了主模型和备用模型时，当前层不读宿主路线；但「跟随上级
        # 时为 X」回答的是「删掉这层覆盖后会走哪个模型」，必须用上一层的路线来算。
        # 借用当前层的答案会把宿主模型说成任务档默认，与实际路由不符。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]

        def variant(name):
            return replace(base, binding=replace(base.binding, provider=name))

        self.app.router.providers = MappingProxyType({
            **self.app.router.providers, "extra-alt": variant("extra-alt"),
            "host-alt": variant("host-alt")})
        self.app.router.default_provider_resolver = lambda context, profile: "host-alt"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "primary", group + ".fallback": "extra-alt"},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["provider"]

        self.assertEqual((field["value"], field["source"]), ("primary", "role"))
        self.assertEqual((field["inherited_value"], field["inherited_source"]),
                         ("host-alt", "host_conversation"))

    async def test_the_parent_view_still_opens_when_the_adapter_lookup_fails(self):
        # 失效的安装层值在解析适配器时可能抛出 TaskError。上级视图只是展示，
        # 不能因此让整个模型设置页打不开。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        router = self.app.router
        real = router.provider_for

        def flaky(provider_id):
            if provider_id == "removed-provider":
                raise TaskError("model_provider_unavailable", "host returned a mismatched adapter")
            return real(provider_id)

        router.provider_for = flaky
        try:
            task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                        if item["kind"] == kind)
        finally:
            router.provider_for = real

        # The projection degrades to the stored value instead of recovering it:
        # the page opens and shows what is saved, while dispatch keeps failing.
        self.assertEqual(task["fields"]["provider"]["inherited_value"], "removed-provider")

    async def test_a_save_cannot_recreate_a_cleared_fallback_duplicate(self):
        # 投影会把与恢复后主模型重复的备用清空展示，但那一行仍保存在本层。
        # 保存主模型为同一个值时，必须按保存值判定重复，而不是按投影的空值放行。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        now = self.app.now()
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        status = self.manager.status(self.core, layer="role").unwrap()
        task = next(item for item in status["tasks"] if item["kind"] == kind)
        self.assertTrue(task["fields"]["fallback"].get("cleared"))
        self.assertEqual(task["fields"]["fallback"]["value"], "")

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "rebuild-primary",
            "edit_token": task["edit_token"], "changes": {"provider": "primary"}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_a_window_save_cannot_recreate_a_cleared_fallback_duplicate(self):
        # window 层是派发读取的层，explicit 直接生效：这里保存主模型为与
        # 隐藏备用相同的值，下一次 resolve() 会真的报 model_fallback_duplicate。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        window = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.WINDOW,
                              self.role_context.role_binding.role, self.role_context.scope,
                              bot_id=self.role_context.scope.bot_id)
        now = self.app.now()
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=window, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=window, group_id=group,
            operation=self.operation(window.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        status = self.manager.status(self.core, layer="window").unwrap()
        task = next(item for item in status["tasks"] if item["kind"] == kind)
        self.assertTrue(task["fields"]["fallback"].get("cleared"))

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "window", "operation_key": "rebuild-window-primary",
            "edit_token": task["edit_token"], "changes": {"provider": "primary"}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_following_an_unavailable_parent_provider_is_refused(self):
        # window 层跟随上级时，上一层的显式坏值会被保存路径拒绝；UI 提前
        # 拦截的理由就在这里。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()

        status = self.manager.status(self.core, layer="window").unwrap()
        task = next(item for item in status["tasks"] if item["kind"] == kind)
        self.assertEqual(task["fields"]["provider"]["inherited_value"], "removed-provider")

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "window", "operation_key": "follow-unavailable",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["provider"],
        })

        self.assertEqual(refused.failure.code, "model_provider_unavailable")

    async def test_a_recovered_installation_value_is_not_marked_as_cleared(self):
        # 安装层的值被恢复成宿主路线时，本层并没有保存过任何东西；把它标成
        # cleared 会让页面当成"本层已单独设置"，保存时把宿主路线写死成本层值。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        now = self.app.now()
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["provider"]

        self.assertEqual(field["value"], "primary")
        self.assertNotIn("cleared", field)
        self.assertTrue(field["inherited_value"])

    async def test_resetting_a_provider_does_not_false_positive_the_duplicate_guard(self):
        # reset 只是删除本层 provider 行，比较时用的却是恢复后的路由值；那个值
        # 不是本次保存写入的内容，拿它和存储的备用比较会误报 duplicate。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        now = self.app.now()
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "reset-provider",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["provider"],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_a_registry_default_primary_does_not_collide_with_an_explicit_fallback(self):
        # 主模型没有保存行、由宿主路线填充时投影是 host-alt；备用保存为插件
        # 默认 primary。这是可派发的合法路由，保存其他字段不能被判成重复。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "host-alt": replace(base, binding=replace(base.binding, provider="host-alt"))})
        self.app.router.default_provider_resolver = lambda context, profile: "host-alt"
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["provider"]["value"], "host-alt")
        self.assertEqual(task["fields"]["fallback"]["value"], "primary")

        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "edit-timeout",
            "edit_token": task["edit_token"], "changes": {"timeout_seconds": 30}, "reset": [],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_resetting_a_window_provider_is_refused_when_it_collides_with_a_saved_fallback(self):
        # window 层是派发读取的层：reset 主模型后继承到一个可用的 provider，
        # 与本层显式备用相同，dispatch 会以 duplicate 拒绝，保存时就必须拦下。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        window = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.WINDOW,
                              self.role_context.role_binding.role, self.role_context.scope,
                              bot_id=self.role_context.scope.bot_id)
        now = self.app.now()
        for target, changes in ((role, {"provider": "primary"}),
                                (window, {"fallback": "primary"})):
            snapshot = self.app.configuration.read(self.core, target=target, group_id=group,
                                                   observed_at=now).unwrap()
            self.app.configuration.patch(self.core, target=target, group_id=group,
                operation=self.operation(target.authorization_scope),
                expected_edit_token=snapshot.edit_token,
                changes={group + "." + name: value for name, value in changes.items()},
                observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="window").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "window", "operation_key": "reset-window-provider",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["provider"],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_a_hidden_fallback_is_compared_when_recovery_used_the_default(self):
        # 恢复把隐藏的显式备用换成任务档默认时，投影里它不是空的；保存主模型
        # 为该备用值时仍须判定重复，否则会留下永久不可路由的配置。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        self.app.router.profiles = MappingProxyType({
            **self.app.router.profiles,
            kind: replace(self.app.router.profiles[kind], default_fallback="secondary")})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["fallback"]["value"], "secondary")

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "set-primary-explicit",
            "edit_token": task["edit_token"], "changes": {"provider": "primary"}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_editing_an_unrelated_field_does_not_reject_a_recovered_route(self):
        # 恢复把隐藏的备用重指到任务档默认后，路由是可用的；保存其他字段时
        # 不能拿那行旧值去和恢复后的主模型比较。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        self.app.router.profiles = MappingProxyType({
            **self.app.router.profiles,
            kind: replace(self.app.router.profiles[kind], default_fallback="secondary")})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["fallback"]["value"], "secondary")

        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "edit-timeout-recovered",
            "edit_token": task["edit_token"], "changes": {"timeout_seconds": 30}, "reset": [],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_a_substituted_fallback_exposes_the_stored_row(self):
        # 恢复把撞车的显式备用换成任务档默认时，那一行仍保存在本层；投影必须
        # 让用户看得见它，否则宿主变化后备用会静默变回旧值。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        self.app.router.profiles = MappingProxyType({
            **self.app.router.profiles,
            kind: replace(self.app.router.profiles[kind], default_fallback="secondary")})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["fallback"]

        self.assertEqual(field["value"], "secondary")
        self.assertTrue(field.get("replaced"))
        self.assertEqual(field.get("stored_value"), "primary")

    async def test_a_reset_with_an_explicit_fallback_still_checks_the_duplicate(self):
        # reset 只豁免 provider 一侧；同一次保存显式写入的 fallback 仍必须与
        # 继承来的主模型比较，否则会存下一条派发永远丢弃的备用。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "secondary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "reset-and-write-fallback",
            "edit_token": task["edit_token"], "changes": {"fallback": "primary"},
            "reset": ["provider"],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_a_recovered_hidden_row_is_still_checked_for_its_ceiling(self):
        # 恢复隐藏的备用会在主模型改变后重新生效，它的输出上限必须一起校验；
        # 只检查投影里的替换值会放过一条随后被派发拒绝的路由。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary")),
            "small": replace(base, binding=replace(base.binding, provider="small"),
                             maximum_output_tokens=50)})
        self.app.router.profiles = MappingProxyType({
            **self.app.router.profiles,
            kind: replace(self.app.router.profiles[kind], default_fallback="secondary")})
        self.app.router.default_provider_resolver = lambda context, profile: "small"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "small", group + ".max_output_tokens": 100},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["fallback"]["value"], "secondary")
        self.assertTrue(task["fields"]["fallback"].get("replaced"))

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "move-primary",
            "edit_token": task["edit_token"], "changes": {"provider": "primary"}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_a_recovered_default_fallback_is_checked_when_the_primary_moves(self):
        # 注册表默认备用被恢复清空时，它不是本层显式行；但主模型一变，那条
        # 默认备用会重新生效，上限必须一起校验。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        window = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.WINDOW,
                              self.role_context.role_binding.role, self.role_context.scope,
                              bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"),
                           maximum_output_tokens=8192)})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="window").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["provider"]["value"], "primary")
        self.assertEqual(task["fields"]["fallback"]["value"], "")

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "window", "operation_key": "move-to-big",
            "edit_token": task["edit_token"],
            "changes": {"provider": "big", "max_output_tokens": 4096}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_resetting_a_fallback_uses_the_parents_stored_value(self):
        # 本层主模型显式时，父视图的恢复不会在这里发生；reset 备用后真正继承
        # 的是父层存储值，而不是父视图因撞车清空的空值。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        window = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.WINDOW,
                              self.role_context.role_binding.role, self.role_context.scope,
                              bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"),
                           maximum_output_tokens=8192),
            "fb": replace(base, binding=replace(base.binding, provider="fb"),
                          maximum_output_tokens=8192)})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=window, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=window, group_id=group,
            operation=self.operation(window.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "big", group + ".fallback": "fb",
                     group + ".max_output_tokens": 4096}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="window").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["provider"]["value"], "big")

        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "window", "operation_key": "reset-fallback",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["fallback"],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_settling_the_primary_and_resetting_the_fallback_checks_the_inherited_route(self):
        # 同一次保存把主模型写成显式值并 reset 备用时，保存后真正继承的备用
        # 也要参与上限校验；父视图可能因恢复把它清空了。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"),
                           maximum_output_tokens=8192),
            "fb": replace(base, binding=replace(base.binding, provider="fb"),
                          maximum_output_tokens=8192)})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".provider": "removed-provider"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".fallback": "fb", group + ".max_output_tokens": 4096},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "settle-and-reset",
            "edit_token": task["edit_token"], "changes": {"provider": "big"},
            "reset": ["fallback"],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_resetting_a_provider_to_an_available_value_still_checks_duplicates(self):
        # reset 后继承的是可用的显式主模型时，它与本层保存的备用相同就是真重复，
        # 保存后每次保存都会被 duplicate 拒绝。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"))})
        now = self.app.now()
        for target, changes in ((installation, {"provider": "primary"}),
                                (role, {"provider": "big", "fallback": "primary"})):
            snapshot = self.app.configuration.read(self.core, target=target, group_id=group,
                                                   observed_at=now).unwrap()
            self.app.configuration.patch(self.core, target=target, group_id=group,
                operation=self.operation(target.authorization_scope),
                expected_edit_token=snapshot.edit_token,
                changes={group + "." + name: value for name, value in changes.items()},
                observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "reset-to-available",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["provider"],
        })

        self.assertEqual(refused.failure.code, "model_fallback_duplicate")

    async def test_a_stale_inherited_fallback_does_not_block_a_primary_change(self):
        # 安装层失效的备用在保存后仍会被恢复隐藏；它不该被当成保存后的路由
        # 参与校验，否则用户在页面上修不了这个值。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".fallback": "removed-fallback"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "primary"}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "move-primary-stale-fb",
            "edit_token": task["edit_token"], "changes": {"provider": "secondary"}, "reset": [],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_a_stale_inherited_fallback_does_not_block_a_fallback_reset(self):
        # 同上：reset 备用时父层存储的失效值仍会被恢复，不能当保存后的路由。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".fallback": "removed-fallback"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "primary", group + ".fallback": "secondary"},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "reset-fallback-stale-parent",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["fallback"],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_a_host_filled_fallback_is_not_replaced_by_the_registry_default(self):
        # 备用没有保存行时，保存后生效的是宿主路线，不是注册表默认值；
        # 用默认值校验会误拒一条实际可用的路由。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"),
                           maximum_output_tokens=8192),
            "big2": replace(base, binding=replace(base.binding, provider="big2"),
                            maximum_output_tokens=8192),
            "host-big": replace(base, binding=replace(base.binding, provider="host-big"),
                                maximum_output_tokens=8192)})
        self.app.router.default_provider_resolver = lambda context, profile: "host-big"
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "big", group + ".max_output_tokens": 4096},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        self.assertEqual(task["fields"]["fallback"]["source"], "host_conversation")

        saved = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "move-to-big2",
            "edit_token": task["edit_token"], "changes": {"provider": "big2"}, "reset": [],
        }).unwrap()

        self.assertFalse(saved["replayed"])

    async def test_resetting_a_fallback_without_a_host_route_checks_the_registry_default(self):
        # 后台任务没有宿主路线；reset 备用后生效的是注册表默认值（上限更低），
        # 它必须参与上限校验。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big"),
                           maximum_output_tokens=8192),
            "fb": replace(base, binding=replace(base.binding, provider="fb"),
                          maximum_output_tokens=8192)})
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "big", group + ".fallback": "fb",
                     group + ".max_output_tokens": 4096}, observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "reset-fallback-no-host",
            "edit_token": task["edit_token"], "changes": {}, "reset": ["fallback"],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_a_primary_change_checks_the_fallback_recovery_will_restore(self):
        # 安装层失效的备用会在保存后恢复成宿主路线；它与新主模型不同，会真的
        # 生效，因此必须校验它的上限。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"),
                                 maximum_output_tokens=8192)})
        self.app.router.default_provider_resolver = lambda context, profile: "primary"
        now = self.app.now()
        parent = self.app.configuration.read(self.core, target=installation, group_id=group,
                                             observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=installation, group_id=group,
            operation=self.operation(installation.authorization_scope),
            expected_edit_token=parent.edit_token,
            changes={group + ".fallback": "removed-fallback"}, observed_at=now).unwrap()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "primary", group + ".max_output_tokens": 4096},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        refused = self.manager.configure(self.core, {
            "kind": kind, "layer": "role", "operation_key": "move-primary-restores-fallback",
            "edit_token": task["edit_token"], "changes": {"provider": "secondary"}, "reset": [],
        })

        self.assertEqual(refused.failure.code, "model_output_limit")

    async def test_the_parent_stored_fallback_is_exposed_for_a_reset(self):
        # reset 备用时真正继承的是父层存储值；投影把它清空/替换时，页面需要
        # 这个值才能显示和校验一致。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        installation = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.INSTALLATION)
        window = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.WINDOW,
                              self.role_context.role_binding.role, self.role_context.scope,
                              bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "big": replace(base, binding=replace(base.binding, provider="big")),
            "fb": replace(base, binding=replace(base.binding, provider="fb")),
            "stored-fb": replace(base, binding=replace(base.binding, provider="stored-fb"))})
        now = self.app.now()
        for target, changes in (
                (installation, {"provider": "removed-provider", "fallback": "stored-fb"}),
                (window, {"provider": "big", "fallback": "fb"})):
            snapshot = self.app.configuration.read(self.core, target=target, group_id=group,
                                                   observed_at=now).unwrap()
            self.app.configuration.patch(self.core, target=target, group_id=group,
                operation=self.operation(target.authorization_scope),
                expected_edit_token=snapshot.edit_token,
                changes={group + "." + name: value for name, value in changes.items()},
                observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="window").unwrap()["tasks"]
                    if item["kind"] == kind)

        self.assertEqual(task["fields"]["fallback"]["parent_stored"], "stored-fb")

    async def test_a_parent_without_a_fallback_row_exposes_no_stored_value(self):
        # 父层没有备用行时，字段值是注册表默认，不是保存值；把它当成“上级
        # 存储备用”会让页面展示一条 reset 后不会使用的路线。
        kind = "conversation.context_image_caption"
        group = "model." + kind
        role = ConfigTarget(self.role_context.scope.installation_id, ConfigLayer.ROLE,
                            self.role_context.role_binding.role, bot_id=self.role_context.scope.bot_id)
        base = self.providers[0]
        self.app.router.providers = MappingProxyType({
            **self.app.router.providers,
            "secondary": replace(base, binding=replace(base.binding, provider="secondary"))})
        now = self.app.now()
        own = self.app.configuration.read(self.core, target=role, group_id=group,
                                          observed_at=now).unwrap()
        self.app.configuration.patch(self.core, target=role, group_id=group,
            operation=self.operation(role.authorization_scope), expected_edit_token=own.edit_token,
            changes={group + ".provider": "primary", group + ".fallback": "secondary"},
            observed_at=now).unwrap()

        task = next(item for item in self.manager.status(self.core, layer="role").unwrap()["tasks"]
                    if item["kind"] == kind)
        field = task["fields"]["fallback"]

        self.assertEqual(field.get("parent_stored"), "")
        self.assertFalse(field.get("parent_stored_usable"))
