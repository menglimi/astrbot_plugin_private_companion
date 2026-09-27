# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionLibraryShareMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 307 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiExpressionMixin）。
"""
from __future__ import annotations
from .page_api_expression_shared import Any
from .page_api_expression_shared import hashlib
from .page_api_expression_shared import json
from .page_api_expression_shared import re



class PrivateCompanionPageApiExpressionLibraryShareMixin:
    """PrivateCompanionPageApiExpressionLibraryShareMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    def _expression_library_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        samples: list[dict[str, Any]] = []
        pending_samples: list[dict[str, Any]] = []
        pending_rules: list[dict[str, Any]] = []
        rules: list[dict[str, Any]] = []
        pending_rule_groups: list[dict[str, Any]] = []
        rule_groups: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        scene_totals: dict[str, dict[str, Any]] = {}
        sample_count = 0
        observation_evidence_count = 0
        rule_evidence_count = 0
        pending_count = 0
        injected_count = 0
        positive_feedback_count = 0
        negative_feedback_count = 0
        style_rule_count = 0
        grammar_rule_count = 0
        pending_style_count = 0
        pending_grammar_count = 0

        def collect(source_type: str, source_id: str, item: dict[str, Any]) -> None:
            nonlocal sample_count, observation_evidence_count, rule_evidence_count, pending_count
            nonlocal injected_count, positive_feedback_count, negative_feedback_count
            nonlocal style_rule_count, grammar_rule_count, pending_style_count, pending_grammar_count
            summary = self._expression_profile_summary(item, source_type=source_type)
            if source_type == "persona":
                source_name = "当前人格全局规则"
                active = True
                source_kind_label = "人格全局"
            elif source_type == "group":
                source_name = self._single_line(
                    item.get("name") or item.get("group_name") or item.get("display_name"),
                    80,
                ) or "未命名群聊"
                active = bool(self.plugin._expression_group_learning_source_enabled(source_id))
                source_kind_label = "群聊"
            else:
                source_name = self._single_line(
                    item.get("display_name") or item.get("nickname") or item.get("name"),
                    80,
                ) or source_id
                active = bool(self.plugin._expression_private_learning_source_enabled(item, source_id))
                source_kind_label = "私聊"
            source = {
                "source_type": source_type,
                "source_kind_label": source_kind_label,
                "source_id": source_id,
                "source_name": source_name,
                "source_active": active,
                "scope_revision": self._int(summary.get("scope_revision")),
            }
            source_sample_count = self._int(summary.get("sample_count"))
            source_pending_sample_count = self._int(summary.get("pending_count"))
            source_pending_rule_count = self._int(summary.get("pending_rule_count"))
            source_pending_count = source_pending_sample_count + source_pending_rule_count
            source_rules = summary.get("rules") if isinstance(summary.get("rules"), list) else []
            source_pending_rules = summary.get("pending_rules") if isinstance(summary.get("pending_rules"), list) else []
            source_rule_groups = summary.get("rule_groups") if isinstance(summary.get("rule_groups"), list) else []
            source_pending_rule_groups = summary.get("pending_rule_groups") if isinstance(summary.get("pending_rule_groups"), list) else []
            if source_sample_count <= 0 and source_pending_count <= 0 and not source_rules and not source_pending_rules:
                return
            sources.append(
                {
                    **source,
                    "sample_count": source_sample_count,
                    "observation_count": source_sample_count,
                    "pending_count": source_pending_count,
                    "pending_rule_count": source_pending_rule_count,
                    "rule_count": len(source_rules),
                    "rule_group_count": len(source_rule_groups),
                    "pending_rule_group_count": len(source_pending_rule_groups),
                    "style_rule_count": self._int(summary.get("style_rule_count")),
                    "grammar_rule_count": self._int(summary.get("grammar_rule_count")),
                }
            )
            sample_count += source_sample_count
            observation_evidence_count += self._int(summary.get("observation_evidence_count"))
            rule_evidence_count += self._int(summary.get("rule_evidence_count"))
            pending_count += source_pending_count
            style_rule_count += self._int(summary.get("style_rule_count"))
            grammar_rule_count += self._int(summary.get("grammar_rule_count"))
            pending_style_count += self._int(summary.get("pending_style_count"))
            pending_grammar_count += self._int(summary.get("pending_grammar_count"))
            injected_count += self._int((summary.get("usage") or {}).get("injected_count"))
            for row in summary.get("samples") or []:
                if isinstance(row, dict):
                    samples.append({**row, **source})
            for row in summary.get("pending_samples") or []:
                if isinstance(row, dict):
                    pending_samples.append({**row, **source})
            for row in source_pending_rules:
                if isinstance(row, dict):
                    pending_rules.append({**row, **source})
            for row in source_pending_rule_groups:
                if isinstance(row, dict):
                    pending_rule_groups.append({**row, **source})
            for row in source_rules:
                if isinstance(row, dict):
                    rules.append({**row, **source})
                    if row.get("rule_type") == "semantic":
                        positive_feedback_count += self._int(row.get("positive_feedback"))
                        negative_feedback_count += self._int(row.get("negative_feedback"))
            for row in source_rule_groups:
                if isinstance(row, dict):
                    rule_groups.append({**row, **source})
            for scene in summary.get("scene_profiles") or []:
                if not isinstance(scene, dict):
                    continue
                key = self._single_line(scene.get("scene") or scene.get("label"), 32)
                if not key:
                    continue
                bucket = scene_totals.setdefault(
                    key,
                    {
                        "scene": key,
                        "label": self._single_line(scene.get("label") or key, 32),
                        "count": 0,
                    },
                )
                bucket["count"] += self._int(scene.get("count"))

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user_id, user in users.items():
            if isinstance(user, dict):
                collect("private", self._single_line(user_id, 80), user)
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if isinstance(group, dict):
                collect("group", self._single_line(group_id, 80), group)
        global_profile = data.get("_req041_persona_expression_profile")
        if isinstance(global_profile, dict):
            collect(
                "persona", "current-persona",
                {"expression_profile": global_profile, "display_name": "当前人格全局规则"},
            )

        samples.sort(key=lambda row: (-self._float(row.get("ts")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_samples.sort(key=lambda row: (-self._float(row.get("ts")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_rules.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        rules.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_rule_groups.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        rule_groups.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        sources.sort(key=lambda row: (not bool(row.get("source_active")), row.get("source_type") or "", row.get("source_name") or ""))
        scene_profiles = sorted(
            scene_totals.values(),
            key=lambda row: (-self._int(row.get("count")), row.get("label") or ""),
        )
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "mode": self._single_line(getattr(self.plugin, "expression_learning_mode", "balanced"), 20),
            "manual_review": bool(getattr(self.plugin, "enable_expression_manual_review", False)),
            "style_review": bool(getattr(self.plugin, "enable_expression_style_review", True)),
            "sample_count": sample_count,
            "observation_count": sample_count,
            "observation_evidence_count": observation_evidence_count,
            "pattern_count": len(rules),
            "rule_count": len(rules),
            "rule_group_count": len(rule_groups),
            "style_rule_count": style_rule_count,
            "grammar_rule_count": grammar_rule_count,
            "rule_evidence_count": rule_evidence_count,
            "evidence_count": rule_evidence_count,
            "pending_count": pending_count,
            "source_count": len(sources),
            "private_source_count": sum(1 for source in sources if source.get("source_type") == "private"),
            "group_source_count": sum(1 for source in sources if source.get("source_type") == "group"),
            "persona_source_count": sum(1 for source in sources if source.get("source_type") == "persona"),
            "active_source_count": sum(1 for source in sources if source.get("source_active")),
            "samples": samples,
            "pending_samples": pending_samples,
            "pending_rules": pending_rules,
            "pending_rule_count": len(pending_rules),
            "pending_rule_groups": pending_rule_groups,
            "pending_rule_group_count": len(pending_rule_groups),
            "pending_style_count": pending_style_count,
            "pending_grammar_count": pending_grammar_count,
            "rules": rules,
            "rule_groups": rule_groups,
            "scene_profiles": scene_profiles[:8],
            "sources": sources,
            "usage": {
                "injected_count": injected_count,
                "feedback_positive": positive_feedback_count,
                "feedback_negative": negative_feedback_count,
            },
        }

    @staticmethod
    def _expression_share_value_list(value: Any, *, limit: int = 8) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            item = re.sub(r"\s+", " ", str(raw or "")).strip()[:32]
            if item and item not in result:
                result.append(item)
            if len(result) >= limit:
                break
        return result

    def _expression_share_rule(self, raw: Any) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(raw, dict):
            return None, "规则格式无效"
        rule = {
            "kind": self._single_line(raw.get("kind") or raw.get("type"), 16).lower(),
            "label": self._single_line(raw.get("label"), 100),
            "situation": self._single_line(raw.get("situation"), 100),
            "pattern": self._single_line(raw.get("pattern") or raw.get("style"), 100),
            "instruction": self._single_line(raw.get("instruction"), 160),
            "keywords": self._expression_share_value_list(raw.get("keywords") or raw.get("tags")),
            "signals": self._expression_share_value_list(raw.get("signals")),
            "channels": self._expression_share_value_list(raw.get("channels")),
            "relationship_stages": self._expression_share_value_list(raw.get("relationship_stages")),
            "emotion_gates": self._expression_share_value_list(raw.get("emotion_gates")),
            "intent": self._single_line(raw.get("intent"), 32).lower() or "any",
            "avoid": self._single_line(raw.get("avoid"), 160),
            "persona_conflict": bool(raw.get("persona_conflict", False)),
        }
        validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
        if callable(validator) and not validator(rule):
            return None, "规则不是有效的可复用表达或具体语法"
        serialized = json.dumps(rule, ensure_ascii=False).lower()
        unsafe_markers = (
            "ignore previous", "ignore all previous", "system prompt", "developer message",
            "忽略之前", "忽略以上", "无视之前", "系统提示词", "开发者消息",
            "调用工具", "tool_calls", "<system", "</system",
        )
        if any(marker in serialized for marker in unsafe_markers):
            return None, "规则含有指令污染内容"
        return rule, ""

    def _expression_share_target(
        self,
        source_type: Any,
        source_id: Any,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        target_type = self._single_line(source_type, 16).lower()
        target_id = self._single_line(source_id, 80)
        root = data if isinstance(data, dict) else self.plugin.data
        collection = root.get("groups" if target_type == "group" else "users")
        if target_type not in {"private", "group"} or not target_id or not isinstance(collection, dict):
            return None
        target = collection.get(target_id)
        return target if isinstance(target, dict) else None

    def _normalize_expression_share_pack(self, raw_pack: Any) -> dict[str, Any]:
        pack = raw_pack if isinstance(raw_pack, dict) else {}
        if self._single_line(pack.get("schema"), 80) != "private-companion-expression-pack":
            raise ValueError("不是可识别的表达分享文件")
        if self._int(pack.get("version")) != 1:
            raise ValueError("表达分享文件版本不受支持")
        raw_groups = pack.get("rule_groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("分享文件中没有表达规则")
        if len(raw_groups) > 200:
            raise ValueError("单次最多导入 200 个表达规则组")
        groups: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        seen_group_signatures: set[str] = set()
        for index, raw_group in enumerate(raw_groups):
            if not isinstance(raw_group, dict):
                rejected.append({"index": index, "reason": "规则组格式无效"})
                continue
            raw_rules = raw_group.get("rules")
            if not isinstance(raw_rules, list) or not raw_rules:
                rejected.append({"index": index, "reason": "规则组为空"})
                continue
            rules: list[dict[str, Any]] = []
            kinds: set[str] = set()
            for raw_rule in raw_rules[:4]:
                normalized, reason = self._expression_share_rule(raw_rule)
                if normalized is None:
                    rejected.append({"index": index, "reason": reason})
                    continue
                kind = normalized["kind"]
                if kind in kinds:
                    rejected.append({"index": index, "reason": f"规则组含有重复的 {kind} 组件"})
                    continue
                kinds.add(kind)
                rules.append(normalized)
            if not rules:
                continue
            signature = hashlib.sha256(
                json.dumps(rules, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if signature in seen_group_signatures:
                continue
            seen_group_signatures.add(signature)
            groups.append(
                {
                    "id": f"shared-{signature[:16]}",
                    "label": self._single_line(raw_group.get("label"), 100)
                    or rules[0].get("label")
                    or rules[0].get("situation"),
                    "signature": signature,
                    "rules": rules,
                }
            )
        if not groups:
            reason = rejected[0].get("reason") if rejected else "没有可导入的有效规则"
            raise ValueError(str(reason))
        return {
            "schema": "private-companion-expression-pack",
            "version": 1,
            "title": self._single_line(pack.get("title"), 80) or "表达分享包",
            "rule_groups": groups,
            "rejected": rejected,
        }
