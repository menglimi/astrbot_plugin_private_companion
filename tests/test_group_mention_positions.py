"""Original QQ mention positions survive host parsing and persisted model input."""

from types import SimpleNamespace as NS
import unittest

from tests import astrbot_data_root  # noqa: F401
from astrbot.core.message.components import At, Image, Plain
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter, PatchedGroupMessage,
)
from astrbot_host.core_reply_mentions import restore_group_mentions
from astrbot_host.group_admission import group_trigger_facts
from astrbot_host.group_debounce import replace_group_plain_text
from companion_v2.adapters.astrbot.group import NativeGroupBridge
from companion_v2.adapters.astrbot.group_mentions import group_message_text
from companion_v2.conversation.group_timing import short_input_wait, repeat_signature


async def official_message(content, *, mentioned=True):
    raw = PatchedGroupMessage(None, "synthetic-event", {
        "id": "synthetic-message", "content": content,
        "author": {"member_openid": "SPEAKER", "username": "测试成员"},
        "group_openid": "GROUP", "attachments": [],
        "mentions": [{"id": "BOT", "is_you": True, "username": "测试角色"}] if mentioned else [],
    })
    message = await QQOfficialPlatformAdapter._parse_from_qqofficial(raw, MessageType.GROUP_MESSAGE)
    return NS(message_obj=message, platform_meta=NS(name="qq_official"))


class GroupMentionPositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_mentions_keep_position_and_other_tags_verbatim(self):
        for own in ('<@BOT>', '<@!BOT>', '<qqbot-at-user id="BOT" />'):
            with self.subTest(own=own):
                source = f'<@OTHER> 介绍一下，让 {own} 了解 <qqbot-at-user id="THIRD"/>'
                event = await official_message(source)
                expected = source.replace(own, "<提及自己>")
                self.assertEqual(group_message_text(event, "APP"), expected)
                skeleton = NativeGroupBridge._text_only_parts(event, NS(account_id="APP"))
                self.assertEqual(skeleton, expected)
                facts = group_trigger_facts(event, "APP")
                self.assertTrue(facts["mentioned"])
                self.assertTrue(facts["mentions_others"])

    async def test_only_other_mention_and_literal_marker_do_not_claim_self(self):
        event = await official_message("<提及自己> <@OTHER> 看看", mentioned=False)
        facts = group_trigger_facts(event, "APP")
        self.assertFalse(facts["mentioned"])
        self.assertTrue(facts["mentions_others"])
        self.assertEqual(group_message_text(event, "APP"), "<提及自己> <@OTHER> 看看")

    async def test_source_mentions_remain_available_when_native_at_is_removed(self):
        event = await official_message("让 <@BOT> 了解 <@OTHER>")
        event.message_obj.message = [part for part in event.message_obj.message if isinstance(part, Plain)]
        self.assertEqual(group_message_text(event, "APP"), "让 <提及自己> 了解 <@OTHER>")
        self.assertTrue(group_trigger_facts(event, "APP")["mentioned"])

    async def test_face_and_repeated_self_mentions_keep_order(self):
        text = '<@BOT> 看这个 <faceType=4,ext="e30=">，再让 <@!BOT> 看那个'
        event = await official_message(text)
        self.assertEqual(group_message_text(event, "APP"),
                         "<提及自己> 看这个 [表情]，再让 <提及自己> 看那个")

    async def test_empty_mention_keeps_media_out_of_bare_mention(self):
        event = await official_message(" <@BOT> ")
        event.message_obj.message.append(Image(file="file:///synthetic.png"))
        self.assertEqual(group_message_text(event, "APP"), "<提及自己>")
        skeleton = NativeGroupBridge._text_only_parts(event, NS(account_id="APP"))
        self.assertIn("<提及自己>", skeleton)
        self.assertIn("[图片不可用]", skeleton)

    async def test_old_hosts_keep_component_fallback_without_guessing_raw_position(self):
        event = NS(platform_meta=NS(name="qq_official"), message_obj=NS(
            self_id="qq_official", raw_message=None,
            message=[At(qq="qq_official"), Plain("原生正文 <@OTHER>")]))
        self.assertEqual(group_message_text(event, "APP"), "<提及自己>原生正文 <@OTHER>")
        self.assertTrue(group_trigger_facts(event, "APP")["mentions_others"])

    async def test_old_projection_and_raw_mapping_are_supported(self):
        event = NS(platform_meta=NS(name="qq_official_webhook"), message_obj=NS(
            self_id="qq_official", raw_message={"raw_data": {
                "content": "让 <@BOT> 看看", "mentions": [{"id": "BOT", "is_you": True}]}},
            message=[At(qq="qq_official"), Plain("让 <@BOT> 看看")]))
        self.assertEqual(group_message_text(event, "APP"), "让 <提及自己> 看看")

    async def test_debounce_keeps_all_messages_and_other_mentions(self):
        event = await official_message("第二条", mentioned=False)
        self.assertTrue(replace_group_plain_text(event, "<@OTHER> 第一条\n第二条"))
        self.assertEqual(group_message_text(event, "APP"), "<@OTHER> 第一条\n第二条")
        self.assertTrue(group_trigger_facts(event, "APP")["mentions_others"])
        # A changed Plain body cannot be replaced by an older raw self mention.
        event = await official_message("让 <@BOT> 看看")
        event.message_obj.message[-1].text = "改写后的正文"
        self.assertEqual(group_message_text(event, "APP"), "<提及自己>改写后的正文")


