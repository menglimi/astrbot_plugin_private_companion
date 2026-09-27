# -*- coding: utf-8 -*-
"""诊断 / 故障排查 / 统计 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（45 个方法 / 3066 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import functools
import time
import re
import hashlib
import secrets
import uuid
from copy import copy, deepcopy
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .page_api_diagnostics_model import PrivateCompanionPageApiDiagnosticsModelMixin
from .page_api_diagnostics_checks import PrivateCompanionPageApiDiagnosticsChecksMixin
from .page_api_diagnostics_overview import PrivateCompanionPageApiDiagnosticsOverviewMixin
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_section,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .persona_config import runtime_persona_setting
from .planning import evaluate_daily_plan_quality, generate_daily_plan, generate_detail_enhancement
from .logging_util import get_module_logger


logger = get_module_logger(__name__)



class PrivateCompanionPageApiDiagnosticsMixin(PrivateCompanionPageApiDiagnosticsOverviewMixin, PrivateCompanionPageApiDiagnosticsChecksMixin, PrivateCompanionPageApiDiagnosticsModelMixin):
    """诊断 / 故障排查 / 统计 域（从 PrivateCompanionPageApi 拆出）。"""

    def _build_troubleshooting_proactive_summary(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve relationship snapshots once before rendering diagnostics."""

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        resolved_users: dict[str, Any] = {}
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                resolved_users[user_id] = raw_user
                continue
            user = dict(raw_user)
            try:
                snapshot_getter = getattr(
                    self.plugin,
                    "_req041_relationship_snapshot_view",
                    None,
                )
                snapshot = (
                    snapshot_getter(user, source="troubleshooting_summary")
                    if callable(snapshot_getter)
                    else user
                )
                if isinstance(snapshot, dict):
                    user = dict(snapshot)
            except Exception:
                user = dict(raw_user)
            user["_req041_relationship_snapshot_resolved"] = True
            resolved_users[user_id] = user
        scoped = dict(data)
        scoped["users"] = resolved_users
        return self._proactive_task_summary(scoped)
    def _troubleshooting_proactive_wakeup_tasks(self) -> dict[str, asyncio.Task[Any]]:
        tasks = getattr(self.plugin, "_troubleshooting_proactive_wakeup_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self.plugin._troubleshooting_proactive_wakeup_tasks = tasks
        return tasks
    def _cancel_troubleshooting_proactive_wakeup(self, user_id: str) -> bool:
        tasks = self._troubleshooting_proactive_wakeup_tasks()
        task = tasks.pop(str(user_id or ""), None)
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True
    def _schedule_troubleshooting_proactive_wakeup(
        self,
        user_id: str,
        scheduled_ts: float,
    ) -> asyncio.Task[Any] | None:
        user_key = str(user_id or "").strip()
        kicker = getattr(self.plugin, "_kick_proactive_loop_once", None)
        if not user_key or not callable(kicker):
            return None
        tasks = self._troubleshooting_proactive_wakeup_tasks()
        existing = tasks.get(user_key)
        if isinstance(existing, asyncio.Task) and not existing.done():
            return existing

        async def wake_when_due() -> None:
            try:
                await asyncio.sleep(max(0.0, float(scheduled_ts) - time.time()))
                await kicker()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "主动消息链路测试到点唤醒失败: user=%s error=%s",
                    self._single_line(user_key, 80),
                    self._single_line(exc, 160),
                )
            finally:
                current = tasks.get(user_key)
                if current is asyncio.current_task():
                    tasks.pop(user_key, None)

        task = self._create_page_background_task(
            wake_when_due(),
            label=f"troubleshooting_proactive_{user_key[:40]}",
        )
        if task is None:
            return None
        tasks[user_key] = task
        return task
    def _troubleshooting_warning_type(self, scope: str, *parts: Any) -> str:
        source = "\x1f".join(
            self._single_line(part, 180).strip().lower()
            for part in (scope, *parts)
            if self._single_line(part, 180).strip()
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        return f"warning:{digest}"
    def _troubleshooting_semantic_warning_type(self, code: Any) -> str:
        normalized = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(code, 120).lower()).strip("_.:-")
        return self._troubleshooting_warning_type("semantic", normalized or "unknown")
    def _troubleshooting_proactive_warning_code(self, kind: str, item: dict[str, Any], note: str) -> str:
        text = " ".join(
            self._single_line(value, 180).lower()
            for value in (item.get("reason"), item.get("source"), item.get("action"), note)
            if value
        )
        categories = (
            ("timeout", ("超时", "timeout", "timed out")),
            ("provider", ("provider", "模型", "不可用", "api", "鉴权", "401", "403")),
            ("send", ("发送失败", "投递失败", "send", "发送异常")),
            ("storage", ("保存失败", "写入失败", "database", "sqlite", "locked", "存储")),
            ("media", ("生图", "图片", "photo", "image", "参考图", "下载")),
            ("voice", ("tts", "语音", "音频")),
            ("tool", ("工具", "tool", "调用失败")),
        )
        category = next((name for name, tokens in categories if any(token in text for token in tokens)), "other")
        action = re.sub(r"[^a-z0-9_]+", "_", self._single_line(item.get("action"), 40).lower()).strip("_") or "message"
        return f"proactive.{kind}.{category}.{action}"
    def _troubleshooting_chain_warning_code(self, test_type: str, text: Any) -> str:
        warning = self._single_line(text, 360).lower()
        categories = (
            ("timeout_budget", ("测试外层最多等待", "测试层截断")),
            ("fallback_delay", ("备选在线图片 api", "回退链路")),
            ("sdgen_timeout", ("sdgen",)),
            ("auto_backend", ("当前为自动后端",)),
            ("reference_missing", ("没有解析到可用本地参考图",)),
            ("reference_scope", ("参考图", "文生图")),
            ("serial_queue", ("全局串行锁", "先排队")),
            ("gateway_buffer", ("cloudflare", "网关", "缓冲")),
            ("test_scope", ("只检查生成文件", "不覆盖后续")),
        )
        category = next((name for name, tokens in categories if any(token in warning for token in tokens)), "other")
        normalized_test = re.sub(r"[^a-z0-9_]+", "_", self._single_line(test_type, 60).lower()).strip("_") or "chain"
        return f"chain.{normalized_test}.{category}"
    def _troubleshooting_chain_tests_with_warning_items(
        self,
        results: dict[str, Any],
        suppressed_keys: set[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        decorated = deepcopy(results) if isinstance(results, dict) else {}
        all_items: list[dict[str, Any]] = []
        for test_type, raw_result in decorated.items():
            if not isinstance(raw_result, dict):
                continue
            warning_items: list[dict[str, Any]] = []
            for warning in raw_result.get("warnings", []) if isinstance(raw_result.get("warnings"), list) else []:
                text = self._single_line(warning, 360)
                if not text:
                    continue
                code = self._troubleshooting_chain_warning_code(str(test_type), text)
                warning_items.append(
                    {
                        "level": "warn",
                        "title": self._single_line(text, 90),
                        "text": text,
                        "source": "链路测试",
                        "warning_code": code,
                        "warning_type": self._troubleshooting_semantic_warning_type(code),
                    }
                )
            all_items.extend(warning_items)
            visible_items = self._filter_suppressed_troubleshooting_warnings(warning_items, suppressed_keys)
            raw_result["warning_items"] = visible_items
            raw_result["warnings"] = [item["text"] for item in visible_items]
        return decorated, all_items
    def _troubleshooting_legacy_warning_code(self, title: Any) -> str:
        normalized = self._single_line(title, 90)
        aliases = {
            "TTS 配置已开但 provider 不可用": "tts.provider_unavailable",
            "TTS 强化已开但会话 TTS 未启用": "tts.provider_unavailable",
            "TTS 强化开启但合成 provider 不可用": "tts.provider_unavailable",
            "暂无启用的私聊对象": "proactive.no_enabled_users",
            "主动消息没有私聊对象": "proactive.no_enabled_users",
            "私聊主动已关闭": "proactive.daily_limit_zero",
            "私聊主动总额度为 0": "proactive.daily_limit_zero",
            "Token 软限额正在暂缓后台任务": "token.soft_limit_active",
            "每日 Token 软限额已接管": "token.soft_limit_active",
            "SQLite 并发状态需要关注": "sqlite.wal",
            "主动循环心跳不新鲜": "proactive.loop_stale",
            "私聊图片识别调度状态读取失败": "vision.runtime_unreadable",
            "私聊图片识别暂无可用模型": "vision.no_available_provider",
            "有识图模型被临时降权": "vision.provider_cooldown",
            "配置诊断仍有待处理项": "diagnostic.pending",
        }
        return aliases.get(normalized, "")
    def _troubleshooting_warning_records(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        raw = data.get("troubleshooting_suppressed_warning_types") if isinstance(data, dict) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_raw: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            record = item if isinstance(item, dict) else {"key": item}
            raw_key = self._single_line(record.get("key"), 64)
            if raw_key and raw_key in seen_raw:
                continue
            if raw_key:
                seen_raw.add(raw_key)
            code = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(record.get("code"), 120).lower()).strip("_.:-")
            if not code:
                code = self._troubleshooting_legacy_warning_code(record.get("title"))
            key = self._troubleshooting_semantic_warning_type(code) if code else raw_key
            if not re.fullmatch(r"warning:[0-9a-f]{20}", key) or key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "key": key,
                    "title": self._single_line(record.get("title"), 90) or "未命名警告类型",
                    "source": self._single_line(record.get("source"), 40) or "排障检查",
                    "suppressed_at": self._float(record.get("suppressed_at")),
                    "code": code,
                }
            )
        return records[:120]
    def _troubleshooting_diagnostics_with_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decorated: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            code = self._single_line(item.get("warning_code"), 120)
            item["warning_type"] = self._single_line(item.get("warning_type"), 64) or (
                self._troubleshooting_semantic_warning_type(code)
                if code
                else self._troubleshooting_warning_type("diagnostic", item.get("title"))
            )
            decorated.append(item)
        return decorated
    def _filter_suppressed_troubleshooting_warnings(
        self,
        items: list[dict[str, Any]],
        suppressed_keys: set[str],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in items
            if not (
                self._single_line(item.get("level"), 12) == "warn"
                and self._single_line(item.get("warning_type"), 64) in suppressed_keys
            )
        ]
    def _troubleshooting_suppression_payload(
        self,
        records: list[dict[str, Any]],
        *item_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        active_counts: dict[str, int] = {}
        for items in item_groups:
            for item in items:
                if not isinstance(item, dict) or self._single_line(item.get("level"), 12) != "warn":
                    continue
                key = self._single_line(item.get("warning_type"), 64)
                if key:
                    active_counts[key] = active_counts.get(key, 0) + 1
        return [{**record, "current_count": active_counts.get(record["key"], 0)} for record in records]
    async def update_troubleshooting_warning_suppression(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        action = self._single_line(payload.get("action"), 24).lower()
        if action not in {"suppress", "restore", "restore_all"}:
            return self._error("action 只能是 suppress、restore 或 restore_all")
        key = self._single_line(payload.get("key"), 64)
        code = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(payload.get("code"), 120).lower()).strip("_.:-")
        if action == "suppress" and code:
            key = self._troubleshooting_semantic_warning_type(code)
        if action != "restore_all" and not re.fullmatch(r"warning:[0-9a-f]{20}", key):
            return self._error("无效的警告类型")
        try:
            async with self.plugin._data_lock:
                records = self._troubleshooting_warning_records(self.plugin.data)
                previous = list(records)
                if action == "suppress":
                    record = {
                        "key": key,
                        "title": self._single_line(payload.get("title"), 90) or "未命名警告类型",
                        "source": self._single_line(payload.get("source"), 40) or "排障检查",
                        "suppressed_at": time.time(),
                        "code": code,
                    }
                    records = [item for item in records if item.get("key") != key]
                    records.append(record)
                    records = records[-120:]
                elif action == "restore":
                    records = [item for item in records if item.get("key") != key]
                else:
                    records = []
                changed = records != previous
                self.plugin.data["troubleshooting_suppressed_warning_types"] = records
                if changed:
                    self.plugin._save_data_sync(sections={"troubleshooting_suppressed_warning_types"})
            return self._ok(
                {
                    "items": records,
                    "count": len(records),
                    "changed": changed,
                    "message": "已屏蔽此类警告" if action == "suppress" else ("已恢复全部警告类型" if action == "restore_all" else "已恢复此类警告"),
                }
            )
        except Exception as exc:
            logger.error("更新排障警告屏蔽失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error(str(exc))
    async def get_diagnostics(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                raw_users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                raw_groups = self.plugin.data.get("groups") if isinstance(self.plugin.data.get("groups"), dict) else {}
                users = {str(key): dict(value) for key, value in raw_users.items() if isinstance(value, dict)}
                groups = {str(key): dict(value) for key, value in raw_groups.items() if isinstance(value, dict)}
                suppression_records = self._troubleshooting_warning_records(self.plugin.data)
            tune_result = await self._maybe_apply_personality_iteration_auto_tune(users, groups)
            items = self._build_diagnostics(users, groups)
            tune_item = self._personality_auto_tune_diagnostic_item(tune_result)
            if tune_item:
                items.append(tune_item)
            items = self._troubleshooting_diagnostics_with_types(items)
            visible_items = self._filter_suppressed_troubleshooting_warnings(
                items,
                {record["key"] for record in suppression_records},
            )
            extension_status_getter = getattr(
                getattr(self.plugin, "extension_api", None),
                "extension_control_plane_status",
                None,
            )
            if callable(extension_status_getter):
                try:
                    extension_status = extension_status_getter()
                except Exception as exc:
                    logger.warning("扩展控制面自检失败: %s", self._single_line(exc, 160))
                    extension_status = {"issues": ["control_plane_self_check_failed"]}
            else:
                extension_status = {"issues": ["control_plane_unavailable"]}
            return self._ok(
                {
                    "items": visible_items,
                    "suppressed_warning_types": self._troubleshooting_suppression_payload(suppression_records, items),
                    "extension_control_plane": extension_status,
                }
            )
        except Exception as exc:
            logger.error(f"获取诊断失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_troubleshooting(self) -> dict[str, Any]:
        try:
            await self._recover_stale_troubleshooting_proactive_test()
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
                default_data = deepcopy(getattr(self.plugin, "_data_default", {}) or {})
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            tune_result = await self._maybe_apply_personality_iteration_auto_tune(users, groups)
            suppression_records = self._troubleshooting_warning_records(data)
            suppressed_keys = {record["key"] for record in suppression_records}
            all_diagnostics = self._build_diagnostics(users, groups)
            tune_item = self._personality_auto_tune_diagnostic_item(tune_result)
            if tune_item:
                all_diagnostics.append(tune_item)
            all_diagnostics = self._troubleshooting_diagnostics_with_types(all_diagnostics)
            diagnostics = self._filter_suppressed_troubleshooting_warnings(all_diagnostics, suppressed_keys)
            proactive_tasks = await self._proactive_task_summary_async(data)
            proactive_candidates = self._proactive_candidate_summary(data)
            token_stats = self._token_stats_payload(data.get("token_usage", {}))
            cache = self._cache_summary(data)
            tts = self._tts_runtime_summary(users)
            sqlite_status = await self._sqlite_wal_status_summary()
            all_sqlite_items: list[dict[str, Any]] = []
            for raw_item in sqlite_status.get("items", []) if isinstance(sqlite_status.get("items"), list) else []:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                if self._single_line(item.get("level"), 12) == "warn":
                    item["warning_code"] = "sqlite.wal"
                    item["warning_type"] = self._troubleshooting_semantic_warning_type("sqlite.wal")
                all_sqlite_items.append(item)
            sqlite_status = {**sqlite_status, "items": all_sqlite_items}
            passive_no_reply = self._passive_no_reply_summary(data)
            routing_root = default_data.get("persona_routing_warnings")
            persona_routing_warnings = (
                routing_root.get("items", [])
                if isinstance(routing_root, dict) and isinstance(routing_root.get("items"), list)
                else []
            )
            persona_routing_warnings = self._active_persona_routing_warnings(
                persona_routing_warnings
            )
            screen_companion = self._screen_companion_summary(data)
            qzone = self._qzone_summary(data)
            all_recent_events = self._troubleshooting_recent_events(
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                passive_no_reply=passive_no_reply,
                persona_routing_warnings=persona_routing_warnings,
            )
            all_checks = self._troubleshooting_checks(
                data=data,
                users=users,
                groups=groups,
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                cache=cache,
                tts=tts,
                sqlite_status=sqlite_status,
            )
            recent_events = self._filter_suppressed_troubleshooting_warnings(all_recent_events, suppressed_keys)
            checks = self._filter_suppressed_troubleshooting_warnings(all_checks, suppressed_keys)
            visible_sqlite_items = self._filter_suppressed_troubleshooting_warnings(all_sqlite_items, suppressed_keys)
            visible_sqlite_status = {**sqlite_status, "items": visible_sqlite_items}
            chain_tests, all_chain_warning_items = self._troubleshooting_chain_tests_with_warning_items(
                self._troubleshooting_test_results(data),
                suppressed_keys,
            )
            suppression_payload = self._troubleshooting_suppression_payload(
                suppression_records,
                all_diagnostics,
                all_recent_events,
                all_checks,
                all_sqlite_items,
                all_chain_warning_items,
            )
            active_suppressed_count = sum(self._int(item.get("current_count")) for item in suppression_payload)
            counts = {
                "error": sum(1 for item in recent_events if item.get("level") == "error") + sum(1 for item in checks if item.get("level") == "error"),
                "warn": sum(1 for item in recent_events if item.get("level") == "warn") + sum(1 for item in checks if item.get("level") == "warn"),
                "info": sum(1 for item in recent_events if item.get("level") == "info") + sum(1 for item in checks if item.get("level") == "info"),
                "ok": sum(1 for item in checks if item.get("level") == "ok"),
            }
            headline_level = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
            headline = "发现需要处理的异常" if headline_level == "error" else (
                "有可关注项"
                if headline_level == "warn"
                else ("未发现未屏蔽异常" if active_suppressed_count else "运行状态正常")
            )
            return self._ok(
                {
                    "summary": {
                        "level": headline_level,
                        "headline": headline,
                        "counts": counts,
                        "suppressed_count": active_suppressed_count,
                        "suppressed_types": len(suppression_payload),
                        "generated_at": self.plugin._format_timestamp_elapsed(time.time()),
                    },
                    "recent_events": recent_events[:80],
                    "checks": checks,
                    "diagnostics": diagnostics,
                    "sqlite": visible_sqlite_status,
                    "chain_tests": chain_tests,
                    "image_api_endpoints": self._troubleshooting_image_api_endpoints(),
                    "recent_photo_generations": self._recent_photo_generation_summary(data),
                    "passive_no_reply": passive_no_reply,
                    "prompt_injections": self._prompt_injection_summary(data),
                    "screen_companion": screen_companion,
                    "qzone": qzone,
                    "proactive_intensity": self._proactive_intensity_summary(),
                    "proactive_runtime": proactive_tasks.get("runtime", {}),
                    "token_budget": token_stats.get("budget", {}),
                    "cache": cache,
                    "tts": tts,
                    "suppressed_warning_types": suppression_payload,
                }
            )
        except Exception as exc:
            logger.error(f"获取排障信息失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def _recover_stale_troubleshooting_proactive_test(self, *, max_age_seconds: int = 120) -> int:
        recovered = 0
        now = time.time()
        async with self.plugin._data_lock:
            users = self.plugin.data.get("users")
            if not isinstance(users, dict):
                return 0
            for user_id, user in list(users.items()):
                if not isinstance(user, dict):
                    continue
                if str(user.get("planned_proactive_source") or "") != "troubleshooting":
                    continue
                if not isinstance(user.get("troubleshooting_proactive_restore"), dict):
                    continue
                started = self._float(user.get("troubleshooting_proactive_started_at"))
                if started <= 0 or now - started <= max_age_seconds:
                    continue
                self.plugin._append_troubleshooting_proactive_step(
                    user,
                    "等待结果",
                    "error",
                    f"超过 {max_age_seconds} 秒仍未完成，已停止等待并恢复原主动计划",
                )
                self.plugin._record_troubleshooting_proactive_result(
                    str(user_id),
                    user,
                    ok=False,
                    detail="主动消息测试等待超时，已恢复原主动计划",
                    error=f"超过 {max_age_seconds} 秒仍未完成；请确认主动循环是否启动、目标用户是否有私聊会话、发送端是否可用",
                    action=str(user.get("planned_proactive_action") or "message"),
                    reason=str(user.get("planned_proactive_reason") or "check_in"),
                )
                user["proactive_sending"] = False
                user["proactive_sending_started_at"] = 0
                self.plugin._restore_troubleshooting_proactive_plan(user)
                self._cancel_troubleshooting_proactive_wakeup(str(user_id))
                recovered += 1
            if recovered:
                self.plugin._save_data_sync(
                    sections={"users", "troubleshooting_test_results"}
                )
        return recovered
    def _safe_test_diagnostic_text(self, value: Any, limit: int = 1200) -> str:
        if value in (None, ""):
            return ""
        try:
            cleaned = _redact_outbound_secrets(str(value), getattr(self, "plugin", None))
        except Exception:
            return "测试详情脱敏失败，请根据测试编号查看 AstrBot 后端日志"
        return self._multi_line(cleaned, max(80, limit))
    def _finalize_test_diagnostics(
        self,
        test_type: str,
        result: dict[str, Any] | None,
        started_at: float,
        *,
        title: str = "",
        finished_at: float | None = None,
    ) -> dict[str, Any]:
        item = dict(result or {})
        ended_at = float(finished_at or time.time())
        elapsed_ms = self._int(item.get("elapsed_ms")) or max(0, int((ended_at - started_at) * 1000))
        resolved_title = self._single_line(item.get("title"), 80) or self._single_line(title, 80) or self._troubleshooting_test_title(test_type)
        item["title"] = resolved_title
        for key, limit in (("error", 1600), ("delivery_error", 1200), ("detail", 1200), ("diagnostic_detail", 4000)):
            if item.get(key):
                item[key] = self._safe_test_diagnostic_text(item.get(key), limit)
        item["warnings"] = [
            message
            for warning in (item.get("warnings") if isinstance(item.get("warnings"), list) else [])[:8]
            if (message := self._safe_test_diagnostic_text(warning, 800))
        ]
        if item.get("exception_type"):
            item["exception_type"] = self._single_line(item.get("exception_type"), 120)

        normalized_steps: list[dict[str, Any]] = []
        status_aliases = {"success": "ok", "passed": "ok", "failed": "error", "warning": "warn", "pending": "info"}
        raw_steps = item.get("steps") if isinstance(item.get("steps"), list) else []
        for raw_step in raw_steps[:24]:
            if not isinstance(raw_step, dict):
                continue
            step_status = self._single_line(raw_step.get("status"), 16).lower() or "info"
            step_status = status_aliases.get(step_status, step_status)
            if step_status not in {"ok", "error", "warn", "info"}:
                step_status = "info"
            normalized_steps.append(
                {
                    "name": self._single_line(raw_step.get("name"), 60) or "执行阶段",
                    "status": step_status,
                    "detail": self._safe_test_diagnostic_text(raw_step.get("detail"), 800),
                    "elapsed_ms": self._int(raw_step.get("elapsed_ms")),
                }
            )
        if not normalized_steps:
            summary = item.get("error") or item.get("detail") or ("测试已通过" if item.get("ok") else "测试未通过")
            normalized_steps.append(
                {
                    "name": "执行测试",
                    "status": "info" if item.get("pending") or item.get("unsupported") else ("ok" if item.get("ok") else "error"),
                    "detail": self._safe_test_diagnostic_text(summary, 800),
                    "elapsed_ms": elapsed_ms,
                }
            )
        item["steps"] = normalized_steps

        failure = self._classify_test_failure(test_type, item)
        item.update(failure)
        item["diagnostic_version"] = 1
        request_id = self._single_line(item.get("request_id") or item.get("trace_id"), 32) or uuid.uuid4().hex[:12]
        item["request_id"] = request_id
        item["trace_id"] = request_id
        supplied_status = self._single_line(item.get("test_status"), 16).lower()
        item["test_status"] = supplied_status if supplied_status in {"unsupported", "skipped"} else (
            "pending" if item.get("pending") else ("passed" if item.get("ok") else "failed")
        )
        item["started_at"] = float(started_at)
        item["finished_at"] = ended_at
        item["elapsed_ms"] = elapsed_ms

        entries: list[dict[str, Any]] = [
            {
                "elapsed_ms": 0,
                "level": "info",
                "stage": "开始",
                "message": f"开始执行{resolved_title}",
            }
        ]
        for warning in (item.get("warnings") if isinstance(item.get("warnings"), list) else [])[:8]:
            message = self._safe_test_diagnostic_text(warning, 800)
            if message:
                entries.append({"elapsed_ms": 0, "level": "warn", "stage": "范围说明", "message": message})
        for step in normalized_steps:
            entries.append(
                {
                    "elapsed_ms": self._int(step.get("elapsed_ms")),
                    "level": step.get("status") or "info",
                    "stage": step.get("name") or "执行阶段",
                    "message": step.get("detail") or "",
                }
            )
        final_message = item.get("error") or item.get("detail") or (
            "测试仍在等待异步任务完成" if item.get("pending") else "测试完成"
        )
        entries.append(
            {
                "elapsed_ms": elapsed_ms,
                "level": "info" if item.get("pending") or item.get("unsupported") else ("ok" if item.get("ok") else "error"),
                "stage": "结果",
                "message": self._safe_test_diagnostic_text(final_message, 1200),
            }
        )
        item["diagnostic_entries"] = entries[:32]
        return item
    async def run_troubleshooting_test(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        test_type = self._single_line(payload.get("type"), 40)
        request_id = secrets.token_hex(6)
        payload["_test_request_id"] = request_id
        result_key = test_type
        start = time.time()
        logger.info(
            "[test:%s][type:%s] 开始执行测试",
            request_id,
            test_type or "unknown",
        )
        try:
            if test_type == "proactive_message":
                await self._recover_stale_troubleshooting_proactive_test(max_age_seconds=120)
            if test_type in {"image_generation", "image_generation_text2img", "image_generation_selfie"}:
                image_payload = dict(payload)
                if test_type == "image_generation_text2img":
                    image_payload["workflow_kind"] = "text2img"
                elif test_type == "image_generation_selfie":
                    image_payload["workflow_kind"] = "selfie"
                result = await self._run_image_generation_chain_test(image_payload)
            elif test_type == "image_api_endpoint":
                result = await self._run_image_api_endpoint_test(payload)
                result_key = self._single_line(result.get("test_key"), 80) or test_type
            elif test_type == "tts_generation":
                result = await self._run_tts_generation_chain_test(payload)
            elif test_type == "screen_peek":
                result = await self._run_screen_peek_chain_test(payload)
            elif test_type == "qzone_integration":
                result = await self._run_qzone_chain_test(payload)
            elif test_type == "proactive_message":
                result = await self._run_proactive_message_chain_test(payload)
            elif test_type in {"skill_similarity", "model_diagnostics"}:
                result = await self._run_model_diagnostics_check(payload)
            elif test_type in {"weather_api", "balance_api", "web_search"}:
                result = await self._run_external_api_test(test_type, payload)
            else:
                return self._error("未知排障测试类型")
        except Exception as exc:
            if test_type in {"weather_api", "balance_api", "web_search"}:
                raw_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
                safe_error = self._redact_external_api_test_text(
                    exc,
                    tester=self.plugin,
                    settings=raw_settings,
                    limit=1600,
                )
                logger.warning(
                    "外部接口排障测试失败: type=%s err=%s",
                    test_type,
                    self._single_line(safe_error, 160),
                )
            else:
                safe_error = self._safe_test_diagnostic_text(exc, 1600)
                logger.warning(
                    "排障链路测试失败: %s",
                    self._single_line(exc, 160),
                    exc_info=True,
                )
            result = {
                "type": test_type,
                "ok": False,
                "title": self._troubleshooting_test_title(test_type),
                "error": safe_error,
                "exception_type": exc.__class__.__name__,
            }
            if test_type in {"image_generation", "image_generation_text2img", "image_generation_selfie"}:
                result.update(self._image_generation_called_plugin_diagnostics())
        result["type"] = test_type
        result["elapsed_ms"] = self._int(result.get("elapsed_ms")) or int((time.time() - start) * 1000)
        result["ran_at"] = time.time()
        result["ran_at_text"] = self.plugin._format_timestamp_elapsed(result["ran_at"])
        result.setdefault("request_id", request_id)
        result = self._finalize_test_diagnostics(test_type, result, start, finished_at=result["ran_at"])
        result = self._diagnostic_envelope(
            result,
            test_type=test_type,
            duration_ms=self._int(result.get("elapsed_ms")),
            test_id=diagnostic_test_id(test_type),
        )
        await self._remember_troubleshooting_test_result(result_key, result)
        logger.info(
            "[test:%s][type:%s] 测试结束: status=%s elapsed_ms=%s",
            result.get("request_id"),
            test_type,
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)
    async def _remember_troubleshooting_test_result(self, test_type: str, result: dict[str, Any]) -> None:
        if not test_type:
            return
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
                if not isinstance(raw, dict):
                    raw = {}
                    self.plugin.data["troubleshooting_test_results"] = raw
                raw[test_type] = self._sanitize_troubleshooting_test_result(result)
                if test_type.startswith("image_api_endpoint_"):
                    endpoint_results = sorted(
                        (
                            (key, value)
                            for key, value in raw.items()
                            if str(key).startswith("image_api_endpoint_") and isinstance(value, dict)
                        ),
                        key=lambda item: self._float(item[1].get("ran_at")),
                        reverse=True,
                    )
                    for stale_key, _ in endpoint_results[24:]:
                        raw.pop(stale_key, None)
                self.plugin._save_data_sync(sections={"troubleshooting_test_results"})
        except Exception as exc:
            logger.warning("保存排障测试结果失败: %s", self._single_line(exc, 120))
    def _troubleshooting_test_results(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("troubleshooting_test_results")
        if not isinstance(raw, dict):
            return {}
        results: dict[str, Any] = {}
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        active_proactive_test = any(
            isinstance(user, dict)
            and str(user.get("planned_proactive_source") or "") == "troubleshooting"
            and isinstance(user.get("troubleshooting_proactive_restore"), dict)
            for user in users.values()
        )
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = self._sanitize_troubleshooting_test_result(value)
            if key == "proactive_message" and item.get("pending") and not active_proactive_test:
                item.update(
                    {
                        "ok": False,
                        "pending": False,
                        "error": item.get("error") or "主动消息测试任务状态已丢失，请重新测试",
                        "detail": item.get("detail") or "没有找到正在等待执行的临时主动任务",
                        "ran_at": time.time(),
                        "ran_at_text": self.plugin._format_timestamp_elapsed(time.time()),
                    }
                )
            finished_at = self._float(item.get("finished_at")) or self._float(item.get("ran_at")) or time.time()
            elapsed_seconds = max(0.0, self._int(item.get("elapsed_ms")) / 1000.0)
            started_at = self._float(item.get("started_at")) or max(0.0, finished_at - elapsed_seconds)
            if not item.get("request_id") and not item.get("trace_id"):
                legacy_seed = f"{key}:{finished_at:.6f}:{item.get('title') or ''}"
                item["request_id"] = hashlib.sha256(legacy_seed.encode("utf-8")).hexdigest()[:12]
            item = self._finalize_test_diagnostics(
                str(key),
                item,
                started_at,
                finished_at=finished_at,
            )
            results[key] = item
        return results
    def _sanitize_troubleshooting_test_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self._diagnostic_envelope(result)
    @staticmethod
    def _troubleshooting_test_title(test_type: str) -> str:
        return {
            "image_generation": "图片生成链路测试",
            "image_generation_text2img": "文生图链路测试",
            "image_generation_selfie": "自拍参考图链路测试",
            "image_api_endpoint": "在线图片 API 单独测试",
            "tts_generation": "TTS 生成与投递测试",
            "screen_peek": "窥屏链路测试",
            "qzone_integration": "QQ 空间链路测试",
            "proactive_message": "主动消息链路测试",
            "model_diagnostics": "模型数据排障",
            "skill_similarity": "技能相似项检查",
            "weather_api": "天气 API 请求测试",
            "balance_api": "余额接口请求测试",
            "web_search": "搜索接口请求测试",
        }.get(test_type, "排障链路测试")
