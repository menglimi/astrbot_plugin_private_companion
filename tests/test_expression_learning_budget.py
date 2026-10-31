"""The learning ask must fit the output ceiling the task executes under.

Production evidence (2026-10-30): one compliant three-candidate answer occupied
1098-1318 output tokens, i.e. 54-64% of the 2048-token ceiling every task
inherits by default. A later run asked the same question and was cut after 123
visible characters because the model spent the rest of the ceiling on its own
reasoning. Asking for the product maximum regardless of the ceiling is the bug
these tests pin down.
"""

import unittest

from companion_v2.expression import learning
from companion_v2.expression.configuration import MODES
from companion_v2.expression.style_service import learning_instruction


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


if __name__ == "__main__":
    unittest.main()
