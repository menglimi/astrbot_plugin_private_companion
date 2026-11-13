"""Synthetic Telegram private turns through the companion and AstrBot transport."""

from tests import astrbot_data_root  # noqa: F401

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from telegram import Chat, Message, Update, User
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_main_no_reply import ENTRY

OWNER = 5123456789012
BOT = 6123456789012


class TelegramClient(NS):
    def __getattr__(self, name):
        if name in {"_api", "_wsr_api_clients", "call_action"}:
            self.onebot_accesses.append(name)
        raise AttributeError(name)


class TelegramPlatform:
    def __init__(self):
        self.metadata = NS(name="telegram", id="tg-main", support_streaming_message=False)
        self.client = TelegramClient(id=BOT, send_message=AsyncMock(), send_chat_action=AsyncMock(), onebot_accesses=[])
        self.application = NS(bot=self.client, running=True)
        self._application_started = True
        self._terminating = False

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client


def telegram_event(platform, *, identifier=1, sender=OWNER, text="你好 😀 https://example.invalid", **fields):
    raw = Message(identifier, date=datetime.now(timezone.utc),
                  chat=Chat(sender, "private"), from_user=User(sender, "Synthetic", False), text=text, **fields)
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "synthetic_bot"
    message.sender = MessageMember(str(sender), "Synthetic")
    message.message_id = str(identifier)
    message.message = [Plain(text)]
    message.message_str = text
    message.raw_message = Update(identifier, message=raw)
    event = TelegramPlatformEvent(text, message, platform.meta(), str(sender), platform.client)
    event.set_extra("activated_handlers", [])
    return event


class TelegramPrivateReplyTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.platform = TelegramPlatform()
        self.native.platforms["tg-main"] = self.platform
        self.native.selections["tg-main"] = "original"
        self.native.conversations["tg-main"] = "telegram-conversation"
        self.window_personas[f"tg-main:FriendMessage:{OWNER}"] = "original"
        self.window_personas[f"tg-main:FriendMessage:{OWNER + 1}"] = "original"
        self.types = replace(self.types, event=TelegramPlatformEvent)
        self.config = multi_config(
            accounts=[dict(platform_id="tg-main", account_id="tg-main", bot_id="bot-tg")],
            targets=[dict(target_id="tg-owner", platform_id="tg-main", account_id="tg-main",
                          bot_id="bot-tg", participant_id=str(OWNER)),
                     dict(target_id="tg-other", platform_id="tg-main", account_id="tg-main",
                          bot_id="bot-tg", participant_id=str(OWNER + 1))],
            personas=[dict(bot_id="bot-tg", persona_id="original", role_id="role-tg")], group_targets=[])
        self.controller = self.make_controller(self.config)

    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.plugin.staging = self.controller

    async def flush(self):
        observer = self.controller.session.core_reply_observations
        while pending := observer._pending - observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))
            # Done callbacks retire finished tasks on the next loop iteration.
            await asyncio.sleep(0)

    async def intake(self, **kwargs):
        event = telegram_event(self.platform, **kwargs)
        event.created_at = self.clock.now().timestamp()
        await self.plugin.receive_companion_message(event)
        await self.flush()
        return event

    async def test_admitted_human_private_text_has_an_isolated_native_identity(self):
        event = await self.intake()
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertIsNotNone(state)
        context = state["prepared"]["context"]
        self.assertEqual(context.actor.subject.subject_id, f"telegram-user:{OWNER}")
        self.assertEqual(context.scope.account_id, "tg-main")
        self.assertEqual(context.scope.native_scope_id, str(OWNER))
        self.assertTrue(event.is_at_or_wake_command)

    async def test_untrusted_or_non_private_updates_never_enter_companion_state(self):
        import copy
        mutations = {
            "group": lambda raw: raw["message"]["chat"].update(type="group"),
            "supergroup": lambda raw: raw["message"]["chat"].update(type="supergroup"),
            "channel": lambda raw: raw["message"]["chat"].update(type="channel"),
            "other_chat": lambda raw: raw["message"]["chat"].update(id=OWNER + 1),
            "other_sender": lambda raw: raw["message"]["from"].update(id=OWNER + 1),
            "bot": lambda raw: raw["message"]["from"].update(is_bot=True),
            "service": lambda raw: raw["message"].pop("text"),
            "topic": lambda raw: raw["message"].update(message_thread_id=9),
            "topic_flag": lambda raw: raw["message"].update(is_topic_message=True),
            "direct_topic": lambda raw: raw["message"].update(direct_messages_topic={"topic_id": 9}),
            "edited": lambda raw: raw.update(edited_message=raw.pop("message")),
            "callback": lambda raw: raw.pop("message"),
            "inline": lambda raw: raw.pop("message"),
            "guest": lambda raw: raw.update(guest_message=raw.pop("message")),
        }
        revision = self.controller.authority.identities.revision
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                event = telegram_event(self.platform)
                raw = copy.deepcopy(event.message_obj.raw_message.to_dict())
                mutate(raw)
                event.message_obj.raw_message = Update.de_json(raw, None)
                await self.plugin.receive_companion_message(event)
                self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA))
                result = await self.controller.prepare_core_reply(event)
                self.assertFalse(result.is_success)
        stranger = await self.intake(sender=OWNER + 2)
        self.assertIsNone(stranger.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        self.assertEqual(self.controller.authority.identities.revision, revision)
        self.platform.client.send_message.assert_not_awaited()

    async def test_client_lifecycle_and_replacement_fence_prepared_turns(self):
        event = await self.intake()
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        for owner, field, value in (
            (self.platform.application, "running", False),
            (self.platform, "_application_started", False),
            (self.platform, "_terminating", True),
            (self.platform.application, "bot", object()),
            (event, "client", object()),
            (self.platform.client, "id", BOT + 1),
            (event.session, "session_id", str(OWNER + 1)),
        ):
            with self.subTest(field=field):
                previous = getattr(owner, field)
                setattr(owner, field, value)
                result = await self.controller.authorize_core_reply(event, prepared)
                self.assertFalse(result.is_success)
                setattr(owner, field, previous)
        replacement = TelegramPlatform().client
        self.platform.client = replacement
        self.platform.application = NS(bot=replacement, running=True)
        self.assertFalse((await self.controller.authorize_core_reply(event, prepared)).is_success)
        current = await self.intake(identifier=2)
        self.assertIsNotNone(current.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        self.platform.client.send_message.assert_not_awaited()

    async def test_lifecycle_relies_on_the_public_running_flag(self):
        # A host that renames the adapter-private flags still answers on the SDK's public one.
        del self.platform._application_started, self.platform._terminating
        event = await self.intake()
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        self.assertTrue((await self.controller.authorize_core_reply(event, prepared)).is_success)
        self.platform.application.running = False
        self.assertFalse((await self.controller.authorize_core_reply(event, prepared)).is_success)

    async def test_text_tool_round_decorates_sends_and_persists_both_histories(self):
        import json
        from tests.telegram_reply_support import install_history, run_pipeline
        from companion_v2.conversation.native_history import dialogue
        manager = await install_history(self)
        event = await self.intake()
        agent = await run_pipeline(self, event, tool=True)
        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(agent.tool_results[0]["state"], "succeeded")
        self.assertEqual(agent.requests[0].func_tool.names(), ["companion_v2_turn_evidence"])
        self.assertNotIn("interaction.poke", agent.requests[0].system_prompt)
        self.assertNotIn("qzone", agent.requests[0].system_prompt.lower())
        sent = self.platform.client.send_message.await_args.kwargs
        self.assertEqual(str(sent["chat_id"]), str(OWNER))
        self.assertIn("我在这里", sent["text"])
        cid = await manager.get_curr_conversation_id(event.unified_msg_origin)
        history = json.loads((await manager.get_conversation(event.unified_msg_origin, cid)).history)
        self.assertEqual([item["role"] for item in history], ["user", "assistant", "tool", "assistant"])
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        rows = dialogue(self.app.messages, state["prepared"]["context"], self.clock.now())
        self.assertEqual([row["speaker"] for row in rows], ["user", "assistant"])
        self.assertIn("我在这里", rows[-1]["text"])
        self.assertEqual(self.platform.client.onebot_accesses, [])

    async def test_wildcard_and_first_run_binding_do_not_admit_a_telegram_stranger(self):
        wildcard = replace(self.controller.settings.targets[0], participant_id="*")
        self.controller.settings = replace(self.controller.settings, targets=(wildcard,))
        self.controller.session.bridge.settings = self.controller.settings
        event = await self.intake(sender=OWNER + 1, text="绑定 original")
        self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        self.assertFalse(self.controller.handles_binding(event))
        self.assertFalse((await self.controller.prepare_core_reply(event)).is_success)
        self.assertFalse((await self.controller.session.bridge.capture(event)).is_success)

    async def test_long_emoji_text_uses_bounded_sends_and_native_formatting_fallback(self):
        from telegram.error import BadRequest
        from tests.telegram_reply_support import install_history, run_pipeline
        await install_history(self)
        event = await self.intake()
        wire_texts = []

        async def send_message(**kwargs):
            if kwargs.get("parse_mode"):
                raise BadRequest("synthetic invalid entities")
            text = kwargs["text"]
            self.assertLessEqual(len(text), 4096)
            wire_texts.append(text)
        self.platform.client.send_message.side_effect = send_message
        body = "😀" * 5000 + " https://example.invalid"
        agent = await run_pipeline(self, event, text=body)
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual("".join(wire_texts), body)

    async def test_send_exceptions_stay_unknown_without_replay(self):
        from telegram.error import Forbidden, TimedOut
        from tests.telegram_reply_support import install_history, run_pipeline
        from companion_v2.model_gateway.core_observations import observations_for_role
        from companion_v2.conversation.native_history import dialogue
        await install_history(self)
        for identifier, outcomes, expected, returned, unknown, failed in (
            (1, [TimedOut()], "unknown", 0, 1, 0),
            (2, [None, TimedOut()], "unknown", 0, 1, 0),
            (3, [Forbidden("synthetic rejection")], "unknown", 0, 1, 0),
        ):
            with self.subTest(outcome=expected):
                event = await self.intake(identifier=identifier, text=f"说一段话 {identifier}")
                self.platform.client.send_message.reset_mock(side_effect=True)
                self.platform.client.send_message.side_effect = outcomes
                await run_pipeline(self, event, text="A" * 4096 + "B" * 32)
                state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
                prepared = state["prepared"]
                receipt = next(item for item in observations_for_role(self.app.runtime, prepared["context"])
                               if item["turn_id"] == prepared["turn_id"])["delivery"]
                self.assertEqual((receipt["state"], receipt["returned"], receipt["unknown"], receipt["failed"]),
                                 (expected, returned, unknown, failed))
                rows = dialogue(self.app.messages, prepared["context"], self.clock.now(), include_turns=True)
                sent = [row for row in rows if row["turn_id"] == prepared["turn_id"] and row["speaker"] == "assistant"]
                self.assertEqual(len(sent), returned)
                self.assertEqual(self.platform.client.send_message.await_count, len(outcomes))

    async def test_in_process_redelivery_does_not_run_model_or_rewrite_history(self):
        from tests.telegram_reply_support import install_history, run_pipeline
        manager = await install_history(self)
        event = await self.intake()
        await run_pipeline(self, event)
        cid = await manager.get_curr_conversation_id(event.unified_msg_origin)
        before = (await manager.get_conversation(event.unified_msg_origin, cid)).history
        duplicate = await self.intake()
        self.assertTrue(duplicate.call_llm)
        self.assertIsNone(duplicate.get_extra(CORE_REPLY_CONTEXT_EXTRA))
        agent = await run_pipeline(self, duplicate)
        self.assertEqual(agent.calls, [])
        self.assertEqual((await manager.get_conversation(event.unified_msg_origin, cid)).history, before)
        self.assertEqual(self.platform.client.send_message.await_count, 1)

    async def test_equal_message_ids_in_different_chats_are_separate_turns(self):
        from tests.telegram_reply_support import install_history, run_pipeline
        await install_history(self)
        first = await self.intake(identifier=7)
        await run_pipeline(self, first)
        second = await self.intake(identifier=7, sender=OWNER + 1)
        await run_pipeline(self, second)
        contexts = [event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"] for event in (first, second)]
        self.assertNotEqual(contexts[0].scope.conversation_id, contexts[1].scope.conversation_id)
        self.assertNotEqual(contexts[0].actor.subject, contexts[1].actor.subject)
        self.assertEqual(self.platform.client.send_message.await_count, 2)
        for sender in (OWNER, OWNER + 1):
            duplicate = await self.intake(identifier=7, sender=sender)
            self.assertTrue(duplicate.call_llm)
            agent = await run_pipeline(self, duplicate)
            self.assertEqual(agent.calls, [])
        self.assertEqual(self.platform.client.send_message.await_count, 2)

    async def test_external_update_command_keeps_native_authorization_and_reports(self):
        from copy import deepcopy
        from astrbot.core.config import DEFAULT_CONFIG
        from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
        from astrbot.core.star.star import star_map
        from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
        from tests.telegram_reply_support import install_history, run_pipeline
        await install_history(self)
        calls = []

        async def update(event):
            calls.append(event.message_str.split(maxsplit=1)[-1])
            yield event.plain_result("synthetic update report")

        module = "synthetic_updater"
        command = CommandFilter("update")
        handler = StarHandlerMetadata(event_type=EventType.AdapterMessageEvent, handler_full_name=module + ".update",
                                      handler_name="update", handler_module_path=module, handler=update,
                                      event_filters=[command, PermissionTypeFilter(PermissionType.ADMIN)], extras_configs={})
        command.init_handler_md(handler)
        star_map[module] = NS(name=module, display_name=module, activated=True, reserved=False)
        star_handlers_registry.append(handler)
        for original in tuple(star_handlers_registry):
            if original.handler_module_path == ENTRY.__name__ and original.event_type is EventType.AdapterMessageEvent:
                bound = replace(original, handler=getattr(self.plugin, original.handler_name))
                star_handlers_registry.remove(original)
                star_handlers_registry.append(bound)
                self.addCleanup(star_handlers_registry.append, original)
                self.addCleanup(star_handlers_registry.remove, bound)
        self.addCleanup(star_handlers_registry.remove, handler)
        self.addCleanup(star_map.pop, module, None)
        for admitted in (False, True):
            event = telegram_event(self.platform, text="/update status", identifier=10 + admitted)
            event.plugins_name = [module]
            config = deepcopy(DEFAULT_CONFIG)
            config.update(admins_id=[str(OWNER)] if admitted else [], wake_prefix=["/"])
            stage = WakingCheckStage()
            await stage.initialize(NS(astrbot_config=config, astrbot_config_id="synthetic", db_helper=None))
            await stage.process(event)
            if not admitted:
                self.assertTrue(event.is_stopped())
                self.assertEqual(calls, [])
                continue
            await self.plugin.receive_companion_message(event)
            self.assertIsNone(event.get_extra(CORE_REPLY_CONTEXT_EXTRA))
            self.assertFalse(event.call_llm)
            agent = await run_pipeline(self, event)
            self.assertEqual(agent.calls, [])
            self.assertEqual(calls, ["status"])
        self.assertIn("synthetic update report", self.platform.client.send_message.await_args.kwargs["text"])


class TelegramQQCoexistenceTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_or_removed_telegram_keeps_running_qq_but_startup_is_coupled(self):
        from astrbot.core.platform.astr_message_event import AstrMessageEvent
        from tests.test_core_reply_pipeline import PipelineEvent
        platform = TelegramPlatform()
        self.native.platforms["tg-main"] = platform
        self.native.conversations["tg-main"] = "telegram-conversation"
        self.native.selections["tg-main"] = "original"
        self.window_personas[f"tg-main:FriendMessage:{OWNER}"] = "original"
        self.types = replace(self.types, event=AstrMessageEvent)
        config = multi_config()
        config["multi_host"]["targets"].append(dict(target_id="tg-owner", platform_id="tg-main",
            account_id="tg-main", bot_id="bot-tg", participant_id=str(OWNER)))
        config["multi_host"]["personas"].append(dict(bot_id="bot-tg", persona_id="original", role_id="role-tg"))
        self.controller = self.make_controller(config)
        await self.start_host()
        qq = PipelineEvent()
        qq.bot = self.native.platforms["qq-main"].client
        qq.created_at = self.clock.now().timestamp()
        before = (await self.controller.prepare_core_reply(qq)).unwrap()
        tg = telegram_event(platform)
        tg.created_at = self.clock.now().timestamp()
        telegram = (await self.controller.prepare_core_reply(tg)).unwrap()
        self.assertNotEqual(before["context"].actor.subject, telegram["context"].actor.subject)
        from companion_v2.role_work.state.ownership import role_owner
        self.assertNotEqual(role_owner(before["context"]), role_owner(telegram["context"]))
        platform.application.running = False
        # Startup is deliberately still all-or-nothing in this contribution.
        self.assertEqual(self.controller.startup_readiness(), "host_account_unavailable")
        after = (await self.controller.prepare_core_reply(qq)).unwrap()
        self.assertEqual(before["context"].key, after["context"].key)
        self.native.platforms.pop("tg-main")
        self.assertTrue((await self.controller.prepare_core_reply(qq)).is_success)
        self.assertEqual(self.controller.startup_readiness(), "host_platform_unavailable")


class TelegramAdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_converter_drops_non_message_updates(self):
        from telegram import CallbackQuery, InlineQuery
        from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
        raw = telegram_event(TelegramPlatform()).message_obj.raw_message.message
        updates = [Update(1, **{field: raw}) for field in (
            "edited_message", "channel_post", "edited_channel_post", "business_message",
            "edited_business_message", "guest_message")]
        updates.extend((Update(1, callback_query=CallbackQuery("synthetic", raw.from_user, "synthetic", message=raw)),
                        Update(1, inline_query=InlineQuery("synthetic", raw.from_user, "hello", ""))))
        for update in updates:
            with self.subTest(update=tuple(update.to_dict())):
                self.assertIsNone(await TelegramPlatformAdapter.convert_message(None, update, NS()))

    async def test_host_session_reports_use_the_existing_telegram_sender(self):
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.platform.message_session import MessageSession
        from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter

        class ReportPlatform(TelegramPlatform, TelegramPlatformAdapter):
            pass

        platform = ReportPlatform()
        await platform.send_by_session(MessageSession("tg-main", MessageType.FRIEND_MESSAGE, str(OWNER)),
                                       MessageChain([Plain("synthetic background report")]))
        self.assertEqual(str(platform.client.send_message.await_args.kwargs["chat_id"]), str(OWNER))
        self.assertIn("synthetic background report", platform.client.send_message.await_args.kwargs["text"])
