# -*- coding: utf-8 -*-
"""ReactionAssetLibraryPart03Mixin。

由 tools/split_mixin_domain.py 从 reaction_asset_library.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 383 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ReactionAssetLibrary）。
"""
from __future__ import annotations

import hashlib
import re
import statistics
import time
import uuid
from .helpers import _safe_float, _safe_int, _single_line
from .reaction_asset_library_shared import (
    MAX_SINGLE_FILE_BYTES,
    SUPPORTED_EXTENSIONS,
    _image_signature_matches,
    _query_list,
    _safe_filename,
    _semantic_features,
    _text_list,
)
from pathlib import Path
from typing import Any



class ReactionAssetLibraryPart03Mixin:
    """ReactionAssetLibraryPart03Mixin（从 ReactionAssetLibrary 拆出）。"""


    def find(
        self,
        query: Any,
        *,
        context: Any = "",
        scope: str = "private",
        selection_preferences: Any = None,
        selection_signature: Any = "",
        embedding_query: Any = None,
        embedding_provider_id: Any = "",
        embedding_score_threshold: float = 0.42,
        embedding_weight: float = 0.7,
        embedding_candidate_limit: int = 1200,
    ) -> dict[str, Any] | None:
        query_text = _single_line(query, 500)
        context_text = _single_line(context, 1000)
        scope_text = _single_line(scope, 20).casefold() or "private"
        if scope_text not in {"private", "group"}:
            return None
        context_tokens = self._tokens(context_text)
        # Structured reaction intents put a few alternate lookup phrases in
        # the context. Treat them as first-class queries so a generic provider
        # query does not drown out a useful model-supplied synonym.
        candidate_queries: list[str] = []
        candidate_match = re.search(
            r"(?:候选检索表达|候选检索词|候选表达)\s*[:：]\s*(.*)",
            context_text,
            flags=re.IGNORECASE,
        )
        if candidate_match:
            candidate_text = candidate_match.group(1)
            # The lookup context is a semicolon-delimited diagnostic string;
            # stop at the next labeled context section instead of treating
            # relationship/emotion JSON as a search phrase.
            candidate_text = re.split(
                r"；(?=(?:当前语境|近期用户意图|当前关系状态|情绪余波|用户对近期用户意图))",
                candidate_text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            candidate_queries = _query_list(candidate_text, limit=8)
        query_semantic_clusters, _ = _semantic_features(
            "；".join([query_text, *candidate_queries])
        )
        _, blocked_query_aliases = _semantic_features(query_text)
        # Avoid turning a negated phrase such as “不开心” into a positive
        # keyword hit merely because the shorter alias “开心” is present.
        query_tokens = [
            token
            for token in self._tokens(query_text)
            if not any(alias in token for alias in blocked_query_aliases)
        ]
        preference_rows: list[dict[str, Any]] = []
        if isinstance(selection_preferences, dict):
            raw_rows = selection_preferences.get("assets")
            if isinstance(raw_rows, list):
                preference_rows = [row for row in raw_rows if isinstance(row, dict)]
            elif isinstance(raw_rows, dict):
                preference_rows = [
                    {"key": key, **value}
                    for key, value in raw_rows.items()
                    if isinstance(value, dict)
                ]
        preference_by_key = {
            _single_line(row.get("key"), 180): row
            for row in preference_rows
            if _single_line(row.get("key"), 180)
        }

        def preference_bias(item: dict[str, Any]) -> float:
            if not preference_by_key:
                return 0.0
            keys = {
                _single_line(item.get("id"), 180),
                f"pc-local:{_single_line(item.get('id'), 160)}",
            }
            matched = next(
                (preference_by_key[key] for key in keys if key in preference_by_key),
                None,
            )
            if not isinstance(matched, dict):
                return 0.0
            total_score = _safe_float(matched.get("score"), 0.0, -20.0, 20.0)
            intent_score = _safe_float(matched.get("intent_score"), 0.0, -8.0, 8.0)
            # A same-intent preference has more weight, but never enough to
            # rescue a weak lexical match or overturn a clear topic mismatch.
            return max(-1.2, min(1.2, total_score * 0.06 + intent_score * 0.16))
        embedding_provider = _single_line(embedding_provider_id, 160)
        embedding_vector = self.normalize_embedding_vector(embedding_query)
        embedding_threshold = max(0.0, min(0.99, _safe_float(embedding_score_threshold, 0.42, 0.0, 0.99)))
        embedding_factor = max(0.0, min(2.0, _safe_float(embedding_weight, 0.7, 0.0, 2.0)))
        embedding_limit = _safe_int(embedding_candidate_limit, 1200, 20, 5000)
        with self._lock:
            raw_items = self._load()["items"]
            candidates = [self._normalize_item(raw) for raw in raw_items]
            embedding_rows = sorted(
                zip(raw_items, candidates),
                key=lambda row: row[1]["updated_at"],
                reverse=True,
            )[:embedding_limit]
            embeddings_by_id = {
                item["id"]: self.normalize_embedding_vector(raw.get("embedding"))
                for raw, item in embedding_rows
                if (
                    embedding_vector
                    and embedding_provider
                    and _single_line(raw.get("embedding_provider"), 160) == embedding_provider
                    and _single_line(raw.get("embedding_text_hash"), 80) == self.embedding_text_hash(item)
                )
            }
        eligible_rows: list[tuple[dict[str, Any], Path]] = []
        for item in candidates:
            path = self._path_for(item)
            if not item["enabled"] or scope_text not in item["scopes"] or path is None or not path.is_file():
                continue
            eligible_rows.append((item, path))
        # Text embeddings of short reaction captions are crowded: every asset
        # lands at cosine 0.4-0.8 for any query, so an absolute threshold passes
        # almost the whole catalog and the bonus barely separates assets. Rank
        # against the query's own distribution instead: the best asset earns
        # the full weight, the median asset earns nothing and assets well
        # below the median are demoted by the same amount.
        embedding_scores: dict[str, float] = {}
        for item, _path in eligible_rows:
            candidate_vector = embeddings_by_id.get(item["id"])
            if embedding_vector and candidate_vector and len(candidate_vector) == len(embedding_vector):
                embedding_scores[item["id"]] = max(
                    -1.0,
                    min(1.0, sum(left * right for left, right in zip(embedding_vector, candidate_vector))),
                )
        embedding_top = max(embedding_scores.values(), default=0.0)
        embedding_led = len(embedding_scores) >= 8 and embedding_top >= embedding_threshold
        embedding_median = statistics.median(embedding_scores.values()) if embedding_led else 0.0
        embedding_span = max(0.02, embedding_top - embedding_median)
        ranked: list[tuple[float, float, dict[str, Any], Path, list[str], float, float, float]] = []
        now = time.time()
        for item, path in eligible_rows:
            primary = " ".join(
                [
                    item["name"],
                    item["description"],
                    item["visible_text"],
                    *item["tags"],
                    *item["emotions"],
                    *item["intents"],
                ]
            ).casefold()
            secondary = item["filename"].casefold()
            item_semantic_clusters, _item_blocked_aliases = _semantic_features(primary)
            shared_semantic_clusters = query_semantic_clusters & item_semantic_clusters
            semantic_match = bool(shared_semantic_clusters)
            score = 0.0
            matched_phrases: list[str] = []
            if query_text and query_text.casefold() in primary:
                score += 1.7
                matched_phrases.append(query_text)
            for phrase in candidate_queries:
                phrase_key = phrase.casefold()
                if phrase_key and phrase_key != query_text.casefold() and phrase_key in primary:
                    score += 1.25
                    matched_phrases.append(phrase)
            for token in query_tokens:
                if token in primary:
                    score += 0.38 if len(token) <= 2 else 0.62
                elif token in secondary:
                    score += 0.2
            for phrase in candidate_queries:
                _phrase_clusters, blocked_phrase_aliases = _semantic_features(phrase)
                for token in self._tokens(phrase):
                    if any(alias in token for alias in blocked_phrase_aliases):
                        continue
                    if token in primary:
                        score += 0.28 if len(token) <= 2 else 0.48
                    elif token in secondary:
                        score += 0.14
            for token in context_tokens:
                if token in primary:
                    score += 0.1
            if not query_tokens and not query_text:
                score += 0.12
            # Local semantic equivalence is intentionally weaker than an
            # explicit lexical hit. It makes “高兴” find “开心” and “安慰”
            # find “抱抱”, while leaving unrelated assets below the floor.
            semantic_bonus = min(0.7, 0.4 * len(shared_semantic_clusters))
            if semantic_match:
                matched_phrases.append(
                    "语义相近：" + "、".join(sorted(shared_semantic_clusters))
                )
            embedding_score = embedding_scores.get(item["id"])
            if embedding_led:
                # Assets whose vector is missing or stale (caption re-analysed,
                # backfill pending) stay neutral rather than being demoted.
                embedding_bonus = (
                    embedding_factor * max(-1.0, min(1.0, (embedding_score - embedding_median) / embedding_span))
                    if embedding_score is not None
                    else 0.0
                )
                embedding_score = embedding_score or 0.0
                # ponytail: once vectors lead, lexical hits only break ties inside
                # the matched cluster. A stray tag on a wrong-category asset used
                # to win outright through the 1.7 phrase bonus.
                score = min(score, 0.5)
                semantic_bonus = min(semantic_bonus, 0.3)
            else:
                embedding_score = embedding_score or 0.0
                embedding_bonus = (
                    embedding_factor * max(0.0, (embedding_score - embedding_threshold) / max(0.01, 1.0 - embedding_threshold))
                    if embedding_score >= embedding_threshold
                    else 0.0
                )
            relevance_score = score + semantic_bonus + embedding_bonus
            if embedding_bonus > 0.0 and not matched_phrases:
                matched_phrases.append("语义相近")
            diversity_penalty = min(item["usage_count"], 20) * 0.004
            last_used_at = _safe_float(item.get("last_used_at"), 0.0, 0.0)
            if last_used_at > 0:
                age_seconds = max(0.0, now - last_used_at)
                if age_seconds < 6 * 3600:
                    diversity_penalty += 1.1 * (1.0 - age_seconds / (6 * 3600))
                elif age_seconds < 24 * 3600:
                    diversity_penalty += 0.18 * (
                        1.0 - (age_seconds - 6 * 3600) / (18 * 3600)
                    )
            learned_bias = preference_bias(item)
            ranked.append(
                (
                    relevance_score - diversity_penalty + learned_bias,
                    relevance_score,
                    item,
                    path,
                    matched_phrases,
                    learned_bias,
                    embedding_score,
                    embedding_bonus,
                )
            )
        if not ranked:
            return None
        best_relevance = max(row[1] for row in ranked)
        relevance_floor = best_relevance - 0.65
        if query_tokens:
            relevance_floor = max(0.22, relevance_floor)
        eligible = [row for row in ranked if row[1] >= relevance_floor]
        if not eligible:
            return None
        eligible.sort(key=lambda row: (row[0], row[2]["updated_at"]), reverse=True)
        _selection_score, score, item, path, matched_phrases, learned_bias, embedding_score, embedding_bonus = eligible[0]
        semantic_match = any(
            phrase.startswith("语义相近：") for phrase in matched_phrases
        )
        # A weak lexical match is not enough to force an image into the conversation.
        if query_tokens and score < 0.22 and not semantic_match and embedding_score < embedding_threshold:
            return None
        confidence = max(0.22, min(0.99, 0.35 + score / 2.8))
        return {
            "success": True,
            "status": "success",
            "found": True,
            "image_id": f"pc-local:{item['id']}",
            "asset_id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "path": str(path),
            "tags": [*item["tags"], *item["emotions"], *item["intents"]][:20],
            "need": query_text,
            "matched_queries": matched_phrases,
            "candidate_queries": candidate_queries,
            "reason": (
                "本插件素材库按关键词及本地语义近似匹配"
                if semantic_match and embedding_bonus <= 0.0
                else "本插件素材库按候选检索表达、标签、情绪和沟通用途匹配"
                if matched_phrases
                else "本插件素材库按标签、情绪和沟通用途匹配"
            ),
            "confidence": round(confidence, 3),
            "preference_bias": round(learned_bias, 3),
            "embedding_score": round(embedding_score, 4) if embedding_score else 0.0,
            "match_basis": (
                "embedding"
                if embedding_bonus > 0.0 and embedding_bonus >= score - embedding_bonus
                else "hybrid"
                if embedding_bonus > 0.0
                else "keyword_semantic"
                if semantic_match
                else "keyword"
            ),
            "provider": "private_companion_library",
        }

    def mark_used(self, item_id: Any) -> bool:
        item_key = _single_line(item_id, 64)
        if item_key.startswith("pc-local:"):
            item_key = item_key.split(":", 1)[1]
        if not item_key:
            return False
        with self._lock:
            catalog = self._load()
            changed = False
            for index, raw in enumerate(catalog["items"]):
                item = self._normalize_item(raw)
                if item["id"] != item_key:
                    continue
                item["usage_count"] += 1
                item["last_used_at"] = time.time()
                catalog["items"][index] = item
                changed = True
                break
            if changed:
                self._usage.mark_used(
                    item_key,
                    baseline_count=max(0, item["usage_count"] - 1),
                    used_at=item["last_used_at"],
                )
                self._cached_summary = None
                path = self._path_for(item)
                if path is None or not path.is_file():
                    self._lookup_index.checked_at = 0.0
                self._selection_revision += 1
            return changed

    def rescan(self) -> dict[str, Any]:
        with self._lock:
            catalog = self._load()
            indexed = {_safe_filename(item.get("stored_name")) for item in catalog["items"] if isinstance(item, dict)}
            hashes = {
                _single_line(item.get("sha256"), 64).lower()
                for item in catalog["items"]
                if isinstance(item, dict)
            }
            imported: list[dict[str, Any]] = []
            duplicates: list[str] = []
            rejected: list[dict[str, str]] = []
            scanned = 0
            for path in self.images_dir.iterdir():
                if not path.is_file() or path.name in indexed or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                scanned += 1
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if not data or len(data) > MAX_SINGLE_FILE_BYTES or not _image_signature_matches(data, path.suffix.lower()):
                    rejected.append({"name": path.name, "reason": "图片格式无效或超过 20 MB"})
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if digest in hashes:
                    duplicates.append(path.name)
                    rejected.append({"name": path.name, "reason": "内容已存在于索引"})
                    continue
                now = time.time()
                width, height = self._dimensions(data)
                item = self._normalize_item(
                    {
                        "id": uuid.uuid4().hex,
                        "filename": path.name,
                        "stored_name": path.name,
                        "sha256": digest,
                        "name": path.stem,
                        "tags": _text_list(re.sub(r"[_\-.]+", " ", path.stem), limit=8),
                        "scopes": ["private", "group"],
                        "enabled": True,
                        "source": "rescan",
                        "size": len(data),
                        "width": width,
                        "height": height,
                        "analysis_status": "pending",
                        "manual_fields": [],
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                catalog["items"].append(item)
                imported.append(item)
                hashes.add(digest)
            if imported:
                self._save(catalog)
        return {
            "scanned": scanned,
            "imported": len(imported),
            "duplicates": duplicates,
            "rejected": rejected,
            "items": imported,
            "analysis_queued": sum(1 for item in imported if item["analysis_status"] == "pending"),
            "summary": self.summary(),
        }
