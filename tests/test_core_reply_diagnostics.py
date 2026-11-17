"""Existing source reads and diagnostics retain their native turn boundaries."""

from datetime import timedelta
import unittest

from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import require
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_tools import inject_core_reply_tools, native_tool_name
from astrbot_host.functional_commands import classify_functional_command
from tests.multi_host_support import MultiFixture
from tests.test_core_reply_owner_tools import NativeOwnerCalls


class CoreReplyDiagnosticToolsTests(NativeOwnerCalls, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def test_offered_source_read_replays_and_rechecks_withdrawal(self):
        event = self.event(identifier="native-source-read", text="刚刚看完了一篇新闻")
        prepared, request = await self.bind(event)
        evidence = await self.call(event, request, "turn.evidence", {})
        handle = evidence["evidence_handles"][0]
        result = await self.call(event, request, "source.read", {"handle": handle}, "read-current-input")
        self.assertEqual((result["state"], result["effect"]), ("succeeded", "none"), result)
        self.assertIn("刚刚看完了一篇新闻", result["payload"])
        replay = await self.call(event, request, "source.read", {"handle": handle}, "read-current-input")
        self.assertTrue(replay["replayed"], replay)
        self.assertEqual(result["content_hash"], replay["content_hash"])
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(prepared["context"].scope,
            prepared["event_id"], prepared["context"].actor.principal.native_account_id)))
        withdrawn = await self.call(event, request, "source.read", {"handle": handle}, "read-current-input")
        self.assertEqual(withdrawn["state"], "failed", withdrawn)
        self.assertNotIn("payload", withdrawn)
        self.assert_no_reply_work()

    async def test_source_handle_from_another_native_turn_is_unavailable(self):
        event = self.event(identifier="native-source-first")
        _, request = await self.bind(event)
        evidence = await self.call(event, request, "turn.evidence", {})
        second = self.event(identifier="native-source-second")
        _, second_request = await self.bind(second)
        result = await self.call(second, second_request, "source.read",
            {"handle": evidence["evidence_handles"][0]})
        self.assertEqual(result["code"], "tool_source_unavailable", result)
        self.assert_no_reply_work()

    async def test_design_explanation_and_actual_inspection_use_the_existing_owner(self):
        event = self.event(identifier="native-diagnostic-tools")
        _, request = await self.bind(event)
        explanation = await self.call(event, request, "diagnostics.explain", {"topic": "capability"})
        self.assertEqual(explanation["state"], "succeeded", explanation)
        self.assertEqual(explanation["report"]["mode"], "explain", explanation)
        self.assertEqual(explanation["report"]["results"], [], explanation)
        inspected = await self.call(event, request, "diagnostics.inspect", {"check": "execution"})
        self.assertEqual(inspected["state"], "succeeded", inspected)
        self.assertEqual(inspected["report"]["mode"], "self_check", inspected)
        self.assertTrue(inspected["report"]["results"], inspected)
        replay = await self.call(event, request, "diagnostics.inspect", {"check": "execution"})
        self.assertTrue(replay["replayed"], replay)
        self.assertEqual(replay["report"]["report_id"], inspected["report"]["report_id"])
        self.assert_no_reply_work()

    async def test_expired_diagnostic_report_is_not_replaced_during_replay(self):
        event = self.event(identifier="native-diagnostic-expiry")
        _, request = await self.bind(event)
        explanation = await self.call(event, request, "diagnostics.explain", {"topic": "operation"})
        self.assertEqual(explanation["state"], "succeeded", explanation)
        self.clock.advance(timedelta(minutes=2))
        expired = await self.call(event, request, "diagnostics.explain", {"topic": "operation"})
        self.assertEqual(expired["code"], "diagnostic_report_expired", expired)
        self.assertNotIn("report", expired)
        self.assert_no_reply_work()

    async def test_invalid_arguments_return_failure_and_partial_query_uses_defaults(self):
        event = self.event(identifier="native-diagnostic-arguments")
        _, request = await self.bind(event)
        for action, arguments in (("source.read", {"handle": []}),
                                  ("diagnostics.explain", {"topic": []}),
                                  ("diagnostics.inspect", {"query": {"window": {}}})):
            result = await self.call(event, request, action, arguments, "invalid-" + action)
            self.assertEqual(result["code"], "invalid_tool_arguments", result)
        denied = await self.call(event, request, "diagnostics.inspect", {"check": "capability", "extension_id": "content"})
        self.assertEqual(denied["code"], "authorization_denied", denied)
        self.assertNotIn("report", denied)
        result = await self.call(event, request, "diagnostics.inspect", {"check": "execution"}, "inspect-local-execution")
        self.assertEqual(result["state"], "succeeded", result)
        self.assert_no_reply_work()

    async def test_confirmation_tools_are_scoped_to_the_exact_private_command(self):
        event = self.event(identifier="native-diagnostic-confirm", text="陪伴 答疑确认 repair-123")
        _, request = await self.bind(event)
        command = classify_functional_command(event, channel="private")
        self.assertEqual((command.intent, command.argument), ("diagnostics_confirm", "repair-123"))
        inject_core_reply_tools(event, request, application=self.app)
        tool = request.func_tool.get_tool(native_tool_name("diagnostics.proposal.confirm"))
        self.assertIsNotNone(tool)
        self.assertEqual(tool.parameters["properties"]["proposal_id"]["const"], "repair-123")
        wrong = await self.call(event, request, "diagnostics.proposal.confirm", {"proposal_id": "repair-other"})
        self.assertEqual(wrong["code"], "invalid_tool_arguments", wrong)
        missing = await self.call(event, request, "diagnostics.proposal.confirm", {"proposal_id": "repair-123"})
        self.assertEqual(missing["code"], "repair_proposal_unavailable", missing)
        self.assert_no_reply_work()

    async def test_inspect_branch_errors_are_safe_native_failures(self):
        event = self.event(identifier="native-diagnostic-branch-errors")
        _, request = await self.bind(event)
        for index, arguments in enumerate((
            {"check": "execution", "window_minutes": 60},
            {"check": "capability", "capability_id": "image.generate"},
            {"check": "storage", "private-secret-name": "private-secret-value"},
        )):
            with self.subTest(arguments=arguments):
                result = await self.call(event, request, "diagnostics.inspect", arguments, f"invalid-branch-{index}")
                self.assertEqual((result["state"], result["code"]), ("failed", "invalid_tool_arguments"), result)
                self.assertNotIn("report", result)
                self.assertNotIn("private-secret", str(result))
        self.assert_no_reply_work()

    async def test_confirmation_tool_is_not_offered_for_natural_language(self):
        event = self.event(identifier="native-diagnostic-natural-confirm", text="请确认一下建议 repair-123")
        _, request = await self.bind(event)
        classify_functional_command(event, channel="private")
        self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["functional_command"])
        inject_core_reply_tools(event, request, application=self.app)
        self.assertIsNone(request.func_tool.get_tool(native_tool_name("diagnostics.proposal.confirm")))
        self.assert_no_reply_work()

    async def test_repair_proposal_is_cancelled_or_confirmed_only_by_its_exact_private_command(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.identity_policy.models import AuthorizationTier

        check_event = self.event(identifier="native-diagnostic-repair-check", text="陪伴 答疑 检查回复配置")
        prepared, check_request = await self.bind(check_event)
        context = prepared["context"]
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)
        identity = self.app.tools.diagnostics.identity
        for index, capability in enumerate(("configuration.read", "configuration.write"), start=1):
            identity.issue_grant(grant_id=f"diagnostic-repair-{index}", subject=context.actor.subject,
                scope=target.authorization_scope, capability=capability, tier=AuthorizationTier.STANDARD,
                valid_from=self.clock.now(), expected_revision=identity.revision)
        classify_functional_command(check_event, channel="private")
        report = await self.call(check_event, check_request, "diagnostics.inspect",
            {"check": "configuration", "group_id": "conversation.presentation"}, "repair-check")
        self.assertEqual(report["state"], "succeeded", report)
        before = self.app.tools.diagnostics.configuration.read(context.actor, target=target,
            group_id="conversation.presentation", observed_at=self.clock.now()).unwrap()
        field = "conversation.presentation.group_wakeup_enabled"
        self.assertTrue(before.values[field])

        async def propose(call_id):
            event = self.event(identifier="native-diagnostic-" + call_id, text="陪伴 答疑 修复配置")
            _, request = await self.bind(event)
            classify_functional_command(event, channel="private")
            result = await self.call(event, request, "diagnostics.propose", {
                "report_id": report["report"]["report_id"],
                "changes": {"conversation.presentation.group_wakeup_enabled": False},
                "rationale": "按检查结果关闭输出进行观察。",
            }, call_id)
            self.assertEqual((result["state"], result["effect"]), ("succeeded", "committed"), result)
            self.assertNotIn("review_token", result["proposal"])
            return result["proposal"]["proposal_id"]

        cancel_id = await propose("repair-cancel-propose")
        cancel_event = self.event(identifier="native-diagnostic-cancel", text=f"陪伴 答疑取消 {cancel_id}")
        _, cancel_request = await self.bind(cancel_event)
        cancel_command = classify_functional_command(cancel_event, channel="private")
        self.assertEqual((cancel_command.intent, cancel_command.argument), ("diagnostics_cancel", cancel_id))
        inject_core_reply_tools(cancel_event, cancel_request, application=self.app)
        cancel_result = await self.call(cancel_event, cancel_request, "diagnostics.proposal.cancel",
            {"proposal_id": cancel_id}, "repair-cancel")
        self.assertEqual((cancel_result["state"], cancel_result["effect"], cancel_result["proposal"]["state"]),
                         ("succeeded", "committed", "cancelled"), cancel_result)
        self.assertEqual(cancel_result["proposal"]["receipt"]["effect"], "proposal_cancelled")
        after_cancel = self.app.tools.diagnostics.configuration.read(context.actor, target=target,
            group_id="conversation.presentation", observed_at=self.clock.now()).unwrap()
        self.assertTrue(after_cancel.values[field])

        confirm_id = await propose("repair-confirm-propose")
        confirm_event = self.event(identifier="native-diagnostic-confirm-exact", text=f"陪伴 答疑确认 {confirm_id}")
        _, confirm_request = await self.bind(confirm_event)
        confirm_command = classify_functional_command(confirm_event, channel="private")
        self.assertEqual((confirm_command.intent, confirm_command.argument), ("diagnostics_confirm", confirm_id))
        inject_core_reply_tools(confirm_event, confirm_request, application=self.app)
        confirm_result = await self.call(confirm_event, confirm_request, "diagnostics.proposal.confirm",
            {"proposal_id": confirm_id}, "repair-confirm")
        self.assertEqual((confirm_result["state"], confirm_result["effect"], confirm_result["proposal"]["state"]),
                         ("succeeded", "committed", "confirmed"), confirm_result)
        after_confirm = self.app.tools.diagnostics.configuration.read(context.actor, target=target,
            group_id="conversation.presentation", observed_at=self.clock.now()).unwrap()
        self.assertFalse(after_confirm.values[field])
        self.assert_no_reply_work()


if __name__ == "__main__":
    unittest.main()
