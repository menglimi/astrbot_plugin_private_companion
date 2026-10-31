"""Group image safety review: configuration, verdicts and the review ledger.

These tests exercise the review settings, the answer vocabulary, the verdict
contract and the durable ledger over real V2 storage. The synthetic verdicts
stand in for the vision model so the decision's behaviour is provable without a
provider: an unsafe or unusable answer must never let the reviewed original reach
the group audience.
"""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry, ValueKind
from companion_v2.conversation.media.review import (ALLOW, BLOCK, DEFAULTS, DUAL_PROVIDER_FIELD, GROUP,
                                                    ImageReviewConfigurationOwner, LABELS, LIMITS, ReviewPlan,
                                                    WITHHOLD, check_verdict, configuration_fields, decide,
                                                    merge_answers, normalize_label, parse_answer, review_prompt,
                                                    review_settings, verdict_document)
from companion_v2.conversation.media.fallback import ImageReviewFallback
from companion_v2.execution.models import TaskError, stamp
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW


class ReviewConfigurationTests(unittest.TestCase):
    def test_registry_contract_matches_the_v1_setting_names(self):
        fields = configuration_fields()
        names = tuple(f.field_id.removeprefix(GROUP + ".") for f in fields)
        self.assertEqual(names[:len(DEFAULTS)], tuple(DEFAULTS))
        self.assertEqual(names[len(DEFAULTS):], ("review_provider", "review_dual_provider"))
        for field in fields:
            self.assertEqual((field.consumer_id, field.owner_id, field.layers, field.apply_mode),
                             (GROUP, GROUP, (ConfigLayer.ROLE, ConfigLayer.WINDOW), ApplyMode.IMMEDIATE))
        registry = FieldRegistry(version="image-review.v1", fields=fields)
        self.assertEqual({f.field_id for f in registry.group(GROUP)}, {f.field_id for f in fields})
    def test_declared_bounds_are_the_ones_enforced(self):
        values = {f.field_id.removeprefix(GROUP + "."): f.default for f in configuration_fields()}
        settings = review_settings(values)
        self.assertFalse(settings["enable_group_nsfw_private_fallback"])
        self.assertEqual(settings["group_nsfw_image_review_mode"], "single")
        self.assertEqual(settings["group_nsfw_image_review_sensitivity"], "balanced")
        self.assertEqual(settings["group_nsfw_image_review_failure_action"], "private")
        self.assertEqual(settings["group_nsfw_image_review_max_images"], 1)
        self.assertEqual(settings["group_nsfw_image_review_max_dimension"], 1280)
        declared = {f.field_id.removeprefix(GROUP + "."): (f.minimum, f.maximum) for f in configuration_fields()}
        for name, (minimum, maximum) in LIMITS.items():
            self.assertEqual(declared[name], (minimum, maximum))
            with self.assertRaises(TaskError):
                review_settings({**values, name: minimum - 1})
            with self.assertRaises(TaskError):
                review_settings({**values, name: maximum + 1})
        for name, bad in (("group_nsfw_image_review_mode", "triple"),
                          ("group_nsfw_image_review_sensitivity", "loose"),
                          ("group_nsfw_image_review_failure_action", "ignore"),
                          ("enable_group_nsfw_private_fallback", "yes"),
                          ("group_nsfw_image_review_custom_prompt", "x" * 1201)):
            with self.assertRaises(TaskError):
                review_settings({**values, name: bad})

    def test_each_setting_declares_the_kind_that_can_hold_its_default(self):
        kinds = {f.field_id: f.kind for f in configuration_fields()}
        self.assertIs(kinds[GROUP + ".group_nsfw_image_review_min_confidence"], ValueKind.PROBABILITY)
        self.assertIs(kinds[GROUP + ".group_nsfw_image_review_timeout_seconds"], ValueKind.WEIGHT)
        self.assertIs(kinds[GROUP + ".enable_group_nsfw_private_fallback"], ValueKind.BOOLEAN)
        self.assertIs(kinds[GROUP + ".group_nsfw_image_review_max_images"], ValueKind.INTEGER)
        self.assertIs(kinds[GROUP + ".group_nsfw_image_review_max_dimension"], ValueKind.INTEGER)
        self.assertIs(kinds[GROUP + ".group_nsfw_image_review_custom_prompt"], ValueKind.TEXT)
        self.assertIs(kinds[DUAL_PROVIDER_FIELD], ValueKind.TEXT)


