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
        existing_grants = identity.grants_for_subject(context.actor.subject)
        for index, capability in enumerate(("configuration.read", "configuration.write"), start=1):
            has_active = any(
                g.capability == capability and g.scope == target.authorization_scope and g.revoked_at is None
                for g in existing_grants
            )
            if not has_active:
                gid = f"diag-cmd-grant-{proposal_id}-{index}"
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

    async def test_confirm_reports_config_revision_conflict_and_enforces_write_permission_on_expired(self):
        from datetime import timedelta
        from companion_v2.foundation.refs import OperationRef, VersionRef

        context, target, proposal = await self._seed_proposal("repair-conflict-8c3d", new_value=False)
        # 1) Mutate target config directly -> confirm must report configuration_revision_conflict, not evidence changed
        snapshot = self.diagnostics.configuration.read(
            context.actor, target=target, group_id="conversation.presentation", observed_at=self.clock.now()
        ).unwrap()
        op = OperationRef("external-edit-1", "conversation.presentation", target.authorization_scope,
                          VersionRef("external-edit-1", 1), "external-edit-1")
        self.diagnostics.configuration.patch(
            context.actor, target=target, group_id="conversation.presentation", operation=op,
            expected_edit_token=snapshot.edit_token,
            changes={"conversation.presentation.group_wakeup_enabled": False},
            observed_at=self.clock.now(),
        ).unwrap()
        conflict_event = self._private_message("cmd-conflict", "陪伴 答疑确认 8c3d")
        res = (await receive_diagnostic_command(self.controller, conflict_event)).unwrap()
        self.assertIn("目标配置在生成建议后已变化", res["text"])

        # 2) Revoke write grant + expire proposal -> confirm must deny permission, not leak expiry
        context2, target2, _proposal2 = await self._seed_proposal("repair-perm-9f1e", new_value=True)
        identity = self.diagnostics.identity
        for grant in identity.grants_for_subject(context2.actor.subject):
            if grant.capability == "configuration.write" and grant.scope == target2.authorization_scope:
                identity.revoke_grant(grant.grant_id, revoked_at=self.clock.now(), expected_revision=identity.revision)
        self.clock.advance(timedelta(minutes=30))
        denied_event = self._private_message("cmd-denied-expired", "陪伴 答疑确认 9f1e")
        denied = (await receive_diagnostic_command(self.controller, denied_event)).unwrap()
        self.assertIn("权限", denied["text"])
        self.assertNotIn("已过期", denied["text"])

    async def test_bare_list_disambiguates_colliding_four_char_suffixes(self):
        await self._seed_proposal("repair-first-aaaa", new_value=False)
        await self._seed_proposal("repair-second-aaaa", new_value=False)
        list_event = self._private_message("cmd-collide-list", "陪伴 答疑确认")
        listed = (await receive_diagnostic_command(self.controller, list_event)).unwrap()
        self.assertIn("[rst-aaaa]", listed["text"])
        self.assertIn("[ond-aaaa]", listed["text"])
        self.assertNotIn("[aaaa]", listed["text"])


if __name__ == "__main__":
    unittest.main()
