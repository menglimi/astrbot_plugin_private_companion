"""Telegram identity is keyed by numeric ids, never by nickname.

The original plugin identifies a person by the OneBot QQ number and a group by
the QQ group number.  On Telegram the equivalents are:

* person  -> ``sender.user_id`` (numeric Telegram user id)
* group   -> ``group.group_id`` / ``session_id`` (numeric chat id, negative for
             supergroups, ``chat#thread`` for forum topics)

Nicknames/usernames are display labels only.  These tests pin that contract:
two different ids with the same nickname stay distinct, a renamed id keeps its
identity, and no name-based path can turn into an account/group key.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace as NS

from astrbot.core.message.components import File, Image, Plain, Record, Reply, Video

from astrbot_host.audio.loader import load_native_audio
from astrbot_host.binding import event_identity
from astrbot_host.files.loader import load_native_file
from astrbot_host.media import load_native_image
from astrbot_host.media_components import group_components
from astrbot_host.quotes import load_native_group_quote, load_native_quote
from astrbot_host.telegram_media import prepare_telegram_media
from astrbot_host.video.loader import load_native_video
from astrbot_host.display_names import PlatformDisplayNames
from astrbot_host.group_admission import group_trigger_facts, mentioned_account_ids
from astrbot_host.group_remarks import _key, _route
from astrbot_host.session_topology import SessionTopology
from companion_v2.conversation.group_llm_block import group_block_key
from companion_v2.relationships.group_network import route_owner_key
from astrbot_host.group_settings import GroupTarget, parse_group_targets
from astrbot_host.session_topology import session_route
from companion_v2.adapters.astrbot.group import NativeGroupBridge, group_event_route
from companion_v2.adapters.astrbot.native import (
    HostAccount,
    HostPersona,
    account_number,
    event_account_id,
    event_peer_id,
    native_user_subject_id,
    persona_instruction,
    route_id,
)
from companion_v2.conversation.outbound_plan import mention_targets, quote_target
from companion_v2.execution.models import digest
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from tests.test_management_access import INSTALLATION, NOW, ROLE
from tests.test_telegram_capture import (
    FakeTelegramContext,
    FakeTelegramEvent,
    TelegramCaptureFixture,
)


def telegram_identity_event(*, user_id="123456789", nickname="alice",
                            platform_id="telegram", self_id="companion_bot"):
    meta = NS(name="telegram", id=platform_id)
    message = NS(self_id=self_id, sender=NS(user_id=user_id, nickname=nickname))
    return NS(platform_meta=meta, message_obj=message,
              unified_msg_origin=f"{platform_id}:FriendMessage:{user_id}")


def telegram_group_event(*, group_id="-1001234567890", group_name="读书群",
                         sender_id="123456789", nickname="alice", parts=()):
    meta = NS(name="telegram", id="telegram")
    message = NS(
        type=NS(value="GroupMessage"),
        self_id="companion_bot",
        sender=NS(user_id=sender_id, nickname=nickname),
        group_id=group_id,
        group=NS(group_id=group_id, group_name=group_name),
        session_id=group_id,
        message_id="42",
        message=list(parts),
        raw_message=NS(),
    )
    return NS(platform_meta=meta, message_obj=message,
              unified_event_origin=None,
              unified_msg_origin=f"telegram:GroupMessage:{group_id}",
              session=NS(message_type=NS(value="GroupMessage"), platform_id="telegram",
                         platform_name="telegram", session_id=group_id),
              created_at=NOW.timestamp())


class TelegramBindingIdentityTests(unittest.TestCase):
    """Binding identity comes from the numeric user id, not the nickname."""

    def test_same_nickname_different_user_ids_are_distinct(self):
        first = event_identity(telegram_identity_event(user_id="111", nickname="alice"))
        second = event_identity(telegram_identity_event(user_id="222", nickname="alice"))
        self.assertEqual(first["participant_id"], "111")
        self.assertEqual(second["participant_id"], "222")
        self.assertNotEqual(first["participant_id"], second["participant_id"])

    def test_renamed_user_keeps_the_same_identity(self):
        before = event_identity(telegram_identity_event(user_id="111", nickname="alice"))
        after = event_identity(telegram_identity_event(user_id="111", nickname="alice_new"))
        self.assertEqual(before, after)

    def test_account_key_is_the_platform_instance_not_the_bot_username(self):
        identity = event_identity(telegram_identity_event(user_id="111"))
        self.assertEqual(identity["platform_id"], "telegram")
        self.assertEqual(identity["account_id"], "telegram")
        # A different bot username on a second instance still keys by instance id.
        second = event_identity(telegram_identity_event(
            user_id="111", platform_id="telegram-2", self_id="another_bot"))
        self.assertEqual(second["account_id"], "telegram-2")

    def test_user_without_a_username_still_has_a_numeric_identity(self):
        # Telegram users without a public username get nickname "Unknown"; the
        # numeric id must still be the only identity key.
        identity = event_identity(telegram_identity_event(user_id="333", nickname="Unknown"))
        self.assertEqual(identity["participant_id"], "333")


class TelegramSubjectIdentityTests(unittest.TestCase):
    def test_subject_namespace_uses_the_numeric_id(self):
        self.assertEqual(native_user_subject_id("telegram", "111"), "telegram-user:111")
        self.assertEqual(native_user_subject_id("telegram", "222"), "telegram-user:222")
        self.assertNotEqual(native_user_subject_id("telegram", "111"),
                            native_user_subject_id("telegram", "222"))

    def test_event_ids_come_from_numeric_fields(self):
        event = telegram_identity_event(user_id="111")
        self.assertEqual(event_account_id(event), "telegram")
        self.assertEqual(event_peer_id(event), "111")
        # Nickname is not consulted anywhere in the id projection.
        event.message_obj.sender.nickname = "completely different"
        self.assertEqual(event_peer_id(event), "111")


class TelegramCaptureIdentityTests(TelegramCaptureFixture, unittest.IsolatedAsyncioTestCase):
    """End-to-end capture: same nickname, different ids -> different subjects."""

    def bind_user(self, user_id, subject_id):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "telegram",
                                               str(user_id), "bot-a", "telegram")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, subject_id)
        self.identity.bind(binding_id="telegram-user-" + str(user_id), principal=principal,
                           subject=subject, valid_from=NOW,
                           expected_revision=self.identity.revision)
        return subject

    async def capture_user(self, user_id, nickname):
        event = FakeTelegramEvent(self.native, sender=str(user_id), nickname=nickname,
                                  text="你好")
        captured = await self.capture(event)
        self.assertTrue(captured.is_success, captured)
        return captured.unwrap()

    async def test_two_users_with_the_same_nickname_are_distinct(self):
        alice_one = self.bind_user("111", "telegram-user:111")
        alice_two = self.bind_user("222", "telegram-user:222")
        first = await self.capture_user("111", "alice")
        second = await self.capture_user("222", "alice")
        self.assertEqual(first.principal.native_account_id, "111")
        self.assertEqual(second.principal.native_account_id, "222")
        self.assertEqual(first.route.user, alice_one)
        self.assertEqual(second.route.user, alice_two)
        self.assertNotEqual(first.route.scope.native_scope_id,
                            second.route.scope.native_scope_id)
        self.assertNotEqual(first.route.scope.session_id, second.route.scope.session_id)
        self.assertEqual(first.route.scope.native_scope_id, "111")
        self.assertEqual(second.route.scope.native_scope_id, "222")

    async def test_renaming_a_user_does_not_change_identity(self):
        subject = self.bind_user("111", "telegram-user:111")
        before = await self.capture_user("111", "alice")
        after = await self.capture_user("111", "alice_renamed")
        self.assertEqual(before.route.user, subject)
        self.assertEqual(after.route.user, subject)
        self.assertEqual(before.route.scope.native_scope_id, after.route.scope.native_scope_id)
        self.assertEqual(before.principal.native_account_id,
                         after.principal.native_account_id)

    async def test_two_users_without_usernames_are_distinct(self):
        self.bind_user("333", "telegram-user:333")
        self.bind_user("444", "telegram-user:444")
        first = await self.capture_user("333", "Unknown")
        second = await self.capture_user("444", "Unknown")
        self.assertEqual(first.route.user.subject_id, "telegram-user:333")
        self.assertEqual(second.route.user.subject_id, "telegram-user:444")
        self.assertNotEqual(first.route.scope.native_scope_id,
                            second.route.scope.native_scope_id)


class TelegramGroupIdentityTests(unittest.TestCase):
    """Group identity is the numeric chat id, never the group title."""

    def test_two_groups_with_the_same_title_are_distinct(self):
        first = telegram_group_event(group_id="-1001111111111", group_name="读书群")
        second = telegram_group_event(group_id="-1002222222222", group_name="读书群")
        route_one = group_event_route(first)
        route_two = group_event_route(second)
        self.assertEqual(route_one.group_id, "-1001111111111")
        self.assertEqual(route_two.group_id, "-1002222222222")
        self.assertNotEqual(route_one.route_key, route_two.route_key)

    def test_forum_topic_is_distinct_from_its_parent_group(self):
        parent = group_event_route(telegram_group_event(group_id="-1001111111111"))
        topic = group_event_route(telegram_group_event(group_id="-1001111111111#42"))
        self.assertNotEqual(parent.group_id, topic.group_id)
        self.assertNotEqual(parent.route_key, topic.route_key)

    def test_group_route_uses_ids_not_names(self):
        event = telegram_group_event(group_id="-1001111111111", group_name="读书群")
        event.message_obj.group.group_name = "完全不同的群名"
        route = group_event_route(event)
        self.assertEqual(route.group_id, "-1001111111111")

    def test_group_targets_are_keyed_by_group_id(self):
        targets = parse_group_targets([
            {"target_id": "group-a", "platform_id": "telegram", "account_id": "telegram",
             "bot_id": "bot-a", "group_id": "-1001111111111"},
            {"target_id": "group-b", "platform_id": "telegram", "account_id": "telegram",
             "bot_id": "bot-a", "group_id": "-1002222222222"},
        ])
        self.assertEqual({target.group_id for target in targets},
                         {"-1001111111111", "-1002222222222"})
        self.assertNotEqual(targets[0].route_key, targets[1].route_key)
        # Negative ids and forum topics are accepted as canonical routes.
        self.assertEqual(GroupTarget("group-c", "telegram", "telegram", "bot-a",
                                     "-1001111111111#42").group_id, "-1001111111111#42")

    def test_group_remarks_are_keyed_by_route_ids(self):
        first = _key(_route({"platform_id": "telegram", "bot_id": "bot-a",
                             "account_id": "telegram", "group_id": "-1001111111111"}))
        second = _key(_route({"platform_id": "telegram", "bot_id": "bot-a",
                              "account_id": "telegram", "group_id": "-1002222222222"}))
        self.assertNotEqual(first, second)


class TelegramGroupCaptureIdentityTests(TelegramCaptureFixture, unittest.IsolatedAsyncioTestCase):
    """Group capture uses the numeric chat id as the group identity."""

    def setUp(self):
        super().setUp()
        from astrbot_host.group_settings import GroupTarget

        self.group_targets = (
            GroupTarget("group-one", "telegram", "telegram", "bot-a", "-1001111111111"),
            GroupTarget("group-two", "telegram", "telegram", "bot-a", "-1002222222222"),
            # Topic routes are separate native ids; a wildcard keeps the test
            # focused on identity rather than on target configuration.
            GroupTarget("group-wild", "telegram", "telegram", "bot-a", "*"),
        )
        persona = HostPersona("bot-a", "original", ROLE,
                              digest(persona_instruction(self.native.persona)))
        self.group_bridge = NativeGroupBridge(
            self.native, version="4.29.1", event_type=FakeTelegramEvent,
            installation_id=INSTALLATION, host_id="host", identities=self.identity,
            accounts=(HostAccount("telegram", "telegram", "bot-a"),),
            personas=(persona,),
            group_targets=self.group_targets,
            image_type=Image, image_loader=load_native_image,
            reply_type=Reply, quote_loader=load_native_group_quote,
            audio_type=Record, audio_loader=load_native_audio,
            video_type=Video, video_loader=load_native_video,
            file_type=File, file_loader=load_native_file,
            component_reader=group_components,
            media_preparer=prepare_telegram_media,
        )

    def bind_user(self, user_id):
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "telegram",
                                               str(user_id), "bot-a", "telegram")
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "telegram-user:" + str(user_id))
        self.identity.bind(binding_id="telegram-user-" + str(user_id), principal=principal,
                           subject=subject, valid_from=NOW,
                           expected_revision=self.identity.revision)
        return subject

    async def capture_group(self, group_id, *, sender="123456789", nickname="alice",
                            group_name="读书群"):
        event = FakeTelegramEvent(self.native, sender=sender, nickname=nickname,
                                  group_id=group_id, group_name=group_name,
                                  parts=[Plain("群消息")])
        captured = await self.group_bridge.capture(event)
        self.assertTrue(captured.is_success, captured)
        return captured.unwrap()

    async def test_two_groups_with_the_same_title_are_distinct_scopes(self):
        first = await self.capture_group("-1001111111111", group_name="读书群")
        second = await self.capture_group("-1002222222222", group_name="读书群")
        self.assertEqual(first.route.scope.native_scope_id, "-1001111111111")
        self.assertEqual(second.route.scope.native_scope_id, "-1002222222222")
        self.assertNotEqual(first.route.scope.session_id, second.route.scope.session_id)
        self.assertNotEqual(first.route.scope, second.route.scope)

    async def test_group_scope_does_not_follow_the_group_title(self):
        before = await self.capture_group("-1001111111111", group_name="读书群")
        after = await self.capture_group("-1001111111111", group_name="改过的群名")
        self.assertEqual(before.route.scope, after.route.scope)
        self.assertEqual(before.route.scope.native_scope_id, "-1001111111111")

    async def test_same_nickname_speakers_in_one_group_are_distinct(self):
        self.bind_user("111")
        self.bind_user("222")
        first = await self.capture_group("-1001111111111", sender="111", nickname="alice")
        second = await self.capture_group("-1001111111111", sender="222", nickname="alice")
        self.assertEqual(first.route.scope.native_scope_id, second.route.scope.native_scope_id)
        # Group routes carry the speaker as the audience subject, not route.user.
        self.assertEqual(first.recipients.recipients[0].subject_id, "telegram-user:111")
        self.assertEqual(second.recipients.recipients[0].subject_id, "telegram-user:222")
        self.assertNotEqual(first.principal.native_account_id,
                            second.principal.native_account_id)

    async def test_forum_topic_is_a_distinct_group_scope(self):
        parent = await self.capture_group("-1001111111111")
        topic = await self.capture_group("-1001111111111#42")
        self.assertNotEqual(parent.route.scope.native_scope_id,
                            topic.route.scope.native_scope_id)
        self.assertEqual(topic.route.scope.native_scope_id, "-1001111111111#42")


class TelegramStorageKeyTests(unittest.TestCase):
    """Stored state is keyed by numeric ids; titles/names are payload only."""

    def test_group_llm_block_key_uses_group_id(self):
        first = group_block_key(platform_id="telegram", account_id="telegram",
                                bot_id="bot-a", group_id="-1001111111111")
        second = group_block_key(platform_id="telegram", account_id="telegram",
                                 bot_id="bot-a", group_id="-1002222222222")
        self.assertNotEqual(first, second)

    def test_group_network_owner_key_uses_group_id(self):
        first = route_owner_key("install", "telegram", "bot-a", "telegram",
                                "-1001111111111", "role-a")
        second = route_owner_key("install", "telegram", "bot-a", "telegram",
                                 "-1002222222222", "role-a")
        same = route_owner_key("install", "telegram", "bot-a", "telegram",
                               "-1001111111111", "role-a")
        self.assertNotEqual(first, second)
        self.assertEqual(first, same)

    def test_session_directory_keys_by_ids_and_keeps_names_as_labels(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            controller = NS(data_root=Path(directory), _terminated=False)
            topology = SessionTopology(controller)
            rows = {
                "a": {"unified_msg_origin": "telegram:GroupMessage:-1001111111111",
                      "account_id": "telegram", "group_name": "读书群",
                      "last_seen_at": "2026-01-01T00:00:00+00:00"},
                "b": {"unified_msg_origin": "telegram:GroupMessage:-1002222222222",
                      "account_id": "telegram", "group_name": "读书群",
                      "last_seen_at": "2026-01-01T00:00:01+00:00"},
            }
            topology.path.parent.mkdir(parents=True, exist_ok=True)
            topology.path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            names = topology.stored_group_names()
            self.assertEqual(set(names), {("telegram", "telegram", "-1001111111111"),
                                          ("telegram", "telegram", "-1002222222222")})
            self.assertEqual(set(names.values()), {"读书群"})


class TelegramSessionRouteTests(unittest.TestCase):
    def test_session_route_parses_numeric_ids(self):
        self.assertEqual(session_route("telegram:FriendMessage:123456789"),
                         ("telegram", "private", "123456789"))
        self.assertEqual(session_route("telegram:GroupMessage:-1001234567890"),
                         ("telegram", "group", "-1001234567890"))
        self.assertEqual(session_route("telegram:GroupMessage:-1001234567890#42"),
                         ("telegram", "group", "-1001234567890#42"))

    def test_same_title_groups_have_distinct_sessions(self):
        first = session_route("telegram:GroupMessage:-1001111111111")
        second = session_route("telegram:GroupMessage:-1002222222222")
        self.assertNotEqual(first, second)


class TelegramMentionIdentityTests(unittest.TestCase):
    """@ handling must not turn a username into an account id."""

    def test_username_mention_is_not_an_account_id(self):
        event = telegram_group_event(parts=[
            NS(type=NS(value="At"), qq="alice", name="alice"),
            Plain("你好"),
        ])
        facts = group_trigger_facts(event, "telegram")
        self.assertFalse(facts["mentioned"])
        self.assertTrue(facts["mentions_others"])
        # A username can never be returned as a native account id.
        self.assertEqual(mentioned_account_ids(event), ())

    def test_bot_mention_uses_the_bot_username_only(self):
        event = telegram_group_event(parts=[
            NS(type=NS(value="At"), qq="companion_bot", name="companion_bot"),
            Plain("你好"),
        ])
        facts = group_trigger_facts(event, "telegram")
        self.assertTrue(facts["mentioned"])

    def test_numeric_at_target_is_still_an_account_id(self):
        event = telegram_group_event(parts=[NS(type=NS(value="At"), qq="123456789")])
        self.assertEqual(mentioned_account_ids(event), ("123456789",))

    def test_platform_instance_id_is_not_a_bot_username(self):
        # The configured account key ("telegram") is not a Telegram username;
        # a user whose username equals it must not count as a bot mention.
        event = telegram_group_event(parts=[NS(type=NS(value="At"), qq="telegram")])
        facts = group_trigger_facts(event, "telegram")
        self.assertFalse(facts["mentioned"])
        self.assertTrue(facts["mentions_others"])


class TelegramOutboundIdentityTests(unittest.TestCase):
    """Outbound addressing uses native ids; the username is decoration only."""

    def test_mention_target_is_the_native_account_id(self):
        mentions = mention_targets({"reply_mention_speaker_enabled": True},
                                   inputs=(("41", "123456789"),))
        self.assertEqual(mentions, ("123456789",))
        # A nickname-looking account value is refused rather than guessed.
        self.assertEqual(mention_targets({"reply_mention_speaker_enabled": True},
                                         inputs=(("41", "alice"),)), ())

    def test_quote_target_is_the_native_message_id(self):
        self.assertEqual(quote_target({"reply_quote_enabled": True},
                                      text="回复", inputs=(("41", "123456789"),)), "41")

    def test_telegram_mention_name_is_display_only(self):
        from astrbot_host.telegram_media import telegram_mention_name

        event = telegram_identity_event(user_id="111", nickname="alice")
        self.assertEqual(telegram_mention_name(event, "111"), "alice")
        # The account id is what identifies the target; the username is only
        # what Telegram needs to render the @ prefix.
        self.assertEqual(telegram_mention_name(event, "222"), "")


class FakeDisplayContext:
    def __init__(self, platform):
        self.platform = platform

    def get_platform_inst(self, platform_id):
        return self.platform if platform_id == self.platform.meta().id else None


class FakeTelegramNameClient:
    def __init__(self, users, groups):
        self.users = users
        self.groups = groups

    async def get_me(self):
        return NS(username="companion_bot", first_name="Companion")

    async def get_chat(self, chat_id):
        if chat_id in self.groups:
            return NS(title=self.groups[chat_id])
        return NS(username=self.users.get(chat_id, "Unknown"), first_name="")


class FakeTelegramNamePlatform:
    def __init__(self, users, groups):
        self.metadata = NS(name="telegram", id="telegram")
        self.client = FakeTelegramNameClient(users, groups)

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client


class TelegramDisplayNameTests(unittest.TestCase):
    """Display names never overwrite the identity fields of a target row."""

    def test_display_names_are_labels_only(self):
        platform = FakeTelegramNamePlatform({111: "alice", 222: "alice"},
                                            {-1001111111111: "读书群"})
        names = PlatformDisplayNames(FakeDisplayContext(platform))
        rows = asyncio.run(names.targets([
            {"platform_id": "telegram", "account_id": "telegram", "participant_id": "111",
             "scope_kind": "private"},
            {"platform_id": "telegram", "account_id": "telegram", "participant_id": "222",
             "scope_kind": "private"},
            {"platform_id": "telegram", "account_id": "telegram", "group_id": "-1001111111111",
             "scope_kind": "group"},
        ]))
        self.assertEqual([row["participant_id"] for row in rows[:2]], ["111", "222"])
        self.assertEqual([row["display_name"] for row in rows[:2]], ["alice", "alice"])
        self.assertEqual(rows[2]["group_id"], "-1001111111111")
        self.assertEqual(rows[2]["group_name"], "读书群")

    def test_same_title_groups_keep_distinct_ids(self):
        platform = FakeTelegramNamePlatform({}, {-1001111111111: "读书群",
                                                 -1002222222222: "读书群"})
        names = PlatformDisplayNames(FakeDisplayContext(platform))
        rows = asyncio.run(names.targets([
            {"platform_id": "telegram", "account_id": "telegram",
             "group_id": "-1001111111111", "scope_kind": "group"},
            {"platform_id": "telegram", "account_id": "telegram",
             "group_id": "-1002222222222", "scope_kind": "group"},
        ]))
        self.assertEqual([row["group_id"] for row in rows],
                         ["-1001111111111", "-1002222222222"])
        self.assertEqual([row["group_name"] for row in rows], ["读书群", "读书群"])


if __name__ == "__main__":
    unittest.main()
