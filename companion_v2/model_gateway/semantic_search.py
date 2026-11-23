"""Shared ranking of domain-validated catalog items, with explicit coverage.

The blend used to be one line (``lexical * 0.25 + max(0, cosine)``), which is
enough for memory and group vocabulary but not for reaction originals: their
captions are short, so every asset lands inside the same cosine band and an
absolute bonus cannot separate them.  V1 solved that with a "vector leads" mode
(``reaction_asset_library.py:1234-1327``).  The ranking parameters are therefore
optional keyword arguments with the previous behaviour as their defaults, so the
memory and slang callers in ``expression/semantic_service.py`` keep the exact
score, admission gate and ordering they had.
"""

import asyncio
import logging
import statistics

from companion_v2.memory.search import terms
from companion_v2.storage.semantic import VECTOR_BLOB_MAGIC, batch_semantic_dots, decode_vector_f32
logger = logging.getLogger(__name__)


def _vector_has_dims(raw, dims):
    if raw is None:
        return False
    if isinstance(raw, (bytes, bytearray, memoryview)):
        mv = memoryview(raw)
        if len(mv) >= 4 and mv[:4].tobytes() == VECTOR_BLOB_MAGIC:
            return len(mv) == 4 + dims * 2
    if isinstance(raw, (list, tuple)):
        return len(raw) == dims
    decoded = decode_vector_f32(raw)
    if decoded is None:
        return False
    return len(decoded) == dims


# V2 defaults: they reproduce ``lexical * 0.25 + max(0, cosine)`` with the
# existing ``cosine >= 0.4`` admission gate, bit for bit.
DEFAULT_LEXICAL_WEIGHT = 0.25
DEFAULT_EMBEDDING_WEIGHT = 1.0
DEFAULT_EMBEDDING_THRESHOLD = 0.0
DEFAULT_ADMISSION_SIMILARITY = 0.4
# A candidate without a comparable vector used to score ``lexical * 0.25 + lexical``:
# the raw lexical score stood in for the missing cosine. Reaction lookup keeps
# that fallback at zero, because V1 never boosted an asset for *lacking* a vector.
DEFAULT_LEXICAL_FALLBACK_WEIGHT = 1.0

# V1 parity constants for the reaction caller (``reaction_asset_library.py``).
VECTOR_LED_MIN_CANDIDATES = 8      # :1249
VECTOR_LED_LEXICAL_CAP = 0.5       # :1319
SEMANTIC_CLUSTER_BONUS = 0.4       # :1301
SEMANTIC_CLUSTER_BONUS_CAP = 0.7   # :1301
VECTOR_LED_SEMANTIC_BONUS_CAP = 0.3  # :1320

# One embedding request is bounded by the gateway; a backfill run may span several.
BACKFILL_REQUEST_CHUNK = 24


def _bounded(value, low, high):
    return max(low, min(high, value))


