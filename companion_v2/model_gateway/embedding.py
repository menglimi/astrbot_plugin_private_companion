"""Typed, bounded embedding and rerank calls, shared by semantic consumers."""

import asyncio
from collections import OrderedDict
import math
import threading
import time

from companion_v2.execution.models import TaskError, digest


DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES = 1024
DEFAULT_EMBEDDING_CACHE_TTL_SECONDS = 300.0
_MAX_SHARED_CACHE_NAMESPACES = 64


def unit(payload):
    if isinstance(payload, dict):
        payload = payload.get("embedding", payload.get("vector", payload.get("data")))
    if not isinstance(payload, (list, tuple)) or not 1 <= len(payload) <= 4096:
        raise ValueError("invalid embedding dimensions")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in payload):
        raise ValueError("embedding must contain finite numbers")
    norm = math.hypot(*payload)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("embedding has no finite direction")
    return tuple(x / norm for x in payload)


class QueryEmbeddingCache:
    """Thread-safe bounded LRU + TTL cache isolated by (owner, model_binding, text_digest)."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_EMBEDDING_CACHE_TTL_SECONDS,
        clock=time.monotonic,
    ):
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict = OrderedDict()

    def get(self, key):
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, vector = entry
            if self.ttl_seconds > 0 and now >= expires_at:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return vector

    def put(self, key, vector):
        now = self._clock()
        expires_at = (now + self.ttl_seconds) if self.ttl_seconds > 0 else float("inf")
        with self._lock:
            self._entries[key] = (expires_at, vector)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def __contains__(self, key):
        return self.get(key) is not None


_SHARED_CACHES_LOCK = threading.Lock()
_SHARED_CACHES: OrderedDict[str, QueryEmbeddingCache] = OrderedDict()


def shared_query_embedding_cache(
    namespace: str,
    *,
    max_entries: int = DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES,
    ttl_seconds: float = DEFAULT_EMBEDDING_CACHE_TTL_SECONDS,
) -> QueryEmbeddingCache:
    """Return a bounded process-wide short-term embedding cache for *namespace*."""
    with _SHARED_CACHES_LOCK:
        cache = _SHARED_CACHES.get(namespace)
        if cache is not None:
            _SHARED_CACHES.move_to_end(namespace)
            return cache
        cache = QueryEmbeddingCache(max_entries=max_entries, ttl_seconds=ttl_seconds)
        _SHARED_CACHES[namespace] = cache
        while len(_SHARED_CACHES) > _MAX_SHARED_CACHE_NAMESPACES:
            _SHARED_CACHES.popitem(last=False)
        return cache


class EmbeddingGateway:
    def __init__(
        self,
        port,
        *,
        timeout=8,
        concurrency=2,
        cache: QueryEmbeddingCache | None = None,
        cache_max_entries: int = DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES,
        cache_ttl_seconds: float = DEFAULT_EMBEDDING_CACHE_TTL_SECONDS,
    ):
        self.port, self.timeout = port, timeout
        self.slots = asyncio.Semaphore(concurrency)
        self.cache = cache if cache is not None else QueryEmbeddingCache(
            max_entries=cache_max_entries,
            ttl_seconds=cache_ttl_seconds,
        )
        self._inflight: dict[tuple, dict] = {}
        self.metrics = {"calls": 0, "texts": 0, "cache_hits": 0, "failures": 0, "last_error": None}

    async def embed(self, owner, texts, provider_id=""):
        if not texts or len(texts) > 64 or any(type(t) is not str or not t.strip() or len(t) > 8000 for t in texts):
            raise ValueError("bounded nonempty embedding text required")
        binding = self.port.binding(provider_id, "embedding")
        keys = [(owner, binding, digest(t)) for t in texts]
        resolved: dict[tuple, tuple[float, ...]] = {}
        key_to_text: dict[tuple, str] = {}
        duplicate_count = 0
        pending_keys: list[tuple] = []

        for key, text in zip(keys, texts):
            cached = self.cache.get(key)
            if cached is not None:
                resolved[key] = cached
                self.metrics["cache_hits"] += 1
                continue
            if key in key_to_text:
                # Duplicate text within the same batch shares one provider slot;
                # counted as a cache hit only after resolution succeeds.
                duplicate_count += 1
                continue
            key_to_text[key] = text
            pending_keys.append(key)

        while pending_keys:
            missing_to_fetch: list[tuple] = []
            waiting_holders: list[tuple[tuple, dict]] = []
            for key in pending_keys:
                cached = self.cache.get(key)
                if cached is not None:
                    resolved[key] = cached
                    self.metrics["cache_hits"] += 1
                    continue
                holder = self._inflight.get(key)
                if holder is not None:
                    waiting_holders.append((key, holder))
                else:
                    missing_to_fetch.append(key)

            own_holders: dict[tuple, dict] = {}
            if missing_to_fetch:
                for key in missing_to_fetch:
                    holder = {"event": asyncio.Event(), "value": None, "error": None, "cancelled": False}
                    self._inflight[key] = holder
                    own_holders[key] = holder
                documents = [key_to_text[key] for key in missing_to_fetch]
                try:
                    async with self.slots:
                        self.metrics["calls"] += 1
                        self.metrics["texts"] += len(documents)
                        values = await asyncio.wait_for(self.port.embed(provider_id, documents), self.timeout)
                    if self.port.binding(provider_id, "embedding") != binding:
                        raise TaskError("embedding_provider_changed", "embedding model changed during the request")
                    if len(values) != len(documents):
                        raise ValueError("embedding batch length mismatch")
                    vectors = [unit(v) for v in values]
                    if len({len(v) for v in vectors}) != 1:
                        raise ValueError("inconsistent embedding dimensions")
                    for key, vector in zip(missing_to_fetch, vectors):
                        self.cache.put(key, vector)
                        resolved[key] = vector
                        own_holders[key]["value"] = vector
                    self.metrics["last_error"] = None
                except asyncio.CancelledError:
                    # Do not poison innocent waiters with our own cancellation;
                    # let them wake up and retry or elect a new leader.
                    for holder in own_holders.values():
                        holder["cancelled"] = True
                    raise
                except Exception as error:
                    self.metrics["failures"] += 1
                    self.metrics["last_error"] = getattr(error, "code", type(error).__name__)
                    for holder in own_holders.values():
                        holder["error"] = error
                    raise
                except BaseException as error:
                    for holder in own_holders.values():
                        holder["error"] = error
                    raise
                finally:
                    for key, holder in own_holders.items():
                        if self._inflight.get(key) is holder:
                            del self._inflight[key]
                        holder["event"].set()

            retry_keys: list[tuple] = []
            for key, holder in waiting_holders:
                await holder["event"].wait()
                if holder["value"] is not None:
                    resolved[key] = holder["value"]
                    self.metrics["cache_hits"] += 1
                elif holder["error"] is not None:
                    raise holder["error"]
                else:
                    # Leader was cancelled before producing a vector; retry cleanly.
                    retry_keys.append(key)
            pending_keys = retry_keys

        if duplicate_count:
            self.metrics["cache_hits"] += duplicate_count

        return binding, tuple(resolved[key] for key in keys)

    async def rerank(self, provider_id, query, documents):
        if not provider_id or len(documents) < 2:
            return None
        async with self.slots:
            scores = await asyncio.wait_for(self.port.rerank(provider_id, query, documents), self.timeout)
        if len(scores) != len(documents) or any(type(s) not in (int, float) or not math.isfinite(s) for s in scores):
            raise ValueError("invalid rerank scores")
        return scores

    def clear(self):
        self.cache.clear()
