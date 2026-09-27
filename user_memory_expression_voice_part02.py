# -*- coding: utf-8 -*-
"""UserMemoryExpressionVoicePart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_voice.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 470 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionVoiceMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .expression_scope_ownership import bind_expression_item, bind_expression_profile
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .scoped_runtime_view import scoped_approved_expression_rules
from .user_memory_render_shared import _render_conversation_section_labeled
from copy import deepcopy
from datetime import datetime
from typing import Any



class UserMemoryExpressionVoicePart02Mixin:
    """UserMemoryExpressionVoicePart02Mixin（从 UserMemoryExpressionVoiceMixin 拆出）。"""


    def _expression_voice_selection(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_expression_learning", True)):
            return {"prompt": "", "rules": [], "context": {}}
        if scope in {"private", "proactive"} and not self._expression_private_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        if scope == "group" and not self._expression_group_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        scoped_rules = scoped_approved_expression_rules(context_owner)
        profile = self._expression_voice_profile() if scoped_rules is None else {}
        context = self._expression_companion_context(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        learned_rules = self._select_learned_expression_rules(
            profile.get("learned_rules") if scoped_rules is None else scoped_rules,
            hint=inbound_text,
            limit=2,
            context=context,
        )
        if not learned_rules:
            return {"prompt": "", "rules": [], "context": context}
        guidance: list[str] = []
        for rule in learned_rules:
            line = self._format_expression_rule_bundle_line(rule)
            if line:
                guidance.append(line)
        if not guidance:
            return {"prompt": "", "rules": [], "context": context}
        scope_label = {"private": "私聊回复", "proactive": "私聊主动消息", "group": "群聊回复"}.get(scope, "当前回复")
        evidence_count = sum(_safe_int(item.get("evidence_count"), 0, 0) for item in learned_rules)
        source_label = (
            "当前私聊/群聊命名空间内"
            if scoped_rules is not None else "已允许的私聊/群聊来源"
        )
        body = (
            f"这些规则只来自{source_label}，共 {evidence_count} 条支持证据。当前用于{scope_label}：\n"
            + "\n".join(guidance[:4])
            + "\n执行优先级：工具与事实结果 > 安全及能力边界 > AstrBot 人格 > 当前关系与情绪 > 已审核表达规则 > 装饰性口癖/标点。"
            + "任何冲突都舍弃较低优先级；工具失败时绝不能声称已发送、已完成或已成功。"
            + "情境表达可以改写或替换占位符，语法习惯只控制句法；不要机械复读。"
            + "句尾括号或颜文字后缀必须与所属句保持同一行；规则要求括号前无标点时，不得补逗号或其他标点。"
            + "不得带出来源身份、称呼、账号、关系、事实、秘密或支持片段。"
        )
        section = prompt_section(
            key="expression.voice",
            title="已审核的表达学习规则",
            source="expression",
            content=body,
        )
        return {
            "prompt": _render_conversation_section_labeled(section),
            "section": section,
            "rules": [dict(item) for item in learned_rules],
            "context": context,
            "selection_scope": "current_namespace" if scoped_rules is not None else "legacy_aggregate",
        }

    def _format_expression_voice_for_prompt(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
        stage_owner: dict[str, Any] | None = None,
    ) -> str:
        section = self._format_expression_voice_prompt_section(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
            stage_owner=stage_owner,
        )
        return _render_conversation_section_labeled(section)

    def _format_expression_voice_prompt_section(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
        stage_owner: dict[str, Any] | None = None,
    ) -> PromptSection | None:
        selection = self._expression_voice_selection(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        if isinstance(stage_owner, dict) and selection.get("rules"):
            profile = stage_owner.setdefault("expression_profile", {})
            if isinstance(profile, dict):
                profile["staged_semantic_selection"] = {
                    "ts": _now_ts(),
                    "rules": [dict(item) for item in selection.get("rules", []) if isinstance(item, dict)][:2],
                    "context": dict(selection.get("context") or {}),
                }
        section = selection.get("section")
        return section if isinstance(section, PromptSection) else None

    def _update_expression_profile_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_expression_learning", True):
            return
        cleaned = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), self._expression_sample_max_chars())
        if not cleaned:
            return
        if self._should_skip_expression_sample(cleaned):
            return
        managed, scope_context = self._expression_formal_scope_for_owner(user, source_kind="private")
        if managed and scope_context is None:
            return
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples")
        if not isinstance(samples, list):
            samples = []
            legacy_count = _safe_int(profile.get("samples"), 0, 0)
            legacy_short = _safe_int(profile.get("short_count"), 0, 0)
            legacy_punctuation = profile.get("punctuation") if isinstance(profile.get("punctuation"), dict) else {}
            legacy_endings = profile.get("endings") if isinstance(profile.get("endings"), list) else []
            legacy_phrases = profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []
            punctuation_items = [
                (str(mark), _safe_int(count, 0, 0))
                for mark, count in legacy_punctuation.items()
                if _safe_int(count, 0, 0) > 0
            ]
            if legacy_count:
                migrate_count = min(legacy_count, runtime_persona_setting(self, "max_learned_expression_items", 60))
                for idx in range(migrate_count):
                    punctuation = {}
                    if punctuation_items:
                        mark, count = punctuation_items[idx % len(punctuation_items)]
                        punctuation[mark] = min(3, max(1, count // max(1, migrate_count)))
                    samples.append(
                        {
                            "ts": now - (idx + 1) * 3600,
                            "length": 12 if idx < legacy_short else 32,
                            "punctuation": punctuation,
                            "ending": _single_line(legacy_endings[idx], 12) if idx < len(legacy_endings) else "",
                            "phrase": _single_line(legacy_phrases[idx], 40) if idx < len(legacy_phrases) else "",
                        }
                    )
        samples = [item for item in samples if isinstance(item, dict)]
        cutoff = now - 30 * 86400
        samples = [item for item in samples if _safe_float(item.get("ts"), now) >= cutoff]
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        sample = self._expression_sample_from_text(cleaned, now)
        if scope_context is not None:
            pending_review = self._expression_manual_review_enabled()
            sample = bind_expression_item(
                sample, scope_context,
                approval_state="pending" if pending_review else "approved",
                approved_by="" if pending_review else "automatic_policy",
            )
        if self._expression_manual_review_enabled():
            profile["samples"] = samples[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
            self._queue_expression_pending_sample(profile, sample, cleaned)
            self._refresh_expression_profile_legacy_summary(profile)
            if scope_context is not None:
                user["expression_profile"] = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=True,
                )
            return
        samples.insert(0, sample)
        profile["samples"] = samples[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        self._refresh_expression_profile_legacy_summary(profile)
        if scope_context is not None:
            user["expression_profile"] = self._expression_bind_profile_scope(
                profile, scope_context, bump_revision=True,
            )

    def _update_group_expression_profile_from_message(self, group: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_expression_learning", True):
            return
        cleaned = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), self._expression_sample_max_chars())
        if not cleaned or self._should_skip_expression_sample(cleaned):
            return
        managed, scope_context = self._expression_formal_scope_for_owner(group, source_kind="group")
        if managed and scope_context is None:
            return
        profile = group.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            group["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample = self._expression_sample_from_text(cleaned, now)
        # Group sources retain only aggregate-safe metadata, never a group member's original phrasing.
        for key in ("text", "phrase", "ending"):
            sample.pop(key, None)
        sample["evidence_count"] = 1
        if scope_context is not None:
            sample = bind_expression_item(
                sample, scope_context, approval_state="approved", approved_by="automatic_policy",
            )
        samples.insert(0, sample)
        profile["samples"] = samples
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._normalize_group_expression_profile(profile, now=now)
        if scope_context is not None:
            group["expression_profile"] = self._expression_bind_profile_scope(
                profile, scope_context, bump_revision=True,
            )

    def _expression_sample_from_text(self, cleaned: str, now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        punctuation = {}
        for mark in ("！", "!", "？", "?", "~", "～", "…", "。"):
            count = cleaned.count(mark)
            if count:
                punctuation[mark] = count
        stripped = cleaned.rstrip("。！？!?~～… ")
        ending = ""
        if 2 <= len(stripped) <= 80:
            ending = stripped[-min(6, max(2, len(stripped))):]
        phrase = ""
        phrase_limit = 56 if self._expression_learning_mode() == "aggressive" else 40
        if 2 <= len(cleaned) <= phrase_limit and not re.search(r"https?://|<[^>]+>", cleaned):
            phrase = cleaned
        return {
            "id": hashlib.sha1(f"{now}:{cleaned}".encode("utf-8")).hexdigest()[:12],
            "ts": now,
            "text": cleaned,
            "length": len(cleaned),
            "punctuation": punctuation,
            "ending": ending,
            "phrase": phrase,
            "scene": self._expression_scene_from_text(cleaned),
            "features": self._expression_style_features_from_text(cleaned),
        }

    @staticmethod
    def _expression_scene_label(scene: Any) -> str:
        labels = {
            "acknowledgement": "短确认",
            "question": "提问/追问",
            "request": "提出请求",
            "tease": "玩笑/打趣",
            "emotion": "情绪表达",
            "casual": "普通闲聊",
        }
        return labels.get(_single_line(scene, 32), "普通闲聊")

    def _expression_scene_from_text(self, text: Any) -> str:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return "casual"
        stripped = cleaned.rstrip("。！？!?~～… ").lower()
        if re.search(r"(?:帮我|给我|麻烦|能不能|可不可以|要不|请你|记得|别忘|提醒我|帮忙)", cleaned):
            return "request"
        if re.search(r"(?:难过|委屈|烦|好累|累死|想哭|哭了|生气|不开心|emo|破防|崩溃|害怕|焦虑)", cleaned, re.I):
            return "emotion"
        if re.search(r"(?:笨蛋|坏蛋|哼|才不要|你又|真是你|可恶)", cleaned):
            return "tease"
        if "？" in cleaned or "?" in cleaned or re.search(r"(?:怎么|为什么|啥|什么|是不是|对吗|行吗|好不好)$", stripped):
            return "question"
        if len(stripped) <= 28 and re.search(r"^(?:嗯|好|行|可以|知道|收到|对|没事|好吧|好呀|行吧|确实|原来|懂了|哦|啊|诶)", stripped):
            return "acknowledgement"
        return "casual"

    @staticmethod
    def _expression_style_features_from_text(text: Any) -> list[str]:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return []
        stripped = cleaned.rstrip("。！？!?~～… ")
        features: list[str] = []
        if len(cleaned) <= 18:
            features.append("short")
        if re.match(r"^(?:嗯|啊|诶|欸|唔|哎|哈哈|嘿嘿|哼|唉)", stripped):
            features.append("casual_opener")
        lowered = cleaned.lower()
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www")):
            features.append("laugh_marker")
        if not any(marker in lowered for marker in ("哈哈", "嘿嘿")) and re.search(r"([\u4e00-\u9fff])\1", stripped):
            features.append("reduplication")
        if "~" in cleaned or "～" in cleaned:
            features.append("soft_wave")
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www", "~", "～", "捏", "哼")):
            features.append("playful")
        if stripped.endswith(("吧", "呀", "啦", "嘛", "呢", "哦", "诶")):
            features.append("soft_ending")
        if "…" in cleaned or "..." in cleaned:
            features.append("pause")
        if "？" in cleaned or "?" in cleaned:
            features.append("question")
        return features

    def _expression_learning_mode(self) -> str:
        mode = str(runtime_persona_setting(self, "expression_learning_mode", "balanced") or "balanced").strip().lower()
        if mode not in {"light", "balanced", "aggressive"}:
            return "balanced"
        return mode

    def _expression_formal_scope_for_owner(
        self,
        owner: dict[str, Any],
        *,
        source_kind: str,
    ) -> tuple[bool, Any | None]:
        """Return (scoped-managed, formal context); managed failures are fail-closed."""
        managed = getattr(self, "req041_scoped_projection_sync", None) is not None
        if not managed or not isinstance(owner, dict):
            return managed, None
        if source_kind == "private":
            resolver = getattr(self, "_req041_scoped_context_for_user", None)
            context = resolver(owner, kind="private", purpose="rule_write") if callable(resolver) else None
        elif source_kind == "group":
            resolver = getattr(self, "_req041_scoped_group_context", None)
            group_id = _single_line(owner.get("group_id"), 160)
            context = resolver(group_id, purpose="rule_write") if callable(resolver) and group_id else None
        else:
            context = None
        return managed, context

    def _expression_bind_profile_scope(
        self,
        profile: dict[str, Any],
        context: Any,
        *,
        bump_revision: bool,
    ) -> dict[str, Any]:
        """Bind durable evidence/rules, migrating stale runtime scope metadata.

        Profiles live under a stable user/group record, but their ownership
        envelope also contains persona and migration-epoch metadata.  A persona
        switch or an upgrade can therefore leave an otherwise valid profile
        carrying an old envelope.  Rebinding the envelope is safe at this
        storage boundary and preserves the learned content; explicit callers
        that bind an item/profile directly still retain strict validation.
        """
        try:
            result = bind_expression_profile(profile, context, bump_revision=bump_revision)
        except ValueError as exc:
            if str(exc) != "expression_profile_scope_mismatch":
                raise
            result = deepcopy(profile)
            # The profile remains in the same owner record, so keep its
            # monotonic revision while replacing only stale scope metadata.
            result.pop("scope_ownership", None)
            result["scope_revision"] = max(1, _safe_int(result.get("scope_revision"), 1, 1))
            for key in (
                "samples", "pending_samples", "expression_rules", "pending_rules",
                "learned_rules", "rejected_samples", "revoked_samples",
                "rejected_rules", "revoked_rules",
            ):
                items = result.get(key)
                if not isinstance(items, list):
                    continue
                migrated: list[Any] = []
                for item in items:
                    if isinstance(item, dict):
                        item = deepcopy(item)
                        item.pop("scope_binding", None)
                    migrated.append(item)
                result[key] = migrated
            result = bind_expression_profile(result, context, bump_revision=False)
        collections = (
            ("samples", "approved", "automatic_policy"),
            ("pending_samples", "pending", ""),
            ("expression_rules", "pending", ""),
            ("pending_rules", "pending", ""),
            ("learned_rules", "approved", "legacy_migration"),
            ("rejected_samples", "rejected", "administrator"),
            ("revoked_samples", "revoked", "administrator"),
            ("rejected_rules", "rejected", "administrator"),
            ("revoked_rules", "revoked", "administrator"),
        )
        for key, approval_state, default_actor in collections:
            items = result.get(key)
            if not isinstance(items, list):
                continue
            bound: list[Any] = []
            for raw in items:
                if not isinstance(raw, dict):
                    raw = {"legacy_value": deepcopy(raw)}
                existing = raw.get("scope_binding") if isinstance(raw.get("scope_binding"), dict) else {}
                actor = _single_line(existing.get("approved_by"), 80) or default_actor
                bound.append(bind_expression_item(
                    raw, context, approval_state=approval_state, approved_by=actor,
                ))
            result[key] = bound
        return result

    def _expression_sample_max_chars(self) -> int:
        return 180 if self._expression_learning_mode() == "aggressive" else 120

    def _expression_style_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(self, "enable_expression_learning", True)
            and runtime_persona_setting(self, "enable_expression_style_review", True)
        )

    def _expression_manual_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(self, "enable_expression_learning", True)
            and runtime_persona_setting(self, "enable_expression_manual_review", False)
        )

    def _queue_expression_pending_sample(self, profile: dict[str, Any], sample: dict[str, Any], cleaned: str) -> None:
        pending = profile.get("pending_samples")
        if not isinstance(pending, list):
            pending = []
        compact = self._compact_repeat_text(cleaned)
        kept: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            old_text = _single_line(item.get("text") or item.get("phrase"), 180)
            if old_text and self._compact_repeat_text(old_text) == compact:
                continue
            kept.append(item)
        item = dict(sample)
        item["review_status"] = "pending"
        item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        kept.insert(0, item)
        profile["pending_samples"] = kept[: min(80, max(12, runtime_persona_setting(self, "max_learned_expression_items", 60) * 2))]
        profile["pending_count"] = len(profile["pending_samples"])
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _should_skip_expression_sample(self, cleaned: str) -> bool:
        if len(cleaned) > self._expression_sample_max_chars():
            return True
        if re.search(r"https?://|www\.|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|sudo\b|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("\n") >= 2 or cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(傻逼|滚|闭嘴|垃圾|废物|妈的|草泥马|操你|死全家)", cleaned):
            return True
        if re.search(r"(习近平|共产党|中共|六四|天安门|法轮功|台独|港独|藏独|疆独|民主运动|政治敏感)", cleaned):
            return True
        if re.search(r"(复制|日志|报错|堆栈|代码|配置|schema|版本号|commit|diff|traceback)", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:我叫|我是|叫我)[^。！？!?\n]{1,40}", cleaned):
            return True
        return False

    def _safe_expression_phrase(self, phrase: Any, limit: int = 56) -> str:
        text = _single_line(phrase, limit)
        if not text or len(text) < 2:
            return ""
        if self._should_skip_expression_sample(text):
            return ""
        if re.search(r"<[^>]{1,120}>|@[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}|QQ|群聊|群友|私聊", text, re.IGNORECASE):
            return ""
        if re.search(r"(你是|我是|他是|她是|叫我|叫你|名字|主人|主要用户|次要用户|朋友|同学|老师|室友|父母|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹)", text):
            return ""
        return text

    def _expression_profile_phrases(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("recent_phrases") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        phrases: list[str] = []
        for item in raw:
            phrase = self._safe_expression_phrase(item, 56)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= limit:
                break
        return phrases

    def _expression_profile_endings(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("endings") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        endings: list[str] = []
        for item in raw:
            ending = self._safe_expression_phrase(item, 12)
            if ending and ending not in endings:
                endings.append(ending)
            if len(endings) >= limit:
                break
        return endings
