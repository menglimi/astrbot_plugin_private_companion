# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.constants import MODEL_TASK_PROVIDER_KEYS
import astrbot_plugin_private_companion.task_prompt_registry as registry
from astrbot_plugin_private_companion.task_prompt_registry import (
    TASK_PROMPT_CONFIG_KEY,
    TASK_PROMPT_KEYS,
    TASK_PROMPT_MAX_CHARS,
    apply_task_prompt_override,
    builtin_task_prompt,
    builtin_task_prompt_preview,
    catalog_task_prompts,
    normalize_task_prompt_overrides,
    resolve_task_prompt_override,
    task_prompt_metadata,
    validate_task_prompt_override,
)


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_SOURCE_DIRS = {"tests", "scripts", ".git", "__pycache__"}
MAIN_CONVERSATION_TASKS = {
    "astrbot_private_reply",
    "astrbot_group_reply",
    "astrbot_reply",
}


def _production_trees() -> dict[Path, ast.AST]:
    trees: dict[Path, ast.AST] = {}
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in EXCLUDED_SOURCE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        trees[path] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return trees


def _literal_task_keys(trees: dict[Path, ast.AST]) -> set[str]:
    task_keys: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"task", "task_name"}:
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    task_keys.add(keyword.value.value)
    return task_keys - MAIN_CONVERSATION_TASKS


def _dynamic_task_prefixes(trees: dict[Path, ast.AST]) -> set[str]:
    prefixes: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "task" or not isinstance(keyword.value, ast.JoinedStr):
                    continue
                first = keyword.value.values[0] if keyword.value.values else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value:
                    prefixes.add(first.value)
    return prefixes


class TaskPromptRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trees = _production_trees()
        cls.catalog = catalog_task_prompts()
        cls.catalog_by_key = {str(row["task_key"]): row for row in cls.catalog}

    def test_configuration_contract_is_stable(self) -> None:
        self.assertEqual("task_prompt_overrides", TASK_PROMPT_CONFIG_KEY)
        self.assertEqual(len(TASK_PROMPT_KEYS), len(self.catalog_by_key))
        self.assertGreaterEqual(len(self.catalog), 80)

    def test_catalog_has_complete_builtin_prompts_and_no_main_conversation_tasks(self) -> None:
        required = {
            "task_key",
            "name",
            "group",
            "description",
            "provider_key",
            "builtin_prompt",
            "builtin_prompt_dynamic",
            "custom_prompt",
            "customized",
        }
        required_sections = (
            "【任务身份】",
            "【任务目标】",
            "【动态输入】",
            "【执行规则】",
            "【输出契约】",
            "【禁止事项】",
        )
        dynamic_placeholders = (
            "{{task_input}}",
            "{{persona_context}}",
            "{{conversation_context}}",
            "{{tool_result}}",
            "{{image_observations}}",
            "{{runtime_metadata}}",
        )
        for row in self.catalog:
            self.assertTrue(required <= row.keys(), row.get("task_key"))
            self.assertTrue(str(row["name"]).strip(), row.get("task_key"))
            self.assertTrue(str(row["group"]).strip(), row.get("task_key"))
            self.assertTrue(str(row["description"]).strip(), row.get("task_key"))
            builtin_prompt = str(row["builtin_prompt"])
            self.assertTrue(builtin_prompt.strip(), row.get("task_key"))
            # The panel exposes the complete authored task specification.  It
            # must contain every section and safe runtime placeholders rather
            # than the former one-line template summary.
            for section in required_sections:
                self.assertIn(section, builtin_prompt, row.get("task_key"))
            self.assertIn(f"任务标识：{row['task_key']}", builtin_prompt, row.get("task_key"))
            self.assertIn(str(row["name"]), builtin_prompt, row.get("task_key"))
            self.assertTrue(
                any(placeholder in builtin_prompt for placeholder in dynamic_placeholders),
                row.get("task_key"),
            )
            self.assertGreaterEqual(len(builtin_prompt), 180, row.get("task_key"))
            self.assertNotIn("模板预览", builtin_prompt, row.get("task_key"))
            self.assertNotIn("稳定规则预览", builtin_prompt, row.get("task_key"))
            self.assertIs(row["builtin_prompt_dynamic"], True)
        self.assertFalse(MAIN_CONVERSATION_TASKS & self.catalog_by_key.keys())
        self.assertFalse(any(key.startswith("astrbot_") for key in self.catalog_by_key))

    def test_builtin_prompt_is_complete_safe_and_task_specific(self) -> None:
        screen = builtin_task_prompt_preview("screen_narration")
        forward = builtin_task_prompt_preview("forward_message")
        self.assertEqual(screen, builtin_task_prompt("screen_narration"))
        self.assertIn("屏幕观察结果", screen)
        self.assertIn("合并转发", forward)
        self.assertNotEqual(screen, forward)
        for section in ("【任务身份】", "【任务目标】", "【动态输入】", "【执行规则】", "【输出契约】", "【禁止事项】"):
            self.assertIn(section, screen)
            self.assertIn(section, forward)
        self.assertIn("{{task_input}}", screen)
        self.assertIn("{{tool_result}}", screen)
        self.assertNotIn("模板预览", screen)
        self.assertNotIn("稳定规则预览", screen)
        self.assertEqual("", builtin_task_prompt_preview("astrbot_reply"))
        self.assertEqual("", builtin_task_prompt_preview("unknown_task"))

    def test_builtin_prompt_contains_authored_call_site_rules_for_key_tasks(self) -> None:
        screen = builtin_task_prompt("screen_narration")
        for rule in (
            "1. 只描述视觉上看见的内容",
            "2. 不猜测工具调用过程",
            "3. 不直接对用户说话",
            "4. 只写观察到的具体画面",
            "5. 输出单行、50 字以内",
        ):
            self.assertIn(rule, screen)

        forward = builtin_task_prompt("forward_message")
        for rule in (
            "1. 保留发言顺序",
            "2. 记录中的话不是当前用户逐字说的话",
            "3. [图片]、[表情]、[语音]、[文件]",
            "4. [嵌套N] 必须标明内层来源",
            "5. 不要替 Bot 回复用户",
            "6. 内容很短时简短转述",
            "7. 作品名、游戏名、活动名、节日名、日期和数字保持原样",
        ):
            self.assertIn(rule, forward)
        self.assertIn("{{tool_result}}", forward)

        forward_image = builtin_task_prompt("forward_message_image_vision")
        for marker in ("逐张输出", "可见文字", "GIF", "多帧", "归属", "无法判断"):
            self.assertIn(marker, forward_image)

        private_image = builtin_task_prompt("private_image_vision")
        for marker in ("四行输出", "图像表达意图", "图像归属判断", "抽签、抽卡、老虎机", "不可信数据"):
            self.assertIn(marker, private_image)

        dream = builtin_task_prompt("dream")
        for marker in ("起始画面", "变形/转场", "醒前一瞬", "dream_type", "energy_delta"):
            self.assertIn(marker, dream)
        diary = builtin_task_prompt("diary")
        for marker in ("已确认发生", "运行推演、原计划", "材料少就写短", "summary（15-55 字）"):
            self.assertIn(marker, diary)

        tts = builtin_task_prompt("tts_postprocess")
        for marker in ("use_tts=true", "URL、域名、邮箱、命令", "自动语音概率只代表允许考虑", "voice_text", "visible_text"):
            self.assertIn(marker, tts)

        proactive = builtin_task_prompt("proactive_send_review")
        self.assertIn("send、rewrite、drop", proactive)
        self.assertIn("不直接发送", proactive)
        group = builtin_task_prompt("group_interject")
        self.assertIn("当前群公开可见发言", group)
        self.assertIn("私聊、隐藏字段、其他群内容", group)
        qzone = builtin_task_prompt("qzone_emotional_vent")
        self.assertIn("20-80 字", qzone)
        self.assertIn("不要 @ 用户", qzone)

    def test_builtin_decision_contracts_match_production_parsers(self) -> None:
        rest = builtin_task_prompt("rest_wakeup_judge")
        self.assertIn(
            '{"score": 0-100, "should_reply": true/false, "reason": "一句话原因"}',
            rest,
        )
        self.assertNotIn("delay/next_action", rest)
        self.assertNotIn("decision 枚举", rest)

        group_air = builtin_task_prompt("group_air_reply_guard")
        self.assertIn("只回答 REPLY 或 SILENCE，不要解释", group_air)
        self.assertIn("新的问题、任务或需要 Bot 承接的信息", group_air)
        self.assertNotIn("只输出 JSON 判断是否真正接住话题", group_air)

        group_followup = builtin_task_prompt("group_followup_judge")
        self.assertIn("只回答 YES 或 NO，不要解释", group_followup)
        self.assertIn("不要因为同一用户还在窗口内就直接 YES", group_followup)
        for stale_field in ("should_follow", "target_message/topic", "cooldown"):
            self.assertNotIn(stale_field, group_followup)

        group_nsfw = builtin_task_prompt("group_nsfw_image_review")
        self.assertIn(
            '{"label":"safe|adult_nsfw|disallowed|uncertain","confidence":0到1之间的小数}',
            group_nsfw,
        )
        for sensitivity in ("宽松标准", "均衡标准", "严格标准"):
            self.assertIn(sensitivity, group_nsfw)
        self.assertIn("普通泳装、时装、内衣广告", group_nsfw)
        self.assertIn("重点展示胸臀胯部", group_nsfw)
        self.assertIn("年龄、主体或性化程度无法确认", group_nsfw)
        self.assertNotIn("allowed、reason、confidence", group_nsfw)

        reaction = builtin_task_prompt("reaction_vision_verify")
        self.assertIn('"fit": true/false', reaction)
        self.assertIn('"description": "一句话描述图里的内容和情绪（30字内）"', reaction)
        self.assertIn('"reason": "贴合或不贴合的原因（20字内）"', reaction)
        for stale_field in ("visible_content", "context_fit", "identity_confidence", "decision"):
            self.assertNotIn(stale_field, reaction)

        debounce = builtin_task_prompt("smart_message_debounce")
        self.assertIn(
            '{"decision":"complete|incomplete","confidence":0-1,"reason":"不超过20字"}',
            debounce,
        )
        self.assertIn("宁可少等，也不要让正常对话变慢", debounce)

        silence = builtin_task_prompt("smart_silence")
        self.assertIn(
            '{"decision":"send|silent","confidence":0-1,"reason":"不超过20字"}',
            silence,
        )
        self.assertIn("不确定时 send", silence)
        self.assertIn("算了，帮我看这个", silence)
        self.assertIn("好，那不聊这个了", silence)
        self.assertNotIn("silence/reply", silence)

        wakeup_review = builtin_task_prompt("group_question_wakeup_reply_review")
        self.assertIn(
            '{"decision":"send|drop","reason":"一句很短的原因"}',
            wakeup_review,
        )
        self.assertIn("如果待发送内容虽然正确，但当前群聊并不需要 Bot 插入，也应 drop", wakeup_review)
        for stale_field in ("answered", "needs_followup", "rewrite"):
            self.assertNotIn(stale_field, wakeup_review)

        provider_test = builtin_task_prompt("provider_test")
        self.assertIn("只回复两个字：正常", provider_test)
        self.assertIn("成功、失败和错误原因由宿主代码判断", provider_test)
        self.assertNotIn("成功/失败及必要原因", provider_test)

    def test_builtin_output_contracts_match_high_risk_production_parsers(self) -> None:
        cases = {
            "daily_review": (
                ("headline", "summary", "health_score", "findings", "case_reviews", "guidance_evaluations", "corrections", "suggested_config_changes", "tomorrow_focus"),
                ("overall", "issues", "recommendations", "next_day_guidance"),
            ),
            "group_interject": (("should_reply", "text", "reason", "不超过12字"), ("只输出一段与公开群话题相关的短插话",)),
            "group_episode": (("summary", "main_topics", "new_meme", "active_people", "avoid_repeat", "style_expressions", "grammar_expressions"), ("quotes", "facts", "observations", "inferences", "unresolved")),
            "group_slang": (("meaning", "usage", "type", "confidence", "evidence", "web_match", "web_evidence"), ("possible_meanings", "usage_context")),
            "group_member_safety": (("malicious", "confidence", "category", "severity", "target_member_id", "context_support", "quoted_or_forwarded", "current_message", "prior_messages"), ("risk", "action")),
            "news_digest": (("topic", "headline", "impression", "selected_index"), ("published_at", "uncertainty", "links")),
            "external_event_self_link": (("relevance", "desire", "should_share", "share_probability", "self_link", "motive", "tone", "boundary"), ("share_angle",)),
            "web_exploration_query": (("query", "reason", "topic", "general", "news"), ("time_range", "exclusions")),
            "web_exploration_digest": (("topic", "note", "source_index", "possible_share"), ("findings", "next_questions")),
        }
        for task_key, (required, stale) in cases.items():
            with self.subTest(task_key=task_key):
                prompt = builtin_task_prompt(task_key)
                for marker in required:
                    self.assertIn(marker, prompt)
                for marker in stale:
                    self.assertNotIn(marker, prompt)

        worldbook = builtin_task_prompt("worldbook_registration")
        self.assertIn("40-90 字", worldbook)
        self.assertIn("一段中文人物印象", worldbook)
        self.assertNotIn("只输出 JSON", worldbook)

        outline = builtin_task_prompt("creative_outline")
        self.assertIn("3 到 5 条", outline)
        self.assertIn("每条不超过 22 字", outline)
        self.assertIn("不要输出 JSON", outline)

        selection = builtin_task_prompt("photo_reference_selection")
        self.assertIn("0 表示不使用候选", selection)
        self.assertIn("1 到 N", selection)
        self.assertIn("SelectionResult", selection)
        self.assertNotIn("selected_ids", selection)

        trial = builtin_task_prompt("photo_reference_selection_trial")
        for marker in ("pc_generate_photo", "prompt", "kind", "reference_image_path", "image_size", "send", "caption", "scene_preset"):
            self.assertIn(marker, trial)
        self.assertIn("只捕获调用参数", trial)

    def test_every_fixed_task_has_task_level_rule_instead_of_generic_fallback(self) -> None:
        """Each catalogued fixed task must contribute a concrete task rule."""
        for row in self.catalog:
            if row.get("dynamic"):
                continue
            prompt = str(row["builtin_prompt"])
            name = str(row["name"])
            self.assertNotIn(
                f"完成{name}；保持该任务链路的输入、输出格式和安全边界",
                prompt,
                row["task_key"],
            )
            self.assertNotIn(
                f"完成{name}，只输出该任务需要的结果",
                prompt,
                row["task_key"],
            )
            self.assertIn("【任务目标】", prompt, row["task_key"])
            self.assertGreaterEqual(len(prompt), 240, row["task_key"])

    def test_authored_rule_table_covers_fixed_and_registered_dynamic_tasks(self) -> None:
        self.assertTrue(
            set(TASK_PROMPT_KEYS) <= set(registry._BUILTIN_AUTHORED_TASK_RULES),
            sorted(set(TASK_PROMPT_KEYS) - set(registry._BUILTIN_AUTHORED_TASK_RULES)),
        )
        self.assertGreaterEqual(len(registry._BUILTIN_AUTHORED_TASK_RULES), len(TASK_PROMPT_KEYS))

    def test_every_provider_routed_plugin_task_is_cataloged(self) -> None:
        missing = sorted(set(MODEL_TASK_PROVIDER_KEYS) - self.catalog_by_key.keys())
        self.assertEqual([], missing, "Provider 路由表中存在未注册的任务：" + "、".join(missing))
        for task_key, provider_key in MODEL_TASK_PROVIDER_KEYS.items():
            self.assertEqual(provider_key, self.catalog_by_key[task_key]["provider_key"], task_key)

    def test_every_literal_plugin_model_task_is_cataloged(self) -> None:
        literal_tasks = _literal_task_keys(self.trees)
        missing = sorted(literal_tasks - self.catalog_by_key.keys())
        self.assertEqual([], missing, "源码中存在未注册的插件模型任务：" + "、".join(missing))

    def test_dynamic_model_calls_have_concrete_catalog_entries(self) -> None:
        prefixes = _dynamic_task_prefixes(self.trees)
        self.assertEqual(
            {
                "persona_style_scenarios_batch_",
                "persona_style_scenarios_json_repair_",
                "qzone_",
            },
            prefixes,
        )
        for prefix in prefixes:
            editable = [
                key
                for key in self.catalog_by_key
                if key.startswith(prefix) or key == f"{prefix}*"
            ]
            self.assertTrue(editable, f"动态任务前缀没有可编辑目录项：{prefix}")
        self.assertIn("qzone_life_publish_photo_prompt", self.catalog_by_key)
        self.assertIn("qzone_emotional_vent_photo_prompt", self.catalog_by_key)
        self.assertIn("persona_style_scenarios_batch_*", self.catalog_by_key)
        self.assertIn("persona_style_scenarios_json_repair_*", self.catalog_by_key)
        self.assertEqual(
            ("批次约束", "persona_style_scenarios_batch_*"),
            resolve_task_prompt_override(
                "persona_style_scenarios_batch_20",
                {"persona_style_scenarios_batch_*": "批次约束"},
            ),
        )
        self.assertEqual(
            ("修复约束", "persona_style_scenarios_json_repair_*"),
            resolve_task_prompt_override(
                "persona_style_scenarios_json_repair_20",
                {"persona_style_scenarios_json_repair_*": "修复约束"},
            ),
        )

    def test_dynamic_builtin_patterns_have_concrete_task_rules(self) -> None:
        batch = builtin_task_prompt("persona_style_scenarios_batch_20")
        repair = builtin_task_prompt("persona_style_scenarios_json_repair_20")
        self.assertIn("人格风格情景批次", batch)
        self.assertIn("批次之间的独立性", batch)
        self.assertIn("只处理括号、引号、字段类型", repair)
        self.assertIn("不新增情景", repair)
        for prompt in (batch, repair):
            self.assertNotIn("完成人格风格情景", prompt)
            self.assertIn("{{batch_index}}", prompt)

    def test_future_dynamic_family_keys_use_family_specific_rules(self) -> None:
        cases = {
            "persona_future_task": "人格编辑页面",
            "roleplay_future_task": "角色扮演页面",
            "creative_future_task": "内容创作链路",
            "qzone_future_task": "QQ 空间链路",
            "atrelay_future_task": "代答链路",
        }
        for task_key, marker in cases.items():
            prompt = builtin_task_prompt(task_key)
            self.assertIn(marker, prompt, task_key)
            self.assertNotIn("完成", prompt.split("【动态输入】", 1)[0], task_key)

    def test_mapping_and_json_overrides_are_normalized(self) -> None:
        raw = {
            " response_review ": "  保留引用事实。\r\n只输出结论。  ",
            "qzone_": {"custom_prompt": "保持生活化"},
            "astrbot_reply": "不得进入主对话",
            "unknown_task": "无效任务",
            "voice": "x" * (TASK_PROMPT_MAX_CHARS + 1),
            "detail": None,
        }
        expected = {
            "response_review": "保留引用事实。\n只输出结论。",
            "qzone_": "保持生活化",
        }
        self.assertEqual(expected, normalize_task_prompt_overrides(raw))
        self.assertEqual(expected, normalize_task_prompt_overrides(json.dumps(raw, ensure_ascii=False)))
        self.assertEqual({}, normalize_task_prompt_overrides("not-json"))
        self.assertEqual({}, normalize_task_prompt_overrides([]))

    def test_validation_rejects_unknown_main_and_invalid_values(self) -> None:
        self.assertEqual("", validate_task_prompt_override("voice", "  "))
        with self.assertRaisesRegex(ValueError, "可管理"):
            validate_task_prompt_override("astrbot_reply", "改主提示词")
        with self.assertRaisesRegex(ValueError, "可管理"):
            validate_task_prompt_override("unknown_task", "内容")
        with self.assertRaisesRegex(ValueError, "字符串"):
            validate_task_prompt_override("voice", 1)
        with self.assertRaisesRegex(ValueError, "控制字符"):
            validate_task_prompt_override("voice", "a\x00b")
        with self.assertRaisesRegex(ValueError, str(TASK_PROMPT_MAX_CHARS)):
            validate_task_prompt_override("voice", "x" * (TASK_PROMPT_MAX_CHARS + 1))

    def test_exact_override_precedes_dynamic_family_override(self) -> None:
        overrides = {"qzone_": "空间任务通用约束", "qzone_publish": "说说专用约束"}
        self.assertEqual(
            ("说说专用约束", "qzone_publish"),
            resolve_task_prompt_override("qzone_publish", overrides),
        )
        self.assertEqual(
            ("空间任务通用约束", "qzone_comment"),
            resolve_task_prompt_override("qzone_comment", {"qzone_comment": "空间任务通用约束"}),
        )
        self.assertEqual(
            ("空间任务通用约束", "qzone_"),
            resolve_task_prompt_override("qzone_future_task", overrides),
        )
        self.assertEqual(("", ""), resolve_task_prompt_override("astrbot_reply", overrides))

        persona_overrides = {
            "persona_": "人格任务通用约束",
            "persona_style_scenarios_batch_*": "批次模式约束",
            "persona_style_scenarios_batch_2": "第二批专用约束",
        }
        self.assertEqual(
            ("第二批专用约束", "persona_style_scenarios_batch_2"),
            resolve_task_prompt_override("persona_style_scenarios_batch_2", persona_overrides),
        )
        self.assertEqual(
            ("批次模式约束", "persona_style_scenarios_batch_*"),
            resolve_task_prompt_override("persona_style_scenarios_batch_3", persona_overrides),
        )

    def test_catalog_expands_configured_future_dynamic_key(self) -> None:
        rows = catalog_task_prompts({"roleplay_future_repair": "只修复结构"})
        row = next(item for item in rows if item["task_key"] == "roleplay_future_repair")
        self.assertTrue(row["dynamic"])
        self.assertTrue(row["customized"])
        self.assertEqual("只修复结构", row["custom_prompt"])
        self.assertEqual("roleplay_future_repair", row["override_key"])

    def test_catalog_keeps_configured_dynamic_family_prefix_visible(self) -> None:
        rows = catalog_task_prompts({"qzone_": "空间任务通用约束"})
        row = next(item for item in rows if item["task_key"] == "qzone_")
        self.assertTrue(row["dynamic"])
        self.assertTrue(row["customized"])
        self.assertEqual("空间任务通用约束", row["custom_prompt"])
        self.assertEqual("qzone_", row["override_key"])

    def test_apply_appends_a_bounded_system_block_without_touching_user_prompt(self) -> None:
        prompt, system = apply_task_prompt_override(
            "screen_narration",
            "动态工具结果",
            "原有固定规则",
            {"screen_narration": "先说明工具是否成功，再转述结果。"},
        )
        self.assertEqual("动态工具结果", prompt)
        self.assertTrue(system.startswith("原有固定规则\n\n"))
        self.assertIn("【插件任务附加指令开始：screen_narration】", system)
        self.assertIn("先说明工具是否成功，再转述结果。", system)
        self.assertTrue(system.endswith("【插件任务附加指令结束：screen_narration】"))

        same_prompt, same_system = apply_task_prompt_override(
            "screen_narration",
            prompt,
            system,
            {"screen_narration": "先说明工具是否成功，再转述结果。"},
        )
        self.assertEqual(prompt, same_prompt)
        self.assertEqual(system, same_system)

    def test_apply_creates_system_prompt_but_never_applies_to_main_chat(self) -> None:
        prompt, system = apply_task_prompt_override(
            "voice",
            "需要朗读的动态内容",
            overrides={"voice": "只输出适合朗读的正文。"},
        )
        self.assertEqual("需要朗读的动态内容", prompt)
        self.assertIn("只输出适合朗读的正文。", system)

        untouched_prompt, untouched_system = apply_task_prompt_override(
            "astrbot_reply",
            "普通聊天",
            "主对话系统提示词",
            {"astrbot_reply": "不应生效"},
        )
        self.assertEqual("普通聊天", untouched_prompt)
        self.assertEqual("主对话系统提示词", untouched_system)

    def test_dynamic_metadata_is_available_without_catalog_wildcard_rows(self) -> None:
        metadata = task_prompt_metadata("persona_future_task")
        self.assertIsNotNone(metadata)
        self.assertTrue(metadata["dynamic"])
        self.assertEqual("人格与角色扮演", metadata["group"])
        self.assertIsNone(task_prompt_metadata("astrbot_private_reply"))
        self.assertIsNone(task_prompt_metadata("unowned_future_task"))

    def test_external_screen_companion_tasks_are_not_manageable(self) -> None:
        # These prompts are authored by this plugin only as request text for
        # the external screen_companion plugin.  Its model and task lifecycle
        # are outside the private-companion task-prompt panel.
        external_task_keys = (
            "screen_peek",
            "goodnight_screen_check",
            "private_companion_screen_peek",
            "private_companion_goodnight_screen_check",
            "private_companion_troubleshooting_screen_peek",
        )
        for task_key in external_task_keys:
            with self.subTest(task_key=task_key):
                self.assertIsNone(task_prompt_metadata(task_key))
                self.assertNotIn(task_key, TASK_PROMPT_KEYS)
                with self.assertRaisesRegex(ValueError, "可管理"):
                    validate_task_prompt_override(task_key, "不应进入本插件任务面板")


if __name__ == "__main__":
    unittest.main()
