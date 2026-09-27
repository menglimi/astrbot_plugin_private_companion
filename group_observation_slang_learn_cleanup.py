# -*- coding: utf-8 -*-
"""GroupObservationSlangLearnCleanupMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 437 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import re
import unicodedata
from .group_observation_shared import _persona_value
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from datetime import datetime
from typing import Any



class GroupObservationSlangLearnCleanupMixin:
    """GroupObservationSlangLearnCleanupMixin（从 GroupObservationMixin 拆出）。"""


    @staticmethod
    def _is_group_slang_transport_metadata_term(term: Any) -> bool:
        normalized = unicodedata.normalize("NFKC", str(term or "")).strip().lower()
        compact = re.sub(r"[\s\[\]【】()（）<>《》:：|]+", "", normalized)
        if compact in {
            "引用消息",
            "被引用消息",
            "回复消息",
            "引用消息id",
            "回复消息id",
            "reply",
            "quote",
            "quoted",
            "msg_id",
            "message_id",
            "message_seq",
            "real_id",
            "reply_id",
            "reply_msg_id",
            "reply_message_id",
            "quote_id",
            "quoted_id",
            "quoted_message_id",
            "share_source",
            "share_medium",
            "share_url",
            "share_title",
            "share_content",
            "share_type",
            "share_target",
            "share_app",
            "share_channel",
        }:
            return True
        return bool(
            re.fullmatch(
                r"(?:msg|message|reply|quote|quoted)_(?:id|seq|msg_id|message_id|real_id)"
                r"|share_(?:source|medium|url|title|content|type|target|app|channel)",
                compact,
            )
        )

    @classmethod
    def _is_group_slang_noise_term(cls, term: Any) -> bool:
        normalized = unicodedata.normalize("NFKC", str(term or "")).strip()
        if not normalized:
            return True
        lower = normalized.casefold()
        compact = re.sub(r"[\s._:/\\-]+", "", lower)
        if cls._is_group_slang_transport_metadata_term(normalized):
            return True
        if re.fullmatch(r"\d+", compact):
            return True
        if re.search(r"https?://|www\.|\.(?:com|cn|net|org|io|ai)(?:\b|/)", lower):
            return True
        if lower in {
            "http", "https", "www", "com", "cn", "net", "org", "html", "url", "b23",
            "api", "token", "pro", "plus", "app", "bot", "browser", "bilibili",
            "gpt", "vlm", "qwen", "gemini", "codex", "opencode",
        }:
            return True
        if compact in {
            "什么", "这个", "那个", "就是", "感觉", "可以", "不是", "没有", "真的",
            "一下", "一个", "怎么", "为什么", "能不能", "是不是", "已经", "现在",
            "今天", "明天", "昨天", "然后", "但是", "还是", "因为", "所以", "可能",
            "需要", "应该", "知道", "看看", "请问", "谢谢", "好的", "收到", "版本",
            "支持", "应用", "升级", "记录", "出来", "找到", "浏览器",
        }:
            return True
        return False

    def _group_slang_term_is_promoted(self, group: dict[str, Any], item: Any) -> bool:
        term = _single_line(item.get("term") if isinstance(item, dict) else item, 40)
        if not term or self._is_group_slang_noise_term(term):
            return False
        meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        meaning_item = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
        if _single_line(meaning_item.get("source"), 32) in {"explicit_correction", "manual"}:
            return True
        count = _safe_int(item.get("count"), 0, 0) if isinstance(item, dict) else 0
        meaning = _single_line(meaning_item.get("meaning"), 120)
        confidence = _safe_float(meaning_item.get("confidence"), 0, 0.0, 1.0)
        if meaning and confidence >= 0.55 and not self._is_uncertain_group_slang_meaning(
            meaning,
            _single_line(meaning_item.get("usage"), 120),
        ):
            return True
        return count >= 2

    def _group_slang_candidates_from_text(self, text: Any) -> list[str]:
        raw = str(text or "")
        if not raw:
            return []
        cleaned = re.sub(r"https?://\S+|www\.\S+", " ", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\[CQ:[^\]]+\]", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"```[\s\S]*?```|`[^`]*`", " ", cleaned)
        candidates: list[str] = []

        def add(value: Any) -> None:
            if self._is_group_slang_transport_metadata_term(value):
                return
            token = _single_line(value, 16).strip("'\"“”‘’「」『』【】[]()（）<>《》：:，,。.!！?？")
            if not token or self._is_group_slang_noise_term(token):
                return
            if self._is_group_slang_transport_metadata_term(token):
                return
            if len(token) <= 2 and token not in {"草", "绷", "典", "急", "乐", "急了", "笑死"}:
                return
            if token not in candidates:
                candidates.append(token)

        # Latin abbreviations and coined identifiers have stable boundaries;
        # URL/code payloads were removed above before tokenization.
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,31}", cleaned):
            add(token)

        # A short standalone message can itself be a repeated group expression.
        short_message = re.sub(r"\s+", "", cleaned).strip()
        if (
            2 <= len(short_message) <= 12
            and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_]+", short_message)
        ):
            add(short_message)

        # In longer sentences only accept explicitly quoted/named expressions;
        # never split ordinary Chinese prose into arbitrary eight-character blocks.
        for token in re.findall(r"[“\"「『【]([^”\"」』】\n]{2,16})[”\"」』】]", cleaned):
            add(token)
        for pattern in (
            r"(?:群里|你们|大家)(?:说的|叫的|讲的)?[“\"「『【]?([\u4e00-\u9fffA-Za-z0-9_]{2,12})[”\"」』】]?(?:是什么意思|是啥|什么梗)",
            r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:这个词|这个梗)?(?:是什么意思|是啥意思|什么梗)",
            r"(?:我们|群里|大家)(?:都)?(?:叫|称|简称)([\u4e00-\u9fffA-Za-z0-9_]{2,12})",
        ):
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                add(match.group(1))

        for marker in ("草", "绷", "典", "急了", "笑死", "蚌埠住", "乐"):
            if marker in cleaned:
                add(marker)
        return candidates[:8]

    def _learn_group_slang(self, group: dict[str, Any], text: str) -> None:
        if self._group_text_blocked_by_injection_guard(text):
            return
        terms = group.setdefault("slang_terms", [])
        if not isinstance(terms, list):
            terms = []
            group["slang_terms"] = terms
        self._cleanup_group_slang_terms(group)
        candidates = [
            token
            for token in self._group_slang_candidates_from_text(text)
            if not self._looks_like_group_member_name(group, token)
        ]
        if not candidates:
            return
        indexed = {}
        for item in terms:
            if isinstance(item, dict) and item.get("term"):
                indexed[str(item.get("term"))] = item
        for token in candidates[:8]:
            item = indexed.get(token)
            if not item:
                item = {"term": token, "count": 0, "last_seen": 0}
                terms.append(item)
                indexed[token] = item
            item["count"] = min(999, _safe_int(item.get("count"), 0, 0) + 1)
            item["last_seen"] = _now_ts()
        terms.sort(key=lambda item: (_safe_int(item.get("count"), 0, 0), _safe_float(item.get("last_seen"), 0)), reverse=True)
        del terms[_safe_int(_persona_value(self, "max_group_slang_terms", 80), 80, 1):]

    def _learn_group_nickname_correction(self, group: dict[str, Any], text: str) -> None:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return
        if self._group_text_blocked_by_injection_guard(cleaned):
            return
        cleaned = re.sub(r"\[CQ:at,qq=\d+(?:,[^\]]*)?\]", "", cleaned)
        token = r"[\u4e00-\u9fffA-Za-z0-9_]{2,16}"
        updates: dict[str, dict[str, str]] = {}
        negatives: dict[str, list[str]] = {}

        for match in re.finditer(rf"(?P<nick>{token})(?:是|就是)(?P<owner>{token})(?:的)?(?:外号|昵称|别称)?", cleaned):
            nick = _single_line(match.group("nick"), 20)
            owner = _single_line(match.group("owner"), 20)
            if not nick or not owner or nick == owner:
                continue
            owner_label = self._group_member_identity_label_for_token(group, owner)
            if not owner_label:
                continue
            updates[nick] = {
                "meaning": f"{owner_label} 的外号/称呼",
                "usage": "称呼该群友时使用；身份以 QQ 锚点为准",
            }
            suffix = cleaned[match.end(): match.end() + 24]
            neg_match = re.match(rf"(?:不是|不等于|并不是)(?P<owner>{token})", suffix)
            if neg_match:
                negative_owner = _single_line(neg_match.group("owner"), 20)
                if negative_owner and negative_owner != owner:
                    negative_label = self._group_member_identity_label_for_token(group, negative_owner) or negative_owner
                    negatives.setdefault(nick, []).append(negative_label)

        for match in re.finditer(rf"(?P<owner>{token})(?:的)?(?:外号|昵称|别称)(?:是|叫)(?P<nick>{token})", cleaned):
            owner = _single_line(match.group("owner"), 20)
            nick = _single_line(match.group("nick"), 20)
            if not nick or not owner or nick == owner:
                continue
            owner_label = self._group_member_identity_label_for_token(group, owner)
            if not owner_label:
                continue
            updates[nick] = {
                "meaning": f"{owner_label} 的外号/称呼",
                "usage": "称呼该群友时使用；身份以 QQ 锚点为准",
            }

        for match in re.finditer(rf"(?P<nick>{token})(?:不是|不等于|并不是)(?P<owner>{token})", cleaned):
            nick = _single_line(match.group("nick"), 20)
            owner = _single_line(match.group("owner"), 20)
            if nick and owner and nick != owner:
                if nick not in updates and self._group_member_identity_label_for_token(group, nick):
                    continue
                owner_label = self._group_member_identity_label_for_token(group, owner) or owner
                negatives.setdefault(nick, []).append(owner_label)

        if not updates and not negatives:
            return
        meanings = group.setdefault("slang_meanings", {})
        if not isinstance(meanings, dict):
            meanings = {}
            group["slang_meanings"] = meanings
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
        for nick, payload in updates.items():
            existing = meanings.get(nick) if isinstance(meanings.get(nick), dict) else {}
            negative_values = list(negatives.get(nick) or [])
            existing_negative = existing.get("not_owner") if isinstance(existing, dict) else ""
            if existing_negative:
                negative_values.extend([item for item in re.split(r"[、,，;；]+", str(existing_negative)) if item])
            meanings[nick] = {
                "meaning": payload["meaning"],
                "usage": payload["usage"],
                "not_owner": "、".join(dict.fromkeys(negative_values)),
                "source": "explicit_correction",
                "evidence": cleaned,
                "updated_at": now_text,
            }
        for nick, negative_values in negatives.items():
            if nick in updates:
                continue
            existing = meanings.get(nick) if isinstance(meanings.get(nick), dict) else {}
            merged = []
            if isinstance(existing, dict) and existing.get("not_owner"):
                merged.extend([item for item in re.split(r"[、,，;；]+", str(existing.get("not_owner") or "")) if item])
            merged.extend(negative_values)
            meanings[nick] = {
                "meaning": _single_line(existing.get("meaning"), 90) if isinstance(existing, dict) else "外号归属被纠正，具体对象未确认",
                "usage": _single_line(existing.get("usage"), 90) if isinstance(existing, dict) else "遇到该称呼时不要猜归属",
                "not_owner": "、".join(dict.fromkeys(merged)),
                "source": "explicit_correction",
                "evidence": cleaned,
                "updated_at": now_text,
            }
        self._enforce_group_slang_meanings_budget(group)

    def _group_member_name_tokens(self, group: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()

        def add(value: Any) -> None:
            text = _single_line(value, 40)
            if not text or text.isdigit():
                return
            tokens.add(text)
            compact = re.sub(r"\s+", "", text)
            if compact:
                tokens.add(compact)

        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        for user_id, member in members.items():
            if not isinstance(member, dict):
                continue
            add(member.get("name"))
            add(member.get("identity_name"))
            add(member.get("display_name"))
            add(member.get("nickname"))
            add(member.get("card"))
        return tokens

    def _cleanup_group_slang_terms(self, group: dict[str, Any]) -> bool:
        terms = group.get("slang_terms")
        if not isinstance(terms, list):
            return False
        now = _now_ts()
        name_tokens = self._group_member_name_tokens(group)
        kept: list[Any] = []
        removed: set[str] = set()
        for item in terms:
            term = _single_line(item.get("term") if isinstance(item, dict) else item, 40)
            if self._is_group_slang_transport_metadata_term(term):
                removed.add(term)
                continue
            meanings = group.get("slang_meanings")
            meaning_item = meanings.get(term) if isinstance(meanings, dict) else None
            if isinstance(meaning_item, dict) and meaning_item.get("source") in {"explicit_correction", "manual"}:
                kept.append(item)
                continue
            if self._is_group_slang_noise_term(term):
                removed.add(term)
                continue
            if isinstance(item, dict):
                count = _safe_int(item.get("count"), 0, 0)
                last_seen = _safe_float(item.get("last_seen"), 0)
                if last_seen > 0:
                    age_days = max(0.0, (now - last_seen) / 86400.0)
                    if age_days >= 21 and count <= 2:
                        removed.add(term)
                        continue
                    if age_days >= 45 and count <= 5:
                        removed.add(term)
                        continue
            if term and self._looks_like_group_member_name(group, term, name_tokens=name_tokens):
                removed.add(term)
                continue
            kept.append(item)
        if len(kept) == len(terms):
            return False
        group["slang_terms"] = kept
        meanings = group.get("slang_meanings")
        if isinstance(meanings, dict):
            for term in removed:
                meanings.pop(term, None)
        return True

    def _cleanup_group_members(self, group: dict[str, Any], *, now: float | None = None) -> bool:
        members = group.get("members")
        if not isinstance(members, dict):
            return False
        now = _now_ts() if now is None else now
        changed = False
        for user_id, member in list(members.items()):
            if not isinstance(member, dict):
                members.pop(user_id, None)
                changed = True
                continue
            last_seen = _safe_float(member.get("last_seen"), 0)
            if last_seen > 0 and now - last_seen > 90 * 86400 and _safe_int(member.get("count"), 0, 0) <= 2:
                members.pop(user_id, None)
                changed = True
                continue
            for stale_key in ("identity_note", "boundary_note"):
                if stale_key in member:
                    member.pop(stale_key, None)
                    changed = True
            phrases = member.get("recent_phrases")
            if isinstance(phrases, list):
                deduped: list[str] = []
                for item in phrases:
                    text = _single_line(item, 40)
                    if text and text not in deduped:
                        deduped.append(text)
                if deduped != phrases:
                    member["recent_phrases"] = deduped[:8]
                    changed = True
        return changed

    def _group_share_event_ts(self, share: dict[str, Any]) -> float:
        if not isinstance(share, dict):
            return 0.0
        for key in ("event_ts", "latest_ts", "created_ts"):
            value = _safe_float(share.get(key), 0)
            if value > 0:
                return value
        return 0.0

    def _group_share_age_seconds(self, share: dict[str, Any], *, now: float | None = None) -> float:
        event_ts = self._group_share_event_ts(share)
        if event_ts <= 0:
            return 0.0
        check_now = _now_ts() if now is None else now
        return max(0.0, check_now - event_ts)

    def _group_share_recency_label(self, share: dict[str, Any], *, now: float | None = None) -> str:
        age = self._group_share_age_seconds(share, now=now)
        if age < 10 * 60:
            return "刚才"
        if age < 45 * 60:
            return f"{max(10, int(age // 60))} 分钟前"
        if age < 6 * 3600:
            return "前面"
        check_now = _now_ts() if now is None else now
        try:
            event_day = datetime.fromtimestamp(self._group_share_event_ts(share)).date()
            now_day = datetime.fromtimestamp(check_now).date()
            delta_days = (now_day - event_day).days
            if delta_days == 0:
                return "今天早些时候"
            if delta_days == 1:
                return "昨天"
            if delta_days > 1:
                return f"{delta_days} 天前"
        except Exception:
            pass
        return "前面"

    def _repair_group_share_recency_text(self, user: dict[str, Any], text: str) -> str:
        cleaned = str(text or "")
        share = user.get("group_share_context") if isinstance(user.get("group_share_context"), dict) else {}
        if not cleaned or not isinstance(share, dict):
            return cleaned
        if self._group_share_age_seconds(share) < 30 * 60:
            return cleaned
        label = self._group_share_recency_label(share)
        if label in {"刚才", "刚刚"}:
            return cleaned
        parts = re.split(r"([。！？!?；;\n])", cleaned)
        repaired: list[str] = []
        for index in range(0, len(parts), 2):
            segment = parts[index]
            punct = parts[index + 1] if index + 1 < len(parts) else ""
            if any(token in segment for token in ("群", "群里", "群友", "Bot", "bot", "机器人")):
                segment = re.sub(r"群里\s*(?:刚刚|刚才)", f"群里{label}", segment)
                segment = re.sub(r"(?:刚刚|刚才)\s*(?=有人|那个|那条|这条|这段|群友|Bot|bot|机器人)", label, segment)
            repaired.append(segment + punct)
        return "".join(repaired)

    def _cleanup_group_relationship_edges(self, group: dict[str, Any], *, now: float | None = None) -> bool:
        edges = group.get("relationship_edges")
        if not isinstance(edges, dict):
            return False
        now = _now_ts() if now is None else now
        changed = False
        for key, item in list(edges.items()):
            if not isinstance(item, dict):
                edges.pop(key, None)
                changed = True
                continue
            last_seen = _safe_float(item.get("last_ts") or item.get("last_seen") or item.get("updated_ts"), 0)
            weight = _safe_int(item.get("count"), 0, 0)
            if last_seen > 0 and now - last_seen > 60 * 86400 and weight <= 2:
                edges.pop(key, None)
                changed = True
        return changed

    def _cleanup_all_group_slang_terms(self) -> bool:
        groups = self.data.get("groups") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(groups, dict):
            return False
        changed = False
        for group in groups.values():
            if isinstance(group, dict) and self._cleanup_group_slang_terms(group):
                changed = True
        return changed
