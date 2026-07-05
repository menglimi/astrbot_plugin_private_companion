# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key

class TokenBudgetMixin:
    """Methods split from main.PrivateCompanionPlugin."""

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
        return max(1, int(ascii_chars / 4.0 + non_ascii_chars / 1.6))

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
        candidates = [
            getattr(resp, "usage", None),
            getattr(resp, "token_usage", None),
            getattr(resp, "raw_usage", None),
        ]
        raw_completion = getattr(resp, "raw_completion", None)
        if raw_completion is not None:
            candidates.append(getattr(raw_completion, "usage", None))
        raw_response = getattr(resp, "raw_response", None)
        if isinstance(raw_response, dict):
            candidates.extend([
                raw_response.get("usage"),
                raw_response.get("token_usage"),
            ])
        usage = next((item for item in candidates if item), None)
        prompt_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens", "prompt", "input")
        completion_tokens = self._usage_value(usage, "completion_tokens", "output_tokens", "completion", "output")
        total_tokens = self._usage_value(usage, "total_tokens", "total")
        cache_read_tokens = self._usage_value(
            usage,
            "input_cached",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
            "input_token_details.cache_read",
            "cache_read_input_tokens",
            "cache_read_tokens",
            "prompt_cache_hit_tokens",
        )
        cache_write_tokens = self._usage_value(
            usage,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_input_tokens",
            "cache_write_tokens",
            "prompt_cache_creation_tokens",
        )
        cached_tokens = self._usage_value(
            usage,
            "input_cached",
            "cached_tokens",
            "prompt_cached_tokens",
            "input_cached_tokens",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
        )
        if cached_tokens <= 0:
            cached_tokens = cache_read_tokens
        input_other_tokens = self._usage_value(usage, "input_other")
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
            estimated = (not usage) or prompt_estimated or completion_estimated
        elif prompt_tokens <= 0 and completion_tokens <= 0:
            prompt_tokens = self._estimate_token_count(prompt)
            completion_tokens = max(0, total_tokens - prompt_tokens)
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
            "cached_tokens": max(0, cached_tokens),
            "cache_read_tokens": max(0, cache_read_tokens),
            "cache_write_tokens": max(0, cache_write_tokens),
            "estimated": estimated or not usage,
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
            ("memory_profile", ("私聊记忆整理", "长期画像", "user_traits")),
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
                self._save_data_sync()
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
                schedule_save(delay=2.0)
            except Exception:
                pass
        last_save = _safe_float(getattr(self, "_external_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 30:
            self._external_token_usage_last_save_at = now_ts
            try:
                self._save_data_sync()
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
            "private_image_only_framework",
            "private_image_only_fallback",
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
            "private_reading_vision",
            "proactive_framework",
            "voice_framework",
            "voice",
            "voice_repair",
            "yesterday_summary",
            "worldbook_registration",
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
                self._save_data_sync()
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
        return str(provider_id or self.llm_provider_id or self._default_chat_provider_id(umo) or "").strip()

    async def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
    ) -> str | None:
        start = time.time()
        selected_provider = self._resolve_chat_provider_id(provider_id)
        task_key = _single_line(task, 40) or self._classify_llm_prompt(prompt)
        budget_exempt = self._is_llm_budget_exempt_task(task_key)
        if not budget_exempt and self._daily_token_soft_limit_should_defer(task_key):
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                error="daily_token_soft_limit_deferred",
            )
            return None
        if not budget_exempt and self._llm_daily_budget_remaining() == 0:
            self._record_llm_budget_skip(provider_id=selected_provider, task=task_key, prompt=prompt)
            return None
        try:
            if not selected_provider:
                raise RuntimeError("未找到可用的 AstrBot 默认模型 Provider")
            kwargs = {"prompt": prompt, "chat_provider_id": selected_provider}
            resp = await self.context.llm_generate(**kwargs)
            if resp and resp.completion_text:
                completion = resp.completion_text.strip()
                self._record_llm_usage(
                    provider_id=selected_provider,
                    task=task_key,
                    prompt=prompt,
                    completion=completion,
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=True,
                    resp=resp,
                    budget_exempt=budget_exempt,
                )
                return completion
            self._record_llm_usage(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                completion="",
                elapsed_ms=int((time.time() - start) * 1000),
                success=False,
                error="empty_response",
                resp=resp if "resp" in locals() else None,
                budget_exempt=budget_exempt,
            )
        except Exception as e:
            self._record_llm_usage(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                completion="",
                elapsed_ms=int((time.time() - start) * 1000),
                success=False,
                error=str(e),
                budget_exempt=budget_exempt,
            )
            logger.warning(f"[PrivateCompanion] LLM 调用失败: {e}")
        return None