class ReviewAnswerTests(unittest.TestCase):
    def test_unsafe_answers_can_never_be_read_as_safe(self):
        for answer in ("not safe", "unsafe", "nsfw", "explicit", "adult_nsfw", "不安全", "露骨", "色情"):
            self.assertEqual(normalize_label(answer), "adult_nsfw", answer)
        for answer in ("disallowed", "underage", "minor", "illegal", "未成年", "违法"):
            self.assertEqual(normalize_label(answer), "disallowed", answer)
        for answer in ("safe", "SAFE", "normal", "安全", "正常"):
            self.assertEqual(normalize_label(answer), "safe", answer)
        for answer in ("maybe later", None, "", 7):
            self.assertEqual(normalize_label(answer), "uncertain", repr(answer))

    def test_structured_answers_report_their_confidence(self):
        self.assertEqual(parse_answer('{"label":"safe","confidence":0.91}'), ("safe", 0.91))
        self.assertEqual(parse_answer('{"label":"nsfw","confidence":2}'), ("adult_nsfw", 1.0))
        self.assertEqual(parse_answer('{"label":"safe"}'), ("safe", 0.0))
        self.assertEqual(parse_answer("safe"), ("safe", 0.0))
        self.assertEqual(parse_answer(""), ("uncertain", 0.0))

    def test_dual_answers_keep_the_stricter_label_and_lower_confidence(self):
        merged = merge_answers([{"provider_id": "a", "label": "safe", "confidence": 0.95},
                                {"provider_id": "b", "label": "adult_nsfw", "confidence": 0.88}])
        self.assertEqual(merged["label"], "adult_nsfw")
        self.assertEqual(merged["confidence"], 0.88)
        self.assertEqual(merged["provider_ids"], ["a", "b"])
        self.assertIsNone(merge_answers([{"provider_id": "a", "label": "safe", "confidence": 0.9},
                                         {"provider_id": "b", "label": "uncertain", "confidence": 0.2}]))

    def test_sensitivity_and_custom_rules_reach_the_prompt(self):
        settings = review_settings({**DEFAULTS, "group_nsfw_image_review_sensitivity": "strict",
                                   "group_nsfw_image_review_custom_prompt": "明显血腥内容不得发群"})
        prompt = review_prompt(settings)
        self.assertIn("严格标准", prompt)
        self.assertIn("明显血腥内容不得发群", prompt)
        self.assertIn("不能把非法内容判为 safe", prompt)


class ReviewDecisionTests(unittest.TestCase):
    def settings(self, **changes):
        return review_settings({**DEFAULTS, "enable_group_nsfw_private_fallback": True, **changes})

    def test_a_disabled_review_is_the_only_path_that_sends_unreviewed(self):
        self.assertEqual(decide("adult_nsfw", review_settings(dict(DEFAULTS)), ran=True), (ALLOW, "review_disabled"))

    def test_only_a_confident_safe_label_reaches_the_group(self):
        self.assertEqual(decide("safe", self.settings(), ran=True), (ALLOW, "safe"))
        self.assertEqual(decide("adult_nsfw", self.settings(), ran=True)[0], WITHHOLD)
        self.assertEqual(decide("disallowed", self.settings(), ran=True)[0], WITHHOLD)

    def test_unusable_answers_follow_the_configured_failure_action(self):
        self.assertEqual(decide("uncertain", self.settings(), ran=True)[0], WITHHOLD)
        self.assertEqual(decide("uncertain", self.settings(), ran=False)[0], WITHHOLD)
        self.assertEqual(decide("uncertain", self.settings(group_nsfw_image_review_failure_action="block"),
                                ran=True)[0], BLOCK)
        self.assertEqual(decide("safe", self.settings(), ran=False)[0], WITHHOLD)

    def test_every_label_is_decided_with_a_reason(self):
        for label in LABELS:
            decision, reason = decide(label, self.settings(), ran=True)
            self.assertIn(decision, (ALLOW, WITHHOLD, BLOCK))
            self.assertTrue(reason)


