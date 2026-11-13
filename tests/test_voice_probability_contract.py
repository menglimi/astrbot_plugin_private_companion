"""Probability contracts keep UI percentages and stored fractions consistent."""
import unittest
from companion_v2.conversation.reply_settings import configuration_fields
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy


class VoiceProbabilityContractTests(unittest.TestCase):
    def test_voice_probability_contract_declares_fraction_bounds(self):
        fields = {item.field_id.rsplit(".", 1)[-1]: item for item in
                  configuration_fields(SegmentationPolicy(), False, 0, VoiceReplyPolicy(probability=0.3))}
        for name in ("voice_probability", "voice_primary_mention_probability"):
            with self.subTest(name=name):
                self.assertEqual((fields[name].minimum, fields[name].maximum), (0, 1))
                self.assertEqual(fields[name].validate(1), 1)
                self.assertEqual(fields[name].validate(0.3), 0.3)
