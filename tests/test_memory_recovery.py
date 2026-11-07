"""Fault injection across capture, parse, partial commit, recovery and scheduling."""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import encode, stamp
from companion_v2.foundation.outcome import Outcome
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.reflection import reflection_profile
from companion_v2.memory.reflection_management import ReflectionManagement
from companion_v2.memory import reflection_recovery
from companion_v2.memory.reflection_output import OutputProblem, parse_output, redact_output
from companion_v2.model_gateway.models import Amount, CapturedModelOutput, ProviderResponse
from companion_v2.model_gateway.routing import configuration_fields
from tests import test_memory_reflection as support
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class MemoryRecoveryTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    setup_reflection = support.MemoryReflectionTests.setup_reflection

    def setUp(self):
        super().setUp()
        self.profiles += (reflection_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="memory-recovery-test.v1", fields=configuration_fields(self.profiles))

    def configure(self, app, context, **values):
        manager = MemoryManagement(app.memory, self.core)
        settings = manager.settings(context).unwrap()
        manager.patch_settings(context, operation_key="test-config-" + str(len(self.called)),
            expected_edit_token=settings["edit_token"], changes={"memory." + k: v for k, v in values.items()}).unwrap()

    async def setup_manual(self, *, prepare=True):
        host, context, _ = await self.setup_reflection(prepare=False)
        self.configure(host.application, context, reflection_auto_repair=False)
        if not prepare:
            return host, context, None
        prepared = host.application.memory_reflection.prepare(context, request_id="manual",
            start=self.clock.now() - timedelta(hours=1), end=self.clock.now(), role_instruction="原角色人格提示").unwrap()
        return host, context, prepared

    def atoms(self, app):
        return app.runtime.read_all("SELECT * FROM memory_atoms", ())

    async def test_startup_settles_an_interrupted_running_batch_after_the_grace_period(self):
        """重启后 in-flight 的批次不会永远停在 running。"""
        host, context, prepared = await self.setup_manual()
        app = host.application
        batch_id = prepared["batch_id"]
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'running', updated_at = ? WHERE batch_id = ?",
                               (stamp(self.clock.now() - timedelta(hours=3)), batch_id))

        self.assertTrue(reflection_recovery.abandon_stale_running(
            app.memory_reflection, context, batch_id, self.clock.now()))

        row = app.runtime.read_one("SELECT state, result_json FROM memory_reflections WHERE batch_id = ?", (batch_id,))
        self.assertEqual(row["state"], "failed")
        self.assertIn("memory_reflection_abandoned", row["result_json"])

    async def test_a_running_batch_inside_the_grace_period_is_left_alone(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        batch_id = prepared["batch_id"]
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'running', updated_at = ? WHERE batch_id = ?",
                               (stamp(self.clock.now() - timedelta(minutes=5)), batch_id))

        self.assertFalse(reflection_recovery.abandon_stale_running(
            app.memory_reflection, context, batch_id, self.clock.now()))
        row = app.runtime.read_one("SELECT state FROM memory_reflections WHERE batch_id = ?", (batch_id,))
        self.assertEqual(row["state"], "running")

    async def test_fenced_json_is_unwrapped_once_without_a_second_model_call(self):
        host, context, prepared = await self.setup_reflection()
        original = self.model_behavior
        async def fenced(envelope):
            result = await original(envelope)
            return answered("```json\n" + result.draft.text + "\n```")
        self.model_behavior = fenced
        count = len(self.called)
        result = (await host.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.called), count + 1)
        self.assertEqual(len(self.atoms(host.application)), 1)

    async def test_known_invalid_output_gets_one_repair_and_keeps_the_original(self):
        host, context, prepared = await self.setup_reflection()
        original = self.model_behavior
        secret = "sk-" + "R" * 24
        seen = []
        async def once(envelope):
            seen.append(envelope)
            if len(seen) == 1:
                return answered('{"summary":"api_key=' + secret + '",')
            repair = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "format_repair"))
            self.assertEqual(repair["issues"][0]["code"], "invalid_json")
            self.assertNotIn(secret, encode(repair))
            return await original(envelope)
        self.model_behavior = once
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertTrue(result["repair_batch"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(self.atoms(host.application)), 1)
        saved = reflection._row(context, prepared["batch_id"])["result_json"]
        self.assertNotIn(secret, saved)
        self.assertIn("[REDACTED]", saved)
        self.assertIn("invalid_json", saved)
        await reflection.run(context, prepared["batch_id"])
        self.assertEqual(len(seen), 2)

    async def test_invalid_anniversary_shape_gets_one_format_repair(self):
        host, context, prepared = await self.setup_reflection()
        original, seen = self.model_behavior, []
        async def once(envelope):
            seen.append(envelope)
            if len(seen) == 1:
                output = json.loads((await original(envelope)).draft.text)
                output["anniversaries"] = [
                    {"label": "第一次见面", "reason": "错误地返回了多个候选", "confidence": 0.8,
                     "evidence_handle": "s1"},
                    {"label": "第一次一起吃饭", "reason": "错误地返回了多个候选", "confidence": 0.8,
                     "evidence_handle": "s1"},
                ]
                return answered(encode(output))
            repair = json.loads(next(m.payload for m in envelope.original.context.materials
                                     if m.purpose == "format_repair"))
            self.assertEqual(repair["issues"][0]["code"], "invalid_anniversaries")
            return await original(envelope)
        self.model_behavior = once
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertTrue(result["repair_batch"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(self.atoms(host.application)), 1)

    async def test_unknown_evidence_handle_gets_auto_repair(self):
        host, context, prepared = await self.setup_reflection()
        original, seen = self.model_behavior, []
        async def once(envelope):
            seen.append(envelope)
            if len(seen) == 1:
                output = json.loads((await original(envelope)).draft.text)
                for item in output["proposals"]:
                    item["proposal"]["evidence_handles"] = ["s999"]
                return answered(encode(output))
            repair = json.loads(next(m.payload for m in envelope.original.context.materials
                                     if m.purpose == "format_repair"))
            self.assertIn("unknown_evidence", [item["problem"]["code"] for item in repair["items"]])
            proposals = []
            for item in repair["items"]:
                raw = item["raw"]
                raw["evidence_handles"] = ["s1"]
                proposals.append({"item_id": item["item_id"], "proposal": raw})
            return answered(encode({"summary": "已修正来源编号。", "proposals": proposals}))
        self.model_behavior = once
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertTrue(result["repair_batch"])
        self.assertEqual(len(seen), 2)
        self.assertGreaterEqual(len(self.atoms(host.application)), 1)

    async def test_repeated_format_failure_is_bounded_and_never_becomes_empty(self):
        host, context, prepared = await self.setup_reflection()
        async def invalid(_):
            return answered('{"summary":"a","summary":"b","proposals":[]}')
        self.model_behavior = invalid
        count = len(self.called)
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["issues"][0]["code"], "duplicate_json_field")
        self.assertEqual(len(self.called), count + 2)
        self.assertEqual(self.atoms(host.application), ())
        self.assertFalse((await reflection.run(context, prepared["batch_id"])).is_success)
        self.assertEqual(len(self.called), count + 2)

    async def test_restart_after_capture_reuses_output_after_query_session_expiry(self):
        host, context, prepared = await self.setup_reflection()
        reflection = host.application.memory_reflection
        with patch.object(reflection_recovery, "consume", side_effect=RuntimeError("simulated power interruption")):
            result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "received")
        count = len(self.called)
        await host.application.close()
        self.clock.advance(timedelta(minutes=11))
        replacement = await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)
        result = (await replacement.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertEqual(len(self.called), count)
        self.assertEqual(len(self.atoms(replacement.application)), 1)

    async def test_independent_valid_item_survives_and_repair_cannot_repeat_it(self):
        host, context, prepared = await self.setup_manual()
        app, original = host.application, self.model_behavior
        async def mixed(envelope):
            good = json.loads((await original(envelope)).draft.text)
            bad = {**good["proposals"][0]["proposal"], "predicate": "饮品习惯", "confidence": "high", "single_value": False}
            good["proposals"].append({"item_id": "bad", "proposal": bad})
            return answered(encode(good))
        self.model_behavior = mixed
        reflection = app.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "partial", result)
        self.assertEqual(result["unresolved_items"][0]["item_id"], "bad")
        self.assertEqual(len(self.atoms(app)), 1)
        retry = reflection.retry(context, prepared["batch_id"], request_id="repair-one").unwrap()
        self.assertEqual(reflection.retry(context, prepared["batch_id"], request_id="repair-one").unwrap(), retry)
        async def fix(envelope):
            offered = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "format_repair"))
            self.assertEqual([v["item_id"] for v in offered["items"]], ["bad"])
            item = offered["items"][0]["raw"]
            item["confidence"] = 0.8
            return answered(encode({"summary": "补齐独立项。", "proposals": [{"item_id": "bad", "proposal": item}]}))
        self.model_behavior = fix
        self.assertEqual((await reflection.run(context, retry["batch_id"])).unwrap()["state"], "complete")
        self.assertEqual(reflection.status(context, prepared["batch_id"]).unwrap()["state"], "complete")
        self.assertEqual(len(self.atoms(app)), 2)
        count = len(self.called)
        await reflection.run(context, retry["batch_id"])
        self.assertEqual(len(self.called), count)
        self.assertEqual(len(self.atoms(app)), 2)

    async def test_related_invalid_item_quarantines_the_fact_group(self):
        host, context, prepared = await self.setup_manual()
        original = self.model_behavior
        async def conflict(envelope):
            output = json.loads((await original(envelope)).draft.text)
            output["proposals"].append({"item_id": "conflict", "proposal": {
                **output["proposals"][0]["proposal"], "confidence": "bad"}})
            return answered(encode(output))
        self.model_behavior = conflict
        result = (await host.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(len(result["unresolved_items"]), 2)
        self.assertEqual(self.atoms(host.application), ())

    async def test_truncated_even_valid_json_is_captured_but_never_applied(self):
        host, context, prepared = await self.setup_reflection()
        original = self.model_behavior
        async def truncated(envelope):
            result = await original(envelope)
            return ProviderResponse("generated", captured_output=CapturedModelOutput(result.draft.text, "length"),
                                    usage=Amount(10, 10), receipt_ref="truncated")
        self.model_behavior = truncated
        count = len(self.called)
        result = (await host.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["finish_reason"], "length")
        self.assertEqual(len(self.called), count + 2)
        self.assertEqual(self.atoms(host.application), ())

    async def test_unknown_transport_is_not_repaired_or_replayed(self):
        host, context, prepared = await self.setup_reflection()
        async def interrupted(_):
            return ProviderResponse("unknown")
        self.model_behavior = interrupted
        count = len(self.called)
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "running")
        self.assertFalse(result["captured"])
        self.assertFalse(reflection.retry(context, prepared["batch_id"], request_id="no-repeat").is_success)
        self.assertFalse((await reflection.run(context, prepared["batch_id"])).is_success)
        self.assertEqual(len(self.called), count + 1)

    async def test_failed_batch_does_not_block_new_sources_or_advance_coverage(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app = host.application
        app.memory_schedule.configure(context, schedule_id="failure-day", expected_revision=0,
            role_instruction="原角色人格提示").unwrap()
        async def invalid(_):
            return answered("{invalid")
        original = self.model_behavior
        self.model_behavior = invalid
        schedule = dict(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'failure-day'", ()))
        first = await app.memory_schedule._run(schedule, self.clock.now())
        self.assertEqual(first["state"], "failed")
        row = dict(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'failure-day'", ()))
        self.assertIsNone(row["pending_batch"])
        self.assertEqual(row["covered_until"], schedule["covered_until"])
        self.model_behavior = original
        self.advance(app)
        message = host.receive(self.event("later", text="我确实不喝咖啡")).unwrap()
        self.advance(app)
        await self.done(app, message)
        async def new_sources(envelope):
            output = json.loads((await original(envelope)).draft.text)
            source = next(json.loads(m.payload) for m in envelope.original.context.materials
                          if m.purpose == "reflection_source" and json.loads(m.payload)["source_kind"] == "human_message")
            output["proposals"][0]["proposal"]["evidence_handles"] = [source["handle"]]
            return answered(encode(output))
        self.model_behavior = new_sources
        row["next_at"] = self.clock.now().isoformat()
        second = await app.memory_schedule._run(row, self.clock.now())
        self.assertEqual(second["state"], "complete", second)
        self.assertGreater(second["deferred_sources"], 0)
        self.assertEqual(len(self.atoms(app)), 1)
        self.assertEqual(app.runtime.read_one("SELECT covered_until FROM memory_schedules WHERE schedule_id = 'failure-day'", ())["covered_until"], schedule["covered_until"])

    async def test_revoked_source_cannot_be_restored_by_saved_output(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        with patch.object(reflection_recovery, "consume", side_effect=RuntimeError("interrupted")):
            await app.memory_reflection.run(context, prepared["batch_id"])
        from companion_v2.memory.codec import evidence
        manifest = json.loads(app.memory_reflection._row(context, prepared["batch_id"])["manifest_json"])
        source = evidence(manifest["evidence"][0]).source
        app.memory.writer.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        count = len(self.called)
        self.assertFalse((await app.memory_reflection.run(context, prepared["batch_id"])).is_success)
        result = app.memory_reflection.status(context, prepared["batch_id"]).unwrap()
        self.assertIsNone(result["summary"])
        self.assertNotIn("raw_output", result)
        self.assertEqual(len(self.called), count)
        self.assertEqual(self.atoms(app), ())

    async def test_compact_proposal_preserves_conditions_and_uses_host_ids(self):
        host, context, prepared = await self.setup_reflection()
        async def compact(_):
            return answered(encode({"summary": "保留情境。", "proposals": [{
                "item_id": None, "operation": "add", "evidence_handles": ["s1"], "predicate": "饮用偏好",
                "object": "不喝咖啡", "representation": "用户自述平时不喝咖啡。", "target": None,
                "details": [{"field": "qualifiers", "value": [{"key": "情境", "value": "平时"}]}],
            }], "pending_decisions": []}))
        self.model_behavior = compact
        result = (await host.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertIn("item-0001", result["receipts"])
        record = json.loads(self.atoms(host.application)[0]["record_json"])
        self.assertEqual(record["proposal"]["qualifiers"], {"情境": "平时"})

    async def test_repair_scope_cannot_expand_to_an_already_saved_item(self):
        host, context, prepared = await self.setup_manual()
        reflection, original = host.application.memory_reflection, self.model_behavior
        async def mixed(envelope):
            output = json.loads((await original(envelope)).draft.text)
            bad = {**output["proposals"][0]["proposal"], "predicate": "其他习惯", "confidence": "invalid"}
            output["proposals"].append({"item_id": "bad", "proposal": bad})
            return answered(encode(output))
        self.model_behavior = mixed
        await reflection.run(context, prepared["batch_id"])
        first = reflection.retry(context, prepared["batch_id"], request_id="scope-1").unwrap()
        # This response improperly repeats the already-written preference item.
        await reflection.run(context, first["batch_id"])
        self.assertEqual(len(self.atoms(host.application)), 1)
        second = reflection.retry(context, prepared["batch_id"], request_id="scope-2").unwrap()
        async def correct(envelope):
            material = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "format_repair"))
            self.assertEqual(material["allowed_item_ids"], ["bad"])
            output = json.loads((await original(envelope)).draft.text)
            item = output["proposals"][0]["proposal"]
            item["predicate"] = "其他习惯"
            return answered(encode({"summary": "只处理原失败项。", "proposals": [{"item_id": "bad", "proposal": item}]}))
        self.model_behavior = correct
        result = (await reflection.run(context, second["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertEqual(len(self.atoms(host.application)), 2)

    async def test_pending_acceptance_and_duplicate_add_are_not_split(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app, original = host.application, self.model_behavior
        from companion_v2.memory.models import Proposal
        evidence = next(iter(app.memory.query._documents(context, "personal", "sources")[1].values()))["evidence"]
        pending = app.memory.writer.submit(context, Proposal("add", predicate="饮用偏好", object="不喝咖啡",
            representation="用户不喝咖啡。", evidence=(evidence,), defer_until_boundary=True), operation_key="pending-overlap").unwrap()
        prepared = app.memory_reflection.prepare(context, request_id="pending-overlap", start=self.clock.now() - timedelta(hours=1),
            end=self.clock.now(), role_instruction="原角色人格提示").unwrap()
        async def overlap(envelope):
            output = json.loads((await original(envelope)).draft.text)
            output["pending_decisions"] = [{"operation_id": pending["operation_id"], "decision": "accept"}]
            return answered(encode(output))
        self.model_behavior = overlap
        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(len(result["unresolved_items"]), 2)
        self.assertEqual(self.atoms(app), ())
        self.assertEqual(app.runtime.read_one("SELECT state FROM memory_operations WHERE operation_id = ?", (pending["operation_id"],))["state"], "pending")

    async def test_stale_captured_output_remains_visible_for_explicit_repair(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        with patch.object(reflection_recovery, "consume", side_effect=RuntimeError("interrupted")):
            await app.memory_reflection.run(context, prepared["batch_id"])
        from companion_v2.memory.models import Proposal
        evidence = next(iter(app.memory.query._documents(context, "personal", "sources")[1].values()))["evidence"]
        app.memory.writer.submit(context, Proposal("add", predicate="另一事实", object="值", representation="另一事实。",
            evidence=(evidence,)), operation_key="concurrent-update").unwrap()
        count = len(self.called)
        self.assertEqual((await app.memory_reflection.run(context, prepared["batch_id"])).failure.code, "memory_reflection_stale")
        result = app.memory_reflection.status(context, prepared["batch_id"]).unwrap()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["captured"])
        self.assertTrue(result["retryable"])
        self.assertEqual(len(self.called), count)
        self.assertEqual(len(self.atoms(app)), 1)

    async def test_management_resume_can_replay_its_completed_receipt(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        with patch.object(reflection_recovery, "consume", side_effect=RuntimeError("interrupted")):
            await app.memory_reflection.run(context, prepared["batch_id"])
        manager = ReflectionManagement(app, self.core)
        resumed = manager.prepare_retry(context, batch_id=prepared["batch_id"], operation_key="resume-once").unwrap()
        self.assertEqual(resumed["batch_id"], prepared["batch_id"])
        count = len(self.called)
        completed = (await manager.run(context, resumed["batch_id"])).unwrap()
        repeated = manager.prepare_retry(context, batch_id=prepared["batch_id"], operation_key="resume-once").unwrap()
        self.assertEqual((await manager.run(context, repeated["batch_id"])).unwrap(), completed)
        self.assertEqual(len(self.called), count)
        self.assertEqual(len(self.atoms(app)), 1)

    async def test_legacy_modelled_record_keeps_repair_evidence_when_a_write_is_rejected(self):
        host, context, prepared = await self.setup_manual()
        reflection = host.application.memory_reflection
        with patch.object(reflection, "apply", return_value=Outcome.failed("interrupted", "fixture interruption")):
            await reflection.run(context, prepared["batch_id"])
        row = reflection._row(context, prepared["batch_id"])
        manifest, result = json.loads(row["manifest_json"]), json.loads(row["result_json"])
        for key in ("handles", "fact_handles", "fact_records"):
            manifest.pop(key, None)
        for key in ("raw_output", "issues", "rejected"):
            result.pop(key, None)
        with reflection.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET manifest_json = ?, result_json = ? WHERE batch_id = ?",
                               (encode(manifest), encode(result), prepared["batch_id"]))
        count = len(self.called)
        with patch.object(reflection.memory.writer, "submit", return_value=Outcome.failed("memory_fact_conflict", "fixture conflict")):
            restored = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(restored["state"], "partial")
        self.assertEqual(len(self.called), count)
        record = json.loads(reflection._row(context, prepared["batch_id"])["result_json"])
        self.assertEqual(record["rejected"][0]["raw"]["evidence_handles"], ["s1"])


class ReflectionOutputParsingTests(unittest.TestCase):
    def test_invalid_numbers_unicode_and_ambiguous_wrappers_stay_failures(self):
        inputs = [
            '{"summary":"x","proposals":[],"x":' + "9" * 5000 + '}',
            '{"summary":"\\ud800","proposals":[]}',
            '{"summary":"x","proposals":[],"x":NaN}',
            '下面是结果：{"summary":"x","proposals":[]}',
            '{"summary":"x","proposals":[]} {"summary":"y","proposals":[]}',
            '{"summary":"x","proposals":[],"summary":"y"}',
        ]
        for raw in inputs:
            with self.subTest(raw=raw[:80]):
                saved = redact_output(raw)
                saved.encode("utf-8")
                with self.assertRaises(OutputProblem):
                    parse_output({"text": saved, "finish_reason": "stop"})

    def test_escaped_tokens_and_credential_fact_values_are_redacted_before_storage(self):
        secret = "sk-" + "T" * 24
        body = encode({"summary": secret, "proposals": [{"operation": "add", "predicate": "password",
            "object": "fixture-password", "representation": "password=fixture-password", "evidence_handles": ["s1"]}]})
        body = body.replace("sk-", "\\u0073k-")
        saved = redact_output(body)
        self.assertNotIn(secret, saved)
        self.assertNotIn("fixture-password", saved)
        self.assertIn("[REDACTED]", saved)

    def test_one_soft_detail_word_does_not_discard_the_whole_proposal(self):
        """每日复盘曾整批 invalid_proposal：context_mode 写成了自由文本。"""
        from companion_v2.memory.reflection_output import normalize_proposal

        data = normalize_proposal({
            "operation": "add", "evidence_handles": ["s1"], "predicate": "照片话题互动方式",
            "object": "主动要求看自拍", "representation": "照片话题里的即时玩闹", "target": None,
            "details": [{"field": "context_mode", "value": "轻松私聊、围绕照片的玩笑话题"},
                        {"field": "single_value", "value": "true"},
                        {"field": "polarity", "value": "不明确"},
                        {"field": "retention", "value": "长期"},
                        {"field": "valid_to", "value": "下周"}]}, {"fact_handles": {}})

        self.assertNotIn("context_mode", data, "自由文本的 context_mode 应回落到默认 relevant")
        self.assertIs(data["single_value"], True)
        self.assertEqual(data["polarity"], "uncertain")
        self.assertEqual(data["retention"], "durable")
        self.assertNotIn("valid_to", data, "解析不了的时间不能带进 Proposal")

        pinned = normalize_proposal({"operation": "add", "evidence_handles": ["s1"],
            "details": [{"field": "context_mode", "value": "置顶"}]}, {"fact_handles": {}})
        self.assertEqual(pinned["context_mode"], "pinned")


if __name__ == "__main__":
    unittest.main()
