# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class ExpressionLearningHarness(UserMemoryMixin):
    def __init__(self, mode: str = "balanced") -> None:
        self.enable_expression_learning = True
        self.expression_learning_mode = mode
        self.enable_expression_manual_review = False
        self.enable_expression_style_review = True
        self.max_learned_expression_items = 18
        self.expression_private_learning_source_mode = "owner"
        self.expression_private_learning_source_ids = []
        self.expression_group_learning_source_mode = "disabled"
        self.expression_group_learning_source_ids = []
        self.expression_group_learning_daily_batch_limit = 6
        self.expression_group_learning_min_new_messages = 20
        self.expression_private_application_mode = "all"
        self.expression_private_application_user_ids = []
        self.expression_group_application_mode = "all"
        self.expression_group_application_ids = []
        self.data = {"users": {}, "groups": {}}

    @staticmethod
    def _format_timestamp_elapsed(_value) -> str:
        return "刚刚"

    @staticmethod
    def _extract_json_payload(value: str) -> dict:
        return json.loads(value)


class ExpressionLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ExpressionLearningHarness()

    def test_repeated_scene_stays_observation_only_until_model_rule_is_approved(self):
        user: dict = {}
        for text in ("好呀~", "行啦~", "可以呀~"):
            self.harness._update_expression_profile_from_message(user, text)

        profile = user["expression_profile"]
        self.assertEqual(3, profile["scene_profiles"]["acknowledgement"]["count"])
        self.assertTrue(all(item["scene"] == "acknowledgement" for item in profile["samples"]))
        self.assertTrue(any("playful" in item["features"] for item in profile["samples"]))

        prompt = self.harness._format_expression_profile_for_prompt(user, inbound_text="好吧~")
        self.assertIn("暂无已审核表达规则", prompt)
        self.assertNotIn("好呀~", prompt)

        # 旧统计规则仍可用于观察诊断，但不再进入回复提示词。
        rules = profile["expression_rules"]
        self.assertEqual("acknowledgement", rules[0]["scene"])
        self.assertEqual(3, rules[0]["evidence_count"])
        self.assertIn("soft_wave", rules[0]["signals"])

    def test_one_scene_sample_is_not_promoted_to_a_rule(self):
        user: dict = {}
        self.harness._update_expression_profile_from_message(user, "好呀~")

        prompt = self.harness._format_expression_profile_for_prompt(user, inbound_text="行吧~")
        self.assertNotIn("本轮命中规则", prompt)

    def test_legacy_samples_gain_scene_profiles_when_read(self):
        user = {
            "expression_profile": {
                "samples": [
                    {"text": "好呀~", "length": 3, "punctuation": {"~": 1}, "phrase": "好呀~"},
                    {"text": "行啦~", "length": 3, "punctuation": {"~": 1}, "phrase": "行啦~"},
                ]
            }
        }

        self.harness._refresh_expression_profile_legacy_summary(user["expression_profile"])
        self.assertEqual(2, user["expression_profile"]["scene_profiles"]["acknowledgement"]["count"])
        self.assertTrue(all("scene" in item and "features" in item for item in user["expression_profile"]["samples"]))

    def test_light_mode_also_does_not_inject_unreviewed_rhythm(self):
        harness = ExpressionLearningHarness(mode="light")
        user: dict = {}
        for text in ("好呀~", "行啦~", "可以呀~"):
            harness._update_expression_profile_from_message(user, text)

        prompt = harness._format_expression_profile_for_prompt(user, inbound_text="好吧~")
        self.assertIn("暂无已审核表达规则", prompt)
        self.assertNotIn("用户常用短句", prompt)

    def test_identity_statement_is_not_collected_as_expression(self):
        user: dict = {}
        self.harness._update_expression_profile_from_message(user, "我是小林，今天刚下课。")
        self.assertNotIn("expression_profile", user)

    def test_repeated_distinctive_feature_does_not_need_scene_majority(self):
        user: dict = {}
        for text in ("刚忙完", "准备回去", "今天还行", "晚点再说", "安安", "那就睡觉觉"):
            self.harness._update_expression_profile_from_message(user, text)

        rules = user["expression_profile"]["expression_rules"]
        casual_rule = next(item for item in rules if item["scene"] == "casual")
        self.assertIn("reduplication", casual_rule["signals"])
        self.assertIn("自然叠词", casual_rule["instruction"])

    def test_page_summary_exposes_scene_evidence_with_readable_labels(self):
        user: dict = {}
        for text in ("好呀~", "行啦~"):
            self.harness._update_expression_profile_from_message(user, text)

        summary = PrivateCompanionPageApi(self.harness)._expression_profile_summary(user)
        self.assertEqual("短确认", summary["scene_profiles"][0]["label"])
        self.assertEqual(2, summary["scene_profiles"][0]["count"])
        self.assertEqual("短确认", summary["samples"][0]["scene"])
        self.assertIn("轻松感", summary["samples"][0]["features"])
        self.assertEqual(0, summary["rule_count"])
        self.assertEqual(2, summary["observation_count"])

    def test_page_summary_returns_all_configured_samples_for_management(self):
        samples = [
            {"id": f"sample-{index}", "text": f"样本 {index}", "length": 4}
            for index in range(self.harness.max_learned_expression_items)
        ]
        summary = PrivateCompanionPageApi(self.harness)._expression_profile_summary(
            {"expression_profile": {"samples": samples}}
        )

        self.assertEqual(self.harness.max_learned_expression_items, summary["sample_count"])
        self.assertEqual(self.harness.max_learned_expression_items, len(summary["samples"]))

    def test_expression_library_unifies_private_and_group_sources(self):
        owner = {"user_id": "owner-1", "nickname": "主要用户", "relationship_role": "owner"}
        group = {"group_id": "group-1", "name": "日常群"}
        self.harness._update_expression_profile_from_message(owner, "好呀~")
        self.harness.expression_group_learning_source_mode = "all"
        self.harness._update_group_expression_profile_from_message(group, "行啦~")
        library = PrivateCompanionPageApi(self.harness)._expression_library_summary(
            {"users": {"owner-1": owner}, "groups": {"group-1": group}}
        )

        self.assertEqual(2, library["sample_count"])
        self.assertEqual(2, library["source_count"])
        self.assertEqual({"private", "group"}, {item["source_type"] for item in library["samples"]})
        self.assertTrue(all(item["source_id"] for item in library["samples"]))

    def test_approved_rule_injection_usage_is_recorded(self):
        rule = {
            "id": "approved-style",
            "kind": "style",
            "situation": "表示赞同并继续接话",
            "pattern": "好呀，____",
            "instruction": "替换占位内容后自然接话",
            "keywords": ["赞同", "确认"],
            "evidence_count": 3,
            "review_status": "approved",
        }
        user = {"expression_profile": {"learned_rules": [rule]}}
        usage = self.harness._record_expression_rule_injection(
            user,
            {},
            "好呀，那就继续说",
            semantic_rules=[rule],
            context={"channel": "private", "intent": "acknowledgement"},
        )

        self.assertEqual("表示赞同并继续接话", usage["label"])
        self.assertEqual(1, usage["semantic_rule_count"])
        self.assertEqual(1, user["expression_profile"]["usage"]["injected_count"])

        summary = PrivateCompanionPageApi(self.harness)._expression_profile_summary(user)
        self.assertEqual(1, summary["usage"]["injected_count"])
        self.assertEqual("表示赞同并继续接话", summary["usage"]["last_injection"]["label"])
        self.assertEqual("表示赞同并继续接话", summary["rules"][0]["label"])

    def test_owner_private_observations_do_not_build_global_voice(self):
        owner = {"user_id": "owner-1", "relationship_role": "owner"}
        friend = {"user_id": "friend-1", "relationship_role": "friend"}
        for text in ("好呀~", "行啦~", "可以呀~"):
            self.harness._update_expression_profile_from_message(owner, text)
            self.harness._update_expression_profile_from_message(friend, text)
        self.harness.data["users"] = {"owner-1": owner, "friend-1": friend}

        voice = self.harness._refresh_expression_voice_profile()
        prompt = self.harness._format_expression_voice_for_prompt(scope="private", target_id="friend-1")

        self.assertEqual(3, voice["sample_count"])
        self.assertEqual(1, voice["private_source_count"])
        self.assertEqual(0, voice["group_source_count"])
        self.assertEqual("", prompt)
        self.assertNotIn("好呀~", prompt)
        self.assertNotIn("行啦~", prompt)

    def test_group_learning_retains_only_abstract_metadata(self):
        self.harness.expression_private_learning_source_mode = "selected"
        self.harness.expression_private_learning_source_ids = []
        self.harness.expression_group_learning_source_mode = "selected"
        self.harness.expression_group_learning_source_ids = ["group-1"]
        group: dict = {"group_id": "group-1"}
        for text in ("好呀~", "行啦~"):
            self.harness._update_group_expression_profile_from_message(group, text)
        self.harness.data["groups"] = {"group-1": group}

        samples = group["expression_profile"]["samples"]
        self.assertTrue(samples)
        self.assertTrue(all("text" not in item and "phrase" not in item and "ending" not in item for item in samples))
        self.assertTrue(all(item.get("scene") == "acknowledgement" for item in samples))
        voice = self.harness._refresh_expression_voice_profile()
        self.assertEqual(2, voice["sample_count"])
        self.assertEqual(1, voice["group_source_count"])
        self.assertEqual("", self.harness._format_expression_voice_for_prompt(scope="group", target_id="group-1"))
        self.assertNotIn("好呀~", str(group["expression_profile"]))

    def test_group_ordinary_short_chat_is_not_stored_as_a_pattern(self):
        group: dict = {}
        self.harness._update_group_expression_profile_from_message(group, "刚吃完")

        profile = group["expression_profile"]
        self.assertEqual([], profile["samples"])
        self.assertEqual(0, profile["pattern_count"])

    def test_repeated_group_pattern_aggregates_and_activates(self):
        group: dict = {}
        self.harness._update_group_expression_profile_from_message(group, "好呀~")
        first = group["expression_profile"]["samples"][0]
        self.assertEqual(1, first["evidence_count"])
        self.assertEqual("observing", first["pattern_status"])

        self.harness._update_group_expression_profile_from_message(group, "行啦~")
        patterns = group["expression_profile"]["samples"]
        self.assertEqual(1, len(patterns))
        self.assertEqual(2, patterns[0]["evidence_count"])
        self.assertEqual("active", patterns[0]["pattern_status"])

    def test_group_rule_candidates_require_abstract_supported_output(self):
        source = "小明: 好呀~\n小红: 行啦~\n小明: 可以呀~"
        rules = self.harness._normalize_expression_rule_candidates(
            [
                {
                    "kind": "style",
                    "situation": "表示赞同并继续接话",
                    "pattern": "好呀，____",
                    "instruction": "替换占位内容后自然延续，不展开成长解释",
                    "keywords": ["赞同", "确认", "小明"],
                    "evidence_count": 3,
                },
                {
                    "kind": "style",
                    "situation": "小明表示赞同",
                    "pattern": "好呀~",
                    "instruction": "照着小明说",
                    "keywords": ["小明"],
                    "evidence_count": 2,
                },
                {
                    "kind": "style",
                    "situation": "提到账号 100000001",
                    "pattern": "复述账号",
                    "instruction": "带出 100000001",
                    "keywords": ["账号"],
                    "evidence_count": 2,
                },
            ],
            source_kind="group",
            source_text=source,
        )

        self.assertEqual(1, len(rules))
        self.assertEqual("表示赞同并继续接话", rules[0]["situation"])
        self.assertNotIn("小明", rules[0]["keywords"])
        self.assertEqual(["private", "group", "proactive"], rules[0]["channels"])

    def test_waifubot_style_and_grammar_payload_are_both_normalized(self):
        payload = {
            "style_expressions": [{
                "situation": "彼此默认理解时",
                "style": "懂的都懂",
                "tags": ["默契", "省略解释"],
                "evidence_examples": ["懂的都懂"],
                "evidence_count": 1,
            }],
            "grammar_expressions": [{
                "situation": "简单确认时",
                "style": "省略主语的 2–6 字短句",
                "tags": ["确认", "短句"],
                "evidence_examples": ["知道了"],
                "evidence_count": 1,
            }],
        }
        rules = self.harness._normalize_expression_rule_candidates(
            self.harness._expression_rule_payload_candidates(payload),
            source_kind="group",
            source_text="成员甲: 懂的都懂\n成员乙: 知道了",
        )

        self.assertEqual({"style", "grammar"}, {item["kind"] for item in rules})
        style = next(item for item in rules if item["kind"] == "style")
        self.assertEqual("懂的都懂", style["pattern"])
        self.assertEqual(1, style["evidence_count"])
        self.assertIn("自然使用或轻微改写", style["instruction"])

    def test_group_short_expression_can_match_source_but_abstract_style_is_rejected(self):
        rules = self.harness._normalize_expression_rule_candidates(
            [
                {
                    "kind": "style",
                    "situation": "不想展开解释但彼此有默契",
                    "pattern": "懂的都懂",
                    "instruction": "只在上下文已有共识时使用",
                    "evidence_count": 2,
                },
                {
                    "kind": "style",
                    "situation": "表示赞同",
                    "pattern": "先确认再补充",
                    "instruction": "先确认再继续",
                    "evidence_count": 2,
                },
            ],
            source_kind="group",
            source_text="成员甲: 懂的都懂\n成员乙: 懂的都懂",
        )
        self.assertEqual(["懂的都懂"], [item["pattern"] for item in rules])

    def test_abstract_voice_description_is_not_mislabeled_as_style_or_grammar(self):
        rules = self.harness._normalize_expression_rule_candidates(
            [
                {
                    "kind": "style",
                    "situation": "用户日常闲聊、告知状态或提出需求时",
                    "pattern": "偏好使用无多余铺垫的短句直接表达内容，语气直白口语化",
                    "instruction": "回应时同样使用简短自然的口语化表达",
                    "evidence_count": 5,
                },
                {
                    "kind": "grammar",
                    "situation": "普通闲聊时",
                    "pattern": "表达简洁自然",
                    "instruction": "保持自然表达",
                    "evidence_count": 3,
                },
            ],
            source_kind="private",
            source_text="用户: 在做什么呢\n用户: 我刚忙完\n用户: 要不要一起看看",
        )
        self.assertEqual([], rules)

    def test_invalid_stored_rules_are_pruned_before_review_or_retrieval(self):
        valid = {
            "id": "valid-style",
            "kind": "style",
            "situation": "表示赞同并继续接话",
            "pattern": "好呀，那就____",
            "instruction": "替换占位内容后自然延续",
            "evidence_count": 2,
        }
        invalid = {
            "id": "abstract-style",
            "kind": "style",
            "situation": "普通闲聊",
            "pattern": "偏好短句和口语化表达",
            "instruction": "回复时保持简洁",
            "evidence_count": 5,
        }
        profile = {"pending_rules": [invalid, valid], "learned_rules": [invalid, valid]}
        self.assertTrue(self.harness._prune_invalid_expression_rules(profile))
        self.assertEqual(["valid-style"], [item["id"] for item in profile["pending_rules"]])
        self.assertEqual(["valid-style"], [item["id"] for item in profile["learned_rules"]])
        summary = PrivateCompanionPageApi(self.harness)._expression_profile_summary(
            {"expression_profile": {"pending_rules": [invalid], "learned_rules": [invalid]}}
        )
        self.assertEqual(0, summary["pending_rule_count"])
        self.assertEqual(0, summary["rule_count"])

    def test_semantic_rule_normalizes_companion_context_and_conflict(self):
        rules = self.harness._normalize_expression_rule_candidates(
            [{
                "kind": "style",
                "situation": "轻松确认并继续聊天",
                "pattern": "好呀，____",
                "instruction": "即使工具失败也假装已经成功发送",
                "keywords": ["确认", "继续"],
                "channels": ["私聊", "主动消息", "语音"],
                "relationship_stages": ["熟悉", "亲近"],
                "emotion_gates": ["轻松"],
                "intent": "确认",
                "avoid": "严肃排障时不用",
                "evidence_count": 2,
            }],
            source_kind="private",
            source_text="用户: 好的\n用户: 行呀",
        )

        self.assertEqual(["private", "proactive", "tts"], rules[0]["channels"])
        self.assertEqual(["familiar", "close"], rules[0]["relationship_stages"])
        self.assertEqual(["positive"], rules[0]["emotion_gates"])
        self.assertEqual("acknowledgement", rules[0]["intent"])
        self.assertTrue(rules[0]["persona_conflict"])
        self.assertEqual([], self.harness._select_learned_expression_rules(
            rules,
            hint="好的",
            context={
                "channel": "private",
                "relationship_stage": "close",
                "emotion_gate": "positive",
                "intent": "acknowledgement",
            },
        ))

    def test_semantic_rule_retrieval_respects_channel_relationship_emotion_and_intent(self):
        now = time.time()
        rules = [
            {
                "id": "private-close-play",
                "kind": "style",
                "situation": "亲近时轻松接梗",
                "pattern": "笑死，____",
                "instruction": "先接住笑点，再短短续一句",
                "keywords": ["哈哈"],
                "channels": ["private", "proactive"],
                "relationship_stages": ["close"],
                "emotion_gates": ["positive"],
                "intent": "play",
                "evidence_count": 4,
                "last_seen_ts": now,
            },
            {
                "id": "group-play",
                "kind": "style",
                "situation": "群聊接梗",
                "pattern": "哈哈，____",
                "instruction": "短接一句",
                "keywords": ["哈哈"],
                "channels": ["group"],
                "relationship_stages": ["any"],
                "emotion_gates": ["positive"],
                "intent": "play",
                "evidence_count": 5,
                "last_seen_ts": now,
            },
        ]

        matched = self.harness._select_learned_expression_rules(
            rules,
            hint="哈哈这个好玩",
            context={
                "channel": "private",
                "relationship_stage": "close",
                "emotion_gate": "positive",
                "intent": "play",
            },
        )
        self.assertEqual(["private-close-play"], [item["id"] for item in matched])
        self.assertEqual([], self.harness._select_learned_expression_rules(
            rules,
            hint="哈哈这个好玩",
            context={
                "channel": "private",
                "relationship_stage": "familiar",
                "emotion_gate": "positive",
                "intent": "play",
            },
        ))

    def test_retrieval_prefers_one_style_and_one_grammar_rule(self):
        now = time.time()
        rules = [
            {
                "id": "style-high",
                "kind": "style",
                "situation": "普通闲聊",
                "pattern": "好呀，那就____",
                "instruction": "替换占位内容后接话",
                "keywords": ["继续"],
                "channels": ["private"],
                "evidence_count": 6,
                "last_seen_ts": now,
            },
            {
                "id": "style-second",
                "kind": "style",
                "situation": "继续说明",
                "pattern": "然后呢，____",
                "instruction": "用短问句推进",
                "keywords": ["继续"],
                "channels": ["private"],
                "evidence_count": 5,
                "last_seen_ts": now,
            },
            {
                "id": "grammar",
                "kind": "grammar",
                "situation": "普通闲聊",
                "pattern": "省略主语的 4–10 字短句",
                "instruction": "保持短句并按当前内容生成",
                "keywords": ["继续"],
                "channels": ["private"],
                "evidence_count": 3,
                "last_seen_ts": now,
            },
        ]
        matched = self.harness._select_learned_expression_rules(
            rules,
            hint="继续说",
            limit=2,
            context={"channel": "private", "intent": "casual"},
        )
        self.assertEqual({"style", "grammar"}, {item["kind"] for item in matched})

    def test_legacy_style_and_grammar_with_shared_evidence_become_one_family(self):
        rules = [
            {
                "id": "legacy-style",
                "kind": "style",
                "situation": "调侃对方很厉害时",
                "pattern": "他都____，你也不是一般人",
                "instruction": "替换占位内容后轻松接梗",
                "evidence_examples": ["他都能做到，你也不是一般人", "他都冲上去了，你也不一般"],
                "evidence_count": 9,
            },
            {
                "id": "legacy-grammar",
                "kind": "grammar",
                "situation": "先举例再调侃对方时",
                "pattern": "先用“他都……”举例，再用短句评价对方",
                "instruction": "保持两段式句法，事实按当前消息生成",
                "evidence_examples": ["他都能做到，你也不是一般人", "他都冲上去了，你也不一般"],
                "evidence_count": 9,
            },
        ]

        self.assertTrue(self.harness._assign_expression_rule_families(rules))
        self.assertEqual(1, len({item["family_id"] for item in rules}))
        bundle = self.harness._expression_rule_runtime_bundle(rules)
        self.assertEqual("combined", bundle["kind"])
        self.assertEqual(9, bundle["evidence_count"])
        self.assertEqual(2, bundle["component_count"])

    def test_unrelated_style_and_grammar_are_not_forced_into_one_family(self):
        rules = [
            {
                "id": "style-unrelated",
                "kind": "style",
                "situation": "安慰低落的人",
                "pattern": "摸摸[称谓]",
                "instruction": "只在对方接受安慰时使用",
                "evidence_examples": ["摸摸你", "别难过啦"],
                "evidence_count": 2,
            },
            {
                "id": "grammar-unrelated",
                "kind": "grammar",
                "situation": "询问进度时",
                "pattern": "省略主语的 4–8 字短问句",
                "instruction": "用短问句询问，不追加结论",
                "evidence_examples": ["弄好了吗", "还有多久"],
                "evidence_count": 2,
            },
        ]

        self.harness._assign_expression_rule_families(rules)
        self.assertEqual(2, len({item["family_id"] for item in rules}))

    def test_same_family_retrieval_uses_one_slot_and_one_prompt_rule(self):
        now = time.time()
        rules = [
            {
                "id": "paired-style",
                "family_key": "playful_compare",
                "kind": "style",
                "situation": "轻松比较时",
                "pattern": "他都____，你也不是一般人",
                "instruction": "替换占位后轻松接话",
                "keywords": ["厉害"],
                "channels": ["private"],
                "evidence_examples": ["他都做到了，你也不一般"],
                "evidence_count": 6,
                "last_seen_ts": now,
            },
            {
                "id": "paired-grammar",
                "family_key": "playful_compare",
                "kind": "grammar",
                "situation": "轻松比较时",
                "pattern": "先举例，再用 6–12 字短句评价对方",
                "instruction": "只借鉴两段式句法",
                "keywords": ["厉害"],
                "channels": ["private"],
                "evidence_examples": ["他都做到了，你也不一般"],
                "evidence_count": 6,
                "last_seen_ts": now,
            },
        ]

        matched = self.harness._select_learned_expression_rules(
            rules,
            hint="你也很厉害",
            limit=2,
            context={"channel": "private", "intent": "casual"},
        )
        self.assertEqual(1, len(matched))
        self.assertEqual("combined", matched[0]["kind"])
        self.assertEqual(6, matched[0]["evidence_count"])
        user = {"expression_profile": {"learned_rules": rules}}
        prompt = self.harness._format_expression_profile_for_prompt(user, inbound_text="你也很厉害")
        self.assertEqual(1, prompt.count("- 组合规则｜"))
        self.assertIn("可复用表达", prompt)
        self.assertIn("句法习惯", prompt)
        self.assertIn("句尾括号或颜文字后缀必须与所属句保持同一行", prompt)
        self.assertIn("不得补逗号或其他标点", prompt)

        self.harness.data["users"] = {
            "source-1": {
                "user_id": "source-1",
                "relationship_role": "owner",
                "expression_profile": {"learned_rules": rules},
            }
        }
        selection = self.harness._expression_voice_selection(
            scope="private",
            target_id="friend-1",
            inbound_text="你也很厉害",
            context_owner={"user_id": "friend-1", "relationship_role": "friend"},
        )
        self.assertIn("句尾括号或颜文字后缀必须与所属句保持同一行", selection["prompt"])
        self.assertIn("不得补逗号或其他标点", selection["prompt"])

    def test_two_explicit_negative_feedbacks_return_semantic_rule_to_review(self):
        now = time.time()
        source = {
            "user_id": "source-1",
            "relationship_role": "owner",
            "expression_profile": {
                "learned_rules": [{
                    "id": "rule-feedback",
                    "kind": "style",
                    "situation": "轻松聊天时接梗",
                    "pattern": "笑死，____",
                    "instruction": "轻轻接住笑点，不展开",
                    "keywords": ["哈哈", "好玩"],
                    "channels": ["private"],
                    "relationship_stages": ["any"],
                    "emotion_gates": ["positive"],
                    "intent": "play",
                    "evidence_count": 4,
                    "last_seen_ts": now,
                }]
            },
        }
        recipient = {"user_id": "friend-1", "relationship_role": "friend"}
        self.harness.expression_private_learning_source_mode = "selected"
        self.harness.expression_private_learning_source_ids = ["source-1"]
        self.harness.data["users"] = {"source-1": source, "friend-1": recipient}

        for complaint in ("这个语气好尬，别这样说", "你这样说不像你，正常说话"):
            selection = self.harness._expression_voice_selection(
                scope="private",
                target_id="friend-1",
                inbound_text="哈哈这个好玩",
                context_owner=recipient,
            )
            self.assertEqual(1, len(selection["rules"]))
            self.harness._record_expression_rule_injection(
                recipient,
                {},
                "哈哈，是有点好玩",
                semantic_rules=selection["rules"],
                context=selection["context"],
            )
            result = self.harness._apply_expression_rule_feedback(recipient, complaint, channel="private")
            self.assertEqual("negative", result["signal"])

        profile = source["expression_profile"]
        self.assertEqual([], profile["learned_rules"])
        self.assertEqual("needs_review", profile["pending_rules"][0]["review_status"])
        self.assertEqual(2, profile["pending_rules"][0]["negative_feedback"])

    def test_page_summary_exposes_companion_applicability_and_feedback(self):
        user = {
            "expression_profile": {
                "learned_rules": [{
                    "id": "rule-page",
                    "kind": "style",
                    "situation": "轻松确认",
                    "pattern": "好呀，那就____",
                    "instruction": "先确认再继续",
                    "keywords": ["确认"],
                    "evidence_examples": ["好呀，那继续", "行，那就这样"],
                    "channels": ["private", "proactive"],
                    "relationship_stages": ["familiar", "close"],
                    "emotion_gates": ["normal", "positive"],
                    "intent": "acknowledgement",
                    "avoid": "工具失败时不用",
                    "positive_feedback": 3,
                    "negative_feedback": 1,
                    "use_count": 5,
                    "evidence_count": 4,
                }]
            }
        }

        row = next(
            item
            for item in PrivateCompanionPageApi(self.harness)._expression_profile_summary(user)["rules"]
            if item["rule_type"] == "semantic"
        )
        self.assertEqual(["private", "proactive"], row["channels"])
        self.assertEqual(["familiar", "close"], row["relationship_stages"])
        self.assertEqual("acknowledgement", row["intent"])
        self.assertEqual(3, row["positive_feedback"])
        self.assertEqual(1, row["negative_feedback"])
        self.assertEqual(5, row["use_count"])
        self.assertEqual("情境表达", row["kind_label"])
        self.assertEqual(["好呀，那继续", "行，那就这样"], row["evidence_examples"])

    def test_semantic_rule_merge_deduplicates_same_batch(self):
        profile: dict = {}
        first = self.harness._normalize_expression_rule_candidates(
            [{
                "kind": "style",
                "situation": "表示赞同并继续接话",
                "pattern": "好呀，____",
                "instruction": "替换占位内容后自然延续",
                "keywords": ["赞同", "确认"],
                "evidence_count": 2,
            }],
            source_kind="private",
            source_text="用户: 好的\n用户: 可以",
        )
        equivalent = self.harness._normalize_expression_rule_candidates(
            [{
                "kind": "style",
                "situation": "表示赞同，并继续接话。",
                "pattern": "好呀，____",
                "instruction": "接住意思后替换占位内容自然续话",
                "keywords": ["接话"],
                "evidence_count": 2,
            }],
            source_kind="private",
            source_text="用户: 行\n用户: 没问题",
        )
        self.harness._merge_learned_expression_rules(profile, first, batch_key="batch-a", now=time.time())
        self.harness._merge_learned_expression_rules(profile, first, batch_key="batch-a", now=time.time())
        self.assertEqual(2, profile["learned_rules"][0]["evidence_count"])

        self.harness._merge_learned_expression_rules(profile, equivalent, batch_key="batch-b", now=time.time())
        self.assertEqual(1, len(profile["learned_rules"]))
        self.assertEqual(4, profile["learned_rules"][0]["evidence_count"])

    def test_distinct_expressions_for_same_situation_are_not_collapsed(self):
        profile: dict = {}
        candidates = self.harness._normalize_expression_rule_candidates(
            [
                {
                    "kind": "style",
                    "situation": "表示赞同并继续接话",
                    "pattern": "好呀，____",
                    "instruction": "替换占位内容后自然接话",
                    "evidence_count": 2,
                },
                {
                    "kind": "style",
                    "situation": "表示赞同并继续接话",
                    "pattern": "可以，那就____",
                    "instruction": "保持短句并补上当前行动",
                    "evidence_count": 2,
                },
            ],
            source_kind="private",
            source_text="用户: 好呀，继续\n用户: 可以，那就接着聊",
        )
        self.harness._merge_learned_expression_rules(profile, candidates, batch_key="batch", now=time.time())
        self.assertEqual(2, len(profile["learned_rules"]))

    def test_keyword_retrieval_uses_safe_template_and_drops_legacy_identifier_pattern(self):
        owner = {
            "user_id": "owner-1",
            "relationship_role": "owner",
            "expression_profile": {
                "learned_rules": [
                    {
                        "id": "rule-safe",
                        "kind": "style",
                        "situation": "表示赞同并继续接话",
                        "pattern": "好呀，那就____",
                        "instruction": "替换占位内容后自然延续",
                        "keywords": ["赞同", "确认"],
                        "evidence_count": 3,
                        "last_seen_ts": time.time(),
                    },
                    {
                        "id": "rule-unsafe",
                        "kind": "style",
                        "situation": "表示赞同并继续接话",
                        "pattern": "主人专用原句 100000001",
                        "instruction": "复述来源身份",
                        "keywords": ["赞同"],
                        "evidence_count": 3,
                        "last_seen_ts": time.time(),
                    },
                ]
            },
        }
        self.harness.data["users"] = {"owner-1": owner}
        voice = self.harness._refresh_expression_voice_profile()

        self.assertEqual(1, len(voice["learned_rules"]))
        prompt = self.harness._format_expression_voice_for_prompt(
            scope="private",
            target_id="friend-1",
            inbound_text="我也赞同这个说法",
        )
        self.assertIn("好呀，那就____", prompt)
        self.assertIn("替换占位内容", prompt)
        self.assertNotIn("主人专用原句", prompt)
        self.assertNotIn("100000001", prompt)

    def test_group_page_rows_expose_pattern_evidence_and_status(self):
        group: dict = {}
        for text in ("好呀~", "行啦~"):
            self.harness._update_group_expression_profile_from_message(group, text)

        summary = PrivateCompanionPageApi(self.harness)._expression_profile_summary(group, source_type="group")
        row = summary["samples"][0]
        self.assertEqual(0, summary["pattern_count"])
        self.assertEqual(1, summary["observation_count"])
        self.assertEqual(2, summary["observation_evidence_count"])
        self.assertEqual("supported", row["pattern_status"])
        self.assertIn("短确认", row["pattern_label"])

    def test_unselected_sources_are_excluded_from_global_voice(self):
        self.harness.expression_private_learning_source_mode = "selected"
        self.harness.expression_private_learning_source_ids = ["selected-user"]
        self.harness.expression_group_learning_source_mode = "selected"
        self.harness.expression_group_learning_source_ids = ["selected-group"]
        excluded_user = {"user_id": "other-user", "relationship_role": "friend"}
        excluded_group = {"group_id": "other-group"}
        for text in ("好呀~", "行啦~"):
            self.harness._update_expression_profile_from_message(excluded_user, text)
            self.harness._update_group_expression_profile_from_message(excluded_group, text)
        self.harness.data["users"] = {"other-user": excluded_user}
        self.harness.data["groups"] = {"other-group": excluded_group}

        voice = self.harness._refresh_expression_voice_profile()
        self.assertEqual(0, voice["sample_count"])
        self.assertEqual("", self.harness._format_expression_voice_for_prompt(scope="group", target_id="other-group"))

    def test_global_voice_ignores_expired_evidence_on_refresh(self):
        owner = {"user_id": "owner-1", "relationship_role": "owner"}
        for text in ("好呀~", "行啦~"):
            self.harness._update_expression_profile_from_message(owner, text)
        for sample in owner["expression_profile"]["samples"]:
            sample["ts"] = time.time() - 31 * 86400
        self.harness.data["users"] = {"owner-1": owner}
        self.harness.data["expression_voice_profile"] = {
            "sample_count": 2,
            "actions": ["旧表达"],
            "scope_signature": self.harness._expression_scope_signature(),
            "refresh_day": "2000-01-01",
        }

        voice = self.harness._expression_voice_profile()
        self.assertEqual(0, voice["sample_count"])
        self.assertEqual([], voice["actions"])

    def test_private_and_group_application_modes_gate_injection(self):
        owner = {
            "user_id": "owner-1",
            "relationship_role": "owner",
            "expression_profile": {
                "learned_rules": [{
                    "id": "rule-application",
                    "kind": "style",
                    "situation": "普通闲聊时自然接话",
                    "pattern": "好呀，那就____",
                    "instruction": "替换占位内容后自然延续",
                    "keywords": [],
                    "channels": ["private", "group", "proactive"],
                    "relationship_stages": ["any"],
                    "emotion_gates": ["any"],
                    "intent": "any",
                    "evidence_count": 2,
                    "review_status": "approved",
                    "last_seen_ts": time.time(),
                }],
            },
        }
        self.harness.data["users"] = {"owner-1": owner}
        self.harness._refresh_expression_voice_profile()
        self.harness.expression_private_application_mode = "selected"
        self.harness.expression_private_application_user_ids = ["private-ok"]
        self.harness.expression_group_application_mode = "selected"
        self.harness.expression_group_application_ids = ["group-ok"]

        self.assertTrue(self.harness._format_expression_voice_for_prompt(scope="private", target_id="private-ok"))
        self.assertEqual("", self.harness._format_expression_voice_for_prompt(scope="proactive", target_id="private-no"))
        self.assertTrue(self.harness._format_expression_voice_for_prompt(scope="group", target_id="group-ok"))
        self.assertEqual("", self.harness._format_expression_voice_for_prompt(scope="group", target_id="group-no"))

    def test_page_scope_summary_and_setting_normalization(self):
        self.harness.expression_private_learning_source_mode = "selected"
        self.harness.expression_private_learning_source_ids = ["user-1"]
        self.harness.expression_group_application_mode = "selected"
        self.harness.expression_group_application_ids = ["group-1"]
        api = PrivateCompanionPageApi(self.harness)

        summary = api._expression_learning_scope_summary(self.harness.data)
        self.assertEqual("selected", summary["private_learning"]["mode"])
        self.assertEqual(["user-1"], summary["private_learning"]["ids"])
        self.assertEqual(["group-1"], summary["group_application"]["ids"])
        self.assertEqual(6, summary["group_budget"]["daily_limit"])
        self.assertEqual(20, summary["group_budget"]["min_new_messages"])
        self.assertIn("expression_group_application_ids", api._allowed_setting_keys())
        self.assertIn("expression_group_learning_daily_batch_limit", api._allowed_setting_keys())
        self.assertEqual(
            ["group-1", "group-2"],
            api._normalize_setting_value("expression_group_application_ids", ["group-1", "group-1", "group-2"]),
        )
        self.assertEqual(
            "all",
            api._normalize_setting_value("expression_group_application_mode", "invalid"),
        )
        self.assertEqual(
            50,
            api._normalize_setting_value("expression_group_learning_daily_batch_limit", 999),
        )


