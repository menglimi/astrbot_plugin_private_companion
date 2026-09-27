# -*- coding: utf-8 -*-
"""UserMemoryExpressionRulePart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_rule.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 487 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionRuleMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _safe_float, _safe_int, _single_line
from typing import Any



class UserMemoryExpressionRulePart02Mixin:
    """UserMemoryExpressionRulePart02Mixin（从 UserMemoryExpressionRuleMixin 拆出）。"""


    def _expression_rule_contexts_compatible(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        def compatible(field: str) -> bool:
            left_values = self._expression_rule_value_set(left.get(field))
            right_values = self._expression_rule_value_set(right.get(field))
            if not left_values or not right_values or "any" in left_values or "any" in right_values:
                return True
            return bool(left_values & right_values)

        if not all(compatible(field) for field in ("channels", "relationship_stages", "emotion_gates")):
            return False
        left_intent = self._normalize_expression_intent(left.get("intent"))
        right_intent = self._normalize_expression_intent(right.get("intent"))
        if "any" in {left_intent, right_intent} or left_intent == right_intent:
            return True
        compatible_intent_groups = (
            {"play", "tease", "intimacy"},
            {"question", "request", "help"},
            {"comfort", "emotion"},
            {"acknowledgement", "casual"},
        )
        return any({left_intent, right_intent}.issubset(group) for group in compatible_intent_groups)

    def _expression_rule_duplicate_analysis(
        self,
        left: Any,
        right: Any,
    ) -> dict[str, Any]:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return {}
        left_kind = _single_line(left.get("kind"), 16).lower()
        right_kind = _single_line(right.get("kind"), 16).lower()
        if left_kind not in {"style", "grammar"} or left_kind != right_kind:
            return {}

        left_pattern = left.get("pattern") or left.get("style")
        right_pattern = right.get("pattern") or right.get("style")
        left_key = self._expression_rule_pattern_key(left_pattern)
        right_key = self._expression_rule_pattern_key(right_pattern)
        if not left_key or not right_key:
            return {}

        context_compatible = self._expression_rule_contexts_compatible(left, right)
        manually_edited = bool(left.get("manually_edited") or right.get("manually_edited"))
        left_examples = {
            key
            for value in (left.get("evidence_examples") if isinstance(left.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        right_examples = {
            key
            for value in (right.get("evidence_examples") if isinstance(right.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        shared_evidence = bool(left_examples & right_examples)
        pattern_similarity = self._expression_rule_text_similarity(left_pattern, right_pattern)
        situation_similarity = self._expression_rule_text_similarity(
            left.get("situation"),
            right.get("situation"),
        )
        left_keywords = self._expression_rule_value_set(left.get("keywords") or left.get("tags"))
        right_keywords = self._expression_rule_value_set(right.get("keywords") or right.get("tags"))
        keyword_overlap = len(left_keywords & right_keywords)

        if left_key == right_key:
            return {
                "code": "same_pattern" if context_compatible else "same_pattern_distinct_context",
                "confidence": 0.99 if context_compatible else 0.72,
                "auto_merge": bool(context_compatible and not manually_edited),
                "pattern_similarity": 1.0,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": (
                    "同类规则使用相同表达模板，适用上下文兼容"
                    if context_compatible
                    else "同类规则使用相同模板，但适用上下文存在差异"
                ),
            }
        if shared_evidence and pattern_similarity >= 0.62:
            return {
                "code": "shared_evidence_variant",
                "confidence": 0.96 if context_compatible else 0.82,
                "auto_merge": bool(context_compatible and not manually_edited),
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": True,
                "reason": "同类规则由相同支持片段归纳，模板只是占位符或语气变体",
            }
        if pattern_similarity >= 0.78 and (situation_similarity >= 0.25 or keyword_overlap >= 1):
            return {
                "code": "near_pattern_context",
                "confidence": min(0.93, 0.72 + pattern_similarity * 0.14 + situation_similarity * 0.12),
                "auto_merge": False,
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": "模板和适用情境高度相近，建议人工确认是否保留两个规则组",
            }
        if pattern_similarity >= 0.84:
            return {
                "code": "near_pattern",
                "confidence": min(0.86, 0.68 + pattern_similarity * 0.18),
                "auto_merge": False,
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": "表达模板高度相似，但现有情境证据不足以自动合并",
            }
        return {}

    def _merge_expression_rule_duplicate_metadata(
        self,
        target: dict[str, Any],
        incoming: dict[str, Any],
    ) -> None:
        scope_before = dict(target)
        scope_before.pop("scope_binding", None)
        for field, limit in (("keywords", 8), ("tags", 8), ("evidence_examples", 3), ("source_kinds", 8)):
            left_values = target.get(field) if isinstance(target.get(field), list) else []
            right_values = incoming.get(field) if isinstance(incoming.get(field), list) else []
            target[field] = list(dict.fromkeys([
                *[str(item) for item in left_values if str(item).strip()],
                *[str(item) for item in right_values if str(item).strip()],
            ]))[:limit]
        if target.get("keywords"):
            target["tags"] = list(target["keywords"])
        for field in ("channels", "relationship_stages", "emotion_gates"):
            target[field] = list(dict.fromkeys([
                *sorted(self._expression_rule_value_set(target.get(field))),
                *sorted(self._expression_rule_value_set(incoming.get(field))),
            ]))[:8]

        target_intent = self._normalize_expression_intent(target.get("intent"))
        incoming_intent = self._normalize_expression_intent(incoming.get("intent"))
        if target_intent == "any":
            target["intent"] = incoming_intent
        elif incoming_intent == "any" or target_intent == incoming_intent:
            target["intent"] = target_intent
        else:
            target["intent"] = "any"
        incoming_avoid = _single_line(incoming.get("avoid"), 160)
        if incoming_avoid and len(incoming_avoid) > len(_single_line(target.get("avoid"), 160)):
            target["avoid"] = incoming_avoid
        if not _single_line(target.get("label"), 100) and _single_line(incoming.get("label"), 100):
            target["label"] = _single_line(incoming.get("label"), 100)
        target["persona_conflict"] = bool(
            self._expression_rule_bool(target.get("persona_conflict"))
            or self._expression_rule_bool(incoming.get("persona_conflict"))
        )

        target_batch = _single_line(target.get("last_batch_key"), 80)
        incoming_batch = _single_line(incoming.get("last_batch_key"), 80)
        target_evidence = _safe_int(target.get("evidence_count"), 0, 0)
        incoming_evidence = _safe_int(incoming.get("evidence_count"), 0, 0)
        if target_batch and incoming_batch and target_batch == incoming_batch:
            target["evidence_count"] = max(target_evidence, incoming_evidence)
        else:
            target["evidence_count"] = min(99, target_evidence + incoming_evidence)
        for field, ceiling in (("positive_feedback", 999), ("negative_feedback", 999), ("use_count", 99999)):
            target[field] = min(
                ceiling,
                _safe_int(target.get(field), 0, 0) + _safe_int(incoming.get(field), 0, 0),
            )
        target["last_seen_ts"] = max(
            _safe_float(target.get("last_seen_ts"), 0.0),
            _safe_float(incoming.get("last_seen_ts"), 0.0),
        )
        target["last_used_ts"] = max(
            _safe_float(target.get("last_used_ts"), 0.0),
            _safe_float(incoming.get("last_used_ts"), 0.0),
        )
        created_values = [
            value
            for value in (
                _safe_float(target.get("created_ts"), 0.0),
                _safe_float(incoming.get("created_ts"), 0.0),
            )
            if value > 0
        ]
        if created_values:
            target["created_ts"] = min(created_values)
        if _safe_float(incoming.get("last_seen_ts"), 0.0) >= _safe_float(target.get("last_seen_ts"), 0.0):
            if incoming_batch:
                target["last_batch_key"] = incoming_batch

        source_refs = []
        seen_refs: set[tuple[str, str, str]] = set()
        for raw_ref in [
            *(target.get("source_refs") if isinstance(target.get("source_refs"), list) else []),
            *(incoming.get("source_refs") if isinstance(incoming.get("source_refs"), list) else []),
        ]:
            if not isinstance(raw_ref, dict):
                continue
            ref = {
                "source_kind": _single_line(raw_ref.get("source_kind"), 24),
                "source_id": _single_line(raw_ref.get("source_id"), 80),
                "rule_id": _single_line(raw_ref.get("rule_id"), 40),
            }
            key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
            if all(key) and key not in seen_refs:
                seen_refs.add(key)
                source_refs.append(ref)
        if source_refs:
            target["source_refs"] = source_refs[:24]
        scope_binding = target.get("scope_binding") if isinstance(target.get("scope_binding"), dict) else None
        scope_after = dict(target)
        scope_after.pop("scope_binding", None)
        if scope_binding is not None and scope_before != scope_after:
            scope_binding["revision"] = max(1, _safe_int(scope_binding.get("revision"), 1, 1) + 1)

    @staticmethod
    def _expression_rule_family_priority(items: list[dict[str, Any]]) -> tuple[int, int, int, float]:
        return (
            sum(_safe_int(item.get("evidence_count"), 0, 0) for item in items),
            sum(_safe_int(item.get("use_count"), 0, 0) for item in items),
            sum(1 for item in items if re.search(r"_{2,}|\[[^\]]+\]", _single_line(item.get("pattern"), 100))),
            max((_safe_float(item.get("last_seen_ts"), 0.0) for item in items), default=0.0),
        )

    def _deduplicate_expression_rule_families(self, rules: Any) -> bool:
        if not isinstance(rules, list) or len(rules) < 2:
            return False
        self._assign_expression_rule_families(rules)
        groups = self._expression_rule_groups(rules)
        kept: list[list[dict[str, Any]]] = []
        changed = False

        def anchor(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            return next(
                (item for item in items if _single_line(item.get("kind"), 16).lower() == "style"),
                next((item for item in items if isinstance(item, dict)), None),
            )

        for group in groups:
            current_anchor = anchor(group)
            if current_anchor is None:
                continue
            matched_index = -1
            for index, existing_group in enumerate(kept):
                existing_anchor = anchor(existing_group)
                analysis = self._expression_rule_duplicate_analysis(existing_anchor, current_anchor)
                if analysis.get("auto_merge"):
                    matched_index = index
                    break
            if matched_index < 0:
                kept.append(group)
                continue

            target_group = kept[matched_index]
            incoming_group = group
            if self._expression_rule_family_priority(incoming_group) > self._expression_rule_family_priority(target_group):
                target_group, incoming_group = incoming_group, target_group
                kept[matched_index] = target_group
            target_by_kind = {
                _single_line(item.get("kind"), 16).lower(): item
                for item in target_group
                if isinstance(item, dict)
            }
            for incoming in incoming_group:
                if not isinstance(incoming, dict):
                    continue
                kind = _single_line(incoming.get("kind"), 16).lower()
                target = target_by_kind.get(kind)
                if target is None:
                    target_group.append(incoming)
                    target_by_kind[kind] = incoming
                else:
                    self._merge_expression_rule_duplicate_metadata(target, incoming)
            family_key = next(
                (
                    _single_line(item.get("family_key"), 80).lower()
                    for item in target_group
                    if _single_line(item.get("family_key"), 80)
                ),
                "",
            )
            if not family_key:
                seed = "|".join(sorted(_single_line(item.get("id"), 100) for item in target_group))
                family_key = f"merged_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"
            for item in target_group:
                item["family_key"] = family_key
            changed = True

        if not changed:
            return False
        rules[:] = [item for group in kept for item in group]
        self._assign_expression_rule_families(rules)
        return True

    def _expression_rule_pair_score(self, style: dict[str, Any], grammar: dict[str, Any]) -> float:
        style_family = _single_line(style.get("family_id"), 64)
        grammar_family = _single_line(grammar.get("family_id"), 64)
        if style_family and style_family == grammar_family and not style_family.startswith("xs-"):
            return 1000.0

        style_key = _single_line(style.get("family_key"), 80).lower()
        grammar_key = _single_line(grammar.get("family_key"), 80).lower()
        if style_key and style_key == grammar_key:
            return 900.0

        style_examples = {
            key
            for value in (style.get("evidence_examples") if isinstance(style.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        grammar_examples = {
            key
            for value in (grammar.get("evidence_examples") if isinstance(grammar.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        overlap_count = len(style_examples & grammar_examples)
        overlap_ratio = overlap_count / max(1, max(len(style_examples), len(grammar_examples)))
        situation_similarity = self._expression_rule_text_similarity(
            style.get("situation"),
            grammar.get("situation"),
        )
        style_keywords = {
            _single_line(value, 24).lower()
            for value in (style.get("keywords") if isinstance(style.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        grammar_keywords = {
            _single_line(value, 24).lower()
            for value in (grammar.get("keywords") if isinstance(grammar.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        keyword_overlap = len(style_keywords & grammar_keywords)
        same_batch = bool(
            _single_line(style.get("last_batch_key"), 80)
            and _single_line(style.get("last_batch_key"), 80)
            == _single_line(grammar.get("last_batch_key"), 80)
        )
        same_evidence_count = _safe_int(style.get("evidence_count"), 0, 0) == _safe_int(
            grammar.get("evidence_count"), 0, 0
        )

        # 旧规则没有 family_key，只在支持片段确实重叠时自动配对；
        # 同批次、情境高度相近只作为辅助，避免把同一批中的不同规则强行绑在一起。
        if overlap_ratio >= 0.45:
            return 500.0 + overlap_ratio * 100 + situation_similarity * 20 + min(2, keyword_overlap) * 5
        if overlap_count and same_batch and situation_similarity >= 0.3:
            return 430.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        if same_batch and same_evidence_count and situation_similarity >= 0.72 and keyword_overlap:
            return 320.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        return -1.0

    def _assign_expression_rule_families(
        self,
        rules: Any,
        *,
        batch_key: str = "",
    ) -> bool:
        if not isinstance(rules, list):
            return False
        valid_rules = [item for item in rules if isinstance(item, dict)]
        if not valid_rules:
            return False
        for item in valid_rules:
            if batch_key and not _single_line(item.get("last_batch_key"), 80):
                item["last_batch_key"] = _single_line(batch_key, 80)
            family_key = _single_line(item.get("family_key"), 80).lower()
            if family_key:
                item["family_key"] = family_key

        styles = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "style"]
        grammars = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "grammar"]
        pair_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for style in styles:
            for grammar in grammars:
                score = self._expression_rule_pair_score(style, grammar)
                if score >= 0:
                    pair_candidates.append((score, style, grammar))
        pair_candidates.sort(key=lambda item: item[0], reverse=True)

        paired_ids: set[int] = set()
        family_by_object: dict[int, str] = {}
        for _, style, grammar in pair_candidates:
            if id(style) in paired_ids or id(grammar) in paired_ids:
                continue
            rule_ids = sorted([
                value
                for value in (
                    _single_line(style.get("id"), 100) or self._expression_rule_signature(style),
                    _single_line(grammar.get("id"), 100) or self._expression_rule_signature(grammar),
                )
                if value
            ])
            family_seed = "|".join(rule_ids)
            family_id = f"xf-{hashlib.sha1(family_seed.encode('utf-8')).hexdigest()[:16]}"
            family_by_object[id(style)] = family_id
            family_by_object[id(grammar)] = family_id
            paired_ids.update({id(style), id(grammar)})

        changed = False
        for item in valid_rules:
            family_id = family_by_object.get(id(item))
            if not family_id:
                rule_id = _single_line(item.get("id"), 100) or self._expression_rule_signature(item)
                family_id = f"xs-{hashlib.sha1(rule_id.encode('utf-8')).hexdigest()[:16]}"
            if _single_line(item.get("family_id"), 64) != family_id:
                item["family_id"] = family_id
                changed = True
        return changed

    def _backfill_expression_rule_families(self, profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            rules = profile.get(storage_key)
            if isinstance(rules, list) and self._assign_expression_rule_families(rules):
                changed = True
        return changed

    def _expression_rule_groups(self, rules: Any) -> list[list[dict[str, Any]]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                continue
            if family_id not in groups:
                groups[family_id] = []
                order.append(family_id)
            groups[family_id].append(item)
        return [groups[family_id] for family_id in order]

    def _expression_rule_runtime_bundle(self, group: Any) -> dict[str, Any]:
        items = [dict(item) for item in group if isinstance(item, dict)] if isinstance(group, list) else []
        if not items:
            return {}
        items.sort(key=lambda item: 0 if _single_line(item.get("kind"), 16).lower() == "style" else 1)
        style_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "style"), None)
        grammar_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "grammar"), None)
        primary = style_rule or grammar_rule or items[0]
        family_id = _single_line(primary.get("family_id"), 64)
        bundle = dict(primary)
        bundle["id"] = family_id if len(items) > 1 else _single_line(primary.get("id"), 100)
        bundle["family_id"] = family_id
        bundle["kind"] = "combined" if style_rule and grammar_rule else _single_line(primary.get("kind"), 16).lower()
        bundle["component_kinds"] = [
            kind for kind in ("style", "grammar")
            if any(_single_line(item.get("kind"), 16).lower() == kind for item in items)
        ]
        bundle["component_count"] = len(items)
        bundle["component_rules"] = items
        bundle["style_rule"] = dict(style_rule) if style_rule else None
        bundle["grammar_rule"] = dict(grammar_rule) if grammar_rule else None
        bundle["evidence_count"] = max(_safe_int(item.get("evidence_count"), 0, 0) for item in items)
        bundle["positive_feedback"] = max(_safe_int(item.get("positive_feedback"), 0, 0) for item in items)
        bundle["negative_feedback"] = max(_safe_int(item.get("negative_feedback"), 0, 0) for item in items)
        bundle["use_count"] = max(_safe_int(item.get("use_count"), 0, 0) for item in items)
        bundle["last_seen_ts"] = max(_safe_float(item.get("last_seen_ts"), 0.0) for item in items)
        bundle["last_used_ts"] = max(_safe_float(item.get("last_used_ts"), 0.0) for item in items)
        for field, limit in (("keywords", 8), ("tags", 8), ("signals", 8), ("channels", 8), ("relationship_stages", 8), ("emotion_gates", 8)):
            values: list[str] = []
            for item in items:
                for value in item.get(field, []) if isinstance(item.get(field), list) else []:
                    normalized = _single_line(value, 32)
                    if normalized and normalized not in values:
                        values.append(normalized)
            bundle[field] = values[:limit]
        examples: list[str] = []
        refs: list[dict[str, str]] = []
        ref_keys: set[tuple[str, str, str]] = set()
        for item in items:
            for example in item.get("evidence_examples", []) if isinstance(item.get("evidence_examples"), list) else []:
                value = _single_line(example, 80)
                if value and value not in examples:
                    examples.append(value)
            for raw_ref in item.get("source_refs", []) if isinstance(item.get("source_refs"), list) else []:
                if not isinstance(raw_ref, dict):
                    continue
                ref = {
                    "source_kind": _single_line(raw_ref.get("source_kind"), 16).lower(),
                    "source_id": _single_line(raw_ref.get("source_id"), 80),
                    "rule_id": _single_line(raw_ref.get("rule_id"), 40),
                }
                key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
                if all(key) and key not in ref_keys:
                    ref_keys.add(key)
                    refs.append(ref)
        bundle["evidence_examples"] = examples[:6]
        bundle["source_refs"] = refs
        avoid_values = [
            _single_line(item.get("avoid"), 160)
            for item in items
            if _single_line(item.get("avoid"), 160)
        ]
        bundle["avoid"] = "；".join(dict.fromkeys(avoid_values))[:240]
        bundle["persona_conflict"] = any(self._expression_rule_bool(item.get("persona_conflict")) for item in items)
        return bundle
