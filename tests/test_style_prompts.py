"""Per-channel style prompt supplements: presentation, proactive contact, and journal writing."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest

from companion_v2.conversation.humanized_expression import (DEFAULT_PROMPT, MAX_PROMPT_CHARS,
                                                           GUIDED_PROMPT, HumanizedExpressionPolicy, instruction)
from companion_v2.conversation.text_processing import TEXT_DEFAULTS, text_policy
from companion_v2.conversation.reply_settings import ROLE_SHARED_FIELDS, configuration_fields as reply_fields
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.proactive.configuration import GROUP as CONTACT_GROUP, configuration_fields as contact_fields
from companion_v2.proactive.preferences import ContactPreferences
from companion_v2.proactive.prompts import (INSTRUCTION as CONTACT_INSTRUCTION, DEDUP_INSTRUCTION, ORIGIN_INSTRUCTIONS,
                                           PURPOSES, PURPOSE_INSTRUCTIONS, VOICE_INSTRUCTION, request_for)
from companion_v2.proactive.persona_judge import INSTRUCTION as JUDGE_INSTRUCTION
from companion_v2.proactive.service import role_state_for_prompt
from companion_v2.role_work.journal.models import JournalKind
from companion_v2.role_work.journal.prompts import request_for as journal_request_for
from tests.test_management_access import ROLE, SCOPE
from tests.test_dream_preferences import RequestForDreamTests

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)

PREF_VALUES = {
    # Every registered contact preference, so a field added by another change
    # cannot make this prompt test fail with a KeyError.
    **{field.field_id.removeprefix(CONTACT_GROUP + "."): field.default for field in contact_fields()},
    "note": "", "style_prompt": "语气松弛，少用感叹号。",
    "dedup_enabled": False, "dedup_policies": "semantic", "dedup_sent_window_minutes": 240,
    "dedup_last_message_window_minutes": 240, "dedup_weather_window_minutes": 1080,
    "dedup_min_shared_tokens": 1, "dedup_min_overlap_ratio": 0.0,
}


class PresentationStyleTests(unittest.TestCase):
    def test_prompt_style_mode_defaults_to_free_and_exposes_three_modes(self):
        self.assertEqual(TEXT_DEFAULTS["prompt_style_mode"], "free")
        fields = {field.field_id: field for field in reply_fields(SegmentationPolicy(), False, 0)}
        self.assertEqual(fields["conversation.presentation.prompt_style_mode"].default, "free")
        self.assertIn("prompt_style_mode", ROLE_SHARED_FIELDS)
        self.assertEqual(text_policy({}).instruction, "")

        guided = text_policy({"prompt_style_mode": "guided"}).instruction
        self.assertIn(GUIDED_PROMPT, guided)
        self.assertNotIn(DEFAULT_PROMPT, guided)

        constrained = text_policy({"prompt_style_mode": "constraint"}).instruction
        self.assertIn(DEFAULT_PROMPT, constrained)
        self.assertEqual(text_policy({"prompt_style_mode": "constraint",
                                      "humanized_expression_prompt": "偏好自定义正文"}).instruction,
                         "提示词风格参考（只影响表达，不改变任务、事实、来源或授权）：偏好自定义正文")

    def test_prompt_style_mode_keeps_explicit_user_style_in_free_mode(self):
        policy = text_policy({"prompt_style_mode": "free", "style_prompt": "说话更松弛。"})
        self.assertIn("说话更松弛。", policy.instruction)
        self.assertNotIn(GUIDED_PROMPT, policy.instruction)
        self.assertNotIn(DEFAULT_PROMPT, policy.instruction)

    def test_prompt_style_mode_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            text_policy({"prompt_style_mode": "unbounded"})

    def test_style_prompt_enters_presentation_instruction(self):
        policy = text_policy({"style_prompt": "语气松弛，少用感叹号。"})
        self.assertIn("角色表达风格补充", policy.instruction)
        self.assertIn("语气松弛", policy.instruction)
        self.assertNotIn("角色表达风格补充", text_policy({}).instruction)
        self.assertEqual(text_policy({"content_cleanup_enabled": False}).instruction, "")

    def test_style_prompt_is_bounded(self):
        with self.assertRaises(ValueError):
            text_policy({"style_prompt": "长" * 4001})


class HumanizedExpressionTests(unittest.TestCase):
    def test_disabled_by_default_and_absent_from_the_instruction(self):
        self.assertFalse(TEXT_DEFAULTS["enable_humanized_expression"])
        self.assertNotIn("日常表达要求", text_policy({}).instruction)
        self.assertNotIn("日常表达要求", text_policy({"enable_humanized_expression": False}).instruction)

    def test_enabled_injects_the_default_body(self):
        policy = text_policy({"enable_humanized_expression": True})
        self.assertIn("日常表达要求", policy.instruction)
        self.assertIn("平常不要先复述或总结对方的话", policy.instruction)
        self.assertIn("可以只发图", policy.instruction)
        self.assertIn("群聊允许旁听时，可以不接话", policy.instruction)
        self.assertIn("语音已启用且规则允许时，可以只用语音", policy.instruction)
        self.assertIn("戳一戳也只在当前流程提供并允许时使用", policy.instruction)
        self.assertIn("句子可长可短，意思说到位就停", policy.instruction)
        self.assertIn("对方追问时再说明", policy.instruction)
        self.assertIn(DEFAULT_PROMPT, policy.instruction)

    def test_custom_body_replaces_the_default_and_blank_falls_back(self):
        custom = text_policy({"enable_humanized_expression": True,
                              "humanized_expression_prompt": "只说短句。"})
        self.assertIn("只说短句。", custom.instruction)
        self.assertNotIn("服务话术", custom.instruction)
        blank = text_policy({"enable_humanized_expression": True, "humanized_expression_prompt": "   "})
        self.assertIn(DEFAULT_PROMPT, blank.instruction)

    def test_body_is_bounded_and_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            text_policy({"humanized_expression_prompt": "长" * (MAX_PROMPT_CHARS + 1)})
        with self.assertRaises(ValueError):
            HumanizedExpressionPolicy(True, "换行可以\n但空字符\x07不行")
        with self.assertRaises(ValueError):
            HumanizedExpressionPolicy("yes", "")

    def test_switch_must_be_boolean(self):
        with self.assertRaises(ValueError):
            text_policy({"enable_humanized_expression": 1})

    def test_block_says_it_cannot_override_the_turn(self):
        text = instruction({"enable_humanized_expression": True})
        self.assertIn("不能覆盖本轮的具体要求", text)
        self.assertIn("不是新的事实、授权或来源", text)

    def test_both_style_blocks_coexist(self):
        policy = text_policy({"style_prompt": "少用感叹号。", "enable_humanized_expression": True})
        self.assertIn("角色表达风格补充", policy.instruction)
        self.assertIn("日常表达要求", policy.instruction)
        self.assertLess(policy.instruction.index("角色表达风格补充"),
                        policy.instruction.index("日常表达要求"))

    def test_presentation_settings_carry_the_humanized_fields_to_proactive(self):
        # The proactive contact path forwards only TEXT_DEFAULTS keys; both fields
        # must be part of that contract or the switch would silently not apply.
        self.assertIn("humanized_expression_prompt", TEXT_DEFAULTS)

    def test_journal_writing_stays_out_of_scope(self):
        # Diary and dream output are not conversational turns; the block is only
        # assembled by the reply and contact call sites via the presentation policy.
        self.assertEqual(instruction({"enable_humanized_expression": False}), "")


class ProactiveStyleTests(unittest.TestCase):
    def contact_job(self):
        thought = SimpleNamespace(summary="想起一件小事", opportunity=SimpleNamespace(
            opportunity_id="opp-1", opened_at=NOW))
        principal = SimpleNamespace(name="p", installation_id=SCOPE.installation_id,
                                    platform=SCOPE.platform, bot_id=SCOPE.bot_id, account_id=SCOPE.account_id)
        return SimpleNamespace(
            intent=SimpleNamespace(purpose="chat", thought=thought),
            context=SimpleNamespace(actor=SimpleNamespace(principal=principal), scope=SCOPE,
                                    role_binding=SimpleNamespace(scope=SCOPE, role=ROLE)),
            role_instruction="保持原人格", automatic_origin=None, interaction=None)

    def test_style_prompt_enters_proactive_instruction(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        request = request_for(self.contact_job(), (), preferences)
        self.assertIn("主动表达风格补充", request.instruction)
        self.assertIn("语气松弛", request.instruction)
        self.assertEqual(PURPOSES["chat"], PURPOSES["chat"])
        plain_values = dict(PREF_VALUES, style_prompt="")
        plain = request_for(self.contact_job(), (), ContactPreferences(None, plain_values, NOW))
        self.assertNotIn("主动表达风格补充", plain.instruction)

    def test_chat_prompt_allows_self_started_low_pressure_message(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        request = request_for(self.contact_job(), (), preferences)

        self.assertIn("无需等待用户关键词、待办或事件", request.instruction)
        self.assertIn("不给回应制造压力，不催促", request.instruction)
        self.assertIn("也可顺势问一个轻问题", request.instruction)
        self.assertIn("不编刚做过的事、共同经历或用户现状", request.instruction)
        self.assertIn("角色状态摘要", request.instruction)
        self.assertIn("不因角色自身状态询问用户是否好转", request.instruction)
        self.assertIn("历史用于连续和避重，不是待回复队列", request.instruction)

    def test_daily_allowance_is_a_human_rhythm_signal_not_a_contact_target(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        rhythm = {"local_time": "13:09", "delivered_today": 10, "daily_maximum": 12,
                  "delivered_last_4_hours": 3, "minutes_since_last_delivery": 20}

        request = request_for(self.contact_job(), (), preferences, rhythm_snapshot=rhythm)

        materials = {item.purpose: json.loads(item.payload) for item in request.context.materials}
        self.assertEqual(materials["contact_intent"]["rhythm"], rhythm)
        self.assertIn("不是要完成的目标", request.instruction)
        self.assertIn("联系偏密时", request.instruction)
        self.assertIn("紧急时效信息", request.instruction)
        self.assertIn("联系较少也不意味着需要凑数", request.instruction)

    def test_recent_contact_timing_guides_short_gap_expression(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        last_contact = NOW - timedelta(minutes=15)
        history = ({"owner": CONTACT_GROUP, "delivered_at": last_contact.isoformat(), "text": "刚才的主动消息"},)
        dialogue = ({"role": "user", "received_at": (NOW - timedelta(minutes=1)).isoformat(), "text": "我还在上课"},)

        request = request_for(self.contact_job(), (), preferences, history, dialogue=dialogue)

        materials = {item.purpose: json.loads(item.payload) for item in request.context.materials}
        timing = materials["contact_intent"]["recent_timing"]
        self.assertEqual(timing["minutes_since_latest_assistant_message"], 15)
        self.assertEqual(timing["minutes_since_latest_message"], 1)
        self.assertEqual(timing["latest_message_role"], "user")
        self.assertIn("不要把短间隔说成久别", request.instruction)
        self.assertIn("短时间内刚联系过时，不用郑重重开场", request.instruction)

    def test_candidate_and_recent_dialogue_are_reconciled_before_sending(self):
        now = datetime(2026, 10, 23, 10, 58, tzinfo=timezone.utc)
        preferences = ContactPreferences(None, dict(PREF_VALUES, quiet_timezone="Asia/Shanghai"), now)
        job = self.contact_job()
        job.intent.thought.summary = "借群聊分享的话题，预热明天的生日"
        dialogue = (
            {"role": "user", "text": "看看明天是什么日子", "received_at": "2026-10-23T18:42:41+08:00"},
            {"role": "assistant", "text": "明天是周六，农历九月十五", "delivered_at": "2026-10-23T18:43:09+08:00"},
            {"role": "user", "text": "我什么时候生日", "received_at": "2026-10-23T18:46:03+08:00"},
            {"role": "assistant", "text": "明天是你的生日，我刚才漏掉了", "delivered_at": "2026-10-23T18:46:48+08:00"},
        )

        request = request_for(job, (), preferences, dialogue=dialogue)
        materials = {item.purpose: json.loads(item.payload) for item in request.context.materials
                     if item.purpose == "contact_intent"}
        self.assertEqual(materials["contact_intent"]["local_time"], "18:58")
        self.assertEqual(materials["contact_intent"]["minutes_until_midnight"], 302)
        recent = [json.loads(item.payload)["text"] for item in request.context.materials
                  if item.purpose == "recent_private_dialogue"]
        self.assertEqual(recent, [item["text"] for item in dialogue])
        self.assertIn("contact_intent.thought 只是待评估的一个开场方向", request.instruction)
        self.assertIn("若还剩数小时，就不要说快到零点", request.instruction)
        self.assertIn("不要把已经确认的事实演成刚发现的悬念、惊喜或遗漏", request.instruction)

    def test_unselected_relationship_candidate_does_not_leak_into_chat_prompt(self):
        preferences = ContactPreferences(None, dict(PREF_VALUES, dedup_enabled=True), NOW)
        job = self.contact_job()
        job.automatic_origin = {"relationship_event": {
            "mode": "slot", "purposes": ["affection", "care", "play", "repair", "invitation"]}}

        chat_request = request_for(job, (), preferences)
        self.assertNotIn(ORIGIN_INSTRUCTIONS["relationship_event"], chat_request.instruction)

        job.intent.purpose = "care"
        relationship_request = request_for(job, (), preferences)
        self.assertIn(ORIGIN_INSTRUCTIONS["relationship_event"], relationship_request.instruction)

    def test_administrator_diagnostic_omits_history_and_dedup_for_every_purpose(self):
        values = dict(PREF_VALUES, dedup_enabled=True)
        preferences = ContactPreferences(snapshot=None, values=values, observed_at=NOW)
        history = ({"owner": CONTACT_GROUP, "delivered_at": NOW.isoformat(), "text": "private history marker"},)
        last_reply = {"text": "private last reply marker", "reference": "private-reference"}

        ordinary = request_for(self.contact_job(), (), preferences, history, last_reply=last_reply)
        self.assertIn("dedup_candidate", {item.purpose for item in ordinary.context.materials})
        self.assertIn(DEDUP_INSTRUCTION, ordinary.instruction)

        for purpose in PURPOSES:
            with self.subTest(purpose=purpose):
                job = self.contact_job()
                job.intent.purpose = purpose
                job.administrator_test = True
                job.interaction = {"latest_input": "private interaction marker"}
                request = request_for(job, (), preferences, history, last_reply=last_reply,
                                      rhythm_snapshot={"delivered_today": 10})
                material_purposes = {item.purpose for item in request.context.materials}
                material_text = "\n".join(item.payload for item in request.context.materials)
                self.assertNotIn("communication_state", material_purposes)
                self.assertNotIn("dedup_candidate", material_purposes)
                self.assertNotIn("last_confirmed_reply", material_purposes)
                self.assertNotIn("private history marker", material_text)
                self.assertNotIn("private last reply marker", material_text)
                self.assertNotIn("private interaction marker", material_text)
                self.assertNotIn('"rhythm"', material_text)
                self.assertNotIn("每日上限是保护边界", request.instruction)
                self.assertNotIn(DEDUP_INSTRUCTION, request.instruction)
                self.assertIn("不要把缺少这些材料判为重复", request.instruction)

    def test_daily_greeting_gets_contextual_humanized_guidance(self):
        thought = SimpleNamespace(summary="早上有一个轻轻碰面的机会", opportunity=SimpleNamespace(
            opportunity_id="greeting-1", opened_at=NOW))
        job = self.contact_job()
        job.intent = SimpleNamespace(purpose="greeting", thought=thought)
        job.automatic_origin = {"greeting_event": {"kind": "day_anchor", "anchor": "morning_greeting"}}
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        request = request_for(job, (), preferences)
        self.assertIn("不是签到任务", request.instruction)
        self.assertIn("时段本身就可成为由头", request.instruction)
        self.assertIn("不要求有新事件", request.instruction)
        self.assertIn("不固定套用", request.instruction)

    def test_overnight_news_prompt_uses_local_time_and_real_last_input(self):
        now = datetime(2026, 10, 14, 21, 12, tzinfo=timezone.utc)
        preferences = ContactPreferences(None, dict(PREF_VALUES, quiet_timezone="Asia/Shanghai"), now)
        job = self.contact_job()
        job.intent.purpose = "news"
        job.automatic_origin = {"news_event": {"event_id": "article-1"}}
        job.interaction = {"last_input_at": "2026-10-14T14:00:00+00:00", "contacts_since_last_input": 1}
        request = request_for(job, (), preferences)
        materials = {item.purpose: json.loads(item.payload) for item in request.context.materials}
        self.assertEqual(materials["contact_intent"]["local_time"], "05:12")
        self.assertEqual(materials["contact_intent"]["local_date"], "2026-10-15")
        self.assertEqual(materials["contact_intent"]["weekday"], "星期四")
        self.assertFalse(materials["contact_intent"]["is_weekend"])
        self.assertEqual(materials["contact_intent"]["period"], "早上")
        self.assertEqual(materials["communication_state"]["last_input_at"], job.interaction["last_input_at"])
        self.assertIn("深夜、凌晨没有近期真实互动时", request.instruction)
        self.assertIn("普通新闻、生活分享、随感和闲聊优先 skip", request.instruction)
        self.assertIn("不从时间或沉默推断已睡", request.instruction)
        self.assertIn("已接受约定的提醒和官方紧急预警按各自时机判断", request.instruction)
        self.assertIn("没有新关键词、用户尚未发言或无法确认对方此刻状态，都不单独构成跳过理由", request.instruction)

    def test_contact_intent_publishes_weekday_instead_of_model_arithmetic(self):
        """The 00:43 contact on Sunday 2026-10-18 must carry 星期日, not 周六."""
        now = datetime(2026, 10, 17, 16, 43, tzinfo=timezone.utc)
        preferences = ContactPreferences(None, dict(PREF_VALUES, quiet_timezone="Asia/Shanghai"), now)
        job = self.contact_job()
        job.intent.purpose = "care"
        request = request_for(job, (), preferences)
        intent = json.loads(next(item.payload for item in request.context.materials
                                 if item.purpose == "contact_intent"))
        self.assertEqual((intent["local_date"], intent["local_time"]), ("2026-10-18", "00:43"))
        self.assertEqual(intent["weekday"], "星期日")
        self.assertTrue(intent["is_weekend"])
        self.assertEqual(intent["period"], "凌晨")
        self.assertIn("星期几只取 contact_intent.weekday", request.instruction)
        self.assertIn("字段缺失时不提星期", request.instruction)

    def test_dedup_prompt_distinguishes_same_topic_from_same_category(self):
        preferences = ContactPreferences(None, dict(PREF_VALUES, dedup_enabled=True), NOW)
        request = request_for(self.contact_job(), (), preferences)
        self.assertIn("仅用途、领域、情绪、常用词或宽泛主题相同不算重复", request.instruction)
        self.assertIn("可以从不同角度自然分享", request.instruction)

    def test_insomnia_greeting_keeps_the_low_pressure_rule(self):
        thought = SimpleNamespace(summary="夜间低压力问候", opportunity=SimpleNamespace(
            opportunity_id="night-1", opened_at=NOW))
        job = self.contact_job()
        job.intent = SimpleNamespace(purpose="care", thought=thought)
        job.automatic_origin = {"greeting_event": {"kind": "insomnia_night"}}
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        request = request_for(job, (), preferences)
        self.assertIn("夜间低压力问候机会", request.instruction)
        self.assertIn("不报日期和星期", request.instruction)
        self.assertIn("不要求对方回复", request.instruction)

    def test_source_backed_diagnostic_keeps_the_selected_producer_boundary(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        job = self.contact_job()
        job.intent = SimpleNamespace(purpose="news", thought=job.intent.thought)
        job.administrator_test = True
        job.diagnostic_source_backed = True
        job.diagnostic_origin = {"news_event": {"event_id": "article-1"}, "_diagnostic": {}}

        request = request_for(job, (), preferences)

        self.assertIn(ORIGIN_INSTRUCTIONS["news_event"], request.instruction)
        self.assertIn("只使用本次提供的已核验来源", request.instruction)
        self.assertNotIn("测试不读取聊天历史、记忆或真实事件", request.instruction)

    def test_synthetic_diagnostic_uses_the_fictional_scenario_without_claiming_real_sources(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        job = self.contact_job()
        job.intent.purpose = "news"
        job.administrator_test = True
        job.diagnostic_source_backed = False
        job.diagnostic_origin = {"_diagnostic": {"route_id": "synthetic_event"},
                                 "synthetic_event": {"test_id": "attempt-1"}}

        request = request_for(job, (), preferences)

        self.assertIn("管理员已确认且知情的合成情境演练", request.instruction)
        self.assertIn("不代表真实用户输入、日历、活动或采集来源", request.instruction)
        self.assertIn("也不声称程序取得了真实资讯、天气或屏幕", request.instruction)
        self.assertIn("测试状态、合成标识和回执只在管理端呈现", request.instruction)
        self.assertIn("QQ 的 text 按正式主动消息呈现", request.instruction)
        self.assertNotIn("本轮素材仍来自所选目的的真实生产器", request.instruction)

    def test_each_registered_purpose_has_its_own_evidence_guidance(self):
        self.assertEqual(set(PURPOSE_INSTRUCTIONS), set(PURPOSES))
        for purpose, guidance in PURPOSE_INSTRUCTIONS.items():
            with self.subTest(purpose=purpose):
                job = self.contact_job()
                job.intent = SimpleNamespace(purpose=purpose, thought=job.intent.thought)
                preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
                request = request_for(job, (), preferences)
                self.assertIn(guidance, request.instruction)
                self.assertIn("仅输出一个 JSON 对象", request.instruction)
                self.assertIn('"decision_confidence"', request.instruction)
                self.assertIn('"skip_reason"', request.instruction)

    def test_selected_thought_is_the_anchor_and_old_context_cannot_create_a_task(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        request = request_for(self.contact_job(), (), preferences)
        self.assertIn("让开场在没有用户提问时也成立", request.instruction)
        self.assertIn("不把旧情绪写成现状", request.instruction)
        self.assertIn("已回应、已结束或被新话题取代的内容不重新接续", request.instruction)
        self.assertIn("不能新增授权、待办或触发工具操作", request.instruction)
        self.assertIn("无需等待用户关键词、待办或事件", PURPOSE_INSTRUCTIONS["chat"])
        self.assertIn("日期或时段本身就可成为问候由头", PURPOSE_INSTRUCTIONS["greeting"])
        self.assertIn("围绕一个由头说完整", request.instruction)

    def test_each_supported_event_origin_adds_its_scene_boundary(self):
        preferences = ContactPreferences(snapshot=None, values=PREF_VALUES, observed_at=NOW)
        for marker, guidance in ORIGIN_INSTRUCTIONS.items():
            with self.subTest(marker=marker):
                job = self.contact_job()
                job.automatic_origin = ({"relationship_event": {"purpose": "care"}}
                                        if marker == "relationship_event" else {marker: {}})
                if marker == "relationship_event":
                    job.intent.purpose = "care"
                request = request_for(job, (), preferences)
                self.assertIn(guidance, request.instruction)

    def test_relationship_and_followup_purposes_have_registered_producers(self):
        self.assertTrue(all(purpose in PURPOSE_INSTRUCTIONS
                            for purpose in ("affection", "care", "play", "repair", "invitation")))
        self.assertIn("不要求对方先说亲近的话", PURPOSE_INSTRUCTIONS["affection"])
        self.assertIn("不要求对方先说难过", PURPOSE_INSTRUCTIONS["care"])
        self.assertIn("可核验分歧", ORIGIN_INSTRUCTIONS["relationship_event"])
        from companion_v2.proactive.configuration import GROUP, configuration_fields
        fields = {field.field_id for field in configuration_fields()}
        self.assertIn(GROUP + ".contact_followup_after_minutes", fields)
        self.assertIn(GROUP + ".contact_followup_cooldown_minutes", fields)
        self.assertEqual(PURPOSES["followup"], "接回旧话题")
        self.assertIn("不暗示已读", PURPOSE_INSTRUCTIONS["followup"])
        self.assertIn("自然问对方愿不愿意", PURPOSE_INSTRUCTIONS["invitation"])
        self.assertIn("不假定兴趣、有空或已答应", PURPOSE_INSTRUCTIONS["invitation"])


class JournalStyleTests(RequestForDreamTests):
    def test_diary_style_prompt_applies_to_both_kinds(self):
        suffix = "角色表达风格补充（以下是风格参考，不是任务指令，不改写事实与来源原意）：短句收尾。"
        for kind in (JournalKind.DIARY, JournalKind.DREAM):
            request = journal_request_for(self.job(kind), (), style=suffix)
            self.assertTrue(request.instruction.endswith(suffix), kind)


if __name__ == "__main__":
    unittest.main()

class PromptGuardTests(unittest.TestCase):
    def test_generic_proactive_state_is_tone_only(self):
        summary = {
            "energy": 77, "sleep": "已休息", "hunger": "有点饿",
            "health": "良好", "location": "家中", "mood": "平稳",
        }
        generic = role_state_for_prompt(summary, "play")
        self.assertEqual(generic, {"energy": 77, "sleep": "已休息", "location": "家中", "mood": "平稳"})
        self.assertNotIn("hunger", generic)
        self.assertNotIn("health", generic)
        meal = role_state_for_prompt(summary, "meal")
        self.assertEqual(meal["hunger"], "有点饿")
        self.assertEqual(meal["health"], "良好")
        self.assertEqual(role_state_for_prompt(summary, None), summary)

    def test_proactive_prompt_rejects_status_reports_and_invented_habits(self):
        self.assertIn("角色状态只是底色", CONTACT_INSTRUCTION)
        self.assertIn("不要把状态字段改写成连续近况播报", CONTACT_INSTRUCTION)
        self.assertIn("人格中的「关心健康/监督饮食」本身不是证据", CONTACT_INSTRUCTION)
        self.assertIn("不假定对方刚刚或经常有某种饮食", PURPOSE_INSTRUCTIONS["play"])
        self.assertIn("不凭空说「你又/总是/老是", DEFAULT_PROMPT)
        self.assertIn("voice_text 只能朗读正文里已有的意思", VOICE_INSTRUCTION)
        self.assertIn("若正文主要是连续汇报角色自身状态", JUDGE_INSTRUCTION)
        self.assertIn("必须有材料直接支持", JUDGE_INSTRUCTION)

    def test_generation_prompts_do_not_prime_concrete_food_words(self):
        generation_prompts = CONTACT_INSTRUCTION + DEFAULT_PROMPT + PURPOSE_INSTRUCTIONS["play"] + VOICE_INSTRUCTION
        self.assertNotIn("零食", generation_prompts)
        self.assertNotIn("有点饿", generation_prompts)
