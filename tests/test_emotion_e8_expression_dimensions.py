from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from companion_interaction_expression import (
    EXPRESSION_CONTRACT_VERSION,
    build_expression_decision,
    expression_decision_prompt,
    expression_decision_prompt_section,
)
from conversation_prompt_section import PromptRenderMode, render_prompt_sections


ROOT = Path(__file__).resolve().parents[1]


class EmotionE8ExpressionDimensionTests(unittest.TestCase):
    def build(self, band: str, **updates: Any):
        source = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "relationship_score": 1200,
            "current_interaction": {"expression_band": band},
            "bot_state": {"energy": 70},
            "proactive_candidate": {"eligible": True, "budget": 2},
        }
        source.update(updates)
        return build_expression_decision(source)

    def test_all_bands_map_to_bounded_v2_dimensions(self) -> None:
        allowed = {
            "pacing": {"slow", "steady", "bright"},
            "directness": {"indirect", "natural", "direct"},
            "validation_style": {"none", "acknowledge", "support_first"},
            "self_disclosure": {"none", "light", "allowed"},
            "humor_mode": {"off", "light", "playful"},
            "topic_initiative": {"reply_only", "followup", "shared_topic"},
        }
        self.assertEqual("companion_interaction_expression.v2", EXPRESSION_CONTRACT_VERSION)
        for band in ("avoidant", "hurt", "relaxed", "lively", "warm", "close", "affectionate"):
            decision = self.build(band).to_dict()
            for key, values in allowed.items():
                self.assertIn(decision[key], values)

    def test_v2_preserves_official_proactive_projection_fields(self) -> None:
        decision = self.build(
            "warm",
            relationship_baseline={"proactive_care_limit": 3, "stage_key": "familiar"},
            proactive_candidate={
                "eligible": True,
                "budget": 2,
                "cooldown_until": 1_234.0,
                "current_ts": 1_200.0,
            },
        ).to_dict()
        self.assertEqual(3, decision["proactive_target"])
        self.assertEqual(1_234.0, decision["proactive_cooldown_until"])
        self.assertEqual(0, decision["proactive_budget"])

    def test_safety_and_low_energy_only_suppress_dimensions(self) -> None:
        tired = self.build("lively", bot_state={"energy": 15}).to_dict()
        self.assertEqual("slow", tired["pacing"])
        self.assertEqual("off", tired["humor_mode"])
        self.assertEqual("reply_only", tired["topic_initiative"])
        blocked = self.build("affectionate", safety_constraints={"contact_boundary": True}).to_dict()
        self.assertEqual("contact_boundary", blocked["blocker"])
        self.assertEqual("off", blocked["humor_mode"])
        self.assertEqual("none", blocked["self_disclosure"])
        self.assertEqual("reply_only", blocked["topic_initiative"])

    def test_prompt_page_and_tts_consume_the_same_projection(self) -> None:
        decision = self.build(
            "warm",
            bot_state={
                "energy": 70,
                "affect_modulation": {
                    "valence": 0.3,
                    "arousal": 0.1,
                    "vulnerability": 0.8,
                    "confidence": 1.0,
                },
            },
        ).to_dict()
        prompt = expression_decision_prompt(decision)
        section = expression_decision_prompt_section(decision)
        self.assertEqual("expression.decision", section.key)
        self.assertEqual("Companion expression", section.title)
        self.assertEqual("expression_decision", section.source)
        self.assertEqual(
            prompt,
            render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY),
        )
        labels = {
            "pacing": "节奏",
            "directness": "直接度",
            "validation_style": "回应",
            "self_disclosure": "自述",
            "humor_mode": "幽默",
            "topic_initiative": "话题",
        }
        for key, label in labels.items():
            self.assertIn(f"{label}={decision[key]}", prompt)
        self.assertLess(len(prompt), 620)

        page_source = "".join((ROOT / p).read_text(encoding="utf-8") for p in ["page_api.py", *sorted(ROOT.glob("page_api_*.py"))])
        for key, fallback in {
            "pacing": "steady",
            "directness": "natural",
            "validation_style": "none",
            "self_disclosure": "none",
            "humor_mode": "off",
            "topic_initiative": "reply_only",
        }.items():
            self.assertIn(f'"{key}": "{fallback}"', page_source)

        source = (ROOT / "tts_enhancement.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TtsEnhancementMixin")
        # 拆分后 _tts_expression_style_context 落在 TtsEnhancementVisibleChineseMixin 中，
        # 需从聚合类体中查找。
        from tests.module_source_index import class_body_defs_for_file
        method = next(
            node
            for node in class_body_defs_for_file(ROOT, "tts_enhancement.py", "TtsEnhancementMixin")
            if isinstance(node, ast.FunctionDef) and node.name == "_tts_expression_style_context"
        )
        method.decorator_list = []
        module = ast.Module(body=[method], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"Any": Any, "_single_line": lambda value, limit=0: " ".join(str(value or "").split())[:limit]}
        exec(compile(module, str(ROOT / "tts_enhancement.py"), "exec"), namespace)
        event = SimpleNamespace(_private_companion_expression_decision=decision)
        tts_context = namespace["_tts_expression_style_context"](event)
        for key, label in labels.items():
            self.assertIn(f"{label}={decision[key]}", tts_context)


if __name__ == "__main__":
    unittest.main()
