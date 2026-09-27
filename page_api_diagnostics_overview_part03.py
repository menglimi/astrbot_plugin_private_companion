# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiDiagnosticsOverviewPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_diagnostics_overview.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 448 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsOverviewMixin）。
"""
from __future__ import annotations
from .page_api_diagnostics_overview_shared import Any
from .page_api_diagnostics_overview_shared import _today_key
from .page_api_diagnostics_overview_shared import hashlib



class PrivateCompanionPageApiDiagnosticsOverviewPart03Mixin:
    """PrivateCompanionPageApiDiagnosticsOverviewPart03Mixin（从 PrivateCompanionPageApiDiagnosticsOverviewMixin 拆出）。"""


    def _build_diagnostics(self, users: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []

        def add(level: str, title: str, text: str, action: str = "", warning_code: str = "") -> None:
            item = {"level": level, "title": title, "text": text, "action": action}
            if warning_code:
                item["warning_code"] = warning_code
                item["warning_type"] = self._troubleshooting_semantic_warning_type(warning_code)
            items.append(item)

        features = self._feature_flags()
        providers = self._provider_settings()
        group_mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = self.plugin._configured_group_ids()
        blacklist = self.plugin._configured_group_blacklist_ids()
        enabled_users = sum(
            1
            for item in users.values()
            if isinstance(item, dict)
            and (
                item.get("proactive_private_enabled") is True
                or (
                    isinstance(item.get("unified_profile_capabilities"), dict)
                    and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                )
            )
        )
        enabled_groups = sum(1 for item in groups.values() if isinstance(item, dict) and item.get("enabled", True))

        if getattr(self.plugin, "enabled", False):
            add("ok", "插件已启用", "后台主动检查与事件处理会正常运行")
        else:
            add("error", "插件未启用", "当前不会进行私聊主动陪伴或群聊观察", "在配置中打开 enabled")

        if providers.get("LLM_PROVIDER_ID") or getattr(self.plugin, "llm_provider_id", ""):
            add("ok", "主模型可见", providers.get("LLM_PROVIDER_ID") or "运行态已配置")
        else:
            add("info", "主模型留空", "会回退到 AstrBot 默认模型；建议为陪伴插件单独配置主模型")

        intensity = self._proactive_intensity_summary()
        if intensity.get("enabled"):
            effective = intensity.get("effective") if isinstance(intensity.get("effective"), dict) else {}
            add(
                "warn",
                f"正在使用主动强度预设：{intensity.get('label') or intensity.get('preset')}",
                (
                    f"私聊有效上限 {effective.get('max_daily_messages_text') or effective.get('max_daily_messages')}，"
                    f"空闲 {effective.get('idle_minutes')} 分钟，"
                    f"最小间隔 {effective.get('min_interval_minutes')} 分钟；"
                    f"群唤醒冷却 {effective.get('group_wakeup_cooldown_seconds')} 秒，"
                    f"群插话间隔 {effective.get('group_interject_min_interval_minutes')} 分钟，"
                    f"群插话上限 {effective.get('group_interject_max_daily_text') or effective.get('group_interject_max_daily')}，"
                    f"兴趣唤醒概率 {round(float(effective.get('group_wakeup_interest_probability') or 0) * 100)}%。"
                    f"{'当前最高档会忽略 Token 软限额降载；' if effective.get('ignore_token_soft_limit') else ''}"
                    "预设只覆盖运行态有效频率，不改写手动参数，也不会绕过免打扰、休息、用户拒绝、隐私和硬限额。"
                ),
                "需要恢复原配置时，将“主动强度预设”改为关闭",
                "proactive.intensity_preset",
            )
        else:
            add(
                "info",
                "主动强度预设未启用",
                "当前完全沿用手动主动频率参数；如果想要高频主动，可在配置 → 功能开关 → 通用能力里选择预设。",
            )

        tts_summary = self._tts_runtime_summary(users)
        if tts_summary.get("enhancement_enabled"):
            if tts_summary.get("provider_available"):
                add(
                    "ok",
                    "TTS 语音链路可用",
                    f"模式 {tts_summary.get('mode')}，语种 {tts_summary.get('language')}，真实合成后端：{tts_summary.get('provider_label')}",
                )
            elif tts_summary.get("settings_enabled"):
                add(
                    "warn",
                    "TTS 配置已开但合成后端不可用",
                    f"会话 {tts_summary.get('umo') or '-'} 已启用 TTS 设置，但当前取不到 AstrBot TTS Provider 或 MiMo Voice Clone 服务",
                    "检查 TTS 合成后端；MiMo 模式需启用目标插件并保留 mimo_tts_speak 工具",
                    "tts.provider_unavailable",
                )
            else:
                add(
                    "warn",
                    "TTS 强化已开但没有可用合成后端",
                    "本插件能处理 <tts> 标签和文本转换；真正合成音频需要 AstrBot 会话 TTS Provider 或 MiMo Voice Clone 插件",
                    "在 TTS 配置中选择并启用一种真实语音合成后端",
                    "tts.provider_unavailable",
                )
        else:
            add(
                "info",
                "TTS 强化未开启",
                "VOICE_PROMPT_PROVIDER_ID / TTS文本转换模型都是文本模型；语音合成模型请在 AstrBot TTS provider 中配置",
            )

        if enabled_users:
            add("ok", "私聊对象已就绪", f"已启用 {enabled_users} 个私聊对象")
        else:
            add("warn", "暂无启用的私聊对象", "私聊主动陪伴没有明确目标", "在私聊页新增对象或配置 target_user_ids", "proactive.no_enabled_users")

        max_daily = int(getattr(self.plugin, "max_daily_messages", 0) or 0)
        effective_max_daily = max_daily
        max_daily_getter = getattr(self.plugin, "_runtime_max_daily_messages", None)
        if callable(max_daily_getter):
            try:
                effective_max_daily = int(max_daily_getter() or 0)
            except Exception:
                effective_max_daily = max_daily
        if effective_max_daily > 0:
            limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
            limit_is_unlimited = getattr(self.plugin, "_proactive_daily_limit_is_unlimited", None)
            effective_limit_text = limit_formatter(effective_max_daily) if callable(limit_formatter) else str(effective_max_daily)
            detail = f"每日有效上限 {effective_limit_text}"
            if effective_max_daily != max_daily:
                detail += f"（手动配置 {max_daily} 条）"
            if callable(limit_is_unlimited) and limit_is_unlimited(effective_max_daily):
                detail += "，当前使用最高档主动策略"
            add("ok", "私聊主动额度可用", detail)
        else:
            add("warn", "私聊主动已关闭", "每日主动上限为 0", "在模块配置里调高每日主动上限", "proactive.daily_limit_zero")

        if getattr(self.plugin, "enable_daily_token_soft_limit", True):
            soft_limit = int(getattr(self.plugin, "daily_token_soft_limit", 0) or 0)
            today_tokens = int(getattr(self.plugin, "_today_llm_token_total", lambda: 0)() or 0)
            ignore_soft_limit = bool(self._proactive_intensity_summary().get("effective", {}).get("ignore_token_soft_limit"))
            if soft_limit > 0 and today_tokens >= soft_limit:
                if ignore_soft_limit:
                    add(
                        "warn",
                        "Token 软限额已触发但最高档放行",
                        f"今日已用约 {today_tokens} Token；当前主动强度最高档会忽略软限额降载，低优先级任务仍可继续运行",
                        warning_code="token.soft_limit_ignored",
                    )
                else:
                    add(
                        "warn",
                        "每日 Token 软限额已接管",
                        f"今日已用约 {today_tokens} Token，低优先级后台 LLM 任务会暂缓",
                        warning_code="token.soft_limit_active",
                    )
            elif soft_limit > 0:
                add("ok", "每日 Token 软限额已启用", f"软限额 {soft_limit}，当前约 {today_tokens}")
            else:
                add("info", "每日 Token 软限额未设置", "只使用每日硬限额")
        else:
            add("info", "每日 Token 软限额已关闭", "功能全开时后台任务会按各自开关正常运行")

        if features.get("enable_companion_memory"):
            add("ok", "私聊本地画像已启用", "按私聊对象整理偏好、边界、关系线索和重要事实")
        else:
            add("info", "私聊本地画像已关闭", "不会新增私聊本地画像，已有资料仍可管理")
        if features.get("enable_expression_learning"):
            add("ok", "通用表达学习已启用", "按学习页设置的来源与范围用于私聊、主动私聊和群聊")
        else:
            add("info", "通用表达学习已关闭", "不会继续归纳或注入表达规则")

        if features.get("enable_livingmemory_integration"):
            living_summary = self._livingmemory_summary()
            living_level = "warn" if living_summary.get("conflict") else ("ok" if living_summary.get("compatible_available") else "warn")
            if living_summary.get("conflict_warning"):
                living_text = str(living_summary.get("conflict_warning") or "")
            elif living_summary.get("selected_plugin_name"):
                living_text = f"当前使用：{living_summary.get('selected_plugin_name')}"
            else:
                living_text = "已启用协同，但当前未检测到可用记忆插件"
            add(
                living_level,
                "记忆插件协同",
                living_text,
                warning_code="memory.integration_conflict" if living_summary.get("conflict") else "memory.integration_unavailable",
            )

        if features.get("enable_bilibili_integration"):
            bili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
            add(
                "ok" if bili_available else "info",
                "B站 AI Bot 联动",
                "已检测到 B站 AI Bot 或观看日志" if bili_available else "联动开关已开，但暂未检测到 B站 AI Bot 实例或日志",
            )

        if features.get("enable_reading_archive_integration"):
            archive_available = bool(getattr(self.plugin, "_reading_archive_available", lambda: False)())
            add(
                "ok" if archive_available else "info",
                "资料归档素材",
                "已检测到可用素材能力" if archive_available else "开关已开，但暂未检测到可用素材能力",
            )

        if features.get("enable_photo_text_action") and getattr(self.plugin, "enable_local_photo_load_guard", False):
            load_state = getattr(self.plugin, "_local_photo_generation_load_state", lambda: {})()
            if isinstance(load_state, dict):
                if load_state.get("available"):
                    add(
                        "warn" if load_state.get("busy") else "ok",
                        "本地生图负载保护",
                        str(load_state.get("reason") or "负载正常"),
                        warning_code="image.local_load_busy" if load_state.get("busy") else "",
                    )
                else:
                    add("info", "本地生图负载保护未采样", str(load_state.get("reason") or "无法读取系统负载"))

        if features.get("enable_photo_text_action") and getattr(self.plugin, "_external_photo_available", lambda: False)():
            model_checker = getattr(self.plugin, "_external_image_model_misconfiguration_note", None)
            model_note = model_checker() if callable(model_checker) else ""
            if model_note:
                add(
                    "error",
                    "在线图片模型配置错误",
                    model_note,
                    "把 EXTERNAL_IMAGE_API_MODEL 改成该平台的图片模型名，不要填聊天模型",
                )

        llm_perception_available = bool(getattr(self.plugin, "_llmperception_available", lambda: False)())
        if llm_perception_available:
            if features.get("enable_environment_perception"):
                add(
                    "warn",
                    "检测到 LLMPerception 插件",
                    "本插件已内置时间、节假日、农历节气和平台环境感知；两者同时启用会重复注入并增加 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_environment_perception",
                    "integration.environment_duplicate",
                )
            else:
                add("ok", "环境感知由外部插件接管", "检测到 LLMPerception，且本插件内置环境感知当前为关闭")

        if features.get("enable_creative_writing"):
            projects = self._creative_summary({"creative_projects": getattr(self.plugin, "data", {}).get("creative_projects", [])})
            active = projects.get("active_projects", 0)
            add(
                "ok" if active else "info",
                "私下创作行为",
                f"当前进行中创作 {active} 个" if active else "已开启；会在生活/梦境触发后慢慢开坑",
            )

        if getattr(self.plugin, "enable_group_companion", False):
            if group_mode == "whitelist" and not whitelist:
                add("warn", "群聊白名单为空", "白名单模式下所有群都会被拦截", "在配置页加入群号或切换为黑名单模式", "group.whitelist_empty")
            elif group_mode == "blacklist":
                add("ok", "群聊黑名单模式", f"已屏蔽 {len(blacklist)} 个群，其余群可观察")
            else:
                add("ok", "群聊白名单模式", f"允许 {len(whitelist)} 个群")

            if enabled_groups:
                add("ok", "已有群聊观测数据", f"已启用 {enabled_groups} 个群")
            else:
                add("info", "暂无群聊观测数据", "收到群消息后会逐步建立群内观察")
        else:
            add("info", "群聊陪伴未开启", "当前不会记录群聊上下文")

        if features.get("enable_group_interjection"):
            limit_getter = getattr(self.plugin, "_effective_group_interject_max_daily", None)
            limit = int(limit_getter() if callable(limit_getter) else getattr(self.plugin, "group_interject_max_daily", 0) or 0)
            if limit > 0:
                limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
                limit_text = limit_formatter(limit) if callable(limit_formatter) else str(limit)
                suffix = "" if limit_text == "不限" else " 次"
                add("ok", "群聊插话可用", f"每群每日上限 {limit_text}{suffix}")
            else:
                add("warn", "群聊插话开关已开但额度为 0", "功能不会真正触发", "在模块配置里调高每群每日插话上限", "group.interject_limit_zero")
        elif getattr(self.plugin, "enable_group_companion", False):
            add("info", "群聊以观察为主", "当前只积累群上下文，不主动插话")

        context_aware_installed = bool(getattr(self.plugin, "_context_aware_available", lambda: False)())
        if context_aware_installed:
            if features.get("enable_group_scene_awareness"):
                add(
                    "warn",
                    "检测到上下文场景感知增强插件",
                    "不会造成代码级冲突，但若两个插件同时注入群聊场景，会增加重复上下文和 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_group_scene_awareness",
                    "integration.context_aware_duplicate",
                )
            else:
                add("ok", "群聊场景感知由外部插件接管", "检测到 context_aware，且本插件对应内置功能当前为关闭")

        atrelay_installed = bool(getattr(self.plugin, "_atrelay_plugin_available", lambda: False)())
        if atrelay_installed:
            if features.get("enable_atrelay_tools"):
                add(
                    "warn",
                    "检测到艾特群友插件",
                    "本插件已内置跨群转述与 @ 群友工具；两者同时启用可能让模型看到重复工具",
                    "建议手动二选一；本插件不会再自动改写 enable_atrelay_tools",
                    "integration.atrelay_duplicate",
                )
            else:
                add("ok", "跨群转述由外部插件接管", "检测到 atrelay，且本插件对应内置工具当前为关闭")

        if not features.get("enable_group_privacy_guard"):
            add("warn", "群聊隐私保护未开启", "私聊记忆注入群聊时缺少额外防护", "建议打开 enable_group_privacy_guard", "group.privacy_guard_disabled")

        refresh_minutes = int(getattr(self.plugin, "memory_refresh_interval_minutes", 0) or 0)
        if refresh_minutes and refresh_minutes < 60:
            add("warn", "长期记忆整理过于频繁", f"当前 {refresh_minutes} 分钟，可能增加模型调用量", "建议设置为 120 分钟以上", "memory.refresh_too_frequent")

        if features.get("enable_personality_iteration_experiment"):
            suggestions = self._personality_iteration_suggestions(users, groups)
            if suggestions:
                for suggestion in suggestions:
                    dimension = self._single_line(suggestion.get("dimension"), 40)
                    dimension_code = hashlib.sha256(dimension.encode("utf-8")).hexdigest()[:12]
                    add(
                        self._single_line(suggestion.get("level"), 12) or "info",
                        f"角色贴合校准：{dimension}",
                        self._single_line(suggestion.get("text"), 260),
                        self._single_line(suggestion.get("action"), 160),
                        f"personality.{dimension_code}",
                    )
            else:
                add(
                    "ok",
                    "角色贴合校准",
                    "已启用理论检查，暂未从运行态观察到需要调整的角色贴合问题；该功能只帮助用户定位调整方向，不会自动修改 AstrBot 人格。",
                )

        return items

    def _token_stats_payload(self, usage: Any, balance_state: Any = None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        external_usage = usage.get("external") if isinstance(usage.get("external"), dict) else {}
        memory_plugin_usage = self._memory_plugin_token_usage_raw()
        together_plugin_usage = self._safe_together_plugin_token_usage_raw()
        totals = self._token_bucket(usage.get("totals"))
        by_provider = self._token_ranked_map(usage.get("by_provider"))
        by_task = self._token_ranked_map(usage.get("by_task"))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            providers = self._token_ranked_map(by_day_provider_raw.get(day_key))[:5]
            tasks = self._token_ranked_map(by_day_task_raw.get(day_key))[:6]
            by_day_detail.append({**item, "providers": providers, "tasks": tasks})
        by_hour = self._token_series_map(usage.get("by_hour"), limit=48)
        today_key = _today_key()
        today_bucket = usage.get("by_day", {}).get(today_key, {}) if isinstance(usage.get("by_day"), dict) else {}
        today_total_tokens = self._int(today_bucket.get("total_tokens")) if isinstance(today_bucket, dict) else 0
        exempt_by_day = usage.get("budget_exempt_by_day") if isinstance(usage.get("budget_exempt_by_day"), dict) else {}
        today_exempt_bucket = exempt_by_day.get(today_key, {}) if isinstance(exempt_by_day, dict) else {}
        today_exempt_tokens = self._int(today_exempt_bucket.get("total_tokens")) if isinstance(today_exempt_bucket, dict) else 0
        if today_exempt_tokens <= 0:
            today_tasks = by_day_task_raw.get(today_key, {}) if isinstance(by_day_task_raw, dict) else {}
            if isinstance(today_tasks, dict):
                is_exempt_task = getattr(self.plugin, "_is_llm_budget_exempt_task", None)
                today_exempt_tokens = sum(
                    self._int(bucket.get("total_tokens"))
                    for task, bucket in today_tasks.items()
                    if (
                        (
                            callable(is_exempt_task)
                            and is_exempt_task(task)
                        )
                        or (
                            not callable(is_exempt_task)
                            and str(task) in {"proactive_framework", "voice_framework"}
                        )
                    )
                    and isinstance(bucket, dict)
                )
        today_tokens = max(0, today_total_tokens - today_exempt_tokens)
        daily_limit = self._int(getattr(self.plugin, "daily_token_limit", 0))
        soft_limit = self._int(getattr(self.plugin, "daily_token_soft_limit", 0))
        soft_enabled = bool(getattr(self.plugin, "enable_daily_token_soft_limit", True))
        budget_skips = usage.get("budget_skips", {})
        today_skips = budget_skips.get(today_key, {}) if isinstance(budget_skips, dict) else {}
        budget = {
            "day": today_key,
            "limit": daily_limit,
            "soft_limit": soft_limit,
            "soft_enabled": soft_enabled,
            "soft_active": bool(soft_enabled and soft_limit > 0 and today_tokens >= soft_limit),
            "used": today_tokens,
            "total_used": today_total_tokens,
            "exempt_used": today_exempt_tokens,
            "remaining": max(0, daily_limit - today_tokens) if daily_limit > 0 else None,
            "soft_remaining": max(0, soft_limit - today_tokens) if soft_enabled and soft_limit > 0 else None,
            "ratio": round(today_tokens / daily_limit, 4) if daily_limit > 0 else 0,
            "soft_ratio": round(today_tokens / soft_limit, 4) if soft_enabled and soft_limit > 0 else 0,
            "exceeded": bool(daily_limit > 0 and today_tokens >= daily_limit),
            "deferred_calls": (
                self._int(today_skips.get("daily_token_soft_limit_deferred"))
                + self._int(today_skips.get("maintenance_token_saver_deferred"))
            )
            if isinstance(today_skips, dict)
            else 0,
            "skipped_calls": self._int(today_skips.get("count")) if isinstance(today_skips, dict) else 0,
        }
        recent_raw = usage.get("recent")
        recent = []
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent_total_tokens = self._int(item.get("total_tokens"))
                recent_estimated_tokens = self._int(item.get("estimated_tokens"))
                recent_reported_tokens = self._int(item.get("reported_tokens"), -1)
                if recent_reported_tokens < 0:
                    if recent_estimated_tokens <= 0 and bool(item.get("estimated", False)):
                        recent_estimated_tokens = recent_total_tokens
                    recent_reported_tokens = max(0, recent_total_tokens - recent_estimated_tokens)
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 40),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "reasoning_tokens": self._int(item.get("reasoning_tokens")),
                        "total_tokens": recent_total_tokens,
                        "reported_tokens": recent_reported_tokens,
                        "estimated_tokens": recent_estimated_tokens,
                        "usage_source": self._single_line(item.get("usage_source"), 20),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                        "budget_exempt": bool(item.get("budget_exempt", False)),
                        "request_max_attempts": self._int(item.get("request_max_attempts")),
                        "request_retry_source": self._single_line(item.get("request_retry_source"), 32),
                        "request_retry_supported": item.get("request_retry_supported") if type(item.get("request_retry_supported")) is bool else None,
                        "provider_attempts": None,
                        "retry_after": self._float(item.get("retry_after")),
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "by_provider": by_provider,
            "by_task": by_task,
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": by_hour,
            "budget": budget,
            "balance": self._balance_status_payload(balance_state),
            "recent": recent,
            "external": self._token_external_payload(external_usage),
            "memory_plugin": self._token_memory_plugin_payload(memory_plugin_usage),
            "together_plugin": self._token_memory_plugin_payload(together_plugin_usage),
        }
