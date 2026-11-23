"""End-to-end and unit regression tests for QQ merged-forward core-reply support."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from astrbot_host.core_reply_closing import _has_heavy_content
from astrbot_host.core_reply_forwards import (
    COMPANION_FORWARD_INJECTED_EXTRA,
    COMPANION_FORWARD_OWNED_EXTRA,
    FORWARD_DISABLED_SUMMARY,
    FORWARD_FALLBACK_SUMMARY,
    FORWARD_OMITTED_LIMIT_SUMMARY,
    FORWARD_ONLY_SENTINEL_PROMPT,
    FORWARD_REQUEST_BLOCK_PREFIX,
    MAX_CORE_FORWARD_IMAGES,
    PARSED_FORWARDS_EXTRA,
    apply_debounced_private_merge,
    await_event_forwards_parsed,
    claim_core_reply_forward_injection,
    coalesce_debounced_forwards,
    ensure_event_forwards_parsed,
    event_will_enter_astrbot_llm,
    forward_burst_debounce_policy,
    get_parsed_forwards,
    inject_core_reply_forwards,
    merge_parsed_forward_payloads,
    parse_event_forwards,
    prepare_forward_only_request,
    private_debounce_input,
)
from astrbot_host.private_debounce import PrivateDebounce
from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.private_debounce import Policy
from companion_v2.foundation.outcome import Outcome
from tests.group_host_support import GroupFixture
from tests.test_main_no_reply import ENTRY


def _make_event(
    parts,
    *,
    message_str="",
    message_id="msg-1",
    scene="FriendMessage",
    call_action=None,
    created_at=1_700_000_000.0,
    platform="aiocqhttp",
    group_id=None,
):
    extra = {}
    bot = NS(api=NS(call_action=call_action)) if call_action is not None else None
    event = NS(
        platform_meta=NS(id="qq-main", name=platform),
        message_obj=NS(
            type=NS(value=scene),
            message=list(parts),
            message_id=message_id,
            message_str=message_str,
            raw_message={"post_type": "message"},
            group_id=group_id,
            sender=NS(user_id="200", nickname="小晴"),
        ),
        message_str=message_str,
        created_at=created_at,
        unified_msg_origin=f"qq-main:{scene}:{'900' if group_id else '200'}",
        bot=bot,
        call_llm=False,
        is_wake=False,
        is_at_or_wake_command=False,
        _has_send_oper=False,
        stopped=False,
        extra=extra,
        sent=[],
        get_platform_name=lambda: platform,
        get_platform_id=lambda: "qq-main",
    )
    event.get_extra = lambda key, default=None: extra.get(key, default)
    event.set_extra = lambda key, value: extra.__setitem__(key, value)
    event.should_call_llm = lambda value: setattr(event, "call_llm", value)
    event.stop_event = lambda: setattr(event, "stopped", True)

    async def _send(message):
        event.sent.append(message)
        event._has_send_oper = True

    async def _send_streaming(*args, **kwargs):
        event.sent.append((args, kwargs))

    event.send = _send
    event.send_streaming = _send_streaming
    return event


def _node(sender_name, segments, *, user_id="300", ts=1_700_000_100):
    return {
        "type": "node",
        "data": {
            "sender": {"user_id": user_id, "nickname": sender_name},
            "time": ts,
            "message": segments,
        },
    }


def _fake_native_context():
    conversation = NS(cid="conv-forward-1", history="[]")
    conv_mgr = NS(
        get_curr_conversation_id=AsyncMock(return_value="conv-forward-1"),
        get_conversation=AsyncMock(return_value=conversation),
        new_conversation=AsyncMock(return_value="conv-forward-1"),
    )
    return NS(conversation_manager=conv_mgr)


def _passes_unpatched_internal_gate(event) -> bool:
    """Exact gate from AstrBot internal.py:192-209 on unpatched builds."""
    has_provider_request = event.get_extra("provider_request") is not None
    has_valid_message = bool(event.message_str and event.message_str.strip())
    has_media = any(
        getattr(getattr(c, "type", None), "value", getattr(c, "type", None))
        in {"Image", "File", "Record", "Video"}
        for c in event.message_obj.message
    )
    has_reply = any(
        getattr(getattr(c, "type", None), "value", getattr(c, "type", None)) == "Reply"
        for c in event.message_obj.message
    )
    return has_provider_request or has_valid_message or has_media or has_reply


class CoreReplyForwardParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_pure_forward_parses_penetrates_gate_and_injects_once(self):
        calls = []

        async def call_action(action, **params):
            calls.append((action, params))
            return {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "messages": [
                        _node("张三", [{"type": "text", "data": {"text": "周五晚上七点集合"}}]),
                        _node("李四", [
                            {"type": "text", "data": {"text": "收到，我带投影仪"}},
                            {"type": "image", "data": {"url": "https://img.example.com/map.png"}},
                        ], user_id="301"),
                    ]
                },
            }

        event = _make_event(
            [NS(type="Forward", id="fwd-single-1")],
            message_str="",
            message_id="msg-pure-1",
            call_action=call_action,
        )
        parsed = await parse_event_forwards(event, policy=ForwardIntakePolicy())
        self.assertIsNotNone(parsed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(parsed["source"], "private_companion")
        self.assertEqual(parsed["total_nodes"], 2)
        self.assertFalse(parsed["truncated"])
        self.assertEqual(len(parsed["cards"]), 1)
        self.assertEqual(parsed["cards"][0]["forward_id"], "fwd-single-1")
        self.assertEqual(parsed["cards"][0]["message_id"], "msg-pure-1")
        self.assertEqual(parsed["image_urls"], ["https://img.example.com/map.png"])
        self.assertIn("张三", parsed["combined_text"])
        self.assertIn("周五晚上七点集合", parsed["combined_text"])
        self.assertIn("李四", parsed["combined_text"])
        self.assertIs(get_parsed_forwards(event), parsed)

        # Before prepare_forward_only_request, unpatched AstrBot would drop this pure Forward.
        self.assertFalse(_passes_unpatched_internal_gate(event))
        attached = await prepare_forward_only_request(event, native_context=_fake_native_context())
        self.assertTrue(attached)
        self.assertTrue(_passes_unpatched_internal_gate(event))
        self.assertEqual(event.message_str, "")
        req = event.get_extra("provider_request")
        self.assertEqual(req.prompt, FORWARD_ONLY_SENTINEL_PROMPT)
        if hasattr(req, "retrieval_query"):
            self.assertEqual(req.retrieval_query, "")

        claim_core_reply_forward_injection(event)
        self.assertTrue(event.get_extra(COMPANION_FORWARD_INJECTED_EXTRA))
        self.assertTrue(event.get_extra(COMPANION_FORWARD_OWNED_EXTRA))

        self.assertTrue(inject_core_reply_forwards(event, req))
        self.assertFalse(event.get_extra(COMPANION_FORWARD_OWNED_EXTRA))
        self.assertTrue(event.get_extra(COMPANION_FORWARD_INJECTED_EXTRA))
        self.assertEqual(len(req.extra_user_content_parts), 1)
        injected_text = req.extra_user_content_parts[0].text
        self.assertTrue(injected_text.startswith(FORWARD_REQUEST_BLOCK_PREFIX))
        self.assertIn("不代表当前用户本轮自述", injected_text)
        self.assertIn("周五晚上七点集合", injected_text)
        self.assertEqual(req.image_urls, ["https://img.example.com/map.png"])

        # Second injection attempt is a no-op (strictly idempotent).
        self.assertFalse(inject_core_reply_forwards(event, req))
        self.assertEqual(len(req.extra_user_content_parts), 1)

    async def test_graceful_degradation_for_mixed_qq_segments_and_cq_strings(self):
        multimsg_json = json.dumps({
            "app": "com.tencent.multimsg",
            "config": {"forward": 1},
            "meta": {"detail": {"news": [{"text": "A: 摘要一"}, {"text": "B: 摘要二"}]}},
        }, ensure_ascii=False)
        card_json = json.dumps({"app": "com.tencent.structmsg", "prompt": "[分享] 周末攻略"}, ensure_ascii=False)

        async def call_action(action, **params):
            return {
                "messages": [
                    _node("小王", [
                        {"type": "reply", "data": {"id": "99", "text": "上条问题"}},
                        {"type": "at", "data": {"qq": "555", "name": "阿明"}},
                        {"type": "at", "data": {"qq": "all"}},
                        {"type": "face", "data": {"id": "178", "raw": {"faceText": "斜眼笑"}}},
                        {"type": "mface", "data": {"summary": "点赞"}},
                        {"type": "text", "data": {"text": "看附件和视频"}},
                        {"type": "image", "data": {"url": "https://img.example.com/photo.jpg"}},
                        {"type": "image", "data": {"url": "https://img.example.com/sticker.gif", "sub_type": 1}},
                        {"type": "file", "data": {"name": "report.pdf"}},
                        {"type": "record", "data": {"file": "voice.amr"}},
                        {"type": "video", "data": {"file": "clip.mp4"}},
                        {"type": "json", "data": {"data": multimsg_json}},
                        {"type": "json", "data": {"data": card_json}},
                        {"type": "poke", "data": {"type": 1}},
                    ]),
                    {
                        "sender": {"nickname": "小李", "user_id": "302"},
                        "content": "[CQ:face,id=1]你好[CQ:at,qq=888,name=和纱][CQ:image,url=https://img.example.com/cq.png][CQ:file,name=note.txt]",
                    },
                ]
            }

        event = _make_event([NS(type="Forward", id="fwd-mixed")], message_id="msg-mixed", call_action=call_action)
        parsed = await parse_event_forwards(event)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total_nodes"], 2)
        self.assertFalse(parsed["cards"][0]["failed"])
        text = parsed["combined_text"]
        for expected in (
            "[引用: 上条问题]",
            "[@阿明]",
            "[@全体成员]",
            "[表情:斜眼笑]",
            "[表情包:点赞]",
            "看附件和视频",
            "[图片]",
            "[表情包]",
            "[文件:report.pdf]",
            "[语音]",
            "[视频]",
            "A: 摘要一 / B: 摘要二",
            "[卡片消息: [分享] 周末攻略]",
            "[消息组件]",
            "[表情]你好[@和纱][图片][文件:note.txt]",
        ):
            self.assertIn(expected, text)
        # Stickers (sub_type=1) are excluded from photo image_urls; real photos & CQ images kept.
        self.assertEqual(
            parsed["image_urls"],
            ["https://img.example.com/photo.jpg", "https://img.example.com/cq.png"],
        )

    async def test_nested_forwards_respect_parse_nested_max_depth_and_cycles(self):
        remote_calls = []

        async def call_action(action, **params):
            fid = str(params.get("message_id") or params.get("id"))
            remote_calls.append(fid)
            if fid == "fwd-root":
                return {
                    "messages": [
                        _node("外层", [
                            {"type": "text", "data": {"text": "外层正文"}},
                            {"type": "forward", "data": {"id": "fwd-level-1"}},
                        ])
                    ]
                }
            if fid == "fwd-level-1":
                return {
                    "messages": [
                        _node("一层", [
                            {"type": "text", "data": {"text": "一层正文"}},
                            {"type": "forward", "data": {"id": "fwd-level-2"}},
                            {"type": "forward", "data": {"id": "fwd-root"}},  # cycle
                        ])
                    ]
                }
            if fid == "fwd-level-2":
                return {"messages": [_node("二层", [{"type": "text", "data": {"text": "二层正文"}}])]}
            return None

        # parse_nested=False skips all nested RPC calls
        event_disabled = _make_event([NS(type="Forward", id="fwd-root")], call_action=call_action)
        parsed_disabled = await parse_event_forwards(
            event_disabled, policy=ForwardIntakePolicy(parse_nested=False),
        )
        self.assertEqual(remote_calls, ["fwd-root"])
        self.assertIn("[嵌套合并转发未展开]", parsed_disabled["combined_text"])
        self.assertNotIn("一层正文", parsed_disabled["combined_text"])

        # parse_nested=True, max_depth=2 expands depth 0 & 1, truncates depth 2
        remote_calls.clear()
        event_depth2 = _make_event([NS(type="Forward", id="fwd-root")], call_action=call_action)
        parsed_depth2 = await parse_event_forwards(
            event_depth2, policy=ForwardIntakePolicy(parse_nested=True, max_depth=2),
        )
        self.assertEqual(remote_calls, ["fwd-root", "fwd-level-1"])
        self.assertIn("外层正文", parsed_depth2["combined_text"])
        self.assertIn("一层正文", parsed_depth2["combined_text"])
        self.assertIn("[嵌套合并转发（已达最大深度）]", parsed_depth2["combined_text"])
        self.assertNotIn("二层正文", parsed_depth2["combined_text"])
        self.assertTrue(parsed_depth2["truncated"])

        # parse_nested=True, max_depth=4 expands depth 2 and guards against cycle back to fwd-root
        remote_calls.clear()
        event_depth4 = _make_event([NS(type="Forward", id="fwd-root")], call_action=call_action)
        parsed_depth4 = await parse_event_forwards(
            event_depth4, policy=ForwardIntakePolicy(parse_nested=True, max_depth=4),
        )
        self.assertEqual(remote_calls, ["fwd-root", "fwd-level-1", "fwd-level-2"])
        self.assertIn("二层正文", parsed_depth4["combined_text"])
        self.assertIn("[嵌套合并转发未重复展开]", parsed_depth4["combined_text"])

    async def test_over_limit_truncation_for_nodes_cards_and_images(self):
        async def call_action(action, **params):
            fid = str(params.get("message_id") or params.get("id"))
            return {
                "messages": [
                    _node(
                        f"用户{i}",
                        [
                            {"type": "text", "data": {"text": f"{fid}-消息{i}"}},
                            {"type": "image", "data": {"url": f"https://img.example.com/{fid}-{i}.png"}},
                        ],
                    )
                    for i in range(10)
                ]
            }

        event = _make_event(
            [NS(type="Forward", id=f"fwd-{i}") for i in range(4)],
            call_action=call_action,
        )
        parsed = await parse_event_forwards(
            event, policy=ForwardIntakePolicy(max_forwards=2, max_nodes=8),
        )
        self.assertEqual(len(parsed["cards"]), 2)
        self.assertEqual(parsed["total_nodes"], 8)
        self.assertEqual(len(parsed["image_urls"]), MAX_CORE_FORWARD_IMAGES)
        self.assertTrue(parsed["truncated"])

    async def test_timeout_exception_and_empty_payload_degrade_gracefully(self):
        async def slow_rpc(action, **params):
            await asyncio.sleep(5)
            return {"messages": []}

        async def broken_rpc(action, **params):
            raise RuntimeError("napcat disconnected")

        async def empty_rpc(action, **params):
            return {"status": "ok", "retcode": 0, "data": {"messages": []}}

        for label, rpc in (("timeout", slow_rpc), ("error", broken_rpc), ("empty", empty_rpc)):
            with self.subTest(case=label):
                event = _make_event([NS(type="Forward", id=f"fwd-{label}")], call_action=rpc)
                parsed = await parse_event_forwards(event, fetch_timeout=0.05, total_timeout=0.2)
                self.assertIsNotNone(parsed)
                self.assertEqual(len(parsed["cards"]), 1)
                self.assertTrue(parsed["cards"][0]["failed"])
                self.assertEqual(parsed["cards"][0]["summary_text"], FORWARD_FALLBACK_SUMMARY)
                self.assertEqual(parsed["combined_text"], FORWARD_FALLBACK_SUMMARY)

    async def test_cache_hit_makes_zero_rpc_calls_and_external_injected_flag_prevents_duplicate(self):
        rpc = AsyncMock(side_effect=AssertionError("cached forward must never call get_forward_msg"))
        event = _make_event([NS(type="Forward", id="fwd-cached")], call_action=rpc)
        prebuilt = {
            "version": 1,
            "source": "astrbot_core",
            "cards": [{
                "forward_id": "fwd-cached",
                "message_id": "msg-cached",
                "node_count": 1,
                "truncated": False,
                "summary_text": "[合并转发聊天记录 · 共1条]\n甲: 缓存内容",
                "image_urls": [],
                "failed": False,
            }],
            "combined_text": "[合并转发聊天记录 · 共1条]\n甲: 缓存内容",
            "total_nodes": 1,
            "image_urls": [],
            "truncated": False,
        }
        event.set_extra(PARSED_FORWARDS_EXTRA, prebuilt)

        parsed = await parse_event_forwards(event)
        self.assertIs(parsed, prebuilt)
        rpc.assert_not_awaited()

        # If another layer already set _companion_forward_injected (without our ownership), skip injection.
        event.set_extra(COMPANION_FORWARD_INJECTED_EXTRA, True)
        req = NS(prompt="看看", extra_user_content_parts=[], image_urls=[])
        self.assertFalse(inject_core_reply_forwards(event, req))
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_forward_adaptation_disabled_suppresses_node_expansion(self):
        async def call_action(action, **params):
            return {"messages": [_node("秘密发言人", [{"type": "text", "data": {"text": "绝密内容"}}])]}

        event = _make_event([NS(type="Forward", id="fwd-secret")], call_action=call_action)
        await parse_event_forwards(event)
        req = NS(prompt=" ", extra_user_content_parts=[], image_urls=[])
        self.assertTrue(inject_core_reply_forwards(
            event, req, prepared={"forward_adaptation_enabled": False},
        ))
        self.assertNotIn("绝密内容", req.extra_user_content_parts[0].text)
        self.assertEqual(req.image_urls, [])


class CoreReplyForwardDebounceAndMainTests(unittest.IsolatedAsyncioTestCase):
    async def test_eight_forwards_in_15s_plus_followup_text_coalesce_into_one_turn(self):
        """Reproduces the 8-forward burst in 15s + user question -> 1 turn, 0 dropped cards."""
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        now = [100.0]
        # Default policy (min=1.5s, max=4.0s, total=8.0s); forward burst scales total -> 20.0s.
        base_policy = Policy(enabled=True, min_seconds=0.04, max_seconds=0.08, total_seconds=0.20)
        self.assertEqual(
            forward_burst_debounce_policy(Policy(), has_forwards=True).total_seconds,
            20.0,
        )
        plugin._private_debounce = PrivateDebounce(log_enabled=False)

        rpc_calls = []

        async def call_action(action, **params):
            fid = str(params.get("message_id") or params.get("id"))
            rpc_calls.append(fid)
            return {
                "messages": [
                    _node(f"群友{fid}", [{"type": "text", "data": {"text": f"第{fid}份聊天记录正文"}}]),
                ]
            }

        target = NS(key="target", target_id="target-qq-200")
        prepared_calls = []

        async def prepare_context(event, _target, group_admission=None):
            return Outcome.success(NS(
                captured=NS(text=event.message_str or ""),
                context=NS(key="ctx-200"),
                debounce=base_policy,
            ))

        async def prepare_core_reply(event, _target, group_admission=None, plan=None):
            prepared_calls.append((event, plan.captured.text if plan is not None else event.message_str))
            return Outcome.success({
                "context": NS(key="ctx-200"),
                "instruction": "Companion persona.",
                "prompt_instruction": "Normal reply.",
                "stable_prompt_instruction": "Stable voice.",
                "target_id": target.target_id,
                "channel": "private",
                "event_id": event.message_obj.message_id,
                "text": plan.captured.text if plan is not None else event.message_str,
                "native_input": NS(text=plan.captured.text if plan is not None else "", event_id=event.message_obj.message_id),
                "forward_adaptation_enabled": True,
            })

        staging = NS(
            runtime=None,
            now=lambda: datetime.now(timezone.utc),
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True, forward_intake=ForwardIntakePolicy(max_nodes=300)),
            context=_fake_native_context(),
            session=NS(
                profile=NS(target=target),
                bridge=NS(forward_intake=ForwardIntakePolicy(max_nodes=300)),
                core_reply_observations=NS(phase=Mock()),
            ),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare_core_reply),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin.staging = staging

        forward_events = [
            _make_event(
                [NS(type="Forward", id=f"fwd-{i}")],
                message_str="",
                message_id=f"msg-{i}",
                call_action=call_action,
                created_at=1_700_000_000.0 + i * 2.0,
            )
            for i in range(1, 9)
        ]
        question_event = _make_event(
            [NS(type="Plain", text="帮我看看这些聊天记录")],
            message_str="帮我看看这些聊天记录",
            message_id="msg-9",
            created_at=1_700_000_018.0,
        )

        # Fire 8 consecutive forwards + 1 follow-up text question spaced inside the burst window.
        tasks = []
        for idx, ev in enumerate(forward_events):
            tasks.append(asyncio.create_task(plugin.receive_companion_message(ev)))
            await asyncio.sleep(0.025)
        tasks.append(asyncio.create_task(plugin.receive_companion_message(question_event)))
        await asyncio.gather(*tasks)

        # Exactly 1 LLM turn triggered (no 8x spam!)
        self.assertEqual(len(prepared_calls), 1)
        winner, captured_text = prepared_calls[0]
        self.assertIs(winner, question_event)
        self.assertEqual(question_event.message_str, "帮我看看这些聊天记录")
        self.assertTrue(question_event.is_wake)
        self.assertTrue(question_event.get_extra(COMPANION_FORWARD_INJECTED_EXTRA))
        self.assertTrue(_has_heavy_content(question_event))

        # Every single RPC called exactly ONCE across the entire burst (zero duplicate RPC).
        self.assertEqual(sorted(rpc_calls), [f"fwd-{i}" for i in range(1, 9)])

        # Each suppressed event retained its own parsed forward with its real message_id.
        for idx, ev in enumerate(forward_events, 1):
            self.assertTrue(ev.call_llm)
            own = get_parsed_forwards(ev)
            self.assertIsNotNone(own)
            self.assertEqual(own["cards"][0]["message_id"], f"msg-{idx}")
            self.assertEqual(own["cards"][0]["forward_id"], f"fwd-{idx}")

        # Winning event carries ALL 8 cards in exact arrival order with real message_id/forward_id!
        aggregated = get_parsed_forwards(question_event)
        self.assertIsNotNone(aggregated)
        self.assertEqual(len(aggregated["cards"]), 8)
        self.assertEqual([c["message_id"] for c in aggregated["cards"]], [f"msg-{i}" for i in range(1, 9)])
        self.assertEqual([c["forward_id"] for c in aggregated["cards"]], [f"fwd-{i}" for i in range(1, 9)])
        self.assertEqual([c["timestamp"] for c in aggregated["cards"]], [1_700_000_000.0 + i * 2.0 for i in range(1, 9)])
        self.assertEqual(aggregated["total_nodes"], 8)
        self.assertIn("帮我看看这些聊天记录", captured_text)
        self.assertIn("第fwd-8份聊天记录正文", captured_text)

        # on_llm_request injects all 8 cards cleanly ONCE.
        req = NS(system_prompt="Base", prompt="帮我看看这些聊天记录", extra_user_content_parts=[], image_urls=[])
        await plugin.prepare_companion_core_request(question_event, req)
        forward_parts = [
            p for p in req.extra_user_content_parts
            if isinstance(getattr(p, "text", None), str) and p.text.startswith(FORWARD_REQUEST_BLOCK_PREFIX)
        ]
        self.assertEqual(len(forward_parts), 1)
        for i in range(1, 9):
            self.assertIn(f"第fwd-{i}份聊天记录正文", forward_parts[0].text)

        # Calling on_llm_request a second time must not duplicate the forward block.
        await plugin.prepare_companion_core_request(question_event, req)
        forward_parts_after = [
            p for p in req.extra_user_content_parts
            if isinstance(getattr(p, "text", None), str) and p.text.startswith(FORWARD_REQUEST_BLOCK_PREFIX)
        ]
        self.assertEqual(len(forward_parts_after), 1)

    async def test_pure_forward_only_burst_penetrates_unpatched_astrbot_and_opens_valid_followup(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._private_debounce = PrivateDebounce(log_enabled=False)
        base_policy = Policy(enabled=True, min_seconds=0.03, max_seconds=0.06, total_seconds=0.15)
        target = NS(key="target", target_id="target-pure")

        async def call_action(action, **params):
            fid = str(params.get("message_id") or params.get("id"))
            return {"messages": [_node("转发来源", [{"type": "text", "data": {"text": f"纯转发-{fid}"}}])]}

        async def prepare_context(event, _target, group_admission=None):
            return Outcome.success(NS(captured=NS(text=""), context=NS(key="ctx-pure"), debounce=base_policy))

        async def prepare_core_reply(event, _target, group_admission=None, plan=None):
            return Outcome.success({
                "context": NS(key="ctx-pure"),
                "instruction": "Persona.",
                "prompt_instruction": "",
                "stable_prompt_instruction": "",
                "target_id": target.target_id,
                "channel": "private",
                "event_id": event.message_obj.message_id,
                "text": plan.captured.text if plan is not None else "",
                "native_input": NS(text=plan.captured.text if plan is not None else "", event_id=event.message_obj.message_id),
                "forward_adaptation_enabled": True,
            })

        plugin.staging = NS(
            runtime=None,
            now=lambda: datetime.now(timezone.utc),
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True, forward_intake=ForwardIntakePolicy(max_nodes=100)),
            context=_fake_native_context(),
            session=NS(
                profile=NS(target=target),
                bridge=NS(forward_intake=ForwardIntakePolicy(max_nodes=100)),
                core_reply_observations=NS(phase=Mock()),
            ),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare_core_reply),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )

        ev1 = _make_event([NS(type="Forward", id="f1")], message_str="", message_id="m1", call_action=call_action)
        ev2 = _make_event([NS(type="Forward", id="f2")], message_str="", message_id="m2", call_action=call_action)
        t1 = asyncio.create_task(plugin.receive_companion_message(ev1))
        await asyncio.sleep(0.015)
        t2 = asyncio.create_task(plugin.receive_companion_message(ev2))
        await asyncio.gather(t1, t2)

        self.assertTrue(ev1.call_llm)
        self.assertFalse(ev2.call_llm)
        self.assertTrue(_passes_unpatched_internal_gate(ev2))
        self.assertTrue(event_will_enter_astrbot_llm(ev2))

        # A late pure-forward arriving before on_llm_request folds cleanly without polluting prompt!
        ev3 = _make_event([NS(type="Forward", id="f3")], message_str="", message_id="m3", call_action=call_action)
        await plugin.receive_companion_message(ev3)
        self.assertTrue(ev3.call_llm)

        req = ev2.get_extra("provider_request")
        await plugin.prepare_companion_core_request(ev2, req)
        self.assertEqual(req.prompt, FORWARD_ONLY_SENTINEL_PROMPT)
        injected = next(p.text for p in req.extra_user_content_parts if p.text.startswith(FORWARD_REQUEST_BLOCK_PREFIX))
        self.assertIn("纯转发-f1", injected)
        self.assertIn("纯转发-f2", injected)
        self.assertIn("纯转发-f3", injected)

    async def test_empty_non_deliverable_event_does_not_open_zombie_followup_window(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(key="target", target_id="target-empty")
        empty_event = _make_event([NS(type="Face", id="1")], message_str="", message_id="msg-empty")
        plugin.staging = NS(
            runtime=None,
            now=lambda: datetime.now(timezone.utc),
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            settings=NS(enabled=True),
            context=_fake_native_context(),
            session=NS(profile=NS(target=target), core_reply_observations=NS(phase=Mock())),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply=AsyncMock(return_value=Outcome.success({
                "context": NS(key="ctx-empty"),
                "instruction": "Persona.",
                "prompt_instruction": "",
                "stable_prompt_instruction": "",
                "target_id": target.target_id,
                "channel": "private",
                "event_id": "msg-empty",
                "text": "[表情]",
                "native_input": NS(text="[表情]", event_id="msg-empty"),
            })),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        await plugin.receive_companion_message(empty_event)
        self.assertFalse(event_will_enter_astrbot_llm(empty_event))
        self.assertIsNone(plugin._private_reply_followups().active_for("target-empty"))


class CoreReplyForwardPersistenceAndGroupTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def test_private_pure_forward_writes_observation_source_and_non_null_turn_input(self):
        async def call_action(action, **params):
            return {
                "messages": [
                    _node("好友A", [{"type": "text", "data": {"text": "下周三去露营记得带帐篷"}}]),
                ]
            }

        event = self.event(identifier="forward-private-1", text="")
        event.message_obj.message = [NS(type="Forward", id="fwd-persist-1")]
        event.message_obj.message_str = ""
        event.message_str = ""
        event.bot.api = NS(call_action=call_action)

        parsed = await parse_event_forwards(event, policy=self.controller.session.bridge.forward_intake)
        self.assertIsNotNone(parsed)

        result = await self.controller.prepare_core_reply(event)
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertIn("下周三去露营记得带帐篷", prepared["text"])

        reference = await prepared["native_source_future"]
        self.assertIsNotNone(reference)

        obs_rows = self.app.runtime.read_all(
            "SELECT observation_id, kind, delivery_state FROM conversation_native_observations", (),
        )
        self.assertEqual(len(obs_rows), 1)
        self.assertEqual(obs_rows[0]["kind"], "input")
        self.assertEqual(obs_rows[0]["delivery_state"], "input_observed")

        turn_rows = self.app.runtime.read_all(
            "SELECT turn_id, generation_state, input_observation_id FROM conversation_native_turns", (),
        )
        self.assertEqual(len(turn_rows), 1)
        self.assertEqual(turn_rows[0]["input_observation_id"], obs_rows[0]["observation_id"])

        source_row = self.app.runtime.read_one(
            "SELECT payload_json FROM sources WHERE source_id = ?", (obs_rows[0]["observation_id"],),
        )
        self.assertIsNotNone(source_row)
        self.assertIn("下周三去露营记得带帐篷", source_row["payload_json"])

    async def test_group_forward_capture_and_observation_preserve_parsed_content(self):
        async def call_action(action, **params):
            return {
                "messages": [
                    _node("群友甲", [{"type": "text", "data": {"text": "群活动改到周六下午两点"}}]),
                ]
            }

        event = self.group_event(identifier="forward-group-1", text="")
        event.message_obj.message = [
            NS(type="At", qq="100"),
            NS(type="Forward", id="fwd-group-1"),
        ]
        event.message_obj.raw_message = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 300,
            "self_id": 100,
            "user_id": 200,
            "sender": {"user_id": 200},
            "message_id": "forward-group-1",
            "message": [
                {"type": "at", "data": {"qq": "100"}},
                {"type": "forward", "data": {"id": "fwd-group-1"}},
            ],
        }
        event.message_obj.message_str = ""
        event.message_str = ""
        event.bot.api = NS(call_action=call_action)

        parsed = await parse_event_forwards(event, policy=self.controller.session.bridge.forward_intake)
        self.assertIsNotNone(parsed)

        candidate = await self.controller.group_reply_candidate(event)
        self.assertTrue(candidate.is_success, candidate.failure)
        admission = candidate.unwrap()
        self.assertTrue(admission["admitted"])

        result = await self.controller.prepare_core_reply(event, group_admission=admission)
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertFalse(prepared["group_trigger"]["bare_mention"])
        self.assertIn("群活动改到周六下午两点", prepared["text"])
        self.assertNotIn("[合并转发不可用]", prepared["text"])

        reference = await prepared["native_source_future"]
        self.assertIsNotNone(reference)
        turn_rows = self.app.runtime.read_all(
            "SELECT input_observation_id FROM conversation_native_turns WHERE turn_id = ?",
            (prepared["turn_id"],),
        )
        self.assertEqual(len(turn_rows), 1)
        self.assertIsNotNone(turn_rows[0]["input_observation_id"])

    def _disable_forward_adaptation(self, context, *, key):
        from companion_v2.conversation.reply_settings import GROUP, target_for

        cfg = self.app.configuration
        target = target_for(context)
        definitions = cfg.registry.group(GROUP)
        owner = cfg._owners[definitions[0].owner_id]
        state = owner.read(target, definitions)
        owner.write(
            target, definitions,
            changes={GROUP + ".forward_message_adaptation_enabled": False},
            reset=(), expected_revision=state.revision, observed_at=self.clock.now(),
        )

    async def test_group_forward_adaptation_disabled_prevents_early_observation_and_db_leak(self):
        secret = "绝密内容-不应该泄露"

        async def call_action(action, **params):
            return {"messages": [_node("群友乙", [{"type": "text", "data": {"text": secret}}])]}

        # Seed context and disable forward_message_adaptation_enabled for this group target.
        seed_event = self.group_event(identifier="seed-group-1", text="先初始化群设置")
        seed_cand = await self.controller.group_reply_candidate(seed_event)
        self.assertTrue(seed_cand.is_success, seed_cand.failure)
        seed_prep = await self.controller.prepare_core_reply(seed_event)
        self.assertTrue(seed_prep.is_success, seed_prep.failure)
        self._disable_forward_adaptation(seed_prep.unwrap()["context"], key="disable-group-forward")

        event = self.group_event(identifier="forward-group-secret-1", text="")
        event.message_obj.message = [
            NS(type="At", qq="100"),
            NS(type="Forward", id="fwd-group-secret"),
        ]
        event.message_obj.raw_message = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 300,
            "self_id": 100,
            "user_id": 200,
            "sender": {"user_id": 200},
            "message_id": "forward-group-secret-1",
            "message": [
                {"type": "at", "data": {"qq": "100"}},
                {"type": "forward", "data": {"id": "fwd-group-secret"}},
            ],
        }
        event.message_obj.message_str = ""
        event.message_str = ""
        event.bot.api = NS(call_action=call_action)

        await parse_event_forwards(event, policy=self.controller.session.bridge.forward_intake)
        candidate = await self.controller.group_reply_candidate(event)
        self.assertTrue(candidate.is_success, candidate.failure)
        admission = candidate.unwrap()
        self.assertTrue(admission["admitted"])

        result = await self.controller.prepare_core_reply(event, group_admission=admission)
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertFalse(prepared["forward_adaptation_enabled"])
        self.assertNotIn(secret, prepared["text"])
        self.assertEqual(prepared["text"], FORWARD_DISABLED_SUMMARY)
        await prepared["native_source_future"]

        for table, column in (("runtime_metadata", "value"), ("sources", "payload_json")):
            rows = self.app.runtime.read_all(f"SELECT {column} FROM {table}", ())
            joined = " ".join(str(row[column]) for row in rows)
            self.assertNotIn(secret, joined, f"secret leaked into {table}")

    async def test_private_forward_adaptation_disabled_preserves_face_sticker_and_stt(self):
        secret = "私聊绝密转发正文"

        async def call_action(action, **params):
            return {"messages": [_node("外部好友", [{"type": "text", "data": {"text": secret}}])]}

        seed_event = self.event(identifier="seed-private-1", text="先初始化私聊设置")
        seed_prep = await self.controller.prepare_core_reply(seed_event)
        self.assertTrue(seed_prep.is_success, seed_prep.failure)
        self._disable_forward_adaptation(seed_prep.unwrap()["context"], key="disable-private-forward")

        event = self.event(identifier="forward-private-face-1", text="")
        event.message_obj.message = [
            NS(type="Face", id="13"),
            NS(type="Record", file="voice.amr"),
            NS(type="Forward", id="fwd-private-secret"),
        ]
        event.message_obj.raw_message = {
            "post_type": "message",
            "message": [
                {"type": "face", "data": {"id": "13"}},
                {"type": "image", "data": {"sub_type": 1, "file": "sticker.gif"}},
                {"type": "record", "data": {"file": "voice.amr"}},
                {"type": "forward", "data": {"id": "fwd-private-secret"}},
            ],
        }
        event.message_obj.message_str = ""
        event.message_str = ""
        event.set_extra("astrbot_stt_transcripts", [{"transcript": "听语音这段"}])
        event.bot.api = NS(call_action=call_action)

        await parse_event_forwards(event, policy=self.controller.session.bridge.forward_intake)
        result = await self.controller.prepare_core_reply(event)
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertFalse(prepared["forward_adaptation_enabled"])
        self.assertIn("[表情]", prepared["text"])
        self.assertIn("[表情包]", prepared["text"])
        self.assertIn("听语音这段", prepared["text"])
        self.assertNotIn(secret, prepared["text"])


class CoreReplyForwardHardeningRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_ref_sanitizer_rejects_local_paths_file_uris_and_bare_filenames(self):
        async def call_action(action, **params):
            return {
                "messages": [
                    _node("恶意节点", [
                        {"type": "image", "data": {"file": r"C:\Users\example\.astrbot\data\cmd_config.json"}},
                        {"type": "image", "data": {"url": "file:///etc/passwd"}},
                        {"type": "image", "data": {"file": "/etc/shadow"}},
                        {"type": "image", "data": {"file": "AABBCC112233.jpg"}},
                        {"type": "image", "data": {"url": "data:text/plain;base64,aGVsbG8="}},
                        {"type": "image", "data": {"url": "https://img.example.com/safe.png", "file": "HASH.jpg"}},
                        {"type": "image", "data": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                        {"type": "image", "data": {"file": "base64://iVBORw0KGgo="}},
                    ]),
                    _node("CQ恶意节点", "[CQ:image,file=file:///C:/secret.txt][CQ:image,url=https://img.example.com/cq_ok.jpg]"),
                ]
            }

        event = _make_event([NS(type="Forward", id="fwd-lfi")], call_action=call_action)
        parsed = await parse_event_forwards(event)
        self.assertEqual(
            parsed["image_urls"],
            [
                "https://img.example.com/safe.png",
                "data:image/png;base64,iVBORw0KGgo=",
                "base64://iVBORw0KGgo=",
                "https://img.example.com/cq_ok.jpg",
            ],
        )
        # Even if an external cache put unsafe URLs in _astrbot_parsed_forwards, injection filters them.
        parsed["image_urls"].insert(0, "file:///etc/passwd")
        parsed["image_urls"].insert(0, r"C:\Windows\win.ini")
        req = NS(prompt=" ", extra_user_content_parts=[], image_urls=[])
        inject_core_reply_forwards(event, req)
        self.assertEqual(
            req.image_urls,
            [
                "https://img.example.com/safe.png",
                "data:image/png;base64,iVBORw0KGgo=",
                "base64://iVBORw0KGgo=",
                "https://img.example.com/cq_ok.jpg",
            ],
        )

    async def test_node_budget_exhaustion_skips_subsequent_card_rpc_and_marks_omitted(self):
        rpc_calls = []

        async def call_action(action, **params):
            fid = str(params.get("message_id") or params.get("id"))
            rpc_calls.append(fid)
            return {
                "messages": [
                    _node("甲", [{"type": "text", "data": {"text": f"{fid}-1"}}]),
                    _node("乙", [{"type": "text", "data": {"text": f"{fid}-2"}}]),
                ]
            }

        event = _make_event(
            [NS(type="Forward", id="f1"), NS(type="Forward", id="f2")],
            call_action=call_action,
        )
        parsed = await parse_event_forwards(event, policy=ForwardIntakePolicy(max_forwards=4, max_nodes=2))
        self.assertEqual(rpc_calls, ["f1"])
        self.assertTrue(parsed["truncated"])
        self.assertEqual(len(parsed["cards"]), 2)
        self.assertEqual(parsed["cards"][0]["node_count"], 2)
        self.assertEqual(parsed["cards"][1]["node_count"], 0)
        self.assertTrue(parsed["cards"][1]["truncated"])
        self.assertFalse(parsed["cards"][1]["failed"])
        self.assertEqual(parsed["cards"][1]["summary_text"], FORWARD_OMITTED_LIMIT_SUMMARY)

    async def test_empty_cards_rejected_no_aliasing_and_cross_payload_dedup_with_fallback(self):
        empty_event = _make_event([NS(type="Plain", text="hi")], message_str="hi")
        empty_event.set_extra(PARSED_FORWARDS_EXTRA, {
            "version": 1, "source": "external", "cards": [],
            "combined_text": "", "total_nodes": 0, "image_urls": [], "truncated": False,
        })
        self.assertIsNone(get_parsed_forwards(empty_event))
        self.assertFalse(_has_heavy_content(empty_event))

        # Single-card coalesce must NOT alias mutable dict or bypass max_nodes/max_images caps.
        big_lines = "\n".join(f"人{i}: 内容{i}" for i in range(50))
        big_payload = {
            "version": 1,
            "source": "astrbot_core",
            "cards": [{
                "forward_id": "fwd-big",
                "message_id": "msg-big",
                "node_count": 50,
                "truncated": False,
                "summary_text": f"[合并转发聊天记录 · 共50条]\n{big_lines}",
                "image_urls": [f"https://img.example.com/{i}.png" for i in range(10)],
                "failed": False,
            }],
            "combined_text": f"[合并转发聊天记录 · 共50条]\n{big_lines}",
            "total_nodes": 50,
            "image_urls": [f"https://img.example.com/{i}.png" for i in range(10)],
            "truncated": False,
        }
        ev1_fwd = _make_event([NS(type="Forward", id="fwd-big")], message_id="msg-big")
        ev1_fwd.set_extra(PARSED_FORWARDS_EXTRA, big_payload)
        ev2_plain = _make_event([NS(type="Plain", text="看看")], message_str="看看", message_id="msg-plain")

        merged = await coalesce_debounced_forwards(
            ev2_plain,
            ((ev1_fwd, "", 1.0), (ev2_plain, "看看", 2.0)),
            policy=ForwardIntakePolicy(max_nodes=5),
        )
        self.assertIsNot(merged, big_payload)
        self.assertEqual(merged["total_nodes"], 5)
        self.assertEqual(len(merged["image_urls"]), MAX_CORE_FORWARD_IMAGES)
        self.assertTrue(merged["truncated"])

        # Cross-payload dedup by per-payload occurrence index + NapCat same-id multi-card + shared-cache fallback compat.
        p1 = {
            "version": 1,
            "cards": [
                {"message_id": "m1", "forward_id": "f1", "node_count": 1, "summary_text": "[合并转发聊天记录 · 共1条]\nA: 卡1", "image_urls": []},
                {"message_id": "m2", "forward_id": "f2", "node_count": 1, "summary_text": "[合并转发聊天记录 · 共1条]\nB: 卡2", "image_urls": []},
            ],
        }
        p2 = {
            "version": 1,
            "cards": [
                {"message_id": "m2", "forward_id": "f2", "node_count": 1, "summary_text": "[合并转发聊天记录 · 共1条]\nB: 卡2", "image_urls": []},
                {"message_id": "m3", "forward_id": "f3", "node_count": 0, "summary_text": FORWARD_FALLBACK_SUMMARY, "image_urls": [], "fallback": True},
            ],
        }
        deduped = merge_parsed_forward_payloads([p1, p2])
        self.assertEqual([(c["message_id"], c["forward_id"]) for c in deduped["cards"]], [("m1", "f1"), ("m2", "f2"), ("m3", "f3")])
        self.assertTrue(deduped["cards"][2]["failed"])

        # Two distinct cards inside one outer NapCat message sharing (m9, f9) stay both preserved.
        same_outer = {
            "version": 1,
            "cards": [
                {"message_id": "m9", "forward_id": "f9", "node_count": 1, "summary_text": "[合并转发聊天记录 · 共1条]\nA: 第一份", "image_urls": []},
                {"message_id": "m9", "forward_id": "f9", "node_count": 1, "summary_text": "[合并转发聊天记录 · 共1条]\nB: 第二份", "image_urls": []},
            ],
        }
        kept_both = merge_parsed_forward_payloads([same_outer])
        self.assertEqual(len(kept_both["cards"]), 2)

    async def test_finished_status_and_external_task_cancellation_never_crash_winner(self):
        # Case A: external cancellation of ev1's background parse task recovers cleanly in ev2!
        rpc_count = [0]
        started = asyncio.Event()
        release = asyncio.Event()

        async def call_action(action, **params):
            rpc_count[0] += 1
            if rpc_count[0] == 1:
                started.set()
                await release.wait()
            return {"messages": [_node("甲", [{"type": "text", "data": {"text": "恢复后的内容"}}])]}

        ev1 = _make_event([NS(type="Forward", id="fwd-race")], message_id="m-race", call_action=call_action)
        task = ensure_event_forwards_parsed(ev1)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        waiter = asyncio.create_task(await_event_forwards_parsed(ev1))
        await asyncio.sleep(0)
        task.cancel()
        result = await waiter
        self.assertIsNotNone(result)
        self.assertIn("恢复后的内容", result["combined_text"])

        # Case B: status == "finished" in receive_companion_message does NOT cancel ev1's parse task!
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin._private_debounce = PrivateDebounce(log_enabled=False)
        target = NS(key="target", target_id="target-race")
        policy = Policy(enabled=True, min_seconds=0.02, max_seconds=0.04, total_seconds=0.10)

        ev1_ctx_release = asyncio.Event()
        ev1_rpc_release = asyncio.Event()

        async def slow_forward_rpc(action, **params):
            await ev1_rpc_release.wait()
            return {"messages": [_node("甲", [{"type": "text", "data": {"text": "竞态安全内容"}}])]}

        async def prepare_context(event, _target, group_admission=None):
            if event.message_obj.message_id == "race-1":
                await ev1_ctx_release.wait()
            return Outcome.success(NS(captured=NS(text=event.message_str or ""), context=NS(key="ctx-race"), debounce=policy))

        prepared_wins = []

        async def prepare_core_reply(event, _target, group_admission=None, plan=None):
            prepared_wins.append(event)
            return Outcome.success({
                "context": NS(key="ctx-race"), "instruction": "Persona.", "prompt_instruction": "",
                "stable_prompt_instruction": "", "target_id": target.target_id, "channel": "private",
                "event_id": event.message_obj.message_id, "text": plan.captured.text if plan else "",
                "native_input": NS(text=plan.captured.text if plan else "", event_id=event.message_obj.message_id),
                "forward_adaptation_enabled": True,
            })

        plugin.staging = NS(
            runtime=None, now=lambda: datetime.now(timezone.utc), claims=lambda _e: True,
            target_for_event=lambda _e: target,
            settings=NS(enabled=True, forward_intake=ForwardIntakePolicy()),
            context=_fake_native_context(),
            session=NS(profile=NS(target=target), bridge=NS(forward_intake=ForwardIntakePolicy()),
                       core_reply_observations=NS(phase=Mock())),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply_context=AsyncMock(side_effect=prepare_context),
            prepare_core_reply=AsyncMock(side_effect=prepare_core_reply),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )

        ev_f = _make_event([NS(type="Forward", id="f-race-2")], message_str="", message_id="race-1", call_action=slow_forward_rpc)
        ev_t = _make_event([NS(type="Plain", text="好了")], message_str="好了", message_id="race-2")
        t_f = asyncio.create_task(plugin.receive_companion_message(ev_f))
        await asyncio.sleep(0.01)
        t_t = asyncio.create_task(plugin.receive_companion_message(ev_t))
        await asyncio.sleep(0.08)  # ev_t finishes debounce while ev_f is still blocked before configure()
        ev1_ctx_release.set()      # ev_f now hits configure -> "finished" while ev_t awaits ev_f's parse task!
        await asyncio.sleep(0.02)
        ev1_rpc_release.set()
        await asyncio.gather(t_f, t_t)

        self.assertEqual(len(prepared_wins), 1)
        self.assertIs(prepared_wins[0], ev_t)
        self.assertIsNotNone(get_parsed_forwards(ev_f))
        self.assertIsNotNone(get_parsed_forwards(ev_t))
        self.assertIn("竞态安全内容", get_parsed_forwards(ev_t)["combined_text"])

    async def test_pure_forward_followup_updates_prepared_metadata_and_respects_adaptation(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(key="target", target_id="target-fu")
        plugin.staging = NS(
            runtime=None, now=lambda: datetime.now(timezone.utc),
            settings=NS(enabled=True, forward_intake=ForwardIntakePolicy()),
            session=NS(bridge=NS(forward_intake=ForwardIntakePolicy())),
        )

        for adaptation_enabled in (True, False):
            with self.subTest(adaptation_enabled=adaptation_enabled):
                plugin._private_reply_followups().close()
                ev1 = _make_event([NS(type="Plain", text="帮我看看")], message_str="帮我看看", message_id="fu-1", created_at=1_700_000_001.0)
                ev2 = _make_event([NS(type="Forward", id="f-fu")], message_str="", message_id="fu-2", created_at=1_700_000_002.0)
                ev2.set_extra(PARSED_FORWARDS_EXTRA, {
                    "version": 1, "source": "private_companion",
                    "cards": [{
                        "forward_id": "f-fu", "message_id": "fu-2", "node_count": 1,
                        "truncated": False, "summary_text": "[合并转发聊天记录 · 共1条]\n甲: 追发正文",
                        "image_urls": [], "failed": False,
                    }],
                    "combined_text": "[合并转发聊天记录 · 共1条]\n甲: 追发正文",
                    "total_nodes": 1, "image_urls": [], "truncated": False,
                })
                prepared = {
                    "event_ref": "ref-fu",
                    "text": "帮我看看",
                    "forward_adaptation_enabled": adaptation_enabled,
                }
                ev1.set_extra(ENTRY.CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
                plugin._private_reply_followups().start(
                    target.target_id, ev1, "ref-fu", ((ev1, "帮我看看", ev1.created_at),),
                )
                self.assertTrue(plugin._fold_private_follow_up(ev2, target, "ref-fu-2", "", allow_empty=True))

                req = NS(prompt="帮我看看", extra_user_content_parts=[], image_urls=[])
                plugin._apply_private_follow_ups_to_request(ev1, req)
                self.assertEqual(req.prompt, "帮我看看")
                self.assertEqual(prepared["debounced_message_count"], 2)
                self.assertIn("debounced_inputs", prepared)
                if adaptation_enabled:
                    self.assertIn("追发正文", prepared["text"])
                else:
                    self.assertNotIn("追发正文", prepared["text"])
                    self.assertEqual(prepared["text"], "帮我看看")


if __name__ == "__main__":
    unittest.main()
