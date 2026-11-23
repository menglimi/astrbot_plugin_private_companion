"""Binding: the first private message decides who this role talks to.

The command runs on *every* private message the bot receives, before the staging
target is pinned, which is the whole reason it needs tests of its own. Two failure
modes matter more than the happy path:

1. **Over-matching.** If ordinary conversation is read as a command, a normal
   private message silently binds a target. The parser therefore only accepts an
   exact leading keyword and refuses anything it does not fully understand.
2. **Under-reading.** If a persona the operator typed is dropped, binding succeeds
   with the wrong voice and the mistake is invisible until memory lands in the wrong
   role. An unrecognized trailing word is a refusal, never a silent fallback.

The identity reading is tested separately, because it is what turns an event into
``(platform, account, participant)`` -- getting that wrong binds the wrong person.
"""

import unittest

from astrbot_host.binding import (
    BindRequest,
    binding_identity,
    describe_pin,
    event_identity,
    is_bind_command,
    is_private_message,
    message_text,
    parse_bind_command,
    refuse_already_bound,
)


class Simple:
    """A stand-in for the native event objects, which AstrBot is not needed for."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def event(*, self_id="10002", user_id="10001", platform="default",
          origin="default:FriendMessage:10001"):
    return Simple(platform_meta=Simple(id=platform),
                  message_obj=Simple(self_id=self_id, sender=Simple(user_id=user_id)),
                  unified_msg_origin=origin)


class ParsingTests(unittest.TestCase):
    def test_the_bare_command_binds_with_the_conversation_persona(self):
        self.assertEqual(parse_bind_command("companion_bind"), BindRequest("bind", ""))

    def test_a_leading_slash_is_accepted(self):
        """AstrBot lets people type commands with a slash; that is the same command."""
        self.assertEqual(parse_bind_command("/companion_bind").kind, "bind")

    def test_an_inline_persona_is_carried_through(self):
        self.assertEqual(parse_bind_command("companion_bind 晚星").persona_id, "晚星")

    def test_a_persona_that_cannot_be_used_is_a_refusal_not_a_silent_default(self):
        """Dropping the tail would bind the wrong voice without saying so."""
        result = parse_bind_command("companion_bind 晚\n星")
        self.assertEqual(result.kind, "bind")
        self.assertEqual(result.reason, "host_identity_invalid")
        self.assertEqual(result.persona_id, "")

    def test_ordinary_conversation_is_never_a_command(self):
        for text in ("你好", "companion", "bind", "companion_bindx",
                     "请 companion_bind 一下", "", "   ", None, 42, ["companion_bind"]):
            with self.subTest(text=text):
                self.assertEqual(parse_bind_command(text).kind, "ignore")

    def test_the_command_must_lead_the_message(self):
        """Only the first token counts, so a quoted command cannot bind anything."""
        self.assertEqual(parse_bind_command("我想 companion_bind").kind, "ignore")
        self.assertEqual(parse_bind_command("companion_bind 晚星 请").persona_id, "晚星 请")

    def test_confirmation_is_a_separate_acknowledgement(self):
        self.assertEqual(parse_bind_command("companion_confirm").kind, "confirm")
        self.assertEqual(parse_bind_command("companion_confirm 确认").kind, "confirm")
        self.assertEqual(parse_bind_command("companion_confirm 随便").kind, "ignore")

    def test_unbind_is_its_own_word(self):
        self.assertEqual(parse_bind_command("companion_unbind").kind, "unbind")

    def test_the_predicate_agrees_with_the_parser(self):
        self.assertTrue(is_bind_command("companion_bind"))
        self.assertFalse(is_bind_command("companion_bindx"))


class EventIdentityTests(unittest.TestCase):
    def test_the_sender_becomes_the_participant_and_the_bot_the_account(self):
        value = event_identity(event())
        self.assertEqual(value, {"platform_id": "default", "account_id": "10002",
                                 "participant_id": "10001"})

    def test_integer_ids_are_normalized_the_way_onebot_sends_them(self):
        value = event_identity(event(self_id=10002, user_id=10001))
        self.assertEqual(value["account_id"], "10002")
        self.assertEqual(value["participant_id"], "10001")

    def test_a_message_from_the_bot_itself_is_not_a_binding(self):
        self.assertIsNone(event_identity(event(self_id=10002, user_id=10002)))

    def test_an_event_without_identity_is_ignored_rather_than_guessed(self):
        for kwargs in ({"self_id": None}, {"user_id": None}, {"user_id": "bad/id"},
                       {"platform": None}):
            with self.subTest(**kwargs):
                self.assertIsNone(event_identity(event(**kwargs)))

    def test_qq_official_openids_bind_with_the_platform_instance_as_account(self):
        value = event_identity(event(platform="qq_official-main", self_id="unknown_selfid",
            user_id="607097777B7E7FE3E162A8F56825F8521",
            origin="qq_official-main:FriendMessage:607097777B7E7FE3E162A8F56825F8521"))
        self.assertEqual(value["platform_id"], "qq_official-main")
        self.assertEqual(value["account_id"], "qq_official-main")
        self.assertEqual(value["participant_id"], "607097777B7E7FE3E162A8F56825F8521")

    def test_binding_yields_the_derived_identity(self):
        value = event_identity(event())
        pinned = binding_identity(**value, persona_id="晚星")
        self.assertEqual(pinned["bot_id"], "bot-10002")
        self.assertTrue(pinned["role_id"].startswith("role-10002-"))

    def test_a_group_message_is_not_a_private_binding(self):
        """A group id bound as the private participant would never match a private chat."""
        self.assertTrue(is_private_message(event()))
        self.assertFalse(is_private_message(
            event(origin="default:GroupMessage:20001")))


class MessageTextTests(unittest.TestCase):
    """Reading the command off an event, across the shapes AstrBot itself ships.

    AstrBot is not installed where these run, so the point is not to pin one
    attribute but to prove the reader survives each shape it may meet. The failure
    this prevents is silent: a text reader that returns "" would make every bind
    command look like ordinary conversation, and the operator would see nothing
    happen with no error to explain it.
    """

    def test_the_event_level_text_is_used_first(self):
        self.assertEqual(message_text(Simple(text="companion_bind")), "companion_bind")

    def test_message_str_on_the_message_object_is_accepted(self):
        """This is where the contract cases attach it, so it must be honoured."""
        event = Simple(message_obj=Simple(message_str="companion_bind"))
        self.assertEqual(message_text(event), "companion_bind")

    def test_plain_components_are_reassembled_as_a_last_resort(self):
        class Plain:
            def __init__(self, text):
                self.type, self.text = "Plain", text

        event = Simple(message_obj=Simple(message=[Plain("companion_bind"), Plain(" 晚星")]))
        self.assertEqual(message_text(event), "companion_bind 晚星")

    def test_a_non_plain_only_message_reads_as_empty_not_as_a_command(self):
        """An image or a sticker is ordinary conversation, not a malformed command."""
        class Image:
            type = "Image"
            text = None

        event = Simple(message_obj=Simple(message=[Image()]))
        self.assertEqual(message_text(event), "")

    def test_an_event_with_no_text_at_all_reads_as_empty(self):
        for event in (Simple(), Simple(message_obj=None), Simple(message_obj=Simple()),
                      Simple(text=123), Simple(text="   ")):
            with self.subTest(event=event.__dict__):
                self.assertEqual(message_text(event), "")

    def test_leading_native_mention_markers_do_not_hide_the_command(self):
        """Group events may keep [At:...] / CQ at syntax in event.text.

        The command parser only accepts a leading root, so a mention prefix used
        to make ``陪伴群 关闭LLM`` look like ordinary chat and the command was
        silently ignored.
        """
        self.assertEqual(message_text(Simple(text="[At:3491542998] 陪伴群 关闭LLM")),
                         "陪伴群 关闭LLM")
        self.assertEqual(message_text(Simple(text="[CQ:at,qq=3491542998] 陪伴群 开启LLM")),
                         "陪伴群 开启LLM")
        self.assertEqual(message_text(Simple(text="<@3491542998> /陪伴群 LLM状态")),
                         "/陪伴群 LLM状态")
        self.assertEqual(message_text(Simple(text="[At:1][At:2] 普通正文")), "普通正文")

    def test_a_blank_event_text_falls_through_to_the_message_object(self):
        """A whitespace-only event.text must not shadow a real command below it."""
        event = Simple(text="   ", message_obj=Simple(message_str="companion_bind"))
        self.assertEqual(message_text(event), "companion_bind")


class RefusalTests(unittest.TestCase):
    def test_rebinding_a_pinned_profile_is_refused_with_its_current_target(self):
        error = refuse_already_bound({"participant_id": "10001"})
        self.assertEqual(error.code, "host_binding_exists")
        self.assertIn("10001", str(error))

    def test_the_confirmation_summary_shows_what_will_be_pinned(self):
        text = describe_pin(binding_identity(platform_id="default", account_id="10002",
                                             participant_id="10001", persona_id="晚星"))
        # 确认动作在「开始使用」页完成，聊天里不再引导发送 companion_confirm。
        for expected in ("10001", "10002", "晚星", "请回到「开始使用」"):
            self.assertIn(expected, text)

    def test_an_unnamed_persona_is_described_as_following_the_conversation(self):
        text = describe_pin(binding_identity(platform_id="default", account_id="10002",
                                             participant_id="10001"))
        self.assertIn("随会话当前人格", text)


if __name__ == "__main__":
    unittest.main()