class CatalogSearch:
    def __init__(self, store, gateway, validate):
        self.store, self.gateway, self.validate = store, gateway, validate

    async def search(self, owner, kind, text, *, context=None, limit=5, validate=None,
                     lexical_weight=DEFAULT_LEXICAL_WEIGHT,
                     lexical_fallback_weight=DEFAULT_LEXICAL_FALLBACK_WEIGHT,
                     embedding_weight=DEFAULT_EMBEDDING_WEIGHT,
                     embedding_score_threshold=DEFAULT_EMBEDDING_THRESHOLD,
                     admission_similarity=DEFAULT_ADMISSION_SIMILARITY,
                     embedding_candidate_limit=None,
                     vector_led_min_candidates=None,
                     relevance_floor_delta=None,
                     minimum_relevance=0.0,
                     shared_clusters=None,
                     query_terms_fn=None):
        """Rank one owner's catalog for ``text``.

        ``embedding_candidate_limit`` caps how many items take part in vector
        comparison; items beyond the cap keep their lexical score and stay
        neutral, exactly like an item whose vector is still missing
        (V1 ``reaction_asset_library.py:1308-1314``).  ``vector_led_min_candidates``
        enables V1's distribution-relative mode once that many candidates carry a
        vector and the best one clears the threshold.  ``relevance_floor_delta``
        drops candidates below ``best - delta`` (and below ``minimum_relevance``
        when the query has terms) instead of returning a weak match.
        ``shared_clusters`` is an injected ``(query, document) -> names`` callable
        so this shared module never imports the expression domain.
        """
        valid = validate or self.validate
        cfg = self.store.settings()
        query_vector, model, error = None, None, None
        if cfg["enabled"]:
            try:
                model, values = await self.gateway.embed(owner, [text], cfg["embedding_provider"])
                query_vector = values[0]
            except Exception as exc:
                error = getattr(exc, "code", type(exc).__name__)
        query_terms = set(query_terms_fn(text) if query_terms_fn else terms(text))
        scored, after, scanned, embedded, compared = [], "", 0, 0, 0
        # Keyset pages ensure old items can be selected; only model work is batched.
        while True:
            page = self.store.list(owner, kind, after=after)
            if not page:
                break
            after = page[-1]["id"]
            eligible = [r for r in page if r["enabled"] and valid(r, context)]
            batch_loader = getattr(self.store, "vectors_for_items", None)
            if model and callable(batch_loader):
                page_vectors = batch_loader(owner, kind, model, eligible)
            elif model:
                page_vectors = {}
                for r in eligible:
                    vec = self.store.vector(r, model)
                    if vec is not None:
                        page_vectors[r["id"]] = vec
            else:
                page_vectors = {}
            missing = [r for r in eligible if model and r["id"] not in page_vectors]
            for start in range(0, len(missing), cfg["batch_size"]):
                batch = missing[start:start + cfg["batch_size"]]
                try:
                    binding, vectors = await self.gateway.embed(owner, [r["payload"]["text"][:8000] for r in batch], cfg["embedding_provider"])
                    if binding != model:
                        raise ValueError("embedding model changed")
                    for item, vector in zip(batch, vectors):
                        if valid(item, context):
                            if self.store.put_vector(item, model, vector):
                                page_vectors[item["id"]] = vector
                except Exception as exc:
                    error = getattr(exc, "code", type(exc).__name__)
                    break
            validated_page = []
            dot_candidates = []
            query_dims = len(query_vector) if query_vector else 0
            for item in eligible:
                if not valid(item, context):
                    continue
                scanned += 1
                idx = len(validated_page)
                validated_page.append(item)
                raw_vec = page_vectors.get(item["id"]) if model else None
                if raw_vec is not None and query_vector and _vector_has_dims(raw_vec, query_dims):
                    if embedding_candidate_limit is None or compared < embedding_candidate_limit:
                        compared += 1
                        dot_candidates.append((idx, raw_vec))
            cosines = batch_semantic_dots(query_vector, dot_candidates) if dot_candidates else {}
            embedded += len(cosines)
            for idx, item in enumerate(validated_page):
                document = item["payload"]["text"]
                lexical = len(query_terms.intersection(terms(document))) / max(1, len(query_terms))
                clusters = tuple(shared_clusters(text, document)) if shared_clusters else ()
                cosine = cosines.get(idx)
                if lexical or clusters or (cosine is not None and cosine >= admission_similarity):
                    scored.append({"item": item, "lexical": lexical, "cosine": cosine, "clusters": clusters})
            # Yield between local pages and allow host shutdown to cancel this work.
            await asyncio.sleep(0)
        rows, led = self._rank(scored, lexical_weight=lexical_weight,
            lexical_fallback_weight=lexical_fallback_weight, embedding_weight=embedding_weight,
            embedding_score_threshold=embedding_score_threshold,
            vector_led_min_candidates=vector_led_min_candidates)
        rows.sort(key=lambda r: (-r["score"], r["item"]["id"]))
        if relevance_floor_delta is not None and rows:
            floor = rows[0]["score"] - relevance_floor_delta
            if query_terms:
                floor = max(minimum_relevance, floor)
            rows = [row for row in rows if row["score"] >= floor]
        selected = rows[:max(limit * 3, 12)]
        if selected and cfg["rerank_provider"]:
            try:
                scores = await self.gateway.rerank(cfg["rerank_provider"], text, [r["item"]["payload"]["text"] for r in selected])
                if scores is not None:
                    selected = [r for _, r in sorted(zip(scores, selected), key=lambda pair: pair[0], reverse=True)]
            except Exception as exc:
                error = getattr(exc, "code", type(exc).__name__)
        return {"items": [{"id": r["item"]["id"], "revision": r["item"]["revision"], "payload": r["item"]["payload"],
                           "score": round(r["score"], 4), "similarity": r["cosine"],
                           "basis": r["basis"], "clusters": list(r["clusters"])} for r in selected[:limit]
                          if valid(r["item"], context)],
                "mode": "hybrid" if query_vector else "lexical", "embedded": embedded,
                "ranking": "vector_led" if led else "blended",
                "eligible": scanned, "complete": True, "fallback_reason": error}

    @staticmethod
    def _rank(scored, *, lexical_weight, lexical_fallback_weight, embedding_weight,
              embedding_score_threshold, vector_led_min_candidates):
        """Score admitted candidates; V1's two vector modes, applied after the scan."""
        similarities = [row["cosine"] for row in scored if row["cosine"] is not None]
        best = max(similarities, default=0.0)
        led = (vector_led_min_candidates is not None and len(similarities) >= vector_led_min_candidates
               and best >= embedding_score_threshold)
        median = statistics.median(similarities) if led else 0.0
        # A near-flat distribution must not be amplified into a huge bonus.
        span = max(0.02, best - median)
        rows = []
        for row in scored:
            lexical = row["lexical"] * lexical_weight
            if row["cosine"] is None:
                lexical += row["lexical"] * lexical_fallback_weight
            if led:
                # Once vectors lead, lexical hits only break ties inside the
                # matched cluster: a stray tag on a wrong-category asset used to
                # win outright through the phrase bonus.
                lexical = min(lexical, VECTOR_LED_LEXICAL_CAP)
                cap = VECTOR_LED_SEMANTIC_BONUS_CAP
                bonus = (embedding_weight * _bounded((row["cosine"] - median) / span, -1.0, 1.0)
                         if row["cosine"] is not None else 0.0)
            else:
                cap = SEMANTIC_CLUSTER_BONUS_CAP
                bonus = (embedding_weight * max(0.0, (row["cosine"] - embedding_score_threshold)
                                                / max(0.01, 1.0 - embedding_score_threshold))
                         if row["cosine"] is not None and row["cosine"] >= embedding_score_threshold else 0.0)
            semantic = min(cap, SEMANTIC_CLUSTER_BONUS * len(row["clusters"]))
            basis = ("embedding" if bonus > 0.0 and bonus >= lexical
                     else "hybrid" if bonus > 0.0
                     else "keyword_semantic" if row["clusters"] else "keyword")
            rows.append({"item": row["item"], "cosine": row["cosine"], "clusters": row["clusters"],
                         "basis": basis, "score": lexical + semantic + bonus})
        return rows, led

    async def backfill(self, owner, kind, *, limit, validate=None):
        """Fill at most ``limit`` stale vectors for one owner; returns the count written.

        V1 ran this from its lifecycle background tasks (``llm_tool_actions.py:5789-5837``).
        Only the scan is bounded by ``limit``; the caller owns throttling,
        per-owner serialisation and the enable switch.
        """
        if type(limit) is not int or limit <= 0:
            return 0
        cfg = self.store.settings()
        if not cfg["enabled"]:
            return 0
        # The current binding decides freshness; if it cannot be resolved the
        # stored models are the fallback, and several of them mean "unknown".
        try:
            model = self.gateway.port.binding(cfg["embedding_provider"], "embedding")
        except Exception:
            with self.store.db() as db:
                models = {row["model"] for row in db.execute(
                    "SELECT DISTINCT model FROM vectors WHERE owner=? AND kind=?", (owner, kind))}
            model = next(iter(models)) if len(models) == 1 else None
        pending, after = [], ""
        batch_loader = getattr(self.store, "vectors_for_items", None)
        while len(pending) < limit:
            page = self.store.list(owner, kind, after=after)
            if not page:
                break
            after = page[-1]["id"]
            existing_vectors = (
                batch_loader(owner, kind, model, page)
                if model is not None and callable(batch_loader)
                else None
            )
            for item in page:
                if len(pending) >= limit:
                    break
                if not item["enabled"] or not item["payload"].get("text", "").strip():
                    continue
                # A background sweep walks a mutable catalog: an item disabled or
                # removed mid-scan is skipped, never fatal for the whole run.
                if validate is not None:
                    try:
                        if not validate(item):
                            continue
                    except Exception as error:
                        logger.warning("V2 semantic backfill validation failed: %s", type(error).__name__)
                        continue
                if model is not None:
                    if existing_vectors is not None:
                        if item["id"] in existing_vectors:
                            continue
                    elif self.store.vector(item, model) is not None:
                        continue
                pending.append(item)
        written = 0
        for start in range(0, len(pending), BACKFILL_REQUEST_CHUNK):
            batch = pending[start:start + BACKFILL_REQUEST_CHUNK]
            binding, vectors = await self.gateway.embed(
                owner, [item["payload"]["text"][:8000] for item in batch], cfg["embedding_provider"])
            for item, vector in zip(batch, vectors):
                written += 1 if self.store.put_vector(item, binding, vector) else 0
        return written