class ReviewFallbackPolicyTests(unittest.TestCase):
    @staticmethod
    def fallback(action):
        owner = ImageReviewFallback.__new__(ImageReviewFallback)
        owner.messages = SimpleNamespace(image_review=SimpleNamespace(
            settings_for=lambda _context: {"group_nsfw_image_review_failure_action": action}))
        owner._group_context = lambda _row: object()
        return owner

    def test_inconclusive_private_fallback_obeys_the_failure_action(self):
        inconclusive = {"state": "reviewed", "label": "uncertain"}
        unavailable = {"state": "unavailable", "label": "uncertain"}
        unsafe = {"state": "reviewed", "label": "adult_nsfw"}
        safe = {"state": "reviewed", "label": "safe"}

        private = self.fallback("private")
        text_only = self.fallback("block")
        self.assertTrue(private._private_fallback_allowed({}, inconclusive))
        self.assertTrue(private._private_fallback_allowed({}, unavailable))
        self.assertFalse(text_only._private_fallback_allowed({}, inconclusive))
        self.assertTrue(text_only._private_fallback_allowed({}, unsafe),
                        "a definite unsafe verdict still uses the independent private route")
        self.assertFalse(private._private_fallback_allowed({}, safe))


class ReviewVerdictTests(unittest.TestCase):
    @staticmethod
    def target():
        return {"part_id": "part-1", "batch_id": "batch-1", "image_position": "after_text",
                "images": [{"source": {"source_id": "s"}, "parent": {"source_id": "p"},
                            "descriptor": {"position": 0, "mime_type": "image/png", "width": 2, "height": 2,
                                           "byte_count": 8, "content_hash": "0" * 64}}]}

    def document(self, **changes):
        document = {**verdict_document("reviewed", "single", "balanced", label="safe", confidence=0.9,
                                       provider_ids=["primary"], revision=1),
                    "target": self.target()}
        document.update(changes)
        return document

    def test_a_well_formed_verdict_is_accepted(self):
        self.assertEqual(check_verdict(self.document())["label"], "safe")
        self.assertEqual(check_verdict(self.document(updated_at=stamp(NOW)))["label"], "safe")

    def test_a_damaged_verdict_is_refused(self):
        for damaged in (self.document(version="other"), self.document(state="pending"),
                        self.document(label="approved"), self.document(confidence=1.5),
                        self.document(provider_ids=[]), self.document(mode="triple"),
                        self.document(sensitivity="loose"), self.document(revision="1"),
                        self.document(target={}), self.document(target={**self.target(), "image_position": "middle"}),
                        self.document(target={**self.target(), "images": []})):
            with self.assertRaises(TaskError):
                check_verdict(damaged)

    def test_an_unavailable_verdict_needs_its_reason(self):
        unavailable = {**verdict_document("unavailable", "single", "balanced", reason="没有可用视觉模型"),
                       "target": self.target()}
        self.assertEqual(check_verdict(unavailable)["state"], "unavailable")
        for damaged in ({**unavailable, "reason": None}, {**unavailable, "confidence": 0.5},
                        {**unavailable, "label": "safe"}, {**unavailable, "reason": "x" * 513}):
            with self.assertRaises(TaskError):
                check_verdict(damaged)


