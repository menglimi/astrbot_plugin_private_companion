# -*- coding: utf-8 -*-
"""UserMemoryExpressionRulePart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_expression_rule.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 470 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryExpressionRuleMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from copy import deepcopy
from datetime import datetime
from typing import Any



class UserMemoryExpressionRulePart01Mixin:
    """UserMemoryExpressionRulePart01Mixin（从 UserMemoryExpressionRuleMixin 拆出）。"""


    @staticmethod
    def _expression_length_bucket(length: Any) -> str:
        value = _safe_int(length, 0, 0)
        if value <= 6:
            return "2-6"
        if value <= 12:
            return "7-12"
        if value <= 20:
            return "13-20"
        if value <= 36:
            return "21-36"
        return "37+"

    def _group_expression_pattern_signature(self, item: dict[str, Any]) -> str:
        scene = _single_line(item.get("scene"), 32)
        if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
            scene = "casual"
        raw_features = item.get("features")
        features = sorted({
            _single_line(feature, 32)
            for feature in raw_features
            if _single_line(feature, 32)
        }) if isinstance(raw_features, list) else []
        distinctive = [feature for feature in features if feature not in {"short", "question"}]
        if scene == "casual" and not distinctive:
            return ""
        marks = item.get("punctuation") if isinstance(item.get("punctuation"), dict) else {}
        mark_keys = sorted(str(mark) for mark, count in marks.items() if _safe_int(count, 0, 0) > 0)
        length_bucket = self._expression_length_bucket(item.get("length"))
        return "|".join((scene, length_bucket, ",".join(features), ",".join(mark_keys)))

    def _group_expression_pattern_samples(self, profile: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        if not isinstance(profile, dict):
            return []
        now = now or _now_ts()
        cutoff = now - 30 * 86400
        raw_samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        buckets: dict[str, dict[str, Any]] = {}
        for raw in raw_samples:
            if not isinstance(raw, dict) or _safe_float(raw.get("ts"), now) < cutoff:
                continue
            signature = self._group_expression_pattern_signature(raw)
            if not signature:
                continue
            evidence = _safe_int(raw.get("evidence_count"), 1, 1, 9999)
            length = _safe_int(raw.get("length"), 0, 0)
            length_total = _safe_int(raw.get("length_total"), length * evidence, 0)
            ts = _safe_float(raw.get("ts"), now)
            first_seen_ts = _safe_float(raw.get("first_seen_ts"), ts)
            features = [
                _single_line(feature, 32)
                for feature in raw.get("features", [])
                if _single_line(feature, 32)
            ] if isinstance(raw.get("features"), list) else []
            marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            bucket = buckets.setdefault(
                signature,
                {
                    "id": hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
                    "ts": ts,
                    "first_seen_ts": first_seen_ts,
                    "scene": _single_line(raw.get("scene"), 32) or "casual",
                    "features": list(dict.fromkeys(features)),
                    "length_bucket": self._expression_length_bucket(length),
                    "length_total": 0,
                    "evidence_count": 0,
                    "punctuation": {},
                },
            )
            bucket["ts"] = max(_safe_float(bucket.get("ts"), 0.0), ts)
            bucket["first_seen_ts"] = min(_safe_float(bucket.get("first_seen_ts"), ts), first_seen_ts)
            bucket["length_total"] += length_total
            bucket["evidence_count"] += evidence
            bucket_marks = bucket["punctuation"]
            for mark, count in marks.items():
                value = _safe_int(count, 0, 0)
                if value > 0:
                    bucket_marks[str(mark)] = _safe_int(bucket_marks.get(str(mark)), 0, 0) + value
        patterns = []
        for bucket in buckets.values():
            evidence = max(1, _safe_int(bucket.get("evidence_count"), 1, 1))
            bucket["length"] = round(_safe_int(bucket.get("length_total"), 0, 0) / evidence)
            bucket["pattern_status"] = "active" if evidence >= 2 else "observing"
            patterns.append(bucket)
        patterns.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), -_safe_float(item.get("ts"), 0.0)))
        return patterns[: runtime_persona_setting(self, "max_learned_expression_items", 60)]

    def _normalize_group_expression_profile(self, profile: dict[str, Any], *, now: float | None = None) -> bool:
        if not isinstance(profile, dict):
            return False
        before = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        patterns = self._group_expression_pattern_samples(profile, now=now)
        previous_by_id = {
            _single_line(item.get("id"), 40): item
            for item in before if isinstance(item, dict) and _single_line(item.get("id"), 40)
        }
        for pattern in patterns:
            previous = previous_by_id.get(_single_line(pattern.get("id"), 40))
            binding = previous.get("scope_binding") if isinstance(previous, dict) and isinstance(previous.get("scope_binding"), dict) else None
            if binding is None:
                continue
            pattern["scope_binding"] = deepcopy(binding)
            old_content = dict(previous)
            old_content.pop("scope_binding", None)
            new_content = dict(pattern)
            new_content.pop("scope_binding", None)
            if old_content != new_content:
                pattern["scope_binding"]["revision"] = max(
                    1, _safe_int(pattern["scope_binding"].get("revision"), 1, 1) + 1,
                )
        changed = before != patterns
        profile["samples"] = patterns
        profile["pattern_count"] = len(patterns)
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._refresh_expression_profile_legacy_summary(profile)
        return changed

    @staticmethod
    def _expression_rule_signature(item: dict[str, Any]) -> str:
        def compact(value: Any, limit: int) -> str:
            return re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(value, limit).lower(),
            )

        parts = (
            compact(item.get("kind"), 16),
            compact(item.get("situation"), 80),
            compact(item.get("pattern"), 100),
            compact(item.get("instruction"), 140),
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _expression_rule_source_parts(source_text: str, *, source_kind: str) -> tuple[list[str], set[str]]:
        utterances: list[str] = []
        speaker_names: set[str] = set()
        for raw_line in str(source_text or "").splitlines():
            line = raw_line.strip()
            match = re.match(
                r"^(?:\d{2}-\d{2}\s+\d{2}:\d{2}\s+)?([^:：]{1,40})[:：]\s*(.*)$",
                line,
            )
            if not match:
                continue
            speaker, content = match.groups()
            speaker = speaker.strip()
            content = _single_line(content, 260)
            if not content:
                continue
            if source_kind == "private":
                if speaker != "用户":
                    continue
            else:
                if speaker:
                    speaker_names.add(speaker)
            utterances.append(content)
        return utterances, speaker_names

    @staticmethod
    def _expression_rule_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return _single_line(value, 16).lower() in {"1", "true", "yes", "on", "是", "有", "冲突"}

    @staticmethod
    def _normalize_expression_rule_values(
        value: Any,
        *,
        allowed: set[str],
        aliases: dict[str, str],
        defaults: list[str],
    ) -> list[str]:
        if isinstance(value, str):
            raw_values = re.split(r"[,，/、|\s]+", value)
        elif isinstance(value, list):
            raw_values = value
        else:
            raw_values = []
        normalized: list[str] = []
        for raw in raw_values:
            item = _single_line(raw, 24).lower()
            item = aliases.get(item, item)
            if item == "all":
                item = "any"
            if item in allowed and item not in normalized:
                normalized.append(item)
        return normalized or list(defaults)

    def _normalize_expression_rule_channels(self, value: Any, *, source_kind: str) -> list[str]:
        # 来源与使用范围是两件事。群聊里学到的脱敏表达默认也可以用于
        # 已配置的私聊/主动消息目标，最终仍会经过频道、关系和审核门控。
        defaults = ["private", "group", "proactive"] if source_kind == "group" else ["private", "proactive"]
        return self._normalize_expression_rule_values(
            value,
            allowed={"private", "group", "proactive", "qzone", "tts"},
            aliases={
                "私聊": "private",
                "群聊": "group",
                "主动": "proactive",
                "主动消息": "proactive",
                "空间": "qzone",
                "qq空间": "qzone",
                "说说": "qzone",
                "语音": "tts",
            },
            defaults=defaults,
        )

    def _normalize_expression_relationship_stages(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "stranger", "familiar", "close"},
            aliases={"不限": "any", "任意": "any", "陌生": "stranger", "熟悉": "familiar", "亲近": "close"},
            defaults=["any"],
        )

    def _normalize_expression_emotion_gates(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "normal", "positive", "low", "guarded"},
            aliases={
                "不限": "any",
                "任意": "any",
                "普通": "normal",
                "中性": "normal",
                "轻松": "positive",
                "积极": "positive",
                "低落": "low",
                "安抚": "low",
                "防备": "guarded",
                "边界": "guarded",
            },
            defaults=["any"],
        )

    @staticmethod
    def _normalize_expression_intent(value: Any) -> str:
        intent = _single_line(value, 32).lower()
        aliases = {
            "不限": "any",
            "任意": "any",
            "确认": "acknowledgement",
            "提问": "question",
            "请求": "request",
            "求助": "help",
            "安抚": "comfort",
            "玩笑": "play",
            "亲近": "intimacy",
            "边界": "boundary",
            "情绪": "emotion",
            "闲聊": "casual",
            "主动": "proactive",
        }
        intent = aliases.get(intent, intent)
        allowed = {
            "any",
            "acknowledgement",
            "question",
            "request",
            "help",
            "comfort",
            "play",
            "tease",
            "intimacy",
            "boundary",
            "emotion",
            "casual",
            "proactive",
        }
        return intent if intent in allowed else "any"

    @staticmethod
    def _expression_rule_payload_candidates(payload: Any) -> list[dict[str, Any]]:
        """兼容旧 expression_rules，并优先接收 WaifuBot 式的双分类结果。"""
        if not isinstance(payload, dict):
            return []
        result: list[dict[str, Any]] = []
        sections = (
            ("style_expressions", "style"),
            ("grammar_expressions", "grammar"),
            ("expression_rules", ""),
        )
        for key, forced_kind in sections:
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for raw in values:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                if forced_kind:
                    item["kind"] = forced_kind
                pattern = _single_line(item.get("pattern") or item.get("style"), 120)
                if pattern and not _single_line(item.get("instruction"), 160):
                    if _single_line(item.get("kind"), 16).lower() == "grammar":
                        item["instruction"] = f"在匹配情境中采用“{pattern}”的句法，内容仍按当前事实生成"
                    else:
                        item["instruction"] = f"在匹配情境中自然使用或轻微改写“{pattern}”，不要机械复读"
                if "keywords" not in item and isinstance(item.get("tags"), list):
                    item["keywords"] = list(item.get("tags") or [])
                result.append(item)
                if len(result) >= 12:
                    return result
        return result

    def _expression_rule_generation_reference(
        self,
        profile: Any,
        *,
        hint: str = "",
        limit: int = 14,
    ) -> str:
        if not isinstance(profile, dict):
            return "- 暂无已有规则；只在证据充分时新增，不要为了凑数输出。"
        query = _single_line(hint, 6000).lower()
        query_key = self._expression_rule_pattern_key(query)
        rows: list[tuple[float, str]] = []
        for storage_key, status in (("learned_rules", "已启用"), ("pending_rules", "待审核")):
            rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            for raw in rules:
                if not isinstance(raw, dict) or not self._expression_rule_definition_is_valid(raw):
                    continue
                rule_id = _single_line(raw.get("id"), 40)
                kind = _single_line(raw.get("kind"), 16).lower()
                situation = _single_line(raw.get("situation"), 70)
                pattern = _single_line(raw.get("pattern") or raw.get("style"), 80)
                if not rule_id or not situation or not pattern:
                    continue
                keywords = [
                    _single_line(value, 24)
                    for value in (raw.get("keywords") if isinstance(raw.get("keywords"), list) else [])
                    if _single_line(value, 24)
                ][:5]
                matched = sum(1 for keyword in keywords if query and keyword.lower() in query)
                pattern_key = self._expression_rule_pattern_key(pattern)
                if query_key and pattern_key and pattern_key in query_key:
                    matched += 2
                score = (
                    matched * 20
                    + min(12, _safe_int(raw.get("evidence_count"), 0, 0))
                    + min(5, _safe_int(raw.get("use_count"), 0, 0))
                    + min(3.0, _safe_float(raw.get("last_seen_ts"), 0.0) / max(1.0, _now_ts()) * 3.0)
                )
                intent = self._normalize_expression_intent(raw.get("intent"))
                rows.append((
                    score,
                    f"- {status} {kind} id={rule_id}｜情境：{situation}｜模板：{pattern}"
                    + (f"｜意图：{intent}" if intent != "any" else "")
                    + (f"｜标签：{'、'.join(keywords)}" if keywords else ""),
                ))
        if not rows:
            return "- 暂无已有规则；只在证据充分时新增，不要为了凑数输出。"
        rows.sort(key=lambda item: item[0], reverse=True)
        return "\n".join(text for _, text in rows[: max(4, min(20, limit))])

    @staticmethod
    def _expression_style_pattern_is_reusable(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 2 or len(value) > 64:
            return False
        quoted = re.fullmatch(r"[“\"‘'](.{2,48})[”\"’']", value)
        if quoted:
            value = quoted.group(1).strip()
        compact = re.sub(r"[\s，。！？!?、；;：:]", "", value)
        if compact in {
            "短句", "长句", "柔和收尾", "轻松语气", "自然表达", "口语化表达",
            "先确认再补充", "先接住再延续", "简短回应", "语气自然",
        }:
            return False
        has_template_marker = bool(re.search(r"_{2,}|\[[^\]]{1,20}\]|[（(][^）)]{1,20}[）)]|[“\"].{1,30}[”\"]", value))
        meta_description = bool(
            re.search(
                r"(?:偏好|习惯|倾向|通常|经常|多用|常用|口语化|书面化|"
                r"语气|风格|句式|句法|字数|主语|拆句|铺垫|柔和收尾|"
                r"表达内容|表达方式|回应时|回复时|句子结构|长篇大论)",
                value,
            )
        )
        looks_like_instruction = bool(
            re.match(
                r"^(?:先|使用|采用|保持|表达|回复|回应|开头|结尾|收尾|"
                r"语气|句式|句法|短句|长句|直接|简短|自然|柔和)",
                value,
            )
        )
        if meta_description or looks_like_instruction:
            return False
        return has_template_marker or len(value) <= 32

    @staticmethod
    def _expression_grammar_pattern_is_specific(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 4 or len(value) > 80:
            return False
        if re.search(r"(?:语气自然|自然表达|口语化表达|表达简洁|说话直接)$", value):
            return False
        return bool(
            re.search(
                r"(?:主语|省略|\d+\s*[—–~-]\s*\d+\s*字|\d+\s*字|"
                r"[一二三四五六七八九十]+\s*[—–~-]\s*[一二三四五六七八九十]+\s*字|"
                r"短句|长句|单句|双句|两句|拆句|断句|反问|祈使|问句|"
                r"感叹句|陈述句|倒装|重复|叠词|标点|停顿|句首|句尾|连接词)",
                value,
            )
        )

    def _expression_rule_definition_is_valid(self, raw_rule: Any) -> bool:
        if not isinstance(raw_rule, dict):
            return False
        kind = _single_line(raw_rule.get("kind") or raw_rule.get("type"), 16).lower()
        situation = _single_line(raw_rule.get("situation"), 100)
        pattern = _single_line(raw_rule.get("pattern") or raw_rule.get("style"), 100)
        instruction = _single_line(raw_rule.get("instruction"), 160)
        if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
            return False
        if kind == "style":
            return self._expression_style_pattern_is_reusable(pattern)
        return self._expression_grammar_pattern_is_specific(pattern)

    def _prune_invalid_expression_rules(self, profile: dict[str, Any]) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            existing = profile.get(storage_key)
            if not isinstance(existing, list):
                continue
            kept = [
                item
                for item in existing
                if (
                    self._expression_rule_definition_is_valid(item)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                )
            ]
            if kept != existing:
                profile[storage_key] = kept
                changed = True
            if self._assign_expression_rule_families(kept):
                changed = True
            if self._deduplicate_expression_rule_families(kept):
                profile[storage_key] = kept
                changed = True
        return changed

    @staticmethod
    def _expression_rule_evidence_key(value: Any) -> str:
        text = _single_line(value, 96).lower()
        return re.sub(r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]", "", text)

    @staticmethod
    def _expression_rule_text_similarity(left: Any, right: Any) -> float:
        def grams(value: Any) -> set[str]:
            compact = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]",
                "",
                _single_line(value, 160).lower(),
            )
            if not compact:
                return set()
            if len(compact) == 1:
                return {compact}
            return {compact[index:index + 2] for index in range(len(compact) - 1)}

        left_grams = grams(left)
        right_grams = grams(right)
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))

    @staticmethod
    def _expression_rule_pattern_key(value: Any) -> str:
        text = _single_line(value, 120).lower()
        text = re.sub(r"_{2,}|\[[^\]]{1,24}\]|[（(][^）)]{1,24}[）)]", "<slot>", text)
        return re.sub(
            r"[\s，。！？!?、；;：:‘’“”\"'~～…—–_-]",
            "",
            text,
        )

    @staticmethod
    def _expression_rule_value_set(value: Any, *, limit: int = 24) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            normalized
            for item in value
            if (normalized := _single_line(item, limit).lower())
        }
