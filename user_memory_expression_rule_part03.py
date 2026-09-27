# -*- coding: utf-8 -*-
"""UserMemoryExpressionRulePart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_rule.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 479 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionRuleMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from typing import Any



class UserMemoryExpressionRulePart03Mixin:
    """UserMemoryExpressionRulePart03Mixin（从 UserMemoryExpressionRuleMixin 拆出）。"""


    @staticmethod
    def _normalize_expression_evidence_examples(
        value: Any,
        *,
        source_kind: str,
        source_names: set[str],
    ) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            example = _single_line(raw, 72)
            example = re.sub(r"^[^:：]{1,32}[:：]\s*", "", example).strip()
            if not example or re.search(r"https?://|@|\b\d{5,}\b|QQ|群号|用户ID", example, re.IGNORECASE):
                continue
            if source_kind == "group" and any(name and name in example for name in source_names):
                continue
            if example not in result:
                result.append(example)
            if len(result) >= 3:
                break
        return result

    def _normalize_expression_rule_candidates(
        self,
        raw_rules: Any,
        *,
        source_kind: str,
        source_text: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_rules, list):
            return []
        source_utterances, source_names = self._expression_rule_source_parts(
            source_text,
            source_kind=source_kind,
        )
        compact_utterances = {
            re.sub(r"\s+", "", line).lower()
            for line in source_utterances
            if line.strip()
        }
        result: list[dict[str, Any]] = []
        for raw in raw_rules[:12]:
            if not isinstance(raw, dict):
                continue
            kind = _single_line(raw.get("kind") or raw.get("type"), 16).lower()
            if kind not in {"style", "grammar"}:
                continue
            situation = _single_line(raw.get("situation"), 80)
            pattern = _single_line(raw.get("pattern") or raw.get("style"), 100)
            instruction = _single_line(raw.get("instruction"), 140)
            avoid = _single_line(raw.get("avoid"), 160)
            evidence_examples = self._normalize_expression_evidence_examples(
                raw.get("evidence_examples") or raw.get("examples"),
                source_kind=source_kind,
                source_names=source_names,
            )
            evidence = _safe_int(raw.get("evidence_count"), len(evidence_examples) or 1, 1, 20)
            if not situation or not pattern or not instruction:
                continue
            if kind == "style" and not self._expression_style_pattern_is_reusable(pattern):
                continue
            if kind == "grammar" and not self._expression_grammar_pattern_is_specific(pattern):
                continue
            if source_utterances:
                evidence = min(evidence, len(source_utterances))
            if evidence < 1:
                continue
            combined_rule = f"{situation} {pattern} {instruction}"
            if any(marker in combined_rule for marker in ("SELF", "系统提示", "提示词", "用户ID", "群号", "QQ号")):
                continue
            if re.search(r"@|\b\d{5,}\b|QQ|昵称为|ID为", combined_rule, re.IGNORECASE):
                continue
            if source_kind == "group":
                if any(name and name in combined_rule for name in source_names):
                    continue
                # 允许保留短而有辨识度的表达或占位模板，这是 WaifuBot 式学习的核心；
                # 仍拒绝长句照搬、成员身份和账号等不可迁移内容。
                compact_pattern = re.sub(r"\s+", "", pattern).lower()
                if len(pattern) > 48 and any(len(line) >= 16 and line in compact_pattern for line in compact_utterances):
                    continue
                quoted = re.findall(r"[“\"‘']([^”\"’']{2,40})[”\"’']", combined_rule)
                if any(
                    len(quote) > 24 and re.sub(r"\s+", "", quote).lower() in line
                    for quote in quoted
                    for line in compact_utterances
                ):
                    continue
            raw_keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else raw.get("tags")
            keywords = []
            if isinstance(raw_keywords, list):
                for keyword in raw_keywords:
                    value = _single_line(keyword, 24)
                    if source_kind == "group" and (
                        value in source_names
                        or re.search(r"\d{5,}", value)
                        or re.sub(r"\s+", "", value).lower() in compact_utterances
                    ):
                        continue
                    if len(value) >= 2 and value not in keywords:
                        keywords.append(value)
                    if len(keywords) >= 8:
                        break
            item = {
                "kind": kind,
                "situation": situation,
                "pattern": pattern,
                "instruction": instruction,
                "keywords": keywords,
                "tags": list(keywords),
                "evidence_examples": evidence_examples,
                "evidence_count": evidence,
                "source_kind": source_kind,
                "family_key": _single_line(raw.get("family_key"), 80).lower(),
                "merge_into_id": _single_line(raw.get("merge_into_id"), 40),
                "channels": self._normalize_expression_rule_channels(raw.get("channels"), source_kind=source_kind),
                "relationship_stages": self._normalize_expression_relationship_stages(raw.get("relationship_stages")),
                "emotion_gates": self._normalize_expression_emotion_gates(raw.get("emotion_gates")),
                "intent": self._normalize_expression_intent(raw.get("intent")),
                "avoid": avoid or "事实、工具结果、安全边界或人格发生冲突时不用",
                "persona_conflict": self._expression_rule_bool(raw.get("persona_conflict")) or bool(
                    re.search(
                        r"(?:假装|谎称|声称).{0,12}(?:成功|完成|已发|发过)|"
                        r"(?:无视|覆盖|改写).{0,8}(?:人格|安全|事实|工具结果)|"
                        r"(?:必须|永远|无条件).{0,10}(?:服从|同意|答应)",
                        combined_rule,
                        re.IGNORECASE,
                    )
                ),
                "positive_feedback": 0,
                "negative_feedback": 0,
                "use_count": 0,
            }
            item["id"] = self._expression_rule_signature(item)
            duplicate = next((old for old in result if old.get("id") == item["id"]), None)
            if duplicate is not None:
                duplicate["evidence_count"] = max(
                    _safe_int(duplicate.get("evidence_count"), 0, 0),
                    evidence,
                )
                continue
            result.append(item)
        return result[:6]

    def _merge_learned_expression_rules(
        self,
        profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        batch_key: str,
        now: float,
        pending: bool = False,
    ) -> bool:
        if not isinstance(profile, dict) or not candidates:
            return False
        candidates = [item for item in candidates if self._expression_rule_definition_is_valid(item)]
        if not candidates:
            return False
        self._assign_expression_rule_families(candidates, batch_key=batch_key)
        storage_key = "pending_rules" if pending else "learned_rules"
        approved_changed = False
        if pending:
            approved_rules = [
                dict(item)
                for item in profile.get("learned_rules", [])
                if isinstance(item, dict)
            ]
            approved_by_id = {
                _single_line(item.get("id"), 40): item
                for item in approved_rules
                if _single_line(item.get("id"), 40)
            }
            pending_candidates: list[dict[str, Any]] = []
            for candidate in candidates:
                target = None
                requested_merge_id = _single_line(candidate.get("merge_into_id"), 40)
                requested_target = approved_by_id.get(requested_merge_id) if requested_merge_id else None
                if requested_target is not None and not requested_target.get("manually_edited"):
                    analysis = self._expression_rule_duplicate_analysis(requested_target, candidate)
                    if (
                        analysis.get("auto_merge")
                        or (
                            analysis.get("confidence", 0.0) >= 0.78
                            and self._expression_rule_contexts_compatible(requested_target, candidate)
                        )
                    ):
                        target = requested_target
                if target is None:
                    for approved in approved_rules:
                        analysis = self._expression_rule_duplicate_analysis(approved, candidate)
                        if analysis.get("auto_merge"):
                            target = approved
                            break
                if target is None:
                    pending_candidates.append(candidate)
                    continue
                incoming = dict(candidate)
                incoming["last_seen_ts"] = now
                incoming["last_batch_key"] = batch_key
                self._merge_expression_rule_duplicate_metadata(target, incoming)
                approved_changed = True
            if approved_changed:
                self._deduplicate_expression_rule_families(approved_rules)
                approved_rules.sort(
                    key=lambda item: (
                        -_safe_int(item.get("evidence_count"), 0, 0),
                        -_safe_float(item.get("last_seen_ts"), 0.0),
                    )
                )
                profile["learned_rules"] = approved_rules[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
            candidates = pending_candidates
            if not candidates:
                return approved_changed
        existing = [dict(item) for item in profile.get(storage_key, []) if isinstance(item, dict)]
        families_changed = self._assign_expression_rule_families(existing)
        by_id = {_single_line(item.get("id"), 40): item for item in existing if _single_line(item.get("id"), 40)}

        def semantic_key(item: dict[str, Any]) -> str:
            kind = _single_line(item.get("kind"), 16).lower()
            situation = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("situation"), 80).lower(),
            )
            pattern = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("pattern") or item.get("style"), 100).lower(),
            )
            return f"{kind}|{situation}|{pattern}"

        by_semantic_key = {
            semantic_key(item): item
            for item in existing
            if semantic_key(item) != "||"
        }
        changed = bool(families_changed or approved_changed)
        for candidate in candidates:
            rule_id = _single_line(candidate.get("id"), 40)
            requested_merge_id = _single_line(candidate.get("merge_into_id"), 40)
            requested_target = by_id.get(requested_merge_id) if requested_merge_id else None
            if requested_target is not None:
                analysis = self._expression_rule_duplicate_analysis(requested_target, candidate)
                if not (
                    analysis.get("auto_merge")
                    or (
                        analysis.get("confidence", 0.0) >= 0.78
                        and self._expression_rule_contexts_compatible(requested_target, candidate)
                    )
                ):
                    requested_target = None
            old = requested_target or by_id.get(rule_id) or by_semantic_key.get(semantic_key(candidate))
            if old is None:
                old = dict(candidate)
                old.pop("merge_into_id", None)
                if pending:
                    old["review_status"] = "pending"
                old["created_ts"] = now
                old["last_seen_ts"] = now
                old["last_batch_key"] = batch_key
                existing.append(old)
                by_id[rule_id] = old
                by_semantic_key[semantic_key(old)] = old
                changed = True
                continue
            scope_before = dict(old)
            scope_before.pop("scope_binding", None)
            old["last_seen_ts"] = now
            incoming_family_key = _single_line(candidate.get("family_key"), 80).lower()
            if incoming_family_key and not _single_line(old.get("family_key"), 80):
                old["family_key"] = incoming_family_key
            old["keywords"] = list(dict.fromkeys([
                *[str(item) for item in old.get("keywords", []) if str(item).strip()],
                *[str(item) for item in candidate.get("keywords", []) if str(item).strip()],
            ]))[:8]
            old["tags"] = list(old["keywords"])
            old["evidence_examples"] = list(dict.fromkeys([
                *[str(item) for item in old.get("evidence_examples", []) if str(item).strip()],
                *[str(item) for item in candidate.get("evidence_examples", []) if str(item).strip()],
            ]))[:3]
            for field in ("channels", "relationship_stages", "emotion_gates"):
                old_values = old.get(field) if isinstance(old.get(field), list) else []
                candidate_values = candidate.get(field) if isinstance(candidate.get(field), list) else []
                old[field] = list(dict.fromkeys([
                    *[_single_line(item, 24).lower() for item in old_values if _single_line(item, 24)],
                    *[_single_line(item, 24).lower() for item in candidate_values if _single_line(item, 24)],
                ]))[:8]
            old_intent = self._normalize_expression_intent(old.get("intent"))
            candidate_intent = self._normalize_expression_intent(candidate.get("intent"))
            if old_intent == "any":
                old["intent"] = candidate_intent
            elif candidate_intent == "any" or old_intent == candidate_intent:
                old["intent"] = old_intent
            else:
                old["intent"] = "any"
            candidate_avoid = _single_line(candidate.get("avoid"), 160)
            if candidate_avoid and len(candidate_avoid) > len(_single_line(old.get("avoid"), 160)):
                old["avoid"] = candidate_avoid
            old["persona_conflict"] = bool(
                self._expression_rule_bool(old.get("persona_conflict"))
                or self._expression_rule_bool(candidate.get("persona_conflict"))
            )
            old["positive_feedback"] = _safe_int(old.get("positive_feedback"), 0, 0)
            old["negative_feedback"] = _safe_int(old.get("negative_feedback"), 0, 0)
            old["use_count"] = _safe_int(old.get("use_count"), 0, 0)
            if _single_line(old.get("last_batch_key"), 40) != batch_key:
                old["evidence_count"] = min(
                    99,
                    _safe_int(old.get("evidence_count"), 0, 0) + _safe_int(candidate.get("evidence_count"), 0, 0),
                )
                old["last_batch_key"] = batch_key
            else:
                old["evidence_count"] = max(
                    _safe_int(old.get("evidence_count"), 0, 0),
                    _safe_int(candidate.get("evidence_count"), 0, 0),
                )
            scope_binding = old.get("scope_binding") if isinstance(old.get("scope_binding"), dict) else None
            scope_after = dict(old)
            scope_after.pop("scope_binding", None)
            if scope_binding is not None and scope_before != scope_after:
                scope_binding["revision"] = max(1, _safe_int(scope_binding.get("revision"), 1, 1) + 1)
            changed = True
        if self._deduplicate_expression_rule_families(existing):
            changed = True
        if self._assign_expression_rule_families(existing, batch_key=batch_key):
            changed = True
        existing.sort(
            key=lambda item: (
                -_safe_int(item.get("evidence_count"), 0, 0),
                -_safe_float(item.get("last_seen_ts"), 0.0),
            )
        )
        profile[storage_key] = existing[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        return changed

    def _select_learned_expression_rules(
        self,
        rules: Any,
        *,
        hint: str = "",
        limit: int = 2,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        query = _single_line(hint, 300).lower()
        context = context if isinstance(context, dict) else {}
        channel = _single_line(context.get("channel"), 24).lower()
        relationship_stage = _single_line(context.get("relationship_stage"), 24).lower()
        emotion_gate = _single_line(context.get("emotion_gate"), 24).lower()
        current_intent = self._normalize_expression_intent(context.get("intent"))
        now = _now_ts()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw in rules:
            if not isinstance(raw, dict) or _safe_int(raw.get("evidence_count"), 0, 0) < 1:
                continue
            if not self._expression_rule_definition_is_valid(raw):
                continue
            review_status = _single_line(raw.get("review_status"), 24).lower()
            if review_status in {"pending", "needs_review", "rejected"}:
                continue
            if self._expression_rule_bool(raw.get("persona_conflict")):
                continue
            negative_feedback = _safe_int(raw.get("negative_feedback"), 0, 0)
            positive_feedback = _safe_int(raw.get("positive_feedback"), 0, 0)
            if negative_feedback >= 2 and negative_feedback > positive_feedback:
                continue

            context_score = 0
            channels = raw.get("channels") if isinstance(raw.get("channels"), list) else []
            normalized_channels = {_single_line(item, 24).lower() for item in channels if _single_line(item, 24)}
            if normalized_channels and channel and channel not in normalized_channels:
                continue
            if normalized_channels and channel in normalized_channels:
                context_score += 4

            relationship_stages = raw.get("relationship_stages") if isinstance(raw.get("relationship_stages"), list) else []
            normalized_relationships = {
                _single_line(item, 24).lower() for item in relationship_stages if _single_line(item, 24)
            }
            if normalized_relationships and "any" not in normalized_relationships:
                if relationship_stage and relationship_stage not in normalized_relationships:
                    continue
                if relationship_stage in normalized_relationships:
                    context_score += 3

            emotion_gates = raw.get("emotion_gates") if isinstance(raw.get("emotion_gates"), list) else []
            normalized_emotions = {_single_line(item, 24).lower() for item in emotion_gates if _single_line(item, 24)}
            if normalized_emotions and "any" not in normalized_emotions:
                if emotion_gate and emotion_gate not in normalized_emotions:
                    continue
                if emotion_gate in normalized_emotions:
                    context_score += 3

            rule_intent = self._normalize_expression_intent(raw.get("intent"))
            intent_equivalents = {
                "help": {"help", "request", "question"},
                "comfort": {"comfort", "emotion"},
                "play": {"play", "tease"},
                "tease": {"play", "tease"},
                "intimacy": {"intimacy", "emotion"},
                "boundary": {"boundary"},
                "acknowledgement": {"acknowledgement", "casual"},
                "question": {"question", "help"},
                "request": {"request", "help"},
                "emotion": {"emotion", "comfort", "intimacy"},
                "casual": {"casual", "acknowledgement"},
                "proactive": {"proactive", "casual"},
            }
            if rule_intent != "any" and current_intent != "any":
                if rule_intent not in intent_equivalents.get(current_intent, {current_intent}):
                    continue
                context_score += 5
            keywords = [
                _single_line(item, 24).lower()
                for item in raw.get("keywords", [])
                if _single_line(item, 24)
            ] if isinstance(raw.get("keywords"), list) else []
            matched = sum(1 for keyword in keywords if query and keyword in query)
            if query and keywords and matched <= 0 and context_score <= 0:
                continue
            last_seen_ts = _safe_float(raw.get("last_seen_ts"), 0.0)
            age_days = max(0.0, (now - last_seen_ts) / 86400) if last_seen_ts > 0 else 0.0
            freshness = max(0.01, 1.0 - age_days / 30.0)
            feedback_score = min(8, positive_feedback * 1.5) - min(16, negative_feedback * 4)
            score = (
                matched * 10
                + context_score
                + min(9, _safe_int(raw.get("evidence_count"), 0, 0)) * freshness
                + feedback_score
            )
            ranked.append((score, raw))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        grouped_ranked: dict[str, dict[str, Any]] = {}
        group_order: list[str] = []
        for score, item in ranked:
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                family_id = f"xs-{hashlib.sha1(_single_line(item.get('id'), 100).encode('utf-8')).hexdigest()[:16]}"
            if family_id not in grouped_ranked:
                grouped_ranked[family_id] = {"score": score, "items": []}
                group_order.append(family_id)
            grouped_ranked[family_id]["score"] = max(_safe_float(grouped_ranked[family_id].get("score"), score), score)
            grouped_ranked[family_id]["items"].append(item)

        ranked_groups: list[tuple[float, dict[str, Any]]] = []
        for family_id in group_order:
            entry = grouped_ranked[family_id]
            bundle = self._expression_rule_runtime_bundle(entry.get("items"))
            if not bundle:
                continue
            complement_bonus = min(1.5, max(0, _safe_int(bundle.get("component_count"), 1, 1) - 1) * 0.75)
            ranked_groups.append((_safe_float(entry.get("score"), 0.0) + complement_bonus, bundle))
        ranked_groups.sort(key=lambda pair: pair[0], reverse=True)

        limit = max(1, limit)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        seen_kinds: set[str] = set()
        # 同源的表达与语法作为一个规则组占一个名额；独立规则仍优先覆盖两种能力。
        for _, bundle in ranked_groups:
            component_kinds = {
                _single_line(item, 16).lower()
                for item in bundle.get("component_kinds", [])
                if _single_line(item, 16).lower() in {"style", "grammar"}
            }
            if component_kinds and component_kinds.issubset(seen_kinds):
                continue
            selected.append(bundle)
            selected_ids.add(_single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100))
            seen_kinds.update(component_kinds)
            if len(selected) >= limit:
                return selected
        for _, bundle in ranked_groups:
            bundle_id = _single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100)
            if bundle_id in selected_ids:
                continue
            selected.append(bundle)
            if len(selected) >= limit:
                break
        return selected
