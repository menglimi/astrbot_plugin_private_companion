"""Verification suite for Requirement 15 token-efficiency & background optimizations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_context import (
    LEGACY_MEMORY_INSTRUCTION,
    PRIVATE_GROUP_CONTEXT_INSTRUCTION,
    _MaterialSection,
)
from astrbot_host.core_reply_perception import (
    _GROUP_STATIC_CONTRACT,
    _PRIVATE_STATIC_CONTRACT,
    inject_core_reply_perception_context,
    inject_core_reply_perception_policy,
)
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.agenda.calendar_prompt import CONTINUOUS_SCHEDULE_RULES, ORCHESTRATED_SCHEDULE_RULES
from companion_v2.conversation.foreground import PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.participation_prompts import STATIC_GROUP_PARTICIPATION_RULES
from companion_v2.conversation.private_reply_context import INSTRUCTION as PRIVATE_REPLY_CONTEXT_INSTRUCTION
from companion_v2.conversation.style_context import EXPRESSION_STYLE_INSTRUCTION
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import (
    Lane,
    TERMINAL,
    digest,
    encode,
    stamp,
)
from companion_v2.expression.configuration import DEFAULTS as EXPRESSION_DEFAULTS
from companion_v2.expression.review import (
    CONTACT_KIND as REVIEW_CONTACT_KIND,
    KIND as REVIEW_KIND,
    expression_review_profile,
)
from companion_v2.expression.sharing import build_pack
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import (
    EXISTING_PROPOSAL_SUMMARY_CHARS,
    RULE_READ_LIMIT,
    _compact_existing_rule,
)
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.cache_diagnostics import signature_for_payload
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.proactive.configuration import GROUP as CONTACT_GROUP, configuration_fields as contact_fields
from companion_v2.proactive.preferences import ContactPreferences
from companion_v2.proactive.prompts import ORIGIN_INSTRUCTIONS, PURPOSE_INSTRUCTIONS, request_for as proactive_request_for
from companion_v2.proactive.service import persona_judge_profile, plan_profile
from companion_v2.proactive.staged_prompts import plan_request_for, write_request_for
from companion_v2.role_work.state.maintenance import (
    IDLE_REFRESH_MAX_MINUTES,
    _calendar_transition_crossed,
    _local_day_bucket,
)
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_management_access import ROLE, SCOPE
from tests.test_role_state import NOW as STATE_NOW, StateFixture, state_data
from tests.test_role_tool_loop import answered


NOW = datetime(2026, 9, 27, 4, 0, tzinfo=timezone.utc)
PREF_VALUES = {
    **{field.field_id.removeprefix(CONTACT_GROUP + "."): field.default for field in contact_fields()},
    "note": "",
    "style_prompt": "自然简洁",
    "dedup_enabled": True,
}


def _extract_context_tail(request) -> str:
    parts = getattr(request, "extra_user_content_parts", ())
    for part in parts:
        text = getattr(part, "text", "")
        if "<companion_v2_context>" in text:
            return text
    return ""


class StaticPrefixAndTailSlimmingTests(unittest.IsolatedAsyncioTestCase):
    def _private_prepared(self, turn_index: int):
        scope = NS(kind=ScopeKind.PRIVATE, native_scope_id="user-1", session_id="priv-1")
        ctx = NS(scope=scope, key="priv:key")
        blocks = (
            {
                "title": "私聊生活状态",
                "instruction": ORCHESTRATED_SCHEDULE_RULES,
                "materials": [{"kind": "calendar_snapshot", "content": {"today": f"安排-{turn_index}"}}],
            },
            {
                "title": "历史记忆补充",
                "instruction": LEGACY_MEMORY_INSTRUCTION,
                "materials": [{"kind": "legacy_memory", "content": {"summary": f"记忆-{turn_index}"}}],
            },
        )
        return {
            "context": ctx,
            "turn_id": f"priv-turn-{turn_index}",
            "native_input": NS(text=f"继续 {turn_index} [表情:微笑]"),
            "prompt_instruction": PRIVATE_REPLY_CONTEXT_INSTRUCTION,
            "prompt_context_instruction": PRIVATE_REPLY_CONTEXT_INSTRUCTION,
            "prompt_context_blocks": blocks,
            "perception_instructions": (
                EXPRESSION_STYLE_INSTRUCTION,
                f"本轮风格参考：轻快简洁（轮次 {turn_index}）",
            ),
        }

    def _group_prepared(self, turn_index: int, speaker: str):
        scope = NS(kind=ScopeKind.GROUP, native_scope_id="group-1", session_id="grp-1")
        ctx = NS(scope=scope, key="grp:key")
        verbose_participation = (
            "这是一段很长的群聊参与固定规则说明，每轮都会重复注入到尾部未缓存上下文中，"
            "详细规定了直呼、旁听、连续对话冷却、空气护栏、礼貌收尾、引用回复与不抢话边界。" * 10
        )
        blocks = (
            {
                "title": "群聊参与参考",
                "instruction": verbose_participation,
                "instruction_only_render": True,
                "materials": [{
                    "kind": "group_participation_facts",
                    "content": {
                        "mode": "reply",
                        "reason": "direct_mention",
                        "speaker": speaker,
                        "turn": turn_index,
                    },
                }],
            },
            {
                "title": "群聊生活状态",
                "instruction": CONTINUOUS_SCHEDULE_RULES,
                "materials": [{"kind": "calendar_snapshot", "content": {"status": f"状态-{turn_index}"}}],
            },
            {
                "title": "发言者私聊背景参考",
                "instruction": PRIVATE_GROUP_CONTEXT_INSTRUCTION,
                "materials": [{"kind": "private_digest", "content": {"summary": f"私聊摘记-{turn_index}"}}],
            },
        )
        return {
            "context": ctx,
            "turn_id": f"grp-turn-{turn_index}",
            "native_input": NS(text=f"{speaker}: 大家好 {idx_label}" if (idx_label := str(turn_index)) else ""),
            "prompt_instruction": STATIC_GROUP_PARTICIPATION_RULES,
            "prompt_context_instruction": STATIC_GROUP_PARTICIPATION_RULES,
            "prompt_context_blocks": blocks,
            "group_admission": {"reason": "mentioned", "mentioned": True},
            "group_trigger": {"mentioned": True, "speaker_name": speaker},
            "perception_instructions": (
                EXPRESSION_STYLE_INSTRUCTION,
                "群聊语气自然收住",
            ),
        }

    async def test_private_system_prefix_is_byte_stable_and_tail_drops_hoisted_rules(self):
        app = NS(messages=None)
        system_hashes = []
        tail_lengths = []
        for idx in range(1, 4):
            prepared = self._private_prepared(idx)
            state = {"prepared": prepared}
            event = NS(
                get_extra=lambda key, s=state: s if key == CORE_REPLY_CONTEXT_EXTRA else None,
            )
            req = NS(
                prompt=f"继续 {idx}",
                system_prompt="固定角色人格设定。",
                extra_user_content_parts=[],
                contexts=[],
            )
            inject_core_reply_perception_policy(event, req)
            await inject_core_reply_perception_context(event, req, application=app)
            sig = signature_for_payload(
                "test-model",
                [{"role": "system", "content": req.system_prompt}, {"role": "user", "content": req.prompt}],
                [],
            )
            system_hashes.append(sig.system_hash)
            tail = _extract_context_tail(req)
            tail_lengths.append(len(tail))
            # Hoisted static rules live in cacheable system_prompt, NOT repeated in uncached tail.
            self.assertIn(_PRIVATE_STATIC_CONTRACT, req.system_prompt)
            self.assertIn(PRIVATE_REPLY_CONTEXT_INSTRUCTION, req.system_prompt)
            self.assertNotIn(PRIVATE_REPLY_CONTEXT_INSTRUCTION, tail)
            self.assertNotIn(ORCHESTRATED_SCHEDULE_RULES, tail)
            self.assertNotIn(LEGACY_MEMORY_INSTRUCTION, tail)
            self.assertNotIn(EXPRESSION_STYLE_INSTRUCTION, tail)
            # Dynamic facts remain intact in tail.
            self.assertIn(f"安排-{idx}", tail)
            self.assertIn(f"记忆-{idx}", tail)

        self.assertEqual(len(set(system_hashes)), 1, "system_hash must remain byte-identical across private turns")
        unhoisted_rules_chars = sum(len(rule) for rule in (
            PRIVATE_REPLY_CONTEXT_INSTRUCTION,
            ORCHESTRATED_SCHEDULE_RULES,
            LEGACY_MEMORY_INSTRUCTION,
            EXPRESSION_STYLE_INSTRUCTION,
        ))
        self.assertGreater(unhoisted_rules_chars, 1200)
        self.assertLess(max(tail_lengths), unhoisted_rules_chars, "slimmed dynamic tail is smaller than hoisted static prose alone")

    async def test_group_system_prefix_is_byte_stable_across_speakers_and_tail_stays_compact(self):
        app = NS(messages=None)
        system_hashes = []
        for idx, speaker in enumerate(("Alice", "Bob", "Carol"), 1):
            prepared = self._group_prepared(idx, speaker)
            state = {"prepared": prepared}
            event = NS(
                get_extra=lambda key, s=state: s if key == CORE_REPLY_CONTEXT_EXTRA else None,
            )
            req = NS(
                prompt=f"{speaker}: 大家好 {idx}",
                system_prompt="固定群聊角色人格设定。",
                extra_user_content_parts=[],
                contexts=[],
            )
            inject_core_reply_perception_policy(event, req)
            await inject_core_reply_perception_context(event, req, application=app)
            sig = signature_for_payload(
                "test-model",
                [{"role": "system", "content": req.system_prompt}, {"role": "user", "content": req.prompt}],
                [],
            )
            system_hashes.append(sig.system_hash)
            tail = _extract_context_tail(req)
            self.assertIn(_GROUP_STATIC_CONTRACT, req.system_prompt)
            self.assertIn(STATIC_GROUP_PARTICIPATION_RULES, req.system_prompt)
            self.assertNotIn("这是一段很长的群聊参与固定规则说明", tail)
            self.assertNotIn(CONTINUOUS_SCHEDULE_RULES, tail)
            self.assertNotIn(PRIVATE_GROUP_CONTEXT_INSTRUCTION, tail)
            self.assertNotIn(EXPRESSION_STYLE_INSTRUCTION, tail)
            self.assertIn(speaker, tail)
            self.assertIn(f"私聊摘记-{idx}", tail)
            self.assertIn("group_participation_facts", tail)
        self.assertEqual(len(set(system_hashes)), 1, "system_hash must stay invariant across group speakers")


class ProactivePrefixStabilityTests(unittest.TestCase):
    def _job(self, *, purpose="chat", origin=None, admin_test=False):
        thought = NS(summary="想起一个小念头", opportunity=NS(opportunity_id="opp-1", opened_at=NOW))
        principal = NS(name="p", installation_id=SCOPE.installation_id, platform=SCOPE.platform,
                       bot_id=SCOPE.bot_id, account_id=SCOPE.account_id)
        return NS(
            intent=NS(purpose=purpose, thought=thought),
            context=NS(actor=NS(principal=principal), scope=SCOPE, role_binding=NS(scope=SCOPE, role=ROLE)),
            role_instruction="固定角色人格",
            automatic_origin=origin,
            diagnostic_origin=None,
            interaction=None,
            administrator_test=admin_test,
            diagnostic_source_backed=False,
            diagnostic_image_intent=False,
            voice_policy=None,
            plan={"decision": "send", "decision_confidence": 0.9, "angle": "聊天气", "facts": [], "avoid": []},
        )

    def _wire_system_hash(self, request):
        envelope = NS(
            original=request,
            tools=(),
            tool_history=(),
            max_output_tokens=200,
            task_prompt="保持原人格，按场景决定是否主动联系。",
            record_prompt_snapshot=None,
        )
        wire = build_wire(envelope, model_name="test-model")
        system_msg = wire.messages[0]["content"]
        user_msg = wire.messages[1]["content"]
        sig = signature_for_payload("test-model", wire.messages, [])
        return sig.system_hash, system_msg, user_msg

    def test_proactive_compose_system_hash_is_invariant_across_purposes_origins_and_toggles(self):
        preferences = ContactPreferences(None, PREF_VALUES, NOW)
        cases = [
            ("chat", None, False, False, False, False),
            ("news", {"news_event": {"event_id": "n1"}}, False, False, False, False),
            ("greeting", {"greeting_event": {"kind": "day_anchor"}}, True, False, True, False),
            ("care", {"relationship_event": {"purpose": "care"}}, False, True, False, False),
            ("play", None, True, True, True, True),
        ]
        hashes = set()
        for purpose, origin, reactions, poke, dialogue, admin in cases:
            job = self._job(purpose=purpose, origin=origin, admin_test=admin)
            req = proactive_request_for(
                job,
                (),
                preferences,
                reactions_available=reactions,
                poke_available=poke,
                dialogue=({"role": "user", "text": "在吗", "received_at": stamp(NOW)},) if dialogue else (),
            )
            system_hash, system_msg, user_msg = self._wire_system_hash(req)
            hashes.add(system_hash)
            self.assertNotIn(PURPOSE_INSTRUCTIONS[purpose], system_msg)
            self.assertIn(PURPOSE_INSTRUCTIONS[purpose], user_msg)
            self.assertIn('"round_guidance"', user_msg)
            if origin:
                marker = next(iter(origin))
                if marker in ORIGIN_INSTRUCTIONS:
                    self.assertNotIn(ORIGIN_INSTRUCTIONS[marker], system_msg)
                    self.assertIn(ORIGIN_INSTRUCTIONS[marker], user_msg)
        self.assertEqual(len(hashes), 1, "proactive.compose system_hash must be byte-stable across purposes/origins/toggles")

    def test_two_stage_plan_and_write_system_hashes_are_invariant(self):
        preferences = ContactPreferences(None, PREF_VALUES, NOW)
        plan_hashes, write_hashes = set(), set()
        for purpose, origin in (
            ("chat", None),
            ("news", {"news_event": {"event_id": "a1"}}),
            ("greeting", {"greeting_event": {"kind": "insomnia_night"}}),
        ):
            job = self._job(purpose=purpose, origin=origin)
            base = proactive_request_for(job, (), preferences)
            plan_req = plan_request_for(base, job, ())
            write_req = write_request_for(
                base, job, (), preferences,
                reactions_available=(purpose == "chat"),
                voice_available=(purpose == "greeting"),
                poke_available=(purpose == "news"),
                extra_instruction="固定风格",
                variable_extra_instruction=f"本轮补充-{purpose}",
            )
            p_hash, p_sys, p_usr = self._wire_system_hash(plan_req)
            w_hash, w_sys, w_usr = self._wire_system_hash(write_req)
            plan_hashes.add(p_hash)
            write_hashes.add(w_hash)
            self.assertIn(PURPOSE_INSTRUCTIONS[purpose], p_usr)
            self.assertNotIn(PURPOSE_INSTRUCTIONS[purpose], p_sys)
            self.assertIn(PURPOSE_INSTRUCTIONS[purpose], w_usr)
            self.assertNotIn(PURPOSE_INSTRUCTIONS[purpose], w_sys)
        self.assertEqual(len(plan_hashes), 1)
        self.assertEqual(len(write_hashes), 1)


class ProactiveJudgeBeforeReviewAndLedgerTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (
            plan_profile("primary", max_output_tokens=100),
            persona_judge_profile("primary", max_output_tokens=100),
            expression_review_profile("primary", max_output_tokens=120),
            expression_review_profile("primary", max_output_tokens=120, contact=True),
        )
        self.registry = FieldRegistry(
            version="proactive-order-review.v1",
            fields=configuration_fields(self.profiles),
        )

    def _seed_adopted_expression_rule(self, app, context):
        manager = ExpressionStyleManagement(app)
        rule = {
            "dimension": "sentence_rhythm",
            "observation": "习惯用轻松简短的语气开头",
            "proposal": "开头保持简短自然，避免客套",
            "confidence": 0.9,
            "expression": {
                "kind": "grammar",
                "situation": "日常主动开启新话题时",
                "pattern": "先轻声打招呼再用短句切入",
                "avoid": "冗长客套",
            },
        }
        pack = build_pack([rule], title="自然短句")
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=pack).unwrap()
        manager.import_pack(
            operator=self.core, context=context, raw_pack=pack,
            preview_token=preview["preview_token"], operation_key="order-test-import",
        ).unwrap()
        row = manager.list(operator=self.core, context=context, state="pending").unwrap()["items"][0]
        manager.command(
            operator=self.core, context=context, signal_id=row["signal_id"],
            action="adopt", view_token=row["view_token"], operation_key="order-test-adopt",
        ).unwrap()

    async def test_persona_judge_reject_skips_contact_review_completely(self):
        host, _ = await self.scheduled()
        app = host.application
        self.sent.clear()
        self.settings(app, {"enable_llm_proactive_persona_judge": True}).unwrap()
        self._seed_adopted_expression_rule(app, self.contact_context())

        observed_formats = []

        async def model_fn(envelope):
            fmt = envelope.original.output_format
            observed_formats.append(fmt)
            if any(item.purpose == "proactive_draft" for item in envelope.original.context.materials):
                return answered('{"decision":"drop","score":25,"reason":"不符合当前人设"}')
            if fmt == "role_expression_review":
                raise AssertionError("contact_review must NEVER run when persona_judge rejects the draft")
            return answered('{"decision":"send","text":"这是一条会被人设裁判拦截的草稿。"}')

        self.model_behavior = model_fn
        self.advance(app, 60)
        app.contact_schedule.advance()
        await self.until(lambda: any(
            row["state"] in TERMINAL
            for row in app.runtime.read_all("SELECT state FROM execution_tasks WHERE kind = 'proactive.judge'", ())
        ))
        self.assertNotIn("role_expression_review", observed_formats)
        self.assertEqual(self.sent, [])

    async def test_persona_judge_allow_runs_contact_review_after_judge_and_keeps_ledger_generated(self):
        host, _ = await self.scheduled()
        app = host.application
        self.sent.clear()
        self.settings(app, {"enable_llm_proactive_persona_judge": True}).unwrap()
        self._seed_adopted_expression_rule(app, self.contact_context())

        call_sequence = []

        async def model_fn(envelope):
            fmt = envelope.original.output_format
            if any(item.purpose == "proactive_draft" for item in envelope.original.context.materials):
                call_sequence.append("judge")
                return answered('{"decision":"send","score":92,"reason":"贴合人设"}')
            if fmt == "role_expression_review":
                call_sequence.append("review")
                return answered('{"decision":"rewrite","text":"润色后的自然主动消息。"}')
            call_sequence.append("compose")
            return answered('{"decision":"send","text":"原始主动草稿消息。"}')

        self.model_behavior = model_fn
        self.advance(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)

        self.assertEqual(call_sequence, ["compose", "judge", "review"])
        self.assertEqual([msg.text for msg in self.sent], ["润色后的自然主动消息。"])

        # Verify expression/review.py ledger accounting bug fix: successful review stays 'generated' (NOT 'discarded')!
        review_calls = app.runtime.read_all(
            """SELECT c.state FROM model_calls c
               JOIN execution_tasks t ON t.task_id = c.task_id
               WHERE t.kind = ?""",
            (REVIEW_CONTACT_KIND,),
        )
        self.assertEqual(len(review_calls), 1)
        self.assertEqual(review_calls[0]["state"], "generated")

    async def test_delayed_contact_review_refreshes_sealed_chinese_voice_speech(self):
        host, _ = await self.scheduled()
        app = host.application
        self.sent.clear()
        self.settings(app, {
            "enable_llm_proactive_persona_judge": True,
            "voice_action_enabled": True,
            "voice_action_cooldown_minutes": 0,
        }).unwrap()
        self._seed_adopted_expression_rule(app, self.contact_context())

        async def model_fn(envelope):
            fmt = envelope.original.output_format
            if any(item.purpose == "proactive_draft" for item in envelope.original.context.materials):
                return answered('{"decision":"send","score":95,"reason":"贴合人设"}')
            if fmt == "role_expression_review":
                return answered('{"decision":"rewrite","text":"润色后的中文朗读正文。"}')
            return answered('{"decision":"send","text":"未润色的旧草稿正文。","voice_intent":true}')

        self.model_behavior = model_fn
        self.advance(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)

        self.assertEqual([msg.text for msg in self.sent], ["润色后的中文朗读正文。"])
        self.assertIsNotNone(self.sent[0].voice)
        self.assertEqual(self.sent[0].voice.speech, "润色后的中文朗读正文。")
        saved = json.loads(self.contacts(app)[-1]["record_json"])
        self.assertEqual(saved["voice"]["speech"], "润色后的中文朗读正文。")


class ExpressionLearningSlimmingTests(unittest.TestCase):
    def test_defaults_and_compact_rule_shape(self):
        self.assertEqual(RULE_READ_LIMIT, 30)
        self.assertEqual(EXPRESSION_DEFAULTS["sample_limit"], 25)
        self.assertEqual(EXPRESSION_DEFAULTS["sample_chars"], 300)
        self.assertEqual(EXPRESSION_DEFAULTS["scan_interval_minutes"], 360)

        long_obs = "很长的历史观察描述。" * 80
        long_prop = "简洁表达建议。" * 40
        row = {
            "signal_id": "sig-1",
            "dimension": "rhythm",
            "observation": long_obs,
            "proposal": long_prop,
            "state": "adopted",
        }
        with patch("companion_v2.expression.style_service.rule_details.form_for", return_value={
            "situation": "睡前简短道别" * 20,
            "form": "早点睡呀",
            "usage": "不要堆砌长句",
            "kind": "closing",
            "applicability": {"huge_blob": "x" * 500},
        }):
            compact = _compact_existing_rule(None, row)
        self.assertNotIn("observation", compact)
        self.assertNotIn("applicability", compact)
        self.assertLessEqual(len(compact["proposal"]), EXISTING_PROPOSAL_SUMMARY_CHARS)
        self.assertNotIn("applicability", compact.get("expression", {}))
        legacy_bytes = len(json.dumps({**row, "applicability": {"huge_blob": "x" * 500}}, ensure_ascii=False).encode("utf-8"))
        compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
        self.assertLess(compact_bytes, legacy_bytes * 0.35)


class StateMaintenanceIdleSkipGateTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self):
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        app.state_maintenance.bind(context, "原角色人格提示")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", ((STATE_NOW + timedelta(days=1)).isoformat(),))
        await self.start(host)
        return host, app, context, manager

    def test_idle_helpers_detect_bucket_and_calendar_transitions(self):
        t1 = datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc)   # 10:00 +08 morning
        t2 = datetime(2026, 9, 27, 2, 30, tzinfo=timezone.utc)  # 10:30 +08 morning
        t3 = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)   # 14:00 +08 afternoon
        self.assertEqual(_local_day_bucket(t1, "Asia/Shanghai"), _local_day_bucket(t2, "Asia/Shanghai"))
        self.assertNotEqual(_local_day_bucket(t2, "Asia/Shanghai"), _local_day_bucket(t3, "Asia/Shanghai"))

        snapshot = {"items": [{"title": "午休", "start": "2026-09-27T02:15:00+00:00", "end": "2026-09-27T03:00:00+00:00"}]}
        self.assertTrue(_calendar_transition_crossed(snapshot, since=t1, now=t2))
        self.assertFalse(_calendar_transition_crossed(snapshot, since=t1, now=datetime(2026, 9, 27, 2, 10, tzinfo=timezone.utc)))

    async def test_idle_skip_gate_skips_when_unchanged_and_runs_on_new_dialogue_or_ceiling(self):
        host, app, context, _ = await self.ready()

        async def _model(env):
            if env.original.output_format == "role_state":
                return answered(encode(state_data(hunger="不饿", energy=80)))
            return answered("好呀，刚看到你的消息。")

        self.model_behavior = _model

        def state_calls():
            return sum(1 for item in self.called if item.original.output_format == "role_state")

        # 1. Initial maintenance commits state and saves idle fingerprint.
        first = await app.state_maintenance.run(context, "原角色人格提示", "initial-run")
        self.assertEqual(first["state"], "succeeded", first)
        self.assertEqual(state_calls(), 1)

        # 2. Advance 30 min with zero changes -> advance() skips LLM call cleanly.
        self.clock.advance(timedelta(minutes=30))
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (stamp(self.clock.now()),))
        app.state_maintenance.advance()
        self.assertEqual(state_calls(), 1, "idle unchanged cycle must skip LLM call")
        row = self.runtime.read_one("SELECT result_json, next_at FROM role_state_schedules", ())
        saved_result = json.loads(row["result_json"])
        self.assertEqual(saved_result["state"], "skipped")
        self.assertEqual(saved_result["reason"], "idle_no_change")
        self.assertGreater(row["next_at"], stamp(self.clock.now()))

        # 3. New dialogue arrives -> idle skip gate opens and dispatches LLM maintenance!
        from companion_v2.conversation.native_observations import NativeConversationObservations
        self.clock.advance(timedelta(minutes=30))
        NativeConversationObservations(app.messages).capture_together(
            context, observation_id="new-dialogue-trigger", event_id="new-dialogue-trigger",
            text="刚刚回来啦", at=self.clock.now(), kind="input",
        )
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (stamp(self.clock.now()),))
        app.state_maintenance.advance()
        await self.until(lambda: state_calls() == 2)
        self.assertEqual(state_calls(), 2)

        # 4. Advance >= IDLE_REFRESH_MAX_MINUTES (240 min) with no dialogue -> periodic refresh runs!
        self.clock.advance(timedelta(minutes=IDLE_REFRESH_MAX_MINUTES + 1))
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", (stamp(self.clock.now()),))
        app.state_maintenance.advance()
        await self.until(lambda: state_calls() == 3)
        self.assertEqual(state_calls(), 3)

    async def test_idle_skip_gate_detects_calendar_occurrence_that_ended_since_last_settlement(self):
        host, app, context, _ = await self.ready()

        async def _model(env):
            if env.original.output_format == "role_state":
                return answered(encode(state_data(hunger="不饿", energy=80)))
            return answered("好呀。")

        self.model_behavior = _model

        def state_calls():
            return sum(1 for item in self.called if item.original.output_format == "role_state")

        # Schedule a 10-minute occurrence starting 5 min after initial settlement and ending 15 min after.
        settled_at = self.clock.now()
        occ_start = settled_at + timedelta(minutes=5)
        occ_end = settled_at + timedelta(minutes=15)

        def fake_occurrences(_ctx, *, start, end, **_kwargs):
            items = []
            if occ_start < end and occ_end > start:
                items.append({
                    "id": "cal-short-1",
                    "title": "短会",
                    "start": stamp(occ_start),
                    "end": stamp(occ_end),
                    "state": "active",
                })
            return {"items": items}

        with patch.object(app.calendar, "occurrences", side_effect=fake_occurrences):
            first = await app.state_maintenance.run(context, "原角色人格提示", "cal-initial")
            self.assertEqual(first["state"], "succeeded", first)
            self.assertEqual(state_calls(), 1)

            # Advance 30 minutes: the occurrence BOTH started and ended strictly inside (settled_at, now].
            # Querying from settled_at must detect the crossed transition and trigger maintenance!
            self.clock.advance(timedelta(minutes=30))
            with self.runtime.transaction() as connection:
                connection.execute("UPDATE role_state_schedules SET next_at = ?", (stamp(self.clock.now()),))
            app.state_maintenance.advance()
            await self.until(lambda: state_calls() == 2)
            self.assertEqual(state_calls(), 2)


if __name__ == "__main__":
    unittest.main()
