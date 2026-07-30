import asyncio
import json
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _PersonaManager:
    async def get_default_persona_v3(self, umo=""):
        await asyncio.sleep(0)
        return {"prompt": f"persona-for:{umo}"}


class _PersonaCacheHarness(DailyStateMixin):
    def __init__(self):
        self.plugin_specific_persona_id = ""
        self.context = SimpleNamespace(persona_manager=_PersonaManager())
        self._default_persona_prompt_cache = ""
        self._default_persona_prompt_cache_at = 0.0
        self._default_persona_prompt_cache_umo = ""
        self._default_persona_prompt_cache_persona_id = ""
        self._default_persona_prompt_cache_by_scope = {}


class _ProactivePersonaHarness(ProactiveMessageMixin, DailyStateMixin):
    schedule_persona_prompt = "高一学生"
    schedule_worldview_prompt = "现代校园"

    def __init__(self):
        self.enable_proactive_message_review = True
        self.enable_passive_response_review = False
        self.enable_response_self_review = False
        self.proactive_review_mode = "full"
        self.response_review_provider_id = "review"
        self.mai_style_provider_id = "style"
        self.persona_proactive_voice_prompt = "开头常用：嗯…\n保持克制但有一点俏皮。"
        self.captured_prompt = ""
        self.resolved_sessions = []

    async def _refresh_default_persona_prompt(self, umo=""):
        self.resolved_sessions.append(umo)
        return f"完整人格::{umo}::冷静、俏皮、称呼稳定"

    def _get_default_persona_prompt(self, umo=""):
        return f"fallback::{umo}"

    def _local_proactive_send_decision(self, *args, **kwargs):
        return {"decision": "send", "reason": "本地通过"}

    def _normalize_proactive_review_decision_policy(self, user, payload, **kwargs):
        return dict(payload)

    async def _recent_private_conversation_for_proactive_review(self, user, *, limit=10):
        return "用户: 最近在看书"

    def _format_proactive_generation_intent_hint(self, *args, **kwargs):
        return "保持低压力分享"

    def _format_proactive_voice_prompt(self):
        return "主动风格：嗯…开头，俏皮但不黏人"

    def _format_proactive_recipient_identity_guard(self, user, name=""):
        return f"当前收件人：{name or '你'}；普通朋友边界"

    def _format_proactive_review_runtime_context(self, user, *, now=None):
        return "当前适合轻量开口"

    def _proactive_review_strength(self):
        return "lenient"

    def _task_provider(self, *values):
        return next((value for value in values if value), "")

    async def _llm_call(self, prompt, **kwargs):
        self.captured_prompt = prompt
        if kwargs.get("task") == "proactive_send_review":
            return json.dumps({"decision": "send", "text": "", "reason": "符合人格"}, ensure_ascii=False)
        return "嗯…这页看完记得歇一下。"

    @staticmethod
    def _parse_json_object(raw):
        return json.loads(raw)

    @staticmethod
    def _sanitize_action_boundaries(text, **kwargs):
        return text

    @staticmethod
    def _sanitize_proactive_text(text):
        return str(text or "").strip()

    @staticmethod
    def _normalize_proactive_sentence_flow(text):
        return text

    @staticmethod
    def _framework_agent_meta_summary_leak(text):
        return False

    @staticmethod
    def _repair_proactive_recipient_address(text, user, name=""):
        return text, ""

    def _proactive_reply_air_flags(self, text, **kwargs):
        return ["回复式开场"] if text == "原始候选" else []

    @staticmethod
    def _strip_parenthetical_stage_directions(text):
        return text

    @staticmethod
    def _trim_proactive_status_inventory(text):
        return text

    @staticmethod
    def _trim_performative_self_state_tail(text):
        return text


class _SourceCorruptingReviewHarness(_ProactivePersonaHarness):
    async def _llm_call(self, prompt, **kwargs):
        self.captured_prompt = prompt
        if kwargs.get("task") == "proactive_send_review":
            return json.dumps(
                {
                    "decision": "rewrite",
                    "text": "刚刷到 B站这个视频，想给你看看。https://www.reddit.com/r/example/comments/test-post/",
                    "reason": "更自然",
                },
                ensure_ascii=False,
            )
        return await super()._llm_call(prompt, **kwargs)


class ProactivePersonaConsistencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_persona_cache_is_isolated_by_session(self):
        harness = _PersonaCacheHarness()
        first, second = await asyncio.gather(
            harness._refresh_default_persona_prompt("session-a"),
            harness._refresh_default_persona_prompt("session-b"),
        )

        self.assertEqual(first, "persona-for:session-a")
        self.assertEqual(second, "persona-for:session-b")
        self.assertEqual(harness._get_default_persona_prompt("session-a"), "persona-for:session-a")
        self.assertEqual(harness._get_default_persona_prompt("session-b"), "persona-for:session-b")

    async def test_final_send_review_receives_full_persona(self):
        harness = _ProactivePersonaHarness()
        user = {"umo": "session-a", "nickname": "小林", "user_id": "1"}

        self.assertFalse(harness.enable_passive_response_review)
        self.assertTrue(harness.enable_proactive_message_review)

        result = await harness._review_proactive_message_send_decision(
            user,
            "窗边那页书看得有点久啦。",
            reason="quiet_care",
            action="message",
            motive="顺手提醒休息",
            topic="看书",
        )

        self.assertEqual(result["decision"], "send")
        self.assertIn("完整人格::session-a::冷静、俏皮、称呼稳定", harness.captured_prompt)
        self.assertIn("主动风格：嗯…开头，俏皮但不黏人", harness.captured_prompt)
        self.assertIn("当前收件人：小林；普通朋友边界", harness.captured_prompt)

    async def test_troubleshooting_review_treats_user_request_as_real_reason(self):
        harness = _ProactivePersonaHarness()
        user = {
            "umo": "session-test",
            "nickname": "小林",
            "user_id": "1",
            "planned_proactive_source": "troubleshooting",
        }

        result = await harness._review_proactive_message_send_decision(
            user,
            "夜深了，轻轻来和你说句话。",
            reason="check_in",
            action="message",
            motive="用户明确希望测试一次主动消息",
            topic="自然来找对方一下",
        )

        self.assertEqual("send", result["decision"])
        self.assertIn("用户刚刚在控制面板明确发起了一次主动消息链路测试", harness.captured_prompt)
        self.assertIn("Do not drop solely because it is late", harness.captured_prompt)

    async def test_final_review_cannot_replace_verified_bilibili_link(self):
        harness = _SourceCorruptingReviewHarness()
        user = {
            "umo": "session-bili",
            "nickname": "小林",
            "user_id": "1",
            "bilibili_video_context": {
                "title": "春季新番预告",
                "bvid": "BV1AB411C7mD",
                "comment": "这个片段很轻松",
            },
        }
        original = "刚刷到 B站《春季新番预告》，这个片段很轻松。https://www.bilibili.com/video/BV1AB411C7mD"

        result = await harness._review_proactive_message_send_decision(
            user,
            original,
            reason="bili_video_share",
            action="message",
            motive="想分享刚看到的视频",
            topic="春季新番预告",
        )

        self.assertEqual(result["decision"], "rewrite")
        self.assertEqual(result["text"], original)
        self.assertNotIn("reddit.com", result["text"])

    async def test_reply_air_rewrite_keeps_persona_context(self):
        harness = _ProactivePersonaHarness()
        user = {"umo": "session-b", "nickname": "阿青", "planned_proactive_topic": "看书"}

        result = await harness._review_proactive_message_stance(
            user,
            "原始候选",
            reason="quiet_care",
            action="message",
            motive="提醒休息",
        )

        self.assertTrue(result.startswith("嗯…"))
        self.assertIn("完整人格::session-b::冷静、俏皮、称呼稳定", harness.captured_prompt)
        self.assertIn("不得把原文改成另一种人格", harness.captured_prompt)

    async def test_proactive_review_does_not_reuse_an_unverified_mother(self):
        harness = _ProactivePersonaHarness()
        user = {
            "umo": "session-b",
            "nickname": "阿青",
            "planned_proactive_topic": "妈妈洗了青提，后来翻开练习册",
        }

        await harness._review_proactive_message_stance(
            user,
            "原始候选",
            reason="activity_share",
            action="message",
            motive="被妈妈提醒后，想说已经把物理题写完了",
        )

        self.assertNotIn("妈妈", harness.captured_prompt)
        self.assertIn("后来翻开练习册", harness.captured_prompt)
        self.assertIn("已经把物理题写完", harness.captured_prompt)

    async def test_resolver_uses_returned_session_persona(self):
        harness = _ProactivePersonaHarness()
        resolved = await harness._resolve_proactive_persona_prompt({"umo": "session-c"})
        self.assertEqual(resolved, "完整人格::session-c::冷静、俏皮、称呼稳定")
        self.assertEqual(harness.resolved_sessions, ["session-c"])

    def test_configured_signature_opening_is_not_removed(self):
        harness = _ProactivePersonaHarness()
        user = {"action_consequences": [{"text": "嗯…昨晚那本书还挺有意思。"}]}
        text = "嗯…窗边那页看完就歇一下。"
        self.assertEqual(harness._apply_proactive_style_variation(text, user), text)

    def test_framework_conversation_uses_plugin_persona_without_mutating_original(self):
        harness = _ProactivePersonaHarness()
        harness.plugin_specific_persona_id = "plugin-persona"
        original = SimpleNamespace(persona_id="conversation-persona", history="[]")

        scoped = harness._proactive_conversation_with_configured_persona(original)

        self.assertIsNot(scoped, original)
        self.assertEqual(scoped.persona_id, "plugin-persona")
        self.assertEqual(original.persona_id, "conversation-persona")

    def test_persona_phrase_is_not_hard_deleted(self):
        harness = _ProactivePersonaHarness()
        text = "突然想起你，窗外那阵雨刚停。"
        self.assertIn("突然想起你", harness._soften_social_proactive_text(text))

    def test_visible_proactive_body_does_not_inherit_tts_script_format(self):
        hint = _ProactivePersonaHarness._proactive_visible_text_format_hint("photo_text")

        self.assertIn("最终显示在聊天里的普通正文", hint)
        self.assertIn("图片动作写可见附言", hint)
        self.assertIn("[happy]/[sad]", hint)
        self.assertIn("不能仅凭 TTS 语种要求切换", hint)


if __name__ == "__main__":
    unittest.main()
