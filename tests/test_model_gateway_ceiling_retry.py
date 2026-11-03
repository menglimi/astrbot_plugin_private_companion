from __future__ import annotations

import logging
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation.foreground import ModelDraft
from companion_v2.execution.models import ProviderBinding, TaskError, TaskResult
from companion_v2.execution.runner import TaskRun
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import (Amount, BudgetPolicy, CapturedModelOutput, LocalProvider,
    ProviderResponse, ToolCall, ToolSpec)
from companion_v2.model_gateway.routing import ModelRouter, configuration_fields
from companion_v2.model_gateway.service import LocalModelGateway
from tests.test_model_gateway import ModelFixture

LOGGER_NAME = "companion_v2.model_gateway.service"


class CeilingRetryTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """A thinking-heavy truncation is retried once with a wider output ceiling.

    The provider's visible body is the only signal: a thinking model leaves a few
    percent of the declared ceiling, while a non-thinking model that really needs
    the room writes close to it and must not be re-dispatched at four times the
    price.
    """

    def setUp(self):
        super().setUp()
        self.messages = []
        handler = logging.Handler()
        handler.emit = lambda record: self.messages.append(record.getMessage())
        logging.getLogger(LOGGER_NAME).addHandler(handler)
        self.addCleanup(logging.getLogger(LOGGER_NAME).removeHandler, handler)
        self.rebuild()

    @staticmethod
    def wide_policy():
        amount = Amount(100_000, 1_000_000)
        return BudgetPolicy(amount, amount, amount, amount, background=amount)

    def rebuild(self, *, profile_ceiling=200, provider_ceiling=1000, policy=None, capabilities=("text",)):
        self.profile = replace(self.profiles[0], max_output_tokens=profile_ceiling)
        self.profiles = (self.profile, self.profiles[1])
        base_fields = tuple(field for field in self.registry.fields.values()
                            if not field.field_id.startswith("model.fixture."))
        self.registry = FieldRegistry(version="models.ceiling-retry.v1",
                                      fields=base_fields + configuration_fields(self.profiles))
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.providers = (self.ceiling_provider("primary", provider_ceiling, capabilities),
                          self.ceiling_provider("fallback", provider_ceiling, capabilities))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        generation = self.runtime.read_one(
            "SELECT generation FROM configuration_consumers WHERE consumer_id = 'model_gateway'", ())
        self.router.start(expected_generation=generation["generation"])
        self.ledger = BudgetLedger(self.runtime, policy or self.wide_policy())
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    def ceiling_provider(self, name, provider_ceiling, capabilities=("text",)):
        async def generate(envelope):
            self.called.append((name, envelope))
            return await self.responses[name](envelope)

        return LocalProvider(ProviderBinding("model.text", name, "1", "local-1"), capabilities, provider_ceiling,
                             lambda envelope: Amount(30 + envelope.max_output_tokens, 1000), generate)

    @staticmethod
    def cut(text="嗯" * 20):
        return ProviderResponse("generated", captured_output=CapturedModelOutput(text, "length"),
                                usage=Amount(15, 40), receipt_ref="receipt-cut")

    @staticmethod
    def success(text='{"ok": true}'):
        return ProviderResponse("generated", ModelDraft(text, ()), Amount(15, 40), "receipt-ok")

    @staticmethod
    def contract_failure():
        """A confirmed-usage structured failure: today's one contract fallback."""
        return ProviderResponse("unknown", usage=Amount(12, 30),
                                failure=ModelFailure.WIRE_DECODE, receipt_ref="receipt-contract")

    @staticmethod
    def tool_round():
        return ProviderResponse("generated", usage=Amount(15, 40), receipt_ref="receipt-tool",
                                tool_calls=(ToolCall("retry-tool-1", "memo.list", '{"limit": 2}'),))

    def respond_with(self, name, *responses):
        queue = list(responses)

        async def generate(envelope):
            return queue.pop(0)

        self.responses[name] = generate

    def model_run(self, identifier="task-1", *, context=None, kind="fixture.read", deadline_seconds=None):
        changes = ({} if deadline_seconds is None
                   else {"deadline_at": self.clock.now() + timedelta(seconds=deadline_seconds)})
        claim = self.running(identifier, context=context, kind=kind, **changes)
        task = self.tasks.read(context or self.context, identifier).unwrap()
        return TaskRun(self.tasks, claim, task)

    def rounds(self, run):
        return self.runtime.read_all(
            "SELECT round_no, state FROM model_rounds WHERE task_id = ? ORDER BY round_no",
            (run.task.task_id,))

    def retry_warnings(self):
        return [message for message in self.messages if "升限重试" in message]

    async def test_reasoning_cut_retries_once_with_a_wider_ceiling(self):
        self.respond_with("primary", self.cut(), self.success())
        run = self.model_run()

        completion = await self.gateway.generate(run, self.request())

        self.assertEqual([(name, envelope.max_output_tokens) for name, envelope in self.called],
                         [("primary", 200), ("primary", 800)])
        self.assertEqual(completion.draft.text, '{"ok": true}')
        self.assertEqual([row["position"] for row in self.call_rows()], [0, 2])
        self.assertEqual([(row["round_no"], row["state"]) for row in self.rounds(run)],
                         [(0, "started"), (1, "final")])
        # Both calls are real usage: the cut call settles before the retry reserves.
        self.assertTrue(all((account["spent_tokens"], account["held_tokens"]) == (30, 0)
                            for account in self.accounts()))

    async def test_visible_body_near_the_ceiling_is_not_retried(self):
        # 340 characters are about 189 tokens of the 200-token ceiling: this is a
        # body that really needs the room, not reasoning eating the budget.
        self.respond_with("primary", self.cut(text="可" * 340), self.success())

        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.retry_warnings(), [])

    async def test_no_retry_when_the_provider_cannot_go_higher(self):
        self.rebuild(provider_ceiling=200)
        self.respond_with("primary", self.cut(), self.success())

        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.retry_warnings(), [])

    async def test_second_truncation_keeps_the_code_and_makes_no_third_call(self):
        self.respond_with("primary", self.cut(), self.cut(), self.success())

        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual([name for name, _ in self.called], ["primary", "primary"])
        self.assertEqual(len(self.call_rows()), 2)
        self.assertEqual([row["position"] for row in self.call_rows()], [0, 2])

    async def test_retry_is_skipped_for_independent_reviews(self):
        self.respond_with("fallback", self.cut())
        run = self.model_run()
        request = self.request()
        session = self.gateway.open_session(run, request, independent_providers=("fallback", ""))

        with self.assertRaises(TaskError) as error:
            await session.generate(session.plan.envelope.original)

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual([name for name, _ in self.called], ["fallback"])
        # The second independent check still owns round 1.
        self.assertEqual([row["round_no"] for row in self.rounds(run)], [0])
        self.assertEqual(self.retry_warnings(), [])

    async def test_refused_retry_budget_keeps_the_original_failure(self):
        narrow = BudgetPolicy(*(Amount(500, 1000) for _ in range(4)), background=Amount(500, 1000))
        self.rebuild(policy=narrow)
        self.respond_with("primary", self.cut(), self.success())

        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual(len(self.called), 1)
        self.assertIsNone(self.runtime.read_one(
            "SELECT 1 FROM runtime_metadata WHERE key LIKE 'model.budget.deferral%'", ()))

    async def test_retry_respects_the_task_deadline(self):
        self.respond_with("primary", self.cut(), self.success())

        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(deadline_seconds=10), self.request())

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.retry_warnings(), [])

    async def test_ceiling_retry_is_diagnosable_from_logs_and_calls(self):
        self.respond_with("primary", self.cut(), self.success())
        run = self.model_run()

        await self.gateway.generate(run, self.request())

        self.assertTrue(any(
            "from=200" in message and "to=800" in message and "visible_tokens=12" in message
            and "reason=reasoning_headroom" in message for message in self.retry_warnings()),
            self.retry_warnings())
        calls = self.gateway.observe(self.context, run.task.task_id).unwrap()["calls"]
        self.assertEqual([(call["round"], call["state"]) for call in calls],
                         [(0, "discarded"), (1, "generated")])
        self.assertEqual(calls[0]["failure"]["code"], "response_contract_rejected")
        self.assertEqual([call["reserved_tokens"] for call in calls], [230, 830])
        audit = self.gateway.ceiling_retry_audit()
        record = audit["records"][-1]
        self.assertEqual((record["from"], record["to"], record["visible_chars"], record["outcome"]),
                         (200, 800, 20, "succeeded"))

    async def test_ceiling_retry_audit_is_bounded(self):
        calls = {"count": 0}

        async def alternating(envelope):
            calls["count"] += 1
            return self.cut() if calls["count"] % 2 else self.success()

        self.responses["primary"] = alternating
        tasks = self.supervisor(limits=replace(self.limits, retained_tasks=64, queued_per_lane=64))
        tasks.start(expected_generation=1).unwrap()
        self.tasks = tasks
        self.ledger = BudgetLedger(self.runtime, self.wide_policy())
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

        request = self.request()
        for index in range(40):
            run = self.model_run("ceiling-audit-" + str(index))
            completion = await self.gateway.generate(run, request)
            self.tasks.finish(run.claim, TaskResult("succeeded", result_ref=completion.call_id)).unwrap()

        records = self.gateway.ceiling_retry_audit()["records"]
        self.assertEqual(len(records), 32)

    async def test_contract_fallback_then_ceiling_retry_is_three_calls(self):
        # 一轮网关调用最多 3 次：主 provider 契约失败 1 次、备用 provider 被截断 1 次、
        # 升限重试 1 次。位置是 0/1/3（重试落在第 1 回合），第 0 回合只有前两次。
        self.respond_with("primary", self.contract_failure())
        self.respond_with("fallback", self.cut(), self.success())
        run = self.model_run()

        completion = await self.gateway.generate(run, self.request())

        self.assertEqual(completion.draft.text, '{"ok": true}')
        self.assertEqual([(name, envelope.max_output_tokens) for name, envelope in self.called],
                         [("primary", 200), ("fallback", 200), ("fallback", 800)])
        self.assertEqual(len(self.called), 3)
        self.assertEqual([row["position"] for row in self.call_rows()], [0, 1, 3])
        self.assertEqual([(row["round_no"], row["state"]) for row in self.rounds(run)],
                         [(0, "started"), (1, "final")])

    async def test_retry_tool_round_fails_safely_instead_of_using_another_round(self):
        # 升限重试已经把第 1 回合写进 model_rounds；若它带回工具调用，会话会带着同一个
        # 回合号再 INSERT 一次而撞主键。重试回合因此不允许变成工具回合：按原截断结论
        # 安全失败，错误码不变，也不留未结算的 call 行。
        self.rebuild(capabilities=("text", "tools"))
        self.respond_with("primary", self.cut(), self.tool_round())
        run = self.model_run()
        request = self.request()
        session = self.gateway.open_session(run, request, tools=(ToolSpec("memo.list", "Read memos", "{}"),))

        with self.assertRaises(TaskError) as error:
            await session.generate(request)

        self.assertEqual(error.exception.code, "model_output_truncated")
        self.assertEqual([(name, envelope.max_output_tokens) for name, envelope in self.called],
                         [("primary", 200), ("primary", 800)])
        self.assertEqual([(row["position"], row["state"]) for row in self.call_rows()],
                         [(0, "discarded"), (2, "discarded")])
        # 第 1 回合行没有被写成 tools：会话已安全关闭，再要一轮是既有 code，不是主键冲突。
        self.assertEqual([(row["round_no"], row["state"]) for row in self.rounds(run)],
                         [(0, "started"), (1, "started")])
        self.assertEqual(self.gateway.ceiling_retry_audit()["records"][-1]["outcome"], "failed")
        with self.assertRaises(TaskError) as closed:
            await session.generate(request)
        self.assertEqual(closed.exception.code, "model_session_closed")

    async def test_retry_round_bookkeeping_failure_leaves_no_dispatched_call(self):
        # 回合行与预算 hold 在同一个事务里：写入回合行时的校验失败（这里是配置代际
        # 冲突）必须连 hold 和 model_calls 一起回滚，不能留下"已派发但永远不会结算"的行。
        self.respond_with("primary", self.cut(), self.success())
        run = self.model_run()
        write_round = self.gateway._begin_round

        def begin_round_then_reject(*args):
            write_round(*args)
            raise TaskError("consumer_generation_conflict", "configuration generation changed")

        with patch.object(self.gateway, "_begin_round", side_effect=begin_round_then_reject):
            with self.assertRaises(TaskError) as error:
                await self.gateway.generate(run, self.request())

        self.assertEqual(error.exception.code, "consumer_generation_conflict")
        self.assertEqual([(row["position"], row["state"]) for row in self.call_rows()],
                         [(0, "discarded")])
        self.assertEqual([(row["round_no"], row["state"]) for row in self.rounds(run)],
                         [(0, "started")])
        self.assertTrue(all((account["held_tokens"], account["held_cost"]) == (0, 0)
                            for account in self.accounts()))
        self.assertEqual(self.gateway.ceiling_retry_audit()["records"][-1]["outcome"], "failed")

    async def test_owner_degrade_retry_keeps_the_six_call_bound(self):
        # owner 的整轮降载重试会再跑一整轮；每轮最多 3 次（契约回退 + 截断 + 升限重试），
        # 同一任务叠加后的上界是 6 次，且第二次 owner 重跑不会溢出第 7 次调用。
        self.respond_with("primary", self.contract_failure(), self.contract_failure())
        self.respond_with("fallback", self.cut(), self.cut(), self.cut(), self.cut())

        request = self.request()

        async def owner_round(identifier):
            for owner_attempt in range(2):
                run = self.model_run(identifier + "-" + str(owner_attempt))
                try:
                    return await self.gateway.generate(run, request)
                except TaskError as error:
                    # The runner retires the cut round before the owner resubmits
                    # under a new attempt token.
                    self.tasks.finish(run.claim, TaskResult("failed", retryable=True)).unwrap()
                    if error.code != "model_output_truncated" or owner_attempt == 1:
                        raise
            raise AssertionError("unreachable")

        with self.assertRaises(TaskError) as failure:
            await owner_round("owner-degrade")

        self.assertEqual(failure.exception.code, "model_output_truncated")
        self.assertEqual(len(self.called), 6)
        self.assertEqual([(name, envelope.max_output_tokens) for name, envelope in self.called],
                         [("primary", 200), ("fallback", 200), ("fallback", 800)] * 2)
        self.assertEqual([(row["task_id"], row["position"]) for row in self.call_rows()],
                         [("owner-degrade-0", 0), ("owner-degrade-0", 1), ("owner-degrade-0", 3),
                          ("owner-degrade-1", 0), ("owner-degrade-1", 1), ("owner-degrade-1", 3)])


if __name__ == "__main__":
    unittest.main()
