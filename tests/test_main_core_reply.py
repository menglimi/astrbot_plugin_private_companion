"""The V2 intake hands admitted turns to AstrBot's core response path."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.conversation.private_debounce import Policy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from tests.test_main_no_reply import ENTRY


class CoreReplyEvent:
    def __init__(self):
        self.platform_meta = NS(name="aiocqhttp")
        self.message_obj = NS(type=NS(value="FriendMessage"), raw_message={"post_type": "message"},
                              message=[NS(type="Plain", text="摸摸")])
        self.message_str = "摸摸"
        self.call_llm = False
        self.is_wake = False
        self.is_at_or_wake_command = False
        self._has_send_oper = False
        self.extra = {}
        self.sent = []
        self.unified_msg_origin = "qq-main:FriendMessage:200"
        self.send = self._send
        self.send_streaming = self._send_streaming

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def should_call_llm(self, value):
        self.call_llm = value

    def stop_event(self):
        self.stopped = True

    async def _send(self, message):
        self.sent.append(message)
        self._has_send_oper = True

    async def _send_streaming(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class MainCoreReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_admitted_private_turn_uses_core_and_rechecks_before_delivery(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        event = CoreReplyEvent()
        target = NS(key="target", target_id="target")
        prepared = {
            "context": object(),
            "instruction": "Use this companion persona.",
            "prompt_instruction": "Reply conversationally.",
            "stable_prompt_instruction": "Use the configured companion voice.",
            "target_id": "target",
            "channel": "private",
            "event_id": "event-1",
        }
        staging = NS(
            runtime=None,
            now=lambda: None,
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            settings=NS(enabled=True),
            session=NS(profile=NS(target=target)),
            session_topology=NS(
                reply_enabled=AsyncMock(return_value=True),
                schedule_observe=Mock(),
            ),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging
        staging.session.core_reply_observations = NS(phase=Mock())

        await plugin.receive_companion_message(event)

        staging.persona_mapping_for_event.assert_not_awaited()
        staging.prepare_core_reply.assert_awaited_once_with(event, target, group_admission=None)
        self.assertTrue(event.is_wake)
        self.assertTrue(event.is_at_or_wake_command)
        self.assertFalse(event.call_llm)

        request = NS(system_prompt="AstrBot base prompt")
        await plugin.prepare_companion_core_request(event, request)
        staging.session.core_reply_observations.phase.assert_called_once()
        self.assertEqual(staging.session.core_reply_observations.phase.call_args.args[1], "running")
        self.assertNotIn(prepared["instruction"], request.system_prompt)
        self.assertIn(prepared["stable_prompt_instruction"], request.system_prompt)
        self.assertNotIn(prepared["prompt_instruction"], request.system_prompt)
        self.assertIn(prepared["prompt_instruction"], request.extra_user_content_parts[-1].text)
        self.assertTrue(request.extra_user_content_parts[-1]._no_save)
        steps = event.get_extra(ENTRY.CORE_REPLY_CONTEXT_EXTRA)["_preparation_steps"]
        self.assertEqual([item["name"] for item in steps], ["request_authorization", "prompt_injection",
            "send_tool_injection", "perception_injection"])
        self.assertTrue(all(type(item["elapsed_ms"]) is int and item["elapsed_ms"] >= 0 for item in steps))
        staging.authorize_core_reply.assert_awaited_once()
        authorization_args = staging.authorize_core_reply.await_args.args
        self.assertEqual(authorization_args[:2], (event, target))
        prepared_for_core = authorization_args[2]
        self.assertEqual(prepared_for_core["event_id"], prepared["event_id"])
        self.assertEqual(prepared_for_core["channel"], "private")

        await event.send("Other plugin notice")
        self.assertEqual(staging.authorize_core_reply.await_count, 1)

        await plugin.guard_companion_core_delivery(event)
        await event.send("AstrBot core reply")
        self.assertEqual(staging.authorize_core_reply.await_count, 2)
        self.assertEqual(event.sent, ["Other plugin notice", "AstrBot core reply"])

        await plugin.observe_companion_core_reply_sent(event)
        self.assertIs(event.send.__self__, event)
        self.assertIs(event.send.__func__, CoreReplyEvent._send)

    async def test_post_debounce_follow_up_joins_the_first_pending_model_request(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        first, second = CoreReplyEvent(), CoreReplyEvent()
        for event, text, identifier, created_at in (
                (first, "早上吃了什么", "native-first", 1_700_000_000),
                (second, "中午又吃了什么", "native-second", 1_700_000_001)):
            event.message_str = text
            event.message_obj.message = [NS(type="Plain", text=text)]
            event.message_obj.raw_message["message_id"] = identifier
            event.created_at = created_at
        target = NS(key="target", target_id="target")
        plan = NS(captured=NS(text="早上吃了什么"), context=NS(key="context"),
                  debounce=Policy(enabled=False))
        prepared = {
            "context": object(), "instruction": "Use this companion persona.",
            "prompt_instruction": "Reply conversationally.",
            "stable_prompt_instruction": "Use the configured companion voice.",
            "target_id": "target", "channel": "private", "event_id": "native-first",
            "event_ref": "event-first", "text": "早上吃了什么",
            "native_input": NS(text="早上吃了什么", event_id="native-first"),
        }
        staging = NS(
            runtime=None, now=lambda: None, claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True),
            session=NS(profile=NS(target=target)),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(return_value=Outcome.success(plan)),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging
        staging.session.core_reply_observations = NS(phase=Mock())

        await plugin.receive_companion_message(first)
        self.assertTrue(first.is_wake)
        self.assertTrue(first.is_at_or_wake_command)
        self.assertEqual(staging.prepare_core_reply.await_count, 1)

        await plugin.receive_companion_message(second)
        self.assertTrue(second.call_llm)
        self.assertFalse(second.is_wake)
        self.assertEqual(staging.prepare_core_reply.await_count, 1)

        request = NS(system_prompt="AstrBot base prompt", prompt="早上吃了什么",
                     extra_user_content_parts=[])
        await plugin.prepare_companion_core_request(first, request)
        self.assertEqual(request.prompt, "早上吃了什么\n中午又吃了什么")
        current = first.extra[ENTRY.CORE_REPLY_CONTEXT_EXTRA]["prepared"]
        self.assertEqual(current["text"], "早上吃了什么\n中午又吃了什么")
        self.assertEqual(current["debounced_message_count"], 2)
        self.assertEqual([item["text"] for item in current["debounced_inputs"]],
                         ["早上吃了什么", "中午又吃了什么"])

    async def test_private_text_burst_uses_latest_event_for_merge_and_authorization(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        first, second = CoreReplyEvent(), CoreReplyEvent()
        for event, text, identifier, created_at in (
                (first, "在吗", "native-first", 1_700_000_000),
                (second, "我想问你个事", "native-latest", 1_700_000_001)):
            event.message_str = text
            event.message_obj.message = [NS(type="Plain", text=text)]
            event.message_obj.raw_message["message_id"] = identifier
            event.created_at = created_at
        target = NS(key="target", target_id="target")
        prepared_results = []
        first_context_started = asyncio.Event()
        second_context_started = asyncio.Event()
        release_first_context = asyncio.Event()
        plan = NS(captured=NS(text="在吗"), context=NS(key="context"),
                  debounce=Policy(enabled=True, min_seconds=0.05,
                                 max_seconds=0.08, total_seconds=0.2))

        async def prepare_context(event, _target, **_kwargs):
            if event is first:
                first_context_started.set()
                await release_first_context.wait()
            else:
                second_context_started.set()
            return Outcome.success(plan)

        async def prepare(event, _target, **kwargs):
            self.assertIs(event, second)
            self.assertIs(kwargs.get("plan"), plan)
            self.assertEqual(plan.captured.text, "在吗\n我想问你个事")
            prepared = {
                "context": object(), "instruction": "Use this companion persona.",
                "prompt_instruction": "Reply conversationally.",
                "stable_prompt_instruction": "Use the configured companion voice.",
                "target_id": "target", "channel": "private",
                "event_id": event.message_obj.raw_message["message_id"],
                "text": event.message_str,
                "native_input": NS(text=event.message_str,
                                    event_id=event.message_obj.raw_message["message_id"]),
            }
            prepared_results.append(prepared)
            return Outcome.success(prepared)

        staging = NS(
            runtime=None, now=lambda: None, claims=lambda _event: True,
            target_for_event=lambda _event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            settings=NS(enabled=True),
            session=NS(profile=NS(target=target)),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging
        staging.session.core_reply_observations = NS(phase=Mock())
        first_task = asyncio.create_task(plugin.receive_companion_message(first))
        second_task = None
        try:
            await asyncio.wait_for(first_context_started.wait(), 2)
            second_task = asyncio.create_task(plugin.receive_companion_message(second))
            await asyncio.wait_for(second_context_started.wait(), 2)
            release_first_context.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), 2)
        finally:
            release_first_context.set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
        staging.prepare_core_reply.assert_awaited_once()
        self.assertIs(staging.prepare_core_reply.await_args.args[0], second)
        self.assertEqual(first.message_str, "在吗")
        self.assertTrue(first.call_llm)
        self.assertTrue(second.is_wake)
        self.assertTrue(second.is_at_or_wake_command)
        self.assertEqual(second.message_str, "在吗\n我想问你个事")
        self.assertEqual(len(prepared_results), 1)
        current = second.extra[ENTRY.CORE_REPLY_CONTEXT_EXTRA]["prepared"]
        self.assertEqual(current["event_id"], "native-latest")
        self.assertEqual(current["native_input"].event_id, "native-latest")
        self.assertEqual(current["text"], second.message_str)
        self.assertEqual(current["debounced_message_count"], 2)
        self.assertEqual([item["text"] for item in current["debounced_inputs"]], ["在吗", "我想问你个事"])

        await plugin.guard_companion_core_delivery(second)
        await second.send("回复正文")
        auth_event, auth_target, authorized = staging.authorize_core_reply.await_args.args
        self.assertIs(auth_event, second)
        self.assertIs(auth_target, target)
        self.assertEqual(authorized["event_id"], "native-latest")

    async def test_stale_event_reply_disabled_does_not_abort_latest_pending(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        first, second = CoreReplyEvent(), CoreReplyEvent()
        for event, text, identifier in ((first, "在吗", "native-first"),
                                        (second, "我想问你个事", "native-latest")):
            event.message_str = text
            event.message_obj.message = [NS(type="Plain", text=text)]
            event.message_obj.raw_message["message_id"] = identifier
        target = NS(key="target", target_id="target")
        first_check_started = asyncio.Event()
        release_first_check = asyncio.Event()
        second_context_started = asyncio.Event()
        plan = NS(captured=NS(text="我想问你个事"), context=NS(key="context"),
                  debounce=Policy(enabled=True, min_seconds=0.03,
                                 max_seconds=0.05, total_seconds=0.12))

        async def reply_enabled(event):
            if event is first:
                first_check_started.set()
                await release_first_check.wait()
                return False
            return True

        async def prepare_context(event, _target, **_kwargs):
            if event is second:
                second_context_started.set()
            return Outcome.success(plan)

        async def prepare(event, _target, **_kwargs):
            return Outcome.success({
                "context": object(), "instruction": "Use this companion persona.",
                "prompt_instruction": "Reply conversationally.",
                "stable_prompt_instruction": "Use the configured companion voice.",
                "target_id": "target", "channel": "private", "event_id": "native-latest",
                "text": event.message_str, "native_input": NS(text=event.message_str),
            })

        staging = NS(
            runtime=None, now=lambda: None, claims=lambda _event: True,
            target_for_event=lambda _event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            settings=NS(enabled=True), session=NS(profile=NS(target=target)),
            session_topology=NS(reply_enabled=AsyncMock(side_effect=reply_enabled), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging
        staging.session.core_reply_observations = NS(phase=Mock())
        first_task = asyncio.create_task(plugin.receive_companion_message(first))
        second_task = None
        try:
            await asyncio.wait_for(first_check_started.wait(), 2)
            second_task = asyncio.create_task(plugin.receive_companion_message(second))
            await asyncio.wait_for(second_context_started.wait(), 2)
            release_first_check.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), 2)
        finally:
            release_first_check.set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        self.assertTrue(first.call_llm)
        staging.prepare_core_reply.assert_awaited_once()
        self.assertIs(staging.prepare_core_reply.await_args.args[0], second)
        self.assertTrue(second.is_at_or_wake_command)

    async def test_stale_busy_check_cannot_suppress_reused_latest_event(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        event = CoreReplyEvent()
        event.message_obj.message = [NS(type="Plain", text="在吗")]
        event.message_obj.raw_message["message_id"] = "native-first"
        event.message_str = "在吗"
        event.created_at = 1_700_000_000
        target = NS(key="target", target_id="target")
        first_check_started = asyncio.Event()
        release_first_check = asyncio.Event()
        latest_context_started = asyncio.Event()
        reply_checks = 0
        plan = NS(captured=NS(text="在吗"), context=NS(key="context"),
                  debounce=Policy(enabled=True, min_seconds=0.02,
                                 max_seconds=0.03, total_seconds=0.08))

        async def reply_enabled(_event):
            nonlocal reply_checks
            reply_checks += 1
            if reply_checks == 1:
                first_check_started.set()
                await release_first_check.wait()
                return False
            return True

        async def prepare_context(_event, _target, **_kwargs):
            latest_context_started.set()
            return Outcome.success(plan)

        async def prepare(current, _target, **kwargs):
            self.assertIs(current, event)
            self.assertEqual(kwargs["plan"].captured.text, "在吗\n我想问你个事")
            event_id = current.message_obj.raw_message["message_id"]
            return Outcome.success({
                "context": object(), "instruction": "Use this companion persona.",
                "prompt_instruction": "Reply conversationally.",
                "stable_prompt_instruction": "Use the configured companion voice.",
                "target_id": "target", "channel": "private", "event_id": event_id,
                "text": current.message_str, "native_input": NS(text=current.message_str, event_id=event_id),
            })

        staging = NS(
            runtime=None, now=lambda: None, claims=lambda _event: True,
            target_for_event=lambda _event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            settings=NS(enabled=True), session=NS(profile=NS(target=target)),
            session_topology=NS(reply_enabled=AsyncMock(side_effect=reply_enabled), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging
        staging.session.core_reply_observations = NS(phase=Mock())
        first_task = asyncio.create_task(plugin.receive_companion_message(event))
        latest_task = None
        try:
            await asyncio.wait_for(first_check_started.wait(), 2)
            event.message_obj.message = [NS(type="Plain", text="我想问你个事")]
            event.message_obj.raw_message["message_id"] = "native-latest"
            event.message_str = "我想问你个事"
            event.created_at = 1_700_000_001
            latest_task = asyncio.create_task(plugin.receive_companion_message(event))
            await asyncio.wait_for(latest_context_started.wait(), 2)
            await asyncio.wait_for(latest_task, 2)
            release_first_check.set()
            await asyncio.wait_for(first_task, 2)
        finally:
            release_first_check.set()
            for task in (first_task, latest_task):
                if task is not None and not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        staging.prepare_core_reply.assert_awaited_once()
        self.assertIs(staging.prepare_core_reply.await_args.args[0], event)
        self.assertEqual(event.message_str, "在吗\n我想问你个事")
        self.assertEqual(event.message_obj.raw_message["message_id"], "native-latest")
        self.assertFalse(event.call_llm)
        self.assertEqual(event.extra[ENTRY.CORE_REPLY_CONTEXT_EXTRA]["prepared"]["event_id"],
                         "native-latest")

    async def test_core_request_injects_configured_model_route_perception(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        event = CoreReplyEvent()
        event.extra["selected_provider"] = "chat-route"
        target = NS(key="target", target_id="target")
        provider = NS(provider_config={"id": "chat-route", "modalities": ["text", "image"]},
                      meta=lambda: NS(id="chat-route"), get_model=lambda: "qwen-chat-alias")
        tts = NS(provider_config={"id": "voice-route", "type": "fishaudio_tts_api"},
                 get_model=lambda: "drama-3", get_audio=AsyncMock())
        observations = NS(phase=Mock())
        staging = NS(
            runtime=None,
            now=lambda: None,
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True),
            context=NS(get_provider_by_id=Mock(side_effect=lambda key: tts if key == "voice-route" else provider),
                       get_using_provider_async=AsyncMock(), get_all_tts_providers=lambda: [tts]),
            session=NS(profile=NS(target=target), core_reply_observations=observations),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply=AsyncMock(return_value=Outcome.success({
                "context": NS(scope=NS(platform="qq-main")),
                "instruction": "Use this companion persona.",
                "prompt_instruction": "Reply conversationally.",
                "stable_prompt_instruction": "Use the configured companion voice.",
                "perception_options": (True, True, True),
                "configured_voice_reply_policy": VoiceReplyPolicy(provider_id="voice-route", trigger_mode="request"),
                "target_id": "target", "channel": "private", "event_id": "event-1",
            })),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging

        await plugin.receive_companion_message(event)
        request = NS(system_prompt="AstrBot base prompt", model=None, image_urls=[], audio_urls=[], func_tool=None)
        await plugin.prepare_companion_core_request(event, request)

        self.assertIn("自然交谈", request.system_prompt)
        self.assertIn("Use the configured companion voice.", request.system_prompt)
        self.assertNotIn("Reply conversationally.", request.system_prompt)
        self.assertNotIn("chat-route", request.system_prompt)
        perception = request.extra_user_content_parts[-1].text
        self.assertIn("Reply conversationally.", perception)
        self.assertIn('"platform":"qq-main"', perception)
        self.assertIn('"provider_id":"chat-route"', perception)
        self.assertIn('"model":"qwen-chat-alias"', perception)
        self.assertIn('"values":["text","image"]', perception)
        self.assertIn('"tts_route":', perception)
        self.assertIn('"provider_id":"voice-route"', perception)
        self.assertIn('"model":"drama-3"', perception)
        self.assertIn('"companion_voice_active_this_turn":false', perception)
        self.assertNotIn("drama-3", request.system_prompt)
        tts.get_audio.assert_not_awaited()
        staging.context.get_using_provider_async.assert_not_awaited()

    async def test_native_runner_completion_observes_assistant_empty_and_error_phases_without_awaiting_storage(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        phase = Mock()
        plugin.staging = NS(session=NS(core_reply_observations=NS(phase=phase)))
        event = CoreReplyEvent()
        prepared = {"event_id": "phase-event", "turn_id": "phase-turn", "context": object()}
        ENTRY.prepare_core_reply_event(event, prepared, authorize=AsyncMock(return_value=Outcome.success(True)))
        for response, expected in ((NS(role="assistant", completion_text=""), "completed"), (None, "failed"),
                                   (NS(role="err", completion_text="model failed"), "failed")):
            await plugin.observe_companion_core_agent_done(event, NS(), response)
            self.assertEqual(phase.call_args.args, (prepared, expected))
        event.get_result = lambda: NS(result_content_type=NS(name="AGENT_RUNNER_ERROR"))
        await plugin.observe_companion_core_reply_sent(event)
        self.assertEqual(phase.call_args.args, (prepared, "failed"))
        phase.side_effect = OSError("optional background unavailable")
        await plugin.observe_companion_core_agent_done(event, NS(), NS(role="assistant"))
        self.assertEqual(event.sent, [])

    async def test_admitted_group_turn_keeps_candidate_and_enables_core_wake(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        event = CoreReplyEvent()
        event.message_obj.type = NS(value="GroupMessage")
        event.message_str = "小星来看看"
        target = NS(key="group-target", target_id="group-target")
        group_admission = {
            "admitted": True,
            "reason": "direct_word",
            "mentioned": False,
            "context_key": "group-context",
            "event_id": "group-event-1",
        }
        prepared = {
            "context": object(),
            "instruction": "Use the group companion persona.",
            "prompt_instruction": "Reply only when appropriate.",
            "target_id": "group-target",
            "channel": "group",
            "event_id": "group-event-1",
        }
        staging = NS(
            runtime=None,
            now=lambda: None,
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            settings=NS(enabled=True),
            session=NS(profile=NS(target=target)),
            session_topology=NS(
                reply_enabled=AsyncMock(return_value=True),
                schedule_observe=Mock(),
            ),
            group_reply_candidate=AsyncMock(return_value=NS(
                failure=None, unwrap=lambda: group_admission)),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging

        await plugin.receive_companion_message(event)

        staging.persona_mapping_for_event.assert_not_awaited()
        staging.prepare_core_reply.assert_awaited_once_with(
            event, target, group_admission=group_admission)
        self.assertTrue(event.is_wake)
        self.assertTrue(event.is_at_or_wake_command)
        state = event.get_extra("_companion_v2_core_reply_context")
        self.assertEqual(state["prepared"]["channel"], "group")

    async def test_slow_directory_observation_does_not_delay_intake_or_model_response(self):
        from astrbot_host.session_topology import SessionTopology

        temporary = TemporaryDirectory(prefix="companion-main-directory-")
        self.addCleanup(temporary.cleanup)
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        event = CoreReplyEvent()
        target = NS(key="target", target_id="target")
        staging = NS(data_root=Path(temporary.name), _terminated=False, runtime=None, now=lambda: None,
            claims=lambda event: True, target_for_event=lambda event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="persona")),
            session=NS(profile=NS(target=target)), settings=NS(enabled=True),
            prepare_core_reply=AsyncMock(return_value=Outcome.success({"event_id": "directory-slow"})),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)))
        topology = SessionTopology(staging)
        staging.session_topology = topology
        topology.reply_enabled = AsyncMock(return_value=True)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_observe(event):
            entered.set()
            await release.wait()

        topology.observe = slow_observe
        plugin.staging = staging
        try:
            await asyncio.wait_for(plugin.receive_companion_message(event), 2)
            await asyncio.wait_for(entered.wait(), 2)
            self.assertTrue(event.is_at_or_wake_command)
            self.assertTrue(topology._pending)
            await asyncio.wait_for(plugin.observe_companion_model_session(event,
                NS(role="assistant", completion_text="摸摸你")), 2)
        finally:
            release.set()
            await topology.close()
        self.assertFalse(topology._pending)

    async def test_hot_reload_new_instance_adopts_in_flight_core_reply(self):
        event = CoreReplyEvent()
        event.get_result = lambda: NS(result_content_type=NS(name="LLM_RESULT"))
        target = NS(key="target", target_id="target")
        prepared = {
            "context": object(),
            "instruction": "Use this companion persona.",
            "target_id": "target",
            "channel": "private",
            "event_id": "in-flight-event",
            "event_ref": "in-flight-event",
            "origin_key": "origin",
        }
        old_authorize = AsyncMock(return_value=Outcome.failed(
            "host_not_claimed", "the native event is outside the active configured targets"))
        on_denied = Mock()
        ENTRY.prepare_core_reply_event(
            event, prepared, authorize=old_authorize, on_denied=on_denied, owner=object())
        ENTRY.activate_core_reply_delivery_guard(event)

        with self.assertRaises(TaskError):
            await event.send("stale host reply")
        self.assertEqual(event.sent, [])
        on_denied.assert_called_once()

        new_authorize = AsyncMock(return_value=Outcome.success(True))
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS(
            now=lambda: None,
            target_for_event=lambda _event: target,
            authorize_core_reply=new_authorize,
            session=NS(),
        )

        await plugin.guard_companion_core_delivery(event)
        state = event.get_extra(ENTRY.CORE_REPLY_CONTEXT_EXTRA)
        self.assertIs(state["owner"], plugin._core_reply_owner_token())
        self.assertFalse(state["denial_notified"])

        await event.send("recovered core reply")

        new_authorize.assert_awaited_once()
        self.assertEqual(new_authorize.await_args.args[:2], (event, target))
        self.assertEqual(event.sent, ["recovered core reply"])
        self.assertEqual((state["send_attempts"], state["send_returns"], state["send_unknown"]), (1, 1, 0))


    async def test_denied_send_names_the_gate_and_says_the_reply_was_not_sent(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS(now=lambda: None)
        plugin._record_no_reply = Mock()
        event = CoreReplyEvent()
        prepared = {"event_ref": "offline-event", "origin_key": "origin", "channel": "group"}

        callbacks = plugin._core_reply_delivery_callbacks(event, prepared, target=None)
        with patch.object(ENTRY.logger, "warning") as warning:
            callbacks["on_denied"](prepared, TaskError(
                "host_client_offline", "this account's native client is not connected"))

        self.assertEqual(plugin._record_no_reply.call_args.args[1], "core_reply_send_denied")
        self.assertEqual(plugin._record_no_reply.call_args.kwargs["code"], "host_client_offline")
        template, *arguments = warning.call_args.args
        self.assertIn("这条已生成的回复没有发出", template)
        self.assertEqual(arguments[1], "host_client_offline")
        self.assertEqual(arguments[2], ENTRY.CORE_REPLY_SEND_DENIAL_HINTS["host_client_offline"])


if __name__ == "__main__":
    unittest.main()
