# -*- coding: utf-8 -*-
"""admin_token。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 484 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiAdminTokenMixin:
    """admin_token（从 PrivateCompanionPageApi 拆出）。"""


    @staticmethod
    def _token_task_label(task: Any) -> str:
        normalized = str(task or "").strip()
        if not normalized:
            return "未分类模型调用"
        labels = {
            "daily_plan": "日程生成",
            "detail": "日程细化",
            "dream": "梦境内容",
            "diary": "日记整理",
            "diary_rewrite": "日记修订",
            "diary_derivatives": "日记线索提取",
            "memory_profile": "本地陪伴画像",
            "dialogue_episode": "私聊片段",
            "response_review": "回复/主动复核",
            "emotion_judgement": "情绪判断",
            "relationship": "关系分析",
            "group_interject": "群聊插话",
            "group_episode": "群聊片段",
            "group_slang": "黑话释义",
            "group_question_wakeup_reply_review": "群聊答疑复核",
            "group_followup_judge": "群聊续接判断",
            "worldbook_registration": "关系网自登记",
            "web_exploration_query": "探索选题",
            "web_exploration_digest": "探索笔记",
            "external_event_self_link": "外界信息关联",
            "news_digest": "新闻整理",
            "creative_project": "创作立项",
            "creative_outline": "创作大纲",
            "creative_writing": "文本创作",
            "creative_review": "创作审校",
            "creative_extract": "创作抽取",
            "photo_prompt": "生图提示",
            "screen_narration": "识屏转述",
            "forward_message": "合并转发转述",
            "forward_message_image_vision": "转发图片识别",
            "private_image_vision": "私聊图片识别",
            "group_image_vision": "群聊图片识别",
            "private_image_only_framework": "单图回复主链",
            "private_image_only_fallback": "单图兜底回复",
            "voice": "语音文本",
            "proactive_framework": "主动主回复",
            "proactive_persona_judge": "主动人格判定",
            "voice_framework": "框架语音",
            "voice_repair": "语音格式修复",
            "tts_conversion": "TTS 快速转换",
            "tts_spoken_conversion": "TTS 口语转换",
            "tts_postprocess": "TTS 后处理",
            "tts_visible_translation": "TTS 可见译文",
            "smart_message_debounce": "智能收口防抖",
            "smart_silence": "智能沉默判断",
            "group_air_reply_guard": "群聊插话把关",
            "group_nsfw_image_review": "群图安全审核",
            "rest_wakeup_judge": "休息醒来判断",
            "yesterday_summary": "昨日摘要",
            "full_test_detail": "完整测试细化",
            "provider_test": "模型测试",
            "qzone_comment": "空间评论",
            "qzone_comment_inbox_decision": "空间评论判断",
            "qzone_publish": "空间说说",
            "qzone_publish_test": "空间发布测试",
            "qzone_publish_sanitize": "空间文案清理",
            "qzone_publish_image_test_draft": "空间配图测试草稿",
            "qzone_emotional_vent": "空间情绪表达",
            "companion_manual_diagnosis": "陪伴答疑",
            "proactive_send_review": "主动发送复核",
            "atrelay_rewrite": "代答转写",
            "bookshelf_password": "资料柜密码生成",
            "bookshelf_password_reason": "资料柜密码缘由",
            "astrbot_private_reply": "非插件私聊主回复",
            "astrbot_group_reply": "非插件群聊主回复",
            "astrbot_reply": "非插件主回复",
            "other": "未分类模型调用",
        }
        if normalized in labels:
            return labels[normalized]
        if normalized.startswith("qzone_") and normalized.endswith("_photo_prompt"):
            return "空间配图提示"
        if normalized.startswith("qzone_"):
            return "QQ 空间任务"
        if normalized.startswith("astrbot_"):
            return "AstrBot 主回复"
        if normalized.startswith("private_image_"):
            return "私聊图片处理"
        if normalized.startswith("web_exploration_"):
            return "主动搜索"
        return normalized

    def _active_token_failures(
        self,
        recent: Any,
        *,
        max_age_seconds: float = 30 * 60,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        if not isinstance(recent, list):
            return []
        now = time.time()
        recovered: set[tuple[str, str]] = set()
        failures: list[dict[str, Any]] = []
        for item in recent[:limit]:
            if not isinstance(item, dict):
                continue
            task = self._single_line(item.get("task"), 40) or "LLM 调用"
            provider = self._single_line(item.get("provider"), 80) or "-"
            key = (task, provider)
            if bool(item.get("success", True)):
                recovered.add(key)
                continue
            ts = self._float(item.get("ts"))
            if ts > 0 and now - ts > max_age_seconds:
                continue
            if key in recovered:
                continue
            failures.append(item)
        return failures

    def _memory_plugin_token_usage_raw(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "_memory_companion_token_usage_summary", None)
        if not callable(getter):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "陪伴插件当前未接入记忆插件桥"}
        try:
            usage = getter()
        except Exception as exc:
            return {"available": False, "display_name": "我会牢牢记住你", "reason": self._single_line(exc, 160)}
        if not isinstance(usage, dict):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "记忆插件返回的 Token 统计格式无效"}
        usage.setdefault("available", True)
        usage.setdefault("display_name", "我会牢牢记住你")
        usage.setdefault("counted_in_private_companion_budget", False)
        return usage

    def _together_plugin_token_usage_raw(self) -> dict[str, Any]:
        target_plugin_name = "astrbot_plugin_together_companion"

        def static_namespace(module: Any) -> dict[str, Any]:
            if module is None:
                return {}
            try:
                namespace = object.__getattribute__(module, "__dict__")
            except (AttributeError, TypeError):
                return {}
            return namespace if isinstance(namespace, dict) else {}

        def is_target_module_name(value: Any) -> bool:
            name = str(value or "").strip()
            target_main = f"{target_plugin_name}.main"
            return name == target_main or name.endswith(f".{target_main}")

        modules: list[Any] = []
        seen_module_ids: set[int] = set()

        def append_module(module: Any) -> None:
            if module is None or id(module) in seen_module_ids:
                return
            seen_module_ids.add(id(module))
            modules.append(module)

        append_module(sys.modules.get("data.plugins.astrbot_plugin_together_companion.main"))
        append_module(sys.modules.get("astrbot_plugin_together_companion.main"))
        for loaded_name, module in tuple(sys.modules.items()):
            namespace = static_namespace(module)
            module_name = namespace.get("__name__", loaded_name)
            if (
                namespace.get("PLUGIN_NAME") == target_plugin_name
                or is_target_module_name(loaded_name)
                or is_target_module_name(module_name)
            ):
                append_module(module)
        for module in modules:
            # transformers 等懒加载模块会在 getattr() 时导入 torch/torchvision。
            # 集成发现只读取模块已注册的静态符号，不能触发任意第三方模块加载。
            getter = static_namespace(module).get("get_together_companion_bridge")
            if not callable(getter):
                continue
            try:
                bridge = getter()
                summary_getter = getattr(bridge, "get_token_usage_summary", None) if bridge is not None else None
                usage = summary_getter() if callable(summary_getter) else None
            except Exception as exc:
                return {
                    "available": False,
                    "installed": True,
                    "display_name": "我会和你在一起",
                    "reason": self._single_line(exc, 160),
                }
            if isinstance(usage, dict):
                usage.setdefault("available", True)
                usage.setdefault("installed", True)
                usage.setdefault("display_name", "我会和你在一起")
                usage.setdefault("counted_in_private_companion_budget", False)
                return usage
        return {
            "available": False,
            "installed": False,
            "display_name": "我会和你在一起",
            "reason": "未检测到运行中的一起插件",
        }

    def _safe_together_plugin_token_usage_raw(self) -> dict[str, Any]:
        try:
            return self._together_plugin_token_usage_raw()
        except Exception as exc:
            reason = self._single_line(exc, 160) or "联动状态读取失败"
            warning_key = f"{type(exc).__name__}:{reason}"
            if getattr(self, "_together_token_usage_warning_key", "") != warning_key:
                self._together_token_usage_warning_key = warning_key
                logger.warning(
                    "一起插件 Token 统计暂不可用，已跳过该可选来源: %s",
                    reason,
                )
            return {
                "available": False,
                "installed": False,
                "display_name": "我会和你在一起",
                "reason": "一起插件统计暂不可用，不影响陪伴面板其他功能",
            }

    def _token_external_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_session_raw = usage.get("by_day_session") if isinstance(usage.get("by_day_session"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:6],
                    "sessions": self._token_ranked_map(by_day_session_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 40),
                        "session": self._single_line(item.get("session"), 160),
                        "sender": self._single_line(item.get("sender"), 80),
                        "message_type": self._single_line(item.get("message_type"), 20),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "reasoning_tokens": self._int(item.get("reasoning_tokens")),
                        "total_tokens": self._int(item.get("total_tokens")),
                        "reported_tokens": self._int(item.get("reported_tokens")),
                        "estimated_tokens": self._int(item.get("estimated_tokens")),
                        "usage_source": self._single_line(item.get("usage_source"), 20),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                        "external": True,
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_session": self._token_ranked_map(usage.get("by_session")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    def _token_memory_plugin_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {"available": False}
        available = bool(usage.get("available", True))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
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
                        "task": self._single_line(item.get("task"), 60),
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
                    }
                )
        return {
            "available": available,
            "installed": bool(usage.get("installed", available)),
            "display_name": self._single_line(usage.get("display_name") or "我会牢牢记住你", 80),
            "plugin_name": self._single_line(usage.get("plugin_name") or "astrbot_plugin_memory_companion", 80),
            "reason": self._single_line(usage.get("reason"), 160),
            "note": self._single_line(
                usage.get("note") or "仅展示记忆插件自身模型调用；已确认 Token 来自 Provider 用量，估算 Token 只用于标记无用量返回或失败请求，不计入陪伴插件每日 Token 限额。",
                300,
            ),
            "counted_in_private_companion_budget": bool(usage.get("counted_in_private_companion_budget", False)),
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    @classmethod
    def _token_bucket(cls, value: Any) -> dict[str, Any]:
        bucket = value if isinstance(value, dict) else {}
        calls = cls._int(bucket.get("calls"))
        elapsed = cls._int(bucket.get("elapsed_ms"))
        total_tokens = cls._int(bucket.get("total_tokens"))
        estimated_tokens = cls._int(bucket.get("estimated_tokens"))
        reported_tokens = cls._int(bucket.get("reported_tokens"), -1)
        if reported_tokens < 0:
            reported_tokens = max(0, total_tokens - estimated_tokens)
        cached_tokens = cls._int(bucket.get("cached_tokens"))
        cache_read_tokens = cls._int(bucket.get("cache_read_tokens"))
        cache_write_tokens = cls._int(bucket.get("cache_write_tokens"))
        reasoning_tokens = cls._int(bucket.get("reasoning_tokens"))
        return {
            "calls": calls,
            "success": cls._int(bucket.get("success")),
            "errors": cls._int(bucket.get("errors")),
            "prompt_tokens": cls._int(bucket.get("prompt_tokens")),
            "completion_tokens": cls._int(bucket.get("completion_tokens")),
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "reported_tokens": reported_tokens,
            "cached_tokens": cached_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cached_ratio": round(cached_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "estimated_tokens": estimated_tokens,
            "estimated_ratio": round(estimated_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "reported_ratio": round(reported_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "estimated_calls": cls._int(bucket.get("estimated_calls")),
            "avg_tokens": round(total_tokens / calls, 1) if calls > 0 else 0,
            "avg_reported_tokens": round(reported_tokens / calls, 1) if calls > 0 else 0,
            "avg_latency_ms": round(elapsed / calls, 1) if calls > 0 else 0,
            "last_ts": cls._float(bucket.get("last_ts")),
        }

    @classmethod
    def _token_ranked_map(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 120)
            rows.append(item)
        rows.sort(key=lambda item: item.get("total_tokens", 0), reverse=True)
        return rows

    @classmethod
    def _token_series_map(cls, value: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 32)
            rows.append(item)
        rows.sort(key=lambda item: item.get("key", ""))
        return rows[-limit:]

    def _query_livingmemory_for_tokens(self, db_path: Path, token_bundle: dict[str, list[str]], limit: int) -> list[dict[str, Any]]:
        tokens = token_bundle.get("primary_tokens") or token_bundle.get("tokens", [])
        if not tokens:
            return []
        db_uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=1500")
            like_tokens = [self._sqlite_like_pattern(token) for token in tokens]
            table_limit = max(80, limit * 8)
            items: list[dict[str, Any]] = []

            def where_for(columns: list[str]) -> tuple[str, list[str]]:
                parts: list[str] = []
                params: list[str] = []
                for pattern in like_tokens:
                    sub = []
                    for column in columns:
                        sub.append(f"{column} LIKE ? ESCAPE '\\'")
                        params.append(pattern)
                    parts.append("(" + " OR ".join(sub) + ")")
                return " OR ".join(parts), params

            if self._sqlite_table_exists(conn, "documents"):
                where, params = where_for(["text", "metadata"])
                for row in conn.execute(
                    f"SELECT id, doc_id, text, metadata, created_at, updated_at FROM documents WHERE {where} ORDER BY id DESC LIMIT ?",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_document(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "memory_atoms"):
                where, params = where_for(["content", "entities", "metadata"])
                for row in conn.execute(
                    f"""SELECT id, parent_memory_id, atom_type, content, entities, importance, confidence,
                              created_at, last_accessed_at, session_id, persona_id, metadata
                         FROM memory_atoms
                        WHERE (status IS NULL OR status != 'expired') AND ({where})
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_atom(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "graph_entries"):
                where, params = where_for(["content", "metadata", "session_id"])
                for row in conn.execute(
                    f"""SELECT id, entry_key, source_memory_id, session_id, persona_id, entry_type,
                              relation_type, content, metadata, created_at, updated_at
                         FROM graph_entries
                        WHERE {where}
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_graph_entry(row, token_bundle)
                    if item:
                        items.append(item)

            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, Any]] = []
            for item in sorted(items, key=lambda entry: (entry.get("score") or 0, entry.get("last_access_time") or entry.get("create_time") or 0), reverse=True):
                key = (str(item.get("source") or ""), str(item.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
                if len(unique) >= limit:
                    break
            return unique
        finally:
            conn.close()
