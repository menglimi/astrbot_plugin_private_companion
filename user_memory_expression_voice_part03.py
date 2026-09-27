# -*- coding: utf-8 -*-
"""UserMemoryExpressionVoicePart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_voice.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 170 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionVoiceMixin）。
"""
from __future__ import annotations

from .helpers import _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from typing import Any



class UserMemoryExpressionVoicePart03Mixin:
    """UserMemoryExpressionVoicePart03Mixin（从 UserMemoryExpressionVoiceMixin 拆出）。"""


    def _refresh_expression_profile_legacy_summary(self, profile: dict[str, Any]) -> None:
        samples = profile.get("samples")
        if not isinstance(samples, list):
            return
        profile["pattern_count"] = len(samples)
        profile["sample_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict)
        )
        profile["short_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict) and _safe_int(item.get("length"), 0, 0) <= 18
        )
        punctuation: dict[str, int] = {}
        endings: list[str] = []
        phrases: list[str] = []
        scene_stats: dict[str, dict[str, Any]] = {}
        fingerprint_features: dict[str, int] = {}
        for item in samples:
            if not isinstance(item, dict):
                continue
            evidence = _safe_int(item.get("evidence_count"), 1, 1)
            marks = item.get("punctuation")
            if isinstance(marks, dict):
                for mark, count in marks.items():
                    punctuation[str(mark)] = punctuation.get(str(mark), 0) + _safe_int(count, 0, 0)
            ending = _single_line(item.get("ending"), 12)
            if ending and ending not in endings:
                endings.append(ending)
            phrase = _single_line(item.get("phrase"), 40)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            sample_text = _single_line(item.get("text") or phrase, 180)
            scene = _single_line(item.get("scene"), 32)
            if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                scene = self._expression_scene_from_text(sample_text)
                item["scene"] = scene
            raw_features = item.get("features")
            features = [
                _single_line(feature, 32)
                for feature in raw_features
                if _single_line(feature, 32)
            ] if isinstance(raw_features, list) else self._expression_style_features_from_text(sample_text)
            item["features"] = list(dict.fromkeys(features))
            bucket = scene_stats.setdefault(
                scene,
                {"count": 0, "short_count": 0, "feature_counts": {}, "latest_ts": 0.0},
            )
            bucket["count"] += evidence
            if _safe_int(item.get("length"), len(sample_text), 0) <= 18:
                bucket["short_count"] += evidence
            bucket["latest_ts"] = max(_safe_float(bucket.get("latest_ts"), 0.0), _safe_float(item.get("ts"), 0.0))
            feature_counts = bucket["feature_counts"]
            for feature in item["features"]:
                feature_counts[feature] = _safe_int(feature_counts.get(feature), 0, 0) + evidence
                fingerprint_features[feature] = _safe_int(fingerprint_features.get(feature), 0, 0) + evidence
        profile["punctuation"] = punctuation
        profile["endings"] = endings[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        profile["recent_phrases"] = phrases[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        profile["scene_profiles"] = {
            scene: {
                "count": _safe_int(bucket.get("count"), 0, 0),
                "short_ratio": round(
                    _safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)),
                    2,
                ),
                "feature_counts": dict(bucket.get("feature_counts") or {}),
                "latest_ts": _safe_float(bucket.get("latest_ts"), 0.0),
            }
            for scene, bucket in scene_stats.items()
            if _safe_int(bucket.get("count"), 0, 0) > 0
        }
        profile["style_fingerprint"] = {
            "short_ratio": round(profile["short_count"] / max(1, profile["sample_count"]), 2),
            "feature_counts": fingerprint_features,
        }
        profile["expression_rules"] = self._expression_rules_from_scene_profiles(profile["scene_profiles"])

    def _expression_rule_details_for_scene(
        self,
        scene: Any,
        scene_profile: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_scene = _single_line(scene, 32)
        count = _safe_int(scene_profile.get("count"), 0, 0)
        if normalized_scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"} or count < 2:
            return {}
        base_rules = {
            "acknowledgement": "先用简短口语确认接住，不把一个短确认扩写成长说明",
            "question": "先直接回应核心，再自然接下去，不绕成客服式解释",
            "request": "先给明确答复或行动，再补必要说明",
            "tease": "保持轻松有来有回，不突然说教或端着",
            "emotion": "先接住情绪，短一点、慢一点，不急着讲道理",
            "casual": "从眼前话头直接接，不套客气开场",
        }
        short_ratio = _safe_float(scene_profile.get("short_ratio"), 0.0)
        raw_feature_counts = scene_profile.get("feature_counts")
        feature_counts = raw_feature_counts if isinstance(raw_feature_counts, dict) else {}
        feature_threshold = 2
        actions = [base_rules[normalized_scene]]
        signals: list[str] = []

        if short_ratio >= 0.6:
            actions.append("长度控制在一两句，保留即时聊天感")
            signals.append("short")
        elif short_ratio <= 0.2 and normalized_scene in {"emotion", "casual"}:
            actions.append("可以完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= feature_threshold:
            actions.append("开头可自然地随口起一句，避开客服式开场")
            signals.append("casual_opener")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= feature_threshold:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
            signals.append("laugh_marker")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= feature_threshold:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
            signals.append("soft_wave")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= feature_threshold:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
            signals.append("playful")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= feature_threshold:
            actions.append("收尾可以放轻一点，不必强行加语气词")
            signals.append("soft_ending")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= feature_threshold:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
            signals.append("reduplication")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= feature_threshold:
            actions.append("允许留一点停顿感，最多一个省略号")
            signals.append("pause")

        signals = list(dict.fromkeys(signals))
        rule_id = f"{normalized_scene}:{'.'.join(signals[:3]) or 'scene'}"
        return {
            "id": rule_id,
            "scene": normalized_scene,
            "label": self._expression_scene_label(normalized_scene),
            "evidence_count": count,
            "confidence": min(0.96, round(0.45 + min(count, 8) * 0.06, 2)),
            "actions": actions[:4],
            "signals": signals[:4],
            "instruction": "；".join(actions[:4]) + "。",
        }

    def _expression_rules_from_scene_profiles(self, scene_profiles: Any) -> list[dict[str, Any]]:
        if not isinstance(scene_profiles, dict):
            return []
        rules: list[dict[str, Any]] = []
        for scene, raw_profile in scene_profiles.items():
            if not isinstance(raw_profile, dict):
                continue
            details = self._expression_rule_details_for_scene(scene, raw_profile)
            if details:
                rules.append(details)
        rules.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), _single_line(item.get("scene"), 32)))
        return rules[:6]

    def _expression_rule_details_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> dict[str, Any]:
        scene = self._expression_scene_from_text(inbound_text)
        raw_profiles = profile.get("scene_profiles") if isinstance(profile, dict) else {}
        scene_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
        scene_profile = scene_profiles.get(scene) if isinstance(scene_profiles.get(scene), dict) else {}
        return self._expression_rule_details_for_scene(scene, scene_profile)

    def _expression_scene_rule_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> str:
        if self._expression_learning_mode() == "light":
            return ""
        details = self._expression_rule_details_for_inbound(profile, inbound_text)
        if not details:
            return ""
        return (
            f"当前场景「{details['label']}」已有 {details['evidence_count']} 条表达证据："
            f"{details['instruction']}"
        )
