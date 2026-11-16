"""Account replacement stops the original native turn and its remaining effects."""

import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401
from tests.test_astrbot_core_reply import FakeEvent
from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard, prepare_core_reply_event,
)
from astrbot_host.core_reply_tools import CoreReplyToolSession
from astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome


class Connection:
    def __init__(self):
        self.changed = asyncio.Event()

    def assert_current(self):
        if self.changed.is_set():
            raise TaskError("host_connection_changed", "original account connection was replaced")


class CoreReplyConnectionTests(unittest.IsolatedAsyncioTestCase):
    def prepare(self, connection, *, event=None, watch=False):
        event = event or FakeEvent()
        prepare_core_reply_event(event, {"event_id": "original-turn"},
            authorize=AsyncMock(return_value=Outcome.success(True)),
            assert_connection_current=connection.assert_current,
            wait_connection_changed=connection.changed.wait if watch else None)
        activate_core_reply_delivery_guard(event)
        return event

    async def test_connection_change_during_media_conversion_never_sends(self):
        connection = Connection()
        event = self.prepare(connection)

        async def converted(_, message):
            connection.changed.set()
            await asyncio.sleep(0)
            return message

        with patch("astrbot_host.core_reply_media_delivery.native_wire_message", converted):
            with self.assertRaises(TaskError) as stopped:
                await event.send("old reply")
        self.assertEqual(stopped.exception.code, "host_connection_changed")
        self.assertEqual(event.sent, [])
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["send_attempts"], 0)

    async def test_remaining_bubbles_stop_after_connection_changes(self):
        connection = Connection()
        event = FakeEvent()

        async def send(message):
            event.sent.append(message)
            connection.changed.set()
            return True

        event.send = send
        self.prepare(connection, event=event)
        with patch("astrbot_host.core_reply_media_delivery.native_reply_messages", return_value=("first", "second")):
            with self.assertRaises(TaskError):
                await event.send("two bubbles")
        self.assertEqual(event.sent, ["first"])
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertEqual((state["send_attempts"], state["send_returns"], state["send_unknown"]), (1, 1, 0))

    async def test_stream_connection_change_stops_following_chunk(self):
        connection = Connection()
        event = FakeEvent()

        async def send_stream(generator):
            async for item in generator:
                event.sent.append(item)

        event.send_streaming = send_stream
        self.prepare(connection, event=event)

        async def chunks():
            yield "first"
            connection.changed.set()
            yield "second"

        with patch("astrbot_host.core_reply_presentation.strip_internal_expression_stream", side_effect=lambda value: value), \
                patch("astrbot_host.core_reply_presentation.core_reply_presentation", return_value=None):
            with self.assertRaises(TaskError):
                await event.send_streaming(chunks())
        self.assertEqual(event.sent, ["first"])

    async def test_only_affected_turn_is_cancelled_while_waiting_for_model(self):
        first, second = Connection(), Connection()
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        events = []

        async def turn(connection, index):
            event = self.prepare(connection, watch=True)
            events.append(event)
            entered[index].set()
            await release.wait()
            await event.send("fresh answer")
            return event

        tasks = [asyncio.create_task(turn(first, 0)), asyncio.create_task(turn(second, 1))]
        try:
            await asyncio.gather(*(value.wait() for value in entered))
            first.changed.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(tasks[0], 1)
            self.assertFalse(tasks[1].done())
            release.set()
            healthy = await asyncio.wait_for(tasks[1], 1)
            self.assertEqual(len(healthy.sent), 1)
            await asyncio.sleep(0)
            self.assertTrue(all("_connection_waiter" not in event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
                                for event in events))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_tool_context_is_stale_even_when_application_is_running(self):
        connection = Connection()
        event = self.prepare(connection)
        session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        session.event, session.state = event, event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        session.prepared = session.state["prepared"]
        session.application = NS(require_running=lambda: None)
        connection.changed.set()
        with self.assertRaises(TaskError) as stopped:
            session.assert_current()
        self.assertEqual(stopped.exception.code, "host_connection_changed")

    async def test_native_external_tool_round_is_not_dispatched_for_stale_account(self):
        connection = Connection()
        event = self.prepare(connection)
        calls = []

        class Runner:
            async def _handle_function_tools(self):
                calls.append("external tool")
                yield NS(kind="tool_call_result_blocks", tool_call_result_blocks=[])

            async def step(self):
                calls.append("model")
                yield None

            def done(self):
                return False

        runner = Runner()
        with patch("astrbot_host.core_reply_tool_dispatch.active_core_runner", return_value=runner):
            self.assertIsNotNone(observe_core_tool_dispatch_begin(event, object()))
            connection.changed.set()
            with self.assertRaises(TaskError):
                async for _ in runner._handle_function_tools():
                    pass
        self.assertEqual(calls, [])
