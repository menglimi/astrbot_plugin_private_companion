"""The learning ask must fit the output ceiling the task executes under.

Production evidence (2026-10-30): one compliant three-candidate answer occupied
1098-1318 output tokens, i.e. 54-64% of the 2048-token ceiling every task
inherits by default. A later run asked the same question and was cut after 123
visible characters because the model spent the rest of the ceiling on its own
reasoning. Asking for the product maximum regardless of the ceiling is the bug
these tests pin down.

The same batch has to fit the *input* ceiling as well: a window's newest 80
reference rules can exceed the default 131072-byte ceiling on their own once
JSON-escaped by the adapter's wire (2026-11-01), so the batch trims them instead
of failing. The floor keeps the priors deduplication needs, and the round bound
is derived from the same read limit and the same two-thirds step. Both are the
same budget question, so the trim policy is pinned here.
"""

import unittest

from companion_v2.expression import learning
from companion_v2.expression.configuration import MODES
from companion_v2.expression.style_service import (MAX_TRIM_ROUNDS, MIN_EXISTING_RULES, RULE_READ_LIMIT,
    learning_instruction, reference_rule_priority, trim_reference_rules, trim_rounds)


class LearningBudgetTests(unittest.TestCase):
    def test_the_default_ceiling_only_pays_for_three_candidates(self):
        # 2048 tokens: the measured three-candidate answer plus its JSON envelope
        # already used 1098-1318 of them, and reasoning must fit in the rest.
        for mode in MODES:
            self.assertEqual(learning.signal_limit(mode, 2048), 3, mode)

    def test_a_larger_ceiling_unlocks_the_product_caps(self):
        self.assertEqual(learning.signal_limit("light", 8192), 3)
        self.assertEqual(learning.signal_limit("balanced", 8192), 6)
        self.assertEqual(learning.signal_limit("aggressive", 8192), 12)

    def test_the_ask_never_promises_more_than_the_answer_share(self):
        share, total = learning.LEARNING_ANSWER_SHARE
        for ceiling in (1024, 2048, 4096, 8192, 32768):
            for mode in MODES:
                with self.subTest(ceiling=ceiling, mode=mode):
                    limit = learning.signal_limit(mode, ceiling)
                    self.assertGreaterEqual(limit, 1)
                    self.assertLessEqual(limit, learning.MODE_SIGNAL_CAPS[mode])
                    estimated = (limit * learning.LEARNING_SIGNAL_TOKENS
                                 + learning.LEARNING_SUMMARY_TOKENS)
                    self.assertLessEqual(estimated, ceiling * share // total)

    def test_a_tiny_ceiling_still_asks_for_one_candidate(self):
        self.assertEqual(learning.signal_limit("balanced", 256), 1)

    def test_every_mode_has_a_cap_and_guidance(self):
        self.assertEqual(set(learning.MODE_SIGNAL_CAPS), set(MODES))
        self.assertEqual(set(learning.MODE_GUIDANCE), set(MODES))

    def test_the_instruction_states_the_derived_count_and_text_bound(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                instruction = learning.mode_instruction(mode, 3)
                self.assertIn("3条", instruction)
                self.assertIn(str(learning.text_budget(3)), instruction)
                cap = learning.MODE_SIGNAL_CAPS[mode]
                if cap != 3:
                    self.assertNotIn(f"{cap}条", instruction)

    def test_the_schema_bounds_the_array_to_the_same_number(self):
        self.assertEqual(learning.schema(3)["properties"]["signals"]["maxItems"], 3)
        self.assertEqual(learning.schema()["properties"]["signals"]["maxItems"],
                         learning.MAX_SIGNALS_PER_RUN)
        self.assertEqual(learning.SCHEMA, learning.schema())

    def test_the_service_builds_the_same_number_into_the_request(self):
        limit = learning.signal_limit("balanced", 2048)
        instruction = learning_instruction("balanced", limit)
        self.assertIn(learning.mode_instruction("balanced", limit), instruction)
        self.assertIn(learning.FORM_INSTRUCTION, instruction)
        self.assertIn(f"{limit}条", instruction)
        self.assertIn(str(learning.text_budget(limit)), instruction)


class ReferenceRuleTrimTests(unittest.TestCase):
    """The reference rules are the material one batch can afford to lose."""

    def test_one_step_keeps_the_affordable_rules_in_stored_order(self):
        # Plain values rank like the lowest state, so value order falls back to
        # the stored newest-first order and the step behaves as a prefix.
        rules = tuple(range(RULE_READ_LIMIT))
        kept = trim_reference_rules(rules)
        self.assertEqual(kept, list(rules)[:len(kept)], "the stored order is newest first")
        self.assertLess(len(kept), len(rules))

    def test_a_lowest_value_window_keeps_the_newest_prefix(self):
        rules = tuple({"signal_id": index, "state": "rejected"} for index in range(RULE_READ_LIMIT))
        kept = trim_reference_rules(rules)
        self.assertEqual(kept, list(rules)[:len(kept)], "equal value falls back to recency")

    def test_an_older_adopted_rule_outranks_a_newer_rejected_one(self):
        # The adopted rule sits at the oldest end of a newest-first window: a
        # recency-only trim would drop it before any rejected rule.
        rules = [{"signal_id": index, "state": "rejected"} for index in range(RULE_READ_LIMIT)]
        rules[-1] = {"signal_id": "adopted", "state": "adopted"}
        kept = trim_reference_rules(rules)
        self.assertIn("adopted", [rule["signal_id"] for rule in kept])
        self.assertEqual(kept, [rule for rule in rules if rule in kept],
                         "the priority order must not reorder the prompt")
        self.assertEqual(len(kept), max(MIN_EXISTING_RULES, len(rules) * 2 // 3))
        # adopted 先于 pending，pending 先于其余状态
        self.assertLess(reference_rule_priority({"state": "adopted"}),
                        reference_rule_priority({"state": "pending"}))
        self.assertLess(reference_rule_priority({"state": "pending"}),
                        reference_rule_priority({"state": "rejected"}))

    def test_a_missing_or_unknown_state_ranks_lowest(self):
        rules = [{"signal_id": index} for index in range(RULE_READ_LIMIT)]
        rules[-1] = {"signal_id": "retired", "state": "retired"}
        kept = trim_reference_rules(rules)
        self.assertEqual(kept, list(rules)[:len(kept)], "unknown states are the cheapest to drop")

    def test_the_floor_is_never_crossed(self):
        for count in range(MIN_EXISTING_RULES + 1):
            with self.subTest(count=count):
                self.assertEqual(len(trim_reference_rules(tuple(range(count)))), count)
        for count in (9, 12, 40, RULE_READ_LIMIT):
            with self.subTest(count=count):
                self.assertGreaterEqual(len(trim_reference_rules(tuple(range(count)))),
                                        MIN_EXISTING_RULES)

    def test_the_round_bound_reaches_the_floor_from_a_full_window(self):
        # The bound exists so a pathological estimate cannot loop, not to leave a
        # request that still does not fit: a full window must land on the floor.
        rules = tuple(range(RULE_READ_LIMIT))
        for _ in range(MAX_TRIM_ROUNDS):
            rules = trim_reference_rules(rules)
        self.assertEqual(len(rules), MIN_EXISTING_RULES)

    def test_the_round_bound_follows_the_read_limit(self):
        # Not six by decree: the bound is whatever the same step needs from the
        # window the service actually reads, so raising the read limit raises it.
        self.assertEqual(MAX_TRIM_ROUNDS, trim_rounds(RULE_READ_LIMIT))
        self.assertGreater(trim_rounds(RULE_READ_LIMIT * 2), MAX_TRIM_ROUNDS)
        self.assertEqual(trim_rounds(MIN_EXISTING_RULES), 0)


if __name__ == "__main__":
    unittest.main()
