# -*- coding: utf-8 -*-
"""LlmToolActionsReactionSearchPart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_search.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 366 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionSearchMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
from .helpers import _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import logger
from .persona_config import runtime_persona_setting
from .reaction_asset_library import ReactionAssetLibrary
from typing import Any



class LlmToolActionsReactionSearchPart01Mixin:
    """LlmToolActionsReactionSearchPart01Mixin（从 LlmToolActionsReactionSearchMixin 拆出）。"""


    @staticmethod
    def _is_reaction_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    @staticmethod
    def _reaction_embedding_provider_runtime_id(provider: Any) -> str:
        try:
            meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
        except Exception:
            meta = None
        if isinstance(meta, dict):
            value = meta.get("id")
        else:
            value = getattr(meta, "id", "") if meta is not None else ""
        if value:
            return _single_line(value, 160)
        config = getattr(provider, "provider_config", None)
        if isinstance(config, dict) and config.get("id"):
            return _single_line(config.get("id"), 160)
        direct = _single_line(getattr(provider, "id", "") or getattr(provider, "provider_id", ""), 160)
        if direct:
            return direct
        provider_class = provider.__class__
        return _single_line(
            f"auto:{getattr(provider_class, '__module__', '')}.{getattr(provider_class, '__qualname__', provider_class.__name__)}",
            160,
        )

    async def _embedding_provider_for_configured_id(self, configured_id: Any = "") -> tuple[Any, str]:
        configured = _single_line(configured_id, 160)
        context = getattr(self, "context", None)
        if context is None:
            return None, configured

        async def resolve(getter_name: str, provider_id: str = "") -> Any:
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                return None
            try:
                value = getter(provider_id) if provider_id else getter()
                return await value if inspect.isawaitable(value) else value
            except Exception:
                return None

        if configured:
            for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
                provider = await resolve(getter_name, configured)
                if self._is_reaction_embedding_provider(provider):
                    return provider, configured
            manager = getattr(context, "provider_manager", None)
            candidates = list(getattr(manager, "embedding_provider_insts", []) or [])
            if isinstance(getattr(manager, "inst_map", None), dict):
                candidates.extend(manager.inst_map.values())
            for provider in candidates:
                if (
                    self._reaction_embedding_provider_runtime_id(provider) == configured
                    and self._is_reaction_embedding_provider(provider)
                ):
                    return provider, configured
            logger.warning(
                "Embedding Provider 不可用，回退本地语义与关键词: provider_id=%s",
                configured,
            )
            return None, configured

        for getter_name in ("get_all_embedding_providers", "get_all_providers"):
            providers = await resolve(getter_name)
            provider_rows = providers.values() if isinstance(providers, dict) else providers or []
            for provider in provider_rows:
                if self._is_reaction_embedding_provider(provider):
                    return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        manager = getattr(context, "provider_manager", None)
        for provider in (
            list(getattr(manager, "embedding_provider_insts", []) or [])
            + list(getattr(manager, "inst_map", {}).values() if manager is not None else [])
        ):
            if self._is_reaction_embedding_provider(provider):
                return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        return None, ""

    async def _shared_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(self, "embedding_provider_id", "")
            or runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            ),
            160,
        )
        if not configured:
            return None, ""
        return await self._embedding_provider_for_configured_id(configured)

    async def _reaction_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            )
            or runtime_persona_setting(self, "embedding_provider_id", ""),
            160,
        )
        return await self._embedding_provider_for_configured_id(configured)

    @staticmethod
    def _reaction_embedding_input_text(value: Any) -> str:
        """Keep BGE-style embedding requests below common 512-token limits.

        Providers expose different tokenizers and many local BGE servers reject
        an oversized request before they can truncate it.  A conservative
        character budget keeps the semantic labels at both ends of a catalog
        entry while avoiding a provider-specific dependency in the plugin.
        """
        text = _single_line(value, 1800)
        limit = 480
        if len(text) <= limit:
            return text
        head = 360
        tail = limit - head - 3
        return f"{text[:head]}...{text[-tail:]}"

    async def _reaction_embedding_vector(self, provider: Any, text: str) -> list[float]:
        if not self._is_reaction_embedding_provider(provider):
            return []
        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))
        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)
        get_embedding = getattr(provider, "get_embedding", None)
        input_text = self._reaction_embedding_input_text(text)
        if callable(get_embedding):
            payload = await wait_result(get_embedding(input_text))
        else:
            get_embeddings = getattr(provider, "get_embeddings", None)
            if callable(get_embeddings):
                payload = await wait_result(get_embeddings([input_text]))
            else:
                get_batch = getattr(provider, "get_embeddings_batch", None)
                if not callable(get_batch):
                    return []
                try:
                    payload = await wait_result(get_batch([input_text], batch_size=1, tasks_limit=1, max_retries=1))
                except TypeError:
                    payload = await wait_result(get_batch([input_text]))
        return ReactionAssetLibrary.normalize_embedding_vector(payload)

    async def _reaction_embedding_vectors(self, provider: Any, texts: list[str]) -> list[list[float]]:
        cleaned = [
            self._reaction_embedding_input_text(item)
            for item in texts
            if self._reaction_embedding_input_text(item)
        ]
        if not cleaned or not self._is_reaction_embedding_provider(provider):
            return []
        if len(cleaned) == 1:
            vector = await self._reaction_embedding_vector(provider, cleaned[0])
            return [vector] if vector else []

        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))

        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)

        payload: Any = None
        get_embeddings = getattr(provider, "get_embeddings", None)
        get_batch = getattr(provider, "get_embeddings_batch", None)
        if callable(get_embeddings):
            try:
                payload = await wait_result(get_embeddings(cleaned))
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        elif callable(get_batch):
            try:
                payload = await wait_result(
                    get_batch(cleaned, batch_size=min(32, len(cleaned)), tasks_limit=2, max_retries=1)
                )
            except TypeError:
                try:
                    payload = await wait_result(get_batch(cleaned))
                except Exception as exc:
                    logger.debug(
                        "批量表情向量请求失败，回退逐条生成: error_type=%s",
                        type(exc).__name__,
                    )
                    return await asyncio.gather(
                        *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                    )
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        else:
            return await asyncio.gather(
                *(self._reaction_embedding_vector(provider, item) for item in cleaned)
            )

        rows = payload
        if isinstance(payload, dict):
            rows = next(
                (payload.get(key) for key in ("data", "embeddings", "vectors") if isinstance(payload.get(key), list)),
                payload,
            )
        elif not isinstance(payload, (list, tuple)):
            for attribute in ("data", "embeddings", "vectors"):
                value = getattr(payload, attribute, None)
                if isinstance(value, (list, tuple)):
                    rows = value
                    break
        if not isinstance(rows, (list, tuple)):
            return []
        vectors = [ReactionAssetLibrary.normalize_embedding_vector(item) for item in rows]
        return vectors if len(vectors) == len(cleaned) and all(vectors) else []

    async def _reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        try:
            batch_size = max(1, min(100, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_batch_size', 24), 24, 1)))
            rows = await asyncio.to_thread(library.list_embedding_missing, provider_id, limit=batch_size)
            updates: list[dict[str, Any]] = []
            for item, text_hash in rows:
                try:
                    vector = await self._reaction_embedding_vector(provider, library.embedding_text(item))
                except Exception as exc:
                    logger.debug("表情向量补齐失败: provider=%s error_type=%s", provider_id, type(exc).__name__)
                    continue
                if vector:
                    updates.append({"id": item.get("id"), "text_hash": text_hash, "vector": vector})
            if updates:
                await asyncio.to_thread(library.upsert_embeddings, provider_id, updates)
                logger.info("已补齐表情语义向量: provider=%s count=%s", provider_id, len(updates))
        finally:
            inflight = getattr(self, "_reaction_embedding_backfill_inflight", set())
            inflight.discard(provider_id)

    def _schedule_reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        if not bool(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_enabled', True)):
            return
        inflight = getattr(self, "_reaction_embedding_backfill_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            setattr(self, "_reaction_embedding_backfill_inflight", inflight)
        if provider_id in inflight:
            return
        now = time.monotonic()
        last_runs = getattr(self, "_reaction_embedding_backfill_last_run", None)
        if not isinstance(last_runs, dict):
            last_runs = {}
            setattr(self, "_reaction_embedding_backfill_last_run", last_runs)
        interval = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_interval_seconds', 300), 300, 0))
        if interval and now - _safe_float(last_runs.get(provider_id), 0.0, 0.0) < interval:
            return
        inflight.add(provider_id)
        last_runs[provider_id] = now
        coroutine = self._reaction_embedding_backfill(library, provider, provider_id)
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                creator(coroutine, label=f"reaction_embedding:{provider_id[:24]}")
            else:
                asyncio.create_task(coroutine)
        except Exception:
            inflight.discard(provider_id)
            coroutine.close()

    @staticmethod
    def _reaction_expression_lookup_cache_key(
        provider: Any,
        query: str,
        context: str,
        meme_only: bool,
        scope: str = "",
        revision: str = "",
    ) -> tuple[int, str, str, bool, str, str]:
        def normalize(value: Any) -> str:
            return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

        return (
            id(provider),
            normalize(query),
            normalize(context),
            bool(meme_only),
            normalize(scope),
            normalize(revision),
        )

    @staticmethod
    def _reaction_expression_lookup_cache_revision(provider: Any) -> str:
        """Return a cheap catalog revision so UI edits do not leave stale hits alive."""
        revision_getter = getattr(provider, "selection_revision", None)
        if not callable(revision_getter):
            revision_getter = getattr(provider, "lookup_revision", None)
        if callable(revision_getter):
            try:
                return str(revision_getter() or "")
            except Exception:
                pass
        catalog_path = getattr(provider, "catalog_path", None)
        if catalog_path is None:
            return ""
        try:
            stat = os.stat(catalog_path)
            return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        except (OSError, TypeError, ValueError):
            return ""

    @staticmethod
    def _reaction_expression_selection_revision(
        selection_preferences: Any,
        selection_signature: Any = "",
    ) -> str:
        """Hash the bounded preference snapshot used by reaction selection."""
        if not isinstance(selection_preferences, dict):
            return ""
        signature = _single_line(
            selection_signature or selection_preferences.get("intent_signature"),
            40,
        )
        raw_assets = selection_preferences.get("assets")
        rows: list[dict[str, Any]] = []
        if isinstance(raw_assets, dict):
            raw_assets = [
                {"key": key, **value}
                for key, value in raw_assets.items()
                if isinstance(value, dict)
            ]
        if isinstance(raw_assets, list):
            for raw_item in raw_assets:
                if not isinstance(raw_item, dict):
                    continue
                key = _single_line(raw_item.get("key"), 180)
                if not key:
                    continue
                rows.append(
                    {
                        "key": key,
                        "score": _safe_int(raw_item.get("score"), 0, -20, 20),
                        "positive_count": _safe_int(
                            raw_item.get("positive_count"), 0, 0, 1000
                        ),
                        "negative_count": _safe_int(
                            raw_item.get("negative_count"), 0, 0, 1000
                        ),
                        "intent_score": _safe_int(
                            raw_item.get("intent_score"), 0, -8, 8
                        ),
                    }
                )
        if not rows:
            return ""
        rows.sort(key=lambda item: item["key"])
        payload = json.dumps(
            {"intent_signature": signature, "assets": rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
