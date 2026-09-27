# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaDiagnosticsPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_diagnostics.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 429 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaDiagnosticsMixin）。
"""
from __future__ import annotations

from .page_api_media_diagnostics_shared import logger
from .page_api_media_diagnostics_shared import Any
from .page_api_media_diagnostics_shared import Mapping
from .page_api_media_diagnostics_shared import Path
from .page_api_media_diagnostics_shared import _redact_outbound_secrets
from .page_api_media_diagnostics_shared import asyncio
from .page_api_media_diagnostics_shared import generation_log_candidates
from .page_api_media_diagnostics_shared import hashlib
from .page_api_media_diagnostics_shared import json
from .page_api_media_diagnostics_shared import math
from .page_api_media_diagnostics_shared import request
from .page_api_media_diagnostics_shared import time
from .page_api_media_diagnostics_shared import urlparse



class PrivateCompanionPageApiMediaDiagnosticsPart01Mixin:
    """PrivateCompanionPageApiMediaDiagnosticsPart01Mixin（从 PrivateCompanionPageApiMediaDiagnosticsMixin 拆出）。"""


    async def swap_image_api_settings(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            force = self._normalize_bool_value(payload.get("force"))
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            raw_endpoints = self._config_get_raw("external_image_api_endpoints", [])
            endpoints = normalizer(raw_endpoints) if callable(normalizer) else (raw_endpoints if isinstance(raw_endpoints, list) else [])
            if endpoints:
                if len(endpoints) < 2:
                    return self._error("在线生图 API 队列少于 2 条，无法交换优先级。")
                second = endpoints[1] if isinstance(endpoints[1], dict) else {}
                second_missing = [
                    label
                    for key, label in (
                        ("base_url", "第二条 API 地址"),
                        ("api_key", "第二条 API Key"),
                        ("model", "第二条图片模型"),
                    )
                    if not str(second.get(key) or "").strip()
                ]
                if (not second.get("enabled", True) or second_missing) and not force:
                    reason = "第二条 API 已关闭" if not second.get("enabled", True) else "、".join(second_missing)
                    return self._error(f"第二条在线图片 API 不可用，不能切换：{reason}")
                changed = list(endpoints)
                changed[0], changed[1] = changed[1], changed[0]
                async with self._image_api_runtime_lock():
                    self._apply_config_value("external_image_api_endpoints", changed)
                    config_saved = await self._save_config_if_possible()
                overview = await self.get_overview()
                if self._is_http_error_response(overview):
                    return overview
                if overview.get("success"):
                    data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                    data["changed"] = {"external_image_api_endpoints": changed}
                    data["config_saved"] = config_saved
                    data["message"] = "已交换在线图片 API 队列前两项。"
                    overview["data"] = data
                return overview
            pairs = (
                ("external_image_api_platform", "backup_external_image_api_platform"),
                ("EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL"),
                ("EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_KEY"),
                ("EXTERNAL_IMAGE_API_MODEL", "BACKUP_EXTERNAL_IMAGE_API_MODEL"),
                ("external_image_api_size", "backup_external_image_api_size"),
                ("external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds"),
                ("external_image_api_custom_headers", "backup_external_image_api_custom_headers"),
            )

            current: dict[str, Any] = {}
            for primary_key, backup_key in pairs:
                current[primary_key] = self._normalize_setting_value(primary_key, self._config_get(primary_key))
                current[backup_key] = self._normalize_setting_value(backup_key, self._config_get(backup_key))

            backup_required = (
                "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
                "BACKUP_EXTERNAL_IMAGE_API_KEY",
                "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            )
            missing_backup = [key for key in backup_required if not str(current.get(key) or "").strip()]
            if missing_backup and not force:
                labels = {
                    "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "备选在线 API 地址",
                    "BACKUP_EXTERNAL_IMAGE_API_KEY": "备选在线 API Key",
                    "BACKUP_EXTERNAL_IMAGE_API_MODEL": "备选在线图片模型",
                }
                return self._error("备选在线图片 API 未配置完整，不能切换：" + "、".join(labels.get(key, key) for key in missing_backup))

            changed: dict[str, Any] = {}
            for primary_key, backup_key in pairs:
                changed[primary_key] = current.get(backup_key)
                changed[backup_key] = current.get(primary_key)

            old_primary_complete = bool(
                str(current.get("EXTERNAL_IMAGE_API_BASE_URL") or "").strip()
                and str(current.get("EXTERNAL_IMAGE_API_KEY") or "").strip()
                and str(current.get("EXTERNAL_IMAGE_API_MODEL") or "").strip()
            )
            changed["enable_backup_external_image_api"] = old_primary_complete

            async with self._image_api_runtime_lock():
                for key, value in changed.items():
                    self._apply_config_value(key, value, changed)
                self._sync_photo_generation_runtime_config()
                config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                data["changed"] = changed
                data["config_saved"] = config_saved
                data["message"] = "已交换主在线图片 API 与备选在线图片 API。"
                overview["data"] = data
            return overview
        except Exception as exc:
            logger.error(f"切换在线生图 API 失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _image_api_endpoint_test_key(self, endpoint: dict[str, Any]) -> str:
        custom_headers = str(endpoint.get("custom_headers") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        identity = json.dumps(
            [
                bool(endpoint.get("enabled", True)),
                str(endpoint.get("platform") or "auto").strip().lower(),
                str(endpoint.get("base_url") or "").strip().rstrip("/"),
                str(endpoint.get("model") or "").strip(),
                str(endpoint.get("api_key") or "").strip(),
                str(endpoint.get("size") or "1024x1024").strip().lower(),
                str(endpoint.get("ratio") or "").strip().lower(),
                self._int(endpoint.get("timeout_seconds"), 180, 20, 600),
                custom_headers,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"image_api_endpoint_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"

    def _troubleshooting_safe_image_api_url(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.hostname:
                return self._single_line(raw.split("?", 1)[0].split("#", 1)[0], 180)
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except ValueError:
                port = ""
            return self._single_line(f"{parsed.scheme}://{host}{port}{parsed.path or ''}", 180)
        except Exception:
            return self._single_line(raw.split("?", 1)[0].split("#", 1)[0], 180)

    def _redact_image_api_test_text(self, value: Any, endpoint: Any = None, limit: int = 220) -> str:
        cleaned = _redact_outbound_secrets(value, self.plugin)
        endpoint_data = endpoint if isinstance(endpoint, dict) else {}
        secrets_to_hide = [str(endpoint_data.get("api_key") or "").strip()]
        custom_headers = str(endpoint_data.get("custom_headers") or "")
        for line in custom_headers.splitlines():
            _, separator, raw_value = line.partition(":")
            if separator:
                secrets_to_hide.append(raw_value.strip())
        for secret in secrets_to_hide:
            if len(secret) >= 4:
                cleaned = cleaned.replace(secret, "[密钥已隐藏]")
        return self._single_line(cleaned, limit)

    def _image_api_test_artifact_path(self, value: Any) -> Path | None:
        raw = str(value or "").strip()
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if not raw or not data_dir:
            return None
        try:
            path = Path(raw).resolve(strict=False)
            root = (Path(data_dir) / "generated_photos").resolve(strict=False)
            path.relative_to(root)
        except (OSError, ValueError):
            return None
        if not path.name.startswith("private_companion_troubleshooting_"):
            return None
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return None
        return path

    async def _cleanup_image_api_test_artifact(self, value: Any) -> bool:
        path = self._image_api_test_artifact_path(value)
        if path is None or not path.exists():
            return False
        try:
            await asyncio.to_thread(path.unlink)
            return True
        except OSError as exc:
            logger.warning(
                "清理生图 API 测试图片失败: path=%s error=%s",
                self._single_line(path, 180),
                self._single_line(exc, 160),
            )
            return False

    async def _prune_stale_image_api_test_artifacts(self, max_age_seconds: int = 3600) -> int:
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if not data_dir:
            return 0
        root = Path(data_dir) / "generated_photos"
        cutoff = time.time() - max(300, int(max_age_seconds))

        def prune() -> int:
            if not root.exists():
                return 0
            removed = 0
            for path in root.glob("private_companion_troubleshooting_*"):
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
            return removed

        return await asyncio.to_thread(prune)

    @staticmethod
    def _image_api_endpoint_configuration_note(endpoint: dict[str, Any]) -> str:
        if not bool(endpoint.get("enabled", True)):
            return "该端点已停用"
        missing: list[str] = []
        if not str(endpoint.get("base_url") or "").strip():
            missing.append("API 地址")
        if not str(endpoint.get("api_key") or "").strip():
            missing.append("API Key")
        if not str(endpoint.get("model") or "").strip():
            missing.append("图片模型")
        return f"缺少{'、'.join(missing)}" if missing else ""

    def _troubleshooting_image_api_endpoints(self) -> list[dict[str, Any]]:
        getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
        if not callable(getter):
            return []
        try:
            endpoints = getter(include_incomplete=True, include_disabled=True)
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        for index, endpoint in enumerate(endpoints[:12] if isinstance(endpoints, list) else []):
            if isinstance(endpoint, dict):
                items.append(self._troubleshooting_image_api_endpoint_summary(endpoint, index))
        return items

    def _troubleshooting_image_api_endpoint_summary(self, endpoint: dict[str, Any], index: int) -> dict[str, Any]:
        platform_labels = {
            "auto": "自动识别",
            "openai": "OpenAI 兼容",
            "openrouter": "OpenRouter",
            "agnes": "Agnes Image",
            "sensenova": "SenseNova 日日新",
            "bailian": "阿里云百炼",
            "modelscope": "魔搭社区",
            "doubao": "豆包/火山方舟",
            "gemini": "Gemini",
            "minimax": "MiniMax",
        }
        note = self._image_api_endpoint_configuration_note(endpoint)
        platform = self._single_line(endpoint.get("platform"), 30).lower() or "auto"
        enabled = bool(endpoint.get("enabled", True))
        return {
            "index": index,
            "test_key": self._image_api_endpoint_test_key(endpoint),
            "name": self._single_line(endpoint.get("name"), 80) or f"在线 API {index + 1}",
            "enabled": enabled,
            "ready": not note,
            "status": "disabled" if not enabled else ("incomplete" if note else "ready"),
            "status_text": note or "配置完整，尚未单独测试",
            "platform": platform,
            "platform_label": platform_labels.get(platform, platform or "自动识别"),
            "base_url": self._troubleshooting_safe_image_api_url(endpoint.get("base_url")),
            "model": self._single_line(endpoint.get("model"), 100),
            "size": self._single_line(endpoint.get("size"), 40) or "1024x1024",
            "ratio": self._single_line(endpoint.get("ratio"), 20),
            "timeout_seconds": self._int(endpoint.get("timeout_seconds"), 180, 20, 600),
        }

    def _legacy_recent_photo_generation_debug(self, *, event_limit: int = 240) -> dict[str, Any]:
        """读取生图 trace 的最近事件，供生图父面板按需展开查看。

        文件内容已经由生图运行时统一按 JSONL 写入并执行默认脱敏。页面只返回最近
        的一段事件，避免把历史日志或无界请求体一次性送到浏览器。
        """
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        root = Path(data_dir) if data_dir else None
        if root is None:
            return {
                "enabled": False,
                "available": False,
                "path": "",
                "latest": {},
                "traces": [],
                "events": [],
            }
        candidates = generation_log_candidates(root)
        paths = [item for item in candidates if item.is_file()]
        if not paths:
            return {
                "enabled": False,
                "available": False,
                "path": str(root / "photo_generation_trace.txt"),
                "latest": {},
                "traces": [],
                "events": [],
            }
        events: list[dict[str, Any]] = []
        try:
            per_file_limit = max(1, min(1000, int(event_limit) * 4))
            for path in paths:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-per_file_limit:]:
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and (value.get("trace") or value.get("trace_id")):
                        if not value.get("trace"):
                            value["trace"] = value.get("trace_id")
                        value["source_file"] = path.name
                        events.append(value)
        except (OSError, UnicodeError):
            return {
                "enabled": True,
                "available": False,
                "path": str(paths[0]),
                "latest": {},
                "traces": [],
                "events": [],
            }
        events.sort(key=lambda item: (
            self._photo_debug_event_timestamp(item),
            self._photo_debug_event_sequence(item),
        ))
        events = events[-max(1, min(240, int(event_limit))):]
        trace_rows: dict[str, dict[str, Any]] = {}
        for event in events:
            trace_id = self._single_line(event.get("trace"), 80)
            if not trace_id:
                continue
            row = trace_rows.pop(trace_id, None)
            if row is None:
                row = {
                    "trace": trace_id,
                    "started_at": event.get("time") or event.get("ts"),
                    "last_at": event.get("time") or event.get("ts"),
                    "stage": "",
                    "status": "",
                    "event_count": 0,
                }
            row["last_at"] = event.get("time") or event.get("ts")
            row["stage"] = self._single_line(event.get("stage"), 80)
            row["status"] = self._single_line(event.get("status"), 30)
            row["event_count"] = self._int(row.get("event_count"), 0) + 1
            trace_rows[trace_id] = row
        traces = list(trace_rows.values())[-24:]
        latest_trace = events[-1].get("trace") if events else ""
        latest = next((row for row in reversed(events) if row.get("trace") == latest_trace), {}) if latest_trace else {}
        return {
            "enabled": True,
            "available": bool(events),
            "path": str(paths[0]),
            "sources": [str(item) for item in paths],
            "latest": latest,
            "traces": traces,
            "events": events,
        }

    def _image_debug_data_roots(self) -> list[Path]:
        """Resolve the image extension's actual debug roots for this owner."""
        roots: list[str] = []
        getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            api = getter() if callable(getter) else None
            resolver = getattr(api, "debug_data_dirs", None)
            if callable(resolver):
                values = resolver(self.plugin)
                if isinstance(values, (list, tuple)):
                    roots.extend(str(item or "").strip() for item in values)
        except Exception:
            pass
        fallback = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if fallback:
            roots.append(fallback)
        result: list[Path] = []
        seen: set[str] = set()
        for raw in roots:
            if not raw:
                continue
            try:
                root = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                continue
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                result.append(root)
        return result

    @staticmethod
    def _photo_debug_event_timestamp(value: Mapping[str, Any]) -> float:
        try:
            timestamp = float(value.get("ts") or 0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return timestamp if math.isfinite(timestamp) and timestamp > 0 else 0.0

    @staticmethod
    def _photo_debug_event_sequence(value: Mapping[str, Any]) -> int:
        """Parse an event sequence without letting malformed logs abort reads."""
        try:
            raw = value.get("seq")
            if raw in (None, ""):
                return 0
            return int(raw)
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _photo_debug_event_fingerprint(value: Mapping[str, Any]) -> str:
        """Return the source-independent portion of a generation event.

        Legacy TXT and unified JSONL records deliberately differ in envelope
        fields such as sequence, timestamp and severity. The bridge writes
        both records for one operation, so use only the shared semantic fields
        to find those pairs. Optional route metadata remains included when it
        is present, preventing unrelated engine events from being coalesced.
        """
        fingerprint: dict[str, Any] = {
            "stage": str(value.get("stage") or ""),
            "status": str(value.get("status") or ""),
            "context": value.get("context"),
            "data": value.get("data"),
        }
        for key in (
            "operation",
            "workflow",
            "backend",
            "route",
            "attempt",
            "error_code",
            "failure_stage",
        ):
            item = value.get(key)
            if item not in (None, ""):
                fingerprint[key] = item
        return hashlib.sha256(
            json.dumps(
                fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
