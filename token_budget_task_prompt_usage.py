# -*- coding: utf-8 -*-
"""TokenBudgetTaskPromptUsageMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 355 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

from .helpers import _flat_get, _single_line
from .task_prompt_registry import TASK_PROMPT_CONFIG_KEY, apply_task_prompt_override, normalize_task_prompt_overrides
from datetime import datetime
from typing import Any



class TokenBudgetTaskPromptUsageMixin:
    """TokenBudgetTaskPromptUsageMixin（从 TokenBudgetMixin 拆出）。"""


    def _task_prompt_overrides_for_call(self) -> dict[str, str]:
        """Read the plugin-owned task prompt overrides for one internal call.

        The runtime attribute is preferred after a page hot update, while the
        persisted config is used on startup.  This deliberately does not read
        or mutate an AstrBot ``ProviderRequest``/conversation system prompt.
        """
        missing = object()
        runtime_value = getattr(self, TASK_PROMPT_CONFIG_KEY, missing)
        config = getattr(self, "config", None)
        persisted_value = _flat_get(config, TASK_PROMPT_CONFIG_KEY, missing)
        raw = runtime_value if runtime_value is not missing else persisted_value
        if raw is missing:
            return {}
        try:
            return normalize_task_prompt_overrides(raw)
        except Exception:
            # A malformed legacy value must never break an otherwise valid
            # plugin task call.  The registry is intentionally defensive too.
            return {}

    def _apply_task_prompt_override_for_call(
        self,
        task: str | None,
        prompt: str,
        system_prompt: str | None = None,
        *,
        flatten_system_prompt: bool = False,
    ) -> tuple[str, str | None]:
        """Apply one configured task instruction to a plugin-internal call.

        ``flatten_system_prompt`` is for direct visual Provider APIs which do
        not accept a ``system_prompt`` argument.  The normal budgeted paths
        preserve the separate system channel, so the host's main conversation
        request is never involved.
        """
        original_prompt = str(prompt or "")
        original_system = system_prompt
        overrides = self._task_prompt_overrides_for_call()
        try:
            updated_prompt, updated_system = apply_task_prompt_override(
                task,
                original_prompt,
                original_system,
                overrides,
            )
        except Exception:
            return original_prompt, original_system
        if not flatten_system_prompt:
            return updated_prompt, updated_system
        # Direct Provider ``text_chat`` variants commonly reject an unknown
        # system_prompt kwarg.  Preserve both channels in the user prompt in
        # the same order as the normal path (task input, then task constraints)
        # and explicitly clear the unsupported channel.
        system_text = str(updated_system or "").strip()
        if not system_text:
            return updated_prompt, None
        prompt_text = str(updated_prompt or "").strip()
        flattened = "\n\n".join(part for part in (prompt_text, system_text) if part)
        return flattened, None

    def _token_usage_now_dt(self) -> datetime:
        now_getter = getattr(self, "_environment_now", None)
        if callable(now_getter):
            try:
                return now_getter()
            except Exception:
                pass
        return datetime.now()

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        ascii_chars = sum(1 for ch in raw if ord(ch) < 128)
        non_ascii_chars = max(0, len(raw) - ascii_chars)
        # CJK and many emoji tokenizers are close to one token per character.
        # Use a conservative estimate here so a configured card ceiling does
        # not silently allow an over-limit request to reach the primary model.
        return max(1, int(ascii_chars / 4.0 + non_ascii_chars))

    @staticmethod
    def _usage_raw_value(usage: Any, key: str) -> Any:
        if not usage:
            return None
        current = usage
        for part in str(key or "").split("."):
            if not part:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    @classmethod
    def _usage_value(cls, usage: Any, *keys: str) -> int:
        if not usage:
            return 0
        for key in keys:
            value = cls._usage_raw_value(usage, key)
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                return parsed
        return 0

    @classmethod
    def _usage_candidates(cls, value: Any, *, _depth: int = 0) -> list[Any]:
        """Flatten SDK response usage containers without importing provider SDKs."""
        if value is None or _depth > 3:
            return []
        candidates = [value]
        if isinstance(value, dict):
            for key in ("usage", "token_usage", "raw_usage", "usage_metadata", "model_extra"):
                nested = value.get(key)
                if nested is not None and nested is not value:
                    candidates.extend(cls._usage_candidates(nested, _depth=_depth + 1))
            return candidates
        for attr in ("usage", "token_usage", "raw_usage", "usage_metadata", "model_extra"):
            try:
                nested = getattr(value, attr, None)
            except Exception:
                nested = None
            if nested is not None and nested is not value:
                candidates.extend(cls._usage_candidates(nested, _depth=_depth + 1))
        dumper = getattr(value, "model_dump", None)
        if callable(dumper):
            try:
                dumped = dumper()
            except Exception:
                dumped = None
            if dumped is not None and dumped is not value:
                candidates.extend(cls._usage_candidates(dumped, _depth=_depth + 1))
        return candidates

    @classmethod
    def _usage_value_from_candidates(cls, candidates: list[Any], *keys: str) -> int:
        for candidate in candidates:
            value = cls._usage_value(candidate, *keys)
            if value > 0:
                return value
        return 0

    @classmethod
    def _llm_text_from_content(cls, value: Any, *, limit: int = 8000) -> str:
        """Extract printable text from AstrBot/OpenAI-style message content."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value[:limit]
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            item_type = str(value.get("type") or "").strip()
            if item_type == "text":
                return str(value.get("text") or "")[:limit]
            if item_type == "image_url":
                return "[图片]"
            if item_type == "audio_url":
                return "[音频]"
            parts: list[str] = []
            for key in ("text", "content", "message", "result", "name"):
                if key in value:
                    text = cls._llm_text_from_content(value.get(key), limit=limit)
                    if text:
                        parts.append(text)
            return "\n".join(parts)[:limit]
        if isinstance(value, (list, tuple)):
            parts = []
            remaining = limit
            for item in value:
                if remaining <= 0:
                    break
                text = cls._llm_text_from_content(item, limit=remaining)
                if text:
                    parts.append(text)
                    remaining -= len(text)
            return "\n".join(parts)[:limit]
        dumper = getattr(value, "model_dump_for_context", None)
        if callable(dumper):
            try:
                return cls._llm_text_from_content(dumper(), limit=limit)
            except Exception:
                return ""
        return str(value)[:limit] if value else ""

    @classmethod
    def _request_prompt_for_token_stats(cls, req: Any) -> str:
        if req is None:
            return ""
        parts: list[str] = []
        for attr in ("system_prompt", "prompt"):
            value = getattr(req, attr, None)
            if value:
                text = cls._llm_text_from_content(value)
                if text:
                    parts.append(text)
        contexts = getattr(req, "contexts", None)
        if isinstance(contexts, list):
            for ctx in contexts:
                if not isinstance(ctx, dict):
                    continue
                role = _single_line(ctx.get("role"), 40)
                content = cls._llm_text_from_content(ctx.get("content"))
                if content:
                    parts.append(f"{role}: {content}" if role else content)
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(extra_parts, list) and extra_parts:
            text = cls._llm_text_from_content(extra_parts)
            if text:
                parts.append(text)
        image_count = len(getattr(req, "image_urls", None) or [])
        audio_count = len(getattr(req, "audio_urls", None) or [])
        if image_count > 0:
            parts.append(f"[图片] x{image_count}")
        if audio_count > 0:
            parts.append(f"[音频] x{audio_count}")
        return "\n\n".join(part for part in parts if part).strip()

    @classmethod
    def _completion_text_for_token_stats(cls, resp: Any) -> str:
        if resp is None:
            return ""
        text = str(getattr(resp, "completion_text", "") or "")
        if text:
            return text
        result_chain = getattr(resp, "result_chain", None)
        chain = getattr(result_chain, "chain", None)
        if isinstance(chain, list):
            parts: list[str] = []
            for item in chain:
                item_text = ""
                if isinstance(item, dict):
                    item_text = str(item.get("text") or item.get("content") or "")
                else:
                    item_text = str(getattr(item, "text", "") or getattr(item, "content", "") or "")
                if item_text:
                    parts.append(item_text)
            if parts:
                return "\n".join(parts)
        return cls._llm_text_from_content(result_chain)

    def _extract_llm_usage(self, resp: Any, prompt: str, completion: str) -> dict[str, Any]:
        candidates = self._usage_candidates(resp)
        raw_completion = getattr(resp, "raw_completion", None)
        if raw_completion is not None:
            candidates.extend(self._usage_candidates(raw_completion))
        raw_response = getattr(resp, "raw_response", None)
        if raw_response is not None:
            candidates.extend(self._usage_candidates(raw_response))
        prompt_tokens = self._usage_value_from_candidates(
            candidates,
            "prompt_tokens", "input_tokens", "prompt", "input",
            "prompt_token_count", "input_token_count",
        )
        standard_completion_tokens = self._usage_value_from_candidates(
            candidates,
            "completion_tokens", "output_tokens", "completion", "output",
            "output_token_count", "generated_tokens",
        )
        candidate_completion_tokens = self._usage_value_from_candidates(
            candidates, "candidates_token_count"
        )
        reasoning_tokens = self._usage_value_from_candidates(
            candidates,
            "reasoning_tokens", "reasoning_token_count", "thoughts_token_count",
            "completion_tokens_details.reasoning_tokens",
            "output_tokens_details.reasoning_tokens",
        )
        # OpenAI-style completion/output counts already include reasoning.
        # Gemini candidate counts exclude thoughts, so only that form is additive.
        completion_tokens = standard_completion_tokens or (candidate_completion_tokens + reasoning_tokens)
        total_tokens = self._usage_value_from_candidates(
            candidates, "total_tokens", "total", "total_token_count"
        )
        cache_read_tokens = self._usage_value_from_candidates(
            candidates,
            "input_cached",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
            "input_token_details.cache_read",
            "cache_read_input_tokens",
            "cache_read_tokens",
            "prompt_cache_hit_tokens",
            "cached_content_token_count",
        )
        cache_write_tokens = self._usage_value_from_candidates(
            candidates,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_input_tokens",
            "cache_write_tokens",
            "prompt_cache_creation_tokens",
        )
        cached_tokens = self._usage_value_from_candidates(
            candidates,
            "input_cached",
            "cached_tokens",
            "prompt_cached_tokens",
            "input_cached_tokens",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
            "cached_content_token_count",
        )
        if cached_tokens <= 0:
            cached_tokens = cache_read_tokens
        input_other_tokens = self._usage_value_from_candidates(candidates, "input_other")
        usage_present = any(
            (
                prompt_tokens,
                standard_completion_tokens,
                candidate_completion_tokens,
                reasoning_tokens,
                total_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cached_tokens,
                input_other_tokens,
            )
        )
        if prompt_tokens <= 0 and (input_other_tokens > 0 or cached_tokens > 0):
            prompt_tokens = input_other_tokens + cached_tokens
        estimated = False
        if total_tokens <= 0:
            prompt_estimated = prompt_tokens <= 0
            completion_estimated = completion_tokens <= 0
            if prompt_estimated:
                prompt_tokens = self._estimate_token_count(prompt)
            if completion_estimated:
                completion_tokens = self._estimate_token_count(completion)
            total_tokens = prompt_tokens + completion_tokens
            estimated = (not usage_present) or prompt_estimated or completion_estimated
        elif prompt_tokens <= 0 and completion_tokens <= 0:
            prompt_tokens = min(total_tokens, self._estimate_token_count(prompt))
            completion_tokens = max(0, total_tokens - prompt_tokens)
            estimated = True
        elif prompt_tokens <= 0:
            prompt_tokens = max(0, total_tokens - completion_tokens)
            estimated = True
        elif completion_tokens <= 0:
            completion_tokens = max(0, total_tokens - prompt_tokens)
            estimated = True
        # Provider totals may include reasoning/thoughts or another output bucket.
        # Keep the displayed invariant stable instead of silently undercounting.
        if total_tokens < prompt_tokens + completion_tokens:
            total_tokens = prompt_tokens + completion_tokens
        elif total_tokens > prompt_tokens + completion_tokens:
            completion_tokens += total_tokens - (prompt_tokens + completion_tokens)
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "reasoning_tokens": max(0, reasoning_tokens),
            "total_tokens": max(0, total_tokens),
            "cached_tokens": max(0, cached_tokens),
            "cache_read_tokens": max(0, cache_read_tokens),
            "cache_write_tokens": max(0, cache_write_tokens),
            "estimated": estimated or not usage_present,
        }
