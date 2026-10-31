"""Verify that stored tool-call history is made legal without losing meaning.

Production shape this reproduces (2026-10-30/31, session
``onebot_qq:GroupMessage:1032290619``): the persisted conversation contained

    49 assistant  tool_calls=[call_314584 companion_v2_group_participation_choose]
    50 assistant  "除了我家那个死脑筋的经纪人还能有谁？..."

i.e. an assistant message whose tool call is answered by nothing. Every provider
of the fallback chain rejected the request -- OpenAI-compatible relays with
"An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id'", Gemini relays with "functionCall appears
before 1 pending functionResponse part(s)" -- and the persona's custom error line
(``出错了......``) was sent to the chat group three times.
"""

from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot.core.agent.message import Message, TextPart
from astrbot.core.provider.entities import LLMResponse

from astrbot_host import core_reply_history_guard as history_guard
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_history_guard import (
    repair_request_history,
    repair_run_context_history,
)
from companion_v2.conversation.history_legality import (
    MISSING_TOOL_RESULT_TEXT,
    repair_tool_call_pairing,
    tool_call_pairing_is_legal,
)

logger_name = history_guard.__name__


def tool_call(call_id, name="companion_v2_group_participation_choose", arguments="{}"):
    return {"type": "function", "id": call_id, "function": {"name": name, "arguments": arguments}}


def assistant_calls(*call_ids):
    return {"role": "assistant", "content": None, "tool_calls": [tool_call(call_id) for call_id in call_ids]}


def tool_result(call_id, content='{"effect":"none"}'):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def user(text="摸"):
    return {"role": "user", "content": text}


def assistant(text):
    return {"role": "assistant", "content": text}


class _Event:
    """Minimal event carrying the companion request state."""

    def __init__(self, *, event_ref="reference", companion=True):
        self._extra = {}
        if companion:
            self._extra[CORE_REPLY_CONTEXT_EXTRA] = {"prepared": {"event_ref": event_ref}}

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value


class HistoryLegalityTests(unittest.TestCase):
    def test_orphaned_tool_call_is_answered_and_stays_in_place(self):
        messages = [user(), assistant_calls("call_314584"), assistant("除了我家那个死脑筋的经纪人还能有谁？")]

        # The production history is illegal before the repair -- this is the
        # exact condition every provider rejected.
        self.assertFalse(tool_call_pairing_is_legal(messages))

        report = repair_tool_call_pairing(messages)

        self.assertEqual(report.filled, 1)
        self.assertEqual(report.dropped_isolated_tool_messages, 0)
        self.assertTrue(tool_call_pairing_is_legal(messages))
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "assistant"])
        # The call keeps its identity, so the model reads it as the same call
        # that failed -- not as a new instruction.
        self.assertEqual(messages[2]["tool_call_id"], "call_314584")
        self.assertTrue(messages[2]["content"].startswith("error:"))
        self.assertIn(MISSING_TOOL_RESULT_TEXT, messages[2]["content"])
        # Nothing else moved or changed.
        self.assertEqual(messages[3], assistant("除了我家那个死脑筋的经纪人还能有谁？"))
        self.assertEqual(messages[0], user())

    def test_legal_multi_round_tool_history_is_untouched(self):
        messages = [
            user("放一首《夏天的风》吧"),
            assistant_calls("call_57225", "call_57226"),
            tool_result("call_57225"),
            tool_result("call_57226", "error: tool play_song execution timeout after 120 seconds."),
            assistant("放歌那边好像卡住了没响起来……"),
            user("和纱握手"),
            assistant_calls("call_460220"),
            tool_result("call_460220"),
            assistant("握什么手啊，少把我当成小狗来使唤。"),
        ]
        before = deepcopy(messages)

        report = repair_tool_call_pairing(messages)

        self.assertFalse(report.changed)
        self.assertEqual(messages, before)

    def test_trim_boundary_that_drops_the_tool_response_is_repaired(self):
        # Turn-based truncation can cut the tail of a round: the assistant call
        # survives in the kept window while its response does not.
        kept = [user("你要揍谁"), assistant_calls("call_269306")]
        report = repair_tool_call_pairing(kept)

        self.assertEqual(report.filled, 1)
        self.assertTrue(tool_call_pairing_is_legal(kept))
        self.assertEqual(kept[2]["tool_call_id"], "call_269306")

    def test_trim_boundary_that_drops_the_assistant_call_drops_the_orphan_result(self):
        # The opposite cut: the assistant message is gone, the response remains.
        kept = [user("刚睡醒来上这么一集真是惬意"), tool_result("call_689163"), assistant("你管这种吵死人的噪音叫惬意啊？")]

        report = repair_tool_call_pairing(kept)

        self.assertEqual(report.filled, 0)
        self.assertEqual(report.dropped_isolated_tool_messages, 1)
        self.assertEqual([m["role"] for m in kept], ["user", "assistant"])
        self.assertTrue(tool_call_pairing_is_legal(kept))

    def test_repair_is_idempotent(self):
        messages = [user(), assistant_calls("call_a", "call_b"), tool_result("call_a"), assistant("好")]
        repair_tool_call_pairing(messages)
        once = deepcopy(messages)

        report = repair_tool_call_pairing(messages)

        self.assertFalse(report.changed)
        self.assertEqual(messages, once)

    def test_parallel_calls_keep_their_answers_and_only_the_missing_one_is_filled(self):
        messages = [
            user("这个视频讲了什么"),
            assistant_calls("call_315962", "call_315963"),
            tool_result("call_315962"),
            assistant("顶着我的发型让一帮猫来组乐队……"),
        ]

        report = repair_tool_call_pairing(messages)

        self.assertEqual(report.filled, 1)
        self.assertEqual(
            [m.get("tool_call_id") for m in messages if m["role"] == "tool"],
            ["call_315962", "call_315963"],
        )
        self.assertTrue(tool_call_pairing_is_legal(messages))

    def test_duplicate_and_foreign_answers_are_dropped_while_the_call_is_filled(self):
        messages = [
            user(),
            assistant_calls("call_x"),
            tool_result("call_x", "first"),
            tool_result("call_x", "duplicate"),
            tool_result("call_other", "belongs to nobody"),
            assistant("好"),
        ]

        report = repair_tool_call_pairing(messages)

        self.assertEqual(report.filled, 0)
        self.assertEqual(report.dropped_isolated_tool_messages, 2)
        contents = [m.get("content") for m in messages if m["role"] == "tool"]
        self.assertEqual(contents, ["first"])
        self.assertTrue(tool_call_pairing_is_legal(messages))

    def test_messages_without_tools_are_never_touched(self):
        messages = [user("在吗"), {"role": "system", "content": "人格"}, assistant("在")]
        before = deepcopy(messages)

        self.assertTrue(tool_call_pairing_is_legal(messages))
        self.assertFalse(repair_tool_call_pairing(messages).changed)
        self.assertEqual(messages, before)

    def test_repaired_list_survives_the_host_message_model(self):
        # The core path stores pydantic Message objects, not dicts.
        messages = [
            Message(role="user", content="你要揍谁"),
            Message(role="assistant", content=[TextPart(text="")],
                    tool_calls=[{"type": "function", "id": "call_314584",
                                 "function": {"name": "companion_v2_group_participation_choose", "arguments": "{}"}}]),
            Message(role="assistant", content="除了我家那个死脑筋的经纪人还能有谁？"),
        ]

        report = repair_tool_call_pairing(
            messages, factory=lambda call_id, text: Message(role="tool", tool_call_id=call_id, content=text))

        self.assertEqual(report.filled, 1)
        self.assertIsInstance(messages[2], Message)
        self.assertEqual(messages[2].tool_call_id, "call_314584")
        self.assertTrue(tool_call_pairing_is_legal(messages))
        # What the provider receives must be a valid tool message.
        dumped = messages[2].model_dump()
        self.assertEqual(dumped["role"], "tool")
        self.assertEqual(dumped["tool_call_id"], "call_314584")


