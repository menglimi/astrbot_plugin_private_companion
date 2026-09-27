# -*- coding: utf-8 -*-
"""表达规则注射反馈与决策。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 598 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import re
from .companion_interaction_expression import build_expression_decision, current_interaction_projection
from .expression_scope_ownership import bind_expression_item
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from datetime import datetime
from typing import Any



class UserMemoryExpressionFeedbackMixin:
    """表达规则注射反馈与决策（从 UserMemoryMixin 拆出）。"""


    def _format_expression_profile_for_prompt(
        self,
        user: dict[str, Any],
        *,
        inbound_text: str = "",
        include_semantic: bool = True,
    ) -> str:
        profile = user.get("expression_profile")
        if not isinstance(profile, dict):
            return "暂无已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
        if not include_semantic or not learned_rules:
            return "暂无已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        semantic_matches = self._select_learned_expression_rules(
            learned_rules,
            hint=inbound_text,
            limit=2,
        )
        if not semantic_matches:
            return "暂无匹配的已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        lines: list[str] = []
        for rule in semantic_matches:
            line = self._format_expression_rule_bundle_line(rule)
            if line:
                lines.append(line)
        if lines:
            lines.append(
                "只使用已经审核通过的规则；观察素材、支持片段、句长和标点统计不得直接影响回复。"
                "工具与事实、安全边界、AstrBot 人格、当前关系和情绪始终优先。"
                "句尾括号或颜文字后缀必须与所属句保持同一行；规则要求括号前无标点时，不得补逗号或其他标点。"
            )
        return "\n".join(lines) if lines else "暂无匹配的已审核表达规则。保持 AstrBot 默认人格的自然表达。"

    def _expression_visible_signals_in_reply(
        self,
        response_text: Any,
        rule_details: dict[str, Any],
    ) -> list[str]:
        cleaned = _single_line(_strip_internal_message_blocks(response_text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 500)
        if not cleaned or not isinstance(rule_details, dict):
            return []
        expected = {
            _single_line(item, 32)
            for item in rule_details.get("signals", [])
            if _single_line(item, 32)
        } if isinstance(rule_details.get("signals"), list) else set()
        if not expected:
            return []
        actual = set(self._expression_style_features_from_text(cleaned))
        if "short" in expected:
            sentence_count = len(re.findall(r"[^。！？!?\n]+[。！？!?]?", cleaned))
            if len(cleaned) <= 72 and sentence_count <= 2:
                actual.add("short")
        return [signal for signal in rule_details.get("signals", []) if signal in expected and signal in actual]

    def _record_expression_rule_injection(
        self,
        user: dict[str, Any],
        rule_details: dict[str, Any] | None,
        response_text: Any,
        *,
        semantic_rules: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        local_rule = rule_details if isinstance(rule_details, dict) and rule_details.get("id") else {}
        selected_semantic_rules = [
            dict(item)
            for item in (semantic_rules if isinstance(semantic_rules, list) else [])
            if isinstance(item, dict) and item.get("id")
        ][:2]
        if not isinstance(user, dict) or (not local_rule and not selected_semantic_rules):
            return {}
        current_channel = _single_line((context or {}).get("channel"), 24).lower()
        source_kind = "group" if current_channel == "group" or user.get("group_id") else "private"
        updated_sections = {"groups" if source_kind == "group" else "users"}
        scope_managed, scope_context = self._expression_formal_scope_for_owner(
            user, source_kind=source_kind,
        )
        if scope_managed and scope_context is None:
            return {}
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        if scope_context is not None:
            try:
                profile = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=False,
                )
            except (TypeError, ValueError):
                return {}
            user["expression_profile"] = profile
        scoped_changed: dict[int, tuple[dict[str, Any], Any]] = {}
        if scope_context is not None:
            scoped_changed[id(user)] = (user, scope_context)
        usage = profile.setdefault("usage", {})
        if not isinstance(usage, dict):
            usage = {}
            profile["usage"] = usage
        visible_signals = self._expression_visible_signals_in_reply(response_text, local_rule)
        injected_count = _safe_int(usage.get("injected_count"), 0, 0) + 1
        visible_match_count = _safe_int(usage.get("visible_match_count"), 0, 0) + (1 if visible_signals else 0)
        now = _now_ts()
        primary_rule = local_rule or selected_semantic_rules[0]
        semantic_primary = selected_semantic_rules[0] if selected_semantic_rules else {}
        last = {
            "ts": now,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "rule_id": _single_line(primary_rule.get("id"), 100),
            "scene": _single_line(primary_rule.get("scene") or primary_rule.get("intent") or primary_rule.get("kind"), 32),
            "label": _single_line(primary_rule.get("label") or primary_rule.get("situation"), 80),
            "instruction": _single_line(primary_rule.get("instruction"), 260),
            "evidence_count": _safe_int(primary_rule.get("evidence_count"), 0, 0),
            "confidence": max(
                0.0,
                min(
                    1.0,
                    _safe_float(
                        primary_rule.get("confidence"),
                        min(0.98, 0.52 + min(8, _safe_int(primary_rule.get("evidence_count"), 0, 0)) * 0.055),
                    ),
                ),
            ),
            "expected_signals": [
                _single_line(item, 32)
                for item in local_rule.get("signals", [])
                if _single_line(item, 32)
            ][:4] if isinstance(local_rule.get("signals"), list) else [],
            "visible_signals": visible_signals[:4],
            "rule_type": "heuristic" if local_rule else "semantic",
            "semantic_rule_count": len(selected_semantic_rules),
            "channel": _single_line((context or {}).get("channel"), 24),
            "relationship_stage": _single_line((context or {}).get("relationship_stage"), 24),
            "emotion_gate": _single_line((context or {}).get("emotion_gate"), 24),
            "intent": _single_line((context or {}).get("intent"), 32),
        }
        if local_rule and semantic_primary:
            last["semantic_rule_id"] = _single_line(semantic_primary.get("id"), 100)
            last["semantic_label"] = _single_line(semantic_primary.get("situation"), 80)
        usage.update(
            {
                "injected_count": injected_count,
                "visible_match_count": visible_match_count,
                "last_injection": last,
                "updated_at": last["at"],
            }
        )
        if selected_semantic_rules:
            usage["semantic_injected_count"] = _safe_int(usage.get("semantic_injected_count"), 0, 0) + 1
            feedback_rules: list[dict[str, Any]] = []
            seen_refs: set[tuple[str, str, str]] = set()
            for selected in selected_semantic_rules:
                refs = selected.get("source_refs") if isinstance(selected.get("source_refs"), list) else []
                compact_refs: list[dict[str, str]] = []
                for raw_ref in refs:
                    if not isinstance(raw_ref, dict):
                        continue
                    ref = {
                        "source_kind": _single_line(raw_ref.get("source_kind"), 16).lower(),
                        "source_id": _single_line(raw_ref.get("source_id"), 80),
                        "rule_id": _single_line(raw_ref.get("rule_id"), 40),
                    }
                    key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
                    if not all(key) or key in seen_refs:
                        continue
                    seen_refs.add(key)
                    compact_refs.append(ref)
                    collection_key = "groups" if ref["source_kind"] == "group" else "users"
                    collection = self.data.get(collection_key) if isinstance(getattr(self, "data", None), dict) else {}
                    source_owner = collection.get(ref["source_id"]) if isinstance(collection, dict) else None
                    source_profile = source_owner.get("expression_profile") if isinstance(source_owner, dict) else None
                    source_rules = source_profile.get("learned_rules") if isinstance(source_profile, dict) else None
                    if not isinstance(source_rules, list):
                        continue
                    source_scope_context = None
                    if scope_managed:
                        source_managed, source_scope_context = self._expression_formal_scope_for_owner(
                            source_owner,
                            source_kind="group" if ref["source_kind"] == "group" else "private",
                        )
                        if (
                            not source_managed
                            or source_scope_context is None
                            or source_scope_context.cache_scope() != scope_context.cache_scope()
                        ):
                            continue
                        try:
                            source_profile = self._expression_bind_profile_scope(
                                source_profile, source_scope_context, bump_revision=False,
                            )
                        except (TypeError, ValueError):
                            continue
                        source_owner["expression_profile"] = source_profile
                        source_rules = source_profile.get("learned_rules")
                        updated_sections.add(collection_key)
                        scoped_changed[id(source_owner)] = (source_owner, source_scope_context)
                    for source_rule in source_rules:
                        if not isinstance(source_rule, dict) or _single_line(source_rule.get("id"), 40) != ref["rule_id"]:
                            continue
                        source_rule["use_count"] = _safe_int(source_rule.get("use_count"), 0, 0) + 1
                        source_rule["last_used_ts"] = now
                        binding = source_rule.get("scope_binding") if isinstance(source_rule.get("scope_binding"), dict) else None
                        if binding is not None:
                            binding["revision"] = max(1, _safe_int(binding.get("revision"), 1, 1) + 1)
                        updated_sections.add(collection_key)
                        break
                if compact_refs:
                    feedback_rules.append(
                        {
                            "id": _single_line(selected.get("id"), 100),
                            "situation": _single_line(selected.get("situation"), 80),
                            "source_refs": compact_refs,
                        }
                    )
            feedback_channel = _single_line((context or {}).get("channel"), 24).lower()
            if feedback_rules and feedback_channel in {"private", "proactive", "group"}:
                profile["pending_semantic_feedback"] = {
                    "ts": now,
                    "channel": feedback_channel,
                    "seen_after": 0,
                    "rules": feedback_rules,
                }
        profile["last_injected_at"] = last["at"]
        for changed_owner, changed_context in scoped_changed.values():
            changed_profile = changed_owner.get("expression_profile")
            if isinstance(changed_profile, dict):
                changed_owner["expression_profile"] = self._expression_bind_profile_scope(
                    changed_profile, changed_context, bump_revision=True,
                )
        result = dict(last)
        result["updated_sections"] = sorted(updated_sections)
        return result

    def _record_staged_expression_rule_injection(
        self,
        owner: dict[str, Any],
        response_text: Any,
        *,
        channel: str,
    ) -> dict[str, Any]:
        if not isinstance(owner, dict):
            return {}
        profile = owner.get("expression_profile")
        staged = profile.pop("staged_semantic_selection", None) if isinstance(profile, dict) else None
        if not isinstance(staged, dict) or _now_ts() - _safe_float(staged.get("ts"), 0.0) > 15 * 60:
            return {}
        rules = staged.get("rules") if isinstance(staged.get("rules"), list) else []
        context = dict(staged.get("context") or {}) if isinstance(staged.get("context"), dict) else {}
        context["channel"] = _single_line(channel, 24).lower()
        return self._record_expression_rule_injection(
            owner,
            {},
            response_text,
            semantic_rules=rules,
            context=context,
        )

    @staticmethod
    def _classify_expression_rule_feedback(text: Any, *, channel: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        negative = bool(
            re.search(
                r"(别这么说|别这样说|别学|不像你|正常说话|好尬|尴尬|油腻|别夹|别装|"
                r"这个语气.{0,6}(?:怪|烦|恶心|不喜欢)|你怎么说话|说话怎么.{0,6}怪|闭嘴|吵死)",
                cleaned,
                re.IGNORECASE,
            )
        )
        if negative:
            return "negative"
        if channel == "group" and re.search(r"(哈哈|笑死|草|绷|乐|hhh|可以|确实|对啊)", cleaned, re.IGNORECASE):
            return "positive"
        positive = bool(
            re.search(
                r"(?:(?:这样说|这个语气|你这么说|你这样说|这个说法).{0,8}(?:好|喜欢|自然|舒服|可爱|对味))|"
                r"(?:(?:好喜欢|很喜欢).{0,8}(?:你这样|这个语气|你这么说))",
                cleaned,
                re.IGNORECASE,
            )
        )
        return "positive" if positive else ""

    def _apply_expression_rule_feedback(
        self,
        owner: dict[str, Any],
        text: Any,
        *,
        channel: str = "private",
    ) -> dict[str, Any]:
        if not isinstance(owner, dict):
            return {}
        profile = owner.get("expression_profile")
        pending = profile.get("pending_semantic_feedback") if isinstance(profile, dict) else None
        if not isinstance(pending, dict) or not pending:
            return {}
        current_channel = _single_line(channel, 24).lower()
        source_kind = "group" if current_channel == "group" or owner.get("group_id") else "private"
        scope_managed, scope_context = self._expression_formal_scope_for_owner(
            owner, source_kind=source_kind,
        )
        if scope_managed and scope_context is None:
            return {}
        if scope_context is not None:
            try:
                profile = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=False,
                )
            except (TypeError, ValueError):
                return {}
            owner["expression_profile"] = profile
            pending = profile.get("pending_semantic_feedback")
        scoped_changed: dict[int, tuple[dict[str, Any], Any]] = {}
        if scope_context is not None:
            scoped_changed[id(owner)] = (owner, scope_context)
        now = _now_ts()
        if now - _safe_float(pending.get("ts"), 0.0) > 10 * 60:
            profile.pop("pending_semantic_feedback", None)
            if scope_context is not None:
                owner["expression_profile"] = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=True,
                )
            return {}
        pending_channel = _single_line(pending.get("channel"), 24).lower()
        if pending_channel == "group" and current_channel != "group":
            return {}
        if pending_channel in {"private", "proactive"} and current_channel != "private":
            return {}

        signal = self._classify_expression_rule_feedback(text, channel=current_channel)
        pending["seen_after"] = _safe_int(pending.get("seen_after"), 0, 0) + 1
        max_unmatched = 3 if current_channel == "group" else 1
        if not signal:
            if _safe_int(pending.get("seen_after"), 0, 0) >= max_unmatched:
                profile.pop("pending_semantic_feedback", None)
            if scope_context is not None:
                owner["expression_profile"] = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=True,
                )
            return {}

        feedback_field = "positive_feedback" if signal == "positive" else "negative_feedback"
        updated = 0
        demoted = 0
        updated_sections: set[str] = set()
        processed: set[tuple[str, str, str]] = set()
        for pending_rule in pending.get("rules", []) if isinstance(pending.get("rules"), list) else []:
            if not isinstance(pending_rule, dict):
                continue
            refs = pending_rule.get("source_refs") if isinstance(pending_rule.get("source_refs"), list) else []
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                source_kind = _single_line(ref.get("source_kind"), 16).lower()
                source_id = _single_line(ref.get("source_id"), 80)
                rule_id = _single_line(ref.get("rule_id"), 40)
                key = (source_kind, source_id, rule_id)
                if not all(key) or key in processed:
                    continue
                processed.add(key)
                collection_key = "groups" if source_kind == "group" else "users"
                collection = self.data.get(collection_key) if isinstance(getattr(self, "data", None), dict) else {}
                source_owner = collection.get(source_id) if isinstance(collection, dict) else None
                source_profile = source_owner.get("expression_profile") if isinstance(source_owner, dict) else None
                learned_rules = source_profile.get("learned_rules") if isinstance(source_profile, dict) else None
                if not isinstance(learned_rules, list):
                    continue
                source_scope_context = None
                if scope_managed:
                    source_managed, source_scope_context = self._expression_formal_scope_for_owner(
                        source_owner,
                        source_kind="group" if source_kind == "group" else "private",
                    )
                    if (
                        not source_managed
                        or source_scope_context is None
                        or source_scope_context.cache_scope() != scope_context.cache_scope()
                    ):
                        continue
                    try:
                        source_profile = self._expression_bind_profile_scope(
                            source_profile, source_scope_context, bump_revision=False,
                        )
                    except (TypeError, ValueError):
                        continue
                    source_owner["expression_profile"] = source_profile
                    learned_rules = source_profile.get("learned_rules")
                    scoped_changed[id(source_owner)] = (source_owner, source_scope_context)
                for index, source_rule in enumerate(list(learned_rules)):
                    if not isinstance(source_rule, dict) or _single_line(source_rule.get("id"), 40) != rule_id:
                        continue
                    source_rule[feedback_field] = _safe_int(source_rule.get(feedback_field), 0, 0) + 1
                    source_rule["last_feedback"] = signal
                    source_rule["last_feedback_ts"] = now
                    updated += 1
                    updated_sections.add(collection_key)
                    if (
                        signal == "negative"
                        and _safe_int(source_rule.get("negative_feedback"), 0, 0) >= 2
                        and _safe_int(source_rule.get("negative_feedback"), 0, 0)
                        > _safe_int(source_rule.get("positive_feedback"), 0, 0)
                    ):
                        needs_review = dict(source_rule)
                        needs_review["review_status"] = "needs_review"
                        needs_review["review_reason"] = "连续收到 2 次明确负向表达反馈，已自动停用"
                        needs_review["reviewed_back_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        if source_scope_context is not None:
                            needs_review = bind_expression_item(
                                needs_review, source_scope_context,
                                approval_state="pending", bump_revision=True,
                            )
                        learned_rules.pop(index)
                        pending_rules = source_profile.get("pending_rules") if isinstance(source_profile.get("pending_rules"), list) else []
                        pending_rules = [
                            item
                            for item in pending_rules
                            if not isinstance(item, dict) or _single_line(item.get("id"), 40) != rule_id
                        ]
                        pending_rules.insert(0, needs_review)
                        source_profile["learned_rules"] = learned_rules
                        source_profile["pending_rules"] = pending_rules[
                            : runtime_persona_setting(self, "max_learned_expression_items", 60)
                        ]
                        demoted += 1
                    elif source_scope_context is not None:
                        binding = source_rule.get("scope_binding") if isinstance(source_rule.get("scope_binding"), dict) else None
                        if binding is not None:
                            binding["revision"] = max(1, _safe_int(binding.get("revision"), 1, 1) + 1)
                    source_profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    break

        usage = profile.setdefault("usage", {})
        if isinstance(usage, dict):
            counter = "feedback_positive" if signal == "positive" else "feedback_negative"
            usage[counter] = _safe_int(usage.get(counter), 0, 0) + 1
            usage["last_feedback"] = {
                "signal": signal,
                "ts": now,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "updated_rules": updated,
                "demoted_rules": demoted,
            }
        profile.pop("pending_semantic_feedback", None)
        for changed_owner, changed_context in scoped_changed.values():
            changed_profile = changed_owner.get("expression_profile")
            if isinstance(changed_profile, dict):
                changed_owner["expression_profile"] = self._expression_bind_profile_scope(
                    changed_profile, changed_context, bump_revision=True,
                )
        if updated:
            self._refresh_expression_voice_profile()
        return {
            "signal": signal,
            "updated_rules": updated,
            "demoted_rules": demoted,
            "updated_sections": sorted(updated_sections),
        }

    def _build_expression_decision_for_user(
        self,
        user: dict[str, Any],
        *,
        proactive_candidate: dict[str, Any] | None = None,
        safety_constraints: dict[str, Any] | None = None,
        passive_reengagement: bool = False,
        bot_state: dict[str, Any] | None = None,
        schedule: dict[str, Any] | None = None,
        message_intent: dict[str, Any] | None = None,
        content_policy: dict[str, Any] | None = None,
        channel_scope: str = "private",
        now: float | None = None,
        _authoritative_relationship_view: bool = False,
    ):
        if not bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", False)):
            # Keep the caller contract stable without reading or projecting
            # archived affinity data when the master switch is off.
            return build_expression_decision({})
        view_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        if (
            not _authoritative_relationship_view
            and callable(view_getter)
            and channel_scope != "group"
        ):
            user = view_getter(user, source="expression_decision")
        decision_now = _now_ts() if now is None else _safe_float(now, _now_ts(), 0)
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        relationship_mode = str(user.get("relationship_mode") or "normal")
        is_owner_group = channel_scope == "group" and role == "owner"
        project_relationship = is_owner_group and bool(
            runtime_persona_setting(self, "owner_group_relationship_projection", True)
        )
        project_interaction = is_owner_group and bool(
            runtime_persona_setting(self, "owner_group_interaction_projection", True)
        )
        if role == "owner" and relationship_mode == "owner_exclusive" and not project_relationship:
            relationship_baseline = {
                "stage_key": "owner_exclusive",
                "tone": _single_line(
                    runtime_persona_setting(self, "owner_exclusive_tone", "温暖、亲近、稳定"),
                    120,
                ),
                "address_level": _single_line(
                    runtime_persona_setting(self, "owner_exclusive_address_style", "优先使用已确认的专属称呼"),
                    100,
                ),
                "proactive_care_limit": _safe_int(
                    runtime_persona_setting(self, "owner_exclusive_proactive_limit", 6),
                    6,
                    0,
                    30,
                ),
                "soft_behaviors": {
                    "allow_playful_jokes": True,
                    "allow_followup": True,
                    "allow_memory_mention": True,
                    "allow_daily_care": True,
                },
            }
        else:
            policy = (
                runtime_persona_setting(self, "relationship_stage_policy", None)
                if bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", False))
                else None
            )
            stage_projection = relationship_stage_for_score(
                user.get("relationship_score", 0),
                policy,
                previous_stage_key=user.get("relationship_phase_key", ""),
            )
            stage = stage_projection["phase"]
            user["relationship_phase_key"] = stage.get("key", "acquaintance")
            relationship_baseline = {
                "stage_key": stage.get("key"),
                "tone": stage.get("tone"),
                "address_level": stage.get("address_level"),
                "proactive_care_limit": stage.get("proactive_care_limit"),
                "soft_behaviors": {
                    "allow_playful_jokes": bool(stage.get("allow_playful_jokes")),
                    "allow_followup": bool(stage.get("allow_followup")),
                    "allow_memory_mention": bool(stage.get("allow_memory_mention")),
                    "allow_daily_care": bool(stage.get("allow_daily_care")),
                },
            }
        interaction_source = user.get("current_interaction")
        # ``relationship_state`` is retained only for historical diagnostics.
        # It must not become a second authority for the unified expression.
        legacy_cooldown_until = 0
        has_explicit_interaction = bool(
            isinstance(interaction_source, dict)
            and _single_line(
                interaction_source.get("expression_band")
                or interaction_source.get("band")
                or interaction_source.get("state")
                or interaction_source.get("mode"),
                24,
            )
        )
        interaction = current_interaction_projection(
            interaction_source,
            relationship_role="friend" if project_interaction else role,
            relationship_mode="normal" if project_relationship else relationship_mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=decision_now,
        )
        contact = user.get("contact_preference")
        boundary = bool(
            (isinstance(contact, dict) and (contact.get("no_contact") or contact.get("backoff") or contact.get("active")))
            or str(contact or "").strip().lower() in {"no_contact", "backoff", "avoid", "stop"}
        )
        safety = dict(safety_constraints or {})
        if boundary:
            safety["contact_boundary"] = True
            if passive_reengagement:
                safety["passive_reengagement"] = True
        manual_override = interaction if interaction.get("manual_override") else None
        proactive_input = dict(proactive_candidate or {})
        if proactive_input or legacy_cooldown_until > 0:
            proactive_input.setdefault("current_ts", decision_now)
            if legacy_cooldown_until > decision_now:
                proactive_input.setdefault("cooldown_until", legacy_cooldown_until)
        return build_expression_decision(
            {
                "relationship_score": user.get("relationship_score", 0),
                "relationship_role": "friend" if project_interaction else role,
                "relationship_mode": "normal" if project_relationship else relationship_mode,
                "relationship_baseline": relationship_baseline,
                "relationship_stage": relationship_baseline.get("stage_key"),
                "normal_interaction_band_cap": runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
                "current_interaction": interaction,
                "administrator_override": manual_override,
                "bot_state": bot_state or {"energy": user.get("bot_energy", 70)},
                "schedule": schedule or {},
                "message_intent": message_intent if isinstance(message_intent, dict) else {},
                "proactive_candidate": proactive_input,
                "safety_constraints": safety,
                "content_policy": content_policy or {},
            }
        )
