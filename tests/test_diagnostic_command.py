"""Fast-path private '陪伴 答疑确认' / '陪伴 答疑取消' command tests."""

from __future__ import annotations

import unittest

from astrbot_host.diagnostic_command import (
    DiagnosticControlCommand,
    parse_diagnostic_command,
    receive_diagnostic_command,
)
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.diagnostics.models import DiagnosticContext, DiagnosticQuery
from companion_v2.diagnostics.repairs import RepairProposalService
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from tests.multi_host_support import MultiFixture


class DiagnosticCommandParsingTests(unittest.TestCase):
    def test_parses_bare_and_argument_variants_and_ignores_unrelated(self):
        self.assertEqual(parse_diagnostic_command("陪伴 答疑确认"), DiagnosticControlCommand("confirm", ""))
        self.assertEqual(parse_diagnostic_command("陪伴答疑确认"), DiagnosticControlCommand("confirm", ""))
        self.assertEqual(parse_diagnostic_command("陪伴 答疑确认 ab12"), DiagnosticControlCommand("confirm", "ab12"))
        self.assertEqual(parse_diagnostic_command("陪伴 应用答疑建议 repair-1234"), DiagnosticControlCommand("confirm", "repair-1234"))
        self.assertEqual(parse_diagnostic_command("陪伴 答疑取消"), DiagnosticControlCommand("cancel", ""))
        self.assertEqual(parse_diagnostic_command("陪伴 答疑取消 ab12"), DiagnosticControlCommand("cancel", "ab12"))
        self.assertIsNone(parse_diagnostic_command("陪伴 答疑设置"))
        self.assertIsNone(parse_diagnostic_command("陪伴 答疑 帮我看下配置"))
        self.assertIsNone(parse_diagnostic_command("普通聊天消息"))


class DiagnosticCommandFastPathTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.diagnostics = self.app.tools.diagnostics
        self.repairs = RepairProposalService(self.diagnostics)

    def _private_message(self, identifier: str, text: str):
        event = self.event(identifier=identifier, text=text)
        event.message_obj.raw_message = {
            "post_type": "message",
            "message_type": "private",
            "self_id": int(event.message_obj.self_id),
            "user_id": int(event.message_obj.sender.user_id),
            "message_id": identifier,
        }
        return event

    async def _seed_proposal(self, proposal_id: str, *, new_value: bool = False):
        event = self._private_message("seed-" + proposal_id, "陪伴 答疑 检查配置")
        plan = (await self.controller.prepare_core_reply_context(event)).unwrap()
        context = plan.context
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)
        identity = self.diagnostics.identity
        for index, capability in enumerate(("configuration.read", "configuration.write"), start=1):
            gid = f"diag-cmd-grant-{proposal_id}-{index}"
            if not any(g.grant_id == gid for g in identity.grants_for_subject(context.actor.subject)):
                identity.issue_grant(
                    grant_id=gid, subject=context.actor.subject,
                    scope=target.authorization_scope, capability=capability,
                    tier=AuthorizationTier.STANDARD, valid_from=self.clock.now(),
                    expected_revision=identity.revision,
                )
        original = DiagnosticContext(context.actor, context.role_binding, context.audience)
        report = self.diagnostics.inspect(
            original, DiagnosticQuery(target=target, group_id="conversation.presentation")
        ).unwrap()
        proposal = self.repairs.propose(
            original,
            proposal_id=proposal_id,
            report_id=report.report_id,
            changes={"conversation.presentation.group_wakeup_enabled": new_value},
            rationale="按检查结果临时关闭群唤醒观察",
        ).unwrap()
        return context, target, proposal

    async def test_bare_command_lists_pending_and_short_code_confirms_without_llm(self):
        async def fail_llm(payload, tools):
            raise AssertionError("diagnostic confirmation fast-path must never call LLM")

        self.native.provider.client.state.behavior = fail_llm
        context, target, proposal = await self._seed_proposal("repair-fast-path-7a9c", new_value=False)

        # Unrelated identity/policy activity before user replies must NOT stale the proposal.
        identity = self.diagnostics.identity
        policy = self.diagnostics.policy
        stranger = SubjectRef(context.scope.installation_id, SubjectKind.HUMAN, "stranger-999")
        identity.bind(
            binding_id="stranger-bind",
            principal=AuthenticatedHostPrincipal(
                context.scope.installation_id, "host", "onebot", "stranger", context.scope.bot_id, context.scope.account_id
            ),
            subject=stranger,
            valid_from=self.clock.now(),
            expected_revision=identity.revision,
        )
        policy.set_tier(stranger, AuthorizationTier.STANDARD, expected_revision=policy.revision, observed_at=self.clock.now())

        # 1) Bare '陪伴 答疑确认' lists pending with 4-char code
        list_event = self._private_message("cmd-list", "陪伴 答疑确认")
        listed = (await receive_diagnostic_command(self.controller, list_event)).unwrap()
        self.assertTrue(listed["handled"])
        self.assertIn("[7a9c]", listed["text"])
        self.assertIn("陪伴 答疑确认 7a9c", listed["text"])
        self.assertEqual(len(self.native.provider.client.state.calls), 0)

        # 2) Confirm using 4-char short code applies immediately
        confirm_event = self._private_message("cmd-confirm", "陪伴 答疑确认 7a9c")
        confirmed = (await receive_diagnostic_command(self.controller, confirm_event)).unwrap()
        self.assertTrue(confirmed["handled"])
        self.assertIn("已确认并保存配置修改", confirmed["text"])
        self.assertIn("7a9c", confirmed["text"])
        after = self.diagnostics.configuration.read(
            context.actor, target=target, group_id="conversation.presentation", observed_at=self.clock.now()
        ).unwrap()
        self.assertFalse(after.values["conversation.presentation.group_wakeup_enabled"])
        self.assertEqual(len(self.native.provider.client.state.calls), 0)
        api = self.native.platform.client._api._wsr_api
        self.assertEqual([action for action, _ in api.calls], ["send_private_msg", "send_private_msg"])

    async def test_short_code_cancel_leaves_config_untouched(self):
        context, target, _proposal = await self._seed_proposal("repair-cancel-4e2f", new_value=False)
        cancel_event = self._private_message("cmd-cancel", "陪伴 答疑取消 4e2f")
        cancelled = (await receive_diagnostic_command(self.controller, cancel_event)).unwrap()
        self.assertTrue(cancelled["handled"])
        self.assertIn("已取消修复建议", cancelled["text"])
        after = self.diagnostics.configuration.read(
            context.actor, target=target, group_id="conversation.presentation", observed_at=self.clock.now()
        ).unwrap()
        self.assertTrue(after.values["conversation.presentation.group_wakeup_enabled"])


if __name__ == "__main__":
    unittest.main()
