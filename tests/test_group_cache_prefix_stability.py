"""Regression tests for group cache-prefix stability (tools_hash, sys_hash) and GROUP_INSTRUCTION dedup."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest

from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.astr_agent_tool_exec import call_local_llm_tool

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_context import GROUP_INSTRUCTION, build_core_reply_prompt
from astrbot_host.core_reply_media_prompt import core_media_request_diagnostics, finalize_core_reply_media_prompt
from astrbot_host.core_reply_reaction_delivery import inject_core_reaction_instruction
from astrbot_host.core_reply_telemetry import _request_signature, _signature
from astrbot_host.core_reply_tools import (
    close_core_reply_tools, inject_core_reply_tools, inject_core_turn_notes, native_tool_name,
)
from companion_v2.capabilities.models import CapabilityPackageDescriptor
from companion_v2.capabilities.reality_access import (
    ACTION as REALITY_SNAPSHOT_ACTION, EXTENSION as REALITY_EXTENSION, PACKAGE_ID as REALITY_PACKAGE_ID,
    RealitySnapshotAccess,
)
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.capabilities.screen_access import (
    ACTION as SCREEN_ACTION, CAPABILITIES as SCREEN_CAPABILITIES, EXTENSION as SCREEN_EXTENSION,
    PACKAGE_ID as SCREEN_PACKAGE_ID, PROVIDER as SCREEN_PROVIDER, ScreenAccess,
)
from companion_v2.conversation.external_tools import (
    ExtensionToolDescriptor, ExternalToolExecutor, ExternalToolProvider, ExternalToolResponse,
)
from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.conversation.tool_loop import LocalRoleTools
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.execution.models import ProviderBinding, SideEffect, TaskContext, TaskError, digest, encode, require
from companion_v2.expression.reply_intent import INSTRUCTION as REACTION_INSTRUCTION
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.model_gateway.models import ToolSpec
from companion_v2.sources.models import SourceRecord
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW, SCOPE


SCREEN_PACKAGE = CapabilityPackageDescriptor(
    SCREEN_EXTENSION, SCREEN_PACKAGE_ID, "private-companion-v2", "7.2.7", "screen-artifact-hash",
    SCREEN_CAPABILITIES, ("screen",),
)
REALITY_PACKAGE = CapabilityPackageDescriptor(
    REALITY_EXTENSION, REALITY_PACKAGE_ID, "private-companion-v2", "7.2.7", "reality-artifact-hash",
    ("reality.snapshot.read", "reality.camera.observe", "reality.device.control"), ("reality",),
)


class GroupToolsHashStabilityTests(MessageFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.invocations = []
        self.screen_descriptor = ExtensionToolDescriptor(
            ToolSpec(SCREEN_ACTION, "采集桌面屏幕画面。", encode({
                "type": "object", "properties": {}, "additionalProperties": False,
            })),
            SCREEN_PACKAGE,
            ProviderBinding("screen.process", SCREEN_PROVIDER, "7.2.7", "screen-gen-1"),
            "screen", SideEffect.EXTERNAL_DEVICE, timeout_seconds=10,
        )
        self.reality_descriptors = tuple(
            ExtensionToolDescriptor(
                ToolSpec(action, f"Reality action {action}.", encode({
                    "type": "object", "properties": {}, "additionalProperties": False,
                })),
                REALITY_PACKAGE,
                ProviderBinding(action, "reality.native", "7.2.7", "reality-gen-1"),
                "reality",
                SideEffect.EXTERNAL_NETWORK if action == "reality.device.control" else SideEffect.NONE,
                timeout_seconds=10,
            )
            for action in ("reality.snapshot.read", "reality.camera.observe", "reality.device.control")
        )
        self.descriptors = (self.screen_descriptor, *self.reality_descriptors)
        self.bindings = {item.tool.action: item.binding for item in self.descriptors}
        super().setUp()
        self.group_scope = replace(
            SCOPE, kind=ScopeKind.GROUP, native_scope_id="group-cache-1",
            conversation_id="group-conv-1", session_id="group-sess-1",
        )
        self.personas.register_route(ConversationRoute(self.group_scope, group_id="group-cache-1"), observed_at=NOW)
        self.admin_context = self._member_context(self.core, grant_invoke=False)
        self.member_context = self._member_context(self.standard, grant_invoke=True)
        self.tools = LocalRoleTools(self.messages, external=self.external)
        self.capabilities = CapabilityRepository(self.runtime)
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM capability_descriptors WHERE extension_id = ?", (SCREEN_EXTENSION,))
        for package in (SCREEN_PACKAGE, REALITY_PACKAGE):
            self.capabilities.register_descriptor(package, registered_at=NOW).unwrap()
            self.capabilities.acquire(
                extension_id=package.extension_id, package_id=package.package_id,
                package_version=package.package_version, expected_revision=0, observed_at=NOW,
            ).unwrap()
            self.capabilities.set_role_enabled(
                extension_id=package.extension_id, role=self.admin_context.role_binding.role,
                bot_id=self.group_scope.bot_id, enabled=True, expected_revision=0, observed_at=NOW,
            ).unwrap()
        self.app = NS(
            runtime=self.runtime, tasks=self.tasks, messages=self.messages,
            tools=self.tools, external=self.external,
            require_running=lambda: require(self.tasks.check_generation()),
        )
        self.addAsyncCleanup(close_core_reply_tools, self.app)

    def components(self, runtime, *, takeover=False):
        providers = tuple(
            ExternalToolProvider(
                descriptor, self.invoke,
                lambda action=descriptor.tool.action: self.bindings.get(action),
            )
            for descriptor in self.descriptors
        )
        external = ExternalToolExecutor(runtime, providers)
        registrations = self.registrations
        self.registrations += external.registrations
        try:
            tasks, messages, delivery = super().components(runtime, takeover=takeover)
        finally:
            self.registrations = registrations
        self.external = external.attach(messages)
        self.addAsyncCleanup(external.close)
        return tasks, messages, delivery

    def _member_context(self, actor, *, grant_invoke):
        actions = ["text", "conversation.reply", "configuration.read", "group.memory.read", "group.memory.write"]
        if grant_invoke:
            actions.append("extension.tool.invoke")
        for action in actions:
            self.grant(actor, self.group_scope, action)
        role = self.identity.resolve_role(self.group_scope, observed_at=NOW).unwrap()
        return TaskContext(actor, role, group_audience(self.group_scope, actor.subject))

    async def invoke(self, invocation):
        self.invocations.append(invocation)
        reference = SourceRef(
            "source-" + invocation.invocation_id, invocation.descriptor.source_owner,
            invocation.context.scope, VersionRef(invocation.invocation_id, 1), self.clock.now(),
        )
        payload = encode({"captured": True, "action": invocation.descriptor.tool.action})
        source = SourceRecord(reference, invocation.context.audience, digest(payload), payload)
        effect = "committed" if invocation.descriptor.side_effect is not SideEffect.NONE else "none"
        return ExternalToolResponse(
            invocation.invocation_id, invocation.context.key, invocation.arguments_hash,
            invocation.descriptor.binding, "succeeded", effect, source,
            "op-" + invocation.invocation_id if effect == "committed" else None,
            "rcpt-" + invocation.invocation_id if effect == "committed" else None,
        )

    def _prepare_request(self, context, *, profile="full", event_id="evt"):
        event = FakeEvent()

        async def authorize(_prepared):
            return Outcome.success(True)

        prepared = {"context": context, "event_id": event_id, "group_tool_profile": profile}
        prepare_core_reply_event(event, prepared, authorize=authorize)
        req = NS(model="test-model", system_prompt="group persona", prompt="hi", contexts=[],
                 func_tool=None, extra_user_content_parts=[])
        inject_core_reply_tools(event, req, application=self.app)
        return event, req

    async def _call_native(self, event, req, action, call_id="call-1"):
        tool = req.func_tool.get_tool(native_tool_name(action))
        self.assertIsNotNone(tool, f"expected tool {action} to be mounted in catalog")
        call = ToolCall(id=call_id, function=ToolCall.FunctionBody(name=tool.name, arguments="{}"))
        wrapper = NS(context=NS(event=event), messages=[Message(role="assistant", tool_calls=[call])])
        output = [value async for value in call_local_llm_tool(wrapper, tool.call, "call")]
        return json.loads(output[-1])

    def test_group_tools_hash_invariant_across_admin_and_member_for_all_profiles(self):
        for profile in ("full", "standard", "lean"):
            with self.subTest(profile=profile):
                _, req_admin = self._prepare_request(self.admin_context, profile=profile, event_id=f"admin-{profile}")
                _, req_member = self._prepare_request(self.member_context, profile=profile, event_id=f"member-{profile}")
                admin_names = [tool.name for tool in req_admin.func_tool.tools]
                member_names = [tool.name for tool in req_member.func_tool.tools]
                self.assertEqual(admin_names, member_names)
                self.assertEqual(
                    _request_signature(req_admin)["tools_hash"],
                    _request_signature(req_member)["tools_hash"],
                )
                self.assertIn(native_tool_name("reality.camera.observe"), admin_names)
                if profile == "full":
                    self.assertIn(native_tool_name(SCREEN_ACTION), admin_names)
                    self.assertIn(native_tool_name("reality.snapshot.read"), admin_names)
                    self.assertIn(native_tool_name("reality.device.control"), admin_names)

        # Disabling screen & reality at the role/window level removes them identically for both speakers.
        for package in (SCREEN_PACKAGE, REALITY_PACKAGE):
            self.capabilities.set_role_enabled(
                extension_id=package.extension_id, role=self.admin_context.role_binding.role,
                bot_id=self.group_scope.bot_id, enabled=False, expected_revision=1, observed_at=NOW,
            ).unwrap()
        for profile in ("full", "standard", "lean"):
            with self.subTest(disabled_profile=profile):
                _, req_admin = self._prepare_request(self.admin_context, profile=profile, event_id=f"off-a-{profile}")
                _, req_member = self._prepare_request(self.member_context, profile=profile, event_id=f"off-m-{profile}")
                admin_names = [tool.name for tool in req_admin.func_tool.tools]
                member_names = [tool.name for tool in req_member.func_tool.tools]
                self.assertEqual(admin_names, member_names)
                self.assertEqual(
                    _request_signature(req_admin)["tools_hash"],
                    _request_signature(req_member)["tools_hash"],
                )
                self.assertNotIn(native_tool_name(SCREEN_ACTION), admin_names)
                self.assertNotIn(native_tool_name("reality.camera.observe"), admin_names)

    async def test_non_admin_group_member_strictly_refused_at_execution_time(self):
        member_event, member_req = self._prepare_request(self.member_context, profile="full", event_id="member-exec")
        expected_codes = (
            (SCREEN_ACTION, "screen_scope_denied"),
            ("reality.camera.observe", "reality_camera_private_required"),
            ("reality.snapshot.read", "reality_admin_only"),
            ("reality.device.control", "reality_admin_only"),
        )
        for index, (action, expected_code) in enumerate(expected_codes):
            with self.subTest(action=action):
                result = await self._call_native(member_event, member_req, action, call_id=f"deny-{index}")
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["effect"], "none")
                self.assertEqual(result["code"], expected_code)
                self.assertEqual(result["retry_disposition"], "stop")
                self.assertIn("核心管理员", result.get("reason", ""))
        self.assertEqual(self.invocations, [])
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks", ())["n"], 0)

        # Core admin in the same group executes screen.capture successfully.
        admin_event, admin_req = self._prepare_request(self.admin_context, profile="full", event_id="admin-exec")
        allowed = await self._call_native(admin_event, admin_req, SCREEN_ACTION, call_id="allow-screen")
        self.assertEqual(allowed["state"], "succeeded")
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual(self.invocations[0].descriptor.tool.action, SCREEN_ACTION)

    def test_screen_and_reality_access_status_reflect_actual_invokability(self):
        member_screen = ScreenAccess(self.app, self.member_context, self.screen_descriptor).status(self.core, NOW)
        admin_screen = ScreenAccess(self.app, self.admin_context, self.screen_descriptor).status(self.core, NOW)
        self.assertFalse(member_screen["invokable"])
        self.assertTrue(admin_screen["invokable"])

        member_reality = RealitySnapshotAccess(
            self.app, self.member_context, REALITY_PACKAGE, capability=REALITY_SNAPSHOT_ACTION,
        ).status(self.core, NOW)
        admin_reality = RealitySnapshotAccess(
            self.app, self.admin_context, REALITY_PACKAGE, capability=REALITY_SNAPSHOT_ACTION,
        ).status(self.core, NOW)
        self.assertFalse(member_reality["invokable"])
        self.assertTrue(admin_reality["invokable"])


class ReactionSysHashStabilityTests(unittest.TestCase):
    def _run_turn(self, *, capable, enabled, voice_policy=None):
        event = FakeEvent()
        context = NS(scope=NS(kind=ScopeKind.GROUP))
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {
            "prepared": {"event_ref": "turn-ref", "context": context, "voice_reply_policy": voice_policy},
            "_turn_notes": {},
        })
        semantic = NS(
            reaction_capable=lambda _ctx: capable,
            reaction_available=lambda _ctx, _turn_id: enabled,
        )
        req = NS(system_prompt="固定角色人格。", prompt="哈哈", extra_user_content_parts=[])
        inject_core_reaction_instruction(event, req, semantic, "turn-1")
        inject_core_turn_notes(event, req)
        run_context = NS(messages=[
            Message(role="system", content=req.system_prompt),
            Message(role="user", content="哈哈"),
        ])
        finalize_core_reply_media_prompt(event, run_context)
        system_content = run_context.messages[0].content
        sig = _signature("test-model", [{"role": "system", "content": system_content}], None)
        notes = "\n".join(getattr(part, "text", "") for part in req.extra_user_content_parts)
        diagnostics = core_media_request_diagnostics(event, run_context)
        return system_content, sig["system_hash"], notes, diagnostics

    def test_consecutive_turns_flipping_reaction_enabled_keep_identical_system_prompt_and_sys_hash(self):
        ja_voice = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        for label, voice_policy in (("text_only", None), ("ja_translation_voice", ja_voice)):
            with self.subTest(mode=label):
                sys_on, hash_on, notes_on, diag_on = self._run_turn(
                    capable=True, enabled=True, voice_policy=voice_policy)
                sys_off, hash_off, notes_off, diag_off = self._run_turn(
                    capable=True, enabled=False, voice_policy=voice_policy)
                self.assertEqual(sys_on, sys_off)
                self.assertEqual(hash_on, hash_off)
                self.assertEqual(sys_on.count(REACTION_INSTRUCTION), 1)
                self.assertIn("本轮图库表情可用", notes_on)
                self.assertIn("本轮不使用图库表情", notes_off)
                self.assertTrue(diag_on["reaction_enabled"])
                self.assertTrue(diag_on["reaction_prompt_present"])
                self.assertFalse(diag_off["reaction_enabled"])
                self.assertTrue(diag_off["reaction_prompt_present"])

    def test_reaction_incapable_window_omits_reaction_prompt_and_turn_note(self):
        sys_text, _, notes, diag = self._run_turn(capable=False, enabled=False, voice_policy=None)
        self.assertEqual(sys_text, "固定角色人格。")
        self.assertEqual(notes, "")
        self.assertIsNone(diag)


class GroupInstructionDedupTests(unittest.TestCase):
    @staticmethod
    def _ready_contribution(purpose, payload):
        return ContextContribution(
            contribution_id=f"contrib-{purpose}", owner="test", purpose=purpose,
            state=ContributionState.READY, payload=json.dumps(payload, ensure_ascii=False),
        )

    def _build_group_prompt(self, *, has_person_card=True, speaker_fails=False):
        card = (self._ready_contribution("person_card", {"native_account_id": "u1", "display_name": "小明"})
                if has_person_card else None)
        network = self._ready_contribution("group_network", {"members": [{"id": "u1"}]})
        memory = self._ready_contribution("group_memory", {"entries": [{"text": "昨天聊过电影"}]})

        def authorize_group(_ctx, _at):
            if speaker_fails:
                raise TaskError("group_context_denied", "group context unavailable")

        messages = NS(
            acquaintance=NS(card_contribution=lambda _ctx, _at: card),
            group_context=NS(authorize=authorize_group),
            group_network=NS(contribution=lambda _ctx, _inputs, at=None: network),
            group_memory=NS(context=NS(
                capture=lambda _ctx, _key: object(),
                contributions=lambda _snap, _ctx, _key: (memory,),
            )),
            role_state_context=None,
            expression_style_context=None,
            cross_window_context=None,
            sent_messages=None,
            knowledge=None,
            memory_context=None,
            proactive_inspirations=None,
            relationship_profile_context=None,
            relationship_context=None,
            life_context=None,
            role_growth_context=None,
            role_self_review_context=None,
            open_items_context=None,
        )
        application = NS(
            messages=messages,
            runtime=None,
            tools=NS(group_participation=None),
            router=NS(profiles={}),
            reply_settings=NS(snapshot=lambda *_args, **_kwargs: NS(values={
                "reply.style_prompt": "", "reply.prompt_style_mode": "balanced",
                "reply.enable_humanized_expression": False, "reply.humanized_expression_prompt": "",
            })),
            perception_settings=NS(capture=lambda _ctx, _at: {"settings": {
                "guidance": "", "calendar_timezone": "Asia/Shanghai", "calendar_enabled": False,
                "health_enabled": False, "platform_enabled": False, "model_route_enabled": False,
                "configured_modalities_enabled": False,
            }}),
            background_history=None,
            memory=None,
        )
        context = NS(
            scope=NS(kind=ScopeKind.GROUP, native_scope_id="g-100", installation_id="inst",
                     platform="onebot", bot_id="bot-1", account_id="acc-1",
                     conversation_id="conv-1", session_id="sess-1"),
            key="group:g-100",
            actor=NS(principal=NS(native_account_id="u1"), subject=NS(subject_id="sub-u1")),
            role_binding=NS(role=NS(subject_id="role-1")),
        )
        native_input = NS(text="大家好", event_id="evt-1", at=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
        return build_core_reply_prompt(application, context, native_input,
                                       at=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))

    def test_group_instruction_appears_once_when_all_four_group_sections_present(self):
        prompt = self._build_group_prompt(has_person_card=True, speaker_fails=False)
        for section in ("person_card", "group_speaker", "group_network", "group_memory"):
            self.assertIn(section, prompt.sections)
        self.assertEqual(prompt.instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(prompt.context_instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(json.dumps(prompt.context_blocks, ensure_ascii=False).count(GROUP_INSTRUCTION), 1)
        # All data payloads remain intact while duplicate instructions are omitted cleanly.
        self.assertIn("小明", prompt.context_instruction)
        self.assertIn('"speaker_id":"u1"', prompt.context_instruction)
        self.assertIn("昨天聊过电影", prompt.context_instruction)
        self.assertNotIn("\n\n【群成员与近期互动】\n\n", prompt.context_instruction)
        network_block = next(block for block in prompt.context_blocks if block.get("title") == "群成员与近期互动")
        self.assertNotIn("instruction", network_block)

    def test_group_instruction_falls_back_to_group_speaker_when_person_card_empty(self):
        prompt = self._build_group_prompt(has_person_card=False, speaker_fails=False)
        self.assertNotIn("person_card", prompt.sections)
        for section in ("group_speaker", "group_network", "group_memory"):
            self.assertIn(section, prompt.sections)
        self.assertEqual(prompt.instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(prompt.context_instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(json.dumps(prompt.context_blocks, ensure_ascii=False).count(GROUP_INSTRUCTION), 1)

    def test_group_instruction_not_swallowed_when_group_speaker_authorize_fails(self):
        prompt = self._build_group_prompt(has_person_card=False, speaker_fails=True)
        self.assertNotIn("person_card", prompt.sections)
        self.assertNotIn("group_speaker", prompt.sections)
        for section in ("group_network", "group_memory"):
            self.assertIn(section, prompt.sections)
        self.assertEqual(prompt.instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(prompt.context_instruction.count(GROUP_INSTRUCTION), 1)
        self.assertEqual(json.dumps(prompt.context_blocks, ensure_ascii=False).count(GROUP_INSTRUCTION), 1)


if __name__ == "__main__":
    unittest.main()
