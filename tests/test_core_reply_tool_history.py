"""Unpaired tool-call batches must never reach an OpenAI-compatible provider."""

import unittest

from astrbot_host.core_reply import repair_tool_call_history


def call(identifier):
    return {"id": identifier, "type": "function",
            "function": {"name": "companion_v2_reaction_search", "arguments": "{}"}}


class RepairToolCallHistoryTests(unittest.TestCase):
    def test_complete_batch_is_kept(self):
        contexts = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [call("a")]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        self.assertEqual(repair_tool_call_history(contexts), 0)
        self.assertEqual(len(contexts), 4)

    def test_orphan_batch_is_dropped_and_visible_text_kept(self):
        contexts = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "看看", "tool_calls": [call("a")]},
            {"role": "user", "content": "next"},
        ]
        self.assertEqual(repair_tool_call_history(contexts), 1)
        self.assertEqual([item["role"] for item in contexts], ["user", "assistant", "user"])
        self.assertNotIn("tool_calls", contexts[1])
        self.assertEqual(contexts[1]["content"], "看看")

    def test_partially_answered_batch_is_dropped_with_stray_results(self):
        contexts = [
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
            {"role": "tool", "tool_call_id": "a", "content": "only one"},
            {"role": "user", "content": "继续"},
        ]
        self.assertEqual(repair_tool_call_history(contexts), 1)
        self.assertEqual([item["role"] for item in contexts], ["user"])

    def test_missing_history_is_ignored(self):
        self.assertEqual(repair_tool_call_history(None), 0)
        self.assertEqual(repair_tool_call_history("not a list"), 0)


if __name__ == "__main__":
    unittest.main()
