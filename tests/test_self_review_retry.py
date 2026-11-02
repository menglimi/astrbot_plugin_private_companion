"""Deterministic self-review output failures back off instead of retrying fast."""

import unittest

from companion_v2.role_work.journal.self_review_service import (
    OUTPUT_FAILURE_RETRY_SECONDS,
    TRANSIENT_FAILURE_RETRY_SECONDS,
    retry_delay,
)


class SelfReviewRetryTests(unittest.TestCase):
    def test_output_contract_failure_backs_off_for_hours(self):
        self.assertEqual(retry_delay("self_review_output_invalid"), OUTPUT_FAILURE_RETRY_SECONDS)
        self.assertGreaterEqual(OUTPUT_FAILURE_RETRY_SECONDS, 3600)

    def test_material_change_does_not_hot_loop_either(self):
        self.assertEqual(retry_delay("self_review_material_changed"), OUTPUT_FAILURE_RETRY_SECONDS)

    def test_transient_failure_keeps_the_short_retry(self):
        self.assertEqual(retry_delay("model_transport_error"), TRANSIENT_FAILURE_RETRY_SECONDS)
        self.assertEqual(retry_delay(None), TRANSIENT_FAILURE_RETRY_SECONDS)


if __name__ == "__main__":
    unittest.main()
