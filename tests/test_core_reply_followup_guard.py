"""Media follow-up serialization, arrival ordering, and host follow-up cleanup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_followup_guard import (
    CoreReplyFollowUpGuard,
    event_media_kinds,
    event_requires_media_serialization,
    runner_can_capture_followup,
)
from astrbot_host.core_reply_tracking import CORE_REPLY_TRACKING_EXTRA
from companion_v2.foundation.outcome import Outcome

_HAS_ASTRBOT_SDK = importlib.util.find_spec("astrbot") is not None


def _stub_event(
    *,
    text: str = "",
    components: list | tuple = (),
    umo: str = "onebot_qq:GroupMessage:10001",
    sender_id: str = "20001",
    channel: str = "group",
    message_id: str = "msg-1",
    extras: dict | None = None,
):
    msg_type = "FriendMessage" if channel == "private" else "GroupMessage"
    ev = type("StubEvent", (), {})()
    ev.platform_meta = NS(name="aiocqhttp")
    ev.unified_msg_origin = umo
    ev.session_id = umo
    ev.message_str = text
    ev.call_llm = False
    ev.is_wake = False
    ev.is_at_or_wake_command = False
    ev._has_send_oper = False
    ev._stopped = False
    ev.sent = []
    ev.created_at = datetime(2026, 11, 22, 3, 0, 0, tzinfo=timezone.utc)
    ev.message_obj = NS(
        type=NS(value=msg_type),
        raw_message={"post_type": "message"},
        message=list(components),
        message_id=message_id,
        message_str=text,
        sender=NS(user_id=sender_id),
    )
    extra_store = dict(extras or {})
    ev.extra = extra_store

    def get_sender_id():
        return sender_id

    def get_messages():
        return ev.message_obj.message

    def get_message_str():
        return ev.message_str

    def get_message_outline():
        parts = []
        for comp in ev.message_obj.message:
            kind = getattr(getattr(comp, "type", None), "value", getattr(comp, "type", None))
            if kind == "Plain":
                parts.append(getattr(comp, "text", ""))
            elif kind == "Image":
                parts.append("[图片]")
            elif kind == "Video":
                parts.append("[视频]")
            elif kind == "Record":
                parts.append("[语音]")
            elif kind == "File":
                parts.append("[文件]")
        return "".join(parts)

    def get_extra(key, default=None):
        return extra_store.get(key, default)

    def set_extra(key, value):
        extra_store[key] = value

    def should_call_llm(value):
        ev.call_llm = bool(value)

    def stop_event():
        ev._stopped = True

    def is_stopped():
        return ev._stopped

    async def send(message):
        ev.sent.append(message)
        ev._has_send_oper = True

    async def send_streaming(*args, **kwargs):
        ev.sent.append((args, kwargs))

    ev.get_sender_id = get_sender_id
    ev.get_messages = get_messages
    ev.get_message_str = get_message_str
    ev.get_message_outline = get_message_outline
    ev.get_extra = get_extra
    ev.set_extra = set_extra
    ev.should_call_llm = should_call_llm
    ev.stop_event = stop_event
    ev.is_stopped = is_stopped
    ev._original_send_ref = send
    ev.send = send
    ev.send_streaming = send_streaming
    return ev


class _FakeRunner:
    """Minimal stand-in matching AstrBot ToolLoopAgentRunner follow-up contract."""

    def __init__(self, event, *, stop_requested=False):
        self.run_context = NS(context=NS(event=event))
        self._done = False
        self._stop = stop_requested
        self._seq = 0
        self.pending = []
        self.consumed_texts = []

    def done(self):
        return self._done

    def _is_stop_requested(self):
        return self._stop

    def request_stop(self):
        self._stop = True

    def follow_up(self, *, message_text: str):
        if self._done or self._stop:
            return None
        text = (message_text or "").strip()
        if not text:
            return None
        ticket = NS(seq=self._seq, text=text, consumed=False, resolved=asyncio.Event())
        self._seq += 1
        self.pending.append(ticket)
        return ticket

    def consume_tool_followups(self):
        items = list(self.pending)
        self.pending.clear()
        for ticket in items:
            ticket.consumed = True
            ticket.resolved.set()
            self.consumed_texts.append(ticket.text)
        return [ticket.text for ticket in items]

    def finish(self):
        self._done = True
        items = list(self.pending)
        self.pending.clear()
        for ticket in items:
            ticket.resolved.set()


class MediaDetectionTests(unittest.TestCase):
    def test_plain_at_and_face_do_not_require_media_serialization(self):
        ev = _stub_event(
            text="早上好",
            components=[
                NS(type="At", qq="999"),
                NS(type="Plain", text="早上好"),
                NS(type="Face", id=12),
            ],
        )
        self.assertEqual(event_media_kinds(ev), ())
        self.assertFalse(event_requires_media_serialization(ev))

    def test_direct_media_components_are_detected(self):
        cases = [
            ([NS(type="Image", file="a.png")], ("image",)),
            ([NS(type="Plain", text="看看"), NS(type="Image", file="a.png")], ("image",)),
            ([NS(type="Video", file="clip.mp4")], ("video",)),
            ([NS(type="Record", file="voice.amr")], ("record",)),
            ([{"type": "audio", "file": "voice.ogg"}], ("record",)),
            ([NS(type="File", name="report.pdf")], ("file",)),
            ([NS(type="Forward", id="fwd-1")], ("forward",)),
            ([NS(type="Node", content=[])], ("node",)),
            ([NS(type="Nodes", nodes=[])], ("nodes",)),
        ]
        for components, expected in cases:
            with self.subTest(expected=expected):
                ev = _stub_event(components=components)
                self.assertEqual(event_media_kinds(ev), expected)
                self.assertTrue(event_requires_media_serialization(ev))

    def test_reply_components_distinguish_media_unresolved_and_plain(self):
        quoted_img = _stub_event(
            text="这是什么",
            components=[
                NS(type="Reply", id="r-1", chain=[NS(type="Image", file="cat.png")]),
                NS(type="Plain", text="这是什么"),
            ],
        )
        self.assertEqual(event_media_kinds(quoted_img), ("reply_image",))

        nested_quote = _stub_event(
            components=[
                NS(type="Reply", id="r-2", chain=[
                    NS(type="Reply", id="r-1", chain=[NS(type="File", name="spec.pdf")])
                ]),
            ],
        )
        self.assertEqual(event_media_kinds(nested_quote), ("reply_file",))

        unresolved_quote = _stub_event(
            text="看看这条",
            components=[NS(type="Reply", id="98765", chain=[]), NS(type="Plain", text="看看这条")],
        )
        self.assertEqual(event_media_kinds(unresolved_quote), ("reply_unresolved",))

        plain_quote = _stub_event(
            text="好的",
            components=[
                NS(type="Reply", id="r-3", chain=[NS(type="Plain", text="早安")]),
                NS(type="Plain", text="好的"),
            ],
        )
        self.assertEqual(event_media_kinds(plain_quote), ())

        cyclic = NS(type="Reply", id="r-cycle", chain=[])
        cyclic.chain.append(cyclic)
        cyclic_ev = _stub_event(components=[cyclic])
        self.assertEqual(event_media_kinds(cyclic_ev), ())

    def test_provider_request_extra_media_is_detected(self):
        ev_img = _stub_event(extras={"provider_request": NS(image_urls=["img.png"], audio_urls=[])})
        self.assertEqual(event_media_kinds(ev_img), ("provider_request_media",))

        ev_part = _stub_event(
            extras={
                "provider_request": NS(
                    image_urls=[],
                    audio_urls=[],
                    extra_user_content_parts=[NS(type="audio_url", audio_url=NS(url="data:video/mp4;base64,AA=="))],
                )
            }
        )
        self.assertEqual(event_media_kinds(ev_part), ("provider_request_media",))

    def test_runner_can_capture_followup_matches_host_eligibility(self):
        ev1 = _stub_event(sender_id="u1")
        runner = _FakeRunner(ev1)
        self.assertTrue(runner_can_capture_followup(runner, "u1"))
        self.assertFalse(runner_can_capture_followup(runner, "u2"))
        self.assertFalse(runner_can_capture_followup(runner, "u1", exclude_event=ev1))

        ev1.set_extra("agent_stop_requested", True)
        self.assertFalse(runner_can_capture_followup(runner, "u1"))
        ev1.set_extra("agent_stop_requested", False)

        runner.request_stop()
        self.assertFalse(runner_can_capture_followup(runner, "u1"))

        runner2 = _FakeRunner(ev1)
        runner2.finish()
        self.assertFalse(runner_can_capture_followup(runner2, "u1"))


class CoreReplyFollowUpGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_followup_passes_immediately_when_no_media_ahead(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev1 = _stub_event(text="第一句", components=[NS(type="Plain", text="第一句")], message_id="m1")
        ev2 = _stub_event(text="补充一句", components=[NS(type="Plain", text="补充一句")], message_id="m2")
        release_t1 = asyncio.Event()
        entered_t1 = asyncio.Event()

        async def run_t1():
            out = await guard.enter(ev1, event_ref="e1", channel="group")
            self.assertFalse(out["waited"])
            self.assertFalse(out["had_active_turn"])
            entered_t1.set()
            await release_t1.wait()

        t1 = asyncio.create_task(run_t1())
        await entered_t1.wait()

        out2 = await asyncio.wait_for(guard.enter(ev2, event_ref="e2", channel="group"), timeout=0.5)
        self.assertFalse(out2["waited"])
        self.assertTrue(out2["had_active_turn"])
        self.assertEqual(out2["reason"], "immediate")

        release_t1.set()
        await t1
        guard.finish_turn(ev2)
        self.assertEqual(guard._keys, {})

    async def test_media_followup_waits_for_active_turn_and_blocks_later_text_until_media_begins(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev1 = _stub_event(text="先聊两句", components=[NS(type="Plain", text="先聊两句")], message_id="m1")
        ev2 = _stub_event(text="", components=[NS(type="Image", file="pic.png")], message_id="m2")
        ev3 = _stub_event(text="帮我看看这张图", components=[NS(type="Plain", text="帮我看看这张图")], message_id="m3")
        ev4 = _stub_event(text="", components=[NS(type="Video", file="v.mp4")], message_id="m4")

        timeline: list[str] = []
        t1_ready = asyncio.Event()
        t1_release = asyncio.Event()
        t2_entered = asyncio.Event()
        t2_allow_begin = asyncio.Event()
        t2_release = asyncio.Event()
        t3_release = asyncio.Event()

        async def run_t1():
            await guard.enter(ev1, event_ref="e1", channel="group")
            timeline.append("t1_enter")
            t1_ready.set()
            await t1_release.wait()
            timeline.append("t1_done")

        async def run_t2():
            out = await guard.enter(ev2, event_ref="e2", channel="group")
            self.assertTrue(out["waited"])
            self.assertEqual(out["reason"], "prior_turn_finished")
            self.assertFalse(out["had_active_turn"])
            timeline.append("t2_enter")
            t2_entered.set()
            await t2_allow_begin.wait()
            guard.mark_agent_begin(ev2)
            timeline.append("t2_agent_begin")
            await t2_release.wait()
            timeline.append("t2_done")

        async def run_t3():
            out = await guard.enter(ev3, event_ref="e3", channel="group")
            self.assertTrue(out["waited"])
            self.assertEqual(out["reason"], "prior_media_started")
            self.assertTrue(out["had_active_turn"])
            timeline.append("t3_enter")
            await t3_release.wait()
            timeline.append("t3_done")

        async def run_t4():
            out = await guard.enter(ev4, event_ref="e4", channel="group")
            self.assertTrue(out["waited"])
            self.assertEqual(out["reason"], "prior_turn_finished")
            self.assertFalse(out["had_active_turn"])
            timeline.append("t4_enter")

        t1 = asyncio.create_task(run_t1())
        await t1_ready.wait()

        t2 = asyncio.create_task(run_t2())
        await asyncio.sleep(0.01)
        t3 = asyncio.create_task(run_t3())
        await asyncio.sleep(0.01)
        t4 = asyncio.create_task(run_t4())
        await asyncio.sleep(0.01)

        # While T1 is running, neither media nor queued text may enter core.
        self.assertEqual(timeline, ["t1_enter"])

        t1_release.set()
        await t1
        await asyncio.wait_for(t2_entered.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        # T2 entered core, but has not hit on_agent_begin yet -> T3 must still wait!
        self.assertEqual(timeline, ["t1_enter", "t1_done", "t2_enter"])

        t2_allow_begin.set()
        await asyncio.sleep(0.02)
        # Once T2 hits on_agent_begin, T3 releases, while T4 (video) still waits for T2 & T3!
        self.assertEqual(timeline, ["t1_enter", "t1_done", "t2_enter", "t2_agent_begin", "t3_enter"])

        t2_release.set()
        await t2
        await asyncio.sleep(0.01)
        # T3 is still active -> T4 must still wait until T3 finishes too.
        self.assertNotIn("t4_enter", timeline)

        t3_release.set()
        await t3
        await asyncio.wait_for(t4, timeout=1.0)
        await asyncio.sleep(0)
        self.assertEqual(
            timeline,
            ["t1_enter", "t1_done", "t2_enter", "t2_agent_begin", "t3_enter", "t2_done", "t3_done", "t4_enter"],
        )
        self.assertEqual(guard._keys, {})

    async def test_idle_media_blocks_immediate_text_until_agent_begin(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev_img = _stub_event(components=[NS(type="Image", file="a.png")], message_id="m1")
        ev_txt = _stub_event(text="这是啥", components=[NS(type="Plain", text="这是啥")], message_id="m2")
        img_entered = asyncio.Event()
        img_begin = asyncio.Event()
        img_done = asyncio.Event()
        order: list[str] = []

        async def run_img():
            out = await guard.enter(ev_img, event_ref="e1", channel="private")
            self.assertFalse(out["waited"])
            order.append("img_enter")
            img_entered.set()
            await img_begin.wait()
            guard.mark_agent_begin(ev_img)
            order.append("img_begin")
            await img_done.wait()

        async def run_txt():
            out = await guard.enter(ev_txt, event_ref="e2", channel="private")
            self.assertTrue(out["waited"])
            self.assertEqual(out["reason"], "prior_media_started")
            order.append("txt_enter")

        t_img = asyncio.create_task(run_img())
        await img_entered.wait()
        t_txt = asyncio.create_task(run_txt())
        await asyncio.sleep(0.02)
        self.assertEqual(order, ["img_enter"])

        img_begin.set()
        await asyncio.wait_for(t_txt, timeout=1.0)
        self.assertEqual(order, ["img_enter", "img_begin", "txt_enter"])

        img_done.set()
        await t_img
        self.assertEqual(guard._keys, {})

    async def test_cross_sender_and_cross_chat_never_block_each_other(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev_a = _stub_event(umo="qq:GroupMessage:1", sender_id="user-A", components=[NS(type="Plain", text="hi")])
        ev_b_same_group = _stub_event(umo="qq:GroupMessage:1", sender_id="user-B", components=[NS(type="Image")])
        ev_a_other_group = _stub_event(umo="qq:GroupMessage:2", sender_id="user-A", components=[NS(type="Image")])

        ready = asyncio.Event()
        release = asyncio.Event()

        async def run_a():
            await guard.enter(ev_a, event_ref="ea", channel="group")
            ready.set()
            await release.wait()

        t_a = asyncio.create_task(run_a())
        await ready.wait()

        out_b = await asyncio.wait_for(guard.enter(ev_b_same_group, event_ref="eb", channel="group"), timeout=0.5)
        out_other = await asyncio.wait_for(guard.enter(ev_a_other_group, event_ref="eo", channel="group"), timeout=0.5)
        self.assertFalse(out_b["waited"])
        self.assertFalse(out_other["waited"])

        release.set()
        await t_a
        guard.finish_turn(ev_b_same_group)
        guard.finish_turn(ev_a_other_group)
        self.assertEqual(guard._keys, {})

    async def test_foreign_host_runner_is_awaited_until_cleared(self):
        runners = {}
        guard = CoreReplyFollowUpGuard(
            host_poll_interval_seconds=0.01,
            active_runners_getter=lambda: runners,
        )
        foreign_event = _stub_event(umo="qq:GroupMessage:5", sender_id="u5", text="foreign")
        foreign_runner = _FakeRunner(foreign_event)
        runners["qq:GroupMessage:5"] = foreign_runner

        img_event = _stub_event(
            umo="qq:GroupMessage:5",
            sender_id="u5",
            components=[NS(type="Image", file="x.png")],
        )

        async def clear_runner_soon():
            await asyncio.sleep(0.03)
            foreign_runner.finish()
            runners.pop("qq:GroupMessage:5", None)

        cleaner = asyncio.create_task(clear_runner_soon())
        outcome = await asyncio.wait_for(guard.enter(img_event, event_ref="e-img", channel="group"), timeout=1.0)
        await cleaner
        self.assertTrue(outcome["waited"])
        self.assertEqual(outcome["reason"], "prior_turn_finished")
        self.assertFalse(outcome["had_active_turn"])
        guard.finish_turn(img_event)
        self.assertEqual(guard._keys, {})

    async def test_timeout_degrades_safely_without_hanging(self):
        guard = CoreReplyFollowUpGuard(
            wait_timeout_seconds=0.04,
            active_runners_getter=lambda: {},
        )
        ev1 = _stub_event(text="stuck", components=[NS(type="Plain", text="stuck")])
        ev2 = _stub_event(components=[NS(type="Image", file="a.png")])
        ready = asyncio.Event()
        release = asyncio.Event()

        async def run_t1():
            await guard.enter(ev1, event_ref="e1", channel="group")
            ready.set()
            await release.wait()

        t1 = asyncio.create_task(run_t1())
        await ready.wait()

        out2 = await asyncio.wait_for(guard.enter(ev2, event_ref="e2", channel="group"), timeout=1.0)
        self.assertTrue(out2["waited"])
        self.assertEqual(out2["reason"], "timeout")

        release.set()
        await t1
        guard.finish_turn(ev2)
        self.assertEqual(guard._keys, {})

    async def test_cancelling_waiting_media_unblocks_queued_text_and_leaves_no_leak(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev1 = _stub_event(text="run1", components=[NS(type="Plain", text="run1")])
        ev2 = _stub_event(components=[NS(type="Image", file="a.png")])
        ev3 = _stub_event(text="追发文本", components=[NS(type="Plain", text="追发文本")])
        ready = asyncio.Event()
        release = asyncio.Event()

        async def run_t1():
            await guard.enter(ev1, event_ref="e1", channel="group")
            ready.set()
            await release.wait()

        t1 = asyncio.create_task(run_t1())
        await ready.wait()

        t2 = asyncio.create_task(guard.enter(ev2, event_ref="e2", channel="group"))
        await asyncio.sleep(0.01)
        t3 = asyncio.create_task(guard.enter(ev3, event_ref="e3", channel="group"))
        await asyncio.sleep(0.01)

        # Cancel waiting media -> T3 has no media ahead of it anymore and unblocks immediately!
        t2.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t2
        out3 = await asyncio.wait_for(t3, timeout=1.0)
        self.assertTrue(out3["waited"])
        self.assertEqual(out3["reason"], "prior_media_started")

        release.set()
        await t1
        self.assertEqual(guard._keys, {})

    async def test_close_unblocks_all_waiters_and_clears_state(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev1 = _stub_event(text="run1", components=[NS(type="Plain", text="run1")])
        ev2 = _stub_event(components=[NS(type="Image", file="a.png")])
        ready = asyncio.Event()
        release = asyncio.Event()

        async def run_t1():
            await guard.enter(ev1, event_ref="e1", channel="group")
            ready.set()
            await release.wait()

        t1 = asyncio.create_task(run_t1())
        await ready.wait()

        t2 = asyncio.create_task(guard.enter(ev2, event_ref="e2", channel="group"))
        await asyncio.sleep(0.01)

        guard.close()
        out2 = await asyncio.wait_for(t2, timeout=1.0)
        self.assertEqual(out2["reason"], "closed")
        self.assertEqual(guard._keys, {})

        release.set()
        await t1

    async def test_same_task_reentry_never_self_deadlocks(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev1 = _stub_event(text="first", components=[NS(type="Plain", text="first")])
        ev2 = _stub_event(components=[NS(type="Image", file="img.png")])
        out1 = await guard.enter(ev1, event_ref="e1", channel="private")
        out2 = await asyncio.wait_for(guard.enter(ev2, event_ref="e2", channel="private"), timeout=0.5)
        self.assertFalse(out1["waited"])
        self.assertFalse(out2["waited"])
        guard.close()

    async def test_has_pending_media_barrier_detects_waiters_and_respects_owner_event(self):
        guard = CoreReplyFollowUpGuard(active_runners_getter=lambda: {})
        ev_img1 = _stub_event(
            umo="qq:FriendMessage:77",
            sender_id="u77",
            text="看这张图",
            components=[NS(type="Plain", text="看这张图"), NS(type="Image", file="1.png")],
            channel="private",
            message_id="pm-1",
        )
        ev_txt2 = _stub_event(
            umo="qq:FriendMessage:77",
            sender_id="u77",
            text="补充一句",
            components=[NS(type="Plain", text="补充一句")],
            channel="private",
            message_id="pm-2",
        )
        ev_img2 = _stub_event(
            umo="qq:FriendMessage:77",
            sender_id="u77",
            components=[NS(type="Image", file="2.png")],
            channel="private",
            message_id="pm-3",
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def run_img1():
            await guard.enter(ev_img1, event_ref="e1", channel="private")
            entered.set()
            await release.wait()

        t1 = asyncio.create_task(run_img1())
        await entered.wait()

        # When Img1 itself is the pre-request owner with no waiters behind it, folding stays allowed.
        self.assertFalse(guard.has_pending_media_barrier(ev_txt2, owner_event=ev_img1))
        # When another event owns the window, an unstarted media turn counts as a barrier.
        self.assertTrue(guard.has_pending_media_barrier(ev_txt2, owner_event=object()))

        # Once Img2 is waiting in the queue, folding is blocked even for Img1's owner window.
        t2 = asyncio.create_task(guard.enter(ev_img2, event_ref="e3", channel="private"))
        await asyncio.sleep(0.01)
        self.assertTrue(guard.has_pending_media_barrier(ev_txt2, owner_event=ev_img1))

        release.set()
        await t1
        await t2
        await asyncio.sleep(0)
        self.assertEqual(guard._keys, {})


@unittest.skipUnless(_HAS_ASTRBOT_SDK, "requires AstrBot SDK")
class EndToEndPluginFollowUpTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end tests with PrivateCompanionV2StagingPlugin and real AstrBot follow_up."""

    def _build_plugin(self, *, channel: str, admission_store=None, host_serializes_media: bool | None = False):
        from tests.test_main_no_reply import ENTRY

        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        if host_serializes_media is not None:
            plugin._core_followup_guard = CoreReplyFollowUpGuard(host_serializes_media=host_serializes_media)
        target = NS(key=f"{channel}-target-key", target_id=f"{channel}-target")
        phase_calls = []

        class FakeAdmission:
            def __init__(self):
                self.live = []
                self.records = {}

            def note_live_core_turn(self, context, event_id, *, active):
                if active:
                    if event_id not in self.live:
                        self.live.append(event_id)
                elif event_id in self.live:
                    self.live.remove(event_id)

            def record_core_reply(self, context, event_id, **kwargs):
                self.records[event_id] = {"state": "admitted", **kwargs}

            def mark_core_reply(self, context, event_id, *, stage, **kwargs):
                rec = self.records.setdefault(event_id, {"state": "admitted"})
                if stage == "completed":
                    rec["state"] = "completed"
                elif stage == "inflight":
                    rec["state"] = "inflight"

            def mark_core_reply_logged(self, context, event_id, stage):
                return False

            def has_concurrent_or_newer_core_turn(self, context, event_id):
                return any(other != event_id for other in self.live)

        fake_admission = admission_store if admission_store is not None else FakeAdmission()
        from companion_v2.execution.models import TaskContext
        from companion_v2.foundation.refs import (
            AudienceRef,
            ScopeKind,
            ScopeRef,
            SubjectKind,
            SubjectRef,
        )
        from companion_v2.identity_policy.models import (
            AuthenticatedHostPrincipal,
            ResolvedIdentity,
            RoleBinding,
        )

        scope_kind = ScopeKind.PRIVATE if channel == "private" else ScopeKind.GROUP
        scope = ScopeRef(
            installation_id="inst",
            platform="onebot_qq",
            kind=scope_kind,
            native_scope_id="10001",
            bot_id="bot-1",
            account_id="100",
            conversation_id="conv-1",
            session_id="10001",
        )
        principal = AuthenticatedHostPrincipal(
            installation_id="inst",
            host_id="host",
            platform="onebot_qq",
            native_account_id="20001",
        )
        subject = SubjectRef("inst", SubjectKind.HUMAN, "u-20001")
        role = SubjectRef("inst", SubjectKind.BOT, "role-1")
        context = TaskContext(
            actor=ResolvedIdentity(
                principal=principal,
                subject=subject,
                binding_id="b-1",
                binding_revision=1,
                policy_revision=1,
            ),
            role_binding=RoleBinding(scope=scope, role=role, revision=1),
            audience=AudienceRef(scope=scope, recipients=(subject,)),
        )

        async def group_reply_candidate(event, **_kwargs):
            return Outcome.success({
                "admitted": True,
                "reason": "active_follow_up",
                "mentioned": False,
                "participation_policy": {},
            })

        async def prepare_core_reply(event, _target, **_kwargs):
            msg_id = str(getattr(event.message_obj, "message_id", "evt"))
            return Outcome.success({
                "context": context,
                "instruction": "companion persona",
                "prompt_instruction": "reply naturally",
                "stable_prompt_instruction": "stable persona",
                "target_id": target.target_id,
                "channel": channel,
                "event_id": msg_id,
                "turn_id": f"core-reply:{msg_id}",
                "text": event.message_str,
            })

        plugin.staging = NS(
            runtime=None,
            now=lambda: datetime(2026, 11, 22, 3, 0, 0, tzinfo=timezone.utc),
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True),
            session=NS(
                profile=NS(target=target),
                application=NS(tools=NS(group_participation=NS(admission=fake_admission))),
                core_reply_observations=NS(
                    phase=lambda prepared, state: phase_calls.append((prepared["event_id"], state)),
                    submit=None,
                ),
            ),
            session_topology=NS(
                reply_enabled=AsyncMock(return_value=True),
                schedule_observe=Mock(),
            ),
            group_reply_candidate=AsyncMock(side_effect=group_reply_candidate),
            prepare_core_reply=AsyncMock(side_effect=prepare_core_reply),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        return plugin, fake_admission, phase_calls

    async def test_e2e_group_image_followup_bypasses_astrbot_text_swallow_and_preserves_order(self):
        from astrbot.core.pipeline.process_stage import follow_up

        plugin, _admission, _phases = self._build_plugin(channel="group")
        umo = "onebot_qq:GroupMessage:e2e-group-1"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-1",
            text="先聊聊这件事",
            components=[NS(type="Plain", text="先聊聊这件事")],
            channel="group",
            message_id="g-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-1",
            text="",
            components=[NS(type="Image", file="photo.jpg")],
            channel="group",
            message_id="g-2",
        )
        ev3 = _stub_event(
            umo=umo,
            sender_id="u-1",
            text="看看我发的图",
            components=[NS(type="Plain", text="看看我发的图")],
            channel="group",
            message_id="g-3",
        )

        # Avoid real debounce delay for group plain text in this deterministic E2E test.
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.group_debounce import GroupDebounce

        plugin._group_debounce = GroupDebounce(Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=0.02))

        run1_active = asyncio.Event()
        run1_proceed_tool = asyncio.Event()
        run2_started = asyncio.Event()
        run2_release = asyncio.Event()
        multimodal_events_seen: list[str] = []
        runner1 = _FakeRunner(ev1)
        runner2 = _FakeRunner(ev2)

        async def simulate_event1():
            await plugin.receive_companion_message(ev1)
            follow_up.register_active_runner(umo, runner1)
            try:
                await plugin.observe_companion_core_agent_begin(ev1, runner1.run_context)
                run1_active.set()
                await run1_proceed_tool.wait()
                # Tool call consumes any follow-up that reached runner1.
                runner1.consume_tool_followups()
                runner1.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner1)

        async def simulate_event2():
            await plugin.receive_companion_message(ev2)
            capture = follow_up.try_capture_follow_up(ev2)
            self.assertIsNone(capture, "Image follow-up must NOT be captured by active runner1")
            multimodal_events_seen.append(ev2.message_obj.message_id)
            follow_up.register_active_runner(umo, runner2)
            try:
                await plugin.observe_companion_core_agent_begin(ev2, runner2.run_context)
                run2_started.set()
                await run2_release.wait()
                runner2.consume_tool_followups()
                runner2.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner2)

        async def simulate_event3():
            await plugin.receive_companion_message(ev3)
            capture = follow_up.try_capture_follow_up(ev3)
            self.assertIsNotNone(capture, "Plain text after image should attach to runner2 (with image)")
            consumed, activated = await follow_up.prepare_follow_up_capture(capture)
            if consumed:
                ev3.set_extra("_follow_up_captured", {"target_run_id": capture.target_run_id})
            await follow_up.finalize_follow_up_capture(
                capture,
                activated=activated,
                consumed_marked=consumed,
            )

        t1 = asyncio.create_task(simulate_event1())
        await run1_active.wait()

        t2 = asyncio.create_task(simulate_event2())
        await asyncio.sleep(0.02)
        t3 = asyncio.create_task(simulate_event3())
        await asyncio.sleep(0.05)

        # Run 1 runs its tool call: neither [图片] nor ev3 may have been swallowed by runner1!
        run1_proceed_tool.set()
        await t1
        self.assertEqual(runner1.consumed_texts, [])

        # Run 2 (image) now enters full multimodal pipeline and receives ev3 after on_agent_begin!
        await asyncio.wait_for(run2_started.wait(), timeout=1.0)
        await asyncio.sleep(0.03)
        run2_release.set()
        await asyncio.wait_for(asyncio.gather(t2, t3), timeout=1.0)

        self.assertEqual(multimodal_events_seen, ["g-2"])
        self.assertEqual(runner2.consumed_texts, ["看看我发的图"])
        self.assertEqual(ev3.get_extra("_follow_up_captured"), {"target_run_id": "g-2"})
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_private_media_followup_and_captured_text_cleanup(self):
        from astrbot.core.pipeline.process_stage import follow_up

        plugin, _admission, phases = self._build_plugin(channel="private")
        umo = "onebot_qq:FriendMessage:e2e-priv-1"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-priv",
            text="帮我整理今天的安排",
            components=[NS(type="Plain", text="帮我整理今天的安排")],
            channel="private",
            message_id="p-1",
        )
        ev_text_fu = _stub_event(
            umo=umo,
            sender_id="u-priv",
            text="顺便加上晚饭",
            components=[NS(type="Plain", text="顺便加上晚饭")],
            channel="private",
            message_id="p-2",
        )
        ev_img_fu = _stub_event(
            umo=umo,
            sender_id="u-priv",
            text="",
            components=[NS(type="Image", file="schedule.png")],
            channel="private",
            message_id="p-3",
        )

        run1_active = asyncio.Event()
        run1_consume = asyncio.Event()
        runner1 = _FakeRunner(ev1)

        async def simulate_run1():
            await plugin.receive_companion_message(ev1)
            # Mark first private request started so subsequent text is not folded pre-request.
            plugin._private_reply_followups().for_request(ev1)
            follow_up.register_active_runner(umo, runner1)
            try:
                await plugin.observe_companion_core_agent_begin(ev1, runner1.run_context)
                run1_active.set()
                await run1_consume.wait()
                runner1.consume_tool_followups()
                runner1.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner1)

        async def simulate_text_followup():
            await plugin.receive_companion_message(ev_text_fu)
            capture = follow_up.try_capture_follow_up(ev_text_fu)
            self.assertIsNotNone(capture)
            consumed, activated = await follow_up.prepare_follow_up_capture(capture)
            self.assertTrue(consumed)
            ev_text_fu.set_extra("_follow_up_captured", {"target_run_id": capture.target_run_id})
            await follow_up.finalize_follow_up_capture(
                capture,
                activated=activated,
                consumed_marked=consumed,
            )

        async def simulate_img_followup():
            await plugin.receive_companion_message(ev_img_fu)
            capture = follow_up.try_capture_follow_up(ev_img_fu)
            self.assertIsNone(capture, "Private image follow-up must NOT be captured by runner1")

        t1 = asyncio.create_task(simulate_run1())
        await run1_active.wait()

        # Plain-text follow-up passes immediately and is captured by runner1 without clobbering private followups.
        t_txt = asyncio.create_task(simulate_text_followup())
        await asyncio.sleep(0.02)
        # Image follow-up waits for Run 1 to finish.
        t_img = asyncio.create_task(simulate_img_followup())
        await asyncio.sleep(0.02)
        self.assertFalse(t_img.done())

        run1_consume.set()
        await asyncio.wait_for(asyncio.gather(t1, t_txt, t_img), timeout=1.0)
        await asyncio.sleep(0)

        self.assertEqual(runner1.consumed_texts, ["顺便加上晚饭"])
        # Captured plain-text follow-up marks native turn as 'absorbed' and cleans event.send wrapper.
        self.assertIn(("p-2", "absorbed"), phases)
        self.assertIs(ev_text_fu.send, ev_text_fu._original_send_ref)
        # PrivateReplyFollowUps was not clobbered by the captured plain-text event.
        active_window = plugin._private_reply_followups().active_for("private-target")
        self.assertTrue(active_window is None or active_window.event is not ev_text_fu)
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_group_captured_followup_cleans_tracking_and_live_turns(self):
        from astrbot.core.pipeline.process_stage import follow_up
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.group_debounce import GroupDebounce

        plugin, admission, phases = self._build_plugin(channel="group")
        plugin._group_debounce = GroupDebounce(Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=0.02))
        umo = "onebot_qq:GroupMessage:e2e-group-cleanup"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-c",
            text="你好",
            components=[NS(type="Plain", text="你好")],
            channel="group",
            message_id="gc-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-c",
            text="再补一句",
            components=[NS(type="Plain", text="再补一句")],
            channel="group",
            message_id="gc-2",
        )
        runner1 = _FakeRunner(ev1)
        run1_active = asyncio.Event()
        run1_consume = asyncio.Event()

        async def simulate_run1():
            await plugin.receive_companion_message(ev1)
            follow_up.register_active_runner(umo, runner1)
            try:
                await plugin.observe_companion_core_agent_begin(ev1, runner1.run_context)
                run1_active.set()
                await run1_consume.wait()
                runner1.consume_tool_followups()
                runner1.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner1)

        async def simulate_captured_text():
            await plugin.receive_companion_message(ev2)
            self.assertIn("gc-2", admission.live)
            capture = follow_up.try_capture_follow_up(ev2)
            self.assertIsNotNone(capture)
            consumed, activated = await follow_up.prepare_follow_up_capture(capture)
            self.assertTrue(consumed)
            ev2.set_extra("_follow_up_captured", {"target_run_id": capture.target_run_id})
            await follow_up.finalize_follow_up_capture(
                capture,
                activated=activated,
                consumed_marked=consumed,
            )

        t1 = asyncio.create_task(simulate_run1())
        await run1_active.wait()
        t2 = asyncio.create_task(simulate_captured_text())
        await asyncio.sleep(0.04)

        run1_consume.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
        await asyncio.sleep(0)

        # Captured follow-up must NOT dangle in live turns, group_admission records, or native_turns,
        # and must mark native phase as 'absorbed' so it never shadows an in-flight owner turn!
        self.assertNotIn("gc-2", admission.live)
        self.assertEqual(admission.records["gc-2"]["state"], "completed")
        self.assertIn(("gc-2", "absorbed"), phases)
        self.assertIs(ev2.send, ev2._original_send_ref)
        self.assertIsInstance(ev2.get_extra(CORE_REPLY_TRACKING_EXTRA), dict)
        self.assertTrue(ev2.get_extra(CORE_REPLY_TRACKING_EXTRA)["completed"])
        self.assertIsInstance(ev2.get_extra(CORE_REPLY_CONTEXT_EXTRA), dict)

    async def test_e2e_coexists_with_upstream_host_that_already_skips_media_followup(self):
        plugin, _admission, _phases = self._build_plugin(channel="group")
        umo = "onebot_qq:GroupMessage:e2e-coexist"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-co",
            text="问个问题",
            components=[NS(type="Plain", text="问个问题")],
            channel="group",
            message_id="co-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-co",
            text="",
            components=[NS(type="Image", file="co.png")],
            channel="group",
            message_id="co-2",
        )
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.group_debounce import GroupDebounce

        plugin._group_debounce = GroupDebounce(Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=0.02))
        session_lock = asyncio.Lock()
        events_order: list[str] = []
        run1_holding_lock = asyncio.Event()
        run1_release = asyncio.Event()

        def upstream_try_capture(event):
            # Simulates an upgraded AstrBot where media follow-up already returns None.
            return None

        async def run_event(ev, label, *, hold=False):
            await plugin.receive_companion_message(ev)
            self.assertIsNone(upstream_try_capture(ev))
            async with session_lock:
                events_order.append(f"{label}_lock")
                if hold:
                    run1_holding_lock.set()
                    await run1_release.wait()
                events_order.append(f"{label}_done")

        t1 = asyncio.create_task(run_event(ev1, "r1", hold=True))
        await run1_holding_lock.wait()
        t2 = asyncio.create_task(run_event(ev2, "r2"))
        await asyncio.sleep(0.02)

        run1_release.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
        self.assertEqual(events_order, ["r1_lock", "r1_done", "r2_lock", "r2_done"])
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_cancelled_waiting_media_cleans_group_tracking(self):
        plugin, admission, phases = self._build_plugin(channel="group")
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.group_debounce import GroupDebounce

        plugin._group_debounce = GroupDebounce(Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=0.02))
        umo = "onebot_qq:GroupMessage:e2e-cancel"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-can",
            text="hi",
            components=[NS(type="Plain", text="hi")],
            channel="group",
            message_id="can-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-can",
            components=[NS(type="Image", file="a.png")],
            channel="group",
            message_id="can-2",
        )
        run1_entered = asyncio.Event()
        run1_release = asyncio.Event()

        async def run1():
            await plugin.receive_companion_message(ev1)
            run1_entered.set()
            await run1_release.wait()

        t1 = asyncio.create_task(run1())
        await run1_entered.wait()

        t2 = asyncio.create_task(plugin.receive_companion_message(ev2))
        await asyncio.sleep(0.03)
        self.assertIn("can-2", admission.live)

        t2.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t2

        self.assertNotIn("can-2", admission.live)
        self.assertEqual(admission.records["can-2"]["state"], "completed")
        self.assertIn(("can-2", "failed"), phases)
        self.assertIs(ev2.send, ev2._original_send_ref)

        run1_release.set()
        await t1
        await asyncio.sleep(0)
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_real_astrbot_sdk_media_components_detected(self):
        from astrbot.core.message.components import (
            At,
            Face,
            File,
            Forward,
            Image,
            Plain,
            Record,
            Reply,
            Video,
        )

        self.assertEqual(
            event_media_kinds(_stub_event(components=[At(qq="100"), Plain("hi"), Face(id=1)])),
            (),
        )
        self.assertEqual(event_media_kinds(_stub_event(components=[Image(file="a.jpg")])), ("image",))
        self.assertEqual(event_media_kinds(_stub_event(components=[Video(file="a.mp4")])), ("video",))
        self.assertEqual(event_media_kinds(_stub_event(components=[Record(file="a.amr")])), ("record",))
        self.assertEqual(event_media_kinds(_stub_event(components=[File(name="a.pdf")])), ("file",))
        self.assertEqual(event_media_kinds(_stub_event(components=[Forward(id="f-1")])), ("forward",))
        self.assertEqual(
            event_media_kinds(
                _stub_event(components=[Reply(id="10", chain=[Image(file="q.png")]), Plain("看这个")])
            ),
            ("reply_image",),
        )
        self.assertEqual(
            event_media_kinds(_stub_event(components=[Reply(id="11", chain=[]), Plain("看这个")])),
            ("reply_unresolved",),
        )
        self.assertEqual(
            event_media_kinds(_stub_event(components=[Reply(id="12", chain=[Plain("纯文本")]), Plain("嗯")])),
            (),
        )

    async def test_e2e_passthrough_awakened_media_is_also_serialized(self):
        from astrbot.core.pipeline.process_stage import follow_up
        from companion_v2.execution.models import TaskError

        plugin, _admission, _phases = self._build_plugin(channel="group")
        umo = "onebot_qq:GroupMessage:e2e-passthrough"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-pt",
            text="先聊聊",
            components=[NS(type="Plain", text="先聊聊")],
            channel="group",
            message_id="pt-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-pt",
            text="",
            components=[NS(type="Reply", id="q-1", chain=[NS(type="Image", file="q.png")])],
            channel="group",
            message_id="pt-2",
        )
        ev2.is_at_or_wake_command = True
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.group_debounce import GroupDebounce

        plugin._group_debounce = GroupDebounce(Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=0.02))

        original_candidate = plugin.staging.group_reply_candidate

        async def candidate_with_passthrough(event, **kwargs):
            if event is ev2:
                return Outcome.fail(TaskError("host_group_media_unsupported", "passthrough media"))
            return await original_candidate(event, **kwargs)

        plugin.staging.group_reply_candidate = AsyncMock(side_effect=candidate_with_passthrough)

        runner1 = _FakeRunner(ev1)
        run1_active = asyncio.Event()
        run1_release = asyncio.Event()

        async def run1():
            await plugin.receive_companion_message(ev1)
            follow_up.register_active_runner(umo, runner1)
            try:
                await plugin.observe_companion_core_agent_begin(ev1, runner1.run_context)
                run1_active.set()
                await run1_release.wait()
                runner1.consume_tool_followups()
                runner1.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner1)

        async def run2():
            await plugin.receive_companion_message(ev2)
            self.assertIsNone(follow_up.try_capture_follow_up(ev2))

        t1 = asyncio.create_task(run1())
        await run1_active.wait()
        t2 = asyncio.create_task(run2())
        await asyncio.sleep(0.03)
        self.assertFalse(t2.done(), "Awakened passthrough quoted-image event must wait for Run 1")

        run1_release.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
        await asyncio.sleep(0)
        self.assertEqual(runner1.consumed_texts, [])
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_private_pre_request_fold_does_not_overtake_waiting_media(self):
        from astrbot.core.pipeline.process_stage import follow_up
        from companion_v2.conversation.private_debounce import Policy
        from astrbot_host.private_debounce import PrivateDebounce

        plugin, _admission, _phases = self._build_plugin(channel="private")
        plugin._private_debounce = PrivateDebounce(
            Policy(enabled=False),
            log_enabled=False,
        )
        umo = "onebot_qq:FriendMessage:e2e-priv-order"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-ord",
            text="先问一个问题",
            components=[NS(type="Plain", text="先问一个问题")],
            channel="private",
            message_id="po-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-ord",
            text="",
            components=[NS(type="Image", file="chart.png")],
            channel="private",
            message_id="po-2",
        )
        ev3 = _stub_event(
            umo=umo,
            sender_id="u-ord",
            text="看看我刚发的图",
            components=[NS(type="Plain", text="看看我刚发的图")],
            channel="private",
            message_id="po-3",
        )

        run1_pre_request = asyncio.Event()
        run1_release = asyncio.Event()
        run2_started = asyncio.Event()
        run2_release = asyncio.Event()
        runner1 = _FakeRunner(ev1)
        runner2 = _FakeRunner(ev2)
        folded_into_run1 = []
        order = []

        async def simulate_run1():
            await plugin.receive_companion_message(ev1)
            # Stay in pre-request gap (PrivateReplyFollowUps still open with request_started=False).
            run1_pre_request.set()
            await run1_release.wait()
            resolved = plugin._private_reply_followups().for_request(ev1)
            if resolved is not None:
                folded_into_run1.extend(item[1] for item in resolved[0])
            follow_up.register_active_runner(umo, runner1)
            try:
                await plugin.observe_companion_core_agent_begin(ev1, runner1.run_context)
                order.append("run1_done")
                runner1.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner1)
                plugin._private_reply_followups().finish(ev1)

        async def simulate_run2():
            await plugin.receive_companion_message(ev2)
            self.assertIsNone(follow_up.try_capture_follow_up(ev2))
            order.append("run2_enter")
            follow_up.register_active_runner(umo, runner2)
            try:
                await plugin.observe_companion_core_agent_begin(ev2, runner2.run_context)
                run2_started.set()
                await run2_release.wait()
                runner2.consume_tool_followups()
                runner2.finish()
            finally:
                follow_up.unregister_active_runner(umo, runner2)

        async def simulate_run3():
            await plugin.receive_companion_message(ev3)
            self.assertFalse(ev3.call_llm, "Txt3 must not be suppressed/folded into pre-request Txt1")
            order.append("run3_enter")
            capture = follow_up.try_capture_follow_up(ev3)
            self.assertIsNotNone(capture)
            consumed, activated = await follow_up.prepare_follow_up_capture(capture)
            if consumed:
                ev3.set_extra("_follow_up_captured", {"target_run_id": capture.target_run_id})
            await follow_up.finalize_follow_up_capture(
                capture,
                activated=activated,
                consumed_marked=consumed,
            )

        t1 = asyncio.create_task(simulate_run1())
        await run1_pre_request.wait()

        # Img2 arrives while Txt1 is still pre-request and queues in guard.waiters.
        t2 = asyncio.create_task(simulate_run2())
        await asyncio.sleep(0.02)
        # Txt3 arrives while Txt1's PrivateReplyFollowUps window is still open!
        t3 = asyncio.create_task(simulate_run3())
        await asyncio.sleep(0.02)

        # Txt3 must NOT have been folded into Txt1, and must stay queued behind Img2.
        self.assertFalse(ev3.call_llm)
        self.assertFalse(t2.done())
        self.assertFalse(t3.done())

        run1_release.set()
        await t1
        self.assertEqual(folded_into_run1, [])

        await asyncio.wait_for(run2_started.wait(), timeout=1.0)
        await asyncio.sleep(0.02)
        run2_release.set()
        await asyncio.wait_for(asyncio.gather(t2, t3), timeout=1.0)
        await asyncio.sleep(0)

        self.assertEqual(order, ["run1_done", "run2_enter", "run3_enter"])
        self.assertEqual(runner2.consumed_texts, ["看看我刚发的图"])
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_guard_close_during_wait_skips_private_followup_window_and_cleans_state(self):
        plugin, _admission, phases = self._build_plugin(channel="private")
        umo = "onebot_qq:FriendMessage:e2e-priv-close"
        ev1 = _stub_event(
            umo=umo,
            sender_id="u-close",
            text="第一句",
            components=[NS(type="Plain", text="第一句")],
            channel="private",
            message_id="pc-1",
        )
        ev2 = _stub_event(
            umo=umo,
            sender_id="u-close",
            text="配图说明",
            components=[NS(type="Plain", text="配图说明"), NS(type="Image", file="a.png")],
            channel="private",
            message_id="pc-2",
        )
        run1_entered = asyncio.Event()
        run1_release = asyncio.Event()

        async def run1():
            await plugin.receive_companion_message(ev1)
            plugin._private_reply_followups().finish_event(ev1)
            run1_entered.set()
            await run1_release.wait()

        t1 = asyncio.create_task(run1())
        await run1_entered.wait()

        t2 = asyncio.create_task(plugin.receive_companion_message(ev2))
        await asyncio.sleep(0.02)
        self.assertFalse(t2.done())

        # Simulate plugin shutdown/reload closing the guard while ev2 is waiting.
        plugin._core_reply_followup_guard().close()
        await asyncio.wait_for(t2, timeout=1.0)

        self.assertIsNone(plugin._private_reply_followups().active_for("private-target"))
        self.assertTrue(ev2.call_llm, "guard close must suppress AstrBot bare LLM fallback")
        self.assertFalse(ev2.is_wake)
        self.assertFalse(ev2.is_at_or_wake_command)
        self.assertIn(("pc-2", "failed"), phases)
        self.assertIs(ev2.send, ev2._original_send_ref)

        run1_release.set()
        await t1
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

    async def test_e2e_host_serializes_media_auto_bypasses_wait_and_runs_capture_cleanup(self):
        from astrbot.core.pipeline.process_stage import follow_up

        has_native_media_order = (
            callable(getattr(follow_up, "extract_plain_steering_text", None))
            and hasattr(follow_up, "_FOLLOW_UP_ORDER_STATE")
        )
        plugin, _admission, phases = self._build_plugin(
            channel="private",
            host_serializes_media=None if has_native_media_order else True,
        )
        umo = "onebot_qq:FriendMessage:e2e-host-native"
        ev1 = _stub_event(
            umo=umo, sender_id="u-native", text="第一句",
            components=[NS(type="Plain", text="第一句")], channel="private", message_id="hn-1",
        )
        ev2 = _stub_event(
            umo=umo, sender_id="u-native", text="配图",
            components=[NS(type="Plain", text="配图"), NS(type="Image", file="a.png")],
            channel="private", message_id="hn-2",
        )
        release = asyncio.Event()
        entered = asyncio.Event()

        async def run1():
            out = await plugin._enter_core_reply_followup_guard(
                ev1, event_ref="hn-1", channel="private", prepared={"event_id": "hn-1"},
            )
            self.assertEqual(out["reason"], "host_serialized")
            entered.set()
            await release.wait()

        t1 = asyncio.create_task(run1())
        await entered.wait()

        async def run2():
            out = await plugin._enter_core_reply_followup_guard(
                ev2, event_ref="hn-2", channel="private", prepared={"event_id": "hn-2"},
            )
            self.assertEqual(out["reason"], "host_serialized")
            self.assertFalse(out["waited"])
            ev2.set_extra("_follow_up_captured", {"target_run_id": "run-native"})

        t2 = asyncio.create_task(run2())
        await asyncio.wait_for(t2, timeout=0.5)
        await asyncio.sleep(0)
        self.assertIn(("hn-2", "absorbed"), phases)
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

        release.set()
        await t1

    async def test_e2e_agent_done_and_after_sent_release_guard_immediately_and_preserve_done_cleanup(self):
        plugin, _admission, phases = self._build_plugin(channel="private", host_serializes_media=False)
        umo = "onebot_qq:FriendMessage:e2e-early-release"
        ev1 = _stub_event(
            umo=umo, sender_id="u-rel", text="第一句",
            components=[NS(type="Plain", text="第一句")], channel="private", message_id="rel-1",
        )
        ev2 = _stub_event(
            umo=umo, sender_id="u-rel", text="配图",
            components=[NS(type="Plain", text="配图"), NS(type="Image", file="a.png")],
            channel="private", message_id="rel-2",
        )
        ev1_ready = asyncio.Event()
        ev1_hold = asyncio.Event()

        async def run1():
            await plugin.receive_companion_message(ev1)
            plugin._private_reply_followups().finish_event(ev1)
            ev1_ready.set()
            await ev1_hold.wait()

        t1 = asyncio.create_task(run1())
        await ev1_ready.wait()

        t2 = asyncio.create_task(plugin.receive_companion_message(ev2))
        await asyncio.sleep(0.02)
        self.assertFalse(t2.done(), "ev2 should wait while ev1 is active")

        # Calling on_agent_done releases the guard slot even before run1's outer task exits.
        await plugin.observe_companion_core_agent_done(ev1, None, NS(role="assistant"))
        await asyncio.wait_for(t2, timeout=0.5)

        # Calling after_message_sent also releases ev2 immediately.
        await plugin.observe_companion_core_reply_sent(ev2)
        self.assertEqual(plugin._core_reply_followup_guard()._keys, {})

        ev1_hold.set()
        await t1

    async def test_timeout_does_not_register_active_turn_or_trigger_domino_cascade(self):
        guard = CoreReplyFollowUpGuard(
            wait_timeout_seconds=0.03,
            active_runners_getter=lambda: {},
            host_serializes_media=False,
        )
        umo = "onebot_qq:GroupMessage:domino"
        ev1 = _stub_event(umo=umo, sender_id="u1", text="stuck", components=[NS(type="Plain", text="stuck")])
        ev2 = _stub_event(umo=umo, sender_id="u1", components=[NS(type="Image", file="a.png")])
        ev3 = _stub_event(umo=umo, sender_id="u1", text="next", components=[NS(type="Plain", text="next")])
        ready = asyncio.Event()
        release = asyncio.Event()
        done_callbacks = []

        async def run_stuck():
            await guard.enter(ev1, event_ref="e1", channel="group")
            ready.set()
            await release.wait()

        t1 = asyncio.create_task(run_stuck())
        await ready.wait()

        async def run_timed_out():
            out2 = await guard.enter(
                ev2, event_ref="e2", channel="group",
                on_turn_done=lambda ev, task: done_callbacks.append(ev),
            )
            self.assertEqual(out2["reason"], "timeout")
            # ev2 must NOT sit in active_turns as an unstarted media barrier.
            out3 = await asyncio.wait_for(guard.enter(ev3, event_ref="e3", channel="group"), timeout=0.5)
            self.assertFalse(out3["waited"])
            self.assertEqual(out3["reason"], "immediate")

        t2 = asyncio.create_task(run_timed_out())
        await asyncio.wait_for(t2, timeout=1.0)
        await asyncio.sleep(0)
        self.assertEqual(done_callbacks, [ev2])

        release.set()
        await t1
        guard.finish_turn(ev3)
        self.assertEqual(guard._keys, {})

    async def test_e2e_passthrough_guard_close_suppresses_bare_llm(self):
        plugin, _admission, _phases = self._build_plugin(channel="private", host_serializes_media=False)
        umo = "onebot_qq:FriendMessage:e2e-pass-close"
        ev1 = _stub_event(
            umo=umo, sender_id="u-pc", text="第一句",
            components=[NS(type="Plain", text="第一句")], channel="private", message_id="pass-1",
        )
        ev2 = _stub_event(
            umo=umo, sender_id="u-pc", text="图",
            components=[NS(type="Image", file="p.png")], channel="private", message_id="pass-2",
        )
        ev2.is_at_or_wake_command = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def run1():
            await plugin.receive_companion_message(ev1)
            plugin._private_reply_followups().finish_event(ev1)
            entered.set()
            await release.wait()

        t1 = asyncio.create_task(run1())
        await entered.wait()

        t2 = asyncio.create_task(
            plugin._guard_passthrough_core_reply(ev2, event_ref="pass-2", channel="private")
        )
        await asyncio.sleep(0.02)
        self.assertFalse(t2.done())

        plugin._core_reply_followup_guard().close()
        await asyncio.wait_for(t2, timeout=1.0)
        self.assertTrue(ev2.call_llm, "passthrough guard close must suppress bare LLM fallback")

        release.set()
        await t1


if __name__ == "__main__":
    unittest.main()
