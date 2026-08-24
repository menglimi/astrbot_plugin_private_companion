# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .constants import (
    MODEL_PROVIDER_KEYS,
    MODEL_QUICK_TIMEOUT_KEYS,
    MODEL_TASK_PROVIDER_KEYS,
    MODEL_TASK_PROVIDER_PREFIXES,
)
from .helpers import _flat_get, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import contains_sensitive_refusal, scope_allows
from .persona_config import runtime_persona_setting


def _looks_like_upstream_llm_error_response(text: Any) -> bool:
    """Match high-confidence error envelopes returned as successful LLM text."""
    cleaned = _single_line(text, 2000)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", lowered).strip()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", lowered)

    direct_markers = (
        "allchatmodelsfailed",
        "allllmprovidersfailed",
        "allavailablechatmodelsareunavailable",
        "promptcouldnotbesubmitted",
        "promptwasnotsubmitted",
        "promptcontainssensitivewords",
        "unabletosubmitrequest",
        "providerapierrorupstreamreturnedaninternalfailuremessage",
    )
    if any(marker in compact for marker in direct_markers):
        return True

    google_policy_features = (
        "generativeaiprohibitedusepolicy",
        "tryrephrasingtheprompt",
        "sensitivewords",
    )
    if sum(feature in compact for feature in google_policy_features) >= 2:
        return True

    if "functiondeclaration" in compact and any(
        marker in compact
        for marker in (
            "schemadidntspecify",
            "invalidrequest",
            "badrequest",
            "errorcode400",
        )
    ):
        return True

    if lowered.lstrip().startswith("traceback (most recent call last):"):
        return True

    if re.match(
        r"^(?:模型|api|provider|函数工具|工具)\s*调用失败\s*[:：]",
        cleaned,
        flags=re.IGNORECASE,
    ):
        return True

    if re.match(
        r"^(?:api connection error|api status error|authentication error|"
        r"permission denied error|rate limit error|internal server error)\s*(?:[:：-]|$)",
        lowered,
    ):
        return True

    error_classes = (
        "badrequesterror",
        "apiconnectionerror",
        "apistatuserror",
        "authenticationerror",
        "permissiondeniederror",
        "ratelimiterror",
        "notfounderror",
        "internalservererror",
    )
    error_class = next((name for name in error_classes if name in compact), "")
    if error_class:
        structured_signal = any(
            marker in compact
            for marker in (
                "errorcode",
                "statuscode",
                "httpstatus",
                "requestid",
                "invalidrequest",
            )
        )
        leading_error_class = bool(
            re.match(
                rf"^(?:(?:llm|provider|api)\s+(?:response\s+)?error\s+|error\s+)?{error_class}\b",
                normalized,
            )
        )
        if structured_signal or (
            leading_error_class
            and (":" in cleaned[:100] or "：" in cleaned[:100] or len(cleaned) <= 48)
        ):
            return True

    if lowered.lstrip().startswith(("{", "[")) and any(
        marker in lowered for marker in ('"error"', "'error'")
    ) and any(
        marker in compact
        for marker in ("invalid_request", "badrequest", "permissiondenied", "ratelimit")
    ):
        return True

    return False