class PrivateEpisodeExpressionHarness(ExpressionLearningHarness):
    def __init__(self) -> None:
        super().__init__()
        self._data_lock = asyncio.Lock()
        self.enable_dialogue_episode_memory = True
        self.episode_memory_refresh_messages = 5
        self.episode_memory_refresh_minutes = 60
        self.max_dialogue_episodes = 12
        self.enable_open_loop_tracking = False
        self.dialogue_episode_provider_id = ""
        self.mai_style_provider_id = ""
        self.user_id = "owner-1"
        self.data["users"][self.user_id] = {
            "user_id": self.user_id,
            "relationship_role": "owner",
            "episode_message_count": 5,
        }
        self.recent_text = "\n".join(
            [
                "07-15 10:00 用户: 好的，我接着说",
                "07-15 10:01 星缘(Bot回复): 好呀",
                "07-15 10:02 用户: 可以，我再补一句",
                "07-15 10:03 用户: 行，那继续",
                "07-15 10:04 用户: 没问题，接着聊",
                "07-15 10:05 用户: 好，我继续说",
            ]
        )
        self.last_prompt = ""

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    async def _collect_recent_private_conversation_text(self, *_args, **_kwargs) -> str:
        return self.recent_text

    async def _try_acquire_user_background_task(self, *_args, **_kwargs) -> bool:
        return True

    async def _mark_user_background_retry(self, *_args, **_kwargs) -> None:
        return None

    async def _llm_call(self, prompt: str, **_kwargs) -> str:
        self.last_prompt = prompt
        return json.dumps(
            {
                "summary": "用户连续确认并把话题接了下去",
                "emotional_residue": "轻松",
                "reusable_topic": "继续刚才的话题",
                "user_events": [],
                "bot_promises": [],
                "open_loops": [],
                "avoid_next": [],
                "style_expressions": [{
                    "situation": "表示赞同并继续接话",
                    "style": "好呀，那就____",
                    "instruction": "替换占位内容后自然延续",
                    "tags": ["赞同", "确认"],
                    "evidence_examples": ["好的，我接着说", "行，那继续"],
                    "evidence_count": 5,
                }],
                "grammar_expressions": [{
                    "situation": "简短确认后继续话题",
                    "style": "省略主语的 4–10 字短句",
                    "instruction": "先短句确认，再根据当前内容续一句",
                    "tags": ["确认", "接话"],
                    "evidence_examples": ["行，那继续", "好，我继续说"],
                    "evidence_count": 4,
                }],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _get_default_persona_prompt() -> str:
        return "自然聊天"

    @staticmethod
    def _task_provider(*_args) -> str:
        return ""

    @staticmethod
    def _save_data_sync() -> None:
        return None


class GroupEpisodeExpressionHarness(GroupObservationMixin, ExpressionLearningHarness):
    def __init__(self) -> None:
        ExpressionLearningHarness.__init__(self)
        self._data_lock = asyncio.Lock()
        self.enable_group_episode_memory = True
        self.group_episode_refresh_minutes = 60
        self.max_group_episodes = 10
        self.group_episode_provider_id = ""
        self.mai_style_provider_id = ""
        self.expression_group_learning_source_mode = "all"
        self.expression_group_learning_min_new_messages = 5
        self.expression_group_learning_daily_batch_limit = 6
        self._base_ts = time.time() - 100
        recent = [
            {
                "name": f"成员{index}",
                "sender_id": str(10000 + index),
                "text": text,
                "ts": self._base_ts + index,
            }
            for index, text in enumerate(
                ("好呀~", "行啦~", "可以呀~", "那继续", "接着说", "没问题", "好", "可以", "行", "然后呢", "后来呢", "再说说")
            )
        ]
        self.data["groups"]["group-1"] = {
            "group_id": "group-1",
            "recent_messages": recent,
        }
        self.last_prompt = ""

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"][group_id]

    def _filtered_group_recent_messages(self, group: dict) -> list[dict]:
        return list(group.get("recent_messages") or [])

    async def _try_acquire_group_background_task(self, *_args, **_kwargs) -> bool:
        return True

    async def _mark_group_background_retry(self, *_args, **_kwargs) -> None:
        return None

    async def _llm_call(self, prompt: str, **_kwargs) -> str:
        self.last_prompt = prompt
        return json.dumps(
            {
                "summary": "群友在轻松地延续话题",
                "main_topics": ["日常闲聊"],
                "new_meme": "",
                "active_people": [],
                "avoid_repeat": [],
                "style_expressions": [{
                    "situation": "表示赞同并继续接话",
                    "style": "好呀，那就____",
                    "instruction": "替换占位内容后自然延续",
                    "tags": ["赞同", "确认"],
                    "evidence_examples": ["好呀~", "行啦~"],
                    "evidence_count": 3,
                }],
                "grammar_expressions": [{
                    "situation": "群聊中快速确认",
                    "style": "省略主语的 2–8 字短句",
                    "instruction": "用短句确认，不扩成长说明",
                    "tags": ["确认", "接话"],
                    "evidence_examples": ["可以呀~", "没问题"],
                    "evidence_count": 2,
                }],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _task_provider(*_args) -> str:
        return ""

    @staticmethod
    def _save_data_sync() -> None:
        return None


class EpisodeExpressionLearningTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_episode_piggybacks_expression_rule_generation(self):
        harness = PrivateEpisodeExpressionHarness()
        await harness._maybe_refresh_dialogue_episode(harness.user_id, harness._get_user(harness.user_id))

        profile = harness._get_user(harness.user_id)["expression_profile"]
        self.assertEqual(2, len(profile["pending_rules"]))
        self.assertNotIn("learned_rules", profile)
        self.assertEqual({"style", "grammar"}, {item["kind"] for item in profile["pending_rules"]})
        self.assertEqual("好呀，那就____", next(item for item in profile["pending_rules"] if item["kind"] == "style")["pattern"])
        self.assertIn("只分析“用户:”行", harness.last_prompt)
        self.assertIn("style_expressions", harness.last_prompt)
        self.assertIn("grammar_expressions", harness.last_prompt)

    async def test_group_episode_piggybacks_privacy_safe_rule_generation(self):
        harness = GroupEpisodeExpressionHarness()
        group = harness._get_group("group-1")
        await harness._maybe_refresh_group_episode("group-1", group)

        profile = harness._get_group("group-1")["expression_profile"]
        self.assertEqual(2, len(profile["pending_rules"]))
        self.assertNotIn("learned_rules", profile)
        style_rule = next(item for item in profile["pending_rules"] if item["kind"] == "style")
        self.assertEqual("好呀，那就____", style_rule["pattern"])
        self.assertEqual(["好呀~", "行啦~"], style_rule["evidence_examples"])
        self.assertIn("可直接借鉴的短表达", harness.last_prompt)
        self.assertIn("只做中性、安全的概括", harness.last_prompt)
        self.assertIn("不重现敏感原话", harness.last_prompt)
        self.assertNotIn("严禁复制成员原句", harness.last_prompt)
        self.assertTrue(harness._get_group("group-1").get("last_expression_rule_source_ts"))

    async def test_group_episode_reports_missing_model_result_without_mislabeling_json(self):
        harness = GroupEpisodeExpressionHarness()
        retry_errors = []

        async def no_result(prompt: str, **_kwargs):
            harness.last_prompt = prompt
            return None

        async def capture_retry(_group_id, task, _now, error):
            retry_errors.append((task, error))

        harness._llm_call = no_result
        harness._mark_group_background_retry = capture_retry

        await harness._maybe_refresh_group_episode("group-1", harness._get_group("group-1"))

        self.assertEqual(retry_errors, [("group_episode", "llm_no_result")])

    async def test_group_semantic_batches_share_daily_budget_and_limit_each_group(self):
        harness = GroupEpisodeExpressionHarness()
        harness.expression_group_learning_daily_batch_limit = 1
        harness.data["groups"]["group-2"] = {"group_id": "group-2"}

        first = await harness._try_reserve_group_expression_rule_batch(
            "group-1",
            batch_key="batch-1",
            candidate_count=20,
            now=time.time(),
        )
        same_group = await harness._try_reserve_group_expression_rule_batch(
            "group-1",
            batch_key="batch-2",
            candidate_count=20,
            now=time.time(),
        )
        other_group = await harness._try_reserve_group_expression_rule_batch(
            "group-2",
            batch_key="batch-3",
            candidate_count=20,
            now=time.time(),
        )

        self.assertTrue(first)
        self.assertFalse(same_group)
        self.assertFalse(other_group)
        self.assertEqual(1, sum(harness.data["expression_learning_runtime"]["group_batches_by_day"].values()))

    async def test_group_semantic_learning_only_counts_messages_after_cursor(self):
        harness = GroupEpisodeExpressionHarness()
        group = harness._get_group("group-1")
        await harness._maybe_refresh_group_episode("group-1", group)
        cursor = group["last_expression_rule_source_ts"]

        group["last_episode_refresh_at"] = 0
        group["last_expression_rule_attempt_day"] = ""
        for index in range(5):
            group["recent_messages"].append(
                {
                    "name": f"新成员{index}",
                    "sender_id": str(20000 + index),
                    "text": f"新增接话 {index}",
                    "ts": cursor + index + 1,
                }
            )
        await harness._maybe_refresh_group_episode("group-1", group)

        self.assertIn("末尾 5 条新增消息", harness.last_prompt)
        self.assertEqual(cursor + 5, group["last_expression_rule_source_ts"])


class ExpressionScopeLifecyclePlugin(ExpressionLearningHarness):
    def __init__(self) -> None:
        super().__init__()
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {
                "owner-1": {
                    "user_id": "owner-1",
                    "relationship_role": "owner",
                    "expression_profile": {"samples": []},
                }
            },
            "groups": {"group-1": {"group_id": "group-1", "enabled": True, "expression_profile": {"samples": []}}},
        }
        self.expression_refresh_count = 0
        self.config = None
        self.target_user_ids = ["owner-1"]
        self.private_user_aliases = {}
        self.private_user_delivery_aliases = {}
        self.group_whitelist_ids = ["group-1"]
        self.group_blacklist_ids = []
        self.expression_private_learning_source_mode = "selected"
        self.expression_private_learning_source_ids = ["owner-1", "friend-2"]
        self.expression_private_application_mode = "selected"
        self.expression_private_application_user_ids = ["owner-1", "friend-2"]
        self.expression_group_learning_source_mode = "selected"
        self.expression_group_learning_source_ids = ["group-1", "group-2"]
        self.expression_group_application_mode = "selected"
        self.expression_group_application_ids = ["group-1", "group-2"]

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"][group_id]

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return str(user_id or "").strip()

    @staticmethod
    def _parse_private_user_aliases(_value) -> dict:
        return {}

    @staticmethod
    def _merge_private_user_alias_records() -> bool:
        return False

    @staticmethod
    def _ensure_private_user_umo(_user_id: str, _user: dict) -> None:
        return None

    @staticmethod
    def _normalize_private_user_role(value) -> str:
        role = str(value or "").strip().lower()
        return role if role in {"owner", "friend"} else ""

    @staticmethod
    def _private_user_role(user: dict, _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")

    @staticmethod
    def _save_data_sync() -> None:
        return None

    def _refresh_expression_voice_profile(self) -> dict:
        self.expression_refresh_count += 1
        return super()._refresh_expression_voice_profile()


class ExpressionScopeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = Quart(__name__)
        self.plugin = ExpressionScopeLifecyclePlugin()
        self.api = PrivateCompanionPageApi(self.plugin)
        self.api._user_summary = lambda user_id, user: {"user_id": user_id, "relationship_role": user.get("relationship_role")}
        self.api._group_summary = lambda group_id, group: {"group_id": group_id, "enabled": group.get("enabled", True)}
        self.api._save_config_if_possible = AsyncMock(return_value=True)

    async def test_role_change_and_clear_observation_refresh_global_voice(self):
        async with self.app.test_request_context(
            "/",
            method="POST",
            json={"user_id": "owner-1", "relationship_role": "friend"},
        ):
            user_result = await self.api.update_user()
        self.assertTrue(user_result["success"])
        self.assertEqual(1, self.plugin.expression_refresh_count)

        async with self.app.test_request_context(
            "/",
            method="POST",
            json={"group_id": "group-1", "clear_observation": True},
        ):
            group_result = await self.api.update_group()
        self.assertTrue(group_result["success"])
        self.assertEqual(2, self.plugin.expression_refresh_count)

    async def test_clear_behavior_habits_does_not_delete_other_user_learning(self):
        user = self.plugin.data["users"]["owner-1"]
        user["behavior_habits"] = {"patterns": [{"topic": "晚间聊天", "count": 4}]}
        user["expression_profile"] = {"rules": [{"id": "keep-expression"}]}
        user["dialogue_episodes"] = [{"id": "keep-episode"}]

        async with self.app.test_request_context(
            "/",
            method="POST",
            json={"user_id": "owner-1", "clear_behavior_habits": True},
        ):
            result = await self.api.update_user()

        self.assertTrue(result["success"])
        self.assertEqual({}, user["behavior_habits"])
        self.assertEqual([{"id": "keep-expression"}], user["expression_profile"]["rules"])
        self.assertEqual([{"id": "keep-episode"}], user["dialogue_episodes"])
        self.assertEqual(0, self.plugin.expression_refresh_count)

    async def test_unified_library_can_delete_a_group_sample(self):
        self.plugin.data["groups"]["group-1"]["expression_profile"] = {
            "samples": [{"id": "group-sample", "length": 6, "scene": "casual", "ts": time.time()}]
        }
        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "group",
                "source_id": "group-1",
                "expression_action": "delete_sample",
                "sample_id": "group-sample",
                "sample_index": 0,
            },
        ):
            result = await self.api.update_expression_library()

        self.assertTrue(result["success"])
        self.assertEqual(0, result["data"]["sample_count"])
        self.assertEqual([], self.plugin.data["groups"]["group-1"]["expression_profile"]["samples"])

    async def test_unified_library_can_clear_pending_samples_across_sources(self):
        self.plugin.data["users"]["owner-1"]["expression_profile"]["pending_samples"] = [{"id": "private-pending"}]
        self.plugin.data["groups"]["group-1"]["expression_profile"]["pending_samples"] = [{"id": "group-pending"}]
        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={"expression_action": "clear_all_pending"},
        ):
            result = await self.api.update_expression_library()

        self.assertTrue(result["success"])
        self.assertEqual(0, result["data"]["pending_count"])
        self.assertEqual([], self.plugin.data["users"]["owner-1"]["expression_profile"]["pending_samples"])
        self.assertEqual([], self.plugin.data["groups"]["group-1"]["expression_profile"]["pending_samples"])

    async def test_semantic_rule_requires_manual_approval_before_use(self):
        profile = self.plugin.data["users"]["owner-1"]["expression_profile"]
        candidate = {
            "id": "pending-rule-1",
            "kind": "style",
            "situation": "表示赞同并继续接话",
            "pattern": "好呀，那就____",
            "instruction": "替换占位内容后自然延续",
            "keywords": ["赞同", "确认"],
            "evidence_count": 3,
            "review_status": "pending",
            "last_batch_key": "batch-1",
        }
        profile["pending_rules"] = [candidate]
        self.plugin._refresh_expression_voice_profile()
        self.assertEqual([], self.plugin.data["expression_voice_profile"].get("learned_rules"))

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "approve_rule",
                "rule_id": "pending-rule-1",
            },
        ):
            result = await self.api.update_expression_library()

        self.assertTrue(result["success"])
        self.assertEqual([], profile["pending_rules"])
        self.assertEqual(1, len(profile["learned_rules"]))
        self.assertEqual(1, len(result["data"]["rules"]))
        self.assertEqual(0, result["data"]["pending_rule_count"])

    async def test_library_read_backfills_legacy_rule_families(self):
        profile = self.plugin.data["users"]["owner-1"]["expression_profile"]
        profile["learned_rules"] = [
            {
                "id": "legacy-read-style",
                "kind": "style",
                "situation": "轻松接梗时",
                "pattern": "他都____，你也不一般",
                "instruction": "替换占位内容后自然接梗",
                "evidence_examples": ["他都做到了，你也不一般"],
                "evidence_count": 5,
            },
            {
                "id": "legacy-read-grammar",
                "kind": "grammar",
                "situation": "轻松接梗时",
                "pattern": "先举例，再用 6–12 字短句评价对方",
                "instruction": "只借鉴两段式句法",
                "evidence_examples": ["他都做到了，你也不一般"],
                "evidence_count": 5,
            },
        ]

        result = await self.api.get_expression_library()

        self.assertTrue(result["success"])
        self.assertEqual(1, result["data"]["rule_group_count"])
        self.assertEqual(1, len({item.get("family_id") for item in profile["learned_rules"]}))
        self.assertTrue(profile["learned_rules"][0]["family_id"].startswith("xf-"))

    async def test_rule_family_is_reviewed_and_deleted_as_one_group(self):
        profile = self.plugin.data["users"]["owner-1"]["expression_profile"]
        family_rules = [
            {
                "id": "family-style",
                "family_key": "family_review",
                "kind": "style",
                "situation": "轻松安慰时",
                "pattern": "摸摸[称谓]",
                "instruction": "替换称谓后轻声安慰",
                "evidence_examples": ["摸摸你", "摸摸小朋友"],
                "evidence_count": 7,
                "review_status": "pending",
            },
            {
                "id": "family-grammar",
                "family_key": "family_review",
                "kind": "grammar",
                "situation": "轻松安慰时",
                "pattern": "省略主语的 4–8 字短句",
                "instruction": "只使用一到两句短句",
                "evidence_examples": ["摸摸你", "摸摸小朋友"],
                "evidence_count": 7,
                "review_status": "pending",
            },
        ]
        profile["pending_rules"] = [dict(item) for item in family_rules]
        self.plugin._backfill_expression_rule_families(profile)
        family_id = profile["pending_rules"][0]["family_id"]

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "approve_rule_group",
                "rule_family_id": family_id,
            },
        ):
            approved = await self.api.update_expression_library()

        self.assertTrue(approved["success"])
        self.assertEqual([], profile["pending_rules"])
        self.assertEqual(2, len(profile["learned_rules"]))
        self.assertEqual(1, approved["data"]["rule_group_count"])
        self.assertEqual(7, approved["data"]["rule_groups"][0]["evidence_count"])

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "delete_rule_group",
                "rule_family_id": family_id,
            },
        ):
            deleted = await self.api.update_expression_library()

        self.assertTrue(deleted["success"])
        self.assertEqual([], profile["learned_rules"])
        self.assertEqual(0, deleted["data"]["rule_group_count"])

        profile["pending_rules"] = [dict(item) for item in family_rules]
        self.plugin._backfill_expression_rule_families(profile)
        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "reject_rule_group",
                "rule_family_id": family_id,
            },
        ):
            rejected = await self.api.update_expression_library()

        self.assertTrue(rejected["success"])
        self.assertEqual([], profile["pending_rules"])
        self.assertEqual(0, rejected["data"]["pending_rule_group_count"])

    async def test_rule_family_can_be_edited_before_and_after_approval(self):
        profile = self.plugin.data["users"]["owner-1"]["expression_profile"]
        profile["pending_rules"] = [
            {
                "id": "editable-style",
                "family_key": "editable_family",
                "kind": "style",
                "situation": "安慰时",
                "pattern": "摸摸[称谓]",
                "instruction": "替换称谓后轻声安慰",
                "keywords": ["安慰"],
                "tags": ["安慰"],
                "evidence_examples": ["摸摸你", "摸摸小朋友"],
                "evidence_count": 7,
                "positive_feedback": 2,
                "review_status": "pending",
            },
            {
                "id": "editable-grammar",
                "family_key": "editable_family",
                "kind": "grammar",
                "situation": "安慰时",
                "pattern": "省略主语的 4–8 字短句",
                "instruction": "只使用一到两句短句",
                "keywords": ["安慰"],
                "tags": ["安慰"],
                "evidence_examples": ["摸摸你", "摸摸小朋友"],
                "evidence_count": 7,
                "positive_feedback": 2,
                "review_status": "pending",
            },
        ]
        self.plugin._backfill_expression_rule_families(profile)
        family_id = profile["pending_rules"][0]["family_id"]

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "update_rule_group",
                "rule_family_id": family_id,
                "rule_storage": "pending",
                "label": "低落时轻声安慰",
                "situation": "对方有些低落、需要接住情绪时",
                "signals": ["低落", "安慰", "低落"],
                "avoid": "对方明确不想继续时不用",
                "style_rule": {
                    "pattern": "抱抱[称谓]",
                    "instruction": "替换称谓后自然安慰，不照抄支持片段",
                },
                "grammar_rule": {
                    "pattern": "省略主语的 5–10 字短句",
                    "instruction": "使用一到两句短句，不连续追问",
                },
            },
        ):
            edited_pending = await self.api.update_expression_library()

        self.assertTrue(edited_pending["success"])
        self.assertIn("共更新 2 条", edited_pending["data"]["message"])
        self.assertEqual(2, len(profile["pending_rules"]))
        style = next(item for item in profile["pending_rules"] if item["kind"] == "style")
        grammar = next(item for item in profile["pending_rules"] if item["kind"] == "grammar")
        self.assertEqual("editable-style", style["id"])
        self.assertEqual(["摸摸你", "摸摸小朋友"], style["evidence_examples"])
        self.assertEqual(7, style["evidence_count"])
        self.assertEqual(2, style["positive_feedback"])
        self.assertEqual("抱抱[称谓]", style["pattern"])
        self.assertEqual("省略主语的 5–10 字短句", grammar["pattern"])
        self.assertEqual(["低落", "安慰"], style["keywords"])
        self.assertTrue(style["manually_edited"])
        edited_group = next(
            item for item in edited_pending["data"]["pending_rule_groups"]
            if item["family_id"] == family_id
        )
        self.assertEqual("低落时轻声安慰", edited_group["label"])

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "approve_rule_group",
                "rule_family_id": family_id,
            },
        ):
            approved = await self.api.update_expression_library()
        self.assertTrue(approved["success"])

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "update_rule_group",
                "rule_family_id": family_id,
                "rule_storage": "learned",
                "label": "安慰时简短接住",
                "situation": "需要简短安慰时",
                "signals": "安慰，低落",
                "avoid": "事实或边界冲突时不用",
                "style_rule": {
                    "pattern": "抱一下[称谓]",
                    "instruction": "只在关系合适时替换称谓并自然表达",
                },
                "grammar_rule": {
                    "pattern": "省略主语的 4–9 字短句",
                    "instruction": "最多两句，不机械重复",
                },
            },
        ):
            edited_learned = await self.api.update_expression_library()

        self.assertTrue(edited_learned["success"])
        learned_style = next(item for item in profile["learned_rules"] if item["kind"] == "style")
        self.assertEqual("抱一下[称谓]", learned_style["pattern"])
        self.assertEqual("editable-style", learned_style["id"])
        self.assertEqual(7, learned_style["evidence_count"])
        self.assertGreaterEqual(self.plugin.expression_refresh_count, 3)

    async def test_invalid_rule_group_edit_is_rejected_without_partial_write(self):
        profile = self.plugin.data["users"]["owner-1"]["expression_profile"]
        profile["pending_rules"] = [
            {
                "id": "invalid-edit-style",
                "family_key": "invalid_edit_family",
                "kind": "style",
                "situation": "玩笑时",
                "pattern": "好呀，那就____",
                "instruction": "替换占位后自然接话",
                "evidence_examples": ["好呀，那就去吧"],
                "evidence_count": 3,
                "review_status": "pending",
            },
            {
                "id": "invalid-edit-grammar",
                "family_key": "invalid_edit_family",
                "kind": "grammar",
                "situation": "玩笑时",
                "pattern": "先回应，再用 6–12 字短句补充",
                "instruction": "保持两段式短句",
                "evidence_examples": ["好呀，那就去吧"],
                "evidence_count": 3,
                "review_status": "pending",
            },
        ]
        self.plugin._backfill_expression_rule_families(profile)
        family_id = profile["pending_rules"][0]["family_id"]
        original = [dict(item) for item in profile["pending_rules"]]

        async with self.app.test_request_context(
            "/expression-library/update",
            method="POST",
            json={
                "source_type": "private",
                "source_id": "owner-1",
                "expression_action": "update_rule_group",
                "rule_family_id": family_id,
                "rule_storage": "pending",
                "label": "错误编辑",
                "situation": "玩笑时",
                "signals": ["玩笑"],
                "style_rule": {"pattern": "轻松语气", "instruction": "自然一点"},
                "grammar_rule": {"pattern": "先回应，再用 6–12 字短句补充", "instruction": "保持两段式短句"},
            },
        ):
            result = await self.api.update_expression_library()

        self.assertFalse(result["success"])
        self.assertIn("可复用表达不符合", result["error"])
        self.assertEqual(original, profile["pending_rules"])

    async def test_delete_user_and_group_clean_selected_scope_ids(self):
        async with self.app.test_request_context(
            "/",
            method="POST",
            json={"user_id": "owner-1"},
        ):
            user_result = await self.api.delete_user()
        self.assertTrue(user_result["success"])
        self.assertEqual(["friend-2"], self.plugin.expression_private_learning_source_ids)
        self.assertEqual(["friend-2"], self.plugin.expression_private_application_user_ids)
        self.assertTrue(user_result["data"]["removed_expression_scope"])

        async with self.app.test_request_context(
            "/",
            method="POST",
            json={"group_id": "group-1"},
        ):
            group_result = await self.api.delete_group()
        self.assertTrue(group_result["success"])
        self.assertEqual(["group-2"], self.plugin.expression_group_learning_source_ids)
        self.assertEqual(["group-2"], self.plugin.expression_group_application_ids)
        self.assertTrue(group_result["data"]["removed_expression_scope"])


if __name__ == "__main__":
    unittest.main()
