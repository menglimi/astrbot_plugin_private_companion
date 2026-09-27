# -*- coding: utf-8 -*-
"""trace 记录与参考反馈域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 721 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from .helpers import _path_text, _redact_outbound_secrets, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .photo_reference_feedback import analyze_photo_reference_feedback
from .photo_reference_intent import ReferenceIntent
from .photo_reference_plan import PhotoReferencePlan, ReferenceFallback
from .photo_wardrobe_decision import PhotoWardrobeDecision
from .proactive_message_photo_generation_shared import _PHOTO_GENERATION_TRACE_FILE_LOCK, _now_ts, logger
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any



class ProactiveMessagePhotoGenerationTraceRecordFeedbackMixin:
    """trace 记录与参考反馈域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _choose_photo_workflow_name(self, kind: str) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized in {"selfie", "portrait", "自拍", "人像", "edit", "改图", "修图", "重绘", "p图"}:
            return self.comfyui_selfie_workflow_name or self.comfyui_text2img_workflow_name
        return self.comfyui_text2img_workflow_name or self.comfyui_selfie_workflow_name

    def _photo_generation_trace_id(self, session_key: str, workflow_kind: str) -> str:
        seed = f"{session_key}|{workflow_kind}|{_now_ts()}|{uuid.uuid4().hex[:8]}"
        return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:10]

    def _photo_generation_file_detail(self, image_path: str) -> str:
        path_text = _path_text(image_path, 1000)
        if not path_text:
            return "path=- exists=false size=0"
        if re.match(r"^(?:https?://|data:|base64://)", path_text, flags=re.I):
            return f"path={_single_line(path_text, 120)} exists=remote size=-"
        local_text = path_text[len("file://"):] if path_text.startswith("file://") else path_text
        try:
            path = Path(local_text)
            exists = path.exists() and path.is_file()
            size = path.stat().st_size if exists else 0
            return f"path={_single_line(str(path), 160)} exists={str(exists).lower()} size={size}"
        except Exception:
            return f"path={_single_line(path_text, 120)} exists=unknown size=-"

    def _photo_generation_backend_config_summary(self) -> str:
        nai_api_getter = getattr(self, "_nai_image_api", None)
        nai_installed = nai_api_getter() is not None if callable(nai_api_getter) else False
        configured_endpoints = getattr(self, "external_image_api_endpoints", [])
        if isinstance(configured_endpoints, list) and configured_endpoints:
            queue_getter = getattr(self, "_external_image_api_endpoint_queue", None)
            endpoints: list[dict[str, Any]] = []
            if callable(queue_getter):
                try:
                    endpoints = [
                        endpoint
                        for endpoint in queue_getter(include_incomplete=True, include_disabled=True)
                        if isinstance(endpoint, dict)
                    ]
                except Exception:
                    endpoints = []
            endpoint_bits = []
            for index, endpoint in enumerate(endpoints[:6]):
                ready = not bool(self._external_image_api_endpoint_unavailable_note(endpoint))
                endpoint_bits.append(
                    f"{index + 1}:{_single_line(endpoint.get('name') or endpoint.get('model'), 40) or '-'}"
                    f"/{_single_line(endpoint.get('platform'), 20) or 'auto'}"
                    f"/{'ready' if ready else 'unready'}"
                )
            return (
                f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
                f"comfyui={self._comfyui_photo_available()} "
                f"sdgen={self._sdgen_photo_available()} "
                f"external={self._external_photo_available()} "
                f"external_queue={len(endpoints)} "
                f"external_queue_items={';'.join(endpoint_bits) or '-'} "
                f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
                f"nai={nai_installed} "
                f"tool_call={self._custom_tool_photo_available()} "
                f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
            )
        external_base = _single_line(self._normalized_external_image_api_base_url(), 120)
        if external_base:
            external_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", external_base, flags=re.I)
        backup_base = _single_line(getattr(self, "backup_external_image_api_base_url", ""), 120)
        if backup_base:
            backup_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", backup_base, flags=re.I)
        return (
            f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
            f"comfyui={self._comfyui_photo_available()} "
            f"sdgen={self._sdgen_photo_available()} "
            f"external={self._external_photo_available()} "
            f"external_platform={self._resolved_external_image_api_platform()} "
            f"external_model={_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'} "
            f"external_size={_single_line(runtime_persona_setting(self, 'external_image_api_size', ''), 40) or '-'} "
            f"external_base={external_base or '-'} "
            f"backup_external={self._backup_external_photo_available()} "
            f"backup_platform={_single_line(runtime_persona_setting(self, 'backup_external_image_api_platform', ''), 30) or '-'} "
            f"backup_model={_single_line(getattr(self, 'backup_external_image_api_model', ''), 80) or '-'} "
            f"backup_base={backup_base or '-'} "
            f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
            f"nai={nai_installed} "
            f"tool_call={self._custom_tool_photo_available()} "
            f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
        )

    def _photo_generation_trace_max_bytes(self) -> int:
        max_kb = _safe_int(
            runtime_persona_setting(self, "photo_generation_trace_max_size_kb", 0),
            0,
        )
        return max(0, min(102400, max_kb)) * 1024

    def _photo_generation_trace_backup_count(self) -> int:
        return max(
            0,
            min(
                20,
                _safe_int(
                    runtime_persona_setting(self, "photo_generation_trace_backup_count", 5), 5
                ),
            ),
        )

    def _photo_generation_trace_file_path(self) -> Path:
        return Path(self.data_dir) / "photo_generation_trace.txt"

    def _rotate_photo_generation_trace_files(self, path: Path) -> None:
        backup_count = self._photo_generation_trace_backup_count()
        if backup_count <= 0:
            path.unlink(missing_ok=True)
            return
        for index in range(backup_count, 0, -1):
            source = path if index == 1 else path.with_name(
                f"{path.stem}.{index - 1}{path.suffix}"
            )
            target = path.with_name(f"{path.stem}.{index}{path.suffix}")
            if source.exists():
                os.replace(source, target)

    def _sanitize_photo_generation_trace_value(
        self,
        value: Any,
        *,
        key: str = "",
        depth: int = 0,
    ) -> Any:
        if depth > 5:
            return "[truncated]"
        normalized_key = str(key or "").strip().lower()
        if any(
            token in normalized_key
            for token in ("api_key", "apikey", "authorization", "access_token", "secret", "password")
        ):
            return "***"
        if isinstance(value, dict):
            return {
                _single_line(item_key, 80): self._sanitize_photo_generation_trace_value(
                    item_value,
                    key=str(item_key),
                    depth=depth + 1,
                )
                for item_key, item_value in list(value.items())[:48]
                if _single_line(item_key, 80)
            }
        if isinstance(value, (list, tuple, set)):
            return [
                self._sanitize_photo_generation_trace_value(item, depth=depth + 1)
                for item in list(value)[:48]
            ]
        if isinstance(value, str):
            redacted = _redact_outbound_secrets(value, self)
            if normalized_key.endswith("path") or normalized_key.endswith("_path"):
                return _path_text(redacted, 1000)
            if normalized_key in {"prompt", "submitted_prompt"}:
                return redacted
            return _single_line(redacted, 1200)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _single_line(value, 500)

    def _append_photo_generation_trace_event(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "ok",
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        payloads: dict[str, Any] | None = None,
    ) -> None:
        try:
            max_bytes = self._photo_generation_trace_max_bytes()
            if max_bytes <= 0:
                return
            normalized_trace = _single_line(trace_id, 80)
            normalized_stage = _single_line(stage, 80)
            if not normalized_trace or not normalized_stage:
                return
            now = _now_ts()
            states = getattr(self, "_photo_generation_trace_states", None)
            if not isinstance(states, dict):
                states = {}
                self._photo_generation_trace_states = states
            if normalized_trace not in states and len(states) >= 128:
                states.pop(next(iter(states)), None)
            state = states.setdefault(
                normalized_trace,
                {"started_at": now, "seq": 0, "context": {}},
            )
            state["seq"] = _safe_int(state.get("seq"), 0, 0) + 1
            if context:
                state["context"].update(self._sanitize_photo_generation_trace_value(context))
            payload = {
                "schema_version": 1,
                "ts": now,
                "time": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
                "trace": normalized_trace,
                "seq": state["seq"],
                "stage": normalized_stage,
                "status": _single_line(status, 30) or "ok",
                "elapsed_ms": max(0, int((now - _safe_float(state.get("started_at"), now, 0.0)) * 1000)),
                "context": dict(state["context"]),
                "data": self._sanitize_photo_generation_trace_value(data or {}),
            }
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_size = len(line.encode("utf-8"))
            if encoded_size > max_bytes:
                payload["context"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                }
                payload["data"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                    "original_bytes": encoded_size,
                }
                line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                encoded_size = len(line.encode("utf-8"))
            path = self._photo_generation_trace_file_path()
            with _PHOTO_GENERATION_TRACE_FILE_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                current_size = path.stat().st_size if path.exists() else 0
                if current_size and current_size + encoded_size > max_bytes:
                    self._rotate_photo_generation_trace_files(path)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
            if normalized_stage in {"delivery_completed", "delivery_failed", "failed"}:
                states.pop(normalized_trace, None)
        except Exception as exc:
            logger.debug(
                "记录生图可观测 trace 失败: %s",
                _single_line(exc, 120),
            )

    async def _append_photo_generation_trace_event_async(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "ok",
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        # trace 文件追加涉及 path.stat() 与写盘，从事件循环移到线程池。
        await asyncio.to_thread(
            self._append_photo_generation_trace_event,
            trace_id,
            stage,
            status=status,
            data=data,
            context=context,
        )

    def _record_recent_photo_generation(
        self,
        *,
        trace_id: str,
        session_key: str,
        continuity_key: str = "",
        workflow_kind: str,
        backend: str,
        ok: bool,
        prompt_text: str,
        image_path: str = "",
        note: str = "",
        reference_image_path: str = "",
        image_size: str = "",
        elapsed_ms: int = 0,
        presets: list[str] | None = None,
        reference_used: bool = False,
        reference_candidate: dict[str, Any] | None = None,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        submitted_reference_ids: tuple[str, ...] = (),
        wardrobe: PhotoWardrobeDecision | None = None,
        prompt_hash: str = "",
        submitted_prompt_hash: str = "",
        prompt_path: str = "",
        complete_prompt_length: int = 0,
        submitted_prompt_length: int = 0,
        prompt_sections: dict[str, str] | None = None,
        conflicts: list[str] | None = None,
        removed_conflicts: list[str] | None = None,
        residual_conflicts: list[str] | None = None,
        reference_removed: dict[str, Any] | None = None,
        sanitizer_version: int = 0,
        detected_conflict_details: list[dict[str, Any]] | None = None,
        removed_conflict_details: list[dict[str, Any]] | None = None,
        residual_conflict_details: list[dict[str, Any]] | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
        generation_completed: bool = False,
        failure_stage: str = "",
    ) -> None:
        try:
            reference_candidate = reference_candidate or {}
            wardrobe_payload = wardrobe.as_dict() if wardrobe is not None else {}
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            final_presets = [
                _single_line(name, 40)
                for name in (presets or [])
                if _single_line(name, 40)
            ][:1]

            def compact_audit(values: list[dict[str, Any]] | None) -> list[dict[str, str]]:
                result: list[dict[str, str]] = []
                for value in values or []:
                    if not isinstance(value, dict):
                        continue
                    item = {
                        key: _single_line(value.get(key), 120 if key == "preview" else 80)
                        for key in ("source", "section", "rule", "category", "action", "preview", "sha256")
                        if _single_line(value.get(key), 120 if key == "preview" else 80)
                    }
                    if item:
                        result.append(item)
                return result[:24]

            fixed_prompt_audit = dict(workflow_fixed_prompt_audit or {})
            item = {
                "schema_version": 3,
                "ts": _now_ts(),
                "trace": _single_line(trace_id, 40),
                "session": _single_line(session_key, 340),
                "continuity_key": self._normalize_photo_continuity_key(continuity_key),
                "kind": _single_line(workflow_kind, 30),
                "backend": _single_line(backend, 80),
                "ok": bool(ok),
                "generation_completed": bool(generation_completed),
                "failure_stage": _single_line(failure_stage, 60),
                "prompt_format": (
                    self._normalize_photo_generation_prompt_format(prompt_format)
                    if prompt_format
                    else self._photo_generation_prompt_format_mode()
                ),
                "prompt": _single_line(prompt_text, 900),
                "path": _path_text(image_path, 1000),
                "note": _single_line(note, 240),
                "reference": bool(reference_image_path),
                "reference_used": bool(reference_used),
                "reference_path": _path_text(reference_image_path, 1000),
                "reference_id": _single_line(reference_candidate.get("id"), 60),
                "reference_kind": _single_line(reference_candidate.get("kind"), 40),
                "reference_roles": list(reference_candidate.get("reference_roles") or [])[:8],
                "reference_outfit_category": _single_line(reference_candidate.get("outfit_category"), 40),
                "reference_intent": {
                    "requested_roles": list(intent_payload.requested_roles),
                    "excluded_roles": list(intent_payload.excluded_roles),
                    "continuity_mode": _single_line(intent_payload.continuity_mode, 30),
                    "confidence": round(float(intent_payload.confidence), 3),
                    "source": _single_line(intent_payload.source, 40),
                },
                "reference_plan": {
                    "bindings": [
                        {
                            "reference_id": _single_line(binding.reference_id, 80),
                            "path": _path_text(binding.path, 1000),
                            "roles": list(binding.roles),
                            "priority": int(binding.priority),
                            "preserve": list(binding.preserve),
                            "ignore": list(binding.ignore),
                            "submitted": binding.reference_id in submitted_reference_ids,
                        }
                        for binding in plan_payload.bindings
                    ],
                    "primary_reference_id": _single_line(plan_payload.primary_reference_id, 80),
                    "selection_reason": _single_line(plan_payload.selection_reason, 80),
                    "fallback_reason": _single_line(plan_payload.fallback_reason, 80),
                    "submitted_reference_ids": [
                        _single_line(reference_id, 80)
                        for reference_id in submitted_reference_ids
                        if _single_line(reference_id, 80)
                    ],
                },
                "reference_fallback": {
                    "requested_roles": list(fallback_payload.requested_roles),
                    "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                    "missing_roles": list(fallback_payload.missing_roles),
                    "message": _single_line(fallback_payload.message, 260),
                },
                "image_size": _single_line(image_size, 40),
                "elapsed_ms": int(max(0, elapsed_ms or 0)),
                "presets": final_presets,
                "preset_hint": _single_line(suggested_scene_preset, 80),
                "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                "scene_preset": final_presets[0] if final_presets else "",
                "wardrobe_decision_version": _safe_int(wardrobe_payload.get("decision_version"), 0),
                "wardrobe_rule_id": _single_line(wardrobe_payload.get("rule_id"), 80),
                "wardrobe_mode": _single_line(wardrobe_payload.get("mode"), 40),
                "wardrobe_source": _single_line(wardrobe_payload.get("source"), 40),
                "wardrobe_category": _single_line(wardrobe_payload.get("category"), 40),
                "outfit_locked": bool(wardrobe_payload.get("lock_outfit")),
                "daily_outfit_removed": bool(wardrobe_payload.get("remove_daily_outfit_context")),
                "wardrobe_reason": _single_line(wardrobe_payload.get("reason"), 240),
                "preset_source": _single_line(wardrobe_payload.get("preset_source"), 40),
                "suggestion_status": _single_line(wardrobe_payload.get("suggestion_status"), 60),
                "wardrobe_selected_presets": [
                    _single_line(value, 80)
                    for value in (wardrobe_payload.get("selected_presets") or [])
                    if _single_line(value, 80)
                ][:6],
                "wardrobe_adjustments": [
                    _single_line(value, 120)
                    for value in (wardrobe_payload.get("adjustments") or [])
                    if _single_line(value, 120)
                ][:12],
                "prompt_hash": _single_line(prompt_hash, 80),
                "submitted_prompt_hash": _single_line(submitted_prompt_hash, 80),
                "prompt_path": _path_text(prompt_path, 1000),
                "complete_prompt_length": _safe_int(complete_prompt_length, 0, 0),
                "submitted_prompt_length": _safe_int(submitted_prompt_length, 0, 0),
                "prompt_sections": {
                    _single_line(key, 50): _single_line(value, 240)
                    for key, value in (prompt_sections or {}).items()
                    if _single_line(key, 50) and _single_line(value, 240)
                },
                "conflicts": [_single_line(value, 120) for value in (conflicts or []) if _single_line(value, 120)][:12],
                "removed_conflicts": [
                    _single_line(value, 120)
                    for value in (removed_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "residual_conflicts": [
                    _single_line(value, 120)
                    for value in (residual_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "reference_removed": bool(reference_removed),
                "reference_removal": dict(reference_removed or {}),
                "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                "workflow_fixed_prompt": {
                    "scope": _single_line(fixed_prompt_audit.get("scope"), 30),
                    "config_key": _single_line(fixed_prompt_audit.get("config_key"), 80),
                    "configured": bool(fixed_prompt_audit.get("configured")),
                    "normalized": bool(fixed_prompt_audit.get("normalized")),
                    "normalization_changed": bool(
                        fixed_prompt_audit.get("normalization_changed")
                    ),
                    "conflict_cleaned": bool(fixed_prompt_audit.get("conflict_cleaned")),
                    "cleaned": bool(fixed_prompt_audit.get("cleaned")),
                    "applied": bool(fixed_prompt_audit.get("applied")),
                    "raw_length": _safe_int(fixed_prompt_audit.get("raw_length"), 0, 0),
                    "normalized_length": _safe_int(
                        fixed_prompt_audit.get("normalized_length"), 0, 0
                    ),
                    "applied_length": _safe_int(
                        fixed_prompt_audit.get("applied_length"), 0, 0
                    ),
                    "raw_sha256": _single_line(
                        fixed_prompt_audit.get("raw_sha256"), 80
                    ),
                    "normalized_sha256": _single_line(
                        fixed_prompt_audit.get("normalized_sha256"), 80
                    ),
                    "applied_sha256": _single_line(
                        fixed_prompt_audit.get("applied_sha256"), 80
                    ),
                    "removed_rules": [
                        _single_line(value, 80)
                        for value in (fixed_prompt_audit.get("removed_rules") or [])
                        if _single_line(value, 80)
                    ][:12],
                },
                "detected_conflicts": compact_audit(detected_conflict_details),
                "removed_conflict_details": compact_audit(removed_conflict_details),
                "residual_conflict_details": compact_audit(residual_conflict_details),
            }
            raw = self.data.setdefault("recent_photo_generations", [])
            if not isinstance(raw, list):
                raw = []
                self.data["recent_photo_generations"] = raw
            raw.insert(0, item)
            del raw[48:]
            self._save_data_sync(sections={"recent_photo_generations"})
        except Exception as exc:
            logger.debug("记录最近生图提示词失败: %s", _single_line(exc, 120))

    def _record_photo_reference_feedback(
        self,
        feedback_text: Any,
        *,
        continuity_key: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        feedback = analyze_photo_reference_feedback(feedback_text)
        if not feedback.issues and not feedback.regenerate_requested:
            return {}
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        generations = data.get("recent_photo_generations")
        if not isinstance(generations, list):
            return {}
        normalized_continuity = self._normalize_photo_continuity_key(continuity_key)
        normalized_session = _single_line(session_key, 340)
        if not normalized_continuity and not normalized_session:
            return {}
        linked: dict[str, Any] | None = None
        now = _now_ts()
        for candidate in generations:
            if not isinstance(candidate, dict):
                continue
            if normalized_continuity and self._normalize_photo_continuity_key(
                candidate.get("continuity_key")
            ) != normalized_continuity:
                continue
            if normalized_session and _single_line(candidate.get("session"), 340) != normalized_session:
                continue
            generated_at = _safe_float(candidate.get("ts"), 0.0, 0.0)
            if generated_at and now - generated_at > 6 * 3600:
                continue
            linked = candidate
            break
        if linked is None:
            return {}

        issues = list(dict.fromkeys((*linked.get("reference_feedback_issues", []), *feedback.issues)))
        linked["regeneration_requested"] = bool(
            linked.get("regeneration_requested") or feedback.regenerate_requested
        )
        linked["reference_feedback_issues"] = issues
        linked["reference_feedback_count"] = _safe_int(
            linked.get("reference_feedback_count"), 0, 0
        ) + 1
        record = {
            "schema_version": 1,
            "ts": now,
            "feedback": _single_line(feedback_text, 500),
            "regenerate_requested": feedback.regenerate_requested,
            "issues": list(feedback.issues),
            "confidence": feedback.confidence,
            "source": feedback.source,
            "generation_trace": _single_line(linked.get("trace"), 40),
            "generation_ts": linked.get("ts"),
            "continuity_key": self._normalize_photo_continuity_key(linked.get("continuity_key")),
            "session": _single_line(linked.get("session"), 340),
            "backend": _single_line(linked.get("backend"), 80),
            "final_prompt": _single_line(linked.get("prompt"), 900),
            "prompt_hash": _single_line(linked.get("prompt_hash"), 80),
            "prompt_path": _path_text(linked.get("prompt_path"), 1000),
            "reference_intent": deepcopy(linked.get("reference_intent") or {}),
            "reference_plan": deepcopy(linked.get("reference_plan") or {}),
            "reference_fallback": deepcopy(linked.get("reference_fallback") or {}),
        }
        records = data.setdefault("photo_reference_feedback", [])
        if not isinstance(records, list):
            records = []
            data["photo_reference_feedback"] = records
        records.insert(0, record)
        del records[96:]
        self._save_data_sync(
            sections={"recent_photo_generations", "photo_reference_feedback"}
        )
        return record

    def _record_photo_reference_feedback_from_event(self, event: Any) -> dict[str, Any]:
        if event is None or bool(getattr(event, "_private_companion_photo_feedback_recorded", False)):
            return {}
        try:
            setattr(event, "_private_companion_photo_feedback_recorded", True)
        except Exception:
            pass
        text = str(getattr(event, "message_str", "") or "")
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        session = _single_line(getattr(event, "unified_msg_origin", ""), 340)
        continuity_key = self._compose_photo_continuity_key(session, user_id)
        if not continuity_key:
            return {}
        return self._record_photo_reference_feedback(
            text,
            continuity_key=continuity_key,
        )

    def _write_photo_prompt_debug_file(
        self,
        *,
        trace_id: str,
        session_key: str,
        workflow_kind: str,
        base_prompt: str,
        scene_context_before: str,
        scene_context_after: str,
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
        presets: list[str],
        prompt_sections_before: dict[str, Any],
        prompt_sections: dict[str, str],
        prompt_sections_after: dict[str, Any],
        final_prompt: str,
        submitted_prompt: str = "",
        conflicts: list[str],
        removed_conflicts: list[str],
        residual_conflicts: list[str],
        detected_conflict_details: list[dict[str, Any]],
        removed_conflict_details: list[dict[str, Any]],
        residual_conflict_details: list[dict[str, Any]],
        reference_removed: dict[str, Any] | None,
        sanitizer_version: int,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        prompt_hash = hashlib.sha256(str(final_prompt or "").encode("utf-8", "ignore")).hexdigest()
        submitted_prompt_hash = hashlib.sha256(
            str(submitted_prompt or final_prompt or "").encode("utf-8", "ignore")
        ).hexdigest()
        if self._photo_generation_trace_max_bytes() <= 0:
            return "", prompt_hash
        try:
            root = Path(self.data_dir) / "photo_prompt_debug"
            root.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            filename = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{_single_line(trace_id, 40) or 'photo'}.json"
            path = root / filename

            def redact(value: Any) -> Any:
                if isinstance(value, str):
                    return _redact_outbound_secrets(value, self)
                if isinstance(value, dict):
                    return {str(key): redact(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(item) for item in value]
                return value

            reference_payload = {
                "id": _single_line((reference or {}).get("id"), 60),
                "kind": _single_line((reference or {}).get("kind"), 40),
                "path": _path_text((reference or {}).get("path"), 1000),
                "roles": list((reference or {}).get("reference_roles") or []),
                "outfit_category": _single_line((reference or {}).get("outfit_category"), 40),
                "outfit_lock_default": bool((reference or {}).get("outfit_lock_default")),
                "preferred_preset": _single_line((reference or {}).get("preferred_preset"), 60),
                "metadata_source": _single_line((reference or {}).get("metadata_source"), 30),
            }
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            payload = redact(
                {
                    "schema_version": 4,
                    "created_at": now.isoformat(timespec="seconds"),
                    "trace": _single_line(trace_id, 40),
                    "session": _single_line(session_key, 340),
                    "workflow_kind": _single_line(workflow_kind, 40),
                    "preset_hint": _single_line(suggested_scene_preset, 80),
                    "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                    "prompt_format": (
                        self._normalize_photo_generation_prompt_format(prompt_format)
                        if prompt_format
                        else self._photo_generation_prompt_format_mode()
                    ),
                    "base_prompt": base_prompt,
                    "scene_context_before": scene_context_before,
                    "scene_context_after": scene_context_after,
                    "reference": reference_payload,
                    "reference_intent": {
                        "requested_roles": list(intent_payload.requested_roles),
                        "excluded_roles": list(intent_payload.excluded_roles),
                        "continuity_mode": intent_payload.continuity_mode,
                        "confidence": intent_payload.confidence,
                        "source": intent_payload.source,
                    },
                    "reference_plan": {
                        "bindings": [
                            {
                                "reference_id": binding.reference_id,
                                "path": binding.path,
                                "roles": list(binding.roles),
                                "priority": binding.priority,
                                "preserve": list(binding.preserve),
                                "ignore": list(binding.ignore),
                            }
                            for binding in plan_payload.bindings
                        ],
                        "primary_reference_id": plan_payload.primary_reference_id,
                        "selection_reason": plan_payload.selection_reason,
                        "fallback_reason": plan_payload.fallback_reason,
                    },
                    "reference_fallback": {
                        "requested_roles": list(fallback_payload.requested_roles),
                        "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                        "missing_roles": list(fallback_payload.missing_roles),
                        "message": fallback_payload.message,
                    },
                    "wardrobe_decision": wardrobe.as_dict(),
                    "presets": list(presets)[:1],
                    "prompt_sections_before": prompt_sections_before,
                    "prompt_sections": prompt_sections,
                    "prompt_sections_after": prompt_sections_after,
                    "conflicts": list(conflicts),
                    "removed_conflicts": list(removed_conflicts),
                    "residual_conflicts": list(residual_conflicts),
                    "detected_conflicts": list(detected_conflict_details),
                    "removed_conflict_details": list(removed_conflict_details),
                    "residual_conflict_details": list(residual_conflict_details),
                    "reference_removed": dict(reference_removed or {}),
                    "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                    "workflow_fixed_prompt": dict(workflow_fixed_prompt_audit or {}),
                    "final_prompt": final_prompt,
                    "final_prompt_length": len(str(final_prompt or "")),
                    "final_prompt_sha256": prompt_hash,
                    "submitted_prompt_length": len(str(submitted_prompt or final_prompt or "")),
                    "submitted_prompt_sha256": submitted_prompt_hash,
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            debug_files = sorted(
                root.glob("*.json"),
                key=lambda item: item.name,
                reverse=True,
            )
            for stale in debug_files[40:]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            return str(path), prompt_hash
        except Exception as exc:
            logger.debug(
                "写入完整生图提示词调试文件失败: trace=%s error=%s",
                _single_line(trace_id, 40),
                _single_line(exc, 160),
            )
            return "", prompt_hash
