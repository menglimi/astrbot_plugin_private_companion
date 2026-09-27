# -*- coding: utf-8 -*-
"""UserMemoryExpressionVoicePart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_voice.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 449 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionVoiceMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from datetime import datetime
from typing import Any



class UserMemoryExpressionVoicePart01Mixin:
    """UserMemoryExpressionVoicePart01Mixin（从 UserMemoryExpressionVoiceMixin 拆出）。"""


    def _expression_scope_mode(self, key: str, allowed: set[str], default: str) -> str:
        value = str(runtime_persona_setting(self, key, default) or default).strip().lower()
        return value if value in allowed else default

    def _expression_scope_ids(self, key: str, *, group: bool = False) -> set[str]:
        raw = runtime_persona_setting(self, key, [])
        parser = getattr(self, "_parse_group_id_list" if group else "_parse_text_list_config", None)
        try:
            values = parser(raw) if callable(parser) else (raw if isinstance(raw, list) else [])
        except Exception:
            values = raw if isinstance(raw, list) else []
        normalized: set[str] = set()
        for item in values:
            value = _single_line(item, 80)
            if not value:
                continue
            if not group:
                value = self._expression_private_scope_id(value)
            if value:
                normalized.add(value)
        return normalized

    def _expression_private_scope_id(self, user_id: Any) -> str:
        """Normalize configured private IDs so aliases follow the same identity boundary."""
        value = _single_line(user_id, 80)
        normalizer = getattr(self, "_canonical_private_user_id", None)
        if value and callable(normalizer):
            try:
                value = _single_line(normalizer(value), 80) or value
            except Exception:
                pass
        return value

    def _expression_private_learning_source_enabled(self, user: dict[str, Any], user_id: Any = "") -> bool:
        mode = self._expression_scope_mode(
            "expression_private_learning_source_mode",
            {"owner", "selected", "all"},
            "owner",
        )
        user_id = self._expression_private_scope_id(user_id or user.get("user_id"))
        if mode == "all":
            return True
        if mode == "selected":
            return bool(user_id and user_id in self._expression_scope_ids("expression_private_learning_source_ids"))
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
        except Exception:
            role = str(user.get("relationship_role") or "")
        return str(role or "").strip().lower() == "owner"

    def _expression_group_learning_source_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_learning_source_mode",
            {"disabled", "selected", "all"},
            "disabled",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_learning_source_ids", group=True))
        return False

    def _expression_private_application_enabled(self, user_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_private_application_mode",
            {"all", "selected"},
            "all",
        )
        user_id = self._expression_private_scope_id(user_id)
        return mode == "all" or bool(user_id and user_id in self._expression_scope_ids("expression_private_application_user_ids"))

    def _expression_group_application_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_application_mode",
            {"disabled", "all", "selected"},
            "all",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_application_ids", group=True))
        return False

    def _expression_scope_signature(self) -> str:
        parts = [
            self._expression_scope_mode("expression_private_learning_source_mode", {"owner", "selected", "all"}, "owner"),
            ",".join(sorted(self._expression_scope_ids("expression_private_learning_source_ids"))),
            self._expression_scope_mode("expression_group_learning_source_mode", {"disabled", "selected", "all"}, "disabled"),
            ",".join(sorted(self._expression_scope_ids("expression_group_learning_source_ids", group=True))),
            self._expression_scope_mode("expression_private_application_mode", {"all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_private_application_user_ids"))),
            self._expression_scope_mode("expression_group_application_mode", {"disabled", "all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_group_application_ids", group=True))),
        ]
        return "|".join(parts)

    @staticmethod
    def _expression_voice_actions(
        sample_count: int,
        short_ratio: float,
        feature_counts: dict[str, Any],
        *,
        limit: int = 3,
    ) -> list[str]:
        if sample_count < 2:
            return []
        actions: list[str] = []
        if short_ratio >= 0.55:
            actions.append("优先用一两句完整短句，保持即时聊天感")
        elif short_ratio <= 0.2:
            actions.append("可以说完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= 2:
            actions.append("开头可以自然地随口起一句，避开客服式开场")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= 2:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= 2:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= 2:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= 2:
            actions.append("收尾可以放轻一点，不必强行加语气词")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= 2:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= 2:
            actions.append("允许留一点停顿感，最多一个省略号")
        return actions[: max(1, limit)]

    def _refresh_expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        now = _now_ts()
        cutoff = now - 30 * 86400
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        total_samples = 0
        total_short = 0
        private_sources = 0
        group_sources = 0
        feature_counts: dict[str, int] = {}
        scene_profiles: dict[str, dict[str, Any]] = {}
        semantic_rules: dict[str, dict[str, Any]] = {}

        def collect(profile: Any, *, source_kind: str, source_id: str) -> None:
            nonlocal total_samples, total_short, private_sources, group_sources
            if not isinstance(profile, dict):
                return
            self._backfill_expression_rule_families(profile)
            if source_kind == "group":
                samples = [
                    item
                    for item in self._group_expression_pattern_samples(profile, now=now)
                    if _safe_int(item.get("evidence_count"), 1, 1) >= 2
                ]
            else:
                raw_samples = profile.get("samples")
                samples = [
                    item
                    for item in (raw_samples if isinstance(raw_samples, list) else [])
                    if isinstance(item, dict) and _safe_float(item.get("ts"), now) >= cutoff
                ]
            learned_rules = [
                item
                for item in (profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else [])
                if (
                    isinstance(item, dict)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                    and self._expression_rule_definition_is_valid(item)
                )
            ]
            if not samples and not learned_rules:
                return
            if source_kind == "private":
                private_sources += 1
            else:
                group_sources += 1
            for item in samples:
                evidence = _safe_int(item.get("evidence_count"), 1, 1)
                weight = min(evidence, 6) if source_kind == "group" else 1
                total_samples += weight
                length = _safe_int(item.get("length"), 0, 0)
                if 0 < length <= 18:
                    total_short += weight
                scene = _single_line(item.get("scene"), 32)
                if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                    scene = self._expression_scene_from_text(item.get("text") or item.get("phrase"))
                bucket = scene_profiles.setdefault(scene, {"count": 0, "short_count": 0, "feature_counts": {}})
                bucket["count"] += weight
                if 0 < length <= 18:
                    bucket["short_count"] += weight
                raw_features = item.get("features")
                features = raw_features if isinstance(raw_features, list) else self._expression_style_features_from_text(item.get("text") or item.get("phrase"))
                for feature in features:
                    key = _single_line(feature, 32)
                    if not key:
                        continue
                    feature_counts[key] = _safe_int(feature_counts.get(key), 0, 0) + weight
                    bucket_features = bucket["feature_counts"]
                    bucket_features[key] = _safe_int(bucket_features.get(key), 0, 0) + weight
            for item in learned_rules:
                # 只汇总已经审核通过的规则。pattern 是脱敏后的可复用表达模板，
                # 与 evidence_examples 不同，可以进入召回；支持片段永远只留在审核页。
                kind = _single_line(item.get("kind"), 16).lower()
                situation = _single_line(item.get("situation"), 80)
                pattern = _single_line(item.get("pattern") or item.get("style"), 100)
                instruction = _single_line(item.get("instruction"), 140)
                if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
                    continue
                if not self._safe_expression_phrase(pattern, 100):
                    continue
                family_id = _single_line(item.get("family_id"), 64)
                signature_text = "|".join(
                    (
                        family_id,
                        kind,
                        re.sub(r"[\s，。！？!?、；;：:]", "", situation).lower(),
                        re.sub(r"[\s，。！？!?、；;：:]", "", pattern).lower(),
                    )
                )
                signature = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
                evidence = min(6, _safe_int(item.get("evidence_count"), 0, 0))
                bucket = semantic_rules.setdefault(
                    signature,
                    {
                        "id": signature,
                        "family_id": family_id,
                        "kind": kind,
                        "situation": situation,
                        "pattern": pattern,
                        "instruction": instruction,
                        "keywords": [],
                        "evidence_count": 0,
                        "source_kinds": [],
                        "source_refs": [],
                        "channels": [],
                        "relationship_stages": [],
                        "emotion_gates": [],
                        "intent": "",
                        "avoid": "",
                        "persona_conflict": False,
                        "positive_feedback": 0,
                        "negative_feedback": 0,
                        "use_count": 0,
                        "last_seen_ts": 0.0,
                    },
                )
                bucket["evidence_count"] = min(99, _safe_int(bucket.get("evidence_count"), 0, 0) + evidence)
                bucket["last_seen_ts"] = max(
                    _safe_float(bucket.get("last_seen_ts"), 0.0),
                    _safe_float(item.get("last_seen_ts"), now),
                )
                if source_kind not in bucket["source_kinds"]:
                    bucket["source_kinds"].append(source_kind)
                source_ref = {
                    "source_kind": source_kind,
                    "source_id": _single_line(source_id, 80),
                    "rule_id": _single_line(item.get("id"), 40),
                }
                if source_ref["source_id"] and source_ref["rule_id"] and source_ref not in bucket["source_refs"]:
                    bucket["source_refs"].append(source_ref)
                for field in ("channels", "relationship_stages", "emotion_gates"):
                    values = item.get(field) if isinstance(item.get(field), list) else []
                    for value in values:
                        normalized = _single_line(value, 24).lower()
                        if normalized and normalized not in bucket[field]:
                            bucket[field].append(normalized)
                incoming_intent = _single_line(item.get("intent"), 32).lower()
                if incoming_intent:
                    if bucket["intent"] and bucket["intent"] != incoming_intent:
                        bucket["intent"] = "any"
                    else:
                        bucket["intent"] = incoming_intent
                incoming_avoid = _single_line(item.get("avoid"), 160)
                if incoming_avoid and len(incoming_avoid) > len(bucket["avoid"]):
                    bucket["avoid"] = incoming_avoid
                bucket["persona_conflict"] = bool(bucket["persona_conflict"] or item.get("persona_conflict"))
                bucket["positive_feedback"] = min(
                    999,
                    _safe_int(bucket.get("positive_feedback"), 0, 0)
                    + _safe_int(item.get("positive_feedback"), 0, 0),
                )
                bucket["negative_feedback"] = min(
                    999,
                    _safe_int(bucket.get("negative_feedback"), 0, 0)
                    + _safe_int(item.get("negative_feedback"), 0, 0),
                )
                bucket["use_count"] = min(
                    99999,
                    _safe_int(bucket.get("use_count"), 0, 0)
                    + _safe_int(item.get("use_count"), 0, 0),
                )
                for keyword in item.get("keywords", []) if isinstance(item.get("keywords"), list) else []:
                    value = _single_line(keyword, 24)
                    if value and value not in bucket["keywords"]:
                        bucket["keywords"].append(value)
                bucket["keywords"] = bucket["keywords"][:8]

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user_id, user in users.items():
            if isinstance(user, dict) and self._expression_private_learning_source_enabled(user, user_id):
                collect(user.get("expression_profile"), source_kind="private", source_id=str(user_id))
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if isinstance(group, dict) and self._expression_group_learning_source_enabled(group_id):
                collect(group.get("expression_profile"), source_kind="group", source_id=str(group_id))

        runtime_rules = list(semantic_rules.values())
        self._deduplicate_expression_rule_families(runtime_rules)
        profile = {
            "sample_count": total_samples,
            "private_source_count": private_sources,
            "group_source_count": group_sources,
            "short_ratio": round(total_short / max(1, total_samples), 2),
            "feature_counts": feature_counts,
            "scene_profiles": {
                scene: {
                    "count": _safe_int(bucket.get("count"), 0, 0),
                    "short_ratio": round(_safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)), 2),
                    "feature_counts": dict(bucket.get("feature_counts") or {}),
                }
                for scene, bucket in scene_profiles.items()
                if _safe_int(bucket.get("count"), 0, 0) > 0
            },
            "learned_rules": sorted(
                runtime_rules,
                key=lambda item: (
                    -_safe_int(item.get("evidence_count"), 0, 0),
                    -_safe_float(item.get("last_seen_ts"), 0.0),
                ),
            )[: runtime_persona_setting(self, "max_learned_expression_items", 60)],
            "scope_signature": self._expression_scope_signature(),
            "refresh_day": refresh_day,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        profile["actions"] = self._expression_voice_actions(
            total_samples,
            _safe_float(profile.get("short_ratio"), 0.0),
            feature_counts,
            limit=4,
        )
        data["expression_voice_profile"] = profile
        return profile

    def _expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        profile = data.get("expression_voice_profile")
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        if (
            not isinstance(profile, dict)
            or profile.get("scope_signature") != self._expression_scope_signature()
            or profile.get("refresh_day") != refresh_day
        ):
            profile = self._refresh_expression_voice_profile()
        return profile if isinstance(profile, dict) else {}

    def _expression_companion_context(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        channel = _single_line(scope, 24).lower() or "private"
        owner = context_owner if isinstance(context_owner, dict) else None
        if owner is None and channel in {"private", "proactive", "tts"}:
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            candidate = users.get(str(target_id)) if isinstance(users, dict) else None
            owner = candidate if isinstance(candidate, dict) else None

        relationship_stage = "any"
        if isinstance(owner, dict) and channel in {"private", "proactive", "tts"}:
            level = _single_line(self._relationship_profile(owner).get("level"), 24).lower()
            relationship_stage = {
                "陌生": "stranger",
                "stranger": "stranger",
                "熟悉": "familiar",
                "familiar": "familiar",
                "亲近": "close",
                "close": "close",
            }.get(level, "any")

        intent_profile: dict[str, Any] = {}
        if inbound_text:
            try:
                intent_profile = self._analyze_inbound_intent(inbound_text)
            except Exception:
                intent_profile = {}
        elif isinstance(owner, dict) and isinstance(owner.get("intent_profile"), dict):
            intent_profile = owner.get("intent_profile") or {}
        intent = _single_line(intent_profile.get("intent"), 32).lower()
        if channel == "proactive" and not inbound_text:
            intent = "proactive"
        elif channel == "qzone":
            intent = "emotion" if re.search(r"(低落|委屈|难受|emo|情绪)", inbound_text, re.IGNORECASE) else "casual"
        elif intent in {"", "chat", "empty"}:
            intent = self._expression_scene_from_text(inbound_text) if inbound_text else "casual"

        emotion_gate = "normal"
        expression_band = ""
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if isinstance(owner, dict) and callable(expression_builder):
            try:
                decision = expression_builder(owner, passive_reengagement=True)
                projection = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
                expression_band = _single_line(projection.get("expression_band"), 24).lower()
            except Exception:
                expression_band = ""
        intent_emotion = _single_line(intent_profile.get("emotion"), 24).lower()
        if expression_band in {"avoidant", "hurt"} or intent == "boundary" or intent_emotion == "resistant":
            emotion_gate = "guarded"
        elif intent in {"comfort", "emotion"} or intent_emotion == "low":
            emotion_gate = "low"
        elif expression_band in {"lively", "warm", "close", "affectionate"} or intent in {"play", "intimacy"} or intent_emotion in {"light", "close", "positive"}:
            emotion_gate = "positive"

        return {
            "channel": channel,
            "relationship_stage": relationship_stage,
            "emotion_gate": emotion_gate,
            "intent": intent or "casual",
        }

    @staticmethod
    def _format_expression_rule_bundle_line(rule: Any) -> str:
        if not isinstance(rule, dict):
            return ""
        style_rule = rule.get("style_rule") if isinstance(rule.get("style_rule"), dict) else None
        grammar_rule = rule.get("grammar_rule") if isinstance(rule.get("grammar_rule"), dict) else None
        if style_rule is None and _single_line(rule.get("kind"), 16).lower() == "style":
            style_rule = rule
        if grammar_rule is None and _single_line(rule.get("kind"), 16).lower() == "grammar":
            grammar_rule = rule
        situation = _single_line(
            (style_rule or {}).get("situation") or (grammar_rule or {}).get("situation") or rule.get("situation"),
            80,
        )
        if not situation:
            return ""
        parts: list[str] = []
        if style_rule:
            pattern = _single_line(style_rule.get("pattern") or style_rule.get("style"), 100)
            instruction = _single_line(style_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"可复用表达“{pattern}”（{instruction}）")
        if grammar_rule:
            pattern = _single_line(grammar_rule.get("pattern") or grammar_rule.get("style"), 100)
            instruction = _single_line(grammar_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"句法习惯“{pattern}”（{instruction}）")
        if not parts:
            return ""
        avoid = _single_line(rule.get("avoid"), 200)
        if avoid:
            parts.append(f"边界：{avoid}")
        kind_label = "组合规则" if style_rule and grammar_rule else ("情境表达" if style_rule else "语法习惯")
        return f"- {kind_label}｜当“{situation}”时：" + "；".join(parts)
