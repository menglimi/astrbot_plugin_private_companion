# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionReactionDiagnosticsMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 470 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiExpressionMixin）。
"""
from __future__ import annotations
from .page_api_expression_shared import Any
from .page_api_expression_shared import get_reaction_asset_library
from .page_api_expression_shared import re



class PrivateCompanionPageApiExpressionReactionDiagnosticsMixin:
    """PrivateCompanionPageApiExpressionReactionDiagnosticsMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    def _reaction_expression_runtime_summary(self, raw_data: Any) -> dict[str, Any]:
        """Aggregate experiment metrics without exposing user or image details."""
        raw_runtime = getattr(self.plugin, "_reaction_expression_runtime", None)
        runtime = raw_runtime if isinstance(raw_runtime, dict) else {}
        runtime_payload: dict[str, Any] = {}
        for key in (
            "attempts",
            "offers",
            "model_omissions",
            "local_fallbacks",
            "lookups",
            "cache_hits",
            "sent",
            "skipped",
        ):
            if key in runtime:
                runtime_payload[key] = self._int(runtime.get(key), 0, 0)
        trigger_modes = runtime.get("trigger_modes")
        if isinstance(trigger_modes, dict):
            runtime_payload["trigger_modes"] = {
                self._single_line(key, 40): self._int(value, 0, 0)
                for key, value in trigger_modes.items()
                if self._single_line(key, 40)
            }
        for key in ("last_latency_ms", "total_lookup_ms"):
            if key in runtime:
                runtime_payload[key] = round(self._float(runtime.get(key), 0.0, 0.0), 2)
        if "last_reason" in runtime:
            runtime_payload["last_reason"] = self._single_line(runtime.get("last_reason"), 120)

        users = raw_data.get("users") if isinstance(raw_data, dict) else {}
        if not isinstance(users, dict):
            users = {}
        tracked_user_count = 0
        recent_attempt_count = 0
        recent_sent_count = 0
        recent_skipped_count = 0
        positive_feedback_count = 0
        negative_feedback_count = 0
        last_activity_at = 0.0
        skip_reason_counts: dict[str, int] = {}
        for user in users.values():
            if not isinstance(user, dict):
                continue
            state = user.get("reaction_expression")
            if not isinstance(state, dict):
                continue
            tracked_user_count += 1
            outcomes = state.get("recent_outcomes")
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    recent_attempt_count += 1
                    status = self._single_line(outcome.get("status"), 32).lower()
                    if status == "sent":
                        recent_sent_count += 1
                    elif status == "skipped":
                        recent_skipped_count += 1
                        reason = self._single_line(outcome.get("reason"), 120) or "unknown"
                        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
                    last_activity_at = max(
                        last_activity_at,
                        self._float(outcome.get("at"), 0.0, 0.0),
                    )
            preference = state.get("preference")
            if isinstance(preference, dict):
                positive_feedback_count += self._int(preference.get("positive_count"), 0, 0)
                negative_feedback_count += self._int(preference.get("negative_count"), 0, 0)

        ordered_reasons = sorted(
            skip_reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:12]
        try:
            library = get_reaction_asset_library(self.plugin)
            library_summary = library.summary() if library is not None else {}
            embedding_provider_id = self._single_line(
                getattr(self.plugin, "reaction_expression_embedding_provider_id", "")
                or getattr(self.plugin, "_reaction_embedding_active_provider_id", ""),
                160,
            )
            embedding_summary = (
                library.embedding_status(embedding_provider_id)
                if library is not None and embedding_provider_id
                else {"provider_id": embedding_provider_id, "indexed": 0, "missing": 0, "total": 0}
            )
        except Exception:
            library_summary = {}
            embedding_summary = {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_reaction_expression_experiment", False)),
            "library": library_summary,
            "embedding": {
                "enabled": bool(getattr(self.plugin, "reaction_expression_embedding_enabled", False)),
                **embedding_summary,
            },
            "runtime": runtime_payload,
            "recent": {
                "tracked_user_count": tracked_user_count,
                "attempt_count": recent_attempt_count,
                "sent_count": recent_sent_count,
                "skipped_count": recent_skipped_count,
                "skip_reasons": {reason: count for reason, count in ordered_reasons},
                "last_activity_at": last_activity_at,
                "positive_feedback_count": positive_feedback_count,
                "negative_feedback_count": negative_feedback_count,
                "partial": True,
            },
        }

    def _model_diagnostics_expression_duplicate_candidates(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        analyzer = getattr(self.plugin, "_expression_rule_duplicate_analysis", None)
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        def fallback_analysis(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
            if self._single_line(left.get("kind"), 16).lower() != self._single_line(right.get("kind"), 16).lower():
                return {}
            compact = lambda value: re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"'~～…—–_-]",
                "",
                self._single_line(value, 120).lower(),
            )
            if compact(left.get("pattern") or left.get("style")) != compact(right.get("pattern") or right.get("style")):
                return {}
            return {
                "code": "same_pattern",
                "confidence": 0.9,
                "auto_merge": False,
                "reason": "同类规则使用相同表达模板",
            }

        def analyze(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
            if callable(analyzer):
                try:
                    result = analyzer(left, right)
                    if isinstance(result, dict):
                        return result
                except Exception:
                    pass
            return fallback_analysis(left, right)

        def inspect_source(
            *,
            source_type: str,
            source_id: str,
            source_name: str,
            profile: Any,
        ) -> None:
            if not isinstance(profile, dict):
                return
            rows: list[dict[str, Any]] = []
            for storage_key, storage_label in (("learned_rules", "已启用"), ("pending_rules", "待审核")):
                values = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                for raw in values:
                    if isinstance(raw, dict):
                        rows.append({**raw, "_storage_key": storage_key, "_storage_label": storage_label})
            duplicate_count = 0
            duplicate_families: set[str] = set()
            parents = list(range(len(rows)))
            edges: list[tuple[int, int, dict[str, Any]]] = []

            def find(index: int) -> int:
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            def union(left_index: int, right_index: int) -> None:
                left_root = find(left_index)
                right_root = find(right_index)
                if left_root != right_root:
                    parents[right_root] = left_root

            for index, left in enumerate(rows):
                for right_index in range(index + 1, len(rows)):
                    right = rows[right_index]
                    left_family = self._single_line(left.get("family_id"), 100)
                    right_family = self._single_line(right.get("family_id"), 100)
                    if left_family and left_family == right_family:
                        continue
                    analysis = analyze(left, right)
                    confidence = self._float(analysis.get("confidence"))
                    if confidence < 0.78:
                        continue
                    left_id = self._single_line(left.get("id"), 100)
                    right_id = self._single_line(right.get("id"), 100)
                    pair_key = (
                        source_type,
                        source_id,
                        min(left_id, right_id),
                        max(left_id, right_id),
                    )
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    union(index, right_index)
                    edges.append((index, right_index, analysis))

            component_members: dict[int, set[int]] = {}
            involved = {value for left_index, right_index, _ in edges for value in (left_index, right_index)}
            for index in involved:
                component_members.setdefault(find(index), set()).add(index)
            for members in component_members.values():
                component_edges = [
                    analysis
                    for left_index, right_index, analysis in edges
                    if left_index in members and right_index in members
                ]
                if not component_edges:
                    continue
                duplicate_count += 1
                member_rows = [rows[index] for index in sorted(members)]
                family_ids = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("family_id"), 100))
                ))
                duplicate_families.update(family_ids)
                patterns = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("pattern") or item.get("style"), 80))
                ))
                storage_text = " / ".join(dict.fromkeys(
                    self._single_line(item.get("_storage_label"), 20)
                    for item in member_rows
                    if self._single_line(item.get("_storage_label"), 20)
                ))
                auto_merge = all(bool(item.get("auto_merge")) for item in component_edges)
                action_text = "可保守合并证据与适用边界" if auto_merge else "应人工确认是否保留这些情境"
                reasons = list(dict.fromkeys(
                    self._single_line(item.get("reason"), 120)
                    for item in component_edges
                    if self._single_line(item.get("reason"), 120)
                ))
                rule_ids = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("id"), 100))
                ))
                confidence = max((self._float(item.get("confidence")) for item in component_edges), default=0.0)
                pattern_text = " / ".join(patterns[:4])
                if len(patterns) > 4:
                    pattern_text += f" 等 {len(patterns)} 种模板"
                if len(member_rows) > len(patterns):
                    pattern_text += f"（共 {len(member_rows)} 条规则）"
                candidates.append({
                    "category": "duplicate_rule",
                    "source_type": source_type,
                    "source_id": source_id,
                    "user_id": source_id,
                    "name": source_name,
                    "text": pattern_text,
                    "reason": f"{'；'.join(reasons[:2]) or '疑似近义规则'}；{action_text}",
                    "confidence": round(confidence, 3),
                    "storage": storage_text,
                    "rule_ids": rule_ids,
                    "family_ids": family_ids,
                    "auto_merge": auto_merge,
                })
                if len(candidates) >= 24:
                    return
            learned = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            rule_limit = max(1, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60)
            if duplicate_count and len(learned) >= rule_limit:
                candidates.append({
                    "category": "rule_budget",
                    "source_type": source_type,
                    "source_id": source_id,
                    "user_id": source_id,
                    "name": source_name,
                    "text": f"已启用 {len(learned)}/{rule_limit} 条，重复候选涉及 {len(duplicate_families)} 个规则组",
                    "reason": "表达规则已占满来源预算，近义规则会挤掉其他有效表达",
                    "confidence": 0.98,
                    "auto_merge": False,
                })

        for collection_key, source_type in (("users", "private"), ("groups", "group")):
            collection = data.get(collection_key) if isinstance(data.get(collection_key), dict) else {}
            for source_id, owner in collection.items():
                if not isinstance(owner, dict):
                    continue
                name = self._single_line(
                    owner.get("nickname") or owner.get("name") or owner.get("group_name") or source_id,
                    40,
                )
                inspect_source(
                    source_type=source_type,
                    source_id=self._single_line(source_id, 80),
                    source_name=name,
                    profile=owner.get("expression_profile"),
                )
                if len(candidates) >= 24:
                    return candidates[:24]

        runtime = data.get("expression_voice_profile") if isinstance(data.get("expression_voice_profile"), dict) else {}
        runtime_rules = runtime.get("learned_rules") if isinstance(runtime.get("learned_rules"), list) else []
        runtime_limit = max(1, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60)
        runtime_parents = list(range(len(runtime_rules)))
        runtime_involved: set[int] = set()

        def runtime_find(index: int) -> int:
            while runtime_parents[index] != index:
                runtime_parents[index] = runtime_parents[runtime_parents[index]]
                index = runtime_parents[index]
            return index

        def runtime_union(left_index: int, right_index: int) -> None:
            left_root = runtime_find(left_index)
            right_root = runtime_find(right_index)
            if left_root != right_root:
                runtime_parents[right_root] = left_root

        for index, left in enumerate(runtime_rules):
            if not isinstance(left, dict):
                continue
            for right_index in range(index + 1, len(runtime_rules)):
                right = runtime_rules[right_index]
                if not isinstance(right, dict):
                    continue
                analysis = analyze(left, right)
                if self._float(analysis.get("confidence")) >= 0.9:
                    runtime_union(index, right_index)
                    runtime_involved.update((index, right_index))
        runtime_duplicates = len({runtime_find(index) for index in runtime_involved})
        if runtime_duplicates and len(runtime_rules) >= runtime_limit:
            candidates.append({
                "category": "runtime_budget",
                "source_type": "runtime",
                "source_id": "expression_voice_profile",
                "user_id": "expression_voice_profile",
                "name": "运行时表达池",
                "text": f"当前 {len(runtime_rules)}/{runtime_limit} 条，含 {runtime_duplicates} 组高置信重复候选",
                "reason": "运行时规则池已达上限，重复项正在占用召回槽位",
                "confidence": 0.99,
                "auto_merge": False,
            })
        return candidates[:24]

    def _model_diagnostics_expression_candidates(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "commit ", "diff ")
        model_markers = ("<pc_tts", "</pc_tts>", "[[PCTTS:", "send_message_to_user", "assistant", "system prompt", "提示词")
        political_markers = (
            "习近平",
            "共产党",
            "中共",
            "六四",
            "天安门",
            "法轮功",
            "台独",
            "港独",
            "藏独",
            "疆独",
            "民主运动",
            "政治敏感",
        )

        def reason_for(text: str) -> str:
            lowered = text.lower()
            if any(marker.lower() in lowered for marker in log_markers):
                return "像日志/代码/报错内容，不该作为表达习惯"
            if any(marker.lower() in lowered for marker in model_markers):
                return "像模型输出格式或内部标签，不该作为表达习惯"
            if any(marker in text for marker in political_markers):
                return "含政治敏感内容，不适合进入表达学习"
            if text.count("…") + text.count("～") + text.count("~") >= 5:
                return "标点留白过多，容易污染表达节奏"
            if text.count("？") + text.count("?") + text.count("！") + text.count("!") >= 6:
                return "疑问/感叹标点过多，容易污染表达节奏"
            return ""

        candidates = self._model_diagnostics_expression_duplicate_candidates(data)
        seen_pollution: set[tuple[str, str, str]] = set()
        for collection_key, source_type in (("users", "private"), ("groups", "group")):
            collection = data.get(collection_key) if isinstance(data.get(collection_key), dict) else {}
            for source_id, owner in collection.items():
                if not isinstance(owner, dict):
                    continue
                name = self._single_line(
                    owner.get("nickname") or owner.get("name") or owner.get("group_name") or source_id,
                    40,
                )
                profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
                samples = [
                    *(profile.get("samples") if isinstance(profile.get("samples"), list) else []),
                    *(profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []),
                ]
                for raw in samples[:48]:
                    if not isinstance(raw, dict):
                        continue
                    text = self._single_line(raw.get("text") or raw.get("phrase") or raw.get("ending"), 120)
                    marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
                    pause_total = sum(self._int(marks.get(mark)) for mark in ("…", "～", "~"))
                    strong_total = sum(self._int(marks.get(mark)) for mark in ("？", "?", "！", "!"))
                    direct_reason = ""
                    if pause_total >= 5:
                        direct_reason = "标点留白过多，容易污染表达节奏"
                    elif strong_total >= 6:
                        direct_reason = "疑问/感叹标点过多，容易污染表达节奏"
                    if not text:
                        text = " ".join(
                            f"{mark}×{self._int(count)}"
                            for mark, count in marks.items()
                            if self._int(count) > 0
                        )
                    reason = direct_reason or reason_for(text)
                    key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                    if not reason or key in seen_pollution:
                        continue
                    seen_pollution.add(key)
                    candidates.append({
                        "category": "pollution",
                        "source_type": source_type,
                        "source_id": self._single_line(source_id, 80),
                        "user_id": self._single_line(source_id, 80),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    })
                    if len(candidates) >= 32:
                        return candidates
                phrase_values = [
                    *(profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []),
                    *(profile.get("endings") if isinstance(profile.get("endings"), list) else []),
                ]
                for raw_text in phrase_values[:36]:
                    text = self._single_line(raw_text, 120)
                    reason = reason_for(text)
                    key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                    if not reason or key in seen_pollution:
                        continue
                    seen_pollution.add(key)
                    candidates.append({
                        "category": "pollution",
                        "source_type": source_type,
                        "source_id": self._single_line(source_id, 80),
                        "user_id": self._single_line(source_id, 80),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    })
                    if len(candidates) >= 32:
                        return candidates
                for storage_key in ("learned_rules", "pending_rules"):
                    rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                    for raw in rules[:60]:
                        if not isinstance(raw, dict):
                            continue
                        text = "｜".join(filter(None, (
                            self._single_line(raw.get("situation"), 80),
                            self._single_line(raw.get("pattern") or raw.get("style"), 100),
                            self._single_line(raw.get("instruction"), 120),
                        )))
                        reason = reason_for(text)
                        key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                        if not reason or key in seen_pollution:
                            continue
                        seen_pollution.add(key)
                        candidates.append({
                            "category": "pollution",
                            "source_type": source_type,
                            "source_id": self._single_line(source_id, 80),
                            "user_id": self._single_line(source_id, 80),
                            "name": name,
                            "text": text,
                            "reason": reason,
                        })
                        if len(candidates) >= 32:
                            return candidates
        return candidates
