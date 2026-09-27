# -*- coding: utf-8 -*-
"""GroupObservationTopicRelationshipMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 479 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import asyncio
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .group_observation_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from datetime import datetime
from typing import Any



class GroupObservationTopicRelationshipMixin:
    """GroupObservationTopicRelationshipMixin（从 GroupObservationMixin 拆出）。"""


    def _group_topic_signature(self, text: str) -> str:
        return self._proactive_topic_signature(text)

    def _update_group_topic_threads(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        if self._group_text_blocked_by_injection_guard(text):
            return
        signature = self._group_topic_signature(text)
        if not signature:
            return
        threads = group.setdefault("topic_threads", [])
        if not isinstance(threads, list):
            threads = []
            group["topic_threads"] = threads
        now = _now_ts()
        active_threads = [
            item for item in threads
            if isinstance(item, dict) and now - _safe_float(item.get("last_ts"), 0) <= 90 * 60
        ]
        matched = None
        for item in active_threads:
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                matched = item
                break
        if not matched:
            matched = {
                "signature": signature,
                "title": _single_line(text, 40),
                "started_ts": now,
                "last_ts": now,
                "participants": [],
                "message_count": 0,
                "bot_joined": False,
                "recent_examples": [],
            }
            active_threads.append(matched)
        matched["last_ts"] = now
        matched["message_count"] = _safe_int(matched.get("message_count"), 0, 0) + 1
        participants = matched.setdefault("participants", [])
        if not isinstance(participants, list):
            participants = []
            matched["participants"] = participants
        if sender_id and sender_id not in participants:
            participants.append(sender_id)
        examples = matched.setdefault("recent_examples", [])
        if not isinstance(examples, list):
            examples = []
            matched["recent_examples"] = examples
        examples.append(
            {
                "sender_id": sender_id,
                "name": self._group_member_identity_name(sender_id, sender_name, limit=20),
                "text": _single_line(text, 80),
                "ts": now,
            }
        )
        del examples[:-6]
        active_threads.sort(key=lambda item: _safe_float(item.get("last_ts"), 0), reverse=True)
        group["topic_threads"] = active_threads[: _safe_int(_persona_value(self, "max_group_topic_threads", 20), 20, 1)]

    def _update_group_interjection_feedback(self, group: dict[str, Any], *, sender_id: str, text: str) -> None:
        last = group.get("last_bot_interjection")
        if not isinstance(last, dict) or not last:
            return
        sent_ts = _safe_float(last.get("ts"), 0)
        if sent_ts <= 0 or _now_ts() - sent_ts > 10 * 60:
            return
        if sender_id == str(last.get("bot_sender_id") or ""):
            return
        feedback = group.setdefault("interjection_feedback", {})
        if not isinstance(feedback, dict):
            feedback = {}
            group["interjection_feedback"] = feedback
        feedback["replies_after"] = _safe_int(feedback.get("replies_after"), 0, 0) + 1
        if re.search(r"(哈哈|笑死|草|绷|乐|hhh|可以|确实|对啊)", text, re.IGNORECASE):
            feedback["positive"] = _safe_int(feedback.get("positive"), 0, 0) + 1
        if re.search(r"(别吵|闭嘴|吵死|机器人|别发|烦)", text):
            feedback["negative"] = _safe_int(feedback.get("negative"), 0, 0) + 1
        last["last_feedback_at"] = _now_ts()

    def _update_group_relationship_graph(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        if self._group_text_blocked_by_injection_guard(text):
            return
        last = group.get("last_speaker")
        now = _now_ts()
        if isinstance(last, dict):
            prev_id = str(last.get("sender_id") or "")
            prev_name = self._group_member_identity_name(prev_id, last.get("identity_name") or last.get("name"), limit=30)
            prev_ts = _safe_float(last.get("ts"), 0)
            if prev_id and prev_id != sender_id and now - prev_ts <= 180:
                left, right = sorted([prev_id, sender_id])
                current_name = self._group_member_identity_name(sender_id, sender_name, limit=30)
                key = f"{left}|{right}"
                edges = group.setdefault("relationship_edges", {})
                if not isinstance(edges, dict):
                    edges = {}
                    group["relationship_edges"] = edges
                edge = edges.setdefault(
                    key,
                    {
                        "a": left,
                        "b": right,
                        "a_name": prev_name if left == prev_id else current_name,
                        "b_name": current_name if right == sender_id else prev_name,
                        "count": 0,
                        "tone": {},
                        "last_ts": 0,
                    },
                )
                if isinstance(edge, dict):
                    edge["a_name"] = self._group_member_identity_name(left, edge.get("a_name"), limit=30)
                    edge["b_name"] = self._group_member_identity_name(right, edge.get("b_name"), limit=30)
                    edge["count"] = _safe_int(edge.get("count"), 0, 0) + 1
                    edge["last_ts"] = now
                    tone = edge.setdefault("tone", {})
                    if not isinstance(tone, dict):
                        tone = {}
                        edge["tone"] = tone
                    tone_key = "玩笑" if re.search(r"(哈哈|笑死|草|绷|乐|hhh)", text, re.IGNORECASE) else "普通"
                    if re.search(r"(吵|骂|别|烦|急)", text):
                        tone_key = "紧绷"
                    tone[tone_key] = _safe_int(tone.get(tone_key), 0, 0) + 1
                max_edges = _safe_int(_persona_value(self, "max_group_relationship_edges", 80), 80, 1)
                if len(edges) > max_edges:
                    ranked = sorted(
                        edges.items(),
                        key=lambda item: (_safe_int((item[1] or {}).get("count"), 0, 0), _safe_float((item[1] or {}).get("last_ts"), 0)),
                        reverse=True,
                    )
                    group["relationship_edges"] = dict(ranked[: max_edges])
        group["last_speaker"] = {
            "sender_id": sender_id,
            "name": _single_line(sender_name, 30) or sender_id,
            "identity_name": self._group_member_identity_name(sender_id, sender_name, limit=30),
            "ts": now,
            "text": _single_line(text, 80),
        }

    def _format_group_relationship_graph_for_prompt(self, group: dict[str, Any], sender_id: str = "", text: str = "") -> str:
        edges = group.get("relationship_edges")
        if not isinstance(edges, dict):
            return ""
        cleaned = _single_line(text, 160)
        recent = self._filtered_group_recent_messages(group)
        recent_ids = {
            str(item.get("sender_id") or "")
            for item in recent[-8:]
            if isinstance(item, dict) and item.get("sender_id")
        }
        focus_ids = {str(sender_id or "").strip(), *recent_ids}
        ranked_all = sorted(
            [item for item in edges.values() if isinstance(item, dict)],
            key=lambda item: (
                1 if str(item.get("a") or "") in focus_ids or str(item.get("b") or "") in focus_ids else 0,
                _safe_float(item.get("last_ts"), 0),
                _safe_int(item.get("count"), 0, 0),
            ),
            reverse=True,
        )
        relevant = []
        for item in ranked_all:
            a_id = str(item.get("a") or "")
            b_id = str(item.get("b") or "")
            a_name = _single_line(item.get("a_name"), 24)
            b_name = _single_line(item.get("b_name"), 24)
            if sender_id and (a_id == str(sender_id) or b_id == str(sender_id)):
                relevant.append(item)
                continue
            if cleaned and ((a_name and a_name in cleaned) or (b_name and b_name in cleaned)):
                relevant.append(item)
                continue
            if a_id in recent_ids or b_id in recent_ids:
                relevant.append(item)
        ranked = relevant[:4]
        lines = []
        for item in ranked:
            a_id = str(item.get("a") or "")
            b_id = str(item.get("b") or "")
            a_name = self._group_member_identity_label(a_id, item.get("a_name"), limit=16) if a_id else (_single_line(item.get("a_name"), 16) or "群友A")
            b_name = self._group_member_identity_label(b_id, item.get("b_name"), limit=16) if b_id else (_single_line(item.get("b_name"), 16) or "群友B")
            tone = item.get("tone") if isinstance(item.get("tone"), dict) else {}
            main_tone = "普通"
            if tone:
                main_tone = max(tone.items(), key=lambda pair: _safe_int(pair[1], 0, 0))[0]
            line = f"- {a_name} ↔ {b_name}"
            if main_tone and main_tone != "普通":
                line += f"｜常见氛围 {main_tone}"
            lines.append(line)
        return "\n".join(lines)

    def _format_group_slang_meanings_for_prompt(self, group: dict[str, Any]) -> str:
        meanings = group.get("slang_meanings")
        if not isinstance(meanings, dict) or not meanings:
            return ""
        lines = []
        for term, item in list(meanings.items())[:10]:
            if not isinstance(item, dict):
                continue
            meaning = _single_line(item.get("meaning"), 80)
            usage = _single_line(item.get("usage"), 80)
            not_owner = _single_line(item.get("not_owner"), 80)
            confidence = min(1.0, _safe_float(item.get("confidence"), 1.0, 0.0))
            if self._group_text_blocked_by_injection_guard(f"{term} {meaning} {usage} {not_owner}"):
                continue
            if self._is_uncertain_group_slang_meaning(meaning, usage) or confidence < 0.55:
                continue
            if meaning:
                source = _single_line(item.get("source"), 30)
                lines.append(
                    f"- {term}：{meaning}"
                    + (f"｜不是：{not_owner}" if not_owner else "")
                    + (f"｜用法：{usage}" if usage else "")
                    + ("｜显式纠正" if source == "explicit_correction" else "")
                    + ("｜手动校正" if source == "manual" else "")
                )
        return "\n".join(lines)

    async def _group_slang_embedding_body(
        self,
        group: dict[str, Any],
        text: Any,
    ) -> str:
        """Soft-retrieve confirmed group slang meanings for an unfamiliar short expression."""
        if not bool(_persona_value(self, "enable_group_slang_meanings", False)):
            return ""
        cleaned = _single_line(text, 160)
        meanings = group.get("slang_meanings") if isinstance(group, dict) else None
        if not cleaned or not isinstance(meanings, dict) or not meanings:
            return ""
        candidates = self._group_slang_candidates_from_text(cleaned)
        if not candidates:
            return ""

        eligible: list[tuple[str, str]] = []
        known_terms: set[str] = set()
        for raw_term, item in list(meanings.items())[:40]:
            if not isinstance(item, dict):
                continue
            term = _single_line(raw_term, 24)
            meaning = _single_line(item.get("meaning"), 100)
            usage = _single_line(item.get("usage"), 80)
            confidence = min(1.0, _safe_float(item.get("confidence"), 1.0, 0.0))
            if (
                not term
                or not meaning
                or confidence < 0.55
                or self._is_uncertain_group_slang_meaning(meaning, usage)
                or self._group_text_blocked_by_injection_guard(f"{term} {meaning} {usage}")
            ):
                continue
            known_terms.add(term.casefold())
            eligible.append((term, f"群内表达：{term}；含义：{meaning}" + (f"；用法：{usage}" if usage else "")))
        unknown = [item for item in candidates if item.casefold() not in known_terms]
        if not unknown or not eligible:
            return ""

        # R1/R2: cache the embedding query by its term set instead of by the raw
        # message, and rate-limit the (network) soft recall per group.  A heated
        # group chat would otherwise fire an embedding request for every reply.
        now_ts = _now_ts()
        cache_memo = getattr(self, "_group_slang_embedding_query_memo", None)
        if not isinstance(cache_memo, dict):
            cache_memo = {}
            setattr(self, "_group_slang_embedding_query_memo", cache_memo)
        group_key = str(group.get("group_id") or group.get("group_name") or "")
        query_key = f"群聊里出现的新表达：{'、'.join(unknown[:4])}"
        memo_key = f"{group_key}\n{query_key}"
        memo_ttl = max(60.0, _safe_float(_persona_value(self, "group_slang_embedding_memo_ttl_seconds", 300), 300, 0))
        memoized = cache_memo.get(memo_key)
        if isinstance(memoized, tuple) and len(memoized) == 2 and now_ts - memoized[0] < memo_ttl:
            return memoized[1]
        cooldown_seconds = max(0.0, _safe_float(_persona_value(self, "group_slang_embedding_cooldown_seconds", 30), 30, 0))
        last_run_key = f"{group_key}\nlast_run_at"
        last_run_at = cache_memo.get(last_run_key)
        if isinstance(last_run_at, (int, float)) and now_ts - last_run_at < cooldown_seconds:
            return ""
        if len(cache_memo) >= 512:
            for stale_key in list(cache_memo)[:128]:
                cache_memo.pop(stale_key, None)
        cache_memo[last_run_key] = now_ts

        provider_getter = getattr(self, "_shared_embedding_provider", None)
        vector_getter = getattr(self, "_reaction_embedding_vector", None)
        vectors_getter = getattr(self, "_reaction_embedding_vectors", None)
        if not callable(provider_getter) or not callable(vector_getter):
            return ""
        try:
            provider, provider_id = await provider_getter()
        except Exception as exc:
            logger.debug("群黑话嵌入模型解析失败: %s", _single_line(exc, 120))
            return ""
        if provider is None or not provider_id:
            return ""

        cache = getattr(self, "_shared_embedding_vector_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_shared_embedding_vector_cache", cache)

        async def vector_for(value: str) -> list[float]:
            key = f"{provider_id}\n{value}"
            cached = cache.get(key)
            if isinstance(cached, list) and cached:
                return cached
            vector = await vector_getter(provider, value)
            if vector:
                if len(cache) >= 256:
                    for stale_key in list(cache)[:64]:
                        cache.pop(stale_key, None)
                cache[key] = vector
            return vector

        async def vectors_for(values: list[str]) -> list[list[float]]:
            results: list[list[float]] = [[] for _item in values]
            missing_indexes: list[int] = []
            missing_values: list[str] = []
            for index, value in enumerate(values):
                cached = cache.get(f"{provider_id}\n{value}")
                if isinstance(cached, list) and cached:
                    results[index] = cached
                else:
                    missing_indexes.append(index)
                    missing_values.append(value)
            if missing_values:
                if callable(vectors_getter):
                    generated = await vectors_getter(provider, missing_values)
                else:
                    generated = await asyncio.gather(*(vector_for(value) for value in missing_values))
                if len(generated) != len(missing_values):
                    return []
                for index, value, vector in zip(missing_indexes, missing_values, generated):
                    if not vector:
                        return []
                    if len(cache) >= 256:
                        for stale_key in list(cache)[:64]:
                            cache.pop(stale_key, None)
                    cache[f"{provider_id}\n{value}"] = vector
                    results[index] = vector
            return results

        query_text = query_key
        try:
            query_vector = await vector_for(query_text)
            if not query_vector:
                return ""
            ranked: list[tuple[float, str, str]] = []
            selected = eligible[:12]
            vectors = await vectors_for([meaning_text for _term, meaning_text in selected])
            for (term, meaning_text), vector in zip(selected, vectors):
                if not vector or len(vector) != len(query_vector):
                    continue
                score = sum(left * right for left, right in zip(query_vector, vector))
                if score >= 0.68:
                    ranked.append((score, term, meaning_text))
        except Exception as exc:
            logger.debug("群黑话向量软召回失败: %s", _single_line(exc, 120))
            return ""
        if not ranked:
            return ""
        ranked.sort(reverse=True)
        lines: list[str] = []
        for score, term, meaning_text in ranked[:2]:
            detail = meaning_text.split("；含义：", 1)[-1]
            lines.append(f"- 当前“{unknown[0]}”可能接近本群“{term}”：{detail}（相似度 {score:.2f}）")
        lines.append("只有结合当前原句确实说得通时才采用；不要把向量近似当成确定词义或用户纠正。")
        result = "\n".join(lines)
        cache_memo[memo_key] = (now_ts, result)
        return result

    async def _group_slang_embedding_prompt_section(
        self,
        group: dict[str, Any],
        text: Any,
    ) -> PromptSection:
        return prompt_section(
            key="group.slang_similarity",
            title="群内黑话语义近似（仅作软参考）",
            source="group_observation",
            content=await self._group_slang_embedding_body(group, text),
        )

    async def _group_slang_embedding_context(
        self,
        group: dict[str, Any],
        text: Any,
    ) -> str:
        section = await self._group_slang_embedding_prompt_section(group, text)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _is_uncertain_group_slang_meaning(self, meaning: str = "", usage: str = "") -> bool:
        text = _single_line(f"{meaning} {usage}", 180)
        if not text:
            return True
        uncertain_markers = (
            "语境不明", "上下文不明", "含义不明", "无法判断", "不能判断", "暂不确定",
            "不确定", "不清楚", "看不出", "未看出", "无法确定", "可能是", "大概是",
            "也许是", "疑似", "需要更多上下文", "需要结合上下文",
        )
        return any(marker in text for marker in uncertain_markers)

    def _prune_uncertain_group_slang_meanings(self, group: dict[str, Any]) -> int:
        meanings = group.get("slang_meanings")
        if not isinstance(meanings, dict):
            return 0
        removed = 0
        for term, item in list(meanings.items()):
            if not isinstance(item, dict):
                continue
            if item.get("source") in {"explicit_correction", "manual"}:
                continue
            confidence = min(1.0, _safe_float(item.get("confidence"), 1.0, 0.0))
            if confidence < 0.55 or self._is_uncertain_group_slang_meaning(item.get("meaning"), item.get("usage")):
                meanings.pop(term, None)
                removed += 1
        return removed

    @staticmethod
    def _group_slang_meaning_age_seconds(item: Any) -> float:
        """Age of a slang_meanings entry in seconds; unknown timestamps are treated as fresh."""
        if not isinstance(item, dict):
            return 0.0
        raw = item.get("updated_at") or item.get("ts")
        if isinstance(raw, (int, float)) and raw > 1e12:
            raw = raw / 1000.0
        if isinstance(raw, (int, float)):
            age = float(_now_ts() - raw)
            # 未来时间戳（时钟漂移/异常写入）视为已过期，避免被当成“刚更新”而永不淘汰。
            if age < 0.0:
                return float("inf")
            return age
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()[:19]
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    age = float(_now_ts() - datetime.strptime(text, fmt).timestamp())
                    if age < 0.0:
                        return float("inf")
                    return age
                except Exception:
                    continue
        return 0.0

    def _enforce_group_slang_meanings_budget(self, group: dict[str, Any]) -> int:
        """Cap slang_meanings size and drop stale non-pinned entries (R3).

        Pinned sources (explicit_correction / manual) are user-driven and never
        dropped; learned LLM entries age out by TTL or are evicted by confidence.
        """
        meanings = group.get("slang_meanings")
        if not isinstance(meanings, dict):
            return 0
        max_entries = max(8, _safe_int(_persona_value(self, "max_group_slang_meanings", 120), 120, 8))
        ttl_days = max(0, _safe_int(_persona_value(self, "group_slang_meanings_ttl_days", 180), 180, 0))
        pinned_sources = {"explicit_correction", "manual"}
        removed = 0
        if ttl_days > 0:
            ttl_seconds = float(ttl_days) * 86400
            for term, item in list(meanings.items()):
                if not isinstance(item, dict) or item.get("source") in pinned_sources:
                    continue
                if self._group_slang_meaning_age_seconds(item) > ttl_seconds:
                    meanings.pop(term, None)
                    removed += 1
        while len(meanings) > max_entries:
            candidates = [
                (term, item)
                for term, item in meanings.items()
                if isinstance(item, dict) and item.get("source") not in pinned_sources
            ]
            if not candidates:
                break
            candidates.sort(key=lambda entry: _safe_float(entry[1].get("confidence"), 0.0, 0.0))
            meanings.pop(candidates[0][0], None)
            removed += 1
        return removed
