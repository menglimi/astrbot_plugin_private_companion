# -*- coding: utf-8 -*-
"""TokenBudgetBudgetProviderMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 299 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import contains_sensitive_refusal, scope_allows
from .persona_config import runtime_persona_setting
from .token_budget_shared import logger
from typing import Any



class TokenBudgetBudgetProviderMixin:
    """TokenBudgetBudgetProviderMixin（从 TokenBudgetMixin 拆出）。"""


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
                    "每日 Token 软限额已暂缓低优先级 LLM 任务: used=%s soft_limit=%s task=%s",
                    self._today_llm_token_total(),
                    self.daily_token_soft_limit,
                    task or "other",
                )
            else:
                logger.warning(
                    "今日插件 Token 限额已达到: %s/%s",
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
            logger.info("默认对话 Provider 经注册表兜底解析: %s", fallback_id)
            return fallback_id
        logger.warning(
            "无法解析默认对话 Provider：get_using_provider 与注册表兜底都没有结果；"
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
