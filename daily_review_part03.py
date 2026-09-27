# -*- coding: utf-8 -*-
"""DailyReviewPart03Mixin。

由 tools/split_mixin_domain.py 从 daily_review.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 491 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyReviewMixin）。
"""
from __future__ import annotations

from .daily_review_shared import logger
from .daily_review_shared import Any
from .daily_review_shared import Counter
from .daily_review_shared import _safe_float
from .daily_review_shared import _safe_int
from .daily_review_shared import _single_line
from .daily_review_shared import datetime
from .daily_review_shared import deepcopy
from .daily_review_shared import hashlib
from .daily_review_shared import re
from .daily_review_shared import time
from .daily_review_shared import timedelta



class DailyReviewPart03Mixin:
    """DailyReviewPart03Mixin（从 DailyReviewMixin 拆出）。"""


    @staticmethod
    def _daily_review_guidance_is_safe(instruction: str) -> bool:
        text = str(instruction or "").strip().lower()
        if not text:
            return False
        protected_terms = (
            "provider",
            "配置",
            "阈值",
            "白名单",
            "黑名单",
            "名单",
            "权限",
            "删除",
            "封禁",
            "屏蔽",
            "停用",
            "关闭功能",
            "开启功能",
            "系统提示词",
            "忽略规则",
            "调用工具",
        )
        return not any(term in text for term in protected_terms)

    def _normalize_daily_review_payload(self, payload: dict[str, Any], *, date_key: str) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        for raw in payload.get("findings", []) if isinstance(payload.get("findings"), list) else []:
            if not isinstance(raw, dict):
                continue
            severity = _single_line(raw.get("severity"), 12).lower()
            category = _single_line(raw.get("category"), 32).lower()
            findings.append(
                {
                    "severity": severity if severity in self._DAILY_REVIEW_SEVERITIES else "info",
                    "category": category if category in self._DAILY_REVIEW_CATEGORIES else "other",
                    "title": _single_line(raw.get("title"), 100) or "未命名巡视项",
                    "evidence": _single_line(raw.get("evidence"), 320),
                    "impact": _single_line(raw.get("impact"), 240),
                }
            )
            if len(findings) >= 12:
                break

        case_reviews: list[dict[str, Any]] = []
        case_samples = self._daily_review_case_samples(date_key)
        valid_case_ids = {
            _single_line(item.get("case_id"), 20)
            for item in case_samples.get("cases", [])
            if isinstance(item, dict)
        }
        for raw in payload.get("case_reviews", []) if isinstance(payload.get("case_reviews"), list) else []:
            if not isinstance(raw, dict):
                continue
            case_id = _single_line(raw.get("case_id"), 20)
            if not case_id or case_id not in valid_case_ids:
                continue
            verdict = _single_line(raw.get("verdict"), 24).lower()
            dimensions = [
                _single_line(item, 24).lower()
                for item in (raw.get("dimensions", []) if isinstance(raw.get("dimensions"), list) else [])
                if _single_line(item, 24).lower() in {"relevance", "completeness", "tone", "timing", "safety", "tts"}
            ][:6]
            case_reviews.append({
                "case_id": case_id,
                "verdict": verdict if verdict in {"good", "needs_attention", "uncertain"} else "uncertain",
                "confidence": round(_safe_float(raw.get("confidence"), 0.0, 0.0, 1.0), 3),
                "dimensions": dimensions,
                "evidence": _single_line(raw.get("evidence"), 280),
                "missing_information": _single_line(raw.get("missing_information"), 220),
                "counterfactual": _single_line(raw.get("counterfactual"), 280),
                "reason": _single_line(raw.get("reason"), 280),
                "recommended_behavior": _single_line(raw.get("recommended_behavior"), 280),
            })
            if len(case_reviews) >= 16:
                break

        guidance_evaluations: list[dict[str, Any]] = []
        valid_guidance_ids = {
            _single_line(item.get("guidance_id"), 20)
            for item in self._daily_review_active_guidance_context(date_key).get("items", [])
            if isinstance(item, dict)
        }
        for raw in payload.get("guidance_evaluations", []) if isinstance(payload.get("guidance_evaluations"), list) else []:
            if not isinstance(raw, dict):
                continue
            guidance_id = _single_line(raw.get("guidance_id"), 20)
            verdict = _single_line(raw.get("verdict"), 24).lower()
            if not guidance_id or guidance_id not in valid_guidance_ids:
                continue
            guidance_evaluations.append({
                "guidance_id": guidance_id,
                "verdict": verdict if verdict in {"improved", "unchanged", "worse", "uncertain"} else "uncertain",
                "confidence": round(_safe_float(raw.get("confidence"), 0.0, 0.0, 1.0), 3),
                "evidence": _single_line(raw.get("evidence"), 280),
            })
            if len(guidance_evaluations) >= 8:
                break

        attention_by_id = {
            item["case_id"]: item
            for item in case_reviews
            if item.get("verdict") == "needs_attention" and _safe_float(item.get("confidence"), 0.0) >= 0.72
        }
        experimental_evidence_available = bool(case_samples.get("enabled") and valid_case_ids)
        corrections: list[dict[str, Any]] = []
        safe_guidance: list[dict[str, Any]] = []
        for raw in payload.get("corrections", []) if isinstance(payload.get("corrections"), list) else []:
            if not isinstance(raw, dict):
                continue
            correction_type = _single_line(raw.get("type"), 32).lower()
            scope = _single_line(raw.get("scope"), 24).lower()
            risk = _single_line(raw.get("risk"), 16).lower() or "medium"
            instruction = _single_line(raw.get("instruction"), 300)
            confidence = round(_safe_float(
                raw.get("confidence"),
                0.0 if experimental_evidence_available else 0.75,
                0.0,
                1.0,
            ), 3)
            evidence_case_ids = []
            for value in raw.get("evidence_case_ids", []) if isinstance(raw.get("evidence_case_ids"), list) else []:
                case_id = _single_line(value, 20)
                if case_id in valid_case_ids and case_id not in evidence_case_ids:
                    evidence_case_ids.append(case_id)
            item = {
                "type": correction_type or "suggestion",
                "scope": scope or "reply",
                "instruction": instruction,
                "reason": _single_line(raw.get("reason"), 220),
                "risk": risk if risk in {"low", "medium", "high"} else "medium",
                "confidence": confidence,
                "evidence_case_ids": evidence_case_ids[:8],
                "auto_apply": bool(raw.get("auto_apply", False)),
            }
            corrections.append(item)
            if (
                item["type"] == "prompt_guidance"
                and item["scope"] in self._DAILY_REVIEW_GUIDANCE_SCOPES
                and item["risk"] == "low"
                and item["auto_apply"]
                and confidence >= 0.72
                and (
                    not experimental_evidence_available
                    or any(case_id in attention_by_id for case_id in evidence_case_ids)
                )
                and instruction
                and self._daily_review_guidance_is_safe(instruction)
            ):
                safe_guidance.append(
                    {
                        "scope": item["scope"],
                        "instruction": instruction,
                        "reason": item["reason"],
                        "confidence": confidence,
                        "evidence_case_ids": evidence_case_ids[:8],
                    }
                )
            if len(corrections) >= 8:
                break

        suggestions: list[dict[str, Any]] = []
        for raw in payload.get("suggested_config_changes", []) if isinstance(payload.get("suggested_config_changes"), list) else []:
            if not isinstance(raw, dict):
                continue
            suggestions.append(self._daily_review_config_suggestion(raw))
            if len(suggestions) >= 8:
                break

        focus = [
            _single_line(item, 120)
            for item in (payload.get("tomorrow_focus", []) if isinstance(payload.get("tomorrow_focus"), list) else [])
            if _single_line(item, 120)
        ][:8]
        return {
            "date": date_key,
            "generated_at": time.time(),
            "status": "completed",
            "headline": _single_line(payload.get("headline"), 120) or "每日巡视已完成",
            "summary": _single_line(payload.get("summary"), 520) or "模型未提供详细复盘。",
            "health_score": _safe_int(payload.get("health_score"), 0, 0, 100),
            "findings": findings,
            "case_reviews": case_reviews,
            "guidance_evaluations": guidance_evaluations,
            "corrections": corrections,
            "suggested_config_changes": suggestions,
            "tomorrow_focus": focus,
            "safe_guidance": safe_guidance,
        }

    def _daily_review_guidance_expiry(self, date_key: str) -> float:
        try:
            target = datetime.strptime(date_key, "%Y-%m-%d").date()
            local = self._daily_review_now()
            # The review runs the next morning, so date + 4 keeps guidance active for at most three full days.
            expiry = datetime.combine(target + timedelta(days=4), datetime.min.time(), tzinfo=local.tzinfo)
            return expiry.timestamp()
        except Exception:
            return time.time() + 72 * 60 * 60

    @staticmethod
    def _daily_review_guidance_id(scope: Any, instruction: Any) -> str:
        fingerprint = f"{_single_line(scope, 24).lower()}|{_single_line(instruction, 300).lower()}"
        digest = hashlib.sha256(fingerprint.encode("utf-8", errors="ignore")).hexdigest().upper()
        return f"G-{digest[:8]}"

    def _activate_daily_review_guidance(self, report: dict[str, Any]) -> dict[str, Any]:
        guidance = report.get("safe_guidance") if isinstance(report.get("safe_guidance"), list) else []
        previous = self.data.get("daily_review_active_guidance")
        if not isinstance(previous, dict):
            previous = {}
        previous_items = [
            deepcopy(item) for item in previous.get("items", [])
            if isinstance(item, dict) and _single_line(item.get("instruction"), 300)
        ] if isinstance(previous.get("items"), list) else []
        evaluations = {
            _single_line(item.get("guidance_id"), 20): item
            for item in report.get("guidance_evaluations", [])
            if isinstance(item, dict) and _single_line(item.get("guidance_id"), 20)
        } if isinstance(report.get("guidance_evaluations"), list) else {}
        retired = [
            deepcopy(item) for item in previous.get("retired_items", []) if isinstance(item, dict)
        ] if isinstance(previous.get("retired_items"), list) else []
        now = time.time()
        date_key = _single_line(report.get("date"), 16)
        expiry = self._daily_review_guidance_expiry(date_key)
        carry_by_scope: dict[str, dict[str, Any]] = {}
        lifecycle_counts: Counter[str] = Counter()

        for old in previous_items:
            scope = _single_line(old.get("scope"), 24)
            instruction = _single_line(old.get("instruction"), 300)
            guidance_id = _single_line(old.get("guidance_id"), 20) or self._daily_review_guidance_id(scope, instruction)
            old["guidance_id"] = guidance_id
            evaluation = evaluations.get(guidance_id, {})
            verdict = _single_line(evaluation.get("verdict"), 24) or "uncertain"
            confidence = _safe_float(evaluation.get("confidence"), 0.0, 0.0, 1.0)
            old["last_evaluation"] = deepcopy(evaluation) if evaluation else {
                "guidance_id": guidance_id,
                "verdict": "uncertain",
                "confidence": 0.0,
                "evidence": "当日没有足够证据判断效果",
            }
            old["evaluation_count"] = _safe_int(old.get("evaluation_count"), 0, 0) + (1 if evaluation else 0)
            retire_reason = ""
            if verdict == "worse" and confidence >= 0.65:
                retire_reason = "rolled_back"
            elif verdict == "unchanged" and confidence >= 0.72 and old["evaluation_count"] >= 2:
                retire_reason = "no_effect"
            elif _safe_float(old.get("active_until") or previous.get("active_until"), expiry, 0.0) <= now:
                retire_reason = "expired"
            if retire_reason:
                old["status"] = retire_reason
                old["retired_at"] = now
                retired.append(old)
                lifecycle_counts[retire_reason] += 1
            elif scope in self._DAILY_REVIEW_GUIDANCE_SCOPES:
                if verdict == "improved" and confidence >= 0.72:
                    old["status"] = "validated"
                    lifecycle_counts["improved"] += 1
                else:
                    old["status"] = "observing"
                old["active_until"] = _safe_float(old.get("active_until") or previous.get("active_until"), expiry, 0.0)
                carry_by_scope[scope] = old

        best_new_by_scope: dict[str, dict[str, Any]] = {}
        for raw in guidance:
            if not isinstance(raw, dict):
                continue
            scope = _single_line(raw.get("scope"), 24)
            instruction = _single_line(raw.get("instruction"), 300)
            if scope not in self._DAILY_REVIEW_GUIDANCE_SCOPES or not instruction:
                continue
            current = best_new_by_scope.get(scope)
            if current is None or _safe_float(raw.get("confidence"), 0.0) > _safe_float(current.get("confidence"), 0.0):
                best_new_by_scope[scope] = deepcopy(raw)

        for scope, raw in best_new_by_scope.items():
            instruction = _single_line(raw.get("instruction"), 300)
            guidance_id = self._daily_review_guidance_id(scope, instruction)
            old = carry_by_scope.get(scope)
            if old and _single_line(old.get("guidance_id"), 20) != guidance_id:
                old["status"] = "superseded"
                old["retired_at"] = now
                retired.append(old)
                lifecycle_counts["superseded"] += 1
                old = None
            item = {
                "guidance_id": guidance_id,
                "scope": scope,
                "instruction": instruction,
                "reason": _single_line(raw.get("reason"), 220),
                "confidence": round(_safe_float(raw.get("confidence"), 0.0, 0.0, 1.0), 3),
                "evidence_case_ids": list(raw.get("evidence_case_ids", []))[:8] if isinstance(raw.get("evidence_case_ids"), list) else [],
                "first_date": _single_line((old or {}).get("first_date"), 16) or date_key,
                "last_date": date_key,
                "support_days": _safe_int((old or {}).get("support_days"), 0, 0, 30) + 1,
                "evaluation_count": _safe_int((old or {}).get("evaluation_count"), 0, 0),
                "active_until": expiry,
                "status": "observing",
            }
            if old and isinstance(old.get("last_evaluation"), dict):
                item["last_evaluation"] = deepcopy(old["last_evaluation"])
                lifecycle_counts["renewed"] += 1
            else:
                lifecycle_counts["new"] += 1
            carry_by_scope[scope] = item

        items = list(carry_by_scope.values())[:4]
        manual_paused = bool(previous.get("manual_paused"))
        active = {
            "source_date": date_key,
            "generated_at": _safe_float(report.get("generated_at"), time.time()),
            "active_until": max((_safe_float(item.get("active_until"), expiry) for item in items), default=expiry),
            "active": bool(items) and bool(self._daily_review_setting("daily_review_auto_apply_guidance", True)) and not manual_paused,
            "manual_paused": manual_paused,
            "items": items,
            "retired_items": retired[-16:],
        }
        self.data["daily_review_active_guidance"] = active
        report["applied_safe_guidance"] = deepcopy(active.get("items", [])) if active["active"] else []
        report["guidance_lifecycle"] = {
            "active": len(items),
            "new": lifecycle_counts.get("new", 0),
            "renewed": lifecycle_counts.get("renewed", 0),
            "improved": lifecycle_counts.get("improved", 0),
            "resolved": lifecycle_counts.get("resolved", 0),
            "rolled_back": lifecycle_counts.get("rolled_back", 0),
            "no_effect": lifecycle_counts.get("no_effect", 0),
            "expired": lifecycle_counts.get("expired", 0),
            "superseded": lifecycle_counts.get("superseded", 0),
        }
        return active

    async def _ensure_daily_review(
        self,
        force: bool = False,
        *,
        target_date: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not bool(self._daily_review_setting("enable_daily_review", True)) and not force:
            return None
        date_key = _single_line(target_date, 16) or self._daily_review_target_date(now=now)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            raise ValueError("巡视日期格式必须为 YYYY-MM-DD")
        async with self._daily_review_lock():
            existing = self._daily_review_report_for_date(date_key)
            if isinstance(existing, dict) and not force:
                return existing
            previous_attempt = deepcopy(self.data.get("daily_review_last_attempt"))
            current_ts = now.timestamp() if isinstance(now, datetime) else time.time()
            if not force and self._daily_review_retry_delay_seconds(
                previous_attempt,
                now=current_ts,
            ) > 0:
                return None
            provider_id = self._task_provider(
                self._daily_review_setting("daily_review_provider_id", ""),
                self._daily_review_setting("troubleshooting_provider_id", ""),
                self._daily_review_setting("complex_reasoning_provider_id", ""),
                self._daily_review_setting("mai_style_provider_id", ""),
                self._daily_review_setting("llm_provider_id", ""),
            )
            if not provider_id:
                resolver = getattr(self, "_resolve_chat_provider_id", None)
                if callable(resolver):
                    try:
                        provider_id = _single_line(resolver(None), 160)
                    except Exception:
                        provider_id = ""
            if not provider_id:
                error = "未配置可用的每日巡视模型"
                async with self._data_lock:
                    self.data["daily_review_last_attempt"] = self._daily_review_failure_attempt(
                        date_key, error, previous=previous_attempt
                    )
                    self._save_data_sync(sections={"daily_review_last_attempt"})
                if force:
                    raise RuntimeError(error)
                return None

            async with self._data_lock:
                snapshot = self._daily_review_snapshot(date_key)
                self.data["daily_review_last_attempt"] = {
                    "date": date_key,
                    "status": "running",
                    "attempted_at": time.time(),
                    "error": "",
                }
                self._save_data_sync(sections={"daily_review_last_attempt"})
            try:
                raw = await self._llm_call(
                    self._daily_review_prompt(snapshot),
                    max_tokens=3400,
                    provider_id=provider_id,
                    task="daily_review",
                    timeout_key="DAILY_REVIEW_PROVIDER_ID",
                )
                payload = self._daily_review_json_object(raw)
                if not payload:
                    raise ValueError("巡视模型未返回有效 JSON")
                report = self._normalize_daily_review_payload(payload, date_key=date_key)
                report["provider_id"] = _single_line(provider_id, 160)
                report["evidence_summary"] = {
                    "model_tasks": len(snapshot.get("model_tasks", [])),
                    "model_failures": len(snapshot.get("model_failures", [])),
                    "proactive_events": _safe_int((snapshot.get("proactive") or {}).get("total"), 0, 0),
                    "passive_no_reply_types": len(snapshot.get("passive_no_reply", [])),
                    "member_safety_events": sum((snapshot.get("member_safety") or {}).get("category_counts", {}).values()),
                    "case_samples": _safe_int((snapshot.get("case_review") or {}).get("sampled"), 0, 0),
                }
                report["quality_metrics"] = self._daily_review_quality_metrics(snapshot, report)
            except Exception as exc:
                safe_error = self._daily_review_safe_text(exc, 180)
                async with self._data_lock:
                    self.data["daily_review_last_attempt"] = self._daily_review_failure_attempt(
                        date_key, safe_error, previous=previous_attempt
                    )
                    self._save_data_sync(sections={"daily_review_last_attempt"})
                if force:
                    raise
                logger.warning(
                    "每日终盘巡视失败，将在退避后重试: date=%s failures=%s status=%s error=%s",
                    date_key,
                    self.data["daily_review_last_attempt"].get("failure_count", 1),
                    self.data["daily_review_last_attempt"].get("status", "failed"),
                    safe_error,
                )
                return None

            async with self._data_lock:
                reports = self._daily_review_reports()
                reports[:] = [
                    item for item in reports
                    if not (isinstance(item, dict) and _single_line(item.get("date"), 16) == date_key)
                ]
                reports.append(report)
                reports.sort(key=lambda item: _single_line(item.get("date"), 16) if isinstance(item, dict) else "")
                retention = max(3, _safe_int(self._daily_review_setting("daily_review_retention_days", 30), 30, 3, 180))
                del reports[:-retention]
                self._activate_daily_review_guidance(report)
                self.data["daily_review_completed_day"] = date_key
                self.data["daily_review_last_attempt"] = {
                    "date": date_key,
                    "status": "completed",
                    "attempted_at": report["generated_at"],
                    "error": "",
                }
                self._save_data_sync(sections={"daily_review_reports", "daily_review_active_guidance", "daily_review_last_attempt", "daily_review_completed_day"})
            logger.info(
                "每日终盘巡视完成: date=%s score=%s findings=%s guidance=%s",
                date_key,
                report.get("health_score"),
                len(report.get("findings", [])),
                len(report.get("applied_safe_guidance", [])),
            )
            return report

    def _daily_review_pending_dates(
        self,
        *,
        now: datetime | None = None,
        max_days: int = 3,
    ) -> list[str]:
        target_text = self._daily_review_target_date(now=now)
        try:
            target = datetime.strptime(target_text, "%Y-%m-%d").date()
        except Exception:
            return [target_text]
        completed_values = [
            _single_line(self.data.get("daily_review_completed_day"), 16),
            *[
                _single_line(item.get("date"), 16)
                for item in self._daily_review_reports() if isinstance(item, dict)
            ],
        ]
        completed_dates = []
        for value in completed_values:
            try:
                completed_dates.append(datetime.strptime(value, "%Y-%m-%d").date())
            except Exception:
                continue
        if not completed_dates:
            return [] if self._daily_review_report_for_date(target_text) else [target_text]
        latest = max(completed_dates)
        start = latest + timedelta(days=1)
        if start > target:
            return [] if self._daily_review_report_for_date(target_text) else [target_text]
        dates = []
        current = start
        while current <= target:
            value = current.isoformat()
            if self._daily_review_report_for_date(value) is None:
                dates.append(value)
            current += timedelta(days=1)
        return dates[-max(1, min(7, max_days)):]
