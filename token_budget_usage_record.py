# -*- coding: utf-8 -*-
"""TokenBudgetUsageRecordMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 284 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

import time
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from typing import Any



class TokenBudgetUsageRecordMixin:
    """TokenBudgetUsageRecordMixin（从 TokenBudgetMixin 拆出）。"""


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
        request_policy: dict[str, Any] | None = None,
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
                **(request_policy or {}),
                "provider_attempts": None,
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
