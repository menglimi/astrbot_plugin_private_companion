# -*- coding: utf-8 -*-
"""运行时健康与插件集成面板域。

由 tools/split_mixin_domain.py 从 page_api_summary_panel.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 602 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiSummaryPanelMixin）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiSummaryPanelRuntimeMixin:
    """运行时健康与插件集成面板域（从 PrivateCompanionPageApiSummaryPanelMixin 拆出）。"""


    def _companion_plugins_summary(self) -> dict[str, dict[str, bool]]:
        image_api_getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            image_api = image_api_getter() if callable(image_api_getter) else None
        except Exception:
            image_api = None
        image_status_getter = getattr(image_api, "status", None) if image_api is not None else None
        try:
            image_status = image_status_getter() if callable(image_status_getter) else {}
        except Exception:
            image_status = {}
        if not isinstance(image_status, dict):
            image_status = {}
        image_contract = self._image_extension_contract_status(image_api)
        if image_contract:
            image_status = {**image_status, "companion_contract": image_contract}
            if not image_contract["available"]:
                image_status["available"] = False
                image_status["reason"] = image_contract["reason"] or "image_contract_incompatible"

        nai_api_getter = getattr(self.plugin, "_nai_image_api", None)
        try:
            nai_api = nai_api_getter() if callable(nai_api_getter) else None
        except Exception:
            nai_api = None
        nai_status_getter = getattr(self.plugin, "_nai_image_status", None)
        if not callable(nai_status_getter):
            nai_status_getter = getattr(nai_api, "status", None) if nai_api is not None else None
        try:
            nai_status = nai_status_getter() if callable(nai_status_getter) else {}
        except Exception:
            nai_status = {}
        if not isinstance(nai_status, dict):
            nai_status = {}

        reality_api_getter = getattr(self.plugin, "_reality_companion_api", None)
        try:
            reality_api = reality_api_getter() if callable(reality_api_getter) else None
        except Exception:
            reality_api = None
        reality_status_getter = getattr(reality_api, "status", None) if reality_api is not None else None
        try:
            reality_status = reality_status_getter() if callable(reality_status_getter) else {}
        except Exception:
            reality_status = {}
        if not isinstance(reality_status, dict):
            reality_status = {}

        content_status_getter = getattr(self.plugin, "_content_companion_status", None)
        try:
            content_status = content_status_getter() if callable(content_status_getter) else {}
        except Exception as exc:
            logger.warning(
                "创作扩展状态读取失败: %s",
                self._single_line(exc, 160),
            )
            content_status = {}
        if not isinstance(content_status, dict):
            content_status = {}

        image_summary = {
            "installed": image_api is not None,
            "enabled": bool(image_status.get("enabled")),
            "available": bool(image_api is not None and image_status.get("available", True)),
            "reason": self._single_line(image_status.get("reason"), 120),
        }
        if image_status.get("companion_contract"):
            image_summary["companion_contract"] = image_status["companion_contract"]

        return {
            # These two capabilities are part of the companion core. Keep them
            # visible in the same status payload so the panel and diagnostics
            # do not mistake them for optional external plugins.
            "boundary_feedback": {
                "installed": True,
                "enabled": bool(getattr(self.plugin, "enable_relationship_boundary_feedback", True)),
                "available": callable(getattr(self.plugin, "_enrich_boundary_feedback_intent", None)),
            },
            "temp_emotion": {
                "installed": True,
                "enabled": bool(getattr(self.plugin, "enable_emotion_simulation", True)),
                "available": callable(getattr(self.plugin, "_record_interaction_emotion_event", None)),
            },
            "content": {
                "installed": bool(content_status.get("installed")),
                "enabled": bool(content_status.get("enabled")),
                "available": bool(content_status.get("available")),
                "reason": self._single_line(
                    content_status.get("reason") or "content_companion_unavailable",
                    120,
                ),
            },
            "image": image_summary,
            "nai": {
                "installed": nai_api is not None,
                "enabled": bool(nai_status.get("enabled")),
                "available": bool(nai_api is not None and nai_status.get("available", False)),
            },
            "reality": {
                "installed": reality_api is not None,
                "enabled": bool(reality_status.get("enabled")),
                "available": bool(reality_api is not None and reality_status.get("available", True)),
            },
        }

    def _req041_runtime_summary(self) -> dict[str, Any]:
        """Build an aggregate-only migration and isolation status for administrators."""
        runtime = getattr(self.plugin, "req041_migration_status", None)
        runtime = runtime if isinstance(runtime, dict) else {}
        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        outbox = getattr(self.plugin, "req041_migration_outbox", None)
        control: dict[str, Any] = {}
        aggregates: dict[str, Any] = {
            "identities": [], "active_read_leases": 0,
            "pending": {"total": 0, "reasons": []},
        }
        queue: dict[str, Any] = {"backlog": 0, "states": {}}
        try:
            if coordinator is not None:
                control = coordinator.status()
                summary_getter = getattr(coordinator, "safe_admin_summary", None)
                if callable(summary_getter):
                    aggregates = summary_getter()
            epoch = str(control.get("migration_epoch") or "")
            queue_getter = getattr(outbox, "safe_admin_summary", None)
            if epoch and callable(queue_getter):
                queue = queue_getter(epoch)
        except Exception:
            return {
                "state": "degraded", "phase": "", "code": "admin_summary_unavailable",
                "checkpoint": "", "required": bool(runtime.get("required")),
                "migration": aggregates, "outbox": queue,
                "observability": {}, "config_consistency": {},
            }
        state = str(runtime.get("state") or control.get("state") or "unknown")
        phase = str(runtime.get("phase") or control.get("phase") or "")
        pending_total = int((aggregates.get("pending") or {}).get("total") or 0)
        observability = getattr(self.plugin, "req041_observability", None)
        if observability is not None:
            observability.migration(
                state=state, phase=phase, backlog=int(queue.get("backlog") or 0),
                pending=pending_total,
                mismatches=int((observability.snapshot().get("counters") or {}).get("migration_mismatch") or 0),
            )
            metrics = observability.snapshot()
        else:
            metrics = {}
        allowlist = getattr(self.plugin, "group_relationship_affinity_allowlist", [])
        allowlist_count = len(allowlist) if isinstance(allowlist, (list, tuple, set, frozenset)) else 0
        affinity_enabled = bool(getattr(self.plugin, "enable_group_relationship_affinity", False))
        return {
            "state": state if state in {"active", "replaying", "degraded", "paused", "complete"} else "unknown",
            "phase": phase if phase in {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"} else "",
            "code": self._single_line(runtime.get("code") or control.get("error_code"), 120),
            "checkpoint": self._single_line(runtime.get("checkpoint") or control.get("checkpoint"), 120),
            "required": bool(runtime.get("required")),
            "migration": aggregates,
            "outbox": queue,
            "observability": metrics,
            "config_consistency": {
                "group_affinity_enabled": affinity_enabled,
                "group_affinity_allowlist_count": allowlist_count,
                "group_affinity_effective": bool(affinity_enabled and allowlist_count > 0),
                "memory_bridge_bound": bool(runtime.get("memory_bound")),
                "scoped_projection_ready": bool((runtime.get("scoped") or {}).get("ok")),
            },
        }

    def _prompt_injection_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("recent_prompt_injections")
        if not isinstance(raw, dict):
            raw = {}
        raw_events = data.get("recent_prompt_injection_events")
        if not isinstance(raw_events, list):
            raw_events = []

        def preview_is_internal_prompt(value: Any) -> bool:
            cleaned = self._single_line(value, 260)
            if not cleaned:
                return False
            internal_markers = (
                "【语音消息规则】",
                "<pc_tts>",
                "</pc_tts>",
                "语音消息规则",
                "提示词片段",
                "请求级环境感知注入",
                "被动回复注入",
                "当前语音正文目标语种",
                "自然聊天时用中文文字推进对话",
                "不要写“中文含义”",
            )
            if any(marker in cleaned for marker in internal_markers):
                return True
            return cleaned.startswith("【") and "规则" in cleaned[:40]

        def safe_message_preview(value: Any, limit: int = 120) -> str:
            preview = self._single_line(value, limit)
            if preview_is_internal_prompt(preview):
                return ""
            return preview

        def normalize_item(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            ts = self._float(item.get("ts"))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            raw_modules = item.get("modules") if isinstance(item.get("modules"), list) else []
            if not raw_modules:
                normalizer = getattr(self.plugin, "_normalize_prompt_injection_modules", None)
                if callable(normalizer):
                    try:
                        raw_modules = normalizer(str(item.get("content") or ""), None)
                    except Exception:
                        raw_modules = []
            modules: list[dict[str, Any]] = []
            for index, module in enumerate(raw_modules[:28]):
                if not isinstance(module, dict):
                    continue
                content = str(module.get("content") or "")[:6500]
                if not content.strip():
                    continue
                module_metadata = module.get("metadata") if isinstance(module.get("metadata"), dict) else {}
                modules.append(
                    {
                        "key": self._single_line(module.get("key"), 100) or f"module.{index + 1}",
                        "source": self._single_line(module.get("source"), 80),
                        "priority": self._int(module.get("priority")),
                        "title": self._single_line(module.get("title"), 80) or "提示词片段",
                        "description": self._single_line(module.get("description"), 260),
                        "chars": self._int(module.get("chars")),
                        "truncated": bool(module.get("truncated")),
                        "preview": self._single_line(module.get("preview"), 220),
                        "content": content,
                        "metadata": {
                            self._single_line(key, 40): self._single_line(value, 120)
                            for key, value in module_metadata.items()
                            if self._single_line(key, 40) and self._single_line(value, 120)
                        },
                    }
                )
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "kind": self._single_line(item.get("kind"), 20),
                "session": self._single_line(item.get("session"), 160),
                "title": self._single_line(item.get("title"), 80),
                "mode": self._single_line(item.get("mode"), 40),
                "chars": self._int(item.get("chars")),
                "truncated": bool(item.get("truncated")),
                "preview": self._single_line(item.get("preview"), 260),
                "trace_seq": self._int(item.get("trace_seq")),
                "content": str(item.get("content") or "")[:13000],
                "modules": modules,
                "metadata": {
                    self._single_line(key, 40): self._single_line(value, 240)
                    for key, value in metadata.items()
                    if self._single_line(key, 40) and self._single_line(value, 240)
                },
            }

        def normalize_message(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            raw_items = item.get("items") if isinstance(item.get("items"), list) else []
            normalized_items = [entry for entry in (normalize_item(raw_item) for raw_item in raw_items[:32]) if entry]
            normalized_items.sort(
                key=lambda entry: (
                    self._float(entry.get("ts")),
                    self._int(entry.get("trace_seq")),
                )
            )
            if not normalized_items:
                return None
            first_ts = self._float(item.get("first_ts")) or min(self._float(entry.get("ts")) for entry in normalized_items)
            last_ts = self._float(item.get("last_ts")) or max(self._float(entry.get("ts")) for entry in normalized_items)
            kinds: list[str] = []
            for entry in normalized_items:
                kind = self._single_line(entry.get("kind"), 20)
                if kind and kind not in kinds:
                    kinds.append(kind)
            message_preview = safe_message_preview(item.get("message_preview"), 120)
            if not message_preview:
                message_preview = next(
                    (
                        safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                        for entry in normalized_items
                        if isinstance(entry, dict)
                        and safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                    ),
                    "",
                )
            return {
                "trace_id": self._single_line(item.get("trace_id"), 80),
                "session": self._single_line(item.get("session"), 160) or self._single_line(normalized_items[-1].get("session"), 160),
                "sender_label": self._single_line(item.get("sender_label"), 80),
                "message_preview": message_preview,
                "first_ts": first_ts,
                "first_time": self.plugin._format_timestamp_elapsed(first_ts),
                "last_ts": last_ts,
                "time": self.plugin._format_timestamp_elapsed(last_ts),
                "item_count": len(normalized_items),
                "module_count": sum(len(entry.get("modules") or []) for entry in normalized_items),
                "kinds": kinds,
                "items": normalized_items,
            }

        result: dict[str, Any] = {}
        for kind in ("tts", "proactive", "passive", "request"):
            items = raw.get(kind) if isinstance(raw.get(kind), list) else []
            limit = 8 if kind == "tts" else 5
            normalized = [entry for entry in (normalize_item(item) for item in items[:limit]) if entry]
            result[kind] = normalized
        messages = [entry for entry in (normalize_message(item) for item in raw_events[:10]) if entry]
        if not messages:
            legacy_messages: list[dict[str, Any]] = []
            for kind in ("request", "passive", "proactive", "tts"):
                for index, item in enumerate(result.get(kind, [])):
                    if not isinstance(item, dict):
                        continue
                    ts = self._float(item.get("ts"))
                    legacy_messages.append(
                        {
                            "trace_id": self._single_line(item.get("trace_id"), 80) or f"legacy-{kind}-{index}",
                            "session": self._single_line(item.get("session"), 160),
                            "sender_label": self._single_line(item.get("metadata", {}).get("发送者"), 80),
                            "message_preview": safe_message_preview(item.get("metadata", {}).get("触发消息"), 120),
                            "first_ts": ts,
                            "first_time": self.plugin._format_timestamp_elapsed(ts),
                            "last_ts": ts,
                            "time": self.plugin._format_timestamp_elapsed(ts),
                            "item_count": 1,
                            "module_count": len(item.get("modules") or []),
                            "kinds": [kind],
                            "items": [item],
                        }
                    )
            legacy_messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
            messages = legacy_messages[:10]
        else:
            messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
        result["messages"] = messages[:10]
        result["message_total"] = len(result["messages"])
        result["total"] = (
            len(result.get("tts", []))
            + len(result.get("proactive", []))
            + len(result.get("passive", []))
            + len(result.get("request", []))
        )
        return result

    async def _sqlite_wal_status_summary(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        paths_getter = getattr(self.plugin, "_sqlite_wal_candidate_paths", None)
        paths = paths_getter() if callable(paths_getter) else []

        def inspect(path: Path) -> dict[str, Any]:
            try:
                conn = sqlite3.connect(str(path), timeout=2.0)
                try:
                    mode_row = conn.execute("PRAGMA journal_mode").fetchone()
                    timeout_row = conn.execute("PRAGMA busy_timeout").fetchone()
                    mode = str(mode_row[0] if mode_row else "").lower()
                    timeout_ms = self._int(timeout_row[0] if timeout_row else 0)
                finally:
                    conn.close()
                level = "ok" if mode == "wal" and timeout_ms >= 1000 else "warn"
                text = f"journal_mode={mode or '-'}，busy_timeout={timeout_ms}ms"
                return {"path": str(path), "name": path.name, "level": level, "text": text, "journal_mode": mode, "busy_timeout_ms": timeout_ms}
            except Exception as exc:
                return {"path": str(path), "name": path.name, "level": "error", "text": self._single_line(exc, 180)}

        for path in paths[:12]:
            items.append(await self._to_thread_sqlite_inspect(inspect, path))
        return {
            "items": items,
            "ok": sum(1 for item in items if item.get("level") == "ok"),
            "warn": sum(1 for item in items if item.get("level") == "warn"),
            "error": sum(1 for item in items if item.get("level") == "error"),
        }

    def _external_ability_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "external_proactive_abilities", None)
        if callable(runtime_getter):
            raw_items = runtime_getter()
        else:
            store = data.get("external_proactive_abilities") if isinstance(data.get("external_proactive_abilities"), dict) else {}
            raw_items = list(store.values()) if isinstance(store, dict) else []
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
            schema = raw.get("config_schema") if isinstance(raw.get("config_schema"), dict) else {}
            items.append(
                {
                    "name": self._single_line(raw.get("name"), 64),
                    "module": self._single_line(raw.get("module"), 24) or "外部主动能力",
                    "label": self._single_line(raw.get("label"), 32) or self._single_line(raw.get("name"), 64),
                    "description": self._single_line(raw.get("description"), 180),
                    "when": self._single_line(raw.get("when"), 140),
                    "use_for": self._single_line(raw.get("use_for"), 140),
                    "avoid": self._single_line(raw.get("avoid"), 140),
                    "enabled": bool(raw.get("enabled")),
                    "available": bool(raw.get("available")),
                    "registered": bool(raw.get("registered")),
                    "share_probability": max(0.0, min(1.0, self._float(raw.get("share_probability")))),
                    "min_interval_hours": max(0.0, self._float(raw.get("min_interval_hours"))),
                    "config": config,
                    "config_schema": schema,
                    "last_executed": self.plugin._format_timestamp_elapsed(raw.get("last_executed_ts", 0)),
                    "last_status": self._single_line(raw.get("last_status"), 160),
                    "last_summary": self._single_line(raw.get("last_summary"), 160),
                    "success_count": self._int(raw.get("success_count")),
                    "failure_count": self._int(raw.get("failure_count")),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (not item["enabled"], not item["available"], item["module"], item["label"]))
        return {
            "total": len(items),
            "enabled_count": sum(1 for item in items if item["enabled"]),
            "available_count": sum(1 for item in items if item["available"]),
            "items": items,
        }

    def _body_monitor_integration_summary(self) -> dict[str, Any]:
        enabled = bool(getattr(self.plugin, "enable_body_monitor_integration", False))
        installed = False
        detector = getattr(self.plugin, "_integrated_plugin_installed", None)
        if callable(detector):
            try:
                installed = bool(detector("astrbot_plugin_body_monitor"))
            except Exception:
                installed = False

        raw: dict[str, Any] = {}
        status_getter = getattr(self.plugin, "_body_monitor_integration_status_view", None)
        if callable(status_getter):
            try:
                value = status_getter()
                raw = value if isinstance(value, dict) else {}
            except Exception as exc:
                raw = {"state": "error", "last_error": exc}

        if "installed" in raw:
            installed = bool(raw.get("installed"))
        elif raw.get("available") is True:
            installed = True
        state = self._body_monitor_status_state(raw, enabled=enabled, installed=installed)
        state_text = {
            "disabled": "联动已关闭",
            "not_installed": "未安装 Body Monitor",
            "incompatible": "接口版本不兼容",
            "initializing": "正在初始化",
            "connected": "已连接",
            "error": "连接异常",
        }[state]

        last_pull_at = self._float(
            raw.get("last_pull_at")
            or raw.get("last_pull_ts")
            or raw.get("last_polled_at")
        )
        last_pull_text = self._single_line(raw.get("last_pull_text"), 80)
        formatter = getattr(self.plugin, "_format_timestamp_elapsed", None)
        if not last_pull_text and last_pull_at > 0 and callable(formatter):
            try:
                last_pull_text = self._single_line(formatter(last_pull_at), 80)
            except Exception:
                last_pull_text = ""

        raw_batch = raw.get("last_batch")
        if not isinstance(raw_batch, dict):
            raw_batch = raw.get("batch") if isinstance(raw.get("batch"), dict) else {}
        batch = {
            "received": max(0, self._int(raw_batch.get("received"))),
            "accepted": max(0, self._int(raw_batch.get("accepted") or raw_batch.get("queued"))),
            "skipped": max(0, self._int(raw_batch.get("skipped") or raw_batch.get("rejected"))),
            "duplicate": max(0, self._int(raw_batch.get("duplicate") or raw_batch.get("duplicates"))),
            "expired": max(0, self._int(raw_batch.get("expired"))),
        }
        api_version = self._int(
            raw.get("api_version")
            or raw.get("proactive_event_api_version")
            or raw.get("version")
        )
        supported_api_version = self._int(
            raw.get("supported_api_version")
            or raw.get("expected_api_version")
            or 1
        )
        return {
            "enabled": enabled,
            "installed": installed,
            "state": state,
            "state_text": state_text,
            "api_version": api_version,
            "supported_api_version": supported_api_version,
            "last_pull_at": last_pull_at,
            "last_pull_text": last_pull_text,
            "last_batch": batch,
            "error": self._body_monitor_status_error(raw.get("last_error") or raw.get("error")),
        }

    def _deepseek_peak_routing_summary(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "_deepseek_peak_status", None)
        if not callable(getter):
            return {"enabled": False, "active": False, "configured": False}
        try:
            return dict(getter())
        except Exception as exc:
            return {
                "enabled": bool(getattr(self.plugin, "enable_deepseek_peak_replacement", False)),
                "active": False,
                "configured": False,
                "error": self._single_line(exc, 160),
            }

    def _livingmemory_summary(self) -> dict[str, Any]:
        try:
            available = bool(self.plugin._livingmemory_available())
        except Exception:
            available = False
        try:
            plugin_dir = str(self.plugin._livingmemory_plugin_dir())
        except Exception:
            plugin_dir = ""
        try:
            status = self.plugin._format_livingmemory_status()
        except Exception:
            status = "记忆插件：状态探测失败，已跳过协同。"
        # Detect "我会牢牢记住你" (RememberYou) bridge availability
        memory_companion_active = False
        memory_companion_display_name = ""
        memory_companion_presence: dict[str, Any] = {}
        try:
            bridge = self.plugin._memory_companion_bridge()  # type: ignore[attr-defined]
            if bridge is not None:
                memory_companion_active = True
                memory_companion_display_name = getattr(bridge, "display_name", "") or "我会牢牢记住你"
                if str(memory_companion_display_name).strip().lower() in {"rememberyou", "remember you", "memorycompanion", "memory companion", "astrbot_plugin_memory_companion", "astrbot_plugin_remember_you"}:
                    memory_companion_display_name = "我会牢牢记住你"
        except Exception:
            pass
        presence_getter = getattr(self.plugin, "_memory_companion_presence", None)
        if callable(presence_getter):
            try:
                presence = presence_getter()
                if isinstance(presence, dict):
                    memory_companion_presence = dict(presence)
            except Exception:
                memory_companion_presence = {}
        if not memory_companion_display_name and memory_companion_presence.get("detected"):
            memory_companion_display_name = self._single_line(
                memory_companion_presence.get("display_name"),
                80,
            ) or "我会牢牢记住你"
        configured_enabled = bool(getattr(self.plugin, "enable_livingmemory_integration", False))
        active_plugins: list[dict[str, Any]] = []
        if memory_companion_active:
            active_plugins.append(
                {
                    "type": "memory_companion",
                    "name": memory_companion_display_name or "我会牢牢记住你",
                    "display_name": memory_companion_display_name or "我会牢牢记住你",
                    "status": "桥接可用",
                }
            )
        if available:
            active_plugins.append(
                {
                    "type": "livingmemory",
                    "name": "LivingMemory",
                    "display_name": "LivingMemory",
                    "status": "工具式召回可用",
                    "tool_name": getattr(self.plugin, "livingmemory_tool_name", "") or "recall_long_term_memory",
                    "plugin_dir": plugin_dir,
                }
            )
        selected_plugin = active_plugins[0] if active_plugins else None
        conflict = bool(memory_companion_active and available)
        conflict_warning = (
            f"同时检测到{memory_companion_display_name or '我会牢牢记住你'}和 LivingMemory。建议只保留一个可协同记忆插件，避免重复召回、重复写入或提示词膨胀。"
            if conflict
            else ""
        )
        return {
            "enabled": bool(configured_enabled and active_plugins),
            "configured_enabled": configured_enabled,
            "compatible_available": bool(active_plugins),
            "available": available,
            "tool_name": getattr(self.plugin, "livingmemory_tool_name", ""),
            "plugin_dir": plugin_dir,
            "status": status,
            "memory_companion_active": memory_companion_active,
            "memory_companion_detected": bool(memory_companion_presence.get("detected")),
            "memory_companion_loaded": bool(memory_companion_presence.get("loaded")),
            "memory_companion_activated": bool(memory_companion_presence.get("activated")),
            "memory_companion_reason": self._single_line(memory_companion_presence.get("reason"), 80),
            "memory_companion_version": self._single_line(memory_companion_presence.get("version"), 40),
            "memory_companion_plugin_dir": self._single_line(memory_companion_presence.get("plugin_dir"), 260),
            "memory_companion_display_name": memory_companion_display_name,
            "active_plugins": active_plugins,
            "selected_plugin": selected_plugin,
            "selected_plugin_name": str((selected_plugin or {}).get("display_name") or ""),
            "conflict": conflict,
            "conflict_warning": conflict_warning,
        }