class MentionRequestTests(unittest.TestCase):
    def test_request_preserves_configured_prefixes_and_other_plugin_rewrites(self):
        native = NS(text="/帮我问 <@OTHER>", group_message_text="/<提及自己>帮我问 <@OTHER>")
        event = NS(is_at_or_wake_command=True, unified_msg_origin="test:GroupMessage:group")
        context = NS(get_config=lambda **_: {"wake_prefix": ["/"], "provider_settings": {
            "prompt_prefix": "前置\n{{prompt}}\n后置", "wake_prefix": ""}})
        cases = (
            (native.text, native.group_message_text),
            ("帮我问 <@OTHER>", "<提及自己>帮我问 <@OTHER>"),
            ("前置\n帮我问 <@OTHER>\n后置", "前置\n<提及自己>帮我问 <@OTHER>\n后置"),
            ("另一插件的完整请求", "另一插件的完整请求"),
        )
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                request = NS(prompt=prompt)
                restore_group_mentions(event, request, native, context)
                self.assertEqual(request.prompt, expected)
                restore_group_mentions(event, request, native, context)
                self.assertEqual(request.prompt, expected)

    def test_provider_wake_prefix_after_self_mention_is_not_reintroduced(self):
        event = NS(is_at_or_wake_command=False)
        native = NS(text="ask:看看", group_message_text="<提及自己>ask:看看")
        context = NS(get_config=lambda **_: {"provider_settings": {"wake_prefix": "ask:",
                                                                  "prompt_prefix": "提示："}})
        request = NS(prompt="提示：看看")
        restore_group_mentions(event, request, native, context)
        self.assertEqual(request.prompt, "提示：<提及自己>看看")

    def test_new_and_legacy_markers_have_same_timing_and_repeat_behavior(self):
        from companion_v2.foundation.refs import ScopeKind
        event = NS(scope=NS(kind=ScopeKind.GROUP))
        settings = {"group_wakeup_enabled": True, "group_wakeup_short_text_wait_ms": 1500}
        self.assertEqual(short_input_wait(event, "<提及自己> 等等", settings), 1.5)
        self.assertEqual(short_input_wait(event, "[提及机器人] 等等", settings), 1.5)
        self.assertEqual(repeat_signature("<提及自己> 哈哈！"), repeat_signature("[提及机器人] 哈哈！"))

    def test_host_attachment_placeholder_keeps_self_mention(self):
        for kind in ("Reply", "Video", "File", "Forward"):
            with self.subTest(kind=kind):
                event = NS(message_obj=NS(message=[At(qq="BOT"), NS(type=kind)]))
                native = NS(text="", group_message_text="<提及自己>")
                request = NS(prompt="<attachment>")
                restore_group_mentions(event, request, native, None)
                self.assertEqual(request.prompt, "<提及自己>")
        # A plugin's literal placeholder without a native attachment is not host-generated.
        request = NS(prompt="<attachment>")
        restore_group_mentions(NS(), request, native, None)
        self.assertEqual(request.prompt, "<attachment>")