class CoreReplyHistoryGuardTests(unittest.TestCase):
    def test_request_hook_repairs_the_contexts_the_sdk_will_convert(self):
        event = _Event(event_ref="f0214b76bd5a4f60872ca89e16cc587f")
        req = NS(contexts=[user(), assistant_calls("call_314584"), assistant("除了我家那个死脑筋的经纪人还能有谁？")])

        report = repair_request_history(event, req)

        self.assertEqual(report.filled, 1)
        self.assertTrue(tool_call_pairing_is_legal(req.contexts))
        # Second pass is a no-op: the hook can run again without growing history.
        self.assertFalse(repair_request_history(event, req).changed)
        self.assertEqual(len(req.contexts), 4)

    def test_run_context_hook_repairs_messages_a_later_hook_broke(self):
        event = _Event()
        context = NS(messages=[
            Message(role="user", content="刚睡醒来上这么一集真是惬意"),
            Message(role="assistant", content=None,
                    tool_calls=[{"type": "function", "id": "call_689163",
                                 "function": {"name": "companion_v2_group_participation_choose", "arguments": "{}"}}]),
            Message(role="assistant", content="你管这种吵死人的噪音叫惬意啊？"),
        ])

        report = repair_run_context_history(event, context)

        self.assertEqual(report.filled, 1)
        self.assertTrue(tool_call_pairing_is_legal(context.messages))

    def test_guard_stays_out_of_other_plugins_conversations(self):
        event = _Event(companion=False)
        req = NS(contexts=[user(), assistant_calls("call_1"), assistant("好")])
        before = deepcopy(req.contexts)

        report = repair_request_history(event, req)

        # The plugin only repairs the conversations it answers in, like every
        # other core-path hook it registers.
        self.assertFalse(report.changed)
        self.assertEqual(req.contexts, before)

    def test_empty_and_missing_contexts_are_safe(self):
        event = _Event()
        self.assertFalse(repair_request_history(event, NS(contexts=None)).changed)
        self.assertFalse(repair_request_history(event, NS(contexts=[])).changed)
        self.assertFalse(repair_run_context_history(event, NS()).changed)

    def test_llm_response_shape_is_not_mistaken_for_history(self):
        # A runner response is not a message list; the guard must not touch it.
        event = _Event()
        response = LLMResponse(role="assistant", completion_text="好")
        self.assertFalse(repair_run_context_history(event, NS(messages=response)).changed)

    def test_a_failure_inside_the_guard_never_costs_the_turn(self):
        # The guard runs so that one damaged entry cannot end the turn, so its own
        # failure must stay contained and named instead of escaping to the host.
        event = _Event()
        req = NS(contexts=[user(), assistant_calls("call_314584"), assistant("好")])
        before = deepcopy(req.contexts)

        with patch.object(history_guard, "repair_tool_call_pairing", side_effect=ValueError("boom")):
            with self.assertLogs(logger_name, level="WARNING") as captured:
                report = history_guard.repair_request_history(event, req)

        self.assertFalse(report.changed)
        self.assertEqual(req.contexts, before, "the history is left exactly as it arrived")
        self.assertIn("复核失败", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
