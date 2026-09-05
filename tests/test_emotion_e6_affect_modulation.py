from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from affect_modulation import compose_affect_modulation  # noqa: E402
from affect_modulation_contract import normalize_affect_modulation  # noqa: E402
from companion_interaction_expression import build_expression_decision  # noqa: E402


class EmotionE6AffectModulationTests(unittest.TestCase):
    def test_contract_shape_and_malicious_numbers_fail_neutral(self) -> None:
        # Verify the portable public behavior, not a byte hash that changes on
        # harmless formatting or line-ending updates.
        baseline = normalize_affect_modulation({})
        self.assertEqual("affect_modulation.v1", baseline["schema_version"])
        self.assertEqual(
            {"schema_version", "valence", "arousal", "vulnerability", "confidence",
             "source_event_ids", "computed_at"},
            set(baseline),
        )
        result = normalize_affect_modulation({
            "valence": "0.9",
            "arousal": math.inf,
            "vulnerability": object(),
            "confidence": math.nan,
            "source_event_ids": ["emo-1", object()],
        })
        self.assertEqual(0.0, result["valence"])
        self.assertEqual(0.0, result["arousal"])
        self.assertEqual(0.0, result["vulnerability"])
        self.assertEqual(0.0, result["confidence"])
        self.assertEqual(["emo-1"], result["source_event_ids"])

    def test_condition_modulation_decays_monotonically(self) -> None:
        condition = {
            "source_event_id": "emo-1",
            "start_ts": 1000.0,
            "half_life_seconds": 100.0,
            "modulation": {"valence": 0.8, "arousal": 0.6, "vulnerability": 0.4, "confidence": 1.0},
        }
        first = compose_affect_modulation([condition], now=1000.0)
        second = compose_affect_modulation([condition], now=1100.0)
        self.assertGreater(first["valence"], second["valence"])
        self.assertGreater(first["confidence"], second["confidence"])

    def test_modulation_changes_expression_detail_not_authority(self) -> None:
        base = {
            "relationship_score": 650,
            "relationship_role": "friend",
            "current_interaction": {"expression_band": "warm"},
            "proactive_candidate": {"budget": 1},
            "content_policy": {"private_chat": True, "flirt_enabled": False},
        }
        quiet = build_expression_decision({
            **base,
            "bot_state": {"energy": 70, "affect_modulation": {"valence": 0.4, "arousal": 0.1, "vulnerability": 0.2, "confidence": 1.0}},
        })
        bright = build_expression_decision({
            **base,
            "bot_state": {"energy": 70, "affect_modulation": {"valence": 0.4, "arousal": 0.9, "vulnerability": 0.2, "confidence": 1.0}},
        })
        self.assertEqual("warm", quiet.expression_band)
        self.assertEqual(quiet.expression_band, bright.expression_band)
        self.assertEqual(quiet.content_tier, bright.content_tier)
        self.assertEqual(quiet.proactive_budget, bright.proactive_budget)
        self.assertEqual("soft", quiet.tts_style)
        self.assertEqual("bright", bright.tts_style)