class ReviewLedgerTests(MessageFixture, unittest.TestCase):
    """The ledger over real storage: pending, decided, and one substitution per part."""

    def setUp(self):
        super().setUp()
        self.review = self.messages.image_review

    @staticmethod
    def target(part_id="part-1", batch_id="batch-1"):
        return {"part_id": part_id, "batch_id": batch_id, "image_position": "after_text",
                "images": [{"source": {"source_id": "generated-images-1"},
                            "parent": {"source_id": "generated-images-1"},
                            "descriptor": {"position": 0, "mime_type": "image/png", "width": 2, "height": 2,
                                           "byte_count": 8, "content_hash": "0" * 64}}]}

    def pending(self, review_key, *, part_id="part-1", batch_id="batch-1"):
        document = {**verdict_document("pending", "single", "balanced"), "origin_key": "origin-key",
                    "role_key": "role-key", "group_key": "group-key", "created_at": stamp(NOW),
                    "target": self.target(part_id, batch_id)}
        self.review.store(review_key, document)
        return document

    def decided(self, review_key, label, *, part_id="part-1", batch_id="batch-1"):
        document = {**verdict_document("reviewed", "single", "balanced", label=label, confidence=0.93,
                                       provider_ids=["primary"], revision=1),
                    "origin_key": "origin-key", "role_key": "role-key", "group_key": "group-key",
                    "created_at": stamp(NOW), "target": self.target(part_id, batch_id)}
        check_verdict(document)
        self.review.store(review_key, document)
        return document

    def test_a_pending_review_is_discoverable_by_part_and_by_scan(self):
        self.pending("review-a")
        row = self.review.row_for_part("part-1")
        self.assertEqual((row["review_key"], row["state"], row["batch_id"]), ("review-a", "pending", "batch-1"))
        self.assertTrue(self.review.pending_for({"part_id": "part-1"}))
        self.assertFalse(self.review.pending_for({"part_id": "part-2"}))
        self.assertEqual([item["review_key"] for item in self.review.pending_requests()], ["review-a"])
        self.assertIsNone(self.review.verdict("review-a"))
        self.assertIsNone(self.review.row("missing"))

    def test_a_decided_review_is_no_longer_pending_and_reports_its_label(self):
        self.pending("review-b")
        self.decided("review-b", "adult_nsfw")
        self.assertFalse(self.review.pending_for({"part_id": "part-1"}))
        self.assertEqual(self.review.pending_requests(), ())
        verdict = self.review.verdict("review-b")
        self.assertEqual((verdict["state"], verdict["label"]), ("reviewed", "adult_nsfw"))
        self.assertEqual(self.review.row_for_part("part-1")["state"], "reviewed")

    def test_one_part_keeps_one_substitution(self):
        plan = ReviewPlan("review-c", "part-c", "batch-c", WITHHOLD, "adult_nsfw", "adult_nsfw",
                          "after_text", {"descriptor": {}})
        manifests = ({"descriptor": {"position": 0}},)
        first = self.review.record(plan, text="图给你", manifests=manifests, at=NOW)
        self.assertEqual(first, self.review.record(plan, text="图给你", manifests=manifests, at=NOW))
        self.assertEqual(self.review.substituted_hash("part-c", image_position="after_text"), first)
        self.assertEqual(self.review.substitution("part-c")["mode"], WITHHOLD)
        self.assertIsNone(self.review.substituted_hash("part-c", image_position="middle"))
        with self.assertRaises(TaskError):
            self.review.record(replace(plan, decision=BLOCK), text="图给你", manifests=manifests, at=NOW)
        with self.assertRaises(TaskError):
            self.review.substitution_hash("part-c", text="改了", manifests=manifests, image_position="after_text")

    def test_an_unreviewed_part_has_no_ledger_entry(self):
        self.assertIsNone(self.review.substitution("part-d"))
        self.assertIsNone(self.review.substituted_hash("part-d", image_position="after_text"))
        self.assertIsNone(self.review.substitution_hash("part-d", text="", manifests=(),
                                                        image_position="after_text"))
        self.assertEqual(self.review.plans(self.context, ({"part_id": "part-d"},), ()), {})


if __name__ == "__main__":
    unittest.main()
