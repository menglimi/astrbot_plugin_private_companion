"""AstrBot embedding, rerank and visual-analysis ports; no provider secrets leave here."""

import inspect
import json
import os
import re

from companion_v2.execution.models import TaskError, digest


SLANG_SEARCH_ENV = ("COMPANION_V2_SLANG_SEARCH_URL", "COMPANION_V2_SLANG_SEARCH_API_KEY",
                    "COMPANION_V2_SLANG_SEARCH_AUTH_HEADER", "COMPANION_V2_SLANG_SEARCH_AUTH_SCHEME")


async def awaited(value):
    return await value if inspect.isawaitable(value) else value


def _embedding_batch_limit(error, count):
    """Read a Provider's explicit batch rejection, without retrying other errors."""
    if getattr(error, "status_code", None) not in {400, 413, 422} or count < 2:
        return None
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    body = body.get("error", body)
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return None
    message = body["message"].lower()
    if not re.search(r"\bbatch(?:[ _-]*size)?\b", message):
        return None
    match = re.search(r"(?:larger than|at most|maximum(?: batch size)?(?: is)?|max(?:imum)?(?: size)?\s*[:=]|limit\s*[:=])\s*(\d{1,5})", message)
    if match:
        limit = int(match.group(1))
        return limit if 0 < limit < count else None
    return max(1, count // 2) if any(word in message for word in ("too large", "exceed")) else None


def _embedding_values(values, count):
    if isinstance(values, dict):
        values = values.get("data", values.get("embeddings", values.get("vectors")))
    if not isinstance(values, (list, tuple)):
        values = getattr(values, "data", ())
    if len(values) != count:
        raise ValueError("embedding response length mismatch")
    if values and all(isinstance(value, dict) and "index" in value for value in values):
        if sorted(value["index"] for value in values) != list(range(count)):
            raise ValueError("embedding response indices mismatch")
        values = sorted(values, key=lambda value: value["index"])
    return list(values)


def _slang_search_results(batch, limit):
    items = getattr(batch, "items", ())
    results = []
    for item in items[:limit]:
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        results.append({"title": title[:200], "summary": str(getattr(item, "summary", "") or "").strip()[:400],
                        "source": str(getattr(item, "source", "") or "").strip()[:60]})
    return results


def configured_slang_search_port(context=None, *, deepseek_provider_resolver=None):
    """Build the reference search port from explicit, DeepSeek, or AstrBot settings.

    Credentials stay in host settings and are never written into V2 configuration. Missing
    search configuration quietly degrades to "no external reference".
    """
    values = {name: os.environ.get(name, "") for name in SLANG_SEARCH_ENV}
    url = values[SLANG_SEARCH_ENV[0]].strip()
    if url:
        return NativeSlangSearch(url, values[SLANG_SEARCH_ENV[1]], values[SLANG_SEARCH_ENV[2]] or "Authorization",
                                 values[SLANG_SEARCH_ENV[3]] or "Bearer", host_context=context)
    from .news import astrbot_web_search_available, deepseek_api_key
    selected_provider = ""
    if callable(deepseek_provider_resolver):
        try:
            selected_provider = deepseek_provider_resolver()
        except Exception:
            selected_provider = "invalid-search-provider-selection"
    if (deepseek_provider_resolver is not None or selected_provider or deepseek_api_key(context)
            or astrbot_web_search_available(context)):
        return NativeSlangSearch(host_context=context, deepseek_provider_resolver=deepseek_provider_resolver)
    return None


class NativeSlangSearch:
    """Optional external reference search that never overwrites an in-group usage."""

    def __init__(self, url="", api_key="", auth_header="Authorization", auth_scheme="Bearer", *,
                 host_context=None, deepseek_provider_resolver=None):
        if type(url) is not str:
            raise ValueError("slang search endpoint is invalid")
        self.url, self.api_key, self.host_context = url.strip(), api_key, host_context
        self.auth_header, self.auth_scheme = auth_header, auth_scheme
        self.deepseek_provider_resolver = deepseek_provider_resolver

    @property
    def available(self):
        return True

    async def search(self, term, limit):
        if type(term) is not str or not 0 < len(term.strip()) <= 64:
            raise ValueError("slang search needs a bounded term")
        from .news import configured_search_port
        result_count = max(1, min(5, int(limit)))
        port = configured_search_port(self.host_context, query=term.strip(), result_count=result_count,
            search_url=self.url, auth={"api_key": self.api_key, "auth_header": self.auth_header,
                                       "auth_scheme": self.auth_scheme}, topic="general",
            deepseek_provider_resolver=self.deepseek_provider_resolver)
        if port is None:
            return []
        batch = await port.fetch()
        return _slang_search_results(batch, result_count)


class NativeSemanticModels:
    def __init__(self, context, default_analysis="", slang_search=None):
        self.context, self.default_analysis = context, default_analysis
        self.slang_search = slang_search
        self._embedding_batch_limits = {}

    def providers(self, kind):
        getter = getattr(self.context, "get_all_providers" if kind == "analysis" else "get_all_" + kind + "_providers", None)
        values = getter() if callable(getter) else ()
        if not values and kind == "rerank":
            values = getattr(getattr(self.context, "provider_manager", None), "rerank_provider_insts", ())
        if isinstance(values, dict):
            values = values.values()
        return tuple(values or ())

    def resolve(self, identifier, kind):
        if kind == "analysis":
            identifier = identifier or self.default_analysis
        provider = None
        if identifier:
            for name in ("get_" + kind + "_provider_by_id", "get_provider_by_id"):
                getter = getattr(self.context, name, None)
                if callable(getter):
                    provider = getter(identifier)
                    if provider is not None:
                        break
        else:
            provider = next(iter(self.providers(kind)), None)
        methods = {"embedding": ("get_embedding", "get_embeddings", "get_embeddings_batch"),
                   "rerank": ("rerank",), "analysis": ("text_chat",)}[kind]
        if provider is None or not any(callable(getattr(provider, method, None)) for method in methods):
            raise TaskError("semantic_provider_unavailable", "no compatible " + kind + " provider is configured")
        return provider

    def binding(self, identifier, kind):
        provider = self.resolve(identifier, kind)
        config = getattr(provider, "provider_config", {})
        return kind + ":" + digest([type(provider).__module__, type(provider).__qualname__, config])

    def inventory(self):
        results = {}
        for kind in ("embedding", "rerank", "analysis"):
            results[kind] = [{"id": str(getattr(p, "provider_config", {}).get("id", "")),
                              "name": str(getattr(p, "provider_config", {}).get("id", ""))}
                             for p in self.providers(kind)]
        return results

    async def embed(self, identifier, texts):
        provider = self.resolve(identifier, "embedding")
        batch = getattr(provider, "get_embeddings", None)
        if not callable(batch):
            batch = getattr(provider, "get_embeddings_batch", None)
        if not callable(batch):
            return [await awaited(provider.get_embedding(text)) for text in texts]
        binding = self.binding(identifier, "embedding")
        values, offset = [], 0
        while offset < len(texts):
            count = min(len(texts) - offset, self._embedding_batch_limits.get(binding, len(texts)))
            try:
                current = _embedding_values(await awaited(batch(texts[offset:offset + count])), count)
            except Exception as error:
                limit = _embedding_batch_limit(error, count)
                if limit is None:
                    raise
                self._embedding_batch_limits[binding] = min(limit, self._embedding_batch_limits.get(binding, limit))
                continue
            # Each Provider subrequest numbers its own results from zero.
            values.extend({**value, "index": value["index"] + offset}
                          if isinstance(value, dict) and "index" in value else value for value in current)
            offset += count
        return values

    async def rerank(self, identifier, query, documents):
        provider = self.resolve(identifier, "rerank")
        value = await awaited(provider.rerank(query=query, documents=documents))
        if isinstance(value, dict):
            value = value.get("results", value.get("data"))
        if not isinstance(value, (list, tuple)):
            value = getattr(value, "results", ())
        scores = [0.0] * len(documents)
        seen = set()
        for result in value:
            index = result.get("index") if isinstance(result, dict) else getattr(result, "index", None)
            score = result.get("relevance_score", result.get("score")) if isinstance(result, dict) else getattr(result, "relevance_score", None)
            if type(index) is not int or index in seen or not 0 <= index < len(documents):
                raise ValueError("rerank response indices mismatch")
            scores[index] = score
            seen.add(index)
        if len(seen) != len(documents):
            raise ValueError("incomplete rerank response")
        return scores

    async def analyze(self, identifier, prompt, images=()):
        provider = self.resolve(identifier, "analysis")
        if images:
            config = getattr(provider, "provider_config", {})
            if "image" not in (config.get("modalities") or ()):
                candidates = list(self.providers("analysis"))
                if self.default_analysis:
                    try:
                        candidates.insert(0, self.resolve(self.default_analysis, "analysis"))
                    except TaskError:
                        pass
                provider = next((candidate for candidate in candidates
                    if "image" in (getattr(candidate, "provider_config", {}).get("modalities") or ())
                    and callable(getattr(candidate, "text_chat", None))), None)
                if provider is None:
                    raise TaskError("semantic_vision_provider_unavailable",
                                    "image analysis requires a configured vision provider")
        kwargs = {"prompt": prompt, "max_tokens": 1400}
        if images:
            kwargs["image_urls"] = list(images)
        response = await awaited(provider.text_chat(**kwargs))
        text = getattr(response, "completion_text", response)
        if not isinstance(text, str) or len(text) > 32000:
            raise ValueError("invalid analysis response")
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
