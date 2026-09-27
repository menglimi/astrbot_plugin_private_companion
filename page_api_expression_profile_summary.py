# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionProfileSummaryMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 370 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiExpressionMixin）。
"""
from __future__ import annotations
from .page_api_expression_shared import Any
from .page_api_expression_shared import _today_key



class PrivateCompanionPageApiExpressionProfileSummaryMixin:
    """PrivateCompanionPageApiExpressionProfileSummaryMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    def _expression_learning_scope_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        scope_ids = getattr(self.plugin, "_expression_scope_ids", None)

        def ids(key: str, *, group: bool = False) -> list[str]:
            if callable(scope_ids):
                try:
                    return sorted(scope_ids(key, group=group))
                except Exception:
                    pass
            raw = getattr(self.plugin, key, [])
            return sorted(self._normalize_id_list(raw))

        voice = data.get("expression_voice_profile") if isinstance(data.get("expression_voice_profile"), dict) else {}
        actions = voice.get("actions") if isinstance(voice.get("actions"), list) else []
        runtime = data.get("expression_learning_runtime") if isinstance(data.get("expression_learning_runtime"), dict) else {}
        by_day = runtime.get("group_batches_by_day") if isinstance(runtime.get("group_batches_by_day"), dict) else {}
        group_daily_limit = self._int(getattr(self.plugin, "expression_group_learning_daily_batch_limit", 6)) or 6
        group_used_today = self._int(by_day.get(_today_key()))
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "private_learning": {
                "mode": self._single_line(getattr(self.plugin, "expression_private_learning_source_mode", "owner"), 20),
                "ids": ids("expression_private_learning_source_ids"),
            },
            "group_learning": {
                "mode": self._single_line(getattr(self.plugin, "expression_group_learning_source_mode", "disabled"), 20),
                "ids": ids("expression_group_learning_source_ids", group=True),
            },
            "private_application": {
                "mode": self._single_line(getattr(self.plugin, "expression_private_application_mode", "all"), 20),
                "ids": ids("expression_private_application_user_ids"),
            },
            "group_application": {
                "mode": self._single_line(getattr(self.plugin, "expression_group_application_mode", "all"), 20),
                "ids": ids("expression_group_application_ids", group=True),
            },
            "group_budget": {
                "daily_limit": group_daily_limit,
                "used_today": group_used_today,
                "remaining_today": max(0, group_daily_limit - group_used_today),
                "min_new_messages": self._int(
                    getattr(self.plugin, "expression_group_learning_min_new_messages", 20)
                ) or 20,
                "last_batch_at": self._single_line(runtime.get("last_group_batch_at"), 30),
                "last_defer_reason": self._single_line(runtime.get("last_group_defer_reason"), 40),
            },
            "voice": {
                "sample_count": self._int(voice.get("sample_count")),
                "private_source_count": self._int(voice.get("private_source_count")),
                "group_source_count": self._int(voice.get("group_source_count")),
                "actions": [self._single_line(item, 120) for item in actions[:4] if self._single_line(item, 120)],
                "updated_at": self._single_line(voice.get("updated_at"), 30),
            },
        }

    def _expression_rule_group_rows(self, rules: Any) -> list[dict[str, Any]]:
        rows = [dict(item) for item in rules if isinstance(item, dict)] if isinstance(rules, list) else []
        if not rows:
            return []
        grouper = getattr(self.plugin, "_expression_rule_groups", None)
        bundler = getattr(self.plugin, "_expression_rule_runtime_bundle", None)
        raw_groups = grouper(rows) if callable(grouper) else [[item] for item in rows]
        result: list[dict[str, Any]] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, list) or not raw_group:
                continue
            bundle = bundler(raw_group) if callable(bundler) else dict(raw_group[0])
            if not isinstance(bundle, dict) or not bundle:
                continue
            items = [dict(item) for item in raw_group if isinstance(item, dict)]
            style_rule = next((item for item in items if item.get("kind") == "style"), None)
            grammar_rule = next((item for item in items if item.get("kind") == "grammar"), None)
            review_statuses = {self._single_line(item.get("review_status"), 24).lower() for item in items}
            review_status = "needs_review" if "needs_review" in review_statuses else (
                "pending" if "pending" in review_statuses else "approved"
            )
            group_row = {
                **bundle,
                "id": self._single_line(bundle.get("family_id") or bundle.get("id"), 100),
                "family_id": self._single_line(bundle.get("family_id"), 100),
                "label": self._single_line(
                    (style_rule or {}).get("label") or (grammar_rule or {}).get("label") or bundle.get("label"),
                    100,
                ),
                "situation": self._single_line(
                    (style_rule or {}).get("situation") or (grammar_rule or {}).get("situation") or bundle.get("situation"),
                    100,
                ),
                "kind": "combined" if style_rule and grammar_rule else self._single_line(bundle.get("kind"), 24),
                "kind_label": "组合规则" if style_rule and grammar_rule else (
                    "情境表达" if style_rule else "语法习惯"
                ),
                "component_count": len(items),
                "component_kinds": [kind for kind in ("style", "grammar") if any(item.get("kind") == kind for item in items)],
                "items": items,
                "component_rules": items,
                "style_rule": dict(style_rule) if style_rule else None,
                "grammar_rule": dict(grammar_rule) if grammar_rule else None,
                "review_status": review_status,
                "review_reason": self._single_line(
                    next((item.get("review_reason") for item in items if item.get("review_reason")), ""),
                    180,
                ),
                "evidence_count": max(self._int(item.get("evidence_count")) for item in items),
                "item_revisions": {
                    self._single_line(item.get("id"), 100): self._int(item.get("item_revision"))
                    for item in items if self._single_line(item.get("id"), 100)
                },
            }
            result.append(group_row)
        result.sort(key=lambda item: (-self._int(item.get("evidence_count")), self._single_line(item.get("situation"), 100)))
        return result

    def _expression_profile_summary(self, user: dict[str, Any], *, source_type: str = "private") -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        scene_label = getattr(self.plugin, "_expression_scene_label", None)
        feature_labels = {
            "short": "短句",
            "casual_opener": "随口开头",
            "playful": "轻松感",
            "laugh_marker": "笑声口语",
            "reduplication": "自然叠词",
            "soft_wave": "波浪收束",
            "soft_ending": "柔和收尾",
            "pause": "留白停顿",
            "question": "问句推进",
        }

        def sample_row(item: Any, index: int) -> dict[str, Any]:
            raw = item if isinstance(item, dict) else {}
            text = self._single_line(raw.get("text") or raw.get("phrase") or raw.get("ending"), 120)
            punctuation = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            marks = "".join(f"{key}{value}" for key, value in punctuation.items() if self._int(value) > 0)
            scene = self._single_line(
                scene_label(raw.get("scene")) if callable(scene_label) else raw.get("scene"),
                32,
            )
            feature_values = [
                self._single_line(feature_labels.get(str(feature), str(feature)), 24)
                for feature in raw.get("features", [])
                if self._single_line(feature, 24)
            ] if isinstance(raw.get("features"), list) else []
            distinctive_features = [item for item in feature_values if item not in {"短句", "问句推进"}]
            pattern_label = scene
            if source_type == "group":
                if distinctive_features:
                    pattern_label = f"{scene or '日常交流'}中的{'与'.join(distinctive_features[:2])}"
                elif scene:
                    pattern_label = f"{scene}表达模式"
                pattern_details = []
                length_bucket = self._single_line(raw.get("length_bucket"), 20)
                if length_bucket:
                    pattern_details.append(f"{length_bucket} 字")
                mark_types = "".join(
                    str(mark)
                    for mark, count in punctuation.items()
                    if self._int(count) > 0
                )
                if mark_types:
                    pattern_details.append(f"含 {mark_types}")
                if pattern_details:
                    pattern_label = f"{pattern_label or '日常交流'} · {' · '.join(pattern_details)}"
            observation_status = "supported" if self._int(raw.get("evidence_count")) >= 2 else "single"
            scope_binding = raw.get("scope_binding") if isinstance(raw.get("scope_binding"), dict) else {}
            return {
                "id": self._single_line(raw.get("id"), 40) or str(index),
                "index": index,
                "text": text,
                "phrase": self._single_line(raw.get("phrase"), 80),
                "ending": self._single_line(raw.get("ending"), 20),
                "scene": scene,
                "features": feature_values,
                "length": self._int(raw.get("length")),
                "length_bucket": self._single_line(raw.get("length_bucket"), 20),
                "punctuation": marks,
                "evidence_count": max(1, self._int(raw.get("evidence_count"))),
                "pattern_status": observation_status,
                "observation_status": observation_status,
                "pattern_label": pattern_label,
                "created_at": self._single_line(raw.get("created_at"), 30),
                "ts": self._float(raw.get("ts")),
                "time": self.plugin._format_timestamp_elapsed(raw.get("ts", 0)),
                "item_revision": self._int(scope_binding.get("revision")),
            }

        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        formatter = getattr(self.plugin, "_format_expression_profile_for_prompt", None)
        try:
            prompt_preview = formatter(user) if callable(formatter) else ""
        except Exception:
            prompt_preview = ""
        rules: list[dict[str, Any]] = []
        def semantic_rule_row(raw_rule: dict[str, Any], *, pending_review: bool) -> dict[str, Any] | None:
            if not isinstance(raw_rule, dict):
                return None
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            if callable(validator) and not validator(raw_rule):
                return None
            evidence_count = self._int(raw_rule.get("evidence_count"))
            if evidence_count < 1:
                return None
            situation = self._single_line(raw_rule.get("situation"), 100)
            instruction = self._single_line(raw_rule.get("instruction"), 300)
            pattern = self._single_line(raw_rule.get("pattern"), 180)
            kind = self._single_line(raw_rule.get("kind"), 24).lower()
            if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
                return None
            review_status = self._single_line(raw_rule.get("review_status"), 24).lower()
            if not review_status:
                review_status = "pending" if pending_review else "approved"
            scope_binding = raw_rule.get("scope_binding") if isinstance(raw_rule.get("scope_binding"), dict) else {}
            return {
                "id": self._single_line(raw_rule.get("id"), 100),
                "family_id": self._single_line(raw_rule.get("family_id"), 100),
                "family_key": self._single_line(raw_rule.get("family_key"), 80),
                "scene": self._single_line(raw_rule.get("kind"), 24),
                "label": self._single_line(raw_rule.get("label"), 100) or situation or "语义表达规则",
                "situation": situation,
                "pattern": pattern,
                "instruction": instruction,
                "evidence_count": evidence_count,
                "confidence": min(0.98, round(0.52 + min(8, evidence_count) * 0.055, 2)),
                "signals": [
                    self._single_line(item, 24)
                    for item in (raw_rule.get("keywords") or raw_rule.get("tags") or [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("keywords") or raw_rule.get("tags"), list) else [],
                "rule_type": "semantic",
                "kind": kind,
                "kind_label": "情境表达" if kind == "style" else "语法习惯",
                "evidence_examples": [
                    self._single_line(item, 80)
                    for item in raw_rule.get("evidence_examples", [])
                    if self._single_line(item, 80)
                ][:3] if isinstance(raw_rule.get("evidence_examples"), list) else [],
                "pattern_status": review_status if pending_review else "active",
                "review_status": review_status,
                "review_reason": self._single_line(raw_rule.get("review_reason"), 180),
                "channels": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("channels", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("channels"), list) else [],
                "relationship_stages": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("relationship_stages", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("relationship_stages"), list) else [],
                "emotion_gates": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("emotion_gates", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("emotion_gates"), list) else [],
                "intent": self._single_line(raw_rule.get("intent"), 32).lower() or "any",
                "avoid": self._single_line(raw_rule.get("avoid"), 220),
                "persona_conflict": raw_rule.get("persona_conflict") is True
                or self._single_line(raw_rule.get("persona_conflict"), 12).lower() in {"1", "true", "yes", "on", "是", "冲突"},
                "positive_feedback": self._int(raw_rule.get("positive_feedback")),
                "negative_feedback": self._int(raw_rule.get("negative_feedback")),
                "use_count": self._int(raw_rule.get("use_count")),
                "last_used_time": self.plugin._format_timestamp_elapsed(raw_rule.get("last_used_ts", 0))
                if self._float(raw_rule.get("last_used_ts")) > 0 else "",
                "item_revision": self._int(scope_binding.get("revision")),
            }

        learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
        for raw_rule in learned_rules:
            row = semantic_rule_row(raw_rule, pending_review=False)
            if row:
                rules.append(row)
        pending_rules = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
        pending_rule_rows = [
            row
            for raw_rule in pending_rules
            if (row := semantic_rule_row(raw_rule, pending_review=True)) is not None
        ]
        rules.sort(key=lambda item: (-self._int(item.get("evidence_count")), self._single_line(item.get("scene"), 32)))
        rule_groups = self._expression_rule_group_rows(rules)
        pending_rule_groups = self._expression_rule_group_rows(pending_rule_rows)
        raw_usage = profile.get("usage") if isinstance(profile.get("usage"), dict) else {}
        raw_last_injection = raw_usage.get("last_injection") if isinstance(raw_usage.get("last_injection"), dict) else {}
        usage = {
            "injected_count": self._int(raw_usage.get("injected_count")),
            "visible_match_count": self._int(raw_usage.get("visible_match_count")),
            "semantic_injected_count": self._int(raw_usage.get("semantic_injected_count")),
            "feedback_positive": self._int(raw_usage.get("feedback_positive")),
            "feedback_negative": self._int(raw_usage.get("feedback_negative")),
            "last_injection": {
                "time": self.plugin._format_timestamp_elapsed(raw_last_injection.get("ts", 0)) if raw_last_injection else "",
                "at": self._single_line(raw_last_injection.get("at"), 30),
                "rule_id": self._single_line(raw_last_injection.get("rule_id"), 100),
                "scene": self._single_line(raw_last_injection.get("scene"), 32),
                "label": self._single_line(raw_last_injection.get("label"), 32),
                "instruction": self._single_line(raw_last_injection.get("instruction"), 300),
                "evidence_count": self._int(raw_last_injection.get("evidence_count")),
                "confidence": max(0.0, min(1.0, self._float(raw_last_injection.get("confidence")))),
                "expected_signals": [
                    self._single_line(feature_labels.get(str(signal), str(signal)), 24)
                    for signal in raw_last_injection.get("expected_signals", [])
                    if self._single_line(signal, 24)
                ] if isinstance(raw_last_injection.get("expected_signals"), list) else [],
                "visible_signals": [
                    self._single_line(feature_labels.get(str(signal), str(signal)), 24)
                    for signal in raw_last_injection.get("visible_signals", [])
                    if self._single_line(signal, 24)
                ] if isinstance(raw_last_injection.get("visible_signals"), list) else [],
                "rule_type": self._single_line(raw_last_injection.get("rule_type"), 24),
                "semantic_rule_count": self._int(raw_last_injection.get("semantic_rule_count")),
                "channel": self._single_line(raw_last_injection.get("channel"), 24),
                "relationship_stage": self._single_line(raw_last_injection.get("relationship_stage"), 24),
                "emotion_gate": self._single_line(raw_last_injection.get("emotion_gate"), 24),
                "intent": self._single_line(raw_last_injection.get("intent"), 32),
            } if raw_last_injection else {},
        }
        raw_scene_profiles = profile.get("scene_profiles") if isinstance(profile.get("scene_profiles"), dict) else {}
        scene_profiles = []
        for scene, item in raw_scene_profiles.items():
            if not isinstance(item, dict):
                continue
            count = self._int(item.get("count"))
            if count <= 0:
                continue
            label = scene_label(scene) if callable(scene_label) else self._single_line(scene, 32)
            scene_profiles.append(
                {
                    "scene": self._single_line(scene, 32),
                    "label": self._single_line(label, 32),
                    "count": count,
                    "short_ratio": float(item.get("short_ratio") or 0),
                    "feature_counts": item.get("feature_counts") if isinstance(item.get("feature_counts"), dict) else {},
                }
            )
        scene_profiles.sort(key=lambda item: (-self._int(item.get("count")), self._single_line(item.get("scene"), 32)))
        sample_limit = max(
            4,
            min(60, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60),
        )
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "mode": self._single_line(getattr(self.plugin, "expression_learning_mode", "balanced"), 20),
            "manual_review": bool(getattr(self.plugin, "enable_expression_manual_review", False)),
            "style_review": bool(getattr(self.plugin, "enable_expression_style_review", True)),
            "updated_at": self._single_line(profile.get("updated_at"), 30),
            "scope_revision": self._int(profile.get("scope_revision")),
            "sample_count": len(samples),
            "observation_count": len(samples),
            "observation_evidence_count": sum(max(1, self._int(item.get("evidence_count"))) for item in samples if isinstance(item, dict)),
            "pattern_count": len(rules),
            "rule_count": len(rules),
            "rule_evidence_count": sum(self._int(item.get("evidence_count")) for item in rule_groups),
            "style_rule_count": sum(1 for item in rules if item.get("kind") == "style"),
            "grammar_rule_count": sum(1 for item in rules if item.get("kind") == "grammar"),
            "rule_group_count": len(rule_groups),
            "pending_count": len(pending),
            "pending_rule_count": len(pending_rule_rows),
            "pending_rule_group_count": len(pending_rule_groups),
            "pending_style_count": sum(1 for item in pending_rule_rows if item.get("kind") == "style"),
            "pending_grammar_count": sum(1 for item in pending_rule_rows if item.get("kind") == "grammar"),
            "short_count": self._int(profile.get("short_count")),
            "endings": [self._single_line(item, 20) for item in (profile.get("endings") if isinstance(profile.get("endings"), list) else [])[:8]],
            "recent_phrases": [self._single_line(item, 80) for item in (profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else [])[:8]],
            "scene_profiles": scene_profiles[:6],
            "rules": rules[: max(6, min(60, sample_limit * 2))],
            "pending_rules": pending_rule_rows[: max(6, min(60, sample_limit * 2))],
            "rule_groups": rule_groups[: max(6, min(60, sample_limit * 2))],
            "pending_rule_groups": pending_rule_groups[: max(6, min(60, sample_limit * 2))],
            "usage": usage,
            "samples": [sample_row(item, idx) for idx, item in enumerate(samples[:sample_limit])],
            "pending_samples": [sample_row(item, idx) for idx, item in enumerate(pending[:24])],
            "prompt_preview": self._multi_line(prompt_preview, 500),
        }
