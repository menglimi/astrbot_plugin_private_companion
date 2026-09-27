# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaDiagnosticsPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_diagnostics.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 446 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaDiagnosticsMixin）。
"""
from __future__ import annotations

from .page_api_media_diagnostics_shared import logger
from .page_api_media_diagnostics_shared import Any
from .page_api_media_diagnostics_shared import Path
from .page_api_media_diagnostics_shared import _path_text
from .page_api_media_diagnostics_shared import asyncio
from .page_api_media_diagnostics_shared import deepcopy
from .page_api_media_diagnostics_shared import diagnostic_test_id
from .page_api_media_diagnostics_shared import generation_log_candidates
from .page_api_media_diagnostics_shared import json
from .page_api_media_diagnostics_shared import request
from .page_api_media_diagnostics_shared import secrets
from .page_api_media_diagnostics_shared import time



class PrivateCompanionPageApiMediaDiagnosticsPart02Mixin:
    """PrivateCompanionPageApiMediaDiagnosticsPart02Mixin（从 PrivateCompanionPageApiMediaDiagnosticsMixin 拆出）。"""


    def _recent_photo_generation_debug(
        self,
        *,
        event_limit: int = 240,
        trace_id: str = "",
        summary_only: bool = False,
    ) -> dict[str, Any]:
        """Read generation events from the resolved data roots.

        The collapsed status endpoint reads only file tails and returns a
        summary. Full events are read only after the user opens the detail.
        """
        roots = self._image_debug_data_roots()
        if not roots:
            return {"enabled": False, "available": False, "path": "", "latest": {}, "traces": [], "events": []}
        candidates: list[tuple[Path, str, int]] = []
        for root in roots:
            paths = generation_log_candidates(root)
            for path in paths:
                try:
                    resolved = path.resolve(strict=True)
                    if path.is_symlink() or not resolved.is_file():
                        continue
                    if root not in resolved.parents:
                        continue
                    logical = str(resolved.relative_to(root)).replace("\\", "/")
                    candidates.append((resolved, logical, 2 if logical.startswith("photo_debug/generation") else 1))
                except (OSError, RuntimeError, ValueError):
                    continue
        unique: dict[str, tuple[Path, str, int]] = {
            str(path).casefold(): (path, logical, rank)
            for path, logical, rank in candidates
        }
        paths = list(unique.values())
        if not paths:
            return {"enabled": False, "available": False, "path": "", "latest": {}, "traces": [], "events": []}
        requested_trace = self._single_line(trace_id, 80)
        # Preserve same-source repetitions: retries and fallbacks can visit
        # the same stage more than once. Only combine the paired records that
        # the migration bridge writes to the legacy and unified log formats.
        records: list[tuple[int, dict[str, Any]]] = []
        records_by_event_id: dict[tuple[str, str], int] = {}
        records_by_fingerprint: dict[tuple[str, str], list[int]] = {}
        try:
            for path, logical, rank in paths:
                for line in self._read_debug_lines(path, tail_lines=256 if summary_only else None):
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    trace = self._single_line(value.get("trace") or value.get("trace_id"), 80)
                    if not trace or (requested_trace and trace != requested_trace):
                        continue
                    value["trace"] = trace
                    value["source_file"] = logical
                    event_id = self._single_line(value.get("event_id"), 120)
                    seq = self._photo_debug_event_sequence(value)
                    if event_id:
                        event_key = (trace, event_id)
                        existing_index = records_by_event_id.get(event_key)
                        if existing_index is not None:
                            existing_rank, _existing = records[existing_index]
                            if rank >= existing_rank:
                                records[existing_index] = (rank, value)
                            continue

                    fingerprint = self._photo_debug_event_fingerprint(value)
                    pair_key = (trace, fingerprint)
                    timestamp = self._photo_debug_event_timestamp(value)
                    bridge_index: int | None = None
                    bridge_distance: tuple[int, float] | None = None
                    for candidate_index in records_by_fingerprint.get(pair_key, []):
                        candidate_rank, candidate = records[candidate_index]
                        if candidate_rank == rank:
                            continue
                        candidate_seq = self._photo_debug_event_sequence(candidate)
                        candidate_timestamp = self._photo_debug_event_timestamp(candidate)
                        # A bridge normally keeps the sequence aligned. A
                        # short timestamp window covers migration paths with
                        # independent counters while preserving older repeats.
                        same_seq = bool(seq and candidate_seq and seq == candidate_seq)
                        close_in_time = bool(
                            timestamp
                            and candidate_timestamp
                            and abs(timestamp - candidate_timestamp) <= 1.0
                        )
                        if not same_seq and not close_in_time:
                            continue
                        distance = (0 if same_seq else 1, abs(timestamp - candidate_timestamp))
                        if bridge_distance is None or distance < bridge_distance:
                            bridge_index = candidate_index
                            bridge_distance = distance
                    if bridge_index is not None:
                        existing_rank, _existing = records[bridge_index]
                        if rank >= existing_rank:
                            records[bridge_index] = (rank, value)
                        if event_id:
                            records_by_event_id[(trace, event_id)] = bridge_index
                        continue

                    record_index = len(records)
                    records.append((rank, value))
                    if event_id:
                        records_by_event_id[(trace, event_id)] = record_index
                    records_by_fingerprint.setdefault(pair_key, []).append(record_index)
        except (OSError, UnicodeError):
            return {"enabled": True, "available": False, "path": paths[0][1], "latest": {}, "traces": [], "events": []}
        # New JSONL events win when the same legacy event was dual-written,
        # while older unique legacy events remain available for a complete
        # trace across a migration boundary.
        events = [value for _rank, value in records]
        events.sort(key=lambda item: (
            self._photo_debug_event_timestamp(item),
            self._photo_debug_event_sequence(item),
        ))
        limit = max(1, min(1000, int(event_limit)))
        events = events[-limit:]
        trace_rows: dict[str, dict[str, Any]] = {}
        for event in events:
            trace = self._single_line(event.get("trace"), 80)
            row = trace_rows.pop(trace, None)
            if row is None:
                row = {
                    "trace": trace,
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
            # Reinsert on every event so insertion order reflects each Trace's
            # most recent event rather than its first appearance.
            trace_rows[trace] = row
        traces = list(trace_rows.values())[-24:]
        latest = events[-1] if events else {}
        if summary_only:
            latest = {
                key: latest.get(key)
                for key in (
                    "trace", "trace_id", "request_id", "seq", "time", "ts", "elapsed_ms",
                    "stage", "status", "severity", "backend", "workflow", "route", "attempt",
                    "error_code", "failure_stage",
                )
                if key in latest
            }
        return {
            "enabled": True,
            "available": bool(events),
            "path": paths[0][1],
            "sources": [logical for _path, logical, _rank in paths],
            "latest": latest,
            "traces": traces,
            "events": [] if summary_only else events,
        }

    async def get_image_debug(self) -> dict[str, Any]:
        """Return full recent image debug events only when the panel expands them."""
        try:
            limit = self._int(request.args.get("limit"), 240, 1, 1000)
            trace_id = self._single_line(request.args.get("trace"), 80)
            payload = self._recent_photo_generation_debug(
                event_limit=limit,
                trace_id=trace_id,
            )
            if trace_id:
                summary = self._recent_photo_generation_debug(
                    event_limit=240,
                    summary_only=True,
                )
                # Keep the recent trace chooser intact after loading one
                # selected trace; only the event body is trace-scoped.
                payload["traces"] = summary.get("traces", [])
                payload["latest"] = payload["events"][-1] if payload["events"] else {}
            if isinstance(payload.get("events"), list):
                self._attach_debug_payload_contents(
                    payload["events"],
                    self._image_debug_data_roots(),
                )
            payload["requested_trace"] = trace_id
            return self._ok(payload)
        except Exception as exc:
            logger.warning(
                "读取生图 debug 失败: %s",
                self._single_line(exc, 180),
                exc_info=True,
            )
            return self._exception_error("读取生图 debug 失败")

    async def get_image_api_status(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                raw_results = deepcopy(self.plugin.data.get("troubleshooting_test_results", {}))
            stored = raw_results if isinstance(raw_results, dict) else {}
            items = self._troubleshooting_image_api_endpoints()
            for item in items:
                raw_result = stored.get(str(item.get("test_key") or ""))
                item["result"] = self._sanitize_troubleshooting_test_result(raw_result) if isinstance(raw_result, dict) else {}
            return self._ok(
                {
                    "items": items,
                    "backend": self._single_line(getattr(self.plugin, "photo_generation_backend", "auto"), 30) or "auto",
                }
            )
        except Exception as exc:
            logger.warning("获取生图 API 状态失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error(str(exc))

    async def test_image_api_endpoint(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        request_id = secrets.token_hex(6)
        started = time.time()
        logger.info("[test:%s][type:image_api_endpoint] 开始执行测试", request_id)
        try:
            result = await self._run_image_api_endpoint_test(payload)
        except Exception as exc:
            endpoint = payload.get("endpoint") if isinstance(payload.get("endpoint"), dict) else {}
            safe_error = self._redact_image_api_test_text(exc, endpoint, 220)
            logger.warning("生图 API 单独测试失败: %s", safe_error)
            result = {
                "ok": False,
                "title": "在线图片 API 单独测试",
                "error": safe_error,
                "exception_type": exc.__class__.__name__,
            }
        result["type"] = "image_api_endpoint"
        result["elapsed_ms"] = self._int(result.get("elapsed_ms")) or int((time.time() - started) * 1000)
        result["ran_at"] = time.time()
        result["ran_at_text"] = self.plugin._format_timestamp_elapsed(result["ran_at"])
        result.setdefault("request_id", request_id)
        result = self._finalize_test_diagnostics(
            "image_api_endpoint",
            result,
            started,
            title="在线图片 API 单独测试",
            finished_at=result["ran_at"],
        )
        result = self._diagnostic_envelope(
            result,
            test_type="image_api_endpoint",
            duration_ms=self._int(result.get("elapsed_ms")),
            test_id=diagnostic_test_id("image_api_endpoint"),
        )
        result_key = self._single_line(result.get("test_key"), 80) or "image_api_endpoint"
        await self._remember_troubleshooting_test_result(result_key, result)
        logger.info(
            "[test:%s][type:image_api_endpoint] 测试结束: status=%s elapsed_ms=%s",
            result.get("request_id"),
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)

    async def _run_image_api_endpoint_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            endpoint_index = int(payload.get("endpoint_index"))
        except (TypeError, ValueError):
            endpoint_index = -1
        submitted_endpoint = payload.get("endpoint")
        if isinstance(submitted_endpoint, dict):
            if endpoint_index < 0:
                endpoint_index = 0
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoint", None)
            endpoint = normalizer(submitted_endpoint, index=endpoint_index) if callable(normalizer) else dict(submitted_endpoint)
            if not isinstance(endpoint, dict) or not endpoint:
                return {"ok": False, "title": "在线图片 API 单独测试", "error": "提交的生图 API 配置无效"}
            summary = self._troubleshooting_image_api_endpoint_summary(endpoint, endpoint_index)
        else:
            summaries = self._troubleshooting_image_api_endpoints()
            getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
            try:
                endpoints = getter(include_incomplete=True, include_disabled=True) if callable(getter) else []
            except Exception:
                endpoints = []
            if endpoint_index < 0 or endpoint_index >= len(endpoints) or endpoint_index >= len(summaries):
                return {
                    "ok": False,
                    "title": "在线图片 API 单独测试",
                    "error": "指定的在线图片 API 不存在或配置队列已变化，请刷新后重试",
                }
            endpoint = endpoints[endpoint_index]
            summary = summaries[endpoint_index]
        base_result = {
            "test_key": summary["test_key"],
            "title": f"{summary['name']} 单独测试",
            "backend": "在线图片 API（单独）",
            "endpoint_index": endpoint_index,
            "endpoint_name": summary["name"],
            "endpoint_platform": summary["platform_label"],
            "endpoint_url": summary["base_url"],
            "endpoint_status": summary["status"],
            "image_model": summary["model"],
            "image_size": summary["size"],
            "endpoint_timeout_seconds": summary["timeout_seconds"],
            "backend_preference": "external_endpoint",
            "warnings": [
                "本次是纯文单端点验证：只调用所选在线 API，不会尝试队列中的其他 API，也不会回退到 ComfyUI 或 SDGen。",
                "不会上传参考图，也不覆盖自拍、改图、角色一致性或长提示词的真实调用；这些场景请在排障页运行“测试自拍”。",
            ],
        }
        configuration_note = self._image_api_endpoint_configuration_note(endpoint)
        if configuration_note:
            return {
                **base_result,
                "ok": False,
                "detail": configuration_note,
                "error": configuration_note,
            }
        runner = getattr(self.plugin, "_image_companion_test_endpoint", None)
        if not callable(runner):
            return {
                **base_result,
                "ok": False,
                "error": "插件缺少单端点在线生图入口",
            }

        prompt_text = self._single_line(payload.get("prompt"), 600) or (
            "A small green check mark sticker on a clean white desk beside a warm table lamp, "
            "clear composition, realistic photo, no people, no text, no watermark"
        )
        endpoint_timeout = self._int(summary.get("timeout_seconds"), 180, 20, 600)
        test_timeout = min(900, max(45, endpoint_timeout * 2 + 30))
        queue_timeout = min(900, max(60, test_timeout))
        started = time.time()
        lock = self._image_api_runtime_lock()
        wait_started = time.monotonic()
        lock_acquired = False
        try:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=queue_timeout)
                lock_acquired = True
            except asyncio.TimeoutError:
                queue_wait_ms = int((time.monotonic() - wait_started) * 1000)
                elapsed_ms = int((time.time() - started) * 1000)
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "detail": f"等待其他生图任务释放队列超过 {queue_timeout}s，所选接口尚未开始调用",
                    "error": f"生图测试排队超时（{queue_timeout}s）",
                }

            queue_wait_ms = int((time.monotonic() - wait_started) * 1000)
            try:
                external_result = await asyncio.wait_for(
                    runner(endpoint, prompt_text),
                    timeout=test_timeout,
                )
                external_result = external_result if isinstance(external_result, dict) else {}
                image_path = _path_text(
                    external_result.get("image_path"),
                    1000,
                )
                note = self._single_line(
                    external_result.get("message") or external_result.get("detail"),
                    500,
                )
            except asyncio.TimeoutError:
                elapsed_ms = int((time.time() - started) * 1000)
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "detail": f"接口开始调用后 {test_timeout}s 内仍未完成",
                    "error": f"单端点接口测试超时（{test_timeout}s）",
                }
            except Exception as exc:
                elapsed_ms = int((time.time() - started) * 1000)
                safe_error = self._redact_image_api_test_text(exc, endpoint, 220)
                logger.warning(
                    "在线图片 API 单端点测试失败: endpoint=%s error=%s",
                    self._single_line(summary["name"], 80),
                    safe_error,
                )
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "error": safe_error or "单端点调用失败",
                }
        finally:
            if lock_acquired:
                lock.release()

        elapsed_ms = int((time.time() - started) * 1000)
        if external_result.get("unsupported"):
            detail = self._redact_image_api_test_text(
                external_result.get("detail") or note,
                endpoint,
                1200,
            )
            return {
                **base_result,
                "ok": False,
                "unsupported": True,
                "test_status": "unsupported",
                "code": self._single_line(
                    external_result.get("code"),
                    80,
                ) or "image_current_contract_endpoint_test_unsupported",
                "error": detail or "新版 Image 扩展不支持旧式单端点测试",
                "detail": detail or "当前配置未被判定为错误，旧式单端点测试未执行",
                "next_step": self._single_line(
                    external_result.get("next_step"),
                    600,
                ) or "到排障页运行完整图片生成链路测试。",
                "prompt": prompt_text,
                "timeout_seconds": test_timeout,
                "queue_wait_ms": queue_wait_ms,
                "elapsed_ms": elapsed_ms,
            }
        exists = False
        file_size = 0
        if image_path:
            try:
                image_file = Path(str(image_path))
                exists = image_file.exists()
                file_size = image_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        ok = bool(image_path and exists)
        safe_note = self._redact_image_api_test_text(note, endpoint, 220)
        artifact_cleaned = await self._cleanup_image_api_test_artifact(image_path)
        await self._prune_stale_image_api_test_artifacts()
        return {
            **base_result,
            "ok": ok,
            "path": "" if artifact_cleaned else _path_text(image_path, 1000),
            "file_size": file_size,
            "detail": safe_note or ("已生成图片" if ok else "接口未返回有效图片文件"),
            "prompt": prompt_text,
            "timeout_seconds": test_timeout,
            "queue_wait_ms": queue_wait_ms,
            "elapsed_ms": elapsed_ms,
            "error": "" if ok else (safe_note or "接口未返回有效图片文件"),
        }