class TokenBudgetMixin:
    """Methods split from main.PrivateCompanionPlugin."""

    # A card limit is an optional, per-request estimate used only to decide
    # whether the configured fallback should take the request.  It is not a
    # daily quota and it does not truncate a request when no fallback exists.
    MODEL_TOKEN_LIMIT_MIN = 256
    MODEL_TOKEN_LIMIT_MAX = 2_000_000
    MODEL_IMAGE_TOKEN_ESTIMATE = 256

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

    @staticmethod
    def _classify_llm_prompt(prompt: str) -> str:
        text = str(prompt or "")[:1200]
        rules = (
            ("daily_plan", ("日程生成器", "生成今天的一日生活日程", "\"schedule\"")),
            ("detail", ("日程细化生成器", "today_events", "presence_status")),
            ("full_test_detail", ("完整测试", "缺少这些主动行为", "today_events")),
            ("dream", ("梦境生成器", "dream_type", "afterglow")),
            ("diary", ("日记生成器", "dream_fragments", "long_term_events")),
            ("memory_profile", ("本地陪伴画像整理", "本地陪伴画像", "user_traits")),
            ("dialogue_episode", ("私聊对话整理成片段", "共同经历", "open_loops")),
            ("response_review", ("改写成更像真实私聊", "需要修正的问题", "原回复")),
            ("relationship", ("关系站位", "relationship", "互动边界")),
            ("worldbook_registration", ("自我介绍原文", "人物画像插件", "初始印象")),
            ("group_interject", ("群聊主动插话", "插话", "群聊")),
            ("group_episode", ("群聊片段", "群聊阶段性", "topic_threads")),
            ("group_slang", ("黑话", "slang", "群内")),
            ("forward_message", ("合并消息转述", "聊天记录节点", "不要把记录中的话当成当前用户说的话")),
            ("photo_prompt", ("ComfyUI", "社交媒体随手拍", "\"caption\"")),
            ("screen_narration", ("屏幕后留在脑子里的印象", "原始结果")),
            ("voice_repair", ("主动语音修正", "当前版本")),
            ("voice", ("主动语音", "TTS", "语音内容")),
            ("yesterday_summary", ("昨日/最近完整对话", "残留影响", "dream_reference")),
            ("creative_project", ("输出 JSON", "target_chars", "next_hint")),
            ("creative_writing", ("慢慢写作品", "本次字数上限", "只输出本次片段")),
            ("provider_test", ("请只回复两个字：正常",)),
        )
        for label, markers in rules:
            if all(marker in text for marker in markers):
                return label
        return "other"

    def _record_llm_usage(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        completion: str,
        elapsed_ms: int,
        success: bool,
        error: str = "",
        resp: Any = None,
        budget_exempt: bool | None = None,
    ) -> None:
        usage = self._extract_llm_usage(resp, prompt, completion)
        now_ts = _now_ts()
        day = _today_key()
        now_dt = self._token_usage_now_dt()
        hour = now_dt.strftime("%Y-%m-%d %H:00")
        store = self.data.setdefault("token_usage", {})
        if not isinstance(store, dict):
            store = {}
            self.data["token_usage"] = store
        totals = store.setdefault("totals", {})
        if not isinstance(totals, dict):
            totals = {}
            store["totals"] = totals
        by_provider = store.setdefault("by_provider", {})
        by_task = store.setdefault("by_task", {})
        by_day = store.setdefault("by_day", {})
        by_day_provider = store.setdefault("by_day_provider", {})
        by_day_task = store.setdefault("by_day_task", {})
        by_hour = store.setdefault("by_hour", {})
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        task_key = task or "other"
        exempt = self._is_llm_budget_exempt_task(task_key) if budget_exempt is None else bool(budget_exempt)
        budget_exempt_totals = store.setdefault("budget_exempt_totals", {}) if exempt else None
        budget_exempt_by_day = store.setdefault("budget_exempt_by_day", {}) if exempt else None
        budget_exempt_by_task = store.setdefault("budget_exempt_by_task", {}) if exempt else None

        def bump(bucket: dict[str, Any]) -> None:
            bucket["calls"] = _safe_int(bucket.get("calls"), 0) + 1
            bucket["success"] = _safe_int(bucket.get("success"), 0) + (1 if success else 0)
            bucket["errors"] = _safe_int(bucket.get("errors"), 0) + (0 if success else 1)
            bucket["prompt_tokens"] = _safe_int(bucket.get("prompt_tokens"), 0) + usage["prompt_tokens"]
            bucket["completion_tokens"] = _safe_int(bucket.get("completion_tokens"), 0) + usage["completion_tokens"]
            bucket["reasoning_tokens"] = _safe_int(bucket.get("reasoning_tokens"), 0) + usage["reasoning_tokens"]
            bucket["total_tokens"] = _safe_int(bucket.get("total_tokens"), 0) + usage["total_tokens"]
            bucket["cached_tokens"] = _safe_int(bucket.get("cached_tokens"), 0) + usage["cached_tokens"]
            bucket["cache_read_tokens"] = _safe_int(bucket.get("cache_read_tokens"), 0) + usage["cache_read_tokens"]
            bucket["cache_write_tokens"] = _safe_int(bucket.get("cache_write_tokens"), 0) + usage["cache_write_tokens"]
            bucket["estimated_tokens"] = _safe_int(bucket.get("estimated_tokens"), 0) + (usage["total_tokens"] if usage["estimated"] else 0)
            bucket["elapsed_ms"] = _safe_int(bucket.get("elapsed_ms"), 0) + max(0, elapsed_ms)
            bucket["last_ts"] = now_ts

        provider_key = provider_id or "(default)"
        for target in (
            totals,
            by_provider.setdefault(provider_key, {}),
            by_task.setdefault(task_key, {}),
            by_day.setdefault(day, {}),
            by_day_provider.setdefault(day, {}).setdefault(provider_key, {}),
            by_day_task.setdefault(day, {}).setdefault(task_key, {}),
            by_hour.setdefault(hour, {}),
        ):
            if isinstance(target, dict):
                bump(target)
        if exempt:
            for target in (
                budget_exempt_totals,
                budget_exempt_by_day.setdefault(day, {}) if isinstance(budget_exempt_by_day, dict) else None,
                budget_exempt_by_task.setdefault(task_key, {}) if isinstance(budget_exempt_by_task, dict) else None,
            ):
                if isinstance(target, dict):
                    bump(target)

        recent.append(
            {
                "ts": now_ts,
                "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_key,
                "task": task_key,
                "success": success,
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "reasoning_tokens": usage["reasoning_tokens"],
                "total_tokens": usage["total_tokens"],
                "cached_tokens": usage["cached_tokens"],
                "cache_read_tokens": usage["cache_read_tokens"],
                "cache_write_tokens": usage["cache_write_tokens"],
                "estimated": usage["estimated"],
                "elapsed_ms": max(0, elapsed_ms),
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": len(str(completion or "")),
                "error": _single_line(error, 160),
                "budget_exempt": exempt,
            }
        )
        del recent[:-240]
        store["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        last_save = _safe_float(getattr(self, "_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 60:
            self._token_usage_last_save_at = now_ts
            try:
                self._save_data_sync(sections={"token_usage"})
            except Exception:
                pass

    def _record_external_llm_usage(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        completion: str,
        elapsed_ms: int,
        success: bool,
        error: str = "",
        resp: Any = None,
        session_id: str = "",
        sender_id: str = "",
        message_type: str = "",
    ) -> None:
        usage = self._extract_llm_usage(resp, prompt, completion)
        now_ts = _now_ts()
        day = _today_key()
        now_dt = self._token_usage_now_dt()
        hour = now_dt.strftime("%Y-%m-%d %H:00")
        root = self.data.setdefault("token_usage", {})
        if not isinstance(root, dict):
            root = {}
            self.data["token_usage"] = root
        store = root.setdefault("external", {})
        if not isinstance(store, dict):
            store = {}
            root["external"] = store
        totals = store.setdefault("totals", {})
        by_provider = store.setdefault("by_provider", {})
        by_task = store.setdefault("by_task", {})
        by_day = store.setdefault("by_day", {})
        by_day_provider = store.setdefault("by_day_provider", {})
        by_day_task = store.setdefault("by_day_task", {})
        by_session = store.setdefault("by_session", {})
        by_day_session = store.setdefault("by_day_session", {})
        by_hour = store.setdefault("by_hour", {})
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        task_key = _single_line(task, 40) or "astrbot_reply"
        provider_key = provider_id or "(default)"
        session_key = _single_line(session_id, 160) or "(unknown_session)"
        sender_key = _single_line(sender_id, 80)
        message_type_key = _single_line(message_type, 20)

        def bump(bucket: dict[str, Any]) -> None:
            bucket["calls"] = _safe_int(bucket.get("calls"), 0) + 1
            bucket["success"] = _safe_int(bucket.get("success"), 0) + (1 if success else 0)
            bucket["errors"] = _safe_int(bucket.get("errors"), 0) + (0 if success else 1)
            bucket["prompt_tokens"] = _safe_int(bucket.get("prompt_tokens"), 0) + usage["prompt_tokens"]
            bucket["completion_tokens"] = _safe_int(bucket.get("completion_tokens"), 0) + usage["completion_tokens"]
            bucket["reasoning_tokens"] = _safe_int(bucket.get("reasoning_tokens"), 0) + usage["reasoning_tokens"]
            bucket["total_tokens"] = _safe_int(bucket.get("total_tokens"), 0) + usage["total_tokens"]
            bucket["cached_tokens"] = _safe_int(bucket.get("cached_tokens"), 0) + usage["cached_tokens"]
            bucket["cache_read_tokens"] = _safe_int(bucket.get("cache_read_tokens"), 0) + usage["cache_read_tokens"]
            bucket["cache_write_tokens"] = _safe_int(bucket.get("cache_write_tokens"), 0) + usage["cache_write_tokens"]
            bucket["estimated_tokens"] = _safe_int(bucket.get("estimated_tokens"), 0) + (usage["total_tokens"] if usage["estimated"] else 0)
            bucket["elapsed_ms"] = _safe_int(bucket.get("elapsed_ms"), 0) + max(0, elapsed_ms)
            bucket["last_ts"] = now_ts

        for target in (
            totals,
            by_provider.setdefault(provider_key, {}),
            by_task.setdefault(task_key, {}),
            by_day.setdefault(day, {}),
            by_day_provider.setdefault(day, {}).setdefault(provider_key, {}),
            by_day_task.setdefault(day, {}).setdefault(task_key, {}),
            by_session.setdefault(session_key, {}),
            by_day_session.setdefault(day, {}).setdefault(session_key, {}),
            by_hour.setdefault(hour, {}),
        ):
            if isinstance(target, dict):
                bump(target)
        recent.append(
            {
                "ts": now_ts,
                "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_key,
                "task": task_key,
                "session": session_key,
                "sender": sender_key,
                "message_type": message_type_key,
                "success": success,
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "reasoning_tokens": usage["reasoning_tokens"],
                "total_tokens": usage["total_tokens"],
                "cached_tokens": usage["cached_tokens"],
                "cache_read_tokens": usage["cache_read_tokens"],
                "cache_write_tokens": usage["cache_write_tokens"],
                "estimated": usage["estimated"],
                "elapsed_ms": max(0, elapsed_ms),
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": len(str(completion or "")),
                "error": _single_line(error, 160),
                "external": True,
            }
        )
        del recent[:-240]
        store["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        schedule_save = getattr(self, "_schedule_data_save", None)
        if callable(schedule_save):
            try:
                schedule_save(sections={"token_usage"}, delay=2.0)
            except Exception:
                pass
        last_save = _safe_float(getattr(self, "_external_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 30:
            self._external_token_usage_last_save_at = now_ts
            try:
                self._save_data_sync(sections={"token_usage"})
            except Exception:
                pass

    @staticmethod
    def _provider_id_from_llm_response(resp: Any) -> str:
        if resp is None:
            return ""
        for key in ("provider_id", "llm_provider_id", "chat_provider_id", "model"):
            value = _single_line(getattr(resp, key, ""), 160)
            if value:
                return value
        raw_response = getattr(resp, "raw_response", None)
        if isinstance(raw_response, dict):
            for key in ("provider_id", "llm_provider_id", "chat_provider_id", "model"):
                value = _single_line(raw_response.get(key), 160)
                if value:
                    return value
        return ""

    def _remember_external_llm_request_for_token_stats(self, event: Any, req: Any) -> None:
        if event is None or req is None:
            return
        if bool(getattr(event, "private_companion_skip_external_token_stats", False)):
            return
        prompt = self._request_prompt_for_token_stats(req)
        try:
            setattr(event, "private_companion_external_token_prompt", prompt)
            setattr(event, "private_companion_external_token_start", time.time())
        except Exception:
            pass

    @staticmethod
    def _is_llm_budget_exempt_task(task: str | None) -> bool:
        return str(task or "") in {
            "proactive_framework",
            "voice_framework",
            "private_image_vision",
            "group_image_vision",
            "private_image_only_framework",
            "private_image_only_fallback",
            "roleplay_draft_from_persona",
            "roleplay_draft_json_repair",
            "provider_test",
        }

    def _today_llm_token_total(self, *, include_budget_exempt: bool = False) -> int:
        usage = self.data.get("token_usage")
        if not isinstance(usage, dict):
            return 0
        by_day = usage.get("by_day")
        if not isinstance(by_day, dict):
            return 0
        today = by_day.get(_today_key())
        if not isinstance(today, dict):
            return 0
        total = _safe_int(today.get("total_tokens"), 0)
        if include_budget_exempt:
            return total
        exempt_by_day = usage.get("budget_exempt_by_day")
        exempt_today = exempt_by_day.get(_today_key()) if isinstance(exempt_by_day, dict) else None
        exempt_tokens = _safe_int(exempt_today.get("total_tokens"), 0) if isinstance(exempt_today, dict) else 0
        if exempt_tokens <= 0:
            by_day_task = usage.get("by_day_task")
            today_tasks = by_day_task.get(_today_key()) if isinstance(by_day_task, dict) else None
            if isinstance(today_tasks, dict):
                exempt_tokens = sum(
                    _safe_int(bucket.get("total_tokens"), 0)
                    for task, bucket in today_tasks.items()
                    if self._is_llm_budget_exempt_task(task) and isinstance(bucket, dict)
                )
        return max(0, total - exempt_tokens)

    def _llm_daily_budget_remaining(self) -> int | None:
        limit = _safe_int(getattr(self, "daily_token_limit", 0), 0)
        if limit <= 0:
            return None
        return max(0, limit - self._today_llm_token_total())

    def _daily_token_soft_limit_should_defer(self, task: str | None = None) -> bool:
        if not getattr(self, "enable_daily_token_soft_limit", True):
            return False
        soft_limit = _safe_int(getattr(self, "daily_token_soft_limit", 0), 0)
        if soft_limit <= 0 or self._today_llm_token_total() < soft_limit:
            return False
        task_key = _single_line(task, 40) or "other"
        if self._is_llm_budget_exempt_task(task_key):
            return False
        ignore_soft_limit = getattr(self, "_proactive_intensity_ignores_token_soft_limit", None)
        if callable(ignore_soft_limit) and ignore_soft_limit(task_key):
            return False
        low_priority_tasks = {
            "news_digest",
            "external_event_self_link",
            "web_exploration_query",
            "web_exploration_digest",
            "qzone_comment",
            "qzone_publish",
            "creative_project",
            "creative_writing",
            "group_interject",
            "group_episode",
            "group_slang",
            "dialogue_episode",
            "memory_profile",
            "response_review",
            "relationship",
            "screen_narration",
            "photo_prompt",
            "reading_archive_vision",
            "proactive_framework",
            "voice_framework",
            "voice",
            "voice_repair",
            "yesterday_summary",
            "worldbook_registration",
            "game_emotional_afterglow",
        }
        return task_key in low_priority_tasks

    def _maintenance_token_saver_should_defer(self, task: str | None = None) -> bool:
        return self._daily_token_soft_limit_should_defer(task)

    def _can_run_llm_task(self, provider_id: str = "", *, task: str | None = None) -> bool:
        task_key = _single_line(task, 40) or "other"
        if self._is_llm_budget_exempt_task(task_key):
            return True
        if self._daily_token_soft_limit_should_defer(task_key):
            return False
        return self._llm_daily_budget_remaining() != 0

    def _record_llm_budget_skip(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        error: str = "daily_token_limit_exceeded",
    ) -> None:
        now_ts = _now_ts()
        day = _today_key()
        now_dt = self._token_usage_now_dt()
        store = self.data.setdefault("token_usage", {})
        if not isinstance(store, dict):
            store = {}
            self.data["token_usage"] = store
        skips = store.setdefault("budget_skips", {})
        if not isinstance(skips, dict):
            skips = {}
            store["budget_skips"] = skips
        skip_bucket = skips.setdefault(day, {})
        if isinstance(skip_bucket, dict):
            skip_bucket["count"] = _safe_int(skip_bucket.get("count"), 0) + 1
            skip_bucket["last_ts"] = now_ts
            skip_bucket[error] = _safe_int(skip_bucket.get(error), 0) + 1
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        recent.append(
            {
                "ts": now_ts,
                "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_id or "(default)",
                "task": task or "other",
                "success": False,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated": False,
                "elapsed_ms": 0,
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": 0,
                "error": error,
            }
        )
        del recent[:-240]
        store["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        log_key = f"{day}:{error}"
        if getattr(self, "_token_budget_skip_logged_key", "") != log_key:
            self._token_budget_skip_logged_key = log_key
            if error in {"daily_token_soft_limit_deferred", "maintenance_token_saver_deferred"}:
                logger.info(
                    "[PrivateCompanion] 每日 Token 软限额已暂缓低优先级 LLM 任务: used=%s soft_limit=%s task=%s",
                    self._today_llm_token_total(),
                    self.daily_token_soft_limit,
                    task or "other",
                )
            else:
                logger.warning(
                    "[PrivateCompanion] 今日插件 Token 限额已达到: %s/%s",
                    self._today_llm_token_total(),
                    self.daily_token_limit,
                )
        last_save = _safe_float(getattr(self, "_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 60:
            self._token_usage_last_save_at = now_ts
            try:
                self._save_data_sync(sections={"token_usage"})
            except Exception:
                pass

    def _default_chat_provider_id(self, umo: str = "") -> str:
        """Resolve AstrBot's current chat provider for SDK versions that require an explicit id."""
        context = getattr(self, "context", None)
        candidates: list[Any] = []
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) else None
        if umo:
            candidates.append(umo)
        if isinstance(users, dict):
            candidates.extend(
                str(user.get("umo") or "").strip()
                for user in users.values()
                if isinstance(user, dict) and str(user.get("umo") or "").strip()
            )
        candidates.append("")
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            seen: set[str] = set()
            for raw_umo in candidates:
                candidate_umo = str(raw_umo or "").strip()
                if candidate_umo in seen:
                    continue
                seen.add(candidate_umo)
                provider = None
                try:
                    provider = get_using(umo=candidate_umo) if candidate_umo else get_using()
                except TypeError:
                    try:
                        provider = get_using(candidate_umo) if candidate_umo else get_using(None)
                    except Exception:
                        provider = None
                except Exception:
                    provider = None
                provider_id = self._provider_id_from_instance(provider)
                if provider_id:
                    return provider_id
        # get_using_provider 拿不到时再兜两层：AstrBot 已经选出了默认对话模型
        # （启动日志会打印 "Selected ... as default chat model provider"），
        # 插件这边不该因为一条取值路径不通就整个放弃；否则所有没有显式配置
        # provider 的任务（日程、日记）会静默退化成模板兜底，且无从归因。
        fallback_id = self._chat_provider_id_from_registry(context)
        if fallback_id:
            logger.info("[PrivateCompanion] 默认对话 Provider 经注册表兜底解析: %s", fallback_id)
            return fallback_id
        logger.warning(
            "[PrivateCompanion] 无法解析默认对话 Provider：get_using_provider 与注册表兜底都没有结果；"
            "未显式配置 provider 的模型任务将退化为模板兜底"
        )
        return ""

    @staticmethod
    def _provider_id_from_instance(provider: Any) -> str:
        if provider is None:
            return ""
        try:
            meta = provider.meta()
            value = getattr(meta, "id", "") or (meta.get("id") if isinstance(meta, dict) else "")
            if value:
                return _single_line(value, 160)
        except Exception:
            pass
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        if isinstance(config, dict):
            for key in ("id", "provider_id"):
                value = _single_line(config.get(key), 160)
                if value:
                    return value
        return _single_line(getattr(provider, "provider_id", ""), 160)

    def _resolve_chat_provider_id(self, provider_id: str | None = None, *, umo: str = "") -> str:
        return str(
            provider_id
            or runtime_persona_setting(self, "llm_provider_id", "")
            or self._default_chat_provider_id(umo)
            or ""
        ).strip()

    def _sensitive_model_replacement_provider(self, primary_provider_id: str = "") -> str:
        if not bool(getattr(self, "enable_sensitive_model_replacement", False)):
            return ""
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), "plugin"):
            return ""
        replacement = _single_line(getattr(self, "sensitive_replacement_provider_id", ""), 160)
        if not replacement or replacement == _single_line(primary_provider_id, 160):
            return ""
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not callable(getter):
            return ""
        try:
            return replacement if getter(replacement) is not None else ""
        except Exception:
            return ""

    def _sensitive_model_replacement_keyword(self, text: Any) -> str:
        return contains_sensitive_refusal(
            text,
            getattr(self, "sensitive_replacement_keywords", ""),
        )

    def _chat_provider_id_from_registry(self, context: Any) -> str:
        """从 AstrBot Provider 注册表/配置里兜底取一个已加载的对话 Provider。"""
        get_all = getattr(context, "get_all_providers", None)
        if callable(get_all):
            try:
                providers = get_all() or []
            except Exception:
                providers = []
            for provider in providers:
                provider_id = self._provider_id_from_instance(provider)
                if provider_id:
                    return provider_id

        config = None
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter()
            except Exception:
                config = None
        settings = config.get("provider_settings") if isinstance(config, dict) else None
        if isinstance(settings, dict):
            value = _single_line(settings.get("default_provider_id"), 160)
            # 配置里存在 default provider 不代表实例已经装载；启动竞态时
            # 必须通过注册表确认，避免把“未就绪”误报成可用。
            if value and self._provider_instance_exists(context, value):
                return value
        return ""

    @staticmethod
    def _provider_instance_exists(context: Any, provider_id: str) -> bool:
        getter = getattr(context, "get_provider_by_id", None)
        if not callable(getter):
            return False
        try:
            return getter(provider_id) is not None
        except Exception:
            return False

    def _chat_provider_ready(self) -> bool:
        """Return whether an explicit or currently loaded chat Provider is ready."""
        if _single_line(runtime_persona_setting(self, "llm_provider_id", ""), 160):
            return True
        return bool(self._default_chat_provider_id())

    @staticmethod
    def _normalize_model_timeout_overrides(value: Any) -> dict[str, int]:
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, int] = {}
        for raw_key, raw_timeout in raw.items():
            key = str(raw_key or "").strip()
            if key not in MODEL_PROVIDER_KEYS:
                continue
            try:
                timeout = int(float(raw_timeout))
            except (TypeError, ValueError):
                continue
            if 5 <= timeout <= 600:
                normalized[key] = timeout
        return normalized

    @classmethod
    def _normalize_model_token_limit_overrides(cls, value: Any) -> dict[str, int]:
        """Normalize optional per-card single-request token ceilings."""
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, int] = {}
        for raw_key, raw_limit in raw.items():
            key = str(raw_key or "").strip()
            if key not in MODEL_PROVIDER_KEYS:
                continue
            try:
                limit = int(float(raw_limit))
            except (TypeError, ValueError):
                continue
            if cls.MODEL_TOKEN_LIMIT_MIN <= limit <= cls.MODEL_TOKEN_LIMIT_MAX:
                normalized[key] = limit
        return normalized

    @staticmethod
    def _normalize_model_fallback_overrides(value: Any) -> dict[str, str]:
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_key, raw_provider_id in raw.items():
            key = str(raw_key or "").strip()
            provider_id = _single_line(raw_provider_id, 160)
            if key in MODEL_PROVIDER_KEYS and provider_id:
                normalized[key] = provider_id
        return normalized

    def _model_provider_key_for_call(self, task: str, provider_id: str = "", explicit_key: str = "") -> str:
        key = str(explicit_key or "").strip()
        if key in MODEL_PROVIDER_KEYS:
            provider_key = key
        else:
            task_key = str(task or "").strip()
            provider_key = MODEL_TASK_PROVIDER_KEYS.get(task_key, "")
            if not provider_key:
                for prefix, candidate_key in MODEL_TASK_PROVIDER_PREFIXES:
                    if task_key.startswith(prefix):
                        provider_key = candidate_key
                        break
        mode = str(getattr(self, "provider_config_mode", "quick") or "quick")
        if provider_key and mode == "quick":
            provider_key = MODEL_QUICK_TIMEOUT_KEYS.get(provider_key, provider_key)
        if provider_key:
            return provider_key

        selected_provider = str(provider_id or "").strip()
        config = getattr(self, "config", {})
        if not selected_provider:
            return ""
        matching_keys = [
            candidate_key
            for candidate_key in MODEL_PROVIDER_KEYS
            if str(_flat_get(config, candidate_key, "") or "").strip() == selected_provider
        ]
        if mode == "quick":
            for candidate_key in (
                "FAST_RESPONSE_PROVIDER_ID",
                "COMPLEX_REASONING_PROVIDER_ID",
                "CREATIVE_MODEL_PROVIDER_ID",
                "PLUGIN_VISION_PROVIDER_ID",
            ):
                if candidate_key in matching_keys:
                    return candidate_key
        return matching_keys[0] if len(matching_keys) == 1 else ""

    def _model_fallback_provider_id(self, provider_key: str, primary_provider_id: str = "") -> str:
        key = str(provider_key or "").strip()
        fallbacks = getattr(self, "model_fallback_overrides", {})
        if key not in MODEL_PROVIDER_KEYS or not isinstance(fallbacks, dict):
            return ""
        fallback_id = _single_line(fallbacks.get(key), 160)
        return fallback_id if fallback_id and fallback_id != str(primary_provider_id or "").strip() else ""

    def _model_fallback_provider_for_call(
        self,
        *,
        task: str,
        primary_provider_id: str,
        provider_key: str = "",
    ) -> tuple[str, str]:
        resolved_key = self._model_provider_key_for_call(task, primary_provider_id, provider_key)
        return resolved_key, self._model_fallback_provider_id(resolved_key, primary_provider_id)

    def _model_timeout_provider_key(self, task: str, provider_id: str = "", timeout_key: str = "") -> str:
        provider_key = self._model_provider_key_for_call(task, provider_id, timeout_key)
        overrides = getattr(self, "model_timeout_overrides", {})
        if provider_key and isinstance(overrides, dict) and provider_key in overrides:
            return provider_key
        return provider_key

    def _model_timeout_seconds_for_call(
        self,
        *,
        task: str,
        provider_id: str = "",
        timeout_key: str = "",
        timeout_seconds: float | None = None,
    ) -> float | None:
        if timeout_seconds is not None:
            explicit = _safe_float(timeout_seconds, 0.0, 0.0)
            return min(600.0, explicit) if explicit >= 5.0 else None
        overrides = getattr(self, "model_timeout_overrides", {})
        if not isinstance(overrides, dict):
            return None
        provider_key = self._model_timeout_provider_key(task, provider_id, timeout_key)
        configured = _safe_float(overrides.get(provider_key), 0.0, 0.0)
        return min(600.0, configured) if configured >= 5.0 else None

    def _model_token_limit_provider_key(
        self,
        task: str,
        provider_id: str = "",
        token_limit_key: str = "",
    ) -> str:
        return self._model_provider_key_for_call(task, provider_id, token_limit_key)

    def _model_token_limit_for_call(
        self,
        *,
        task: str,
        provider_id: str = "",
        token_limit_key: str = "",
        token_limit: int | float | None = None,
    ) -> int | None:
        """Return the configured card ceiling, or ``None`` when disabled."""
        raw = token_limit
        if raw is None:
            overrides = getattr(self, "model_token_limit_overrides", {})
            if not isinstance(overrides, dict):
                return None
            provider_key = self._model_token_limit_provider_key(task, provider_id, token_limit_key)
            raw = overrides.get(provider_key)
        try:
            parsed = int(float(raw))
        except (TypeError, ValueError):
            return None
        if self.MODEL_TOKEN_LIMIT_MIN <= parsed <= self.MODEL_TOKEN_LIMIT_MAX:
            return parsed
        return None

    @classmethod
    def _estimate_model_request_tokens(
        cls,
        prompt: Any,
        *,
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
    ) -> int:
        """Estimate text, bounded image input, and requested output tokens."""
        parts = [
            str(system_prompt or "").strip(),
            str(prompt or "").strip(),
            str(tool_schema or "").strip(),
        ]
        input_text = "\n\n".join(part for part in parts if part)
        try:
            output_tokens = max(0, int(float(max_tokens or 0)))
        except (TypeError, ValueError):
            output_tokens = 0
        try:
            images = max(0, int(float(image_count or 0)))
        except (TypeError, ValueError):
            images = 0
        return (
            cls._estimate_token_count(input_text)
            + output_tokens
            + images * cls.MODEL_IMAGE_TOKEN_ESTIMATE
        )

    def _model_token_limit_route_for_call(
        self,
        *,
        task: str,
        primary_provider_id: str,
        fallback_provider_id: str,
        provider_key: str = "",
        prompt: Any = "",
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
        token_limit: int | float | None = None,
    ) -> tuple[bool, int | None, int]:
        """Decide whether a card's fallback should handle this request."""
        if not fallback_provider_id or not primary_provider_id:
            return False, None, self._estimate_model_request_tokens(
                prompt,
                system_prompt=system_prompt,
                tool_schema=tool_schema,
                max_tokens=max_tokens,
                image_count=image_count,
            )
        limit = self._model_token_limit_for_call(
            task=task,
            provider_id=primary_provider_id,
            token_limit_key=provider_key,
            token_limit=token_limit,
        )
        estimate = self._estimate_model_request_tokens(
            prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            image_count=image_count,
        )
        if limit is None or estimate <= limit:
            return False, limit, estimate
        logger.warning(
            "[PrivateCompanion] 请求预估 Token 超过模型卡上限，跳过主模型并切换备用模型: task=%s card=%s estimate=%s limit=%s primary=%s fallback=%s",
            _single_line(task, 80) or "unknown",
            provider_key or "unknown",
            estimate,
            limit,
            _single_line(primary_provider_id, 120),
            _single_line(fallback_provider_id, 120),
        )
        return True, limit, estimate

    def _model_token_limit_should_skip_primary(
        self,
        *,
        task: str,
        provider_id: str,
        primary_provider_id: str,
        fallback_provider_id: str,
        provider_key: str,
        prompt: Any = "",
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
        token_limit: int | float | None = None,
    ) -> bool:
        """Return whether a direct provider path should skip its primary card."""
        if not provider_id or provider_id != primary_provider_id:
            return False
        routed, _limit, _estimate = self._model_token_limit_route_for_call(
            task=task,
            primary_provider_id=primary_provider_id,
            fallback_provider_id=fallback_provider_id,
            provider_key=provider_key,
            prompt=prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            image_count=image_count,
            token_limit=token_limit,
        )
        return routed

    @staticmethod
    def _llm_tool_schema_for_usage(tools: Any) -> str:
        """Serialize a tool schema for fallback token estimation only."""
        if tools is None:
            return ""
        schema: Any = None
        for method_name in ("openai_schema", "get_func_desc_openai_style"):
            builder = getattr(tools, method_name, None)
            if not callable(builder):
                continue
            try:
                schema = builder()
                break
            except Exception:
                continue
        if schema is None:
            schema = tools
        try:
            return json.dumps(schema, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(schema or "")

    @staticmethod
    def _llm_tool_response_for_usage(response: Any, completion: str) -> str:
        """Include tool-only output when a provider omits native usage data."""
        if response is None:
            return completion
        names = getattr(response, "tools_call_name", None) or []
        arguments = getattr(response, "tools_call_args", None) or []
        if not names and not arguments:
            return completion
        try:
            tool_output = json.dumps(
                {"tools_call_name": names, "tools_call_args": arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except Exception:
            tool_output = str({"tools_call_name": names, "tools_call_args": arguments})
        return "\n\n".join(part for part in (completion, tool_output) if part)

    async def _llm_tool_call(
        self,
        prompt: str,
        *,
        tools: Any,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
        system_prompt: str | None = None,
        timeout_key: str | None = None,
        timeout_seconds: float | None = None,
        token_limit: int | float | None = None,
        strict_provider: bool = False,
    ) -> Any | None:
        """Call a provider with native tools and one optional card fallback."""
        selected_provider = self._resolve_chat_provider_id(provider_id)
        task_key = _single_line(task, 40) or self._classify_llm_prompt(prompt)
        tool_schema = self._llm_tool_schema_for_usage(tools)
        usage_prompt = "\n\n".join(
            part
            for part in (
                str(system_prompt or "").strip(),
                str(prompt or "").strip(),
                tool_schema,
            )
            if part
        )
        budget_exempt = self._is_llm_budget_exempt_task(task_key)
        if not budget_exempt and self._daily_token_soft_limit_should_defer(task_key):
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=usage_prompt,
                error="daily_token_soft_limit_deferred",
            )
            return None
        if not budget_exempt and self._llm_daily_budget_remaining() == 0:
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=usage_prompt,
            )
            return None
        if not selected_provider:
            return None
        if strict_provider:
            provider_key, fallback_provider = str(timeout_key or task_key), ""
        else:
            provider_key, fallback_provider = self._model_fallback_provider_for_call(
                task=task_key,
                primary_provider_id=selected_provider,
                provider_key=str(timeout_key or ""),
            )
        token_routed, _, estimated_tokens = self._model_token_limit_route_for_call(
            task=task_key,
            primary_provider_id=selected_provider,
            fallback_provider_id=fallback_provider,
            provider_key=provider_key or str(timeout_key or ""),
            prompt=prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            token_limit=token_limit,
        )
        candidates = ([fallback_provider] if token_routed else [selected_provider])
        if not token_routed and fallback_provider:
            candidates.append(fallback_provider)
        sensitive_replacement = self._sensitive_model_replacement_provider(selected_provider)
        sensitive_replacement_used = False
        for attempt_index, attempt_provider in enumerate(candidates):
            started_at = time.time()
            response = None
            try:
                kwargs: dict[str, Any] = {
                    "prompt": prompt,
                    "chat_provider_id": attempt_provider,
                    "tools": tools,
                }
                if max_tokens and max_tokens > 0:
                    kwargs["max_tokens"] = max_tokens
                if system_prompt:
                    kwargs["system_prompt"] = system_prompt
                effective_timeout = self._model_timeout_seconds_for_call(
                    task=task_key,
                    provider_id=attempt_provider,
                    timeout_key=provider_key or str(timeout_key or ""),
                    timeout_seconds=timeout_seconds,
                )
                request_call = self.context.llm_generate(**kwargs)
                try:
                    response = (
                        await asyncio.wait_for(request_call, timeout=effective_timeout)
                        if effective_timeout is not None
                        else await request_call
                    )
                except asyncio.TimeoutError as exc:
                    if effective_timeout is None:
                        raise TimeoutError(f"模型任务 {task_key} 调用超时") from exc
                    raise TimeoutError(f"模型任务 {task_key} 超过 {effective_timeout:.0f} 秒未返回") from exc

                completion = str(getattr(response, "completion_text", "") or "").strip()
                usage_completion = self._llm_tool_response_for_usage(response, completion)
                sensitive_keyword = self._sensitive_model_replacement_keyword(completion)
                if sensitive_keyword:
                    if sensitive_replacement and not sensitive_replacement_used:
                        sensitive_replacement_used = True
                        candidates.append(sensitive_replacement)
                        logger.info(
                            "[PrivateCompanion] 插件工具模型命中敏感拒答，切换指定模型重试: provider=%s target=%s keyword=%s",
                            _single_line(attempt_provider, 120),
                            _single_line(sensitive_replacement, 120),
                            _single_line(sensitive_keyword, 80),
                        )
                        continue
                    logger.warning(
                        "[PrivateCompanion] 插件工具指定模型仍返回敏感拒答，丢弃本次文本: provider=%s keyword=%s",
                        _single_line(attempt_provider, 120),
                        _single_line(sensitive_keyword, 80),
                    )
                    return None
                response_role = _single_line(getattr(response, "role", ""), 20).lower()
                semantic_provider_error = _looks_like_upstream_llm_error_response(completion)
                if response_role == "err" or semantic_provider_error:
                    failure_code = (
                        "provider_error_role"
                        if response_role == "err"
                        else "semantic_provider_error"
                    )
                    self._record_llm_usage(
                        provider_id=attempt_provider,
                        task=task_key,
                        prompt=usage_prompt,
                        completion=usage_completion,
                        elapsed_ms=int((time.time() - started_at) * 1000),
                        success=False,
                        error=failure_code,
                        resp=response,
                        budget_exempt=budget_exempt,
                    )
                    if attempt_index + 1 < len(candidates):
                        logger.warning(
                            "[PrivateCompanion] 工具调用主模型失败，尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s kind=%s",
                            _single_line(task_key, 80) or "unknown",
                            provider_key or "unknown",
                            _single_line(selected_provider, 120),
                            _single_line(candidates[attempt_index + 1], 120),
                            failure_code,
                        )
                        continue
                    return None
                if response is None:
                    self._record_llm_usage(
                        provider_id=attempt_provider,
                        task=task_key,
                        prompt=usage_prompt,
                        completion="",
                        elapsed_ms=int((time.time() - started_at) * 1000),
                        success=False,
                        error="empty_response",
                        budget_exempt=budget_exempt,
                    )
                    if attempt_index + 1 < len(candidates):
                        continue
                    return None
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion=usage_completion,
                    elapsed_ms=int((time.time() - started_at) * 1000),
                    success=True,
                    resp=response,
                    budget_exempt=budget_exempt,
                )
                if attempt_index > 0 or token_routed:
                    logger.info(
                        "[PrivateCompanion] 工具调用使用备用模型: task=%s card=%s provider=%s estimated_tokens=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120),
                        estimated_tokens,
                    )
                return response
            except Exception as exc:
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - started_at) * 1000),
                    success=False,
                    error=str(exc),
                    resp=response,
                    budget_exempt=budget_exempt,
                )
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "[PrivateCompanion] 工具调用失败，尝试卡片备用模型: task=%s card=%s error=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(exc, 160),
                    )
                    continue
                raise
        return None

    async def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
        *,
        system_prompt: str | None = None,
        timeout_key: str | None = None,
        timeout_seconds: float | None = None,
        token_limit: int | float | None = None,
        strict_provider: bool = False,
    ) -> str | None:
        selected_provider = self._resolve_chat_provider_id(provider_id)
        peak_router = getattr(self, "_apply_deepseek_peak_replacement", None)
        if not strict_provider and callable(peak_router) and (
            str(provider_id or "").strip()
            or str(runtime_persona_setting(self, "llm_provider_id", "") or "").strip()
        ):
            selected_provider = peak_router(selected_provider)
        task_key = _single_line(task, 40) or self._classify_llm_prompt(prompt)
        usage_prompt = (
            f"{str(system_prompt or '').strip()}\n\n{str(prompt or '').strip()}".strip()
            if str(system_prompt or "").strip()
            else str(prompt or "")
        )
        budget_exempt = self._is_llm_budget_exempt_task(task_key)
        if not budget_exempt and self._daily_token_soft_limit_should_defer(task_key):
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=usage_prompt,
                error="daily_token_soft_limit_deferred",
            )
            return None
        if not budget_exempt and self._llm_daily_budget_remaining() == 0:
            self._record_llm_budget_skip(provider_id=selected_provider, task=task_key, prompt=usage_prompt)
            return None
        if strict_provider:
            provider_key, fallback_provider = str(timeout_key or task_key), ""
        else:
            provider_key, fallback_provider = self._model_fallback_provider_for_call(
                task=task_key,
                primary_provider_id=selected_provider,
                provider_key=str(timeout_key or ""),
            )
        if fallback_provider and callable(peak_router):
            fallback_provider = peak_router(fallback_provider)
        token_routed, _, estimated_tokens = self._model_token_limit_route_for_call(
            task=task_key,
            primary_provider_id=selected_provider,
            fallback_provider_id=fallback_provider,
            provider_key=provider_key or str(timeout_key or ""),
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            token_limit=token_limit,
        )
        candidates = ([fallback_provider] if token_routed else [selected_provider])
        if not token_routed and fallback_provider:
            candidates.append(fallback_provider)
        sensitive_replacement = self._sensitive_model_replacement_provider(selected_provider)
        sensitive_replacement_used = False
        for attempt_index, attempt_provider in enumerate(candidates):
            start = time.time()
            resp = None
            try:
                if not attempt_provider:
                    raise RuntimeError("未找到可用的 AstrBot 默认模型 Provider")
                kwargs: dict[str, Any] = {"prompt": prompt, "chat_provider_id": attempt_provider}
                if max_tokens and max_tokens > 0:
                    kwargs["max_tokens"] = max_tokens
                if system_prompt:
                    kwargs["system_prompt"] = system_prompt
                effective_timeout = self._model_timeout_seconds_for_call(
                    task=task_key,
                    provider_id=attempt_provider,
                    timeout_key=provider_key or str(timeout_key or ""),
                    timeout_seconds=timeout_seconds,
                )
                try:
                    request_call = self.context.llm_generate(**kwargs)
                    if effective_timeout is not None:
                        resp = await asyncio.wait_for(request_call, timeout=effective_timeout)
                    else:
                        resp = await request_call
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(f"模型任务 {task_key} 超过 {effective_timeout:.0f} 秒未返回") from exc
                if resp and resp.completion_text:
                    completion = resp.completion_text.strip()
                    if completion:
                        sensitive_keyword = self._sensitive_model_replacement_keyword(completion)
                        if sensitive_keyword:
                            if sensitive_replacement and not sensitive_replacement_used:
                                sensitive_replacement_used = True
                                candidates.append(sensitive_replacement)
                                logger.info(
                                    "[PrivateCompanion] 插件模型命中敏感拒答，切换指定模型重试: provider=%s target=%s keyword=%s",
                                    _single_line(attempt_provider, 120),
                                    _single_line(sensitive_replacement, 120),
                                    _single_line(sensitive_keyword, 80),
                                )
                                continue
                            logger.warning(
                                "[PrivateCompanion] 插件指定模型仍返回敏感拒答，丢弃本次文本: provider=%s keyword=%s",
                                _single_line(attempt_provider, 120),
                                _single_line(sensitive_keyword, 80),
                            )
                            return None
                        response_role = _single_line(getattr(resp, "role", ""), 20).lower()
                        semantic_provider_error = _looks_like_upstream_llm_error_response(
                            completion
                        )
                        if response_role == "err" or semantic_provider_error:
                            failure_code = (
                                "provider_error_role"
                                if response_role == "err"
                                else "semantic_provider_error"
                            )
                            self._record_llm_usage(
                                provider_id=attempt_provider,
                                task=task_key,
                                prompt=usage_prompt,
                                completion=completion,
                                elapsed_ms=int((time.time() - start) * 1000),
                                success=False,
                                error=failure_code,
                                resp=resp,
                                budget_exempt=budget_exempt,
                            )
                            if attempt_index + 1 < len(candidates):
                                logger.warning(
                                    "[PrivateCompanion] 主模型返回 Provider 错误响应,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s kind=%s",
                                    _single_line(task_key, 80) or "unknown",
                                    provider_key or "unknown",
                                    _single_line(attempt_provider, 120),
                                    _single_line(candidates[attempt_index + 1], 120),
                                    failure_code,
                                )
                            else:
                                logger.warning(
                                    "[PrivateCompanion] LLM 返回 Provider 错误响应: task=%s provider=%s kind=%s",
                                    _single_line(task_key, 80) or "unknown",
                                    _single_line(attempt_provider, 120) or "default",
                                    failure_code,
                                )
                            continue
                        self._record_llm_usage(
                            provider_id=attempt_provider,
                            task=task_key,
                            prompt=usage_prompt,
                            completion=completion,
                            elapsed_ms=int((time.time() - start) * 1000),
                            success=True,
                            resp=resp,
                            budget_exempt=budget_exempt,
                        )
                        if attempt_index > 0 or token_routed:
                            logger.info(
                                "[PrivateCompanion] 备用模型调用成功: task=%s card=%s provider=%s estimated_tokens=%s",
                                _single_line(task_key, 80) or "unknown",
                                provider_key or "unknown",
                                _single_line(attempt_provider, 120),
                                estimated_tokens,
                            )
                        return completion
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error="empty_response",
                    resp=resp,
                    budget_exempt=budget_exempt,
                )
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "[PrivateCompanion] 主模型返回空结果,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120),
                        _single_line(candidates[attempt_index + 1], 120),
                    )
            except Exception as e:
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=str(e),
                    budget_exempt=budget_exempt,
                )
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "[PrivateCompanion] 主模型调用失败,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s error=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120) or "default",
                        _single_line(candidates[attempt_index + 1], 120),
                        _single_line(e, 160),
                    )
                    continue
                logger.warning(
                    "[PrivateCompanion] LLM 调用失败: task=%s provider=%s error=%s",
                    _single_line(task_key, 80) or "unknown",
                    _single_line(attempt_provider, 120) or "default",
                    _single_line(e, 160),
                )
        return None
